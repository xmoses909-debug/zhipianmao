/* 制片帽 · 选题雷达 —— v5（欢迎/注册/选偏好/主页 多屏 + AI 小助手） */
(function () {
  "use strict";
  var DATA = window.MAOZHIPIAN;
  if (!DATA) { document.body.innerHTML = "<p style='padding:40px'>数据未加载。</p>"; return; }

  /* 简笔画鸭舌帽 logo（currentColor 控制颜色） */
  var HAT = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M12 39 C 9 25, 31 14, 45 23 C 51 27, 52 34, 50 39"/><path d="M12 39 L 50 39"/><path d="M12 39 C 5 40, 7 45, 17 44"/></svg>';

  var PALETTE = ["古装", "美食", "治愈", "音乐", "热血", "运动", "电竞", "年代", "民国", "都市",
    "校园", "职场", "群像", "励志", "悬疑", "探案", "科幻", "玄幻", "历史", "言情", "日系文艺", "人文"];
  var SOURCES = ["豆瓣阅读", "晋江", "番茄", "文学期刊"];
  var SCALE_OPTS = ["剧集", "电影", "短剧"];
  var DEFAULT_PROFILE = {
    likes: ["古装", "美食", "治愈", "音乐", "热血", "运动", "日系文艺", "人文"],
    dislikes: [], sources: ["豆瓣阅读", "晋江"], scale: ["剧集", "电影"], status: "优先已完结", customWants: ""
  };

  /* 存储 */
  var KEY = "maozhipian.v1";
  var state = load();
  function load() {
    var s; try { s = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { s = {}; }
    s.fav = s.fav || {}; s.feedback = s.feedback || {};
    s.profile = s.profile || JSON.parse(JSON.stringify(DEFAULT_PROFILE));
    if (!s.profile.sources) s.profile.sources = DEFAULT_PROFILE.sources.slice();
    if (s.profile.customWants == null) s.profile.customWants = "";
    return s;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }

  /* 工具 */
  function el(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function allBooks() { return (livePicks || []).concat(DATA.picks).concat(DATA.candidates); }
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
    box.innerHTML = PALETTE.map(function (g) {
      var st = genreState(g);
      return '<span class="chip pref ' + st + '" data-g="' + g + '">' + (st === "like" ? "✓ " : "") + g + "</span>";
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
    if (ci) { ci.value = state.profile.customWants || ""; ci.oninput = function () { state.profile.customWants = ci.value; save(); renderSummary("profileSummary" + sfx); if (current === "s-main") renderAssistant(); }; }
    renderSummary("profileSummary" + sfx);
  }
  function onProfileChange() {
    save();
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
    el("logout").onclick = function () { if (confirm("退出当前账号？（偏好会保留）")) { delete state.account; save(); showScreen("s-welcome"); } };
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
      body: JSON.stringify({ profile: state.profile, count: 3 })
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
    if (act === "fav") state.fav[id] = !state.fav[id];
    else state.feedback[id] = (state.feedback[id] === act) ? undefined : act;
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

  /* ---------- 流程绑定 ---------- */
  function bindFlow() {
    el("toRegister").onclick = function () { showScreen("s-register"); };
    el("toOnboard").onclick = function () {
      var u = el("regUser").value.trim(), p = el("regPass").value;
      if (!u || !p) { el("regErr").textContent = "用户名和密码都填一下吧。"; el("regErr").hidden = false; return; }
      el("regErr").hidden = true;
      state.account = { username: u, t: 1 }; save();
      showScreen("s-onboard"); renderPrefs("-ob");
      var sob = el("statusPick-ob"); if (sob) sob.onchange = function () { state.profile.status = sob.value; onProfileChange(); };
    };
    el("toMain").onclick = function () { enterMain(); discover(); };

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
  if (state.account) enterMain(); else showScreen("s-welcome");
})();
