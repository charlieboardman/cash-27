/* Browser helpers shared by every authenticated page. */
(function exposeCashHelpers(global) {
  const moneyFormat = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  // Display only: keep decimal input values and API payloads ungrouped.
  global.cashMoney = value => moneyFormat.format(Number(value || 0));

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
  // Mutations also send this device's date for dates the server records.
  // keepalive lets a request finish while the page is being left.
  global.cashApi = async function cashApi(url, { method = "GET", body, formData, raw = false,
      keepalive = false } = {}) {
    const options = { method, headers: {}, keepalive };
    if (method !== "GET") {
      options.headers["X-CSRF"] = await csrf();
      options.headers["X-Client-Date"] = cashToday();
    }
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

  // Every command that posts to the ledger goes through here. While a
  // command's reply is unknown (no answer, or a server error), it is held:
  // sending it again reuses its submission ID, so the server treats it as the
  // same command and never records it twice, and a changed request to the same
  // place is refused until a reload shows whether the first one went through.
  // Any definite answer frees it.
  const unanswered = new Map();  // "METHOD url" -> { payload, submission_id }
  global.cashCommand = async function cashCommand(url, body = {}, { method = "POST" } = {}) {
    const key = `${method} ${url}`;
    const payload = JSON.stringify(body);
    const held = unanswered.get(key);
    if (held && held.payload !== payload) {
      throw new Error("The last save may have gone through. Reload the page before changing it.");
    }
    const submission_id = held?.submission_id || global.cashUUID();
    unanswered.set(key, { payload, submission_id });
    try {
      const data = await global.cashApi(url, { method, body: { ...body, submission_id } });
      unanswered.delete(key);
      return data;
    } catch (error) {
      if (error.status && error.status < 500) unanswered.delete(key);
      throw error;
    }
  };

  // Save a file from a raw response under the name the server gives it.
  global.cashSaveDownload = async (response, fallbackName) => {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = match ? decodeURIComponent(match[1]) : fallbackName;
    link.click();
    URL.revokeObjectURL(link.href);
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

  // A toast with an Undo button for actions that wait a few seconds before they
  // are committed. onExpire runs when time is up (or as soon as another toast
  // or settle() replaces this one); onUndo runs instead if Undo is used.
  // Returns { settle } to commit early. Only one is shown at a time.
  let activeToast = null;
  global.cashUndoToast = ({ text, undoLabel = "Undo", onUndo, onExpire,
      ms = global.cashUndoMs ?? 7000 }) => {
    if (activeToast) activeToast.settle();
    const element = document.createElement("output");
    element.className = "undo-toast";
    element.setAttribute("role", "status");
    const label = document.createElement("span");
    label.textContent = text;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = undoLabel;
    element.append(label, button);
    document.body.append(element);
    let finished = false;
    const close = () => { finished = true; window.clearTimeout(timer); element.remove();
      if (activeToast === toast) activeToast = null; };
    const toast = {
      settle() { if (finished) return; close(); return onExpire?.(); },
    };
    button.addEventListener("click", () => { if (finished) return; close(); onUndo?.(); });
    const timer = window.setTimeout(() => toast.settle(), ms);
    activeToast = toast;
    return toast;
  };

  // One confirmation dialog for every admin action that discards or replaces
  // something. Resolves true only on the confirm button; Escape cancels.
  // A dangerous action puts focus on Cancel, an ordinary one on Confirm.
  global.cashConfirm = (title, { message = "", confirmLabel = "Confirm", cancelLabel = "Cancel",
      danger = false } = {}) => new Promise(resolve => {
    const dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    const form = document.createElement("form");
    form.method = "dialog";
    const heading = document.createElement("h2");
    heading.textContent = title;
    heading.id = `confirm-title-${Math.random().toString(36).slice(2)}`;
    dialog.setAttribute("aria-labelledby", heading.id);
    form.append(heading);
    if (message) {
      const detail = document.createElement("p");
      detail.textContent = message;
      form.append(detail);
    }
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.value = "cancel"; cancel.className = "btn ghost"; cancel.textContent = cancelLabel;
    const ok = document.createElement("button");
    ok.value = "ok"; ok.className = `btn ${danger ? "danger" : "primary"}`; ok.textContent = confirmLabel;
    (danger ? cancel : ok).autofocus = true;
    actions.append(cancel, ok);
    form.append(actions);
    dialog.append(form);
    dialog.addEventListener("close", () => { dialog.remove(); resolve(dialog.returnValue === "ok"); });
    document.body.append(dialog);
    dialog.showModal();
  });

  // Top navigation: counts of what is waiting, and a fade where the phone nav
  // scrolls off screen. Both are decoration; a failure leaves the nav as is.
  document.addEventListener("DOMContentLoaded", () => {
    const scroller = document.querySelector(".topnav .nav-scroll");
    if (!scroller) return;
    const fade = () => scroller.classList.toggle("more",
      scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 2);
    scroller.addEventListener("scroll", fade, { passive: true });
    window.addEventListener("resize", fade);
    fade();
    const badges = document.querySelectorAll("[data-nav-count]");
    if (!badges.length) return;
    let loading = false;
    const refresh = async () => {
      if (loading || document.hidden) return;
      loading = true;
      try {
        const counts = await global.cashApi("/api/nav-counts");
        badges.forEach(badge => {
          const count = Number(counts[badge.dataset.navCount] || 0);
          badge.textContent = count > 99 ? "99+" : String(count);
          badge.hidden = count === 0;
        });
        fade();
      } catch (_) { /* Counts are optional. */ }
      finally { loading = false; }
    };
    document.addEventListener("visibilitychange", refresh);
    global.cashRefreshNavCounts = refresh;
    refresh();
  });

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
  };
  const statusLabels = {
    draft: "Draft", pending: "Open", open: "Open", submitted: "Submitted",
    approved: "Approved", complete: "Complete", closed: "Closed",
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
    `/api/documents/${encodeURIComponent(doc.document_id)}/${encodeURIComponent(doc.document_id)}.pdf?v=${encodeURIComponent(doc.sha256 || "")}`;
})(window);
