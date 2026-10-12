// md2feishu2md 前端逻辑
(function () {
  "use strict";

  // pywebview 的 API 对象在页面加载完成后才注入（_pywebviewready 事件）。
  // 在页面加载时直接取 window.pywebview.api 会得到 null，导致所有按钮静默失效。
  let api = null;

  function $(id) { return document.getElementById(id); }

  // ------------------------------------------------------------ 结果卡片（DOM API 构建，防 XSS）
  function showResult(el, ok, title, text) {
    el.classList.remove("hidden", "success", "error");
    el.classList.add(ok ? "success" : "error");
    el.innerHTML = "";
    const t = document.createElement("div");
    t.className = "title";
    t.textContent = title;
    el.appendChild(t);
    if (text) appendText(el, text);
    return el;
  }
  function appendText(el, text) {
    const p = document.createElement("p");
    p.textContent = text;
    el.appendChild(p);
  }
  function appendLink(el, url) {
    const a = document.createElement("a");
    a.href = url;
    a.textContent = url;
    a.target = "_blank";
    el.appendChild(a);
  }
  function appendActions(el) {
    const div = document.createElement("div");
    div.className = "actions";
    el.appendChild(div);
    return div;
  }
  function appendWarning(el, text) {
    const p = document.createElement("p");
    p.className = "warn-line";
    p.textContent = "⚠ " + text;
    el.appendChild(p);
  }
  function hideResult(el) { el.classList.add("hidden"); }

  function copyText(text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  }

  // ------------------------------------------------------------ tab 切换
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
    });
  });

  // ------------------------------------------------------------ 目标路径树
  let destTree = { wiki: [], folders: [] };

  function renderDest(select) {
    select.innerHTML = "";
    const root = document.createElement("option");
    root.value = "";
    root.textContent = "（根目录）";
    select.appendChild(root);

    destTree.wiki.forEach((sp) => {
      const opt = document.createElement("option");
      opt.value = sp.name;
      opt.textContent = "📚 知识库: " + sp.name;
      select.appendChild(opt);
    });
    destTree.folders.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f.name;
      opt.textContent = "📁 文件夹: " + f.name;
      select.appendChild(opt);
    });
  }

  async function loadDestTree() {
    if (!api) return;
    try {
      const r = await api.list_dest_tree();
      if (r && r.ok) {
        destTree = { wiki: r.wiki || [], folders: r.folders || [] };
        renderDest($("convert-dest"));
        renderDest($("batch-dest"));
      }
    } catch (e) { /* 忽略 */ }
  }

  function resolveDest(select, manual) {
    const manualVal = manual.value.trim();
    if (manualVal) return manualVal;
    return select.value;
  }

  // ------------------------------------------------------------ 转换 tab
  $("btn-convert-clear").addEventListener("click", () => {
    // 清空本页所有输入，方便转换下一个文档
    $("convert-file").value = "";
    $("convert-title").value = "";
    $("convert-dest-manual").value = "";
    $("convert-dest").value = "";
    hideResult($("convert-result"));
  });

  $("btn-pick-file").addEventListener("click", async () => {
    const p = await api.open_file_dialog();
    if (p) {
      $("convert-file").value = p;
      if (!$("convert-title").value) {
        const base = p.split(/[\\/]/).pop().replace(/\.md$/i, "");
        $("convert-title").value = base;
      }
    }
  });

  $("btn-convert").addEventListener("click", async () => {
    const path = $("convert-file").value.trim();
    if (!path) { alert("请先选择 .md 文件"); return; }
    const title = $("convert-title").value.trim();
    const dest = resolveDest($("convert-dest"), $("convert-dest-manual"));
    const btn = $("btn-convert");
    btn.disabled = true; btn.textContent = "转换中…";
    hideResult($("convert-result"));
    try {
      const r = await api.convert_md(path, title, dest || null);
      if (r.ok) {
        const card = showResult($("convert-result"), true, "转换成功");
        appendLink(card, r.url);
        if (r.warning) appendWarning(card, r.warning);
        const actions = appendActions(card);
        const open = document.createElement("a");
        open.href = r.url; open.target = "_blank"; open.textContent = "打开文档";
        const copy = document.createElement("button");
        copy.className = "btn"; copy.textContent = "复制链接";
        copy.addEventListener("click", () => copyText(r.url));
        actions.appendChild(open); actions.appendChild(copy);
      } else {
        showResult($("convert-result"), false, "转换失败", r.error || "");
      }
    } catch (e) {
      showResult($("convert-result"), false, "转换失败", String(e));
    } finally {
      btn.disabled = false; btn.textContent = "开始转换";
    }
  });

  // ------------------------------------------------------------ 批量 tab
  let batchFiles = [];
  let pollTimer = null;

  // 行内目标链接记忆：{ [文件路径]: 链接 }，添加同文件时自动回填
  const TARGETS_KEY = "md2feishu2md.batchTargets";
  function loadTargets() {
    try { return JSON.parse(localStorage.getItem(TARGETS_KEY)) || {}; } catch (e) { return {}; }
  }
  function saveTarget(path, url) {
    const m = loadTargets();
    if (url) m[path] = url; else delete m[path];
    localStorage.setItem(TARGETS_KEY, JSON.stringify(m));
  }

  function renderBatchList() {
    const ul = $("batch-list");
    ul.innerHTML = "";
    batchFiles.forEach((f) => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = f.name;
      name.title = f.path;
      li.appendChild(name);

      // 行内目标文档链接：留空 = 用上方统一目标
      const target = document.createElement("input");
      target.type = "text";
      target.className = "target-input";
      target.placeholder = "目标文档链接（留空用统一目标）";
      target.value = f.target || "";
      target.addEventListener("input", () => {
        f.target = target.value;
        saveTarget(f.path, target.value.trim());
      });
      li.appendChild(target);

      const delBtn = document.createElement("button");
      delBtn.className = "del-btn";
      delBtn.title = "从列表删除";
      delBtn.innerHTML = '<svg class="trash-svg" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">'
        + '<path d="M8.5 2.5a1 1 0 0 0-.8.4l-.7.9H4.5a1.2 1.2 0 1 0 0 2.4h15a1.2 1.2 0 1 0 0-2.4h-2.5l-.7-.9a1 1 0 0 0-.8-.4h-7z"/>'
        + '<path d="M5.6 8h12.8l-.9 11.2a2.4 2.4 0 0 1-2.4 2.2H8.9a2.4 2.4 0 0 1-2.4-2.2L5.6 8z"/>'
        + '<rect x="9" y="10.5" width="1.6" height="8" rx="0.8"/>'
        + '<rect x="11.2" y="10.5" width="1.6" height="8" rx="0.8"/>'
        + '<rect x="13.4" y="10.5" width="1.6" height="8" rx="0.8"/>'
        + '</svg>';
      delBtn.addEventListener("click", () => removeBatchItem(f));
      li.appendChild(delBtn);

      // 状态区：轮询时只重建这一块，不碰输入框（避免打字被 800ms 重绘打断）
      const statusBox = document.createElement("span");
      statusBox.className = "status-box";
      li.appendChild(statusBox);
      f._els = { statusBox };
      ul.appendChild(li);
      renderRowStatus(f);
    });
  }

  function renderRowStatus(f) {
    const box = f._els && f._els.statusBox;
    if (!box) return;
    box.innerHTML = "";
    const badge = document.createElement("span");
    badge.className = "badge " + f.status;
    badge.textContent = { pending: "排队", running: "进行中", success: "成功", failed: "失败" }[f.status] || f.status;
    box.appendChild(badge);
    if (f.status === "failed") {
      const retryBtn = document.createElement("button");
      retryBtn.className = "btn";
      retryBtn.textContent = "重试";
      retryBtn.addEventListener("click", () => retryTask(f.taskId));
      box.appendChild(retryBtn);
      if (f.error) {
        const err = document.createElement("span");
        err.className = "err";
        err.textContent = f.error;
        box.appendChild(err);
      }
    } else if (f.status === "success" && f.error) {
      // 成功但有警告（如部分图片上传失败），warning 借道 error 字段展示
      const warn = document.createElement("span");
      warn.className = "err warn-line";
      warn.textContent = "⚠ " + f.error;
      box.appendChild(warn);
    }
  }

  function updateBatchStatuses() {
    batchFiles.forEach(renderRowStatus);
  }

  $("btn-pick-files").addEventListener("click", async () => {
    const paths = await api.open_files_dialog();
    if (paths && paths.length) {
      const saved = loadTargets();
      paths.forEach((p) => {
        batchFiles.push({
          name: p.split(/[\\/]/).pop(), path: p, status: "pending",
          taskId: null, error: "", target: saved[p] || "",
        });
      });
      renderBatchList();
    }
  });

  $("btn-batch-clear").addEventListener("click", () => {
    stopPoll();
    batchFiles = [];
    renderBatchList();
  });

  function removeBatchItem(f) {
    // 从列表移除单个文件项（仅 UI 移除，不影响已在后台提交的任务）
    const idx = batchFiles.indexOf(f);
    if (idx >= 0) batchFiles.splice(idx, 1);
    renderBatchList();
  }

  $("btn-batch-start").addEventListener("click", async () => {
    if (!batchFiles.length) { alert("请先添加文件"); return; }
    const unified = resolveDest($("batch-dest"), $("batch-dest-manual")) || "";
    // 再次点击「开始全部」= 对列表中所有未在运行的文件重新执行
    // （已成功的会按行内链接再次覆盖写入；running 中的跳过，防止重复任务）
    // 每个文件的目标 = 行内链接 || 统一目标；模式由目标形态决定：
    // 行内链接=覆盖写入各自文档；统一链接=追加到同一篇；路径/空=各自新建
    const targets = batchFiles.filter((f) => f.status !== "running");
    const items = targets.map((f) => {
      const own = (f.target || "").trim();
      const dest = own || unified;
      const isDocLink = /^https?:\/\//i.test(dest) && /\/(docx|wiki)\//i.test(dest);
      return { path: f.path, dest, mode: isDocLink ? (own ? "overwrite" : "append") : "create" };
    });
    if (!items.length) { alert("没有可转换的文件（任务进行中）"); return; }
    const btn = $("btn-batch-start");
    btn.disabled = true;
    try {
      const r = await api.convert_batch(items);
      if (!r.ok) { alert("提交失败: " + r.error); return; }
      // 关联 task_id，并乐观标记为 running（避免 800ms 轮询窗口内重复提交）
      let idx = 0;
      targets.forEach((f) => {
        f.taskId = r.task_ids[idx++];
        f.status = "running";
      });
      renderBatchList();
      startPoll();
    } catch (e) { alert("提交失败: " + e); } finally {
      btn.disabled = false;
    }
  });

  function startPoll() {
    stopPoll();
    pollTimer = setInterval(pollTasks, 800);
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollTasks() {
    const tasks = await api.get_tasks();
    const byId = {};
    tasks.forEach((t) => { byId[t.id] = t; });
    let allDone = true;
    batchFiles.forEach((f) => {
      if (f.taskId != null && byId[f.taskId]) {
        const t = byId[f.taskId];
        f.status = t.status;
        f.error = t.error || "";
        if (t.status === "running" || t.status === "pending") allDone = false;
      }
    });
    updateBatchStatuses();  // 只更新状态区，不重建输入框（避免打字被打断）
    if (allDone) stopPoll();
  }

  async function retryTask(taskId) {
    const r = await api.retry_task(taskId);
    if (r.ok) { startPoll(); }
  }

  // ------------------------------------------------------------ 反向 tab
  $("btn-pick-outdir").addEventListener("click", async () => {
    const p = await api.open_folder_dialog();
    if (p) $("reverse-out").value = p;
  });

  $("btn-reverse").addEventListener("click", async () => {
    const url = $("reverse-url").value.trim();
    if (!url) { alert("请粘贴飞书文档链接"); return; }
    const out = $("reverse-out").value.trim();
    const btn = $("btn-reverse");
    btn.disabled = true; btn.textContent = "转换中…";
    hideResult($("reverse-result"));
    try {
      const r = await api.convert_back(url, out || null);
      if (r.ok) {
        const card = showResult($("reverse-result"), true, "转换成功", "保存至：" + r.path);
        if (r.warning) appendWarning(card, r.warning);
      } else {
        showResult($("reverse-result"), false, "转换失败", r.error || "");
      }
    } catch (e) {
      showResult($("reverse-result"), false, "转换失败", String(e));
    } finally {
      btn.disabled = false; btn.textContent = "转换下载";
    }
  });

  $("btn-clone").addEventListener("click", async () => {
    // 复刻：读只读文档 → 生成归用户所有的新文档（可编辑）
    const url = $("reverse-url").value.trim();
    if (!url) { alert("请粘贴飞书文档链接"); return; }
    const btn = $("btn-clone");
    btn.disabled = true; btn.textContent = "复刻中…";
    hideResult($("reverse-result"));
    try {
      const r = await api.clone_doc(url, null);
      if (r.ok) {
        const card = showResult($("reverse-result"), true, "复刻成功");
        appendLink(card, r.url);
        if (r.warning) appendWarning(card, r.warning);
        const actions = appendActions(card);
        const open = document.createElement("a");
        open.href = r.url; open.target = "_blank"; open.textContent = "打开文档";
        const copy = document.createElement("button");
        copy.className = "btn"; copy.textContent = "复制链接";
        copy.addEventListener("click", () => copyText(r.url));
        actions.appendChild(open); actions.appendChild(copy);
      } else {
        showResult($("reverse-result"), false, "复刻失败", r.error || "");
      }
    } catch (e) {
      showResult($("reverse-result"), false, "复刻失败", String(e));
    } finally {
      btn.disabled = false; btn.textContent = "复刻成我的文档";
    }
  });

  // ------------------------------------------------------------ 删除 tab
  $("btn-delete").addEventListener("click", async () => {
    const url = $("delete-url").value.trim();
    if (!url) { alert("请粘贴要删除的飞书文档链接"); return; }
    const ok = confirm("确认删除该文档？\n\n仅能删除本应用创建/拥有（通常是转换产出）的文档；删除后移入飞书回收站。");
    if (!ok) return;
    const btn = $("btn-delete");
    btn.disabled = true; btn.textContent = "删除中…";
    hideResult($("delete-result"));
    try {
      const r = await api.delete_doc(url);
      if (r.ok) {
        showResult($("delete-result"), true, "已删除", "文档已移入飞书回收站（" + r.doc_id + "）");
      } else {
        showResult($("delete-result"), false, "删除失败", r.error || "");
      }
    } catch (e) {
      showResult($("delete-result"), false, "删除失败", String(e));
    } finally {
      btn.disabled = false; btn.textContent = "删除文档";
    }
  });

  // ------------------------------------------------------------ 设置 tab
  const concurrencyInput = $("set-concurrency");
  concurrencyInput.addEventListener("input", () => {
    $("set-concurrency-val").textContent = concurrencyInput.value;
  });

  $("btn-pick-set-outdir").addEventListener("click", async () => {
    const p = await api.open_folder_dialog();
    if (p) $("set-outdir").value = p;
  });

  $("btn-test-conn").addEventListener("click", async () => {
    // 优先用输入框当前值测试（不落盘）；输入框为空才用已保存配置
    const appId = $("set-app-id").value.trim();
    const appSecret = $("set-app-secret").value;
    const r = await api.test_connection(appId || null, appSecret || null);
    if (r.ok) {
      showResult($("settings-result"), true, "连接成功", "凭据有效");
      updateCredStatus(true);
    } else {
      showResult($("settings-result"), false, "连接失败", r.error || "");
    }
  });

  $("btn-save-config").addEventListener("click", async () => {
    const appId = $("set-app-id").value.trim();
    const appSecret = $("set-app-secret").value;
    const concurrency = parseInt(concurrencyInput.value, 10);
    const outDir = $("set-outdir").value.trim();
    const ownerOpenId = $("set-owner-openid").value.trim();
    const r = await api.set_config(appId, appSecret, concurrency, outDir, ownerOpenId);
    if (r.ok) {
      showResult($("settings-result"), true, "已保存", "配置已保存");
      $("set-app-secret").value = "";
      updateCredStatus(!!appId);
      loadDestTree();
    } else {
      showResult($("settings-result"), false, "保存失败", r.error || "");
    }
  });

  // ------------------------------------------------------------ 状态栏
  function updateCredStatus(ok) {
    const el = $("cred-status");
    el.textContent = ok ? "凭据已配置" : "凭据未配置";
    el.className = ok ? "status-ok" : "status-no";
  }

  async function loadConfig() {
    if (!api) return;
    const cfg = await api.get_config();
    if (cfg.app_id) {
      $("set-app-id").value = cfg.app_id;
      updateCredStatus(true);
    }
    if (cfg.concurrency) {
      concurrencyInput.value = cfg.concurrency;
      $("set-concurrency-val").textContent = cfg.concurrency;
    }
    if (cfg.default_out_dir) $("set-outdir").value = cfg.default_out_dir;
    if (cfg.owner_open_id) $("set-owner-openid").value = cfg.owner_open_id;
  }

  // ------------------------------------------------------------ 初始化
  async function init() {
    if (!api) {
      updateCredStatus(false);
      return;
    }
    await loadConfig();
    await loadDestTree();
  }

  // pywebview 注入完成后触发；比 window.onload 更可靠（两者都监听）
  function bindApi() {
    if (window.pywebview && window.pywebview.api) {
      api = window.pywebview.api;
      init();
    }
  }
  window.addEventListener("pywebviewready", bindApi);
  // 兜底：某些情况下事件先于本脚本执行（脚本被 defer/放到 body 尾部时）
  bindApi();
})();
