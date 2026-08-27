/* Three-pane review desk: work queue, typed decision workspace, evidence rail. */
const els = {
  list: document.getElementById("worklist"),
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

function docShort(id) {
  return id ? id.slice(0, 8).toUpperCase() : "None selected";
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
  visible.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = keyFor(item) === state.selectedKey ? "selected" : "";
    const amounts = [item.usd && Number(item.usd) !== 0 ? money(item.usd, "USD") : "",
                     item.mxn && Number(item.mxn) !== 0 ? money(item.mxn, "MXN") : ""]
      .filter(Boolean).join(" · ");
    li.innerHTML = `<button type="button" class="work-card" data-index="${index}">
      <span class="work-topline"><span class="eyebrow">${esc(item.eyebrow)}</span>
        <time>${esc(item.date || "")}</time></span>
      <strong>${esc(item.label || item.id)}</strong>
      <span class="work-secondary">${esc(item.secondary || item.id)}</span>
      ${amounts ? `<span class="work-amount">${esc(amounts)}</span>` : ""}
      <span class="badge-row">${(item.badges || []).map(b =>
        `<span class="badge">${esc(b)}</span>`).join("")}</span>
    </button>`;
    li.querySelector("button").addEventListener("click", () => selectItem(item));
    els.list.appendChild(li);
  });
  els.empty.hidden = visible.length !== 0;
  els.summary.textContent = `${state.items.length} open item${state.items.length === 1 ? "" : "s"}` +
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
      <strong>Inbox is clear</strong><span>Upload evidence above.</span></li>`;
    return;
  }
  for (const inboxDocument of state.inbox) {
    const li = document.createElement("li");
    const selected = state.activeDocument?.document_id === inboxDocument.document_id;
    li.className = selected ? "selected" : "";
    li.innerHTML = `<button type="button" class="evidence-card">
      <span class="pdf-mark" aria-hidden="true">PDF</span>
      <span class="evidence-copy"><strong>Document ${docShort(inboxDocument.document_id)}</strong>
        <span>${esc(inboxDocument.page_count || "?")} page(s) · ${esc((inboxDocument.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </span><span class="preview-arrow" aria-hidden="true">›</span>
    </button>`;
    li.querySelector("button").addEventListener("click", () => {
      setActiveDocument(inboxDocument);
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
      <span>Document ${docShort(document.document_id)}</span>
      <a href="${esc(url)}" target="_blank" rel="noopener">Open</a>
    </div><iframe src="${esc(url)}#view=FitH" title="Document preview"></iframe>`;
}

function markDirty() {
  state.dirty = true;
}

async function selectItem(item, { force = false } = {}) {
  if (!force && state.dirty &&
      !window.confirm("Discard unsaved review changes and open another item?")) return;
  state.dirty = false;
  state.selectedItem = item;
  state.selectedKey = keyFor(item);
  state.claimIndex = 0;
  state.returnNotes = {};
  state.attachments = { primary: null, support: [] };
  renderWorklist();
  els.detail.innerHTML = `<div class="loading-state">Loading review…</div>`;
  try {
    state.detail = await request(`/api/review/item/${item.kind}/${item.id}`);
    renderDetail();
  } catch (error) {
    els.detail.innerHTML = `<div class="empty-state"><strong>Unable to open item</strong>
      <span>${esc(error.message)}</span></div>`;
  }
}

function detailHeader(eyebrow, title, subtitle, badges = []) {
  return `<header class="detail-header">
    <div><p class="eyebrow">${esc(eyebrow)}</p><h2>${esc(title)}</h2>
      <p class="muted">${esc(subtitle || "")}</p></div>
    <div class="badge-row">${badges.map(b =>
      `<span class="badge">${esc(b)}</span>`).join("")}</div>
  </header>`;
}

function fact(label, value, detail = "") {
  return `<div class="fact"><span>${esc(label)}</span><strong>${esc(value)}</strong>
    ${detail ? `<small>${esc(detail)}</small>` : ""}</div>`;
}

function accountOptions(selected = "") {
  return `<option value="">Choose account</option>` +
    state.options.expense_accounts.map(account =>
      `<option value="${esc(account.account_id)}" ${account.account_id === selected ? "selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
}

function classOptions(selected = "") {
  return `<option value="">Choose class</option>` +
    state.options.classes.map(item =>
      `<option value="${esc(item.class_code)}" ${item.class_code === selected ? "selected" : ""}>${esc(item.name)} · ${esc(item.class_code)}</option>`).join("");
}

function allocationRow(allocation = {}, { faceRequired = false,
                                          classRequired = false,
                                          deriveTender = false } = {}) {
  return `<div class="allocation-row">
    <div class="allocation-row-head"><strong>Allocation</strong>
      <button type="button" class="text-button danger remove-allocation">Remove</button></div>
    <div class="form-grid two">
      <label><span>Expense account</span><select name="expense_account" required>
        ${accountOptions(allocation.expense_account || allocation.to_account || "")}
      </select></label>
      <label><span>Class</span><select name="class_code" ${classRequired ? "required" : ""}>
        ${classOptions(allocation.class_code || "")}
      </select></label>
      ${deriveTender ? "" : `<label><span>USD allocated</span><input name="usd" inputmode="decimal"
        value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
      <label><span>MXN allocated</span><input name="mxn" inputmode="decimal"
        value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>`}
      <label><span>Receipt face share</span><input name="face_value"
        inputmode="decimal" value="${esc(allocation.face_value ??
          (allocation.face_value_minor == null ? "" : decimal(allocation.face_value_minor)))}"
        ${faceRequired ? "required" : ""}></label>
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
      showMessage("At least one allocation is required.", "warning");
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
    face_value: row.querySelector('[name="face_value"]').value || null,
    project: row.querySelector('[name="project"]').value || "",
    description: row.querySelector('[name="description"]').value || "",
  }));
}

function attachmentPanel({ support = false } = {}) {
  return `<section class="decision-section attachment-section">
    <div class="section-heading"><div><p class="eyebrow">Evidence decision</p>
      <h3>Attach paperwork</h3></div></div>
    <div class="attachment-choice">
      <div><span>Signed document</span><strong id="primary-document-label">${docShort(state.attachments.primary)}</strong></div>
      <button type="button" class="btn ghost use-primary">Use selected PDF</button>
    </div>
    ${support ? `<div class="attachment-choice">
      <div><span>Additional support</span><strong id="support-document-label">${state.attachments.support.length ? state.attachments.support.map(docShort).join(", ") : "None"}</strong></div>
      <button type="button" class="btn ghost add-support">Add selected PDF</button>
    </div>` : ""}
    <p class="field-help">Select or upload a document in the evidence rail, then assign it here.</p>
  </section>`;
}

function wireAttachments(root, { support = false } = {}) {
  root.querySelector(".use-primary")?.addEventListener("click", () => {
    if (!state.activeDocument) {
      showMessage("Select or upload a document first.", "warning"); return;
    }
    state.attachments.primary = state.activeDocument.document_id;
    root.querySelector("#primary-document-label").textContent =
      docShort(state.attachments.primary);
    markDirty();
  });
  if (support) root.querySelector(".add-support")?.addEventListener("click", () => {
    if (!state.activeDocument) {
      showMessage("Select or upload a document first.", "warning"); return;
    }
    const id = state.activeDocument.document_id;
    if (!state.attachments.support.includes(id)) state.attachments.support.push(id);
    root.querySelector("#support-document-label").textContent =
      state.attachments.support.map(docShort).join(", ");
    markDirty();
  });
}

function renderDirectExpense() {
  const { voucher: voucher, allocations } = state.detail;
  const sparse = allocations[0] || {};
  const faceKnown = voucher.face_value_minor != null;
  const initial = [{
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
    usd_cents: sparse.usd_cents || voucher.authorized_usd_cents || 0,
    mxn_centavos: sparse.mxn_centavos || voucher.authorized_mxn_centavos || 0,
    face_value_minor: voucher.face_value_minor,
    description: voucher.purpose,
  }];
  els.detail.innerHTML = `${detailHeader("Direct expense", voucher.voucher_id,
    `${voucher.recipient} · ${voucher.purpose}`, ["allocation required"])}
    <section class="decision-section"><div class="section-heading"><div>
      <p class="eyebrow">Prepared facts</p><h3>Verify the signed paper</h3></div>
      <span class="version-pill">Version ${voucher.version}</span></div>
      <div class="fact-grid">
        ${fact("Receipt face", faceKnown ? money(decimal(voucher.face_value_minor), voucher.face_currency) : "Legacy voucher", faceKnown ? "Whole document value" : "Confirm from signed paper")}
        ${fact("USD paid", money(decimal(voucher.usd_paid_cents), "USD"))}
        ${fact("MXN paid", money(decimal(voucher.mxn_paid_centavos), "MXN"))}
        ${fact("Change returned", `${money(decimal(voucher.usd_change_cents), "USD")} · ${money(decimal(voucher.mxn_change_centavos), "MXN")}`)}
        ${fact("Net cash movement", `${money(decimal(voucher.authorized_usd_cents), "USD")} · ${money(decimal(voucher.authorized_mxn_centavos), "MXN")}`)}
      </div>
    </section>
    ${attachmentPanel({ support: true })}
    <form id="direct-expense-form" class="review-form">
      <section class="decision-section"><div class="section-heading"><div>
        <p class="eyebrow">Accounting decision</p><h3>Allocate the expense</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${!state.options.expense_accounts.length ? `<p class="callout danger">No expense accounts are configured.</p>` : ""}
        ${!state.options.classes.length ? `<p class="callout danger">No accounting classes are configured. Add one in Settings before completing this expense.</p>` : ""}
        ${faceKnown ? `<p class="field-help">Enter how much of the receipt belongs to each allocation. USD and MXN tender are divided proportionally in exact minor units; any remainder is assigned deterministically so the original cash movement is conserved.</p>` : ""}
        <div class="allocation-list">${initial.map(row => allocationRow(row, {
          faceRequired: faceKnown, classRequired: true,
          deriveTender: faceKnown })).join("")}</div>
      </section>
      <div class="sticky-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="stay">Commit</button>
        <button type="submit" class="btn primary" value="next">Commit &amp; next</button>
      </div>
    </form>`;
  const form = document.getElementById("direct-expense-form");
  wireAllocationEditor(form, () => ({
    expense_account: state.options.expense_accounts[0]?.account_id || "",
    class_code: state.options.classes[0]?.class_code || "",
  }), { faceRequired: faceKnown, classRequired: true,
    deriveTender: faceKnown });
  wireAttachments(els.detail, { support: true });
  form.addEventListener("input", markDirty);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const status = form.querySelector(".form-status");
    if (!state.attachments.primary) {
      status.textContent = "Attach the signed voucher before committing."; return;
    }
    status.textContent = "Committing…";
    try {
      await request(`/api/review/direct-expense/${voucher.voucher_id}/complete`, {
        method: "POST", body: {
          submission_id: crypto.randomUUID(), expected_version: voucher.version,
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
  const balance = state.detail.balance || { usd: "0.00", mxn: "0.00", basis_usd: "0" };
  els.detail.innerHTML = `${detailHeader("Signout paperwork", voucher.voucher_id,
    `${voucher.recipient} · ${voucher.purpose}`, ["custody voucher needed"])}
    <section class="decision-section"><p class="eyebrow">Custody facts</p>
      <div class="fact-grid">
        ${fact("Issued", `${money(decimal(voucher.authorized_usd_cents), "USD")} · ${money(decimal(voucher.authorized_mxn_centavos), "MXN")}`)}
        ${fact("Currently held", `${money(balance.usd, "USD")} · ${money(balance.mxn, "MXN")}`)}
        ${fact("MXN basis held", money(balance.basis_usd, "USD"))}
        ${fact("Purpose", voucher.purpose || "—")}
      </div>
      <p class="callout">Attaching the custody voucher resolves this paperwork task. It does not close the signout; closure happens only after complete reconciliation.</p>
    </section>
    ${attachmentPanel()}
    <form id="signout-form" class="review-form"><div class="sticky-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Attach voucher &amp; next</button>
    </div></form>`;
  wireAttachments(els.detail);
  document.getElementById("signout-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Select the signed custody voucher first."; return;
    }
    try {
      await request(`/api/review/signout/${voucher.voucher_id}/attach-voucher`, {
        method: "POST", body: { submission_id: crypto.randomUUID(),
          expected_version: voucher.version,
          document_id: state.attachments.primary },
      });
      await afterCommit(`${voucher.voucher_id} paperwork attached.`, true);
    } catch (error) { status.textContent = error.message; }
  });
}

function renderAddendum() {
  const addendum = state.detail.addendum;
  els.detail.innerHTML = `${detailHeader("Addendum paperwork", addendum.addendum_id,
    `${addendum.voucher_id} · ${addendum.kind === "positive" ? "Additional cash" : "Cash return"}`,
    ["signed document needed"])}
    <section class="decision-section"><div class="fact-grid">
      ${fact("Effective date", addendum.date)}
      ${fact("Cash movement", `${money(decimal(addendum.usd_cents), "USD")} · ${money(decimal(addendum.mxn_centavos), "MXN")}`)}
      ${fact("Reason", addendum.reason || "—")}
      ${fact("Parent voucher", addendum.voucher_id)}
    </div></section>
    ${attachmentPanel()}
    <form id="addendum-form" class="review-form"><div class="sticky-actions">
      <output class="form-status" role="alert"></output>
      <button class="btn primary" type="submit">Complete addendum &amp; next</button>
    </div></form>`;
  wireAttachments(els.detail);
  document.getElementById("addendum-form").addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    if (!state.attachments.primary) {
      status.textContent = "Select the signed addendum first."; return;
    }
    try {
      await request(`/api/review/addendum/${addendum.addendum_id}/complete`, {
        method: "POST", body: { submission_id: crypto.randomUUID(),
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
  els.detail.innerHTML = `${detailHeader("Inbox decision", `Document ${docShort(inboxDocument.document_id)}`,
    `${inboxDocument.page_count || "?"} page(s) · captured ${(inboxDocument.created_at || "").slice(0, 10)}`,
    ["unassigned"])}
    <section class="decision-section"><p class="callout">Review the paper, then create the transaction it proves. The document will be filed with that transaction.</p></section>
    <form id="inbox-decision-form" class="review-form">
      <section class="decision-section"><div class="form-grid two">
        <label><span>Document proves</span><select name="entry_type">
          <option value="withdrawal">Bank withdrawal</option>
          <option value="exchange">Currency exchange</option>
        </select></label>
        <label><span>Transaction date</span><input type="date" name="date" required></label>
        <label class="exchange-field"><span>Cash source</span><select name="source_account">${cashAccountOptions()}</select></label>
        <label><span>USD amount</span><input name="usd" inputmode="decimal" required></label>
        <label class="exchange-field"><span>MXN received</span><input name="mxn" inputmode="decimal"></label>
        <label class="full"><span>Notes</span><textarea name="notes" rows="2"></textarea></label>
      </div><p class="derived-movement" id="derived-entry">BANK → SAFE</p></section>
      <div class="sticky-actions"><output class="form-status" role="alert"></output>
        <button class="btn primary" type="submit">Create transaction &amp; next</button></div>
    </form>`;
  const form = document.getElementById("inbox-decision-form");
  const updateType = () => {
    const exchange = form.entry_type.value === "exchange";
    form.querySelectorAll(".exchange-field").forEach(el => { el.hidden = !exchange; });
    form.querySelector('[name="mxn"]').required = exchange;
    document.getElementById("derived-entry").textContent = exchange ?
      `${form.source_account.value || "SAFE"} → FX counterparty (USD out, MXN in)` :
      "BANK → SAFE";
  };
  form.entry_type.addEventListener("change", updateType);
  form.source_account.addEventListener("change", updateType);
  form.addEventListener("input", markDirty);
  updateType();
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const data = new FormData(form);
    const kind = data.get("entry_type");
    const body = { submission_id: crypto.randomUUID(), date: data.get("date"),
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
    els.detail.innerHTML = `${detailHeader("Receipt package", packageData.voucher_id,
      "No receipt claims", [packageData.status])}`; return;
  }
  state.claimIndex = Math.max(0, Math.min(state.claimIndex, claims.length - 1));
  const claim = claims[state.claimIndex];
  if (claim.document) setActiveDocument(claim.document);
  const allocations = claim.allocations.length ? claim.allocations :
    [packageAllocationDefaults(claim)];
  const verified = claim.state === "verified" &&
    claim.verified_version === claim.content_version;
  const verifiedCount = claims.filter(item => item.state === "verified" &&
    item.verified_version === item.content_version).length;
  const reviewable = packageData.status !== "needs_changes";
  els.detail.innerHTML = `${detailHeader("Receipt package", packageData.voucher_id,
    `${packageData.recipient} · package ${packageData.seq}`,
    [`${verifiedCount}/${claims.length} verified`, packageData.status])}
    <nav class="claim-nav" aria-label="Package receipts">
      <button type="button" class="btn ghost previous-claim" ${state.claimIndex === 0 ? "disabled" : ""}>← Previous</button>
      <span>Receipt ${state.claimIndex + 1} of ${claims.length}</span>
      <button type="button" class="btn ghost next-claim" ${state.claimIndex === claims.length - 1 ? "disabled" : ""}>Next →</button>
    </nav>
    <section class="decision-section"><div class="section-heading"><div>
      <p class="eyebrow">Signee-submitted facts</p><h3>${esc(claim.vendor || "Unnamed vendor")}</h3></div>
      <span class="status-pill ${verified ? "success" : ""}">${verified ? "Verified" : esc(claim.state)}</span></div>
      <div class="fact-grid">
        ${fact("Receipt date", claim.claim_date)}
        ${fact("Face value", money(decimal(claim.face_value_minor), claim.face_currency))}
        ${fact("Paid", `${money(decimal(claim.usd_paid_cents), "USD")} · ${money(decimal(claim.mxn_paid_centavos), "MXN")}`)}
        ${fact("Change", `${money(decimal(claim.usd_change_cents), "USD")} · ${money(decimal(claim.mxn_change_centavos), "MXN")}`)}
        ${fact("Net settlement", `${money(decimal(claim.usd_paid_cents - claim.usd_change_cents), "USD")} · ${money(decimal(claim.mxn_paid_centavos - claim.mxn_change_centavos), "MXN")}`)}
        ${fact("Description", claim.description || "—")}
      </div>
      ${claim.notes ? `<p class="submitted-note"><strong>Signee note:</strong> ${esc(claim.notes)}</p>` : ""}
    </section>
    ${reviewable ? `<form id="claim-review-form" class="review-form">
      <section class="decision-section"><div class="section-heading"><div>
        <p class="eyebrow">Administrator decision</p><h3>Accounting allocation</h3></div>
        <button type="button" class="btn ghost add-allocation">Add split</button></div>
        ${!state.options.classes.length ? `<p class="callout danger">No accounting classes are configured. Add one before this package can be accepted.</p>` : ""}
        <p class="field-help">Split the receipt face value. Tender and change are distributed proportionally in exact cents/centavos and always conserve the signee-submitted settlement.</p>
        <div class="allocation-list">${allocations.map(row =>
          allocationRow(row, { faceRequired: true, classRequired: true,
            deriveTender: true })).join("")}</div>
        <label><span>Administrator note</span><textarea name="admin_note" rows="2">${esc(claim.admin_note || "")}</textarea></label>
      </section>
      <div class="sticky-actions"><output class="form-status" role="alert"></output>
        <button type="submit" class="btn ghost" value="save">Save allocation</button>
        <button type="submit" class="btn primary" value="verify">Save &amp; verify receipt</button>
      </div></form>` : `<section class="decision-section"><p class="callout warning">This package has been returned to the signee: ${esc(packageData.admin_summary || "Corrections requested")}</p></section>`}
    <section class="decision-section package-decision"><p class="eyebrow">Package decision</p>
      <label><span>Return summary</span><textarea id="return-summary" rows="2" ${reviewable ? "" : "disabled"}>${esc(packageData.admin_summary || "")}</textarea></label>
      <label><span>Correction note for this receipt</span><textarea id="return-item-note" rows="2" ${reviewable ? "" : "disabled"}>${esc(state.returnNotes[claim.claim_id] || "")}</textarea></label>
      <div class="package-actions">
        <button type="button" class="btn danger return-package" ${reviewable ? "" : "disabled"}>Return package</button>
        <button type="button" class="btn primary accept-package" ${verifiedCount === claims.length && reviewable ? "" : "disabled"}>Accept all ${claims.length} receipts</button>
      </div><output class="package-status" role="alert"></output>
    </section>`;
  els.detail.querySelector(".previous-claim")?.addEventListener("click", () => {
    state.claimIndex -= 1; state.dirty = false; renderPackage();
  });
  els.detail.querySelector(".next-claim")?.addEventListener("click", () => {
    state.claimIndex += 1; state.dirty = false; renderPackage();
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
          method: "POST", body: { submission_id: crypto.randomUUID(),
            expected_content_version: claim.content_version,
            allocations: readAllocations(form) },
        });
        if (event.submitter?.value === "verify") {
          await request(`/api/review/claims/${claim.claim_id}/verify`, {
            method: "POST", body: { submission_id: crypto.randomUUID(),
              expected_content_version: claim.content_version,
              admin_note: form.admin_note.value },
          });
          showMessage(`Receipt ${state.claimIndex + 1} verified.`);
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
    if (!window.confirm("Return this entire package to the signee?")) return;
    try {
      await request(`/api/review/packages/${packageData.package_id}/return`, {
        method: "POST", body: { submission_id: crypto.randomUUID(),
          admin_summary: summary, item_notes: state.returnNotes },
      });
      state.dirty = false;
      await afterCommit("Package returned with correction notes.", true);
    } catch (error) { status.textContent = error.message; }
  });
  els.detail.querySelector(".accept-package")?.addEventListener("click", async () => {
    const status = els.detail.querySelector(".package-status");
    if (!window.confirm(`Accept all ${claims.length} receipts atomically?`)) return;
    try {
      await request(`/api/review/packages/${packageData.package_id}/accept`, {
        method: "POST", body: { submission_id: crypto.randomUUID() },
      });
      await afterCommit("Package accepted and ledger rows created.", true);
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
      <strong>${next.length ? "Item committed" : "Review queue complete"}</strong>
      <span>${next.length ? "Select the next item when ready." : "There is no open work matching this view."}</span></div>`;
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
  finally { els.upload.textContent = "Save to inbox"; renderUploadFiles(); }
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
