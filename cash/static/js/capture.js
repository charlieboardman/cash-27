/* Capture: assemble one ordered document from camera photos or existing files. */
const pickerRoot = document.getElementById("capture-document-picker");
const go = document.getElementById("go");
const statusOut = document.getElementById("status");
const inboxList = document.getElementById("inbox");
const documentPicker = createOrderedDocumentPicker(pickerRoot, {
  onError: message => { statusOut.textContent = message; },
});

async function csrf() {
  return (await (await fetch("/api/session")).json()).data.csrf;
}

pickerRoot.addEventListener("document-picker-change", event => {
  go.disabled = event.detail.count === 0;
  if (event.detail.count) statusOut.textContent = "";
});

go.addEventListener("click", async () => {
  go.disabled = true;
  try {
    const prepared = await documentPicker.preparedFiles((page, total) => {
      statusOut.textContent = `Preparing page ${page} of ${total}…`;
    });
    const form = new FormData();
    prepared.forEach(file => form.append("files", file, file.name));
    statusOut.textContent = "Uploading document…";
    const response = await fetch("/api/documents/capture-merge", {
      method: "POST",
      headers: { "X-CSRF": await csrf() },
      body: form,
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error?.message || "Upload failed");
    }
    documentPicker.clear();
    statusOut.textContent = "Document uploaded.";
    await loadInbox();
  } catch (error) {
    statusOut.textContent = error.message;
  } finally {
    go.disabled = documentPicker.count === 0;
  }
});

let inboxFingerprint = null;
let inboxLoading = false;
async function loadInbox() {
  const response = await fetch("/api/documents/inbox");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    inboxList.innerHTML = `<li class="empty-state"><strong>Documents could not be loaded</strong>
      <span>${payload.error?.message || "Try refreshing the page."}</span></li>`;
    return;
  }
  const fingerprint = JSON.stringify(payload.data.documents);
  if (fingerprint === inboxFingerprint) return;
  inboxFingerprint = fingerprint;
  inboxList.innerHTML = "";
  for (const uploaded of payload.data.documents) {
    const item = document.createElement("li");
    item.innerHTML = `<span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>${esc(uploaded.display_name ||
        `Document ${uploaded.document_id.slice(0, 8)}`)}</strong>
      <span>${uploaded.page_count || "?"} page(s) · ${(uploaded.created_at || "").slice(0, 16).replace("T", " ")}</span>
      ${cashDocumentProcessing(uploaded)}
      ${uploaded.qr ? `<span>${esc(uploaded.qr.message)}</span>` : ""}</span>
      ${uploaded.qr?.review_url ? `<a class="btn" href="${esc(uploaded.qr.review_url)}">Review ${esc(uploaded.qr.detected_id)}</a>` : ""}
      <a class="btn" href="/review?document=${encodeURIComponent(uploaded.document_id)}">${cashDocumentPending(uploaded) ? "View processing document" : "Attach to transaction…"}</a>
      <a class="btn ghost" href="${esc(cashDocumentStream(uploaded))}"
        target="_blank" rel="noopener">Preview</a>`;
    inboxList.appendChild(item);
  }
  if (!payload.data.documents.length) inboxList.innerHTML =
    `<li class="empty-state"><strong>No documents are waiting</strong>
      <span>Uploaded documents appear here until they are reviewed.</span></li>`;
}

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

loadInbox().catch(() => {});
window.setInterval(async () => {
  if (document.hidden || inboxLoading) return;
  inboxLoading = true;
  try { await loadInbox(); } catch (_) { /* Retain the last successful inbox during network loss. */ }
  finally { inboxLoading = false; }
}, 2000);
