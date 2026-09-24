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

  function splitHTML(row, settings) {
    const { t, disabled } = settings;
    const usd = cents(row.usd), mxn = cents(row.mxn);
    const showUsd = usd !== 0 || mxn === 0, showMxn = mxn !== 0 || usd === 0;
    const isMixed = mixed(usd, mxn);
    return `<div class="rf-split">
      <p class="rf-split-heading"><strong>${esc(t("Split receipt"))}</strong>
        <span class="rf-split-remaining" data-split-remaining></span></p>
      ${row.splits.map((split, index) => `<div class="rf-split-part" data-split="${esc(split.id || index)}">
        ${showUsd ? `<label><span>USD</span><input name="split_usd" inputmode="decimal" value="${esc(inputValue(split.usd))}" ${disabled}></label>` : ""}
        ${showMxn ? `<label><span>MXN</span><input name="split_mxn" inputmode="decimal" value="${esc(inputValue(split.mxn))}" ${disabled}></label>` : ""}
        ${isMixed ? `<label><span>${esc(t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) }))}</span>
          <input name="split_receipt_total" inputmode="decimal" value="${esc(inputValue(split.receipt_total))}" ${disabled}></label>` : ""}
        <label class="rf-split-purpose"><span>${esc(t("What was it for?"))}</span>
          <input name="split_purpose" value="${esc(split.purpose)}" ${disabled}></label>
        ${codeFields(split, settings)}
        ${disabled || row.splits.length <= 2 ? "" : `<button type="button" class="text-button danger"
          data-remove-part="${index}">${esc(t("Remove part"))}</button>`}
      </div>`).join("")}
      ${disabled ? "" : `<p class="rf-split-tools"><button type="button" class="text-button" data-add-part>${esc(t("Add part"))}</button>
        <button type="button" class="text-button" data-unsplit>${esc(t("Undo split"))}</button></p>`}
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
    return `<div class="row-fields">
      <div class="form-grid two">
        ${date ? `<label><span>${esc(t("Date"))}</span><input type="date" name="date" value="${esc(row.date || "")}" ${d}></label>` : ""}
        ${vendor ? `<label><span>${esc(t("Vendor"))}</span><input name="vendor" value="${esc(row.vendor || "")}" ${d}></label>` : ""}
        ${purpose ? `<label class="full"><span>${esc(t("What was it for?"))}</span>
          <input name="purpose" value="${esc(row.purpose || "")}" ${d}></label>` : ""}
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
    const parts = [...fields.querySelectorAll("[data-split]")];
    if (parts.length) {
      result.splits = parts.map(part => {
        const get = name => part.querySelector(`[name="${name}"]`)?.value ?? "";
        return { id: part.dataset.split, usd: get("split_usd"), mxn: get("split_mxn"),
          receipt_total: get("split_receipt_total"), purpose: get("split_purpose"),
          class_code: get("class_code") || null, expense_account: get("expense_account") || null };
      });
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
    const total = fields.querySelector(".rf-receipt-total");
    total.hidden = !mixed(usd, mxn);
    total.querySelector("[data-receipt-total-label]").textContent =
      t("Receipt total ({currency})", { currency: changeCurrency(usd, mxn) || "MXN" });
    const remaining = fields.querySelector("[data-split-remaining]");
    if (remaining) {
      const parts = collect(root).splits;
      const left = [usd - parts.reduce((sum, part) => sum + (cents(part.usd) || 0), 0),
        mxn - parts.reduce((sum, part) => sum + (cents(part.mxn) || 0), 0)];
      remaining.textContent = left[0] || left[1]
        ? t("Still to assign: {amount}", { amount: pair(left[0] / 100, left[1] / 100) })
        : t("The parts add up.");
    }
  }

  /* Wire a rendered row. onInput runs after typing, onChange after a select
     changes, and onStructure receives the new fields when a split is started,
     extended, shortened, or undone; the page saves and re-renders. */
  function wire(root, { t = plain, onInput = () => {}, onChange = onInput, onStructure }) {
    const fields = root.querySelector(".row-fields");
    if (!fields) return;
    refresh(root, t);
    fields.addEventListener("input", () => { refresh(root, t); onInput(); });
    fields.addEventListener("change", event => { if (event.target.matches("select")) onChange(); });
    fields.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button) return;
      const current = collect(root);
      if (button.matches("[data-split-start]")) {
        const codes = { class_code: current.class_code, expense_account: current.expense_account };
        onStructure({ ...current, splits: [
          { usd: current.usd, mxn: current.mxn, receipt_total: current.receipt_total,
            purpose: current.purpose || "", ...codes },
          { usd: "", mxn: "", receipt_total: "", purpose: "", ...codes }] });
      } else if (button.matches("[data-add-part]")) {
        onStructure({ ...current, splits: [...current.splits, { usd: "", mxn: "", purpose: "" }] });
      } else if (button.dataset.removePart) {
        current.splits.splice(Number(button.dataset.removePart), 1);
        onStructure(current);
      } else if (button.matches("[data-unsplit]")) {
        const first = current.splits[0] || {};
        onStructure({ ...current, splits: [],
          class_code: first.class_code || null, expense_account: first.expense_account || null });
      }
    });
  }

  global.cashRowFields = { cents, mixed, changeCurrency, pair, inputValue, html, collect, invalid, refresh, wire };
})(window);
