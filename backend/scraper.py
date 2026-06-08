#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 实时抓取模块（选题雷达的"手"）
#   目的：真的去文学网站抓"新书候选"扩充候选库，而不是只在写死的 10 部里挑。
#
#   v1 数据源：晋江「排行榜」topten.php
#     - 为什么选它：服务端渲染、一页含 ~200 本书；每行就带
#       书名 / 分类 / 进度(完结·连载) / 字数 / 简介(rel 属性) / 作者，
#       一次请求拿全，无需逐个去爬有反爬混淆的详情页。省事又稳。
#     - 编码：晋江是 GBK 系，按 gb18030 解码（GBK 超集，最稳）。
#
#   诚实的边界（侦察实测）：
#     - 番茄：网页 JS 动态渲染，标准库抓不到正文。
#     - 豆瓣阅读：详情页好抓(meta 标签很全)，但"列表/搜索"藏在私有 AJAX 接口、
#       公开的 /j/search 是假搜索(永远返回同一批短篇)，故 v1 先不吃豆瓣列表。
#   纯标准库（urllib + re），零第三方依赖，符合本项目约定。

import urllib.request, urllib.parse, gzip, zlib, ssl, re, json, time
import html as _html
import concurrent.futures  # 并发取番茄详情页（每本一请求，串行太慢）

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 抓哪些榜单：(orderstr, t, 榜单名)。
# 经实测对比各榜"新鲜度"(简介含影视/出版痕迹的比例)：综合榜(o7)几乎全是已售大 IP(59/200)；
# 而 o4 / o6 商业化痕迹极低(1~5/200)、完结作多、体量适中——更贴"在大买家之前发现未售好故事"。
# 故避开热榜、专挑这两个"新鲜完结"榜。(orderstr 的官方榜名未知，标签是按实测特征起的描述名)
JJ_RANKINGS = [
    ("4", "0", "完结新作A"),
    ("6", "0", "完结新作B"),
]

# "选品前置"反向过滤：简介里带这些"影视已动"痕迹的，多半版权已被买走 → 踢掉。
# 注意：只认"影视"信号；"实体书/出版/漫画/有声"不算(出版≠影视权已售)，不误杀。
_OPTIONED_MARKERS = [
    "影视化", "影视改编", "电视剧", "剧版", "网剧", "电影版", "番剧",
    "开机", "杀青", "待播", "定档", "主演", "已售影视", "影视权",
]


def _fetch(url, encoding="gb18030", timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,*/*",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.jjwxc.net/",
    })
    # 只 GET 公开页、不传任何密钥，故关闭证书校验以避免 Python 证书链兼容问题（低风险）。
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read()
        enc = r.headers.get("Content-Encoding", "")
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw)
        except Exception:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode(encoding, "replace")


def _clean(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# 晋江分类路径(如 原创-言情-近代现代-爱情)+简介关键词 → 我们的粗题材标签。
# 注意：这只是"粗标"，给代码层做便宜的预筛；真正的题材/故事判断交给大模型读简介去做。
# 关键词 → 新分类标签（时代/类型/题材/调性，与前端偏好面板一致）。
# 只做"粗标"给代码层预筛；细判断仍交给大模型读简介。
_GENRE_RULES = [
    # 时代
    ("古装", ["古代", "古装", "宫廷", "王府", "架空历史", "将军", "公主", "侯门", "朝堂", "宅斗", "宫斗", "仙侠", "古言", "古代言情"]),
    ("历史", ["历史", "正史", "史诗", "大唐", "北宋", "南宋", "明朝", "清朝", "历史小说"]),
    ("民国", ["民国", "旧上海", "军阀", "租界", "民国情缘"]),
    ("近代", ["近代", "清末", "晚清"]),
    ("现代", ["现代", "都市", "当代", "现代言情"]),
    ("未来", ["未来", "星际", "赛博", "近未来", "废土", "末世"]),
    # 类型
    ("言情", ["言情", "恋爱", "甜宠", "虐恋", "情缘", "幻想言情"]),
    ("悬疑", ["悬疑", "惊悚", "诡异", "灵异"]),
    ("推理", ["推理", "探案", "刑侦", "破案", "侦探", "罪案"]),
    ("科幻", ["科幻", "人工智能", "太空", "星际", "赛博"]),
    ("奇幻", ["奇幻", "玄幻", "魔法", "异世界", "中式奇幻", "志怪"]),
    ("武侠", ["武侠", "江湖", "侠客", "门派"]),
    ("现实", ["现实", "纪实", "乡土", "社会", "年代", "市井生活"]),
    ("犯罪", ["犯罪", "黑帮", "缉毒", "卧底", "复仇"]),
    # 题材
    ("美食", ["美食", "厨", "餐", "面馆", "下厨", "菜谱", "食肆", "饭馆", "酒楼", "烘焙"]),
    ("音乐", ["音乐", "乐队", "钢琴", "作曲", "乐团", "声乐", "摇滚", "民谣", "戏曲", "昆曲"]),
    ("运动", ["运动", "体育", "竞技", "花滑", "滑冰", "赛车", "游泳", "登山", "篮球", "足球", "拳击"]),
    ("电竞", ["电竞", "游戏", "电子竞技", "战队"]),
    ("职场", ["职场", "商战", "律政", "医疗", "金融", "投行", "创业", "职场女性"]),
    ("校园", ["校园", "青春", "高中", "大学", "学院"]),
    ("娱乐圈", ["娱乐圈", "明星", "选秀", "偶像", "影帝"]),
    ("群像", ["群像", "女性群像", "众生"]),
    # 调性
    ("治愈", ["治愈", "温暖", "温情", "日常", "慢节奏"]),
    ("热血", ["热血", "逆袭", "成长逆袭", "战斗", "权谋"]),
    ("励志", ["励志", "奋斗", "逐梦"]),
    ("文艺", ["文艺", "纯文学", "日系", "散文", "诗意"]),
    ("人文", ["人文", "哲思"]),
    ("爽感", ["爽文", "爽感", "打脸", "无敌", "系统", "金手指", "反套路"]),
]


def _map_genres(classification, synopsis):
    text = (classification or "") + " " + (synopsis or "")
    tags = []
    for tag, kws in _GENRE_RULES:
        if any(k in text for k in kws):
            tags.append(tag)
    return tags


def _parse_topten(htmltext, source_label):
    """把晋江排行榜 HTML 解析成书目列表。每行 <tr> 一本书。"""
    books = []
    rows = re.split(r"<tr", htmltext)
    for row in rows:
        m = re.search(r"onebook\.php\?novelid=(\d+)", row)
        if not m:
            continue
        nid = m.group(1)
        # 书名：在 class="tooltip">书名</a>
        mt = (re.search(r'class="tooltip">([^<]+)</a>', row)
              or re.search(r"onebook\.php\?novelid=\d+[^>]*>([^<]+)</a>", row))
        title = _clean(mt.group(1)) if mt else ""
        if not title:
            continue
        # 作者：oneauthor.php?authorid=...>作者</a>
        ma = re.search(r"oneauthor\.php\?authorid=\d+[^>]*>([^<]+)</a>", row)
        author = _clean(ma.group(1)) if ma else ""
        # 简介：藏在标题 <a> 的 rel 属性里（鼠标悬停提示）
        ms = re.search(r'rel="([^"]*)"', row)
        synopsis = _clean(ms.group(1)) if ms else ""
        # 分类路径：原创-言情-近代现代-爱情
        mc = re.search(r"((?:原创|衍生)-[^<]+)", row)
        classification = _clean(mc.group(1)) if mc else ""
        # 进度
        status = "完结" if "完结" in row else ("连载" if "连载" in row else "")
        # 字数：右对齐数字单元格
        mw = re.search(r'align="right">\s*(\d[\d,]*)', row)
        wordcount = int(mw.group(1).replace(",", "")) if mw else 0
        books.append({
            "id": "jj" + nid,
            "title": title,
            "author": author,
            "url": "https://www.jjwxc.net/onebook.php?novelid=" + nid,
            "source": source_label,
            "linkVerified": True,
            "status": status,
            "wordCount": wordcount,
            "genres": _map_genres(classification, synopsis),
            "classification": classification,
            "synopsis": synopsis[:400],
        })
    return books


def scrape_jjwxc():
    """抓晋江配置里的各榜单，按 id 去重，返回候选书目列表。"""
    found = {}
    for orderstr, t, label in JJ_RANKINGS:
        url = "https://www.jjwxc.net/topten.php?orderstr=%s&t=%s" % (orderstr, t)
        try:
            txt = _fetch(url)
            for b in _parse_topten(txt, "晋江·" + label):
                found[b["id"]] = b
        except Exception as e:
            print("  [scraper] 抓取失败 %s: %r" % (url, e))
    return list(found.values())


# ========== 番茄小说（robots: Allow:/ 全站允许）==========
# 书目元数据在详情页的 window.__INITIAL_STATE__ 里（书名/作者/字数/状态码），简介从 meta 取。
# 注：番茄"正文章节"是 JS 动态的——抓不到也不需要，我们只要书目信息。
# 状态码 creationStatus：0=完结，1=连载（已实测核对页面"已完结/连载中"文字）。
FANQIE_LIST_URLS = ["https://fanqienovel.com/", "https://fanqienovel.com/rank"]


def _fanqie_ids(limit=30):
    ids = []
    for url in FANQIE_LIST_URLS:
        try:
            ids += re.findall(r"/page/(\d+)", _fetch(url, "utf-8"))
        except Exception as e:
            print("  [scraper] 番茄列表失败 %s: %r" % (url, e))
    return list(dict.fromkeys(ids))[:limit]


def _fanqie_detail(book_id):
    try:
        page = _fetch("https://fanqienovel.com/page/" + book_id, "utf-8")
    except Exception:
        return None

    def sval(key):
        m = re.search(r'"' + key + r'":"([^"]*)"', page)
        return _clean(m.group(1)) if m else ""

    def ival(key):
        m = re.search(r'"' + key + r'":(\d+)', page)
        return int(m.group(1)) if m else 0

    title = sval("bookName")
    if not title:
        return None
    # 简介：从 meta description 去掉番茄的固定前后缀
    md = re.search(r'<meta name="description" content="([^"]*)"', page)
    syn = _clean(md.group(1)) if md else ""
    syn = re.sub(r"^番茄小说提供.*?在线免费阅读[，,、]?", "", syn)
    syn = re.sub(r"精彩小说尽在番茄小说网[。.]?", "", syn).strip()
    # 题材分类：尽力从页面里抠中文分类名，取不到就空着（靠简介让大模型判断）
    cats = re.findall(r'\\"(?:name|categoryName)\\":\\"([一-龥]{2,8})\\"', page)
    classification = "、".join(list(dict.fromkeys(cats))[:4])
    return {
        "id": "fq" + book_id,
        "title": title,
        "author": sval("author"),
        "url": "https://fanqienovel.com/page/" + book_id,
        "source": "番茄",
        "linkVerified": True,
        "status": "完结" if ival("creationStatus") == 0 else "连载",
        "wordCount": ival("wordNumber"),
        "genres": _map_genres(classification, syn),
        "classification": classification,
        "synopsis": syn[:400],
    }


def scrape_fanqie(limit=30):
    """番茄：榜单/首页拿书 ID → 并发抓各详情页元数据。"""
    ids = _fanqie_ids(limit)
    out = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for b in ex.map(_fanqie_detail, ids):
                if b:
                    out.append(b)
    except Exception as e:
        print("  [scraper] 番茄详情抓取异常：", repr(e))
    return out


# ========== 豆瓣阅读（主力源）==========
# 帽帽明确要求把豆瓣作为主力源。豆瓣 robots 禁 /j/，已知会有封查风险——为此：
#   ① scrape_all 带 1 小时缓存，不每次点都抓；② 真浏览器 UA + 适度请求量。
# 发现入口：/j/tag/<标签名> 返回该题材书单（实测无需 cookie，可 ?start=&limit= 翻页）。
# 豆瓣很多是"原创连载"——正合"选品前置：在被大公司买走前发现早期小众作品"。
DB_TAGS = ["悬疑", "推理", "古装", "言情", "科幻", "奇幻", "武侠", "历史",
           "美食", "治愈", "群像", "现实", "脑洞", "年代", "都市", "职场"]
DB_NONFICTION = ["教程", "入门", "指南", "手册", "图解", "百科", "攻略",
                 "讲义", "实战", "速成", "宝典", "工具书", "教材"]


def _fetch_douban(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, */*",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://read.douban.com/",
        "X-Requested-With": "XMLHttpRequest",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":  # 豆瓣常按 gzip 魔数返回（未必带 header）
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _douban_works(arr):
    out = []
    for w in arr:
        aid = w.get("alias_id") or ""
        title = (w.get("title") or "").strip()
        if not aid or not title:
            continue
        if any(k in title for k in DB_NONFICTION):  # 滤掉工具书/教程（运动/音乐标签常混入）
            continue
        wtags = [t.get("tag") for t in (w.get("tags") or []) if t.get("tag")]
        cat = w.get("category") or ""
        abstract = re.sub(r"\s+", " ", (w.get("abstract") or "")).strip()
        kind = w.get("kind") or ("完结" if w.get("has_finished") else "连载")
        out.append({
            "id": "db" + str(aid),
            "title": title,
            "author": w.get("author") or "",
            "url": "https://read.douban.com/column/%s/" % aid,
            "source": "豆瓣",
            "linkVerified": True,
            "status": kind,
            "wordCount": 0,  # tag 接口不给字数；豆瓣多为连载，不卡字数门槛
            "genres": _map_genres("、".join(wtags) + " " + cat, abstract),
            "classification": (cat + " " + "、".join(wtags[:5])).strip(),
            "synopsis": abstract[:400],
            "heat": w.get("in_library_count") or 0,
        })
    return out


def scrape_douban(per_tag=40):
    """按题材标签拉豆瓣书单（主力源），多标签去重。"""
    found = {}
    for tag in DB_TAGS:
        url = ("https://read.douban.com/j/tag/" + urllib.parse.quote(tag)
               + "?start=0&limit=%d" % per_tag)
        try:
            for b in _douban_works(json.loads(_fetch_douban(url))):
                found[b["id"]] = b
        except Exception as e:
            print("  [scraper] 豆瓣 tag %s 失败：%r" % (tag, e))
    return list(found.values())


# ========== 三源汇总（带缓存）==========
_CACHE = {"t": 0.0, "data": None}
_CACHE_TTL = 3600  # 默认 1 小时：避免每次点选片都重抓，也降低豆瓣封查风险


def set_cache_ttl(seconds):
    """常驻服务器可把缓存有效期设成"刷新周期"（如一周）：这样用户点选片永远命中缓存（秒回），
    只有后台定时线程去真抓——既保证每周新鲜，又把豆瓣请求量压到最低（降封查风险）。"""
    global _CACHE_TTL
    _CACHE_TTL = max(60, int(seconds))


def cache_info():
    """缓存现状：最近一次抓取时间戳 + 条数（给后台/前端看新鲜度）。"""
    return {"t": _CACHE["t"], "count": len(_CACHE["data"] or [])}


def scrape_all(force=False):
    """三源：豆瓣(主力) + 晋江 + 番茄。带 1 小时缓存。"""
    now = time.time()
    if not force and _CACHE["data"] is not None and (now - _CACHE["t"]) < _CACHE_TTL:
        return _CACHE["data"]
    books = []
    for name, fn in [("豆瓣", scrape_douban), ("晋江", scrape_jjwxc), ("番茄", scrape_fanqie)]:
        try:
            r = fn()
            books += r
            print("  [scraper] %s %d 本" % (name, len(r)))
        except Exception as e:
            print("  [scraper] %s 失败：%r" % (name, e))
    seen, out = set(), []
    for b in books:
        t = (b.get("title") or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(b)
    _CACHE["t"], _CACHE["data"] = now, out
    return out


def filter_for_profile(books, profile, limit=30):
    """便宜的代码层"粗筛"：把候选缩到合理数量再喂大模型。细判断仍交给大模型。
    用 .get() 取字段，以便同时兼容"抓来的书"和"本地候选库"两种结构。"""
    p = profile or {}
    wants_done = "完结" in str(p.get("status", ""))
    dislikes = p.get("dislikes", []) or []
    out = []
    for b in books:
        wc = b.get("wordCount") or 0
        status = b.get("status") or ""
        cls = b.get("classification") or ""
        syn = b.get("synopsis") or ""
        # 太短(<8万字)撑不起剧集/电影；太长(>200万字，多是番茄无限连载爽文)改编不现实——都排除
        if wc and (wc < 80000 or wc > 2000000):
            continue
        # 偏好完结：用"包含"判断，兼容"完结"/"已完结"两种写法
        if wants_done and status and "完结" not in status:
            continue
        # 明确不感冒的题材(如 民国)做粗排
        if any(d and (d in cls or d in syn) for d in dislikes):
            continue
        # "选品前置"：简介里带明显影视化痕迹的，多半已被买走，踢掉
        if any(m in syn for m in _OPTIONED_MARKERS):
            continue
        out.append(b)

    def rank(b):
        s = 0
        if "完结" in (b.get("status") or ""):
            s += 2
        if 100000 <= (b.get("wordCount") or 0) <= 800000:  # 改编体量较合适
            s += 2
        if b.get("genres"):  # 命中口味题材
            s += 1
        return -s

    out.sort(key=rank)
    return out[:limit]


if __name__ == "__main__":
    bs = scrape_jjwxc()
    print("抓到", len(bs), "本")
    ok_author = sum(1 for b in bs if b["author"])
    ok_wc = sum(1 for b in bs if b["wordCount"])
    print("有作者:", ok_author, "| 有字数:", ok_wc)
    print("=" * 50)
    for b in bs[:8]:
        print("- 《%s》 / %s | %s | %s字 | 题材%s | %s" % (
            b["title"], b["author"] or "?", b["status"] or "?",
            b["wordCount"], b["genres"], b["classification"]))
        print("    简介:", b["synopsis"][:70])
