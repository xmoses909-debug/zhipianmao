#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 制作板块后端（制片统筹系统）
#
# ⚠️ 互不干扰约定：本文件是【制作板块】专属，选题雷达（策划板块）的逻辑在 server.py / scraper.py。
#    server.py 只在 do_GET / do_POST 开头各加了一个挂载钩子（/api/production/* 全部转进来），
#    除那几行外，两个板块的代码完全分离，互相不 import 对方的业务函数。
#    本模块依赖 server.py Handler 的三个小工具方法：_json() / _body() / _token() —— 改名需同步。
#
# 三个区域：
#   ① 剧本解剖分析  POST /api/production/run {kind:"analysis"}  → DeepSeek 思考型模型通读全本
#   ② 分场表        POST /api/production/run {kind:"scenes"}    → 正则切场 + 快速模型分批提取
#      （顺场表不在后端生成：它只是分场表按"场景地点"重新归组的视图，前端实时推导，改了自动跟）
#   ③ 参考预算      POST /api/production/run {kind:"budget"}    → 按中国市场行情 + 拆解统计估算
#   所有结果存 SQLite，用户在前端改完用 /api/production/save 存回来 —— AI 给初稿，人说了算。
#
# 数据库独立：production.db（不和选题雷达的 data.db 混表），位置跟随 ZPM_DB 所在目录。
# 零第三方依赖：docx 解析用 zipfile+xml（docx 本质是 zip 包），全部标准库。
import os
import re
import io
import json
import time
import sqlite3
import secrets
import zipfile
import threading
import contextlib
import urllib.request
import urllib.error
from xml.etree import ElementTree

import db as account_db  # 只用它的 user_by_token：复用选题雷达的真账号会话（只读，不动它的表）
import pdftext           # 制作板块专属：纯标准库 PDF 文本提取

ROOT = os.path.dirname(os.path.abspath(__file__))
# 数据库放哪：优先 ZPM_PROD_DB 指定；否则放进 ZPM_DB 同目录（服务器上即 /var/lib/zhipianmao/，持久盘）
_zpm_db = os.environ.get("ZPM_DB") or ""
DB_PATH = (os.environ.get("ZPM_PROD_DB")
           or os.path.join(os.path.dirname(_zpm_db) if _zpm_db else ROOT, "production.db"))

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL_DEEP = "deepseek-v4-pro"     # 思考型：剧本深度分析、预算（要文学判断力和行情推理）
MODEL_FAST = "deepseek-v4-flash"   # 快速型：分场表批量提取（结构化抽取，快和便宜更重要）

MAX_SCRIPT_CHARS = 500000          # 上传硬上限 50 万字（再长请按集拆开传）
ANALYSIS_MAX_CHARS = 60000         # 深度分析单次喂入上限（超长取头尾采样并注明）
SCENE_BATCH_CHARS = 4500           # 分场提取每批字数（兼顾速度与上下文完整）
SCENE_BATCH_MAX = 45               # 分场最多处理批数（~20 万字），防超长剧本跑半小时

_lock = threading.Lock()
_init_done = False
_ai_slots = threading.Semaphore(2)  # 全局最多 2 个 AI 任务同时跑（2核小服务器，别挤爆）


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
    """建表（幂等）。首个请求进来时自动调，server.py 不必显式初始化。"""
    global _init_done
    if _init_done:
        return
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
            id          TEXT PRIMARY KEY,
            owner       TEXT NOT NULL,      -- 'u:<用户id>'（登录）或 'd:<设备id>'（匿名）
            title       TEXT,
            script_name TEXT,               -- 上传的文件名
            script_text TEXT,               -- 剧本全文（拆解/分析都从这读）
            meta        TEXT,               -- JSON：项目类型/集数等设置
            analysis    TEXT,               -- JSON：剧本解剖结果（用户可改后存回）
            scenes      TEXT,               -- JSON：分场表（用户可改后存回）
            budget      TEXT,               -- JSON：预算表（用户可改后存回）
            series_id   TEXT,               -- 属于哪个剧集组（多集剧；单本为 NULL）
            episode     INTEGER,            -- 第几集（剧集组内排序用）
            created     REAL, updated REAL
        );
        CREATE TABLE IF NOT EXISTS series(
            id          TEXT PRIMARY KEY,
            owner       TEXT NOT NULL,
            title       TEXT,
            created     REAL, updated REAL
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id          TEXT PRIMARY KEY,
            project_id  TEXT, kind TEXT,    -- analysis | scenes | budget | series_scenes
            status      TEXT,               -- running | done | error
            progress    REAL, message TEXT, error TEXT,
            created     REAL, updated REAL
        );
        """)
        # 老库平滑升级：v1 的 projects 表没有 series 两列，补上（幂等，已有就略过）
        for ddl in ("ALTER TABLE projects ADD COLUMN series_id TEXT",
                    "ALTER TABLE projects ADD COLUMN episode INTEGER"):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass
    _init_done = True


def _now():
    return time.time()


def _resolve_owner(handler, body_or_query):
    """谁在操作：登录用户优先（u:id），否则按前端给的设备号（d:xxx）。
    顺手做"认领"：登录后把这台设备匿名期建的项目归到账号名下——换设备登录也能看到。"""
    tok = handler._token()
    device = (body_or_query.get("device") or "").strip()[:64]
    u = account_db.user_by_token(tok) if tok else None
    if u:
        owner = "u:%d" % u["id"]
        if device:
            with _db() as c:
                c.execute("UPDATE projects SET owner=? WHERE owner=?", (owner, "d:" + device))
        return owner
    return ("d:" + device) if device else None


# ============================================================ 剧本文件解析（零依赖）
def _docx_to_text(data):
    """docx = zip 包，正文在 word/document.xml。段落 w:p、文本 w:t、换行 w:br、制表 w:tab。"""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paras = []
    for p in root.iter(ns + "p"):
        buf = []
        for node in p.iter():
            if node.tag == ns + "t":
                buf.append(node.text or "")
            elif node.tag == ns + "br":
                buf.append("\n")
            elif node.tag == ns + "tab":
                buf.append("  ")
        paras.append("".join(buf))
    return "\n".join(paras)


def _fdx_to_text(data):
    """Final Draft .fdx 是 XML：抽 Paragraph 的类型 + 文本，场头单独成行。"""
    root = ElementTree.fromstring(data)
    lines = []
    for p in root.iter("Paragraph"):
        txt = "".join((t.text or "") for t in p.iter("Text")).strip()
        if txt:
            lines.append(txt)
    return "\n".join(lines)


def _decode_text(data):
    """中文剧本常见 GBK/GB18030 编码，utf-8 读不动就降级试。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "ignore")


def parse_script_file(filename, raw):
    """按扩展名解析上传的剧本 → 纯文本。返回 (text, None) 或 (None, 错误提示)。"""
    name = (filename or "").lower()
    try:
        if name.endswith(".docx"):
            text = _docx_to_text(raw)
        elif name.endswith(".fdx"):
            text = _fdx_to_text(raw)
        elif name.endswith(".pdf"):
            text = pdftext.extract_text(raw)
        elif name.endswith(".doc"):
            return None, "老版 .doc 格式解析不了——请在 Word 里另存为 .docx 或 .txt 再传"
        else:  # .txt / .md / 无后缀，按纯文本
            text = _decode_text(raw)
    except pdftext.PDFTextError as e:
        return None, str(e)
    except Exception as e:
        return None, "文件解析失败（%s）——换成 .txt 或 .docx 试试" % e.__class__.__name__
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if len(text) < 200:
        return None, "读出来的内容太短（不足 200 字），可能不是剧本文件或解析有误"
    if len(text) > MAX_SCRIPT_CHARS:
        return None, "剧本超过 %d 万字——请按集/按部分拆开上传" % (MAX_SCRIPT_CHARS // 10000)
    return text, None


# ============================================================ 多集剧：ZIP 解包 + 按集切分
_EP_PATTERNS = [
    re.compile(r"^\s*第\s*([0-9一二三四五六七八九十百零]+)\s*集"),
    re.compile(r"^\s*(?:EP|ep|Ep)\s*\.?\s*(\d+)"),
    re.compile(r"^\s*(?:Episode|EPISODE)\s+(\d+)"),
    re.compile(r"^\s*(\d+)\s*集\b"),
]


def _cn_num(s):
    """中文数字转整数（一二十/二十六这类，剧集集数够用）。"""
    if s.isdigit():
        return int(s)
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if s == "十":
        return 10
    n, tmp = 0, 0
    for ch in s:
        if ch in digits:
            tmp = digits[ch]
        elif ch == "十":
            n += (tmp or 1) * 10
            tmp = 0
        elif ch == "百":
            n += (tmp or 1) * 100
            tmp = 0
    return n + tmp


def split_episodes(text):
    """单个文件里含多集（"第1集/第2集…"分章）→ 切成 [(集号, 集文本)]；识别不到 2 集以上返回 None。
    为什么切：26 集连续剧一个文件几十万字，整本喂模型必断档；一集一个项目，各拆各的，最后再汇总。"""
    lines = text.split("\n")
    best_pat, best_hits = None, []
    for pat in _EP_PATTERNS:
        hits = [(i, m) for i, ln in enumerate(lines) for m in [pat.match(ln)] if m]
        if len(hits) > len(best_hits):
            best_pat, best_hits = pat, hits
    if len(best_hits) < 2:
        return None
    # 同一集号可能在页眉重复出现（PDF 提取的页眉），只认"集号递增"的首次出现
    marks = []
    for i, m in best_hits:
        ep = _cn_num(m.group(1))
        if ep and (not marks or ep > marks[-1][1]):
            marks.append((i, ep))
    if len(marks) < 2:
        return None
    out = []
    for k, (start, ep) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if len(body) >= 300:  # 太短的"集"多半是目录/页眉误判，丢掉
            out.append((ep, body))
    return out if len(out) >= 2 else None


def _zip_entries(raw):
    """解 ZIP → [(文件名, bytes)]，按文件名里的数字自然排序（"第2集"排在"第10集"前面）。
    中文文件名坑：老压缩工具不打 UTF-8 标记，zipfile 会按 cp437 读出乱码——按惯例转回 GBK。"""
    z = zipfile.ZipFile(io.BytesIO(raw))
    entries = []
    for info in z.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if not (info.flag_bits & 0x800):  # 无 UTF-8 标记：cp437 → gbk 修复中文名
            try:
                name = name.encode("cp437").decode("gbk")
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
        base = name.rsplit("/", 1)[-1]
        if base.startswith(".") or "__MACOSX" in name:
            continue
        if not re.search(r"\.(pdf|txt|docx|fdx|md)$", base, re.I):
            continue
        if info.file_size > 40 * 1024 * 1024:
            continue
        entries.append((base, z.read(info)))

    def natkey(item):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", item[0])]
    entries.sort(key=natkey)
    return entries


# ============================================================ 切场（正则多模式评选）
# 中文剧本场头五花八门，逐一定模式、谁匹配得多就按谁切；都匹配不上就按字数分块交给 AI 自己断。
_SCENE_PATTERNS = [
    re.compile(r"^\s*\d+\s*[-—–一]\s*\d+[、.．:：\s]"),                  # 3-12  / 3—12（集-场）
    re.compile(r"^\s*\d+\s*[、.．:：]\s*(?=.*(内|外|日|夜|晨|黄昏|傍晚))"),  # 12、内 咖啡馆 日
    re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百]+\s*场"),               # 第12场
    re.compile(r"^\s*[0-9]+\s*[.、]?\s*(内景|外景|内外景)"),                # 12 内景：
    re.compile(r"^\s*(INT|EXT|I/E)[\.\s]", re.I),                        # INT. COFFEE SHOP - DAY
    re.compile(r"^\s*[Ss]\s*\d+\b|^\s*场\s*\d+"),                        # S12 / 场12
]


def split_scenes(text):
    """把剧本按场头切成 [{head, body}]；识别不出场头则返回 (None, 0)。"""
    lines = text.split("\n")
    best_pat, best_hits = None, 0
    for pat in _SCENE_PATTERNS:
        hits = sum(1 for ln in lines if pat.match(ln))
        if hits > best_hits:
            best_pat, best_hits = pat, hits
    if best_hits < 3:  # 少于 3 个场头不可信（可能是小说体/文学本）
        return None, 0
    chunks, cur = [], None
    for ln in lines:
        if best_pat.match(ln):
            if cur:
                chunks.append(cur)
            cur = {"head": ln.strip(), "body": []}
        elif cur:
            cur["body"].append(ln)
    if cur:
        chunks.append(cur)
    for ch in chunks:
        ch["body"] = "\n".join(ch["body"]).strip()
    return chunks, best_hits


def make_batches(text):
    """把剧本切成喂给快速模型的批次。能识别场头→按场组批（场不拆散）；识别不了→按字数硬切。
    返回 (batches, mode)：batches=[{text, sceneCount}]，mode='heads'|'blocks'。"""
    chunks, hits = split_scenes(text)
    batches = []
    if chunks:
        cur, cur_len, cur_n = [], 0, 0
        for ch in chunks:
            piece = ch["head"] + "\n" + ch["body"]
            # 单场就超长的（罕见），独立成批并截断，别把整批撑爆
            if len(piece) > SCENE_BATCH_CHARS * 2:
                piece = piece[:SCENE_BATCH_CHARS * 2] + "\n（本场过长，已截断）"
            if cur and cur_len + len(piece) > SCENE_BATCH_CHARS:
                batches.append({"text": "\n\n".join(cur), "sceneCount": cur_n})
                cur, cur_len, cur_n = [], 0, 0
            cur.append(piece)
            cur_len += len(piece)
            cur_n += 1
        if cur:
            batches.append({"text": "\n\n".join(cur), "sceneCount": cur_n})
        return batches, "heads"
    # 没有可识别的场头：按 ~SCENE_BATCH_CHARS 切块，断点尽量落在空行
    pos, n = 0, len(text)
    while pos < n:
        end = min(pos + SCENE_BATCH_CHARS, n)
        if end < n:
            nl = text.rfind("\n\n", pos + SCENE_BATCH_CHARS // 2, end)
            if nl > 0:
                end = nl
        batches.append({"text": text[pos:end], "sceneCount": 0})
        pos = end
    return batches, "blocks"


# ============================================================ DeepSeek 调用
def _load_key():
    """和 server.py 同一套 key 来源（环境变量 / key.txt / .env），但独立实现避免 import 业务模块。"""
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    txt = os.path.join(ROOT, "key.txt")
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


def _call_ai(model, system, user, max_tokens=8000, retries=1):
    """调 DeepSeek（OpenAI 兼容），强制 JSON 模式，空内容/网络抖动自动重试一次。"""
    key = _load_key()
    if not key:
        raise RuntimeError("未配置 DeepSeek key（backend/key.txt）")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
        # 思考型模型的 max_tokens 包含思考链，给足空间防 JSON 被截断
        "max_tokens": max_tokens,
    }).encode("utf-8")
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                DEEPSEEK_URL, data=body,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=280) as r:
                data = json.loads(r.read().decode("utf-8"))
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if content:
                return _parse_json(content)
            last_err = RuntimeError("模型返回空内容")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            last_err = RuntimeError("DeepSeek 接口 %d：%s" % (e.code, detail))
            if e.code in (400, 401, 402, 422):  # 参数/key/欠费类错误，重试也没用
                break
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise last_err


def _parse_json(content):
    """模型偶尔包一层 ```json 围栏或带前后语，剥掉再解析。"""
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise ValueError("模型输出不是合法 JSON")


# ============================================================ 三大任务的提示词
ANALYSIS_SYSTEM = (
    "你是资深的剧本医生 + 制片人顾问，为影视制片人做剧本深度解剖。立场：制片视角（能不能拍、好不好卖、坑在哪），"
    "诚实专业不吹捧，风险照直说。所有判断基于给定剧本原文，禁止编造剧本里没有的情节人物。\n"
    "只输出 JSON，结构如下（所有文字用中文）：\n"
    '{"logline":"一句话故事（35字内）",'
    '"synopsis":"故事梗概（300-450字，讲清开端发展高潮结局）",'
    '"theme":"主题内核（这个故事到底在说什么）",'
    '"genre":"类型定位（如：悬疑/家庭情感/古装权谋）","tone":"调性气质",'
    '"structure":[{"part":"如 第一幕·建置 或 起","range":"对应场次/篇幅范围","desc":"这部分发生什么、承担什么功能（60字内）"}],'
    '"characters":[{"name":"姓名","role":"男主/女主/男配/反派…","age":"约几岁","desc":"人物小传（80字内）",'
    '"arc":"人物弧光（变化轨迹）","castingNote":"选角建议（气质/类型/参考）"}],'
    '"highlights":["制作或市场层面的亮点，逐条"],'
    '"risks":[{"type":"过审/制作/市场/改编","desc":"风险说明与应对思路"}],'
    '"pacing":"节奏与体量评估：适合电影（约多少分钟）还是剧集（约多少集），目前篇幅节奏的问题",'
    '"verdict":"制片人视角总评（150字内：值不值得做、最大卖点、最大的坑）"}'
)

SCENES_SYSTEM = (
    "你是资深影视统筹（制片统筹师），任务：把剧本片段拆成中国剧组标准格式的分场表。要求：\n"
    "- 按剧本场次逐场提取；若片段没有明确场头，按地点/时间跳转自行合理断场。\n"
    "- 原文有场号就照抄原文场号；没有就按给定起始号顺延。\n"
    "- atmo（气氛）只用：日/夜/晨/昏（黄昏傍晚归\"昏\"，清晨黎明归\"晨\"）。\n"
    "- 场景分两级：mainLoc 主场景=大地点（如\"张微家\"\"公司\"\"海边\"，顺场表按它归组转场），"
    "subLoc 分场景=具体空间（如\"客厅-厨房\"\"总裁办公室\"\"沙滩\"），没有更细分就留空。\n"
    "- content（内容提示）：制片视角一句话（40字内）讲清这场干什么；回忆/梦境/闪回要标出来。\n"
    "- action（动作/特效）：打戏/追车/爆破/雨戏/水戏/威亚/特效镜头等会影响成本排期的，没有留空。\n"
    "- characters 列出场的【主要人物】（有名有姓的角色，按戏份排），同一人物全篇用统一姓名；"
    "extras 填特约演员（有戏份的无名角色，如\"出租车司机/护士\"），crowd 填群众演员（如\"环境人物/路人20人\"），没有留空。\n"
    "- propsNote（服化道提示）：该场关键道具+服装化妆要点合写一格（如\"小铲子/手机；周野湿透外卖服\"），没有留空。\n"
    "- pages 按一页≈成片一分钟估该场体量，0.25~3 的小数。\n"
    "只输出 JSON：\n"
    '{"scenes":[{"no":"场号(字符串)","ep":集数数字或null,"atmo":"日/夜/晨/昏","intExt":"内/外/内外",'
    '"mainLoc":"主场景","subLoc":"分场景或空串","pages":0.5,"content":"内容提示",'
    '"action":"动作/特效或空串","characters":["主要人物"],"extras":"特约演员或空串",'
    '"crowd":"群众演员或空串","propsNote":"服化道提示或空串"}]}'
)

BUDGET_SYSTEM = (
    "你是中国影视行业资深制片主任/预算师，熟悉近几年（2024-2026）中国电影、电视剧、网剧、微短剧的真实制作行情。"
    "任务：根据剧本拆解统计与项目定位，编制一份参考预算（单位：万元人民币）。要求：\n"
    "- 金额给 low/high 区间，体现市场真实量级；不同级别（S/A/B）和不同形态（院线电影/平台剧/网大/微短剧）的量级差异要拉开。\n"
    "- 结合场数、内外景比、日夜比、转场地点数、特殊拍摄需求，推算合理拍摄周期（shootDays）。\n"
    "- 科目按中国剧组惯例分组：剧本版权、主创团队、演员、摄制团队、器材租赁、美术置景道具、服装化妆、"
    "场地交通食宿、特殊拍摄（特效/动作/烟火等，按给到的特殊需求列）、后期制作、保险与行政、宣发预留。\n"
    "- 每条 note 写清依据/包含什么；所有前提写进 assumptions。\n"
    "- marketNote 讲同形态同量级项目的大盘行情区间；拿不准的不要编具体片名和具体数字。\n"
    "只输出 JSON：\n"
    '{"currency":"万元人民币","assumptions":["前提假设逐条"],"shootDays":60,'
    '"items":[{"group":"演员","name":"主演片酬（2人）","low":300,"high":800,"note":"说明"}],'
    '"contingencyPct":8,"marketNote":"市场行情参考","disclaimer":"一句话免责：AI 参考估算，实际以询价为准"}'
)


# ============================================================ 任务执行（后台线程）
def _job_update(job_id, **kw):
    sets = ", ".join(k + "=?" for k in kw)
    with _db() as c:
        c.execute("UPDATE jobs SET %s, updated=? WHERE id=?" % sets,
                  list(kw.values()) + [_now(), job_id])


def _project_save_field(pid, field, data):
    assert field in ("analysis", "scenes", "budget", "meta")
    with _db() as c:
        c.execute("UPDATE projects SET %s=?, updated=? WHERE id=?" % field,
                  (json.dumps(data, ensure_ascii=False), _now(), pid))


def _run_analysis(job_id, project):
    text = project["script_text"]
    note = ""
    if len(text) > ANALYSIS_MAX_CHARS:
        # 超长：取头 70% + 尾 30% 采样（开头铺垫和结尾收束最影响判断），并如实注明
        head = int(ANALYSIS_MAX_CHARS * 0.7)
        tail = ANALYSIS_MAX_CHARS - head
        text = text[:head] + "\n\n……（中段略，篇幅超限）……\n\n" + text[-tail:]
        note = "剧本全文 %.1f 万字超出单次分析上限，本报告基于开头+结尾共 %.1f 万字采样，中段未细读。" % (
            len(project["script_text"]) / 10000.0, ANALYSIS_MAX_CHARS / 10000.0)
    _job_update(job_id, message="制片帽正在通读剧本、撰写解剖报告（思考型模型，约 1-3 分钟）…")
    user = "剧本《%s》全文如下：\n\n%s" % (project["title"] or "未命名", text)
    result = _call_ai(MODEL_DEEP, ANALYSIS_SYSTEM, user, max_tokens=8000)
    if note:
        result["sampleNote"] = note
    result["generatedAt"] = _now()
    _project_save_field(project["id"], "analysis", result)


def _do_scenes(project, progress_cb):
    """拆场核心：单集任务和剧集批量任务都走这。progress_cb(百分比, 文案) 由调用方决定怎么汇报。"""
    batches, mode = make_batches(project["script_text"])
    truncated = False
    if len(batches) > SCENE_BATCH_MAX:
        batches = batches[:SCENE_BATCH_MAX]
        truncated = True
    total = len(batches)
    ep_no = project.get("episode")
    all_scenes = []
    for i, b in enumerate(batches):
        progress_cb(round(i * 100.0 / total, 1), "第 %d/%d 批（已出 %d 场）" % (i + 1, total, len(all_scenes)))
        hint = ("本片段含约 %d 场。" % b["sceneCount"]) if b["sceneCount"] else "本片段没有标准场头，请你自行断场。"
        user = ("剧本《%s》片段 %d/%d。%s若原文无场号，从第 %d 场开始顺延编号。%s\n\n%s"
                % (project["title"] or "未命名", i + 1, total, hint, len(all_scenes) + 1,
                   ("本剧本是第 %d 集，每场 ep 填 %d。" % (ep_no, ep_no)) if ep_no else "", b["text"]))
        try:
            out = _call_ai(MODEL_FAST, SCENES_SYSTEM, user, max_tokens=7000)
            scenes = out.get("scenes") or []
        except Exception as e:
            # 单批失败不毁全局：记一条占位，继续往下跑
            scenes = [{"no": "?", "ep": ep_no, "atmo": "", "intExt": "", "mainLoc": "（本批拆解失败：%s）" % e,
                       "subLoc": "", "content": "请手动补这一段", "action": "", "characters": [],
                       "extras": "", "crowd": "", "propsNote": "", "pages": 0}]
        if ep_no:
            for s in scenes:
                s["ep"] = ep_no  # 集号以项目为准，防模型漏填/填错
        all_scenes.extend(scenes)
    result = {"scenes": all_scenes, "mode": mode, "generatedAt": _now()}
    if truncated:
        result["truncatedNote"] = "剧本太长，只拆了前 %d 批（约 %d 万字）。建议按集拆开分别上传。" % (
            SCENE_BATCH_MAX, SCENE_BATCH_MAX * SCENE_BATCH_CHARS // 10000)
    _project_save_field(project["id"], "scenes", result)
    return len(all_scenes)


def _run_scenes(job_id, project):
    _do_scenes(project, lambda pct, msg: _job_update(job_id, progress=pct, message="正在拆解" + msg + "…"))


def _run_series_scenes(job_id, series_id, force):
    """剧集批量拆场：按集号顺序一集一集拆（已拆过的集默认跳过，断点续跑友好）。"""
    with _db() as c:
        rows = c.execute("SELECT * FROM projects WHERE series_id=? ORDER BY episode", (series_id,)).fetchall()
    eps = [dict(r) for r in rows]
    total = len(eps)
    if not total:
        raise RuntimeError("这个剧集组里没有集")
    done_scenes = 0
    for idx, prj in enumerate(eps):
        if prj.get("scenes") and not force:
            continue  # 已有结果的集不重拆（中途断了再点一次，接着跑没拆的）

        def cb(pct, msg, _idx=idx, _t=prj["title"]):
            overall = round((_idx + pct / 100.0) * 100.0 / total, 1)
            _job_update(job_id, progress=overall,
                        message="第 %d/%d 集《%s》：%s" % (_idx + 1, total, _t or "未命名", msg))
        n = _do_scenes(prj, cb)
        done_scenes += n
    _job_update(job_id, message="全部 %d 集拆完，共 %d 场" % (total, done_scenes))


def _scene_stats(scenes_data):
    """从分场表汇总统计，给预算任务当依据。（兼容 v1 旧字段名：location/dayNight/special）"""
    sc = (scenes_data or {}).get("scenes") or []
    if not sc:
        return None
    locs, chars, specials = {}, {}, {}
    day = night = interior = exterior = 0
    pages = 0.0
    for s in sc:
        loc = (s.get("mainLoc") or s.get("location") or "").strip()
        if loc:
            locs[loc] = locs.get(loc, 0) + 1
        for ch in (s.get("characters") or []):
            chars[ch] = chars.get(ch, 0) + 1
        act = s.get("action")
        sp_list = [act] if (act and isinstance(act, str)) else (s.get("special") or [])
        for sp in sp_list:
            if sp:
                specials[sp] = specials.get(sp, 0) + 1
        dn = s.get("atmo") or s.get("dayNight") or ""
        if "夜" in dn:
            night += 1
        elif dn:
            day += 1
        ie = s.get("intExt") or ""
        if "外" in ie:
            exterior += 1
        elif ie:
            interior += 1
        try:
            pages += float(s.get("pages") or 0)
        except (TypeError, ValueError):
            pass
    main_cast = sorted(chars.items(), key=lambda kv: -kv[1])[:10]
    return {
        "sceneCount": len(sc), "locationCount": len(locs),
        "topLocations": sorted(locs.items(), key=lambda kv: -kv[1])[:10],
        "mainCharacters": [{"name": k, "scenes": v} for k, v in main_cast],
        "castSize": len(chars), "dayScenes": day, "nightScenes": night,
        "interior": interior, "exterior": exterior,
        "specials": specials, "estPages": round(pages, 1),
    }


def _run_budget(job_id, project, options):
    _job_update(job_id, message="制片帽正在按中国市场行情编制预算（约 1-3 分钟）…")
    opts = options or {}
    with _db() as c:
        row = c.execute("SELECT scenes, analysis FROM projects WHERE id=?", (project["id"],)).fetchone()
    stats = _scene_stats(json.loads(row["scenes"])) if row and row["scenes"] else None
    ana = json.loads(row["analysis"]) if row and row["analysis"] else {}
    lines = [
        "项目名：《%s》" % (project["title"] or "未命名"),
        "项目形态：%s" % (opts.get("type") or "未指定（请按剧本体量自行判断并写进 assumptions）"),
        "制作级别：%s" % (opts.get("level") or "A（默认）"),
        "集数：%s" % (opts.get("episodes") or "—（电影则忽略）"),
        "剧本字数：约 %.1f 万字" % (len(project["script_text"]) / 10000.0),
    ]
    if opts.get("note"):
        lines.append("制片人补充要求：" + str(opts["note"])[:500])
    if ana:
        lines.append("剧本类型/调性：%s / %s" % (ana.get("genre", "—"), ana.get("tone", "—")))
        if ana.get("pacing"):
            lines.append("体量评估：" + str(ana.get("pacing"))[:200])
    if stats:
        lines.append("分场表统计（真实拆解数据，预算的主要依据）：\n" + json.dumps(stats, ensure_ascii=False))
    else:
        lines.append("（尚未生成分场表，请按剧本字数与类型粗估，并在 assumptions 里写明此局限）")
    result = _call_ai(MODEL_DEEP, BUDGET_SYSTEM, "\n".join(lines), max_tokens=8000)
    result["options"] = opts
    result["generatedAt"] = _now()
    _project_save_field(project["id"], "budget", result)


_RUNNERS = {"analysis": _run_analysis, "scenes": _run_scenes, "budget": _run_budget,
            "series_scenes": _run_series_scenes}


def _job_thread(job_id, kind, project, options):
    with _ai_slots:  # 限流：最多 2 个 AI 任务并行
        try:
            if kind == "budget":
                _run_budget(job_id, project, options)
            elif kind == "series_scenes":
                _run_series_scenes(job_id, project["id"], bool((options or {}).get("force")))
            elif kind == "analysis":
                _run_analysis(job_id, project)
            else:
                _run_scenes(job_id, project)
            if kind == "series_scenes":
                _job_update(job_id, status="done", progress=100)  # 汇总文案已由 runner 写好，别覆盖
            else:
                _job_update(job_id, status="done", progress=100, message="完成")
        except Exception as e:
            _job_update(job_id, status="error", error=str(e)[:500],
                        message="失败：" + str(e)[:200])


def start_job(project, kind, options):
    """起后台任务。同项目同类型已有 running 任务则直接返回它（防双击重复烧钱）。"""
    with _db() as c:
        running = c.execute(
            "SELECT id FROM jobs WHERE project_id=? AND kind=? AND status='running'",
            (project["id"], kind)).fetchone()
        if running:
            return running["id"]
        job_id = secrets.token_hex(8)
        c.execute("INSERT INTO jobs(id,project_id,kind,status,progress,message,created,updated) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (job_id, project["id"], kind, "running", 0, "排队中…", _now(), _now()))
    threading.Thread(target=_job_thread, args=(job_id, kind, project, options), daemon=True).start()
    return job_id


# ============================================================ HTTP 路由
def _project_brief(row):
    return {
        "id": row["id"], "title": row["title"], "scriptName": row["script_name"],
        "words": len(row["script_text"] or ""), "created": row["created"], "updated": row["updated"],
        "seriesId": row["series_id"] if "series_id" in row.keys() else None,
        "episode": row["episode"] if "episode" in row.keys() else None,
        "has": {"analysis": bool(row["analysis"]), "scenes": bool(row["scenes"]), "budget": bool(row["budget"])},
    }


def _create_series(handler, owner, series_title, parts, errors):
    """建一个剧集组 + 每集一个项目。parts=[(集号, 文件名/集名, 文本)]。"""
    sid = secrets.token_hex(8)
    now = _now()
    with _db() as c:
        c.execute("INSERT INTO series(id,owner,title,created,updated) VALUES(?,?,?,?,?)",
                  (sid, owner, series_title, now, now))
        for ep, name, text in sorted(parts, key=lambda x: x[0]):
            c.execute("INSERT INTO projects(id,owner,title,script_name,script_text,meta,"
                      "series_id,episode,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (secrets.token_hex(8), owner, "%s 第%d集" % (series_title, ep),
                       name[:120], text, "{}", sid, ep, now, now))
    out = {"ok": True, "series": {"id": sid, "title": series_title, "epCount": len(parts)}}
    if errors:
        out["warnings"] = errors[:10]
    return handler._json(out)


def _series_payload(c, srow):
    """剧集组详情：组信息 + 各集 brief（按集号排）。"""
    eps = [(_project_brief(r)) for r in c.execute(
        "SELECT * FROM projects WHERE series_id=? ORDER BY episode", (srow["id"],)).fetchall()]
    return {"id": srow["id"], "title": srow["title"], "created": srow["created"],
            "episodes": eps, "epCount": len(eps),
            "scenesDone": sum(1 for e in eps if e["has"]["scenes"])}


def _get_project(pid, owner):
    with _db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND owner=?", (pid, owner)).fetchone()
    return dict(row) if row else None


def _qs(path):
    """解析 query string（标准库 urllib.parse，别手撕）。"""
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(path).query)
    return {k: v[0] for k, v in q.items()}


def handle(handler, method, path):
    """server.py 的唯一入口：/api/production/* 全部进这里。
    人才库（行业人才数据库）是制作板块下的第二个工具，走 /api/production/talent/* 子路由——
    这样 server.py 不必新增钩子（互不干扰：和发行板块那个并行 context 零交叉）。"""
    init()
    try:
        if path.split("?")[0].startswith("/api/production/talent"):
            import talent  # 制作 context 专属：行业人才库（豆瓣真实数据，零 AI 编造）
            return talent.handle(handler, method, path)
        return _route(handler, method, path)
    except Exception as e:
        return handler._json({"ok": False, "error": "制作板块内部错误：%s" % e}, 500)


def _route(handler, method, path):
    p = path.split("?")[0]

    if method == "GET":
        q = _qs(path)
        if p == "/api/production/projects":
            owner = _resolve_owner(handler, q)
            if not owner:
                return handler._json({"ok": True, "projects": [], "series": []})
            with _db() as c:
                rows = c.execute("SELECT * FROM projects WHERE owner=? AND series_id IS NULL "
                                 "ORDER BY updated DESC", (owner,)).fetchall()
                srows = c.execute("SELECT * FROM series WHERE owner=? ORDER BY updated DESC",
                                  (owner,)).fetchall()
                series = [_series_payload(c, s) for s in srows]
            return handler._json({"ok": True, "projects": [_project_brief(r) for r in rows],
                                  "series": series})

        if p == "/api/production/series":
            owner = _resolve_owner(handler, q)
            with _db() as c:
                srow = c.execute("SELECT * FROM series WHERE id=? AND owner=?",
                                 (q.get("id", ""), owner or "")).fetchone()
                if not srow:
                    return handler._json({"ok": False, "error": "剧集不存在或无权限"}, 404)
                return handler._json({"ok": True, "series": _series_payload(c, srow)})

        if p == "/api/production/series_scenes":
            # 总场景表：把各集的分场表按集号串起来返回（顺场归组由前端实时做，和单集一致）
            owner = _resolve_owner(handler, q)
            with _db() as c:
                srow = c.execute("SELECT * FROM series WHERE id=? AND owner=?",
                                 (q.get("id", ""), owner or "")).fetchone()
                if not srow:
                    return handler._json({"ok": False, "error": "剧集不存在或无权限"}, 404)
                rows = c.execute("SELECT episode, scenes FROM projects WHERE series_id=? ORDER BY episode",
                                 (srow["id"],)).fetchall()
            all_scenes, missing = [], []
            for r in rows:
                if not r["scenes"]:
                    missing.append(r["episode"])
                    continue
                try:
                    sc = json.loads(r["scenes"]).get("scenes") or []
                except Exception:
                    continue
                for s in sc:
                    s["ep"] = r["episode"]  # 总表里集号以项目为准
                all_scenes.extend(sc)
            return handler._json({"ok": True, "title": srow["title"], "scenes": all_scenes,
                                  "missingEpisodes": missing})

        if p == "/api/production/project":
            owner = _resolve_owner(handler, q)
            proj = _get_project(q.get("id", ""), owner) if owner else None
            if not proj:
                return handler._json({"ok": False, "error": "项目不存在或无权限"}, 404)
            out = _project_brief_full(proj)
            return handler._json({"ok": True, "project": out})

        if p == "/api/production/job":
            with _db() as c:
                row = c.execute("SELECT * FROM jobs WHERE id=?", (q.get("id", ""),)).fetchone()
            if not row:
                return handler._json({"ok": False, "error": "任务不存在"}, 404)
            return handler._json({"ok": True, "job": {
                "id": row["id"], "kind": row["kind"], "status": row["status"],
                "progress": row["progress"], "message": row["message"], "error": row["error"]}})

        return handler._json({"ok": False, "error": "未知接口"}, 404)

    # ---------- POST ----------
    body = handler._body()
    owner = _resolve_owner(handler, body)
    if not owner:
        return handler._json({"ok": False, "error": "缺少设备标识（请刷新页面重试）"}, 400)

    if p == "/api/production/upload":
        filename = (body.get("filename") or "剧本.txt").strip()[:120]
        title = (body.get("title") or "").strip()[:80] \
            or re.sub(r"\.(txt|docx|fdx|md|pdf|zip)$", "", filename, flags=re.I)
        raw = None
        if body.get("fileB64"):
            import base64
            try:
                raw = base64.b64decode(body["fileB64"])
            except Exception:
                return handler._json({"ok": False, "error": "文件数据损坏，请重新上传"}, 400)
            if len(raw) > 60 * 1024 * 1024:
                return handler._json({"ok": False, "error": "文件超过 60MB"}, 400)

        # ---- ZIP：解包 → 多个文件=每个文件一集；单个文件=继续走单文件逻辑 ----
        if raw is not None and filename.lower().endswith(".zip"):
            try:
                entries = _zip_entries(raw)
            except zipfile.BadZipFile:
                return handler._json({"ok": False, "error": "ZIP 包打不开（文件可能损坏）"}, 400)
            if not entries:
                return handler._json({"ok": False, "error": "ZIP 里没找到剧本文件（支持 .pdf/.txt/.docx/.fdx）"}, 400)
            if len(entries) > 60:
                return handler._json({"ok": False, "error": "ZIP 里文件太多（最多 60 个）"}, 400)
            if len(entries) == 1:
                filename, raw = entries[0]  # 单文件 zip：拆包后按普通单文件处理（可能内部还分集）
            else:
                parts, errors = [], []
                for i, (name, data) in enumerate(entries):
                    t, e = parse_script_file(name, data)
                    if e:
                        errors.append("%s：%s" % (name, e))
                        continue
                    m = re.search(r"(\d+)", name)
                    ep = int(m.group(1)) if m else (i + 1)
                    parts.append((ep, name, t))
                if not parts:
                    return handler._json({"ok": False, "error": "ZIP 里的文件都没解析成功：" + "；".join(errors[:3])}, 400)
                # 集号去重：文件名抓出来的数字撞了就按顺序重排
                if len(set(x[0] for x in parts)) != len(parts):
                    parts = [(i + 1, n, t) for i, (_, n, t) in enumerate(parts)]
                return _create_series(handler, owner, title, parts, errors)

        # ---- 单文件（txt/docx/fdx/pdf 或前端直传文本）----
        if raw is not None:
            text, err = parse_script_file(filename, raw)
        else:
            text = re.sub(r"\r\n?", "\n", str(body.get("text") or "")).strip()
            err = None if len(text) >= 200 else "剧本内容太短（不足 200 字）"
            if text and len(text) > MAX_SCRIPT_CHARS:
                text, err = None, "剧本超过 %d 万字——请按集拆开上传" % (MAX_SCRIPT_CHARS // 10000)
        if err:
            return handler._json({"ok": False, "error": err}, 400)

        # ---- 多集检测：一个文件里装着"第1集/第2集…"→ 自动拆成一集一个项目 ----
        eps = split_episodes(text)
        if eps:
            parts = [(ep, "%s 第%d集" % (title, ep), t) for ep, t in eps]
            return _create_series(handler, owner, title, parts, [])

        pid = secrets.token_hex(8)
        with _db() as c:
            c.execute("INSERT INTO projects(id,owner,title,script_name,script_text,meta,created,updated) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (pid, owner, title, filename, text, "{}", _now(), _now()))
        # 顺手识别一下能不能切出场头，给前端展示预估
        _, hits = split_scenes(text)
        return handler._json({"ok": True, "project": {
            "id": pid, "title": title, "scriptName": filename,
            "words": len(text), "sceneHeads": hits}})

    if p == "/api/production/save":
        proj = _get_project(body.get("id", ""), owner)
        if not proj:
            return handler._json({"ok": False, "error": "项目不存在或无权限"}, 404)
        field = body.get("field")
        if field == "title":
            t = str(body.get("data") or "").strip()[:80]
            if not t:
                return handler._json({"ok": False, "error": "标题不能为空"}, 400)
            with _db() as c:
                c.execute("UPDATE projects SET title=?, updated=? WHERE id=?", (t, _now(), proj["id"]))
        elif field in ("analysis", "scenes", "budget", "meta"):
            _project_save_field(proj["id"], field, body.get("data") or {})
        else:
            return handler._json({"ok": False, "error": "不支持的字段"}, 400)
        return handler._json({"ok": True})

    if p == "/api/production/delete":
        proj = _get_project(body.get("id", ""), owner)
        if not proj:
            return handler._json({"ok": False, "error": "项目不存在或无权限"}, 404)
        with _db() as c:
            c.execute("DELETE FROM projects WHERE id=?", (proj["id"],))
            c.execute("DELETE FROM jobs WHERE project_id=?", (proj["id"],))
        return handler._json({"ok": True})

    if p == "/api/production/run":
        kind = body.get("kind")
        if kind not in _RUNNERS:
            return handler._json({"ok": False, "error": "未知任务类型"}, 400)
        if not _load_key():
            return handler._json({"ok": False, "error": "服务器未配置 DeepSeek key"}, 500)
        if kind == "series_scenes":
            with _db() as c:
                srow = c.execute("SELECT * FROM series WHERE id=? AND owner=?",
                                 (body.get("id", ""), owner)).fetchone()
            if not srow:
                return handler._json({"ok": False, "error": "剧集不存在或无权限"}, 404)
            job_id = start_job({"id": srow["id"]}, kind, body.get("options") or {})
            return handler._json({"ok": True, "jobId": job_id})
        proj = _get_project(body.get("id", ""), owner)
        if not proj:
            return handler._json({"ok": False, "error": "项目不存在或无权限"}, 404)
        job_id = start_job(proj, kind, body.get("options") or {})
        return handler._json({"ok": True, "jobId": job_id})

    if p == "/api/production/delete_series":
        with _db() as c:
            srow = c.execute("SELECT * FROM series WHERE id=? AND owner=?",
                             (body.get("id", ""), owner)).fetchone()
            if not srow:
                return handler._json({"ok": False, "error": "剧集不存在或无权限"}, 404)
            c.execute("DELETE FROM jobs WHERE project_id IN "
                      "(SELECT id FROM projects WHERE series_id=?)", (srow["id"],))
            c.execute("DELETE FROM jobs WHERE project_id=?", (srow["id"],))
            c.execute("DELETE FROM projects WHERE series_id=?", (srow["id"],))
            c.execute("DELETE FROM series WHERE id=?", (srow["id"],))
        return handler._json({"ok": True})

    return handler._json({"ok": False, "error": "未知接口"}, 404)


def _project_brief_full(proj):
    """项目详情：带三块结果 + 剧本摘要信息（全文不回传，太大；拆解都在后端做）。"""
    out = _project_brief(proj)
    for f in ("analysis", "scenes", "budget", "meta"):
        try:
            out[f] = json.loads(proj[f]) if proj[f] else None
        except Exception:
            out[f] = None
    text = proj["script_text"] or ""
    out["scriptExcerpt"] = text[:600]
    return out


if __name__ == "__main__":
    init()
    print("✓ 制作板块数据库已初始化：", DB_PATH)
    with _db() as c:
        n = c.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]
    print("  现有项目：%d 个" % n)
