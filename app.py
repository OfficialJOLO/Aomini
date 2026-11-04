"""Aomini: run with `python app.py`, then open http://127.0.0.1:5000."""

import atexit
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from knowledge import KnowledgeError, Ollama, Store


def is_collection_question(question):
    text = question.lower().strip()
    return bool(
        re.search(r"\b(what|which)\s+(documents?|pdfs?|files?)\b.*\b(collection|upload|have|stored|available)\b", text)
        or re.search(r"\b(list|show)\b.{0,35}\b(documents?|pdfs?|files?|collection)\b", text)
        or text.rstrip("?!.") in {"my documents", "my collection", "uploaded documents"}
        or ("문서" in text and any(word in text for word in ("목록", "어떤", "뭐", "무슨")))
        or ("เอกสาร" in text and any(word in text for word in ("อะไรบ้าง", "รายการ", "มีอะไร")))
    )


def create_app(config=None, ollama=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=21 * 1024 * 1024,
        DATA_DIR=os.environ.get("AOMINI_DATA_DIR", str(Path(__file__).parent / "instance")),
        OLLAMA_URL=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        MIN_SIMILARITY=0.35,
    )
    if config:
        app.config.update(config)
    store = Store(app.config["DATA_DIR"])
    model = ollama or Ollama(app.config["OLLAMA_URL"])
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-index")
    atexit.register(executor.shutdown, wait=False, cancel_futures=True)
    app.extensions.update(store=store, ollama=model, index_executor=executor)

    @app.before_request
    def local_request_guard():
        # No CORS. A custom header prevents cross-site forms from modifying local data.
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            if request.headers.get("X-Aomini-Request") != "1":
                return jsonify(error="Refresh Aomini and try again (missing request header)."), 403
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Requests must come from the Aomini page."), 403
        if request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
            return jsonify(error="Aomini is a local-only application. Open it using localhost or 127.0.0.1."), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        return response

    @app.errorhandler(KnowledgeError)
    def knowledge_error(error):
        return jsonify(error=str(error)), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = "Upload PDFs up to 20 MB each." if error.code == 413 else error.description
        return jsonify(error=message), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unexpected application error")
        return jsonify(error="Something went wrong. Check the Flask terminal, then retry."), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        try:
            data = model.call("/api/tags", timeout=3)
            available = {item["name"] for item in data.get("models", [])}
            missing = [name for name in ("qwen3:1.7b", "nomic-embed-text")
                       if name not in available and name + ":latest" not in available]
            return jsonify(online=True, ready=not missing, missing=missing,
                           message="Models ready" if not missing else "Missing: " + ", ".join(missing))
        except KnowledgeError as exc:
            return jsonify(online=False, ready=False, missing=[], message=str(exc))

    @app.get("/api/documents")
    def documents():
        return jsonify(documents=store.documents())

    @app.post("/api/documents")
    def upload():
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify(error="Choose a PDF file to upload."), 400
        # Display names remain readable, including Thai. Storage names are generated UUIDs.
        filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(char for char in filename if ord(char) >= 32 and ord(char) != 127).strip()
        if not filename.lower().endswith(".pdf") or len(filename) > 200:
            return jsonify(error="Choose a PDF with a filename no longer than 200 characters."), 400
        header = file.stream.read(5)
        file.stream.seek(0)
        if header != b"%PDF-":
            return jsonify(error="This file is not a valid PDF."), 400
        document_id = uuid.uuid4().hex
        path = store.path(document_id)
        try:
            file.save(path)
            if path.stat().st_size > 20 * 1024 * 1024:
                path.unlink()
                return jsonify(error="Upload PDFs up to 20 MB each."), 413
            with store.connect() as db:
                db.execute("INSERT INTO documents(id,filename,status) VALUES (?,?,'queued')", (document_id, filename))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        executor.submit(store.index, document_id, model)
        return jsonify(document=store.document(document_id)), 202

    def find_document(document_id):
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            abort(404, description="Document not found.")
        document = store.document(document_id)
        if not document:
            abort(404, description="Document not found.")
        return document

    @app.get("/api/documents/<document_id>/file")
    def view_document(document_id):
        document = find_document(document_id)
        if not store.path(document_id).is_file():
            abort(404, description="The original PDF is missing. Delete this entry and upload it again.")
        response = send_file(store.path(document_id), mimetype="application/pdf", download_name=document["filename"], as_attachment=False)
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'self'"
        return response

    @app.delete("/api/documents/<document_id>")
    def delete_document(document_id):
        find_document(document_id)
        with store.connect() as db:
            row = db.execute("SELECT status FROM documents WHERE id=?", (document_id,)).fetchone()
            if row and row["status"] in {"queued", "indexing"}:
                return jsonify(error="Wait for indexing to finish before deleting this PDF."), 409
            store.path(document_id).unlink(missing_ok=True)
            db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        return jsonify(deleted=document_id)

    @app.post("/api/documents/<document_id>/retry")
    def retry_document(document_id):
        find_document(document_id)
        with store.connect() as db:
            result = db.execute("UPDATE documents SET status='queued', error=NULL WHERE id=? AND status='error'", (document_id,))
            if not result.rowcount:
                return jsonify(error="Only failed documents can be retried."), 409
        executor.submit(store.index, document_id, model)
        return jsonify(document=store.document(document_id)), 202

    @app.post("/api/chat")
    def chat():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("message"), str):
            return jsonify(error="Send a question as text."), 400
        question = body["message"].strip()
        if not question or len(question) > 2000:
            return jsonify(error="Ask a question between 1 and 2,000 characters."), 400
        docs = store.documents()
        if is_collection_question(question):
            answer = "Your collection is empty. Upload a PDF to get started."
            if docs:
                answer = "Documents in your collection:\n" + "\n".join(
                    f"{number}. {doc['filename']} — {doc['status']}" for number, doc in enumerate(docs, 1))
            return jsonify(answer=answer, sources=[], documents=docs, kind="collection")
        if not any(doc["status"] == "ready" for doc in docs):
            return jsonify(answer="There are no indexed PDFs yet. Upload a text-based PDF and wait until it says Ready. I need document evidence before I can recommend a dish.", sources=[], kind="no_context")
        vector = model.embed([question], query=True)[0]
        sources = store.search(vector, minimum=app.config["MIN_SIMILARITY"])
        if not sources:
            return jsonify(answer="I couldn't find relevant information in your PDFs. The documents do not support an answer to this question. Try a more specific question or upload another source.", sources=[], kind="no_context")
        context = "\n\n".join(f"[{i}] {source['filename']} — PDF page {source['page']}\n{source['text']}" for i, source in enumerate(sources, 1))
        answer = model.answer(question, context)
        if answer.strip().rstrip(".") == "NO_DOCUMENT_SUPPORT":
            return jsonify(answer="The uploaded documents do not provide the information needed to answer this question. I can't establish a price, ingredient, or other detail that isn't stated in them.", sources=[], kind="no_context")
        citations = {int(number) for number in re.findall(r"\[(\d+)\]", answer)}
        if not citations or not citations.issubset(set(range(1, len(sources) + 1))):
            return jsonify(answer="I couldn't verify a cited answer in your PDFs. The documents do not support an answer I can reliably show. Please rephrase your question or add a more relevant PDF.", sources=[], kind="unverified")
        # A document deleted while Qwen was running must not appear as a current source.
        if any(not (doc := store.document(sources[number - 1]["document_id"])) or doc["status"] != "ready" for number in citations):
            return jsonify(error="The collection changed while answering. Ask again using the current documents."), 409
        cited_sources = [dict(source, citation=i) for i, source in enumerate(sources, 1) if i in citations]
        return jsonify(answer=answer, sources=cited_sources, kind="answer")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
