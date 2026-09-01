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
    const sessionResponse = await fetch("/api/session");
    const sessionPayload = await sessionResponse.json();
    const response = await fetch(`/api/entries/${cashEntryKind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json",
        "X-CSRF": sessionPayload.data.csrf },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.error?.message || "Could not save cash");
    }
    if (cashEntryKind === "opening") {
      window.location.assign("/ledger");
      return;
    }
    cashEntryStatus.className = "success";
    cashEntryStatus.textContent = "Donation saved.";
    cashEntryForm.reset();
    setCashEntryToday();
    updateCashEntryRate();
  } catch (error) {
    cashEntryStatus.className = "error";
    cashEntryStatus.textContent = error.message;
  } finally { button.disabled = false; }
});

function setCashEntryToday() {
  const today = new Date();
  today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  cashEntryForm.elements.date.value = today.toISOString().slice(0, 10);
}

setCashEntryToday();
updateCashEntryRate();
