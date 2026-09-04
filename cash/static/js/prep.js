/* Typed preparation workflow. Direct expenses capture final tender up front. */
const form = document.getElementById("prep");
const statusOut = document.getElementById("status");
const directFields = document.getElementById("direct-fields");
const simpleAmounts = document.getElementById("simple-amount-fields");
const exchangeFields = document.getElementById("exchange-fields");
const withdrawalFields = document.getElementById("withdrawal-fields");
const voucherFields = document.getElementById("voucher-fields");
const notesFields = document.getElementById("notes-fields");
const recipientLabel = document.getElementById("recipient-label");
const directNet = document.getElementById("direct-net");
const submitButton = document.getElementById("prep-submit");
const transactionTab = document.getElementById("transaction-tab");
const otherTab = document.getElementById("other-tab");
const otherPanel = document.getElementById("other-panel");

async function csrf() {
  const response = await fetch("/api/session");
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.data?.csrf) {
    throw new Error("Your session has expired. Reload the page and log in again.");
  }
  return payload.data.csrf;
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
    // Every amount box is optional by itself. Blank means zero; the server
    // checks the meaningful rules across the complete receipt and payment.
    input.required = input.name !== "reason" && required &&
      !["receipt_usd", "receipt_mxn", "usd_paid", "mxn_paid",
        "usd_change", "mxn_change"].includes(input.name);
  });
}

function showTab(name) {
  const transactionSelected = name === "transaction";
  form.hidden = !transactionSelected;
  otherPanel.hidden = transactionSelected;
  transactionTab.setAttribute("aria-selected", String(transactionSelected));
  otherTab.setAttribute("aria-selected", String(!transactionSelected));
}

function renderType() {
  const type = selectedType();
  const direct = type === "direct_expense";
  const signout = type === "signout";
  const exchange = type === "exchange";
  const withdrawal = type === "withdrawal";
  directFields.hidden = !direct;
  simpleAmounts.hidden = !signout;
  exchangeFields.hidden = !exchange;
  withdrawalFields.hidden = !withdrawal;
  voucherFields.hidden = !(direct || signout);
  notesFields.hidden = false;
  setRequired(directFields, direct);
  setRequired(exchangeFields, exchange);
  setRequired(withdrawalFields, withdrawal);
  form.elements.recipient.required = direct || signout;
  form.elements.purpose.required = direct || signout;
  recipientLabel.textContent = signout ? "Person receiving cash" : "Vendor";
  submitButton.textContent = (direct || signout)
    ? "Save and open printable voucher" : "Save transaction";
  statusOut.textContent = "";
}

transactionTab.addEventListener("click", () => showTab("transaction"));
otherTab.addEventListener("click", () => showTab("other"));
form.querySelectorAll('input[name="type"]').forEach(input =>
  input.addEventListener("change", renderType));
["usd_paid", "mxn_paid", "usd_change", "mxn_change"].forEach(name =>
  form.elements[name].addEventListener("input", updateNet));

form.addEventListener("submit", async event => {
  event.preventDefault();
  const clickedButton = event.submitter;
  const data = new FormData(form);
  const type = selectedType();
  const body = {
    submission_id: cashUUID(),
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
  if (clickedButton) clickedButton.disabled = true;
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
    const id = payload.data.voucher_id || payload.data.addendum_id ||
      payload.data.txn_ids?.[0];
    statusOut.className = "success";
    statusOut.textContent = payload.data.print_url
      ? `Saved ${id}. Opening printable form…`
      : `Saved transaction ${id}. Opening the ledger…`;
    if (payload.data.print_url) {
      window.location.assign(payload.data.print_url);
    } else if (id) {
      window.location.assign(`/transactions/${id}`);
    }
  } catch (error) {
    statusOut.className = "error";
    statusOut.textContent = error.message || "Could not save the form.";
  } finally {
    if (clickedButton) clickedButton.disabled = false;
  }
});

const localToday = new Date();
localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
form.elements.date.value = localToday.toISOString().slice(0, 10);
renderType();
updateNet();
showTab(new URLSearchParams(window.location.search).get("tab") === "other"
  ? "other" : "transaction");
