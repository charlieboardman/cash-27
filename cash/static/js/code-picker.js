/* Fzf search for existing native account/class selects, including dynamic rows. */
(() => {
  const selector = 'select[name="class_code"], select[name="expense_account"], ' +
    'select[name="from_account"], select[name="to_account"]';
  let picker = null;
  let serial = 0;

  function close(restoreFocus = false) {
    if (!picker) return;
    const {select, popup, observer} = picker;
    picker = null;
    observer.disconnect();
    popup.remove();
    select.removeAttribute("aria-expanded");
    select.removeAttribute("aria-controls");
    if (restoreFocus && select.isConnected) select.focus();
  }

  function position() {
    if (!picker) return;
    const {select, popup} = picker;
    const rect = select.getBoundingClientRect();
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    const top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth;
    const height = viewport?.height || innerHeight;
    const popupWidth = Math.min(Math.max(rect.width, 320), width - 16);
    const below = top + height - rect.bottom - 8;
    const above = rect.top - top - 8;
    const upwards = below < 240 && above > below;
    popup.style.width = `${popupWidth}px`;
    popup.style.maxHeight = `${Math.max(100, Math.min(340, upwards ? above : below))}px`;
    popup.style.left = `${Math.max(left + 8, Math.min(rect.left, left + width - popupWidth - 8))}px`;
    popup.style.top = `${Math.max(top + 8, upwards ? rect.top - popup.offsetHeight - 4 : rect.bottom + 4)}px`;
  }

  function highlight(index) {
    if (!picker) return;
    const {input, list, results} = picker;
    picker.active = Math.max(0, Math.min(index, results.length - 1));
    [...list.children].forEach((option, i) => {
      option.setAttribute("aria-selected", String(i === picker.active));
    });
    const active = list.children[picker.active];
    if (active) {
      input.setAttribute("aria-activedescendant", active.id);
      active.scrollIntoView({block: "nearest"});
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function search() {
    const {input, list, finder, status} = picker;
    picker.results = finder.find(input.value.trim()).map(result => result.item);
    list.replaceChildren();
    for (const [index, item] of picker.results.entries()) {
      const option = document.createElement("div");
      option.id = `${list.id}-${index}`;
      option.role = "option";
      option.className = "code-picker-option";
      option.textContent = item.label;
      option.addEventListener("pointerdown", event => event.preventDefault());
      option.addEventListener("click", () => choose(index));
      list.append(option);
    }
    status.textContent = picker.results.length
      ? `${picker.results.length} ${picker.results.length === 1 ? "match" : "matches"} · Tab or Enter to select`
      : "No matching codes";
    highlight(0);
    position();
  }

  function choose(index = picker?.active) {
    if (!picker) return;
    const {select, results} = picker;
    const item = results[index];
    close(true);
    if (!item || !select.isConnected) return;
    if (select.value !== item.value) {
      select.value = item.value;
      select.dispatchEvent(new Event("input", {bubbles: true}));
      select.dispatchEvent(new Event("change", {bubbles: true}));
    }
  }

  function open(select, query = "") {
    if (select.disabled || !window.fzf) return;
    close();
    const label = select.labels?.[0]?.querySelector("span")?.textContent.trim()
      || "account or class code";
    const popup = document.createElement("div");
    popup.className = "code-picker";
    const input = document.createElement("input");
    input.type = "text";
    input.role = "combobox";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Type a code or any part of its name";
    input.setAttribute("aria-label", `Search ${label}`);
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-autocomplete", "list");
    const list = document.createElement("div");
    list.className = "code-picker-options";
    list.role = "listbox";
    list.id = `code-picker-${++serial}`;
    list.setAttribute("aria-label", label);
    input.setAttribute("aria-controls", list.id);
    const status = document.createElement("div");
    status.className = "code-picker-status";
    status.role = "status";
    popup.append(input, list, status);
    const choices = [...select.options]
      .filter(option => !option.disabled && (option.value || !select.required))
      .map(option => ({value: option.value, label: option.textContent.trim()}));
    const observer = new MutationObserver(() => {
      if (!select.isConnected || !select.getClientRects().length || select.disabled) close();
    });
    picker = {select, popup, input, list, status, observer, results: [], active: 0,
      finder: new window.fzf.Fzf(choices, {selector: item => item.label, casing: "case-insensitive"})};
    select.setAttribute("aria-expanded", "true");
    select.setAttribute("aria-controls", list.id);
    // A modal dialog is in the browser's top layer; its search popup must be
    // inside that dialog to remain visible and keyboard-accessible.
    (select.closest("dialog") || document.body).append(popup);
    input.value = query;
    search();
    if (!query) highlight(Math.max(0, picker.results.findIndex(item => item.value === select.value)));
    input.focus({preventScroll: true});
    input.addEventListener("input", search);
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (["ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"].includes(event.key)) {
        event.stopPropagation();
        if (event.key !== "Tab") event.preventDefault();
      }
      if (event.key === "ArrowDown") highlight(picker.active + 1);
      if (event.key === "ArrowUp") highlight(picker.active - 1);
      if (event.key === "Enter") choose();
      if (event.key === "Escape") close(true);
      // Restore the native select before the browser advances focus, so Tab
      // continues to the next form field rather than the end of the page.
      if (event.key === "Tab") {
        if (event.shiftKey) close(true);
        else choose();
      }
    });
    observer.observe(document.body, {childList: true, subtree: true, attributes: true,
      attributeFilter: ["hidden", "disabled", "style", "class"]});
  }

  document.addEventListener("pointerdown", event => {
    if (!window.fzf) return;
    const select = event.target.closest(selector);
    if (select && !select.disabled) {
      event.preventDefault();
      select.focus();
      if (picker?.select === select) close(true);
      else open(select);
    } else if (picker && !picker.popup.contains(event.target)) close();
  });
  document.addEventListener("click", event => {
    if (!window.fzf) return;
    // Suppress the native menu; keyboard/screen-reader synthetic clicks open search.
    const select = event.target.closest(selector);
    if (select && !select.disabled) {
      event.preventDefault();
      if (event.detail === 0) open(select);
    }
  });
  document.addEventListener("keydown", event => {
    if (!window.fzf) return;
    const select = event.target.closest(selector);
    if (!select || select.disabled || event.ctrlKey || event.metaKey || event.isComposing) return;
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key) ||
        (event.key.length === 1 && !event.altKey)) {
      event.preventDefault();
      open(select, event.key.length === 1 && event.key !== " " ? event.key : "");
    }
  });
  document.addEventListener("focusin", event => {
    if (picker && event.target !== picker.select && !picker.popup.contains(event.target)) close();
  });
  document.addEventListener("scroll", event => {
    if (picker && !picker.popup.contains(event.target)) position();
  }, true);
  window.addEventListener("resize", position);
  window.visualViewport?.addEventListener("resize", position);
})();
