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
let lastDelta = null;

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


els.count.addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const result = await api("/api/balances/safe-count", { method: "POST",
      body: { usd: form.get("usd"), mxn: form.get("mxn") } });
    lastDelta = result.delta;
    const parts = [];
    if (lastDelta.usd) parts.push(`USD ${money(lastDelta.usd)}`);
    if (lastDelta.mxn) parts.push(`MXN ${money(lastDelta.mxn)}`);
    els.delta.innerHTML = parts.length
      ? `<strong>Difference</strong> · ${parts.join(" · ")}`
      : "<strong>The count matches the records.</strong>";
    els.correction.hidden = !parts.length;
  } catch (error) { els.delta.textContent = error.message; }
});

els.correction.addEventListener("submit", async event => {
  event.preventDefault();
  if (!lastDelta) return;
  const reason = new FormData(event.target).get("reason");
  const corrections = [
    { currency: "USD", amount: lastDelta.usd },
    { currency: "MXN", amount: lastDelta.mxn },
  ].filter(item => item.amount !== 0);
  try {
    for (const correction of corrections) {
      const body = { submission_id: cashUUID(),
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
    lastDelta = null;
    els.delta.textContent = "A separate correction was saved for each currency.";
    showMessage("Corrections saved to the ledger.");
    await load();
  } catch (error) { els.delta.textContent = error.message; }
});

load();