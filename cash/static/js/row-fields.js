/* Shared row fields for receipts: the expense report and direct expenses in
   Review enter money the same way. Signed USD and MXN (negative is change
   received), a receipt total only when the signs differ, and either codes or
   split parts that add up to the row. Pages own everything around the fields:
   the receipt, saving, and row actions. Values are decimal strings. */
(function exposeRowFields(global) {
  const esc = value => global.cashEsc(value);
  const plain = (key, values = {}) => key.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");

  // Decimal string -> cents; blank is zero; NaN when malformed.
  function cents(text) {
    const value = String(text ?? "").trim();
    if (!value) return 0;
    if (!/^[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(value)) return NaN;
    return Math.round(Number(value) * 100);
  }
  const inputValue = text => (text == null || Number(text) === 0 ? "" : text);
  const mixed = (usd, mxn) => (usd > 0 && mxn < 0) || (mxn > 0 && usd < 0);
  const changeCurrency = (usd, mxn) => usd > 0 && mxn < 0 ? "MXN" : mxn > 0 && usd < 0 ? "USD" : null;
  const pair = (usd, mxn) => `USD ${global.cashMoney(usd)} · MXN ${global.cashMoney(mxn)}`;

  function options(list, key, selected, t) {
    return `<option value="">${esc(t("Choose…"))}</option>` + list.map(item =>
      `<option value="${esc(item[key])}" ${item[key] === selected ? "selected" : ""}>
        ${esc(item.name)} · ${esc(item[key])}</option>`).join("");
  }

  function codeFields(line, { t, codes, disabled }) {
    return `<label><span>${esc(t("Class code"))}</span><select name="class_code" ${disabled}>
        ${options(codes.classes, "class_code", line.class_code, t)}</select></label>
      <label><span>${esc(t("Account code"))}</span><select name="expense_account" ${disabled}>
        ${options(codes.expense_accounts, "account_id", line.expense_account, t)}</select></label>`;
  }

  function splitHTML(row, { t, disabled }) {
    return `<div class="rf-split-summary">
      <strong>${esc(t("Split into {count} parts", { count: row.splits.length }))}</strong>
      <span>${esc(pair(row.usd, row.mxn))}</span>
      ${disabled ? "" : `<button type="button" class="text-button" data-edit-split>${esc(t("Edit split"))}</button>
        <button type="button" class="text-button danger" data-unsplit>${esc(t("Undo split"))}</button>`}
    </div>`;
  }

  /* The fields for one row. settings: {t, codes: {classes, expense_accounts},
     disabled, show: {date, vendor, purpose, project}, extra: HTML inside the grid}.
     Project is optional and offered to administrators only; suggestions come
     from a <datalist id="project-options"> on the page. */
  function html(row, { t = plain, codes, disabled = false, show = {}, extra = "" } = {}) {
    const settings = { t, codes, disabled: disabled ? "disabled" : "" };
    const d = settings.disabled;
    const usd = cents(row.usd), mxn = cents(row.mxn);
    const { date = true, vendor = true, purpose = true, project = false } = show;
    return `<div class="row-fields" data-split-parts="${esc(JSON.stringify(row.splits || []))}">
      <div class="form-grid two">
        ${date ? `<label><span>${esc(t("Date"))}</span><input type="date" name="date" value="${esc(row.date || "")}" ${d}></label>` : ""}
        ${vendor ? `<label><span>${esc(t("Vendor"))}</span><input name="vendor" maxlength="60" value="${esc(row.vendor || "")}" ${d}></label>` : ""}
        ${purpose ? `<label class="full"><span>${esc(t(row.splits?.length
          ? "Overall receipt description" : "Expense description"))}</span>
          <input name="purpose" maxlength="120" value="${esc(row.purpose || "")}" ${d}></label>` : ""}
        <label><span>USD</span><input name="usd" inputmode="decimal" value="${esc(inputValue(row.usd))}" ${d}></label>
        <label><span>MXN</span><input name="mxn" inputmode="decimal" value="${esc(inputValue(row.mxn))}" ${d}></label>
        <p class="field-help full">${esc(t("Use a negative amount for change received."))}</p>
        <label class="rf-receipt-total" ${mixed(usd, mxn) ? "" : "hidden"}><span data-receipt-total-label>${esc(
          t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) || "MXN" }))}</span>
          <input name="receipt_total" inputmode="decimal" value="${esc(inputValue(row.receipt_total))}" ${d}></label>
        ${row.splits?.length ? "" : codeFields(row, settings)}
        ${project ? `<label><span>${esc(t("Project (optional)"))}</span><input name="project"
          list="project-options" autocomplete="off" value="${esc(row.project || "")}" ${d}></label>` : ""}
        ${extra}
      </div>
      ${row.splits?.length ? splitHTML(row, settings) : ""}
      ${d || row.splits?.length ? "" : `<p class="rf-split-tools"><button type="button" class="text-button"
        data-split-start>${esc(t("Split receipt"))}</button></p>`}
    </div>`;
  }

  // Current values, in the API's shape.
  function collect(root) {
    const fields = root.querySelector(".row-fields");
    const value = name => fields.querySelector(`:scope > .form-grid [name="${name}"]`)?.value;
    const result = { usd: value("usd") ?? "", mxn: value("mxn") ?? "", receipt_total: value("receipt_total") ?? "" };
    for (const name of ["date", "vendor", "purpose", "project"]) {
      const found = value(name);
      if (found !== undefined) result[name] = found;
    }
    const parts = JSON.parse(fields.dataset.splitParts || "[]");
    if (parts.length) {
      result.splits = parts;
    } else {
      Object.assign(result, { class_code: value("class_code") || null,
        expense_account: value("expense_account") || null, splits: [] });
    }
    return result;
  }

  function invalid(fields) {
    const amounts = [fields.usd, fields.mxn, fields.receipt_total,
      ...(fields.splits || []).flatMap(split => [split.usd, split.mxn, split.receipt_total])];
    return amounts.some(amount => Number.isNaN(cents(amount)));
  }

  // Show or hide what depends on the amounts without re-rendering inputs.
  function refresh(root, t = plain) {
    const fields = root.querySelector(".row-fields");
    if (!fields) return;
    const usd = cents(fields.querySelector('[name="usd"]').value);
    const mxn = cents(fields.querySelector('[name="mxn"]').value);
    // Mark amounts that are not valid money so the field itself shows it.
    fields.querySelectorAll(':scope > .form-grid input[inputmode="decimal"]').forEach(input => {
      if (Number.isNaN(cents(input.value))) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    });
    const total = fields.querySelector(".rf-receipt-total");
    total.hidden = !mixed(usd, mxn);
    total.querySelector("[data-receipt-total-label]").textContent =
      t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) || "MXN" });
  }

  function openSplitWorkspace({ fields, codes, preview, t, onDone }) {
    const draft = { ...fields, splits: fields.splits.length
      ? fields.splits.map(part => ({ ...part }))
      : [0, 1].map(() => ({ id: global.cashUUID(), usd: "", mxn: "",
        receipt_total: "", purpose: "", class_code: fields.class_code,
        expense_account: fields.expense_account })) };
    let selected = 0;
    const dialog = global.document.createElement("dialog");
    dialog.className = "split-workspace";
    dialog.setAttribute("aria-label", t("Split receipt"));
    global.document.body.appendChild(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    const codeName = (items, key, value) =>
      items.find(item => item[key] === value)?.name || value || t("Needs codes");
    const field = (label, name, value, extra = "") =>
      `<label><span>${esc(label)}</span><input name="${name}" value="${esc(inputValue(value))}" ${extra}></label>`;

    function refreshWorkspace() {
      const usd = cents(draft.usd), mxn = cents(draft.mxn);
      const face = cents(draft.receipt_total);
      const isMixed = mixed(usd, mxn);
      const faceField = dialog.querySelector(".sfw-face");
      faceField.hidden = !isMixed;
      faceField.querySelector("span").textContent = t("Receipt total ({currency})", {
        currency: changeCurrency(usd, mxn) || "MXN" });
      dialog.querySelector(".sfw-part-face").hidden = !isMixed;
      dialog.querySelector(".sfw-part-face span").textContent = t("Receipt total ({currency})", {
        currency: changeCurrency(usd, mxn) || "MXN" });
      const sums = ["usd", "mxn", "receipt_total"].map(key =>
        draft.splits.reduce((sum, part) => sum + cents(part[key]), 0));
      const left = [usd - sums[0], mxn - sums[1], face - sums[2]];
      const invalid = [usd, mxn, ...draft.splits.flatMap(part =>
        [cents(part.usd), cents(part.mxn)]),
      ...(isMixed ? [face, ...draft.splits.map(part => cents(part.receipt_total))] : [])]
        .some(Number.isNaN);
      dialog.querySelector(".sfw-balance").textContent = invalid
        ? t("Enter amounts with at most two decimal places.")
        : left[0] || left[1] || (isMixed && left[2])
          ? `${t("Still to assign:")} ${pair(left[0] / 100, left[1] / 100)}` +
            (isMixed ? ` · ${t("Receipt total")}: ${global.cashMoney(left[2] / 100)}` : "")
          : t("The parts add up.");
      draft.splits.forEach((part, index) => {
        const card = dialog.querySelector(`[data-sfw-select="${index}"]`);
        card.querySelector(".sfw-card-amount").textContent = pair(part.usd, part.mxn);
        card.querySelector(".sfw-card-codes").textContent =
          `${codeName(codes.classes, "class_code", part.class_code)} · ` +
          codeName(codes.expense_accounts, "account_id", part.expense_account);
      });
    }

    function render() {
      const part = draft.splits[selected];
      dialog.innerHTML = `<div class="sfw-header"><strong>${esc(t("Split receipt"))}</strong>
        <button type="button" class="sfw-close" data-sfw-cancel aria-label="${esc(t("Close"))}">×</button></div>
        <div class="sfw-layout"><section class="sfw-preview" aria-label="${esc(t("Receipt"))}">
          ${preview?.url ? `<div class="sfw-preview-title">${esc(preview.name || t("Receipt"))}
            <a href="${esc(preview.url)}" target="_blank" rel="noopener">${esc(t("Open full size"))}</a></div>
            <iframe src="${esc(preview.url)}#page=1&view=FitH" title="${esc(t("Receipt"))}"></iframe>`
          : `<div class="sfw-no-receipt">${esc(t("No receipt attached"))}</div>`}
        </section><section class="sfw-main" aria-label="${esc(t("Split amounts"))}">
          <h2>${esc(t("Receipt amounts"))}</h2>
          <div class="sfw-totals">
            ${field("USD", "usd", draft.usd, 'inputmode="decimal" data-sfw-total')}
            ${field("MXN", "mxn", draft.mxn, 'inputmode="decimal" data-sfw-total')}
            <label class="sfw-face"><span></span><input name="receipt_total" inputmode="decimal"
              value="${esc(inputValue(draft.receipt_total))}" data-sfw-total></label>
          </div><p class="field-help">${esc(t("Use a negative amount for change received."))}</p>
          <div class="sfw-balance" role="status"></div>
          <h2>${esc(t("Allocations"))}</h2>
          <div class="sfw-cards">${draft.splits.map((split, index) =>
            `<button type="button" class="sfw-card${index === selected ? " selected" : ""}"
              data-sfw-select="${index}" aria-pressed="${index === selected}">
              <strong>${esc(t("Split {number}", { number: index + 1 }))}</strong>
              <span class="sfw-card-amount"></span><small class="sfw-card-codes"></small>
            </button>`).join("")}</div>
          <button type="button" class="btn ghost sfw-add" data-sfw-add>${esc(t("Add another split"))}</button>
        </section><aside class="sfw-detail" aria-label="${esc(t("Split details"))}">
          <h2>${esc(t("Split {number}", { number: selected + 1 }))}</h2>
          ${field("USD", "usd", part.usd, 'inputmode="decimal" data-sfw-part')}
          ${field("MXN", "mxn", part.mxn, 'inputmode="decimal" data-sfw-part')}
          <label class="sfw-part-face"><span></span><input name="receipt_total"
            inputmode="decimal" value="${esc(inputValue(part.receipt_total))}" data-sfw-part></label>
          ${field(t("Expense description"), "purpose", part.purpose, "data-sfw-part")}
          ${codeFields(part, { t, codes, disabled: "" })}
          <button type="button" class="text-button" data-sfw-apply-all>${esc(t("Apply codes to all splits"))}</button>
          ${draft.splits.length > 2 ? `<button type="button" class="text-button danger"
            data-sfw-remove>${esc(t("Remove part"))}</button>` : ""}
        </aside></div><footer class="sfw-footer"><output class="sfw-error" role="alert"></output>
          <button type="button" class="btn ghost" data-sfw-cancel>${esc(t("Cancel"))}</button>
          <button type="button" class="btn primary" data-sfw-done>${esc(t("Done"))}</button></footer>`;
      refreshWorkspace();
    }

    dialog.addEventListener("input", event => {
      const input = event.target;
      if (input.matches("[data-sfw-total]")) draft[input.name] = input.value;
      if (input.matches("[data-sfw-part]")) draft.splits[selected][input.name] = input.value;
      refreshWorkspace();
      dialog.querySelector(".sfw-error").textContent = "";
    });
    dialog.addEventListener("change", event => {
      if (event.target.matches('.sfw-detail select')) {
        draft.splits[selected][event.target.name] = event.target.value || null;
        refreshWorkspace();
      }
    });
    dialog.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.matches("[data-sfw-cancel]")) return dialog.close();
      if (button.dataset.sfwSelect !== undefined) {
        selected = Number(button.dataset.sfwSelect); render();
        dialog.querySelector(".sfw-detail input")?.focus(); return;
      }
      if (button.matches("[data-sfw-add]")) {
        draft.splits.push({ id: global.cashUUID(), usd: "", mxn: "",
          receipt_total: "", purpose: "", class_code: null, expense_account: null });
        selected = draft.splits.length - 1; render();
        dialog.querySelector(".sfw-detail input")?.focus(); return;
      }
      if (button.matches("[data-sfw-remove]")) {
        draft.splits.splice(selected, 1);
        selected = Math.min(selected, draft.splits.length - 1); render();
        dialog.querySelector(".sfw-detail input")?.focus(); return;
      }
      if (button.matches("[data-sfw-apply-all]")) {
        const { class_code, expense_account } = draft.splits[selected];
        draft.splits.forEach(part => Object.assign(part, { class_code, expense_account }));
        refreshWorkspace(); return;
      }
      if (!button.matches("[data-sfw-done]")) return;
      const usd = cents(draft.usd), mxn = cents(draft.mxn);
      const isMixed = mixed(usd, mxn);
      const amounts = [draft.usd, draft.mxn,
        ...draft.splits.flatMap(part => [part.usd, part.mxn]),
        ...(isMixed ? [draft.receipt_total,
          ...draft.splits.map(part => part.receipt_total)] : [])];
      const error = dialog.querySelector(".sfw-error");
      if (amounts.some(value => Number.isNaN(cents(value)))) {
        error.textContent = t("Enter amounts with at most two decimal places."); return;
      }
      if (usd <= 0 && mxn <= 0) {
        error.textContent = t("Enter the receipt amount before finishing the split."); return;
      }
      if (draft.splits.reduce((sum, part) => sum + cents(part.usd), 0) !== usd ||
          draft.splits.reduce((sum, part) => sum + cents(part.mxn), 0) !== mxn) {
        error.textContent = t("The split amounts must add up to the receipt."); return;
      }
      if (isMixed && (cents(draft.receipt_total) <= 0 ||
          draft.splits.reduce((sum, part) => sum + cents(part.receipt_total), 0) !==
          cents(draft.receipt_total))) {
        error.textContent = t("The split receipt totals must add up to the receipt total."); return;
      }
      if (!isMixed) {
        draft.receipt_total = "";
        draft.splits.forEach(part => { part.receipt_total = ""; });
      }
      dialog.close();
      onDone(draft);
    });
    render();
    dialog.showModal();
    dialog.querySelector(".sfw-totals input")?.focus();
  }

  /* Wire a rendered row. onInput runs after typing, onChange after a select
     changes, and onStructure receives the new fields when a split is started,
     extended, shortened, or undone; the page saves and re-renders. */
  function wire(root, { t = plain, codes, preview, onInput = () => {}, onChange = onInput, onStructure }) {
    const fields = root.querySelector(".row-fields");
    if (!fields) return;
    refresh(root, t);
    fields.addEventListener("input", () => { refresh(root, t); onInput(); });
    fields.addEventListener("change", event => { if (event.target.matches("select")) onChange(); });
    fields.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button) return;
      const current = collect(root);
      if (button.matches("[data-split-start], [data-edit-split]")) {
        openSplitWorkspace({ fields: current, codes, preview, t, onDone: onStructure });
      } else if (button.matches("[data-unsplit]")) {
        const first = current.splits[0] || {};
        onStructure({ ...current, splits: [],
          class_code: first.class_code || null, expense_account: first.expense_account || null });
      }
    });
  }

  global.cashRowFields = { cents, mixed, changeCurrency, pair, inputValue, html, collect, invalid, refresh, wire };
})(window);
