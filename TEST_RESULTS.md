# Verification results

Verified on **3 November 2026 (Asia/Bangkok)** using macOS, Python 3.13, Flask 3.1.3, pypdf 6.21.4, requests 2.34.2, and Ollama 0.40.13 on Apple M4.

The real-model checks used a separate local Ollama instance at `http://127.0.0.1:11435` with **qwen3:1.7b** and **nomic-embed-text**. The browser preview used Flask at `http://127.0.0.1:5055`. These are test overrides; the delivered defaults remain ports 11434 and 5000.

## Automated application tests

```bash
python -m unittest tests.test_app -v
```

**20 tests passed.** These use a fake Ollama client, real Flask routes, real pypdf extraction, real SQLite storage, and temporary synthetic PDF bytes.

Covered:

- Empty collection, home page, and static assets.
- Upload and background indexing of five documents, including a Thai filename.
- Exact document metadata listing while Ollama is unavailable.
- Serving the exact original PDF bytes with an inline PDF response.
- Retrieval and source metadata from PDF page 2.
- Deleting original files, metadata, and generated vectors.
- Invalid extensions, missing uploads, invalid file signatures, and the 20 MB limit.
- Corrupt, blank/scanned-style, and password-protected PDF errors.
- Ollama failure during indexing, retained original file, and successful retry.
- Offline chat and model-status errors.
- Withholding answers with missing, zero, or out-of-range citations.
- Explicit model abstention and empty retrieval results.
- Generated safe storage paths, invalid document IDs, cross-origin requests, custom request headers, and local host restrictions.
- Invalid chat payloads and question-length limits.
- Blocking deletion during indexing.
- Persisted ready indexes and recovery of interrupted jobs after restart.
- Readable client errors for simulated connection failures and timeouts.
- Rejection of invalid embeddings and page-aware chunk splitting.

## Real Ollama integration

```bash
AOMINI_LIVE_TESTS=1 OLLAMA_URL=http://127.0.0.1:11435 python -m unittest tests.test_live -v
```

**1 integration test passed**, using real embeddings and real Qwen generation, without a mocked Ollama response.

Five synthetic PDFs were generated temporarily: basil, noodle, soup, rice, and salad guides. All five reached Ready. Their actual filenames appeared in the collection response, and the original PDF endpoint returned PDF bytes.

Question:

> According to the PDF, which ingredients are listed for Pad Kra Pao?

Observed answer:

> Pad Kra Pao is described in the PDF as a Thai basil stir-fry containing Thai basil, chicken, chili, and garlic [1]. The excerpts do not provide additional ingredients for Pad Kra Pao.

Source `[1]` resolved to **basil-guide.pdf, PDF page 2**, whose synthetic text explicitly listed those ingredients.

Question:

> What is the exact restaurant price of Pad Kra Pao in baht? If missing, say the documents do not provide it.

Observed answer:

> The documents do not provide the exact restaurant price of Pad Kra Pao in baht. [1]

No numeric restaurant price was invented in this check. These observations verify these particular fixtures and questions; they do not establish universal model accuracy.

## Live server and browser checks

- Flask started successfully on loopback with debugging disabled and served the page.
- Used the web page's **browse files** control to select five PDFs together. All five appeared by filename and reached **Ready**.
- Used **My documents** in chat: the page displayed exactly the five uploaded filenames and their statuses.
- Submitted the ingredient question through the chat UI: the page displayed the real Qwen answer, source filename, and `p. 2` link.
- Expanded **View retrieved excerpt**: it displayed the stored page text used for the answer.
- Clicked the source link: the original two-page PDF opened in the browser PDF viewer.
- Inspected the desktop layout in Chrome and the stacked narrow layout in the in-app browser. The narrow page had no horizontal overflow. Desktop Chrome reported no captured warning/error console entries during this check.
- Saved a desktop screenshot separately from the source project. Its five-document collection consists of disposable test fixtures, not bundled application content.

## Additional checks

- JavaScript syntax: `node --check static/script.js` passed.
- Python modules compiled successfully during implementation.
- The source package contains no PDFs, uploaded files, SQLite databases, embedding files, virtual environments, model downloads, or secrets. Runtime and test data were kept outside the delivered project.
- Git ignore rules were checked against representative PDF, database, embedding, virtual-environment, and secret paths before packaging.

## Verification limits

- No real user-provided Thai food PDFs were supplied. Tests used English-language synthetic Thai food notes. Thai filenames were tested; Thai-language PDF font extraction, Thai-language answer quality, and complex menu tables were not validated.
- Windows installation commands are documented but were not executed on Windows.
- Offline, timeout, invalid embedding, and failed-index recovery scenarios were tested with controlled client failures. A full 240-second real model timeout was not deliberately induced.
- No OCR, production deployment, multi-user load test, or comprehensive hallucination benchmark was performed. OCR and production hosting are outside this minimal project.
