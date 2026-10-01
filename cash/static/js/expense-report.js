/* Expense report at /FY##-NNNN, shared by the recipient and the administrator.

   The server draws the page and answers every save with the parts that
   changed (htmx swaps them in), so nothing here keeps a copy of the report.
   What remains is what only a browser can do: the ordered receipt picker,
   the split workspace, the receipt viewer, and the plumbing around htmx
   (errors, save status, saving before an action, translation after a swap). */
(() => {
  const root = document.getElementById("expense-report");
  if (!root) return;
  const vid = root.dataset.voucher;
  const admin = root.dataset.admin === "true";
  const codes = JSON.parse(root.dataset.codes);
  const $ = selector => root.querySelector(selector);
  const t = (key, values) => cashI18n.t(key, values);
  const body = document.body;

  /* ---- messages and save status ------------------------------------------------ */

  function message(text, tone = "danger") {
    cashNotice($("#er-message"), text, tone, { fade: tone === "success" });
  }

  function setState(key, failed = false) {
    const state = $("#er-save-state");
    state.dataset.i18n = key;
    state.textContent = t(key);
    state.classList.toggle("er-save-error", failed);
  }

  const saving = () => root.querySelector(".htmx-request, [data-dirty]");

  root.addEventListener("input", event => {
    const form = event.target.closest("[data-autosave]");
    if (!form) return;
    form.dataset.dirty = String(Date.now());
    setState("Saving…");
  });
  body.addEventListener("htmx:beforeRequest", event => {
    event.target.sent = event.target.dataset?.dirty;
  });
  body.addEventListener("htmx:afterRequest", event => {
    const form = event.target;
    if (!form.matches?.("[data-autosave]")) return;
    if (!event.detail.successful) { form.dataset.failed = "true"; return setState("Changes not saved", true); }
    delete form.dataset.failed;
    if (form.dataset.dirty === form.sent) delete form.dataset.dirty;
    if (!saving()) { setState("All changes saved"); message(""); }
  });
  body.addEventListener("htmx:responseError", event => {
    let problem = null;
    try { problem = JSON.parse(event.detail.xhr.responseText).error; } catch (_) { /* Not JSON. */ }
    message(cashI18n.error(problem));
  });
  body.addEventListener("htmx:sendError", () => message(cashI18n.error(null)));
  body.addEventListener("htmx:configRequest", event => {
    event.detail.headers["X-Client-Date"] = cashToday();
  });
  window.addEventListener("beforeunload", event => {
    if (saving()) { event.preventDefault(); event.returnValue = ""; }
  });

  // Actions that must see the latest edits (submit, approve, attach) first
  // save what is pending, and a delete or removal asks first.
  async function saveAll() {
    root.querySelectorAll("[data-dirty]").forEach(form => {
      delete form.dataset.failed;
      htmx.trigger(form, "flush");
    });
    for (let waited = 0; saving() && !$("[data-failed]") && waited < 10000; waited += 50) {
      await new Promise(resolve => window.setTimeout(resolve, 50));
    }
    if (saving()) throw new Error(t("Changes not saved"));
  }

  body.addEventListener("htmx:confirm", async event => {
    const { elt, question, issueRequest } = event.detail;
    if (!question && !elt.matches("[data-flush]")) return;
    event.preventDefault();
    if (question && !(await cashConfirm(t(question), { danger: true, cancelLabel: t("Cancel"),
      confirmLabel: t(elt.matches("[data-delete]") ? "Delete row" : "Remove") }))) return;
    try {
      if (elt.matches("[data-flush]")) await saveAll();
    } catch (error) { return message(error.message); }
    // The saves just redrew the buttons; press the one that is there now.
    if (!elt.isConnected) document.getElementById(elt.id)?.click();
    else issueRequest(true);
  });

  /* ---- after every swap ---------------------------------------------------------- */

  const receiptPreview = editor => editor.dataset.receiptUrl
    ? { url: editor.dataset.receiptUrl, name: editor.dataset.receiptName } : null;

  // A split is saved as one structural change, and the row comes back redrawn.
  function restructure(editor, fields) {
    const id = editor.closest(".er-row").dataset.row;
    const values = { rev: editor.elements.rev.value, splits: JSON.stringify(fields.splits) };
    for (const [name, value] of Object.entries(fields)) if (name !== "splits") values[name] = value ?? "";
    htmx.ajax("POST", `/${vid}/ui/rows/${id}/structure`, { source: editor, target: `#row-${id}`,
      swap: "outerHTML", values });
  }

  function wireEditors() {
    for (const editor of root.querySelectorAll(".er-editor:not([data-wired])")) {
      editor.dataset.wired = "true";
      cashRowFields.wire(editor, { t, codes, preview: receiptPreview(editor),
        onStructure: fields => restructure(editor, fields) });
      editor.addEventListener("input", event => {
        const tools = editor.querySelector("[data-statement-tools]");
        if (tools && event.target.matches('[name="no_receipt_reason"]')) tools.hidden = !event.target.value.trim();
      });
    }
  }

  body.addEventListener("htmx:afterSettle", event => {
    // A redrawn row stays open (the browser closes a newly inserted one beside the old).
    root.querySelectorAll("[data-reopen]").forEach(row => {
      row.querySelector("details").open = true;
      row.removeAttribute("data-reopen");
    });
    wireEditors();
    cashI18n.apply();
    if (event.detail.elt?.id === "er-add") {
      $(".er-row details[open] .er-editor input:not([type=hidden]):not([type=checkbox])")?.focus();
    }
  });
  wireEditors();

  /* ---- receipts ------------------------------------------------------------------ */

  // A receipt is staged whole, every page in order, before it is attached,
  // the same way Capture builds a document.
  const attachPicker = createOrderedDocumentPicker($("#er-attach-picker"), {
    translate: t, onError: text => { $("#er-attach-status").textContent = text; } });
  $("#er-attach-picker").addEventListener("document-picker-change", event => {
    $("#er-attach-go").disabled = event.detail.count === 0;
  });
  let attaching = null;               // {rowId, mode}

  function openAttach(rowId, mode) {
    attaching = { rowId, mode };
    attachPicker.clear();
    $("#er-attach-status").textContent = "";
    if (admin) {
      htmx.ajax("GET", `/${vid}/ui/inbox`, { source: root, target: "#er-attach-inbox", swap: "innerHTML" })
        .then(() => { $(".er-attach-inbox").hidden = !$("#er-attach-inbox").children.length; });
    }
    $("#er-attach").showModal();
  }

  function closeAttach() {
    attaching = null;
    attachPicker.clear();
    $("#er-attach").close();
  }

  // Resolves true when the server accepted the change.
  function post(path, values, { target, swap = "none" } = {}) {
    return new Promise(resolve => {
      const done = event => {
        if (event.detail.pathInfo.requestPath !== path) return;
        body.removeEventListener("htmx:afterRequest", done);
        resolve(event.detail.successful);
      };
      body.addEventListener("htmx:afterRequest", done);
      htmx.ajax("POST", path, { source: root, target: target || root, swap, values });
    });
  }

  async function attachDocument(documentId) {
    const { rowId, mode } = attaching;
    const row = $(`#row-${rowId}`);
    const tool = root.querySelector(`[data-attach][data-row="${rowId}"]`);
    const rev = row ? $(`#rev-${rowId}`).value : tool?.dataset.rev ?? "";
    const accepted = await post(`/${vid}/ui/rows/${rowId}/receipt`, { mode, document_id: documentId, rev },
      row ? { target: `#row-${rowId}`, swap: "outerHTML" } : {});
    if (accepted) closeAttach();
  }

  $("#er-attach-go").addEventListener("click", async () => {
    const status = $("#er-attach-status");
    $("#er-attach-go").disabled = true;
    try {
      await saveAll();
      const prepared = await attachPicker.preparedFiles((page, total) => {
        status.textContent = t("Preparing page {page} of {total}…", { page, total });
      });
      const form = new FormData();
      prepared.forEach(file => form.append("files", file, file.name));
      status.textContent = t("Uploading receipt…");
      const uploaded = await cashApi(`/${vid}/documents`, { method: "POST", formData: form });
      await attachDocument(uploaded.documents[0].document_id);
    } catch (error) {
      status.textContent = cashI18n.error(error);
      $("#er-attach-go").disabled = attachPicker.count === 0;
    }
  });
  $("#er-attach-cancel").addEventListener("click", closeAttach);
  $("#er-attach-inbox")?.addEventListener("click", event => {
    const button = event.target.closest("[data-inbox]");
    if (button) attachDocument(button.dataset.inbox);
  });

  function openViewer(url) {
    const dialog = $("#er-viewer");
    $("#er-viewer-link").href = url;
    dialog.querySelector("iframe").src = url;
    dialog.showModal();
  }
  $("#er-viewer-close").addEventListener("click", () => $("#er-viewer").close());

  /* ---- clicks ---------------------------------------------------------------------- */

  root.addEventListener("click", async event => {
    const target = event.target.closest("button, [data-show]");
    if (!target) return;
    const row = target.closest(".er-row");
    if (target.dataset.attach) {
      try { await saveAll(); } catch (error) { return message(error.message); }
      openAttach(target.dataset.row || row.dataset.row, target.dataset.attach);
    } else if (target.dataset.scan) {
      attaching = { rowId: row.dataset.row, mode: "statement" };
      attachDocument(target.dataset.scan);
    } else if (target.matches("[data-view]")) openViewer(target.dataset.url);
    else if (target.dataset.show) {
      document.getElementById(target.dataset.show).hidden = false;
      document.getElementById(target.dataset.show).scrollIntoView({ block: "nearest" });
    } else if (target.dataset.hide) document.getElementById(target.dataset.hide).hidden = true;
  });

  root.addEventListener("change", event => {
    if (event.target.id !== "er-select-all") return;
    const checks = [...root.querySelectorAll(".er-row-check")];
    checks.forEach(check => { check.checked = event.target.checked; });
    checks[0]?.dispatchEvent(new Event("change", { bubbles: true }));
  });

  /* ---- administrator: add cash and print ------------------------------------------- */

  let printTab = null;
  $("#er-addendum-form")?.addEventListener("click", event => {
    if (!event.target.matches('[name="print"]')) return;
    printTab = window.open("about:blank", "_blank");
    if (printTab) printTab.opener = null;
  });
  body.addEventListener("erAddendum", event => { printTab?.location.replace(event.detail.url); });
  body.addEventListener("htmx:responseError", () => { printTab?.close(); printTab = null; });
})();
