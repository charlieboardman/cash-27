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

function workflowStatus(value) {
  return ({ pending: "Open", submitted: "Sent for review",
    in_review: "Being reviewed", needs_changes: "Needs corrections",
    verified: "Checked", flagged: "Needs correction",
    accepted: "Approved", complete: "Complete" })[value] ||
    String(value || "").replaceAll("_", " ");
}

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
  return `${pages} · Uploaded ${displayDateTime(document.created_at)}`;
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
      ? "Choose an exchange receipt" : "Choose a voucher";
  } else if (state.documentPickTarget === "support") {
    els.evidenceHeading.textContent = "Choose a receipt";
  } else {
    els.evidenceHeading.textContent = state.mode === "submissions"
      ? "Receipt document" : "Supporting documents";
  }
}

function openDocumentRail(target = null) {
  state.documentPickTarget = target;
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

async function loadInbox() {
  const data = await request("/api/documents/inbox");
  state.inbox = [...data.documents].reverse().map(document => ({
    ...document,
    stream_url: `/api/documents/${document.document_id}/stream`,
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
}

/* ---- evidence rail ------------------------------------------------------- */

function renderEvidenceList() {
  els.inboxCount.textContent = state.inbox.length;
  els.mobileInboxCount.textContent = state.inbox.length;
  els.evidenceList.innerHTML = "";
  const activeIsInbox = state.inbox.some(document =>
    document.document_id === state.activeDocument?.document_id);
  const documents = state.mode === "submissions"
    ? (state.activeDocument ? [state.activeDocument] : [])
    : state.activeDocument && !activeIsInbox
    ? [state.activeDocument, ...state.inbox] : state.inbox;
  if (!documents.length) {
    els.evidenceList.innerHTML = `<li class="empty-state compact">
      <strong>${state.mode === "submissions" ? "Select a receipt" : "No documents are waiting"}</strong>${state.mode === "submissions" ? "" : "<span>Upload a document above.</span>"}</li>`;
    return;
  }
  for (const inboxDocument of documents) {
    const li = document.createElement("li");
    const selected = state.activeDocument?.document_id === inboxDocument.document_id;
    const available = state.inbox.some(document =>
      document.document_id === inboxDocument.document_id);
    li.className = selected ? "selected" : "";
    li.dataset.documentId = inboxDocument.document_id;
    li.innerHTML = `<button type="button" class="evidence-card"
      aria-expanded="${selected}"${available ? ' draggable="true"' : ""}>
      <span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>${esc(documentName(inboxDocument))}</strong>
        <span>${esc(documentMeta(inboxDocument))}</span>
      </span>
    </button>${selected ? inlineDocumentViewer(inboxDocument, activeIsInbox) : ""}`;
    const card = li.querySelector(".evidence-card");
    card.addEventListener("click", async () => {
      if (state.documentPickTarget && compactReviewLayout() && available) {
        const target = state.documentPickTarget;
        await applyDocument(target, inboxDocument);
        closeDocumentRail();
        return;
      }
      if (selected) {
        state.activeDocument = null;
        state.documentActionType = null;
        state.documentActionDirty = false;
      } else {
        state.activeDocument = inboxDocument;
        state.documentActionType = null;
        state.documentActionDirty = false;
      }
      renderDocumentRail();
    });
    if (available) card.addEventListener("dragstart", event => {
      event.dataTransfer.setData("text/plain", inboxDocument.document_id);
      event.dataTransfer.effectAllowed = "copy";
    });
    els.evidenceList.appendChild(li);
    if (selected) wireInlineDocumentViewer(li, inboxDocument);
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

function inlineDocumentViewer(document, isInbox) {
  const url = document.stream_url ||
    `/api/documents/${document.document_id}/stream`;
  return `<div class="document-viewer inline-document-viewer${state.documentActionType ? " entry-open" : ""}">
    <div class="viewer-toolbar"><span>${esc(documentName(document))}<small
      class="document-reference">${esc(document.document_id)}</small></span>
      <span class="viewer-actions">${isInbox ? `<button type="button"
        class="text-button record-document-entry${state.documentActionType === "withdrawal" ? " active" : ""}"
        data-entry-type="withdrawal" aria-pressed="${state.documentActionType === "withdrawal"}">Record bank withdrawal</button>
      <button type="button"
        class="text-button record-document-entry${state.documentActionType === "exchange" ? " active" : ""}"
        data-entry-type="exchange" aria-pressed="${state.documentActionType === "exchange"}">Record currency exchange</button>` : ""}
      <a href="${esc(url)}" target="_blank" rel="noopener">Open full size</a></span>
    </div>${state.documentActionType ? documentEntryForm(document, state.documentActionType) : ""}
    <iframe src="${esc(url)}#view=FitH" title="${esc(documentName(document))} preview"></iframe></div>`;
}

function wireInlineDocumentViewer(container, document) {
  container.querySelectorAll(".record-document-entry").forEach(button => {
    button.addEventListener("click", () => {
      if (state.documentActionDirty &&
          !window.confirm("Discard this unfinished entry?")) return;
      const requestedType = button.dataset.entryType;
      state.documentActionType = state.documentActionType === requestedType
        ? null : requestedType;
      state.documentActionDirty = false;
      renderDocumentRail();
    });
  });
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
    showMessage(`${names} ${added.length === 1 ? "is" : "are"} ready to file with this expense.`);
  } else if (list.length) {
    showMessage("That document is already staged here.", "warning");
  }
  if (state.selectedItem?.kind === "direct_expense") renderDirectExpense();
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
    stageDocuments(document);
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
    await applyDocument(target, uploaded);
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
    renderDetail();
  } catch (error) {
    if (state.selectedKey !== itemKey) return;
    els.detail.innerHTML = `<div class="empty-state"><strong>Unable to open record</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

/* ---- allocation editors --------------------------------------------------- */

function accountOptions(selected = "") {
  return `<option value="">Choose account code</option>` +
    state.options.expense_accounts.map(account =>
      `<option value="${esc(account.account_id)}" ${account.account_id === selected ? "selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
}

function classOptions(selected = "") {
  return `<option value="">Choose class code</option>` +
    state.options.classes.map(item =>
      `<option value="${esc(item.class_code)}" ${item.class_code === selected ? "selected" : ""}>${esc(item.name)} · ${esc(item.class_code)}</option>`).join("");
}

function renderProjectOptions() {
  document.getElementById("project-options").innerHTML =
    (state.options.projects || []).map(project =>
      `<option value="${esc(project)}"></option>`).join("");
}

function allocationRow(allocation = {}, { faceParts = null, directExpense = false } = {}) {
  const faceUsd = allocation.face_usd ??
    (allocation.face_usd_cents != null ? decimal(allocation.face_usd_cents) :
     allocation.receipt_face_usd_cents == null ? "" : decimal(allocation.receipt_face_usd_cents));
  const faceMxn = allocation.face_mxn ??
    (allocation.face_mxn_centavos != null ? decimal(allocation.face_mxn_centavos) :
     allocation.receipt_face_mxn_centavos == null ? "" : decimal(allocation.receipt_face_mxn_centavos));
  const receiptFields = faceParts
    ? `${faceParts.usd ? `<label><span>Receipt value, USD portion</span><input name="face_usd" inputmode="decimal" value="${esc(faceUsd)}"></label>` : ""}
       ${faceParts.mxn ? `<label><span>Receipt value, MXN portion</span><input name="face_mxn" inputmode="decimal" value="${esc(faceMxn)}"></label>` : ""}`
    : "";
  if (directExpense) return `<div class="allocation-row direct-allocation-row">
    <div class="allocation-row-head allocation-row-controls" hidden>
      <button type="button" class="text-button danger remove-allocation">Remove split</button></div>
    <div class="form-grid direct-allocation-fields">
      <label><span>USD used</span><input name="usd" inputmode="decimal"
        value="${Math.max(0, Number(allocation.usd ?? decimal(allocation.usd_cents))).toFixed(2)}" required></label>
      <label><span>MXN used</span><input name="mxn" inputmode="decimal"
        value="${Math.max(0, Number(allocation.mxn ?? decimal(allocation.mxn_centavos))).toFixed(2)}" required></label>
      <label><span>Class code</span><select name="class_code" required>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      <label><span>Account code</span><select name="expense_account" required>
        ${accountOptions(allocation.expense_account || allocation.to_account || "")}
      </select></label>
      <label><span>Project</span><input name="project" list="project-options"
        autocomplete="off"
        value="${esc(allocation.project || "")}"></label>
      <details class="full" ${Number(faceUsd) || Number(faceMxn) || Number(allocation.usd ?? decimal(allocation.usd_cents)) < 0 || Number(allocation.mxn ?? decimal(allocation.mxn_centavos)) < 0 ? "open" : ""}>
        <summary>Change or currency conversion</summary>
        <p class="field-help">Enter change received in addition to the amounts above. A USD payment with MXN change needs the MXN purchase value.</p>
        <div class="form-grid two">
          <label><span>USD change received</span><input name="usd_change" inputmode="decimal"
            value="${Math.max(0, -Number(allocation.usd ?? decimal(allocation.usd_cents))).toFixed(2)}"></label>
          <label><span>MXN change received</span><input name="mxn_change" inputmode="decimal"
            value="${Math.max(0, -Number(allocation.mxn ?? decimal(allocation.mxn_centavos))).toFixed(2)}"></label>
          ${receiptFields}
        </div>
      </details>
      <input type="hidden" name="description" value="${esc(allocation.description || "")}">
    </div>
  </div>`;
  return `<div class="allocation-row">
    <div class="allocation-row-head allocation-row-controls" hidden>
      <button type="button" class="text-button danger remove-allocation">Remove split</button></div>
    <div class="form-grid two">
      <label><span>Account code</span><select name="expense_account" required>
        ${accountOptions(allocation.expense_account || allocation.to_account || "")}
      </select></label>
      <label><span>Class code</span><select name="class_code" required>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      <label><span>USD in this split</span><input name="usd" inputmode="decimal"
        value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
      <label><span>MXN in this split</span><input name="mxn" inputmode="decimal"
        value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>
      ${receiptFields}
      <label><span>Project</span><input name="project" list="project-options"
        autocomplete="off"
        value="${esc(allocation.project || "")}"></label>
    </div>
    <label><span>Description</span><input name="description"
      value="${esc(allocation.description || "")}"></label>
  </div>`;
}

function wireAllocationEditor(container, defaults, { faceParts = null } = {}) {
  const list = container.querySelector(".allocation-list");
  const updateControls = () => {
    const rows = [...list.querySelectorAll(".allocation-row")];
    rows.forEach(row => {
      row.querySelector(".allocation-row-controls").hidden = rows.length === 1;
      row.classList.toggle("is-split", rows.length > 1);
    });
  };
  container.querySelector(".add-allocation")?.addEventListener("click", () => {
    list.insertAdjacentHTML("beforeend",
      allocationRow(defaults(), { faceParts }));
    updateControls();
    markDirty();
  });
  list.addEventListener("click", event => {
    if (!event.target.closest(".remove-allocation")) return;
    if (list.querySelectorAll(".allocation-row").length === 1) {
      showMessage("At least one account code is required.", "warning");
      return;
    }
    event.target.closest(".allocation-row").remove();
    updateControls();
    markDirty();
  });
  updateControls();
}

function readAllocations(form) {
  return [...form.querySelectorAll(".allocation-row")].map(row => {
    const net = currency => {
      const amount = row.querySelector(`[name="${currency}"]`)?.value || "0";
      const change = row.querySelector(`[name="${currency}_change"]`);
      return change ? (Number(amount) - Number(change.value || 0)).toFixed(2) : amount;
    };
    return {
    expense_account: row.querySelector('[name="expense_account"]').value,
    class_code: row.querySelector('[name="class_code"]').value || null,
    usd: net("usd"),
    mxn: net("mxn"),
    face_usd: row.querySelector('[name="face_usd"]')?.value || null,
    face_mxn: row.querySelector('[name="face_mxn"]')?.value || null,
    project: row.querySelector('[name="project"]').value || "",
    description: row.querySelector('[name="description"]').value || "",
  }; });
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
  const url = document.stream_url ||
    `/api/documents/${document.document_id}/stream`;
  return `<a class="attached-document-preview" href="${esc(url)}"
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
        data-document-slot="${target}">Use opened · ${esc(documentName(state.activeDocument))}</button>`
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

async function applyDocument(target, document) {
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
  if (item.kind === "exchange") {
    state.primaryDocument = document;
    markDirty();
    renderExchange();
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
    return;
  }
  if (item.kind === "addendum") {
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
      <p class="field-help">Check this only when the vendor acknowledgment is signed. A separate receipt is optional.</p>
      <section class="attachment-choice document-drop-slot" data-document-slot="support"${usingVoucher ? " hidden" : ""}>
        <div class="attachment-slot-heading"><span>${state.stagedReceipts.length ? "Add another receipt" : "Separate receipt"}</span></div>
        <div class="attachment-slot-actions">
          ${state.activeDocument && state.inbox.some(item =>
            item.document_id === state.activeDocument.document_id)
            && state.activeDocument.document_id !== state.primaryDocument?.document_id
            && !state.stagedReceipts.some(document => document.document_id === state.activeDocument.document_id)
            ? `<button type="button" class="btn slot-attach"
              data-document-slot="support">Use opened · ${esc(documentName(state.activeDocument))}</button>` : ""}
          <button type="button" class="text-button slot-choose"
            data-document-slot="support">Choose from documents</button>
          <button type="button" class="btn ghost slot-upload"
            data-document-slot="support">Upload receipt</button>
        </div>
      </section>
    </section>
    <form id="direct-expense-form" class="review-form sheet-action-form">
    <section class="sheet-editor-section">
      <p class="field-help">Authorized: USD ${cashMinorMoney(voucher.authorized_usd_cents)} · MXN ${cashMinorMoney(voucher.authorized_mxn_centavos)}. Record final cash used below; any unused authorized cash must be returned to the safe.</p>
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
        status.textContent = "Progress saved. This expense remains open until review is complete.";
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
}

/* ---- exchange ------------------------------------------------------------- */

function renderExchange() {
  const transaction = state.detail.transaction;
  els.detail.innerHTML = `<nav class="drawer-record-tools" aria-label="Transaction actions">
      <a class="btn ghost" href="/ledger?txn=${transaction.txn_id}">Edit transaction</a>
    </nav><form id="exchange-review-form">
      <div class="compact-document-editor drawer-document-body">
        <p>Check the receipt against USD ${cashMinorMoney(transaction.usd_cents)}
          exchanged for MXN ${cashMinorMoney(-transaction.mxn_centavos)}
          in ${esc(transaction.from_account)} on ${esc(transaction.date)}.</p>
        ${documentSlot({ target: "primary", label: "Exchange receipt",
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
      status.textContent = "Attach the exchange receipt before saving.";
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await request(`/api/review/exchange/${transaction.txn_id}/complete`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: transaction.version,
          document_id: state.primaryDocument.document_id },
      });
      await afterCommit(`Exchange ${transaction.txn_id} completed.`, true);
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
                data-document-slot="primary">Use opened · ${esc(documentName(state.activeDocument))}</button>
            </div>`
          : `<p class="slot-empty-hint">Open the signed voucher on the left, then bring it here.</p>`}
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
        ${state.activeDocument && state.inbox.some(item =>
          item.document_id === state.activeDocument.document_id)
          ? `<div class="attachment-slot-actions">
              <button type="button" class="btn primary slot-attach"
                data-document-slot="primary">File opened · ${esc(documentName(state.activeDocument))}</button>
            </div>`
          : `<p class="slot-empty-hint">Open the signed addendum on the left, then file it here.</p>`}
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
  return `<form id="document-entry-form" class="review-form document-entry-form">
    <input type="hidden" name="entry_type" value="${kind}">
    <div class="document-entry-heading"><strong>Record ${label}</strong>
      <span>${esc(documentName(inboxDocument))}</span></div>
    <div class="form-grid two">
      <label><span>Transaction date</span><input type="date" name="date"
        value="${localDate}" required></label>
      ${exchange ? `<label><span>Cash source</span><select name="source_account">${cashAccountOptions()}</select></label>` : ""}
      <label><span>USD amount</span><input name="usd" inputmode="decimal" required></label>
      ${exchange ? `<label><span>MXN received</span><input name="mxn" inputmode="decimal" required></label>` : ""}
      <label class="full"><span>Notes</span><textarea name="notes" rows="2"></textarea></label>
    </div><p class="derived-movement">${exchange
      ? "USD was exchanged for MXN in the selected cash account."
      : "USD moved from the bank into the safe."}</p>
    <div class="document-entry-actions"><output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Save ${label}</button></div>
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
    try {
      await request(`/api/entries/${kind}`, { method: "POST", body });
      state.documentActionDirty = false;
      state.documentActionType = null;
      state.activeDocument = null;
      await Promise.all([loadWorklist(), loadInbox()]);
      renderDocumentRail();
      showMessage(`${kind === "exchange" ? "Exchange" : "Withdrawal"} created.`);
    } catch (error) { status.textContent = error.message; }
  });
}

/* ---- package reviewer -------------------------------------------------------- */

function packageAllocationDefaults(claim) {
  const faceUsd = claim.face_usd_cents ?? 0;
  const faceMxn = claim.face_mxn_centavos ?? 0;
  return {
    expense_account: "",
    class_code: "",
    usd_cents: claim.usd_paid_cents - claim.usd_change_cents,
    mxn_centavos: claim.mxn_paid_centavos - claim.mxn_change_centavos,
    receipt_face_usd_cents: faceUsd,
    receipt_face_mxn_centavos: faceMxn,
    description: claim.description,
  };
}

function renderPackage() {
  const packageData = state.detail.package;
  const claims = state.detail.claims;
  if (!claims.length) {
    els.detail.innerHTML = `<div class="row-editor-heading"><strong>No receipts in this submission</strong></div>`;
    return;
  }
  state.claimIndex = Math.max(0, Math.min(state.claimIndex, claims.length - 1));
  const claim = claims[state.claimIndex];
  if (claim.document && claim.document.document_id !== state.activeDocument?.document_id) {
    state.activeDocument = claim.document;
    renderDocumentRail();
  }
  const allocations = claim.allocations.length ? claim.allocations :
    [packageAllocationDefaults(claim)];
  const verifiedCount = claims.filter(item => item.state === "verified" &&
    item.verified_version === item.content_version).length;
  const reviewable = packageData.status !== "needs_changes";
  const receiptRows = claims.map((item, index) => {
    const itemVerified = item.state === "verified" &&
      item.verified_version === item.content_version;
    const usdUsed = item.usd_paid_cents - item.usd_change_cents;
    const mxnUsed = item.mxn_paid_centavos - item.mxn_change_centavos;
    const selected = index === state.claimIndex;
    const summary = `<tr class="package-receipt-row${selected ? " selected" : ""}"
        data-claim-index="${index}" tabindex="0" aria-expanded="${selected}">
      <td><span class="sheet-caret" aria-hidden="true">›</span>${esc(item.claim_date)}</td>
      <td>${esc(item.vendor || "Unnamed vendor")}</td>
      <td class="review-record-details">${esc(item.description || "—")}${item.notes ? `<small>${esc(item.notes)}</small>` : ""}</td>
      <td class="num">${esc([item.face_usd_cents ?? 0, item.face_mxn_centavos ?? 0].map((value, currency) => value ? `${currency ? "MXN" : "USD"} ${cashMinorMoney(value)}` : "").filter(Boolean).join(" + ") || "—")}</td>
      <td class="num">${cashMinorMoney(usdUsed)}</td><td class="num">${cashMinorMoney(mxnUsed)}</td>
      <td><span class="status-pill ${itemVerified ? "success" : ""}">${esc(workflowStatus(item.state))}</span></td>
    </tr>`;
    if (!selected) return summary;
    const editor = reviewable ? `<form id="claim-review-form" class="review-form sheet-action-form">
      <section class="sheet-editor-section"><div class="section-heading"><div>
        <h3>Account and class codes</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${claim.payment_mode === "receipt_currency" ? `<p class="field-help">Recorded in the receipt's currency. The remaining currency conversion will be reconciled at signout closeout.</p>` : ""}
        ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one before these receipts can be approved.</p>` : ""}
        <div class="allocation-list">${allocations.map(row =>
          allocationRow(row, { faceParts: { usd: true, mxn: true } })).join("")}</div>
        <label><span>Note for the recipient</span><textarea name="admin_note" rows="2">${esc(claim.admin_note || "")}</textarea></label>
      </section>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="save">Save allocation</button>
        <button type="submit" class="btn primary" value="verify">Save &amp; mark checked</button>
      </div></form>` : `<p class="callout warning">This submission was returned to the recipient: ${esc(packageData.admin_summary || "Corrections requested")}</p>`;
    return `${summary}<tr class="package-receipt-expansion"><td colspan="7">${editor}
      ${reviewable ? `<label class="inline-correction-note"><span>Correction needed for this receipt</span>
        <textarea id="return-item-note" rows="2">${esc(state.returnNotes[item.claim_id] || "")}</textarea></label>` : ""}
      </td></tr>`;
  }).join("");
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>Review ${claims.length} submitted receipt${claims.length === 1 ? "" : "s"}</strong>
      <span>${verifiedCount} of ${claims.length} checked · ${esc(workflowStatus(packageData.status))}</span>
    </div>
    <div class="package-sheet-scroll"><table class="package-sheet">
      <thead><tr><th>Date</th><th>Vendor</th><th>Description</th>
        <th class="num">Receipt value</th><th class="num">USD recorded</th>
        <th class="num">MXN recorded</th><th>Status</th></tr></thead>
      <tbody>${receiptRows}</tbody></table></div>
    <section class="package-decision sheet-editor-section"><p class="eyebrow">Submission decision</p>
      <label><span>What needs to be corrected?</span><textarea id="return-summary" rows="2" ${reviewable ? "" : "disabled"}>${esc(packageData.admin_summary || "")}</textarea></label>
      <div class="package-actions">
        <button type="button" class="btn danger return-package" ${reviewable ? "" : "disabled"}>Request corrections</button>
        <button type="button" class="btn primary accept-package" ${verifiedCount === claims.length && reviewable ? "" : "disabled"}>Approve all ${claims.length} receipts</button>
      </div><output class="package-status" role="alert"></output>
    </section>`;
  els.detail.querySelectorAll(".package-receipt-row").forEach(row => {
    row.addEventListener("click", () => {
      const nextIndex = Number(row.dataset.claimIndex);
      if (nextIndex === state.claimIndex) return;
      if (state.dirty && !window.confirm(
        "Discard unsaved changes and open another receipt?")) return;
      state.claimIndex = nextIndex;
      state.dirty = false;
      renderPackage();
    });
    row.addEventListener("keydown", event => {
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      row.click();
    });
  });
  const note = document.getElementById("return-item-note");
  note?.addEventListener("input", () => {
    state.returnNotes[claim.claim_id] = note.value; markDirty();
  });
  const form = document.getElementById("claim-review-form");
  if (form) {
    wireAllocationEditor(form, () => ({
      expense_account: "",
      class_code: "",
    }), { faceParts: { usd: true, mxn: true } });
    markDirtyEvent(form);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const status = form.querySelector("output");
      try {
        await request(`/api/review/claims/${claim.claim_id}/allocations`, {
          method: "POST", body: { submission_id: cashUUID(),
            expected_content_version: claim.content_version,
            allocations: readAllocations(form) },
        });
        if (event.submitter?.value === "verify") {
          await request(`/api/review/claims/${claim.claim_id}/verify`, {
            method: "POST", body: { submission_id: cashUUID(),
              expected_content_version: claim.content_version,
              admin_note: form.admin_note.value },
          });
          showMessage(`Receipt ${state.claimIndex + 1} marked as checked.`);
        } else showMessage("Allocation saved.");
        state.dirty = false;
        await refreshSelected();
      } catch (error) {
        status.textContent = error.message;
        status.scrollIntoView({ block: "nearest" });
      }
    });
  }
  els.detail.querySelector(".return-package")?.addEventListener("click", async () => {
    const summary = document.getElementById("return-summary").value.trim();
    const status = els.detail.querySelector(".package-status");
    if (!summary) { status.textContent = "Explain what the signee must correct."; return; }
    if (!Object.values(state.returnNotes).some(value => value.trim())) {
      status.textContent = "Add a correction note to at least one receipt."; return;
    }
    try {
      await request(`/api/review/packages/${packageData.package_id}/return`, {
        method: "POST", body: { submission_id: cashUUID(),
          admin_summary: summary, item_notes: state.returnNotes },
      });
      state.dirty = false;
      await afterCommit("Correction requests sent to the recipient.", true);
    } catch (error) { status.textContent = error.message; }
  });
  els.detail.querySelector(".accept-package")?.addEventListener("click", async () => {
    if (!window.confirm(`Approve all ${claims.length} receipts?`)) return;
    const status = els.detail.querySelector(".package-status");
    try {
      await request(`/api/review/packages/${packageData.package_id}/accept`, {
        method: "POST", body: { submission_id: cashUUID() },
      });
      await afterCommit("Receipts approved and cash records updated.", true);
    } catch (error) { status.textContent = error.message; }
  });
}

/* ---- shell ------------------------------------------------------------------ */

function renderDetail() {
  if (!state.selectedItem || !state.detail) return;
  state.detailRendering = true;
  switch (state.selectedItem.kind) {
    case "direct_expense": renderDirectExpense(); break;
    case "exchange": renderExchange(); break;
    case "signout": renderSignout(); break;
    case "addendum": renderAddendum(); break;
    case "package": renderPackage(); break;
    default:
      els.detail.innerHTML = `<div class="empty-state"><strong>Unsupported review item</strong></div>`;
  }
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
      <strong>${next.length ? "Record saved" : "Review queue complete"}</strong>
      <span>${next.length ? "Select the next record when ready." : "There are no records matching this view."}</span></div>`;
  }
}

/* ---- events ----------------------------------------------------------------- */

els.browse.addEventListener("click", () => {
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
  if (!file) return;
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
    syncResponsivePanes();
  } catch (error) {
    showMessage(error.message, "danger");
    els.detail.innerHTML = `<div class="empty-state"><strong>Review could not load</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

bootstrap();
