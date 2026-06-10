/* 制片帽 · 选题雷达 —— v5（欢迎/注册/选偏好/主页 多屏 + AI 小助手） */
(function () {
  "use strict";
  var DATA = window.MAOZHIPIAN;
  if (!DATA) { document.body.innerHTML = "<p style='padding:40px'>数据未加载。</p>"; return; }

  /* 报童帽 logo —— 直接用帽帽提供的原图（app/logo.png，透明底像素原样；尺寸由各容器 CSS 控制） */
  var HAT = '<img src="logo.png" alt="制片帽" style="display:block">';

  // 标签按 4 大类归组（时代 / 类型 / 题材 / 调性），不再是一堆乱标签
  var PALETTE_GROUPS = [
    { name: "时代", tags: ["历史", "古装", "民国", "近代", "现代", "架空", "未来"] },
    { name: "类型", tags: ["言情", "悬疑", "推理", "科幻", "奇幻", "武侠", "现实", "犯罪"] },
    { name: "题材", tags: ["美食", "音乐", "运动", "电竞", "职场", "校园", "娱乐圈", "群像"] },
    { name: "调性", tags: ["治愈", "热血", "励志", "文艺", "人文", "爽感"] }
  ];
  var PALETTE = PALETTE_GROUPS.reduce(function (a, g) { return a.concat(g.tags); }, []);
  var SOURCES = ["豆瓣阅读", "晋江", "番茄", "文学期刊"];
  var SCALE_OPTS = ["剧集", "电影", "短剧"];
  var DEFAULT_PROFILE = {
    likes: ["古装", "美食", "音乐", "运动", "治愈", "热血", "文艺", "人文"],
    dislikes: [], sources: ["豆瓣阅读", "晋江"], scale: ["剧集", "电影"], status: "优先已完结", customWants: ""
  };

  /* 存储（本地）+ 后端 API（带"无后端→本地演示"降级）
     设计：localStorage 始终是缓存/降级层；探测到后端且已登录时，收藏/点赞/偏好额外同步云端。
     这样线上静态版（无后端）仍能完整演示，本地/部署版则是真账号、可跨设备同步。 */
  var KEY = "maozhipian.v1";
  var TKEY = "maozhipian.token";   // 登录令牌：存本地，下次启动用 /api/me 还原
  var BACKEND = false;             // 启动时探测 /api/health；线上静态版探测不到→走本地演示
  var state = load();

  function normalizeProfile(p) {
    p = p || JSON.parse(JSON.stringify(DEFAULT_PROFILE));
    if (!p.likes) p.likes = [];
    if (!p.sources) p.sources = DEFAULT_PROFILE.sources.slice();
    if (!p.scale) p.scale = DEFAULT_PROFILE.scale.slice();
    if (!p.status) p.status = DEFAULT_PROFILE.status;
    if (p.customWants == null) p.customWants = "";
    p.dislikes = [];  // 两态标签后不再有"不感冒"；清掉历史脏数据（曾导致过度过滤、选片为空）
    // 标签归类改版：把旧标签迁移到新分类标签，老用户的口味不丢
    var o2n = { "日系文艺": "文艺", "都市": "现代", "年代": "现实", "探案": "推理", "玄幻": "奇幻" };
    p.likes = p.likes.map(function (g) { return o2n[g] || g; })
      .filter(function (g, i, a) { return a.indexOf(g) === i; });
    return p;
  }
  function load() {
    var s; try { s = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { s = {}; }
    s.fav = s.fav || {}; s.feedback = s.feedback || {};
    s.profile = normalizeProfile(s.profile);
    return s;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }

  /* ---- 令牌 + API 小工具 ---- */
  function token() { try { return localStorage.getItem(TKEY) || ""; } catch (e) { return ""; } }
  function setToken(t) { try { t ? localStorage.setItem(TKEY, t) : localStorage.removeItem(TKEY); } catch (e) {} }
  function authed() { return BACKEND && !!token(); }   // 真账号在线：收藏/偏好才走云端
  function api(path, opts) {
    opts = opts || {};
    var h = { "Content-Type": "application/json" };
    if (token()) h["Authorization"] = "Bearer " + token();
    return fetch(path, {
      method: opts.method || "GET", headers: h,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; },
        function () { return { status: r.status, body: {} }; });
    });
  }
  function probeBackend() {
    return fetch("/api/health").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { BACKEND = !!(j && j.ok); return BACKEND; })
      .catch(function () { BACKEND = false; return false; });
  }
  function adoptServerState(body) {
    // 真账号为准：用云端账号/偏好/收藏/点赞覆盖本地
    state.account = { username: body.username };
    if (body.profile) state.profile = normalizeProfile(body.profile);
    var fav = {}; Object.keys(body.fav || {}).forEach(function (k) { fav[k] = true; });
    state.fav = fav;
    state.feedback = body.feedback || {};
    save();
  }
  /* 把口味偏好同步云端：防抖 800ms（避免每点一下就发一次） */
  var profSyncTimer = null;
  function syncProfile() {
    if (!authed()) return;
    if (profSyncTimer) clearTimeout(profSyncTimer);
    profSyncTimer = setTimeout(function () { api("/api/profile", { method: "POST", body: { profile: state.profile } }); }, 800);
  }
  function pushProfile() { if (authed()) api("/api/profile", { method: "POST", body: { profile: state.profile } }); }

  /* 工具 */
  function el(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function allBooks() { return (livePicks || []).concat(DATA.picks).concat(DATA.candidates); }
  function bookById(id) { return allBooks().filter(function (x) { return x.id === id; })[0] || { id: id }; }
  function headlineScore(b) { return (b.aiScore != null) ? b.aiScore : b.matchScore; }
  function sourceShort(b) {
    if (!b.source) return "—";
    if (b.source.indexOf("晋江") > -1) return "晋江";
    if (b.source.indexOf("豆瓣") > -1) return "豆瓣阅读";
    if (b.source.indexOf("番茄") > -1) return "番茄";
    return b.source;
  }
  function fmtWords(n) { if (!n) return "字数待核实"; return n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, "") + " 万字" : n + " 字"; }
  function dislikedGenres(b) { return (b.genres || []).filter(function (g) { return state.profile.dislikes.indexOf(g) > -1; }); }

  /* ---------- 屏幕路由 ---------- */
  var current = "";
  function showScreen(id) {
    ["s-welcome", "s-register", "s-onboard", "s-main"].forEach(function (s) { el(s).hidden = (s !== id); });
    current = id; window.scrollTo(0, 0);
  }
  function injectLogos() {
    [].forEach.call(document.querySelectorAll(".logo-slot, .brand-hat, .assistant-ava"), function (n) { n.innerHTML = HAT; });
  }

  /* ---------- 偏好控件（参数化，欢迎引导与侧栏共用） ---------- */
  function genreState(g) { return state.profile.likes.indexOf(g) > -1 ? "like" : ""; }
  function renderGenrePalette(id) {
    var box = el(id); if (!box) return;
    box.innerHTML = PALETTE_GROUPS.map(function (grp) {
      var chips = grp.tags.map(function (g) {
        var st = genreState(g);
        return '<span class="chip pref ' + st + '" data-g="' + g + '">' + (st === "like" ? "✓ " : "") + g + "</span>";
      }).join("");
      return '<div class="pref-group"><span class="pref-group-name">' + grp.name
        + '</span><div class="chips chips-pref">' + chips + "</div></div>";
    }).join("");
    [].forEach.call(box.querySelectorAll(".chip"), function (c) { c.onclick = function () { cycleGenre(c.getAttribute("data-g")); }; });
  }
  function cycleGenre(g) {
    // 两态：未选 → 选中；再点 → 取消。（不再有"不感冒打叉"那一态）
    var li = state.profile.likes.indexOf(g);
    if (li > -1) state.profile.likes.splice(li, 1);
    else state.profile.likes.push(g);
    onProfileChange();
  }
  function renderToggle(id, opts, arr) {
    var box = el(id); if (!box) return;
    box.innerHTML = opts.map(function (s) { return '<span class="chip' + (arr.indexOf(s) > -1 ? " on" : "") + '" data-s="' + s + '">' + s + "</span>"; }).join("");
    [].forEach.call(box.querySelectorAll(".chip"), function (c) {
      c.onclick = function () { var s = c.getAttribute("data-s"), i = arr.indexOf(s); if (i > -1) arr.splice(i, 1); else arr.push(s); onProfileChange(); };
    });
  }
  function renderSummary(id) {
    var box = el(id); if (!box) return;
    var p = state.profile;
    var h = "📌 <b>偏好总结</b>：你偏好 " + (p.likes.length ? "【" + p.likes.join(" · ") + "】" : "【还没选】");
    if (p.dislikes.length) h += "，不感冒 【" + p.dislikes.join(" · ") + "】";
    if (p.sources.length) h += "；来源 【" + p.sources.join(" · ") + "】";
    if (p.scale.length) h += "；体量 【" + p.scale.join(" · ") + "】";
    if (p.status && p.status !== "不限") h += "；" + p.status;
    h += "。";
    if (p.customWants && p.customWants.trim()) h += "<br>另外你说：「" + esc(p.customWants.trim()) + "」——记下了，找的时候重点考虑。";
    h += "<br>选品始终：<b>好故事优先于题材</b>。";
    box.innerHTML = h;
  }
  function renderPrefs(sfx) {
    var pr = el("principle" + sfx); if (pr) pr.innerHTML = "★ " + DATA.taste.principle + "<br><span class='formula'>" + DATA.scoring.formula + "</span>";
    renderGenrePalette("genrePalette" + sfx);
    renderToggle("sourcePick" + sfx, SOURCES, state.profile.sources);
    renderToggle("scalePick" + sfx, SCALE_OPTS, state.profile.scale);
    var sp = el("statusPick" + sfx); if (sp) sp.value = state.profile.status;
    var ci = el("customWants" + sfx);
    if (ci) { ci.value = state.profile.customWants || ""; ci.oninput = function () { state.profile.customWants = ci.value; save(); syncProfile(); renderSummary("profileSummary" + sfx); if (current === "s-main") renderAssistant(); }; }
    renderSummary("profileSummary" + sfx);
  }
  function onProfileChange() {
    save(); syncProfile();
    var sfx = current === "s-onboard" ? "-ob" : "";
    renderPrefs(sfx);
    if (current === "s-main") { renderLists(); renderAssistant(); }
  }

  /* ---------- AI 小助手 ---------- */
  function renderAssistant() {
    var u = (state.account && state.account.username) || "你";
    var d = new Date();
    var wk = "日一二三四五六".charAt(d.getDay());
    var date = (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 星期" + wk;
    var n = DATA.picks.length, m = DATA.candidates.length;
    var downs = Object.keys(state.feedback).filter(function (k) { return state.feedback[k] === "down"; }).length;
    var ups = Object.keys(state.feedback).filter(function (k) { return state.feedback[k] === "up"; }).length;
    var line;
    if (ups >= 2) line = "看您已经翻牌子点了 " + ups + " 部，眼光不错——收藏夹里留着，慢慢挑。";
    else if (downs >= 2) line = "您今天拍掉了 " + downs + " 部，口味挺刁的 😏 左边重新点点筛选，我再给您张罗几道新的。";
    else line = "本周给您备了 <b>" + n + "</b> 道主菜、<b>" + m + "</b> 道备选。没一道合胃口？左边随时翻牌子重选。";
    var cw = (state.profile.customWants || "").trim();
    if (cw) line += " 您特意点了道菜——「" + esc(cw.length > 24 ? cw.slice(0, 24) + "…" : cw) + "」，我记菜单上了。";
    el("assistantText").innerHTML = "🎩 <b>" + esc(u) + "</b>，今天 " + date + "。" + line;
  }
  function renderAccount() {
    el("account").innerHTML = '<span class="acc-name">👤 ' + esc((state.account && state.account.username) || "") + '</span><button class="acc-out" id="logout">退出</button>';
    el("logout").onclick = function () {
      if (!confirm("退出当前账号？")) return;
      if (authed()) api("/api/logout", { method: "POST" });
      setToken(""); delete state.account; save(); showScreen("s-welcome");
    };
  }

  /* ---------- 主页头部 / 筛选 ---------- */
  function mdBold(s) { return (s || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"); }
  function renderHeader() {
    el("weekLabel").textContent = DATA.week.label + " · " + DATA.week.range;
    el("sourcePill").textContent = "本周来源：" + DATA.week.source;
    renderAgentNote();
  }
  function renderAgentNote() {
    if (livePicks && liveNote) el("agentNoteText").innerHTML = "🎩 <b>制片帽为您精选</b>：" + esc(liveNote);
    else el("agentNoteText").innerHTML = mdBold(DATA.agentNote);
  }

  /* ---------- 制片帽 · 为我选片（前端 → 本地后端 → 出片）---------- */
  var livePicks = null, liveNote = "";
  var progTimer = null;
  /* 选片约 100 秒，给个会走的进度条 + 分阶段文案，别让用户干等心里没底。
     后端是一次阻塞请求、拿不到真实百分比，故用"渐近曲线"模拟：随时间逼近 95%，
     真结果回来再补到 100%。文案对应后台真实阶段：搜罗→通读→判断→撰写。 */
  var PROG_STAGES = [
    [0, "📡 正在各大网站搜罗新书…"],
    [16, "📖 逐本通读故事文案…"],
    [40, "⚖️ 按好故事标准判断故事力…"],
    [66, "🎯 对照你的需求精挑细选…"],
    [85, "✍️ 撰写改编分析，即将完成…"]
  ];
  function startProgress() {
    var ov = el("loadingOverlay"), fill = el("loadProgFill"), lab = el("loadProgLabel");
    if (!ov) return;
    ov.hidden = false; fill.style.width = "0%";
    var t0 = Date.now();
    function tick() {
      var t = (Date.now() - t0) / 1000;
      var pct = Math.min(95, Math.round((1 - Math.exp(-t / 40)) * 100));
      fill.style.width = pct + "%";
      var label = PROG_STAGES[0][1];
      for (var i = 0; i < PROG_STAGES.length; i++) { if (pct >= PROG_STAGES[i][0]) label = PROG_STAGES[i][1]; }
      lab.textContent = label + "  " + pct + "%";
    }
    tick(); progTimer = setInterval(tick, 500);
  }
  function endProgress(ok) {
    if (progTimer) { clearInterval(progTimer); progTimer = null; }
    var ov = el("loadingOverlay"), fill = el("loadProgFill"), lab = el("loadProgLabel");
    if (!ov) return;
    fill.style.width = "100%";
    lab.textContent = ok ? "✓ 选好了，正在为您呈现…" : "未能完成";
    setTimeout(function () { if (el("loadingOverlay")) el("loadingOverlay").hidden = true; }, ok ? 700 : 1800);
  }
  function discover() {
    var btn = el("aiDiscover"), status = el("discoverStatus");
    if (!btn.getAttribute("data-label")) btn.setAttribute("data-label", btn.textContent);
    btn.disabled = true; btn.textContent = "制片帽正在为您选片…";
    status.textContent = ""; status.className = "discover-status";
    startProgress();
    fetch("/api/discover", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: state.profile, count: 5 })
    }).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok && j.picks && j.picks.length) {
          livePicks = j.picks; liveNote = j.note || "";
          renderAgentNote(); renderLists();
          endProgress(true);
          status.textContent = "✓ 制片帽为您选了 " + j.picks.length + " 部，已更新到「本周精选」。";
          status.className = "discover-status ok";
        } else {
          endProgress(false);
          status.textContent = "✗ " + ((j && j.error) || "这次没挑到合适的，调调偏好再试。");
          status.className = "discover-status err";
        }
      })
      .catch(function () {
        endProgress(false);
        status.textContent = "✗ 没连上本地服务——请先双击「启动-AI.command」启动后再试。";
        status.className = "discover-status err";
      })
      .then(function () { btn.disabled = false; btn.textContent = btn.getAttribute("data-label"); });
  }
  var filters = { genre: "", source: "", status: "", q: "", favOnly: false };
  function renderChipFilter(id, values, key) {
    var box = el(id);
    box.innerHTML = '<span class="chip" data-v="">全部</span>' + values.map(function (v) { return '<span class="chip" data-v="' + v + '">' + v + "</span>"; }).join("");
    [].forEach.call(box.querySelectorAll(".chip"), function (c) {
      if (c.getAttribute("data-v") === filters[key]) c.classList.add("on");
      c.onclick = function () { filters[key] = c.getAttribute("data-v"); [].forEach.call(box.querySelectorAll(".chip"), function (x) { x.classList.remove("on"); }); c.classList.add("on"); renderLists(); };
    });
  }
  function renderGenreFilter() { var set = {}; allBooks().forEach(function (b) { (b.genres || []).forEach(function (g) { set[g] = (set[g] || 0) + 1; }); }); renderChipFilter("genreFilter", Object.keys(set).sort(function (a, b) { return set[b] - set[a]; }), "genre"); }
  function renderSourceFilter() { var set = {}; allBooks().forEach(function (b) { set[sourceShort(b)] = 1; }); renderChipFilter("sourceFilter", Object.keys(set), "source"); }
  function matchFilter(b) {
    if (filters.favOnly && !state.fav[b.id]) return false;
    if (filters.source && sourceShort(b) !== filters.source) return false;
    if (filters.status && (b.status || "").indexOf(filters.status) === -1) return false;
    if (filters.genre && (b.genres || []).indexOf(filters.genre) === -1) return false;
    if (filters.q) { var hay = (b.title + b.author + (b.genres || []).join("")).toLowerCase(); if (hay.indexOf(filters.q.toLowerCase()) === -1) return false; }
    return true;
  }

  /* ---------- 卡片 ---------- */
  function scoresRow(b) {
    var story = (b.storyScore != null) ? b.storyScore : null;
    var sv = story != null ? '<span class="sval">' + story + "</span>" : '<span class="sval pending">待评估</span>';
    return '<div class="bc-scores"><div class="sline"><span class="sk">故事力</span><span class="sbar"><i style="width:' + (story != null ? story : 0) + '%"></i></span>' + sv + "</div>"
      + '<div class="sline"><span class="sk">题材</span><span class="sbar match"><i style="width:' + b.matchScore + '%"></i></span><span class="sval">' + b.matchScore + "</span></div></div>";
  }
  function bookCard(b) {
    var tags = (b.genres || []).slice(0, 4).map(function (g) { return '<span class="tag' + ((b.tier1Hits || []).indexOf(g) > -1 ? " hit" : "") + '">' + g + "</span>"; }).join("");
    var verified = b.linkVerified ? '<span class="ok">✓ 链接已核实</span>' : '<span class="warn">链接待核实</span>';
    var statusCls = (b.status || "").indexOf("完结") > -1 ? "ok" : "";
    var dis = dislikedGenres(b), disFlag = dis.length ? '<span class="dis-flag">⚠ 含你不感冒：' + dis.join("、") + "</span>" : "";
    var fav = state.fav[b.id], fb = state.feedback[b.id], badge = (b.aiScore != null) ? "综合" : "题材";
    return '<div class="book-card' + (dis.length ? " has-dis" : "") + '" data-id="' + b.id + '">'
      + '<div class="bc-top"><div><div class="bc-title">' + b.title + '<span class="src-tag">' + sourceShort(b) + "</span></div><div class=\"bc-author\">" + b.author + "</div></div>"
      + '<div class="score-badge">' + headlineScore(b) + "<small>" + badge + "</small></div></div>"
      + scoresRow(b) + '<div class="bc-logline">' + (b.logline || "") + "</div>"
      + '<div class="bc-tags">' + tags + "</div>" + disFlag
      + '<div class="bc-meta"><span class="' + statusCls + '">' + (b.status || "状态待核实") + "</span><span>" + fmtWords(b.wordCount) + "</span>" + verified + "</div>"
      + '<div class="bc-actions"><button class="act act-fav' + (fav ? " on-fav" : "") + '" data-act="fav" data-id="' + b.id + '">' + (fav ? "★ 已收藏" : "☆ 收藏") + "</button>"
      + '<button class="act act-up' + (fb === "up" ? " on-up" : "") + '" data-act="up" data-id="' + b.id + '">👍</button>'
      + '<button class="act act-down' + (fb === "down" ? " on-down" : "") + '" data-act="down" data-id="' + b.id + '">👎</button></div></div>';
  }
  function renderLists() {
    var weekly = (livePicks || DATA.picks).filter(matchFilter);
    var sk = function (b) { return (b.aiScore != null) ? b.aiScore : -1; };
    var cands = DATA.candidates.filter(matchFilter).sort(function (a, b) { return sk(b) - sk(a); });
    el("weeklyCards").innerHTML = weekly.map(bookCard).join("");
    el("candidateCards").innerHTML = cands.map(bookCard).join("");
    el("weeklyCount").textContent = "（" + weekly.length + (livePicks ? " · 实时精选" : "") + "）";
    el("emptyState").hidden = (weekly.length + cands.length) > 0;
    bindCards();
  }
  function bindCards() {
    [].forEach.call(document.querySelectorAll(".book-card"), function (card) {
      card.onclick = function (e) { if (e.target.closest(".act")) return; openModal(card.getAttribute("data-id")); };
    });
    [].forEach.call(document.querySelectorAll(".book-card .act"), function (btn) {
      btn.onclick = function (e) { e.stopPropagation(); toggle(btn.getAttribute("data-id"), btn.getAttribute("data-act")); renderLists(); renderAssistant(); };
    });
  }
  function toggle(id, act) {
    if (act === "fav") {
      state.fav[id] = !state.fav[id];
      if (authed()) api("/api/favorite", { method: "POST", body: { bookId: id, book: bookById(id), on: !!state.fav[id] } });
    } else {
      state.feedback[id] = (state.feedback[id] === act) ? undefined : act;
      if (authed()) api("/api/feedback", { method: "POST", body: { bookId: id, value: state.feedback[id] || "" } });
    }
    save(); updateLearnNote();
  }
  function updateLearnNote() {
    var u = 0, d = 0; Object.keys(state.feedback).forEach(function (k) { if (state.feedback[k]) (state.feedback[k] === "up" ? u++ : d++); });
    var n = u + d;
    el("learnNote").textContent = n === 0 ? "反馈学习：尚无反馈 —— 点赞 / 拍掉会持续校准" : "反馈学习：已记录 " + n + " 条（👍" + u + " · 👎" + d + "）→ 下周据此调整";
  }

  /* ---------- 弹窗 ---------- */
  function listHTML(a) { return "<ul>" + (a || []).map(function (x) { return "<li>" + x + "</li>"; }).join("") + "</ul>"; }
  function fact(k, v) { return '<div class="m-fact"><span class="k">' + k + '</span><span class="v">' + (v || "—") + "</span></div>"; }
  function section(t, inner) { return '<div class="m-section"><h4>' + t + "</h4>" + inner + "</div>"; }
  function openModal(id) {
    var b = allBooks().filter(function (x) { return x.id === id; })[0]; if (!b) return;
    var sn = (b.storyScore != null) ? b.storyScore : "待评估";
    var chars = (b.characters && b.characters.length) ? '<div class="m-section m-chars"><h4>主要人物</h4><ul>' + b.characters.map(function (c) { return "<li><strong>" + c.name + "</strong> —— " + c.role + "</li>"; }).join("") + "</ul></div>" : "";
    var dn = b.dataNote ? '<div class="data-note">⚠ ' + b.dataNote + "</div>" : "";
    el("modalBody").innerHTML =
      '<div class="m-head"><div><div class="m-title">' + b.title + '<span class="src-tag">' + sourceShort(b) + "</span></div><div class=\"m-author\">" + b.author + " · " + (b.source || "") + "</div></div>"
      + '<div class="score-badge" style="width:56px;height:56px;font-size:22px">' + headlineScore(b) + "<small>" + (b.aiScore != null ? "综合" : "题材") + "</small></div></div>"
      + '<p class="m-logline">「' + (b.logline || "") + "」</p>"
      + '<div class="m-story"><div class="m-story-head"><span class="m-story-label">好故事判断</span><span class="m-story-score">故事力 <b>' + sn + "</b></span><span class=\"m-story-score match\">题材 <b>" + b.matchScore + "</b></span></div><p>" + (b.storyVerdict || "") + "</p></div>"
      + '<div class="m-section"><div class="m-grid">' + fact("状态", b.status) + fact("篇幅", fmtWords(b.wordCount)) + fact("建议体量", b.scale) + fact("调性", b.tone) + fact("热度", b.heat || "—") + fact("题材匹配说明", b.matchNote || "—") + "</div></div>"
      + '<div class="m-section"><h4>故事梗概</h4><p>' + (b.synopsis || "") + "</p></div>" + chars
      + section("改编亮点", listHTML(b.highlights)) + section("改编难点 / 风险", listHTML(b.challenges))
      + section("对标作品", '<div class="m-bench">' + (b.benchmarks || []).map(function (x) { return '<span class="tag">' + x + "</span>"; }).join("") + "</div>")
      + section("为什么是现在", "<p>" + (b.whyNow || "") + "</p>") + section("版权状态线索", "<p>" + (b.rightsClue || "") + "</p>") + section("制片帽总评", "<p>" + (b.verdict || "") + "</p>") + dn
      + '<a class="' + (b.linkVerified ? "m-link" : "m-link unverified") + '" href="' + b.url + '" target="_blank" rel="noopener">' + (b.linkVerified ? "前往原文 →" : "在平台搜索此书 →") + "</a>"
      + '<div class="m-actions"><button class="act act-fav' + (state.fav[b.id] ? " on-fav" : "") + '" data-act="fav" data-id="' + b.id + '">' + (state.fav[b.id] ? "★ 已收藏" : "☆ 收藏") + "</button>"
      + '<button class="act act-up' + (state.feedback[b.id] === "up" ? " on-up" : "") + '" data-act="up" data-id="' + b.id + '">👍 想做</button>'
      + '<button class="act act-down' + (state.feedback[b.id] === "down" ? " on-down" : "") + '" data-act="down" data-id="' + b.id + '">👎 拍掉</button></div>';
    [].forEach.call(el("modalBody").querySelectorAll(".act"), function (btn) { btn.onclick = function () { var bid = btn.getAttribute("data-id"); toggle(bid, btn.getAttribute("data-act")); renderLists(); renderAssistant(); openModal(bid); }; });
    el("modal").hidden = false;
  }
  function closeModal() { el("modal").hidden = true; }

  /* ---------- 进入主页 ---------- */
  function enterMain() {
    showScreen("s-main");
    renderAccount(); renderAssistant(); renderHeader();
    renderPrefs(""); renderGenreFilter(); renderSourceFilter(); updateLearnNote(); renderLists();
  }

  /* ---------- 账号：注册 / 登录（真后端，带本地演示降级） ---------- */
  var authMode = "register";  // register | login
  function authErr(m) { var e = el("regErr"); if (e) { e.textContent = m; e.hidden = false; } }
  function reflectMode() {
    // 按"有无后端 + 注册/登录"刷新注册页文案
    var t = el("authTitle"), sub = el("authSub"), btn = el("toOnboard"), sw = el("authSwitch"), note = el("authNote");
    if (t) t.textContent = authMode === "register" ? "创建你的账号" : "登录你的账号";
    if (sub) sub.textContent = authMode === "register" ? "几秒钟，建一个属于你的选题雷达" : "欢迎回来，继续你的选题雷达";
    if (btn) btn.textContent = authMode === "register" ? "注册并继续 →" : "登录 →";
    if (sw) sw.textContent = authMode === "register" ? "已有账号？点此登录" : "没有账号？点此注册";
    if (note) note.innerHTML = BACKEND
      ? "🔐 <b>真账号</b>：用户名+密码加密存在服务器，可多设备登录，收藏与口味偏好云端同步。"
      : "⚠ <b>演示版</b>（当前未连到后端）：账号与收藏暂存在本机浏览器，不会上传、也无法多设备登录。启动「启动-AI.command」后即为真账号。";
  }
  function bindOnboardStatus() {
    var sob = el("statusPick-ob"); if (sob) sob.onchange = function () { state.profile.status = sob.value; onProfileChange(); };
  }
  function submitAuth() {
    var u = el("regUser").value.trim(), p = el("regPass").value;
    if (!u || !p) return authErr("用户名和密码都填一下吧。");
    el("regErr").hidden = true;
    if (!BACKEND) {  // 无后端：本地演示账号（线上静态版）
      state.account = { username: u, t: 1 }; save();
      showScreen("s-onboard"); renderPrefs("-ob"); bindOnboardStatus(); return;
    }
    var btn = el("toOnboard"); btn.disabled = true;
    var path = authMode === "register" ? "/api/register" : "/api/login";
    api(path, { method: "POST", body: { username: u, password: p } }).then(function (res) {
      btn.disabled = false;
      var j = res.body || {};
      if (!j.ok) {
        if (authMode === "register" && /已经被注册/.test(j.error || "")) { authMode = "login"; reflectMode(); }
        return authErr(j.error || "没成功，再试一下。");
      }
      setToken(j.token);
      if (authMode === "login") {
        // 老用户登录：拉云端状态，直接进主页（不自动选片，省一次等待；可手动点"重新为我选片"）
        api("/api/me").then(function (r2) {
          if (r2.status === 200 && r2.body && r2.body.ok) adoptServerState(r2.body);
          else { state.account = { username: u }; save(); }
          enterMain();
        });
      } else {  // 新用户注册：进选偏好
        state.account = { username: j.username }; save();
        showScreen("s-onboard"); renderPrefs("-ob"); bindOnboardStatus();
      }
    }).catch(function () { btn.disabled = false; authErr("没连上服务器，稍后再试。"); });
  }

  /* ---------- 流程绑定 ---------- */
  function bindFlow() {
    el("toRegister").onclick = function () { authMode = "register"; reflectMode(); showScreen("s-register"); };
    el("authSwitch").onclick = function () { authMode = (authMode === "register") ? "login" : "register"; if (el("regErr")) el("regErr").hidden = true; reflectMode(); };
    el("toOnboard").onclick = submitAuth;
    el("toMain").onclick = function () { pushProfile(); enterMain(); discover(); };

    // 主页侧栏：状态下拉、重置、筛选、弹窗
    el("statusPick").onchange = function () { state.profile.status = el("statusPick").value; onProfileChange(); };
    el("resetPref").onclick = function () { state.profile = JSON.parse(JSON.stringify(DEFAULT_PROFILE)); onProfileChange(); renderLists(); };
    el("aiDiscover").onclick = discover;
    el("searchBox").oninput = function (e) { filters.q = e.target.value; renderLists(); };
    el("statusFilter").onchange = function (e) { filters.status = e.target.value; renderLists(); };
    el("favOnly").onchange = function (e) { filters.favOnly = e.target.checked; renderLists(); };
    el("modalClose").onclick = closeModal;
    el("modal").onclick = function (e) { if (e.target === el("modal")) closeModal(); };
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
  }

  /* ---------- 启动 ---------- */
  injectLogos(); bindFlow();
  boot();
  function boot() {
    probeBackend().then(function () {
      reflectMode();
      if (BACKEND) {
        // 真账号模式：必须有有效令牌才进主页（忽略可能残留的本地演示账号）
        if (token()) {
          return api("/api/me").then(function (res) {
            if (res.status === 200 && res.body && res.body.ok) { adoptServerState(res.body); enterMain(); }
            else { setToken(""); showScreen("s-welcome"); }
          }).catch(function () { showScreen("s-welcome"); });
        }
        return showScreen("s-welcome");
      }
      if (state.account) enterMain(); else showScreen("s-welcome");  // 无后端 · 本地演示
    });
  }
})();
