(() => {
  const table = document.getElementById('bk-table');
  const groups = Array.from(table.querySelectorAll('.bk-group'));
  const search = document.getElementById('bk-search');
  const category = document.getElementById('bk-category');
  const classFilter = document.getElementById('bk-class');
  const summary = document.getElementById('bk-summary');
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  function filter() {
    const terms = normalize(search.value).split(/\s+/).filter(Boolean);
    let count = 0, lines = 0;
    groups.forEach(group => {
      const matches = terms.every(term => normalize(group.dataset.search).includes(term)) &&
        (!category.value || JSON.parse(group.dataset.categories).includes(category.value)) &&
        (!classFilter.value || JSON.parse(group.dataset.classes).includes(classFilter.value));
      group.hidden = !matches;
      if (matches) { count++; lines += group.rows.length; }
    });
    summary.textContent = `${count} of ${groups.length} records · ${lines} allocation lines`;
    document.getElementById('bk-empty').hidden = count > 0;
  }
  search.addEventListener('input', filter);
  category.addEventListener('change', filter);
  classFilter.addEventListener('change', filter);
  document.getElementById('bk-reset').addEventListener('click', () => {
    search.value = ''; category.value = ''; classFilter.value = ''; filter();
  });
  table.querySelectorAll('[data-sort]').forEach(button => {
    if (button.tagName !== 'BUTTON') return;
    button.addEventListener('click', () => {
      const heading = button.closest('th');
      const direction = heading.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
      table.querySelectorAll('th[aria-sort]').forEach(th => th.setAttribute('aria-sort', 'none'));
      heading.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
      const key = button.dataset.sort;
      [...groups].sort((a, b) => {
        const x = JSON.parse(a.dataset.sort)[key], y = JSON.parse(b.dataset.sort)[key];
        return direction * (button.dataset.number === 'yes' ? Number(x) - Number(y) : x.localeCompare(y));
      }).forEach(group => table.append(group));
    });
  });
  filter();
})();
