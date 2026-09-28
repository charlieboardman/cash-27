/* Capture: assemble one ordered document from camera photos or existing files. */
const esc = cashEsc;
const pickerRoot = document.getElementById("capture-document-picker");
const go = document.getElementById("go");
const statusOut = document.getElementById("status");
const inboxList = document.getElementById("inbox");
const documentPicker = createOrderedDocumentPicker(pickerRoot, {
  onError: message => { statusOut.textContent = message; },
});

pickerRoot.addEventListener("document-picker-change", event => {
  go.disabled = event.detail.count === 0;
  if (event.detail.count) {
    statusOut.textContent = "";
    document.getElementById("capture-saved").hidden = true;
  }
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
    await cashApi("/api/documents/capture-merge", { method: "POST", formData: form });
    const pages = documentPicker.count;
    documentPicker.clear();
    statusOut.textContent = "";
    const saved = document.getElementById("capture-saved");
    cashNotice(saved, `Saved to the inbox (${pages} ${pages === 1 ? "page" : "pages"}). ` +
      "Take the next photo when you are ready.", "success", { fade: false });
    saved.scrollIntoView({ block: "nearest" });
    await loadInbox();
  } catch (error) {
    statusOut.textContent = error.message;
  } finally {
    go.disabled = documentPicker.count === 0;
  }
});

let inboxFingerprint = null;
let inboxLoading = false;
let inboxFailures = 0;
const offlineNotice = document.getElementById("inbox-offline");
async function loadInbox() {
  let documents;
  try {
    ({ documents } = await cashApi("/api/documents/inbox"));
  } catch (error) {
    inboxFailures += 1;
    // Keep the last list through a brief outage; say so after repeated failures.
    if (inboxFingerprint === null) {
      inboxList.innerHTML = `<li class="empty-state"><strong>Documents could not be loaded</strong>
        <span>${esc(error.message)}</span></li>`;
    } else if (inboxFailures >= 3) offlineNotice.hidden = false;
    return;
  }
  inboxFailures = 0;
  offlineNotice.hidden = true;
  const fingerprint = JSON.stringify(documents);
  if (fingerprint === inboxFingerprint) return;
  inboxFingerprint = fingerprint;
  window.cashRefreshNavCounts?.();
  inboxList.innerHTML = "";
  for (const uploaded of documents) {
    const item = document.createElement("li");
    item.innerHTML = `<span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>${esc(uploaded.display_name ||
        `Document ${uploaded.document_id.slice(0, 8)}`)}</strong>
      <span>${uploaded.page_count || "?"} page(s) · ${esc(uploaded.created_at ? new Date(uploaded.created_at).toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "")}</span>
      ${cashDocumentProcessing(uploaded)}
      ${uploaded.qr ? `<span>${esc(uploaded.qr.message)}</span>` : ""}</span>
      ${uploaded.qr?.review_url ? `<a class="btn" href="${esc(uploaded.qr.review_url)}">Review ${esc(uploaded.qr.detected_id)}</a>` : ""}
      <a class="btn" href="/review?document=${encodeURIComponent(uploaded.document_id)}">${cashDocumentPending(uploaded) ? "View processing document" : "Attach to transaction…"}</a>
      <a class="btn ghost" href="/documents/${encodeURIComponent(uploaded.document_id)}/preview">Preview</a>`;
    inboxList.appendChild(item);
  }
  if (!documents.length) inboxList.innerHTML =
    `<li class="empty-state"><strong>The inbox is empty</strong></li>`;
}

loadInbox().catch(() => {});
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadInbox().catch(() => {}); });
window.setInterval(async () => {
  if (document.hidden || inboxLoading) return;
  inboxLoading = true;
  try { await loadInbox(); } catch (_) { /* Retain the last successful inbox during network loss. */ }
  finally { inboxLoading = false; }
}, 2000);
