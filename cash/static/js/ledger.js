/* Ledger browser with a transaction drawer for documents, edits, and voiding. */
const tableBody = document.querySelector("#txns tbody");
const drawer = document.getElementById("transaction-drawer");
const drawerBody = document.getElementById("drawer-body");
const scrim = document.getElementById("drawer-scrim");
let currentTransactionId = null;
const esc = cashEsc;
const api = cashApi;


const typeLabel = cashLabels.type;
const documentRoles = { voucher: "Voucher", support: "Receipt" };

function transactionStatus(transaction) {
  if (transaction.posting_state !== "active") return cashLabels.status(transaction.posting_state);
  return transaction.incomplete ? "Incomplete" : "";
}

async function load(params = "") {
  const data = await api("/api/ledger" + params);

  document.getElementById("starting-balance-callout").hidden = data.has_opening;
  fillFilterChoices(data.filters);
  // Selects fill after the first load; restore a value that arrived in the URL.
  for (const name of ["account", "class"]) {
    const wanted = new URLSearchParams(window.location.search).get(name);
    if (wanted && !filterForm.elements[name].value) filterForm.elements[name].value = wanted;
  }
  tableBody.innerHTML = "";
  for (const transaction of data.transactions) {
    const row = document.createElement("tr");
    row.className = transaction.posting_state === "active"
      ? "" : transaction.posting_state;
    row.tabIndex = 0;
    const stateLabel = transactionStatus(transaction);
    const stateClass = transaction.incomplete && transaction.posting_state === "active"
      ? " warning" : "";
    const calculatedUsd = transaction.txn_type === "expense" &&
      transaction.calc_usd != null
      ? cashMoney(transaction.calc_usd) : "—";
    row.innerHTML = `<td class="txn-id">#${transaction.txn_id}</td>
      <td>${esc(transaction.date)}</td><td>${esc(typeLabel(transaction.txn_type))}</td>
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
    tableBody.appendChild(row);
  }
  document.getElementById("ledger-empty").hidden = data.transactions.length !== 0;
}

function optionRows(rows, valueKey, labelKey, selected) {
  return rows.map(row => `<option value="${esc(row[valueKey])}"
    ${row[valueKey] === selected ? "selected" : ""}>${esc(row[labelKey])} · ${esc(row[valueKey])}</option>`).join("");
}

async function openTransaction(txnId) {
  currentTransactionId = txnId;
  drawer.hidden = false;
  scrim.hidden = false;
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
    description: `<label class="full"><span>Description</span><input name="description" value="${esc(transaction.description)}"></label>`,
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
    `<a class="document-link" href="${esc(document.stream_url)}" target="_blank" rel="noopener">
      <span class="pdf-mark">PDF</span><span><strong>${esc(document.reference)}</strong>
      <small>${esc(["voucher", "support"].filter(role => document.roles.includes(role))
        .map(role => documentRoles[role]).join(" and "))} · ${esc(document.page_count || "?")} page(s)</small></span></a>`).join("") :
    `<p class="muted">No documents attached.</p>`;
  const edits = data.edits.length ? `<ol class="edit-history">${data.edits.map(edit =>
    `<li><time>${esc(edit.created_at.replace("T", " ").slice(0, 19))}</time>
      <span>${esc(edit.reason || "Presentation edit")}</span></li>`).join("")}</ol>` :
    `<p class="muted">No edits.</p>`;
  const stateLabel = transactionStatus(transaction);
  const stateFact = stateLabel
    ? `<div class="fact"><span>Status</span><strong>${esc(stateLabel)}</strong></div>`
    : "";
  const openingWarning = transaction.txn_type === "opening"
    ? `<p class="notice warning">Changing starting cash recalculates every
       later transaction and makes previously finalized reports outdated.</p>`
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
    ? `<a class="btn primary" href="/review?item=${transaction.txn_type === "exchange" ? "exchange" : "withdrawal"}:${transaction.txn_id}">Attach receipt</a>` : "";
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
        <label class="full"><span>Reason for change</span><textarea name="reason" rows="2" placeholder="Required when changing the date, accounts, amounts, codes, receipt total, or rate"></textarea></label>
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
    <section class="drawer-section"><h3>Edit history</h3>${edits}</section>
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
    if (transaction.txn_type === "opening" && !window.confirm(
      "Change the starting cash and recalculate all later transactions?")) return;
    const form = new FormData(editForm);
    const status = editForm.querySelector("output");
    const body = Object.fromEntries(form.entries());
    Object.assign(body, { submission_id: cashUUID(),
                          expected_version: transaction.version });
    status.textContent = "Saving…";
    try {
      await api(`/api/ledger/${transaction.txn_id}`, { method: "PATCH", body });
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
      await api(`/api/ledger/${transaction.txn_id}/receipts`, {
        method: "POST", body: { submission_id: cashUUID(),
          document_id: new FormData(event.target).get("document_id") },
      });
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("void-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    const status = event.target.querySelector("output");
    if (!window.confirm(directPayment ? `Void payment ${transaction.voucher_id}?`
                                      : `Void transaction #${transaction.txn_id}?`)) return;
    try {
      await api(`/api/ledger/${transaction.txn_id}/void`, {
        method: "POST", body: { submission_id: cashUUID(),
          expected_version: transaction.version, reason: form.get("reason") },
      });
      await load(currentFilterQuery());
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
}

function closeDrawer() {
  drawer.hidden = true; scrim.hidden = true; currentTransactionId = null;
}
document.getElementById("close-drawer").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !drawer.hidden) closeDrawer();
});

const filterForm = document.getElementById("filters");

function currentFilterQuery() {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(filterForm)) if (value) params.set(key, value);
  const query = params.toString();
  return query ? `?${query}` : "";
}

// Filters apply as they change and live in the URL, so a view can be shared.
let filterTimer = null;
function applyFilters() {
  window.clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => {
    const url = new URL(window.location.href);
    url.search = currentFilterQuery();
    window.history.replaceState(null, "", url);
    load(currentFilterQuery()).catch(error => { tableBody.innerHTML = ""; console.error(error); });
  }, 250);
}

function fillFilterChoices(filters) {
  const fill = (name, list, key) => {
    const select = filterForm.elements[name];
    if (select.options.length > 1) return;
    select.insertAdjacentHTML("beforeend", list.map(item =>
      `<option value="${esc(item[key])}">${esc(item.name)} · ${esc(item[key])}</option>`).join(""));
  };
  fill("account", filters.accounts, "account_id");
  fill("class", filters.classes, "class_code");
}

filterForm.addEventListener("input", applyFilters);
filterForm.addEventListener("change", applyFilters);
filterForm.addEventListener("submit", event => event.preventDefault());
document.getElementById("clear-filters").addEventListener("click", () => {
  filterForm.reset();
  applyFilters();
});

const initial = new URLSearchParams(window.location.search);
for (const name of ["fy", "voucher", "account", "type", "class"]) {
  if (initial.get(name)) filterForm.elements[name].value = initial.get(name);
}
load(currentFilterQuery()).then(() => {
  const requested = Number(initial.get("txn"));
  if (Number.isInteger(requested) && requested > 0) openTransaction(requested);
});
