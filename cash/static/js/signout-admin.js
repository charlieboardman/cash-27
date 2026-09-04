const voucherId = window.CASH_SIGNOUT_ID;
let csrfToken = null;
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
  const values = new FormData(form);
  const status = form.querySelector("output");
  status.textContent = "Saving…";
  try {
    const result = await api("/api/addenda/prepare", {method: "POST", body: {
      submission_id: cashUUID(), voucher_id: voucherId,
      kind: values.get("kind"), date: values.get("date"),
      reason: values.get("reason") || "", usd: values.get("usd") || "0",
      mxn: values.get("mxn") || "0"
    }});
    location.assign(result.print_url);
  } catch (error) { status.textContent = error.message; }
});
load().catch(error => {
  const message = document.getElementById("signout-message");
  message.hidden = false; message.className = "notice danger";
  message.textContent = error.message;
});
