/* Preparation form: submits the prep command, then opens the print
   page. The submission asserts the cash moved (spec 8). */
const form = document.getElementById("prep");
const status = document.getElementById("status");
const radios = form.querySelectorAll('input[name="type"]');
const addendumBox = document.getElementById("addendum-target");

function csrf() {
  return fetch("/api/session").then(r => r.json())
    .then(b => b.data.csrf);
}

radios.forEach(r => r.addEventListener("change", () => {
  addendumBox.hidden = !(r.value === "positive" || r.value === "return")
    || !r.checked;
}));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(form);
  const type = f.get("type");
  const body = {
    submission_id: crypto.randomUUID(),
    date: f.get("date"),
    usd: f.get("usd") || "0",
    mxn: f.get("mxn") || "0",
    notes: f.get("notes") || "",
  };
  let url = "/api/vouchers/prepare";
  if (type === "direct_expense" || type === "signout") {
    Object.assign(body, { type, recipient: f.get("recipient") || "",
                          purpose: f.get("purpose") || "" });
  } else {
    url = "/api/addenda/prepare";
    Object.assign(body, { voucher_id: f.get("voucher_id"),
                          kind: type, reason: f.get("reason") || "" });
  }
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-CSRF": await csrf() },
    body: JSON.stringify(body),
  });
  const json = await res.json();
  if (!json.ok) {
    status.value = "Error: " + json.error.message;
    return;
  }
  status.value = "Recorded " +
    (json.data.voucher_id || json.data.addendum_id || "");
  if (json.data.print_url) window.open(json.data.print_url, "_blank");
});
