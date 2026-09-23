/* Voucher preparation records the authorized cash amounts. */
const form = document.getElementById("prep");
const statusOut = document.getElementById("status");
const simpleAmounts = document.getElementById("simple-amount-fields");
const exchangeFields = document.getElementById("exchange-fields");
const withdrawalFields = document.getElementById("withdrawal-fields");
const voucherFields = document.getElementById("voucher-fields");
const notesFields = document.getElementById("notes-fields");
const recipientLabel = document.getElementById("recipient-label");
const submitButton = document.getElementById("prep-submit");
const saveButton = document.getElementById("prep-save");
let saving = false;

async function csrf() {
  const response = await fetch("/api/session");
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.data?.csrf) {
    throw new Error("Your session has expired. Reload the page and log in again.");
  }
  return payload.data.csrf;
}

function selectedType() {
  return form.elements.type.value;
}

function setRequired(container, required) {
  container.querySelectorAll("input, select").forEach(input => {
    input.required = input.name !== "reason" && required;
  });
}

function renderType() {
  const type = selectedType();
  const direct = type === "direct_expense";
  const signout = type === "signout";
  const exchange = type === "exchange";
  const withdrawal = type === "withdrawal";
  simpleAmounts.hidden = !(direct || signout);
  exchangeFields.hidden = !exchange;
  withdrawalFields.hidden = !withdrawal;
  voucherFields.hidden = !(direct || signout);
  notesFields.hidden = false;
  setRequired(exchangeFields, exchange);
  setRequired(withdrawalFields, withdrawal);
  form.elements.recipient.required = direct || signout;
  form.elements.purpose.required = direct || signout;
  recipientLabel.textContent = signout ? "Recipient" : "Vendor";
  submitButton.hidden = !(direct || signout);
  statusOut.textContent = "";
}

form.querySelectorAll('input[name="type"]').forEach(input =>
  input.addEventListener("change", renderType));

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (saving) return;
  const printVoucher = event.submitter?.value === "print";
  const printTab = printVoucher ? window.open("about:blank", "_blank") : null;
  if (printTab) printTab.opener = null;
  const data = new FormData(form);
  const type = selectedType();
  const body = {
    submission_id: cashUUID(),
    date: data.get("date"),
    notes: data.get("notes") || "",
  };
  let url;
  if (type === "direct_expense" || type === "signout") {
    url = "/api/vouchers/prepare";
    Object.assign(body, {
      type,
      recipient: data.get("recipient"), purpose: data.get("purpose"),
      approver_name: data.get("approver_name"),
      usd: data.get("usd") || "0", mxn: data.get("mxn") || "0",
    });
  } else if (type === "exchange") {
    url = "/api/entries/exchange";
    Object.assign(body, {
      source_account: data.get("source_account") || "SAFE",
      usd: data.get("exchange_usd") || "0",
      mxn: data.get("exchange_mxn") || "0",
    });
  } else if (type === "withdrawal") {
    url = "/api/entries/withdrawal";
    body.usd = data.get("withdrawal_usd") || "0";
  }

  statusOut.className = "";
  statusOut.textContent = "Saving…";
  saving = true;
  saveButton.disabled = submitButton.disabled = true;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": await csrf() },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.error?.message || `Could not save (${response.status}).`);
    }
    const id = payload.data.voucher_id || payload.data.txn_ids?.[0];
    if (printTab && payload.data.print_url) {
      printTab.location.replace(payload.data.print_url);
    } else if (printTab) {
      printTab.close();
    }
    const destination = `/transactions/${payload.data.txn_ids[0]}`;
    statusOut.className = "success";
    statusOut.textContent = printVoucher && payload.data.print_url
      ? `Saved ${id}. Opening printable form…`
      : `Saved ${id}. Opening transaction…`;
    window.location.assign(destination);
  } catch (error) {
    if (printTab) printTab.close();
    statusOut.className = "error";
    statusOut.textContent = error.message || "Could not save the form.";
  } finally {
    saving = false;
    saveButton.disabled = submitButton.disabled = false;
  }
});

const localToday = new Date();
localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
form.elements.date.value = localToday.toISOString().slice(0, 10);
renderType();
