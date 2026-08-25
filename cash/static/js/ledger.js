/* Ledger view: replay-order table, header balances, safe count
   compare, corrections via /api/entries/correction. */
async function api(url, body) {
  const csrf = (await (await fetch("/api/session")).json()).data.csrf;
  const r = await fetch(url, body ? {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF": csrf },
    body: JSON.stringify(body) } : undefined);
  return r.json();
}

async function load(params = "") {
  const { data } = await api("/api/ledger" + params);
  const bal = document.getElementById("balances");
  let html = "<h2>Balances</h2><table><tbody>";
  for (const [acct, b] of Object.entries(data.balances)) {
    if (b.usd || b.mxn)
      html += `<tr><td>${acct}</td><td class="num">${b.usd.toFixed(2)}</td>` +
        `<td class="num">${b.mxn.toFixed(2)}</td>` +
        `<td class="num muted">${(+b.basis_usd).toFixed(2)} basis</td></tr>`;
  }
  html += "</tbody></table>";
  if (data.outstanding_unallocated)
    html += `<p class="warn">${data.outstanding_unallocated} unallocated
      expense(s) block report finalization.</p>`;
  bal.innerHTML = html;

  const tb = document.querySelector("#txns tbody");
  tb.innerHTML = "";
  for (const t of data.transactions) {
    const tr = document.createElement("tr");
    tr.className = t.posting_state === "void" ? "void" : "";
    tr.innerHTML = `<td>${t.date}</td><td>${t.txn_type}</td>
      <td>${t.from_account}</td><td>${t.to_account}</td>
      <td class="num">${(t.usd_cents / 100).toFixed(2)}</td>
      <td class="num">${(t.mxn_centavos / 100).toFixed(2)}</td>
      <td class="num">${t.calc_usd ?? ""}</td>
      <td>${t.voucher_id ?? ""}</td><td>${t.posting_state}</td>`;
    tb.appendChild(tr);
  }
}

document.getElementById("filters").addEventListener("submit", e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const p = new URLSearchParams();
  for (const [k, v] of f) if (v) p.set(k, v);
  load("?" + p);
});

document.getElementById("count").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const r = await api("/api/ledger/safe-count",
                      { usd: f.get("usd"), mxn: f.get("mxn") });
  if (!r.ok) { alert(r.error.message); return; }
  const d = r.data.delta;
  document.getElementById("delta").textContent =
    `Delta: USD ${d.usd.toFixed(2)} · MXN ${d.mxn.toFixed(2)}`;
  const zero = d.usd === 0 && d.mxn === 0;
  document.getElementById("correction").hidden = zero;
});

document.getElementById("correction").addEventListener("submit",
  async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const count = new FormData(document.getElementById("count"));
    const cmp = await api("/api/ledger/safe-count",
                          { usd: count.get("usd"), mxn: count.get("mxn") });
    if (!cmp.ok) { alert(cmp.error.message); return; }
    const d = cmp.data.delta;
    const dir = (d.usd < 0 || d.mxn < 0) ? "out" : "in";
    const r = await api("/api/entries/correction", {
      submission_id: crypto.randomUUID(),
      date: new Date().toISOString().slice(0, 10),
      direction: dir,
      usd: Math.abs(d.usd).toFixed(2),
      mxn: Math.abs(d.mxn).toFixed(2),
      reason: f.get("reason"),
      notes: "from safe count",
    });
    if (!r.ok) { alert(r.error.message); return; }
    load();
  });

load();
