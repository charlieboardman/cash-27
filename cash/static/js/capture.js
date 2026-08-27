/* Capture: build one ordered card from files, save as one document. */
const drop = document.getElementById("drop");
const picker = document.getElementById("picker");
const pages = document.getElementById("pages");
const go = document.getElementById("go");
const statusOut = document.getElementById("status");
const inboxList = document.getElementById("inbox");
let files = [];

async function csrf() {
  return (await (await fetch("/api/session")).json()).data.csrf;
}

function render() {
  pages.innerHTML = "";
  files.forEach((f, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="page-name">${f.name}</span>
      <span class="toolbar">
        <button data-a="up" data-i="${i}" ${i === 0 ? "disabled" : ""}>↑</button>
        <button data-a="down" data-i="${i}"
          ${i === files.length - 1 ? "disabled" : ""}>↓</button>
        <button data-a="del" data-i="${i}">✕</button>
      </span>`;
    pages.appendChild(li);
  });
  go.disabled = files.length === 0;
}

pages.addEventListener("click", e => {
  const a = e.target.dataset.a, i = +e.target.dataset.i;
  if (!a) return;
  if (a === "del") files.splice(i, 1);
  if (a === "up") [files[i - 1], files[i]] = [files[i], files[i - 1]];
  if (a === "down") [files[i + 1], files[i]] = [files[i], files[i + 1]];
  render();
});

function addFiles(list) {
  files.push(...list);
  render();
}

drop.addEventListener("dragover", e => e.preventDefault());
drop.addEventListener("drop", e => {
  e.preventDefault();
  addFiles(e.dataTransfer.files);
});
picker.addEventListener("change", () => addFiles(picker.files));

go.addEventListener("click", async () => {
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  statusOut.textContent = "Uploading…";
  const r = await fetch("/api/documents/capture-merge", {
    method: "POST",
    headers: { "X-CSRF": await csrf() },
    body: fd,
  });
  const j = await r.json();
  if (!j.ok) { statusOut.textContent = j.error.message; return; }
  statusOut.textContent = "Document uploaded.";
  files = [];
  render();
  loadInbox();
});

async function loadInbox() {
  const { data } = await (await fetch("/api/documents/inbox")).json();
  inboxList.innerHTML = "";
  for (const d of data.documents) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>Uploaded document</strong>
      <span>${d.page_count || "?"} page(s) · ${(d.created_at || "").slice(0, 16).replace("T", " ")}</span></span>
      <a class="btn ghost" href="/api/documents/${d.document_id}/stream"
        target="_blank" rel="noopener">Preview</a>`;
    inboxList.appendChild(li);
  }
  if (!data.documents.length) inboxList.innerHTML =
    `<li class="empty-state"><strong>No documents are waiting</strong>
      <span>Uploaded documents appear here until they are reviewed.</span></li>`;
}

loadInbox();
