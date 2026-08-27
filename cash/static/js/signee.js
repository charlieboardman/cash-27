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
  return ({ draft: "Draft", submitted: "Submitted", verified: "Verified",
    flagged: "Correction needed", accepted: "Accepted" })[state] || state;
}

function renderBalances() {
  const voucher = view.voucher;
  document.getElementById("purpose").textContent =
    `${voucher.recipient} · ${voucher.purpose}`;
  const status = document.getElementById("voucher-status");
  status.textContent = voucher.status;
  status.className = `status-pill ${voucher.status === "complete" ? "success" : ""}`;
  document.getElementById("received-balance").textContent =
    `USD ${decimal(voucher.authorized_usd_cents)} · MXN ${decimal(voucher.authorized_mxn_centavos)}`;
  const authoritative = view.authoritative || { usd: "0.00", mxn: "0.00" };
  document.getElementById("held-balance").textContent =
    `USD ${authoritative.usd} · MXN ${authoritative.mxn}`;
  document.getElementById("projected-balance").textContent =
    `USD ${view.projected_after_pending.usd} · MXN ${view.projected_after_pending.mxn}`;
  const negative = Number(view.projected_after_pending.usd) < 0 ||
    Number(view.projected_after_pending.mxn) < 0;
  document.getElementById("projected-card").classList.toggle("negative", negative);
  document.getElementById("projected-note").textContent = negative
    ? "Pending receipts exceed the cash currently held"
    : "Includes drafts and packages under review";
  document.getElementById("read-only-notice").hidden = !view.read_only;
}

function claimCard(claim) {
  const net = settlement(claim);
  const editable = !view.read_only &&
    (claim.state === "draft" || claim.state === "flagged");
  const selectable = !view.read_only && claim.state === "draft" &&
    !claim.package_id;
  return `<li class="signee-claim ${claim.state}">
    <div class="claim-select">${selectable ? `<input type="checkbox" aria-label="Select ${esc(claim.vendor)}" data-claim="${esc(claim.claim_id)}">` : `<span class="claim-state-dot" aria-hidden="true"></span>`}</div>
    <div class="claim-summary"><div><strong>${esc(claim.vendor)}</strong>
      <span class="status-pill ${claim.state === "accepted" || claim.state === "verified" ? "success" : claim.state === "flagged" ? "warning" : ""}">${esc(statusLabel(claim.state))}</span></div>
      <p>${esc(claim.claim_date)} · ${esc(claim.description)}</p>
      <div class="claim-money"><span>Face <b>${esc(claim.face_currency)} ${decimal(claim.face_value_minor)}</b></span>
        <span>From custody <b>USD ${decimal(net.usd)} · MXN ${decimal(net.mxn)}</b></span></div>
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
  return `<li><div class="package-number"><span>Package</span><strong>${pkg.seq}</strong></div>
    <div><div class="package-title"><strong>${claims.length} receipt${claims.length === 1 ? "" : "s"}</strong>
      <span class="status-pill ${statusClass}">${esc(pkg.status.replaceAll("_", " "))}</span></div>
      ${pkg.admin_summary ? `<p>${esc(pkg.admin_summary)}</p>` : `<p>No reviewer note.</p>`}</div>
    <div class="package-actions">
      ${!view.read_only && pkg.status === "needs_changes" ? `<button type="button" class="btn primary resubmit-package" data-package="${esc(pkg.package_id)}">Resubmit corrections</button>` : ""}
      ${canWithdraw ? `<button type="button" class="btn ghost withdraw-package" data-package="${esc(pkg.package_id)}">Withdraw</button>` : ""}
    </div></li>`;
}

function renderPackages() {
  document.getElementById("packages").innerHTML =
    view.packages.map(packageCard).join("") ||
    `<li class="empty-state compact">No packages submitted yet.</li>`;
}

function setFormValues(claim = null) {
  editingClaim = claim;
  form.reset();
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
    claim ? "Correct evidence" : "New evidence";
  document.getElementById("editor-title").textContent =
    claim ? "Edit receipt draft" : "Add a receipt draft";
  document.getElementById("save-claim").textContent =
    claim ? "Save changes" : "Add draft";
  document.getElementById("cancel-edit").hidden = !claim;
  document.getElementById("delete-claim").hidden =
    !claim || claim.state !== "draft";
  document.getElementById("file-label").textContent = claim
    ? "Replace PDF or image (optional)" : "Receipt PDF or image";
  const existing = document.getElementById("existing-document");
  existing.hidden = !claim;
  existing.innerHTML = claim
    ? `<span>Current evidence</span><a href="${esc(claim.stream_url)}" target="_blank" rel="noopener">View receipt PDF</a>` : "";
  updateNet();
  if (claim) document.querySelector(".claim-editor").scrollIntoView({
    behavior: "smooth", block: "start" });
}

function updateNet() {
  const usd = minor(form.usd_paid.value) - minor(form.usd_change.value);
  const mxn = minor(form.mxn_paid.value) - minor(form.mxn_change.value);
  document.getElementById("net-settlement").textContent =
    `Net from custody: USD ${decimal(usd)} · MXN ${decimal(mxn)}`;
}

async function uploadReceipt(file) {
  const data = new FormData();
  data.append("files", file);
  const uploaded = await request(`/${voucherId}/documents`, {
    method: "POST", formData: data });
  return uploaded.documents[0].document_id;
}

function claimBody(documentId) {
  return { submission_id: crypto.randomUUID(), document_id: documentId,
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
  renderClaims();
  renderPackages();
  form.closest(".claim-editor").hidden = view.read_only;
}

form.addEventListener("input", event => {
  if (["usd_paid", "usd_change", "mxn_paid", "mxn_change"]
    .includes(event.target.name)) updateNet();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("claim-status");
  const button = document.getElementById("save-claim");
  const file = document.getElementById("claim-file").files[0];
  if (!file && !editingClaim) {
    status.textContent = "Choose a receipt PDF or image.";
    return;
  }
  button.disabled = true;
  try {
    status.textContent = file ? "Uploading receipt…" : "Saving correction…";
    const documentId = file
      ? await uploadReceipt(file) : editingClaim.document_id;
    const body = claimBody(documentId);
    if (editingClaim) {
      body.expected_content_version = editingClaim.content_version;
      await request(`/${voucherId}/claims/${editingClaim.claim_id}`, {
        method: "PATCH", body });
      show("Receipt correction saved. Resubmit its package when all requested changes are complete.");
    } else {
      await request(`/${voucherId}/claims`, { method: "POST", body });
      show("Receipt draft added. Select it when you are ready to submit.");
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
  if (!editingClaim || !confirm("Discard this unsubmitted receipt draft?")) return;
  try {
    await request(`/${voucherId}/claims/${editingClaim.claim_id}`, {
      method: "DELETE", body: { submission_id: crypto.randomUUID(),
        expected_content_version: editingClaim.content_version } });
    show("Receipt draft discarded.");
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
    show("Select at least one draft to submit.", "warning");
    return;
  }
  try {
    await request(`/${voucherId}/packages`, { method: "POST", body: {
      submission_id: crypto.randomUUID(), claim_ids: ids } });
    show(`${ids.length} receipt${ids.length === 1 ? "" : "s"} submitted for review.`);
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
          method: "POST", body: { submission_id: crypto.randomUUID() } });
      show("Corrected package resubmitted for review.");
    } else if (confirm(
      "Withdraw this package and return all its receipts to drafts?")) {
      await request(
        `/${voucherId}/packages/${withdraw.dataset.package}/withdraw`, {
          method: "POST", body: { submission_id: crypto.randomUUID() } });
      show("Package withdrawn. Its receipts are drafts again.");
    } else return;
    setFormValues();
    await load();
  } catch (error) { show(error.message, "danger"); }
});

setFormValues();
load().catch(error => show(error.message, "danger"));
