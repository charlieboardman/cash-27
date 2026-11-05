(() => {
  const form = document.getElementById('admin-form');
  const status = document.getElementById('admin-status');
  const t = cashI18n.t, esc = cashEsc;
  let accounts = [], current = null, editing = null;
  function reset() {
    editing = null; form.reset();
    form.elements.password.required = form.elements.confirmation.required = true;
    document.getElementById('admin-save').dataset.i18n = 'Add administrator';
    document.getElementById('admin-cancel').hidden = true;
    cashI18n.apply();
  }
  function render() {
    const active = accounts.filter(a => a.enabled).length;
    document.getElementById('admin-list').innerHTML = accounts.map(a => {
      const last = a.enabled && active === 1;
      return `<li><div><strong>${esc(a.name)}</strong><span>${esc(t(a.enabled ? 'Enabled' : 'Disabled'))}</span></div>
        <span class="admin-account-actions"><button type="button" class="btn ghost" data-id="${a.id}" data-action="edit">${esc(t('Edit'))}</button>
        <button type="button" class="btn ghost" data-id="${a.id}" data-action="toggle" ${last ? 'disabled' : ''}>${esc(t(a.enabled ? 'Disable' : 'Enable'))}</button>
        <button type="button" class="btn ghost danger" data-id="${a.id}" data-action="delete" ${last ? 'disabled' : ''}>${esc(t('Delete'))}</button></span></li>`;
    }).join('');
    const self = accounts.find(a => a.id === current);
    if (self) document.getElementById('signed-in-admin').textContent = self.name;
  }
  async function load() {
    const data = await cashApi('/api/settings/admins');
    accounts = data.admins; current = data.current_id; render();
  }
  function error(e) { cashNotice(status, cashI18n.error(e), 'danger'); }
  async function save(id, body) {
    await cashApi('/api/settings/admins' + (id ? '/' + id : ''), {method: 'POST', body});
    if (id === current && (body.password || body.delete || body.enabled === false)) {
      location.assign('/api/logout'); return;
    }
    reset(); await load();
  }
  document.getElementById('admin-cancel').onclick = reset;
  form.onsubmit = async event => {
    event.preventDefault();
    if (form.elements.password.value !== form.elements.confirmation.value) {
      error({message: 'Passwords do not match'}); return;
    }
    const body = {name: form.elements.name.value};
    if (form.elements.password.value) body.password = form.elements.password.value;
    const button = document.getElementById('admin-save'); button.disabled = true;
    try { await save(editing, body); } catch(e) { error(e); }
    finally { button.disabled = false; }
  };
  document.getElementById('admin-list').onclick = async event => {
    const button = event.target.closest('button[data-id]'); if (!button) return;
    const account = accounts.find(a => a.id === button.dataset.id);
    if (button.dataset.action === 'edit') {
      reset(); editing = account.id; form.elements.name.value = account.name;
      form.elements.password.required = form.elements.confirmation.required = false;
      document.getElementById('admin-save').dataset.i18n = 'Save administrator';
      document.getElementById('admin-cancel').hidden = false;
      cashI18n.apply(); form.elements.name.focus(); return;
    }
    if (button.dataset.action === 'delete' && !await cashConfirm(t('Delete administrator?'), {
      confirmLabel: t('Delete'), cancelLabel: t('Cancel'), danger: true})) return;
    button.disabled = true;
    try { await save(account.id, button.dataset.action === 'delete' ? {delete: true} : {enabled: !account.enabled}); }
    catch(e) { error(e); button.disabled = false; }
  };
  document.addEventListener('language-change', render);
  load().catch(error);
})();
