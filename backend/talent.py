#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 制作板块 / 行业人才库（中国影视幕后 & 演职人员数据库）
#
# ⚠️ 归属：本文件是【制作 context】专属，经 production.py 的 /api/production/talent/* 子路由进来，
#    server.py 一行都不碰（与并行开发的发行板块互不干扰）。
#
# 数据原则（帽帽铁律「宁缺毋假」）：
#   人才库里展示的一切履历——作品年表、每部的工种、获奖记录、生平——【全部】来自豆瓣真实接口，
#   零 AI 编造。DeepSeek 在这条线里完全不参与。头部/腰部是用真实信号（作品数、高分作品数、活跃年份）
#   透明计算出来的"估算"，明确标注算法，用户可手动改。
#
# 数据从哪来（实测可用的豆瓣移动端 rexxar 接口，电脑版职员页已被 JS 反爬封死，故走"以人为中心"）：
#   - 人物信息  GET m.douban.com/rexxar/api/v2/celebrity/{id}          → 姓名/性别/生日/出生地/头像/职业
#   - 作品年表  GET m.douban.com/rexxar/api/v2/celebrity/{id}/works    → 每部作品 + 此人在该片的精确工种
#   - 获奖履历  GET m.douban.com/rexxar/api/v2/celebrity/{id}/awards   → 获奖记录
#   - 影视枚举  GET movie.douban.com/j/new_search_subjects             → 按年份/类型/地区列片（喂种子爬取）
#   封查风险：帽帽已拍板接受。缓解 = 移动 UA + 合理 Referer + 严格限速（请求间 sleep）+ 后台低频爬。
import os
import re
import json
import time
import random
import sqlite3
import threading
import contextlib
import urllib.request
import urllib.error
import urllib.parse

import db as account_db  # 只读复用真账号会话（user_by_token）；不动它的表

ROOT = os.path.dirname(os.path.abspath(__file__))
_zpm_db = os.environ.get("ZPM_DB") or ""
DB_PATH = (os.environ.get("ZPM_TALENT_DB")
           or os.path.join(os.path.dirname(_zpm_db) if _zpm_db else ROOT, "talent.db"))

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REXXAR = "https://m.douban.com/rexxar/api/v2"

_lock = threading.Lock()
_init_done = False
_last_call = [0.0]          # 全局上次豆瓣请求时间戳，做最小间隔限速
_MIN_GAP = 1.5              # 两次豆瓣请求最小间隔（秒），降封查风险

# 工种归类：把豆瓣五花八门的 role（"美术 - 美术指导"/"摄影"/"导演"）归到大类，方便按工种搜。
# key 是大类（展示+搜索用），value 是该类的关键词（命中即归类）。
PRO_CATEGORIES = [
    ("导演", ["导演"]),
    ("编剧", ["编剧", "剧本", "故事"]),
    ("演员", ["演员", "主演"]),
    ("摄影", ["摄影"]),
    ("美术", ["美术", "置景", "场景设计"]),
    ("造型", ["造型", "服装", "化妆", "服化"]),
    ("录音", ["录音", "声音", "音效"]),
    ("剪辑", ["剪辑", "剪接"]),
    ("作曲", ["作曲", "音乐", "配乐", "原声"]),
    ("制片人", ["制片人", "制片", "出品", "监制", "制作人"]),
    ("动作指导", ["动作", "武术", "武指", "动作指导"]),
    ("视觉特效", ["特效", "视效", "vfx", "视觉"]),
    ("灯光", ["灯光"]),
    ("副导演", ["副导演", "执行导演"]),
    ("配音", ["配音"]),
]
# 给前端做工种快捷筛选用的顺序（幕后优先，符合制片找人的语境）
PRO_ORDER = ["导演", "编剧", "摄影", "美术", "造型", "录音", "剪辑", "作曲",
             "制片人", "动作指导", "视觉特效", "灯光", "副导演", "演员", "配音"]


# ============================================================ 数据层
@contextlib.contextmanager
def _db():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    global _init_done
    if _init_done:
        return
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS people(
            id          TEXT PRIMARY KEY,   -- 'db<豆瓣celebrity id>' 或 'm<随机>'（手动录入）
            douban_id   TEXT,               -- 豆瓣 celebrity id（手动录入为空）
            name        TEXT NOT NULL,
            avatar      TEXT,
            gender      TEXT, birthday TEXT, birthplace TEXT, imdb TEXT,
            professions TEXT,               -- JSON: ["摄影","导演"]（大类，去重）
            pro_text    TEXT,               -- 职业大类拼成的可 LIKE 文本，搜索用
            works       TEXT,               -- JSON: [{title,year,role,roleCat,rating,url,directors}]
            awards      TEXT,               -- JSON: 获奖记录原样
            work_count  INTEGER, good_count INTEGER, top_count INTEGER, last_year INTEGER,
            tier_auto   TEXT,               -- 头部/腰部/新锐（真实信号算出，可被 tier 覆盖）
            tier        TEXT,               -- 用户手动定级（空则用 tier_auto）
            source      TEXT,               -- 'douban' | 'manual'
            owner       TEXT,               -- 录入者（'u:id'/'d:dev'）；douban 公共条目可为空
            is_private  INTEGER DEFAULT 0,  -- 1=仅录入者可见（私人新人脉）
            note        TEXT,               -- 公共备注
            created     REAL, updated REAL
        );
        CREATE TABLE IF NOT EXISTS contacts(
            owner       TEXT NOT NULL, person_id TEXT NOT NULL,
            contact     TEXT,               -- 私人联系方式（微信/电话/经纪人），仅自己可见
            note        TEXT,               -- 私人备注
            created     REAL,
            PRIMARY KEY(owner, person_id)
        );
        CREATE INDEX IF NOT EXISTS idx_people_pro ON people(pro_text);
        """)
    _init_done = True
    _ensure_daily_crawler()


def _now():
    return time.time()


def _resolve_owner(handler, src):
    tok = handler._token()
    device = (src.get("device") or "").strip()[:64]
    u = account_db.user_by_token(tok) if tok else None
    if u:
        return "u:%d" % u["id"]
    return ("d:" + device) if device else None


# ============================================================ 豆瓣抓取
def _throttle():
    """全局限速：保证两次豆瓣请求间隔 >= _MIN_GAP，外加一点随机抖动，别太机械。"""
    with _lock:
        gap = _now() - _last_call[0]
        wait = _MIN_GAP - gap
    if wait > 0:
        time.sleep(wait + random.uniform(0, 0.6))
    _last_call[0] = _now()


def _fetch_json(url, referer):
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": UA_MOBILE, "Referer": referer})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _role_category(role):
    """把一个具体 role 文本归到大类。'摄影 - 摄影指导' → '摄影'。"""
    head = re.split(r"[\s\-—－·/]", role.strip())[0] if role else ""
    text = role or ""
    for cat, kws in PRO_CATEGORIES:
        if head == cat:
            return cat
        for kw in kws:
            if kw in text:
                return cat
    return head or "其他"


def fetch_person(douban_id):
    """抓一个豆瓣 celebrity 的全套真实档案。返回 dict（未入库）或抛异常。"""
    pid = str(douban_id).strip()
    info = _fetch_json("%s/celebrity/%s" % (REXXAR, pid),
                       "https://m.douban.com/personage/%s/" % pid)
    name = (info.get("title") or "").strip()
    if not name:
        raise ValueError("没取到这个人的姓名，可能 id 不对或不是 /celebrity/ 链接")
    extra = info.get("extra") or {}
    kv = {k: v for k, v in (extra.get("info") or [])}
    cover = info.get("cover") or {}
    avatar = ""
    if isinstance(cover, dict):
        avatar = (((cover.get("large") or {}).get("url")) or
                  ((cover.get("normal") or {}).get("url")) or "")
    avatar = avatar or info.get("cover_img") or ""

    # 作品年表（翻页拿全，封顶 80 部够用，控制请求量）
    works, roles_cat = [], {}
    start, total = 0, 1
    while start < total and start < 80:
        wj = _fetch_json("%s/celebrity/%s/works?start=%d&count=40" % (REXXAR, pid, start),
                         "https://m.douban.com/personage/%s/" % pid)
        total = wj.get("total") or 0
        batch = wj.get("works") or []
        if not batch:
            break
        for it in batch:
            w = it.get("work") or {}
            role = "、".join(it.get("roles") or []) or "—"
            cat = _role_category((it.get("roles") or ["—"])[0])
            roles_cat[cat] = roles_cat.get(cat, 0) + 1
            rating = ((w.get("rating") or {}).get("value")) or 0
            works.append({
                "title": w.get("title") or "", "year": w.get("year") or "",
                "role": role, "roleCat": cat, "rating": rating,
                "url": w.get("url") or "", "type": w.get("type") or "",
                "directors": [d.get("name") for d in (w.get("directors") or [])][:3],
            })
        start += len(batch)

    # 职业大类：作品里出现过的工种（按出现次数排序），叠加 short_info 里的标注
    pros = [c for c, _ in sorted(roles_cat.items(), key=lambda kv: -kv[1]) if c != "其他"]
    for p in re.split(r"[\s/、]+", extra.get("short_info", "").split("/")[0] if extra.get("short_info") else ""):
        cat = _role_category(p)
        if cat and cat != "其他" and cat not in pros:
            pros.append(cat)

    # 获奖履历
    awards = []
    try:
        aj = _fetch_json("%s/celebrity/%s/awards" % (REXXAR, pid),
                         "https://m.douban.com/personage/%s/" % pid)
        awards = aj.get("awards") or []
    except Exception:
        pass

    rec = {
        "id": "db" + pid, "douban_id": pid, "name": name, "avatar": avatar,
        "gender": kv.get("性别", ""), "birthday": kv.get("出生日期", "") or kv.get("生日", ""),
        "birthplace": kv.get("出生地", ""), "imdb": kv.get("IMDb编号", "") or kv.get("IMDb", ""),
        "professions": pros, "works": works, "awards": awards,
        "source": "douban", "sharing_url": info.get("url") or ("https://movie.douban.com/celebrity/%s/" % pid),
    }
    _attach_tier(rec)
    return rec


def _attach_tier(rec):
    """用真实信号透明地算头部/腰部/新锐——不让 AI 拍脑袋。"""
    works = rec.get("works") or []
    wc = len(works)
    good = sum(1 for w in works if (w.get("rating") or 0) >= 7.0)
    top = sum(1 for w in works if (w.get("rating") or 0) >= 8.0)
    years = [int(re.sub(r"\D", "", str(w.get("year"))) or 0) for w in works]
    years = [y for y in years if y > 1900]
    last_year = max(years) if years else 0
    if top >= 3 or (wc >= 15 and good >= 6):
        tier = "头部"
    elif wc >= 4 or top >= 1:
        tier = "腰部"
    else:
        tier = "新锐"
    rec.update({"work_count": wc, "good_count": good, "top_count": top,
                "last_year": last_year, "tier_auto": tier})


def _upsert_person(rec, owner=None, is_private=0):
    pros = rec.get("professions") or []
    pro_text = " ".join(pros)
    now = _now()
    with _db() as c:
        exist = c.execute("SELECT tier, note, created FROM people WHERE id=?", (rec["id"],)).fetchone()
        tier = exist["tier"] if exist else None       # 保留用户手动定级
        note = exist["note"] if exist else (rec.get("note") or "")
        created = exist["created"] if exist else now
        c.execute("""INSERT OR REPLACE INTO people(
            id,douban_id,name,avatar,gender,birthday,birthplace,imdb,professions,pro_text,
            works,awards,work_count,good_count,top_count,last_year,tier_auto,tier,source,owner,
            is_private,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            rec["id"], rec.get("douban_id"), rec["name"], rec.get("avatar"), rec.get("gender"),
            rec.get("birthday"), rec.get("birthplace"), rec.get("imdb"),
            json.dumps(pros, ensure_ascii=False), pro_text,
            json.dumps(rec.get("works") or [], ensure_ascii=False),
            json.dumps(rec.get("awards") or [], ensure_ascii=False),
            rec.get("work_count", 0), rec.get("good_count", 0), rec.get("top_count", 0),
            rec.get("last_year", 0), rec.get("tier_auto", "新锐"), tier, rec.get("source", "douban"),
            owner if owner is not None else rec.get("owner"), is_private, note, created, now))
    return rec["id"]


# ============================================================ 种子爬取 / 每日增量
_crawl = {"busy": False, "done": 0, "added": 0, "msg": "", "started": 0}
_daily_started = False


def _enum_films(year, kind="电视剧", start=0, limit=20):
    """枚举某年中国大陆某类影视（豆瓣发现接口，返回 [{id,title}]）。"""
    tag = urllib.parse.quote(kind)
    url = ("https://movie.douban.com/j/new_search_subjects?sort=T&range=0,10&tags=%s"
           "&countries=%s&year_range=%d,%d&start=%d"
           % (tag, urllib.parse.quote("中国大陆"), year, year, start))
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": UA_DESKTOP, "Referer": "https://movie.douban.com/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode("utf-8", "ignore")).get("data", [])
    return [{"id": d["id"], "title": d.get("title")} for d in data[:limit]]


def _film_people(subject_id, kind="tv"):
    """一部片的可见演职人员（导演组+演员，含编剧/剪辑/摄影等混合工种者）。返回 [(id,name)]。"""
    j = _fetch_json("%s/%s/%s/celebrities" % (REXXAR, kind, subject_id),
                    "https://m.douban.com/movie/subject/%s/" % subject_id)
    out = []
    for grp in ("directors", "actors"):
        for c in (j.get(grp) or []):
            if c.get("id") and c.get("name"):
                out.append((c["id"], c["name"]))
    return out


def crawl_seed(years, kinds, films_per=12, people_cap=400, reason="种子爬取"):
    """从近年影视枚举 → 收集演职人员 id → 逐个抓其完整作品档案入库。
    全程限速（_throttle），后台线程跑。people_cap 控制单次上限，防一次爬太久/太多请求。"""
    if _crawl["busy"]:
        return
    _crawl.update({"busy": True, "done": 0, "added": 0, "msg": "开始" + reason + "…", "started": _now()})
    try:
        seen_people = set()
        with _db() as c:
            for r in c.execute("SELECT douban_id FROM people WHERE douban_id IS NOT NULL"):
                seen_people.add(r["douban_id"])
        pending = []
        for year in years:
            for kind in kinds:
                dkind = "tv" if kind in ("电视剧", "电视剧集", "剧集") else "movie"
                try:
                    films = _enum_films(year, kind, 0, films_per)
                except Exception as e:
                    _crawl["msg"] = "枚举 %d %s 失败：%s" % (year, kind, e)
                    continue
                for f in films:
                    try:
                        for pid, name in _film_people(f["id"], dkind):
                            if pid not in seen_people:
                                seen_people.add(pid)
                                pending.append(pid)
                    except Exception:
                        continue
                    _crawl["msg"] = "%s：已扫 %d 片，待抓 %d 人" % (reason, _crawl["done"], len(pending))
        # 逐人抓档案
        for pid in pending:
            if len(([1] * _crawl["added"])) >= people_cap:
                break
            try:
                rec = fetch_person(pid)
                _upsert_person(rec)
                _crawl["added"] += 1
            except Exception:
                pass
            _crawl["done"] += 1
            _crawl["msg"] = "%s：已入库 %d 人" % (reason, _crawl["added"])
        _crawl["msg"] = "%s完成：新增/更新 %d 人" % (reason, _crawl["added"])
    finally:
        _crawl["busy"] = False


def _daily_loop():
    """每日增量：爬当年新片的演职人员。常驻服务器才生效；首个 talent 请求时惰性启动。"""
    import datetime
    time.sleep(30)
    while True:
        try:
            year = datetime.date.today().year
            crawl_seed([year], ["电视剧", "电影"], films_per=10, people_cap=200, reason="每日增量")
        except Exception as e:
            print("  [人才库] 每日增量异常：%r" % e)
        time.sleep(24 * 3600)


def _ensure_daily_crawler():
    global _daily_started
    if _daily_started:
        return
    _daily_started = True
    if os.environ.get("ZPM_TALENT_DAILY", "1") != "0":
        threading.Thread(target=_daily_loop, daemon=True).start()


# ============================================================ 序列化
def _brief(row):
    return {
        "id": row["id"], "name": row["name"], "avatar": row["avatar"],
        "professions": json.loads(row["professions"] or "[]"),
        "tier": row["tier"] or row["tier_auto"], "tierAuto": row["tier_auto"],
        "workCount": row["work_count"], "goodCount": row["good_count"],
        "topCount": row["top_count"], "lastYear": row["last_year"],
        "source": row["source"], "isPrivate": row["is_private"],
    }


def _full(row, owner):
    d = _brief(row)
    d.update({
        "doubanId": row["douban_id"], "gender": row["gender"], "birthday": row["birthday"],
        "birthplace": row["birthplace"], "imdb": row["imdb"],
        "works": json.loads(row["works"] or "[]"), "awards": json.loads(row["awards"] or "[]"),
        "note": row["note"], "owner": row["owner"],
        "doubanUrl": ("https://movie.douban.com/celebrity/%s/" % row["douban_id"]) if row["douban_id"] else "",
    })
    # 叠加"我的人脉"私人卡片
    if owner:
        with _db() as c:
            ct = c.execute("SELECT contact,note FROM contacts WHERE owner=? AND person_id=?",
                           (owner, row["id"])).fetchone()
        d["inNetwork"] = bool(ct)
        d["myContact"] = ct["contact"] if ct else ""
        d["myNote"] = ct["note"] if ct else ""
    return d


# ============================================================ 路由
def handle(handler, method, path):
    init()
    p = path.split("?")[0]
    if method == "GET":
        from urllib.parse import urlparse, parse_qs
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        owner = _resolve_owner(handler, q)

        if p == "/api/production/talent/search":
            return _do_search(handler, q, owner)
        if p == "/api/production/talent/person":
            with _db() as c:
                row = c.execute("SELECT * FROM people WHERE id=?", (q.get("id", ""),)).fetchone()
            if not row or (row["is_private"] and row["owner"] != owner):
                return handler._json({"ok": False, "error": "查无此人或无权限"}, 404)
            return handler._json({"ok": True, "person": _full(row, owner)})
        if p == "/api/production/talent/network":   # 我的人脉
            if not owner:
                return handler._json({"ok": True, "people": []})
            with _db() as c:
                rows = c.execute(
                    "SELECT pp.* FROM people pp JOIN contacts ct ON ct.person_id=pp.id "
                    "WHERE ct.owner=? ORDER BY ct.created DESC", (owner,)).fetchall()
            return handler._json({"ok": True, "people": [_brief(r) for r in rows]})
        if p == "/api/production/talent/meta":
            with _db() as c:
                n = c.execute("SELECT COUNT(*) n FROM people").fetchone()["n"]
            return handler._json({"ok": True, "count": n, "professions": PRO_ORDER,
                                  "crawl": {k: _crawl[k] for k in ("busy", "added", "msg")}})
        return handler._json({"ok": False, "error": "未知接口"}, 404)

    # POST
    body = handler._body()
    owner = _resolve_owner(handler, body)

    if p == "/api/production/talent/add_douban":
        did = _extract_douban_id(body.get("input") or "")
        if not did:
            return handler._json({"ok": False, "error": "没认出豆瓣 id——请贴这个人的豆瓣页链接（形如 movie.douban.com/celebrity/1234567/）"}, 400)
        try:
            rec = fetch_person(did)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return handler._json({"ok": False, "error": "豆瓣查无此 id；若你贴的是 /personage/ 链接，请改用 /celebrity/ 链接"}, 400)
            return handler._json({"ok": False, "error": "豆瓣接口报错 %d，稍后再试" % e.code}, 502)
        except Exception as e:
            return handler._json({"ok": False, "error": "抓取失败：%s" % e}, 502)
        _upsert_person(rec, owner=None, is_private=0)
        with _db() as c:
            row = c.execute("SELECT * FROM people WHERE id=?", (rec["id"],)).fetchone()
        return handler._json({"ok": True, "person": _full(row, owner)})

    if p == "/api/production/talent/manual":
        name = (body.get("name") or "").strip()[:40]
        if not name:
            return handler._json({"ok": False, "error": "起码填个名字"}, 400)
        pros = [s.strip() for s in (body.get("professions") or []) if s.strip()][:8]
        works = []
        for w in (body.get("works") or [])[:60]:
            if isinstance(w, dict) and (w.get("title") or "").strip():
                works.append({"title": w["title"].strip()[:80], "year": str(w.get("year") or "")[:8],
                              "role": (w.get("role") or "").strip()[:40],
                              "roleCat": _role_category(w.get("role") or (pros[0] if pros else "")),
                              "rating": 0, "url": "", "directors": []})
        pid = "m" + ("%d" % int(_now() * 1000)) + str(random.randint(10, 99))
        rec = {"id": pid, "douban_id": None, "name": name, "avatar": "",
               "gender": "", "birthday": "", "birthplace": "", "imdb": "",
               "professions": pros, "works": works, "awards": [], "source": "manual",
               "note": (body.get("note") or "").strip()[:500]}
        _attach_tier(rec)
        _upsert_person(rec, owner=owner, is_private=1 if body.get("private") else 0)
        # 手动录入默认也加入"我的人脉"
        if owner:
            _set_contact(owner, pid, body.get("contact") or "", body.get("note") or "")
        with _db() as c:
            row = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
        return handler._json({"ok": True, "person": _full(row, owner)})

    if p == "/api/production/talent/refresh":   # 重新从豆瓣拉一次最新
        with _db() as c:
            row = c.execute("SELECT douban_id FROM people WHERE id=?", (body.get("id", ""),)).fetchone()
        if not row or not row["douban_id"]:
            return handler._json({"ok": False, "error": "这条不是豆瓣来源，无法刷新"}, 400)
        try:
            rec = fetch_person(row["douban_id"])
            _upsert_person(rec)
        except Exception as e:
            return handler._json({"ok": False, "error": "刷新失败：%s" % e}, 502)
        with _db() as c:
            r2 = c.execute("SELECT * FROM people WHERE id=?", (rec["id"],)).fetchone()
        return handler._json({"ok": True, "person": _full(r2, owner)})

    if p == "/api/production/talent/set_tier":
        with _db() as c:
            c.execute("UPDATE people SET tier=?, updated=? WHERE id=?",
                      (body.get("tier") or None, _now(), body.get("id", "")))
        return handler._json({"ok": True})

    if p == "/api/production/talent/contact":   # 加入/更新"我的人脉"私人卡片
        if not owner:
            return handler._json({"ok": False, "error": "登录后才能存私人联系方式"}, 401)
        _set_contact(owner, body.get("id", ""), body.get("contact") or "", body.get("note") or "")
        return handler._json({"ok": True})

    if p == "/api/production/talent/uncontact":
        if owner:
            with _db() as c:
                c.execute("DELETE FROM contacts WHERE owner=? AND person_id=?", (owner, body.get("id", "")))
        return handler._json({"ok": True})

    if p == "/api/production/talent/delete":     # 删掉一条人物（公共条目慎用；私人条目随意）
        with _db() as c:
            row = c.execute("SELECT owner,is_private FROM people WHERE id=?", (body.get("id", ""),)).fetchone()
            if row and (row["is_private"] == 0 or row["owner"] == owner):
                c.execute("DELETE FROM people WHERE id=?", (body.get("id", ""),))
                c.execute("DELETE FROM contacts WHERE person_id=?", (body.get("id", ""),))
        return handler._json({"ok": True})

    if p == "/api/production/talent/crawl":      # 手动触发种子爬取（补库）
        if _crawl["busy"]:
            return handler._json({"ok": True, "already": True, "crawl": {k: _crawl[k] for k in ("busy", "added", "msg")}})
        import datetime
        y = datetime.date.today().year
        years = body.get("years") or [y, y - 1, y - 2]
        kinds = body.get("kinds") or ["电视剧", "电影"]
        threading.Thread(target=crawl_seed, args=(years, kinds),
                         kwargs={"films_per": int(body.get("filmsPer") or 12),
                                 "people_cap": int(body.get("cap") or 300),
                                 "reason": "补充人才库"}, daemon=True).start()
        return handler._json({"ok": True, "started": True})

    return handler._json({"ok": False, "error": "未知接口"}, 404)


def _set_contact(owner, person_id, contact, note):
    with _db() as c:
        if not c.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
            return
        c.execute("INSERT OR REPLACE INTO contacts(owner,person_id,contact,note,created) "
                  "VALUES(?,?,?,?,COALESCE((SELECT created FROM contacts WHERE owner=? AND person_id=?),?))",
                  (owner, person_id, contact.strip()[:200], note.strip()[:500], owner, person_id, _now()))


def _extract_douban_id(s):
    """从用户输入里抠出豆瓣 celebrity id：支持完整链接、/celebrity/123/、/personage/123/、或纯数字。"""
    s = (s or "").strip()
    m = re.search(r"/celebrity/(\d+)", s) or re.search(r"/personage/(\d+)", s)
    if m:
        return m.group(1)
    if s.isdigit():
        return s
    m = re.search(r"(\d{5,})", s)
    return m.group(1) if m else ""


def _do_search(handler, q, owner):
    kw = (q.get("q") or "").strip()
    pro = (q.get("pro") or "").strip()
    tier = (q.get("tier") or "").strip()
    sql = "SELECT * FROM people WHERE (is_private=0 OR owner=?)"
    args = [owner or ""]
    if kw:
        sql += " AND name LIKE ?"
        args.append("%" + kw + "%")
    if pro:
        sql += " AND pro_text LIKE ?"
        args.append("%" + pro + "%")
    if tier:
        sql += " AND COALESCE(NULLIF(tier,''), tier_auto)=?"
        args.append(tier)
    # 排序：头部优先 → 高分作品多 → 作品多 → 近作新
    sql += (" ORDER BY CASE COALESCE(NULLIF(tier,''),tier_auto) WHEN '头部' THEN 0 WHEN '腰部' THEN 1 ELSE 2 END,"
            " top_count DESC, good_count DESC, work_count DESC, last_year DESC LIMIT 120")
    with _db() as c:
        rows = c.execute(sql, args).fetchall()
    return handler._json({"ok": True, "people": [_brief(r) for r in rows]})


if __name__ == "__main__":
    init()
    with _db() as c:
        n = c.execute("SELECT COUNT(*) n FROM people").fetchone()["n"]
    print("✓ 人才库数据库已初始化：", DB_PATH, "| 现有", n, "人")
