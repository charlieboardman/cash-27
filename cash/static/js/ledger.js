/* Ledger browser with a transaction drawer for documents, edits, and voiding. */
const tableBody = document.querySelector("#txns tbody");
const drawer = document.getElementById("transaction-drawer");
const drawerBody = document.getElementById("drawer-body");
const scrim = document.getElementById("drawer-scrim");
const searchInput = document.getElementById("ledger-search");
const viewButtons = [...document.querySelectorAll(".ledger-view")];
let currentTransactionId = null;
let activeView = "active";
let currentPage = 1;
let totalPages = 1;
let firstPage = 1;
let loadVersion = 0;
let loading = false;
let failedLoad = null;
const startSentinel = document.getElementById("ledger-start");
const endSentinel = document.getElementById("ledger-end");
const retryButton = document.getElementById("retry-ledger");
const esc = cashEsc;
const api = cashApi;


const typeLabel = cashLabels.type;
const documentRoles = { voucher: "Voucher", support: "Receipt" };

const historyLabels = {
  date: "Date", same_day_seq: "Order on this date", from_account: "From account",
  to_account: "To account", usd_cents: "USD amount", mxn_centavos: "MXN amount",
  class_code: "Class code", receipt_face_usd_cents: "Receipt amount (USD)",
  receipt_face_mxn_centavos: "Receipt amount (MXN)", assigned_rate_milli: "Exchange rate",
  recipient: "Recipient", vendor: "Vendor", description: "Description",
  project: "Project", notes: "Notes", posting_state: "Status", void_reason: "Void reason",
};
function historyValue(field, value) {
  if (value == null || value === "") return "—";
  if (field.endsWith("_cents") || field.endsWith("_centavos")) return esc((value / 100).toFixed(2));
  if (field === "assigned_rate_milli") return esc((value / 1000).toFixed(3));
  if (field === "posting_state") {
    const label = value === "active" ? "Active" : value === "void" ? "Void" : value;
    return `<span data-i18n="${esc(label)}">${esc(cashI18n.t(label))}</span>`;
  }
  return esc(value);
}
function historyChanges(edit, type) {
  return `<dl class="edit-changes">${edit.changes.map(change => {
    const label = type === "signout" && change.field === "description"
      ? "Purpose" : historyLabels[change.field] || change.field;
    return `<div><dt data-i18n="${esc(label)}">${esc(cashI18n.t(label))}</dt>
      <dd><span>${historyValue(change.field, change.before)}</span> → <span>${historyValue(change.field, change.after)}</span></dd></div>`;
  }).join("")}</dl>`;
}

function transactionStatus(transaction) {
  if (transaction.posting_state !== "active") return cashLabels.status(transaction.posting_state);
  return transaction.incomplete ? "Incomplete" : "";
}

async function load(params = "", mode = "replace") {
  if (mode !== "replace" && loading) return;
  const version = ++loadVersion;
  loading = true;
  retryButton.hidden = true;
  observer.disconnect();
  try {
    const data = await api("/api/ledger" + params);
    if (version !== loadVersion) return;
    const anchor = tableBody.firstElementChild;
    const anchorTop = anchor?.getBoundingClientRect().top;
    const fragment = document.createDocumentFragment();

    document.getElementById("starting-balance-callout").hidden = data.has_opening;
    fillFilterChoices(data.filters);
    // Selects fill after the first load; restore a value that arrived in the URL.
    for (const name of ["fy", "account", "class"]) {
      const wanted = new URLSearchParams(window.location.search).get(name);
      if (wanted && !filterForm.elements[name].value) filterForm.elements[name].value = wanted;
    }
    if (mode === "replace") tableBody.innerHTML = "";
    for (const transaction of data.transactions) {
      const row = document.createElement("tr");
      row.className = transaction.posting_state === "active"
        ? "" : transaction.posting_state;
      row.tabIndex = 0;
      row.dataset.txn = transaction.txn_id;
      const stateLabel = transactionStatus(transaction);
      const stateClass = transaction.incomplete && transaction.posting_state === "active"
        ? " warning" : "";
      const calculatedUsd = transaction.txn_type === "expense" &&
        transaction.calc_usd != null
        ? cashMoney(transaction.calc_usd) : "—";
      row.innerHTML = `<td class="txn-id">#${transaction.txn_id}</td>
        <td>${esc(transaction.date)}</td><td>${esc(typeLabel(transaction.txn_type))}</td>
        <td class="ledger-vendor">${esc(transaction.vendor || (transaction.txn_type === "expense" && transaction.to_account === "UNALLOCATED_EXPENSE" ? transaction.recipient : "") || "—")}</td>
        <td><span class="movement-route">${esc(transaction.from_account)} <b>→</b> ${esc(transaction.to_account)}</span>
          ${transaction.description ? `<small>${esc(transaction.description)}</small>` : ""}</td>
        <td class="num">${cashMoney(transaction.usd)}</td>
        <td class="num">${cashMoney(transaction.mxn)}</td>
        <td class="num">${calculatedUsd}</td>
        <td>${esc(transaction.voucher_id || "—")}</td>
        <td>${stateLabel ? `<span class="status-pill${stateClass}">${esc(stateLabel)}</span>` : ""}</td>`;
      row.addEventListener("click", () => openTransaction(transaction.txn_id));
      row.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          openTransaction(transaction.txn_id); event.preventDefault();
        }
      });
      fragment.appendChild(row);
    }
    if (mode === "prepend") tableBody.prepend(fragment);
    else tableBody.append(fragment);
    if (mode === "prepend" && anchor) {
      window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
    }
    document.getElementById("ledger-empty").hidden = tableBody.children.length !== 0;
    if (mode !== "append") firstPage = data.page;
    if (mode !== "prepend") currentPage = data.page;
    totalPages = data.pages;
    const first = data.total ? (firstPage - 1) * data.per_page + 1 : 0;
    const last = Math.min(currentPage * data.per_page, data.total);
    document.getElementById("ledger-summary").textContent = cashI18n.t(
      "Showing {first}–{last} of {total}", { first, last, total: data.total });
    if (mode === "replace" && new URLSearchParams(params).get("page") === "last") {
      tableBody.lastElementChild?.scrollIntoView({ block: "end" });
    }
    failedLoad = null;
  } catch (error) {
    if (version !== loadVersion) return;
    failedLoad = () => load(params, mode);
    retryButton.hidden = false;
    console.error(error);
  } finally {
    if (version === loadVersion) {
      loading = false;
      if (!failedLoad) {
        if (firstPage > 1) observer.observe(startSentinel);
        if (currentPage < totalPages) observer.observe(endSentinel);
      }
    }
  }
}

const observer = new IntersectionObserver(entries => {
  if (loading || failedLoad) return;
  const end = entries.find(entry => entry.target === endSentinel && entry.isIntersecting);
  const start = entries.find(entry => entry.target === startSentinel && entry.isIntersecting);
  const mode = end && currentPage < totalPages ? "append"
    : start && firstPage > 1 ? "prepend" : null;
  if (!mode) return;
  const params = new URLSearchParams(currentFilterQuery());
  params.set("page", String(mode === "append" ? currentPage + 1 : firstPage - 1));
  load(`?${params}`, mode);
}, { rootMargin: "150px" });
retryButton.addEventListener("click", () => failedLoad?.());

function optionRows(rows, valueKey, labelKey, selected) {
  return rows.map(row => `<option value="${esc(row[valueKey])}"
    ${row[valueKey] === selected ? "selected" : ""}>${esc(row[labelKey])} · ${esc(row[valueKey])}</option>`).join("");
}

// Focus goes into the drawer when it opens, stays inside while it is open,
// and returns to the row that opened it.
let drawerOpener = null;
let drawerScrollPosition = 0;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

async function openTransaction(txnId) {
  if (drawer.hidden) {
    drawerOpener = document.activeElement;
    drawerScrollPosition = window.scrollY;
  }
  currentTransactionId = txnId;
  const url = new URL(window.location.href);
  url.searchParams.set("txn", String(txnId));
  window.history.replaceState(window.history.state, "", url);
  drawer.hidden = false;
  scrim.hidden = false;
  drawer.focus();
  drawerBody.innerHTML = `<div class="loading-state">Loading transaction…</div>`;
  try {
    const data = await api(`/api/ledger/${txnId}`);
    const t = data.transaction;
    // A completed direct expense can take a receipt that arrived later.
    data.inbox = t.posting_state === "active" && !t.incomplete && t.txn_type === "expense" &&
      t.from_account === "SAFE" && t.voucher_id
      ? (await api("/api/documents/inbox")).documents.filter(doc => !cashDocumentPending(doc))
      : null;
    renderDrawer(data);
  } catch (error) {
    drawerBody.innerHTML = `<div class="empty-state"><strong>Unable to load</strong><span>${esc(error.message)}</span></div>`;
  }
}

// The fields each kind of transaction can change (history.EDITABLE_BY_TYPE).
const EDIT_FIELDS = {
  expense: ["date", "to_account", "class_code", "usd", "mxn", "receipt_face", "vendor", "project", "description", "notes"],
  signout: ["date", "usd", "mxn", "description", "notes"],
  addendum: ["date", "usd", "mxn", "description", "notes"],
  exchange: ["date", "from_account", "usd", "mxn", "notes"],
  bank_withdrawal: ["date", "usd", "notes"],
  opening: ["date", "usd", "mxn", "assigned_rate", "notes"],
  donation: ["date", "usd", "mxn", "assigned_rate", "notes"],
  adjustment: ["date", "from_account", "to_account", "usd", "mxn", "assigned_rate", "description", "notes"],
};

function editFields(transaction, data, classOptions, requiresClass) {
  const type = transaction.txn_type;
  const accounts = kinds => data.accounts.filter(account => !kinds || kinds.includes(account.kind));
  const input = (name, label, value, attributes = "") =>
    `<label><span>${label}</span><input name="${name}" value="${esc(value ?? "")}" ${attributes}></label>`;
  const money = { inputmode: 'inputmode="decimal"' };
  const usd = Number(transaction.usd), mxn = Number(transaction.mxn);
  const mixed = (usd > 0 && mxn < 0) || (mxn > 0 && usd < 0);
  const fields = {
    date: `<label><span>Date</span><input type="date" name="date" value="${esc(transaction.date)}" required></label>`,
    to_account: type === "expense"
      ? `<label><span>Account code</span><select name="to_account">${optionRows(accounts(["expense"]), "account_id", "name", transaction.to_account)}</select></label>`
      : `<label><span>Cash moved to</span><select name="to_account">${optionRows(accounts(), "account_id", "name", transaction.to_account)}</select></label>`,
    from_account: type === "exchange"
      ? `<label><span>Cash account</span><select name="from_account">${optionRows(accounts(["safe", "signout"]), "account_id", "name", transaction.from_account)}</select></label>`
      : `<label><span>Cash moved from</span><select name="from_account">${optionRows(accounts(), "account_id", "name", transaction.from_account)}</select></label>`,
    class_code: `<label><span>Class code</span><select name="class_code" ${requiresClass ? "required" : ""}>${classOptions}</select></label>`,
    usd: input("usd", "USD", transaction.usd, money.inputmode),
    mxn: input("mxn", "MXN", transaction.mxn, money.inputmode),
    // Only receipts paid in one currency with change in the other carry a total.
    receipt_face: mixed || transaction.receipt_face_usd || transaction.receipt_face_mxn
      ? input("receipt_face_usd", "Receipt total (USD)", transaction.receipt_face_usd, money.inputmode) +
        input("receipt_face_mxn", "Receipt total (MXN)", transaction.receipt_face_mxn, money.inputmode) : "",
    assigned_rate: mxn || transaction.assigned_rate
      ? input("assigned_rate", "MXN per USD rate", transaction.assigned_rate, money.inputmode) : "",
    recipient: input("recipient", "Recipient", transaction.recipient),
    vendor: input("vendor", "Vendor", transaction.vendor),
    project: input("project", "Project", transaction.project),
    description: `<label class="full"><span>${esc(cashI18n.t(type === "signout" ? "Purpose" : "Description"))}</span><input name="description" value="${esc(transaction.description)}" ${type === "signout" ? `maxlength="100" required ${transaction.purpose_locked ? "readonly" : ""}` : ""}></label>`,
    notes: `<label class="full"><span>Notes</span><input name="notes" value="${esc(transaction.notes)}"></label>`,
  };
  return (EDIT_FIELDS[type] || ["date", "description", "notes"]).map(name => fields[name]).join("");
}

function renderDrawer(data) {
  const transaction = data.transaction;
  document.getElementById("drawer-title").textContent =
    `Transaction #${transaction.txn_id}`;
  const requiresClass = transaction.posting_state === "active" &&
    transaction.txn_type === "expense" &&
    transaction.to_account !== "UNALLOCATED_EXPENSE";
  const classOptions = `<option value="">${requiresClass ? "Choose class code" : "No class"}</option>` +
    optionRows(data.classes, "class_code", "name", transaction.class_code);
  const documentsById = new Map();
  for (const document of data.documents) {
    // One PDF can serve both roles; list it once, named as the voucher.
    const seen = documentsById.get(document.document_id);
    const roles = [...(seen?.roles || []), document.role];
    documentsById.set(document.document_id,
      { ...(seen && document.role !== "voucher" ? seen : document), roles });
  }
  const documents = documentsById.size ? [...documentsById.values()].map(document =>
    `<a class="document-link" href="${esc(document.stream_url)}" data-document-id="${esc(document.document_id)}">
      <span class="pdf-mark">PDF</span><span><strong>${esc(document.reference)}</strong>
      <small>${esc(document.doc_type === "statement" ? "No-receipt statement"
        : document.doc_type === "purchase_log" ? "Purchase log"
        : document.doc_type === "physical_count" ? "Count sheet"
        : ["voucher", "support"].filter(role => document.roles.includes(role))
          .map(role => documentRoles[role]).join(" and "))} · ${esc(document.page_count || "?")} page(s)</small></span></a>`).join("") :
    `<p class="muted">No documents attached.</p>`;
  drawerBody.onclick = event => {
    const link = event.target.closest("[data-document-id]");
    if (!link) return;
    event.preventDefault();
    const doc = documentsById.get(link.dataset.documentId);
    const dialog = document.getElementById("transaction-document");
    const pages = document.getElementById("transaction-document-pages");
    document.getElementById("transaction-document-title").textContent = doc.reference;
    pages.innerHTML = cashPageViewer.markup();
    dialog.showModal();
    cashPageViewer.mount(pages, doc);
    document.getElementById("close-document").onclick = () => dialog.close();
    dialog.onclose = () => { pages.replaceChildren(); link.focus(); };
  };
  const edits = data.edits.length ? `<ol class="edit-history">${data.edits.map(edit =>
    `<li><time>${esc(edit.created_at.replace("T", " ").slice(0, 19))}</time>
      ${edit.reason ? `<span>${esc(edit.reason)}</span>` : ""}${historyChanges(edit, transaction.txn_type)}</li>`).join("")}</ol>` :
    `<p class="muted" data-i18n="No edits">${esc(cashI18n.t("No edits"))}</p>`;
  const stateLabel = transactionStatus(transaction);
  const stateFact = stateLabel
    ? `<div class="fact"><span>Status</span><strong>${esc(stateLabel)}</strong></div>`
    : "";
  const openingWarning = transaction.txn_type === "opening"
    ? `<p class="notice warning">Changing starting cash recalculates every
       later transaction, so reports already sent will no longer match.</p>`
    : "";
  const signoutWorkspace = transaction.voucher_id &&
    ["signout", "addendum"].includes(transaction.txn_type)
    ? `<a class="btn ghost" href="/${esc(transaction.voucher_id)}">Open expense report</a>` : "";
  const transactionWorkspace = transaction.incomplete
    ? `<a class="btn primary" href="/transactions/${transaction.txn_id}">Finish this transaction</a>` : "";
  const addendumPrint = transaction.voucher_id && transaction.addendum_id
    ? `<a class="btn ghost" href="/voucher/${esc(transaction.voucher_id)}/addendum/${esc(transaction.addendum_id)}/print" target="_blank" rel="noopener">Print addendum</a>` : "";
  // A direct expense's lines are one payment and are voided together.
  const directPayment = transaction.txn_type === "expense" &&
    transaction.from_account === "SAFE" && Boolean(transaction.voucher_id);
  const addReceipt = !data.inbox ? "" : data.inbox.length
    ? `<form id="add-receipt-form" class="add-receipt-form">
        <label><span>Add receipt</span><select name="document_id" required>
          <option value="">Choose from inbox</option>
          ${data.inbox.map(doc => `<option value="${esc(doc.document_id)}">${esc(doc.display_name || doc.document_id)}</option>`).join("")}
        </select></label>
        <output class="drawer-status" role="alert"></output>
        <button class="btn">Add receipt</button></form>`
    : `<p class="muted">To add a receipt, first <a href="/capture">capture it</a>.</p>`;
  const attachReceipt = transaction.incomplete &&
    ["bank_withdrawal", "exchange"].includes(transaction.txn_type)
    ? `<a class="btn primary" href="/review?item=${transaction.txn_type === "exchange" ? "exchange" : "withdrawal"}:${transaction.txn_id}">Attach receipt</a>`
    : transaction.count_id ? `<a class="btn ghost" href="/balances/count/${transaction.count_id}/print"
        target="_blank" rel="noopener">Print count sheet</a>${transaction.incomplete
        ? ` <a class="btn primary" href="/review?item=count:${transaction.count_id}">Attach count sheet</a>` : ""}` : "";
  drawerBody.innerHTML = `<section class="drawer-summary">
      <div class="fact-grid">
        <div class="fact"><span>Type</span><strong>${esc(typeLabel(transaction.txn_type))}</strong></div>
        <div class="fact"><span>Voucher</span><strong>${esc(transaction.voucher_id || "—")}</strong></div>
        ${stateFact}
      </div>
      <div class="workflow-action-buttons">
        ${signoutWorkspace}
        ${transactionWorkspace}
        ${addendumPrint}
      </div>
    </section>
    <form id="history-edit-form" class="drawer-section">
      <h3>Edit</h3>
      ${openingWarning}
      <div class="form-grid two">${editFields(transaction, data, classOptions, requiresClass)}
        <label class="full"><span>Reason for change</span><textarea name="reason" rows="2"></textarea></label>
      </div>
      <details class="ledger-advanced"><summary>Order on this date</summary>
        <label><span>Position among this day's transactions</span><input type="number" name="same_day_seq" min="0" value="${transaction.same_day_seq}" required>
          <small>Change only when this event happened before another one on the same day.</small></label>
      </details>
      <output class="drawer-status" role="alert"></output>
      <button class="btn primary" ${transaction.posting_state !== "active" ? "disabled" : ""}>Save changes</button>
    </form>
    <section class="drawer-section"><h3>Documents</h3>
      <div class="drawer-documents">${documents}</div>${attachReceipt}${addReceipt}</section>
    <section class="drawer-section"><h3 data-i18n="Edit history">${esc(cashI18n.t("Edit history"))}</h3>${edits}</section>
    ${transaction.posting_state === "active" ? `<form id="void-form" class="drawer-section danger-zone">
      <h3>Void</h3>
      <p>${directPayment
        ? `Voids the whole payment and voucher ${esc(transaction.voucher_id)}. They stay in history but no longer count in balances or reports.`
        : "The transaction stays in history but no longer counts in balances or reports."}</p>
      <label><span>Reason</span><textarea name="reason" rows="2" required></textarea></label>
      <output class="drawer-status" role="alert"></output>
      <button class="btn danger">${directPayment ? "Void payment" : "Void transaction"}</button></form>` : ""}`;

  const editForm = document.getElementById("history-edit-form");
  editForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (transaction.txn_type === "opening" && !(await cashConfirm("Change the starting cash?", {
      message: "Every later transaction is recalculated.", confirmLabel: "Change" }))) return;
    const form = new FormData(editForm);
    const status = editForm.querySelector("output");
    const body = Object.fromEntries(form.entries());
    body.expected_version = transaction.version;
    status.textContent = "Saving…";
    try {
      await cashCommand(`/api/ledger/${transaction.txn_id}`, body, { method: "PATCH" });
      status.textContent = "Saved.";
      await load(currentFilterQuery());
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("add-receipt-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const status = event.target.querySelector("output");
    status.textContent = "Saving…";
    try {
      await cashCommand(`/api/ledger/${transaction.txn_id}/receipts`, {
        document_id: new FormData(event.target).get("document_id") });
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("void-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    const status = event.target.querySelector("output");
    if (!(await cashConfirm(directPayment ? `Void payment ${transaction.voucher_id}?`
                                          : `Void transaction #${transaction.txn_id}?`,
      { message: "It stays in history but no longer counts in balances or reports.",
        confirmLabel: "Void", danger: true }))) return;
    try {
      await cashCommand(`/api/ledger/${transaction.txn_id}/void`, {
        expected_version: transaction.version, reason: form.get("reason") });
      await load(currentFilterQuery());
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
}

function closeDrawer() {
  if (drawer.hidden) return;
  const id = currentTransactionId;
  const scrollPosition = drawerScrollPosition;
  drawer.hidden = true; scrim.hidden = true; currentTransactionId = null;
  const url = new URL(window.location.href);
  url.searchParams.delete("txn");
  window.history.replaceState(window.history.state, "", url);
  const row = drawerOpener?.isConnected ? drawerOpener
    : document.querySelector(`#txns tbody tr[data-txn="${id}"]`);
  row?.focus({ preventScroll: true });
  window.scrollTo(0, scrollPosition);
  drawerOpener = null;
}
document.getElementById("close-drawer").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", event => {
  if (drawer.hidden || document.getElementById("transaction-document").open) return;
  if (event.key === "Escape" && !document.querySelector("dialog[open]")) closeDrawer();
  if (event.key !== "Tab") return;
  const items = [...drawer.querySelectorAll(FOCUSABLE)].filter(item => item.offsetParent !== null);
  if (!items.length) { event.preventDefault(); drawer.focus(); return; }
  const first = items[0], last = items[items.length - 1];
  if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer)) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
});

const filterForm = document.getElementById("filters");

function currentFilterQuery() {
  const params = new URLSearchParams(window.location.search);
  params.delete("txn");
  const query = params.toString();
  return query ? `?${query}` : "";
}

function loadCurrentState() {
  return load(currentFilterQuery()).catch(error => {
    tableBody.innerHTML = "";
    console.error(error);
  });
}

function syncViewButtons() {
  for (const button of viewButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.view === activeView));
  }
}

function updateLedgerFromControls() {
  if (!drawer.hidden) closeDrawer();
  const url = new URL(window.location.href);
  url.search = "";
  if (activeView !== "active") url.searchParams.set("view", activeView);
  for (const [key, value] of new FormData(filterForm)) {
    if (value) url.searchParams.set(key, value);
  }
  const search = searchInput.value.trim();
  if (search) url.searchParams.set("q", search);
  window.history.replaceState(window.history.state, "", url);
  loadCurrentState();
}

// View, filter, and search changes start over at the first page.
let filterTimer = null;
function applyFilters() {
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(updateLedgerFromControls, 250);
}

function jumpToEnd() {
  window.clearTimeout(filterTimer);
  if (!drawer.hidden) closeDrawer();
  const url = new URL(window.location.href);
  url.searchParams.set("page", "last");
  url.searchParams.delete("txn");
  window.history.replaceState(window.history.state, "", url);
  loadCurrentState();
}

function fillFilterChoices(filters) {
  const fill = (name, list, key) => {
    const select = filterForm.elements[name];
    if (select.options.length > 1) return;
    select.insertAdjacentHTML("beforeend", list.map(item =>
      `<option value="${esc(item[key])}">${esc(item.name)} · ${esc(item[key])}</option>`).join(""));
  };
  const years = filterForm.elements.fy;
  if (years.options.length <= 1) {
    years.insertAdjacentHTML("beforeend", filters.fiscal_years.map(year =>
      `<option value="${year}">FY${year}</option>`).join(""));
  }
  fill("account", filters.accounts, "account_id");
  fill("class", filters.classes, "class_code");
}

filterForm.addEventListener("input", applyFilters);
filterForm.addEventListener("change", applyFilters);
filterForm.addEventListener("submit", event => event.preventDefault());
searchInput.addEventListener("input", applyFilters);
for (const button of viewButtons) {
  button.addEventListener("click", () => {
    activeView = button.dataset.view;
    syncViewButtons();
    window.clearTimeout(filterTimer);
    updateLedgerFromControls();
  });
}
document.getElementById("jump-to-end").addEventListener("click", jumpToEnd);
document.getElementById("clear-filters").addEventListener("click", () => {
  filterForm.reset();
  searchInput.value = "";
  activeView = "active";
  syncViewButtons();
  applyFilters();
});

const initial = new URLSearchParams(window.location.search);
for (const name of ["fy", "voucher", "account", "type", "class"]) {
  if (initial.get(name)) filterForm.elements[name].value = initial.get(name);
}
if (viewButtons.some(button => button.dataset.view === initial.get("view"))) {
  activeView = initial.get("view");
}
searchInput.value = initial.get("q") || "";
syncViewButtons();
load(currentFilterQuery()).then(() => {
  const requested = Number(initial.get("txn"));
  if (Number.isInteger(requested) && requested > 0) openTransaction(requested);
}).catch(error => { tableBody.innerHTML = ""; console.error(error); });
