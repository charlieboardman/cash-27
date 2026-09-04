/* Spreadsheet review: a compact document rail beside expandable work rows. */
const els = {
  list: document.querySelector("#worklist tbody"),
  detail: document.getElementById("detail"),
  filter: document.getElementById("work-filter"),
  kind: document.getElementById("kind-filter"),
  summary: document.getElementById("queue-summary"),
  empty: document.getElementById("empty-worklist"),
  message: document.getElementById("review-message"),
  inboxCount: document.getElementById("inbox-count"),
  evidenceHeading: document.getElementById("evidence-heading"),
  evidenceList: document.getElementById("evidence-list"),
  drop: document.getElementById("evidence-drop"),
  picker: document.getElementById("evidence-picker"),
  browse: document.getElementById("browse-evidence"),
  upload: document.getElementById("upload-evidence"),
  uploadPages: document.getElementById("evidence-upload-pages"),
  refreshInbox: document.getElementById("refresh-inbox"),
  layout: document.getElementById("review-sheet-layout"),
  workHeading: document.getElementById("work-heading"),
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
  attachments: { primary: null, support: [] },
  attachmentOptions: {},
  useAsReceipt: false,
  uploadTarget: null,
  uploadFiles: [],
  claimIndex: 0,
  returnNotes: {},
  dirty: false,
  railExpanded: false,
  documentActionType: null,
  documentActionDirty: false,
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

function money(value, currency) {
  return `${currency} ${Number(value || 0).toFixed(2)}`;
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

function documentName(document) {
  if (!document) return "Document";
  return document.display_name || `Document ${document.document_id.slice(0, 8)}`;
}

function documentMeta(document) {
  const pages = `${document.page_count || "?"} page${document.page_count === 1 ? "" : "s"}`;
  return `${pages} · Uploaded ${displayDateTime(document.created_at)}`;
}

function documentForId(documentId) {
  if (!documentId) return null;
  return state.inbox.find(document => document.document_id === documentId) ||
    state.detail?.documents?.find(document =>
      document.document_id === documentId) || null;
}

function keyFor(item) {
  return `${item.kind}:${item.id}`;
}

function showMessage(text, tone = "success") {
  els.message.hidden = false;
  els.message.className = `notice ${tone}`;
  els.message.textContent = text;
  window.clearTimeout(showMessage.timer);
  showMessage.timer = window.setTimeout(() => { els.message.hidden = true; }, 5000);
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
  const kind = els.kind.value;
  return state.items.filter(item => {
    if (state.mode === "transactions" && item.kind === "package") return false;
    if (state.mode === "submissions" && item.kind !== "package") return false;
    if (kind && item.kind !== kind) return false;
    if (!needle) return true;
    return [item.id, item.label, item.secondary, item.eyebrow,
            ...(item.badges || [])].join(" ").toLowerCase().includes(needle);
  });
}

function renderWorklist() {
  const visible = currentFilteredItems();
  els.list.innerHTML = "";
  let selectedVisible = false;
  visible.forEach(item => {
    const row = document.createElement("tr");
    const selected = keyFor(item) === state.selectedKey;
    if (selected) selectedVisible = true;
    row.className = `review-summary-row${selected ? " selected" : ""}`;
    row.tabIndex = 0;
    row.setAttribute("aria-expanded", String(selected));
    const record = item.kind === "package" ? item.secondary : item.id;
    const details = item.kind === "package" ? item.label :
      `${item.label || item.id}${item.secondary ? ` · ${item.secondary}` : ""}`;
    row.innerHTML = `<td class="txn-id"><span class="sheet-caret" aria-hidden="true">›</span>${esc(record)}</td>
      <td>${esc(item.date || "—")}</td><td>${esc(item.eyebrow)}</td>
      <td class="review-record-details">${esc(details)}</td>
      <td class="num receipt-value-cell">${item.receipt == null ? "—" : esc(item.receipt)}</td>
      <td class="num">${item.usd == null ? "—" : esc(item.usd)}</td>
      <td class="num">${item.mxn == null ? "—" : esc(item.mxn)}</td>
      <td><span class="badge-row">${(item.badges || []).map(b =>
        `<span class="badge">${esc(b)}</span>`).join("")}</span></td>`;
    row.addEventListener("click", () => selectItem(item));
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        selectItem(item); event.preventDefault();
      }
    });
    els.list.appendChild(row);
    if (selected) {
      const expansion = document.createElement("tr");
      expansion.className = "review-expansion-row";
      const cell = document.createElement("td");
      cell.colSpan = 8;
      cell.appendChild(els.detail);
      els.detail.hidden = false;
      expansion.appendChild(cell);
      els.list.appendChild(expansion);
    }
  });
  if (!selectedVisible) els.detail.hidden = true;
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

async function loadInbox({ preserve = true } = {}) {
  const data = await request("/api/documents/inbox");
  state.inbox = [...data.documents].reverse().map(document => ({
    ...document,
    stream_url: `/api/documents/${document.document_id}/stream`,
  }));
  if (preserve && state.activeDocument) {
    const refreshed = state.inbox.find(d =>
      d.document_id === state.activeDocument.document_id);
    if (refreshed) state.activeDocument = refreshed;
  }
  renderDocumentRail();
}

function renderEvidenceList() {
  els.inboxCount.textContent = state.inbox.length;
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
    const isInbox = state.inbox.some(document =>
      document.document_id === inboxDocument.document_id);
    li.className = selected ? "selected" : "";
    li.dataset.documentId = inboxDocument.document_id;
    li.innerHTML = `<button type="button" class="evidence-card"
      aria-expanded="${selected}"${isInbox ? ` draggable="true"
      title="Drag onto a document box in an open row"` : ""}>
      <span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>${esc(documentName(inboxDocument))}</strong>
        <span>${esc(documentMeta(inboxDocument))}</span>
      </span>${isInbox ? `<span class="drag-handle" aria-hidden="true">⠿</span>` : ""}
    </button>${selected ? inlineDocumentViewer(inboxDocument, isInbox) : ""}`;
    const card = li.querySelector(".evidence-card");
    card.addEventListener("click", () => {
      if (selected) {
        if (state.documentActionDirty &&
            !window.confirm("Discard this unfinished entry?")) return;
        state.activeDocument = null;
        state.railExpanded = false;
        state.documentActionType = null;
        state.documentActionDirty = false;
        renderDocumentRail();
      } else {
        setActiveDocument(inboxDocument);
      }
    });
    if (isInbox) card.addEventListener("dragstart", event => {
        event.dataTransfer.setData("text/plain", inboxDocument.document_id);
        event.dataTransfer.effectAllowed = "copy";
      });
    els.evidenceList.appendChild(li);
    if (selected) wireInlineDocumentViewer(li, inboxDocument);
  }
}

function setActiveDocument(document) {
  if (state.activeDocument?.document_id !== document.document_id) {
    state.documentActionType = null;
    state.documentActionDirty = false;
  }
  state.activeDocument = document;
  state.railExpanded = true;
  renderDocumentRail();
}

function renderDocumentRail() {
  const document = state.activeDocument;
  els.layout.classList.toggle("document-reading",
    Boolean(document && state.railExpanded));
  renderEvidenceList();
}

function inlineDocumentViewer(document, isInbox) {
  const url = document.stream_url ||
    `/api/documents/${document.document_id}/stream`;
  return `<div class="document-viewer inline-document-viewer${state.documentActionType ? " entry-open" : ""}">
      <div class="viewer-toolbar"><span class="viewer-actions">${isInbox ? `<button type="button"
        class="text-button record-document-entry${state.documentActionType === "withdrawal" ? " active" : ""}"
        data-entry-type="withdrawal" aria-pressed="${state.documentActionType === "withdrawal"}">Record bank withdrawal</button>
      <button type="button"
        class="text-button record-document-entry${state.documentActionType === "exchange" ? " active" : ""}"
        data-entry-type="exchange" aria-pressed="${state.documentActionType === "exchange"}">Record currency exchange</button>` : ""}
      <button type="button" class="text-button toggle-document-rail">
        ${state.railExpanded ? "Collapse" : "Expand"}</button>
      <a href="${esc(url)}" target="_blank" rel="noopener">Open full size</a></span>
    </div>${state.documentActionType ? documentEntryForm(document, state.documentActionType) : ""}
    <iframe src="${esc(url)}#view=FitH" title="${esc(documentName(document))} preview"></iframe></div>`;
}

function wireInlineDocumentViewer(container, document) {
  container.querySelector(".toggle-document-rail").addEventListener("click", () => {
    state.railExpanded = !state.railExpanded;
    renderDocumentRail();
  });
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

function markDirty() {
  state.dirty = true;
}

async function selectItem(item, { force = false } = {}) {
  const itemKey = keyFor(item);
  if (!force && state.dirty &&
      !window.confirm("Discard unsaved review changes and open another item?")) return;
  if (!force && state.selectedKey === itemKey) {
    state.dirty = false;
    state.selectedItem = null;
    state.selectedKey = null;
    state.detail = null;
    state.attachments = { primary: null, support: [] };
    state.useAsReceipt = false;
    state.uploadTarget = null;
    renderWorklist();
    return;
  }
  state.dirty = false;
  state.selectedItem = item;
  state.selectedKey = itemKey;
  state.claimIndex = 0;
  state.returnNotes = {};
  state.attachments = { primary: null, support: [] };
  state.useAsReceipt = false;
  state.uploadTarget = null;
  renderWorklist();
  els.detail.innerHTML = `<div class="loading-state">Loading item…</div>`;
  try {
    const detail = await request(`/api/review/item/${item.kind}/${item.id}`);
    if (state.selectedKey !== itemKey) return;
    state.detail = detail;
    renderDetail();
  } catch (error) {
    if (state.selectedKey !== itemKey) return;
    els.detail.innerHTML = `<div class="empty-state"><strong>Unable to open record</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

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

function allocationRow(allocation = {}, { faceRequired = false,
                                          faceParts = null,
                                          classRequired = false,
                                          deriveTender = false,
                                          directExpense = false,
                                          receiptSharesOnSplitOnly = false } = {}) {
  const receiptFieldAttrs = receiptSharesOnSplitOnly
    ? ` class="receipt-share-field" hidden` : "";
  const receiptFields = faceParts ? `${faceParts.usd ? `<label${receiptFieldAttrs}><span>Receipt value, USD portion</span><input name="face_usd" inputmode="decimal"
        value="${esc(allocation.face_usd ?? (allocation.face_usd_cents != null ? decimal(allocation.face_usd_cents) : allocation.receipt_face_usd_cents == null ? "" : decimal(allocation.receipt_face_usd_cents)))}"></label>` : ""}
      ${faceParts.mxn ? `<label${receiptFieldAttrs}><span>Receipt value, MXN portion</span><input name="face_mxn" inputmode="decimal"
        value="${esc(allocation.face_mxn ?? (allocation.face_mxn_centavos != null ? decimal(allocation.face_mxn_centavos) : allocation.receipt_face_mxn_centavos == null ? "" : decimal(allocation.receipt_face_mxn_centavos)))}"></label>` : ""}` : `<label><span>Part of receipt total</span><input name="face_value"
        inputmode="decimal" value="${esc(allocation.face_value ??
          (allocation.face_value_minor == null ? "" : decimal(allocation.face_value_minor)))}"
        ${faceRequired ? "required" : ""}></label>`;
  if (directExpense) return `<div class="allocation-row direct-allocation-row">
    <div class="allocation-row-head allocation-row-controls" hidden>
      <button type="button" class="text-button danger remove-allocation">Remove split</button></div>
    <div class="form-grid direct-allocation-fields">
      <label><span>USD amount</span><input name="usd" inputmode="decimal"
        value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
      <label><span>MXN amount</span><input name="mxn" inputmode="decimal"
        value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>
      <label><span>Class code</span><select name="class_code" required>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      <label><span>Account code</span><select name="expense_account" required>
        ${accountOptions(allocation.expense_account || allocation.to_account || "")}
      </select></label>
      <label><span>Project</span><input name="project" list="project-options"
        autocomplete="off"
        value="${esc(allocation.project || "")}"></label>
      ${receiptFields}
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
      <label><span>Class code</span><select name="class_code" ${classRequired ? "required" : ""}>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      ${deriveTender ? "" : `<label><span>USD in this split</span><input name="usd" inputmode="decimal"
        value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
      <label><span>MXN in this split</span><input name="mxn" inputmode="decimal"
        value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>`}
      ${receiptFields}
      <label><span>Project</span><input name="project" list="project-options"
        autocomplete="off"
        value="${esc(allocation.project || "")}"></label>
    </div>
    <label><span>Description</span><input name="description"
      value="${esc(allocation.description || "")}"></label>
  </div>`;
}

function wireAllocationEditor(form, defaults, requirements = {}) {
  const container = form.querySelector(".allocation-list");
  const updateControls = () => {
    const rows = [...container.querySelectorAll(".allocation-row")];
    const multiple = rows.length > 1;
    rows.forEach(row => {
      row.querySelector(".allocation-row-controls").hidden = !multiple;
      row.classList.toggle("is-split", multiple);
      if (requirements.receiptSharesOnSplitOnly) {
        row.querySelectorAll(".receipt-share-field").forEach(field => {
          field.hidden = !multiple;
        });
      }
    });
    if (!multiple && requirements.receiptSharesOnSplitOnly) {
      const row = rows[0];
      if (row?.querySelector('[name="face_usd"]') &&
          requirements.singleFaceUsd != null) {
        row.querySelector('[name="face_usd"]').value = decimal(
          requirements.singleFaceUsd);
      }
      if (row?.querySelector('[name="face_mxn"]') &&
          requirements.singleFaceMxn != null) {
        row.querySelector('[name="face_mxn"]').value = decimal(
          requirements.singleFaceMxn);
      }
    }
  };
  form.querySelector(".add-allocation")?.addEventListener("click", () => {
    container.insertAdjacentHTML("beforeend", allocationRow(defaults(), requirements));
    updateControls();
    markDirty();
  });
  container.addEventListener("click", event => {
    if (!event.target.closest(".remove-allocation")) return;
    if (container.querySelectorAll(".allocation-row").length === 1) {
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
  return [...form.querySelectorAll(".allocation-row")].map(row => ({
    expense_account: row.querySelector('[name="expense_account"]').value,
    class_code: row.querySelector('[name="class_code"]').value || null,
    usd: row.querySelector('[name="usd"]')?.value || "0",
    mxn: row.querySelector('[name="mxn"]')?.value || "0",
    face_value: row.querySelector('[name="face_value"]')?.value || null,
    face_usd: row.querySelector('[name="face_usd"]')?.value || null,
    face_mxn: row.querySelector('[name="face_mxn"]')?.value || null,
    project: row.querySelector('[name="project"]').value || "",
    description: row.querySelector('[name="description"]').value || "",
  }));
}

function existingDocumentForRole(role) {
  return state.detail?.documents?.find(document =>
    document.roles?.includes(role)) || null;
}

function attachmentForSlot(target) {
  const documentId = target === "primary" ? state.attachments.primary :
    state.attachments.support[0];
  return documentForId(documentId) || existingDocumentForRole(
    target === "primary" ? "voucher" : "support");
}

function attachmentSlot({ target, label, optional = false,
                          voucherCanBeReceipt = false,
                          multiple = false }) {
  if (target === "support" && multiple) {
    const documents = state.attachments.support.map(documentForId).filter(Boolean);
    return `<section class="attachment-choice document-drop-slot multiple-receipts"
      data-document-slot="support">
      <div class="attachment-slot-heading"><span>${esc(label)}</span>
        <strong>${documents.length} added</strong></div>
      <div class="receipt-document-chips">${documents.map(document => `
        <span><button type="button" class="text-button view-attached"
          data-document-id="${esc(document.document_id)}">${esc(documentName(document))}</button>
        <button type="button" class="text-button danger remove-attached"
          data-document-slot="support" data-document-id="${esc(document.document_id)}"
          aria-label="Remove ${esc(documentName(document))}">×</button></span>`).join("")}</div>
      <div class="drop-instruction"><strong>Drop receipt documents here</strong>
        <span>One document may contain several pages of the same receipt.</span></div>
      <button type="button" class="btn ghost browse-slot" data-document-slot="support">Choose receipt files</button>
    </section>`;
  }
  const document = attachmentForSlot(target);
  const isNewAttachment = target === "primary"
    ? state.attachments.primary === document?.document_id
    : state.attachments.support.includes(document?.document_id);
  if (target === "support" && state.useAsReceipt) {
    const voucher = attachmentForSlot("primary");
    return `<section class="attachment-choice voucher-as-receipt">
      <div class="attachment-slot-heading"><span>${esc(label)}</span>
        <strong>${voucher ? esc(documentName(voucher)) : "Voucher"}</strong></div>
      <p>The voucher will also be filed as the receipt.</p>
      <button type="button" class="btn ghost separate-receipt">Use a separate receipt</button>
    </section>`;
  }
  if (document) {
    const url = document.stream_url ||
      `/api/documents/${document.document_id}/stream`;
    return `<section class="attachment-choice attached-document document-drop-slot" data-document-slot="${target}">
      <div class="attachment-slot-heading"><span>${esc(label)}${optional ? " (optional)" : ""}</span>
        <strong title="${esc(documentName(document))}">${esc(documentName(document))}</strong></div>
      <div class="attached-document-preview view-attached" role="button" tabindex="0"
        data-document-id="${esc(document.document_id)}" aria-label="View ${esc(documentName(document))}">
        <iframe src="${esc(url)}#page=1&view=FitH" tabindex="-1" title="${esc(label)} preview"></iframe>
      </div>
      <div class="attached-document-footer"><span>${esc(documentMeta(document))}</span>
        ${isNewAttachment ? `<button type="button" class="text-button danger remove-attached"
          data-document-slot="${target}">Remove</button>` : `<span class="filed-label">Filed</span>`}</div>
    </section>`;
  }
  const activeCanAttach = state.activeDocument && state.inbox.some(document =>
    document.document_id === state.activeDocument.document_id);
  const voucher = target === "support" ? attachmentForSlot("primary") : null;
  return `<section class="attachment-choice document-drop-slot" data-document-slot="${target}">
    <div class="attachment-slot-heading"><span>${esc(label)}${optional ? " (optional)" : ""}</span></div>
    <div class="drop-instruction"><strong>Drop ${esc(label.toLowerCase())} here</strong>
      <span>Drag from Supporting documents or from your computer.</span></div>
    <div class="attachment-slot-actions">
      <button type="button" class="btn ghost browse-slot" data-document-slot="${target}">Choose file</button>
      ${activeCanAttach ? `<button type="button" class="btn ghost use-open-document"
        data-document-slot="${target}" title="${esc(documentName(state.activeDocument))}">Use open document</button>` : ""}
      ${voucherCanBeReceipt && voucher ? `<button type="button" class="text-button use-voucher-as-receipt">Use voucher as receipt</button>` : ""}
    </div>
  </section>`;
}

function attachmentPanel(options = {}) {
  state.attachmentOptions = {
    primaryLabel: "Voucher", receipt: false, receiptLabel: "Receipt",
    receiptOptional: false, voucherCanBeReceipt: false,
    multipleReceipts: false, ...options,
  };
  const config = state.attachmentOptions;
  return `<section class="inline-document-actions" aria-label="Attach documents">
    ${attachmentSlot({ target: "primary", label: config.primaryLabel })}
    ${config.receipt ? attachmentSlot({ target: "support",
      label: config.receiptLabel, optional: config.receiptOptional,
      voucherCanBeReceipt: config.voucherCanBeReceipt,
      multiple: config.multipleReceipts }) : ""}
  </section>`;
}

function refreshAttachmentPanel() {
  const current = els.detail.querySelector(".inline-document-actions");
  if (!current) return;
  current.outerHTML = attachmentPanel(state.attachmentOptions);
  wireAttachments(els.detail);
}

function attachDocument(document, target) {
  if (!state.selectedItem) {
    showMessage("Choose a record before attaching a document.", "warning");
    return;
  }
  if (!target) {
    showMessage("Drop the document on the Voucher or Receipt box.", "warning");
    return;
  }
  if (!state.inbox.some(item => item.document_id === document.document_id)) {
    showMessage("That document has already been filed.", "warning");
    return;
  }
  if (target === "support") {
    if (document.document_id === state.attachments.primary) {
      state.useAsReceipt = true;
      state.attachments.support = [];
    } else {
      if (state.attachmentOptions.multipleReceipts) {
        if (!state.attachments.support.includes(document.document_id))
          state.attachments.support.push(document.document_id);
      } else state.attachments.support = [document.document_id];
      state.useAsReceipt = false;
    }
  } else {
    state.attachments.primary = document.document_id;
    if (state.attachments.support.includes(document.document_id)) {
      state.attachments.support = [];
      state.useAsReceipt = true;
    }
  }
  setActiveDocument(document);
  if (state.selectedItem.kind === "direct_expense" &&
      state.attachmentOptions.multipleReceipts) renderDirectExpense();
  else refreshAttachmentPanel();
  markDirty();
  showMessage(`${documentName(document)} added as ${target === "support" ? "the receipt" : state.attachmentOptions.primaryLabel.toLowerCase()}.`);
}

function wireAttachments(root) {
  root.querySelectorAll(".use-open-document").forEach(button => {
    button.addEventListener("click", () => {
      if (!state.activeDocument) return;
      attachDocument(state.activeDocument, button.dataset.documentSlot);
    });
  });
  root.querySelectorAll(".browse-slot").forEach(button => {
    button.addEventListener("click", () => {
      state.uploadTarget = button.dataset.documentSlot;
      els.picker.value = "";
      els.picker.click();
    });
  });
  root.querySelectorAll(".remove-attached").forEach(button => {
    button.addEventListener("click", () => {
      if (button.dataset.documentSlot === "primary") {
        state.attachments.primary = null;
        state.useAsReceipt = false;
      } else {
        const documentId = button.dataset.documentId;
        state.attachments.support = documentId
          ? state.attachments.support.filter(id => id !== documentId) : [];
      }
      if (state.selectedItem?.kind === "direct_expense" &&
          state.attachmentOptions.multipleReceipts) renderDirectExpense();
      else refreshAttachmentPanel();
      markDirty();
    });
  });
  root.querySelectorAll(".view-attached").forEach(button => {
    const view = () => {
      const document = documentForId(button.dataset.documentId);
      if (document) setActiveDocument(document);
    };
    button.addEventListener("click", view);
    button.addEventListener("keydown", event => {
      if (!["Enter", " "].includes(event.key)) return;
      view();
      event.preventDefault();
    });
  });
  root.querySelector(".use-voucher-as-receipt")?.addEventListener("click", () => {
    state.useAsReceipt = true;
    state.attachments.support = [];
    refreshAttachmentPanel();
    markDirty();
  });
  root.querySelector(".separate-receipt")?.addEventListener("click", () => {
    state.useAsReceipt = false;
    refreshAttachmentPanel();
    markDirty();
  });
  root.querySelectorAll(".document-drop-slot").forEach(slot => {
    slot.addEventListener("dragover", event => {
      event.preventDefault();
      slot.classList.add("dragging");
    });
    slot.addEventListener("dragleave", () => slot.classList.remove("dragging"));
    slot.addEventListener("drop", event => {
      event.preventDefault();
      slot.classList.remove("dragging");
      if (event.dataTransfer.files.length) {
        uploadFilesToSlot([...event.dataTransfer.files],
                          slot.dataset.documentSlot, slot);
        return;
      }
      const id = event.dataTransfer.getData("text/plain");
      const document = state.inbox.find(item => item.document_id === id);
      if (document) attachDocument(document, slot.dataset.documentSlot);
    });
  });
}

function renderDirectExpense() {
  const { voucher: voucher, allocations } = state.detail;
  const sparse = allocations[0] || {};
  const faceUsd = voucher.face_usd_cents ??
    (voucher.face_currency === "USD" ? voucher.face_value_minor : null);
  const faceMxn = voucher.face_mxn_centavos ??
    (voucher.face_currency === "MXN" ? voucher.face_value_minor : null);
  const initial = {
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
    usd_cents: sparse.usd_cents ?? voucher.authorized_usd_cents ?? 0,
    mxn_centavos: sparse.mxn_centavos ?? voucher.authorized_mxn_centavos ?? 0,
    receipt_face_usd_cents: faceUsd,
    receipt_face_mxn_centavos: faceMxn,
    description: voucher.purpose,
  };
  const receiptDocuments = state.attachments.support.map(documentForId)
    .filter(Boolean);
  const receiptEditors = receiptDocuments.map((document, index) => {
    const allocation = index === 0 ? initial : {
      expense_account: state.options.expense_accounts[0]?.account_id || "",
      class_code: state.options.classes[0]?.class_code || "",
      description: voucher.purpose,
    };
    return `<section class="direct-receipt-editor" data-document-id="${esc(document.document_id)}">
      <div class="direct-receipt-heading"><div><p class="eyebrow">Receipt ${index + 1}</p>
        <strong>${esc(documentName(document))}</strong></div>
        <button type="button" class="btn ghost add-receipt-split">Add split</button></div>
      <div class="form-grid two direct-receipt-facts">
        <label><span>Receipt date</span><input type="date" name="receipt_date"
          value="${esc(sparse.date || new Date().toLocaleDateString("en-CA"))}" required></label>
        <label><span>Vendor</span><input name="receipt_vendor"
          value="${esc(voucher.recipient || "")}" required></label>
        <label class="full"><span>Description</span><input name="receipt_description"
          value="${esc(voucher.purpose || "")}"></label>
      </div>
      <div class="allocation-list">${allocationRow(allocation, {
        classRequired: true, directExpense: true,
        faceParts: { usd: true, mxn: true }
      })}</div>
    </section>`;
  }).join("");
  const usingVoucher = receiptDocuments.length === 0;
  els.detail.innerHTML = `<div class="row-editor-columns">
      ${attachmentPanel({ receipt: true, receiptLabel: "Receipt documents",
        receiptOptional: true, multipleReceipts: true })}
      <form id="direct-expense-form" class="review-form sheet-action-form">
      <section class="sheet-editor-section">
        <div class="direct-allocation-toolbar"><a class="btn ghost"
          href="/voucher/${esc(voucher.voucher_id)}/print">Print voucher</a></div>
        ${!state.options.expense_accounts.length ? `<p class="callout danger">No account codes are available.</p>` : ""}
        ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one in Settings before completing this expense.</p>` : ""}
        ${usingVoucher ? `<div class="direct-receipt-editor voucher-receipt-editor">
          <div class="direct-receipt-heading"><div><p class="eyebrow">Receipt</p>
            <strong>Voucher is also the receipt</strong></div>
            <button type="button" class="btn ghost add-receipt-split">Add split</button></div>
          <div class="allocation-list">${allocationRow(initial, {
            classRequired: true, directExpense: true,
            faceParts: { usd: true, mxn: true }
          })}</div></div>` : receiptEditors}
      </section>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="stay">Save</button>
        <button type="submit" class="btn primary" value="next">Save &amp; review next</button>
      </div>
      </form>
    </div>`;
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
        expense_account: state.options.expense_accounts[0]?.account_id || "",
        class_code: state.options.classes[0]?.class_code || "",
        description: voucher.purpose,
      }, { classRequired: true, directExpense: true,
           faceParts: { usd: true, mxn: true } }));
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
  wireAttachments(els.detail);
  form.addEventListener("input", markDirty);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    if (!state.attachments.primary) {
      status.textContent = "Add the voucher before saving."; return;
    }
    status.textContent = "Saving…";
    try {
      const body = { submission_id: cashUUID(),
        expected_version: voucher.version,
        voucher_document_id: state.attachments.primary };
      if (usingVoucher) body.allocations = readAllocations(form);
      else body.receipts = [...form.querySelectorAll(".direct-receipt-editor")]
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
      await request(`/api/review/direct-expense/${voucher.voucher_id}/complete`, {
        method: "POST", body });
      await afterCommit(`${voucher.voucher_id} completed.`,
                        event.submitter?.value === "next");
    } catch (error) { status.textContent = error.message; }
  });
}

function renderSignout() {
  const voucher = state.detail.voucher;
  const needsReceipt = !state.detail.documents.some(document =>
    document.roles?.includes("support"));
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>File the voucher</strong>
    </div>
    <div class="compact-document-editor">
      <div class="button-row"><a class="btn ghost" href="/voucher/${esc(voucher.voucher_id)}/print">Print voucher</a>
        <a class="btn ghost" href="/signouts/${esc(voucher.voucher_id)}">Open signout workspace</a></div>
      ${attachmentPanel({ receipt: true, receiptOptional: true,
        voucherCanBeReceipt: needsReceipt })}
      <form id="signout-form" class="review-form sheet-action-form"><div class="row-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">File voucher &amp; review next</button>
      </div></form>
    </div>`;
  wireAttachments(els.detail);
  document.getElementById("signout-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Add the voucher first."; return;
    }
    try {
      await request(`/api/review/signout/${voucher.voucher_id}/attach-voucher`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: voucher.version,
          document_id: state.attachments.primary,
          receipt_document_id: state.attachments.support[0] || null,
          use_as_receipt: state.useAsReceipt },
      });
      await afterCommit(`${voucher.voucher_id} voucher filed.`, true);
    } catch (error) { status.textContent = error.message; }
  });
}

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>File the addendum</strong>
    </div>
    <div class="compact-document-editor">
      <a class="btn ghost" href="/voucher/${esc(addendum.voucher_id)}/addendum/${esc(addendum.addendum_id)}/print">Print addendum</a>
      ${attachmentPanel({ primaryLabel: "Addendum" })}
      <form id="addendum-form" class="review-form sheet-action-form"><div class="row-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">File addendum &amp; review next</button>
      </div></form>
    </div>`;
  wireAttachments(els.detail);
  document.getElementById("addendum-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Add the addendum first."; return;
    }
    try {
      await request(`/api/review/addendum/${addendum.addendum_id}/complete`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: addendum.version,
          document_id: state.attachments.primary },
      });
      await afterCommit(`${addendum.addendum_id} completed.`, true);
    } catch (error) { status.textContent = error.message; }
  });
}

function cashAccountOptions() {
  return state.options.cash_accounts.map(account =>
    `<option value="${esc(account.account_id)}">${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
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
      await Promise.all([loadWorklist(), loadInbox({ preserve: false })]);
      renderDocumentRail();
      showMessage(`${kind === "exchange" ? "Exchange" : "Withdrawal"} created.`);
    } catch (error) { status.textContent = error.message; }
  });
}

function packageAllocationDefaults(claim) {
  const faceUsd = claim.face_usd_cents ??
    (claim.face_currency === "USD" ? claim.face_value_minor : 0);
  const faceMxn = claim.face_mxn_centavos ??
    (claim.face_currency === "MXN" ? claim.face_value_minor : 0);
  return {
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
    usd_cents: claim.usd_paid_cents - claim.usd_change_cents,
    mxn_centavos: claim.mxn_paid_centavos - claim.mxn_change_centavos,
    face_value_minor: claim.face_value_minor,
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
  if (claim.document) setActiveDocument(claim.document);
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
      <td class="num">${esc([item.face_usd_cents ?? (item.face_currency === "USD" ? item.face_value_minor : 0), item.face_mxn_centavos ?? (item.face_currency === "MXN" ? item.face_value_minor : 0)].map((value, currency) => value ? `${currency ? "MXN" : "USD"} ${decimal(value)}` : "").filter(Boolean).join(" + ") || "—")}</td>
      <td class="num">${decimal(usdUsed)}</td><td class="num">${decimal(mxnUsed)}</td>
      <td><span class="status-pill ${itemVerified ? "success" : ""}">${esc(workflowStatus(item.state))}</span></td>
    </tr>`;
    if (!selected) return summary;
    const editor = reviewable ? `<form id="claim-review-form" class="review-form sheet-action-form">
      <section class="sheet-editor-section"><div class="section-heading"><div>
        <h3>Account and class codes</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one before these receipts can be approved.</p>` : ""}
        <div class="allocation-list">${allocations.map(row =>
          allocationRow(row, { faceRequired: true, classRequired: true,
            faceParts: { usd: true, mxn: true } })).join("")}</div>
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
        <th class="num">Receipt value</th><th class="num">USD cash</th>
        <th class="num">MXN cash</th><th>Status</th></tr></thead>
      <tbody>${receiptRows}</tbody></table></div>
    <section class="package-decision sheet-editor-section"><p class="eyebrow">Submission decision</p>
      <label><span>What needs to be corrected?</span><textarea id="return-summary" rows="2" ${reviewable ? "" : "disabled"}>${esc(packageData.admin_summary || "")}</textarea></label>
      <div class="package-actions">
        <button type="button" class="btn danger return-package" ${reviewable ? "" : "disabled"}>Request corrections</button>
        <button type="button" class="btn primary accept-package" ${verifiedCount === claims.length && reviewable ? "" : "disabled"}>Approve all ${claims.length} receipts</button>
      </div><output class="package-status" role="alert"></output>
    </section>`;
  els.detail.querySelectorAll(".package-receipt-row").forEach(row => {
    const open = () => {
      const nextIndex = Number(row.dataset.claimIndex);
      if (nextIndex === state.claimIndex) return;
      if (state.dirty && !window.confirm(
        "Discard unsaved changes and open another receipt?")) return;
      state.claimIndex = nextIndex;
      state.dirty = false;
      renderPackage();
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        open(); event.preventDefault();
      }
    });
  });
  const note = document.getElementById("return-item-note");
  note?.addEventListener("input", () => {
    state.returnNotes[claim.claim_id] = note.value; markDirty();
  });
  const form = document.getElementById("claim-review-form");
  if (form) {
    wireAllocationEditor(form, () => ({
      expense_account: state.options.expense_accounts[0]?.account_id || "",
      class_code: state.options.classes[0]?.class_code || "",
    }), { faceRequired: true, classRequired: true,
      faceParts: { usd: true, mxn: true } });
    form.addEventListener("input", markDirty);
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
      } catch (error) { status.textContent = error.message; }
    });
  }
  els.detail.querySelector(".return-package")?.addEventListener("click", async () => {
    const summary = document.getElementById("return-summary").value.trim();
    const status = els.detail.querySelector(".package-status");
    if (!summary) { status.textContent = "Explain what the signee must correct."; return; }
    if (!Object.values(state.returnNotes).some(value => value.trim())) {
      status.textContent = "Add a correction note to at least one receipt."; return;
    }
    if (!window.confirm("Send these correction requests to the recipient?")) return;
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
    const status = els.detail.querySelector(".package-status");
    if (!window.confirm(`Approve all ${claims.length} receipts?`)) return;
    try {
      await request(`/api/review/packages/${packageData.package_id}/accept`, {
        method: "POST", body: { submission_id: cashUUID() },
      });
      await afterCommit("Receipts approved and cash records updated.", true);
    } catch (error) { status.textContent = error.message; }
  });
}

function renderDetail() {
  switch (state.selectedItem.kind) {
    case "direct_expense": renderDirectExpense(); break;
    case "signout": renderSignout(); break;
    case "addendum": renderAddendum(); break;
    case "package": renderPackage(); break;
    default:
      els.detail.innerHTML = `<div class="empty-state"><strong>Unsupported review item</strong></div>`;
  }
}

async function refreshSelected() {
  if (!state.selectedItem) return;
  await Promise.all([loadWorklist(), loadInbox(), loadOptions()]);
  state.detail = await request(`/api/review/item/${state.selectedItem.kind}/${state.selectedItem.id}`);
  renderDetail();
  renderWorklist();
}

async function afterCommit(text, advance) {
  const oldVisible = currentFilteredItems();
  const oldIndex = oldVisible.findIndex(item => keyFor(item) === state.selectedKey);
  state.dirty = false;
  state.selectedItem = null;
  state.selectedKey = null;
  state.detail = null;
  state.activeDocument = null;
  renderDocumentRail();
  await Promise.all([loadWorklist(), loadInbox(), loadOptions()]);
  showMessage(text);
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

function renderUploadFiles() {
  els.uploadPages.innerHTML = "";
  state.uploadFiles.forEach((file, index) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="page-name">${esc(file.name)}</span><span class="mini-actions">
      <button type="button" data-action="up" ${index === 0 ? "disabled" : ""}>↑</button>
      <button type="button" data-action="down" ${index === state.uploadFiles.length - 1 ? "disabled" : ""}>↓</button>
      <button type="button" data-action="remove">×</button></span>`;
    li.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "remove") state.uploadFiles.splice(index, 1);
      if (action === "up") [state.uploadFiles[index - 1], state.uploadFiles[index]] =
        [state.uploadFiles[index], state.uploadFiles[index - 1]];
      if (action === "down") [state.uploadFiles[index + 1], state.uploadFiles[index]] =
        [state.uploadFiles[index], state.uploadFiles[index + 1]];
      renderUploadFiles();
    }));
    els.uploadPages.appendChild(li);
  });
  els.upload.disabled = state.uploadFiles.length === 0;
}

function addUploadFiles(files) {
  state.uploadFiles.push(...files);
  renderUploadFiles();
}

async function createInboxDocument(files) {
  const formData = new FormData();
  files.forEach(file => formData.append("files", file, file.name));
  return request("/api/documents/capture-merge", {
    method: "POST", formData,
  });
}

async function uploadFilesToSlot(files, target, slot = null) {
  if (!files.length) return;
  slot?.classList.add("uploading");
  showMessage("Uploading document…");
  try {
    const created = await createInboxDocument(files);
    await loadInbox({ preserve: false });
    const document = state.inbox.find(item =>
      item.document_id === created.document_id);
    if (!document) throw new Error("Uploaded document was not found");
    attachDocument(document, target);
  } catch (error) {
    showMessage(error.message, "danger");
  } finally {
    slot?.classList.remove("uploading");
  }
}

els.browse.addEventListener("click", () => {
  state.uploadTarget = null;
  els.picker.value = "";
  els.picker.click();
});
els.picker.addEventListener("cancel", () => { state.uploadTarget = null; });
els.picker.addEventListener("change", async () => {
  const files = [...els.picker.files];
  const target = state.uploadTarget;
  state.uploadTarget = null;
  els.picker.value = "";
  if (target) await uploadFilesToSlot(files, target);
  else addUploadFiles(files);
});
els.drop.addEventListener("dragover", event => {
  event.preventDefault(); els.drop.classList.add("dragging");
});
els.drop.addEventListener("dragleave", () => els.drop.classList.remove("dragging"));
els.drop.addEventListener("drop", event => {
  event.preventDefault(); els.drop.classList.remove("dragging");
  addUploadFiles(event.dataTransfer.files);
});
els.upload.addEventListener("click", async () => {
  els.upload.disabled = true;
  els.upload.textContent = "Saving…";
  try {
    const created = await createInboxDocument(state.uploadFiles);
    state.uploadFiles = [];
    renderUploadFiles();
    await loadInbox({ preserve: false });
    const document = state.inbox.find(item =>
      item.document_id === created.document_id);
    if (document) setActiveDocument(document);
    showMessage("Document saved to the inbox and selected.");
  } catch (error) { showMessage(error.message, "danger"); }
  finally { els.upload.textContent = "Upload document"; renderUploadFiles(); }
});
els.refreshInbox.addEventListener("click", () => loadInbox());
els.filter.addEventListener("input", renderWorklist);
els.kind.addEventListener("change", renderWorklist);
els.tabs.forEach(tab => tab.addEventListener("click", () => {
  if (state.dirty && !window.confirm("Discard unsaved review changes?")) return;
  state.mode = tab.dataset.reviewMode;
  state.selectedKey = null;
  state.selectedItem = null;
  state.detail = null;
  state.dirty = false;
  els.kind.value = "";
  els.kind.hidden = state.mode === "submissions";
  els.layout.classList.toggle("package-mode", state.mode === "submissions");
  els.evidenceHeading.textContent = state.mode === "submissions"
    ? "Receipt document" : "Supporting documents";
  els.workHeading.textContent = state.mode === "submissions"
    ? "Receipt submissions" : "Transactions";
  els.tabs.forEach(button => button.classList.toggle("active", button === tab));
  renderWorklist();
}));

els.list.addEventListener("keydown", event => {
  if (!["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
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

async function bootstrap() {
  try {
    await Promise.all([loadOptions(), loadWorklist(), loadInbox(), csrf()]);
    const requested = new URLSearchParams(location.search).get("item");
    const requestedItem = requested
      ? state.items.find(item => keyFor(item) === requested) : null;
    if (requestedItem?.kind === "package") {
      state.mode = "submissions";
      els.kind.hidden = true;
      els.layout.classList.add("package-mode");
      els.evidenceHeading.textContent = "Receipt document";
      els.workHeading.textContent = "Receipt submissions";
      els.tabs.forEach(button => button.classList.toggle("active",
        button.dataset.reviewMode === "submissions"));
      renderWorklist();
    }
    const first = requestedItem || currentFilteredItems()[0];
    if (first) await selectItem(first, { force: true });
  } catch (error) {
    showMessage(error.message, "danger");
    els.detail.innerHTML = `<div class="empty-state"><strong>Review could not load</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

bootstrap();
