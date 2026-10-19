const statusOut = document.getElementById("settings-status");
const databaseStatus = document.getElementById("database-status");
const exportStatus = document.getElementById("export-status");
const stagedDatabase = document.getElementById("staged-database-import");
const importSummary = document.getElementById("database-import-summary");
const activateImportForm = document.getElementById("activate-database-import");
const recipientForm = document.getElementById("signout-recipient-form");
const recipientSave = document.getElementById("signout-recipient-save");
const recipientCancel = document.getElementById("signout-recipient-cancel");
const recipientStatus = document.getElementById("signout-recipient-status");
let stagedImportId = null;
let recipientCache = [];
let editingRecipient = null;
const esc = cashEsc;
const mutate = (url, body) => cashApi(url, { method: "POST", body });
const show = (text, error = false) => cashNotice(statusOut, text, error ? "danger" : "success");
// Database messages stay until the next step; some include a file path.
const showDatabase = (text, tone = "info") =>
  cashNotice(databaseStatus, text, tone, { fade: false });

function settingRow(item, kind, codeKey) {
  return `<li class="${item.active ? "" : "inactive"}"><div>
    <strong>${esc(item.name)}</strong><code>${esc(item[codeKey])}</code></div>
    <span class="status-pill ${item.active ? "success" : ""}">${item.active ? "On" : "Off"}</span>
    <button type="button" class="btn ghost" data-kind="${kind}" data-code="${esc(item[codeKey])}">
      ${item.active ? "Turn off" : "Turn on"}</button></li>`;
}


function recipientRow(item) {
  const aliases = item.aliases.length
    ? `<small>${esc(item.aliases.join(" · "))}</small>` : "";
  return `<li><div>
    <strong>${esc(item.name)}</strong>
    <code><a href="/signouts/${esc(item.slug)}">/signouts/${esc(item.slug)}</a></code>
    ${aliases}</div>
    <button type="button" class="btn ghost" data-edit-recipient="${esc(item.slug)}">Edit</button>
    <button type="button" class="btn ghost" data-remove-recipient="${esc(item.slug)}">Remove</button></li>`;
}

function showRecipient(text, error = false) {
  cashNotice(recipientStatus, text, error ? "danger" : "success",
    { fade: false });
}

function resetRecipientForm() {
  recipientForm.reset();
  editingRecipient = null;
  recipientSave.textContent = "Add recipient";
  recipientCancel.hidden = true;
}

function showSync(sync) {
  const when = value => new Date(value).toLocaleString();
  const state = document.getElementById("sync-state");
  state.textContent = !sync.configured
    ? "Not set up. Generate a token for the Google Apps Script in gas/."
    : !sync.token
      ? "A token made before tokens were shown is in use. Generate or save a new one to see it."
      : sync.last_completed_at
        ? `Last completed ${when(sync.last_completed_at)}.`
        : `Token set ${when(sync.token_created_at)}. No run has completed yet.`;
  state.className = sync.stale ? "notice warning" : "";
  document.getElementById("sync-token").value = sync.token || "";
  document.getElementById("sync-copy").hidden = !sync.token;
  document.getElementById("sync-revoke").hidden = !sync.configured;
  syncConfigured = sync.configured;
}

async function load() {
  const data = await cashApi("/api/settings");
  showSync(data.sync);
  document.querySelector("#voucher-defaults-form [name=approver_name]").value = data.default_approver_name || "";
  document.getElementById("account-list").innerHTML = data.expense_accounts.map(item =>
    settingRow(item, "expense-account", "account_id")).join("") ||
    `<li class="empty-state compact">No account codes have been added.</li>`;
  document.getElementById("class-list").innerHTML = data.classes.map(item =>
    settingRow(item, "class", "class_code")).join("") ||
    `<li class="empty-state compact">No class codes have been added.</li>`;
  recipientCache = data.signout_recipients || [];
  document.getElementById("signout-recipient-list").innerHTML =
    recipientCache.map(recipientRow).join("") ||
    `<li class="empty-state compact">No signout recipients have been added.</li>`;
  cashNotice(recipientStatus, data.signout_recipients_error || "",
    "danger", { fade: false });
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

document.querySelector(".settings-page").addEventListener("click", async event => {
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
  cashNotice(exportStatus, "Preparing database export…", "success", { fade: false });
  try {
    const response = await cashApi("/api/settings/database/export", { method: "POST", raw: true });
    await cashSaveDownload(response, "cash-27-database.sql");
    cashNotice(exportStatus, "Database export downloaded.");
  } catch (error) { cashNotice(exportStatus, error.message, "danger"); }
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
      const result = await cashApi("/api/settings/database/import/validate", {
        method: "POST", formData: new FormData(event.target) });
      stagedImportId = result.import_id;
      const summary = result.summary;
      const values = [
        ["Transactions", summary.transactions],
        ["Active transactions", summary.active_transactions],
        ["Transaction dates", summary.first_transaction_date
          ? `${summary.first_transaction_date} to ${summary.last_transaction_date}` : "None"],
        ["Vouchers", summary.vouchers], ["Documents", summary.documents],
        ["Database version", summary.schema_version],
      ];
      importSummary.innerHTML = values.map(([label, value]) =>
        `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("");
      activateImportForm.reset();
      activateImportForm.querySelector("button").disabled = true;
      stagedDatabase.hidden = false;
      showDatabase("The export is valid.", "success");
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
  if (!stagedImportId || !(await cashConfirm("Replace the current database?", {
    message: "The validated import becomes the database. The current one is saved to the backups directory.",
    confirmLabel: "Replace database", danger: true }))) return;
  const button = event.submitter;
  button.disabled = true;
  showDatabase("Activating imported database…");
  try {
    const form = new FormData(event.target);
    const result = await mutate("/api/settings/database/import/activate", {
      import_id: stagedImportId,
      confirmation: form.get("confirmation"),
      admin_password: form.get("admin_password") });
    stagedImportId = null; stagedDatabase.hidden = true;
    showDatabase(`Database activated. Previous database saved at ${result.previous_database}.`, "success");
    await load();
  } catch (error) {
    showDatabase(error.message, "danger"); button.disabled = false;
  }
});

recipientForm.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const aliases = form.elements.aliases.value.split("\n")
    .map(alias => alias.trim()).filter(Boolean);
  const editing = editingRecipient;
  try {
    if (editing) {
      await cashApi(`/api/settings/signout-recipients/${encodeURIComponent(editing)}`, {
        method: "PATCH", body: { name: form.elements.name.value, aliases } });
    } else {
      await cashApi("/api/settings/signout-recipients", {
        method: "POST", body: { name: form.elements.name.value, aliases } });
    }
    resetRecipientForm();
    await load();
    showRecipient(editing ? "Signout recipient saved." : "Signout recipient added.");
  } catch (error) { showRecipient(error.message, true); }
});

recipientCancel.addEventListener("click", resetRecipientForm);

document.getElementById("signout-recipient-list").addEventListener("click", async event => {
  const edit = event.target.closest("button[data-edit-recipient]");
  if (edit) {
    const item = recipientCache.find(recipient =>
      recipient.slug === edit.dataset.editRecipient);
    if (!item) return;
    editingRecipient = item.slug;
    recipientForm.elements.name.value = item.name;
    recipientForm.elements.aliases.value = item.aliases.join("\n");
    recipientSave.textContent = "Save recipient";
    recipientCancel.hidden = false;
    recipientForm.elements.name.focus();
    return;
  }
  const remove = event.target.closest("button[data-remove-recipient]");
  if (!remove) return;
  if (!(await cashConfirm("Remove signout recipient?", {
    confirmLabel: "Remove", danger: true }))) return;
  try {
    await cashApi(
      `/api/settings/signout-recipients/${encodeURIComponent(remove.dataset.removeRecipient)}`,
      { method: "DELETE" });
    if (editingRecipient === remove.dataset.removeRecipient) resetRecipientForm();
    await load();
    showRecipient("Signout recipient removed.");
  } catch (error) { showRecipient(error.message, true); }
});

load();

const syncStatus = document.getElementById("sync-status");
let syncConfigured = false;
const replaceSync = async () => !syncConfigured || cashConfirm("Replace the token?", {
  message: "The Drive backup script stops working until it has the new one.",
  confirmLabel: "Replace token", danger: true });
async function saveSync(token) {
  try {
    const result = await mutate("/api/settings/sync-token", token === undefined ? {} : { token });
    showSync(result.sync);
    cashNotice(syncStatus, "Token saved.");
  } catch (error) { cashNotice(syncStatus, error.message, "danger"); }
}
document.getElementById("sync-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (await replaceSync()) saveSync(document.getElementById("sync-token").value);
});
document.getElementById("sync-generate").addEventListener("click", async () => {
  if (await replaceSync()) saveSync();
});
document.getElementById("sync-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.getElementById("sync-token").value);
    cashNotice(syncStatus, "Token copied.");
  } catch {
    document.getElementById("sync-token").select();
    cashNotice(syncStatus, "Copy the selected token.", "info");
  }
});
document.getElementById("sync-revoke").addEventListener("click", async () => {
  if (!(await cashConfirm("Revoke the token?", {
    message: "The Drive backup stops until a new token is set.",
    confirmLabel: "Revoke token", danger: true }))) return;
  try {
    const result = await cashApi("/api/settings/sync-token", { method: "DELETE" });
    showSync(result.sync);
    cashNotice(syncStatus, "Token revoked.");
  } catch (error) { cashNotice(syncStatus, error.message, "danger"); }
});

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
    cashNotice(status, "Employee password saved.");
  } catch (error) {
    cashNotice(status, error.message, "danger");
  } finally {
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
