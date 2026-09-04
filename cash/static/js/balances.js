/* Current balances: safe state, count vs books, signout custody list. */
const els = {
  message: document.getElementById("balances-message"),
  safeFigures: document.getElementById("safe-figures"),
  safeTotals: document.getElementById("safe-totals"),
  signoutList: document.getElementById("signout-list"),
  signoutsSummary: document.getElementById("signouts-summary"),
  count: document.getElementById("count"),
  delta: document.getElementById("delta"),
  correction: document.getElementById("correction"),
};

let csrfToken = null;
let lastComparison = null;

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  return Number(value || 0).toFixed(2);
}

async function csrf() {
  if (csrfToken) return csrfToken;
  const response = await fetch("/api/session");
  csrfToken = (await response.json()).data.csrf;
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
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error?.message ||
      `Request failed (${response.status})`);
  }
  return payload.data;
}

function showMessage(text, tone = "success") {
  els.message.hidden = false;
  els.message.className = `notice ${tone}`;
  els.message.textContent = text;
  window.clearTimeout(showMessage.timer);
  showMessage.timer = window.setTimeout(() => {
    els.message.hidden = true;
  }, 7000);
}

function figures(balance, { compact = false } = {}) {
  return `<div class="balance-figures-row">
    <span><small>USD</small><strong>${money(balance.usd)}</strong></span>
    <span><small>MXN</small><strong>${money(balance.mxn)}</strong></span>
    ${Number(balance.mxn) ? `<span class="basis"><small>Recorded USD value of MXN</small>
      <strong>${money(balance.basis_usd)}</strong></span>` : ""}
  </div>`;
}
function render(data) {
  els.safeFigures.innerHTML = figures(data.safe);
  const open = data.signouts.filter(s => s.open);
  const closed = data.signouts.filter(s => !s.open);
  const row = s => `<li class="${s.open ? "open" : "closed"}">
    <a class="signout-link" href="/signouts/${esc(s.voucher_id)}">
      <span class="signout-id">${esc(s.voucher_id)}</span>
      <span class="signout-copy"><strong>${esc(s.recipient || "Recipient")}</strong>
        <small>${esc(s.purpose || "")}</small></span>
      <span class="signout-cash">
        <b>USD ${money(s.balance.usd)}</b>
        ${Number(s.balance.mxn) ? `<b>MXN ${money(s.balance.mxn)}</b>` : ""}
      </span>
      ${s.open
        ? `<span class="status-pill warning">Open</span>`
        : `<span class="status-pill success">Closed</span>`}
    </a>
    ${s.open && s.reasons.length
      ? `<p class="signout-reasons">${s.reasons.map(esc).join(" · ")}</p>` : ""}
  </li>`;
  els.signoutList.innerHTML =
    (open.length || closed.length)
      ? open.map(row).join("") + closed.map(row).join("")
      : `<li class="empty-state compact"><strong>No signouts yet</strong>
          <span>Cash given out on a signout voucher appears here.</span></li>`;
  els.signoutsSummary.textContent =
    `${data.totals.open_count} open · ${data.signouts.length} total`;

  const totals = data.totals;
  els.safeTotals.textContent =
    `Tracked cash everywhere: USD ${money(totals.tracked_usd)} · ` +
    `MXN ${money(totals.tracked_mxn)} ` +
    `(${totals.open_count} open signout${totals.open_count === 1 ? "" : "s"} ` +
    `holding USD ${money(totals.signout_usd)})`;
}

async function load() {
  render(await api("/api/balances"));
}

function localDate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
}

async function uploadEvidence(file) {
  const formData = new FormData();
  formData.append("files", file, file.name);
  const response = await fetch("/api/documents/capture-merge", {
    method: "POST", headers: { "X-CSRF": await csrf() }, body: formData,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error?.message ||
      `Could not upload the count document (${response.status})`);
  }
  return payload.data.document_id;
}

function clearComparison() {
  lastComparison = null;
  els.delta.textContent = "Compare the edited count with the records again.";
  els.correction.hidden = true;
}


els.count.addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const result = await api("/api/balances/safe-count", { method: "POST",
      body: { usd: form.get("usd"), mxn: form.get("mxn") } });
    lastComparison = result;
    const parts = [];
    if (result.delta.usd) parts.push(`USD ${money(result.delta.usd)}`);
    if (result.delta.mxn) parts.push(`MXN ${money(result.delta.mxn)}`);
    els.delta.innerHTML = parts.length
      ? `<strong>Difference</strong> · ${parts.join(" · ")}`
      : "<strong>The count matches the records.</strong>";
    els.correction.hidden = !parts.length;
  } catch (error) { els.delta.textContent = error.message; }
});

els.count.addEventListener("input", () => {
  if (lastComparison) clearComparison();
});

els.correction.addEventListener("submit", async event => {
  event.preventDefault();
  if (!lastComparison) return;
  const fields = new FormData(event.target);
  const reason = fields.get("reason");
  const mxnIncrease = lastComparison.delta.mxn > 0;
  let rate = null;
  if (mxnIncrease) {
    const entered = window.prompt(
      "Approved MXN-per-USD rate for the excess MXN:");
    rate = Number.parseFloat(entered || "");
    if (!Number.isFinite(rate) || rate <= 0) {
      els.delta.textContent = "Enter a valid positive rate to save the MXN correction.";
      return;
    }
  }
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    els.delta.textContent = "Saving correction…";
    const evidence = fields.get("evidence");
    const documentId = evidence?.size ? await uploadEvidence(evidence) : null;
    const body = {
      submission_id: cashUUID(), date: localDate(), reason,
      expected_ledger_revision: lastComparison.ledger_revision,
      usd: money(lastComparison.count.usd),
      mxn: money(lastComparison.count.mxn),
      document_id: documentId,
    };
    if (rate != null) {
      body.assigned_rate_milli = Math.round(rate * 1000);
    }
    await api("/api/balances/safe-count/correct", { method: "POST", body });
    event.target.reset();
    event.target.hidden = true;
    lastComparison = null;
    els.delta.textContent = "The count correction was saved.";
    showMessage("Count correction saved to the ledger.");
    await load();
  } catch (error) {
    els.delta.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
});

load();
