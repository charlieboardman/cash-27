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

async function loadInbox() {
  const response = await fetch("/api/documents/inbox");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    inboxList.innerHTML = `<li class="empty-state"><strong>Documents could not be loaded</strong>
      <span>${payload.error?.message || "Try refreshing the page."}</span></li>`;
    return;
  }
  inboxList.innerHTML = "";
  for (const uploaded of payload.data.documents) {
    const item = document.createElement("li");
    item.innerHTML = `<span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>Uploaded document</strong>
      <span>${uploaded.page_count || "?"} page(s) · ${(uploaded.created_at || "").slice(0, 16).replace("T", " ")}</span></span>
      <a class="btn ghost" href="/api/documents/${uploaded.document_id}/stream"
        target="_blank" rel="noopener">Preview</a>`;
    inboxList.appendChild(item);
  }
  if (!payload.data.documents.length) inboxList.innerHTML =
    `<li class="empty-state"><strong>No documents are waiting</strong>
      <span>Uploaded documents appear here until they are reviewed.</span></li>`;
}

loadInbox();
