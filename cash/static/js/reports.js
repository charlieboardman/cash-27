const statusOut = document.getElementById("report-status");
const fyInput = document.getElementById("report-fy");
const typeInput = document.getElementById("report-type");
const formatInput = document.getElementById("report-format");
const typeLabel = type => type === "cash_ledger" ? "Cash ledger" : "Expense report";
typeInput.addEventListener("change", () => {
  document.getElementById("report-type-description").textContent = typeInput.value === "cash_ledger"
    ? "Every transaction with running safe balances."
    : "Expense lines with account and class codes.";
});
const esc = cashEsc;
const show = (text, tone) => cashNotice(statusOut, text, tone);

function fiscalYear() {
  const value = Number(fyInput.value);
  if (!Number.isInteger(value) || value < 2000 || value > 2200) {
    throw new Error("Enter a fiscal year between 2000 and 2200.");
  }
  return value;
}

function updatePeriodDescription() {
  const year = Number(fyInput.value);
  const startMonth = Number(fyInput.dataset.fyStartMonth);
  const format = new Intl.DateTimeFormat("en-US", {month: "long", day: "numeric", year: "numeric"});
  // Matches prep.fiscal_year_for: FY N starts in year N - 1.
  const startYear = year - 1;
  document.getElementById("period-description").textContent =
    Number.isInteger(year)
      ? `FY${year}: ${format.format(new Date(startYear, startMonth - 1, 1))} to ` +
        `${format.format(new Date(startYear + 1, startMonth - 1, 0))}.`
      : "Enter the year the fiscal year ends.";
}

fyInput.addEventListener("input", updatePeriodDescription);
updatePeriodDescription();

// Previews come back as a file; final reports as the usual JSON envelope.
function generate(body, year = fiscalYear(), { raw = false } = {}) {
  return cashApi(`/api/reports/${year}/generate`, { method: "POST", body, raw });
}

document.getElementById("preview-report").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const year = fiscalYear();
    const format = formatInput.value;
    const exportType = typeInput.value;
    const response = await generate({ format, export_type: exportType }, year, { raw: true });
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `FY${year}-${exportType === "cash_ledger" ? "cash-ledger" : "expenses"}-preview.${format}`;
    link.click();
    URL.revokeObjectURL(link.href);
    const unallocated = Number(response.headers.get("X-Report-Unallocated") || 0);
    const unresolved = Number(response.headers.get("X-Report-Unresolved-Basis") || 0);
    if (unallocated || unresolved) {
      const problems = [];
      if (unallocated) problems.push(`${unallocated} purchase${unallocated === 1 ? " needs" : "s need"} an account code`);
      if (unresolved) problems.push(`${unresolved} MXN item${unresolved === 1 ? " needs" : "s need"} a recorded USD value`);
      show(`Working copy downloaded. A final report cannot be saved yet: ${problems.join("; ")}.`, "warning");
    } else {
      show("Working copy downloaded. This period is ready for a final report.");
    }
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});

document.getElementById("finalize-report").addEventListener("click", async event => {
  const button = event.currentTarget;
  const year = Number(fyInput.value);
  const exportType = typeInput.value;
  if (!confirm(`Save the next numbered ${typeLabel(exportType).toLowerCase()} for FY${year}?`)) return;
  button.disabled = true;
  try {
    const result = await generate({ final: true, export_type: exportType, submission_id: cashUUID(),
      notes: document.getElementById("report-notes").value });
    show(`${result.period} final report ${result.revision} saved with ${result.rows} row(s) (${typeLabel(result.export_type)}).`);
    await loadReports();
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});

async function loadReports() {
  const { reports } = await cashApi("/api/reports");
  document.getElementById("report-list").innerHTML = reports.map(report => `<tr>
    <td data-label="Period"><strong>${esc(report.period)}</strong></td><td data-label="Export type">${typeLabel(report.export_type)}</td><td data-label="Report number" class="num">${report.revision}</td>
    <td data-label="Status"><span class="status-pill ${report.freshness === "current" ? "success" : "warning"}">${report.freshness === "current" ? "Up to date" : "Out of date"}</span></td>
    <td data-label="Saved">${esc(new Date(report.created_at).toLocaleString())}</td>
    <td data-label="Notes">${esc(report.notes || "—")}</td>
    <td><div class="export-actions">
      <a class="btn ghost compact-btn" href="/api/reports/${report.report_id}/download?format=xlsx">Download XLSX</a>
      <a class="btn ghost compact-btn" href="/api/reports/${report.report_id}/download?format=csv">Download CSV</a>
    </div></td>
  </tr>`).join("");
  document.getElementById("no-reports").hidden = reports.length !== 0;
}

loadReports().catch(error => show(error.message, "danger"));

function updateBookkeeperLinks() {
  const query = new URLSearchParams({fy: fyInput.value, export_type: typeInput.value});
  document.getElementById('view-bookkeeper').href = `/reports/bookkeeper?${query}`;
  document.getElementById('download-documents').href = `/api/export/bookkeeper.zip?${query}`;
}
fyInput.addEventListener('input', updateBookkeeperLinks);
typeInput.addEventListener('change', updateBookkeeperLinks);
updateBookkeeperLinks();
