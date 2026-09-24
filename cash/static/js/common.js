/* Browser helpers shared by every authenticated page. */
(function exposeCashHelpers(global) {
  const moneyFormat = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  // Display only: keep decimal input values and API payloads ungrouped.
  global.cashMoney = value => moneyFormat.format(Number(value || 0));
  global.cashMinorMoney = value => global.cashMoney(Number(value || 0) / 100);

  global.cashEsc = value => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  // Today's date in the browser's time zone, as YYYY-MM-DD.
  global.cashToday = () => {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
      .toISOString().slice(0, 10);
  };

  let csrfToken = null;
  async function csrf() {
    if (csrfToken) return csrfToken;
    const response = await fetch("/api/session");
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.data?.csrf) {
      throw new Error("Your session has expired. Reload the page and log in again.");
    }
    csrfToken = payload.data.csrf;
    return csrfToken;
  }

  // Every page's server calls go through here. Sends JSON (body) or files
  // (formData), adds the CSRF token to mutations, and returns the envelope's
  // data. Errors carry the server's message, code, field errors, and status.
  // With raw: true a successful response is returned as-is (file downloads).
  global.cashApi = async function cashApi(url, { method = "GET", body, formData, raw = false } = {}) {
    const options = { method, headers: {} };
    if (method !== "GET") options.headers["X-CSRF"] = await csrf();
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    } else if (formData) {
      options.body = formData;
    }
    const response = await fetch(url, options);
    if (raw && response.ok) return response;
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.ok) {
      const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
      Object.assign(error, { code: payload?.error?.code, fields: payload?.error?.fields || {},
        status: response.status, payload });
      throw error;
    }
    return payload.data;
  };

  // Show a page-level message. Success messages fade after a few seconds
  // unless fade is false; warnings and errors stay until replaced.
  global.cashNotice = (element, text, tone = "success", { fade = tone === "success" } = {}) => {
    window.clearTimeout(element.cashNoticeTimer);
    element.hidden = !text;
    element.className = `notice ${tone}`;
    element.textContent = text;
    if (text && fade) {
      element.cashNoticeTimer = window.setTimeout(() => { element.hidden = true; }, 7000);
    }
  };

  global.cashUUID = function cashUUID() {
    if (global.crypto?.randomUUID) return global.crypto.randomUUID();

    // randomUUID is restricted to secure origins, but getRandomValues is
    // available when this small intranet app is reached over tailnet HTTP.
    const bytes = new Uint8Array(16);
    global.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-` +
      `${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-` +
      hex.slice(10).join("");
  };
  // The only place user-facing type and status names are defined. See GLOSSARY.md.
  const typeLabels = {
    opening: "Starting cash", donation: "Donation", expense: "Expense",
    direct_expense: "Direct expense", signout: "Signout", addendum: "Addendum",
    exchange: "Currency exchange", bank_withdrawal: "Bank withdrawal",
    withdrawal: "Bank withdrawal", adjustment: "Adjustment",
    package: "Receipt submission",
  };
  const statusLabels = {
    draft: "Draft", pending: "Open", open: "Open", submitted: "Submitted",
    in_review: "In review", needs_changes: "Needs corrections",
    flagged: "Needs correction", verified: "Checked", accepted: "Approved",
    withdrawn: "Withdrawn", complete: "Complete", closed: "Closed",
    void: "Void", superseded: "Replaced",
  };
  const fallback = value => String(value || "").replaceAll("_", " ")
    .replace(/^./, first => first.toUpperCase());
  // Signout open reasons come from cash/signouts.py as stable codes.
  const reasonLabels = {
    "cash remaining": "Cash remaining",
    "currency value needs reconciliation": "USD value unresolved",
    "voucher needed": "Needs signed voucher",
    "addendum needed": "Needs signed addendum",
    "receipts in progress": "Receipts in progress",
    "receipt submission in progress": "Receipts awaiting review",
    "closeout needs review": "Settlement needs confirming again",
    "closeout needed": "Needs settlement",
  };
  global.cashLabels = {
    type: value => typeLabels[value] || fallback(value),
    status: value => statusLabels[value] || fallback(value),
    reason: value => reasonLabels[value] || fallback(value),
  };

  global.cashDocumentPending = doc => ["queued", "running"].includes(doc?.processing?.status);
  global.cashDocumentProcessing = doc => global.cashDocumentPending(doc)
    ? '<span class="document-processing" role="status"><span class="processing-spinner" aria-hidden="true"></span>Document processing…</span>'
    : doc?.processing?.status === "failed"
    ? '<span class="document-processing-failed" role="status">Processing failed; review this document manually.</span>' : "";
  global.cashDocumentStream = doc => doc.stream_url ||
    `/api/documents/${encodeURIComponent(doc.document_id)}/stream?v=${encodeURIComponent(doc.sha256 || "")}`;
})(window);
