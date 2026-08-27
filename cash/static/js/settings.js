const statusOut = document.getElementById("settings-status");
let csrfToken;

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

function settingRow(item, kind, codeKey) {
  return `<li class="${item.active ? "" : "inactive"}"><div>
    <strong>${esc(item.name)}</strong><code>${esc(item[codeKey])}</code></div>
    <span class="status-pill ${item.active ? "success" : ""}">${item.active ? "Active" : "Inactive"}</span>
    <button type="button" class="btn ghost" data-kind="${kind}" data-code="${esc(item[codeKey])}">
      ${item.active ? "Deactivate" : "Activate"}</button></li>`;
}

async function load() {
  const payload = await (await fetch("/api/settings")).json();
  const data = payload.data;
  document.getElementById("account-list").innerHTML = data.expense_accounts.map(item =>
    settingRow(item, "expense-account", "account_id")).join("") ||
    `<li class="empty-state compact">No expense accounts configured.</li>`;
  document.getElementById("class-list").innerHTML = data.classes.map(item =>
    settingRow(item, "class", "class_code")).join("") ||
    `<li class="empty-state compact">No classes configured.</li>`;
}

for (const [formId, url, codeField, label] of [
  ["account-form", "/api/settings/expense-accounts", "account_id", "Expense account"],
  ["class-form", "/api/settings/classes", "class_code", "Class"],
]) {
  document.getElementById(formId).addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      await mutate(url, { submission_id: crypto.randomUUID(),
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
      { submission_id: crypto.randomUUID() });
    await load();
  } catch (error) { show(error.message, true); }
});

load();
