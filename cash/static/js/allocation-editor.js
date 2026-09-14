/* Shared account allocation fields for receipt review. */
window.cashAllocationEditor = function ({getOptions, markDirty, showMessage}) {
  const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const decimal = minor => ((Number(minor) || 0) / 100).toFixed(2);
  function accountOptions(selected = "") {
    return `<option value="">Choose account code</option>` +
      getOptions().expense_accounts.map(account =>
        `<option value="${esc(account.account_id)}" ${account.account_id === selected ? "selected" : ""}>${esc(account.name)} · ${esc(account.account_id)}</option>`).join("");
  }

  function classOptions(selected = "") {
    return `<option value="">Choose class code</option>` +
      getOptions().classes.map(item =>
        `<option value="${esc(item.class_code)}" ${item.class_code === selected ? "selected" : ""}>${esc(item.name)} · ${esc(item.class_code)}</option>`).join("");
  }

  function allocationRow(allocation = {}, { faceParts = null, directExpense = false } = {}) {
    const faceUsd = allocation.face_usd ??
      (allocation.face_usd_cents != null ? decimal(allocation.face_usd_cents) :
       allocation.receipt_face_usd_cents == null ? "" : decimal(allocation.receipt_face_usd_cents));
    const faceMxn = allocation.face_mxn ??
      (allocation.face_mxn_centavos != null ? decimal(allocation.face_mxn_centavos) :
       allocation.receipt_face_mxn_centavos == null ? "" : decimal(allocation.receipt_face_mxn_centavos));
    const receiptFields = faceParts
      ? `${faceParts.usd ? `<label><span>Receipt value, USD portion</span><input name="face_usd" inputmode="decimal" value="${esc(faceUsd)}"></label>` : ""}
         ${faceParts.mxn ? `<label><span>Receipt value, MXN portion</span><input name="face_mxn" inputmode="decimal" value="${esc(faceMxn)}"></label>` : ""}`
      : "";
    if (directExpense) return `<div class="allocation-row direct-allocation-row">
      <div class="allocation-row-head allocation-row-controls" hidden>
        <button type="button" class="text-button danger remove-allocation">Remove split</button></div>
      <div class="form-grid direct-allocation-fields">
        <label><span>USD used</span><input name="usd" inputmode="decimal"
          value="${Math.max(0, Number(allocation.usd ?? decimal(allocation.usd_cents))).toFixed(2)}" required></label>
        <label><span>MXN used</span><input name="mxn" inputmode="decimal"
          value="${Math.max(0, Number(allocation.mxn ?? decimal(allocation.mxn_centavos))).toFixed(2)}" required></label>
        <label><span>Class code</span><select name="class_code" required>
          ${classOptions(allocation.class_code || "")}
        </select></label>
        <label><span>Account code</span><select name="expense_account" required>
          ${accountOptions(allocation.expense_account || allocation.to_account || "")}
        </select></label>
        <label><span>Project</span><input name="project" list="project-options"
          autocomplete="off"
          value="${esc(allocation.project || "")}"></label>
        <details class="full" ${Number(faceUsd) || Number(faceMxn) || Number(allocation.usd ?? decimal(allocation.usd_cents)) < 0 || Number(allocation.mxn ?? decimal(allocation.mxn_centavos)) < 0 ? "open" : ""}>
          <summary>Change or currency conversion</summary>
          <p class="field-help">Enter change received in addition to the amounts above. A USD payment with MXN change needs the MXN purchase value.</p>
          <div class="form-grid two">
            <label><span>USD change received</span><input name="usd_change" inputmode="decimal"
              value="${Math.max(0, -Number(allocation.usd ?? decimal(allocation.usd_cents))).toFixed(2)}"></label>
            <label><span>MXN change received</span><input name="mxn_change" inputmode="decimal"
              value="${Math.max(0, -Number(allocation.mxn ?? decimal(allocation.mxn_centavos))).toFixed(2)}"></label>
            ${receiptFields}
          </div>
        </details>
        <input type="hidden" name="description" value="${esc(allocation.description || "")}">
      </div>
    </div>`;
    return `<div class="allocation-row">
      <div class="allocation-row-head allocation-row-controls" hidden>
        <button type="button" class="text-button danger remove-allocation">Remove split</button></div>
      <div class="form-grid two">
        <label><span>Account code</span><select name="expense_account" required>
          ${accountOptions(allocation.expense_account || allocation.to_account || "")}
        </select></label>
        <label><span>Class code</span><select name="class_code" required>
          ${classOptions(allocation.class_code || "")}
        </select></label>
        <label><span>USD in this split</span><input name="usd" inputmode="decimal"
          value="${esc(allocation.usd ?? decimal(allocation.usd_cents))}" required></label>
        <label><span>MXN in this split</span><input name="mxn" inputmode="decimal"
          value="${esc(allocation.mxn ?? decimal(allocation.mxn_centavos))}" required></label>
        ${receiptFields}
        <label><span>Project</span><input name="project" list="project-options"
          autocomplete="off"
          value="${esc(allocation.project || "")}"></label>
      </div>
      <label><span>Description</span><input name="description"
        value="${esc(allocation.description || "")}"></label>
    </div>`;
  }

  function wireAllocationEditor(container, defaults, { faceParts = null } = {}) {
    const list = container.querySelector(".allocation-list");
    const updateControls = () => {
      const rows = [...list.querySelectorAll(".allocation-row")];
      rows.forEach(row => {
        row.querySelector(".allocation-row-controls").hidden = rows.length === 1;
        row.classList.toggle("is-split", rows.length > 1);
      });
    };
    container.querySelector(".add-allocation")?.addEventListener("click", () => {
      list.insertAdjacentHTML("beforeend",
        allocationRow(defaults(), { faceParts }));
      updateControls();
      markDirty();
    });
    list.addEventListener("click", event => {
      if (!event.target.closest(".remove-allocation")) return;
      if (list.querySelectorAll(".allocation-row").length === 1) {
        showMessage("At least one account code is required.", "warning");
        return;
      }
      event.target.closest(".allocation-row").remove();
      updateControls();
      markDirty();
    });
    updateControls();
  }

  function readAllocations(form) {
    return [...form.querySelectorAll(".allocation-row")].map(row => {
      const net = currency => {
        const amount = row.querySelector(`[name="${currency}"]`)?.value || "0";
        const change = row.querySelector(`[name="${currency}_change"]`);
        return change ? (Number(amount) - Number(change.value || 0)).toFixed(2) : amount;
      };
      return {
      expense_account: row.querySelector('[name="expense_account"]').value,
      class_code: row.querySelector('[name="class_code"]').value || null,
      usd: net("usd"),
      mxn: net("mxn"),
      face_usd: row.querySelector('[name="face_usd"]')?.value || null,
      face_mxn: row.querySelector('[name="face_mxn"]')?.value || null,
      project: row.querySelector('[name="project"]').value || "",
      description: row.querySelector('[name="description"]').value || "",
    }; });
  }
  return {allocationRow, wireAllocationEditor, readAllocations};
};
