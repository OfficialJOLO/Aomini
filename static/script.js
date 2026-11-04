"use strict";

const $ = (selector) => document.querySelector(selector);
const conversation = $("#conversation");
const welcome = $("#welcome");
let busy = false;
let lastDocuments = "";
let uploading = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}, timeout = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      ...options, headers: {"X-Aomini-Request": "1", ...options.headers}, signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The request could not be completed.");
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("This request took too long. Check the server and try again. It may still be finishing in the background.");
    if (error instanceof TypeError) throw new Error("Cannot reach Aomini. Check that the Flask server is running, then retry.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function showError(selector, message) {
  $(selector).textContent = message || "";
  $(selector).hidden = !message;
}

function fileLink(document, text, page) {
  const link = element("a", "", text || document.filename);
  link.href = `/api/documents/${encodeURIComponent(document.document_id || document.id)}/file${page ? `#page=${page}` : ""}`;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = "Open original PDF in a new tab";
  return link;
}

function message(role, text, pending = false) {
  welcome.hidden = true;
  const node = element("article", `message ${role}${pending ? " pending" : ""}`);
  const label = element("div", "message-label", role === "user" ? "YOU" : "AOMINI");
  if (pending) label.append(element("span", "thinking-dot"));
  node.append(label, element("div", "message-body", text));
  conversation.append(node);
  conversation.scrollTop = conversation.scrollHeight;
  return node;
}

function addSources(node, sources) {
  if (!sources.length) return;
  const list = element("div", "source-list");
  for (const source of sources) {
    const item = element("div", "source-item");
    item.append(fileLink(source, `[${source.citation}] ${source.filename} · p. ${source.page}`, source.page));
    const details = element("details");
    details.append(element("summary", "", "View retrieved excerpt"), element("blockquote", "", source.text));
    item.append(details);
    list.append(item);
  }
  node.append(list);
}

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question || busy) return;
  busy = true;
  showError("#chat-error", "");
  $("#send").disabled = true;
  $("#clear-chat").disabled = true;
  message("user", question);
  $("#question").value = "";
  const pending = message("assistant", "Looking through your documents…", true);
  const started = Date.now();
  const timer = setInterval(() => {
    const seconds = Math.round((Date.now() - started) / 1000);
    pending.querySelector(".message-body").textContent = `Preparing your answer locally… ${seconds}s\nThe first answer can take a little longer while the models load.`;
  }, 5000);
  try {
    const data = await api("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({message: question})}, 480000);
    pending.remove();
    const node = message("assistant", data.answer);
    addSources(node, data.sources || []);
    if (data.kind === "collection" && data.documents.length) {
      const links = element("div", "collection-links");
      data.documents.forEach((doc) => links.append(fileLink(doc)));
      node.append(links);
    }
    conversation.scrollTop = conversation.scrollHeight;
  } catch (error) {
    pending.remove();
    $("#question").value = question;
    showError("#chat-error", error.message);
  } finally {
    clearInterval(timer);
    busy = false;
    $("#send").disabled = false;
    $("#clear-chat").disabled = false;
    $("#question").focus();
  }
});

$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy) $("#chat-form").requestSubmit();
  }
});
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
  if (busy) return;
  $("#question").value = button.dataset.question;
  $("#chat-form").requestSubmit();
}));
$("#clear-chat").addEventListener("click", () => {
  if (busy) return;
  conversation.querySelectorAll(".message").forEach((node) => node.remove());
  welcome.hidden = false;
  showError("#chat-error", "");
  $("#question").value = "";
  $("#question").focus();
});

function renderDocuments(documents) {
  const serialized = JSON.stringify(documents);
  if (serialized === lastDocuments) return;
  lastDocuments = serialized;
  $("#document-count").textContent = documents.length;
  const ready = documents.filter((doc) => doc.status === "ready").length;
  $("#ready-count").textContent = `${ready} ready`;
  $("#empty-collection").hidden = documents.length > 0;
  $(".welcome-note").textContent = ready ? `${ready} PDF${ready === 1 ? "" : "s"} ready. Ask away.` : "Start by adding a PDF to your collection ↗";
  const list = $("#document-list");
  list.replaceChildren();
  for (const doc of documents) {
    const row = element("div", "document-row");
    row.append(element("span", "pdf-symbol", "PDF"));
    const main = element("div", "document-main");
    main.append(fileLink(doc));
    const active = ["queued", "indexing"].includes(doc.status);
    const statusText = {queued: "Queued", indexing: `Indexing ${doc.indexed_chunks}/${doc.chunks}`, ready: "Ready", error: "Needs attention"}[doc.status];
    const meta = element("div", "doc-meta");
    meta.append(element("span", `doc-status ${doc.status}`, statusText));
    if (doc.pages) meta.append(element("span", "", `· ${doc.pages} p. · ${doc.chunks} chunks`));
    main.append(meta);
    if (doc.error) main.append(element("p", "doc-error", doc.error));
    row.append(main);
    const actions = element("div", "document-actions");
    if (doc.status === "error") {
      const retry = element("button", "retry-button", "Retry");
      retry.type = "button";
      retry.setAttribute("aria-label", `Retry indexing ${doc.filename}`);
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        showError("#upload-error", "");
        try { await api(`/api/documents/${doc.id}/retry`, {method: "POST"}); await refreshDocuments(); }
        catch (error) { showError("#upload-error", error.message); retry.disabled = false; }
      });
      actions.append(retry);
    }
    const remove = element("button", "delete-button", "×");
    remove.type = "button";
    remove.disabled = active;
    remove.title = active ? "Wait for indexing to finish" : "Delete PDF and its index";
    remove.setAttribute("aria-label", `Delete ${doc.filename}`);
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Delete “${doc.filename}” and its searchable text?`)) return;
      remove.disabled = true;
      showError("#upload-error", "");
      try { await api(`/api/documents/${doc.id}`, {method: "DELETE"}); await refreshDocuments(); }
      catch (error) { showError("#upload-error", error.message); remove.disabled = false; }
    });
    actions.append(remove);
    row.append(actions);
    list.append(row);
  }
}

async function refreshDocuments() {
  const data = await api("/api/documents");
  renderDocuments(data.documents);
}

async function uploadFiles(files) {
  if (uploading || !files.length) return;
  uploading = true;
  $("#browse").disabled = true;
  showError("#upload-error", "");
  $("#upload-progress").hidden = false;
  const errors = [];
  for (let index = 0; index < files.length; index++) {
    const file = files[index];
    $("#upload-progress").textContent = `Uploading ${index + 1} of ${files.length}: ${file.name}`;
    if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 20 * 1024 * 1024) {
      errors.push(`${file.name}: choose a PDF up to 20 MB.`);
      continue;
    }
    const data = new FormData();
    data.append("file", file);
    try { await api("/api/documents", {method: "POST", body: data}, 60000); await refreshDocuments(); }
    catch (error) { errors.push(`${file.name}: ${error.message}`); }
  }
  showError("#upload-error", errors.join("\n"));
  $("#upload-progress").hidden = true;
  $("#file-input").value = "";
  $("#browse").disabled = false;
  uploading = false;
}

$("#browse").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (event) => uploadFiles(Array.from(event.target.files)));
const dropZone = $("#drop-zone");
["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {event.preventDefault(); dropZone.classList.add("dragging");}));
["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {event.preventDefault(); dropZone.classList.remove("dragging");}));
dropZone.addEventListener("drop", (event) => uploadFiles(Array.from(event.dataTransfer.files)));
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    $("#status-dot").className = `status-dot ${data.ready ? "ready" : "error"}`;
    $("#status-label").textContent = data.ready ? "Local models ready" : data.online ? "Model setup needed" : "Ollama is offline";
    $("#status-detail").textContent = data.ready ? "Search and answers stay on this computer." : data.message;
  } catch (error) {
    $("#status-dot").className = "status-dot error";
    $("#status-label").textContent = "Server unavailable";
    $("#status-detail").textContent = error.message;
  }
}
$("#refresh-status").addEventListener("click", refreshStatus);
async function pollDocuments() {
  try { await refreshDocuments(); }
  catch (error) { showError("#upload-error", error.message); }
  setTimeout(pollDocuments, 2500);
}
pollDocuments();
refreshStatus();
setInterval(refreshStatus, 30000);
