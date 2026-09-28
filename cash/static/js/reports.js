const statusOut = document.getElementById("report-status");
const fyInput = document.getElementById("report-fy");
const typeInput = document.getElementById("report-type");
const formatInput = document.getElementById("report-format");
const show = (text, tone) => cashNotice(statusOut, text, tone);
const startMonth = Number(fyInput.dataset.fyStartMonth);

typeInput.addEventListener("change", () => {
  document.getElementById("report-type-description").textContent = typeInput.value === "cash_ledger"
    ? "Every transaction with running safe balances."
    : "Expense lines with account and class codes.";
});

// Matches prep.fiscal_year_for: FY N starts in year N - 1.
function fiscalYearFor(isoDate) {
  const [year, month] = isoDate.split("-").map(Number);
  return month >= startMonth ? year + 1 : year;
}

// Every year with transactions, newest first, then everything. The current
// year comes from this device's date and is always offered and selected.
const currentFy = fiscalYearFor(cashToday());
const firstFy = Number(fyInput.dataset.firstFy) || currentFy;
for (let year = Math.max(currentFy, firstFy); year >= Math.min(currentFy, firstFy); year--) {
  fyInput.add(new Option(year === currentFy ? `FY${year} (current)` : `FY${year}`, year));
}
fyInput.add(new Option("All years", ""));
fyInput.value = String(currentFy);

function updatePeriod() {
  const year = Number(fyInput.value);
  const format = new Intl.DateTimeFormat("en-US", {month: "long", day: "numeric", year: "numeric"});
  document.getElementById("period-description").textContent = !fyInput.value
    ? "All fiscal years."
    : `FY${year}: ${format.format(new Date(year - 1, startMonth - 1, 1))} to ` +
      `${format.format(new Date(year, startMonth - 1, 0))}.`;
  const query = new URLSearchParams({export_type: typeInput.value});
  if (fyInput.value) query.set("fy", fyInput.value);
  document.getElementById("view-bookkeeper").href = `/reports/bookkeeper?${query}`;
  document.getElementById("download-documents").href = `/api/export/bookkeeper.zip?${query}`;
}
fyInput.addEventListener("change", updatePeriod);
typeInput.addEventListener("change", updatePeriod);
updatePeriod();

document.getElementById("download-report").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const query = new URLSearchParams({export_type: typeInput.value});
    if (fyInput.value) query.set("fy", fyInput.value);
    const response = await cashApi(`/api/export/transactions.${formatInput.value}?${query}`, { raw: true });
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = match ? decodeURIComponent(match[1]) : `report.${formatInput.value}`;
    link.click();
    URL.revokeObjectURL(link.href);
    const unallocated = Number(response.headers.get("X-Report-Unallocated") || 0);
    const unresolved = Number(response.headers.get("X-Report-Unresolved-Basis") || 0);
    const problems = [];
    if (unallocated) problems.push(`${unallocated} purchase${unallocated === 1 ? " needs" : "s need"} an account code`);
    if (unresolved) problems.push(`${unresolved} MXN item${unresolved === 1 ? " needs" : "s need"} a recorded USD value`);
    if (problems.length) show(`Downloaded. ${problems.join("; ")}.`, "warning");
    else show("Downloaded.");
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});
