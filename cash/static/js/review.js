/* Spreadsheet review: a document rail, stable transaction rows, and a
   selected-record workspace.

   Deliberately few paths: one document is selected in the rail; a slot inside
   an open record attaches the selected document (or uploads-and-attaches in
   one step). Receipt changes save with the unfinished expense; completing
   review files its documents. Removed documents can be restored with Undo. */

const els = {
  list: document.querySelector("#worklist tbody"),
  detail: document.getElementById("detail"),
  filter: document.getElementById("work-filter"),
  needFilter: document.getElementById("need-filter"),
  needFilterLabel: document.getElementById("need-filter-label"),
  summary: document.getElementById("queue-summary"),
  empty: document.getElementById("empty-worklist"),
  message: document.getElementById("review-message"),
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
  previousRecord: document.getElementById("previous-record"),
  nextRecord: document.getElementById("next-record"),
  tabs: [...document.querySelectorAll("[data-review-mode]")],
};

const state = {
  items: [],
  attachmentTargets: [],
  inbox: [],
  options: { expense_accounts: [], classes: [], cash_accounts: [], projects: [] },
  selectedKey: null,
  selectedItem: null,
  detail: null,
  activeDocument: null,
  primaryDocument: null,
  stagedReceipts: [],
  receiptsNeedSave: false,
  directSaveStatus: "",
  editRevision: 0,
  directRows: null,
  useVoucherAsReceipt: false,
  pendingSignoutFiling: null,
  uploadIntent: null,
  dirty: false,
  purposeDraft: null,
  purposeDirty: false,
  documentActionType: null,
  documentActionDirty: false,
  documentAttachSearch: false,
  documentReceiptChoices: new Map(),
  documentPickTarget: null,
  mode: "transactions",
  needFilter: "all",
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
  els.drawerIdentity.innerHTML = `<h2 id="review-drawer-title">${esc(item.id)}</h2>
    <p>${esc([item.type_label, item.label, item.secondary].filter(Boolean).join(" · "))}</p>
    ${amounts.length ? `<div class="review-drawer-amounts">${amounts.map(amount =>
      `<strong>${esc(amount)}</strong>`).join("")}</div>` : ""}
    ${(item.badges || []).length ? `<div class="badge-row">${item.badges.map(badge =>
      `<span class="badge">${esc(badge)}</span>`).join("")}</div>` : ""}`;
}

// Beside the table the drawer keeps the keyboard where it is, so arrow keys
// keep moving through the list. As a full-screen layer it takes focus and
// gives it back to the row that opened it.
let drawerOpener = null;
function setWorkspaceOpen(open) {
  const wasOpen = !els.drawer.hidden;
  els.drawer.hidden = !open;
  els.layout.classList.toggle("workspace-open", open);
  renderDrawerIdentity(open ? state.selectedItem : null);
  if (!compactReviewLayout()) return;
  if (open && !wasOpen) {
    drawerOpener = document.activeElement;
    els.closeDrawer.focus();
  } else if (!open && wasOpen) {
    (drawerOpener?.isConnected ? drawerOpener : els.list).focus?.();
    drawerOpener = null;
  }
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

let documentRailOpener = null;
function openDocumentRail(target = null) {
  if (target && state.documentActionDirty) {
    return afterLeavingDocumentEntry(() => openDocumentRail(target));
  }
  const wasOpen = els.evidencePane.classList.contains("mobile-open");
  if (!wasOpen) documentRailOpener = document.activeElement;
  state.documentPickTarget = target;
  if (target) {
    state.activeDocument = null;
    renderEvidenceList();
  }
  updateEvidenceHeading();
  if (compactReviewLayout()) els.evidencePane.classList.add("mobile-open");
  els.evidencePane.setAttribute("aria-hidden", "false");
  els.openDocuments.setAttribute("aria-expanded", "true");
  els.drawerDocuments.setAttribute("aria-expanded", "true");
  if (!wasOpen && compactReviewLayout()) els.closeDocuments.focus();
}

function closeDocumentRail() {
  const wasOpen = els.evidencePane.classList.contains("mobile-open");
  state.documentPickTarget = null;
  updateEvidenceHeading();
  els.evidencePane.classList.remove("mobile-open");
  syncResponsivePanes();
  if (wasOpen) {
    const fallback = state.selectedItem ? els.drawerDocuments : els.openDocuments;
    const opener = documentRailOpener?.isConnected && documentRailOpener.getClientRects().length
      ? documentRailOpener : fallback;
    opener?.focus();
  }
  documentRailOpener = null;
}

function syncResponsivePanes() {
  if (!compactReviewLayout()) els.evidencePane.classList.remove("mobile-open");
  const open = !compactReviewLayout() || els.evidencePane.classList.contains("mobile-open");
  els.evidencePane.setAttribute("aria-hidden", String(!open));
  els.openDocuments.setAttribute("aria-expanded", String(open));
  els.drawerDocuments.setAttribute("aria-expanded", String(open));
}

// Review messages are brief status updates, so every tone fades.
const showMessage = (text, tone = "success") =>
  cashNotice(els.message, text, tone, { fade: true });

// Undo for removing a staged document belongs to the open record.
let recordUndo = null;
function clearRecordUndo() {
  recordUndo?.settle();
  recordUndo = null;
}

function showUndo(text, onUndo) {
  els.message.hidden = true;
  recordUndo = cashUndoToast({ text, onUndo, ms: 8000 });
}

const request = cashApi;

function currentFilteredItems() {
  const needle = els.filter.value.trim().toLowerCase();
  return state.items.filter(item => {
    if (state.mode === "transactions" && item.kind === "expense_report") return false;
    if (state.mode === "submissions" && item.kind !== "expense_report") return false;
    if (state.mode === "transactions" && state.needFilter !== "all") {
      const badges = item.badges || [];
      const matches = state.needFilter === "codes"
        ? badges.some(badge => /^Needs codes$/i.test(badge))
        : badges.some(badge => /^Needs (receipt|voucher|count sheet|signed addendum)$/i.test(badge));
      if (!matches) return false;
    }
    if (!needle) return true;
    return [item.id, item.label, item.secondary, item.type_label,
            ...(item.badges || [])].join(" ").toLowerCase().includes(needle);
  });
}

/* ---- work list ---------------------------------------------------------- */

function updateRecordNavigation(visible = currentFilteredItems()) {
  const index = visible.findIndex(item => keyFor(item) === state.selectedKey);
  els.previousRecord.disabled = index <= 0;
  els.nextRecord.disabled = index < 0 || index >= visible.length - 1;
}

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
  updateRecordNavigation(visible);
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
  state.attachmentTargets = data.attachment_targets;
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
    if (background) await loadWorklist();
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
    const keepStaged = document => document &&
      document.state !== "discarded" && document.state !== "missing";
    if (state.primaryDocument) {
      const current = state.primaryDocument;
      state.primaryDocument = state.inbox.find(document =>
        document.document_id === current.document_id) ||
        state.detail?.documents?.find(document => document.document_id === current.document_id) ||
        (keepStaged(current) ? current : null);
    }
    state.stagedReceipts = state.stagedReceipts.map(staged =>
      state.inbox.find(document => document.document_id === staged.document_id) ||
      state.detail?.documents?.find(document => document.document_id === staged.document_id) ||
      (keepStaged(staged) ? staged : null))
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
  if (state.receiptsNeedSave) saveReceiptChanges();
}

/* ---- evidence rail ------------------------------------------------------- */

function documentStatus(document) {
  if (cashDocumentPending(document)) return "Document processing…";
  if (document.processing?.status === "failed") return "Processing failed · manual review";
  if (document.qr?.status === "matched") return `Suggested: ${document.qr.detected_id}`;
  if (document.state === "filed") return "Filed";
  return "No match";
}

function resetDocumentEntry() {
  state.documentActionType = null;
  state.documentActionDirty = false;
  state.documentAttachSearch = false;
}

const confirmDiscard = (title, message) => cashConfirm(title, {
  message, confirmLabel: "Discard", danger: true });
const confirmDiscardEntry = () => confirmDiscard("Discard this entry?",
  "The unfinished entry will be lost.");
const confirmDiscardChanges = () => confirmDiscard("Discard unsaved changes?",
  "Your edits to this record have not been saved.");

// Runs proceed() at once, or after the person agrees to discard an unfinished
// entry. Keeps the clean path synchronous for actions that open a file picker.
function afterLeavingDocumentEntry(proceed) {
  if (!state.documentActionDirty) { resetDocumentEntry(); return proceed(); }
  return confirmDiscardEntry().then(agreed => {
    if (!agreed) return undefined;
    resetDocumentEntry();
    return proceed();
  });
}

async function leaveDocumentEntry() {
  if (state.documentActionDirty && !(await confirmDiscardEntry())) return false;
  resetDocumentEntry();
  return true;
}

async function pickDocument(document) {
  const target = state.documentPickTarget;
  state.documentPickTarget = null;
  updateEvidenceHeading();
  await applyDocument(target, document);
  if (!compactReviewLayout()) state.activeDocument = document;
  closeDocumentRail();
  renderDocumentRail();
}

async function openDocument(document) {
  if (!(await leaveDocumentEntry())) return;
  try {
    await loadWorklist();
    state.activeDocument = document;
    renderDocumentRail();
    openDocumentRail();
  } catch (error) {
    showMessage(error.message, "danger");
  }
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
  if (!(await cashConfirm("Delete this document?", { message: `${documentName(document)} will be removed from the inbox.`,
    confirmLabel: "Delete", danger: true }))) return;
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
  const canFile = isInbox && !isStagedHere(document)
    && !cashDocumentPending(document) && state.mode === "transactions";
  const suggested = canFile ? suggestedTransaction(document) : null;
  return `<nav class="document-preview-nav" aria-label="Document navigation">
      <button type="button" class="text-button" id="back-to-documents">${cashPageViewer.icon('previous')}<span data-i18n="Back to documents">${esc(window.cashI18n ? cashI18n.t("Back to documents") : "Back to documents")}</span></button>
      <span><button type="button" class="btn ghost" id="previous-document" aria-label="Previous document" ${index <= 0 ? "disabled" : ""}>${cashPageViewer.icon('previous')}</button>
      <button type="button" class="btn ghost" id="next-document" aria-label="Next document" ${index < 0 || index >= state.inbox.length - 1 ? "disabled" : ""}>${cashPageViewer.icon('next')}</button></span>
    </nav>
    <div class="document-preview-title"><strong>${esc(documentName(document))}</strong>
      ${cashDocumentProcessing(document) || `<span>${esc(documentStatus(document))}</span>`}
      <div class="document-preview-tools"><a href="${esc(url)}" target="_blank" rel="noopener" data-i18n="Open original">${esc(window.cashI18n ? cashI18n.t("Open original") : "Open original")}</a>
      ${isInbox ? `<details class="document-overflow"><summary aria-label="Document actions">•••</summary>
        <button type="button" class="text-button danger delete-inbox-document" aria-label="Delete ${esc(documentName(document))}">Delete document</button></details>` : ""}</div>
    </div>
    ${canFile ? documentActions(document, suggested) : ""}
    ${cashPageViewer.markup()}`;
}

// Attach an existing record, or create a new one. One filled primary at most;
// every other choice uses the same block shape.
function documentActions(document, suggested) {
  return `<div class="document-attachment-actions">
    ${suggested ? suggestedDocumentMatch(document, suggested) : ""}
    <button type="button" class="btn action-block${suggested ? "" : " primary"}" id="attach-to-transaction">${suggested
      ? "Choose another transaction…"
      : "Attach to an existing transaction…"}</button>
    <section id="document-transaction-picker" ${state.documentAttachSearch ? "" : "hidden"}>
      <label><span>Find an existing transaction</span><input type="search" id="document-transaction-search" placeholder="Date, type, amount, person, or record…" autocomplete="off"></label>
      <div id="document-transaction-results"></div>
    </section>
    <div class="document-create-group">
      <h3 class="document-create-heading">Create a new transaction</h3>
      <div class="document-create-actions">
        <button type="button" class="btn action-block record-document-entry" data-entry-type="withdrawal"
                aria-pressed="${state.documentActionType === "withdrawal"}">
          <span class="create-record-mark" aria-hidden="true">+</span>
          <span class="create-record-label">Bank withdrawal</span></button>
        <button type="button" class="btn action-block record-document-entry" data-entry-type="exchange"
                aria-pressed="${state.documentActionType === "exchange"}">
          <span class="create-record-mark" aria-hidden="true">+</span>
          <span class="create-record-label">Currency exchange</span></button>
      </div>
      ${state.documentActionType ? documentEntryForm(document, state.documentActionType) : ""}
    </div>
  </div>`;
}

function suggestedDocumentMatch(document, suggested) {
  return `<div class="document-match">
    <span class="document-match-label">Suggested transaction:</span>
    <strong>${esc(document.qr.detected_id)}${suggested.type_label ? ` · ${esc(suggested.type_label)}` : ""}</strong>
    <button type="button" class="btn action-block primary" id="confirm-suggested-attachment">Attach to ${esc(document.qr.detected_id)}</button>
    ${suggested.kind === "direct_expense" ? `<label class="document-confirm-receipt">
      <input type="checkbox" id="suggested-voucher-as-receipt" ${state.documentReceiptChoices.get(document.document_id) ? "checked" : ""}>
      <span>Also file as a receipt</span></label>` : ""}
  </div>`;
}

function suggestedTransaction(document) {
  if (document.qr?.status !== "matched" || document.state !== "inbox") return null;
  return state.attachmentTargets.find(item => ["signout", "addendum", "direct_expense", "count", "settlement"].includes(item.kind)
    && document.qr.detected_id === (item.kind === "count" ? `Txn-${item.id}` : item.id)) || null;
}

async function confirmDocumentAttachment(item, document, button) {
  if (!item || cashDocumentPending(document)) return;
  const receiptChoice = item.kind === "direct_expense"
    ? { use_voucher_as_receipt: Boolean(state.documentReceiptChoices.get(document.document_id)) } : {};
  button.disabled = true;
  try {
    if (!(await leaveDocumentEntry())) return;
    if ((state.dirty || state.purposeDirty) && !(await confirmDiscardChanges())) return;
    if (item.kind === "settlement") {
      await cashCommand(`/${item.voucher_id}/api/settlement/file/${item.settlement_kind}`, {
        document_id: document.document_id, paper_id: item.id });
    } else {
      const detail = await request(`/api/review/item/${item.kind}/${item.id}`);
      const record = detail.voucher || detail.addendum || detail.transaction;
      const action = ["signout", "direct_expense"].includes(item.kind) ? "attach-voucher" : "complete";
      await cashCommand(`/api/review/${item.kind.replaceAll("_", "-")}/${item.id}/${action}`, {
        expected_version: record.version, document_id: document.document_id, ...receiptChoice });
    }
    state.documentReceiptChoices.delete(document.document_id);
    await afterCommit("Attachment confirmed", false);
  } catch (error) {
    showMessage(error.message, "danger");
  } finally { button.disabled = false; }
}

async function attachDocumentToTransaction(item, target, selectedDocument, button) {
  if (item.kind === "settlement") {
    await confirmDocumentAttachment(item, selectedDocument, button);
    return;
  }
  if (!(await leaveDocumentEntry())) return;
  if (state.selectedKey !== keyFor(item)) await selectItem(item);
  if (state.selectedKey !== keyFor(item) || !state.detail) return;
  // Direct expenses keep their existing coding and completion checks.
  state.documentAttachSearch = false;
  state.activeDocument = null;
  await applyDocument(target, selectedDocument);
  if (!compactReviewLayout()) state.activeDocument = selectedDocument;
  closeDocumentRail();
  renderDocumentRail();
}

function renderTransactionMatches() {
  const input = document.getElementById("document-transaction-search");
  if (!input) return;
  const tokens = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const candidates = state.attachmentTargets.filter(item => tokens.every(token =>
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
    result.querySelectorAll("button").forEach(button => button.addEventListener("click", () =>
      attachDocumentToTransaction(item, button.dataset.target, state.activeDocument, button)));
    list.appendChild(result);
  }
}

function wireInlineDocumentViewer(container, document) {
  cashPageViewer.mount(container.querySelector(".page-viewer"), document);
  container.querySelector("#suggested-voucher-as-receipt")?.addEventListener("change", event =>
    state.documentReceiptChoices.set(document.document_id, event.target.checked));
  container.querySelector("#confirm-suggested-attachment")?.addEventListener("click", event =>
    confirmDocumentAttachment(suggestedTransaction(document), document, event.currentTarget));
  container.querySelector("#back-to-documents").addEventListener("click", async () => {
    if (!(await leaveDocumentEntry())) return;
    state.activeDocument = null;
    renderDocumentRail();
  });
  const index = state.inbox.findIndex(item => item.document_id === document.document_id);
  container.querySelector("#previous-document").addEventListener("click", () => openDocument(state.inbox[index - 1]));
  container.querySelector("#next-document").addEventListener("click", () => openDocument(state.inbox[index + 1]));
  container.querySelector(".delete-inbox-document")?.addEventListener("click", event =>
    deleteInboxDocument(document, event.currentTarget));
  container.querySelector("#attach-to-transaction")?.addEventListener("click", async () => {
    if (!(await leaveDocumentEntry())) return;
    state.documentAttachSearch = true;
    renderEvidenceList();
    window.document.getElementById("document-transaction-search").focus();
  });
  container.querySelector("#document-transaction-search")?.addEventListener("input", renderTransactionMatches);
  container.querySelectorAll(".record-document-entry").forEach(button => {
    button.addEventListener("click", async () => {
      if (!(await leaveDocumentEntry())) return;
      state.documentActionType = button.dataset.entryType;
      renderDocumentRail();
    });
  });
  if (state.documentAttachSearch) renderTransactionMatches();
  if (state.documentActionType) wireDocumentEntryForm(document);
  window.cashI18n?.apply();
}

/* ---- document upload & staging ------------------------------------------- */

async function stageDocuments(documents) {
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
    clearRecordUndo();
    markDirty();
  } else if (list.length) {
    showMessage("That document is already staged here.", "warning");
  }
  if (state.selectedItem?.kind === "direct_expense") renderDirectExpense();
  blockProcessingCommits();
  if (added.length) await saveReceiptChanges();
}

function unstageReceipt(documentId) {
  captureDirectRows();
  const removed = state.stagedReceipts.find(document => document.document_id === documentId);
  const undo = undoableRemoval(`${documentName(removed)} removed.`, () => {
    renderDirectExpense();
    saveReceiptChanges();
  });
  state.stagedReceipts = state.stagedReceipts.filter(document =>
    document.document_id !== documentId);
  markDirty();
  renderDirectExpense();
  undo();
  saveReceiptChanges();
}

// Upload a file to the inbox, select it, then use it as the upload intended.
async function uploadThen(file, use) {
  showMessage("Uploading document…");
  try {
    const formData = new FormData();
    formData.append("files", file, file.name);
    const created = await request("/api/documents/capture-merge", { method: "POST", formData });
    await loadInbox();
    const uploaded = state.inbox.find(item => item.document_id === created.document_id);
    if (!uploaded) throw new Error("Uploaded document was not found");
    state.activeDocument = uploaded;
    await use(uploaded);
    renderDocumentRail();
  } catch (error) {
    showMessage(error.message, "danger");
  }
}

const uploadAndStage = file => uploadThen(file, stageDocuments);
const uploadForSlot = (file, target) => uploadThen(file, uploaded =>
  applyDocument(target, uploaded, { allowProcessing: true }));
const uploadToRail = file => uploadThen(file, () =>
  showMessage("Document saved to the inbox and selected."));

/* ---- row selection -------------------------------------------------------- */

// Forget the open record and everything staged for it.
function clearSelection() {
  clearRecordUndo();
  Object.assign(state, {
    selectedKey: null, selectedItem: null, detail: null, dirty: false,
    purposeDraft: null, purposeDirty: false, pendingSignoutFiling: null,
    stagedReceipts: [], primaryDocument: null, directRows: null,
    receiptsNeedSave: false, directSaveStatus: "",
    useVoucherAsReceipt: false, activeDocument: null, documentActionType: null,
    documentActionDirty: false,
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
  const reports = mode === "submissions";
  els.workHeading.textContent = reports ? "Expense reports" : "Transactions";
  els.tabs.forEach(button => button.classList.toggle("active", button.dataset.reviewMode === mode));
  els.list.closest("table").classList.toggle("reports-mode", reports);
  els.needFilter.disabled = reports;
  els.needFilterLabel.hidden = reports;
}

function markDirty() {
  state.dirty = true;
  state.editRevision += 1;
  state.directSaveStatus = "";
  const output = els.detail.querySelector("#direct-expense-form .form-status");
  if (output) output.textContent = "";
}

async function selectAdjacentRecord(direction) {
  const visible = currentFilteredItems();
  if (!visible.length) return;
  const current = visible.findIndex(item => keyFor(item) === state.selectedKey);
  const next = current < 0 ? 0 : current + direction;
  if (next < 0 || next >= visible.length) return;
  await selectItem(visible[next]);
}

async function selectItem(item, { force = false } = {}) {
  await waitForDirectSave();
  if (item.kind === "expense_report") {
    if (!force && (state.dirty || state.purposeDirty)
        && !(await confirmDiscardChanges())) return;
    window.location.assign(item.url);
    return;
  }
  const itemKey = keyFor(item);
  if (!force && (state.dirty || state.purposeDirty)
      && !(await confirmDiscardChanges())) return;
  if (!force && !(await leaveDocumentEntry())) return;
  clearRecordUndo();
  state.stagedReceipts = [];
  state.receiptsNeedSave = false;
  state.directSaveStatus = "";
  state.primaryDocument = null;
  state.directRows = null;
  state.useVoucherAsReceipt = false;
  state.pendingSignoutFiling = null;
  state.purposeDraft = null;
  state.purposeDirty = false;
  if (!force && state.selectedKey === itemKey) {
    state.dirty = false;
    state.purposeDirty = false;
    state.selectedItem = null;
    state.selectedKey = null;
    state.detail = null;
    syncUrl();
    renderWorklist();
    renderEvidenceList();
    return;
  }
  state.dirty = false;
  state.purposeDirty = false;
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
    if (item.kind === "direct_expense") {
      state.primaryDocument = detail.documents.find(document => document.roles?.includes("voucher"))
        || state.primaryDocument;
      if (state.primaryDocument?.roles?.includes("support")) state.useVoucherAsReceipt = true;
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

const documentShortMeta = document => {
  const pages = `${document.page_count || "?"} page${document.page_count === 1 ? "" : "s"}`;
  return document.qr?.message ? `${pages} · ${document.qr.message}` : pages;
};

const isStagedHere = document => Boolean(document) && (
  state.primaryDocument?.document_id === document.document_id ||
  state.pendingSignoutFiling?.document_id === document.document_id ||
  state.stagedReceipts.some(item => item.document_id === document.document_id));

// The Documents list at the top of an open record: one compact row per
// document or missing document. Clicking a document previews it in the left
// pane. An empty row offers an Attach menu (from the inbox, or upload); a file
// or an inbox card can also be dropped on any row.
function documentRow({ target, label, noun, document, staged = false, extra = "",
    removeClass = "remove-document-slot", removeLabel = null }) {
  if (!document) return emptyRow(target, label, noun);
  const name = documentName(document);
  const active = state.activeDocument?.document_id === document.document_id;
  return `<li class="doc-row filled document-drop-slot${staged ? " staged-receipt" : ""}${active ? " active" : ""}"
      data-document-slot="${target}">
    <button type="button" class="doc-open" data-document-id="${esc(document.document_id)}"
        aria-label="Preview ${esc(removeLabel || label)}: ${esc(name)}"${active ? ' aria-current="true"' : ""}>
      <span class="doc-thumb" aria-hidden="true"><span>PDF</span><img loading="lazy" alt=""
        src="/api/documents/${esc(document.document_id)}/thumbnail?v=${esc(document.sha256 || "")}"></span>
      <span class="doc-copy"><strong class="doc-label">${esc(label)}</strong>
        <span class="doc-name" title="${esc(name)}">${esc(name)}</span>
        <span class="doc-meta">${esc(documentShortMeta(document))}</span></span>
    </button>
    ${document.state === "filed" ? "" : `<button type="button" class="text-button danger ${removeClass}" data-document-slot="${target}"
        data-document-id="${esc(document.document_id)}"
        aria-label="Remove ${esc(removeLabel || label)}">Remove</button>`}
    ${cashDocumentProcessing(document)}${extra}
  </li>`;
}

function attachMenu(target, noun, { text = "Attach", toggleLabel = `Attach ${noun}` } = {}) {
  return `<div class="attach-menu" data-attach-menu="${esc(noun)}">
    <button type="button" class="btn ghost attach-toggle" aria-haspopup="true" aria-expanded="false"
        aria-label="${esc(toggleLabel)}">${esc(text)}</button>
    <div class="attach-options" hidden>
      <button type="button" class="slot-choose" data-document-slot="${target}"
          aria-label="Choose ${esc(noun)} from inbox">From inbox…</button>
      <button type="button" class="slot-upload" data-document-slot="${target}"
          aria-label="Upload ${esc(noun)}">Upload…</button>
    </div></div>`;
}

function emptyRow(target, label, noun) {
  return `<li class="doc-row empty document-drop-slot" data-document-slot="${target}">
    <span class="doc-copy"><strong class="doc-label">${esc(label)}</strong>
      <span class="doc-none">None yet</span></span>
    ${attachMenu(target, noun)}
  </li>`;
}

// The link that follows the receipts already added.
function addReceiptRow() {
  return `<li class="doc-row add document-drop-slot" data-document-slot="support">
    ${attachMenu("support", "receipt", { text: "+ Add another receipt", toggleLabel: "Add another receipt" })}
  </li>`;
}

const documentList = rows => `<section class="doc-list" aria-label="Documents">
  <ul>${rows.join("")}</ul></section>`;

function closeAttachMenus(except = null) {
  document.querySelectorAll(".attach-menu").forEach(menu => {
    if (menu === except) return;
    menu.querySelector(".attach-options").hidden = true;
    menu.querySelector(".attach-toggle").setAttribute("aria-expanded", "false");
  });
}
document.addEventListener("click", event => {
  if (!event.target.closest(".attach-menu")) closeAttachMenus();
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const open = document.querySelector(".attach-options:not([hidden])");
  if (!open) return;
  const toggle = open.parentElement.querySelector(".attach-toggle");
  closeAttachMenus();
  toggle.focus();
  event.stopPropagation();
}, true);

// Removing a staged document is undoable: nothing was filed, so Undo puts the
// same documents back.
function undoableRemoval(text, restoreState) {
  const snapshot = { primary: state.primaryDocument, receipts: [...state.stagedReceipts],
    voucherAsReceipt: state.useVoucherAsReceipt, filing: state.pendingSignoutFiling };
  return () => showUndo(text, () => {
    captureDirectRows();
    Object.assign(state, { primaryDocument: snapshot.primary, stagedReceipts: snapshot.receipts,
      useVoucherAsReceipt: snapshot.voucherAsReceipt, pendingSignoutFiling: snapshot.filing });
    markDirty();
    restoreState();
  });
}

function previewRecordDocument(documentId) {
  const found = detailDocument(documentId) ||
    [state.primaryDocument, ...state.stagedReceipts].find(item => item?.document_id === documentId);
  if (!found) return;
  captureDirectRows();
  openDocument(found);
}

function wireDocumentSlots(root) {
  root.querySelectorAll(".doc-thumb img").forEach(image =>
    image.addEventListener("error", () => { image.hidden = true; }));
  root.querySelectorAll(".doc-open").forEach(button =>
    button.addEventListener("click", () => previewRecordDocument(button.dataset.documentId)));
  root.querySelectorAll(".attach-toggle").forEach(toggle => {
    toggle.addEventListener("click", () => {
      const menu = toggle.parentElement;
      const options = menu.querySelector(".attach-options");
      const opening = options.hidden;
      closeAttachMenus(menu);
      options.hidden = !opening;
      toggle.setAttribute("aria-expanded", String(opening));
      if (opening) options.querySelector("button").focus();
    });
  });
  root.querySelectorAll(".slot-upload").forEach(button => {
    button.addEventListener("click", () => {
      closeAttachMenus();
      afterLeavingDocumentEntry(() => {
        state.uploadIntent = button.dataset.documentSlot;
        els.picker.value = "";
        els.picker.click();
      });
    });
  });
  root.querySelectorAll(".slot-choose").forEach(button => {
    button.addEventListener("click", () => {
      closeAttachMenus();
      captureDirectRows();
      openDocumentRail(button.dataset.documentSlot);
    });
  });
  root.querySelectorAll(".remove-document-slot").forEach(button => {
    button.addEventListener("click", () => {
      if (button.dataset.documentSlot !== "primary") return;
      captureDirectRows();
      const undo = undoableRemoval(`${documentName(state.primaryDocument)} removed.`, renderDetail);
      state.primaryDocument = null;
      state.useVoucherAsReceipt = false;
      markDirty();
      renderDetail();
      undo();
    });
  });
  root.querySelectorAll(".unstage-receipt").forEach(button => {
    button.addEventListener("click", () => unstageReceipt(button.dataset.documentId));
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
      if (dropped) {
        await applyDocument(target, dropped);
        if (!compactReviewLayout()) state.activeDocument = dropped;
        closeDocumentRail();
        renderDocumentRail();
      }
    });
  });
}

async function applyDocument(target, document, { allowProcessing = false } = {}) {
  if (cashDocumentPending(document) && !allowProcessing) {
    showMessage("Document processing… Please wait before attaching it.");
    return;
  }
  if (!state.selectedItem || !document) {
    showMessage("Choose a document first.", "warning");
    return;
  }
  if (!state.inbox.some(item => item.document_id === document.document_id)) {
    showMessage("That document has already been filed.", "warning");
    return;
  }
  if (target === "support") {
    await stageDocuments(document);
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
    if (state.primaryDocument?.state === "filed") {
      showMessage("This voucher is already filed", "warning");
      return;
    }
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
    await cashCommand(`/api/review/signout/${filing.voucher_id}/attach-voucher`, {
      expected_version: filing.expected_version, document_id: filing.document_id });
    state.pendingSignoutFiling = null;
    await afterCommit(`${filing.voucher_id} voucher filed.`, true);
  } catch (error) { showMessage(error.message, "danger"); }
}

/* ---- direct expense -------------------------------------------------------- */

let directSavePromise = null;

async function waitForDirectSave() {
  while (directSavePromise) {
    try { await directSavePromise; } catch (_) { /* Unsaved changes stay guarded. */ }
  }
}

function directSaveStatus(text) {
  state.directSaveStatus = text;
  const output = els.detail.querySelector("#direct-expense-form .form-status");
  if (output) output.textContent = text;
}

function directDocumentsProcessing() {
  return [state.primaryDocument, ...state.stagedReceipts].some(cashDocumentPending);
}

function syncDirectSaveControls() {
  const disabled = Boolean(directSavePromise) || directDocumentsProcessing();
  els.detail.querySelectorAll('#direct-expense-form [type="submit"], #print-direct-voucher')
    .forEach(button => { button.disabled = disabled; });
}

async function saveReceiptChanges() {
  if (state.selectedItem?.kind !== "direct_expense") return;
  const detail = state.detail;
  state.receiptsNeedSave = true;
  if (directDocumentsProcessing()) {
    showMessage("Document processing…");
    return;
  }
  state.receiptsNeedSave = false;
  try {
    await saveDirectExpense(false, { automatic: true });
    if (state.detail === detail) els.message.hidden = true;
  } catch (error) {
    showMessage(error.message, "danger");
  }
}

// Receipt changes and explicit saves share one queue and the current version.
// A save only clears edits that were included in its request.
function saveDirectExpense(finishing, { automatic = false } = {}) {
  const detail = state.detail;
  const save = (directSavePromise || Promise.resolve()).catch(() => {}).then(async () => {
    if (state.detail !== detail) return false;
    captureDirectRows();
    if (state.directRows.some(cashRowFields.invalid)) {
      throw new Error("Enter amounts with at most two decimal places.");
    }
    directSaveStatus("Saving…");
    const { voucher } = detail;
    const revision = state.editRevision;
    const body = { expected_version: voucher.version,
      voucher_notes: document.getElementById("direct-voucher-notes")?.value ?? voucher.notes,
      rows: state.directRows, use_voucher_as_receipt: state.useVoucherAsReceipt,
      receipt_document_ids: state.stagedReceipts.map(document => document.document_id) };
    const voucherAttached = Boolean(state.primaryDocument &&
      ["inbox", "filed"].includes(state.primaryDocument.state));
    // Codes and documents can be saved before the expense is ready to complete.
    if (!finishing || !voucherAttached) {
      body.primary_document_id = voucherAttached ? state.primaryDocument.document_id : null;
      const saved = await cashCommand(`/api/review/direct-expense/${voucher.voucher_id}/draft`, body);
      voucher.version = saved.version;
      detail.review_draft = { ...detail.review_draft, voucher_notes: body.voucher_notes };
      if (saved.purpose) voucher.purpose = saved.purpose;
      if (state.detail !== detail) return { completed: false };
      state.dirty = state.editRevision !== revision;
      await loadWorklist();
      if (state.detail !== detail) return { completed: false };
      state.selectedItem = state.items.find(item => keyFor(item) === state.selectedKey);
      renderWorklist();
      await loadInbox({ background: true });
      window.cashRefreshNavCounts?.();
      if (state.detail === detail) directSaveStatus(state.dirty ? "" : automatic
        ? "All changes saved"
        : finishing
        ? "Progress saved. Attach the signed voucher to finish. This expense stays in the queue until then."
        : "Progress saved. The expense stays open until completed.");
      return { completed: false };
    }
    body.voucher_document_id = state.primaryDocument.document_id;
    await cashCommand(`/api/review/direct-expense/${voucher.voucher_id}/complete`, body);
    return { completed: true };
  });
  const pending = save.catch(error => {
    if (state.detail === detail) directSaveStatus(error.message);
    throw error;
  }).finally(() => {
    if (directSavePromise === pending) directSavePromise = null;
    syncDirectSaveControls();
  });
  directSavePromise = pending;
  syncDirectSaveControls();
  return pending;
}

function renderDirectExpense() {
  const { voucher } = state.detail;
  const receipts = state.stagedReceipts;
  const rows = state.directRows?.length ? state.directRows : [blankDirectRow()];
  const editors = rows.map(row => `<section class="direct-row">
      ${cashRowFields.html(row, { codes: state.options,
        show: { date: false, vendor: false, purpose: true, project: true },
        purposeMax: 100, purposeDisabled: voucher.purpose_locked })}
    </section>`).join("");
  const voucherToggle = state.primaryDocument ? `<label class="doc-toggle"
      title="Only if the vendor signed the acknowledgment.">
      <input type="checkbox" id="use-voucher-as-receipt" ${state.useVoucherAsReceipt ? "checked" : ""}
        ${state.primaryDocument.roles?.includes("support") ? "disabled" : ""}>
      <span>Also file as a receipt</span></label>` : "";
  const documents = [
    documentRow({ target: "primary", label: "Voucher", noun: "voucher",
      document: state.primaryDocument, extra: voucherToggle }),
    ...receipts.map((document, index) => documentRow({ target: "support",
      label: `Receipt ${index + 1}`, noun: "receipt", document, staged: true,
      removeClass: "unstage-receipt" })),
    receipts.length ? addReceiptRow() : emptyRow("support", "Receipt", "receipt"),
  ];
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <button type="button" class="btn ghost" id="print-direct-voucher">Print voucher</button>
    </nav>${documentList(documents)}
    <form id="direct-expense-form" class="review-form">
    <section class="sheet-editor-section">
      <p class="field-help">Paid: USD ${cashMoney(state.detail.cash?.usd ?? voucher.authorized_usd)} · MXN ${cashMoney(state.detail.cash?.mxn ?? voucher.authorized_mxn)}</p>
      ${!state.options.expense_accounts.length ? `<p class="callout danger">No account codes are available.</p>` : ""}
      ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one in Settings before completing this expense.</p>` : ""}
      ${editors}
      <label><span>Notes</span><textarea id="direct-voucher-notes" ${voucher.purpose_locked ? "disabled" : ""}>${esc(state.detail.review_draft?.voucher_notes ?? voucher.notes ?? "")}</textarea></label>
    </section>
    <div class="row-actions"><output class="form-status" role="alert">${esc(state.directSaveStatus)}</output>
      <button type="submit" class="btn ghost" value="draft" formnovalidate>Save progress</button>
      <button type="submit" class="btn primary">Save &amp; review next</button>
    </div>
    </form>
  `;
  document.getElementById("use-voucher-as-receipt")?.addEventListener("change", event => {
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
    const saveButton = event.submitter || form.querySelector('.btn.primary[type="submit"]');
    if (saveButton.disabled) return;
    try {
      const saved = await saveDirectExpense(saveButton.value !== "draft");
      if (saved?.completed) {
        await afterCommit(`${voucher.voucher_id} completed.`, true);
      }
    } catch (error) {
      els.detail.querySelector(".form-status")?.scrollIntoView({ block: "nearest" });
    }
  });
  const printButton = document.getElementById("print-direct-voucher");
  printButton.addEventListener("click", async () => {
    const url = `/voucher/${encodeURIComponent(voucher.voucher_id)}/print`;
    if (!state.dirty) {
      window.open(url, "_blank", "noopener");
      return;
    }
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    printButton.disabled = true;
    try {
      if (await saveDirectExpense(false)) {
        if (tab) tab.location.replace(url);
        else window.location.assign(url);
      } else if (tab) {
        tab.close();
      }
    } catch (error) {
      if (tab) tab.close();
      showMessage(error.message, "danger");
    } finally {
      printButton.disabled = false;
    }
  });
  blockProcessingCommits();
  syncDirectSaveControls();
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
      ${documentList([documentRow({ target: "primary", document: state.primaryDocument,
        label: count ? "Signed count sheet" : withdrawal ? "Withdrawal receipt" : "Exchange receipt",
        noun: count ? "count sheet" : withdrawal ? "withdrawal receipt" : "exchange receipt" })])}
      <div class="record-summary">
        <p>${count
          ? `Safe counted on ${esc(transaction.date)}: ${difference.length ? `difference ${esc(difference.join(" · "))}` : "matched the records"}.`
          : withdrawal
          ? `USD ${cashMoney(transaction.usd)} withdrawn from the bank on ${esc(transaction.date)}.`
          : `USD ${cashMoney(transaction.usd)} exchanged for MXN ${cashMoney(-Number(transaction.mxn))} from ${esc(transaction.from_account)} on ${esc(transaction.date)}.`}</p>
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
      await cashCommand(`/api/review/${kind}/${transaction.txn_id}/complete`, {
        expected_version: transaction.version, document_id: state.primaryDocument.document_id });
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
  if (state.purposeDraft === null) state.purposeDraft = voucher.purpose;
  const locked = Boolean(voucher.purpose_locked);
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <button type="button" class="btn ghost" id="print-signout-voucher">Print voucher</button>
      <a class="btn ghost" href="/${esc(voucher.voucher_id)}">Open expense report</a>
    </nav>
    <form id="signout-purpose-form" class="review-form">
    <section class="sheet-editor-section">
      <div class="form-grid two">
        <label class="full"><span>Purpose</span>
          <input name="purpose" maxlength="100" value="${esc(state.purposeDraft)}"
            ${locked ? "readonly" : "required"}></label>
      </div>
    </section>
    ${locked ? "" : `<div class="row-actions"><output class="form-status" role="alert"></output>
      <button type="submit" class="btn ghost">Save purpose</button></div>`}
    </form>
    ${documentList([documentRow({ target: "primary", label: "Signed voucher", noun: "voucher",
      document: filing ? detailDocument(filing.document_id) : null, removeClass: "cancel-filing" })])}
    <div class="row-actions"><output class="form-status" role="alert"></output>
      ${filing ? '<button type="button" class="btn primary" id="file-voucher">File voucher</button>' : ""}
    </div>`;
  const purposeForm = document.getElementById("signout-purpose-form");
  const purposeStatus = purposeForm.querySelector(".form-status");
  const savePurpose = async () => {
    if (locked) return true;
    const input = purposeForm.elements.purpose;
    if (input.value === voucher.purpose) {
      state.purposeDirty = false;
      return true;
    }
    const button = purposeForm.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    if (purposeStatus) purposeStatus.textContent = "Saving…";
    try {
      const saved = await cashCommand(
        `/api/vouchers/${encodeURIComponent(voucher.voucher_id)}/purpose`,
        { expected_version: voucher.version, purpose: input.value },
        { method: "PATCH" });
      voucher.version = saved.version;
      voucher.purpose = saved.purpose;
      state.purposeDraft = saved.purpose;
      state.purposeDirty = false;
      if (state.pendingSignoutFiling)
        state.pendingSignoutFiling.expected_version = voucher.version;
      await loadWorklist();
      renderWorklist();
      renderSignout();
      showMessage("Purpose saved.");
      return true;
    } catch (error) {
      if (purposeStatus) purposeStatus.textContent = error.message;
      showMessage(error.message, "danger");
      return false;
    } finally {
      if (button) button.disabled = false;
    }
  };
  if (!locked) {
    purposeForm.elements.purpose.addEventListener("input", event => {
      state.purposeDraft = event.target.value;
      state.purposeDirty = true;
    });
  }
  purposeForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!locked) await savePurpose();
  });
  document.getElementById("print-signout-voucher").addEventListener("click", async event => {
    const url = `/voucher/${encodeURIComponent(voucher.voucher_id)}/print`;
    if (!state.purposeDirty) {
      window.open(url, "_blank", "noopener");
      return;
    }
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      if (await savePurpose()) {
        if (tab) tab.location.replace(url);
        else window.location.assign(url);
      } else if (tab) {
        tab.close();
      }
    } finally {
      button.disabled = false;
    }
  });
  if (filing) {
    document.getElementById("file-voucher")
      .addEventListener("click", async event => {
        event.target.disabled = true;
        try {
          if (state.purposeDirty && !(await savePurpose())) return;
          await confirmSignoutFiling();
        } finally { event.target.disabled = false; }
      });
    els.detail.querySelector(".cancel-filing").addEventListener("click", () => {
      const removed = filing;
      state.pendingSignoutFiling = null;
      state.dirty = false;
      renderSignout();
      showUndo(`${documentName(detailDocument(removed.document_id))} removed.`, () => {
        state.pendingSignoutFiling = removed;
        markDirty();
        renderSignout();
      });
    });
  }
  wireDocumentSlots(els.detail);
}

/* ---- addendum -------------------------------------------------------------- */

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/voucher/${esc(addendum.voucher_id)}/addendum/${esc(addendum.addendum_id)}/print" target="_blank" rel="noopener">Print addendum</a>
    </nav>${documentList([documentRow({ target: "primary", label: "Signed addendum", noun: "addendum",
      document: state.primaryDocument })])}
    <div class="row-actions"><output class="form-status" role="alert"></output>
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
    await cashCommand(`/api/review/addendum/${addendumId}/complete`, {
      expected_version: addendum.version, document_id: document.document_id });
    await afterCommit(`${addendumId} filed.`, true);
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
    const body = { date: data.get("date"),
      usd: data.get("usd"), notes: data.get("notes"),
      document_id: inboxDocument.document_id };
    if (kind === "exchange") Object.assign(body, {
      mxn: data.get("mxn"), source_account: data.get("source_account") });
    const status = form.querySelector("output");
    status.textContent = "Saving…";
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await cashCommand(`/api/entries/${kind}`, body);
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

async function afterCommit(text, advance) {
  const oldVisible = currentFilteredItems();
  const oldIndex = oldVisible.findIndex(item => keyFor(item) === state.selectedKey);
  clearSelection();
  syncUrl();
  setWorkspaceOpen(false);
  renderDocumentRail();
  await Promise.all([loadWorklist(), loadInbox(), loadOptions()]);
  window.cashRefreshNavCounts?.();
  showMessage(text);
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

els.browse.addEventListener("click", () => afterLeavingDocumentEntry(() => {
  state.uploadIntent = state.documentPickTarget || "inbox";
  els.picker.value = "";
  els.picker.click();
}));
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
  if (intent !== "inbox") closeDocumentRail();
});
els.drop.addEventListener("dragover", event => {
  event.preventDefault(); els.drop.classList.add("dragging");
});
els.drop.addEventListener("dragleave", () => els.drop.classList.remove("dragging"));
els.drop.addEventListener("drop", async event => {
  event.preventDefault(); els.drop.classList.remove("dragging");
  const file = event.dataTransfer.files[0];
  if (!file || !(await leaveDocumentEntry())) return;
  if (state.documentPickTarget) {
    await uploadForSlot(file, state.documentPickTarget);
    closeDocumentRail();
  } else await uploadToRail(file);
});
els.cancelPick.addEventListener("click", () => {
  state.documentPickTarget = null;
  updateEvidenceHeading();
  closeDocumentRail();
});
els.filter.addEventListener("input", renderWorklist);
els.needFilter.addEventListener("change", () => {
  state.needFilter = els.needFilter.value;
  renderWorklist();
});
els.openDocuments.addEventListener("click", () => openDocumentRail());
els.drawerDocuments.addEventListener("click", () => openDocumentRail());
els.closeDocuments.addEventListener("click", closeDocumentRail);
els.previousRecord.addEventListener("click", () => selectAdjacentRecord(-1));
els.nextRecord.addEventListener("click", () => selectAdjacentRecord(1));
els.closeDrawer.addEventListener("click", () => {
  if (state.selectedItem) selectItem(state.selectedItem);
});

els.tabs.forEach(tab => tab.addEventListener("click", async () => {
  await waitForDirectSave();
  if ((state.dirty || state.purposeDirty) && !(await confirmDiscardChanges())) return;
  if (!(await leaveDocumentEntry())) return;
  clearSelection();
  setMode(tab.dataset.reviewMode);
  closeDocumentRail();
  syncUrl();
  renderWorklist();
  renderDocumentRail();
}));

els.list.addEventListener("keydown", event => {
  if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
  if (event.target.closest("form, .doc-list, .document-viewer")) return;
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
  if (event.defaultPrevented || document.querySelector("dialog[open]")) return;
  const railOpen = els.evidencePane.classList.contains("mobile-open");
  if (event.key === "Escape" && railOpen) {
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
  const target = event.target instanceof Element ? event.target : document.activeElement;
  const typing = target?.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox'], [role='combobox'], [role='dialog']");
  const modified = event.ctrlKey || event.metaKey || event.altKey;
  if (event.key.toLowerCase() === "f" && !modified && !typing) {
    if (railOpen) closeDocumentRail();
    els.filter.focus();
    event.preventDefault();
    return;
  }
  if (railOpen || typing || modified || !["j", "k"].includes(event.key.toLowerCase())) return;
  selectAdjacentRecord(event.key.toLowerCase() === "j" ? 1 : -1);
  event.preventDefault();
});
window.addEventListener("beforeunload", event => {
  if (!state.dirty && !state.purposeDirty && !state.documentActionDirty) return;
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
      if (state.activeDocument) openDocumentRail();
    }
    syncResponsivePanes();
  } catch (error) {
    showMessage(error.message, "danger");
    els.detail.innerHTML = `<div class="empty-state"><strong>Review could not load</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

bootstrap();

// Refresh the inbox and its attachment targets without replacing unsaved coding.
window.setInterval(() => {
  if (document.hidden) return;
  loadInbox({ background: true }).catch(() => {});
}, 2000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadInbox({ background: true }).catch(() => {});
});
