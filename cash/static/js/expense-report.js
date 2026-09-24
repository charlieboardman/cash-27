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
  const saves = new Map();            // row id -> pending local values and save state
  const correctionNotes = {};         // admin notes waiting for "Request corrections"
  const noteSave = {};
  let upload = null;                  // {rowId, mode: "new" | "append" | "replace"}

  /* ---- money --------------------------------------------------------------- */

  const { cents, pair, inputValue } = cashRowFields;
  const decimal = minor => (minor / 100).toFixed(2);

  /* ---- messages and save state -------------------------------------------- */

  function message(text, tone = "danger") {
    cashNotice($("#er-message"), text, tone, { fade: tone === "success" });
  }

  function refreshSaveState() {
    const entries = [...saves.values(), noteSave];
    const failed = entries.some(entry => entry.failed);
    const busy = entries.some(hasUnsaved);
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
    renderRows();
    renderActions();
    renderActivity();
    if (!document.activeElement?.matches("#er-note")) $("#er-note").value = noteSave.draft?.note ?? view.note;
    $("#er-note").disabled = !view.can_edit;
    $("#er-note-box").open ||= Boolean(view.note);
    if (admin) {
      renderAdmin();
      $("#project-options").innerHTML = view.options.projects
        .map(project => `<option value="${esc(project)}"></option>`).join("");
    }
    if (i18n) i18n.apply();
    refreshSaveState();
  }

  function renderSummary() {
    const received = view.activity.filter(item => ["signout", "addendum"].includes(item.kind)
      && (cents(item.usd) > 0 || cents(item.mxn) > 0));
    $("#er-received").textContent = pair(
      received.reduce((sum, item) => sum + cents(item.usd), 0) / 100,
      received.reduce((sum, item) => sum + cents(item.mxn), 0) / 100);
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

  function localRow(row) {
    return { ...row, ...saves.get(row.id)?.draft };
  }

  function renderRows() {
    const list = $("#er-rows");
    list.innerHTML = view.rows.map(localRow).map(row => `<li class="er-row${openId === row.id ? " open" : ""}"
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
    const stored = rowById(id);
    if (!item || !stored) return;
    const row = localRow(stored);
    item.querySelector(".er-row-summary").outerHTML = summaryHTML(row);
  }

  /* ---- row editor ------------------------------------------------------------ */

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

  function editorHTML(row) {
    const disabled = editable(row) ? "" : "disabled";
    const kindSelect = `<label class="er-kind"><span>${esc(t("Row type"))}</span>
      <select name="kind" ${disabled}>${["expense", "cash_return", "reimbursement"].map(kind =>
        `<option value="${kind}" ${row.kind === kind ? "selected" : ""}>${esc(t({
          expense: "Receipt", cash_return: "Cash returned", reimbursement: "Reimbursement requested",
        }[kind]))}</option>`).join("")}</select></label>`;
    const correction = row.admin_note
      ? `<p class="er-correction">${esc(t("Correction requested:"))} ${esc(row.admin_note)}</p>` : "";
    const adminNote = admin && view.status === "submitted" && !row.posted
      ? `<label class="er-correction-note"><span>Correction note for the recipient</span>
          <textarea data-correction rows="2">${esc(correctionNotes[row.id] || "")}</textarea></label>` : "";
    const remove = disabled ? "" : `<button type="button" class="text-button danger" data-delete>${esc(t("Delete row"))}</button>`;
    if (row.kind !== "expense") {
      return `<div class="er-editor">${correction}<div class="form-grid two">${kindSelect}<span></span>
        <label><span>USD</span><input name="usd" inputmode="decimal" value="${esc(inputValue(row.usd))}" ${disabled}></label>
        <label><span>MXN</span><input name="mxn" inputmode="decimal" value="${esc(inputValue(row.mxn))}" ${disabled}></label>
        <label class="full"><span>${esc(t("Note"))}</span><input name="purpose" value="${esc(row.purpose)}" ${disabled}></label>
        </div>${adminNote}<p class="er-editor-tools">${remove}</p></div>`;
    }
    return `<div class="er-editor">${correction}
      ${receiptHTML(row, disabled)}
      ${cashRowFields.html(row, { t, codes: view.options, disabled: Boolean(disabled),
        show: { project: admin } })}
      ${adminNote}
      <p class="er-editor-tools">${kindSelect}${remove}</p>
    </div>`;
  }

  function editorOf(id) {
    return root.querySelector(`.er-row[data-row="${CSS.escape(id)}"] .er-editor`);
  }

  // Current values in an open editor, in the API's shape.
  function collect(id) {
    const editor = editorOf(id);
    const value = name => editor.querySelector(`[name="${name}"]`)?.value ?? "";
    const kind = value("kind") || rowById(id).kind;
    if (kind !== "expense") return { kind, usd: value("usd"), mxn: value("mxn"), purpose: value("purpose") };
    return { kind, ...cashRowFields.collect(editor), no_receipt_reason: value("no_receipt_reason") };
  }

  function wireEditor(id) {
    const editor = editorOf(id);
    if (!editor) return;
    // Receipt rows: amounts, codes, and splits are shared with Review.
    cashRowFields.wire(editor, { t, onInput: () => schedule(id), onChange: () => schedule(id, 0),
      onStructure: fields => saveNow(id, { kind: "expense", ...fields }) });
    editor.addEventListener("input", event => {
      if (event.target.closest(".row-fields")) return;
      if (event.target.matches("[data-correction]")) correctionNotes[id] = event.target.value;
      else schedule(id);
    });
    editor.addEventListener("change", event => {
      if (event.target.matches('select[name="kind"]')) flush(id).then(renderRows, () => {});
    });
    editor.addEventListener("click", async event => {
      const target = event.target.closest("button");
      if (!target || target.closest(".row-fields")) return;
      const row = rowById(id);
      if (target.matches("[data-view]")) openViewer(row.document);
      else if (target.dataset.upload) chooseFiles(id, target.dataset.upload, target.dataset.source);
      else if (target.matches("[data-remove-receipt]")) {
        if (!window.confirm(t("Remove this receipt from the row?"))) return;
        await saveNow(id, { document_id: null });
      } else if (target.matches("[data-delete]")) {
        if (!window.confirm(t("Delete this row?"))) return;
        await deleteRow(id);
      }
    });
  }

  /* ---- saving ----------------------------------------------------------------- */

  const hasUnsaved = entry => Boolean(entry.draft || entry.pending || entry.running || entry.timer);

  function rowSave(id) {
    if (!saves.has(id)) saves.set(id, {});
    return saves.get(id);
  }

  function remember(entry, fields) {
    entry.draft = { ...entry.draft, ...fields };
    entry.generation = (entry.generation || 0) + 1;
    entry.failed = Boolean(entry.conflict);
  }

  function schedule(id, delay = 650) {
    const entry = rowSave(id);
    // Capture now, before another click can remove or rebuild this editor.
    remember(entry, collect(id));
    window.clearTimeout(entry.timer);
    entry.timer = window.setTimeout(() => { entry.timer = null; flush(id).catch(() => {}); }, delay);
    refreshSaveState();
  }

  // Keep the exact request when its outcome is unknown. Once acknowledged,
  // send any newer local values with a fresh ID and the latest row revision.
  // Rows and the report note share these persistence/error semantics.
  async function drain(entry, { validate = () => {}, body, send, saved }) {
    window.clearTimeout(entry.timer);
    entry.timer = null;
    if (entry.running) return entry.running;
    entry.running = (async () => {
      while (entry.draft || entry.pending) {
        if (entry.conflict) throw new Error(t("This row changed on another screen. Your edits are unsaved; reload before editing again."));
        if (!entry.pending) {
          validate(entry.draft);
          entry.pending = { body: body(entry.draft), generation: entry.generation };
        }
        const pending = entry.pending;
        const next = await send(pending.body);
        entry.pending = null;
        entry.failed = false;
        if (entry.generation === pending.generation) entry.draft = null;
        accept(next);
        saved();
      }
    })();
    refreshSaveState();
    try {
      await entry.running;
    } catch (error) {
      // Validation rejection is definite: the corrected fields need a new
      // request. A transport/server failure may have committed: retry its ID.
      if (error.status && error.status < 500) entry.pending = null;
      if (error.status === 409) entry.conflict = true;
      entry.failed = true;
      message(entry.conflict
        ? t("This row changed on another screen. Your edits are unsaved; reload before editing again.")
        : errorText(error));
      throw error;
    } finally {
      entry.running = null;
      refreshSaveState();
    }
  }

  async function flush(id, fields = null) {
    const entry = rowSave(id);
    if (fields) remember(entry, fields);
    if (!rowById(id) && hasUnsaved(entry)) entry.conflict = true;
    return drain(entry, {
      validate(current) {
        if (cashRowFields.invalid(current)) throw new Error(t("Enter amounts with at most two decimal places."));
      },
      body: current => ({ submission_id: cashUUID(), rev: rowById(id).rev, ...current }),
      send: body => api(`/api/rows/${encodeURIComponent(id)}`, { method: "PATCH", body }),
      saved: () => afterSave(id),
    });
  }

  function leaveOpenRow() {
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

  function afterSave(id) {
    refreshRowSummary(id);
    renderSummary();
    renderActions();
    renderBulk();
    if (admin) renderAdmin();
    if (![...saves.values(), noteSave].some(entry => entry.failed)
        && $("#er-message").classList.contains("danger")) message("");
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
    // Keep draining until edits made during an awaited request are saved too.
    while ([...saves.values(), noteSave].some(hasUnsaved)) {
      for (const [id, entry] of saves) {
        if (hasUnsaved(entry)) await flush(id);
      }
      if (hasUnsaved(noteSave)) await saveNote();
    }
  }

  async function saveNote() {
    return drain(noteSave, {
      body: current => ({ submission_id: cashUUID(), ...current }),
      send: body => api("/api/report", { method: "PATCH", body }),
      saved: () => {
        renderActions();
        if (![...saves.values(), noteSave].some(entry => entry.failed)) message("");
      },
    });
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
      await flush(target.rowId);
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
        · ${esc(pair(item.usd, item.mxn))}</span>
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
        $("#er-settlement-preview").innerHTML = `<p>Return to the safe: <strong>${esc(pair(p.return_usd, p.return_mxn))}</strong>.</p>
          ${p.requires_reimbursement_addendum ? `<p>Reimburse the recipient: <strong>${esc(pair(p.reimburse_usd, p.reimburse_mxn))}</strong>.</p>` : ""}
          ${p.rate_mxn_per_usd ? `<p>Currency exchange: USD ${esc(cashMoney(p.exchange_usd))} for MXN ${esc(cashMoney(p.exchange_mxn))}
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
      <span class="er-activity-amount">${esc(pair(item.usd, item.mxn))}</span>
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
    remember(noteSave, { note: $("#er-note").value });
    window.clearTimeout(noteSave.timer);
    noteSave.timer = window.setTimeout(() => { noteSave.timer = null; saveNote().catch(() => {}); }, 650);
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
    const unsaved = [...saves.values(), noteSave].some(hasUnsaved);
    if (unsaved) { event.preventDefault(); event.returnValue = ""; }
  });

  load().catch(error => message(errorText(error)));
})();
