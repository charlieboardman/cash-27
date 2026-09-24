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

let lastComparison = null;
const esc = cashEsc;
const money = cashMoney;
const api = cashApi;
const showMessage = (text, tone) => cashNotice(els.message, text, tone);

function figures(balance, { compact = false } = {}) {
  return `<div class="balance-figures-row">
    <span><small>USD</small><strong>${money(balance.usd)}</strong></span>
    <span><small>MXN</small><strong>${money(balance.mxn)}</strong></span>
    ${Number(balance.mxn) ? `<span class="basis"><small>USD value</small>
      <strong>${money(balance.basis_usd)}</strong></span>` : ""}
  </div>`;
}
function render(data) {
  els.safeFigures.innerHTML = figures(data.safe);
  const open = data.signouts.filter(s => s.open);
  const closed = data.signouts.filter(s => !s.open);
  const row = s => `<li class="${s.open ? "open" : "closed"}">
    <a class="signout-link" href="/${esc(s.voucher_id)}">
      <span class="signout-id">${esc(s.voucher_id)}</span>
      <span class="signout-copy"><strong>${esc(s.recipient || "—")}</strong>
        <small>${esc(s.purpose || "")}</small></span>
      <span class="signout-cash">
        <b>USD ${money(s.balance.usd)}</b>
        ${Number(s.balance.mxn) ? `<b>MXN ${money(s.balance.mxn)}</b>` : ""}
        ${Number(s.balance.mxn) ? `<span class="signout-basis">USD value
          <b>${money(s.balance.basis_usd)}</b></span>` : ""}
      </span>
      ${s.open
        ? `<span class="status-pill warning">Open</span>`
        : `<span class="status-pill success">Closed</span>`}
    </a>
    ${s.open && s.reasons.length
      ? `<p class="signout-reasons">${s.reasons.map(reason => esc(cashLabels.reason(reason))).join(" · ")}</p>` : ""}
  </li>`;
  els.signoutList.innerHTML =
    (open.length || closed.length)
      ? open.map(row).join("") + closed.map(row).join("")
      : `<li class="empty-state compact"><strong>No signouts yet</strong></li>`;
  els.signoutsSummary.textContent =
    `${data.totals.open_count} open · ${data.signouts.length} total`;

  const totals = data.totals;
  els.safeTotals.textContent =
    `Safe plus signouts: USD ${money(totals.tracked_usd)} · ` +
    `MXN ${money(totals.tracked_mxn)}. ` +
    `${totals.open_count} open signout${totals.open_count === 1 ? "" : "s"} ` +
    `worth USD ${money(totals.open_signout_value_usd)}.`;
}

async function load() {
  render(await api("/api/balances"));
}

async function uploadEvidence(file) {
  const formData = new FormData();
  formData.append("files", file, file.name);
  const created = await api("/api/documents/capture-merge", { method: "POST", formData });
  return created.document_id;
}

function clearComparison() {
  lastComparison = null;
  els.delta.textContent = "Compare again after changing the count.";
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
    if (Number(result.delta.usd)) parts.push(`USD ${money(result.delta.usd)}`);
    if (Number(result.delta.mxn)) parts.push(`MXN ${money(result.delta.mxn)}`);
    els.delta.innerHTML = parts.length
      ? `<strong>Difference</strong> · ${parts.join(" · ")}`
      : "<strong>The count matches.</strong>";
    els.correction.hidden = !parts.length;
    const rateNeeded = Number(result.delta.mxn) > 0;
    document.getElementById("correction-rate").hidden = !rateNeeded;
    els.correction.elements.rate.required = rateNeeded;
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
  let rate = null;
  if (Number(lastComparison.delta.mxn) > 0) {
    rate = Number.parseFloat(fields.get("rate") || "");
    if (!Number.isFinite(rate) || rate <= 0) {
      els.delta.textContent = "Enter a positive MXN per USD rate.";
      return;
    }
  }
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    els.delta.textContent = "Saving…";
    const evidence = fields.get("evidence");
    const documentId = evidence?.size ? await uploadEvidence(evidence) : null;
    const body = {
      submission_id: cashUUID(), date: cashToday(), reason,
      expected_ledger_revision: lastComparison.ledger_revision,
      usd: lastComparison.count.usd,
      mxn: lastComparison.count.mxn,
      document_id: documentId,
    };
    if (rate != null) {
      body.assigned_rate_milli = Math.round(rate * 1000);
    }
    await api("/api/balances/safe-count/correct", { method: "POST", body });
    event.target.reset();
    event.target.hidden = true;
    lastComparison = null;
    els.delta.textContent = "";
    showMessage("Adjustment saved to the ledger.");
    await load();
  } catch (error) {
    els.delta.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
});

load();
