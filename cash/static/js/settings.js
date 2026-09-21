const statusOut = document.getElementById("settings-status");
const databaseStatus = document.getElementById("database-status");
const stagedDatabase = document.getElementById("staged-database-import");
const importSummary = document.getElementById("database-import-summary");
const activateImportForm = document.getElementById("activate-database-import");
let csrfToken;
let stagedImportId = null;

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

async function csrf() {
  if (!csrfToken) csrfToken = (await (await fetch("/api/session")).json()).data.csrf;
  return csrfToken;
}

async function mutate(url, body) {
  const response = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF": await csrf() },
    body: JSON.stringify(body) });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Request failed");
  return payload.data;
}

function show(text, error = false) {
  statusOut.hidden = false;
  statusOut.className = `notice ${error ? "danger" : "success"}`;
  statusOut.textContent = text;
}

function showDatabase(text, tone = "success") {
  databaseStatus.hidden = false;
  databaseStatus.className = `notice ${tone}`;
  databaseStatus.textContent = text;
}

async function payloadError(response) {
  const payload = await response.json().catch(() => null);
  return new Error(payload?.error?.message || `Request failed (${response.status})`);
}

function settingRow(item, kind, codeKey) {
  return `<li class="${item.active ? "" : "inactive"}"><div>
    <strong>${esc(item.name)}</strong><code>${esc(item[codeKey])}</code></div>
    <span class="status-pill ${item.active ? "success" : ""}">${item.active ? "Available" : "Off"}</span>
    <button type="button" class="btn ghost" data-kind="${kind}" data-code="${esc(item[codeKey])}">
      ${item.active ? "Turn off" : "Turn on"}</button></li>`;
}


async function load() {
  const payload = await (await fetch("/api/settings")).json();
  const data = payload.data;
  document.querySelector("#voucher-defaults-form [name=approver_name]").value = data.default_approver_name || "";
  document.getElementById("account-list").innerHTML = data.expense_accounts.map(item =>
    settingRow(item, "expense-account", "account_id")).join("") ||
    `<li class="empty-state compact">No account codes have been added.</li>`;
  document.getElementById("class-list").innerHTML = data.classes.map(item =>
    settingRow(item, "class", "class_code")).join("") ||
    `<li class="empty-state compact">No class codes have been added.</li>`;
}


for (const [formId, url, codeField, label] of [
  ["account-form", "/api/settings/expense-accounts", "account_id", "Account code"],
  ["class-form", "/api/settings/classes", "class_code", "Class code"],
]) {
  document.getElementById(formId).addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      await mutate(url, { submission_id: cashUUID(),
        [codeField]: form.get(codeField), name: form.get("name") });
      event.target.reset(); show(`${label} added.`); await load();
    } catch (error) { show(error.message, true); }
  });
}

document.querySelector(".settings-grid").addEventListener("click", async event => {
  const button = event.target.closest("button[data-kind]");
  if (!button) return;
  try {
    await mutate(`/api/settings/${button.dataset.kind}/${encodeURIComponent(button.dataset.code)}/toggle`,
      { submission_id: cashUUID() });
    await load();
  } catch (error) { show(error.message, true); }
});

document.getElementById("export-database").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  showDatabase("Preparing database export…");
  try {
    const response = await fetch("/api/settings/database/export", {
      method: "POST", headers: { "X-CSRF": await csrf() },
    });
    if (!response.ok) throw await payloadError(response);
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const filename = match ? decodeURIComponent(match[1]) : "cash-27-database.sql";
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = filename; link.click();
    URL.revokeObjectURL(url);
    showDatabase("Database export downloaded.");
  } catch (error) { showDatabase(error.message, "danger"); }
  finally { button.disabled = false; }
});

document.getElementById("validate-database-import").addEventListener(
  "submit", async event => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    stagedDatabase.hidden = true;
    showDatabase("Validating database, ledger, documents, and reports…");
    try {
      const response = await fetch("/api/settings/database/import/validate", {
        method: "POST", headers: { "X-CSRF": await csrf() },
        body: new FormData(event.target),
      });
      if (!response.ok) throw await payloadError(response);
      const result = (await response.json()).data;
      stagedImportId = result.import_id;
      const summary = result.summary;
      const values = [
        ["Transactions", summary.transactions],
        ["Active transactions", summary.active_transactions],
        ["Transaction dates", summary.first_transaction_date
          ? `${summary.first_transaction_date} to ${summary.last_transaction_date}` : "None"],
        ["Vouchers", summary.vouchers], ["Documents", summary.documents],
        ["Final reports", summary.final_reports],
        ["Database version", summary.schema_version],
      ];
      importSummary.innerHTML = values.map(([label, value]) =>
        `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
      activateImportForm.reset();
      activateImportForm.querySelector("button").disabled = true;
      stagedDatabase.hidden = false;
      showDatabase("Export passed every validation check.");
    } catch (error) {
      stagedImportId = null; showDatabase(error.message, "danger");
    } finally { button.disabled = false; }
  });

activateImportForm.elements.confirmation.addEventListener("input", () => {
  activateImportForm.querySelector("button").disabled =
    activateImportForm.elements.confirmation.value !== "REPLACE DATABASE";
});

activateImportForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!stagedImportId || !window.confirm(
    "Replace the current database with this validated import?")) return;
  const button = event.submitter;
  button.disabled = true;
  showDatabase("Activating imported database…");
  try {
    const form = new FormData(event.target);
    const response = await fetch("/api/settings/database/import/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": await csrf() },
      body: JSON.stringify({ import_id: stagedImportId,
        confirmation: form.get("confirmation"),
        admin_password: form.get("admin_password") }),
    });
    if (!response.ok) throw await payloadError(response);
    const result = (await response.json()).data;
    stagedImportId = null; stagedDatabase.hidden = true;
    showDatabase(`Database activated. Previous database saved at ${result.previous_database}.`);
    await load();
  } catch (error) {
    showDatabase(error.message, "danger"); button.disabled = false;
  }
});

load();

document.getElementById("user-password-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const status = document.getElementById("user-password-status");
  button.disabled = true;
  try {
    await mutate("/api/settings/user-password", {
      password: form.elements.password.value,
      confirmation: form.elements.confirmation.value });
    form.reset();
    status.className = "notice success";
    status.textContent = "User password saved. Use it for the next signout login.";
  } catch (error) {
    status.className = "notice danger";
    status.textContent = error.message;
  } finally {
    status.hidden = false;
    button.disabled = false;
  }
});

document.getElementById("voucher-defaults-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const result = await mutate("/api/settings/voucher-defaults", {
      approver_name: form.elements.approver_name.value });
    form.elements.approver_name.value = result.default_approver_name;
    show("Voucher defaults saved.");
  } catch (error) { show(error.message, true); }
  finally { button.disabled = false; }
});
