/* Expense report rows share a side tray. Cash rows show the direction of
   movement; purchase logs contain compact purchase subrows. Only expense
   rows can be changed by signees. Approval settles calculated cash amounts. */
(() => {
  const root = document.getElementById("expense-report");
  if (!root) return;
  const vid = root.dataset.voucher;
  const admin = root.dataset.admin === "true";
  const $ = selector => root.querySelector(selector);
  const esc = cashEsc;
  const t = (key, values = {}) => cashI18n.t(key, values);
  const errorText = error => cashI18n.error({ code: error.code, message: error.message });
  const api = (path, options) => cashApi(`/${vid}${path}`, options);

  let view = null;
  let openId = null;
  const selected = new Set();
  const saves = new Map();            // row id -> pending local values and save state
  const correctionNotes = {};         // admin notes waiting for "Request corrections"
  const noteSave = {};
  const logSave = {};
  const allSaves = () => [...saves.values(), noteSave, logSave];
  let attaching = null;               // {rowId, mode: "new" | "replace" | "document"}
  let cashActual = null;              // cash typed in the administrator's difference panel
  // A deleted row or removed receipt waits a few seconds so it can be undone:
  // {kind: "row" | "receipt", id, toast, stash, commit}. Everything else that
  // saves settles it first, so nothing is applied out of order.
  let deferred = null;
  // Once Undo expires, report actions still wait for the deletion's response.
  let committingDeletion = null;

  /* ---- money --------------------------------------------------------------- */

  const { cents, pair, inputValue } = cashRowFields;
  const decimal = minor => (minor / 100).toFixed(2);
  // "USD 1.00 · MXN 2.00" that only ever breaks between the two currencies.
  const pairHTML = (usd, mxn) => `<span>USD ${esc(cashMoney(usd))}</span><span class="sep"> · </span><span>MXN ${esc(cashMoney(mxn))}</span>`;

  /* ---- messages and save state -------------------------------------------- */

  function message(text, tone = "danger") {
    cashNotice($("#er-message"), text, tone, { fade: tone === "success" });
    cashNotice($("#er-tray-message"), text, tone, { fade: tone === "success" });
  }

  function refreshSaveState() {
    const entries = allSaves();
    const failed = entries.some(entry => entry.failed);
    const busy = Boolean(committingDeletion) || entries.some(hasUnsaved);
    $("#er-save-state").textContent = failed ? t("Changes not saved")
      : busy ? t("Saving…") : view ? t("All changes saved") : "";
    $("#er-save-state").classList.toggle("er-save-error", failed);
    $("#er-retry").hidden = !failed;
    $("#er-tray-save-state").textContent = $("#er-save-state").textContent;
    $("#er-tray-save-state").classList.toggle("er-save-error", failed);
    $("#er-tray-retry").hidden = !failed;
  }

  /* ---- loading and rendering ------------------------------------------------ */

  const rowById = id => view.rows.find(row => row.id === id);
  const editable = row => view.can_edit && !row.posted;
  // Approved receipts keep their amounts but can always take more paper.
  const documentable = row => row.posted && row.kind === "expense" && view.voucher.status !== "void";
  const receiptNumber = row => row.receipt_reference?.split("-").pop() || "";
  // A signout has at most one purchase log, filed on one row.
  const logRow = () => view.rows.find(row => row.document_kind === "log");
  const pendingRows = () => view.rows.filter(row => row.status !== "approved");
  const receiptRows = () => view.rows.filter(row => row.kind === "expense" && row.id !== view.cash_rows.log?.row_id);

  function accept(next) {
    // Requests for different rows/notes can finish out of order. Every report
    // mutation advances this version; an older snapshot must not undo a newer one.
    if (view && next.version < view.version) return;
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
    renderCashDifference();
    renderRows();
    renderActions();
    if (!document.activeElement?.matches("#er-note")) $("#er-note").value = noteSave.draft?.note ?? view.note;
    $("#er-note").disabled = !view.can_edit;
    if (admin) {
      $("#project-options").innerHTML = view.options.projects
        .map(project => `<option value="${esc(project)}"></option>`).join("");
    }
    cashI18n.apply();
    refreshSaveState();
  }

  function renderSummary() {
    const received = view.activity.filter(item => ["signout", "addendum"].includes(item.kind)
      && (cents(item.usd) > 0 || cents(item.mxn) > 0));
    $("#er-received").innerHTML = pairHTML(
      received.reduce((sum, item) => sum + cents(item.usd), 0) / 100,
      received.reduce((sum, item) => sum + cents(item.mxn), 0) / 100);
    const { usd, mxn, balanced, implied_exchange: exchange } = view.cash_rows.balance || view.balance;
    const u = cents(usd), m = cents(mxn);
    const reimbursement = u <= 0 && m <= 0 && (u < 0 || m < 0);
    const label = reimbursement ? "Reimbursement due" : "Remaining";
    for (const selector of ["#er-balance-label", "#er-bar-balance-label"]) {
      $(selector).dataset.i18n = label;
      $(selector).textContent = t(label);
    }
    const amounts = reimbursement ? pairHTML(-Number(usd), -Number(mxn)) : pairHTML(usd, mxn);
    $("#er-balance").innerHTML = amounts;
    $("#er-bar-balance").innerHTML = amounts;
    let text = "";
    if (balanced) text = t("Everything is accounted for.");
    else if (exchange) text = t("You spent more pesos than you received and have dollars left. Your administrator will settle the exchange.");
    else if (u <= 0 && m <= 0) text = t("You spent more than you received.");
    else if (u < 0 || m < 0) text = t("Explain the difference below.");
    $("#er-balance-help").textContent = text;
  }

  function renderCashDifference() {
    const section = $("#er-cash-difference");
    if (!section) return;
    const difference = view.cash_difference;
    section.hidden = !difference;
    if (!difference) {
      cashActual = null;
      $("#er-cash-difference-reason").value = "";
      return;
    }
    const reimbursement = difference.direction === "reimbursement";
    const label = reimbursement ? "Reimbursement due" : "Cash left";
    for (const selector of ["#er-cash-difference-direction"]) {
      $(selector).dataset.i18n = label;
      $(selector).textContent = t(label);
    }
    $("#er-cash-difference-exact").value = cashMoney(difference.exact);
    const input = $("#er-cash-difference-actual");
    if (cashActual === null) {
      cashActual = difference.suggested;
      $("#er-cash-difference-reason").value = "";
    }
    if (document.activeElement !== input) input.value = cashActual;
    $("#er-missing-cash").hidden = !difference.can_missing_cash;
    renderCashDifferenceDelta();
  }

  function renderCashDifferenceDelta() {
    const difference = view.cash_difference;
    if (!difference) return;
    const exact = cents(difference.exact);
    const actual = cents(cashActual ?? "");
    if (Number.isNaN(actual)) {
      $("#er-cash-difference-delta").textContent = "";
      $("#er-cash-rounding").disabled = true;
      $("#er-missing-cash").disabled = true;
      $("#er-cash-difference-reason-row").hidden = true;
      return;
    }
    const delta = difference.direction === "reimbursement" ? actual - exact : exact - actual;
    $("#er-cash-difference-delta").textContent =
      `${delta < 0 ? "−" : ""}MXN ${cashMoney(Math.abs(delta) / 100)}`;
    const whole = actual % 100 === 0;
    $("#er-cash-rounding").disabled = !whole || actual === exact;
    $("#er-missing-cash").disabled = !whole || difference.direction !== "return" || actual >= exact;
    $("#er-cash-difference-reason-row").hidden = actual >= exact;
  }

  async function resolveCashDifference(method) {
    const actual = cents(cashActual ?? "");
    if (Number.isNaN(actual)) {
      message(t("Enter amounts with at most two decimal places."));
      return;
    }
    const body = {
      actual_mxn: (actual / 100).toFixed(2),
      method,
      reason: $("#er-cash-difference-reason").value,
    };
    try {
      await flushAll();
      accept(await cashCommand(`/${vid}/api/report/cash-difference`, body));
      render();
      if (method === "missing_cash") {
        const row = view.rows.filter(item => item.kind === "discrepancy" && !item.posted).at(-1);
        if (row) openSettlement(`lost:${row.id}`);
      }
    } catch (error) {
      message(errorText(error));
    }
  }

  function rowTitle(row) {
    return [row.vendor, row.purpose].filter(Boolean).join(" · ") || t("New receipt");
  }

  function rowFlags(row) {
    if (row.posted) return [`<span class="status-pill success">${esc(t("Approved"))}</span>`];
    const flags = [];
    if (row.admin_note) flags.push(`<span class="status-pill warning">${esc(t("Needs correction"))}</span>`);
    if (!row.document && !row.no_receipt_reason) flags.push(`<span class="er-flag">${esc(t("No receipt"))}</span>`);
    else if (!row.document) flags.push(`<span class="er-flag">${esc(t("Needs statement"))}</span>`);
    const lines = row.splits.length ? row.splits : [row];
    if (lines.some(line => !line.class_code || !line.expense_account)) {
      flags.push(`<span class="er-flag">${esc(t("Needs codes"))}</span>`);
    }
    return flags;
  }

  function summaryHTML(row) {
    const selectable = editable(row);
    const thumb = row.document
      ? `<img src="${esc(row.document.thumbnail_url)}" alt="" loading="lazy">`
      : `<span class="er-thumb-empty" aria-hidden="true">—</span>`;
    return `<div class="er-row-summary">
      <span class="er-check">${selectable ? `<input type="checkbox" data-select
        aria-label="${esc(t("Select row"))}" ${selected.has(row.id) ? "checked" : ""}>` : ""}</span>
      <button type="button" class="er-row-open" aria-haspopup="dialog" aria-controls="er-tray" aria-expanded="${openId === row.id}">
        <span class="er-thumb">${thumb}</span>
        <span class="er-row-text"><strong>${esc(rowTitle(row))}</strong>
          <small>${esc([receiptNumber(row), row.date].filter(Boolean).join(" · "))}</small></span>
        <span class="er-row-amounts"><span>USD ${esc(cashMoney(row.usd))}</span>
          <span>MXN ${esc(cashMoney(row.mxn))}</span></span>
        <span class="er-row-flags">${rowFlags(row).join("")}</span>
        <span class="er-row-chevron" aria-hidden="true">›</span>
      </button></div>`;
  }

  function localRow(row) {
    const hidden = deferred?.kind === "receipt" && deferred.id === row.id
      ? { document: null, document_id: null, document_kind: undefined } : {};
    return { ...row, ...saves.get(row.id)?.draft, ...hidden };
  }

  function specialRow(key, label, reference, date, usd, mxn, status, symbol = "→") {
    const meta = [reference, date].filter(Boolean).join(" · ");
    return `<li class="er-row er-row-ledger"><div class="er-row-summary"><span class="er-check"></span><button type="button" class="er-row-open" data-cash-row="${esc(key)}" aria-haspopup="dialog"><span class="er-thumb" aria-hidden="true">${esc(symbol)}</span><span class="er-row-text"><strong>${esc(label)}</strong><small>${esc(meta)}</small></span><span class="er-row-amounts"><span>USD ${esc(cashMoney(usd))}</span><span>MXN ${esc(cashMoney(mxn))}</span></span><span class="er-row-flags">${status ? `<span class="er-flag">${esc(status)}</span>` : ""}</span><span class="er-row-chevron">›</span></button></div></li>`;
  }

  function historyEntry(html, { date = "", order = 0 } = {}) {
    return { html, sortDate: date || "", order };
  }

  function renderHistoryList() {
    const focused = document.activeElement;
    const focusedRow = focused?.closest?.(".er-row[data-row]")?.dataset.row;
    const focusedKey = focused?.dataset?.cashRow;
    const focusedControl = focused?.matches?.("[data-select]") ? "[data-select]"
      : focused?.matches?.(".er-row-open") ? ".er-row-open" : null;
    const shown = receiptRows().filter(row => !(deferred?.kind === "row" && deferred.id === row.id));
    const entries = [];

    // Actual cash movements are one chronological record. Same-day entries keep
    // the cash-flow order: voucher, its addenda by sequence, then receipts.
    const initial = view.activity.find(item => item.kind === "signout");
    if (initial)
      entries.push(historyEntry(
        specialRow("voucher", t("Voucher"), vid, initial.date, initial.usd, initial.mxn,
          view.cash_rows.voucher_documents.length > 0 ? "" : t("Needs signed voucher")),
        { date: initial.date, order: 0 }));
    view.cash_rows.advances.forEach((advance, index) =>
      entries.push(historyEntry(
        specialRow(
          advance.addendum_id,
          t(advance.kind === "return"
            ? "Cash return"
            : advance.settlement
              ? "Reimbursement"
              : "Additional cash"),
          advance.addendum_id,
          advance.date,
          advance.usd,
          advance.mxn,
          advance.status === "complete" ? "" : t("Needs signed addendum"),
          advance.kind === "return" ? "←" : "→",
        ),
        { date: advance.date, order: 10 + index })));
    shown.forEach((row, index) => {
      const shownRow = localRow(row);
      entries.push(historyEntry(
        `<li class="er-row${openId === row.id ? " open" : ""}"
          data-row="${esc(row.id)}">${summaryHTML(shownRow)}</li>`,
        { date: shownRow.date, order: 1000 + index }));
    });

    // Working paper and future movements follow the cash that has moved.
    for (const stage of (admin && view.cash_rows.settlement_review ? view.cash_rows.stages : []))
      entries.push(historyEntry(
        specialRow(
          stage.kind,
          t(stage.kind === "positive" ? "Reimbursement" : "Cash return"),
          stage.paper_id || "",
          stage.date,
          stage.usd,
          stage.mxn,
          stageLabel(stage),
          stage.kind === "return" ? "←" : "→",
        ),
        { order: 4000 }));
    if (view.cash_rows.log) {
      const log = view.cash_rows.log;
      entries.push(historyEntry(
        specialRow("log", t("Purchase log"), "", "", log.usd, log.mxn, "", "☷"),
        { order: 5000 }));
    }
    for (const row of view.rows.filter(item => item.kind === "discrepancy"))
      entries.push(historyEntry(
        specialRow(`lost:${row.id}`, t("Missing cash"), row.purpose, "",
          row.usd, row.mxn, "", ""),
        { order: 6000 }));

    entries.sort((a, b) => {
      if (a.sortDate !== b.sortDate)
        return a.sortDate ? (b.sortDate ? (a.sortDate < b.sortDate ? -1 : 1) : -1) : 1;
      return a.order - b.order;
    });
    const list = $("#er-rows");
    list.innerHTML = entries.map(entry => entry.html).join("");
    // Saves can finish after a row has been focused (for example while closing
    // the tray), so put focus back on the same control after rebuilding.
    const restored = focusedRow && focusedControl
      ? list.querySelector(`.er-row[data-row="${CSS.escape(focusedRow)}"] ${focusedControl}`)
      : focusedKey
        ? list.querySelector(`[data-cash-row="${CSS.escape(focusedKey)}"]`)
        : null;
    restored?.focus({ preventScroll: true });
    $("#er-empty").hidden = shown.length > 0;
    root.querySelector(".er-summary").classList.toggle("stale", deferred?.kind === "row");
    $("#er-add").hidden = !view.can_edit;

    const selectable = receiptRows().filter(editable);
    root.querySelector(".er-select-all").hidden = selectable.length < 2;
    $("#er-select-all").checked = selectable.length > 0 && selectable.every(row => selected.has(row.id));
  }

  function renderRows() {
    renderHistoryList();
    renderBulk();
    renderTray();
  }

  /* ---- row editor ------------------------------------------------------------ */

  function receiptHTML(row, disabled) {
    const viewer = row.document ? `<button type="button" class="er-receipt-thumb" data-view>
      <img src="${esc(row.document.thumbnail_url)}" alt=""><span>${esc(t(
        { statement: "View statement", log: "View purchase log" }[row.document_kind] || "View receipt"))}</span></button>` : "";
    // Without a receipt, a signed no-receipt statement takes its place.
    const statement = !disabled && row.receipt_reference
      ? `<span class="er-receipt-tools" data-statement-tools ${row.no_receipt_reason ? "" : "hidden"}>
          <a class="btn ghost" href="/${esc(vid)}/statements?row=${encodeURIComponent(row.id)}" target="_blank"
            rel="noopener">${esc(t("Print statement"))}</a>
          <button type="button" class="btn ghost" data-attach="statement">${esc(t("Attach signed statement"))}</button>
          ${row.statement_scan ? `<button type="button" class="btn primary" data-scan>${esc(t("Attach scanned {number}", {
            number: `${row.receipt_reference.split("-").pop()}x` }))}</button>` : ""}</span>` : "";
    if (documentable(row)) {
      // A statement stays on record; a receipt found later takes the next number.
      const later = (row.later_receipts || []).map(item => `<button type="button" class="er-receipt-thumb"
        data-later="${esc(item.document.document_id)}"><img src="${esc(item.document.thumbnail_url)}" alt="">
        <span>${esc(t("View receipt"))} ${esc(item.receipt_id.split("-").pop())}</span></button>`).join("");
      const add = !row.document || ["statement", "log"].includes(row.document_kind)
        ? `<span class="er-receipt-tools"><button type="button" class="btn" data-attach="document">${esc(
          t("Add receipt"))}</button></span>` : "";
      return `<div class="er-receipt${row.document ? "" : " er-receipt-missing"}">${viewer}${later}${add}
        ${!row.document && row.no_receipt_reason
          ? `<span class="muted">${esc(t("No receipt"))}: ${esc(row.no_receipt_reason)}</span>` : ""}
      </div>`;
    }
    if (row.document) {
      return `<div class="er-receipt">${viewer}
        ${disabled ? "" : `<span class="er-receipt-tools">
          <button type="button" class="text-button" data-attach="replace">${esc(t("Replace"))}</button>
          <button type="button" class="text-button danger" data-remove-receipt>${esc(t("Remove"))}</button></span>`}
      </div>`;
    }
    return `<div class="er-receipt er-receipt-missing">
      ${disabled ? `<span class="muted">${esc(t("No receipt"))}</span>` : `<span class="er-receipt-tools">
        <button type="button" class="btn" data-attach="new">${esc(t("Add receipt"))}</button>
        ${logRow() ? "" : `<button type="button" class="btn ghost" data-attach="log">${esc(
          t("Attach purchase log"))}</button>`}</span>`}
      <label class="er-no-receipt"><span>${esc(t("No receipt? Explain why"))}</span>
        <input name="no_receipt_reason" maxlength="200" value="${esc(row.no_receipt_reason)}" ${disabled}></label>
      ${statement}
    </div>`;
  }

  function editorHTML(row) {
    const disabled = editable(row) ? "" : "disabled";
    const correction = row.admin_note
      ? `<p class="er-correction">${esc(t("Correction requested:"))} ${esc(row.admin_note)}</p>` : "";
    const adminNote = admin && view.status === "submitted" && !row.posted
      ? `<label class="er-correction-note"><span>${esc(t("Correction note for the recipient"))}</span>
          <textarea data-correction rows="2">${esc(correctionNotes[row.id] || "")}</textarea></label>` : "";
    const remove = disabled ? "" : `<button type="button" class="text-button danger" data-delete>${esc(t("Delete row"))}</button>`;
    return `<div class="er-editor">${correction}
      ${receiptHTML(row, disabled)}
      ${cashRowFields.html(row, { t, codes: view.options, disabled: Boolean(disabled),
        show: { project: admin } })}
      ${adminNote}
      <p class="er-editor-tools">${remove}</p>
    </div>`;
  }

  function closeTray() {
    const id = openId;
    leaveOpenRow();
    openId = null;
    renderRows();
    (root.querySelector(`.er-row[data-row="${CSS.escape(id || "")}"] .er-row-open`) || $("#er-add"))
      .focus({ preventScroll: true });
  }

  function renderTray() {
    const tray = $("#er-tray");
    const row = openId && rowById(openId);
    if (!row) {
      tray.querySelectorAll(".undo-toast").forEach(toast => document.body.append(toast));
      if (tray.open) tray.close();
      document.documentElement.classList.remove("er-tray-open");
      $("#er-tray-editor").replaceChildren();
      return;
    }
    const scroll = tray.open ? $(".er-tray-body").scrollTop : 0;
    $("#er-tray-reference").textContent = [receiptNumber(row), vid].filter(Boolean).join(" · ");
    $("#er-tray-title").textContent = rowTitle(localRow(row));
    $("#er-tray-editor").innerHTML = editorHTML(localRow(row));
    wireEditor(openId);
    if (!tray.open) {
      tray.showModal();
      $("#er-tray-close").focus({ preventScroll: true });
    }
    document.documentElement.classList.add("er-tray-open");
    $(".er-tray-body").scrollTop = scroll;
    refreshSaveState();
  }

  function editorOf(id) {
    return openId === id ? $("#er-tray-editor .er-editor") : null;
  }

  // Current values in an open editor, in the API's shape.
  function collect(id) {
    const editor = editorOf(id);
    const value = name => editor.querySelector(`[name="${name}"]`)?.value ?? "";
    return { kind: "expense", ...cashRowFields.collect(editor), no_receipt_reason: value("no_receipt_reason") };
  }

  function wireEditor(id) {
    const editor = editorOf(id);
    if (!editor) return;
    // Receipt rows: amounts, codes, and splits are shared with Review.
    const receipt = localRow(rowById(id)).document;
    cashRowFields.wire(editor, { t, codes: view.options,
      preview: receipt && { url: cashDocumentStream(receipt), name: receipt.display_name },
      onInput: () => schedule(id), onChange: () => schedule(id, 0),
      onStructure: fields => saveNow(id, { kind: "expense", ...fields }) });
    editor.addEventListener("input", event => {
      if (event.target.closest(".row-fields")) return;
      if (event.target.matches('[name="no_receipt_reason"]')) {
        const tools = editor.querySelector("[data-statement-tools]");
        if (tools) tools.hidden = !event.target.value.trim();
      }
      if (event.target.matches("[data-correction]")) correctionNotes[id] = event.target.value;
      else schedule(id);
    });
    editor.addEventListener("click", async event => {
      const target = event.target.closest("button");
      if (!target || target.closest(".row-fields")) return;
      const row = rowById(id);
      if (target.matches("[data-view]")) openViewer(row.document);
      else if (target.dataset.later) openViewer(row.later_receipts.find(item =>
        item.document.document_id === target.dataset.later).document);
      else if (target.dataset.attach) openAttach(id, target.dataset.attach);
      else if (target.matches("[data-scan]")) {
        attaching = { rowId: id, mode: "statement" };
        try { await attachDocument(row.statement_scan.document_id); }
        catch (error) { message(errorText(error)); }
      }
      else if (target.matches("[data-remove-receipt]")) removeReceipt(id);
      else if (target.matches("[data-delete]")) deleteRow(id);
    });
  }

  /* ---- saving ----------------------------------------------------------------- */

  const hasUnsaved = entry => Boolean(entry.draft || entry.running || entry.timer);
  const changedElsewhere = () => t("This row changed on another screen. Your edits are unsaved; reload before editing again.");

  function rowSave(id) {
    if (!saves.has(id)) saves.set(id, {});
    return saves.get(id);
  }

  function remember(entry, fields) {
    entry.draft = { ...entry.draft, ...fields };
  }

  function schedule(id, delay = 650) {
    const entry = rowSave(id);
    // Capture now, before another click can remove or rebuild this editor.
    remember(entry, collect(id));
    window.clearTimeout(entry.timer);
    entry.timer = window.setTimeout(() => { entry.timer = null; flush(id).catch(() => {}); }, delay);
    refreshSaveState();
  }

  // Send the latest local values until none are left. Drafts are working data
  // until approval, so a save needs no retry safety: a failed save keeps its
  // values, says so, and goes again with the next edit or "Try again". Rows,
  // the report note, and the cash fields all save this way.
  async function drain(entry, { validate = () => {}, body, send, saved }) {
    window.clearTimeout(entry.timer);
    entry.timer = null;
    if (entry.running) return entry.running;
    entry.running = (async () => {
      while (entry.draft) {
        const current = entry.draft;
        validate(current);
        entry.draft = null;
        try {
          accept(await send(body(current)));
        } catch (error) {
          entry.draft = { ...current, ...entry.draft };  // keep edits made meanwhile
          // A save whose reply was lost (common on a phone) has usually landed
          // and moved the row on; take the current report and send once more.
          if (error.status === 409 && entry.unanswered) {
            entry.unanswered = false;
            accept(await api("/api/report"));
            continue;
          }
          entry.unanswered = !error.status;
          throw error;
        }
        entry.unanswered = false;
        entry.failed = false;
        saved();
      }
    })();
    refreshSaveState();
    try {
      await entry.running;
    } catch (error) {
      entry.failed = true;
      message(error.status === 409 ? changedElsewhere() : errorText(error));
      throw error;
    } finally {
      entry.running = null;
      refreshSaveState();
    }
  }

  async function flush(id, fields = null) {
    if (deferred?.kind === "receipt" && deferred.id === id) await settleDeferred();
    const entry = rowSave(id);
    if (fields) remember(entry, fields);
    return drain(entry, {
      validate(current) {
        if (!rowById(id)) throw Object.assign(new Error(changedElsewhere()), { status: 409 });
        if (cashRowFields.invalid(current)) throw new Error(t("Enter amounts with at most two decimal places."));
      },
      body: current => ({ submission_id: cashUUID(), rev: rowById(id).rev, ...current }),
      send: body => api(`/api/rows/${encodeURIComponent(id)}`, { method: "PATCH", body }),
      saved: () => afterSave(),
    });
  }

  function leaveOpenRow() {
    if (hasUnsaved(logSave)) flushLog().catch(() => {});
    if (openId && hasUnsaved(rowSave(openId))) flush(openId).catch(() => {});
  }

  // Structural changes (receipt, split, type) re-render the open editor.
  async function saveNow(id, fields) {
    try {
      if (editorOf(id)) remember(rowSave(id), collect(id));
      await flush(id, fields);
      renderRows();
    } catch (_) { /* The message is already shown. */ }
  }

  function afterSave() {
    renderSummary();
    renderCashDifference();
    renderActions();
    renderBulk();
    renderSettlementRows();
    if (!allSaves().some(entry => entry.failed)
        && $("#er-message").classList.contains("danger")) message("");
  }

  async function addRow() {
    try {
      await settleDeferred();
      leaveOpenRow();
      const body = { submission_id: cashUUID(), kind: "expense", date: cashToday() };
      accept(await api("/api/rows", { method: "POST", body }));
      openId = view.rows[view.rows.length - 1].id;
      render();
    } catch (error) { message(errorText(error)); }
  }

  /* ---- deferred deletes ------------------------------------------------------ */

  function settleDeferred() {
    const current = deferred;
    if (!current) return committingDeletion || Promise.resolve();
    // Committing is the toast's expiry; settle() also removes the toast.
    return Promise.resolve(current.toast.settle());
  }

  async function defer(next, text) {
    await settleDeferred();
    deferred = { ...next };
    deferred.toast = cashUndoToast({ text: t(text), undoLabel: t("Undo"),
      container: openId ? $("#er-tray") : document.body,
      onUndo: () => { const undone = deferred; deferred = null; undone.restore?.(); renderRows(); },
      onExpire: () => {
        const current = deferred;
        if (!current) return;
        deferred = null;
        const operation = Promise.resolve().then(() => current.commit());
        committingDeletion = operation;
        refreshSaveState();
        // Handle timer-triggered failures as well as callers awaiting settle.
        // Callers receive the original rejection so approval cannot continue.
        operation.catch(error => { message(errorText(error)); render(); }).finally(() => {
          if (committingDeletion === operation) committingDeletion = null;
          refreshSaveState();
        });
        return operation;
      } });
  }

  async function deleteRow(id) {
    try { await settleDeferred(); }
    catch (_) { return; } // The failed deletion has already shown its error.
    const stash = saves.get(id);
    if (stash) { window.clearTimeout(stash.timer); stash.timer = null; }
    saves.delete(id);
    selected.delete(id);
    if (openId === id) openId = null;
    await defer({
      kind: "row", id,
      restore: () => { if (stash) { saves.set(id, stash); if (hasUnsaved(stash)) schedule(id, 0); } },
      commit: async () => {
        await flushEdits();
        const row = rowById(id);
        if (!row) return;
        accept(await api(`/api/rows/${encodeURIComponent(id)}`, { method: "DELETE",
          body: { submission_id: cashUUID(), rev: row.rev } }));
        render();
      },
    }, "Row deleted");
    renderRows();
    refreshSaveState();
  }

  async function removeReceipt(id) {
    try { await settleDeferred(); }
    catch (_) { return; }
    if (editorOf(id)) remember(rowSave(id), collect(id));
    await defer({
      kind: "receipt", id,
      commit: async () => {
        if (editorOf(id)) remember(rowSave(id), collect(id));
        await flush(id, { document_id: null });
        renderRows();
      },
    }, "Receipt removed");
    renderRows();
  }

  async function flushAll() {
    await settleDeferred();
    await flushEdits();
  }

  // A deletion uses this too, without waiting for its own pending operation.
  async function flushEdits() {
    // Keep draining until edits made during an awaited request are saved too.
    while (allSaves().some(hasUnsaved)) {
      for (const [id, entry] of saves) {
        if (hasUnsaved(entry)) await flush(id);
      }
      if (hasUnsaved(noteSave)) await saveNote();
      if (hasUnsaved(logSave)) await flushLog();
    }
  }

  async function saveNote() {
    return drain(noteSave, {
      body: current => ({ submission_id: cashUUID(), ...current }),
      send: body => api("/api/report", { method: "PATCH", body }),
      saved: () => {
        renderActions();
        if (!allSaves().some(entry => entry.failed)) message("");
      },
    });
  }

  /* ---- receipts ------------------------------------------------------------------ */

  // A receipt is staged whole, every page in order, before it is attached,
  // the same way Capture builds a document.
  const attachPicker = createOrderedDocumentPicker($("#er-attach-picker"), {
    translate: t, onError: text => { $("#er-attach-status").textContent = text; } });
  $("#er-attach-picker").addEventListener("document-picker-change", event => {
    $("#er-attach-go").disabled = event.detail.count === 0;
  });

  async function openAttach(rowId, mode) {
    try { await settleDeferred(); }
    catch (_) { return; }
    attaching = { rowId, mode };
    attachPicker.clear();
    $("#er-attach-status").textContent = "";
    if (admin) loadAttachInbox().catch(() => {});
    $("#er-attach").showModal();
  }

  function closeAttach() {
    attaching = null;
    attachPicker.clear();
    $("#er-attach").close();
  }

  async function loadAttachInbox() {
    const { documents } = await cashApi("/api/documents/inbox");
    const ready = documents.filter(document => !cashDocumentPending(document));
    root.querySelector(".er-attach-inbox").hidden = !ready.length;
    $("#er-attach-inbox").innerHTML = ready.map(document => `<li>
      <button type="button" class="text-button" data-inbox="${esc(document.document_id)}">${esc(
        document.display_name || document.document_id)}</button>
      <small>${esc(document.page_count || "?")} page(s)</small></li>`).join("");
  }

  async function attachDocument(documentId) {
    const { rowId, mode } = attaching;
    if (mode === "document") {
      accept(await api(`/api/rows/${encodeURIComponent(rowId)}/documentation`, {
        method: "POST", body: { submission_id: cashUUID(), document_id: documentId } }));
      render();
    } else {
      const kind = mode === "replace" ? rowById(rowId).document_kind
        : ["statement", "log"].includes(mode) ? mode : null;
      await flush(rowId, kind ? { document_id: documentId, document_kind: kind } : { document_id: documentId });
      renderRows();
      }
    closeAttach();
  }

  // Scanned purchase log sheets: the log row's document, a new log row, or,
  // once approved, a missing sheet filed under the next receipt number.
  function renderLogScans() {
    const list = $("#er-log-scans");
    if (!list) return;
    const scans = view.cash_rows.log ? [] : view.log_scans || [];
    list.hidden = !scans.length || view.read_only;
    list.innerHTML = scans.map(scan => `<li>${esc(t("Scanned purchase log: {name}", { name: scan.display_name }))}
      (${esc(t(scan.page_count === 1 ? "{count} page" : "{count} pages", { count: scan.page_count || "?" }))})
      <button type="button" class="btn primary" data-log-scan="${esc(scan.document_id)}">${
        esc(logRow() ? t("Attach to {row}", { row: logRow().receipt_reference?.split("-").pop() || t("log row") })
          : t("Add as purchase log row"))}</button></li>`).join("");
  }

  async function attachLogScan(documentId) {
    const row = logRow();
    if (!row) {
      const next = await api("/api/rows", { method: "POST", body: { submission_id: cashUUID(),
        document_id: documentId, document_kind: "log", purpose: "Purchase log" } });
      accept(next);
      openId = next.rows[next.rows.length - 1].id;
      render();
      return;
    }
    attaching = { rowId: row.id, mode: row.posted ? "document" : "log" };
    await attachDocument(documentId);
  }

  async function attachStaged() {
    const status = $("#er-attach-status");
    $("#er-attach-go").disabled = true;
    try {
      if (attaching.mode !== "document") await flush(attaching.rowId);
      const prepared = await attachPicker.preparedFiles((page, total) => {
        status.textContent = t("Preparing page {page} of {total}…", { page, total });
      });
      const form = new FormData();
      prepared.forEach(file => form.append("files", file, file.name));
      status.textContent = t("Uploading receipt…");
      const uploaded = await api("/documents", { method: "POST", formData: form });
      await attachDocument(uploaded.documents[0].document_id);
    } catch (error) {
      status.textContent = errorText(error);
      $("#er-attach-go").disabled = attachPicker.count === 0;
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
    show("#er-submit", view.can_edit && !submitted);
    show("#er-print-statements", view.can_edit && view.rows.some(row => row.kind === "expense" && !("purchases" in row)
      && !row.posted && !row.document && row.no_receipt_reason && row.receipt_reference));
    show("#er-print-log", view.can_edit && !view.cash_rows.log && !logRow());
    renderLogScans();
    $("#er-submit").textContent = t(view.review.summary ? "Submit corrections" : "Submit report");
    show("#er-reopen", !admin && submitted && !view.read_only);
    show("#er-approve", admin && !view.read_only && view.cash_rows.settlement_review);
    if (admin) $("#er-approve").disabled = !view.cash_rows.ready;
    show("#er-corrections", admin && !view.read_only && submitted);
  }

  async function step(name, body = {}) {
    try {
      await flushAll();
      const action = ["submit", "approve"].includes(name) ? `${name}-calculated` : name;
      accept(await cashCommand(`/${vid}/api/report/${action}`, body));
      render();
      return true;
    } catch (error) {
      message(errorText(error));
      return false;
    }
  }


  function openNewRow(type = "") {
    leaveOpenRow();
    openId = null;
    const tray = $("#er-tray");
    $("#er-tray-title").textContent = t("Add row");
    $("#er-tray-reference").textContent = vid;
    const options = [
      ["", "Type"],
      ["expense", "Expense"],
      ["positive", "Add cash"],
      ["return", "Cash return"],
    ];
    $("#er-tray-editor").innerHTML =
      `<label>${esc(t("Type"))}<select data-row-type>${options.map(([value, label]) => `<option value="${value}" ${value === type ? "selected" : ""} ${value === "" ? "disabled" : ""}>${esc(t(label))}</option>`).join("")}</select></label>` +
      (type
        ? `<form data-new-addendum><div class="form-grid two"><label>${esc(t("Date"))}<input name="date" type="date" value="${cashToday()}" required></label><label>${esc(t("Reason"))}<input name="reason" maxlength="200"></label><label>USD<input name="usd" inputmode="decimal" min="0"></label><label>MXN<input name="mxn" inputmode="decimal" min="0"></label><label>${esc(t("Approver name"))}<input name="approver_name" maxlength="60" value="${esc(root.dataset.approver || "")}"></label></div><output role="status"></output><div class="er-row-tools"><button type="submit" class="btn primary">${esc(t("Save"))}</button><button type="submit" class="btn" value="print">${esc(t("Save and print addendum"))}</button></div></form>`
        : "");
    $("[data-row-type]").onchange = (event) =>
      event.target.value === "expense"
        ? addRow()
        : openNewRow(event.target.value);
    const form = $("[data-new-addendum]");
    if (form)
      form.onsubmit = async (event) => {
        event.preventDefault();
        const printing = event.submitter?.value === "print",
          tab = printing ? window.open("about:blank", "_blank") : null;
        const output = form.querySelector("output"),
          buttons = [...form.querySelectorAll("button")];
        buttons.forEach((b) => (b.disabled = true));
        try {
          await flushAll();
          const values = Object.fromEntries(new FormData(form));
          const result = await cashCommand("/api/addenda/prepare", {
            voucher_id: vid,
            kind: type,
            ...values,
            usd: values.usd || "0",
            mxn: values.mxn || "0",
          });
          if (tab) {
            tab.opener = null;
            tab.location.replace(result.print_url);
          }
          await load();
          openSettlement(result.addendum_id);
        } catch (e) {
          if (tab) tab.close();
          output.textContent = errorText(e);
          buttons.forEach((b) => (b.disabled = false));
        }
      };
    if (!tray.open) tray.showModal();
    document.documentElement.classList.add("er-tray-open");
  }

  const stageLabel = (stage) =>
    stage.status === "filed" ? "" : t("Needs signed addendum");

  // Kept for callers that only need the list refreshed; the tray and bulk
  // controls are already current, and re-rendering the tray would disturb typing.
  function renderSettlementRows() {
    renderHistoryList();
  }
  async function flushLog() {
    return drain(logSave, {
      body: (current) => ({ ...current, rev: view.cash_rows.log.rev }),
      send: (body) =>
        cashApi(`/${vid}/api/purchase-log`, { method: "PATCH", body }),
      saved: () => {
        renderSummary();
        renderActions();
        renderSettlementRows();
        const total = $("[data-log-total]");
        if (total)
          total.textContent = pair(
            view.cash_rows.log.usd,
            view.cash_rows.log.mxn,
          );
      },
    });
  }
  async function openPurchaseLog(index = null) {
    leaveOpenRow();
    openId = null;
    const log = view.cash_rows.log,
      doc = log?.document,
      candidate = log?.matched_document;
    const draft = structuredClone(logSave.draft || log);
    const locked = !view.can_edit || !!log.posted;
    const items = draft.items || [];
    const optionList = (list, key, value) =>
      `<option value=""></option>` +
      list
        .map(
          (item) =>
            `<option value="${esc(item[key])}" ${item[key] === value ? "selected" : ""}>${esc(item[key] + " · " + item.name)}</option>`,
        )
        .join("");
    $("#er-tray-reference").textContent =
      index === null ? vid : `${vid} · ${t("Purchase log")}`;
    $("#er-tray-title").textContent =
      index === null
        ? t("Purchase log")
        : t("Purchase {number}", { number: index + 1 });
    if (index === null) {
      $("#er-tray-editor").innerHTML =
        `<ol class="log-subrows" data-purchases>${items.map((item, i) => `<li><button type="button" class="log-subrow" data-open-purchase="${i}" aria-label="${esc(t("Purchase {number}", { number: i + 1 }))}"><span class="log-subrow-text"><strong>${esc(item.vendor || t("Purchase {number}", { number: i + 1 }))}</strong><small>${esc(item.date)}</small><span>${esc(item.purpose)}</span></span><span class="log-subrow-amounts"><span>MXN ${esc(cashMoney(item.mxn))}</span><span>USD ${esc(cashMoney(item.usd))}</span></span><span aria-hidden="true">›</span></button></li>`).join("")}</ol>
      ${locked ? "" : `<button type="button" class="btn" data-add-purchase>${esc(t("Add purchase"))}</button>`}
      <div class="er-row-calculation"><strong>${esc(t("Total"))}</strong><strong data-log-total>${esc(pair(log.usd, log.mxn))}</strong></div>
      <fieldset class="log-codes" ${locked ? "disabled" : ""}><div class="form-grid two"><label>${esc(t("Class code"))}<select name="class_code">${optionList(view.options.classes, "class_code", draft.class_code)}</select></label><label>${esc(t("Account code"))}<select name="expense_account">${optionList(view.options.expense_accounts, "account_id", draft.expense_account)}</select></label></div></fieldset>
      <div class="er-row-tools"><a class="btn" data-print-log href="/${vid}/purchase-log" target="_blank" rel="noopener">${esc(t("Print purchase log"))}</a>${locked ? "" : `<button class="btn" data-log-upload>${esc(t("Attach signed purchase log"))}</button><input type="file" data-log-file accept="application/pdf,image/*" hidden>`}</div>
      ${doc ? `<a class="er-row-document" href="${esc(doc.stream_url)}" target="_blank">${esc(doc.display_name)}</a>` : ""}
      ${candidate ? `<div class="er-row-tools"><a class="er-row-document" href="${esc(candidate.stream_url)}" target="_blank">${esc(candidate.display_name)}</a>${admin && !locked ? `<button class="btn" data-file-matched>${esc(t("File signed copy"))}</button>` : ""}</div>` : ""}
      ${locked ? "" : `<button class="text-button danger" data-log-delete>${esc(t("Delete row"))}</button>`}`;
    } else {
      const item = items[index];
      $("#er-tray-editor").innerHTML =
        `<button type="button" class="text-button log-back" data-log-back>‹ ${esc(t("Back to purchase log"))}</button><fieldset class="log-purchase" data-purchase="${index}" ${locked ? "disabled" : ""}><div class="form-grid two"><label>${esc(t("Date"))}<input name="date" type="date" value="${esc(item.date)}"></label><label>${esc(t("Vendor"))}<input name="vendor" maxlength="120" value="${esc(item.vendor)}"></label><label class="full">${esc(t("Expense description"))}<input name="purpose" maxlength="200" value="${esc(item.purpose)}"></label><label>MXN<input name="mxn" inputmode="decimal" value="${esc(item.mxn)}"></label><label>USD<input name="usd" inputmode="decimal" value="${esc(item.usd)}"></label></div>${locked ? "" : `<button class="text-button danger" type="button" data-remove-purchase="${index}">${esc(t("Remove purchase"))}</button>`}</fieldset>`;
    }
    function collectLog() {
      const editor = $("[data-purchase]");
      if (editor)
        draft.items[index] = Object.fromEntries(
          [...editor.querySelectorAll("input")].map((i) => [i.name, i.value]),
        );
      const codes = $(".log-codes");
      if (codes) {
        draft.class_code = codes.querySelector('[name="class_code"]').value;
        draft.expense_account = codes.querySelector(
          '[name="expense_account"]',
        ).value;
      }
      return {
        items: structuredClone(draft.items || []),
        class_code: draft.class_code,
        expense_account: draft.expense_account,
      };
    }
    function queueLog() {
      remember(logSave, collectLog());
      clearTimeout(logSave.timer);
      logSave.timer = setTimeout(() => flushLog().catch(() => {}), 450);
      refreshSaveState();
    }
    $("#er-tray-editor").oninput = (event) => {
      if (event.target.closest(".log-purchase,.log-codes")) queueLog();
    };
    $("#er-tray-editor").onchange = (event) => {
      if (event.target.closest(".log-codes")) queueLog();
    };
    const add = $("[data-add-purchase]");
    if (add)
      add.onclick = async () => {
        try {
          await flushLog();
          remember(logSave, {
            ...collectLog(),
            items: [
              ...collectLog().items,
              {
                date: cashToday(),
                vendor: "",
                purpose: "",
                usd: "0.00",
                mxn: "0.00",
              },
            ],
          });
          await flushLog();
          openPurchaseLog(view.cash_rows.log.items.length - 1);
        } catch (e) {
          message(errorText(e));
        }
      };
    $("#er-tray-editor")
      .querySelectorAll("[data-remove-purchase]")
      .forEach(
        (button) =>
          (button.onclick = async () => {
            try {
              const data = collectLog();
              data.items.splice(Number(button.dataset.removePurchase), 1);
              remember(logSave, data);
              await flushLog();
              openPurchaseLog();
            } catch (e) {
              message(errorText(e));
            }
          }),
      );
    const back = $("[data-log-back]");
    if (back)
      back.onclick = async () => {
        try {
          await flushLog();
          await openPurchaseLog();
          $(`[data-open-purchase="${index}"]`)?.focus();
        } catch (e) {
          message(errorText(e));
        }
      };
    $("#er-tray-editor")
      .querySelectorAll("[data-open-purchase]")
      .forEach(
        (button) =>
          (button.onclick = async () => {
            try {
              await flushLog();
              openPurchaseLog(Number(button.dataset.openPurchase));
            } catch (e) {
              message(errorText(e));
            }
          }),
      );
    const printLog = $("[data-print-log]");
    if (printLog)
      printLog.onclick = async (event) => {
        event.preventDefault();
        const tab = window.open("about:blank", "_blank");
        try {
          await flushAll();
          if (tab) {
            tab.opener = null;
            tab.location.replace(`/${vid}/purchase-log`);
          }
        } catch (e) {
          if (tab) tab.close();
          message(errorText(e));
        }
      };
    const input = $("[data-log-file]");
    if (input) {
      $("[data-log-upload]").onclick = () => input.click();
      input.onchange = async () => {
        if (!input.files[0]) return;
        try {
          await flushAll();
          const formData = new FormData();
          formData.append("files", input.files[0]);
          accept(
            await cashApi(`/${vid}/api/purchase-log`, {
              method: "POST",
              formData,
            }),
          );
          render();
          openPurchaseLog();
        } catch (e) {
          message(errorText(e));
        }
      };
    }
    const match = $("[data-file-matched]");
    if (match)
      match.onclick = async () => {
        try {
          await flushAll();
          accept(
            await cashApi(`/${vid}/api/purchase-log`, {
              method: "POST",
              body: { attach_matched: true },
            }),
          );
          render();
          openPurchaseLog();
        } catch (e) {
          message(errorText(e));
        }
      };
    const remove = $("[data-log-delete]");
    if (remove)
      remove.onclick = async () => {
        try {
          await flushAll();
          accept(
            await cashApi(`/${vid}/api/purchase-log`, {
              method: "DELETE",
              body: {},
            }),
          );
          closeTray();
          render();
        } catch (e) {
          message(errorText(e));
        }
      };
    if (!$("#er-tray").open) $("#er-tray").showModal();
    document.documentElement.classList.add("er-tray-open");
    $(".er-tray-body").scrollTop = 0;
    if (index !== null) $("[data-log-back]").focus({ preventScroll: true });
  }

  async function openSettlement(key) {
    if (key.startsWith("lost:")) {
      leaveOpenRow(); openId = null;
      const row = rowById(key.slice(5));
      $("#er-tray-reference").textContent = vid;
      $("#er-tray-title").textContent = t("Missing cash");
      $("#er-tray-editor").innerHTML = `<div class="er-row-calculation"><strong>${esc(pair(row.usd,row.mxn))}</strong><span>${esc(row.purpose)}</span></div>${row.document ? `<a class="er-row-document" target="_blank" href="${esc(row.document.stream_url)}">${esc(row.document.display_name)}</a>` : `<a class="btn" target="_blank" href="/${vid}/statements?row=${encodeURIComponent(row.id)}">${esc(t("Print statement"))}</a>${admin ? `<button class="btn" data-lost-statement>${esc(t("Attach signed statement"))}</button>` : ""}`}`;
      const attach = $("[data-lost-statement]");
      if (attach) attach.onclick = () => openAttach(row.id,"statement");
      if (!$("#er-tray").open) $("#er-tray").showModal();
      document.documentElement.classList.add("er-tray-open");
      return;
    }

    if (key === "log") return openPurchaseLog();
    leaveOpenRow();
    openId = null;
    const dialog = $("#er-tray");
    const stage = view.cash_rows.stages.find((s) => s.kind === key);
    const advance = view.cash_rows.advances.find((a) => a.addendum_id === key);
    $("#er-tray-reference").textContent =
      stage?.paper_id || advance?.addendum_id || vid;
    $("#er-tray-title").textContent = t(
      stage
        ? key === "positive"
          ? "Reimbursement"
          : "Cash return"
        : advance
          ? advance.kind === "return"
            ? "Cash return"
            : advance.settlement
              ? "Reimbursement"
              : "Additional cash"
          : "Voucher",
    );
    const print = stage
      ? `/${vid}/settlement/print/${key}`
      : advance
        ? `/voucher/${vid}/addendum/${key}/print`
        : `/voucher/${vid}/print`;
    let body = "";
    if (stage) {
      body = `<div class="er-row-calculation"><strong>${esc(t(key === "positive" ? "Reimbursement" : "Cash return"))}</strong><strong>${esc(pair(stage.usd, stage.mxn))}</strong></div><div class="er-row-tools">`;
      if (
        admin &&
        !view.read_only &&
        ["calculated", "outdated"].includes(stage.status)
      )
        body += `<button class="btn primary" data-generate>${esc(t(stage.status === "outdated" ? "Replace addendum" : "Generate addendum"))}</button>`;
      else if (admin && !view.read_only)
        body += `<a class="btn" href="${print}" target="_blank">${esc(t("Print addendum"))}</a><button class="btn" data-file>${esc(t("File signed copy"))}</button><input type="file" accept="application/pdf,image/*" data-signed hidden>`;
      body += "</div>";
      if (stage.document_id)
        body += `<a class="er-row-document" href="${esc(stage.document.stream_url)}" target="_blank">${esc(stage.filename)}</a>`;
      if (stage.matched_document && admin)
        body += `<div class="er-row-tools"><a class="er-row-document" target="_blank" href="${esc(stage.matched_document.stream_url)}">${esc(stage.matched_document.display_name)}</a><button class="btn" data-file-settlement>${esc(t("File signed copy"))}</button></div>`;
    } else {
      body = admin
        ? `<div class="er-row-tools"><a class="btn" href="${print}" target="_blank">${esc(t(advance ? "Print addendum" : "Print voucher"))}</a></div>`
        : "";
      if (advance?.document_id)
        body += `<a class="er-row-document" href="/api/documents/${advance.document_id}/document.pdf" target="_blank">${esc(advance.addendum_id)}</a>`;
    }
    if (!stage) {
      const item = advance || view.activity.find((x) => x.kind === "signout");
      const detail = advance?.reason || item?.detail || "";
      body =
        `<div class="form-grid two"><label>${esc(t("Date"))}<input value="${esc(item?.date || "")}" readonly></label><label>${esc(t("Reason"))}<input value="${esc(detail)}" readonly></label><label>USD<input value="${esc(item?.usd || "")}" readonly></label><label>MXN<input value="${esc(item?.mxn || "")}" readonly></label></div>` +
        body;
      const docs = advance
        ? advance.document
          ? [advance.document]
          : []
        : view.cash_rows.voucher_documents;
      body = body.replace(/<a class="er-row-document"[^>]*>.*?<\/a>/g, "");
      const candidate = advance
        ? advance.matched_document
        : view.cash_rows.voucher_candidate;
      if (candidate && admin)
        body += `<div class="er-row-tools"><a class="er-row-document" target="_blank" href="${esc(candidate.stream_url)}">${esc(candidate.display_name)}</a><button class="btn" data-ledger-match data-document="${esc(candidate.document_id)}">${esc(t("File signed copy"))}</button></div>`;
      body += docs
        .map(
          (d) =>
            `<a class="er-row-document" href="${esc(d.stream_url || "/api/documents/" + d.document_id + "/document.pdf")}" target="_blank">${esc(d.display_name)}</a>`,
        )
        .join("");
      if (
        admin &&
        !view.read_only &&
        (advance?.status === "pending" ||
          (!advance && !(view.cash_rows.voucher_documents.length > 0)))
      )
        body += `<div class="er-row-tools"><button class="btn" data-ledger-file>${esc(t("File signed copy"))}</button><input type="file" data-ledger-upload accept="application/pdf,image/*" hidden></div>`;
    }
    $("#er-tray-editor").innerHTML = body;
    const matchedLedger = $("[data-ledger-match]");
    if (matchedLedger)
      matchedLedger.onclick = async () => {
        try {
          accept(
            await cashApi(
              `/${vid}/api/cash-rows/${advance?.addendum_id || "voucher"}`,
              {
                method: "POST",
                body: { document_id: matchedLedger.dataset.document },
              },
            ),
          );
          render();
          openSettlement(key);
        } catch (e) {
          message(errorText(e));
        }
      };
    const ledgerFile = $("[data-ledger-upload]");
    if (ledgerFile) {
      $("[data-ledger-file]").onclick = () => ledgerFile.click();
      ledgerFile.onchange = async () => {
        if (!ledgerFile.files[0]) return;
        try {
          const formData = new FormData();
          formData.append("files", ledgerFile.files[0]);
          accept(
            await cashApi(
              `/${vid}/api/cash-rows/${advance?.addendum_id || "voucher"}`,
              { method: "POST", formData },
            ),
          );
          render();
          openSettlement(key);
        } catch (e) {
          message(errorText(e));
        }
      };
    }
    if (!dialog.open) dialog.showModal();
    document.documentElement.classList.add("er-tray-open");
    const generate = $("[data-generate]");
    if (generate)
      generate.onclick = async () => {
        generate.disabled = true;
        try {
          await flushAll();
          accept(
            await cashApi(`/${vid}/api/settlement/prepare/${key}`, {
              method: "POST",
              body: {},
            }),
          );
          render();
          openSettlement(key);
        } catch (e) {
          message(errorText(e));
          generate.disabled = false;
        }
      };
    const matched = $("[data-file-settlement]");
    if (matched)
      matched.onclick = async () => {
        try {
          await flushAll();
          accept(
            await cashApi(`/${vid}/api/settlement/file/${key}`, {
              method: "POST",
              body: { document_id: stage.matched_document.document_id },
            }),
          );
          render();
          openSettlement(key);
        } catch (e) {
          message(errorText(e));
        }
      };
    const file = $("[data-signed]");
    async function upload(blob, name) {
      try {
        await flushAll();
        const formData = new FormData();
        formData.append("files", blob, name);
        accept(
          await cashApi(`/${vid}/api/settlement/file/${key}`, {
            method: "POST",
            formData,
          }),
        );
        render();
        openSettlement(key);
      } catch (e) {
        message(errorText(e));
      }
    }
    if (file) {
      $("[data-file]").onclick = () => file.click();
      file.onchange = () => {
        if (file.files[0]) upload(file.files[0], file.files[0].name);
      };
    }
  }

  /* ---- administrator: settlement, paperwork, addenda ------------------------------ */

  // Approving also settles the signout: the administrator confirms the cash
  // that actually came back or was paid, and sees any lost cash to acknowledge.
  function openApproval() {
    const form = $("#er-approval-form");
    if (!form.elements.date.value) form.elements.date.value = cashToday();
    const lost = view.rows.filter(row => row.kind === "discrepancy" && !row.posted);
    $("#er-approval-discrepancy").hidden = $("#er-approval-acknowledge").hidden = !lost.length;
    $("#er-approval-discrepancy").innerHTML = lost.map(row => `<strong>${esc(t("Missing cash"))}:</strong>
      ${esc(pair(row.usd, row.mxn))} · ${esc(row.purpose)}`).join("<br>");
    $("#er-approval").hidden = false;
    form.elements.date.focus();
  }

  async function approve(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (await step("approve", { date: form.elements.date.value, approver_name: form.elements.approver_name.value,
      resolution_note: form.elements.resolution_note.value,
      acknowledged: form.elements.acknowledged.checked })) {
      $("#er-approval").hidden = true;
      form.elements.acknowledged.checked = false;
    }
  }

  /* ---- events -------------------------------------------------------------------- */

  $("#er-tray-close").addEventListener("click", closeTray);
  $("#er-tray-done").addEventListener("click", closeTray);
  $("#er-tray").addEventListener("cancel", event => {
    event.preventDefault();
    closeTray();
  });
  $("#er-tray").addEventListener("click", event => {
    if (event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom) closeTray();
  });
  $("#er-tray-retry").addEventListener("click", () => flushAll().then(() => renderRows()).catch(() => {}));
  $("#er-rows").addEventListener("click", event => {
    const special = event.target.closest("[data-cash-row]");
    if (special) { openSettlement(special.dataset.cashRow); return; }
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
  $('#er-print-log').addEventListener('click',async()=>{
    try{await flushAll();accept(await cashApi(`/${vid}/api/purchase-log`,{method:'POST',body:{}}));render();openPurchaseLog()}catch(e){message(errorText(e))}
  });
  $("#er-add").addEventListener("click", () => admin ? openNewRow() : addRow());
  $("#er-attach-go").addEventListener("click", attachStaged);
  $("#er-log-scans")?.addEventListener("click", async event => {
    const button = event.target.closest("[data-log-scan]");
    if (!button) return;
    button.disabled = true;
    try { await attachLogScan(button.dataset.logScan); }
    catch (error) { message(errorText(error)); button.disabled = false; }
  });
  $("#er-attach-cancel").addEventListener("click", closeAttach);
  $("#er-attach-inbox")?.addEventListener("click", async event => {
    const button = event.target.closest("[data-inbox]");
    if (!button) return;
    try { await attachDocument(button.dataset.inbox); }
    catch (error) { $("#er-attach-status").textContent = errorText(error); }
  });
  $("#er-viewer-close").addEventListener("click", () => $("#er-viewer").close());
  $("#er-retry").addEventListener("click", () => flushAll().then(() => renderRows()).catch(() => {}));
  $("#er-note").addEventListener("input", () => {
    remember(noteSave, { note: $("#er-note").value });
    window.clearTimeout(noteSave.timer);
    noteSave.timer = window.setTimeout(() => { noteSave.timer = null; saveNote().catch(() => {}); }, 650);
    refreshSaveState();
  });
  $("#er-submit").addEventListener("click", () => step("submit"));
  $("#er-reopen").addEventListener("click", () => step("reopen"));
  if (admin) {
    $("#er-cash-difference-actual").addEventListener("input", () => {
      cashActual = $("#er-cash-difference-actual").value;
      renderCashDifferenceDelta();
    });
    $("#er-cash-rounding").addEventListener("click", () => resolveCashDifference("rounding"));
    $("#er-missing-cash").addEventListener("click", () => resolveCashDifference("missing_cash"));
    $("#er-approve").addEventListener("click", openApproval);
    $("#er-approval-form").addEventListener("submit", approve);
    $("#er-approval-cancel").addEventListener("click", () => { $("#er-approval").hidden = true; });
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
  }
  document.addEventListener("language-change", () => {
    if (!view) return;
    leaveOpenRow();
    render();
  });
  // Leaving during the undo window commits the pending delete, not loses it.
  window.addEventListener("pagehide", () => {
    if (!deferred) return;
    const { kind, id } = deferred;
    const row = rowById(id);
    if (!row) return;
    const body = { submission_id: cashUUID(), rev: row.rev };
    if (kind === "row") {
      cashApi(`/${vid}/api/rows/${encodeURIComponent(id)}`, { method: "DELETE", body, keepalive: true })
        .catch(() => {});
    } else {
      cashApi(`/${vid}/api/rows/${encodeURIComponent(id)}`, { method: "PATCH",
        body: { ...body, document_id: null }, keepalive: true }).catch(() => {});
    }
  });
  window.addEventListener("beforeunload", event => {
    const unsaved = committingDeletion || allSaves().some(hasUnsaved);
    if (unsaved) { event.preventDefault(); event.returnValue = ""; }
  });

  load().catch(error => message(errorText(error)));
})();
