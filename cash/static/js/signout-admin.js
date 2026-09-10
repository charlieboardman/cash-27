const voucherId = window.CASH_SIGNOUT_ID;
let csrfToken = null;
let closeoutPreview = null;
let closeoutSubmissionId = null;
const esc = value => String(value ?? "").replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const money = cents => (Number(cents || 0) / 100).toFixed(2);

async function api(url, options = {}) {
  const method = options.method || "GET";
  const headers = {};
  if (method !== "GET") {
    if (!csrfToken) csrfToken = (await (await fetch("/api/session")).json()).data.csrf;
    headers["X-CSRF"] = csrfToken;
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, {method, headers,
    body: options.body ? JSON.stringify(options.body) : undefined});
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Request failed");
  return payload.data;
}

function typeLabel(row) {
  if (row.txn_type === "signout") return "Originally received";
  if (row.txn_type === "addendum") return row.to_account === voucherId
    ? "Additional cash" : "Cash returned";
  if (row.txn_type === "expense") return "Receipt";
  return row.txn_type.replaceAll("_", " ");
}

async function load() {
  const data = await api(`/api/signouts/${voucherId}`);
  document.getElementById("signout-party").textContent =
    `${data.voucher.recipient} · ${data.voucher.purpose}`;
  const summary = data.summary;
  const closeout = summary.closeout;
  const closeoutForm = document.getElementById("closeout-form");
  closeoutForm.hidden = summary.status === "void" || Boolean(closeout?.valid);
  document.getElementById("closeout-state").textContent = closeout?.valid
    ? `Closeout confirmed ${closeout.confirmed_at}. ${summary.open ? "File the required signed paperwork to finish." : "All cash and paperwork are reconciled."}`
    : closeout ? "Records changed after the previous confirmation. Review the cash activity and confirm a new closeout."
    : "A closeout confirmation is required even when the cash balance is zero.";
  document.getElementById("signout-overview").innerHTML = `
    <article><span>Status</span><strong>${esc(summary.status === "open" ? "Open" : summary.status === "closed" ? "Closed" : "Void")}</strong></article>
    <article><span>Cash remaining</span><strong>USD ${money(summary.usd_cents)} · MXN ${money(summary.mxn_centavos)}</strong></article>
    <article><span>Still needed</span><strong>${summary.reasons.length ? summary.reasons.map(esc).join(" · ") : "Nothing"}</strong></article>`;
  const documents = new Map(data.documents.map(document => [document.document_id, document]));
  document.getElementById("signout-documents").innerHTML = data.documents.length
    ? data.documents.map(document => `<a class="document-link" href="${esc(document.stream_url)}" target="_blank" rel="noopener"><span class="pdf-mark">PDF</span><span><strong>${esc(document.display_name || document.doc_type || "Document")}</strong><small>${esc(document.doc_type || "document")} · ${document.page_count || "?"} page(s)</small></span></a>`).join("")
    : `<p class="empty-state compact">No documents filed yet.</p>`;
  const addenda = new Map(data.addenda.map(item => [item.addendum_id, item]));
  document.getElementById("signout-movements").innerHTML = data.movements.map(row => {
    const addendum = addenda.get(row.addendum_id);
    const paper = addendum ? `<a href="/voucher/${voucherId}/addendum/${addendum.addendum_id}/print">Print ${esc(addendum.addendum_id)}</a>${addendum.status === "pending" ? ` · <a href="/review">file document</a>` : ""}` : row.txn_type === "signout" ? `<a href="/voucher/${voucherId}/print">Print voucher</a>` : "";
    return `<tr class="${row.posting_state === "active" ? "" : "void"}"><td>#${row.txn_id}</td><td>${esc(row.date)}</td><td>${esc(typeLabel(row))}</td><td>${esc(row.from_account)} → ${esc(row.to_account)}</td><td class="num">${money(row.usd_cents)}</td><td class="num">${money(row.mxn_centavos)}</td><td>${paper}</td></tr>`;
  }).join("");
  document.getElementById("addendum-form").hidden = summary.status !== "open";
}

const form = document.getElementById("addendum-form");
const today = new Date();
today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
form.date.value = today.toISOString().slice(0, 10);
form.addEventListener("submit", async event => {
  event.preventDefault();
  const buttons = [...form.querySelectorAll('button[type="submit"]')];
  if (buttons.some(button => button.disabled)) return;
  const printAddendum = event.submitter?.value === "print";
  const values = new FormData(form);
  const status = form.querySelector("output");
  buttons.forEach(button => { button.disabled = true; });
  status.textContent = "Saving…";
  try {
    const result = await api("/api/addenda/prepare", {method: "POST", body: {
      submission_id: cashUUID(), voucher_id: voucherId,
      kind: values.get("kind"), date: values.get("date"),
      reason: values.get("reason") || "", usd: values.get("usd") || "0",
      mxn: values.get("mxn") || "0"
    }});
    location.assign(printAddendum ? result.print_url
      : `/transactions/${result.txn_ids[0]}`);
  } catch (error) { status.textContent = error.message; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
});
const closeoutForm = document.getElementById("closeout-form");
closeoutForm.elements.date.value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
closeoutForm.addEventListener("input", event => {
  if (event.target.name === "confirmed") return;
  closeoutPreview = null;
  closeoutSubmissionId = null;
  closeoutForm.elements.confirmed.checked = false;
  document.getElementById("closeout-preview").hidden = true;
  document.getElementById("closeout-confirmation").hidden = true;
});
closeoutForm.addEventListener("submit", async event => {
  event.preventDefault();
  const message = document.getElementById("closeout-message");
  const buttons = [...closeoutForm.querySelectorAll('button[type="submit"]')];
  if (buttons.some(button => button.disabled)) return;
  const body = {date: closeoutForm.elements.date.value,
    return_usd: closeoutForm.elements.return_usd.value || "0",
    return_mxn: closeoutForm.elements.return_mxn.value || "0"};
  buttons.forEach(button => { button.disabled = true; });
  message.textContent = "";
  try {
    if (event.submitter?.value !== "confirm") {
      closeoutPreview = await api(`/api/signouts/${voucherId}/closeout/preview`, {method: "POST", body});
      closeoutSubmissionId = cashUUID();
      const panel = document.getElementById("closeout-preview");
      panel.innerHTML = `<p>Return to the safe: <strong>USD ${money(closeoutPreview.return_usd_cents)} · MXN ${money(closeoutPreview.return_mxn_centavos)}</strong>.</p>
        ${closeoutPreview.rate_mxn_per_usd ? `<p>Currency settlement: USD ${money(closeoutPreview.exchange_usd_cents)} funded MXN ${money(closeoutPreview.exchange_mxn_centavos)} at <strong>${esc(Number(closeoutPreview.rate_mxn_per_usd).toFixed(6))} MXN/USD</strong>. This is the effective rate for the remaining settlement.</p>` : ""}
        <p>The returned pesos carry USD ${esc(Number(closeoutPreview.returned_mxn_basis_usd).toFixed(2))} of value. Both signout balances will be zero.</p>
        ${closeoutPreview.requires_return_addendum ? "<p>A return addendum will open for printing after confirmation. Sign it and file it in Review to finish closing the signout.</p>" : ""}`;
      panel.hidden = false;
      closeoutForm.elements.confirmed.checked = false;
      document.getElementById("closeout-confirmation").hidden = false;
      return;
    }
    if (!closeoutPreview || !closeoutForm.elements.confirmed.checked) {
      message.textContent = "Review the closeout and confirm that all cash is accounted for.";
      return;
    }
    const result = await api(`/api/signouts/${voucherId}/closeout`, {method: "POST", body: {
      ...body, submission_id: closeoutSubmissionId, preview_token: closeoutPreview.preview_token,
      confirmed: true}});
    if (result.print_url) location.assign(result.print_url);
    else await load();
  } catch (error) { message.textContent = error.message; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
});
load().catch(error => {
  const message = document.getElementById("signout-message");
  message.hidden = false; message.className = "notice danger";
  message.textContent = error.message;
});
