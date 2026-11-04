"""Opt-in test using real Ollama; all synthetic PDFs and vectors are temporary."""

import io
import os
import tempfile
import unittest

from app import create_app
from tests.pdf_fixture import make_pdf


@unittest.skipUnless(os.environ.get("AOMINI_LIVE_TESTS") == "1", "Set AOMINI_LIVE_TESTS=1 for real Ollama integration")
class LiveOllamaTests(unittest.TestCase):
    def test_five_pdfs_real_embeddings_and_grounded_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory})
            client = app.test_client()
            headers = {"X-Aomini-Request": "1"}
            executor = app.extensions["index_executor"]
            try:
                self.assertTrue(client.get("/api/status").json["ready"], "Pull both required models first")
                fixtures = {
                    "basil-guide.pdf": ["Synthetic class test notes. Not a real restaurant menu.",
                                        "Pad Kra Pao is a Thai basil stir-fry. These notes list Thai basil, chicken, chili and garlic. No price is provided."],
                    "noodle-guide.pdf": ["Pad Thai is a rice noodle dish. No price is provided."],
                    "soup-guide.pdf": ["Tom Yum is described here as a sour and spicy soup. No price is provided."],
                    "rice-guide.pdf": ["Khao Man Gai is described here as chicken served with rice. No price is provided."],
                    "salad-guide.pdf": ["Som Tam is described here as a green papaya salad. No price is provided."],
                }
                for name, pages in fixtures.items():
                    response = client.post("/api/documents", headers=headers,
                                           data={"file": (io.BytesIO(make_pdf(pages)), name)})
                    self.assertEqual(response.status_code, 202)
                executor.submit(lambda: None).result(timeout=300)
                docs = client.get("/api/documents").json["documents"]
                self.assertEqual(len(docs), 5)
                self.assertTrue(all(doc["status"] == "ready" for doc in docs), docs)
                listing = client.post("/api/chat", headers=headers, json={"message": "What documents are in my collection?"}).json
                self.assertEqual({doc["filename"] for doc in listing["documents"]}, set(fixtures))
                pdf = client.get(f"/api/documents/{docs[0]['id']}/file")
                self.assertEqual(pdf.mimetype, "application/pdf")
                self.assertTrue(pdf.data.startswith(b"%PDF-"))
                pdf.close()
                original_answer = app.extensions["ollama"].answer
                def record_answer(question, context):
                    answer = original_answer(question, context)
                    print(f"\nRAW MODEL ANSWER: {answer}")
                    return answer
                app.extensions["ollama"].answer = record_answer
                for question in ["According to the PDF, which ingredients are listed for Pad Kra Pao?",
                                 "What is the exact restaurant price of Pad Kra Pao in baht? If missing, say the documents do not provide it."]:
                    response = client.post("/api/chat", headers=headers, json={"message": question})
                    self.assertEqual(response.status_code, 200)
                    result = response.json
                    print(f"\nLIVE QUESTION: {question}\nLIVE ANSWER: {result['answer']}")
                    if "ingredients" in question:
                        self.assertEqual(result["kind"], "answer", result)
                        self.assertTrue(any(source["filename"] == "basil-guide.pdf" and source["page"] == 2 for source in result["sources"]))
                        self.assertIn("basil", result["answer"].lower())
                    else:
                        self.assertIn(result["kind"], ("answer", "no_context"), result)
                        self.assertNotRegex(result["answer"], r"\b\d+\s*(baht|THB)")
                        self.assertRegex(result["answer"].lower(), r"(not|no |missing|unavailable|doesn't)")
                print("LIVE RESULT: 5 PDFs indexed; deterministic listing; original PDF served; page-2 ingredient answer; missing-price refusal.")
            finally:
                executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
