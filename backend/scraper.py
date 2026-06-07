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

import urllib.request, gzip, zlib, ssl, re
import html as _html

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
_GENRE_RULES = [
    ("美食", ["美食", "厨", "餐", "面馆", "下厨", "菜谱", "食肆", "饭馆", "酒楼"]),
    ("音乐", ["音乐", "乐队", "钢琴", "作曲", "乐团", "声乐", "摇滚", "民谣", "唱"]),
    ("运动", ["运动", "体育", "竞技", "花滑", "滑冰", "电竞", "赛车", "游泳", "登山", "篮球", "足球", "排球"]),
    ("热血", ["热血", "江湖", "武侠", "逆袭", "权谋", "战场", "枭雄", "群像"]),
    ("古装", ["古代", "宫廷", "王府", "架空历史", "将军", "公主", "侯门", "朝堂", "宅斗", "宫斗"]),
    ("都市", ["近代现代", "都市", "职场", "娱乐圈", "商战", "种田"]),
    ("人文", ["人文", "历史", "纪实", "年代", "乡土"]),
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
        # 太短(<8万字)撑不起剧集/电影，先排除
        if wc and wc < 80000:
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
