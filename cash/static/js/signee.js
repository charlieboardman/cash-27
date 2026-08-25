/* Signee page (spec 12): claims, projected balance, package submit.
   The signee never sees account codes, classes, basis, or rates. */
const vid = location.pathname.split("/")[1];
let csrf = null;

async function load() {
  const r = await fetch(`/${vid}/state`);
  const { data } = await r.json();
  csrf = data.csrf;
  document.getElementById("purpose").textContent =
    `${data.voucher.recipient} — ${data.voucher.purpose}`;
  document.getElementById("bal-usd").textContent =
    data.authoritative.usd;
  document.getElementById("bal-mxn").textContent =
    data.authoritative.mxn;
  document.getElementById("proj-usd").textContent =
    data.projected_after_pending.usd;
  document.getElementById("proj-mxn").textContent =
    data.projected_after_pending.mxn;
  document.getElementById("neg-warn").hidden =
    parseFloat(data.projected_after_pending.mxn) >= 0;

  const cl = document.getElementById("claims");
  cl.innerHTML = "";
  for (const c of data.claims) {
    const li = document.createElement("li");
    const net = (c.face_value_minor / 100).toFixed(2);
    li.innerHTML = `<label><input type="checkbox"
      data-claim="${c.claim_id}" ${c.state !== 'draft' ? 'disabled' : ''}>
      ${c.claim_date} ${c.vendor} ${net} ${c.face_currency}
      [${c.state}]</label>` +
      (c.admin_note ? ` <em>${c.admin_note}</em>` : "");
    cl.appendChild(li);
  }
  const pl = document.getElementById("packages");
  pl.innerHTML = "";
  for (const p of data.packages) {
    const li = document.createElement("li");
    li.textContent = `Package ${p.seq}: ${p.status}` +
      (p.admin_summary ? ` — ${p.admin_summary}` : "");
    if (p.status === "submitted") {
      const b = document.createElement("button");
      b.textContent = "Withdraw";
      b.onclick = () => withdraw(p.package_id);
      li.appendChild(b);
    }
    pl.appendChild(li);
  }
}

document.getElementById("new-claim").addEventListener("submit",
  async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      submission_id: crypto.randomUUID(),
      date: f.get("date"), vendor: f.get("vendor"),
      description: f.get("description"),
      face_value: f.get("face_value"),
      face_currency: f.get("face_currency"),
      usd_paid: f.get("usd_paid"), mxn_paid: f.get("mxn_paid"),
      mxn_change: f.get("mxn_change"), usd_change: f.get("usd_change"),
      notes: f.get("notes"), document_id: f.get("document_id"),
    };
    const r = await fetch(`/${vid}/claims`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": csrf },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!j.ok) { alert(j.error.message); return; }
    e.target.reset();
    load();
  });

document.getElementById("submit-selected").addEventListener("click",
  async () => {
    const ids = [...document.querySelectorAll(
      "#claims input:checked")].map(cb => cb.dataset.claim);
    if (!ids.length) { alert("Select some drafts."); return; }
    const r = await fetch(`/${vid}/packages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF": csrf },
      body: JSON.stringify({ submission_id: crypto.randomUUID(),
                             claim_ids: ids }),
    });
    const j = await r.json();
    if (!j.ok) { alert(j.error.message); return; }
    load();
  });

async function withdraw(pid) {
  const r = await fetch(`/${vid}/packages/${pid}/withdraw`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF": csrf },
    body: JSON.stringify({ submission_id: crypto.randomUUID() }),
  });
  const j = await r.json();
  if (!j.ok) alert(j.error.message);
  load();
}

load();
