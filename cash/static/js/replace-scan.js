/* Replace filed voucher paper; both entry points use the same dialog. */
(function (global) {
  const esc = global.cashEsc;
  const t = key => cashI18n.t(key);
  function menu(documentId) {
    return `<details class="scan-actions"><summary aria-label="${esc(t('Document actions'))}">⋯</summary>
      <button type="button" class="text-button" data-replace-scan="${esc(documentId)}">${esc(t('Replace scan'))}</button></details>`;
  }
  function open(voucherId, current) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.className = 'replace-scan-dialog';
      dialog.setAttribute('aria-labelledby', 'replace-scan-title');
      dialog.innerHTML = `<form>
        <h2 id="replace-scan-title">${esc(t('Replace scan'))}</h2>
        <label><span>${esc(t('From inbox'))}</span><select name="document_id" aria-label="${esc(t('From inbox'))}" required><option value="">${esc(t('Choose a scan'))}</option></select></label>
        <div><button type="button" class="btn ghost" data-upload>${esc(t('Upload scan'))}</button>
          <input type="file" name="scan" aria-label="${esc(t('Upload scan'))}" accept="application/pdf,image/jpeg,image/png" hidden></div>
        <div class="scan-previews">
          <section><h3>${esc(t('Current scan'))}</h3><div data-current-scan>${cashPageViewer.markup()}</div></section>
          <section><h3>${esc(t('Replacement scan'))}</h3><div data-new-scan></div></section>
        </div>
        <output role="alert"></output>
        <div class="scan-dialog-actions"><button type="button" class="btn ghost" data-refresh>${esc(t('Refresh'))}</button>
          <button type="button" class="btn ghost" data-cancel>${esc(t('Cancel'))}</button>
          <button type="submit" class="btn primary" disabled>${esc(t('Replace scan'))}</button></div>
      </form>`;
      document.body.append(dialog);
      const form = dialog.querySelector('form');
      const select = form.elements.document_id;
      const upload = form.elements.scan;
      const status = dialog.querySelector('output[role="alert"]');
      const submit = dialog.querySelector('[type="submit"]');
      let documents = [], timer, busy = false, result = false, uploadedId = null;
      function setBusy(value) {
        busy = value;
        form.querySelectorAll('.scan-dialog-actions button, [data-upload], input[name], select[name]').forEach(el => { el.disabled = value; });
        submit.disabled = value || !select.value;
      }
      function preview() {
        const area = dialog.querySelector('[data-new-scan]');
        const doc = documents.find(item => item.document_id === select.value);
        area.innerHTML = doc ? cashPageViewer.markup() : '';
        if (doc) cashPageViewer.mount(area, doc);
        submit.disabled = busy || !doc;
      }
      async function refresh() {
        clearTimeout(timer);
        try {
          const data = await cashApi('/api/documents/inbox');
          if (!dialog.open) return;
          const selected = uploadedId || select.value;
          documents = data.documents.filter(doc => !cashDocumentPending(doc));
          select.innerHTML = `<option value="">${esc(t('Choose a scan'))}</option>` + documents.map(doc =>
            `<option value="${esc(doc.document_id)}">${esc(doc.display_name || doc.document_id)}</option>`).join('');
          select.value = documents.some(doc => doc.document_id === selected) ? selected : '';
          const pending = uploadedId && data.documents.some(doc => doc.document_id === uploadedId && cashDocumentPending(doc));
          status.textContent = pending ? t('Processing scan…') : '';
          if (pending) timer = setTimeout(refresh, 1500);
          else uploadedId = null;
          preview();
        } catch (error) {
          if (dialog.open) status.textContent = cashI18n.error(error);
        }
      }
      select.onchange = () => { uploadedId = null; preview(); };
      dialog.querySelector('[data-upload]').onclick = () => upload.click();
      upload.onchange = async () => {
        if (!upload.files.length) return;
        setBusy(true);
        status.textContent = t('Uploading scan…');
        const body = new FormData();
        body.append('files', upload.files[0]);
        try {
          const doc = await cashApi('/api/documents/capture-merge', { method: 'POST', formData: body });
          uploadedId = doc.document_id;
          await refresh();
        } catch (error) { status.textContent = cashI18n.error(error); }
        finally { upload.value = ''; setBusy(false); }
      };
      form.onsubmit = async event => {
        event.preventDefault();
        if (busy || !select.value) return;
        clearTimeout(timer);
        setBusy(true);
        status.textContent = t('Replacing scan…');
        try {
          await cashCommand(`/api/vouchers/${encodeURIComponent(voucherId)}/replace-scan`, {
            old_document_id: current.document_id, document_id: select.value });
          result = true;
          dialog.close();
        } catch (error) { status.textContent = cashI18n.error(error); }
        finally { setBusy(false); }
      };
      dialog.querySelector('[data-refresh]').onclick = refresh;
      dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
      dialog.oncancel = event => { if (busy) event.preventDefault(); };
      dialog.onclose = () => { clearTimeout(timer); dialog.remove(); resolve(result); };
      dialog.showModal();
      cashPageViewer.mount(dialog.querySelector('[data-current-scan]'), current);
      refresh();
    });
  }
  global.cashReplaceScan = { menu, open };
})(window);
