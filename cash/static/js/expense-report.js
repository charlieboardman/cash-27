/* The same expense report is used by recipients and administrators. */
(() => {
  const root = document.getElementById("expense-report");
  if (!root) return;
  const id = root.dataset.voucher, admin = root.dataset.admin === "true";
  const $ = selector => root.querySelector(selector);
  const t = (key, values = {}) => !admin && window.signeeI18n ? signeeI18n.t(key, values) : key.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
  const esc = v => String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const decimal = cents => cents == null ? "" : (cents / 100).toFixed(2);
  const amount = text => {
    const value = String(text).trim() || "0";
    if (!/^[+-]?(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(value)) throw new Error(t("Enter amounts with at most two decimal places."));
    const cents = Math.round(Number(value) * 100);
    if (!Number.isSafeInteger(cents)) throw new Error(t("Enter a valid amount."));
    return cents;
  };
  const money = (usd, mxn) => `USD ${cashMinorMoney(usd)} · MXN ${cashMinorMoney(mxn)}`;
  const statusText = state => t(cashLabels.status(state));
  let view, csrf, reportVersion = 0, reportDirty = false, timer, running = null, busy = false, uploadTarget = null, uploadMode = "new";
  const dirty = new Set();
  const pendingClaims = new Map();
  let pendingReport = null;
  const settlement = $("#report-settlement");
  const show = (message, error = false) => {
    $("#report-message").hidden = !message;
    $("#report-message").className = `notice ${error ? "danger" : ""}`;
    $("#report-message").textContent = message;
  };
  async function request(path, {method = "GET", body, files} = {}) {
    const response = await fetch(path, {method, headers: {...(method !== "GET" ? {"X-CSRF": csrf} : {}), ...(body ? {"Content-Type":"application/json"} : {})}, body: files || (body ? JSON.stringify(body) : undefined)});
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const error = new Error(!admin && window.signeeI18n ? signeeI18n.error(data.error) : data.error?.message || "Request failed");
      error.httpStatus = response.status; throw error;
    }
    return data.data;
  }
  const correction = () => view.packages.find(p => p.status === "needs_changes");
  const locked = () => view.read_only || (view.report?.status === "submitted" && !correction());
  const editable = claim => !locked() && (claim.state === "draft" && !claim.package_id || claim.state === "flagged" && correction());
  function saveState(text, error = false) {
    $("#report-save-state").textContent = t(text);
    $("#report-save-state").classList.toggle("report-save-error", error);
    $("#report-retry").hidden = !error;
  }
  function claimBody(claim, card) {
    const usd = amount(card.querySelector('[name="usd"]').value), mxn = amount(card.querySelector('[name="mxn"]').value);
    const unchanged = usd === claim.usd_paid_cents - claim.usd_change_cents && mxn === claim.mxn_paid_centavos - claim.mxn_change_centavos;
    const notes = card.querySelector('[name="notes"]').value;
    return {submission_id:cashUUID(), expected_content_version:claim.content_version, document_id:claim.document_id,
      date:claim.claim_date, vendor:claim.vendor, description:claim.simple_entry ? notes : claim.description,
      notes, simple_entry:!!claim.simple_entry, payment_mode:unchanged ? claim.payment_mode : "cash",
      face_usd:decimal(claim.face_usd_cents), face_mxn:decimal(claim.face_mxn_centavos),
      usd_paid:decimal(unchanged ? claim.usd_paid_cents : Math.max(usd,0)), usd_change:decimal(unchanged ? claim.usd_change_cents : Math.max(-usd,0)),
      mxn_paid:decimal(unchanged ? claim.mxn_paid_centavos : Math.max(mxn,0)), mxn_change:decimal(unchanged ? claim.mxn_change_centavos : Math.max(-mxn,0))};
  }
  async function flush() {
    clearTimeout(timer);
    if (running) { await running; if (dirty.size || reportDirty) return flush(); return; }
    running = (async () => {
      while (dirty.size || reportDirty) {
        saveState("Saving…");
        if (dirty.size) {
          const cid = dirty.values().next().value;
          const claim = view.claims.find(c => c.claim_id === cid), card = $(`[data-claim="${cid}"]`);
          const currentBody = claimBody(claim, card);
          const body = pendingClaims.get(cid) || currentBody;
          pendingClaims.set(cid, body);
          dirty.delete(cid);
          try {
            const result = await request(`/${id}/claims/${cid}`, {method:"PATCH", body});
            Object.assign(claim, {content_version:result.content_version, notes:body.notes, description:body.description,
              usd_paid_cents:amount(body.usd_paid), usd_change_cents:amount(body.usd_change), mxn_paid_centavos:amount(body.mxn_paid), mxn_change_centavos:amount(body.mxn_change), payment_mode:body.payment_mode});
            pendingClaims.delete(cid);
            if (["usd_paid","usd_change","mxn_paid","mxn_change","notes"].some(key => currentBody[key] !== body[key])) dirty.add(cid);
          } catch (error) {
            if (error.httpStatus && error.httpStatus < 500) pendingClaims.delete(cid);
            dirty.add(cid); throw error;
          }
        } else {
          const currentBody = {submission_id:cashUUID(), expected_version:reportVersion};
          for (const key of ["cash_usd","cash_mxn","personal_usd","personal_mxn","discrepancy"]) currentBody[key] = settlement.elements[key].value;
          const body = pendingReport || currentBody;
          pendingReport = body;
          reportDirty = false;
          try {
            view.report = await request(`/${id}/report/save`, {method:"POST", body});
            reportVersion = view.report.version;
            pendingReport = null;
            if (["cash_usd","cash_mxn","personal_usd","personal_mxn","discrepancy"].some(key => body[key] !== currentBody[key])) reportDirty = true;
          } catch (error) {
            if (error.httpStatus && error.httpStatus < 500) pendingReport = null;
            reportDirty = true; throw error;
          }
        }
      }
      saveState("All changes saved");
      if ($("#report-message").classList.contains("danger")) show("");
    })();
    try { await running; }
    catch (error) { saveState("Changes not saved", true); show(error.message, true); throw error; }
    finally { running = null; }
  }
  const schedule = () => { saveState("Unsaved changes"); clearTimeout(timer); timer = setTimeout(() => flush().catch(() => {}), 650); };
  function balances() {
    if (!view) return;
    let usd = amount(view.authoritative?.usd || "0"), mxn = amount(view.authoritative?.mxn || "0");
    try {
      for (const claim of view.claims.filter(c => c.state !== "accepted")) {
        const card = $(`[data-claim="${claim.claim_id}"]`);
        usd -= amount(card.querySelector('[name="usd"]').value); mxn -= amount(card.querySelector('[name="mxn"]').value);
      }
      $("#report-balance").textContent = money(usd,mxn);
      if (view.settlement_confirmed) {
        $("#report-proposal").textContent = t(view.read_only ? "Settlement complete. No further cash handoff is needed." : "The cash settlement is confirmed. Signed paperwork is still needed to complete the signout.");
        return;
      }
      const cu = amount(settlement.elements.cash_usd.value), cm = amount(settlement.elements.cash_mxn.value), pu = amount(settlement.elements.personal_usd.value), pm = amount(settlement.elements.personal_mxn.value);
      if (Math.min(cu,cm,pu,pm) < 0) throw new Error(t("Cash left and personal money must be zero or positive"));
      const lines = [];
      if (cu || cm) lines.push(t("Cash to return: {amount}", {amount:money(cu,cm)}));
      if (pu || pm) lines.push(t("Reimbursement requested: {amount}", {amount:money(pu,pm)}));
      if (!cu && !cm && !pu && !pm) lines.push(t("No cash return or reimbursement reported."));
      if (usd + pu !== cu || mxn + pm !== cm) lines.push(t("Your cash count differs from the receipt entries. This may reflect a currency exchange or something to explain. Your administrator will review it."));
      if (settlement.elements.discrepancy.value.trim()) lines.push(t("Your discrepancy will be included for review."));
      $("#report-proposal").innerHTML = lines.map(line => `<p>${esc(line)}</p>`).join("");
    } catch (error) { $("#report-proposal").textContent = error.message; }
  }
  function render() {
    freezeInputs(false);
    const isLocked = locked();
    const pending = view.packages.find(p => ["submitted","in_review","needs_changes"].includes(p.status));
    $("#report-state").textContent = view.read_only ? statusText(view.voucher.status)
      : view.settlement_confirmed ? t("Awaiting signed paperwork")
      : pending ? statusText(pending.status)
      : view.report?.status === "submitted" ? t("Awaiting cash settlement") : t("Draft");
    $("#report-party").textContent = `${view.voucher.recipient} · ${view.voucher.purpose}`;
    const advances = view.activity.filter(a => ["signout","addendum"].includes(a.kind) && (a.usd_change_cents > 0 || a.mxn_change_centavos > 0));
    $("#report-issued").textContent = money(advances.reduce((sum,a) => sum+a.usd_change_cents,0),advances.reduce((sum,a) => sum+a.mxn_change_centavos,0));
    $("#report-activity").innerHTML = view.activity.map(a => `<li>${esc(a.date)} · ${esc(t(a.label))} · ${esc(money(a.usd_change_cents,a.mxn_change_centavos))}</li>`).join("");
    $("#report-claims").innerHTML = view.claims.map((claim,index) => {
      const canEdit = editable(claim), disabled = canEdit ? "" : "disabled";
      const usd = claim.usd_paid_cents - claim.usd_change_cents, mxn = claim.mxn_paid_centavos - claim.mxn_change_centavos;
      return `<article class="report-receipt" data-claim="${esc(claim.claim_id)}">
        <button type="button" class="report-thumb" data-action="view" aria-label="${esc(t("View receipt {number}",{number:index+1}))}"><img src="/${id}/documents/${claim.document_id}/thumbnail" alt="" loading="lazy"><span>${esc(t("View receipt"))}</span></button>
        <div><div class="report-receipt-heading"><strong>${esc(t("Receipt {number}",{number:index+1}))}</strong><span class="status-pill">${esc(statusText(claim.state))}</span></div>
        <div class="report-money"><label>USD<input name="usd" inputmode="decimal" value="${usd ? decimal(usd) : ""}" ${disabled}></label><label>MXN<input name="mxn" inputmode="decimal" value="${mxn ? decimal(mxn) : ""}" ${disabled}></label></div>
        ${claim.admin_note ? `<div class="report-correction">${esc(claim.admin_note)}</div>` : ""}</div>
        <label class="report-notes" style="grid-column:1/-1"><span>${esc(t("What was this expense for?"))} <small>${esc(t("Required"))}</small></span><textarea name="notes" rows="2" ${disabled}>${esc(claim.notes)}</textarea></label>
        ${canEdit ? `<div class="report-tools" style="grid-column:1/-1"><button type="button" class="text-button" data-action="append">${esc(t("Add page"))}</button><button type="button" class="text-button" data-action="replace">${esc(t("Replace receipt"))}</button>${claim.state === "draft" ? `<button type="button" class="text-button" data-action="remove">${esc(t("Remove receipt"))}</button>` : ""}</div>` : ""}
      </article>`;
    }).join("") || `<p class="report-empty">${esc(t("Add your first receipt to start your expense report."))}</p>`;
    $("#report-claims").querySelectorAll("img").forEach(img => { img.addEventListener("error", () => { img.hidden = true; }); });
    for (const key of ["cash_usd","cash_mxn","personal_usd","personal_mxn"]) settlement.elements[key].value = view.report ? decimal(view.report[key]) : "";
    settlement.elements.discrepancy.value = view.report?.discrepancy || "";
    $("#report-personal").open = !!(view.report?.personal_usd || view.report?.personal_mxn);
    $("#report-problem").open = !!view.report?.discrepancy;
    $("#report-settlement-fields").disabled = isLocked;
    $("#report-add").hidden = isLocked;
    $("#report-submit").hidden = isLocked;
    $("#report-submit").textContent = t(correction() ? "Submit corrections" : "Submit expense report");
    $("#report-reopen").hidden = view.read_only || !isLocked || pending?.status === "in_review";
    $("#report-history").innerHTML = view.packages.map(p => `<li><strong>${esc(t("Submission"))} ${p.seq} · ${esc(statusText(p.status))}</strong>${p.admin_summary ? `<p>${esc(p.admin_summary)}</p>` : ""}</li>`).join("");
    if (pending?.admin_summary && pending.status === "needs_changes") show(pending.admin_summary);
    else if (view.read_only) show(view.voucher.status === "void" ? t("This signout is void. Its records remain available to view.") : t("This report is complete. Receipts and settlement remain available to view."));
    else if (isLocked) show(t("Your report has been submitted. Your administrator will review the receipts and confirm the cash settlement."));
    balances();
  }
  function freezeInputs(freeze) {
    root.querySelectorAll("input, textarea, #report-add, .report-tools button").forEach(element => {
      if (freeze) {element.dataset.wasDisabled = String(element.disabled); element.disabled = true;}
      else if (element.dataset.wasDisabled != null) {element.disabled = element.dataset.wasDisabled === "true"; delete element.dataset.wasDisabled;}
    });
  }
  async function load() {
    view = await request(`/${id}/state`); csrf = view.csrf; reportVersion = view.report?.version || 0; render();
  }
  const picker = createOrderedDocumentPicker($("#report-picker"), {translate:t, onError:message => show(message,true)});
  function openUpload(mode = "new", claim = null) {
    uploadMode = mode; uploadTarget = claim; picker.clear();
    $("#report-upload").hidden = false;
    $("#report-upload-title").textContent = t(mode === "append" ? "Add page" : mode === "replace" ? "Replace receipt" : "Add receipt");
    $("#report-upload").scrollIntoView({block:"nearest",behavior:"smooth"});
  }
  $("#report-add").addEventListener("click", () => openUpload());
  $("#report-cancel-upload").addEventListener("click", () => { picker.clear(); $("#report-upload").hidden = true; });
  $("#report-attach").addEventListener("click", async () => {
    if (busy || !picker.count) return;
    busy = true; $("#report-attach").disabled = true;
    try {
      freezeInputs(true); await flush(); saveState("Uploading receipt…");
      const data = new FormData();
      if (uploadMode === "append") {
        const response = await fetch(uploadTarget.stream_url);
        if (!response.ok) throw new Error(t("Unable to open receipt"));
        data.append("files",await response.blob(),"existing-pages.pdf");
      }
      (await picker.preparedFiles()).forEach(file => data.append("files",file,file.name));
      if (uploadMode === "new") data.append("simple_entry","1");
      const result = await request(`/${id}/documents`, {method:"POST",files:data});
      if (uploadTarget) {
        const card = $(`[data-claim="${uploadTarget.claim_id}"]`), body = claimBody(uploadTarget,card);
        body.document_id = result.documents[0].document_id;
        await request(`/${id}/claims/${uploadTarget.claim_id}`,{method:"PATCH",body});
      }
      picker.clear(); $("#report-upload").hidden = true;
      await load(); saveState("All changes saved"); show("");
      const cards = $("#report-claims").querySelectorAll(".report-receipt");
      cards[cards.length-1]?.querySelector('input:not(:disabled)')?.focus();
      document.dispatchEvent(new CustomEvent("expense-report-changed"));
    } catch(error) { show(error.message,true); saveState("Changes not saved",true); }
    finally { freezeInputs(false); busy = false; $("#report-attach").disabled = false; }
  });
  $("#report-claims").addEventListener("input", event => {
    const card = event.target.closest("[data-claim]"); if (!card) return;
    dirty.add(card.dataset.claim); balances(); schedule();
  });
  settlement.addEventListener("input", () => { reportDirty = true; balances(); schedule(); });
  $("#report-claims").addEventListener("click", async event => {
    const button = event.target.closest("[data-action]"); if (!button || busy) return;
    const claim = view.claims.find(c => c.claim_id === button.closest("[data-claim]").dataset.claim);
    if (button.dataset.action === "view") {
      $("#report-document-link").href = claim.stream_url; $("#report-document iframe").src = claim.stream_url;
      $("#report-document").showModal(); return;
    }
    if (button.dataset.action !== "remove") { openUpload(button.dataset.action,claim); return; }
    if (!confirm(t("Remove this receipt from the report?"))) return;
    busy = true; freezeInputs(true);
    try { await flush(); await request(`/${id}/claims/${claim.claim_id}`,{method:"DELETE",body:{submission_id:cashUUID(),expected_content_version:claim.content_version}}); await load(); }
    catch(error) { show(error.message,true); } finally {freezeInputs(false); busy = false;}
  });
  $("#report-document-close").addEventListener("click", () => $("#report-document").close());
  settlement.addEventListener("submit", async event => {
    event.preventDefault(); if (busy) return; busy = true; $("#report-submit").disabled = true;
    try {
      if (picker.count) throw new Error(t("Attach your selected receipt pages before submitting."));
      for (const claim of view.claims.filter(c => c.state !== "accepted")) {
        const card = $(`[data-claim="${claim.claim_id}"]`), notes = card.querySelector('[name="notes"]');
        if (!notes.value.trim()) {notes.focus(); throw new Error(t("Describe the purpose of every receipt before submitting"));}
      }
      freezeInputs(true);
      reportDirty = true; // Save blank amounts as zero before submitting.
      await flush();
      await request(`/${id}/report/submit`,{method:"POST",body:{submission_id:cashUUID(),expected_version:reportVersion,claim_versions:Object.fromEntries(view.claims.map(c => [c.claim_id,c.content_version]))}});
      await load(); document.dispatchEvent(new CustomEvent("expense-report-changed"));
    } catch(error) {show(error.message,true);} finally {freezeInputs(false); busy = false; $("#report-submit").disabled = false;}
  });
  $("#report-reopen").addEventListener("click", async () => {
    if (busy) return; busy = true; freezeInputs(true);
    try {await request(`/${id}/report/reopen`,{method:"POST",body:{submission_id:cashUUID(),expected_version:reportVersion}}); show(""); await load(); document.dispatchEvent(new CustomEvent("expense-report-changed"));}
    catch(error) {show(error.message,true);} finally {freezeInputs(false); busy = false;}
  });
  $("#report-retry").addEventListener("click", () => flush().catch(() => {}));
  window.addEventListener("beforeunload", event => { if (dirty.size || reportDirty || running || busy || picker.count) {event.preventDefault(); event.returnValue = "";} });
  document.addEventListener("signee-language-change", () => { if (!admin) flush().then(() => {render();picker.refresh();}).catch(() => {}); });
  document.addEventListener("signout-review-changed", () => { if (!dirty.size && !reportDirty && !running && !busy) load().catch(error => show(error.message,true)); });
  load().catch(error => show(error.message,true));
})();
