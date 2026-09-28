/* A Show/Hide button beside a password field, for typing a long shared
   password on a phone. Buttons carry data-toggle-password; the field is the
   input before them. Labels follow the signee language when it is present. */
(() => {
  const label = (key) => window.cashI18n ? window.cashI18n.t(key) : key;
  const relabel = () => document.querySelectorAll("[data-toggle-password]").forEach(button => {
    const shown = button.getAttribute("aria-pressed") === "true";
    button.textContent = label(shown ? "Hide" : "Show");
  });
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-toggle-password]");
    if (!button) return;
    const input = button.parentElement.querySelector("input");
    const shown = input.type === "text";
    input.type = shown ? "password" : "text";
    button.setAttribute("aria-pressed", String(!shown));
    relabel();
    input.focus();
  });
  document.addEventListener("language-change", relabel);
  relabel();
})();
