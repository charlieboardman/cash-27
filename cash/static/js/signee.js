/* Voucher-scoped signee reconciliation. Submitted facts stay separate from
   administrator coding; returned claims can be corrected and resubmitted. */
const voucherId = location.pathname.split("/")[1];
const form = document.getElementById("claim-form");
const claimEditor = document.getElementById("claim-editor");
const documentPickerPanel = document.getElementById("receipt-document-picker");
const documentViewer = document.getElementById("signee-document-viewer");
const workspaceStatus = document.getElementById("workspace-status");
let view = null;
let csrfToken = null;
let editingClaim = null;
let editorOpen = false;
let selectedClaimId = null;
const selectedDraftIds = new Set();

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

function claimRow(claim) {
  const net = settlement(claim);
  const editable = !view.read_only &&
    (claim.state === "draft" || claim.state === "flagged");
  const selectable = !view.read_only && claim.state === "draft" &&
    !claim.package_id;
  const selected = selectedClaimId === claim.claim_id;
  const expanded = editorOpen && selected;
  return `<tr class="signee-claim-row ${claim.state}${selected ? " selected" : ""}"
      data-claim="${esc(claim.claim_id)}" tabindex="0" aria-expanded="${expanded}">
    <td class="select-cell">${selectable ? `<input type="checkbox" aria-label="Select ${esc(claim.vendor)}"
      data-claim-select="${esc(claim.claim_id)}" ${selectedDraftIds.has(claim.claim_id) ? "checked" : ""}>` : `<span class="claim-state-dot" aria-hidden="true"></span>`}</td>
    <td><span class="sheet-caret" aria-hidden="true">›</span>${esc(claim.claim_date)}</td>
    <td>${esc(claim.vendor)}</td>
    <td class="claim-description">${esc(claim.description)}${claim.admin_note ? `<small><strong>Correction:</strong> ${esc(claim.admin_note)}</small>` : ""}</td>
    <td class="num">${esc(claim.face_currency)} ${decimal(claim.face_value_minor)}</td>
    <td class="num">${decimal(net.usd)}</td><td class="num">${decimal(net.mxn)}</td>
    <td><span class="status-pill ${claim.state === "accepted" || claim.state === "verified" ? "success" : claim.state === "flagged" ? "warning" : ""}">${esc(statusLabel(claim.state))}</span></td>
    <td class="document-cell"><a href="${esc(claim.stream_url)}" target="_blank" rel="noopener">Open PDF</a>
      ${editable ? `<button type="button" class="text-button edit-claim" data-claim="${esc(claim.claim_id)}">Edit</button>` : ""}</td>
  </tr>`;
}

function renderClaims() {
  document.getElementById("claim-count").textContent = view.claims.length;
  const list = document.getElementById("claims");
  let rows = "";
  for (const claim of view.claims) {
    rows += claimRow(claim);
    if (editorOpen && selectedClaimId === claim.claim_id) {
      rows += `<tr class="signee-claim-expansion"><td colspan="9"><div class="claim-editor-slot"></div></td></tr>`;
    }
  }
  if (editorOpen && !editingClaim) {
    rows += `<tr class="signee-claim-expansion new-receipt-expansion"><td colspan="9"><div class="claim-editor-slot"></div></td></tr>`;
  }
  if (!rows) rows = `<tr><td colspan="9" class="empty-state compact">No receipts yet. Add the first row.</td></tr>`;
  list.innerHTML = rows;
  const slot = list.querySelector(".claim-editor-slot");
  if (slot) {
    slot.appendChild(claimEditor);
    claimEditor.hidden = false;
  } else {
    claimEditor.hidden = true;
  }
  document.querySelector(".signee-sheet-panel")?.classList.toggle(
    "editing", editorOpen);
  document.getElementById("draft-actions").hidden = view.read_only ||
    !view.claims.some(claim => claim.state === "draft" && !claim.package_id);
  document.getElementById("open-new-receipt").hidden = view.read_only;
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

function renderDocumentRail() {
  const selected = view?.claims.find(claim => claim.claim_id === selectedClaimId);
  if (selected) {
    documentViewer.className = "document-viewer";
    const name = selected.display_name || "Receipt document";
    documentViewer.innerHTML = `<div class="viewer-toolbar"><span>${esc(name)}</span>
      <a href="${esc(selected.stream_url)}" target="_blank" rel="noopener">Open</a></div>
      <iframe src="${esc(selected.stream_url)}#view=FitH" title="Receipt preview"></iframe>`;
  } else {
    documentViewer.className = "document-viewer empty";
    documentViewer.innerHTML = `<div class="document-placeholder"><strong>${editorOpen ? "New receipt" : "No receipt selected"}</strong>
      <span>${editorOpen ? "Add its photos or files below." : "Add a row or select a receipt."}</span></div>`;
  }
  documentPickerPanel.hidden = view?.read_only || !editorOpen;
  const existing = document.getElementById("existing-document");
  existing.hidden = !editingClaim;
  existing.innerHTML = editingClaim
    ? `<span>Current document</span><a href="${esc(editingClaim.stream_url)}" target="_blank" rel="noopener">Open PDF</a>` : "";
}

function setFormValues(claim = null) {
  editingClaim = claim;
  editorOpen = true;
  selectedClaimId = claim?.claim_id || null;
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
    claim ? "Edit receipt row" : "Add a receipt row";
  document.getElementById("receipt-pages-heading").textContent = claim
    ? "Replace receipt pages (optional)" : "Add photos or files";
  document.getElementById("save-claim").textContent =
    claim ? "Save changes" : "Add receipt";
  document.getElementById("delete-claim").hidden =
    !claim || claim.state !== "draft";
  updateNet();
  if (view) {
    renderClaims();
    renderDocumentRail();
    const scrollTarget = window.matchMedia("(max-width: 700px)").matches
      ? documentPickerPanel : claimEditor;
    scrollTarget.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function closeEditor() {
  editingClaim = null;
  editorOpen = false;
  receiptPicker.clear();
  if (view) {
    renderClaims();
    renderDocumentRail();
  }
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
  const selectable = new Set(view.claims.filter(claim =>
    claim.state === "draft" && !claim.package_id).map(claim => claim.claim_id));
  for (const claimId of selectedDraftIds) {
    if (!selectable.has(claimId)) selectedDraftIds.delete(claimId);
  }
  renderBalances();
  renderActivity();
  renderPackages();
  if (view.read_only) {
    editingClaim = null;
    editorOpen = false;
  }
  if (!view.read_only && !view.claims.length && !editorOpen) {
    setFormValues();
  } else {
    renderClaims();
    renderDocumentRail();
  }
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
    closeEditor();
    await load();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

document.getElementById("cancel-edit").addEventListener("click", closeEditor);
document.getElementById("open-new-receipt").addEventListener("click", () =>
  setFormValues());
document.getElementById("delete-claim").addEventListener("click", async () => {
  if (!editingClaim || !confirm("Discard this receipt?")) return;
  try {
    await request(`/${voucherId}/claims/${editingClaim.claim_id}`, {
      method: "DELETE", body: { submission_id: cashUUID(),
        expected_content_version: editingClaim.content_version } });
    show("Receipt discarded.");
    closeEditor();
    await load();
  } catch (error) { show(error.message, "danger"); }
});

document.getElementById("claims").addEventListener("click", event => {
  const checkbox = event.target.closest("[data-claim-select]");
  if (checkbox) {
    if (checkbox.checked) selectedDraftIds.add(checkbox.dataset.claimSelect);
    else selectedDraftIds.delete(checkbox.dataset.claimSelect);
    return;
  }
  const button = event.target.closest(".edit-claim");
  if (button) {
    setFormValues(view.claims.find(claim =>
      claim.claim_id === button.dataset.claim));
    return;
  }
  if (event.target.closest("a")) return;
  const row = event.target.closest(".signee-claim-row");
  if (!row) return;
  const claim = view.claims.find(item => item.claim_id === row.dataset.claim);
  if (!claim) return;
  const editable = !view.read_only &&
    (claim.state === "draft" || claim.state === "flagged");
  if (editable) setFormValues(claim);
  else {
    editingClaim = null;
    editorOpen = false;
    selectedClaimId = claim.claim_id;
    renderClaims();
    renderDocumentRail();
  }
});

document.getElementById("claims").addEventListener("keydown", event => {
  if (!event.target.matches(".signee-claim-row") ||
      !["Enter", " "].includes(event.key)) return;
  event.target.click();
  event.preventDefault();
});

document.getElementById("submit-selected").addEventListener("click", async () => {
  const ids = [...selectedDraftIds];
  if (!ids.length) {
    show("Select at least one receipt to send.", "warning");
    return;
  }
  try {
    await request(`/${voucherId}/packages`, { method: "POST", body: {
      submission_id: cashUUID(), claim_ids: ids } });
    show(`${ids.length} receipt${ids.length === 1 ? "" : "s"} sent for review.`);
    selectedDraftIds.clear();
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
    closeEditor();
    await load();
  } catch (error) { show(error.message, "danger"); }
});

load().catch(error => show(error.message, "danger"));
