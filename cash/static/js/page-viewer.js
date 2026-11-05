/* Inline document pages with app-owned controls; no embedded browser PDF UI. */
(function (global) {
  const views = new Map();
  const t = key => global.cashI18n ? global.cashI18n.t(key) : key;
  const label = key => `<span data-i18n="${cashEsc(key)}">${cashEsc(t(key))}</span>`;
  const paths = {
    previous: '<path d="m15 18-6-6 6-6"/>',
    next: '<path d="m9 18 6-6-6-6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M7 11h8"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M7 11h8m-4-4v8"/>',
    fit: '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m13-5v3a2 2 0 0 1-2 2h-3"/>',
    retry: '<path d="M20 6v6h-6M4 18v-6h6"/><path d="M6.5 9a7 7 0 0 1 11.5-3L20 8M4 16l2 2a7 7 0 0 0 11.5-3"/>',
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true" focusable="false">${paths[name]}</svg>`;

  global.cashPageViewer = {
    icon,
    markup() {
      return `<section class="page-viewer">
        <div class="page-viewer-toolbar">
          <div class="page-viewer-pages">
            <button type="button" class="btn ghost" data-page-step="-1" aria-label="${t('Previous page')}" data-i18n-aria-label="Previous page">${icon('previous')}</button>
            <label>${label('Page')} <select class="page-viewer-page" aria-label="${t('Page')}" data-i18n-aria-label="Page"></select></label>
            <span class="page-viewer-total"></span>
            <button type="button" class="btn ghost" data-page-step="1" aria-label="${t('Next page')}" data-i18n-aria-label="Next page">${icon('next')}</button>
          </div>
          <div class="page-viewer-zoom">
            <button type="button" class="btn ghost" data-zoom="-25" aria-label="${t('Zoom out')}" data-i18n-aria-label="Zoom out">${icon('zoom-out')}</button>
            <output class="page-viewer-scale" role="status" aria-live="polite"></output>
            <button type="button" class="btn ghost" data-zoom="25" aria-label="${t('Zoom in')}" data-i18n-aria-label="Zoom in">${icon('zoom-in')}</button>
            <button type="button" class="btn ghost" data-fit>${icon('fit')}${label('Fit width')}</button>
          </div>
        </div>
        <div class="page-viewer-surface" tabindex="0" aria-label="${t('Document page')}" data-i18n-aria-label="Document page">
          <output class="page-viewer-status" role="status" aria-live="polite" aria-atomic="true"></output>
          <button type="button" class="btn ghost page-viewer-retry" data-retry hidden>${icon('retry')}${label('Retry')}</button>
          <img class="page-viewer-image" alt="">
        </div>
      </section>`;
    },
    mount(root, doc) {
      const key = `${doc.document_id}:${doc.sha256 || ''}`;
      const view = views.get(key) || {page: 1, zoom: 100, top: 0, left: 0};
      views.set(key, view);
      const pages = Math.max(1, Number(doc.page_count) || 1);
      const picker = root.querySelector('.page-viewer-page');
      const surface = root.querySelector('.page-viewer-surface');
      const img = root.querySelector('.page-viewer-image');
      const status = root.querySelector('.page-viewer-status');
      const retryButton = root.querySelector('[data-retry]');
      for (let page = 1; page <= pages; page++) picker.add(new Option(page, page));
      root.querySelector('.page-viewer-total').textContent = `/ ${pages}`;
      let loading = false;
      let imageWidth = 0;
      let loadRevision = 0;
      let retry = 0;
      surface.addEventListener('scroll', () => {
        if (!loading) { view.top = surface.scrollTop; view.left = surface.scrollLeft; }
      });
      const desiredWidth = () => {
        const density = Math.min(global.devicePixelRatio || 1, 2);
        const desired = surface.clientWidth * density * Math.max(1, view.zoom / 100);
        return desired > 1600 ? 2400 : desired > 1200 ? 1600 : 1200;
      };
      const applyZoom = () => {
        img.style.width = `${view.zoom}%`;
        img.style.margin = view.zoom < 100 ? '0 auto' : '0';
        root.querySelector('.page-viewer-scale').textContent = `${view.zoom}%`;
        root.querySelector('[data-zoom="-25"]').disabled = view.zoom <= 50;
        root.querySelector('[data-zoom="25"]').disabled = view.zoom >= 300;
      };
      const render = () => {
        imageWidth = desiredWidth();
        view.page = Math.min(pages, Math.max(1, view.page));
        picker.value = view.page;
        root.querySelector('[data-page-step="-1"]').disabled = view.page === 1;
        root.querySelector('[data-page-step="1"]').disabled = view.page === pages;
        const revision = ++loadRevision;
        loading = true;
        status.dataset.i18n = 'Loading page';
        status.textContent = t('Loading page');
        status.hidden = false;
        retryButton.hidden = true;
        img.hidden = true;
        img.alt = `${doc.display_name || doc.document_id} · ${view.page}`;
        img.onload = () => {
          if (revision !== loadRevision) return;
          loading = false;
          status.hidden = true;
          img.hidden = false;
          surface.scrollTop = view.top;
          surface.scrollLeft = view.left;
        };
        img.onerror = () => {
          if (revision !== loadRevision) return;
          loading = false;
          status.dataset.i18n = 'Preview unavailable';
          status.textContent = t('Preview unavailable');
          status.hidden = false;
          retryButton.hidden = false;
          img.hidden = true;
        };
        img.src = `/api/documents/${encodeURIComponent(doc.document_id)}/pages/${view.page}?width=${imageWidth}&v=${encodeURIComponent(doc.sha256 || '')}${retry ? `&retry=${retry}` : ''}`;
        applyZoom();
      };
      const zoom = () => {
        applyZoom();
        if (desiredWidth() !== imageWidth) render();
      };
      const go = page => {
        retry = 0;
        view.page = page;
        view.top = view.left = 0;
        render();
      };
      picker.onchange = () => go(Number(picker.value));
      root.querySelectorAll('[data-page-step]').forEach(button => {
        button.onclick = () => go(view.page + Number(button.dataset.pageStep));
      });
      root.querySelectorAll('[data-zoom]').forEach(button => {
        button.onclick = () => {
          view.zoom = Math.max(50, Math.min(300, view.zoom + Number(button.dataset.zoom)));
          zoom();
        };
      });
      root.querySelector('[data-fit]').onclick = () => {
        view.zoom = 100;
        view.left = 0;
        zoom();
        surface.scrollLeft = 0;
      };
      retryButton.onclick = () => { retry += 1; render(); };
      render();
      global.cashI18n?.apply();
    },
  };
})(window);
