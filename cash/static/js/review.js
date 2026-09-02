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
  evidenceList: document.getElementById("evidence-list"),
  viewer: document.getElementById("document-viewer"),
  drop: document.getElementById("evidence-drop"),
  picker: document.getElementById("evidence-picker"),
  browse: document.getElementById("browse-evidence"),
  upload: document.getElementById("upload-evidence"),
  uploadPages: document.getElementById("evidence-upload-pages"),
  refreshInbox: document.getElementById("refresh-inbox"),
};

const state = {
  csrf: null,
  items: [],
  inbox: [],
  options: { expense_accounts: [], classes: [], cash_accounts: [] },
  selectedKey: null,
  selectedItem: null,
  detail: null,
  activeDocument: null,
  attachments: { primary: null, support: [] },
  useAsReceipt: false,
  uploadFiles: [],
  claimIndex: 0,
  returnNotes: {},
  dirty: false,
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
    const record = item.kind === "package" ? item.secondary :
      item.kind === "inbox_document" ? "Uploaded document" : item.id;
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
  els.summary.textContent = `${state.items.length} record${state.items.length === 1 ? "" : "s"} to review` +
    (visible.length !== state.items.length ? ` · ${visible.length} shown` : "");
}

async function loadWorklist() {
  const data = await request("/api/review/worklist");
  state.items = data.items;
  renderWorklist();
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
  renderEvidenceList();
}

function renderEvidenceList() {
  els.inboxCount.textContent = state.inbox.length;
  els.evidenceList.innerHTML = "";
  if (!state.inbox.length) {
    els.evidenceList.innerHTML = `<li class="empty-state compact">
      <strong>No documents are waiting</strong><span>Upload a document above.</span></li>`;
    return;
  }
  for (const inboxDocument of state.inbox) {
    const li = document.createElement("li");
    const selected = state.activeDocument?.document_id === inboxDocument.document_id;
    li.className = selected ? "selected" : "";
    li.draggable = true;
    li.dataset.documentId = inboxDocument.document_id;
    li.innerHTML = `<button type="button" class="evidence-card">
      <span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>Uploaded ${esc(displayDateTime(inboxDocument.created_at))}</strong>
        <span>${esc(inboxDocument.page_count || "?")} page(s)</span>
      </span><span class="preview-arrow" aria-hidden="true">›</span>
    </button><div class="document-actions"><button type="button"
      class="put-on-desk">Attach to row</button></div>`;
    li.querySelector("button").addEventListener("click", () => {
      setActiveDocument(inboxDocument);
    });
    li.querySelector(".put-on-desk").addEventListener("click", () => {
      attachDocument(inboxDocument);
    });
    li.addEventListener("dragstart", event => {
      event.dataTransfer.setData("text/plain", inboxDocument.document_id);
      event.dataTransfer.effectAllowed = "copy";
    });
    els.evidenceList.appendChild(li);
  }
}

function setActiveDocument(document) {
  state.activeDocument = document;
  renderViewer();
  renderEvidenceList();
}

function renderViewer() {
  const document = state.activeDocument;
  if (!document) {
    els.viewer.className = "document-viewer empty";
    els.viewer.innerHTML = `<div class="document-placeholder">
      <strong>No document selected</strong>
      <span>Choose an inbox document to preview it.</span></div>`;
    return;
  }
  const url = document.stream_url ||
    `/api/documents/${document.document_id}/stream`;
  els.viewer.className = "document-viewer";
  els.viewer.innerHTML = `<div class="viewer-toolbar">
      <span>Uploaded document</span>
      <a href="${esc(url)}" target="_blank" rel="noopener">Open</a>
    </div><iframe src="${esc(url)}#view=FitH" title="Document preview"></iframe>`;
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
  return `<option value="">Choose spending category</option>` +
    state.options.expense_accounts.map(account =>
      `<option value="${esc(account.account_id)}" ${account.account_id === selected ? "selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
}

function classOptions(selected = "") {
  return `<option value="">Choose purpose category</option>` +
    state.options.classes.map(item =>
      `<option value="${esc(item.class_code)}" ${item.class_code === selected ? "selected" : ""}>${esc(item.name)} · ${esc(item.class_code)}</option>`).join("");
}

function allocationRow(allocation = {}, { faceRequired = false,
                                          faceParts = null,
                                          classRequired = false,
                                          deriveTender = false } = {}) {
  const receiptFields = faceParts ? `${faceParts.usd ? `<label><span>USD part of receipt</span><input name="face_usd" inputmode="decimal"
        value="${esc(allocation.face_usd ?? (allocation.receipt_face_usd_cents == null ? "" : decimal(allocation.receipt_face_usd_cents)))}" required></label>` : ""}
      ${faceParts.mxn ? `<label><span>MXN part of receipt</span><input name="face_mxn" inputmode="decimal"
        value="${esc(allocation.face_mxn ?? (allocation.receipt_face_mxn_centavos == null ? "" : decimal(allocation.receipt_face_mxn_centavos)))}" required></label>` : ""}` : `<label><span>Part of receipt total</span><input name="face_value"
        inputmode="decimal" value="${esc(allocation.face_value ??
          (allocation.face_value_minor == null ? "" : decimal(allocation.face_value_minor)))}"
        ${faceRequired ? "required" : ""}></label>`;
  return `<div class="allocation-row">
    <div class="allocation-row-head"><strong>Category split</strong>
      <button type="button" class="text-button danger remove-allocation">Remove</button></div>
    <div class="form-grid two">
      <label><span>Spending category</span><select name="expense_account" required>
        ${accountOptions(allocation.expense_account || allocation.to_account || "")}
      </select></label>
      <label><span>Purpose category</span><select name="class_code" ${classRequired ? "required" : ""}>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      ${deriveTender ? "" : `<label><span>USD in this split</span><input name="usd" inputmode="decimal"
        value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
      <label><span>MXN in this split</span><input name="mxn" inputmode="decimal"
        value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>`}
      ${receiptFields}
      <label><span>Project</span><input name="project"
        value="${esc(allocation.project || "")}"></label>
    </div>
    <label><span>Description</span><input name="description"
      value="${esc(allocation.description || "")}"></label>
  </div>`;
}

function wireAllocationEditor(form, defaults, requirements = {}) {
  const container = form.querySelector(".allocation-list");
  form.querySelector(".add-allocation")?.addEventListener("click", () => {
    container.insertAdjacentHTML("beforeend", allocationRow(defaults(), requirements));
    markDirty();
  });
  container.addEventListener("click", event => {
    if (!event.target.closest(".remove-allocation")) return;
    if (container.querySelectorAll(".allocation-row").length === 1) {
      showMessage("At least one category is required.", "warning");
      return;
    }
    event.target.closest(".allocation-row").remove();
    markDirty();
  });
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

function attachmentPanel({ support = false, alsoReceipt = false } = {}) {
  return `<section class="inline-document-actions" aria-label="Attach documents">
    <div class="attachment-choice document-drop-slot" data-document-slot="primary">
      <div><span>Required signed document</span><strong id="primary-document-label">${state.attachments.primary ? "Document attached" : "Drop or select a document"}</strong></div>
      <button type="button" class="btn ghost use-primary">Attach selected</button>
    </div>
    ${alsoReceipt ? `<div class="attachment-choice">
      <div><span>Receipt</span><strong>${state.useAsReceipt ? "Using signed voucher" : "Needed"}</strong></div>
      <button type="button" class="btn ghost also-receipt"
        aria-pressed="${state.useAsReceipt}">Also use voucher as the receipt</button>
    </div>` : ""}
    ${support ? `<div class="attachment-choice document-drop-slot" data-document-slot="support">
      <div><span>Other supporting documents</span><strong id="support-document-label">${state.attachments.support.length ? `${state.attachments.support.length} attached` : "Drop documents here"}</strong></div>
      <button type="button" class="btn ghost add-support">Attach selected</button>
    </div>` : ""}
  </section>`;
}

function updateAttachmentLabels(root = els.detail) {
  const primary = root.querySelector("#primary-document-label");
  if (primary) primary.textContent = state.attachments.primary
    ? "Document attached" : "Drop or select a document";
  const support = root.querySelector("#support-document-label");
  if (support) support.textContent = state.attachments.support.length
    ? `${state.attachments.support.length} attached` : "Drop documents here";
}

function attachDocument(document, target = null) {
  if (!state.selectedItem) {
    showMessage("Choose a record before attaching a document.", "warning");
    return;
  }
  if (state.selectedItem.kind === "inbox_document") {
    showMessage("This document is already the selected row. Record what it represents below.", "warning");
    return;
  }
  const supportsOtherDocuments = Boolean(els.detail.querySelector(".add-support"));
  const slot = target || (!state.attachments.primary || !supportsOtherDocuments
    ? "primary" : "support");
  if (slot === "support") {
    if (!state.attachments.support.includes(document.document_id)) {
      state.attachments.support.push(document.document_id);
    }
  } else {
    state.attachments.primary = document.document_id;
  }
  state.activeDocument = document;
  updateAttachmentLabels();
  renderViewer();
  renderEvidenceList();
  markDirty();
  showMessage(slot === "support" ? "Supporting document attached to this row."
    : "Document attached to this row.");
}

function wireAttachments(root, { support = false, alsoReceipt = false } = {}) {
  root.querySelector(".use-primary")?.addEventListener("click", () => {
    if (!state.activeDocument) {
      showMessage("Choose or upload a document first.", "warning"); return;
    }
    attachDocument(state.activeDocument, "primary");
  });
  if (alsoReceipt) root.querySelector(".also-receipt")?.addEventListener("click", event => {
    state.useAsReceipt = !state.useAsReceipt;
    const button = event.currentTarget;
    button.setAttribute("aria-pressed", String(state.useAsReceipt));
    button.classList.toggle("primary", state.useAsReceipt);
    button.classList.toggle("ghost", !state.useAsReceipt);
    const label = button.closest(".attachment-choice")?.querySelector("strong");
    if (label) label.textContent = state.useAsReceipt
      ? "Using signed voucher" : "Needed";
    markDirty();
  });
  if (support) root.querySelector(".add-support")?.addEventListener("click", () => {
    if (!state.activeDocument) {
      showMessage("Choose or upload a document first.", "warning"); return;
    }
    attachDocument(state.activeDocument, "support");
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
  const faceKnown = faceUsd != null || faceMxn != null;
  const mixedFace = faceUsd != null && faceMxn != null;
  const faceParts = { usd: faceUsd != null, mxn: faceMxn != null };
  const initial = [{
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
    usd_cents: sparse.usd_cents || voucher.authorized_usd_cents || 0,
    mxn_centavos: sparse.mxn_centavos || voucher.authorized_mxn_centavos || 0,
    face_value_minor: voucher.face_value_minor,
    receipt_face_usd_cents: faceUsd,
    receipt_face_mxn_centavos: faceMxn,
    description: voucher.purpose,
  }];
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>Finish this expense</strong>
    </div>
    <div class="row-editor-columns">
      ${attachmentPanel({ support: true })}
      <form id="direct-expense-form" class="review-form sheet-action-form">
      <section class="sheet-editor-section"><div class="section-heading"><div>
        <p class="eyebrow">Spending categories</p><h3>Where does this purchase belong?</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${!state.options.expense_accounts.length ? `<p class="callout danger">No spending categories are available.</p>` : ""}
        ${!state.options.classes.length ? `<p class="callout danger">No purpose categories are available. Add one in Settings before completing this expense.</p>` : ""}
        ${faceKnown ? `<p class="field-help">Enter each category's part of the receipt value. ${mixedFace ? "For a mixed receipt, also enter the USD and MXN cash used in each category." : "Cash used is divided automatically."}</p>` : ""}
        <div class="allocation-list">${initial.map(row => allocationRow(row, {
          faceRequired: faceKnown, faceParts,
          classRequired: true, deriveTender: faceKnown && !mixedFace })).join("")}</div>
      </section>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="stay">Save</button>
        <button type="submit" class="btn primary" value="next">Save &amp; review next</button>
      </div>
      </form>
    </div>`;
  const form = document.getElementById("direct-expense-form");
  wireAllocationEditor(form, () => ({
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
  }), { faceRequired: faceKnown, faceParts, classRequired: true,
    deriveTender: faceKnown && !mixedFace });
  wireAttachments(els.detail, { support: true });
  form.addEventListener("input", markDirty);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    if (!state.attachments.primary) {
      status.textContent = "Attach the signed document before saving."; return;
    }
    status.textContent = "Saving…";
    try {
      await request(`/api/review/direct-expense/${voucher.voucher_id}/complete`, {
        method: "POST", body: {
          submission_id: cashUUID(), expected_version: voucher.version,
          voucher_document_id: state.attachments.primary,
          support_document_ids: state.attachments.support,
          allocations: readAllocations(form),
        },
      });
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
      <strong>File the signed signout form</strong>
    </div>
    <div class="compact-document-editor">
      ${attachmentPanel({ alsoReceipt: needsReceipt })}
      <form id="signout-form" class="review-form sheet-action-form"><div class="row-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Attach form &amp; review next</button>
      </div></form>
    </div>`;
  wireAttachments(els.detail, { alsoReceipt: needsReceipt });
  document.getElementById("signout-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Select the signed form first."; return;
    }
    try {
      await request(`/api/review/signout/${voucher.voucher_id}/attach-voucher`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: voucher.version,
          document_id: state.attachments.primary,
          use_as_receipt: state.useAsReceipt },
      });
      await afterCommit(`${voucher.voucher_id} form attached.`, true);
    } catch (error) { status.textContent = error.message; }
  });
}

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>File the signed ${addendum.kind === "positive" ? "additional-cash" : "cash-return"} form</strong>
    </div>
    <div class="compact-document-editor">
      ${attachmentPanel()}
      <form id="addendum-form" class="review-form sheet-action-form"><div class="row-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Attach form &amp; review next</button>
      </div></form>
    </div>`;
  wireAttachments(els.detail);
  document.getElementById("addendum-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Select the signed form first."; return;
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

function renderInboxDocument() {
  const inboxDocument = state.detail.document;
  setActiveDocument(inboxDocument);
  els.detail.innerHTML = `<div class="row-editor-heading">
      <strong>Record what this document represents</strong>
      <span>The selected document is open in the left panel.</span>
    </div>
    <form id="inbox-decision-form" class="review-form sheet-action-form standalone-sheet-form">
      <section class="sheet-editor-section"><div class="form-grid two">
        <label><span>What happened?</span><select name="entry_type">
          <option value="withdrawal">Bank withdrawal</option>
          <option value="exchange">Currency exchange</option>
        </select></label>
        <label><span>Transaction date</span><input type="date" name="date" required></label>
        <label class="exchange-field"><span>Cash source</span><select name="source_account">${cashAccountOptions()}</select></label>
        <label><span>USD amount</span><input name="usd" inputmode="decimal" required></label>
        <label class="exchange-field"><span>MXN received</span><input name="mxn" inputmode="decimal"></label>
        <label class="full"><span>Notes</span><textarea name="notes" rows="2"></textarea></label>
      </div><p class="derived-movement" id="derived-entry">USD moved from the bank into the safe.</p></section>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button class="btn primary" type="submit">Save &amp; review next</button></div>
    </form>`;
  const form = document.getElementById("inbox-decision-form");
  const updateType = () => {
    const exchange = form.entry_type.value === "exchange";
    form.querySelectorAll(".exchange-field").forEach(el => { el.hidden = !exchange; });
    form.querySelector('[name="mxn"]').required = exchange;
    document.getElementById("derived-entry").textContent = exchange ?
      "USD was exchanged for MXN in the selected cash account." :
      "USD moved from the bank into the safe.";
  };
  form.entry_type.addEventListener("change", updateType);
  form.source_account.addEventListener("change", updateType);
  form.addEventListener("input", markDirty);
  updateType();
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
    try {
      await request(`/api/entries/${kind}`, { method: "POST", body });
      await afterCommit(`${kind === "exchange" ? "Exchange" : "Withdrawal"} created.`, true);
    } catch (error) { status.textContent = error.message; }
  });
}

function packageAllocationDefaults(claim) {
  return {
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
    usd_cents: claim.usd_paid_cents - claim.usd_change_cents,
    mxn_centavos: claim.mxn_paid_centavos - claim.mxn_change_centavos,
    face_value_minor: claim.face_value_minor,
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
      <td class="num">${esc(item.face_currency)} ${decimal(item.face_value_minor)}</td>
      <td class="num">${decimal(usdUsed)}</td><td class="num">${decimal(mxnUsed)}</td>
      <td><span class="status-pill ${itemVerified ? "success" : ""}">${esc(workflowStatus(item.state))}</span></td>
    </tr>`;
    if (!selected) return summary;
    const editor = reviewable ? `<form id="claim-review-form" class="review-form sheet-action-form">
      <section class="sheet-editor-section"><div class="section-heading"><div>
        <p class="eyebrow">Spending categories</p><h3>Where does this receipt belong?</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${!state.options.classes.length ? `<p class="callout danger">No purpose categories are available. Add one before these receipts can be approved.</p>` : ""}
        <p class="field-help">Split the receipt only when it belongs in more than one category.</p>
        <div class="allocation-list">${allocations.map(row =>
          allocationRow(row, { faceRequired: true, classRequired: true,
            deriveTender: true })).join("")}</div>
        <label><span>Note for the recipient</span><textarea name="admin_note" rows="2">${esc(claim.admin_note || "")}</textarea></label>
      </section>
      <div class="row-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="save">Save categories</button>
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
    }), { faceRequired: true, classRequired: true, deriveTender: true });
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
        } else showMessage("Categories saved.");
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
    case "inbox_document": renderInboxDocument(); break;
    case "package": renderPackage(); break;
    default:
      els.detail.innerHTML = `<div class="empty-state"><strong>Unsupported review item</strong></div>`;
  }
}

async function refreshSelected() {
  if (!state.selectedItem) return;
  await Promise.all([loadWorklist(), loadInbox()]);
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
  renderViewer();
  await Promise.all([loadWorklist(), loadInbox()]);
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

els.browse.addEventListener("click", () => els.picker.click());
els.picker.addEventListener("change", () => addUploadFiles(els.picker.files));
els.drop.addEventListener("dragover", event => {
  event.preventDefault(); els.drop.classList.add("dragging");
});
els.drop.addEventListener("dragleave", () => els.drop.classList.remove("dragging"));
els.drop.addEventListener("drop", event => {
  event.preventDefault(); els.drop.classList.remove("dragging");
  addUploadFiles(event.dataTransfer.files);
});
els.upload.addEventListener("click", async () => {
  const formData = new FormData();
  state.uploadFiles.forEach(file => formData.append("files", file));
  els.upload.disabled = true;
  els.upload.textContent = "Saving…";
  try {
    const created = await request("/api/documents/capture-merge", {
      method: "POST", formData,
    });
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
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

async function bootstrap() {
  try {
    const [optionsData] = await Promise.all([
      request("/api/review/options"), loadWorklist(), loadInbox(), csrf(),
    ]);
    state.options = optionsData;
    const first = currentFilteredItems()[0];
    if (first) await selectItem(first, { force: true });
  } catch (error) {
    showMessage(error.message, "danger");
    els.detail.innerHTML = `<div class="empty-state"><strong>Review could not load</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

bootstrap();
