/* 帽制片 · 选题雷达 —— 交互逻辑（v3：好故事 > 题材 + 用户可选偏好） */
(function () {
  "use strict";

  var DATA = window.MAOZHIPIAN;
  if (!DATA) { document.body.innerHTML = "<p style='padding:40px'>数据未加载，请确认 data/recommendations.js 存在。</p>"; return; }

  // 题材调色板（多用户可自选）
  var PALETTE = ["古装", "美食", "治愈", "音乐", "热血", "运动", "电竞", "年代", "民国", "都市",
    "校园", "职场", "群像", "励志", "悬疑", "探案", "科幻", "玄幻", "历史", "言情", "日系文艺", "人文"];
  var SCALE_OPTS = ["剧集", "电影", "短剧"];
  var DEFAULT_PROFILE = {
    likes: ["古装", "美食", "治愈", "音乐", "热血", "运动", "日系文艺", "人文"],
    dislikes: ["民国"], scale: ["剧集", "电影"], status: "优先已完结"
  };

  /* ---------- 本地存储 ---------- */
  var STORE_KEY = "maozhipian.v1";
  var state = load();
  function load() {
    var s;
    try { s = JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { s = {}; }
    s.fav = s.fav || {}; s.feedback = s.feedback || {};
    s.profile = s.profile || JSON.parse(JSON.stringify(DEFAULT_PROFILE));
    return s;
  }
  function save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) {} }

  /* ---------- 工具 ---------- */
  function el(id) { return document.getElementById(id); }
  function allBooks() { return DATA.picks.concat(DATA.candidates); }
  function headlineScore(b) { return (b.aiScore != null) ? b.aiScore : b.matchScore; }
  function sourceShort(b) {
    if (!b.source) return "—";
    if (b.source.indexOf("晋江") > -1) return "晋江";
    if (b.source.indexOf("豆瓣") > -1) return "豆瓣阅读";
    if (b.source.indexOf("番茄") > -1) return "番茄";
    return b.source;
  }
  function fmtWords(n) {
    if (!n) return "字数待核实";
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + " 万字";
    return n + " 字";
  }
  function dislikedGenres(b) {
    return (b.genres || []).filter(function (g) { return state.profile.dislikes.indexOf(g) > -1; });
  }

  /* ---------- 偏好选择器 ---------- */
  function renderPreferences() {
    el("principle").innerHTML = "★ " + DATA.taste.principle + "<br><span class='formula'>" + DATA.scoring.formula + "</span>";
    renderGenrePalette();
    renderScalePick();
    el("statusPick").value = state.profile.status;
    renderSummary();
    updateLearnNote();
  }
  function genreState(g) {
    if (state.profile.likes.indexOf(g) > -1) return "like";
    if (state.profile.dislikes.indexOf(g) > -1) return "dislike";
    return "";
  }
  function renderGenrePalette() {
    var box = el("genrePalette");
    box.innerHTML = PALETTE.map(function (g) {
      var st = genreState(g);
      return '<span class="chip pref ' + st + '" data-g="' + g + '">' +
        (st === "dislike" ? "✕ " : (st === "like" ? "✓ " : "")) + g + "</span>";
    }).join("");
    box.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () { cycleGenre(c.getAttribute("data-g")); });
    });
  }
  function cycleGenre(g) {
    var li = state.profile.likes.indexOf(g), di = state.profile.dislikes.indexOf(g);
    if (li === -1 && di === -1) { state.profile.likes.push(g); }          // 空 → 喜欢
    else if (li > -1) { state.profile.likes.splice(li, 1); state.profile.dislikes.push(g); } // 喜欢 → 不感冒
    else { state.profile.dislikes.splice(di, 1); }                         // 不感冒 → 空
    save(); renderGenrePalette(); renderSummary(); renderLists();
  }
  function renderScalePick() {
    var box = el("scalePick");
    box.innerHTML = SCALE_OPTS.map(function (s) {
      var on = state.profile.scale.indexOf(s) > -1;
      return '<span class="chip' + (on ? " on" : "") + '" data-s="' + s + '">' + s + "</span>";
    }).join("");
    box.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () {
        var s = c.getAttribute("data-s"), i = state.profile.scale.indexOf(s);
        if (i > -1) state.profile.scale.splice(i, 1); else state.profile.scale.push(s);
        save(); renderScalePick(); renderSummary();
      });
    });
  }
  function renderSummary() {
    var p = state.profile;
    var likes = p.likes.length ? "【" + p.likes.join(" · ") + "】" : "【还没选】";
    var html = "📌 <b>偏好总结</b>：你偏好 " + likes;
    if (p.dislikes.length) html += "，不太感冒 【" + p.dislikes.join(" · ") + "】";
    if (p.scale.length) html += "；体量看 【" + p.scale.join(" · ") + "】";
    if (p.status && p.status !== "不限") html += "；" + p.status;
    html += "。<br>选品始终：<b>好故事优先于题材</b>。";
    el("profileSummary").innerHTML = html;
  }
  function updateLearnNote() {
    var ups = 0, downs = 0;
    Object.keys(state.feedback).forEach(function (k) { if (state.feedback[k]) (state.feedback[k] === "up" ? ups++ : downs++); });
    var n = ups + downs;
    el("learnNote").textContent = n === 0
      ? "反馈学习：尚无反馈 —— 点赞 / 拍掉会持续校准"
      : "反馈学习：已记录 " + n + " 条（👍" + ups + " · 👎" + downs + "）→ 下周选品将据此调整";
  }

  /* ---------- 头部 ---------- */
  function renderHeader() {
    el("weekLabel").textContent = DATA.week.label + " · " + DATA.week.range;
    el("sourcePill").textContent = "本周来源：" + DATA.week.source;
    el("agentNoteText").innerHTML = mdBold(DATA.agentNote);
  }
  function mdBold(s) { return (s || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"); }

  /* ---------- 浏览筛选 ---------- */
  var filters = { genre: "", source: "", status: "", q: "", favOnly: false };
  function renderChipFilter(boxId, values, key) {
    var box = el(boxId);
    box.innerHTML = '<span class="chip" data-v="">全部</span>' +
      values.map(function (v) { return '<span class="chip" data-v="' + v + '">' + v + "</span>"; }).join("");
    box.querySelectorAll(".chip").forEach(function (c) {
      if (c.getAttribute("data-v") === filters[key]) c.classList.add("on");
      c.addEventListener("click", function () {
        filters[key] = c.getAttribute("data-v");
        box.querySelectorAll(".chip").forEach(function (x) { x.classList.remove("on"); });
        c.classList.add("on"); renderLists();
      });
    });
  }
  function renderGenreFilter() {
    var set = {};
    allBooks().forEach(function (b) { (b.genres || []).forEach(function (g) { set[g] = (set[g] || 0) + 1; }); });
    renderChipFilter("genreFilter", Object.keys(set).sort(function (a, b) { return set[b] - set[a]; }), "genre");
  }
  function renderSourceFilter() {
    var set = {};
    allBooks().forEach(function (b) { set[sourceShort(b)] = 1; });
    renderChipFilter("sourceFilter", Object.keys(set), "source");
  }
  function matchFilter(b) {
    if (filters.favOnly && !state.fav[b.id]) return false;
    if (filters.source && sourceShort(b) !== filters.source) return false;
    if (filters.status && (b.status || "").indexOf(filters.status) === -1) return false;
    if (filters.genre && (b.genres || []).indexOf(filters.genre) === -1) return false;
    if (filters.q) {
      var hay = (b.title + b.author + (b.genres || []).join("")).toLowerCase();
      if (hay.indexOf(filters.q.toLowerCase()) === -1) return false;
    }
    return true;
  }

  /* ---------- 双分条 ---------- */
  function scoresRow(b) {
    var story = (b.storyScore != null) ? b.storyScore : null;
    var storyBar = story != null ? '<span class="sval">' + story + "</span>" : '<span class="sval pending">待评估</span>';
    return '<div class="bc-scores">' +
      '<div class="sline"><span class="sk">故事力</span><span class="sbar"><i style="width:' + (story != null ? story : 0) + '%"></i></span>' + storyBar + "</div>" +
      '<div class="sline"><span class="sk">题材</span><span class="sbar match"><i style="width:' + b.matchScore + '%"></i></span><span class="sval">' + b.matchScore + "</span></div>" +
      "</div>";
  }

  /* ---------- 卡片 ---------- */
  function bookCard(b) {
    var tags = (b.genres || []).slice(0, 4).map(function (g) {
      var hit = (b.tier1Hits || []).indexOf(g) > -1;
      return '<span class="tag' + (hit ? " hit" : "") + '">' + g + "</span>";
    }).join("");
    var verified = b.linkVerified ? '<span class="ok">✓ 链接已核实</span>' : '<span class="warn">链接待核实</span>';
    var statusCls = (b.status || "").indexOf("完结") > -1 ? "ok" : "";
    var dis = dislikedGenres(b);
    var disFlag = dis.length ? '<span class="dis-flag">⚠ 含你不感冒：' + dis.join("、") + "</span>" : "";
    var fav = state.fav[b.id], fb = state.feedback[b.id];
    var badgeLabel = (b.aiScore != null) ? "综合" : "题材";
    return '<div class="book-card' + (dis.length ? " has-dis" : "") + '" data-id="' + b.id + '">' +
      '<div class="bc-top"><div><div class="bc-title">' + b.title + '<span class="src-tag">' + sourceShort(b) + "</span></div>" +
      '<div class="bc-author">' + b.author + "</div></div>" +
      '<div class="score-badge">' + headlineScore(b) + "<small>" + badgeLabel + "</small></div></div>" +
      scoresRow(b) +
      '<div class="bc-logline">' + (b.logline || "") + "</div>" +
      '<div class="bc-tags">' + tags + "</div>" + disFlag +
      '<div class="bc-meta"><span class="' + statusCls + '">' + (b.status || "状态待核实") + "</span>" +
      "<span>" + fmtWords(b.wordCount) + "</span>" + verified + "</div>" +
      '<div class="bc-actions">' +
      '<button class="act act-fav' + (fav ? " on-fav" : "") + '" data-act="fav" data-id="' + b.id + '">' + (fav ? "★ 已收藏" : "☆ 收藏") + "</button>" +
      '<button class="act act-up' + (fb === "up" ? " on-up" : "") + '" data-act="up" data-id="' + b.id + '">👍</button>' +
      '<button class="act act-down' + (fb === "down" ? " on-down" : "") + '" data-act="down" data-id="' + b.id + '">👎</button>' +
      "</div></div>";
  }
  function renderLists() {
    var weekly = DATA.picks.filter(matchFilter);
    var sortKey = function (b) { return (b.aiScore != null) ? b.aiScore : -1; };
    var cands = DATA.candidates.filter(matchFilter).sort(function (a, b) { return sortKey(b) - sortKey(a); });
    el("weeklyCards").innerHTML = weekly.map(bookCard).join("");
    el("candidateCards").innerHTML = cands.map(bookCard).join("");
    el("weeklyCount").textContent = "（" + weekly.length + "）";
    el("emptyState").hidden = (weekly.length + cands.length) > 0;
    bindCards();
  }
  function bindCards() {
    document.querySelectorAll(".book-card").forEach(function (card) {
      card.addEventListener("click", function (e) {
        if (e.target.closest(".act")) return;
        openModal(card.getAttribute("data-id"));
      });
    });
    document.querySelectorAll(".book-card .act").forEach(function (btn) {
      btn.addEventListener("click", function (e) { e.stopPropagation(); toggle(btn.getAttribute("data-id"), btn.getAttribute("data-act")); renderLists(); });
    });
  }
  function toggle(id, act) {
    if (act === "fav") { state.fav[id] = !state.fav[id]; }
    else { state.feedback[id] = (state.feedback[id] === act) ? undefined : act; }
    save(); updateLearnNote();
  }

  /* ---------- 详情弹窗 ---------- */
  function listHTML(arr) { return "<ul>" + (arr || []).map(function (x) { return "<li>" + x + "</li>"; }).join("") + "</ul>"; }
  function fact(k, v) { return '<div class="m-fact"><span class="k">' + k + '</span><span class="v">' + (v || "—") + "</span></div>"; }
  function section(title, inner) { return '<div class="m-section"><h4>' + title + "</h4>" + inner + "</div>"; }

  function openModal(id) {
    var b = allBooks().filter(function (x) { return x.id === id; })[0];
    if (!b) return;
    var storyNum = (b.storyScore != null) ? b.storyScore : "待评估";
    var chars = (b.characters && b.characters.length)
      ? '<div class="m-section m-chars"><h4>主要人物</h4><ul>' +
        b.characters.map(function (c) { return "<li><strong>" + c.name + "</strong> —— " + c.role + "</li>"; }).join("") + "</ul></div>" : "";
    var dataNote = b.dataNote ? '<div class="data-note">⚠ ' + b.dataNote + "</div>" : "";
    var linkCls = b.linkVerified ? "m-link" : "m-link unverified";
    var linkText = b.linkVerified ? "前往原文 →" : "在平台搜索此书 →";

    el("modalBody").innerHTML =
      '<div class="m-head"><div><div class="m-title">' + b.title + '<span class="src-tag">' + sourceShort(b) + "</span></div>" +
      '<div class="m-author">' + b.author + " · " + (b.source || "") + "</div></div>" +
      '<div class="score-badge" style="width:56px;height:56px;font-size:22px">' + headlineScore(b) + "<small>" + (b.aiScore != null ? "综合" : "题材") + "</small></div></div>" +
      '<p class="m-logline">「' + (b.logline || "") + "」</p>" +
      '<div class="m-story"><div class="m-story-head"><span class="m-story-label">好故事判断</span>' +
      '<span class="m-story-score">故事力 <b>' + storyNum + '</b></span>' +
      '<span class="m-story-score match">题材 <b>' + b.matchScore + "</b></span></div>" +
      "<p>" + (b.storyVerdict || "") + "</p></div>" +
      '<div class="m-section"><div class="m-grid">' +
      fact("状态", b.status) + fact("篇幅", fmtWords(b.wordCount)) +
      fact("建议体量", b.scale) + fact("调性", b.tone) +
      fact("热度", b.heat || "—") + fact("题材匹配说明", b.matchNote || "—") +
      "</div></div>" +
      '<div class="m-section"><h4>故事梗概</h4><p>' + (b.synopsis || "") + "</p></div>" +
      chars +
      section("改编亮点", listHTML(b.highlights)) +
      section("改编难点 / 风险", listHTML(b.challenges)) +
      section("对标作品", '<div class="m-bench">' + (b.benchmarks || []).map(function (x) { return '<span class="tag">' + x + "</span>"; }).join("") + "</div>") +
      section("为什么是现在", "<p>" + (b.whyNow || "") + "</p>") +
      section("版权状态线索", "<p>" + (b.rightsClue || "") + "</p>") +
      section("智能体总评", "<p>" + (b.verdict || "") + "</p>") +
      dataNote +
      '<a class="' + linkCls + '" href="' + b.url + '" target="_blank" rel="noopener">' + linkText + "</a>" +
      '<div class="m-actions">' +
      '<button class="act act-fav' + (state.fav[b.id] ? " on-fav" : "") + '" data-act="fav" data-id="' + b.id + '">' + (state.fav[b.id] ? "★ 已收藏" : "☆ 收藏") + "</button>" +
      '<button class="act act-up' + (state.feedback[b.id] === "up" ? " on-up" : "") + '" data-act="up" data-id="' + b.id + '">👍 想做</button>' +
      '<button class="act act-down' + (state.feedback[b.id] === "down" ? " on-down" : "") + '" data-act="down" data-id="' + b.id + '">👎 拍掉</button>' +
      "</div>";
    el("modalBody").querySelectorAll(".act").forEach(function (btn) {
      btn.addEventListener("click", function () { var bid = btn.getAttribute("data-id"); toggle(bid, btn.getAttribute("data-act")); renderLists(); openModal(bid); });
    });
    el("modal").hidden = false;
  }
  function closeModal() { el("modal").hidden = true; }

  /* ---------- 事件 ---------- */
  el("modalClose").addEventListener("click", closeModal);
  el("modal").addEventListener("click", function (e) { if (e.target === el("modal")) closeModal(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
  el("searchBox").addEventListener("input", function (e) { filters.q = e.target.value; renderLists(); });
  el("statusFilter").addEventListener("change", function (e) { filters.status = e.target.value; renderLists(); });
  el("favOnly").addEventListener("change", function (e) { filters.favOnly = e.target.checked; renderLists(); });
  el("statusPick").addEventListener("change", function (e) { state.profile.status = e.target.value; save(); renderSummary(); });
  el("resetPref").addEventListener("click", function () {
    state.profile = JSON.parse(JSON.stringify(DEFAULT_PROFILE));
    save(); renderPreferences(); renderLists();
  });

  /* ---------- 启动 ---------- */
  renderPreferences();
  renderHeader();
  renderGenreFilter();
  renderSourceFilter();
  renderLists();
})();
