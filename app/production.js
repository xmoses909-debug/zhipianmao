/* 制片帽 · 制作板块（制片统筹系统）
   ⚠️ 互不干扰约定：本文件与 app.js（策划板块）完全分离——
   - 不调用 app.js 的任何函数；只【只读】localStorage 的登录令牌（maozhipian.token），从不写它。
   - 界面是独立的全屏覆盖层（.prod-root），不参与 app.js 的屏幕路由（showScreen）。
   - 唯一的接入点：接管主页导航里"制作"那颗按钮（app.js 没绑它的事件）。
   三个区域：① 剧本解剖分析 ② 分场表/顺场表 ③ 参考预算 —— 全部可编辑、可保存、可导出。 */
(function () {
  "use strict";

  /* ===== 小工具 ===== */
  var HAT = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 39 C 9 25, 31 14, 45 23 C 51 27, 52 34, 50 39"/><path d="M12 39 L 50 39"/><path d="M12 39 C 5 40, 7 45, 17 44"/></svg>';
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function fmtWan(n) { n = n || 0; return n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, "") + " 万字" : n + " 字"; }
  function fmtDate(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function num(v, dflt) { var n = parseFloat(v); return isNaN(n) ? (dflt || 0) : n; }
  function deepCopy(o) { return JSON.parse(JSON.stringify(o)); }
  function splitArr(s) {
    return String(s || "").split(/[、，,;；\/]+/).map(function (x) { return x.trim(); }).filter(function (x) { return x; });
  }

  /* ===== 身份：登录令牌只读复用；匿名时用本机设备号 ===== */
  var DKEY = "zpm.prod.device";
  function deviceId() {
    var d;
    try { d = localStorage.getItem(DKEY); } catch (e) {}
    if (!d) {
      d = "dv" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      try { localStorage.setItem(DKEY, d); } catch (e) {}
    }
    return d;
  }
  function token() { try { return localStorage.getItem("maozhipian.token") || ""; } catch (e) { return ""; } }

  function api(path, opts) {
    opts = opts || {};
    var h = { "Content-Type": "application/json" };
    if (token()) h["Authorization"] = "Bearer " + token();
    var url = path, body = null;
    if (opts.method === "POST") {
      body = opts.body || {};
      body.device = deviceId();
      body = JSON.stringify(body);
    } else {
      url += (url.indexOf("?") > -1 ? "&" : "?") + "device=" + encodeURIComponent(deviceId());
    }
    return fetch(url, { method: opts.method || "GET", headers: h, body: body })
      .then(function (r) { return r.json(); });
  }

  /* ===== 全局状态 ===== */
  var S = {
    built: false, view: "home", projects: [], cur: null,
    tab: "analysis", subTab: "order", editingAnalysis: false, editBuf: null,
    dirty: { scenes: false, budget: false },
    job: null,           // {id, kind, t0} 进行中的任务
    pollTimer: null, progTimer: null
  };

  function el(id) { return document.getElementById(id); }

  /* ===== Toast ===== */
  var toastTimer = null;
  function toast(msg, isErr) {
    var t = el("prodToast");
    t.textContent = msg;
    t.className = "prod-toast show" + (isErr ? " err" : "");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = "prod-toast" + (isErr ? " err" : ""); }, isErr ? 5000 : 2600);
  }

  /* ===== 骨架 ===== */
  function buildRoot() {
    if (S.built) return;
    var root = document.createElement("div");
    root.className = "prod-root"; root.id = "prodRoot"; root.hidden = true;
    root.innerHTML =
      '<div class="prod-topbar">'
      + '<button class="prod-back" id="prodBack">← 返回选题雷达</button>'
      + '<div class="prod-brand"><span class="prod-hat">' + HAT + '</span>'
      + '<div class="prod-brand-text"><strong>制片帽 · 制作</strong><span>制片统筹系统</span></div></div>'
      + '<div class="prod-top-right" id="prodTopRight"></div>'
      + '</div>'
      + '<div class="prod-wrap" id="prodView"></div>'
      + '<div class="prod-loading" id="prodLoading" hidden>'
      + '<div class="prod-loading-card"><div class="prod-hat-pulse">' + HAT + '</div>'
      + '<h3 id="prodLoadTitle">制片帽正在工作</h3>'
      + '<div class="prod-prog-track"><i id="prodProgFill"></i></div>'
      + '<div class="prod-prog-label" id="prodProgLabel"></div>'
      + '<p class="prod-loading-tip" id="prodLoadTip"></p>'
      + '<button class="prod-loading-bg" id="prodLoadBg">收起浮层，让它后台继续 →</button>'
      + '</div></div>'
      + '<div class="prod-toast" id="prodToast"></div>';
    document.body.appendChild(root);
    el("prodBack").onclick = closeProd;
    el("prodLoadBg").onclick = function () { el("prodLoading").hidden = true; renderView(); };
    S.built = true;
  }

  /* ===== 打开 / 关闭 ===== */
  function openProd() {
    buildRoot();
    el("prodRoot").hidden = false;
    document.body.style.overflow = "hidden";   // 背后的主页别跟着滚
    refreshProjects(true);
  }
  function closeProd() {
    if (anyDirty() && !confirm("有还没保存的修改，确定离开？（修改只丢这次的，已保存的都在）")) return;
    el("prodRoot").hidden = true;
    document.body.style.overflow = "";
  }
  function anyDirty() { return S.dirty.scenes || S.dirty.budget || S.editingAnalysis; }

  /* ===== 数据拉取 ===== */
  function refreshProjects(autoOpen) {
    api("/api/production/projects").then(function (j) {
      S.projects = (j && j.projects) || [];
      if (autoOpen && S.cur) {
        // 重新打开时若之前在看某项目，刷新它
        loadProject(S.cur.id, true);
      } else {
        S.view = "home"; renderView();
      }
    }).catch(function () {
      S.projects = [];
      S.view = "home"; renderView();
      toast("没连上后端——制作板块需要后端在线（AI 拆解都在服务器上跑）", true);
    });
  }
  function loadProject(id, keepTab) {
    api("/api/production/project?id=" + encodeURIComponent(id)).then(function (j) {
      if (!j.ok) { toast(j.error || "项目打开失败", true); S.view = "home"; renderView(); return; }
      S.cur = j.project;
      if (!keepTab) S.tab = pickDefaultTab(j.project);
      S.view = "project"; S.editingAnalysis = false; S.editBuf = null;
      S.dirty = { scenes: false, budget: false };
      renderView();
    }).catch(function () { toast("网络出错，稍后再试", true); });
  }
  function pickDefaultTab(p) {
    if (!p.analysis && !p.scenes && !p.budget) return "analysis";
    if (p.analysis) return "analysis";
    if (p.scenes) return "scenes";
    return "budget";
  }

  /* ===== 视图分发 ===== */
  function renderView() {
    var tr = el("prodTopRight");
    tr.innerHTML = token() ? "已登录 · 项目存云端" : "未登录 · 项目绑定本设备（登录后自动归入账号）";
    if (S.view === "project" && S.cur) renderProject();
    else renderHome();
  }

  /* ===================================================== 首页（项目列表） */
  function renderHome() {
    var v = el("prodView"), h = "";
    h += '<div class="prod-head"><div><h1>制片统筹</h1>'
      + '<p class="prod-sub">上传剧本 → 解剖分析 → 分场/顺场表 → 参考预算。AI 出初稿，每一格都能改——最终以你为准。</p></div></div>';
    if (!S.projects.length) {
      h += '<div class="prod-hero"><h2>把剧本交给制片帽</h2>'
        + '<p>支持 .txt / .docx / .fdx（Final Draft）。上传后它会通读全本：</p>'
        + '<div class="prod-feats">'
        + '<div class="prod-feat"><span>🔬</span><b>解剖分析</b><i>梗概·结构·人物小传·亮点风险</i></div>'
        + '<div class="prod-feat"><span>🎬</span><b>分场 / 顺场表</b><i>内外日夜·演员·道具·特殊需求</i></div>'
        + '<div class="prod-feat"><span>💰</span><b>参考预算</b><i>按中国市场行情估科目区间</i></div>'
        + '</div>'
        + uploadCardHTML()
        + '</div>';
    } else {
      h += '<div class="prod-grid">';
      h += uploadCardHTML();
      S.projects.forEach(function (p) {
        h += '<div class="prod-proj-card" data-id="' + p.id + '">'
          + '<div class="prod-proj-title">' + esc(p.title || "未命名") + '</div>'
          + '<div class="prod-proj-meta">' + esc(p.scriptName || "") + ' · ' + fmtWan(p.words) + ' · 更新 ' + fmtDate(p.updated) + '</div>'
          + '<div class="prod-proj-chips">'
          + '<span class="prod-chip' + (p.has.analysis ? " done" : "") + '">' + (p.has.analysis ? "✓ " : "") + '解剖</span>'
          + '<span class="prod-chip' + (p.has.scenes ? " done" : "") + '">' + (p.has.scenes ? "✓ " : "") + '场景表</span>'
          + '<span class="prod-chip' + (p.has.budget ? " done" : "") + '">' + (p.has.budget ? "✓ " : "") + '预算</span>'
          + '</div></div>';
      });
      h += '</div>';
    }
    v.innerHTML = h;
    bindUpload();
    [].forEach.call(v.querySelectorAll(".prod-proj-card"), function (c) {
      c.onclick = function () { loadProject(c.getAttribute("data-id")); };
    });
  }

  function uploadCardHTML() {
    return '<div class="prod-upload-card" id="prodUpload">'
      + '<span class="big">📄</span><b>上传剧本，新建项目</b>'
      + '<i>点击选择文件，或把文件拖进来<br>.txt / .docx / .fdx · 50 万字以内（长剧建议按集传）</i>'
      + '<input type="file" id="prodFile" accept=".txt,.md,.docx,.fdx" style="display:none" />'
      + '</div>';
  }

  function bindUpload() {
    var zone = el("prodUpload"), input = el("prodFile");
    if (!zone) return;
    zone.onclick = function () { input.click(); };
    input.onchange = function () { if (input.files && input.files[0]) handleFile(input.files[0]); input.value = ""; };
    zone.ondragover = function (e) { e.preventDefault(); zone.classList.add("drag"); };
    zone.ondragleave = function () { zone.classList.remove("drag"); };
    zone.ondrop = function (e) {
      e.preventDefault(); zone.classList.remove("drag");
      if (e.dataTransfer.files && e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    };
  }

  function abToB64(buf) {
    var u8 = new Uint8Array(buf), s = "", CH = 8192;
    for (var i = 0; i < u8.length; i += CH) s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
    return btoa(s);
  }

  function handleFile(file) {
    if (file.size > 20 * 1024 * 1024) return toast("文件超过 20MB——剧本不该这么大，检查一下文件", true);
    var title = prompt("给这个项目起个名（默认用文件名）：", file.name.replace(/\.(txt|docx|fdx|md)$/i, ""));
    if (title === null) return;   // 用户取消
    var reader = new FileReader();
    toast("正在上传解析…");
    reader.onload = function () {
      api("/api/production/upload", {
        method: "POST",
        body: { filename: file.name, title: (title || "").trim(), fileB64: abToB64(reader.result) }
      }).then(function (j) {
        if (!j.ok) return toast(j.error || "上传失败", true);
        toast("✓ 剧本已就位（" + fmtWan(j.project.words) + (j.project.sceneHeads ? "，识别到约 " + j.project.sceneHeads + " 个场头" : "") + "）");
        refreshProjectsThenOpen(j.project.id);
      }).catch(function () { toast("上传失败：没连上后端", true); });
    };
    reader.readAsArrayBuffer(file);
  }
  function refreshProjectsThenOpen(id) {
    api("/api/production/projects").then(function (j) {
      S.projects = (j && j.projects) || [];
      loadProject(id);
    });
  }

  /* ===================================================== 项目页 */
  function renderProject() {
    var p = S.cur, v = el("prodView");
    var h = '<div class="prod-proj-head">'
      + '<input class="prod-title-input" id="prodTitle" value="' + esc(p.title || "") + '" title="点击改名" />'
      + '<span class="prod-proj-info">' + esc(p.scriptName || "") + ' · ' + fmtWan(p.words) + '</span>'
      + '<div class="prod-proj-actions">'
      + '<button class="prod-btn minor" id="prodHomeBtn">📁 全部项目</button>'
      + '<button class="prod-btn danger" id="prodDelBtn">删除项目</button>'
      + '</div></div>';
    h += '<div class="prod-tabs">'
      + tabBtn("analysis", "🔬 剧本解剖", !!p.analysis)
      + tabBtn("scenes", "🎬 分场 · 顺场表", !!p.scenes)
      + tabBtn("budget", "💰 参考预算", !!p.budget)
      + '</div>'
      + '<div id="prodTabBody"></div>';
    v.innerHTML = h;
    el("prodHomeBtn").onclick = function () {
      if (anyDirty() && !confirm("有还没保存的修改，确定离开？")) return;
      S.editingAnalysis = false; S.editBuf = null; S.dirty = { scenes: false, budget: false };
      S.cur = null; refreshProjects();
    };
    el("prodDelBtn").onclick = function () {
      if (!confirm("删除项目《" + (p.title || "未命名") + "》？剧本和所有拆解结果都会删掉，不可恢复。")) return;
      api("/api/production/delete", { method: "POST", body: { id: p.id } }).then(function (j) {
        if (j.ok) { toast("已删除"); S.cur = null; refreshProjects(); }
        else toast(j.error || "删除失败", true);
      });
    };
    var ti = el("prodTitle");
    ti.onchange = function () {
      var t = ti.value.trim();
      if (!t) { ti.value = p.title; return; }
      api("/api/production/save", { method: "POST", body: { id: p.id, field: "title", data: t } }).then(function (j) {
        if (j.ok) { p.title = t; toast("✓ 已改名"); } else toast(j.error || "改名失败", true);
      });
    };
    [].forEach.call(v.querySelectorAll(".prod-tab"), function (b) {
      b.onclick = function () {
        var t = b.getAttribute("data-tab");
        if (t === S.tab) return;
        if (S.editingAnalysis && !confirm("解剖报告还在编辑中，离开会丢掉这次没保存的修改。继续？")) return;
        S.editingAnalysis = false; S.editBuf = null;
        S.tab = t; renderProject();
      };
    });
    renderTabBody();
  }
  function tabBtn(key, label, done) {
    return '<button class="prod-tab' + (S.tab === key ? " active" : "") + '" data-tab="' + key + '">'
      + label + (done ? '<span class="dot" title="已生成"></span>' : "") + '</button>';
  }

  function renderTabBody() {
    var box = el("prodTabBody");
    if (!box) return;
    if (S.tab === "analysis") renderAnalysis(box);
    else if (S.tab === "scenes") renderScenes(box);
    else renderBudget(box);
  }

  function runningStrip(kind) {
    if (!S.job || S.job.kind !== kind) return "";
    return '<div class="prod-running-strip"><span class="spin"></span>'
      + '<span>制片帽正在后台干活（' + jobName(kind) + '）——可以先看别的 tab，完成会自动刷新。</span></div>';
  }
  function jobName(k) { return { analysis: "解剖分析", scenes: "拆分场表", budget: "编制预算" }[k] || k; }

  /* ===================================================== ① 剧本解剖 */
  function renderAnalysis(box) {
    var p = S.cur, a = p.analysis;
    if (S.editingAnalysis) return renderAnalysisEdit(box);
    if (!a) {
      box.innerHTML = runningStrip("analysis")
        + '<div class="prod-panel"><div class="prod-empty"><span class="big">🔬</span>'
        + '<h3>剧本解剖分析</h3>'
        + '<p>制片帽会通读全本（' + fmtWan(p.words) + '），从制片视角出报告：一句话故事、梗概、主题、结构节拍、'
        + '人物小传与选角建议、制作亮点、过审与制作风险、体量评估、总评。<br>用的是思考型模型，约需 1–3 分钟。</p>'
        + (S.job ? "" : '<button class="prod-btn" id="prodRunAnalysis">开始解剖 →</button>')
        + '</div></div>';
      bindRun("prodRunAnalysis", "analysis");
      return;
    }
    var h = runningStrip("analysis");
    h += '<div class="prod-toolbar">'
      + '<button class="prod-btn minor" id="prodEditAna">✏️ 编辑报告</button>'
      + '<button class="prod-btn minor" id="prodRedoAna">↻ 重新生成</button>'
      + '<span class="hint">生成于 ' + fmtDate(a.generatedAt) + ' · 每一段都可编辑，以你改后的为准</span></div>';
    h += '<div class="prod-panel prod-report">';
    if (a.sampleNote) h += '<div class="prod-note-strip">⚠ ' + esc(a.sampleNote) + '</div>';
    h += '<div class="prod-logline">「' + esc(a.logline || "") + '」</div>'
      + '<div class="prod-facts">'
      + factHTML("类型", a.genre) + factHTML("调性", a.tone) + factHTML("主题内核", a.theme)
      + '</div>'
      + secHTML("故事梗概", "<p>" + esc(a.synopsis) + "</p>");
    if (a.structure && a.structure.length) {
      var rows = a.structure.map(function (s) {
        return "<tr><td>" + esc(s.part) + "</td><td>" + esc(s.range || "—") + "</td><td>" + esc(s.desc) + "</td></tr>";
      }).join("");
      h += secHTML("结构拆解", '<table class="prod-struct"><tr><th>段落</th><th>范围</th><th>功能与内容</th></tr>' + rows + "</table>");
    }
    if (a.characters && a.characters.length) {
      var cards = a.characters.map(function (c) {
        return '<div class="prod-char"><b>' + esc(c.name) + '</b><span class="role">' + esc(c.role || "") + '</span>'
          + (c.age ? ' <span style="font-size:11px;color:var(--ink-faint)">' + esc(c.age) + '</span>' : "")
          + '<div>' + esc(c.desc || "") + '</div>'
          + (c.arc ? '<div style="margin-top:5px;color:var(--ink-soft)">弧光：' + esc(c.arc) + "</div>" : "")
          + (c.castingNote ? '<div class="cast">选角参考：<em>' + esc(c.castingNote) + "</em></div>" : "")
          + '</div>';
      }).join("");
      h += secHTML("人物（" + a.characters.length + "）", '<div class="prod-chars">' + cards + "</div>");
    }
    if (a.highlights && a.highlights.length)
      h += secHTML("制作 / 市场亮点", '<ul class="prod-list">' + a.highlights.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>");
    if (a.risks && a.risks.length)
      h += secHTML("风险提示", '<ul class="prod-list">' + a.risks.map(function (r) {
        return '<li><span class="prod-risk-type">' + esc(r.type || "风险") + "</span>" + esc(r.desc) + "</li>";
      }).join("") + "</ul>");
    if (a.pacing) h += secHTML("节奏与体量", "<p>" + esc(a.pacing) + "</p>");
    if (a.verdict) h += secHTML("制片帽总评", '<div class="prod-verdict"><p>' + esc(a.verdict) + "</p></div>");
    h += "</div>";
    box.innerHTML = h;
    var eb = el("prodEditAna");
    if (eb) eb.onclick = function () { S.editingAnalysis = true; S.editBuf = deepCopy(a); renderTabBody(); };
    var rb = el("prodRedoAna");
    if (rb) rb.onclick = function () {
      if (confirm("重新生成会覆盖当前报告（包括你的手改）。继续？")) startJob("analysis");
    };
  }
  function factHTML(k, v) { return '<div class="prod-fact"><span class="k">' + k + '</span><span class="v">' + esc(v || "—") + "</span></div>"; }
  function secHTML(t, inner) { return '<div class="prod-sec"><h4>' + t + "</h4>" + inner + "</div>"; }

  /* ---- 解剖报告：编辑模式（写入 editBuf，保存才落库） ---- */
  function renderAnalysisEdit(box) {
    var a = S.editBuf;
    function ta(path, val, rows) {
      return '<textarea class="prod-edit-field" data-apath="' + path + '" rows="' + (rows || 2) + '">' + esc(val || "") + "</textarea>";
    }
    var h = '<div class="prod-toolbar">'
      + '<button class="prod-btn save-hot" id="prodSaveAna">💾 保存报告</button>'
      + '<button class="prod-btn minor" id="prodCancelAna">放弃修改</button>'
      + '<span class="hint">改完记得保存；列表项可整行删、可新增</span></div>';
    h += '<div class="prod-panel prod-report">';
    h += secHTML("一句话故事", ta("logline", a.logline, 2));
    h += secHTML("故事梗概", ta("synopsis", a.synopsis, 7));
    h += '<div class="prod-sec"><h4>类型 / 调性 / 主题</h4><div class="prod-edit-row">'
      + ta("genre", a.genre, 2) + ta("tone", a.tone, 2) + ta("theme", a.theme, 2) + "</div></div>";
    h += '<div class="prod-sec"><h4>结构拆解</h4>';
    (a.structure = a.structure || []).forEach(function (s, i) {
      h += '<div class="prod-edit-row">'
        + '<textarea class="prod-edit-field" style="flex:0 0 130px" data-apath="structure.' + i + '.part" rows="2">' + esc(s.part || "") + '</textarea>'
        + '<textarea class="prod-edit-field" style="flex:0 0 130px" data-apath="structure.' + i + '.range" rows="2">' + esc(s.range || "") + '</textarea>'
        + '<textarea class="prod-edit-field" data-apath="structure.' + i + '.desc" rows="2">' + esc(s.desc || "") + '</textarea>'
        + delBtn("structure", i) + "</div>";
    });
    h += addBtn("structure", "+ 加一段") + "</div>";
    h += '<div class="prod-sec"><h4>人物</h4>';
    (a.characters = a.characters || []).forEach(function (c, i) {
      h += '<div class="prod-edit-row" style="flex-wrap:wrap;border:1px dashed var(--line);border-radius:10px;padding:10px">'
        + '<textarea class="prod-edit-field" style="flex:0 0 110px" data-apath="characters.' + i + '.name" rows="1" placeholder="姓名">' + esc(c.name || "") + '</textarea>'
        + '<textarea class="prod-edit-field" style="flex:0 0 90px" data-apath="characters.' + i + '.role" rows="1" placeholder="角色">' + esc(c.role || "") + '</textarea>'
        + '<textarea class="prod-edit-field" style="flex:0 0 80px" data-apath="characters.' + i + '.age" rows="1" placeholder="年龄">' + esc(c.age || "") + '</textarea>'
        + delBtn("characters", i)
        + '<textarea class="prod-edit-field" style="flex:1 1 100%" data-apath="characters.' + i + '.desc" rows="2" placeholder="人物小传">' + esc(c.desc || "") + '</textarea>'
        + '<textarea class="prod-edit-field" style="flex:1 1 46%" data-apath="characters.' + i + '.arc" rows="2" placeholder="人物弧光">' + esc(c.arc || "") + '</textarea>'
        + '<textarea class="prod-edit-field" style="flex:1 1 46%" data-apath="characters.' + i + '.castingNote" rows="2" placeholder="选角建议">' + esc(c.castingNote || "") + '</textarea>'
        + "</div>";
    });
    h += addBtn("characters", "+ 加一个人物") + "</div>";
    h += '<div class="prod-sec"><h4>亮点（每行一条）</h4>';
    (a.highlights = a.highlights || []).forEach(function (x, i) {
      h += '<div class="prod-edit-row">' + ta("highlights." + i, x, 2) + delBtn("highlights", i) + "</div>";
    });
    h += addBtn("highlights", "+ 加一条亮点") + "</div>";
    h += '<div class="prod-sec"><h4>风险（类型 + 说明）</h4>';
    (a.risks = a.risks || []).forEach(function (r, i) {
      h += '<div class="prod-edit-row">'
        + '<textarea class="prod-edit-field" style="flex:0 0 110px" data-apath="risks.' + i + '.type" rows="2">' + esc(r.type || "") + '</textarea>'
        + '<textarea class="prod-edit-field" data-apath="risks.' + i + '.desc" rows="2">' + esc(r.desc || "") + '</textarea>'
        + delBtn("risks", i) + "</div>";
    });
    h += addBtn("risks", "+ 加一条风险") + "</div>";
    h += secHTML("节奏与体量", ta("pacing", a.pacing, 3));
    h += secHTML("制片帽总评", ta("verdict", a.verdict, 4));
    h += "</div>";
    box.innerHTML = h;

    [].forEach.call(box.querySelectorAll("[data-apath]"), function (f) {
      f.oninput = function () { setPath(S.editBuf, f.getAttribute("data-apath"), f.value); };
    });
    [].forEach.call(box.querySelectorAll("[data-arrdel]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-arrdel"), i = parseInt(b.getAttribute("data-i"), 10);
        S.editBuf[k].splice(i, 1); renderTabBody();
      };
    });
    [].forEach.call(box.querySelectorAll("[data-arradd]"), function (b) {
      b.onclick = function () {
        var k = b.getAttribute("data-arradd");
        var tpl = { structure: { part: "", range: "", desc: "" },
                    characters: { name: "", role: "", age: "", desc: "", arc: "", castingNote: "" },
                    highlights: "", risks: { type: "", desc: "" } }[k];
        (S.editBuf[k] = S.editBuf[k] || []).push(typeof tpl === "string" ? "" : deepCopy(tpl));
        renderTabBody();
      };
    });
    el("prodSaveAna").onclick = function () {
      saveField("analysis", S.editBuf, function () {
        S.cur.analysis = deepCopy(S.editBuf);
        S.editingAnalysis = false; S.editBuf = null;
        toast("✓ 报告已保存"); renderTabBody();
      });
    };
    el("prodCancelAna").onclick = function () {
      if (!confirm("放弃这次的修改？")) return;
      S.editingAnalysis = false; S.editBuf = null; renderTabBody();
    };
  }
  function delBtn(key, i) { return '<button class="prod-row-del" data-arrdel="' + key + '" data-i="' + i + '" title="删除本行">×</button>'; }
  function addBtn(key, label) { return '<button class="prod-row-add" data-arradd="' + key + '">' + label + "</button>"; }
  function setPath(obj, path, val) {
    var ks = path.split("."), o = obj;
    for (var i = 0; i < ks.length - 1; i++) o = o[isNaN(ks[i]) ? ks[i] : +ks[i]];
    o[ks[ks.length - 1]] = val;
  }

  /* ===================================================== ② 分场 / 顺场表 */
  var SCENE_COLS = [
    { k: "no", label: "场号", cls: "c-no" },
    { k: "ep", label: "集" },
    { k: "intExt", label: "内/外", cls: "c-ie" },
    { k: "dayNight", label: "日/夜", cls: "c-dn" },
    { k: "location", label: "场景", cls: "c-loc" },
    { k: "pages", label: "页数" },
    { k: "characters", label: "出场人物", arr: 1, cls: "c-chars" },
    { k: "extras", label: "群演/特约" },
    { k: "props", label: "关键道具", arr: 1 },
    { k: "costume", label: "服化" },
    { k: "special", label: "特殊需求", arr: 1, cls: "c-special" },
    { k: "summary", label: "剧情简述", cls: "c-sum" }
  ];

  function renderScenes(box) {
    var p = S.cur, sd = p.scenes;
    if (!sd || !sd.scenes || !sd.scenes.length) {
      var estBatches = Math.max(1, Math.ceil(p.words / 4500));
      box.innerHTML = runningStrip("scenes")
        + '<div class="prod-panel"><div class="prod-empty"><span class="big">🎬</span>'
        + '<h3>分场表 · 顺场表</h3>'
        + '<p>制片帽把全剧本逐场拆开：场号、内/外、日/夜、场景、出场人物、群特、关键道具、服化、'
        + '特殊拍摄需求（雨戏/车戏/特效…）、页数、剧情简述。<br>'
        + '拆完即得 <b>分场表</b>（按剧本顺序）和 <b>顺场表</b>（按场景归组，转场一目了然）。<br>'
        + '本剧本约 ' + fmtWan(p.words) + '，预计分 ' + estBatches + ' 批拆解、约 ' + estMinutes(estBatches) + '。</p>'
        + (S.job ? "" : '<button class="prod-btn" id="prodRunScenes">开始拆分场表 →</button>')
        + '</div></div>';
      bindRun("prodRunScenes", "scenes");
      return;
    }
    var sc = sd.scenes;
    var h = runningStrip("scenes");
    h += '<div class="prod-toolbar">'
      + '<div class="prod-subtabs">'
      + '<button class="prod-subtab' + (S.subTab === "order" ? " active" : "") + '" data-st="order">分场表（剧本顺序）</button>'
      + '<button class="prod-subtab' + (S.subTab === "set" ? " active" : "") + '" data-st="set">顺场表（按场景归组）</button>'
      + '</div>'
      + (S.dirty.scenes ? '<span class="prod-dirty-flag">● 有未保存的修改</span>' : "")
      + '<button class="prod-btn save-hot" id="prodSaveScenes"' + (S.dirty.scenes ? "" : " disabled") + '>💾 保存</button>'
      + '<button class="prod-btn minor" id="prodCsvScenes">⬇ 导出 CSV</button>'
      + '<button class="prod-btn minor" id="prodRedoScenes">↻ 重新拆</button>'
      + '<span class="hint">单元格直接点开改 · 人物/道具用「、」分隔</span></div>';
    h += statsStripHTML(sc);
    if (sd.truncatedNote) h += '<div class="prod-note-strip">⚠ ' + esc(sd.truncatedNote) + "</div>";
    if (sd.mode === "blocks") h += '<div class="prod-note-strip">ℹ️ 这个剧本没有标准场头（可能是文学本），场次是制片帽按地点/时间自行划分的，建议过一遍核对。</div>';
    if (S.subTab === "order") h += orderTableHTML(sc);
    else h += setTableHTML(sc);
    box.innerHTML = h;

    [].forEach.call(box.querySelectorAll(".prod-subtab"), function (b) {
      b.onclick = function () { S.subTab = b.getAttribute("data-st"); renderTabBody(); };
    });
    el("prodSaveScenes").onclick = function () {
      saveField("scenes", S.cur.scenes, function () { S.dirty.scenes = false; toast("✓ 场景表已保存"); renderTabBody(); });
    };
    el("prodCsvScenes").onclick = function () { exportScenesCSV(); };
    el("prodRedoScenes").onclick = function () {
      if (confirm("重新拆会覆盖当前表（包括你的手改）。继续？")) startJob("scenes");
    };
    if (S.subTab === "order") bindSceneTable(box);
  }
  function estMinutes(batches) {
    var mins = Math.max(1, Math.round(batches * 20 / 60));
    return mins <= 1 ? "1 分钟上下" : ("约 " + mins + " 分钟");
  }

  function statsStripHTML(sc) {
    var locs = {}, chars = {}, night = 0, ext = 0, pages = 0;
    sc.forEach(function (s) {
      if (s.location) locs[s.location.trim()] = 1;
      (s.characters || []).forEach(function (c) { chars[c] = 1; });
      if ((s.dayNight || "").indexOf("夜") > -1) night++;
      if ((s.intExt || "").indexOf("外") > -1) ext++;
      pages += num(s.pages);
    });
    return '<div class="prod-stats-strip">'
      + '<span class="prod-stat">共 <b>' + sc.length + '</b> 场</span>'
      + '<span class="prod-stat">场景 <b>' + Object.keys(locs).length + '</b> 处</span>'
      + '<span class="prod-stat">人物 <b>' + Object.keys(chars).length + '</b> 人</span>'
      + '<span class="prod-stat">夜戏 <b>' + night + '</b> 场</span>'
      + '<span class="prod-stat">外景 <b>' + ext + '</b> 场</span>'
      + '<span class="prod-stat">约 <b>' + pages.toFixed(1) + '</b> 页（≈分钟）</span>'
      + '</div>';
  }

  function cellText(s, col) {
    var v = s[col.k];
    if (col.arr) return (v || []).join("、");
    if (v == null) return "";
    return String(v);
  }
  function orderTableHTML(sc) {
    var head = SCENE_COLS.map(function (c) { return "<th>" + c.label + "</th>"; }).join("") + "<th></th>";
    var rows = sc.map(function (s, i) {
      var tds = SCENE_COLS.map(function (c) {
        var extra = "";
        if (c.k === "dayNight") {
          var dn = String(s.dayNight || "");
          extra = dn.indexOf("夜") > -1 ? ' class="c-dn"' : "";
        }
        return '<td class="' + (c.cls || "") + '" contenteditable="true" data-i="' + i + '" data-k="' + c.k + '">'
          + esc(cellText(s, c)) + "</td>";
      }).join("");
      return '<tr>' + tds + '<td class="c-del"><button data-del="' + i + '" title="删除本场">×</button></td></tr>';
    }).join("");
    return '<div class="prod-table-scroll"><table class="prod-scenes"><tr>' + head + "</tr>" + rows + "</table></div>"
      + '<div style="margin-top:12px"><button class="prod-row-add" id="prodAddScene">+ 添加一场</button></div>';
  }
  function bindSceneTable(box) {
    [].forEach.call(box.querySelectorAll("td[contenteditable]"), function (td) {
      td.onblur = function () {
        var i = +td.getAttribute("data-i"), k = td.getAttribute("data-k");
        var col = SCENE_COLS.filter(function (c) { return c.k === k; })[0];
        var v = td.textContent.trim();
        var s = S.cur.scenes.scenes[i];
        var nv = col.arr ? splitArr(v) : (k === "pages" ? num(v) : (k === "ep" ? (v === "" ? null : num(v)) : v));
        if (JSON.stringify(s[k] == null ? (col.arr ? [] : "") : s[k]) !== JSON.stringify(nv)) {
          s[k] = nv;
          if (!S.dirty.scenes) { S.dirty.scenes = true; refreshScenesToolbar(); }
        }
      };
      td.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); td.blur(); } };
    });
    [].forEach.call(box.querySelectorAll("[data-del]"), function (b) {
      b.onclick = function () {
        var i = +b.getAttribute("data-del");
        var s = S.cur.scenes.scenes[i];
        if (!confirm("删除第 " + (s.no || i + 1) + " 场（" + (s.location || "") + "）？")) return;
        S.cur.scenes.scenes.splice(i, 1);
        S.dirty.scenes = true; renderTabBody();
      };
    });
    var ab = el("prodAddScene");
    if (ab) ab.onclick = function () {
      S.cur.scenes.scenes.push({ no: String(S.cur.scenes.scenes.length + 1), ep: null, intExt: "内", dayNight: "日",
        location: "", summary: "", characters: [], extras: "", props: [], costume: "", special: [], pages: 0.5 });
      S.dirty.scenes = true; renderTabBody();
      var scroll = box.querySelector(".prod-table-scroll");
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    };
  }
  function refreshScenesToolbar() { renderTabBody(); }

  /* 顺场表：分场表按"场景地点"归组的视图——地点相同的戏排一起（剧组转场逻辑），实时从分场表推导 */
  function setTableHTML(sc) {
    var groups = [], byLoc = {};
    sc.forEach(function (s, i) {
      var key = (s.location || "").trim() || "（未填场景）";
      if (!byLoc[key]) { byLoc[key] = { loc: key, list: [], firstIdx: i }; groups.push(byLoc[key]); }
      byLoc[key].list.push(s);
    });
    var h = '<div class="prod-note-strip" style="margin-bottom:14px">ℹ️ 顺场表由分场表自动归组（同场景的戏排到一起，省转场）。要改内容请在「分场表」里改，这里会跟着变。</div>';
    groups.forEach(function (g) {
      var day = 0, night = 0, pages = 0, cast = {};
      g.list.forEach(function (s) {
        if ((s.dayNight || "").indexOf("夜") > -1) night++; else day++;
        pages += num(s.pages);
        (s.characters || []).forEach(function (c) { cast[c] = 1; });
      });
      var castArr = Object.keys(cast);
      var castShow = castArr.slice(0, 8).join("、") + (castArr.length > 8 ? " 等 " + castArr.length + " 人" : "");
      h += '<div class="prod-set-group"><div class="prod-set-head">'
        + "<b>" + esc(g.loc) + "</b>"
        + '<span class="n">' + g.list.length + " 场 · 日 " + day + " / 夜 " + night + " · 约 " + pages.toFixed(1) + " 页</span>"
        + '<span class="cast">' + esc(castShow) + "</span></div><table>";
      g.list.forEach(function (s) {
        var dn = String(s.dayNight || "");
        h += "<tr>"
          + '<td style="white-space:nowrap;font-weight:700;color:var(--accent-deep)">' + esc(s.no || "") + "</td>"
          + '<td style="white-space:nowrap">' + esc(s.intExt || "") + ' <span class="' + (dn.indexOf("夜") > -1 ? "prod-tag-night" : "prod-tag-day") + '">' + esc(dn || "—") + "</span></td>"
          + "<td>" + esc(s.summary || "") + "</td>"
          + '<td style="color:var(--ink-soft)">' + esc((s.characters || []).join("、")) + "</td>"
          + '<td style="color:#9a6253">' + esc((s.special || []).join("、")) + "</td>"
          + "</tr>";
      });
      h += "</table></div>";
    });
    return h;
  }

  /* CSV 导出（Excel 直接能开：BOM + CRLF） */
  function csvCell(v) {
    v = String(v == null ? "" : v);
    if (/[",\n]/.test(v)) v = '"' + v.replace(/"/g, '""') + '"';
    return v;
  }
  function downloadCSV(filename, lines) {
    var blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(a.href); }, 300);
  }
  function exportScenesCSV() {
    var sc = S.cur.scenes.scenes;
    var lines = [SCENE_COLS.map(function (c) { return csvCell(c.label); }).join(",")];
    sc.forEach(function (s) {
      lines.push(SCENE_COLS.map(function (c) { return csvCell(cellText(s, c)); }).join(","));
    });
    if (S.subTab === "set") {
      // 顺场表导出：按场景分组重排
      lines = [["场景", "场号", "内/外", "日/夜", "剧情简述", "出场人物", "特殊需求", "页数"].map(csvCell).join(",")];
      var byLoc = {}, order = [];
      sc.forEach(function (s) {
        var key = (s.location || "").trim() || "（未填场景）";
        if (!byLoc[key]) { byLoc[key] = []; order.push(key); }
        byLoc[key].push(s);
      });
      order.forEach(function (loc) {
        byLoc[loc].forEach(function (s) {
          lines.push([loc, s.no, s.intExt, s.dayNight, s.summary, (s.characters || []).join("、"), (s.special || []).join("、"), s.pages].map(csvCell).join(","));
        });
      });
    }
    downloadCSV((S.cur.title || "剧本") + (S.subTab === "set" ? "-顺场表" : "-分场表") + ".csv", lines);
    toast("✓ 已导出 CSV（Excel 可直接打开）");
  }

  /* ===================================================== ③ 参考预算 */
  function renderBudget(box) {
    var p = S.cur, b = p.budget;
    var opts = (b && b.options) || {};
    var formHTML = '<div class="prod-panel"><div class="prod-budget-form">'
      + field("形态", '<select id="bdType">' + ["院线电影", "网络电影", "电视剧", "网络剧", "微短剧"].map(function (t) {
        return '<option' + (opts.type === t ? " selected" : "") + ">" + t + "</option>";
      }).join("") + "</select>")
      + field("制作级别", '<select id="bdLevel">' + ["S（头部平台/卫视）", "A（主流水准）", "B（中小成本）"].map(function (t) {
        return '<option' + (opts.level === t ? " selected" : "") + ">" + t + "</option>";
      }).join("") + "</select>")
      + field("集数（电影填 1）", '<input type="number" id="bdEps" min="1" max="100" value="' + (opts.episodes || 1) + '" />')
      + '<button class="prod-btn" id="prodRunBudget" style="margin-left:auto"' + (S.job ? " disabled" : "") + '>'
      + (b ? "↻ 重新估" : "生成参考预算 →") + '</button>'
      + '<div class="prod-field prod-budget-note"><span>补充要求（可选：比如"主演用两位一线"、"全棚拍"）</span>'
      + '<textarea class="prod-edit-field" id="bdNote" rows="2">' + esc(opts.note || "") + "</textarea></div>"
      + "</div></div>";

    if (!b) {
      box.innerHTML = runningStrip("budget") + formHTML
        + '<div class="prod-panel" style="margin-top:18px"><div class="prod-empty"><span class="big">💰</span>'
        + '<h3>参考预算</h3>'
        + '<p>按中国市场近年的真实行情，结合分场表统计（场数、内外景比、夜戏、特殊需求）'
        + '估算各科目区间。' + (p.scenes ? "" : "<br><b>建议先拆好分场表再估</b>——有真实场次数据，预算才有依据；不拆也能按字数粗估。")
        + '<br>生成后每一行金额都能改，合计自动重算。</p>'
        + '</div></div>';
      bindBudgetRun();
      return;
    }
    var h = runningStrip("budget") + formHTML;
    h += '<div class="prod-toolbar" style="margin-top:18px">'
      + (S.dirty.budget ? '<span class="prod-dirty-flag">● 有未保存的修改</span>' : "")
      + '<button class="prod-btn save-hot" id="prodSaveBudget"' + (S.dirty.budget ? "" : " disabled") + '>💾 保存</button>'
      + '<button class="prod-btn minor" id="prodCsvBudget">⬇ 导出 CSV</button>'
      + '<span class="hint">生成于 ' + fmtDate(b.generatedAt) + " · 金额单位：万元 · 区间为 低–高 估</span></div>";
    h += '<div id="prodBudgetTotal"></div>';
    // 按 group 分组渲染
    var groups = [], byG = {};
    (b.items = b.items || []).forEach(function (it, i) {
      var g = it.group || "其他";
      if (!byG[g]) { byG[g] = []; groups.push(g); }
      byG[g].push(i);
    });
    groups.forEach(function (g) {
      h += '<div class="prod-budget-group"><h5>' + esc(g) + '<span class="gsum" data-gsum="' + esc(g) + '"></span></h5>'
        + '<table class="prod-budget"><tr><th style="width:26%">科目</th><th style="width:90px">低（万）</th><th style="width:90px">高（万）</th><th>说明</th><th></th></tr>';
      byG[g].forEach(function (i) {
        var it = b.items[i];
        h += '<tr>'
          + '<td><input type="text" data-bi="' + i + '" data-bk="name" value="' + esc(it.name || "") + '" /></td>'
          + '<td class="c-amt"><input type="number" data-bi="' + i + '" data-bk="low" value="' + num(it.low) + '" min="0" /></td>'
          + '<td class="c-amt"><input type="number" data-bi="' + i + '" data-bk="high" value="' + num(it.high) + '" min="0" /></td>'
          + '<td><input type="text" data-bi="' + i + '" data-bk="note" value="' + esc(it.note || "") + '" /></td>'
          + '<td class="c-del"><button data-bdel="' + i + '" title="删除">×</button></td></tr>';
      });
      h += '</table><div style="margin-top:6px"><button class="prod-row-add" data-baddgroup="' + esc(g) + '">+ ' + esc(g) + ' 加一行</button></div></div>';
    });
    if (b.assumptions && b.assumptions.length)
      h += '<div class="prod-panel"><div class="prod-sec" style="margin-top:0"><h4>前提假设</h4><ul class="prod-list">'
        + b.assumptions.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul></div>"
        + (b.marketNote ? '<div class="prod-sec"><h4>市场行情参考</h4><p style="font-size:13.5px;line-height:1.8">' + esc(b.marketNote) + "</p></div>" : "")
        + '<div class="prod-note-strip" style="margin-top:16px">⚠ ' + esc(b.disclaimer || "以上为 AI 参考估算，实际以供应商询价与谈判为准。") + "</div></div>";
    box.innerHTML = h;
    bindBudgetRun();
    recalcBudget();
    [].forEach.call(box.querySelectorAll("[data-bi]"), function (inp) {
      inp.oninput = function () {
        var i = +inp.getAttribute("data-bi"), k = inp.getAttribute("data-bk");
        b.items[i][k] = (k === "low" || k === "high") ? num(inp.value) : inp.value;
        if (!S.dirty.budget) { S.dirty.budget = true; softRefreshBudgetToolbar(); }
        recalcBudget();
      };
    });
    [].forEach.call(box.querySelectorAll("[data-bdel]"), function (btn) {
      btn.onclick = function () {
        var i = +btn.getAttribute("data-bdel");
        if (!confirm("删除「" + (b.items[i].name || "此行") + "」？")) return;
        b.items.splice(i, 1); S.dirty.budget = true; renderTabBody();
      };
    });
    [].forEach.call(box.querySelectorAll("[data-baddgroup]"), function (btn) {
      btn.onclick = function () {
        b.items.push({ group: btn.getAttribute("data-baddgroup"), name: "", low: 0, high: 0, note: "" });
        S.dirty.budget = true; renderTabBody();
      };
    });
    var sb = el("prodSaveBudget");
    if (sb) sb.onclick = function () {
      saveField("budget", b, function () { S.dirty.budget = false; toast("✓ 预算已保存"); renderTabBody(); });
    };
    var cb = el("prodCsvBudget");
    if (cb) cb.onclick = function () { exportBudgetCSV(); };
  }
  function field(label, inner) { return '<div class="prod-field"><span>' + label + "</span>" + inner + "</div>"; }
  function bindBudgetRun() {
    var rb = el("prodRunBudget");
    if (!rb) return;
    rb.onclick = function () {
      if (S.cur.budget && !confirm("重新估会覆盖当前预算表（包括你的手改）。继续？")) return;
      startJob("budget", {
        type: el("bdType").value, level: el("bdLevel").value,
        episodes: num(el("bdEps").value, 1), note: el("bdNote").value.trim()
      });
    };
  }
  function softRefreshBudgetToolbar() { renderTabBody(); }
  function recalcBudget() {
    var b = S.cur.budget;
    if (!b || !el("prodBudgetTotal")) return;
    var lo = 0, hi = 0, byG = {};
    (b.items || []).forEach(function (it) {
      lo += num(it.low); hi += num(it.high);
      var g = it.group || "其他";
      byG[g] = byG[g] || [0, 0];
      byG[g][0] += num(it.low); byG[g][1] += num(it.high);
    });
    var pct = num(b.contingencyPct, 8);
    var tlo = lo * (1 + pct / 100), thi = hi * (1 + pct / 100);
    el("prodBudgetTotal").innerHTML = '<div class="prod-budget-total">'
      + '<span class="label">制作成本小计</span><span class="amount">' + fmtMoney(lo) + " – " + fmtMoney(hi) + "</span>"
      + '<span class="sub">+ 不可预见费 ' + pct + "%</span>"
      + '<span class="label" style="margin-left:8px">总参考</span><span class="amount" style="color:var(--story)">' + fmtMoney(tlo) + " – " + fmtMoney(thi) + "</span>"
      + (b.shootDays ? '<span class="sub">预估拍摄周期 ' + b.shootDays + " 天</span>" : "")
      + "</div>";
    [].forEach.call(document.querySelectorAll("[data-gsum]"), function (n) {
      var g = n.getAttribute("data-gsum");
      if (byG[g]) n.textContent = "小计 " + fmtMoney(byG[g][0]) + " – " + fmtMoney(byG[g][1]) + " 万";
    });
  }
  function fmtMoney(n) {
    n = Math.round(n * 10) / 10;
    if (n >= 10000) return (n / 10000).toFixed(2).replace(/0+$/, "").replace(/\.$/, "") + " 亿";
    return String(Math.round(n * 10) / 10);
  }
  function exportBudgetCSV() {
    var b = S.cur.budget;
    var lines = [["分组", "科目", "低（万元）", "高（万元）", "说明"].map(csvCell).join(",")];
    (b.items || []).forEach(function (it) {
      lines.push([it.group, it.name, num(it.low), num(it.high), it.note].map(csvCell).join(","));
    });
    var lo = 0, hi = 0;
    (b.items || []).forEach(function (it) { lo += num(it.low); hi += num(it.high); });
    var pct = num(b.contingencyPct, 8);
    lines.push(["", "小计", lo, hi, ""].map(csvCell).join(","));
    lines.push(["", "不可预见费 " + pct + "%", Math.round(lo * pct) / 100, Math.round(hi * pct) / 100, ""].map(csvCell).join(","));
    lines.push(["", "总计", Math.round(lo * (1 + pct / 100) * 10) / 10, Math.round(hi * (1 + pct / 100) * 10) / 10, ""].map(csvCell).join(","));
    downloadCSV((S.cur.title || "项目") + "-参考预算.csv", lines);
    toast("✓ 已导出 CSV");
  }

  /* ===================================================== 任务（生成）流程 */
  function bindRun(btnId, kind) {
    var b = el(btnId);
    if (b) b.onclick = function () { startJob(kind); };
  }
  function startJob(kind, options) {
    if (S.job) return toast("已有任务在跑，等它完成再来", true);
    api("/api/production/run", { method: "POST", body: { id: S.cur.id, kind: kind, options: options || {} } })
      .then(function (j) {
        if (!j.ok) return toast(j.error || "任务启动失败", true);
        S.job = { id: j.jobId, kind: kind, t0: Date.now() };
        showLoading(kind);
        pollJob();
        renderTabBody();   // 把"开始"按钮藏掉、显示后台条
      }).catch(function () { toast("没连上后端", true); });
  }

  var LOAD_META = {
    analysis: { title: "制片帽正在解剖剧本", tip: "思考型模型通读全本，约 1–3 分钟。可收起浮层先干别的。",
      stages: [[0, "📖 通读剧本…"], [30, "🔬 拆结构、捋人物…"], [60, "⚖️ 评估亮点与风险…"], [85, "✍️ 撰写解剖报告…"]] },
    scenes: { title: "制片帽正在拆分场表", tip: "逐批拆场提取人物/道具/特殊需求，批数多时会久一点。",
      stages: null },  // scenes 有真实进度
    budget: { title: "制片帽正在编制预算", tip: "对照中国市场行情逐科目估算，约 1–3 分钟。",
      stages: [[0, "📊 汇总拆解数据…"], [30, "💰 对照市场行情…"], [60, "🧮 逐科目估区间…"], [85, "✍️ 写假设与说明…"]] }
  };
  function showLoading(kind) {
    var m = LOAD_META[kind];
    el("prodLoadTitle").textContent = m.title;
    el("prodLoadTip").textContent = m.tip;
    el("prodProgFill").style.width = "0%";
    el("prodProgLabel").textContent = "启动中…";
    el("prodLoading").hidden = false;
    if (S.progTimer) clearInterval(S.progTimer);
    S.progTimer = setInterval(function () { tickProgress(); }, 600);
  }
  function tickProgress(realPct, realMsg) {
    if (!S.job) return;
    var m = LOAD_META[S.job.kind], pct, label;
    if (S.job.kind === "scenes") {
      pct = Math.max(2, num(realPct));
      label = realMsg || "拆解中…";
      S.job.lastPct = pct; S.job.lastMsg = label;
      if (realPct == null && S.job.lastPct != null) { pct = S.job.lastPct; label = S.job.lastMsg; }
    } else {
      var t = (Date.now() - S.job.t0) / 1000;
      pct = Math.min(95, Math.round((1 - Math.exp(-t / 50)) * 100));
      label = m.stages[0][1];
      for (var i = 0; i < m.stages.length; i++) if (pct >= m.stages[i][0]) label = m.stages[i][1];
      if (realMsg) label = realMsg;
    }
    el("prodProgFill").style.width = pct + "%";
    el("prodProgLabel").textContent = label + "  " + pct + "%";
  }
  function pollJob() {
    if (S.pollTimer) clearInterval(S.pollTimer);
    S.pollTimer = setInterval(function () {
      if (!S.job) return clearInterval(S.pollTimer);
      api("/api/production/job?id=" + S.job.id).then(function (j) {
        if (!j.ok || !j.job) return;
        var job = j.job;
        if (job.status === "running") {
          if (S.job.kind === "scenes") tickProgress(job.progress, job.message);
          else if (job.message) tickProgress(null, null);
          return;
        }
        // 终态
        clearInterval(S.pollTimer); S.pollTimer = null;
        if (S.progTimer) { clearInterval(S.progTimer); S.progTimer = null; }
        var kind = S.job.kind;
        S.job = null;
        if (job.status === "done") {
          el("prodProgFill").style.width = "100%";
          el("prodProgLabel").textContent = "✓ 完成，正在呈现…";
          // 重新拉项目，把新结果接进来
          api("/api/production/project?id=" + S.cur.id).then(function (r) {
            el("prodLoading").hidden = true;
            if (r.ok) {
              S.cur = r.project;
              S.tab = kind === "scenes" ? "scenes" : (kind === "budget" ? "budget" : "analysis");
              S.dirty[kind === "analysis" ? "scenes" : kind] = S.dirty[kind] || false;
              if (kind === "scenes") S.dirty.scenes = false;
              if (kind === "budget") S.dirty.budget = false;
              renderProject();
              toast("✓ " + jobName(kind) + "完成");
            }
          });
        } else {
          el("prodLoading").hidden = true;
          toast("✗ " + jobName(kind) + "失败：" + (job.error || "未知错误"), true);
          renderTabBody();
        }
      }).catch(function () { /* 网络抖动：下个周期再试 */ });
    }, 2500);
  }

  function saveField(fieldName, data, cb) {
    api("/api/production/save", { method: "POST", body: { id: S.cur.id, field: fieldName, data: data } })
      .then(function (j) {
        if (j.ok) cb();
        else toast(j.error || "保存失败", true);
      }).catch(function () { toast("保存失败：没连上后端", true); });
  }

  /* ===================================================== 入口接管 */
  function hijackEntry() {
    var btns = document.querySelectorAll(".modules .module");
    var found = false;
    [].forEach.call(btns, function (b) {
      if (b.textContent.indexOf("制作") > -1) {
        found = true;
        b.classList.remove("disabled");
        b.removeAttribute("title");
        b.innerHTML = "制作 · 制片统筹";
        b.onclick = openProd;
      }
    });
    return found;
  }
  // app.js 在前面已同步执行完（script 都在 body 底部），DOM 此刻是齐的；保险起见失败就再试一次
  if (!hijackEntry()) {
    var retry = 0, t = setInterval(function () {
      if (hijackEntry() || ++retry > 20) clearInterval(t);
    }, 500);
  }
  window.ZPMProduction = { open: openProd };   // 调试钩子：控制台 ZPMProduction.open()
})();
