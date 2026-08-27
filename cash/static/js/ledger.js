/* Ledger browser with balance strip, physical count, and history editor. */
const tableBody = document.querySelector("#txns tbody");
const drawer = document.getElementById("transaction-drawer");
const drawerBody = document.getElementById("drawer-body");
const scrim = document.getElementById("drawer-scrim");
let csrfToken = null;
let currentTransactionId = null;
let ledgerMeta = null;

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function decimal(minor) { return ((Number(minor) || 0) / 100).toFixed(2); }

async function csrf() {
  if (csrfToken) return csrfToken;
  csrfToken = (await (await fetch("/api/session")).json()).data.csrf;
  return csrfToken;
}

async function api(url, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-CSRF"] = await csrf();
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Request failed");
  return payload.data;
}

function accountLabel(id) {
  if (id === "SAFE") return "Physical safe";
  if (id === "BANK") return "Bank";
  return id;
}

function renderBalances(data) {
  const container = document.getElementById("balances");
  const entries = Object.entries(data.balances)
    .filter(([, value]) => value.usd || value.mxn)
    .sort(([left], [right]) => {
      const priority = { SAFE: 0, BANK: 1 };
      return (priority[left] ?? 2) - (priority[right] ?? 2) || left.localeCompare(right);
    });
  container.innerHTML = entries.map(([account, balance]) =>
    `<article class="balance-card ${account === "SAFE" ? "primary" : ""}">
      <span>${esc(accountLabel(account))}</span><strong>${esc(account)}</strong>
      <div><b>USD ${Number(balance.usd).toFixed(2)}</b>
        <b>MXN ${Number(balance.mxn).toFixed(2)}</b></div>
      ${Number(balance.mxn) ? `<small>USD ${Number(balance.basis_usd).toFixed(2)} MXN basis</small>` : ""}
    </article>`).join("");
  if (data.outstanding_unallocated) container.insertAdjacentHTML("beforeend",
    `<article class="balance-card warning"><span>Report blocker</span>
      <strong>${data.outstanding_unallocated} unallocated expense${data.outstanding_unallocated === 1 ? "" : "s"}</strong>
      <small>Resolve in Review before finalizing reports.</small></article>`);
}

function typeLabel(type) {
  return ({ bank_withdrawal: "Withdrawal", adjustment: "Correction" }[type] ||
    type.replaceAll("_", " ")).replace(/^./, value => value.toUpperCase());
}

async function load(params = "") {
  const data = await api("/api/ledger" + params);
  ledgerMeta = data;
  renderBalances(data);
  updateCashEventForm();
  tableBody.innerHTML = "";
  for (const transaction of data.transactions) {
    const row = document.createElement("tr");
    row.className = transaction.posting_state === "void" ? "void" : "";
    row.tabIndex = 0;
    row.innerHTML = `<td class="txn-id">#${transaction.txn_id}</td>
      <td>${esc(transaction.date)}</td><td>${esc(typeLabel(transaction.txn_type))}</td>
      <td><span class="movement-route">${esc(transaction.from_account)} <b>→</b> ${esc(transaction.to_account)}</span>
        ${transaction.description ? `<small>${esc(transaction.description)}</small>` : ""}</td>
      <td class="num">${decimal(transaction.usd_cents)}</td>
      <td class="num">${decimal(transaction.mxn_centavos)}</td>
      <td class="num">${transaction.calc_usd == null ? "—" : Number(transaction.calc_usd).toFixed(2)}</td>
      <td>${esc(transaction.voucher_id || "—")}</td>
      <td><span class="status-pill ${transaction.posting_state === "active" ? "success" : ""}">${esc(transaction.posting_state)}</span></td>`;
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
    renderDrawer(data);
  } catch (error) {
    drawerBody.innerHTML = `<div class="empty-state"><strong>Unable to load</strong><span>${esc(error.message)}</span></div>`;
  }
}

function renderDrawer(data) {
  const transaction = data.transaction;
  document.getElementById("drawer-title").textContent =
    `Transaction #${transaction.txn_id}`;
  const accountOptions = optionRows(data.accounts, "account_id", "name");
  const classOptions = `<option value="">No class</option>` +
    optionRows(data.classes, "class_code", "name", transaction.class_code);
  const documents = data.documents.length ? data.documents.map(document =>
    `<a class="document-link" href="${esc(document.stream_url)}" target="_blank" rel="noopener">
      <span class="pdf-mark">PDF</span><span><strong>${esc(document.role)}</strong>
      <small>${esc(document.page_count || "?")} page(s) · ${esc(document.doc_type || "document")}</small></span></a>`).join("") :
    `<p class="muted">No documents linked.</p>`;
  const edits = data.edits.length ? `<ol class="edit-history">${data.edits.map(edit =>
    `<li><time>${esc(edit.created_at.replace("T", " ").slice(0, 19))}</time>
      <span>${esc(edit.reason || "Presentation edit")}</span></li>`).join("")}</ol>` :
    `<p class="muted">No prior edits.</p>`;
  drawerBody.innerHTML = `<section class="drawer-summary">
      <div class="fact-grid">
        <div class="fact"><span>Movement</span><strong>${esc(transaction.movement_id)}</strong></div>
        <div class="fact"><span>Type</span><strong>${esc(typeLabel(transaction.txn_type))}</strong></div>
        <div class="fact"><span>Voucher</span><strong>${esc(transaction.voucher_id || "—")}</strong></div>
        <div class="fact"><span>State</span><strong>${esc(transaction.posting_state)}</strong></div>
      </div>
    </section>
    <form id="history-edit-form" class="drawer-section">
      <div class="section-heading"><div><p class="eyebrow">Correct history</p>
        <h3>Transaction facts</h3></div><span class="version-pill">Version ${transaction.version}</span></div>
      <div class="form-grid two">
        <label><span>Date</span><input type="date" name="date" value="${esc(transaction.date)}" required></label>
        <label><span>Same-day order</span><input type="number" name="same_day_seq" min="0" value="${transaction.same_day_seq}" required></label>
        <label><span>From account</span><select name="from_account">${optionRows(data.accounts, "account_id", "name", transaction.from_account)}</select></label>
        <label><span>To account</span><select name="to_account">${optionRows(data.accounts, "account_id", "name", transaction.to_account)}</select></label>
        <label><span>USD</span><input name="usd" inputmode="decimal" value="${decimal(transaction.usd_cents)}"></label>
        <label><span>MXN</span><input name="mxn" inputmode="decimal" value="${decimal(transaction.mxn_centavos)}"></label>
        <label><span>Class</span><select name="class_code">${classOptions}</select></label>
        <label><span>Assigned MXN/USD rate</span><input name="assigned_rate" inputmode="decimal" value="${transaction.assigned_rate_milli == null ? "" : (transaction.assigned_rate_milli / 1000).toFixed(3)}"></label>
        <label><span>Receipt face value</span><input name="receipt_face_value" inputmode="decimal" value="${transaction.receipt_face_minor == null ? "" : decimal(transaction.receipt_face_minor)}"></label>
        <label><span>Face denomination</span><select name="receipt_face_currency">
          <option value="">None</option><option ${transaction.receipt_face_currency === "USD" ? "selected" : ""}>USD</option>
          <option ${transaction.receipt_face_currency === "MXN" ? "selected" : ""}>MXN</option></select></label>
        <label><span>Recipient</span><input name="recipient" value="${esc(transaction.recipient)}"></label>
        <label><span>Vendor</span><input name="vendor" value="${esc(transaction.vendor)}"></label>
        <label class="full"><span>Description</span><input name="description" value="${esc(transaction.description)}"></label>
        <label><span>Project</span><input name="project" value="${esc(transaction.project)}"></label>
        <label><span>Notes</span><input name="notes" value="${esc(transaction.notes)}"></label>
        <label class="full"><span>Reason for material change</span><textarea name="reason" rows="2" placeholder="Required when changing date, accounts, amounts, class, face value, or rate"></textarea></label>
      </div>
      <output class="drawer-status" role="alert"></output>
      <button class="btn primary" ${transaction.posting_state !== "active" ? "disabled" : ""}>Save validated edit</button>
    </form>
    <section class="drawer-section"><p class="eyebrow">Evidence</p><h3>Linked documents</h3>
      <div class="drawer-documents">${documents}</div></section>
    <section class="drawer-section"><p class="eyebrow">Change record</p><h3>Previous edits</h3>${edits}</section>
    ${transaction.posting_state === "active" ? `<form id="void-form" class="drawer-section danger-zone">
      <p class="eyebrow">Void</p><h3>Remove this row from active replay</h3>
      <p>This keeps transaction #${transaction.txn_id} and its evidence, but excludes it from balances and reports.</p>
      <label><span>Void reason</span><textarea name="reason" rows="2" required></textarea></label>
      <output class="drawer-status" role="alert"></output>
      <button class="btn danger">Void transaction</button></form>` : ""}`;

  const editForm = document.getElementById("history-edit-form");
  editForm.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(editForm);
    const status = editForm.querySelector("output");
    const body = Object.fromEntries(form.entries());
    Object.assign(body, { submission_id: crypto.randomUUID(),
                          expected_version: transaction.version });
    status.textContent = "Validating full ledger replay…";
    try {
      await api(`/api/ledger/${transaction.txn_id}`, { method: "PATCH", body });
      status.textContent = "History updated.";
      await load(currentFilterQuery());
      await openTransaction(transaction.txn_id);
    } catch (error) { status.textContent = error.message; }
  });
  document.getElementById("void-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    const status = event.target.querySelector("output");
    if (!window.confirm(`Void transaction #${transaction.txn_id}?`)) return;
    try {
      await api(`/api/ledger/${transaction.txn_id}/void`, {
        method: "POST", body: { submission_id: crypto.randomUUID(),
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

function currentFilterQuery() {
  const form = new FormData(document.getElementById("filters"));
  const params = new URLSearchParams();
  for (const [key, value] of form) if (value) params.set(key, value);
  const query = params.toString();
  return query ? `?${query}` : "";
}

document.getElementById("filters").addEventListener("submit", event => {
  event.preventDefault(); load(currentFilterQuery());
});
document.getElementById("clear-filters").addEventListener("click", () => {
  document.getElementById("filters").reset(); load();
});

let lastDelta = null;
document.getElementById("count").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const result = await api("/api/ledger/safe-count", { method: "POST",
      body: { usd: form.get("usd"), mxn: form.get("mxn") } });
    lastDelta = result.delta;
    document.getElementById("delta").innerHTML =
      `<strong>Difference</strong> · USD ${Number(lastDelta.usd).toFixed(2)} · MXN ${Number(lastDelta.mxn).toFixed(2)}`;
    document.getElementById("correction").hidden =
      lastDelta.usd === 0 && lastDelta.mxn === 0;
  } catch (error) { document.getElementById("delta").textContent = error.message; }
});

document.getElementById("correction").addEventListener("submit", async event => {
  event.preventDefault();
  if (!lastDelta) return;
  const reason = new FormData(event.target).get("reason");
  const corrections = [
    { currency: "USD", amount: lastDelta.usd },
    { currency: "MXN", amount: lastDelta.mxn },
  ].filter(item => item.amount !== 0);
  try {
    for (const correction of corrections) {
      const body = { submission_id: crypto.randomUUID(),
        date: new Date().toISOString().slice(0, 10),
        direction: correction.amount < 0 ? "out" : "in",
        usd: correction.currency === "USD" ? Math.abs(correction.amount).toFixed(2) : "0",
        mxn: correction.currency === "MXN" ? Math.abs(correction.amount).toFixed(2) : "0",
        reason, notes: `Physical SAFE count · ${correction.currency}` };
      if (correction.currency === "MXN" && correction.amount > 0) {
        const rate = window.prompt("Approved MXN-per-USD rate for the excess MXN:");
        if (!rate) throw new Error("MXN correction cancelled; no rate supplied.");
        body.assigned_rate_milli = Math.round(Number.parseFloat(rate) * 1000);
      }
      await api("/api/entries/correction", { method: "POST", body });
    }
    event.target.reset();
    event.target.hidden = true;
    document.getElementById("delta").textContent = "Corrections posted separately by currency.";
    lastDelta = null;
    await load(currentFilterQuery());
  } catch (error) { document.getElementById("delta").textContent = error.message; }
});

const cashEventForm = document.getElementById("cash-event");
cashEventForm.date.value = new Date().toISOString().slice(0, 10);
function updateCashEventForm() {
  if (!ledgerMeta) return;
  const opening = cashEventForm.kind.querySelector('option[value="opening"]');
  opening.disabled = ledgerMeta.has_opening;
  if (opening.disabled && cashEventForm.kind.value === "opening") {
    cashEventForm.kind.value = "donation";
  }
  const mxn = Number(cashEventForm.mxn.value || 0);
  const safeMxn = Number(ledgerMeta.balances.SAFE?.mxn || 0);
  const needsRate = mxn !== 0 && (cashEventForm.kind.value === "opening" ||
    (cashEventForm.kind.value === "donation" && safeMxn <= 0));
  document.getElementById("cash-event-rate").hidden = !needsRate;
  cashEventForm.rate.required = needsRate;
}
cashEventForm.kind.addEventListener("change", updateCashEventForm);
cashEventForm.mxn.addEventListener("input", updateCashEventForm);
cashEventForm.addEventListener("submit", async event => {
  event.preventDefault();
  const values = new FormData(cashEventForm);
  const status = document.getElementById("cash-event-status");
  const body = { submission_id: crypto.randomUUID(), date: values.get("date"),
    usd: values.get("usd"), mxn: values.get("mxn"), notes: values.get("notes") };
  const rate = values.get("rate");
  if (rate) body.assigned_rate_milli = Math.round(Number(rate) * 1000);
  try {
    await api(`/api/entries/${values.get("kind")}`, { method: "POST", body });
    status.textContent = `${values.get("kind") === "opening" ? "Opening balance" : "Donation"} posted.`;
    cashEventForm.reset();
    cashEventForm.kind.value = "donation";
    cashEventForm.date.value = new Date().toISOString().slice(0, 10);
    await load(currentFilterQuery());
  } catch (error) { status.textContent = error.message; }
});

load();
