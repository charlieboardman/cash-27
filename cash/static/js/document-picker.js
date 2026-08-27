/* Shared ordered camera/file picker. It may resize large images for upload,
   but sends ordinary multipart files and never builds PDFs in the browser. */
(function exposeDocumentPicker(global) {
  const ACCEPTED_TYPES = ["application/pdf", "image/jpeg", "image/png"];

  function esc(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }

  function fileSize(bytes) {
    return bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function resizeLargeImage(file, maxImageEdge) {
    if (!file.type.startsWith("image/")) return file;
    let bitmap;
    try {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (_) {
      return file;
    }
    const longest = Math.max(bitmap.width, bitmap.height);
    if (longest <= maxImageEdge) {
      bitmap.close();
      return file;
    }
    const scale = maxImageEdge / longest;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise(resolve =>
      canvas.toBlob(resolve, "image/jpeg", .88));
    if (!blob) return file;
    const stem = (file.name || "document-page").replace(/\.[^.]+$/, "");
    return new File([blob], `${stem}.jpg`, {
      type: "image/jpeg", lastModified: file.lastModified });
  }

  function createOrderedDocumentPicker(root, {
    maxFiles = 20, maxImageEdge = 2400, onError = () => {},
  } = {}) {
    const camera = root.querySelector('[data-picker-role="camera"]');
    const files = root.querySelector('[data-picker-role="files"]');
    const list = root.querySelector('[data-picker-role="pages"]');
    const count = root.querySelector('[data-picker-role="count"]');
    const empty = root.querySelector('[data-picker-role="empty"]');
    let pages = [];

    function render() {
      empty.hidden = pages.length > 0;
      count.hidden = pages.length === 0;
      count.textContent = `${pages.length} page${pages.length === 1 ? "" : "s"}`;
      list.innerHTML = pages.map((page, index) => `<li data-page="${esc(page.id)}">
        <div class="receipt-page-preview">
          ${page.previewUrl ? `<img src="${esc(page.previewUrl)}" alt="Document page ${index + 1}">` :
            `<span class="receipt-pdf-preview" aria-hidden="true">PDF</span>`}
          <span class="receipt-page-number">${index + 1}</span>
        </div>
        <div class="receipt-page-copy"><strong>${esc(page.file.name || `Page ${index + 1}`)}</strong>
          <span>${fileSize(page.file.size)}</span></div>
        <div class="receipt-page-actions">
          <button type="button" data-action="earlier" aria-label="Move page ${index + 1} earlier"
            ${index === 0 ? "disabled" : ""}>←</button>
          <button type="button" data-action="later" aria-label="Move page ${index + 1} later"
            ${index === pages.length - 1 ? "disabled" : ""}>→</button>
          <button type="button" class="remove-page" data-action="remove"
            aria-label="Remove page ${index + 1}">×</button>
        </div>
      </li>`).join("");
      root.dispatchEvent(new CustomEvent("document-picker-change", {
        detail: { count: pages.length },
      }));
    }

    function add(selected) {
      const source = [...selected];
      const incoming = source.filter(file => ACCEPTED_TYPES.includes(file.type));
      if (incoming.length !== source.length) {
        onError("Use PDF, JPEG, or PNG files.");
      }
      const available = maxFiles - pages.length;
      if (incoming.length > available) {
        onError(`One document can contain up to ${maxFiles} selected photos or files.`);
      }
      incoming.slice(0, Math.max(0, available)).forEach(file => {
        pages.push({ id: cashUUID(), file,
          previewUrl: file.type.startsWith("image/")
            ? URL.createObjectURL(file) : null });
      });
      render();
    }

    root.querySelector('[data-picker-action="camera"]').addEventListener(
      "click", () => camera.click());
    root.querySelector('[data-picker-action="files"]').addEventListener(
      "click", () => files.click());
    for (const picker of [camera, files]) {
      picker.addEventListener("change", () => {
        add(picker.files);
        picker.value = "";
      });
    }
    root.addEventListener("dragover", event => {
      event.preventDefault();
      root.classList.add("dragging");
    });
    root.addEventListener("dragleave", () => root.classList.remove("dragging"));
    root.addEventListener("drop", event => {
      event.preventDefault();
      root.classList.remove("dragging");
      add(event.dataTransfer.files);
    });
    list.addEventListener("click", event => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const index = pages.findIndex(page =>
        page.id === button.closest("li").dataset.page);
      if (index < 0) return;
      if (button.dataset.action === "remove") {
        if (pages[index].previewUrl) URL.revokeObjectURL(pages[index].previewUrl);
        pages.splice(index, 1);
      } else {
        const target = button.dataset.action === "earlier" ? index - 1 : index + 1;
        if (target >= 0 && target < pages.length) {
          [pages[index], pages[target]] = [pages[target], pages[index]];
        }
      }
      render();
    });

    return {
      get count() { return pages.length; },
      clear() {
        pages.forEach(page => {
          if (page.previewUrl) URL.revokeObjectURL(page.previewUrl);
        });
        pages = [];
        render();
      },
      async preparedFiles(onProgress = () => {}) {
        const prepared = [];
        for (let index = 0; index < pages.length; index += 1) {
          onProgress(index + 1, pages.length);
          prepared.push(await resizeLargeImage(pages[index].file, maxImageEdge));
        }
        return prepared;
      },
    };
  }

  global.createOrderedDocumentPicker = createOrderedDocumentPicker;
}(window));
