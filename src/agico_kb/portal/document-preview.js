/* Read-only, authenticated, lazy page viewer. No document is sent to an external viewer. */
(() => {
  let active;
  const node = (tag, className = "", text = "") => {
    const result = document.createElement(tag); result.className = className; result.textContent = text; return result;
  };
  const icon = (name) => {
    const paths = {
      previous:["M19 12H5", "m11 18-6-6 6-6"],
      next:["M5 12h14", "m13 6 6 6-6 6"],
      smaller:["M21 21l-4.4-4.4", "M15 11H7"],
      larger:["M21 21l-4.4-4.4", "M15 11H7", "M11 7v8"],
      left:["M3 8V3", "M3 8h5", "M3 8l3.3-3.3A9 9 0 1 1 3.2 14"],
      right:["M21 8V3", "M21 8h-5", "M21 8l-3.3-3.3A9 9 0 1 0 20.8 14"],
      fit:["M4 4v16", "M20 4v16", "M7 12h10", "m10 9-3 3 3 3", "m14 9 3 3-3 3"]
    };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [key, value] of Object.entries({viewBox:"0 0 24 24", fill:"none", stroke:"currentColor", "stroke-width":"1.7", "stroke-linecap":"round", "stroke-linejoin":"round", "aria-hidden":"true", focusable:"false"})) svg.setAttribute(key, value);
    if (name === "smaller" || name === "larger") {
      const circle = document.createElementNS(svg.namespaceURI, "circle");
      circle.setAttribute("cx", "11"); circle.setAttribute("cy", "11"); circle.setAttribute("r", "7"); svg.append(circle);
    }
    for (const value of paths[name]) {
      const path = document.createElementNS(svg.namespaceURI, "path"); path.setAttribute("d", value); svg.append(path);
    }
    return svg;
  };
  const fullDocument = (item) => /\.(pdf|docx?|xlsx?|pptx?|od[tps]|rtf|png|jpe?g|webp|gif|bmp|tiff?)$/i.test(item.filename || item.title);
  const close = () => {
    if (!active) return;
    const current = active; active = null;
    current.controller.abort(); current.observer?.disconnect();
    current.resize?.disconnect();
    for (const url of current.urls) URL.revokeObjectURL(url);
    current.dialog.close(); current.dialog.remove();
    if (current.focus?.isConnected) current.focus.focus();
  };
  const pause = (signal) => new Promise((resolve, reject) => {
    const timer = setTimeout(done, 1000);
    function done() { signal.removeEventListener("abort", abort); resolve(); }
    function abort() { clearTimeout(timer); reject(new DOMException("Closed", "AbortError")); }
    signal.addEventListener("abort", abort, {once:true});
    if (signal.aborted) abort();
  });
  async function request(current, url) {
    const deadline = Date.now() + 240000;
    while (!current.controller.signal.aborted) {
      const response = await fetch(url, {credentials: "same-origin",
        cache: "no-store", redirect: "error", signal: AbortSignal.any([current.controller.signal, AbortSignal.timeout(30000)])});
      if (response.status === 202 || response.status === 503) {
        if (Date.now() > deadline) throw new Error("预览仍在生成，请稍后重新打开。");
        await pause(current.controller.signal); continue;
      }
      if (!response.ok) throw new Error(response.status === 401 || response.status === 404
        ? "无权访问此文件，或文件已撤回。" : "暂时无法生成预览，可稍后重试或下载原文件查看。");
      return response;
    }
    throw new DOMException("Closed", "AbortError");
  }
  async function open(item, thumbnail) {
    close();
    const current = {controller:new AbortController(), urls:new Set(), zoom:.75, angle:0, pages:[], focus:document.activeElement};
    active = current;
    const dialog = node("dialog", "document-preview-dialog"); current.dialog = dialog;
    dialog.setAttribute("aria-label", "文件预览");
    const heading = node("h2", "", item.title || item.filename);
    const closeButton = node("button", "button secondary", "关闭预览"); closeButton.type = "button"; closeButton.addEventListener("click", close);
    const header = node("div", "media-preview-header"); header.append(heading, closeButton);
    const toolbar = node("div", "document-preview-toolbar");
    toolbar.setAttribute("role", "group"); toolbar.setAttribute("aria-label", "预览操作");
    const scroller = node("div", "document-preview-scroller"); scroller.tabIndex = 0; scroller.setAttribute("aria-label", "文件内容，可上下滚动");
    const status = node("p", "document-preview-status", "正在生成文件预览…"); status.setAttribute("role", "status");
    dialog.append(header, toolbar, scroller, status); document.body.append(dialog);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => { if (active === current) close(); });
    dialog.showModal();
    const button = (text, symbol, action) => {
      const value = node("button", "preview-icon-button"); value.type = "button"; value.disabled = true;
      value.setAttribute("aria-label", text); value.title = text; value.append(icon(symbol));
      value.addEventListener("click", action); toolbar.append(value); return value;
    };
    const separators = [];
    const separate = () => {
      const line = node("span", "preview-toolbar-separator"); line.setAttribute("aria-hidden", "true");
      separators.push(line); toolbar.append(line);
    };
    let page = 1;
    function jump(value) {
      page = Math.max(1, Math.min(current.pages.length, value));
      const target = current.pages[page - 1];
      if (target) { scroller.scrollTop += target.section.getBoundingClientRect().top - scroller.getBoundingClientRect().top; enqueue(target, true); }
      pageInput.value = page; updateNavigation();
    }
    const prev = button("上一页", "previous", () => jump(page - 1));
    const pageInput = node("input", "preview-page-number"); pageInput.type = "number"; pageInput.min = 1; pageInput.value = 1; pageInput.disabled = true;
    pageInput.setAttribute("aria-label", "跳转页码");
    pageInput.addEventListener("change", () => jump(Number(pageInput.value) || 1)); toolbar.append(pageInput);
    pageInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); jump(Number(pageInput.value) || 1); }
    });
    const count = node("span", "", "/ — 页"); toolbar.append(count);
    const next = button("下一页", "next", () => jump(page + 1));
    separate();
    const smaller = button("缩小", "smaller", () => { current.zoom = Math.max(.5, current.zoom - .25); layout(); });
    const larger = button("放大", "larger", () => { current.zoom = Math.min(3, current.zoom + .25); layout(); });
    const fit = button("适合宽度", "fit", () => { current.zoom = 1; layout(); });
    separate();
    const rotateLeft = button("左旋转", "left", () => { current.angle = (current.angle + 270) % 360; layout(); });
    const rotateRight = button("右旋转", "right", () => { current.angle = (current.angle + 90) % 360; layout(); });
    function updateNavigation() { prev.disabled = page <= 1; next.disabled = page >= current.pages.length; }
    function layout() {
      const available = Math.max(180, scroller.clientWidth - 32);
      for (const record of current.pages) {
        const ratio = record.ratio || .707;
        const sideways = current.angle % 180 !== 0;
        const width = Math.min(1100, available) * current.zoom;
        const height = width / (sideways ? 1 / ratio : ratio);
        record.surface.style.width = `${width}px`; record.surface.style.height = `${height}px`;
        if (record.image) {
          record.image.style.width = `${sideways ? height : width}px`;
          record.image.style.height = `${sideways ? width : height}px`;
          record.image.style.transform = `translate(-50%,-50%) rotate(${current.angle}deg)`;
        }
      }
      smaller.disabled = current.zoom <= .5; larger.disabled = current.zoom >= 3;
    }
    const queue = []; let running = 0;
    function release(record) {
      URL.revokeObjectURL(record.url); current.urls.delete(record.url);
      record.surface.replaceChildren(); record.image = null; record.state = "";
    }
    function enqueue(record, priority = false) {
      if (record.state || active !== current) return;
      record.state = "queued";
      if (priority) queue.unshift(record); else queue.push(record);
      pump();
    }
    function pump() {
      while (running < 3 && queue.length && active === current) {
        const record = queue.shift(); running++;
        load(record).finally(() => { running--; pump(); });
      }
    }
    async function load(record) {
      try {
        record.label.textContent = `第 ${record.number} 页 · 加载中…`;
        const response = await request(current, `/v1/versions/${encodeURIComponent(item.version_id)}/preview/pages/${record.number}?width=2400`);
        const blob = await response.blob(); if (active !== current) return;
        const url = URL.createObjectURL(blob); current.urls.add(url);
        const image = node("img"); image.alt = `第 ${record.number} 页`; image.src = url;
        record.image = image; record.url = url;
        image.addEventListener("load", () => { record.ratio = image.naturalWidth / image.naturalHeight; layout(); }, {once:true});
        record.surface.replaceChildren(image); record.label.textContent = `第 ${record.number} 页`;
        await image.decode(); if (active !== current) return;
        record.state = "ready"; layout();
        if (record.visible === false) release(record);
      } catch (error) {
        if (active !== current) return;
        record.label.textContent = `第 ${record.number} 页 · ${error.message}`;
        const retry = node("button", "button secondary", "重新加载此页"); retry.type = "button";
        retry.addEventListener("click", () => { record.state = ""; record.surface.replaceChildren(); enqueue(record, true); }); record.surface.replaceChildren(retry);
        record.state = "error";
      }
    }
    try {
      if (!fullDocument(item)) {
        for (const control of [prev, next, rotateLeft, rotateRight, smaller, larger, fit]) control.hidden = true;
        for (const line of separators) line.hidden = true;
        pageInput.hidden = true; count.hidden = true;
        if (!thumbnail) throw new Error("此格式暂不支持全文预览，可下载原文件查看。");
        const image = node("img", "single-file-preview"); image.src = thumbnail; image.alt = item.title || item.filename; scroller.append(image);
        status.textContent = "图像预览；完整内容请下载原文件查看。"; return;
      }
      const response = await request(current, `/v1/versions/${encodeURIComponent(item.version_id)}/preview`);
      const manifest = await response.json(); if (active !== current) return;
      count.textContent = `/ ${manifest.pages} 页`; pageInput.max = manifest.pages; pageInput.disabled = false;
      for (let number = 1; number <= manifest.pages; number++) {
        const section = node("section", "document-preview-page"); const surface = node("div", "preview-page-surface");
        const label = node("p", "preview-page-label", `第 ${number} 页`); section.append(surface, label); scroller.append(section);
        const record = {number, section, surface, label, state:""}; current.pages.push(record); section.previewRecord = record;
      }
      for (const control of [rotateLeft, rotateRight, smaller, larger, fit]) control.disabled = false;
      current.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          const record = entry.target.previewRecord;
          record.visible = entry.isIntersecting;
          if (entry.isIntersecting) enqueue(record);
          else if (record.state === "ready") {
            // Retain page dimensions, release off-screen pixels in long documents.
            release(record);
          }
        }
      }, {root:scroller, rootMargin:"500px"});
      for (const record of current.pages) current.observer.observe(record.section);
      current.resize = new ResizeObserver(layout); current.resize.observe(scroller); layout(); updateNavigation();
      let scheduled = false;
      scroller.addEventListener("scroll", () => {
        if (scheduled) return; scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false; if (active !== current) return;
          const top = scroller.getBoundingClientRect().top + 20;
          const visible = current.pages.find((record) => record.section.getBoundingClientRect().bottom > top);
          if (visible) { page = visible.number; pageInput.value = page; updateNavigation(); }
        });
      });
      enqueue(current.pages[0], true);
      status.textContent = "只读预览 · 可滚动查看全文，或输入页码跳转";
    } catch (error) { if (active === current) status.textContent = error.message; }
  }
  window.agicoDocumentPreview = {open, close, supports:fullDocument, isOpen:() => !!active};
})();
