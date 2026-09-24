/* Spreadsheet review: a document rail, stable transaction rows, and a
   selected-record workspace.

   Deliberately few paths: one document is selected in the rail; a slot inside
   an open record attaches the selected document (or uploads-and-attaches in
   one step). Filing paper (vouchers, addenda) commits immediately with an
   Undo button; receipts stage with the expense they will support. */

const els = {
  list: document.querySelector("#worklist tbody"),
  detail: document.getElementById("detail"),
  filter: document.getElementById("work-filter"),
  summary: document.getElementById("queue-summary"),
  empty: document.getElementById("empty-worklist"),
  message: document.getElementById("review-message"),
  undo: document.getElementById("undo-toast"),
  inboxCount: document.getElementById("inbox-count"),
  evidenceHeading: document.getElementById("evidence-heading"),
  evidenceList: document.getElementById("evidence-list"),
  documentPreview: document.getElementById("document-preview"),
  drop: document.getElementById("evidence-drop"),
  picker: document.getElementById("evidence-picker"),
  browse: document.getElementById("browse-evidence"),
  refreshInbox: document.getElementById("refresh-inbox"),
  layout: document.getElementById("review-sheet-layout"),
  workHeading: document.getElementById("work-heading"),
  evidencePane: document.getElementById("document-rail"),
  mobileInboxCount: document.getElementById("mobile-inbox-count"),
  openDocuments: document.getElementById("open-documents"),
  drawerDocuments: document.getElementById("drawer-documents"),
  closeDocuments: document.getElementById("close-documents"),
  drawer: document.getElementById("review-drawer"),
  drawerIdentity: document.getElementById("review-drawer-identity"),
  closeDrawer: document.getElementById("close-review-drawer"),
  tabs: [...document.querySelectorAll("[data-review-mode]")],
};

const state = {
  csrf: null,
  items: [],
  inbox: [],
  options: { expense_accounts: [], classes: [], cash_accounts: [], projects: [] },
  selectedKey: null,
  selectedItem: null,
  detail: null,
  activeDocument: null,
  primaryDocument: null,
  stagedReceipts: [],
  directDraft: null,
  useVoucherAsReceipt: false,
  pendingSignoutFiling: null,
  uploadIntent: null,
  claimIndex: 0,
  returnNotes: {},
  dirty: false,
  documentActionType: null,
  documentActionDirty: false,
  documentAttachSearch: false,
  documentPickTarget: null,
  mode: "transactions",
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function decimal(minor) {
  return ((Number(minor) || 0) / 100).toFixed(2);
}

const workflowStatus = cashLabels.status;

function displayDateTime(value) {
  return value ? new Date(value).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }) : "Unknown time";
}

function localDate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
}

function documentName(document) {
  if (!document) return "Document";
  return document.display_name || `Document ${document.document_id.slice(0, 8)}`;
}

function documentMeta(document) {
  const pages = `${document.page_count || "?"} page${document.page_count === 1 ? "" : "s"}`;
  return `${pages} · Uploaded ${displayDateTime(document.created_at)}${document.qr?.message ? ` · ${document.qr.message}` : ""}`;
}

function detailDocument(documentId) {
  if (!documentId) return null;
  return state.inbox.find(document => document.document_id === documentId) ||
    state.detail?.documents?.find(document =>
      document.document_id === documentId) || null;
}

function keyFor(item) {
  return `${item.kind}:${item.id}`;
}

function drawerRecord(item) {
  return item.kind === "package" ? item.secondary : item.id;
}

function renderDrawerIdentity(item) {
  if (!item) {
    els.drawerIdentity.innerHTML = "";
    return;
  }
  const amounts = [item.usd != null ? `USD ${cashMoney(item.usd)}` : "",
    item.mxn != null ? `MXN ${cashMoney(item.mxn)}` : ""].filter(Boolean);
  els.drawerIdentity.innerHTML = `<p class="eyebrow">${esc(item.eyebrow)}</p>
    <h2 id="review-drawer-title">${esc(drawerRecord(item))}</h2>
    <p>${esc(item.label || "")}${item.secondary ? ` · ${esc(item.secondary)}` : ""}</p>
    ${amounts.length ? `<div class="review-drawer-amounts">${amounts.map(amount =>
      `<strong>${esc(amount)}</strong>`).join("")}</div>` : ""}
    ${(item.badges || []).length ? `<div class="badge-row">${item.badges.map(badge =>
      `<span class="badge">${esc(badge)}</span>`).join("")}</div>` : ""}`;
}

function setWorkspaceOpen(open) {
  els.drawer.hidden = !open;
  els.layout.classList.toggle("workspace-open", open);
  els.drawer.classList.toggle("package-drawer",
    open && state.selectedItem?.kind === "package");
  renderDrawerIdentity(open ? state.selectedItem : null);
}

function compactReviewLayout() {
  return window.matchMedia("(max-width: 1200px)").matches;
}

function updateEvidenceHeading() {
  if (state.documentPickTarget === "primary") {
    els.evidenceHeading.textContent = state.selectedItem?.kind === "addendum"
      ? "Choose an addendum" : state.selectedItem?.kind === "exchange"
      ? "Choose an exchange receipt" : state.selectedItem?.kind === "withdrawal"
      ? "Choose a withdrawal receipt" : "Choose a voucher";
  } else if (state.documentPickTarget === "support") {
    els.evidenceHeading.textContent = "Choose a receipt";
  } else {
    els.evidenceHeading.textContent = state.mode === "submissions"
      ? "Receipt document" : "Document inbox";
  }
}

function openDocumentRail(target = null) {
  if (target && !leaveDocumentEntry()) return;
  state.documentPickTarget = target;
  if (target) {
    state.activeDocument = null;
    renderEvidenceList();
  }
  updateEvidenceHeading();
  els.evidencePane.classList.add("mobile-open");
  els.evidencePane.setAttribute("aria-hidden", "false");
  if (compactReviewLayout()) els.closeDocuments.focus();
}

function closeDocumentRail() {
  state.documentPickTarget = null;
  updateEvidenceHeading();
  els.evidencePane.classList.remove("mobile-open");
  els.evidencePane.setAttribute("aria-hidden",
    compactReviewLayout() ? "true" : "false");
  if (compactReviewLayout()) {
    (state.selectedItem ? els.drawerDocuments : els.openDocuments).focus();
  }
}

function syncResponsivePanes() {
  if (!compactReviewLayout()) {
    state.documentPickTarget = null;
    els.evidencePane.classList.remove("mobile-open");
    els.evidencePane.setAttribute("aria-hidden", "false");
    updateEvidenceHeading();
  } else if (!els.evidencePane.classList.contains("mobile-open")) {
    els.evidencePane.setAttribute("aria-hidden", "true");
  }
}

function showMessage(text, tone = "success") {
  els.message.hidden = false;
  els.message.className = `notice ${tone}`;
  els.message.textContent = text;
  window.clearTimeout(showMessage.timer);
  showMessage.timer = window.setTimeout(() => { els.message.hidden = true; }, 7000);
}

function showUndo(text, onUndo) {
  if (!onUndo) { showMessage(text); return; }
  els.message.hidden = true;
  els.undo.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = text;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Undo";
  button.addEventListener("click", () => {
    els.undo.hidden = true;
    window.clearTimeout(showUndo.timer);
    onUndo();
  });
  els.undo.append(label, button);
  els.undo.hidden = false;
  window.clearTimeout(showUndo.timer);
  showUndo.timer = window.setTimeout(() => { els.undo.hidden = true; }, 8000);
}

async function csrf() {
  if (state.csrf) return state.csrf;
  const response = await fetch("/api/session");
  const payload = await response.json();
  state.csrf = payload.data.csrf;
  return state.csrf;
}

async function request(url, { method = "GET", body, formData } = {}) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-CSRF"] = await csrf();
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  } else if (formData) {
    options.body = formData;
  }
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.payload = payload;
    throw error;
  }
  return payload.data;
}

function currentFilteredItems() {
  const needle = els.filter.value.trim().toLowerCase();
  return state.items.filter(item => {
    if (state.mode === "transactions" && item.kind === "package") return false;
    if (state.mode === "submissions" && item.kind !== "package") return false;
    if (!needle) return true;
    return [item.id, item.label, item.secondary, item.eyebrow,
            ...(item.badges || [])].join(" ").toLowerCase().includes(needle);
  });
}

/* ---- work list ---------------------------------------------------------- */

function renderWorklist() {
  const visible = currentFilteredItems();
  els.list.innerHTML = "";
  visible.forEach(item => {
    const row = document.createElement("tr");
    const selected = keyFor(item) === state.selectedKey;
    row.className = `review-summary-row${selected ? " selected" : ""}`;
    row.tabIndex = 0;
    row.setAttribute("aria-expanded", String(selected));
    row.setAttribute("aria-selected", String(selected));
    const record = item.kind === "package" ? item.secondary : item.id;
    const details = item.kind === "package" ? item.label :
      `${item.label || item.id}${item.secondary ? ` · ${item.secondary}` : ""}`;
    row.innerHTML = `<td class="txn-id">${esc(record)}</td>
      <td>${esc(item.date || "—")}</td><td>${esc(item.eyebrow)}</td>
      <td class="review-record-details">${esc(details)}</td>
      <td class="num receipt-value-cell">${item.receipt == null ? "—" : esc(item.receipt)}</td>
      <td class="num">${item.usd == null ? "—" : cashMoney(item.usd)}</td>
      <td class="num">${item.mxn == null ? "—" : cashMoney(item.mxn)}</td>
      <td><span class="badge-row">${(item.badges || []).map(b =>
        `<span class="badge">${esc(b)}</span>`).join("")}</span></td>`;
    row.addEventListener("click", () => selectItem(item));
    els.list.appendChild(row);
  });
  setWorkspaceOpen(Boolean(state.selectedItem));
  els.empty.hidden = visible.length !== 0;
  const modeCount = state.items.filter(item => state.mode === "submissions"
    ? item.kind === "package" : item.kind !== "package").length;
  const noun = state.mode === "submissions" ? "submission" : "transaction";
  els.summary.textContent = `${modeCount} ${noun}${modeCount === 1 ? "" : "s"} to review` +
    (visible.length !== modeCount ? ` · ${visible.length} shown` : "");
}

async function loadWorklist() {
  const data = await request("/api/review/worklist");
  state.items = data.items;
  renderWorklist();
}

async function loadOptions() {
  state.options = await request("/api/review/options");
  renderProjectOptions();
}

let inboxFingerprint = null;
let inboxLoading = false;
async function loadInbox({ background = false } = {}) {
  if (background && (inboxLoading || document.hidden || document.querySelector(".code-picker"))) return;
  inboxLoading = true;
  try {
    const data = await request("/api/documents/inbox");
    const fingerprint = JSON.stringify(data.documents);
    if (background && fingerprint === inboxFingerprint) return;
    inboxFingerprint = fingerprint;
    const focused = document.activeElement;
    const focusId = focused?.id;
    const focusName = focused?.name;
    const focusIndex = focusName ? [...document.querySelectorAll(`[name="${CSS.escape(focusName)}"]`)].indexOf(focused) : -1;
    const selection = [focused?.selectionStart, focused?.selectionEnd];
    const scrollTop = els.evidencePane.scrollTop;
    const drawerScroll = els.drawer.scrollTop;
    const openDetails = [...els.detail.querySelectorAll("details")].map(item => item.open);
    state.inbox = [...data.documents].reverse().map(document => ({
      ...document,
      stream_url: `/api/documents/${document.document_id}/stream?v=${document.sha256 || ""}`,
    }));
    if (state.activeDocument) {
      const refreshed = state.inbox.find(d =>
        d.document_id === state.activeDocument.document_id);
      // A filed document stays on screen with its fresh state; only a
      // discarded/missing one is dropped from the rail.
      state.activeDocument = refreshed ||
        (state.activeDocument.state === "filed"
          ? state.activeDocument : null);
    }
    if (state.primaryDocument) {
      state.primaryDocument = state.inbox.find(document =>
        document.document_id === state.primaryDocument.document_id) || null;
    }
    state.stagedReceipts = state.stagedReceipts.map(staged =>
      state.inbox.find(document => document.document_id === staged.document_id))
      .filter(Boolean);
    renderDocumentRail();
    if (background) {
      els.detail.querySelectorAll("details").forEach((item, index) => { item.open = Boolean(openDetails[index]); });
      const restored = focusId ? document.getElementById(focusId) : focusIndex >= 0
        ? document.querySelectorAll(`[name="${CSS.escape(focusName)}"]`)[focusIndex] : null;
      if (restored) {
        restored.focus({ preventScroll: true });
        if (selection[0] != null && ["text", "search", "tel", "url", "password", "textarea"].includes(restored.type)) {
          restored.setSelectionRange(...selection);
        }
      }
      els.evidencePane.scrollTop = scrollTop;
      els.drawer.scrollTop = drawerScroll;
    }
  } finally { inboxLoading = false; }
}

/* ---- evidence rail ------------------------------------------------------- */

function documentStatus(document) {
  if (cashDocumentPending(document)) return "Document processing…";
  if (document.processing?.status === "failed") return "Processing failed · manual review";
  if (document.qr?.status === "matched") return `Suggested: ${document.qr.detected_id}`;
  if (document.state === "filed") return "Filed";
  return "No match";
}

function leaveDocumentEntry() {
  if (state.documentActionDirty && !window.confirm("Discard this unfinished entry?")) return false;
  state.documentActionType = null;
  state.documentActionDirty = false;
  state.documentAttachSearch = false;
  return true;
}

function openDocument(document) {
  if (!leaveDocumentEntry()) return;
  state.activeDocument = document;
  renderDocumentRail();
}

function renderEvidenceList() {
  const searchValue = document.getElementById("document-transaction-search")?.value || "";
  const existingForm = document.getElementById("document-entry-form");
  const entryDraft = existingForm && state.documentActionDirty
    && existingForm.dataset.documentId === state.activeDocument?.document_id
    && existingForm.elements.entry_type.value === state.documentActionType
    ? new FormData(existingForm) : null;
  els.inboxCount.textContent = state.inbox.length;
  els.mobileInboxCount.textContent = state.inbox.length;
  els.evidenceList.innerHTML = "";
  els.documentPreview.innerHTML = "";
  els.evidenceList.hidden = Boolean(state.activeDocument);
  els.documentPreview.hidden = !state.activeDocument;
  els.evidencePane.querySelector(".inbox-heading").hidden = Boolean(state.activeDocument);
  if (state.activeDocument) {
    const available = state.inbox.some(item => item.document_id === state.activeDocument.document_id);
    els.documentPreview.innerHTML = inlineDocumentViewer(state.activeDocument, available);
    wireInlineDocumentViewer(els.documentPreview, state.activeDocument);
    const search = document.getElementById("document-transaction-search");
    if (search) { search.value = searchValue; renderTransactionMatches(); }
    const form = document.getElementById("document-entry-form");
    if (form && entryDraft) {
      for (const [name, value] of entryDraft) form.elements.namedItem(name).value = value;
    }
    return;
  }
  const documents = state.mode === "submissions" ? [] : state.inbox;
  if (!documents.length) {
    els.evidenceList.innerHTML = `<li class="empty-state compact">
      <strong>${state.mode === "submissions" ? "Select a receipt" : "The inbox is empty"}</strong></li>`;
    return;
  }
  for (const document of documents) {
    const li = window.document.createElement("li");
    li.dataset.documentId = document.document_id;
    li.innerHTML = `<button type="button" class="evidence-card" draggable="true">
      <span class="document-thumbnail"><span>PDF</span><img loading="lazy" alt=""
        src="/api/documents/${esc(document.document_id)}/thumbnail?v=${esc(document.sha256 || "")}"></span>
      <span class="evidence-copy"><strong>${esc(documentName(document))}</strong>
        ${cashDocumentProcessing(document) || `<span>${esc(documentStatus(document))}</span>`}
        <span>${document.page_count || "?"} page(s) · ${esc(displayDateTime(document.created_at))}</span>
      </span></button>`;
    li.querySelector("img").addEventListener("error", event => { event.target.hidden = true; });
    const card = li.querySelector(".evidence-card");
    card.addEventListener("click", () => openDocument(document));
    card.addEventListener("dragstart", event => {
      event.dataTransfer.setData("text/plain", document.document_id);
      event.dataTransfer.effectAllowed = "copy";
    });
    els.evidenceList.appendChild(li);
  }
}

async function deleteInboxDocument(document, button) {
  if (!window.confirm(`Delete ${documentName(document)} from the review queue?`)) return;
  button.disabled = true;
  try {
    await request(`/api/documents/${document.document_id}/discard`, { method: "POST" });
    captureDirectDraft();
    const id = document.document_id;
    const wasStaged = state.primaryDocument?.document_id === id ||
      state.pendingSignoutFiling?.document_id === id ||
      state.stagedReceipts.some(item => item.document_id === id);
    state.inbox = state.inbox.filter(item => item.document_id !== id);
    if (state.activeDocument?.document_id === id) {
      state.activeDocument = null;
      state.documentActionType = null;
      state.documentActionDirty = false;
    }
    if (state.primaryDocument?.document_id === id) {
      state.primaryDocument = null;
      state.useVoucherAsReceipt = false;
    }
    if (state.pendingSignoutFiling?.document_id === id) state.pendingSignoutFiling = null;
    state.stagedReceipts = state.stagedReceipts.filter(item => item.document_id !== id);
    if (wasStaged) markDirty();
    renderDocumentRail();
    showMessage(`${documentName(document)} deleted from the review queue.`);
  } catch (error) {
    showMessage(error.message, "danger");
  } finally {
    button.disabled = false;
  }
}

function renderDocumentRail() {
  els.layout.classList.toggle("document-reading", Boolean(state.activeDocument));
  renderEvidenceList();
  // Slots inside an open record offer the opened document; re-render so a
  // fresh selection is offered immediately. Commits clear selectedItem first
  // and trigger their own refresh, so no loop.
  if (state.selectedItem && !state.detailRendering &&
      state.mode !== "submissions") {
    captureDirectDraft();
    renderDetail();
  }
}

function attachmentActions() {
  const item = state.selectedItem;
  if (!item || !state.detail || item.kind === "package") return "";
  const reference = ["withdrawal", "exchange"].includes(item.kind)
    ? `${item.kind} #${item.id}` : item.id;
  const primaryLabel = ["withdrawal", "exchange"].includes(item.kind)
    ? `Attach receipt to ${reference}` : item.kind === "addendum"
    ? `Attach signed addendum to ${reference}` : `Attach voucher to ${reference}`;
  return `<button type="button" class="btn primary attach-selected-document" data-target="primary">${esc(primaryLabel)}</button>
    ${item.kind === "direct_expense" ? `<button type="button" class="btn ghost attach-selected-document" data-target="support">Attach receipt to ${esc(reference)}</button>` : ""}`;
}

function inlineDocumentViewer(document, isInbox) {
  const url = cashDocumentStream(document);
  const index = state.inbox.findIndex(item => item.document_id === document.document_id);
  return `<nav class="document-preview-nav" aria-label="Document navigation">
      <button type="button" class="text-button" id="back-to-documents">← Back to documents</button>
      <span><button type="button" class="btn ghost" id="previous-document" aria-label="Previous document" ${index <= 0 ? "disabled" : ""}>←</button>
      <button type="button" class="btn ghost" id="next-document" aria-label="Next document" ${index < 0 || index >= state.inbox.length - 1 ? "disabled" : ""}>→</button></span>
    </nav>
    <div class="document-preview-title"><strong>${esc(documentName(document))}</strong>
      ${cashDocumentProcessing(document) || `<span>${esc(documentStatus(document))}</span>`}
      <div class="document-preview-tools"><a href="${esc(url)}" target="_blank" rel="noopener">Open full size</a>
      ${isInbox ? `<details class="document-overflow"><summary aria-label="Document actions">•••</summary>
        <button type="button" class="text-button danger delete-inbox-document" aria-label="Delete ${esc(documentName(document))}">Delete document</button></details>` : ""}</div>
    </div>
    <div class="document-viewer inline-document-viewer">
      <iframe src="${esc(url)}#view=FitH" title="${esc(documentName(document))} preview"></iframe>
    </div>
    ${isInbox && !cashDocumentPending(document) && state.mode === "transactions" ? `<div class="document-attachment-actions">
      ${attachmentActions()}
      <button type="button" class="btn ${state.selectedItem ? "ghost" : "primary"}" id="attach-to-transaction">${state.selectedItem ? "Choose another transaction…" : "Attach to transaction…"}</button>
      <section id="document-transaction-picker" ${state.documentAttachSearch ? "" : "hidden"}>
        <label><span>Find an existing transaction</span><input type="search" id="document-transaction-search" placeholder="Date, type, amount, person, or record…" autocomplete="off"></label>
        <div id="document-transaction-results"></div>
      </section>
      <details class="document-create-menu" ${state.documentActionType ? "open" : ""}><summary>Create new transaction…</summary>
        <div class="document-create-options"><button type="button" class="text-button record-document-entry" data-entry-type="withdrawal">Create new bank withdrawal</button>
        <button type="button" class="text-button record-document-entry" data-entry-type="exchange">Create new currency exchange</button></div>
        ${state.documentActionType ? documentEntryForm(document, state.documentActionType) : ""}
      </details>
    </div>` : ""}`;
}

function renderTransactionMatches() {
  const input = document.getElementById("document-transaction-search");
  if (!input) return;
  const tokens = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const candidates = state.items.filter(item => item.kind !== "package" && tokens.every(token =>
    [item.id, item.kind, item.eyebrow, item.label, item.secondary, item.date, item.usd, item.mxn]
      .join(" ").toLowerCase().includes(token)));
  const list = document.getElementById("document-transaction-results");
  list.innerHTML = candidates.length ? "" : '<p class="muted">No matching transactions awaiting documents.</p>';
  for (const item of candidates) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "document-transaction-result";
    button.innerHTML = `<strong>${esc(item.eyebrow)} · ${esc(item.id)}</strong>
      <span>${esc(item.date)} · USD ${cashMoney(item.usd)} · MXN ${cashMoney(item.mxn)}</span>
      <span>${esc(item.label)}${item.secondary ? ` · ${esc(item.secondary)}` : ""}</span>`;
    button.addEventListener("click", async () => {
      const selectedDocument = state.activeDocument;
      if (!leaveDocumentEntry()) return;
      if (state.selectedKey !== keyFor(item)) await selectItem(item);
      if (state.selectedKey !== keyFor(item) || !state.detail) return;
      // Choosing a transaction does not itself file paper or move money.
      state.activeDocument = selectedDocument;
      state.documentAttachSearch = false;
      renderDocumentRail();
    });
    list.appendChild(button);
  }
}

function wireInlineDocumentViewer(container, document) {
  container.querySelector("#back-to-documents").addEventListener("click", () => {
    if (!leaveDocumentEntry()) return;
    state.activeDocument = null;
    renderDocumentRail();
  });
  const index = state.inbox.findIndex(item => item.document_id === document.document_id);
  container.querySelector("#previous-document").addEventListener("click", () => openDocument(state.inbox[index - 1]));
  container.querySelector("#next-document").addEventListener("click", () => openDocument(state.inbox[index + 1]));
  container.querySelector(".delete-inbox-document")?.addEventListener("click", event =>
    deleteInboxDocument(document, event.currentTarget));
  container.querySelector("#attach-to-transaction")?.addEventListener("click", () => {
    if (!leaveDocumentEntry()) return;
    state.documentAttachSearch = true;
    renderEvidenceList();
    window.document.getElementById("document-transaction-search").focus();
  });
  container.querySelector("#document-transaction-search")?.addEventListener("input", renderTransactionMatches);
  container.querySelectorAll(".attach-selected-document").forEach(button => {
    button.addEventListener("click", async () => {
      if (!leaveDocumentEntry()) return;
      button.disabled = true;
      await applyDocument(button.dataset.target, document);
      if (compactReviewLayout()) closeDocumentRail();
      renderDocumentRail();
    });
  });
  container.querySelectorAll(".record-document-entry").forEach(button => {
    button.addEventListener("click", () => {
      if (!leaveDocumentEntry()) return;
      state.documentActionType = button.dataset.entryType;
      renderDocumentRail();
    });
  });
  if (state.documentAttachSearch) renderTransactionMatches();
  if (state.documentActionType) wireDocumentEntryForm(document);
}

/* ---- document upload & staging ------------------------------------------- */

function stageDocuments(documents) {
  captureDirectDraft();
  const list = Array.isArray(documents) ? documents.filter(Boolean) : [documents];
  const added = [];
  for (const document of list) {
    if (!document) continue;
    if (state.primaryDocument?.document_id === document.document_id) {
      showMessage("The voucher and a separate receipt must be different documents.",
                  "warning");
      continue;
    }
    if (state.stagedReceipts.some(item =>
        item.document_id === document.document_id)) continue;
    state.stagedReceipts.push(document);
    added.push(document);
  }
  if (added.length) {
    state.useVoucherAsReceipt = false;
    markDirty();
    const names = added.map(documentName).join(", ");
    showMessage(added.some(cashDocumentPending) ? "Document processing… You can keep coding while it finishes." : `Added ${names}.`);
  } else if (list.length) {
    showMessage("That document is already staged here.", "warning");
  }
  if (state.selectedItem?.kind === "direct_expense") renderDirectExpense();
  blockProcessingCommits();
}

function unstageReceipt(documentId) {
  captureDirectDraft();
  state.stagedReceipts = state.stagedReceipts.filter(document =>
    document.document_id !== documentId);
  markDirty();
  renderDirectExpense();
}

async function uploadToInbox(file) {
  const formData = new FormData();
  formData.append("files", file, file.name);
  const created = await request("/api/documents/capture-merge", {
    method: "POST", formData });
  return created;
}

async function uploadAndStage(file) {
  showMessage("Uploading document…");
  try {
    const created = await uploadToInbox(file);
    await loadInbox();
    const document = state.inbox.find(item =>
      item.document_id === created.document_id);
    if (!document) throw new Error("Uploaded document was not found");
    state.activeDocument = document;
    stageDocuments(document);
    renderDocumentRail();
  } catch (error) {
    showMessage(error.message, "danger");
  }
}

async function uploadForSlot(file, target) {
  showMessage("Uploading document…");
  try {
    const created = await uploadToInbox(file);
    await loadInbox();
    const uploaded = state.inbox.find(item =>
      item.document_id === created.document_id);
    if (!uploaded) throw new Error("Uploaded document was not found");
    state.activeDocument = uploaded;
    await applyDocument(target, uploaded, { allowProcessing: true });
    renderDocumentRail();
  } catch (error) {
    showMessage(error.message, "danger");
  }
}

async function uploadToRail(file) {
  showMessage("Uploading document…");
  try {
    const created = await uploadToInbox(file);
    await loadInbox();
    const uploaded = state.inbox.find(item =>
      item.document_id === created.document_id);
    if (!uploaded) throw new Error("Uploaded document was not found");
    state.activeDocument = uploaded;
    renderDocumentRail();
    showMessage("Document saved to the inbox and selected.");
  } catch (error) {
    showMessage(error.message, "danger");
  }
}

/* ---- row selection -------------------------------------------------------- */

function markDirty() {
  state.dirty = true;
}

async function selectItem(item, { force = false } = {}) {
  const itemKey = keyFor(item);
  if (!force && state.dirty &&
      !window.confirm("Discard unsaved review changes and open another item?")) return;
  if (!force && !leaveDocumentEntry()) return;
  state.stagedReceipts = [];
  state.primaryDocument = null;
  state.directDraft = null;
  state.useVoucherAsReceipt = false;
  state.pendingSignoutFiling = null;
  if (!force && state.selectedKey === itemKey) {
    state.dirty = false;
    state.selectedItem = null;
    state.selectedKey = null;
    state.detail = null;
    renderWorklist();
    renderEvidenceList();
    return;
  }
  state.dirty = false;
  state.selectedItem = item;
  state.selectedKey = itemKey;
  state.detail = null;
  state.claimIndex = 0;
  state.returnNotes = {};
  renderWorklist();
  els.detail.innerHTML = `<div class="loading-state">Loading item…</div>`;
  try {
    const detail = await request(`/api/review/item/${item.kind}/${item.id}`);
    if (state.selectedKey !== itemKey) return;
    state.detail = detail;
    if (item.kind === "direct_expense" && detail.review_draft) {
      state.directDraft = detail.review_draft;
      state.useVoucherAsReceipt = detail.review_draft.use_voucher_as_receipt;
      state.primaryDocument = detailDocument(detail.review_draft.primary_document_id);
      state.stagedReceipts = detail.review_draft.editors
        .map(editor => detailDocument(editor.document_id)).filter(Boolean);
    }
    if (!state.primaryDocument && !detail.review_draft &&
        ["signout", "addendum", "direct_expense"].includes(item.kind)) {
      state.primaryDocument = state.inbox.find(document =>
        document.qr?.status === "matched" && document.qr.detected_id === item.id) || null;
      if (state.primaryDocument) {
        state.activeDocument = state.primaryDocument;
        if (item.kind === "signout") state.pendingSignoutFiling = {
          voucher_id: detail.voucher.voucher_id,
          expected_version: detail.voucher.version,
          document_id: state.primaryDocument.document_id,
          use_as_receipt: false,
        };
      }
    }
    renderDetail();
    renderDocumentRail();
  } catch (error) {
    if (state.selectedKey !== itemKey) return;
    els.detail.innerHTML = `<div class="empty-state"><strong>Unable to open record</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

/* ---- allocation editors --------------------------------------------------- */

const {allocationRow, wireAllocationEditor, readAllocations} = cashAllocationEditor({
  getOptions: () => state.options, markDirty, showMessage,
});

function renderProjectOptions() {
  document.getElementById("project-options").innerHTML =
    (state.options.projects || []).map(project =>
      `<option value="${esc(project)}"></option>`).join("");
}

function captureDirectDraft() {
  if (state.selectedItem?.kind !== "direct_expense") return;
  const form = document.getElementById("direct-expense-form");
  if (!form) return;
  state.directDraft = {
    editors: [...form.querySelectorAll(".direct-receipt-editor")].map(editor => ({
      document_id: editor.dataset.documentId || null,
      receipt_date: editor.querySelector('[name="receipt_date"]')?.value || "",
      receipt_vendor: editor.querySelector('[name="receipt_vendor"]')?.value || "",
      receipt_description:
        editor.querySelector('[name="receipt_description"]')?.value || "",
      allocations: readAllocations(editor),
    })),
  };
}

function directDraftFor(documentId, index = 0) {
  const editors = state.directDraft?.editors || [];
  const exact = editors.find(editor => editor.document_id === (documentId || null));
  if (exact) return exact;
  // Carry the only editor forward when the user switches between
  // voucher-as-receipt and the first separately attached receipt.
  return index === 0 && editors.length === 1 ? editors[0] : null;
}

/* ---- document slot inside an open row ------------------------------------- */

function documentPreview(document, label = "Document") {
  if (!document) return "";
  const url = cashDocumentStream(document);
  return `${cashDocumentProcessing(document)}<a class="attached-document-preview" href="${esc(url)}"
      target="_blank" rel="noopener" aria-label="Open ${esc(documentName(document))} full size">
    <iframe src="${esc(url)}#page=1&view=FitH" tabindex="-1"
      title="${esc(label)} preview"></iframe>
  </a>`;
}

function documentSlot({ target, label, document, uploadLabel = "Upload document" }) {
  const staged = document;
  if (staged) {
    return `<section class="attachment-choice attached-document document-drop-slot" data-document-slot="${target}">
      <div class="attachment-slot-heading"><span>${esc(label)}</span>
        <strong title="${esc(documentName(staged))}">${esc(documentName(staged))}</strong></div>
      ${documentPreview(staged, label)}
      <div class="attached-document-footer"><span>${esc(documentMeta(staged))}</span>
        <button type="button" class="text-button danger remove-document-slot"
          data-document-slot="${target}">Remove</button>
      </div>
    </section>`;
  }
  const offer = state.activeDocument &&
    state.inbox.some(item =>
      item.document_id === state.activeDocument.document_id)
    ? `<button type="button" class="btn primary slot-attach"
        data-document-slot="${target}" ${cashDocumentPending(state.activeDocument) ? "disabled" : ""}>Use opened · ${esc(documentName(state.activeDocument))}</button>`
    : `<p class="slot-empty-hint">Open a document on the left, or upload one.</p>`;
  return `<section class="attachment-choice document-drop-slot" data-document-slot="${target}">
    <div class="attachment-slot-heading"><span>${esc(label)}</span></div>
    <div class="attachment-slot-actions">
      ${offer}
      <button type="button" class="text-button slot-choose"
        data-document-slot="${target}">Choose from documents</button>
      <button type="button" class="btn ghost slot-upload"
        data-document-slot="${target}">${esc(uploadLabel)}</button>
    </div>
  </section>`;
}

function wireDocumentSlots(root) {
  root.querySelectorAll(".slot-attach").forEach(button => {
    button.addEventListener("click", async () => {
      const target = button.dataset.documentSlot;
      button.disabled = true;
      try {
        await applyDocument(target, state.activeDocument);
      } finally {
        button.disabled = false;
      }
    });
  });
  root.querySelectorAll(".slot-upload").forEach(button => {
    button.addEventListener("click", () => {
      if (!leaveDocumentEntry()) return;
      state.uploadIntent = button.dataset.documentSlot;
      els.picker.value = "";
      els.picker.click();
    });
  });
  root.querySelectorAll(".slot-choose").forEach(button => {
    button.addEventListener("click", () => {
      captureDirectDraft();
      openDocumentRail(button.dataset.documentSlot);
    });
  });
  root.querySelectorAll(".remove-document-slot").forEach(button => {
    button.addEventListener("click", () => {
      captureDirectDraft();
      if (button.dataset.documentSlot === "primary") {
        state.primaryDocument = null;
        markDirty();
        renderDetail();
      }
    });
  });
  root.querySelectorAll(".unstage-receipt").forEach(button => {
    button.addEventListener("click", () => {
      unstageReceipt(button.dataset.documentId);
    });
  });
  root.querySelectorAll(".document-drop-slot").forEach(slot => {
    slot.addEventListener("dragover", event => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      slot.classList.add("dragging");
    });
    slot.addEventListener("dragleave", event => {
      if (!slot.contains(event.relatedTarget)) slot.classList.remove("dragging");
    });
    slot.addEventListener("drop", async event => {
      event.preventDefault();
      event.stopPropagation();
      slot.classList.remove("dragging");
      const target = slot.dataset.documentSlot;
      const file = event.dataTransfer.files[0];
      if (file) {
        await uploadForSlot(file, target);
        return;
      }
      const documentId = event.dataTransfer.getData("text/plain");
      const dropped = state.inbox.find(item => item.document_id === documentId);
      if (dropped) await applyDocument(target, dropped);
    });
  });
}

async function applyDocument(target, document, { allowProcessing = false } = {}) {
  if (cashDocumentPending(document) && !allowProcessing) {
    showMessage("Document processing… Please wait before attaching it.");
    return;
  }
  if (!state.selectedItem || !document) {
    showMessage("Open a document on the left first.", "warning");
    return;
  }
  if (!state.inbox.some(item => item.document_id === document.document_id)) {
    showMessage("That document has already been filed.", "warning");
    return;
  }
  if (target === "support") {
    stageDocuments(document);
    return;
  }
  const item = state.selectedItem;
  if (["exchange", "withdrawal"].includes(item.kind)) {
    state.primaryDocument = document;
    markDirty();
    renderExchange();
    blockProcessingCommits();
    return;
  }
  if (item.kind === "direct_expense") {
    captureDirectDraft();
    if (state.stagedReceipts.some(item =>
        item.document_id === document.document_id)) {
      showMessage("The voucher and a separate receipt must be different documents.",
                  "warning");
      return;
    }
    state.primaryDocument = document;
    markDirty();
    renderDirectExpense();
    blockProcessingCommits();
    return;
  }
  if (item.kind === "addendum") {
    if (cashDocumentPending(document)) {
      showMessage("Document processing… You can file the addendum when it is ready.");
      return;
    }
    const addendum = state.detail.addendum;
    try {
      await request(`/api/review/addendum/${addendum.addendum_id}/complete`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: addendum.version,
          document_id: document.document_id },
      });
      const addendumId = addendum.addendum_id;
      const restore = async () => {
        const detail = await request(`/api/review/item/addendum/${addendumId}`);
        await request(`/api/review/addendum/${addendumId}/reopen`, {
          method: "POST", body: { submission_id: cashUUID(),
            expected_version: detail.addendum.version },
        });
        await Promise.all([loadWorklist(), loadInbox()]);
        showMessage(`${addendumId} filing undone.`);
      };
      await afterCommit(`${addendumId} filed.`, true, {
        undoText: `${addendumId} filed.`, onUndo: restore });
    } catch (error) { showMessage(error.message, "danger"); }
    return;
  }
  if (item.kind === "signout") {
    const voucher = state.detail.voucher;
    state.pendingSignoutFiling = {
      voucher_id: voucher.voucher_id,
      expected_version: voucher.version,
      document_id: document.document_id,
    };
    markDirty();
    renderSignout();
    blockProcessingCommits();
  }
}

async function confirmSignoutFiling(useAsReceipt) {
  const filing = state.pendingSignoutFiling;
  if (!filing) return;
  try {
    await request(`/api/review/signout/${filing.voucher_id}/attach-voucher`, {
      method: "POST", body: { submission_id: cashUUID(),
        expected_version: filing.expected_version,
        document_id: filing.document_id,
        use_as_receipt: useAsReceipt },
    });
    state.pendingSignoutFiling = null;
    const voucherId = filing.voucher_id;
    const restore = async () => {
      const detail = await request(`/api/review/item/signout/${voucherId}`);
      await request(`/api/review/signout/${voucherId}/reopen`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: detail.voucher.version },
      });
      await Promise.all([loadWorklist(), loadInbox()]);
      showMessage(`${voucherId} voucher filing undone.`);
    };
    await afterCommit(`${voucherId} voucher filed.`, true, {
      undoText: `${voucherId} voucher filed.`, onUndo: restore });
  } catch (error) { showMessage(error.message, "danger"); }
}

function markDirtyEvent(form) {
  form.addEventListener("input", markDirty);
}

/* ---- direct expense -------------------------------------------------------- */

function renderDirectExpense() {
  const { voucher, allocations } = state.detail;
  const sparse = allocations[0] || {};
  const initial = {
    expense_account: "",
    class_code: "",
    usd_cents: sparse.usd_cents ?? voucher.authorized_usd_cents ?? 0,
    mxn_centavos: sparse.mxn_centavos ?? voucher.authorized_mxn_centavos ?? 0,
    description: voucher.purpose,
  };
  const receiptEditors = state.stagedReceipts.map((document, index) => {
    const draft = directDraftFor(document.document_id, index);
    const allocation = index === 0 ? initial : {
      expense_account: "",
      class_code: "",
      description: voucher.purpose,
    };
    const editorAllocations = draft?.allocations?.length
      ? draft.allocations : [allocation];
    return `<section class="direct-receipt-editor" data-document-id="${esc(document.document_id)}">
      <div class="direct-receipt-heading"><div><p class="eyebrow">Receipt ${index + 1}</p>
        <strong>${esc(documentName(document))}</strong></div>
        <button type="button" class="text-button danger unstage-receipt"
          data-document-id="${esc(document.document_id)}">Remove</button></div>
      ${documentPreview(document, `Receipt ${index + 1}`)}
      <div class="form-grid two direct-receipt-facts">
        <label><span>Receipt date</span><input type="date" name="receipt_date"
          value="${esc(draft?.receipt_date || sparse.date || localDate())}" required></label>
        <label><span>Vendor</span><input name="receipt_vendor"
          value="${esc(draft?.receipt_vendor || voucher.recipient || "")}" required></label>
        <label class="full"><span>Description</span><input name="receipt_description"
          value="${esc(draft?.receipt_description || voucher.purpose || "")}"></label>
      </div>
      <div class="allocation-list">${editorAllocations.map(row => allocationRow(row, {
        directExpense: true, faceParts: { usd: true, mxn: true } })).join("")}</div>
      <div class="receipt-allocation-actions"><button type="button"
        class="btn ghost add-receipt-split">Add split</button></div>
    </section>`;
  }).join("");
  const usingVoucher = state.useVoucherAsReceipt;
  const voucherDraft = directDraftFor(null, 0);
  const voucherAllocations = voucherDraft?.allocations?.length
    ? voucherDraft.allocations : [initial];
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(voucher.voucher_id)}/print" target="_blank" rel="noopener">Print voucher</a>
    </nav><div class="row-editor-columns">
    <section class="inline-document-actions" aria-label="Attach documents">
      ${documentSlot({ target: "primary", label: "Voucher",
        document: state.primaryDocument, uploadLabel: "Upload voucher" })}
      <label class="receipt-choice"><input type="checkbox" id="use-voucher-as-receipt"
        ${usingVoucher ? "checked" : ""}>
        <span>Also use voucher as the receipt</span></label>
      <p class="field-help">Only if the vendor signed the acknowledgment. A separate receipt is optional.</p>
      <section class="attachment-choice document-drop-slot" data-document-slot="support"${usingVoucher ? " hidden" : ""}>
        <div class="attachment-slot-heading"><span>${state.stagedReceipts.length ? "Add another receipt" : "Separate receipt"}</span></div>
        <div class="attachment-slot-actions">
          ${state.activeDocument && state.inbox.some(item =>
            item.document_id === state.activeDocument.document_id)
            && state.activeDocument.document_id !== state.primaryDocument?.document_id
            && !state.stagedReceipts.some(document => document.document_id === state.activeDocument.document_id)
            ? `<button type="button" class="btn slot-attach"
              data-document-slot="support" ${cashDocumentPending(state.activeDocument) ? "disabled" : ""}>Use opened · ${esc(documentName(state.activeDocument))}</button>` : ""}
          <button type="button" class="text-button slot-choose"
            data-document-slot="support">Choose from documents</button>
          <button type="button" class="btn ghost slot-upload"
            data-document-slot="support">Upload receipt</button>
        </div>
      </section>
    </section>
    <form id="direct-expense-form" class="review-form sheet-action-form">
    <section class="sheet-editor-section">
      <p class="field-help">Authorized: USD ${cashMinorMoney(voucher.authorized_usd_cents)} · MXN ${cashMinorMoney(voucher.authorized_mxn_centavos)}. Enter the cash actually spent. Unused cash goes back to the safe.</p>
      ${!state.options.expense_accounts.length ? `<p class="callout danger">No account codes are available.</p>` : ""}
      ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one in Settings before completing this expense.</p>` : ""}
      ${!state.stagedReceipts.length ? `<div class="direct-receipt-editor voucher-receipt-editor">
        <div class="allocation-list">${voucherAllocations.map(row => allocationRow(row, {
          directExpense: true, faceParts: { usd: true, mxn: true } })).join("")}</div>
        <div class="receipt-allocation-actions"><button type="button"
          class="btn ghost add-receipt-split">Add split</button></div></div>` : receiptEditors}
    </section>
    <div class="row-actions"><output class="form-status" role="alert"></output>
      <button type="submit" class="btn ghost" value="draft" formnovalidate>Save progress</button>
      <button type="submit" class="btn primary">Save &amp; review next</button>
    </div>
    </form>
  </div>`;
  document.getElementById("use-voucher-as-receipt").addEventListener("change", event => {
    const useVoucher = event.target.checked;
    if (useVoucher && state.stagedReceipts.length && !window.confirm(
        "Use the voucher instead of the separate receipts? Their documents will stay in the inbox.")) {
      event.target.checked = false;
      return;
    }
    captureDirectDraft();
    state.useVoucherAsReceipt = useVoucher;
    if (useVoucher) state.stagedReceipts = [];
    markDirty();
    renderDirectExpense();
  });
  const form = document.getElementById("direct-expense-form");
  form.querySelectorAll(".direct-receipt-editor").forEach(editor => {
    const list = editor.querySelector(".allocation-list");
    const controls = () => {
      const rows = [...list.querySelectorAll(".allocation-row")];
      rows.forEach(row => {
        row.querySelector(".allocation-row-controls").hidden = rows.length === 1;
        row.classList.toggle("is-split", rows.length > 1);
      });
    };
    editor.querySelector(".add-receipt-split").addEventListener("click", () => {
      list.insertAdjacentHTML("beforeend", allocationRow({
        expense_account: "",
        class_code: "",
        description: voucher.purpose,
      }, { directExpense: true, faceParts: { usd: true, mxn: true } }));
      controls(); markDirty();
    });
    list.addEventListener("click", event => {
      if (!event.target.closest(".remove-allocation")) return;
      if (list.querySelectorAll(".allocation-row").length === 1) return;
      event.target.closest(".allocation-row").remove();
      controls(); markDirty();
    });
    controls();
  });
  wireDocumentSlots(els.detail);
  markDirtyEvent(form);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    const saveButton = event.submitter || form.querySelector('.btn.primary[type="submit"]');
    if (saveButton.disabled) return;
    const saveButtons = [...form.querySelectorAll('[type="submit"]')];
    saveButtons.forEach(button => { button.disabled = true; });
    status.textContent = "Saving…";
    try {
      const body = { submission_id: cashUUID(),
        expected_version: voucher.version };
      if (saveButton.value === "draft") {
        captureDirectDraft();
        Object.assign(body, state.directDraft, {
          primary_document_id: state.primaryDocument?.document_id || null,
          use_voucher_as_receipt: state.useVoucherAsReceipt,
        });
        const saved = await request(`/api/review/direct-expense/${voucher.voucher_id}/draft`, {
          method: "POST", body });
        voucher.version = saved.version;
        state.dirty = false;
        await loadWorklist();
        state.selectedItem = state.items.find(item => keyFor(item) === state.selectedKey);
        renderWorklist();
        status.textContent = "Progress saved. The expense stays open until completed.";
        return;
      }
      if (!state.primaryDocument ||
          !state.inbox.some(item =>
            item.document_id === state.primaryDocument.document_id)) {
        status.textContent = "Attach the signed voucher before saving.";
        return;
      }
      body.voucher_document_id = state.primaryDocument.document_id;
      body.use_voucher_as_receipt = usingVoucher;
      if (!state.stagedReceipts.length) {
        body.allocations = readAllocations(form);
      } else {
        body.receipts = [...form.querySelectorAll(".direct-receipt-editor")]
          .map(editor => {
            const allocations = readAllocations(editor);
            const total = field => allocations.reduce((sum, allocation) =>
              sum + Number(allocation[field] || 0), 0).toFixed(2);
            return { document_id: editor.dataset.documentId,
              date: editor.querySelector('[name="receipt_date"]').value,
              vendor: editor.querySelector('[name="receipt_vendor"]').value,
              description: editor.querySelector('[name="receipt_description"]').value,
              usd: total("usd"), mxn: total("mxn"),
              face_usd: total("face_usd"), face_mxn: total("face_mxn"),
              allocations };
          });
      }
      await request(`/api/review/direct-expense/${voucher.voucher_id}/complete`, {
        method: "POST", body });
      await afterCommit(`${voucher.voucher_id} completed.`, true);
    } catch (error) {
      status.textContent = error.message;
      status.scrollIntoView({ block: "nearest" });
    } finally {
      saveButtons.forEach(button => { button.disabled = false; });
    }
  });
  blockProcessingCommits();
}

/* ---- exchange ------------------------------------------------------------- */

function renderExchange() {
  const transaction = state.detail.transaction;
  const kind = state.selectedItem.kind;
  const withdrawal = kind === "withdrawal";
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/ledger?txn=${transaction.txn_id}">Open in ledger</a>
    </nav><form id="exchange-review-form">
      <div class="compact-document-editor drawer-document-body">
        <p>${withdrawal
          ? `USD ${cashMinorMoney(transaction.usd_cents)} withdrawn from the bank on ${esc(transaction.date)}.`
          : `USD ${cashMinorMoney(transaction.usd_cents)} exchanged for MXN ${cashMinorMoney(-transaction.mxn_centavos)} from ${esc(transaction.from_account)} on ${esc(transaction.date)}.`}</p>
        ${documentSlot({ target: "primary", label: withdrawal ? "Withdrawal receipt" : "Exchange receipt",
          document: state.primaryDocument, uploadLabel: "Upload receipt" })}
      </div>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn primary">Save & review next</button>
      </div>
    </form>`;
  wireDocumentSlots(els.detail);
  const form = document.getElementById("exchange-review-form");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    if (!state.primaryDocument || !state.inbox.some(item =>
        item.document_id === state.primaryDocument.document_id)) {
      status.textContent = `Attach the ${kind} receipt before saving.`;
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await request(`/api/review/${kind}/${transaction.txn_id}/complete`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: transaction.version,
          document_id: state.primaryDocument.document_id },
      });
      await afterCommit(`${withdrawal ? "Withdrawal" : "Exchange"} ${transaction.txn_id} receipt attached.`, true);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

/* ---- signout -------------------------------------------------------------- */

function renderSignout() {
  const voucher = state.detail.voucher;
  const filing = state.pendingSignoutFiling;
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(voucher.voucher_id)}/print" target="_blank" rel="noopener">Print voucher</a>
      <a class="btn ghost" href="/signouts/${esc(voucher.voucher_id)}">Open signout</a>
    </nav><div class="compact-document-editor drawer-document-body">
      ${filing ? `<section class="attachment-choice attached-document">
        <div class="attachment-slot-heading"><span>Voucher</span>
          <strong>${esc(documentName(detailDocument(filing.document_id)))}</strong></div>
        ${documentPreview(detailDocument(filing.document_id), "Voucher")}
        <div class="attached-document-footer"><button type="button"
          class="text-button danger" id="cancel-filing">Remove</button></div>
      </section>
      <label class="receipt-choice"><input type="checkbox" id="signout-use-voucher-as-receipt"
        ${filing.use_as_receipt ? "checked" : ""}>
        <span>Also use voucher as the receipt</span></label>` : `<section class="attachment-choice document-drop-slot" data-document-slot="primary">
        <div class="attachment-slot-heading"><span>Signed voucher</span></div>
        ${state.activeDocument && state.inbox.some(item =>
          item.document_id === state.activeDocument.document_id)
          ? `<div class="attachment-slot-actions">
              <button type="button" class="btn primary slot-attach"
                data-document-slot="primary" ${cashDocumentPending(state.activeDocument) ? "disabled" : ""}>Use opened · ${esc(documentName(state.activeDocument))}</button>
            </div>`
          : `<p class="slot-empty-hint">Open the signed voucher on the left, or upload it.</p>`}
        <div class="attachment-slot-actions">
          <button type="button" class="text-button slot-choose"
            data-document-slot="primary">Choose from documents</button>
          <button type="button" class="btn ghost" id="upload-voucher">Upload voucher</button>
        </div>
      </section>`}
    </div><div class="row-actions"><output class="form-status" role="alert"></output>
      ${filing ? '<button type="button" class="btn primary" id="file-voucher-only">File voucher</button>' : ""}
    </div>`;
  if (filing) {
    document.getElementById("signout-use-voucher-as-receipt")
      .addEventListener("change", event => {
        filing.use_as_receipt = event.target.checked;
      });
    document.getElementById("file-voucher-only")
      .addEventListener("click", async event => {
        event.target.disabled = true;
        try {
          await confirmSignoutFiling(document.getElementById("signout-use-voucher-as-receipt").checked);
        } finally { event.target.disabled = false; }
      });
    document.getElementById("cancel-filing").addEventListener("click", () => {
      state.pendingSignoutFiling = null;
      state.dirty = false;
      renderSignout();
    });
  } else {
    wireDocumentSlots(els.detail);
    document.getElementById("upload-voucher").addEventListener("click", () => {
      state.uploadIntent = "primary";
      els.picker.value = "";
      els.picker.click();
    });
  }
}

/* ---- addendum -------------------------------------------------------------- */

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(addendum.voucher_id)}/addendum/${esc(addendum.addendum_id)}/print" target="_blank" rel="noopener">Print addendum</a>
    </nav><div class="compact-document-editor drawer-document-body">
      <section class="attachment-choice document-drop-slot" data-document-slot="primary">
        <div class="attachment-slot-heading"><span>Signed addendum</span></div>
        ${cashDocumentProcessing(state.activeDocument)}
        ${state.activeDocument && state.inbox.some(item =>
          item.document_id === state.activeDocument.document_id)
          ? `<div class="attachment-slot-actions">
              <button type="button" class="btn primary slot-attach"
                data-document-slot="primary" ${cashDocumentPending(state.activeDocument) ? "disabled" : ""}>File opened · ${esc(documentName(state.activeDocument))}</button>
            </div>`
          : `<p class="slot-empty-hint">Open the signed addendum on the left, or upload it.</p>`}
        <div class="attachment-slot-actions">
          <button type="button" class="text-button slot-choose"
            data-document-slot="primary">Choose from documents</button>
          <button type="button" class="btn ghost" id="upload-addendum">Upload &amp; file addendum</button>
        </div>
      </section>
      <output class="form-status" role="alert"></output>
    </div>`;
  wireDocumentSlots(els.detail);
  document.getElementById("upload-addendum").addEventListener("click", () => {
    state.uploadIntent = "primary";
    els.picker.value = "";
    els.picker.click();
  });
}

/* ---- document-entry forms (withdrawal / exchange) -------------------------- */

function cashAccountOptions(selected = "SAFE") {
  return state.options.cash_accounts.map(account =>
    `<option value="${esc(account.account_id)}"${account.account_id === selected
      ? " selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
}

function documentEntryForm(inboxDocument, kind) {
  const today = new Date();
  const localDate = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
  const exchange = kind === "exchange";
  const label = exchange ? "currency exchange" : "bank withdrawal";
  return `<form id="document-entry-form" data-document-id="${esc(inboxDocument.document_id)}" class="review-form document-entry-form">
    <input type="hidden" name="entry_type" value="${kind}">
    <div class="document-entry-heading"><strong>Create new ${label}</strong>
      <span>${esc(documentName(inboxDocument))}</span></div>
    <div class="form-grid two">
      <label><span>Transaction date</span><input type="date" name="date"
        value="${localDate}" required></label>
      ${exchange ? `<label><span>Cash source</span><select name="source_account">${cashAccountOptions()}</select></label>` : ""}
      <label><span>USD amount</span><input name="usd" inputmode="decimal"></label>
      ${exchange ? `<label><span>MXN received</span><input name="mxn" inputmode="decimal"></label>` : ""}
      <label class="full"><span>Notes</span><textarea name="notes" rows="2"></textarea></label>
    </div><p class="derived-movement">${exchange
      ? "USD exchanged for MXN from the selected cash account."
      : "USD moved from the bank to the safe."}</p>
    <div class="document-entry-actions"><output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Create ${label}</button></div>
  </form>`;
}

function wireDocumentEntryForm(inboxDocument) {
  const form = document.getElementById("document-entry-form");
  if (!form) return;
  form.addEventListener("input", () => { state.documentActionDirty = true; });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(form);
    const kind = data.get("entry_type");
    const body = { submission_id: cashUUID(), date: data.get("date"),
      usd: data.get("usd"), notes: data.get("notes"),
      document_id: inboxDocument.document_id };
    if (kind === "exchange") Object.assign(body, {
      mxn: data.get("mxn"), source_account: data.get("source_account") });
    const status = form.querySelector("output");
    status.textContent = "Saving…";
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await request(`/api/entries/${kind}`, { method: "POST", body });
      state.documentActionDirty = false;
      state.documentActionType = null;
      state.activeDocument = null;
      await Promise.all([loadWorklist(), loadInbox()]);
      renderDocumentRail();
      showMessage(`${kind === "exchange" ? "Exchange" : "Withdrawal"} created.`);
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; }
  });
}

/* ---- package reviewer -------------------------------------------------------- */

function renderPackage() {
  const submission = state.detail.package;
  cashRenderPackage({state, els, esc, workflowStatus, allocationRow,
    wireAllocationEditor, readAllocations, request, markDirty, markDirtyEvent,
    showMessage, refreshSelected, afterCommit,
    navigation: `<nav class="drawer-record-tools" aria-label="Signout actions">
      <a class="btn ghost" href="/signouts/${esc(submission.voucher_id)}?submission=${esc(submission.package_id)}">Open signout ${esc(submission.voucher_id)}</a>
    </nav>`,
    onDocument(document) {
      if (document && document.document_id !== state.activeDocument?.document_id) {
        state.activeDocument = document;
        renderDocumentRail();
      }
    },
  });
}


/* ---- shell ------------------------------------------------------------------ */

function blockProcessingCommits() {
  const docs = [state.primaryDocument, ...state.stagedReceipts,
    detailDocument(state.pendingSignoutFiling?.document_id)].filter(Boolean);
  if (docs.some(cashDocumentPending)) {
    els.detail.querySelectorAll('button[type="submit"], #file-voucher-only').forEach(button => {
      button.disabled = true;
      button.title = "Wait for document processing to finish";
    });
  }
}

function renderDetail() {
  if (!state.selectedItem || !state.detail) return;
  state.detailRendering = true;
  switch (state.selectedItem.kind) {
    case "direct_expense": renderDirectExpense(); break;
    case "exchange":
    case "withdrawal": renderExchange(); break;
    case "signout": renderSignout(); break;
    case "addendum": renderAddendum(); break;
    case "package": renderPackage(); break;
    default:
      els.detail.innerHTML = `<div class="empty-state"><strong>Unsupported review item</strong></div>`;
  }
  blockProcessingCommits();
  state.detailRendering = false;
}

async function refreshSelected() {
  if (!state.selectedItem) return;
  await Promise.all([loadWorklist(), loadInbox(), loadOptions()]);
  state.detail = await request(`/api/review/item/${state.selectedItem.kind}/${state.selectedItem.id}`);
  renderDetail();
  renderWorklist();
}

async function afterCommit(text, advance, undo = null) {
  const oldVisible = currentFilteredItems();
  const oldIndex = oldVisible.findIndex(item => keyFor(item) === state.selectedKey);
  state.dirty = false;
  state.selectedItem = null;
  state.selectedKey = null;
  state.detail = null;
  state.pendingSignoutFiling = null;
  state.stagedReceipts = [];
  state.primaryDocument = null;
  state.directDraft = null;
  state.useVoucherAsReceipt = false;
  state.activeDocument = null;
  state.documentActionType = null;
  state.documentActionDirty = false;
  setWorkspaceOpen(false);
  renderDocumentRail();
  await Promise.all([loadWorklist(), loadInbox(), loadOptions()]);
  if (undo && undo.onUndo) showUndo(undo.undoText || text, undo.onUndo);
  else showMessage(text);
  const next = currentFilteredItems();
  if (advance && next.length) {
    await selectItem(next[Math.min(Math.max(oldIndex, 0), next.length - 1)],
                     { force: true });
  } else {
    els.detail.innerHTML = `<div class="empty-state decision-empty">
      <span class="empty-icon" aria-hidden="true">✓</span>
      <strong>${next.length ? "Saved" : "Nothing left to review"}</strong></div>`;
  }
}

/* ---- events ----------------------------------------------------------------- */

els.browse.addEventListener("click", () => {
  if (!leaveDocumentEntry()) return;
  state.uploadIntent = state.documentPickTarget || "inbox";
  els.picker.value = "";
  els.picker.click();
});
els.picker.addEventListener("cancel", () => { state.uploadIntent = null; });
els.picker.addEventListener("change", async () => {
  const file = els.picker.files[0];
  const intent = state.uploadIntent;
  state.uploadIntent = null;
  els.picker.value = "";
  if (!file || !intent) return;
  if (intent === "inbox") {
    await uploadToRail(file);
  } else if (intent === "support") {
    await uploadAndStage(file);
  } else {
    await uploadForSlot(file, intent);
  }
  if (compactReviewLayout() && intent !== "inbox") closeDocumentRail();
});
els.drop.addEventListener("dragover", event => {
  event.preventDefault(); els.drop.classList.add("dragging");
});
els.drop.addEventListener("dragleave", () => els.drop.classList.remove("dragging"));
els.drop.addEventListener("drop", async event => {
  event.preventDefault(); els.drop.classList.remove("dragging");
  const file = event.dataTransfer.files[0];
  if (!file || !leaveDocumentEntry()) return;
  if (state.documentPickTarget) {
    await uploadForSlot(file, state.documentPickTarget);
    if (compactReviewLayout()) closeDocumentRail();
  } else await uploadToRail(file);
});
els.refreshInbox.addEventListener("click", () => loadInbox());
els.filter.addEventListener("input", renderWorklist);
els.openDocuments.addEventListener("click", () => openDocumentRail());
els.drawerDocuments.addEventListener("click", () => openDocumentRail());
els.closeDocuments.addEventListener("click", closeDocumentRail);
els.closeDrawer.addEventListener("click", () => {
  if (state.selectedItem) selectItem(state.selectedItem);
});

els.tabs.forEach(tab => tab.addEventListener("click", () => {
  if (state.dirty && !window.confirm("Discard unsaved review changes?")) return;
  if (!leaveDocumentEntry()) return;
  state.mode = tab.dataset.reviewMode;
  state.selectedKey = null;
  state.selectedItem = null;
  state.detail = null;
  state.dirty = false;
  state.pendingSignoutFiling = null;
  state.stagedReceipts = [];
  state.primaryDocument = null;
  state.directDraft = null;
  state.useVoucherAsReceipt = false;
  state.activeDocument = null;
  state.documentActionType = null;
  state.documentActionDirty = false;
  els.layout.classList.toggle("package-mode", state.mode === "submissions");
  closeDocumentRail();
  els.workHeading.textContent = state.mode === "submissions"
    ? "Receipt submissions" : "Transactions";
  els.tabs.forEach(button => button.classList.toggle("active", button === tab));
  renderWorklist();
  renderDocumentRail();
}));

els.list.addEventListener("keydown", event => {
  if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
  if (event.target.closest("form, .attachment-choice, .document-viewer")) return;
  const visible = currentFilteredItems();
  if (!visible.length) return;
  let index = visible.findIndex(item => keyFor(item) === state.selectedKey);
  if (event.key === "ArrowDown") index = Math.min(index + 1, visible.length - 1);
  if (event.key === "ArrowUp") index = Math.max(index - 1, 0);
  if (event.key === "Enter" && index < 0) index = 0;
  selectItem(visible[Math.max(index, 0)]);
  event.preventDefault();
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape" &&
      els.evidencePane.classList.contains("mobile-open")) {
    closeDocumentRail();
    event.preventDefault();
    return;
  }
  if (event.key === "Escape" && state.selectedItem &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    selectItem(state.selectedItem);
    event.preventDefault();
    return;
  }
  if (event.key === "/" && !event.ctrlKey && !event.metaKey &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    els.filter.focus(); event.preventDefault();
  }
});
window.addEventListener("beforeunload", event => {
  if (!state.dirty && !state.documentActionDirty) return;
  event.preventDefault();
  event.returnValue = "";
});
window.matchMedia("(max-width: 1200px)")
  .addEventListener("change", syncResponsivePanes);

async function bootstrap() {
  try {
    await Promise.all([loadOptions(), loadWorklist(), loadInbox(), csrf()]);
    const requested = new URLSearchParams(location.search).get("item");
    const requestedItem = requested
      ? state.items.find(item => keyFor(item) === requested) : null;
    if (requestedItem?.kind === "package") {
      state.mode = "submissions";
      els.layout.classList.add("package-mode");
      updateEvidenceHeading();
      els.workHeading.textContent = "Receipt submissions";
      els.tabs.forEach(button => button.classList.toggle("active",
        button.dataset.reviewMode === "submissions"));
      renderWorklist();
    }
    if (requestedItem) await selectItem(requestedItem, { force: true });
    const requestedDocument = new URLSearchParams(location.search).get("document");
    if (requestedDocument) {
      state.activeDocument = state.inbox.find(item => item.document_id === requestedDocument) || null;
      renderDocumentRail();
      if (state.activeDocument && compactReviewLayout()) openDocumentRail();
    }
    syncResponsivePanes();
  } catch (error) {
    showMessage(error.message, "danger");
    els.detail.innerHTML = `<div class="empty-state"><strong>Review could not load</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

bootstrap();

// Poll only the inbox; keep the current record and all unsaved coding in place.
window.setInterval(() => {
  loadInbox({ background: true }).catch(() => {});
}, 2000);
