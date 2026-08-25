/* Review shell: keyboard-first worklist + contextual body (spec 11). */
const listEl = document.getElementById("worklist");
const detailEl = document.getElementById("detail");
let items = [];
let sel = -1;

async function api(url, method = "GET", body) {
  const csrf = (await (await fetch("/api/session")).json()).data.csrf;
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF": csrf },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

async function load() {
  const { data } = await api("/api/review/worklist");
  items = data.items;
  render();
}

function render() {
  listEl.innerHTML = "";
  items.forEach((it, i) => {
    const li = document.createElement("li");
    li.tabIndex = -1;
    li.dataset.idx = i;
    li.className = i === sel ? "selected" : "";
    li.innerHTML = `<span class="label">${it.label}</span>` +
      it.badges.map(b => `<span class="badge">${b}</span>`).join("");
    li.addEventListener("click", () => select(i));
    listEl.appendChild(li);
  });
}

async function select(i) {
  sel = i;
  render();
  const it = items[i];
  const { data } = await api(`/api/review/item/${it.kind}/${it.id}`);
  renderDetail(it, data);
  detailEl.querySelector("button, input")?.focus();
}

function renderDetail(it, data) {
  if (it.kind === "direct_expense") return renderDirectExpense(it, data);
  detailEl.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
}

function renderDirectExpense(it, data) {
  const v = data.voucher;
  const alloc = data.allocations[0] || {};
  detailEl.innerHTML = `
    <h2>${v.voucher_id}</h2>
    <p>${v.recipient} — ${v.purpose}</p>
    <form id="complete">
      <label>Actual USD paid <input name="usd" inputmode="decimal"
        value="${(alloc.usd_cents ?? 0) / 100}"></label>
      <label>Actual MXN paid <input name="mxn" inputmode="decimal"
        value="${(alloc.mxn_centavos ?? 0) / 100}"></label>
      <label>Expense account
        <select name="expense_account">
          <option>EXP_SUPPLIES</option>
          <option>EXP_PROGRAMS</option>
        </select></label>
      <label>Document ID (inbox) <input name="document_id"></label>
      <button type="submit">Commit</button>
      <button type="submit" form="complete" id="commit-next">
        Commit &amp; next</button>
    </form>`;
  detailEl.querySelector("#complete").addEventListener("submit", async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      submission_id: crypto.randomUUID(),
      expected_version: 1,
      allocations: [{
        expense_account: f.get("expense_account"),
        class_code: null,
        usd: f.get("usd"), mxn: f.get("mxn"),
        project: "", description: "",
      }],
      document_id: f.get("document_id") || undefined,
    };
    const r = await api(`/api/review/direct-expense/${it.id}/complete`,
                        "POST", body);
    if (!r.ok) { alert(r.error.message); return; }
    await load();
    if (e.submitter === document.getElementById("commit-next") &&
        items.length) select(Math.min(sel, items.length - 1));
  });
}

listEl.addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { select(Math.min(sel + 1, items.length - 1)); e.preventDefault(); }
  if (e.key === "ArrowUp") { select(Math.max(sel - 1, 0)); e.preventDefault(); }
  if (e.key === "Enter" && sel >= 0) select(sel);
});
document.addEventListener("keydown", e => {
  if (e.key === "g") { /* quick-jump placeholder */ }
});

load();
