/* Expense report at /FY##-NNNN, shared by the recipient and the administrator.

   Each row is one receipt, a cash return, or a reimbursement request. A row
   opens in place to edit and saves itself shortly after typing stops.
   Approval (administrator only) posts the rows to the ledger. */
(() => {
  const root = document.getElementById("expense-report");
  if (!root) return;
  const vid = root.dataset.voucher;
  const admin = root.dataset.admin === "true";
  const $ = selector => root.querySelector(selector);
  const esc = cashEsc;
  const i18n = admin ? null : window.signeeI18n || null;
  const t = (key, values = {}) => i18n ? i18n.t(key, values)
    : key.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
  const errorText = error => i18n
    ? i18n.error({ code: error.code, message: error.message }) : error.message;
  const api = (path, options) => cashApi(`/${vid}${path}`, options);

  let view = null;
  let openId = null;
  const selected = new Set();
  const saves = new Map();            // row id -> {timer, pending, running}
  const correctionNotes = {};         // admin notes waiting for "Request corrections"
  let noteTimer = null;
  let upload = null;                  // {rowId, mode: "new" | "append" | "replace"}

  /* ---- money --------------------------------------------------------------- */

  // Decimal string -> cents; blank is zero; NaN when malformed.
  function cents(text) {
    const value = String(text ?? "").trim();
    if (!value) return 0;
    if (!/^[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(value)) return NaN;
    return Math.round(Number(value) * 100);
  }
  const decimal = minor => (minor / 100).toFixed(2);
  const inputValue = text => (text == null || Number(text) === 0 ? "" : text);
  const pair = (usd, mxn) => `USD ${cashMoney(usd)} · MXN ${cashMoney(mxn)}`;
  const mixed = (usd, mxn) => (usd > 0 && mxn < 0) || (mxn > 0 && usd < 0);
  const changeCurrency = (usd, mxn) => usd > 0 && mxn < 0 ? "MXN" : mxn > 0 && usd < 0 ? "USD" : null;

  /* ---- messages and save state -------------------------------------------- */

  function message(text, tone = "danger") {
    cashNotice($("#er-message"), text, tone, { fade: tone === "success" });
  }

  function refreshSaveState() {
    const entries = [...saves.values()];
    const failed = entries.some(entry => entry.failed);
    const busy = entries.some(entry => entry.timer || entry.running) || noteTimer;
    $("#er-save-state").textContent = failed ? t("Changes not saved")
      : busy ? t("Saving…") : view ? t("All changes saved") : "";
    $("#er-save-state").classList.toggle("er-save-error", failed);
    $("#er-retry").hidden = !failed;
  }

  /* ---- loading and rendering ------------------------------------------------ */

  const rowById = id => view.rows.find(row => row.id === id);
  const editable = row => view.can_edit && !row.posted;
  const pendingRows = () => view.rows.filter(row => row.status !== "approved");

  function accept(next) {
    view = next;
    for (const id of [...selected]) if (!rowById(id)) selected.delete(id);
    if (openId && !rowById(openId)) openId = null;
  }

  async function load() {
    accept(await api("/api/report"));
    render();
  }

  function statusLabel() {
    if (view.read_only) return t(cashLabels.status(view.voucher.status));
    if (view.status === "submitted") return t("Submitted");
    if (view.status === "approved" && !pendingRows().length) return t("Approved");
    return t("Draft");
  }

  function render() {
    $("#er-party").textContent = `${view.voucher.recipient} · ${view.voucher.purpose}`;
    $("#er-status").textContent = statusLabel();
    $("#er-review").hidden = !view.review.summary;
    $("#er-review").textContent = view.review.summary
      ? `${t("Corrections requested:")} ${view.review.summary}` : "";
    renderSummary();
    renderRows();
    renderActions();
    renderActivity();
    if (!document.activeElement?.matches("#er-note")) $("#er-note").value = view.note;
    $("#er-note").disabled = !view.can_edit;
    $("#er-note-box").open ||= Boolean(view.note);
    if (admin) renderAdmin();
    if (i18n) i18n.apply();
    refreshSaveState();
  }

  function renderSummary() {
    const received = view.activity.filter(item => ["signout", "addendum"].includes(item.kind)
      && (item.usd_change_cents > 0 || item.mxn_change_centavos > 0));
    $("#er-received").textContent = pair(
      received.reduce((sum, item) => sum + item.usd_change_cents, 0) / 100,
      received.reduce((sum, item) => sum + item.mxn_change_centavos, 0) / 100);
    const { usd, mxn, balanced, implied_exchange: exchange } = view.balance;
    $("#er-balance").textContent = pair(usd, mxn);
    const help = $("#er-balance-help");
    const u = cents(usd), m = cents(mxn);
    const hasKind = kind => view.rows.some(row => row.kind === kind && !row.posted);
    let text = "", action = null;
    if (balanced) text = t("Everything is accounted for.");
    else if (exchange) text = t("You spent more pesos than you received and have dollars left. Your administrator will settle the exchange.");
    else if (u >= 0 && m >= 0) {
      text = t("Cash left over goes back to the safe.");
      if (!hasKind("cash_return")) action = { kind: "cash_return", label: t("Add cash return"), usd: u, mxn: m };
    } else if (u <= 0 && m <= 0) {
      text = t("You spent more than you received.");
      if (!hasKind("reimbursement")) action = { kind: "reimbursement", label: t("Request reimbursement"), usd: -u, mxn: -m };
    } else text = t("Explain the difference below.");
    help.innerHTML = `<span>${esc(text)}</span>` + (action && view.can_edit
      ? ` <button type="button" class="text-button" id="er-balance-action">${esc(action.label)}</button>` : "");
    help.querySelector("#er-balance-action")?.addEventListener("click", () => addRow(action.kind, {
      usd: decimal(action.usd), mxn: decimal(action.mxn) }));
  }

  function rowTitle(row) {
    if (row.kind === "cash_return") return t("Cash returned");
    if (row.kind === "reimbursement") return t("Reimbursement requested");
    return [row.vendor, row.purpose].filter(Boolean).join(" · ") || t("New receipt");
  }

  function rowFlags(row) {
    if (row.posted) return [`<span class="status-pill success">${esc(t("Approved"))}</span>`];
    const flags = [];
    if (row.admin_note) flags.push(`<span class="status-pill warning">${esc(t("Needs correction"))}</span>`);
    if (row.kind !== "expense") return flags;
    if (!row.document && !row.no_receipt_reason) flags.push(`<span class="er-flag">${esc(t("No receipt"))}</span>`);
    const lines = row.splits.length ? row.splits : [row];
    if (lines.some(line => !line.class_code || !line.expense_account)) {
      flags.push(`<span class="er-flag">${esc(t("Needs codes"))}</span>`);
    }
    return flags;
  }

  function codeText(row) {
    if (row.kind !== "expense") return "";
    if (row.splits.length) return t("Split in {count}", { count: row.splits.length });
    const name = (list, key, value) => list.find(item => item[key] === value)?.name || value;
    return [row.class_code && name(view.options.classes, "class_code", row.class_code),
      row.expense_account && name(view.options.expense_accounts, "account_id", row.expense_account)]
      .filter(Boolean).join(" · ");
  }

  function summaryHTML(row) {
    const selectable = editable(row) && row.kind === "expense";
    const thumb = row.document
      ? `<img src="${esc(row.document.thumbnail_url)}" alt="" loading="lazy">`
      : `<span class="er-thumb-empty" aria-hidden="true">${row.kind === "expense" ? "—" : "↩"}</span>`;
    return `<div class="er-row-summary">
      <span class="er-check">${selectable ? `<input type="checkbox" data-select
        aria-label="${esc(t("Select row"))}" ${selected.has(row.id) ? "checked" : ""}>` : ""}</span>
      <button type="button" class="er-row-open" aria-expanded="${openId === row.id}">
        <span class="er-thumb">${thumb}</span>
        <span class="er-row-text"><strong>${esc(rowTitle(row))}</strong>
          <small>${esc([row.date, codeText(row)].filter(Boolean).join(" · "))}</small></span>
        <span class="er-row-amounts"><span>USD ${esc(cashMoney(row.usd))}</span>
          <span>MXN ${esc(cashMoney(row.mxn))}</span></span>
        <span class="er-row-flags">${rowFlags(row).join("")}</span>
      </button></div>`;
  }

  function renderRows() {
    const list = $("#er-rows");
    list.innerHTML = view.rows.map(row => `<li class="er-row${openId === row.id ? " open" : ""}"
      data-row="${esc(row.id)}">${summaryHTML(row)}${openId === row.id ? editorHTML(row) : ""}</li>`).join("");
    $("#er-empty").hidden = view.rows.length > 0;
    $("#er-add").hidden = !view.can_edit;
    const selectable = view.rows.filter(row => editable(row) && row.kind === "expense");
    root.querySelector(".er-select-all").hidden = selectable.length < 2;
    $("#er-select-all").checked = selectable.length > 0 && selectable.every(row => selected.has(row.id));
    renderBulk();
    if (openId) wireEditor(openId);
  }

  function refreshRowSummary(id) {
    const item = root.querySelector(`.er-row[data-row="${CSS.escape(id)}"]`);
    const row = rowById(id);
    if (!item || !row) return;
    item.querySelector(".er-row-summary").outerHTML = summaryHTML(row);
  }

  /* ---- row editor ------------------------------------------------------------ */

  function options(list, key, selectedValue) {
    return `<option value="">${esc(t("Choose…"))}</option>` + list.map(item =>
      `<option value="${esc(item[key])}" ${item[key] === selectedValue ? "selected" : ""}>
        ${esc(item.name)} · ${esc(item[key])}</option>`).join("");
  }

  function codeFields(line, disabled) {
    return `<label><span>${esc(t("Class code"))}</span><select name="class_code" ${disabled}>
        ${options(view.options.classes, "class_code", line.class_code)}</select></label>
      <label><span>${esc(t("Account code"))}</span><select name="expense_account" ${disabled}>
        ${options(view.options.expense_accounts, "account_id", line.expense_account)}</select></label>`;
  }

  function receiptHTML(row, disabled) {
    if (row.document) {
      return `<div class="er-receipt">
        <button type="button" class="er-receipt-thumb" data-view><img src="${esc(row.document.thumbnail_url)}" alt="">
          <span>${esc(t("View receipt"))}</span></button>
        ${disabled ? "" : `<span class="er-receipt-tools">
          <button type="button" class="text-button" data-upload="append">${esc(t("Add page"))}</button>
          <button type="button" class="text-button" data-upload="replace">${esc(t("Replace"))}</button>
          <button type="button" class="text-button danger" data-remove-receipt>${esc(t("Remove"))}</button></span>`}
      </div>`;
    }
    return `<div class="er-receipt er-receipt-missing">
      ${disabled ? `<span class="muted">${esc(t("No receipt"))}</span>` : `<span class="er-receipt-tools">
        <button type="button" class="btn" data-upload="new" data-source="camera">${esc(t("Take photo"))}</button>
        <button type="button" class="btn ghost" data-upload="new" data-source="files">${esc(t("Choose file"))}</button></span>`}
      <label class="er-no-receipt"><span>${esc(t("No receipt? Explain why"))}</span>
        <input name="no_receipt_reason" value="${esc(row.no_receipt_reason)}" ${disabled}></label>
    </div>`;
  }

  function splitHTML(row, disabled) {
    const usd = cents(row.usd), mxn = cents(row.mxn);
    const showUsd = usd !== 0 || mxn === 0, showMxn = mxn !== 0 || usd === 0;
    const isMixed = mixed(usd, mxn);
    return `<div class="er-split">
      <p class="er-split-heading"><strong>${esc(t("Split receipt"))}</strong>
        <span class="er-split-remaining" data-split-remaining></span></p>
      ${row.splits.map((split, index) => `<div class="er-split-part" data-split="${esc(split.id)}">
        ${showUsd ? `<label><span>USD</span><input name="split_usd" inputmode="decimal" value="${esc(inputValue(split.usd))}" ${disabled}></label>` : ""}
        ${showMxn ? `<label><span>MXN</span><input name="split_mxn" inputmode="decimal" value="${esc(inputValue(split.mxn))}" ${disabled}></label>` : ""}
        ${isMixed ? `<label><span>${esc(t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) }))}</span>
          <input name="split_receipt_total" inputmode="decimal" value="${esc(inputValue(split.receipt_total))}" ${disabled}></label>` : ""}
        <label class="er-split-purpose"><span>${esc(t("What was it for?"))}</span>
          <input name="split_purpose" value="${esc(split.purpose)}" ${disabled}></label>
        ${codeFields(split, disabled)}
        ${disabled || row.splits.length <= 2 ? "" : `<button type="button" class="text-button danger"
          data-remove-part="${index}">${esc(t("Remove part"))}</button>`}
      </div>`).join("")}
      ${disabled ? "" : `<p class="er-split-tools"><button type="button" class="text-button" data-add-part>${esc(t("Add part"))}</button>
        <button type="button" class="text-button" data-unsplit>${esc(t("Undo split"))}</button></p>`}
    </div>`;
  }

  function editorHTML(row) {
    const disabled = editable(row) ? "" : "disabled";
    const usd = cents(row.usd), mxn = cents(row.mxn);
    const kindSelect = `<label class="er-kind"><span>${esc(t("Row type"))}</span>
      <select name="kind" ${disabled}>${["expense", "cash_return", "reimbursement"].map(kind =>
        `<option value="${kind}" ${row.kind === kind ? "selected" : ""}>${esc(t({
          expense: "Receipt", cash_return: "Cash returned", reimbursement: "Reimbursement requested",
        }[kind]))}</option>`).join("")}</select></label>`;
    const amounts = `<label><span>USD</span><input name="usd" inputmode="decimal"
        value="${esc(inputValue(row.usd))}" ${disabled}></label>
      <label><span>MXN</span><input name="mxn" inputmode="decimal"
        value="${esc(inputValue(row.mxn))}" ${disabled}></label>`;
    const correction = row.admin_note
      ? `<p class="er-correction">${esc(t("Correction requested:"))} ${esc(row.admin_note)}</p>` : "";
    const adminNote = admin && view.status === "submitted" && !row.posted
      ? `<label class="full"><span>Correction note for the recipient</span>
          <textarea data-correction rows="2">${esc(correctionNotes[row.id] || "")}</textarea></label>` : "";
    const remove = disabled ? "" : `<button type="button" class="text-button danger" data-delete>${esc(t("Delete row"))}</button>`;
    if (row.kind !== "expense") {
      return `<div class="er-editor">${correction}<div class="form-grid two">${kindSelect}<span></span>${amounts}
        <label class="full"><span>${esc(t("Note"))}</span><input name="purpose" value="${esc(row.purpose)}" ${disabled}></label>
        ${adminNote}</div><p class="er-editor-tools">${remove}</p></div>`;
    }
    return `<div class="er-editor">${correction}
      ${receiptHTML(row, disabled)}
      <div class="form-grid two">
        <label><span>${esc(t("Date"))}</span><input type="date" name="date" value="${esc(row.date)}" ${disabled}></label>
        <label><span>${esc(t("Vendor"))}</span><input name="vendor" value="${esc(row.vendor)}" ${disabled}></label>
        <label class="full"><span>${esc(t("What was it for?"))}</span>
          <input name="purpose" value="${esc(row.purpose)}" ${disabled}></label>
        ${amounts}
        <p class="field-help full">${esc(t("Use a negative amount for change received."))}</p>
        <label class="er-receipt-total" ${mixed(usd, mxn) ? "" : "hidden"}><span data-receipt-total-label>${esc(
          t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) || "MXN" }))}</span>
          <input name="receipt_total" inputmode="decimal" value="${esc(inputValue(row.receipt_total))}" ${disabled}></label>
        ${row.splits.length ? "" : codeFields(row, disabled)}
        ${adminNote}
      </div>
      ${row.splits.length ? splitHTML(row, disabled) : ""}
      <p class="er-editor-tools">${kindSelect}
        ${!disabled && !row.splits.length ? `<button type="button" class="text-button" data-split-start>${esc(t("Split receipt"))}</button>` : ""}
        ${remove}</p>
    </div>`;
  }

  function editorOf(id) {
    return root.querySelector(`.er-row[data-row="${CSS.escape(id)}"] .er-editor`);
  }

  // Current values in an open editor, in the API's shape.
  function collect(id) {
    const editor = editorOf(id);
    const value = name => editor.querySelector(`[name="${name}"]`)?.value ?? "";
    const fields = { kind: value("kind"), usd: value("usd"), mxn: value("mxn"), purpose: value("purpose") };
    if (fields.kind !== "expense") return fields;
    Object.assign(fields, { date: value("date"), vendor: value("vendor"),
      receipt_total: value("receipt_total"), no_receipt_reason: value("no_receipt_reason") });
    const parts = [...editor.querySelectorAll("[data-split]")];
    if (parts.length) {
      fields.splits = parts.map(part => {
        const get = name => part.querySelector(`[name="${name}"]`)?.value ?? "";
        return { id: part.dataset.split, usd: get("split_usd"), mxn: get("split_mxn"),
          receipt_total: get("split_receipt_total"), purpose: get("split_purpose"),
          class_code: get("class_code") || null, expense_account: get("expense_account") || null };
      });
    } else {
      Object.assign(fields, { class_code: value("class_code") || null,
        expense_account: value("expense_account") || null, splits: [] });
    }
    return fields;
  }

  function invalidAmount(fields) {
    const amounts = [fields.usd, fields.mxn, fields.receipt_total,
      ...(fields.splits || []).flatMap(split => [split.usd, split.mxn, split.receipt_total])];
    return amounts.some(amount => Number.isNaN(cents(amount)));
  }

  // Show or hide what depends on the amounts without re-rendering the inputs.
  function refreshDerived(id) {
    const editor = editorOf(id);
    if (!editor) return;
    const usd = cents(editor.querySelector('[name="usd"]').value);
    const mxn = cents(editor.querySelector('[name="mxn"]').value);
    const total = editor.querySelector(".er-receipt-total");
    if (total) {
      total.hidden = !mixed(usd, mxn);
      total.querySelector("[data-receipt-total-label]").textContent =
        t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) || "MXN" });
    }
    const remaining = editor.querySelector("[data-split-remaining]");
    if (remaining) {
      const parts = collect(id).splits;
      const left = [usd - parts.reduce((sum, part) => sum + (cents(part.usd) || 0), 0),
        mxn - parts.reduce((sum, part) => sum + (cents(part.mxn) || 0), 0)];
      remaining.textContent = left[0] || left[1]
        ? t("Still to assign: {amount}", { amount: pair(left[0] / 100, left[1] / 100) })
        : t("The parts add up.");
    }
  }

  function wireEditor(id) {
    const editor = editorOf(id);
    if (!editor) return;
    refreshDerived(id);
    editor.addEventListener("input", event => {
      if (event.target.matches("[data-correction]")) {
        correctionNotes[id] = event.target.value;
        return;
      }
      refreshDerived(id);
      schedule(id);
    });
    editor.addEventListener("change", event => {
      if (event.target.matches('select[name="kind"]')) flush(id).then(renderRows, () => {});
      else if (event.target.matches("select")) schedule(id, 0);
    });
    editor.addEventListener("click", async event => {
      const target = event.target.closest("button");
      if (!target) return;
      const row = rowById(id);
      if (target.matches("[data-view]")) openViewer(row.document);
      else if (target.dataset.upload) chooseFiles(id, target.dataset.upload, target.dataset.source);
      else if (target.matches("[data-remove-receipt]")) {
        if (!window.confirm(t("Remove this receipt from the row?"))) return;
        await saveNow(id, { document_id: null });
      } else if (target.matches("[data-split-start]")) {
        await saveNow(id, { ...collect(id), splits: [
          { usd: row.usd, mxn: row.mxn, receipt_total: row.receipt_total, purpose: row.purpose,
            class_code: row.class_code, expense_account: row.expense_account },
          { usd: "", mxn: "", receipt_total: "", purpose: "",
            class_code: row.class_code, expense_account: row.expense_account }] });
      } else if (target.matches("[data-add-part]")) {
        const fields = collect(id);
        await saveNow(id, { ...fields, splits: [...fields.splits, { usd: "", mxn: "", purpose: "" }] });
      } else if (target.dataset.removePart) {
        const fields = collect(id);
        fields.splits.splice(Number(target.dataset.removePart), 1);
        await saveNow(id, fields);
      } else if (target.matches("[data-unsplit]")) {
        const first = collect(id).splits[0] || {};
        await saveNow(id, { ...collect(id), splits: [],
          class_code: first.class_code || null, expense_account: first.expense_account || null });
      } else if (target.matches("[data-delete]")) {
        if (!window.confirm(t("Delete this row?"))) return;
        await deleteRow(id);
      }
    });
  }

  /* ---- saving ----------------------------------------------------------------- */

  function schedule(id, delay = 650) {
    const entry = saves.get(id) || {};
    saves.set(id, entry);
    window.clearTimeout(entry.timer);
    entry.timer = window.setTimeout(() => { entry.timer = null; flush(id).catch(() => {}); }, delay);
    refreshSaveState();
  }

  // Save a row's editor. A failed save keeps its submission so a retry cannot
  // apply the same change twice, then saves again if the editor moved on.
  async function flush(id, fields = null) {
    const entry = saves.get(id) || {};
    saves.set(id, entry);
    window.clearTimeout(entry.timer);
    entry.timer = null;
    if (entry.running) {
      await entry.running.catch(() => {});
      return flush(id, fields);
    }
    const row = rowById(id);
    if (!row) return;
    const current = fields || (editorOf(id) ? collect(id) : null);
    if (!current && !entry.pending) return;
    if (current && invalidAmount(current)) {
      entry.failed = false;
      message(t("Enter amounts with at most two decimal places."));
      refreshSaveState();
      return;
    }
    const body = entry.pending || { submission_id: cashUUID(), rev: row.rev, ...current };
    entry.pending = body;
    entry.running = api(`/api/rows/${encodeURIComponent(id)}`, { method: "PATCH", body });
    refreshSaveState();
    try {
      const next = await entry.running;
      entry.pending = null;
      entry.failed = false;
      accept(next);
      afterSave(id);
      if (!fields && editorOf(id)) {
        const now = collect(id);
        if (JSON.stringify(now) !== JSON.stringify({ ...now, ...pick(body, Object.keys(now)) })) schedule(id, 0);
      }
    } catch (error) {
      if (error.status === 409) {
        entry.pending = null;
        entry.failed = false;
        message(t("This row changed on another screen. Showing the latest."), "warning");
        await load();
      } else if (error.status && error.status < 500) {
        entry.pending = null;
        entry.failed = false;
        message(errorText(error));
      } else {
        entry.failed = true;
        message(errorText(error));
      }
      throw error;
    } finally {
      entry.running = null;
      refreshSaveState();
    }
  }

  const pick = (object, keys) => Object.fromEntries(keys.filter(key => key in object)
    .map(key => [key, object[key]]));

  // Save the open row's pending edits before its editor is re-rendered away.
  // flush() reads the inputs synchronously, so this is safe to call just
  // before a render.
  function leaveOpenRow() {
    if (openId && saves.get(openId)?.timer) flush(openId).catch(() => {});
  }

  // Structural changes (receipt, split, type) re-render the open editor.
  async function saveNow(id, fields) {
    try {
      await flush(id, fields);
      renderRows();
    } catch (_) { /* The message is already shown. */ }
  }

  function afterSave(id) {
    refreshRowSummary(id);
    renderSummary();
    renderActions();
    renderBulk();
    if (admin) renderAdmin();
    if ($("#er-message").classList.contains("danger")) message("");
  }

  async function addRow(kind = "expense", fields = {}) {
    leaveOpenRow();
    try {
      const body = { submission_id: cashUUID(), kind, ...fields };
      if (kind === "expense") body.date = body.date || cashToday();
      accept(await api("/api/rows", { method: "POST", body }));
      openId = view.rows[view.rows.length - 1].id;
      render();
      editorOf(openId)?.querySelector("input:not([type=checkbox]), select")?.focus();
    } catch (error) { message(errorText(error)); }
  }

  async function deleteRow(id) {
    try {
      await flushAll();
      const row = rowById(id);
      accept(await api(`/api/rows/${encodeURIComponent(id)}`, { method: "DELETE",
        body: { submission_id: cashUUID(), rev: row.rev } }));
      saves.delete(id);
      render();
    } catch (error) { message(errorText(error)); }
  }

  async function flushAll() {
    for (const id of [...saves.keys()]) {
      const entry = saves.get(id);
      if (entry.timer || entry.pending || entry.running) await flush(id);
    }
    if (noteTimer) await saveNote();
  }

  async function saveNote() {
    window.clearTimeout(noteTimer);
    noteTimer = null;
    accept(await api("/api/report", { method: "PATCH",
      body: { submission_id: cashUUID(), note: $("#er-note").value } }));
    refreshSaveState();
    renderActions();
  }

  /* ---- receipts ------------------------------------------------------------------ */

  function chooseFiles(rowId, mode, source = "files") {
    upload = { rowId, mode };
    const input = source === "camera" ? $("#er-camera") : $("#er-files");
    input.value = "";
    input.click();
  }

  async function receiveFiles(input) {
    const target = upload;
    upload = null;
    if (!target || !input.files.length) return;
    const row = rowById(target.rowId);
    $("#er-save-state").textContent = t("Uploading receipt…");
    try {
      await flush(target.rowId).catch(() => {});
      const form = new FormData();
      if (target.mode === "append" && row.document) {
        const existing = await fetch(row.document.stream_url);
        if (!existing.ok) throw new Error(t("Unable to open receipt"));
        form.append("files", await existing.blob(), "existing-pages.pdf");
      }
      for (const file of await cashPrepareUploads(input.files)) form.append("files", file, file.name);
      const uploaded = await api("/documents", { method: "POST", formData: form });
      await flush(target.rowId, { document_id: uploaded.documents[0].document_id });
      renderRows();
    } catch (error) {
      message(errorText(error));
    } finally {
      refreshSaveState();
    }
  }

  function openViewer(document) {
    const dialog = $("#er-viewer");
    $("#er-viewer-link").href = document.stream_url;
    dialog.querySelector("iframe").src = document.stream_url;
    dialog.showModal();
  }

  /* ---- selection and bulk codes ------------------------------------------------ */

  function renderBulk() {
    const count = selected.size;
    $("#er-bulk").hidden = !count || !view.can_edit;
    if (!count) return;
    $("#er-bulk-count").textContent = t(count === 1 ? "{count} row selected" : "{count} rows selected", { count });
    for (const [id, list, key] of [["#er-bulk-class", view.options.classes, "class_code"],
      ["#er-bulk-account", view.options.expense_accounts, "account_id"]]) {
      const select = $(id);
      if (select.options.length !== list.length + 1) {
        select.innerHTML = `<option value="">${esc(t("Leave unchanged"))}</option>` + list.map(item =>
          `<option value="${esc(item[key])}">${esc(item.name)} · ${esc(item[key])}</option>`).join("");
      }
    }
  }

  async function applyBulk() {
    const body = { submission_id: cashUUID(), row_ids: [...selected] };
    if ($("#er-bulk-class").value) body.class_code = $("#er-bulk-class").value;
    if ($("#er-bulk-account").value) body.expense_account = $("#er-bulk-account").value;
    if (!body.class_code && !body.expense_account) {
      message(t("Choose a class code or an account code to apply."), "warning");
      return;
    }
    try {
      await flushAll();
      accept(await api("/api/rows/codes", { method: "POST", body }));
      selected.clear();
      render();
      message(t("Codes applied."), "success");
    } catch (error) { message(errorText(error)); }
  }

  /* ---- report actions -------------------------------------------------------------- */

  function renderActions() {
    const pending = pendingRows().length;
    const submitted = view.status === "submitted";
    const show = (id, visible) => { const button = $(id); if (button) button.hidden = !visible; };
    show("#er-submit", !admin && view.can_edit && !submitted && pending > 0);
    $("#er-submit").textContent = t(view.review.summary ? "Submit corrections" : "Submit report");
    show("#er-reopen", !admin && submitted && !view.read_only);
    show("#er-approve", admin && !view.read_only && pending > 0);
    show("#er-corrections", admin && !view.read_only && submitted);
  }

  async function step(name, body = {}) {
    try {
      await flushAll();
      accept(await api(`/api/report/${name}`, { method: "POST",
        body: { submission_id: cashUUID(), ...body } }));
      render();
      return true;
    } catch (error) {
      message(errorText(error));
      return false;
    }
  }

  /* ---- administrator: settlement, paperwork, addenda ------------------------------ */

  function renderAdmin() {
    const paperwork = view.paperwork;
    const needs = paperwork.reasons.map(cashLabels.reason);
    $("#er-needs").textContent = needs.length ? `Needs: ${needs.join(" · ")}` : "Nothing is needed.";
    $("#er-addenda").innerHTML = paperwork.addenda.map(item => `<li>
      <span><strong>${esc(item.addendum_id)}</strong> · ${esc(item.kind === "return" ? "Cash return" : "Additional cash")}
        · ${esc(pair(item.usd_cents / 100, item.mxn_centavos / 100))}</span>
      <span><a href="${esc(item.print_url)}" target="_blank" rel="noopener">Print</a>
        ${item.status === "pending" ? ` · <a href="/review?item=addendum:${esc(item.addendum_id)}">File signed copy</a>` : ""}</span>
    </li>`).join("");
    const voucherLine = paperwork.has_voucher ? "" : `<li><span>Signed voucher not filed yet</span>
      <a href="${esc(paperwork.voucher_review_url)}">File in Review</a></li>`;
    $("#er-documents").innerHTML = voucherLine + paperwork.documents.map(document => `<li>
      <a href="${esc(document.review_url || document.stream_url)}" ${document.review_url ? "" : 'target="_blank" rel="noopener"'}>
        ${esc(document.display_name || document.doc_type || "Document")}</a>
      <small>${esc(document.doc_type || "document")}</small></li>`).join("");
    $("#er-addendum").hidden = view.read_only;
    renderSettlement();
  }

  let settlementPreview = null;
  let settlementSubmission = null;
  let settlementPrefill = null;

  function renderSettlement() {
    const panel = $("#er-settlement");
    const closeout = view.paperwork.closeout;
    const ready = view.status === "approved" && !pendingRows().length && !view.read_only;
    panel.hidden = !(ready || closeout);
    const form = $("#er-settlement-form");
    form.hidden = view.settlement_confirmed || !ready;
    $("#er-settlement-state").textContent = view.settlement_confirmed
      ? `Settlement confirmed ${closeout.confirmed_at}. ${view.read_only ? "All cash and paperwork are reconciled." : "File the signed paperwork to finish."}`
      : closeout ? "The report changed after settlement. Review and confirm it again."
      : "Confirm the cash actually returned or reimbursed.";
    const proposed = JSON.stringify(view.paperwork.proposed_settlement);
    if (form.hidden || settlementPrefill === proposed) return;
    settlementPrefill = proposed;
    for (const [name, value] of Object.entries(view.paperwork.proposed_settlement)) {
      form.elements[name].value = inputValue(value);
    }
    if (!form.elements.date.value) form.elements.date.value = cashToday();
    resetSettlementPreview();
  }

  function resetSettlementPreview() {
    settlementPreview = null;
    settlementSubmission = null;
    const form = $("#er-settlement-form");
    form.elements.confirmed.checked = false;
    $("#er-settlement-preview").hidden = true;
    $("#er-settlement-confirm").hidden = true;
  }

  async function settle(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const output = $("#er-settlement-message");
    const values = name => form.elements[name].value || "0";
    const body = { date: form.elements.date.value, return_usd: values("return_usd"),
      return_mxn: values("return_mxn"), reimburse_usd: values("reimburse_usd"),
      reimburse_mxn: values("reimburse_mxn"), resolution_note: form.elements.resolution_note.value };
    output.textContent = "";
    if (event.submitter?.value !== "confirm") {
      try {
        settlementPreview = await cashApi(`/api/signouts/${vid}/closeout/preview`, { method: "POST", body });
        settlementSubmission = cashUUID();
        const p = settlementPreview;
        $("#er-settlement-preview").innerHTML = `<p>Return to the safe: <strong>${esc(pair(p.return_usd_cents / 100, p.return_mxn_centavos / 100))}</strong>.</p>
          ${p.requires_reimbursement_addendum ? `<p>Reimburse the recipient: <strong>${esc(pair(p.reimburse_usd_cents / 100, p.reimburse_mxn_centavos / 100))}</strong>.</p>` : ""}
          ${p.rate_mxn_per_usd ? `<p>Currency exchange: USD ${esc(cashMinorMoney(p.exchange_usd_cents))} for MXN ${esc(cashMinorMoney(p.exchange_mxn_centavos))}
            at <strong>${esc(Number(p.rate_mxn_per_usd).toLocaleString("en-US", { minimumFractionDigits: 6, maximumFractionDigits: 6 }))} MXN/USD</strong>.</p>` : ""}
          <p>Returned MXN is valued at USD ${esc(cashMoney(p.returned_mxn_basis_usd))}. The signout will hold no cash afterward.</p>
          ${p.requires_return_addendum || p.requires_reimbursement_addendum ? "<p>The addenda open for printing. File the signed copies in Review to close the signout.</p>" : ""}`;
        $("#er-settlement-preview").hidden = false;
        $("#er-settlement-confirm").hidden = false;
      } catch (error) { output.textContent = error.message; }
      return;
    }
    if (!settlementPreview || !form.elements.confirmed.checked) {
      output.textContent = "Review the settlement and confirm that all cash is accounted for.";
      return;
    }
    const printing = settlementPreview.requires_return_addendum || settlementPreview.requires_reimbursement_addendum;
    const tab = printing ? window.open("about:blank", "_blank") : null;
    if (tab) tab.opener = null;
    try {
      const result = await cashApi(`/api/signouts/${vid}/closeout`, { method: "POST", body: {
        ...body, submission_id: settlementSubmission, preview_token: settlementPreview.preview_token,
        confirmed: true, approver_name: form.elements.approver_name.value } });
      if (tab && result.print_urls?.length > 1) {
        tab.document.title = "Settlement addenda";
        tab.document.body.innerHTML = `<h1>Print and sign each addendum</h1>${result.print_urls.map((url, index) =>
          `<p><a href="${esc(url)}" target="_blank" rel="noopener">Print addendum ${index + 1}</a></p>`).join("")}`;
      } else if (tab && result.print_url) tab.location.replace(result.print_url);
      else if (tab) tab.close();
      resetSettlementPreview();
      await load();
    } catch (error) {
      if (tab) tab.close();
      output.textContent = error.message;
    }
  }

  async function saveAddendum(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const output = form.querySelector("output");
    const buttons = [...form.querySelectorAll('button[type="submit"]')];
    const printing = event.submitter?.value === "print";
    const tab = printing ? window.open("about:blank", "_blank") : null;
    if (tab) tab.opener = null;
    buttons.forEach(button => { button.disabled = true; });
    output.textContent = "Saving…";
    try {
      const values = new FormData(form);
      const result = await cashApi("/api/addenda/prepare", { method: "POST", body: {
        submission_id: cashUUID(), voucher_id: vid, kind: values.get("kind"),
        date: values.get("date"), reason: values.get("reason") || "",
        approver_name: values.get("approver_name"),
        usd: values.get("usd") || "0", mxn: values.get("mxn") || "0" } });
      if (tab) tab.location.replace(result.print_url);
      form.elements.usd.value = form.elements.mxn.value = form.elements.reason.value = "";
      output.textContent = `Saved ${result.addendum_id}.`;
      await load();
    } catch (error) {
      if (tab) tab.close();
      output.textContent = error.message;
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  }

  function renderActivity() {
    $("#er-activity").innerHTML = view.activity.map(item => `<li>
      <span>${esc(item.date)} · ${esc(t(item.label))}${item.detail ? ` · ${esc(item.detail)}` : ""}</span>
      <span class="er-activity-amount">${esc(pair(item.usd_change_cents / 100, item.mxn_change_centavos / 100))}</span>
    </li>`).join("") || `<li class="muted">${esc(t("No cash activity yet."))}</li>`;
  }

  /* ---- events -------------------------------------------------------------------- */

  $("#er-rows").addEventListener("click", event => {
    if (event.target.matches("[data-select]")) {
      const id = event.target.closest(".er-row").dataset.row;
      if (event.target.checked) selected.add(id); else selected.delete(id);
      $("#er-select-all").checked = view.rows.filter(row => editable(row) && row.kind === "expense")
        .every(row => selected.has(row.id));
      renderBulk();
      return;
    }
    const toggle = event.target.closest(".er-row-open");
    if (!toggle) return;
    const id = toggle.closest(".er-row").dataset.row;
    leaveOpenRow();
    openId = openId === id ? null : id;
    renderRows();
  });
  $("#er-select-all").addEventListener("change", event => {
    leaveOpenRow();
    selected.clear();
    if (event.target.checked) {
      view.rows.filter(row => editable(row) && row.kind === "expense").forEach(row => selected.add(row.id));
    }
    renderRows();
  });
  $("#er-bulk-apply").addEventListener("click", applyBulk);
  $("#er-add").addEventListener("click", () => addRow());
  $("#er-camera").addEventListener("change", event => receiveFiles(event.target));
  $("#er-files").addEventListener("change", event => receiveFiles(event.target));
  $("#er-viewer-close").addEventListener("click", () => $("#er-viewer").close());
  $("#er-retry").addEventListener("click", () => flushAll().then(() => renderRows()).catch(() => {}));
  $("#er-note").addEventListener("input", () => {
    window.clearTimeout(noteTimer);
    noteTimer = window.setTimeout(() => saveNote().catch(error => message(errorText(error))), 650);
    refreshSaveState();
  });
  $("#er-submit").addEventListener("click", () => step("submit"));
  $("#er-reopen").addEventListener("click", () => step("reopen"));
  if (admin) {
    $("#er-approve").addEventListener("click", () => {
      const count = pendingRows().length;
      if (window.confirm(`Approve and post ${count} row${count === 1 ? "" : "s"} to the ledger?`)) step("approve");
    });
    $("#er-corrections").addEventListener("click", () => {
      $("#er-corrections-form").hidden = false;
      $("#er-corrections-summary").focus();
    });
    $("#er-corrections-cancel").addEventListener("click", () => { $("#er-corrections-form").hidden = true; });
    $("#er-corrections-send").addEventListener("click", async () => {
      const notes = Object.fromEntries(Object.entries(correctionNotes).filter(([, note]) => note.trim()));
      if (await step("corrections", { summary: $("#er-corrections-summary").value, row_notes: notes })) {
        $("#er-corrections-form").hidden = true;
        $("#er-corrections-summary").value = "";
        Object.keys(correctionNotes).forEach(key => delete correctionNotes[key]);
      }
    });
    $("#er-settlement-form").addEventListener("submit", settle);
    $("#er-settlement-form").addEventListener("input", event => {
      if (event.target.name !== "confirmed") resetSettlementPreview();
    });
    $("#er-addendum-form").addEventListener("submit", saveAddendum);
    $("#er-addendum-form").elements.date.value = cashToday();
  }
  document.addEventListener("signee-language-change", () => {
    if (!view) return;
    leaveOpenRow();
    render();
  });
  window.addEventListener("beforeunload", event => {
    const unsaved = noteTimer || [...saves.values()].some(entry =>
      entry.timer || entry.running || entry.pending);
    if (unsaved) { event.preventDefault(); event.returnValue = ""; }
  });

  load().catch(error => message(errorText(error)));
})();
