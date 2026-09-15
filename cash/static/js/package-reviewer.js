/* Receipt review used in Review and the administrator signout workspace. */
window.cashRenderPackage = function ({state, els, esc, workflowStatus, allocationRow,
  wireAllocationEditor, readAllocations, request, markDirty, markDirtyEvent,
  showMessage, refreshSelected, afterCommit, onDocument, navigation = ""}) {
  function packageAllocationDefaults(claim) {
    const faceUsd = claim.face_usd_cents ?? 0;
    const faceMxn = claim.face_mxn_centavos ?? 0;
    return {
      expense_account: "",
      class_code: "",
      usd_cents: claim.usd_paid_cents - claim.usd_change_cents,
      mxn_centavos: claim.mxn_paid_centavos - claim.mxn_change_centavos,
      receipt_face_usd_cents: faceUsd,
      receipt_face_mxn_centavos: faceMxn,
      description: claim.description || claim.notes,
      notes: claim.notes,
    };
  }

  function renderPackage() {
    const packageData = state.detail.package;
    const claims = state.detail.claims;
    if (!claims.length) {
      els.detail.innerHTML = navigation + `<div class="row-editor-heading"><strong>No receipts in this submission</strong></div>`;
      return;
    }
    state.claimIndex = Math.max(0, Math.min(state.claimIndex, claims.length - 1));
    const claim = claims[state.claimIndex];
    onDocument(claim.document);
    const allocations = claim.allocations.length ? claim.allocations :
      [packageAllocationDefaults(claim)];
    const verifiedCount = claims.filter(item => item.state === "verified" &&
      item.verified_version === item.content_version).length;
    const reviewable = ["submitted", "in_review"].includes(packageData.status);
    const receiptRows = claims.map((item, index) => {
      const itemVerified = item.state === "verified" &&
        item.verified_version === item.content_version;
      const usdUsed = item.usd_paid_cents - item.usd_change_cents;
      const mxnUsed = item.mxn_paid_centavos - item.mxn_change_centavos;
      const selected = index === state.claimIndex;
      const summary = `<tr class="package-receipt-row${selected ? " selected" : ""}"
          data-claim-index="${index}" tabindex="0" aria-expanded="${selected}">
        <td><span class="sheet-caret" aria-hidden="true">›</span>${esc(item.claim_date)}</td>
        <td>${esc(item.vendor || "Unnamed vendor")}</td>
        <td class="review-record-details">${esc(item.description || "—")}${item.notes ? `<small>${esc(item.notes)}</small>` : ""}</td>
        <td class="num">${esc([item.face_usd_cents ?? 0, item.face_mxn_centavos ?? 0].map((value, currency) => value ? `${currency ? "MXN" : "USD"} ${cashMinorMoney(value)}` : "").filter(Boolean).join(" + ") || "—")}</td>
        <td class="num">${cashMinorMoney(usdUsed)}</td><td class="num">${cashMinorMoney(mxnUsed)}</td>
        <td><span class="status-pill ${itemVerified ? "success" : ""}">${esc(workflowStatus(item.state))}</span></td>
      </tr>`;
      if (!selected) return summary;
      const editor = reviewable ? `<form id="claim-review-form" class="review-form sheet-action-form">
        ${claim.simple_entry ? `<div class="form-grid two"><label><span>Receipt date</span><input type="date" name="receipt_date" value="${esc(claim.claim_date)}" required></label><label><span>Vendor</span><input name="receipt_vendor" value="${esc(claim.vendor)}"></label></div>` : ""}
        <section class="sheet-editor-section"><div class="section-heading"><div>
          <h3>Account and class codes</h3></div>
          <button type="button" class="btn ghost add-allocation">Add split</button></div>
          ${claim.simple_entry ? `<p class="field-help">Read the receipt and enter the purchase total below when payment and change use different currencies. The recipient’s notes explain its purpose.</p>` : ""}
          ${claim.payment_mode === "receipt_currency" ? `<p class="field-help">Recorded in the receipt's currency. The remaining currency conversion will be reconciled at signout closeout.</p>` : ""}
          ${!state.options.classes.length ? `<p class="callout danger">No class codes are available. Add one before these receipts can be approved.</p>` : ""}
          <div class="allocation-list">${allocations.map(row =>
            allocationRow(row, { faceParts: { usd: true, mxn: true } })).join("")}</div>
          <label><span>Note for the recipient</span><textarea name="admin_note" rows="2">${esc(claim.admin_note || "")}</textarea></label>
        </section>
        <div class="row-actions"><output class="form-status" role="alert"></output>
          <button type="submit" class="btn ghost" value="save">Save allocation</button>
          <button type="submit" class="btn primary" value="verify">Save &amp; mark checked</button>
        </div></form>` : `<p class="callout ${packageData.status === "accepted" ? "" : "warning"}">${packageData.status === "needs_changes" ? `This submission was returned to the recipient: ${esc(packageData.admin_summary || "Corrections requested")}` : esc(workflowStatus(packageData.status))}</p>`;
      return `${summary}<tr class="package-receipt-expansion"><td colspan="7">
        <div class="sheet-editor-section"><strong>${esc(item.description || "Receipt")}</strong>
          <p>USD ${cashMinorMoney(usdUsed)} · MXN ${cashMinorMoney(mxnUsed)} recorded${item.notes ? ` · ${esc(item.notes)}` : ""}</p></div>${editor}
        ${reviewable ? `<label class="inline-correction-note"><span>Correction needed for this receipt</span>
          <textarea id="return-item-note" rows="2">${esc(state.returnNotes[item.claim_id] || "")}</textarea></label>` : ""}
        </td></tr>`;
    }).join("");
    els.detail.innerHTML = navigation + `<div class="row-editor-heading">
        <strong>${reviewable ? "Review" : "View"} ${claims.length} submitted receipt${claims.length === 1 ? "" : "s"}</strong>
        <span>${reviewable ? `${verifiedCount} of ${claims.length} checked · ` : ""}${esc(workflowStatus(packageData.status))}</span>
      </div>
      <div class="package-sheet-scroll"><table class="package-sheet">
        <thead><tr><th>Date</th><th>Vendor</th><th>Description</th>
          <th class="num">Receipt value</th><th class="num">USD recorded</th>
          <th class="num">MXN recorded</th><th>Status</th></tr></thead>
        <tbody>${receiptRows}</tbody></table></div>
      ${reviewable ? `<section class="package-decision sheet-editor-section"><p class="eyebrow">Submission decision</p>
        <label><span>What needs to be corrected?</span><textarea id="return-summary" rows="2" ${reviewable ? "" : "disabled"}>${esc(packageData.admin_summary || "")}</textarea></label>
        <div class="package-actions">
          <button type="button" class="btn danger return-package" ${reviewable ? "" : "disabled"}>Request corrections</button>
          <button type="button" class="btn primary accept-package" ${verifiedCount === claims.length && reviewable ? "" : "disabled"}>Approve all ${claims.length} receipts</button>
        </div><output class="package-status" role="alert"></output>
      </section>` : ""}`;
    els.detail.querySelectorAll(".package-receipt-row").forEach(row => {
      row.addEventListener("click", () => {
        const nextIndex = Number(row.dataset.claimIndex);
        if (nextIndex === state.claimIndex) return;
        if (state.dirty && !window.confirm(
          "Discard unsaved changes and open another receipt?")) return;
        state.claimIndex = nextIndex;
        state.dirty = false;
        renderPackage();
      });
      row.addEventListener("keydown", event => {
        if (!["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        row.click();
      });
    });
    const note = els.detail.querySelector("#return-item-note");
    note?.addEventListener("input", () => {
      state.returnNotes[claim.claim_id] = note.value; markDirty();
    });
    const form = els.detail.querySelector("#claim-review-form");
    if (form) {
      wireAllocationEditor(form, () => ({
        expense_account: "",
        class_code: "",
      }), { faceParts: { usd: true, mxn: true } });
      markDirtyEvent(form);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const status = form.querySelector("output");
        try {
          const saved = await request(`/api/review/claims/${claim.claim_id}/allocations`, {
            method: "POST", body: { submission_id: cashUUID(),
              expected_content_version: claim.content_version,
              allocations: readAllocations(form),
              ...(claim.simple_entry ? {receipt_date: form.receipt_date.value, vendor: form.receipt_vendor.value} : {}) },
          });
          if (event.submitter?.value === "verify") {
            await request(`/api/review/claims/${claim.claim_id}/verify`, {
              method: "POST", body: { submission_id: cashUUID(),
                expected_content_version: saved.content_version || claim.content_version,
                admin_note: form.admin_note.value },
            });
            showMessage(`Receipt ${state.claimIndex + 1} marked as checked.`);
          } else showMessage("Allocation saved.");
          state.dirty = false;
          await refreshSelected();
        } catch (error) {
          status.textContent = error.message;
          status.scrollIntoView({ block: "nearest" });
        }
      });
    }
    els.detail.querySelector(".return-package")?.addEventListener("click", async () => {
      const summary = els.detail.querySelector("#return-summary").value.trim();
      const status = els.detail.querySelector(".package-status");
      if (!summary) { status.textContent = "Explain what the signee must correct."; return; }
      if (!Object.values(state.returnNotes).some(value => value.trim())) {
        status.textContent = "Add a correction note to at least one receipt."; return;
      }
      try {
        await request(`/api/review/packages/${packageData.package_id}/return`, {
          method: "POST", body: { submission_id: cashUUID(),
            admin_summary: summary, item_notes: state.returnNotes },
        });
        state.dirty = false;
        await afterCommit("Correction requests sent to the recipient.", true);
      } catch (error) { status.textContent = error.message; }
    });
    els.detail.querySelector(".accept-package")?.addEventListener("click", async () => {
      if (!window.confirm(`Approve all ${claims.length} receipts?`)) return;
      const status = els.detail.querySelector(".package-status");
      try {
        await request(`/api/review/packages/${packageData.package_id}/accept`, {
          method: "POST", body: { submission_id: cashUUID() },
        });
        await afterCommit("Receipts approved and cash records updated.", true);
      } catch (error) { status.textContent = error.message; }
    });
  }


  renderPackage();
};
