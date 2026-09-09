/* Print page: deployer-owned voucher template with $TOKEN substitution
   (spec 8). Names and values print; signature areas stay blank. */
const tokens = JSON.parse(document.getElementById("tokens").textContent);
const tpl = document.getElementById("paper");
tpl.innerHTML = tpl.innerHTML.replace(/\$([A-Z_]+)/g,
  (m, k) => String(tokens[k] ?? "").replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;"));
window.addEventListener("load", () => window.print());
