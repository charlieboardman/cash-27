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
  cancelPick: document.getElementById("cancel-pick"),
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
  items: [],
  inbox: [],
  options: { expense_accounts: [], classes: [], cash_accounts: [], projects: [] },
  selectedKey: null,
  selectedItem: null,
  detail: null,
  activeDocument: null,
  primaryDocument: null,
  stagedReceipts: [],
  directRows: null,
  useVoucherAsReceipt: false,
  pendingSignoutFiling: null,
  uploadIntent: null,
  dirty: false,
  documentActionType: null,
  documentActionDirty: false,
  documentAttachSearch: false,
  documentPickTarget: null,
  mode: "transactions",
};

const esc = cashEsc;


function displayDateTime(value) {
  return value ? new Date(value).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }) : "Unknown time";
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


function renderDrawerIdentity(item) {
  if (!item) {
    els.drawerIdentity.innerHTML = "";
    return;
  }
  const amounts = [item.usd != null ? `USD ${cashMoney(item.usd)}` : "",
    item.mxn != null ? `MXN ${cashMoney(item.mxn)}` : ""].filter(Boolean);
  els.drawerIdentity.innerHTML = `<p class="eyebrow">${esc(item.type_label)}</p>
    <h2 id="review-drawer-title">${esc(item.id)}</h2>
    <p>${esc(item.label || "")}${item.secondary ? ` · ${esc(item.secondary)}` : ""}</p>
    ${amounts.length ? `<div class="review-drawer-amounts">${amounts.map(amount =>
      `<strong>${esc(amount)}</strong>`).join("")}</div>` : ""}
    ${(item.badges || []).length ? `<div class="badge-row">${item.badges.map(badge =>
      `<span class="badge">${esc(badge)}</span>`).join("")}</div>` : ""}`;
}

function setWorkspaceOpen(open) {
  els.drawer.hidden = !open;
  els.layout.classList.toggle("workspace-open", open);
  renderDrawerIdentity(open ? state.selectedItem : null);
}

function compactReviewLayout() {
  return window.matchMedia("(max-width: 1200px)").matches;
}

function updateEvidenceHeading() {
  if (state.documentPickTarget === "primary") {
    els.evidenceHeading.textContent = state.selectedItem?.kind === "addendum"
      ? "Choose an addendum" : state.selectedItem?.kind === "count"
      ? "Choose a count sheet" : state.selectedItem?.kind === "exchange"
      ? "Choose an exchange receipt" : state.selectedItem?.kind === "withdrawal"
      ? "Choose a withdrawal receipt" : "Choose a voucher";
  } else if (state.documentPickTarget === "support") {
    els.evidenceHeading.textContent = "Choose a receipt";
  } else {
    els.evidenceHeading.textContent = "Document inbox";
  }
  els.evidencePane.classList.toggle("picking", Boolean(state.documentPickTarget));
  els.cancelPick.hidden = !state.documentPickTarget;
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

// Review messages are brief status updates, so every tone fades.
const showMessage = (text, tone = "success") =>
  cashNotice(els.message, text, tone, { fade: true });

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

const request = cashApi;

function currentFilteredItems() {
  const needle = els.filter.value.trim().toLowerCase();
  return state.items.filter(item => {
    if (state.mode === "transactions" && item.kind === "expense_report") return false;
    if (state.mode === "submissions" && item.kind !== "expense_report") return false;
    if (!needle) return true;
    return [item.id, item.label, item.secondary, item.type_label,
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
    const record = item.id;
    const details = `${item.label || item.id}${item.secondary ? ` · ${item.secondary}` : ""}`;
    row.innerHTML = `<td class="txn-id">${esc(record)}</td>
      <td class="col-date">${esc(item.date || "—")}</td><td>${esc(item.type_label)}</td>
      <td class="review-record-details">${esc(details)}</td>
      <td class="num col-rows">${item.receipt == null ? "—" : esc(item.receipt)}</td>
      <td class="num col-cash">${item.usd == null ? "—" : cashMoney(item.usd)}</td>
      <td class="num col-cash">${item.mxn == null ? "—" : cashMoney(item.mxn)}</td>
      <td><span class="badge-row">${(item.badges || []).map(b =>
        `<span class="badge">${esc(b)}</span>`).join("")}</span></td>`;
    row.addEventListener("click", () => selectItem(item));
    els.list.appendChild(row);
  });
  setWorkspaceOpen(Boolean(state.selectedItem));
  els.empty.hidden = visible.length !== 0;
  const modeCount = state.items.filter(item => state.mode === "submissions"
    ? item.kind === "expense_report" : item.kind !== "expense_report").length;
  const noun = state.mode === "submissions" ? "expense report" : "transaction";
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
  document.getElementById("project-options").innerHTML = state.options.projects
    .map(project => `<option value="${esc(project)}"></option>`).join("");
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
    state.inbox = [...data.documents].reverse();
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

async function pickDocument(document) {
  const target = state.documentPickTarget;
  state.documentPickTarget = null;
  updateEvidenceHeading();
  await applyDocument(target, document);
  if (compactReviewLayout()) closeDocumentRail();
  renderDocumentRail();
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
  const documents = state.inbox;
  if (!documents.length) {
    els.evidenceList.innerHTML = `<li class="empty-state compact">
      <strong>The inbox is empty</strong></li>`;
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
    card.addEventListener("click", () => {
      if (state.documentPickTarget) pickDocument(document);
      else openDocument(document);
    });
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
    captureDirectRows();
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
    captureDirectRows();
    renderDetail();
  }
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
      <button type="button" class="btn primary" id="attach-to-transaction">Attach to transaction…</button>
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
  const candidates = state.items.filter(item => item.kind !== "expense_report" && tokens.every(token =>
    [item.id, item.kind, item.type_label, item.label, item.secondary, item.date, item.usd, item.mxn]
      .join(" ").toLowerCase().includes(token)))
    // The open record comes first.
    .sort((a, b) => (keyFor(b) === state.selectedKey) - (keyFor(a) === state.selectedKey));
  const list = document.getElementById("document-transaction-results");
  list.innerHTML = candidates.length ? "" : '<p class="muted">No matching transactions awaiting documents.</p>';
  for (const item of candidates) {
    const result = document.createElement("div");
    result.className = "document-transaction-result";
    const summary = `<strong>${esc(item.type_label)} · ${esc(item.id)}</strong>
      <span>${esc(item.date)} · USD ${cashMoney(item.usd)} · MXN ${cashMoney(item.mxn)}</span>
      <span>${esc(item.label)}${item.secondary ? ` · ${esc(item.secondary)}` : ""}</span>`;
    // A direct expense can take the document as its voucher or as a receipt.
    const targets = item.kind === "direct_expense"
      ? [["primary", "As voucher"], ["support", "As receipt"]] : [["primary", "Attach"]];
    result.innerHTML = `<div>${summary}</div><span class="document-result-actions">${targets.map(([target, label]) =>
      `<button type="button" class="btn ghost" data-target="${target}">${label}</button>`).join("")}</span>`;
    result.querySelectorAll("button").forEach(button => button.addEventListener("click", async () => {
      const selectedDocument = state.activeDocument;
      if (!leaveDocumentEntry()) return;
      if (state.selectedKey !== keyFor(item)) await selectItem(item);
      if (state.selectedKey !== keyFor(item) || !state.detail) return;
      // Attaching stages the document; nothing is filed until the record is saved.
      state.documentAttachSearch = false;
      state.activeDocument = null;
      await applyDocument(button.dataset.target, selectedDocument);
      if (compactReviewLayout()) closeDocumentRail();
      renderDocumentRail();
    }));
    list.appendChild(result);
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
  captureDirectRows();
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
  captureDirectRows();
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

// Forget the open record and everything staged for it.
function clearSelection() {
  Object.assign(state, {
    selectedKey: null, selectedItem: null, detail: null, dirty: false,
    pendingSignoutFiling: null, stagedReceipts: [], primaryDocument: null,
    directRows: null, useVoucherAsReceipt: false, activeDocument: null,
    documentActionType: null, documentActionDirty: false,
  });
}

// The open record and tab live in the URL so reload and shared links work.
function syncUrl() {
  // Built by hand so the address keeps a readable "kind:id" like other links.
  const params = [];
  if (state.selectedKey) params.push(`item=${encodeURIComponent(state.selectedKey).replace(/%3A/gi, ":")}`);
  if (state.mode === "submissions") params.push("tab=reports");
  const search = params.length ? `?${params.join("&")}` : "";
  if (search !== window.location.search) {
    window.history.replaceState(null, "", `${window.location.pathname}${search}`);
  }
}

function setMode(mode) {
  state.mode = mode;
  els.workHeading.textContent = mode === "submissions" ? "Expense reports" : "Transactions";
  els.tabs.forEach(button => button.classList.toggle("active", button.dataset.reviewMode === mode));
  els.list.closest("table").classList.toggle("reports-mode", mode === "submissions");
}

function markDirty() {
  state.dirty = true;
}

async function selectItem(item, { force = false } = {}) {
  if (item.kind === "expense_report") {
    if (!force && state.dirty &&
        !window.confirm("Discard unsaved review changes and open another item?")) return;
    window.location.assign(item.url);
    return;
  }
  const itemKey = keyFor(item);
  if (!force && state.dirty &&
      !window.confirm("Discard unsaved review changes and open another item?")) return;
  if (!force && !leaveDocumentEntry()) return;
  state.stagedReceipts = [];
  state.primaryDocument = null;
  state.directRows = null;
  state.useVoucherAsReceipt = false;
  state.pendingSignoutFiling = null;
  if (!force && state.selectedKey === itemKey) {
    state.dirty = false;
    state.selectedItem = null;
    state.selectedKey = null;
    state.detail = null;
    syncUrl();
    renderWorklist();
    renderEvidenceList();
    return;
  }
  state.dirty = false;
  state.selectedItem = item;
  state.selectedKey = itemKey;
  syncUrl();
  state.detail = null;
  renderWorklist();
  els.detail.innerHTML = `<div class="loading-state">Loading item…</div>`;
  try {
    const detail = await request(`/api/review/item/${item.kind}/${item.id}`);
    if (state.selectedKey !== itemKey) return;
    state.detail = detail;
    if (item.kind === "direct_expense" && detail.review_draft) {
      state.directRows = detail.review_draft.rows;
      state.useVoucherAsReceipt = detail.review_draft.use_voucher_as_receipt;
      state.primaryDocument = detailDocument(detail.review_draft.primary_document_id);
      state.stagedReceipts = detail.review_draft.receipt_document_ids
        .map(detailDocument).filter(Boolean);
    }
    if (!state.primaryDocument && !detail.review_draft &&
        ["signout", "addendum", "direct_expense", "count"].includes(item.kind)) {
      const code = item.kind === "count" ? `Txn-${item.id}` : item.id;
      state.primaryDocument = state.inbox.find(document =>
        document.qr?.status === "matched" && document.qr.detected_id === code) || null;
      if (state.primaryDocument) {
        state.activeDocument = state.primaryDocument;
        if (item.kind === "signout") state.pendingSignoutFiling = {
          voucher_id: detail.voucher.voucher_id,
          expected_version: detail.voucher.version,
          document_id: state.primaryDocument.document_id,
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

/* ---- direct-expense rows ---------------------------------------------------- */

// The payment's fields are shared with the expense report page (row-fields.js).
// Receipts are documents on the payment, not rows of their own.
function captureDirectRows() {
  if (state.selectedItem?.kind !== "direct_expense") return;
  const editors = [...els.detail.querySelectorAll(".direct-row")];
  if (!editors.length) return;
  state.directRows = editors.map(editor => cashRowFields.collect(editor));
}

function blankDirectRow() {
  const { voucher, cash } = state.detail;
  return { purpose: voucher.purpose,
    usd: cash?.usd ?? voucher.authorized_usd, mxn: cash?.mxn ?? voucher.authorized_mxn,
    receipt_total: "", class_code: null, expense_account: null, splits: [] };
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
  return emptySlot(target, label, uploadLabel);
}

// Every document slot attaches the same ways: pick from the inbox, upload,
// or drop a file or inbox card onto it.
function emptySlot(target, label, uploadLabel) {
  return `<section class="attachment-choice document-drop-slot" data-document-slot="${target}">
    <div class="attachment-slot-heading"><span>${esc(label)}</span></div>
    <div class="attachment-slot-actions">
      <button type="button" class="btn slot-choose"
        data-document-slot="${target}">Choose from inbox</button>
      <button type="button" class="btn ghost slot-upload"
        data-document-slot="${target}">${esc(uploadLabel)}</button>
    </div>
  </section>`;
}

function wireDocumentSlots(root) {
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
      captureDirectRows();
      openDocumentRail(button.dataset.documentSlot);
    });
  });
  root.querySelectorAll(".remove-document-slot").forEach(button => {
    button.addEventListener("click", () => {
      captureDirectRows();
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
  if (["exchange", "withdrawal", "count"].includes(item.kind)) {
    state.primaryDocument = document;
    markDirty();
    renderExchange();
    blockProcessingCommits();
    return;
  }
  if (item.kind === "direct_expense") {
    captureDirectRows();
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
    state.primaryDocument = document;
    markDirty();
    renderAddendum();
    blockProcessingCommits();
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

async function confirmSignoutFiling() {
  const filing = state.pendingSignoutFiling;
  if (!filing) return;
  try {
    await request(`/api/review/signout/${filing.voucher_id}/attach-voucher`, {
      method: "POST", body: { submission_id: cashUUID(),
        expected_version: filing.expected_version,
        document_id: filing.document_id },
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

/* ---- direct expense -------------------------------------------------------- */

function renderDirectExpense() {
  const { voucher } = state.detail;
  const receipts = state.stagedReceipts;
  const rows = state.directRows?.length ? state.directRows : [blankDirectRow()];
  const editors = rows.map(row => `<section class="direct-row">
      ${cashRowFields.html(row, { codes: state.options,
        show: { date: false, vendor: false, purpose: true, project: true } })}
    </section>`).join("");
  const receiptCards = receipts.map((document, index) => `<section class="attachment-choice attached-document staged-receipt">
      <div class="attachment-slot-heading"><span>Receipt ${index + 1}</span>
        <strong title="${esc(documentName(document))}">${esc(documentName(document))}</strong></div>
      ${documentPreview(document, `Receipt ${index + 1}`)}
      <div class="attached-document-footer"><span>${esc(documentMeta(document))}</span>
        <button type="button" class="text-button danger unstage-receipt"
          data-document-id="${esc(document.document_id)}">Remove</button></div>
    </section>`).join("");
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(voucher.voucher_id)}/print" target="_blank" rel="noopener">Print voucher</a>
    </nav><div class="row-editor-columns">
    <section class="inline-document-actions" aria-label="Attach documents">
      ${documentSlot({ target: "primary", label: "Voucher",
        document: state.primaryDocument, uploadLabel: "Upload voucher" })}
      <label class="receipt-choice"><input type="checkbox" id="use-voucher-as-receipt"
        ${state.useVoucherAsReceipt ? "checked" : ""}>
        <span>Also file as a receipt</span></label>
      <p class="field-help">Only if the vendor signed the acknowledgment.</p>
      ${receiptCards}
      ${emptySlot("support", receipts.length ? "Add another receipt" : "Receipt", "Upload receipt")}
    </section>
    <form id="direct-expense-form" class="review-form sheet-action-form">
    <section class="sheet-editor-section">
      <p class="field-help">Paid: USD ${cashMoney(state.detail.cash?.usd ?? voucher.authorized_usd)} · MXN ${cashMoney(state.detail.cash?.mxn ?? voucher.authorized_mxn)}</p>
      ${!state.options.expense_accounts.length ? `<p class="callout danger">No account codes are available.</p>` : ""}
      ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one in Settings before completing this expense.</p>` : ""}
      ${editors}
    </section>
    <div class="row-actions"><output class="form-status" role="alert"></output>
      <button type="submit" class="btn ghost" value="draft" formnovalidate>Save progress</button>
      <button type="submit" class="btn primary">Save &amp; review next</button>
    </div>
    </form>
  </div>`;
  document.getElementById("use-voucher-as-receipt").addEventListener("change", event => {
    captureDirectRows();
    state.useVoucherAsReceipt = event.target.checked;
    markDirty();
  });
  const form = document.getElementById("direct-expense-form");
  // The split workspace shows the first receipt, or the voucher.
  const shown = receipts[0] || state.primaryDocument;
  form.querySelectorAll(".direct-row").forEach((editor, index) => {
    cashRowFields.wire(editor, { codes: state.options,
      preview: shown && { url: cashDocumentStream(shown), name: documentName(shown) },
      onInput: markDirty, onStructure: fields => {
      captureDirectRows();
      state.directRows[index] = fields;
      markDirty();
      renderDirectExpense();
    } });
  });
  wireDocumentSlots(els.detail);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    const saveButton = event.submitter || form.querySelector('.btn.primary[type="submit"]');
    if (saveButton.disabled) return;
    captureDirectRows();
    if (state.directRows.some(cashRowFields.invalid)) {
      status.textContent = "Enter amounts with at most two decimal places.";
      return;
    }
    const saveButtons = [...form.querySelectorAll('[type="submit"]')];
    saveButtons.forEach(button => { button.disabled = true; });
    status.textContent = "Saving…";
    try {
      const body = { submission_id: cashUUID(), expected_version: voucher.version,
        rows: state.directRows, use_voucher_as_receipt: state.useVoucherAsReceipt,
        receipt_document_ids: receipts.map(document => document.document_id) };
      const finishing = saveButton.value !== "draft";
      const voucherAttached = Boolean(state.primaryDocument && state.inbox.some(item =>
        item.document_id === state.primaryDocument.document_id));
      // Codes, splits, and descriptions save before the signed voucher exists.
      // The expense stays in the queue, still needing that voucher, until finish.
      if (!finishing || !voucherAttached) {
        body.primary_document_id = voucherAttached ? state.primaryDocument.document_id : null;
        const saved = await request(`/api/review/direct-expense/${voucher.voucher_id}/draft`, {
          method: "POST", body });
        voucher.version = saved.version;
        state.dirty = false;
        await loadWorklist();
        state.selectedItem = state.items.find(item => keyFor(item) === state.selectedKey);
        renderWorklist();
        status.textContent = finishing
          ? "Progress saved. Attach the signed voucher to finish. This expense stays in the queue until then."
          : "Progress saved. The expense stays open until completed.";
        return;
      }
      body.voucher_document_id = state.primaryDocument.document_id;
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
  const count = kind === "count";
  const difference = [["USD", transaction.usd], ["MXN", transaction.mxn]]
    .filter(([, amount]) => Number(amount)).map(([currency, amount]) => `${currency} ${cashMoney(amount)}`);
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/ledger?txn=${transaction.txn_id}">Open in ledger</a>
      ${count ? `<a class="btn ghost" href="${esc(transaction.print_url)}" target="_blank" rel="noopener">Print count sheet</a>` : ""}
    </nav><form id="exchange-review-form">
      <div class="compact-document-editor drawer-document-body">
        <p>${count
          ? `Safe counted on ${esc(transaction.date)}: ${difference.length ? `difference ${esc(difference.join(" · "))}` : "matched the records"}.`
          : withdrawal
          ? `USD ${cashMoney(transaction.usd)} withdrawn from the bank on ${esc(transaction.date)}.`
          : `USD ${cashMoney(transaction.usd)} exchanged for MXN ${cashMoney(-Number(transaction.mxn))} from ${esc(transaction.from_account)} on ${esc(transaction.date)}.`}</p>
        ${documentSlot({ target: "primary",
          label: count ? "Signed count sheet" : withdrawal ? "Withdrawal receipt" : "Exchange receipt",
          document: state.primaryDocument, uploadLabel: count ? "Upload count sheet" : "Upload receipt" })}
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
      status.textContent = count ? "Attach the signed count sheet before saving."
        : `Attach the ${kind} receipt before saving.`;
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
      await afterCommit(count ? `Count sheet Txn-${transaction.txn_id} filed.`
        : `${withdrawal ? "Withdrawal" : "Exchange"} ${transaction.txn_id} receipt attached.`, true);
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
      <a class="btn ghost" href="/${esc(voucher.voucher_id)}">Open expense report</a>
    </nav><div class="compact-document-editor drawer-document-body">
      ${filing ? `<section class="attachment-choice attached-document">
        <div class="attachment-slot-heading"><span>Voucher</span>
          <strong>${esc(documentName(detailDocument(filing.document_id)))}</strong></div>
        ${documentPreview(detailDocument(filing.document_id), "Voucher")}
        <div class="attached-document-footer"><button type="button"
          class="text-button danger" id="cancel-filing">Remove</button></div>
      </section>` : emptySlot("primary", "Signed voucher", "Upload voucher")}
    </div><div class="row-actions"><output class="form-status" role="alert"></output>
      ${filing ? '<button type="button" class="btn primary" id="file-voucher">File voucher</button>' : ""}
    </div>`;
  if (filing) {
    document.getElementById("file-voucher")
      .addEventListener("click", async event => {
        event.target.disabled = true;
        try {
          await confirmSignoutFiling();
        } finally { event.target.disabled = false; }
      });
    document.getElementById("cancel-filing").addEventListener("click", () => {
      state.pendingSignoutFiling = null;
      state.dirty = false;
      renderSignout();
    });
  } else {
    wireDocumentSlots(els.detail);
  }
}

/* ---- addendum -------------------------------------------------------------- */

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(addendum.voucher_id)}/addendum/${esc(addendum.addendum_id)}/print" target="_blank" rel="noopener">Print addendum</a>
    </nav><div class="compact-document-editor drawer-document-body">
      ${documentSlot({ target: "primary", label: "Signed addendum",
        document: state.primaryDocument, uploadLabel: "Upload addendum" })}
    </div><div class="row-actions"><output class="form-status" role="alert"></output>
      ${state.primaryDocument ? '<button type="button" class="btn primary" id="file-addendum">File addendum</button>' : ""}
    </div>`;
  wireDocumentSlots(els.detail);
  document.getElementById("file-addendum")?.addEventListener("click", async event => {
    event.target.disabled = true;
    try { await fileAddendum(state.primaryDocument); }
    finally { event.target.disabled = false; }
  });
}

async function fileAddendum(document) {
  const addendum = state.detail.addendum;
  const addendumId = addendum.addendum_id;
  try {
    await request(`/api/review/addendum/${addendumId}/complete`, {
      method: "POST", body: { submission_id: cashUUID(),
        expected_version: addendum.version, document_id: document.document_id },
    });
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
}

/* ---- document-entry forms (withdrawal / exchange) -------------------------- */

function cashAccountOptions(selected = "SAFE") {
  return state.options.cash_accounts.map(account =>
    `<option value="${esc(account.account_id)}"${account.account_id === selected
      ? " selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
}

function documentEntryForm(inboxDocument, kind) {
  const exchange = kind === "exchange";
  const label = exchange ? "currency exchange" : "bank withdrawal";
  return `<form id="document-entry-form" data-document-id="${esc(inboxDocument.document_id)}" class="review-form document-entry-form">
    <input type="hidden" name="entry_type" value="${kind}">
    <div class="document-entry-heading"><strong>Create new ${label}</strong>
      <span>${esc(documentName(inboxDocument))}</span></div>
    <div class="form-grid two">
      <label><span>Transaction date</span><input type="date" name="date"
        value="${cashToday()}" required></label>
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

/* ---- shell ------------------------------------------------------------------ */

function blockProcessingCommits() {
  const docs = [state.primaryDocument, ...state.stagedReceipts,
    detailDocument(state.pendingSignoutFiling?.document_id)].filter(Boolean);
  if (docs.some(cashDocumentPending)) {
    els.detail.querySelectorAll('button[type="submit"], #file-voucher, #file-addendum').forEach(button => {
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
    case "withdrawal":
    case "count": renderExchange(); break;
    case "signout": renderSignout(); break;
    case "addendum": renderAddendum(); break;
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
  clearSelection();
  syncUrl();
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
els.cancelPick.addEventListener("click", () => {
  state.documentPickTarget = null;
  updateEvidenceHeading();
  if (compactReviewLayout()) closeDocumentRail();
});
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
  clearSelection();
  setMode(tab.dataset.reviewMode);
  closeDocumentRail();
  syncUrl();
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
    await Promise.all([loadOptions(), loadWorklist(), loadInbox()]);
    if (new URLSearchParams(location.search).get("tab") === "reports") {
      setMode("submissions");
      renderWorklist();
    }
    const requested = new URLSearchParams(location.search).get("item");
    const requestedItem = requested
      ? state.items.find(item => keyFor(item) === requested) : null;
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
  if (document.hidden) return;
  loadInbox({ background: true }).catch(() => {});
}, 2000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadInbox({ background: true }).catch(() => {});
});
