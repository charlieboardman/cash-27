/* Typed preparation workflow. Direct expenses capture final tender up front. */
const form = document.getElementById("prep");
const statusOut = document.getElementById("status");
const directFields = document.getElementById("direct-fields");
const simpleAmounts = document.getElementById("simple-amount-fields");
const addendumFields = document.getElementById("addendum-fields");
const voucherFields = document.getElementById("voucher-fields");
const recipientLabel = document.getElementById("recipient-label");
const partyHeading = document.getElementById("party-heading");
const directNet = document.getElementById("direct-net");

async function csrf() {
  return (await (await fetch("/api/session")).json()).data.csrf;
}

function amount(name) {
  const value = Number.parseFloat(form.elements[name].value || "0");
  return Number.isFinite(value) ? value : 0;
}

function updateNet() {
  const usd = amount("usd_paid") - amount("usd_change");
  const mxn = amount("mxn_paid") - amount("mxn_change");
  directNet.textContent = `USD ${usd.toFixed(2)} · MXN ${mxn.toFixed(2)}`;
  directNet.classList.toggle("invalid", usd < 0 || mxn < 0 || (usd === 0 && mxn === 0));
}

function selectedType() {
  return form.elements.type.value;
}

function setRequired(container, required) {
  container.querySelectorAll("input, select").forEach(input => {
    if (input.name !== "reason") input.required = required;
  });
}

function renderType() {
  const type = selectedType();
  const direct = type === "direct_expense";
  const signout = type === "signout";
  const addendum = type === "positive" || type === "return";
  directFields.hidden = !direct;
  simpleAmounts.hidden = direct;
  addendumFields.hidden = !addendum;
  voucherFields.hidden = addendum;
  setRequired(directFields, direct);
  form.elements.voucher_id.required = addendum;
  form.elements.recipient.required = !addendum;
  form.elements.purpose.required = !addendum;
  recipientLabel.textContent = signout ? "Person receiving cash" : "Vendor";
  partyHeading.textContent = signout ? "Recipient" : "Vendor";
  statusOut.textContent = "";
}

form.querySelectorAll('input[name="type"]').forEach(input =>
  input.addEventListener("change", renderType));
["usd_paid", "mxn_paid", "usd_change", "mxn_change"].forEach(name =>
  form.elements[name].addEventListener("input", updateNet));

form.addEventListener("submit", async event => {
  event.preventDefault();
  const data = new FormData(form);
  const type = selectedType();
  const body = {
    submission_id: crypto.randomUUID(),
    date: data.get("date"),
    notes: data.get("notes") || "",
  };
  let url;
  if (type === "direct_expense") {
    url = "/api/vouchers/prepare";
    Object.assign(body, {
      type,
      recipient: data.get("recipient"), purpose: data.get("purpose"),
      receipt_usd: data.get("receipt_usd") || "0",
      receipt_mxn: data.get("receipt_mxn") || "0",
      usd_paid: data.get("usd_paid") || "0",
      mxn_paid: data.get("mxn_paid") || "0",
      usd_change: data.get("usd_change") || "0",
      mxn_change: data.get("mxn_change") || "0",
    });
  } else if (type === "signout") {
    url = "/api/vouchers/prepare";
    Object.assign(body, {
      type,
      recipient: data.get("recipient"), purpose: data.get("purpose"),
      usd: data.get("usd") || "0", mxn: data.get("mxn") || "0",
    });
  } else {
    url = "/api/addenda/prepare";
    Object.assign(body, {
      voucher_id: data.get("voucher_id"), kind: type,
      reason: data.get("reason") || "",
      usd: data.get("usd") || "0", mxn: data.get("mxn") || "0",
    });
    delete body.notes;
  }

  statusOut.textContent = "Saving…";
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF": await csrf() },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!payload.ok) {
    statusOut.textContent = payload.error.message;
    statusOut.className = "error";
    return;
  }
  const id = payload.data.voucher_id || payload.data.addendum_id;
  statusOut.className = "success";
  statusOut.textContent = `Saved ${id}. Opening printable form…`;
  if (payload.data.print_url) window.open(payload.data.print_url, "_blank", "noopener");
});

form.elements.date.value = new Date().toISOString().slice(0, 10);
renderType();
updateNet();
