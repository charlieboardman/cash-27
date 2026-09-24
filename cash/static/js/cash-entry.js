const cashEntryForm = document.getElementById("cash-entry");
const cashEntryStatus = document.getElementById("cash-entry-status");
const cashEntryRate = document.getElementById("cash-entry-rate");
const cashEntryKind = cashEntryForm.dataset.kind;
const rateRequiredForMxn = cashEntryForm.dataset.mxnRateRequired === "true";

function cashEntryAmount(name) {
  const value = Number.parseFloat(cashEntryForm.elements[name].value || "0");
  return Number.isFinite(value) ? value : 0;
}

function updateCashEntryRate() {
  const required = cashEntryAmount("mxn") > 0 && rateRequiredForMxn;
  cashEntryRate.hidden = !required;
  cashEntryForm.elements.rate.required = required;
}

cashEntryForm.elements.mxn.addEventListener("input", updateCashEntryRate);
cashEntryForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  const values = new FormData(cashEntryForm);
  const body = { submission_id: cashUUID(), date: values.get("date"),
    usd: values.get("usd") || "0", mxn: values.get("mxn") || "0",
    notes: values.get("notes") || "" };
  const rate = values.get("rate");
  if (rate) body.assigned_rate_milli = Math.round(Number.parseFloat(rate) * 1000);
  cashEntryStatus.className = "";
  cashEntryStatus.textContent = "Saving…";
  button.disabled = true;
  try {
    await cashApi(`/api/entries/${cashEntryKind}`, { method: "POST", body });
    if (cashEntryKind === "opening") {
      window.location.assign("/ledger");
      return;
    }
    cashEntryStatus.className = "success";
    cashEntryStatus.textContent = "Donation saved.";
    cashEntryForm.reset();
    cashEntryForm.elements.date.value = cashToday();
    updateCashEntryRate();
  } catch (error) {
    cashEntryStatus.className = "error";
    cashEntryStatus.textContent = error.message;
  } finally { button.disabled = false; }
});

cashEntryForm.elements.date.value = cashToday();
updateCashEntryRate();
