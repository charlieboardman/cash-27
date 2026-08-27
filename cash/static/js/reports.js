const statusOut = document.getElementById("report-status");
const fyInput = document.getElementById("report-fy");
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

async function generate(body) {
  const response = await fetch(`/api/reports/${fiscalYear()}/generate`, {
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
    const response = await generate({});
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `FY${fiscalYear()}-preview.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    const unallocated = Number(response.headers.get("X-Report-Unallocated") || 0);
    const unresolved = Number(response.headers.get("X-Report-Unresolved-Basis") || 0);
    if (unallocated || unresolved) {
      show(`Preview downloaded. Finalization is blocked by ${unallocated} unallocated expense(s) and ${unresolved} unresolved basis claim(s).`, "warning");
    } else {
      show("Preview downloaded. This period is currently eligible to finalize.");
    }
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});

document.getElementById("finalize-report").addEventListener("click", async event => {
  const button = event.currentTarget;
  const year = Number(fyInput.value);
  if (!confirm(`Finalize and retain the next FY${year} report revision?`)) return;
  button.disabled = true;
  try {
    const response = await generate({ final: true, submission_id: crypto.randomUUID(),
      notes: document.getElementById("report-notes").value });
    const result = (await response.json()).data;
    show(`${result.period} revision ${result.revision} finalized with ${result.rows} row(s).`);
    await loadReports();
  } catch (error) { show(error.message, "danger"); }
  finally { button.disabled = false; }
});

async function loadReports() {
  const response = await fetch("/api/reports");
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "Unable to load reports");
  document.getElementById("ledger-revision").textContent = payload.data.ledger_revision;
  const reports = payload.data.reports;
  document.getElementById("report-list").innerHTML = reports.map(report => `<tr>
    <td><strong>${esc(report.period)}</strong></td><td class="num">${report.revision}</td>
    <td class="num">${report.ledger_revision}</td>
    <td><span class="status-pill ${report.freshness === "current" ? "success" : "warning"}">${esc(report.freshness)}</span></td>
    <td>${esc(new Date(report.created_at).toLocaleString())}</td>
    <td>${esc(report.notes || "—")}</td>
    <td><a class="btn ghost compact-btn" href="/api/reports/${report.report_id}/download">Download</a></td>
  </tr>`).join("");
  document.getElementById("no-reports").hidden = reports.length !== 0;
}

loadReports().catch(error => show(error.message, "danger"));
