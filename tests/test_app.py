import io
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests

from app import create_app
from knowledge import KnowledgeError, Ollama, extract_chunks
from tests.pdf_fixture import make_pdf


HEADERS = {"X-Aomini-Request": "1"}


class FakeOllama:
    """Only for deterministic application tests, never used by the app itself."""
    def __init__(self):
        self.fail = False
        self.answer_text = "Pad Kra Pao contains Thai basil [1]."
        self.last_context = ""
        self.calls = 0

    def call(self, *args, **kwargs):
        if self.fail:
            raise KnowledgeError("Cannot connect to Ollama. Start it with 'ollama serve', then retry.")
        return {"models": [{"name": "qwen3:1.7b"}, {"name": "nomic-embed-text:latest"}]}

    def embed(self, texts, query=False):
        self.calls += 1
        if self.fail:
            raise KnowledgeError("Cannot connect to Ollama. Start it with 'ollama serve', then retry.")
        return [[1.0, 0.0] if query or "basil" in text.lower() else [0.0, 1.0] for text in texts]

    def answer(self, question, context):
        self.calls += 1
        self.last_context = context
        return self.answer_text


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.model = FakeOllama()
        self.app = create_app({"TESTING": True, "DATA_DIR": self.directory.name}, self.model)
        self.client = self.app.test_client()
        self.store = self.app.extensions["store"]

    def tearDown(self):
        self.app.extensions["index_executor"].shutdown(wait=True)
        self.directory.cleanup()

    def upload(self, filename="food.pdf", pages=None, payload=None):
        content = payload if payload is not None else make_pdf(pages or ["Class notes.", "Pad Kra Pao contains Thai basil."])
        return self.client.post("/api/documents", headers=HEADERS,
                                data={"file": (io.BytesIO(content), filename)})

    def wait(self):
        self.app.extensions["index_executor"].submit(lambda: None).result(timeout=10)

    def ask(self, question):
        return self.client.post("/api/chat", json={"message": question}, headers=HEADERS)

    def test_empty_start_and_static_assets(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for path in ("/static/script.js", "/static/style.css"):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/documents").json["documents"], [])
        self.assertEqual(self.ask("Recommend a dish").json["kind"], "no_context")
        self.assertEqual(self.model.calls, 0)

    def test_five_pdfs_and_metadata_listing_without_ai(self):
        names = ["basil.pdf", "noodles.pdf", "soups.pdf", "menus.pdf", "อาหารไทย.pdf"]
        for name in names:
            self.assertEqual(self.upload(name).status_code, 202)
        self.wait()
        docs = self.client.get("/api/documents").json["documents"]
        self.assertEqual([doc["filename"] for doc in docs], names)
        self.assertTrue(all(doc["status"] == "ready" and doc["pages"] == 2 for doc in docs))
        calls = self.model.calls
        self.model.fail = True
        result = self.ask("What documents are in my collection?").json
        self.assertEqual(result["kind"], "collection")
        self.assertEqual(len(result["documents"]), 5)
        self.assertTrue(all(name in result["answer"] for name in names))
        self.assertEqual(self.model.calls, calls)

    def test_pdf_view_exact_bytes_and_inline_response(self):
        payload = make_pdf(["Pad Kra Pao contains basil."])
        document_id = self.upload(payload=payload).json["document"]["id"]
        response = self.client.get(f"/api/documents/{document_id}/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertEqual(response.data, payload)
        self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
        response.close()

    def test_grounded_pipeline_cites_correct_page_and_excerpt(self):
        self.upload()
        self.wait()
        result = self.ask("Which dish contains basil?")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["kind"], "answer")
        source = result.json["sources"][0]
        self.assertEqual(source["filename"], "food.pdf")
        self.assertEqual(source["page"], 2)
        self.assertIn("Thai basil", source["text"])
        self.assertIn("PDF page 2", self.model.last_context)

    def test_delete_removes_original_metadata_and_vectors(self):
        document_id = self.upload().json["document"]["id"]
        self.wait()
        self.assertEqual(self.client.delete(f"/api/documents/{document_id}", headers=HEADERS).status_code, 200)
        self.assertFalse(self.store.path(document_id).exists())
        self.assertEqual(self.store.documents(), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks").fetchone()[0], 0)
        self.assertEqual(self.client.get(f"/api/documents/{document_id}/file").status_code, 404)

    def test_reject_invalid_upload_and_missing_file(self):
        self.assertEqual(self.upload("food.txt").status_code, 400)
        self.assertEqual(self.upload(payload=b"this is not a PDF").status_code, 400)
        self.assertEqual(self.client.post("/api/documents", headers=HEADERS).status_code, 400)
        self.assertEqual(self.store.documents(), [])

    def test_corrupt_blank_and_encrypted_pdf_index_errors(self):
        self.upload("corrupt.pdf", payload=b"%PDF-corrupt")
        self.upload("blank.pdf", pages=[""])
        self.upload("locked.pdf", payload=make_pdf(["food"], encrypted=True))
        self.wait()
        docs = self.store.documents()
        self.assertTrue(all(doc["status"] == "error" for doc in docs))
        self.assertIn("valid", docs[0]["error"])
        self.assertIn("OCR", docs[1]["error"])
        self.assertIn("Password", docs[2]["error"])

    def test_ollama_failure_keeps_pdf_and_retry_recovers(self):
        self.model.fail = True
        document_id = self.upload().json["document"]["id"]
        self.wait()
        self.assertEqual(self.store.document(document_id)["status"], "error")
        self.assertTrue(self.store.path(document_id).exists())
        self.model.fail = False
        self.assertEqual(self.client.post(f"/api/documents/{document_id}/retry", headers=HEADERS).status_code, 202)
        self.wait()
        self.assertEqual(self.store.document(document_id)["status"], "ready")
        self.assertEqual(self.client.post(f"/api/documents/{document_id}/retry", headers=HEADERS).status_code, 409)

    def test_chat_connection_error_and_status(self):
        self.upload()
        self.wait()
        self.assertTrue(self.client.get("/api/status").json["ready"])
        self.model.fail = True
        response = self.ask("Tell me about basil")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Ollama", response.json["error"])
        self.assertFalse(self.client.get("/api/status").json["online"])

    def test_missing_and_invalid_citations_are_not_shown_as_verified_answers(self):
        self.upload()
        self.wait()
        for answer in ["It costs 999 baht.", "It costs 999 baht [99].", "A dish [0]."]:
            self.model.answer_text = answer
            result = self.ask("What about basil?").json
            self.assertEqual(result["kind"], "unverified")
            self.assertNotIn("999", result["answer"])
            self.assertEqual(result["sources"], [])

    def test_no_relevant_chunks_does_not_call_generation(self):
        self.upload(pages=["A general introduction."])
        self.wait()
        self.assertEqual(self.ask("Tell me about basil").json["kind"], "no_context")
        self.assertEqual(self.model.last_context, "")

    def test_explicit_model_abstention(self):
        self.upload()
        self.wait()
        self.model.answer_text = "NO_DOCUMENT_SUPPORT."
        result = self.ask("What is the restaurant price?").json
        self.assertEqual(result["kind"], "no_context")
        self.assertIn("do not provide", result["answer"])
        self.assertEqual(result["sources"], [])

    def test_unsafe_paths_origin_and_mutation_header(self):
        response = self.upload("../../outside.pdf")
        document_id = response.json["document"]["id"]
        self.wait()
        self.assertEqual(self.store.document(document_id)["filename"], "outside.pdf")
        self.assertEqual(self.store.path(document_id).parent, self.store.uploads)
        self.assertEqual(self.client.get("/api/documents/invalid/file").status_code, 404)
        self.assertEqual(self.client.post("/api/chat", json={"message": "hello"}).status_code, 403)
        self.assertEqual(self.client.post("/api/chat", json={"message": "hello"}, headers={**HEADERS, "Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/documents", headers={"Host": "evil.example"}).status_code, 403)

    def test_invalid_chat_payloads(self):
        for body in [{}, [], {"message": 2}, {"message": " "}, {"message": "x" * 2001}]:
            self.assertEqual(self.client.post("/api/chat", json=body, headers=HEADERS).status_code, 400)

    def test_delete_during_indexing_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        original = self.model.embed
        def slow_embed(*args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            return original(*args, **kwargs)
        self.model.embed = slow_embed
        document_id = self.upload().json["document"]["id"]
        try:
            self.assertTrue(entered.wait(timeout=3))
            self.assertEqual(self.client.delete(f"/api/documents/{document_id}", headers=HEADERS).status_code, 409)
        finally:
            release.set()
        self.wait()

    def test_restart_preserves_ready_index_and_marks_interrupted_jobs(self):
        self.upload()
        self.wait()
        with self.store.connect() as db:
            db.execute("INSERT INTO documents(id,filename,status) VALUES (?,?,?)", ("a" * 32, "interrupted.pdf", "indexing"))
        other = create_app({"TESTING": True, "DATA_DIR": self.directory.name}, self.model)
        try:
            docs = other.extensions["store"].documents()
            self.assertEqual(docs[0]["status"], "ready")
            self.assertEqual(docs[1]["status"], "error")
            self.assertIn("interrupted", docs[1]["error"])
            self.assertTrue(other.extensions["store"].search([1, 0]))
        finally:
            other.extensions["index_executor"].shutdown(wait=True)

    def test_upload_size_limit(self):
        response = self.upload(payload=b"%PDF-" + b"x" * (20 * 1024 * 1024))
        try:
            self.assertEqual(response.status_code, 413)
            self.assertEqual(self.store.documents(), [])
        finally:
            # Werkzeug spools large synthetic multipart request bodies to a temp file.
            response.request.input_stream.close()
            response.close()


class OllamaClientTests(unittest.TestCase):
    def test_connection_and_timeout_are_readable_errors(self):
        for failure in [requests.ConnectionError(), requests.Timeout()]:
            with patch("knowledge.requests.request", side_effect=failure):
                with self.assertRaises(KnowledgeError):
                    Ollama("http://127.0.0.1:11434").embed(["food"])

    def test_invalid_embedding_rejected(self):
        for vectors in [[], [[0, 0]], [[float("nan"), 1]], [["bad"]]]:
            with patch.object(Ollama, "call", return_value={"embeddings": vectors}):
                with self.assertRaises(KnowledgeError):
                    Ollama("http://127.0.0.1:11434").embed(["food"])

    def test_page_chunking_overlap_and_full_text(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as file:
            text = "basil " * 500
            file.write(make_pdf([text, "Second page."]))
            file.flush()
            pages, chunks = extract_chunks(file.name)
            self.assertEqual(pages, 2)
            self.assertTrue(all(len(chunk["text"]) <= 900 for chunk in chunks))
            self.assertEqual(chunks[-1]["page"], 2)
            self.assertGreater(len(chunks), 3)


if __name__ == "__main__":
    unittest.main()
