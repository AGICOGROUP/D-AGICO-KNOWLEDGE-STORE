/* Directory traversal is separate from upload: preserve actual File objects and bytes. */
"use strict";
(() => {
  const relativePaths = new WeakMap();
  function fileLabel(file) {
    return file.webkitRelativePath || relativePaths.get(file) || file.name;
  }
  async function collectDroppedFiles(dataTransfer, onProgress = () => {}) {
    // Capture entries/files during the drop event, before its data store becomes protected.
    const fallback = Array.from(dataTransfer.files || []);
    const pending = [], files = [], errors = [];
    for (const item of Array.from(dataTransfer.items || [])) {
      if (item.kind !== "file") continue;
      try {
        const entry = item.webkitGetAsEntry?.();
        if (entry) pending.push({entry, path: entry.name});
        else {
          const file = item.getAsFile?.();
          if (file) pending.push({file});
          else errors.push("拖入内容无法读取，请使用选择文件夹或选择文件重试。");
        }
      } catch { errors.push("拖入内容无法读取，请使用选择文件夹或选择文件重试。"); }
    }
    if (!pending.length && !errors.length) pending.push(...fallback.map(file => ({file})));
    while (pending.length) {
      const item = pending.pop();
      if (item.file) { files.push(item.file); onProgress(files.length); continue; }
      try {
        if (item.entry.isFile) {
          const file = await new Promise((resolve, reject) => item.entry.file(resolve, reject));
          relativePaths.set(file, item.path);
          files.push(file); onProgress(files.length);
        } else if (item.entry.isDirectory) {
          const reader = item.entry.createReader();
          // Chromium returns directory entries in batches, often 100 at a time.
          // Drain the reader until empty, including every nested directory.
          while (true) {
            const children = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
            if (!children.length) break;
            for (const entry of children) pending.push({entry, path: `${item.path}/${entry.name}`});
          }
        }
      } catch { errors.push(`${item.path}：无法读取，请检查文件或文件夹权限后重试。`); }
    }
    files.sort((a, b) => fileLabel(a).localeCompare(fileLabel(b)));
    return {files, errors};
  }
  window.agicoFolderUpload = {fileLabel, collectDroppedFiles};
})();
