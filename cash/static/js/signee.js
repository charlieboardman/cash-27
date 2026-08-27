/* Voucher-scoped signee reconciliation. Submitted facts stay separate from
   administrator coding; returned claims can be corrected and resubmitted. */
const voucherId = location.pathname.split("/")[1];
const form = document.getElementById("claim-form");
const workspaceStatus = document.getElementById("workspace-status");
let view = null;
let csrfToken = null;
let editingClaim = null;

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function minor(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? Math.round(number * 100) : 0;
}

function decimal(value) { return (Number(value || 0) / 100).toFixed(2); }
function displayDate(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric" });
}
function settlement(claim) {
  return { usd: claim.usd_paid_cents - claim.usd_change_cents,
    mxn: claim.mxn_paid_centavos - claim.mxn_change_centavos };
}

function show(text, kind = "success") {
  workspaceStatus.hidden = false;
  workspaceStatus.className = `notice ${kind}`;
  workspaceStatus.textContent = text;
}

async function request(url, { method = "GET", body, formData } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-CSRF"] = csrfToken;
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { method, headers,
    body: body ? JSON.stringify(body) : formData });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error?.message || "Request failed");
  }
  return payload.data;
}

function statusLabel(state) {
  return ({ draft: "Not sent", submitted: "Sent for review", verified: "Checked",
    flagged: "Needs correction", accepted: "Approved" })[state] || state;
}

function submissionStatus(status) {
  return ({ submitted: "Sent for review", in_review: "Being reviewed",
    needs_changes: "Needs corrections", accepted: "Approved",
    withdrawn: "Withdrawn" })[status] || status.replaceAll("_", " ");
}

function renderBalances() {
  const voucher = view.voucher;
  document.getElementById("purpose").textContent =
    `${voucher.recipient} · ${voucher.purpose}`;
  const status = document.getElementById("voucher-status");
  status.textContent = voucher.status === "pending" ? "Open" : "Complete";
  status.className = `status-pill ${voucher.status === "complete" ? "success" : ""}`;
  const authoritative = view.authoritative || { usd: "0.00", mxn: "0.00" };
  document.getElementById("held-balance").textContent =
    `USD ${authoritative.usd} · MXN ${authoritative.mxn}`;
  document.getElementById("projected-balance").textContent =
    `USD ${view.projected_after_pending.usd} · MXN ${view.projected_after_pending.mxn}`;
  const hasOpenReceiptEffect = authoritative.usd !== view.projected_after_pending.usd ||
    authoritative.mxn !== view.projected_after_pending.mxn;
  document.getElementById("projected-note").hidden = !hasOpenReceiptEffect;
  const negative = Number(view.projected_after_pending.usd) < 0 ||
    Number(view.projected_after_pending.mxn) < 0;
  document.querySelector(".signee-cash-summary").classList.toggle("negative", negative);
  document.getElementById("read-only-notice").hidden = !view.read_only;
}

function signedMoney(value) {
  const amount = Number(value || 0) / 100;
  return `${amount > 0 ? "+" : amount < 0 ? "−" : ""}${Math.abs(amount).toFixed(2)}`;
}

function renderActivity() {
  const list = document.getElementById("cash-activity");
  list.innerHTML = view.activity.map(item => `<li>
    <time datetime="${esc(item.date)}">${esc(displayDate(item.date))}</time>
    <div><strong>${esc(item.label)}</strong>${item.detail ? `<span>${esc(item.detail)}</span>` : ""}</div>
    <p>${item.usd_change_cents ? `<span>USD ${signedMoney(item.usd_change_cents)}</span>` : ""}
      ${item.mxn_change_centavos ? `<span>MXN ${signedMoney(item.mxn_change_centavos)}</span>` : ""}</p>
  </li>`).join("") || `<li class="empty-state compact">No cash activity has been recorded.</li>`;
}

function claimCard(claim) {
  const net = settlement(claim);
  const used = [net.usd ? `USD ${decimal(net.usd)}` : "",
    net.mxn ? `MXN ${decimal(net.mxn)}` : ""].filter(Boolean).join(" · ") ||
    "No cash used";
  const editable = !view.read_only &&
    (claim.state === "draft" || claim.state === "flagged");
  const selectable = !view.read_only && claim.state === "draft" &&
    !claim.package_id;
  return `<li class="signee-claim ${claim.state}">
    <div class="claim-select">${selectable ? `<input type="checkbox" aria-label="Select ${esc(claim.vendor)}" data-claim="${esc(claim.claim_id)}">` : `<span class="claim-state-dot" aria-hidden="true"></span>`}</div>
    <div class="claim-summary"><div><strong>${esc(claim.vendor)}</strong>
      <span class="status-pill ${claim.state === "accepted" || claim.state === "verified" ? "success" : claim.state === "flagged" ? "warning" : ""}">${esc(statusLabel(claim.state))}</span></div>
      <p>${esc(displayDate(claim.claim_date))} · ${esc(claim.description)}</p>
      <div class="claim-money"><span>Receipt total <b>${esc(claim.face_currency)} ${decimal(claim.face_value_minor)}</b></span>
        <span>Cash used <b>${esc(used)}</b></span></div>
      ${claim.admin_note ? `<p class="correction-note"><strong>Correction requested:</strong> ${esc(claim.admin_note)}</p>` : ""}
    </div>
    <div class="claim-links"><a href="${esc(claim.stream_url)}" target="_blank" rel="noopener">View PDF</a>
      ${editable ? `<button type="button" class="text-button edit-claim" data-claim="${esc(claim.claim_id)}">Edit</button>` : ""}</div>
  </li>`;
}

function renderClaims() {
  document.getElementById("claim-count").textContent = view.claims.length;
  const list = document.getElementById("claims");
  list.innerHTML = view.claims.map(claimCard).join("") ||
    `<li class="empty-state compact">No receipt drafts yet. Add the first one using the form.</li>`;
  document.getElementById("draft-actions").hidden = view.read_only ||
    !view.claims.some(claim => claim.state === "draft" && !claim.package_id);
}

function packageCard(pkg) {
  const claims = view.claims.filter(claim => claim.package_id === pkg.package_id);
  const statusClass = pkg.status === "accepted" ? "success" :
    pkg.status === "needs_changes" ? "warning" : "";
  const canWithdraw = !view.read_only &&
    ["submitted", "needs_changes"].includes(pkg.status);
  return `<li><div class="package-number"><span>Submission</span><strong>${pkg.seq}</strong></div>
    <div><div class="package-title"><strong>${claims.length} receipt${claims.length === 1 ? "" : "s"}</strong>
      <span class="status-pill ${statusClass}">${esc(submissionStatus(pkg.status))}</span></div>
      ${pkg.admin_summary ? `<p>${esc(pkg.admin_summary)}</p>` : ""}</div>
    <div class="package-actions">
      ${!view.read_only && pkg.status === "needs_changes" ? `<button type="button" class="btn primary resubmit-package" data-package="${esc(pkg.package_id)}">Send corrections</button>` : ""}
      ${canWithdraw ? `<button type="button" class="btn ghost withdraw-package" data-package="${esc(pkg.package_id)}">Withdraw submission</button>` : ""}
    </div></li>`;
}

function renderPackages() {
  document.getElementById("packages").innerHTML =
    view.packages.map(packageCard).join("") ||
    `<li class="empty-state compact">No receipts have been sent for review yet.</li>`;
}

function setFormValues(claim = null) {
  editingClaim = claim;
  form.reset();
  receiptPicker.clear();
  const today = new Date();
  form.date.value = claim?.claim_date || today.toLocaleDateString("en-CA");
  form.vendor.value = claim?.vendor || "";
  form.description.value = claim?.description || "";
  form.face_value.value = claim ? decimal(claim.face_value_minor) : "";
  form.face_currency.value = claim?.face_currency || "MXN";
  form.usd_paid.value = claim ? decimal(claim.usd_paid_cents) : "0";
  form.usd_change.value = claim ? decimal(claim.usd_change_cents) : "0";
  form.mxn_paid.value = claim ? decimal(claim.mxn_paid_centavos) : "0";
  form.mxn_change.value = claim ? decimal(claim.mxn_change_centavos) : "0";
  form.notes.value = claim?.notes || "";
  document.getElementById("editor-eyebrow").textContent =
    claim ? "Correct receipt" : "New receipt";
  document.getElementById("editor-title").textContent =
    claim ? "Edit receipt" : "Add a receipt";
  document.getElementById("receipt-pages-heading").textContent = claim
    ? "Replace receipt pages (optional)" : "Add photos or files";
  document.getElementById("save-claim").textContent =
    claim ? "Save changes" : "Add receipt";
  document.getElementById("cancel-edit").hidden = !claim;
  document.getElementById("delete-claim").hidden =
    !claim || claim.state !== "draft";
  const existing = document.getElementById("existing-document");
  existing.hidden = !claim;
  existing.innerHTML = claim
    ? `<span>Current receipt file</span><a href="${esc(claim.stream_url)}" target="_blank" rel="noopener">View receipt PDF</a>` : "";
  updateNet();
  if (claim) document.querySelector(".claim-editor").scrollIntoView({
    behavior: "smooth", block: "start" });
}

function updateNet() {
  const usd = minor(form.usd_paid.value) - minor(form.usd_change.value);
  const mxn = minor(form.mxn_paid.value) - minor(form.mxn_change.value);
  document.getElementById("net-settlement").textContent =
    `Cash used: USD ${decimal(usd)} · MXN ${decimal(mxn)}`;
}

async function uploadReceipt(status) {
  const data = new FormData();
  const prepared = await receiptPicker.preparedFiles((page, total) => {
    status.textContent = `Preparing page ${page} of ${total}…`;
  });
  prepared.forEach(file => data.append("files", file, file.name));
  status.textContent = "Uploading receipt…";
  const uploaded = await request(`/${voucherId}/documents`, {
    method: "POST", formData: data });
  return uploaded.documents[0].document_id;
}

function claimBody(documentId) {
  return { submission_id: cashUUID(), document_id: documentId,
    date: form.date.value, vendor: form.vendor.value,
    description: form.description.value, face_value: form.face_value.value,
    face_currency: form.face_currency.value, usd_paid: form.usd_paid.value,
    usd_change: form.usd_change.value, mxn_paid: form.mxn_paid.value,
    mxn_change: form.mxn_change.value, notes: form.notes.value };
}

async function load() {
  view = await request(`/${voucherId}/state`);
  csrfToken = view.csrf;
  renderBalances();
  renderActivity();
  renderClaims();
  renderPackages();
  form.closest(".claim-editor").hidden = view.read_only;
}

form.addEventListener("input", event => {
  if (["usd_paid", "usd_change", "mxn_paid", "mxn_change"]
    .includes(event.target.name)) updateNet();
});

const receiptPicker = createOrderedDocumentPicker(
  document.getElementById("receipt-document-picker"), {
    onError: message => {
      document.getElementById("claim-status").textContent = message;
    },
  });

form.addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("claim-status");
  const button = document.getElementById("save-claim");
  if (!receiptPicker.count && !editingClaim) {
    status.textContent = "Take a photo or choose a receipt file.";
    return;
  }
  button.disabled = true;
  try {
    status.textContent = receiptPicker.count ? "Preparing receipt…" : "Saving correction…";
    const documentId = receiptPicker.count
      ? await uploadReceipt(status) : editingClaim.document_id;
    const body = claimBody(documentId);
    if (editingClaim) {
      body.expected_content_version = editingClaim.content_version;
      await request(`/${voucherId}/claims/${editingClaim.claim_id}`, {
        method: "PATCH", body });
      show("Receipt correction saved. Send the corrections when all requested changes are complete.");
    } else {
      await request(`/${voucherId}/claims`, { method: "POST", body });
      show("Receipt added. Select it when you are ready to send it for review.");
    }
    status.textContent = "";
    setFormValues();
    await load();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

document.getElementById("cancel-edit").addEventListener("click", () =>
  setFormValues());
document.getElementById("delete-claim").addEventListener("click", async () => {
  if (!editingClaim || !confirm("Discard this receipt?")) return;
  try {
    await request(`/${voucherId}/claims/${editingClaim.claim_id}`, {
      method: "DELETE", body: { submission_id: cashUUID(),
        expected_content_version: editingClaim.content_version } });
    show("Receipt discarded.");
    setFormValues();
    await load();
  } catch (error) { show(error.message, "danger"); }
});

document.getElementById("claims").addEventListener("click", event => {
  const button = event.target.closest(".edit-claim");
  if (!button) return;
  setFormValues(view.claims.find(claim =>
    claim.claim_id === button.dataset.claim));
});

document.getElementById("submit-selected").addEventListener("click", async () => {
  const ids = [...document.querySelectorAll("#claims input:checked")]
    .map(input => input.dataset.claim);
  if (!ids.length) {
    show("Select at least one receipt to send.", "warning");
    return;
  }
  try {
    await request(`/${voucherId}/packages`, { method: "POST", body: {
      submission_id: cashUUID(), claim_ids: ids } });
    show(`${ids.length} receipt${ids.length === 1 ? "" : "s"} sent for review.`);
    await load();
  } catch (error) { show(error.message, "danger"); }
});

document.getElementById("packages").addEventListener("click", async event => {
  const resubmit = event.target.closest(".resubmit-package");
  const withdraw = event.target.closest(".withdraw-package");
  if (!resubmit && !withdraw) return;
  try {
    if (resubmit) {
      await request(
        `/${voucherId}/packages/${resubmit.dataset.package}/resubmit`, {
          method: "POST", body: { submission_id: cashUUID() } });
      show("Corrections sent for review.");
    } else if (confirm(
      "Withdraw this submission so its receipts can be edited again?")) {
      await request(
        `/${voucherId}/packages/${withdraw.dataset.package}/withdraw`, {
          method: "POST", body: { submission_id: cashUUID() } });
      show("Submission withdrawn. Its receipts can be edited again.");
    } else return;
    setFormValues();
    await load();
  } catch (error) { show(error.message, "danger"); }
});

setFormValues();
load().catch(error => show(error.message, "danger"));
