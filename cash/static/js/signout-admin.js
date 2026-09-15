const voucherId = window.CASH_SIGNOUT_ID;
let csrfToken = null;
let closeoutPreview = null;
let closeoutSubmissionId = null;
const esc = value => String(value ?? "").replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const money = cashMinorMoney;
const receiptState = {
  detail: null, options: {expense_accounts: [], classes: [], projects: []},
  claimIndex: 0, returnNotes: {}, dirty: false,
};
const receiptRoot = document.getElementById("signout-receipt-review");
let selectedPackageId = new URLSearchParams(location.search).get("submission");
let availablePackages = [];
const workflowStatus = value => ({
  submitted: "Awaiting review", in_review: "Being reviewed", needs_changes: "Needs corrections",
  verified: "Checked", flagged: "Needs correction", accepted: "Approved",
  withdrawn: "Withdrawn", draft: "Draft",
})[value] || String(value || "").replaceAll("_", " ");
const markDirty = () => { receiptState.dirty = true; };
function receiptMessage(text) {
  const message = document.getElementById("signout-message");
  message.hidden = false;
  message.className = "notice";
  message.textContent = text;
}
const receiptAllocations = cashAllocationEditor({
  getOptions: () => receiptState.options, markDirty, showMessage: receiptMessage,
});

function renderSubmissions(packages) {
  availablePackages = packages;
  document.getElementById("signout-receipts-summary").textContent =
    `${packages.length} submission${packages.length === 1 ? "" : "s"}`;
  document.getElementById("signout-submissions").innerHTML = packages.length
    ? packages.map(item => `<button type="button" class="signout-submission${item.package_id === selectedPackageId ? " selected" : ""}"
        data-package-id="${esc(item.package_id)}" aria-pressed="${item.package_id === selectedPackageId}">
        <strong>Submission ${item.seq}</strong>
        <span>${item.receipt_count} receipt${item.receipt_count === 1 ? "" : "s"}</span>
        <span class="status-pill ${item.status === "accepted" ? "success" : ""}">${esc(workflowStatus(item.status))}</span>
      </button>`).join("")
    : `<p class="empty-state compact">No receipts submitted yet. Receipts sent from the recipient’s page will appear here.</p>`;
  document.querySelectorAll(".signout-submission").forEach(button => {
    button.addEventListener("click", () => openSubmission(button.dataset.packageId).catch(error => receiptMessage(error.message)));
  });
}

function renderReceiptReview() {
  cashRenderPackage({
    state: receiptState, els: {detail: receiptRoot}, esc, workflowStatus,
    ...receiptAllocations, request: api, markDirty,
    markDirtyEvent: form => form.addEventListener("input", markDirty),
    showMessage: receiptMessage,
    refreshSelected: refreshReceiptReview,
    async afterCommit(text) {
      receiptState.dirty = false;
      receiptMessage(text);
      await refreshReceiptReview();
    },
    onDocument(receiptDocument) {
      const preview = receiptDocument ? `<a class="document-link" href="${esc(receiptDocument.stream_url)}" target="_blank" rel="noopener">Open ${esc(receiptDocument.display_name || "receipt")} full size</a>
        <iframe src="${esc(receiptDocument.stream_url)}#page=1&view=FitH" title="Receipt preview"></iframe>`
        : `<p class="empty-state compact">No receipt document available.</p>`;
      const container = document.getElementById("signout-receipt-preview");
      if (container.dataset.documentId === (receiptDocument?.document_id || "none")) return;
      container.dataset.documentId = receiptDocument?.document_id || "none";
      container.innerHTML = preview;
    },
  });
}

async function refreshReceiptReview() {
  const detail = await api(`/api/review/item/package/${selectedPackageId}`);
  if (detail.package.voucher_id !== voucherId) throw new Error("This submission belongs to another signout.");
  receiptState.detail = detail;
  renderReceiptReview();
  await load();
  document.dispatchEvent(new CustomEvent("signout-review-changed"));
}

async function openSubmission(packageId) {
  if (receiptState.dirty && !window.confirm("Discard unsaved review changes and open another submission?")) return;
  const detail = await api(`/api/review/item/package/${packageId}`);
  if (detail.package.voucher_id !== voucherId) throw new Error("This submission belongs to another signout.");
  selectedPackageId = packageId;
  receiptState.detail = detail;
  receiptState.claimIndex = 0;
  receiptState.returnNotes = {};
  receiptState.dirty = false;
  const url = new URL(location.href);
  url.searchParams.set("submission", packageId);
  history.replaceState(null, "", url);
  document.getElementById("signout-receipt-workspace").hidden = false;
  renderSubmissions(availablePackages);
  renderReceiptReview();
}

async function initializeSignout() {
  const [data, options] = await Promise.all([load(), api("/api/review/options")]);
  receiptState.options = options;
  document.getElementById("project-options").innerHTML = (options.projects || [])
    .map(project => `<option value="${esc(project)}"></option>`).join("");
  const initial = data.packages.find(item => item.package_id === selectedPackageId)
    || data.packages.find(item => ["submitted", "in_review"].includes(item.status));
  if (initial) await openSubmission(initial.package_id);
}

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
  const report = data.report;
  const settlementForm = document.getElementById("closeout-form");
  document.getElementById("reported-settlement").innerHTML = report ?
    `<p><strong>Recipient’s report: ${esc(report.status)}</strong></p><p>Cash left: ${report.cash_usd == null || report.cash_mxn == null ? "Not yet entered" : money(report.cash_usd)+" USD · "+money(report.cash_mxn)+" MXN"}</p>
    <p>Reimbursement requested: ${money(report.personal_usd)} USD · ${money(report.personal_mxn)} MXN</p>
    ${report.discrepancy ? `<p class="notice warning"><strong>Reported discrepancy:</strong> ${esc(report.discrepancy)}</p>` : ""}` : "";
  if (report && settlementForm.dataset.reportVersion !== String(report.version) && !summary.closeout) {
    settlementForm.elements.return_usd.value = report.cash_usd == null ? "0" : (report.cash_usd/100).toFixed(2);
    settlementForm.elements.return_mxn.value = report.cash_mxn == null ? "0" : (report.cash_mxn/100).toFixed(2);
    settlementForm.elements.reimburse_usd.value = (report.personal_usd/100).toFixed(2);
    settlementForm.elements.reimburse_mxn.value = (report.personal_mxn/100).toFixed(2);
    settlementForm.dataset.reportVersion = String(report.version);
    closeoutPreview = null;
    document.getElementById("closeout-preview").hidden = true;
    document.getElementById("closeout-confirmation").hidden = true;
  }
  if (report?.status === "submitted") document.getElementById("closeout-card").open = true;
  const remaining = [summary.usd_cents ? `${money(summary.usd_cents)} USD` : "",
    summary.mxn_centavos ? `${money(summary.mxn_centavos)} MXN` : ""]
    .filter(Boolean).join(" · ") || "0.00 USD · 0.00 MXN";
  const requirements = summary.reasons.filter(reason =>
    reason !== "cash remaining" && reason !== "receipt submission in progress");
  const awaitingReview = data.packages.filter(item => ["submitted", "in_review"].includes(item.status));
  const receiptCount = awaitingReview.reduce((count, item) => count + item.receipt_count, 0);
  if (receiptCount) requirements.push(`${receiptCount} receipt${receiptCount === 1 ? "" : "s"} awaiting review`);
  const corrections = data.packages.filter(item => item.status === "needs_changes").length;
  if (corrections) requirements.push(`${corrections} submission${corrections === 1 ? "" : "s"} awaiting recipient corrections`);
  const closeout = summary.closeout;
  const closeoutForm = document.getElementById("closeout-form");
  closeoutForm.hidden = summary.status === "void" || Boolean(closeout?.valid);
  document.getElementById("closeout-state").textContent = closeout?.valid
    ? `Closeout confirmed ${closeout.confirmed_at}. ${summary.open ? "File the required signed paperwork to finish." : "All cash and paperwork are reconciled."}`
    : closeout ? "Records changed after the previous confirmation. Review the cash activity and confirm a new closeout."
    : "Review the report and confirm its settlement here.";
  document.getElementById("signout-overview").innerHTML = `
    <article><span>Status</span><strong>${esc(summary.status === "open" ? "Open" : summary.status === "closed" ? "Closed" : "Void")}</strong></article>
    <article><span>Cash remaining</span><strong>${remaining}</strong></article>
    <article><span>Still needed</span><strong>${requirements.length ? requirements.map(esc).join(" · ") : "Nothing"}</strong></article>`;
  const documents = new Map(data.documents.map(document => [document.document_id, document]));
  document.getElementById("signout-documents").innerHTML = data.documents.length
    ? data.documents.map(document => `<a class="document-link" href="${esc(document.review_url || document.stream_url)}" ${document.review_url ? "" : 'target="_blank" rel="noopener"'}><span class="pdf-mark">PDF</span><span><strong>${esc(document.display_name || document.doc_type || "Document")}</strong><small>${esc(document.doc_type || "document")} · ${document.page_count || "?"} page(s)</small></span></a>`).join("")
    : `<p class="empty-state compact">No documents filed yet.</p>`;
  const addenda = new Map(data.addenda.map(item => [item.addendum_id, item]));
  document.getElementById("signout-movements").innerHTML = data.movements.map(row => {
    const addendum = addenda.get(row.addendum_id);
    const paper = addendum ? `<a href="/voucher/${voucherId}/addendum/${addendum.addendum_id}/print" target="_blank" rel="noopener">Print ${esc(addendum.addendum_id)}</a>${addendum.status === "pending" ? ` · <a href="/review?item=addendum:${esc(addendum.addendum_id)}">file document</a>` : ""}` : row.txn_type === "signout" ? `<a href="/voucher/${voucherId}/print" target="_blank" rel="noopener">Print voucher</a>` : "";
    return `<tr class="${row.posting_state === "active" ? "" : "void"}"><td>#${row.txn_id}</td><td>${esc(row.date)}</td><td>${esc(typeLabel(row))}</td><td>${esc(row.from_account)} → ${esc(row.to_account)}</td><td class="num">${money(row.usd_cents)}</td><td class="num">${money(row.mxn_centavos)}</td><td>${paper}</td></tr>`;
  }).join("");
  document.getElementById("addendum-card").hidden = summary.status !== "open";
  renderSubmissions(data.packages);
  return data;
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
  const printTab = printAddendum ? window.open("about:blank", "_blank") : null;
  if (printTab) printTab.opener = null;
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
    if (printTab) printTab.location.replace(result.print_url);
    await load();
    form.elements.usd.value = "";
    form.elements.mxn.value = "";
    status.textContent = `Saved ${result.addendum_id}. Print, sign, and file the addendum.`;
    document.dispatchEvent(new CustomEvent("signout-review-changed"));
  } catch (error) {
    if (printTab) printTab.close();
    status.textContent = error.message;
  }
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
  const printTab = event.submitter?.value === "confirm"
    && (closeoutPreview?.requires_return_addendum || closeoutPreview?.requires_reimbursement_addendum)
    && closeoutForm.elements.confirmed.checked
    ? window.open("about:blank", "_blank") : null;
  if (printTab) printTab.opener = null;
  const body = {date: closeoutForm.elements.date.value,
    return_usd: closeoutForm.elements.return_usd.value || "0",
    return_mxn: closeoutForm.elements.return_mxn.value || "0",
    reimburse_usd: closeoutForm.elements.reimburse_usd.value || "0",
    reimburse_mxn: closeoutForm.elements.reimburse_mxn.value || "0",
    resolution_note: closeoutForm.elements.resolution_note.value};
  buttons.forEach(button => { button.disabled = true; });
  message.textContent = "";
  try {
    if (event.submitter?.value !== "confirm") {
      closeoutPreview = await api(`/api/signouts/${voucherId}/closeout/preview`, {method: "POST", body});
      closeoutSubmissionId = cashUUID();
      const panel = document.getElementById("closeout-preview");
      panel.innerHTML = `<p>Return to the safe: <strong>USD ${money(closeoutPreview.return_usd_cents)} · MXN ${money(closeoutPreview.return_mxn_centavos)}</strong>.</p>
        ${closeoutPreview.requires_reimbursement_addendum ? `<p>Reimburse the recipient: <strong>USD ${money(closeoutPreview.reimburse_usd_cents)} · MXN ${money(closeoutPreview.reimburse_mxn_centavos)}</strong>.</p>` : ""}
        ${closeoutPreview.rate_mxn_per_usd ? `<p>Currency settlement: USD ${money(closeoutPreview.exchange_usd_cents)} funded MXN ${money(closeoutPreview.exchange_mxn_centavos)} at <strong>${Number(closeoutPreview.rate_mxn_per_usd).toLocaleString("en-US", {minimumFractionDigits: 6, maximumFractionDigits: 6})} MXN/USD</strong>. This is the effective rate for the remaining settlement.</p>` : ""}
        <p>The returned pesos carry USD ${cashMoney(closeoutPreview.returned_mxn_basis_usd)} of value. Both signout balances will be zero.</p>
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
    if (printTab && result.print_urls?.length > 1) {
      printTab.document.title = "Settlement addenda";
      printTab.document.body.innerHTML = `<h1>Print and sign both addenda</h1>${result.print_urls.map((url,i) => `<p><a href="${esc(url)}" target="_blank" rel="noopener">Print addendum ${i+1}</a></p>`).join("")}`;
    }
    else if (printTab && result.print_url) printTab.location.replace(result.print_url);
    else if (printTab) printTab.close();
    await load();
    document.dispatchEvent(new CustomEvent("signout-review-changed"));
  } catch (error) {
    if (printTab) printTab.close();
    message.textContent = error.message;
  }
  finally { buttons.forEach(button => { button.disabled = false; }); }
});
initializeSignout().catch(error => {
  const message = document.getElementById("signout-message");
  message.hidden = false; message.className = "notice danger";
  message.textContent = error.message;
});

document.addEventListener("expense-report-changed", () => {
  if (receiptState.dirty) return;
  initializeSignout().catch(error => receiptMessage(error.message));
});

document.querySelector('a[href="#admin-expense-report"]')?.addEventListener("click", () => {
  document.getElementById("admin-expense-report").open = true;
});
