const statusOut = document.getElementById("report-status");
const fyInput = document.getElementById("report-fy");
const typeInput = document.getElementById("report-type");
const formatInput = document.getElementById("report-format");
const typeLabel = type => type === "cash_ledger" ? "Cash ledger" : "Expense report";
typeInput.addEventListener("change", () => {
  document.getElementById("report-type-description").textContent = typeInput.value === "cash_ledger"
    ? "All active transactions and running safe balances."
    : "Expenses and accounting codes for the bookkeeper.";
});
let csrfToken;

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

async function csrf() {
  if (!csrfToken) csrfToken = (await (await fetch("/api/session")).json()).data.csrf;
  return csrfToken;
}

function show(text, kind = "success") {
  statusOut.hidden = false;
  statusOut.className = `notice ${kind}`;
  statusOut.textContent = text;
}

function fiscalYear() {
  const value = Number(fyInput.value);
  if (!Number.isInteger(value) || value < 2000 || value > 2200) {
    throw new Error("Enter a fiscal year between 2000 and 2200.");
  }
  return value;
}

function updatePeriodDescription() {
  const year = Number(fyInput.value);
  document.getElementById("period-description").textContent =
    Number.isInteger(year)
      ? `FY${year} runs from September 1, ${year - 1} through August 31, ${year}.`
      : "Enter the ending year for the fiscal period.";
}

fyInput.addEventListener("input", updatePeriodDescription);

async function generate(body, year = fiscalYear()) {
  const response = await fetch(`/api/reports/${year}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF": await csrf() },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error?.message || "Report generation failed");
  }
  return response;
}

document.getElementById("preview-report").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const year = fiscalYear();
    const format = formatInput.value;
    const exportType = typeInput.value;
    const response = await generate({ format, export_type: exportType }, year);
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
    const response = await generate({ final: true, export_type: exportType, submission_id: cashUUID(),
      notes: document.getElementById("report-notes").value });
    const result = (await response.json()).data;
    show(`${result.period} final report ${result.revision} saved with ${result.rows} row(s) (${typeLabel(result.export_type)}).`);
    await loadReports();
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});

async function loadReports() {
  const response = await fetch("/api/reports");
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Unable to load reports");
  const reports = payload.data.reports;
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
