#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 本地 AI 后端
#   - 服务静态站点 app/（同源，无 CORS）
#   - POST /api/discover：把用户口味+需求 + 候选库 喂给 DeepSeek，返回真实的选品+改编分析
# key 从环境变量 DEEPSEEK_API_KEY 或 backend/.env 读取，绝不写进代码/仓库。
import http.server, socketserver, os, sys, json, urllib.request, urllib.error
import threading, time
import scraper  # 同目录的实时抓取模块（豆瓣/晋江/番茄 → 新书候选）
import db        # 同目录的数据层（SQLite：真账号 + 收藏 + 点赞 + 偏好）

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(os.path.dirname(ROOT), "app")
CORPUS_PATH = os.path.join(ROOT, "corpus.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
# 最新最强模型。旧名 deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用。
# v4-pro 带"思考(reasoning)"能力，文学判断更强；想省钱换快速版改成 "deepseek-v4-flash" 即可。
MODEL = "deepseek-v4-pro"

# 每周自动刷新候选池：常驻服务器时，后台守护线程按"周"重抓三源，让"本周精选"自动更新。
# 默认 7 天，可用环境变量 ZPM_REFRESH_DAYS 调（如部署初期想勤一点，设 1 就是每天）。
REFRESH_DAYS = float(os.environ.get("ZPM_REFRESH_DAYS") or 7)
REFRESH_INTERVAL = max(600, REFRESH_DAYS * 24 * 3600)  # 下限 10 分钟，防误填 0 把自己打爆
_refresh = {"last": 0.0, "count": 0, "busy": False}  # 最近刷新时间/条数/是否在抓


def refresh_pool(reason):
    """重抓三源、刷新候选池缓存。后台线程按周调；启动时先暖一次让首个用户不用等抓取。"""
    if _refresh["busy"]:
        return
    _refresh["busy"] = True
    try:
        books = scraper.scrape_all(force=True)
        _refresh["last"] = time.time()
        _refresh["count"] = len(books)
        print("  [刷新] %s完成：候选池 %d 本" % (reason, len(books)))
    except Exception as e:
        print("  [刷新] %s失败：%r" % (reason, e))
    finally:
        _refresh["busy"] = False


def refresh_loop():
    """后台守护线程：启动 4 秒后先暖一次缓存，之后每隔 REFRESH_INTERVAL 重抓一次。"""
    time.sleep(4)
    refresh_pool("启动预热")
    while True:
        time.sleep(REFRESH_INTERVAL)
        refresh_pool("每周自动")


def load_key():
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    txt = os.path.join(ROOT, "key.txt")  # 给非技术用户的简单方式：可见文件，直接粘 key
    if os.path.exists(txt):
        val = open(txt, encoding="utf-8").read().strip()
        if val and "粘贴" not in val:
            return val
    envp = os.path.join(ROOT, ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and line.split("=", 1)[0].strip() == "DEEPSEEK_API_KEY":
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                return "" if (not val or "粘贴" in val) else val
    return ""


def load_corpus():
    try:
        return json.load(open(CORPUS_PATH, encoding="utf-8"))
    except Exception:
        return []


SYSTEM_PROMPT = (
    "你是「制片帽」的影视选题分析师，服务于影视制片人。核心原则：好故事 > 题材（故事质量是硬门槛，题材是软偏好）。\n"
    "任务：从给定的【候选作品库】里，为这位制片人挑出最合适的若干部，并为每部生成贴合他本次需求的改编分析。\n"
    "要求：\n"
    "- 只能从候选库里选，绝不编造书目；用候选的 id 指代。\n"
    "- 综合分 = 故事力×0.6 + 题材×0.4。题材命中但故事平庸要降权；题材不完全对但故事极好可加权。\n"
    "- 特别重视用户的「自定义需求」，据此挑选并解释。\n"
    "- 【硬性要求】即使候选库里没有完全贴合需求的，也必须挑出 3-5 部**最接近**的（按相似度从高到低），"
    "并在 fitReason / matchNote 里老实说明它跟需求像在哪、差在哪。**绝不返回空 picks**。\n"
    "- 诚实：改编难点、过审风险、体量问题都要点出，不吹。\n"
    "- 只输出 JSON，不要任何多余文字、不要 markdown 代码块。\n"
    "输出 JSON 结构：\n"
    '{"note":"一句话本次选品说明(口语，给制片人看，可点出本次按了哪些需求)",'
    '"picks":[{"id":"候选id","matchScore":0到100整数,"storyScore":0到100整数,'
    '"logline":"一句话故事","fitReason":"为什么这部贴合他这次的需求(2-3句，直接回应自定义需求)",'
    '"storyVerdict":"作为故事好不好的直话","highlights":["改编亮点"],"challenges":["改编难点/风险"],'
    '"benchmarks":["对标已播影视"],"whyNow":"为什么是现在/势头","matchNote":"题材匹配说明"}]}'
)


def build_user_msg(profile, corpus, count):
    p = profile or {}
    lib = [{
        "id": w["id"], "title": w["title"], "author": w.get("author"),
        "genres": w.get("genres"), "classification": w.get("classification"),
        "status": w.get("status"), "wordCount": w.get("wordCount"),
        "tone": w.get("tone"), "synopsis": w.get("synopsis"),
    } for w in corpus]
    return (
        "制片人口味：\n"
        + "喜欢题材：" + "、".join(p.get("likes", []) or ["(未填)"]) + "\n"
        + "不感冒：" + "、".join(p.get("dislikes", []) or ["(无)"]) + "\n"
        + "体量偏好：" + "、".join(p.get("scale", []) or ["(不限)"]) + "；完结状态：" + str(p.get("status", "不限")) + "\n"
        + "【自定义需求 · 最重要】：" + (p.get("customWants", "").strip() or "（无，按题材口味挑即可）") + "\n\n"
        + "下面的候选库是【刚从各文学网站实时抓来的新书】（已粗筛掉明显影视化/已售的），多为还没被大公司买走的早期作品。\n"
        + "请从中挑 3-5 部最值得改编的，按综合分从高到低；务必逐部读懂 synopsis(文案) 再判断故事力，别只看题材标签。\n"
        + "即使没有完全贴合自定义需求的，也要给出最接近的 3-5 部，并在每部里说明差距。绝不空手而归。\n\n"
        + "候选作品库(JSON)：\n" + json.dumps(lib, ensure_ascii=False)
    )


def call_deepseek(key, system, user):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        # v4-pro 是思考型模型：temperature 不报错但官方说可能不生效；保留以便将来切非思考模型仍适用。
        "temperature": 0.7,
        # JSON 模式：要求 prompt 含 "json" 字样并给样例——我们的 system/user prompt 都满足。
        "response_format": {"type": "json_object"},
        # ⚠️ 对思考型模型，max_tokens「包含思考链 + 最终回答」。调高到 8000 给思考留空间，否则 JSON 易被截断。
        "max_tokens": 8000,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=150) as r:
        data = json.loads(r.read().decode("utf-8"))
    # 思考型模型会另有 reasoning_content 字段；我们只取最终 content（即那段 JSON）。
    content = (data["choices"][0]["message"].get("content") or "").strip()
    if not content:
        raise ValueError("DeepSeek 返回了空内容（官方说明偶发，可重试一次）")
    return content


def gather_pool(profile, base_corpus, want=24):
    """组候选池：三源【按配额】取，保证晋江/番茄/豆瓣都进到大模型眼前。
    晋江/番茄自动抓（robots 允许）；豆瓣以本地精选纳入（其书单接口 /j/ 被 robots 禁，不自动抓）。
    为什么按配额：晋江一次 400 本，若直接混排会把番茄、豆瓣全挤出前 24——配额保证三源都露脸。"""
    try:
        scraped = scraper.scrape_all()
    except Exception as e:
        print("   [discover] 实时抓取异常：", repr(e))
        scraped = []
    by = lambda s: [b for b in scraped if s in (b.get("source") or "")]
    db, jj, fq = by("豆瓣"), by("晋江"), by("番茄")
    # 豆瓣为主力：豆瓣14 + 晋江6 + 番茄4。豆瓣多为连载——按帽帽要求放宽"完结"门槛，别筛掉。
    db_profile = dict(profile)
    db_profile["status"] = "不限"
    quota = (scraper.filter_for_profile(db, db_profile, limit=14)
             + scraper.filter_for_profile(jj, profile, limit=6)
             + scraper.filter_for_profile(fq, profile, limit=4))
    seen, merged = set(), []
    for b in quota:
        t = (b.get("title") or "").strip()
        if t and t not in seen:
            seen.add(t)
            merged.append(b)
    if len(merged) < 3:  # 兜底：抓取失败时用本地精选，别让用户点了个寂寞
        for b in (scraper.filter_for_profile(list(base_corpus), profile, limit=want) or list(base_corpus)):
            t = (b.get("title") or "").strip()
            if t and t not in seen:
                seen.add(t)
                merged.append(b)
    return merged[:want], len(scraped)


def discover(profile, count=3):
    key = load_key()
    if not key:
        return {"ok": False, "error": "未配置 DEEPSEEK_API_KEY —— 请在 backend/key.txt 里填好你的 key"}
    pool, n_scraped = gather_pool(profile, load_corpus())
    if not pool:
        return {"ok": False, "error": "候选池为空：实时抓取没拿到、本地候选库也读不到"}
    try:
        raw = call_deepseek(key, SYSTEM_PROMPT, build_user_msg(profile, pool, count))
        out = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        return {"ok": False, "error": "DeepSeek 接口报错 %d：%s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "error": "调用失败：" + str(e)}

    byid = {w["id"]: w for w in pool}
    picks = []
    for it in (out.get("picks") or []):
        base = byid.get(it.get("id"))
        if not base:
            continue
        w = dict(base)  # 真实事实基底（书名/作者/链接/字数/梗概来自候选库，不让模型编造）
        ms = it.get("matchScore", 70)
        ss = it.get("storyScore")
        w["matchScore"] = ms
        w["storyScore"] = ss
        w["aiScore"] = round(ss * 0.6 + ms * 0.4) if isinstance(ss, (int, float)) else ms
        for k_out, k_in in [("logline", "logline"), ("storyVerdict", "storyVerdict"),
                            ("highlights", "highlights"), ("challenges", "challenges"),
                            ("benchmarks", "benchmarks"), ("whyNow", "whyNow"), ("matchNote", "matchNote")]:
            if it.get(k_in) is not None:
                w[k_out] = it[k_in]
        w["verdict"] = it.get("fitReason", "")  # 本次为何贴合（智能体的总评）
        w["live"] = True
        picks.append(w)
    return {"ok": True, "picks": picks, "note": out.get("note", ""), "model": MODEL,
            "scrapedCount": n_scraped, "poolSize": len(pool)}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=APP_DIR, **k)

    def log_message(self, fmt, *args):
        pass  # 安静

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ---------- 小工具 ----------
    def _json(self, obj, code=200):
        out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def _token(self):
        h = self.headers.get("Authorization", "")
        return h[7:].strip() if h.startswith("Bearer ") else ""

    def _user(self):
        return db.user_by_token(self._token())

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json({"ok": True, "service": "制片帽", "model": MODEL,
                               "poolCount": _refresh["count"], "lastRefresh": _refresh["last"]})
        if path == "/api/me":  # 凭 token 还原登录态（账号 + 收藏 + 点赞 + 偏好）
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "未登录"}, 401)
            st = db.get_user_state(u["id"])
            return self._json({"ok": True, "username": u["username"],
                               "profile": db.get_profile(u["id"]),
                               "fav": st["fav"], "feedback": st["feedback"]})
        return super().do_GET()  # 其余交给静态文件服务

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()

        # 账号（无需登录）
        if path == "/api/register":
            r, err = db.register(body.get("username"), body.get("password"))
            return self._json({"ok": bool(r), "error": err, "token": (r or {}).get("token"),
                               "username": (r or {}).get("username")}, 200 if r else 400)
        if path == "/api/login":
            r, err = db.login(body.get("username"), body.get("password"))
            return self._json({"ok": bool(r), "error": err, "token": (r or {}).get("token"),
                               "username": (r or {}).get("username")}, 200 if r else 400)
        if path == "/api/logout":
            db.logout(self._token())
            return self._json({"ok": True})

        # 需登录：收藏 / 点赞 / 存偏好
        if path in ("/api/favorite", "/api/feedback", "/api/profile"):
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "登录已失效，请重新登录"}, 401)
            if path == "/api/favorite":
                db.set_favorite(u["id"], body.get("bookId"), body.get("book"), bool(body.get("on")))
            elif path == "/api/feedback":
                db.set_feedback(u["id"], body.get("bookId"), body.get("value"))
            else:
                db.save_profile(u["id"], body.get("profile") or {})
            return self._json({"ok": True})

        # 选片（开放：登录与否都能用；真账号只影响收藏/偏好持久化）
        if path == "/api/discover":
            print(">> /api/discover  自定义需求:", (body.get("profile") or {}).get("customWants", "")[:40])
            result = discover(body.get("profile", {}), int(body.get("count", 5) or 5))
            if result.get("ok"):
                result["lastRefresh"] = _refresh["last"]  # 给前端显示"本周更新于…"
                print("   ✓ 实时抓取 %d 本 → 候选池 %d → 返回 %d 部" % (
                    result.get("scrapedCount", 0), result.get("poolSize", 0), len(result.get("picks", []))))
            else:
                print("   ✗", result.get("error"))
            return self._json(result, 200 if result.get("ok") else 400)

        self._json({"ok": False, "error": "未知接口"}, 404)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)  # 让日志实时刷出（常驻/被管道时不被缓冲攒着）
    except Exception:
        pass
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT") or 4173)
    db.init()
    has_key = "✓ 已配置" if load_key() else "✗ 未配置(去 backend/key.txt 填)"
    print("制片帽 · 后端启动")
    print("  网址      http://localhost:%d" % PORT)
    print("  模型      %s" % MODEL)
    print("  DeepSeek key      %s" % has_key)
    print("  候选库    本地 %d 部 + 豆瓣/晋江/番茄 实时抓取" % len(load_corpus()))
    print("  数据库    %s（已注册 %d 人）" % (os.path.basename(db.DB_PATH), db.stats()["users"]))
    print("  自动刷新  每 %g 天重抓一次候选池（启动后约 4 秒先暖一次）" % REFRESH_DAYS)
    # 缓存有效期设成刷新周期：用户点选片永远命中缓存（秒回），只有后台线程去真抓
    scraper.set_cache_ttl(REFRESH_INTERVAL)
    threading.Thread(target=refresh_loop, daemon=True).start()
    # 多线程：支持多人同时用（单线程会被一次 100 秒的选片堵死）
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
