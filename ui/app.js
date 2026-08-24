// md2feishu2md 前端逻辑
(function () {
  "use strict";

  const api = window.pywebview && window.pywebview.api ? window.pywebview.api : null;

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

  function renderBatchList() {
    const ul = $("batch-list");
    ul.innerHTML = "";
    batchFiles.forEach((f) => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = f.name;
      const badge = document.createElement("span");
      badge.className = "badge " + f.status;
      badge.textContent = { pending: "排队", running: "进行中", success: "成功", failed: "失败" }[f.status] || f.status;
      li.appendChild(name);
      li.appendChild(badge);
      if (f.status === "failed") {
        const retryBtn = document.createElement("button");
        retryBtn.className = "btn";
        retryBtn.textContent = "重试";
        retryBtn.addEventListener("click", () => retryTask(f.taskId));
        li.appendChild(retryBtn);
        if (f.error) {
          const err = document.createElement("span");
          err.className = "err";
          err.textContent = f.error;
          li.appendChild(err);
        }
      }
      ul.appendChild(li);
    });
  }

  $("btn-pick-files").addEventListener("click", async () => {
    const paths = await api.open_files_dialog();
    if (paths && paths.length) {
      paths.forEach((p) => {
        batchFiles.push({ name: p.split(/[\\/]/).pop(), path: p, status: "pending", taskId: null, error: "" });
      });
      renderBatchList();
    }
  });

  $("btn-batch-clear").addEventListener("click", () => {
    stopPoll();
    batchFiles = [];
    renderBatchList();
  });

  $("btn-batch-start").addEventListener("click", async () => {
    if (!batchFiles.length) { alert("请先添加文件"); return; }
    const dest = resolveDest($("batch-dest"), $("batch-dest-manual"));
    const paths = batchFiles.filter((f) => f.status === "pending").map((f) => f.path);
    if (!paths.length) { alert("没有待转换的文件"); return; }
    const btn = $("btn-batch-start");
    btn.disabled = true;
    try {
      const r = await api.convert_batch(paths, dest || null);
      if (!r.ok) { alert("提交失败: " + r.error); return; }
      // 关联 task_id，并乐观标记为 running（避免 800ms 轮询窗口内重复提交）
      let idx = 0;
      batchFiles.forEach((f) => {
        if (f.status === "pending") {
          f.taskId = r.task_ids[idx++];
          f.status = "running";
        }
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
    renderBatchList();
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
        showResult($("reverse-result"), true, "转换成功", "保存至：" + r.path);
      } else {
        showResult($("reverse-result"), false, "转换失败", r.error || "");
      }
    } catch (e) {
      showResult($("reverse-result"), false, "转换失败", String(e));
    } finally {
      btn.disabled = false; btn.textContent = "转换下载";
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
    const r = await api.test_connection();
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
    const r = await api.set_config(appId, appSecret, concurrency, outDir);
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

  init();
})();
