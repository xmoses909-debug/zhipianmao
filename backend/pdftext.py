#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · PDF 文本提取（纯标准库，零第三方依赖）—— 制作板块专属文件
#
# 为什么自己写：剧本常以 PDF 交付（Word/WPS 导出的"文本型 PDF"），而 Python 标准库不带 PDF 解析。
# 装 pypdf 会破坏本项目"零依赖、双击就能跑"的底线，所以手写一个**够用**的提取器：
#   - 支持 FlateDecode（zlib，标准库就有）压缩的内容流 —— Word/WPS 导出的主流情况
#   - 支持 ObjStm（PDF 1.5 对象流）—— 新版 Word 导出几乎都用，不解这个就拿不到对象
#   - 支持 ToUnicode CMap（bfchar/bfrange）—— 中文 PDF 的文字全靠它映射回 Unicode
#   - 不支持：加密 PDF、扫描图片型 PDF（没有文字层）、罕见 filter —— 这些直接报清楚，请用户转格式
#
# 实现取舍：不走 xref 表按需取对象，而是**线性扫描全文件所有对象**（容错强：xref 坏了也能读）；
# 页序按 Page 对象在文件中的出现顺序（绝大多数生成器按页序写入；剧本场景下足够）。
import re
import zlib
import unicodedata


class PDFTextError(Exception):
    """提取失败时抛出，message 直接给用户看（中文、说人话）。"""


# CJK 部首补充区 → 正常汉字（NFKC 不覆盖这一区；只列子集字体里常见出没的）
_RADICAL_FIX = str.maketrans({
    0x2E80: "丶", 0x2E84: "乙", 0x2E85: "亻", 0x2E88: "刀", 0x2E8B: "卩", 0x2E8C: "小",
    0x2E8D: "小", 0x2E95: "彑", 0x2E97: "忄", 0x2EA0: "歺", 0x2EA4: "爫", 0x2EA7: "牛",
    0x2EAA: "疒", 0x2EAE: "礻", 0x2EB1: "罒", 0x2EB2: "罒", 0x2EB7: "羽", 0x2EBC: "月",
    0x2EBE: "艹", 0x2EBF: "艹", 0x2EC0: "艹", 0x2EC4: "虎", 0x2EC6: "衤", 0x2EC8: "见",
    0x2ECA: "角", 0x2ECC: "讠", 0x2ECD: "讠", 0x2ECF: "贝", 0x2ED0: "车", 0x2ED1: "辶",
    0x2ED2: "辶", 0x2ED3: "辶", 0x2ED4: "门", 0x2ED5: "门", 0x2ED6: "邑", 0x2ED7: "酉",
    0x2ED8: "釆", 0x2ED9: "里", 0x2EDA: "钅", 0x2EDB: "钅", 0x2EDD: "长", 0x2EDE: "长",
    0x2EE0: "门", 0x2EE2: "阝", 0x2EE5: "青", 0x2EE6: "韦", 0x2EE7: "页", 0x2EE8: "风",
    0x2EE9: "飞", 0x2EEA: "食", 0x2EEB: "饣", 0x2EED: "马", 0x2EEE: "骨", 0x2EEF: "鬼",
    0x2EF0: "鱼", 0x2EF1: "鱼", 0x2EF2: "鸟", 0x2EF3: "龟",
})


# ---------------------------------------------------------------- 底层：对象池
_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", re.S)
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def _collect_objects(data):
    """线性扫描出所有 'N G obj ... endobj'，返回 {对象号: 对象体bytes}。
    再把 ObjStm（压缩对象流）里打包的对象解出来并入——新版 Word/WPS 的字体和页面对象都藏在里面。"""
    objs = {}
    for m in _OBJ_RE.finditer(data):
        objs[int(m.group(1))] = m.group(3)
    # 展开对象流：/Type /ObjStm，流内先是 "对象号 偏移" 对表，后跟对象本体们
    for num in list(objs.keys()):
        body = objs[num]
        if b"/ObjStm" not in body:
            continue
        try:
            raw = _stream_bytes(body)
            n = int(_dict_value(body, b"N") or 0)
            first = int(_dict_value(body, b"First") or 0)
            head = raw[:first].split()
            for i in range(n):
                onum = int(head[2 * i])
                off = first + int(head[2 * i + 1])
                end = first + int(head[2 * i + 3]) if i + 1 < n else len(raw)
                objs.setdefault(onum, raw[off:end])
        except Exception:
            continue  # 单个对象流坏了不致命，能解多少解多少
    return objs


def _stream_bytes(body):
    """取对象里的 stream 原始数据；FlateDecode 则解压。其他 filter 不认 → 抛错让上层跳过。"""
    m = _STREAM_RE.search(body)
    if not m:
        raise PDFTextError("无流数据")
    raw = m.group(1)
    if b"/FlateDecode" in body:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            # 有些生成器在 stream 后面多/少一个换行，掐头去尾再试一次
            try:
                return zlib.decompress(raw.strip(b"\r\n"))
            except zlib.error:
                raise PDFTextError("流解压失败")
    if b"/Filter" in body and b"/FlateDecode" not in body:
        raise PDFTextError("不支持的压缩方式")
    return raw


def _dict_value(body, key):
    """从对象体里抓 /Key 数字 这种简单值。"""
    m = re.search(rb"/" + key + rb"\s+(\d+)", body)
    return m.group(1) if m else None


def _ref_value(body, key):
    """抓 /Key N 0 R 间接引用的对象号。"""
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", body)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- ToUnicode CMap
_HEX_PAIR = re.compile(rb"<([0-9A-Fa-f]+)>")


def _parse_tounicode(cmap_bytes):
    """解析 ToUnicode CMap → {字节码int: unicode字符串}。支持 bfchar 和 bfrange 两种段。"""
    table = {}

    def u16(hexstr):
        # CMap 的目标值是 UTF-16BE（可能是多个 code unit，如代理对/组合字）
        b = bytes.fromhex(hexstr.decode())
        try:
            return b.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""

    for m in re.finditer(rb"beginbfchar(.*?)endbfchar", cmap_bytes, re.S):
        pairs = _HEX_PAIR.findall(m.group(1))
        for i in range(0, len(pairs) - 1, 2):
            table[int(pairs[i], 16)] = u16(pairs[i + 1])
    for m in re.finditer(rb"beginbfrange(.*?)endbfrange", cmap_bytes, re.S):
        seg = m.group(1)
        # 两种形态：<lo> <hi> <dst>  或  <lo> <hi> [<d1> <d2> ...]
        for r in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", seg):
            lo, hi = int(r.group(1), 16), int(r.group(2), 16)
            dst = r.group(3)
            if dst.startswith(b"["):
                arr = _HEX_PAIR.findall(dst)
                for i, h in enumerate(arr):
                    if lo + i <= hi:
                        table[lo + i] = u16(h)
            else:
                base_hex = dst.strip(b"<>")
                base = int(base_hex, 16)
                width = len(base_hex)
                for c in range(lo, min(hi, lo + 65535) + 1):
                    table[c] = u16(b"%0*X" % (width, base + (c - lo)))
    return table


def _font_maps(objs, page_body):
    """页面 /Resources /Font 里每个字体名 → (ToUnicode表 或 None, 是否双字节码)。"""
    fonts = {}
    # Resources 可能内联在页对象里，也可能是间接引用
    res = page_body
    rref = _ref_value(page_body, b"Resources")
    if rref and rref in objs:
        res = objs[rref]
    fm = re.search(rb"/Font\s*<<(.*?)>>", res, re.S)
    font_src = fm.group(1) if fm else res
    for m in re.finditer(rb"/(\w+)\s+(\d+)\s+\d+\s+R", font_src):
        name, onum = m.group(1).decode(), int(m.group(2))
        if onum not in objs:
            continue
        fbody = objs[onum]
        if b"/Font" not in fbody and b"/Type0" not in fbody and b"/TrueType" not in fbody and b"/Type1" not in fbody:
            continue
        two_byte = b"/Type0" in fbody  # Type0(CID) 字体的字符码是 2 字节——中文 PDF 的标配
        table = None
        tref = _ref_value(fbody, b"ToUnicode")
        if tref and tref in objs:
            try:
                table = _parse_tounicode(_stream_bytes(objs[tref]))
            except Exception:
                table = None
        fonts[name] = (table, two_byte)
    return fonts


# ---------------------------------------------------------------- 内容流 → 文本
_ESCAPES = {b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b", b"f": "\f",
            b"(": "(", b")": ")", b"\\": "\\"}


def _decode_pdf_string(sbytes, font):
    """把 PDF 字符串字节按当前字体解码成文本。"""
    table, two_byte = font if font else (None, False)
    if table:
        out, step = [], 2 if two_byte else 1
        for i in range(0, len(sbytes) - step + 1, step):
            code = int.from_bytes(sbytes[i:i + step], "big")
            ch = table.get(code)
            out.append(ch if ch is not None else "")
        return "".join(out)
    # 无 ToUnicode：英文/西文 PDF 直接按 latin-1 凑合（中文没映射表就救不回来）
    return sbytes.decode("latin-1", "ignore")


def _iter_strings(segment):
    """从内容流片段里依序产出 (start, end, bytes) 的字符串字面量：(..) 与 <..>。手写小扫描器处理转义。"""
    i, n = 0, len(segment)
    while i < n:
        c = segment[i:i + 1]
        if c == b"(":
            j, depth, buf = i + 1, 1, bytearray()
            while j < n and depth:
                ch = segment[j:j + 1]
                if ch == b"\\":
                    nxt = segment[j + 1:j + 2]
                    if nxt in _ESCAPES:
                        buf += _ESCAPES[nxt].encode("latin-1")
                        j += 2
                    elif nxt.isdigit():  # \ooo 八进制
                        oct_s = segment[j + 1:j + 4]
                        oct_m = re.match(rb"[0-7]{1,3}", oct_s)
                        buf.append(int(oct_m.group(0), 8) & 0xFF)
                        j += 1 + len(oct_m.group(0))
                    else:
                        j += 2
                elif ch == b"(":
                    depth += 1; buf += ch; j += 1
                elif ch == b")":
                    depth -= 1
                    if depth:
                        buf += ch
                    j += 1
                else:
                    buf += ch; j += 1
            yield (i, j, bytes(buf))
            i = j
        elif c == b"<" and segment[i + 1:i + 2] != b"<":
            j = segment.find(b">", i)
            if j < 0:
                break
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", segment[i + 1:j])
            if len(hx) % 2:
                hx += b"0"
            yield (i, j + 1, bytes.fromhex(hx.decode()))
            i = j + 1
        else:
            i += 1


def _content_to_text(content, fonts):
    """解析一段页面内容流：跟踪 Tf 字体切换、Td/TD/T* 换行，把 Tj/TJ/'/" 的字符串解码拼接。"""
    out = []
    for bt in re.finditer(rb"BT(.*?)ET", content, re.S):
        seg = bt.group(1)
        # 先把所有"事件"（字体切换/换行算子/字符串）按位置排好序再回放
        events = []
        for m in re.finditer(rb"/(\w+)\s+[\d.]+\s+Tf", seg):
            events.append((m.start(), "font", m.group(1).decode()))
        for m in re.finditer(rb"(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]", seg):
            if abs(float(m.group(2))) > 0.01:  # y 方向有位移 = 换行
                events.append((m.start(), "nl", None))
        for m in re.finditer(rb"T\*", seg):
            events.append((m.start(), "nl", None))
        strs = list(_iter_strings(seg))
        # 字符串要知道它后面跟的算子是不是文字算子（Tj/TJ/'/"），简化：只要在 TJ 数组里或后面 30 字节内出现 Tj/'/" 就算
        for (s, e, b) in strs:
            tail = seg[e:e + 30]
            if re.match(rb"\s*(Tj|'|\")", tail) or _in_tj_array(seg, s):
                events.append((s, "str", b))
        events.sort(key=lambda x: x[0])
        cur_font, line = None, []
        for _, kind, val in events:
            if kind == "font":
                cur_font = fonts.get(val)
            elif kind == "nl":
                if line:
                    out.append("".join(line)); line = []
                else:
                    out.append("")
            else:
                line.append(_decode_pdf_string(val, cur_font))
        if line:
            out.append("".join(line))
        out.append("")  # BT 块间空行
    return "\n".join(out)


def _in_tj_array(seg, pos):
    """该字符串是否处在 [ ... ] TJ 数组里：往前找最近的 [ 或 ]，往后找最近的 ] TJ。粗但够用。"""
    open_b = seg.rfind(b"[", max(0, pos - 2000), pos)
    close_b = seg.rfind(b"]", max(0, pos - 2000), pos)
    if open_b < 0 or close_b > open_b:
        return False
    after = seg.find(b"]", pos)
    return after >= 0 and bool(re.match(rb"\s*TJ", seg[after + 1:after + 10]))


# ---------------------------------------------------------------- 入口
def extract_text(data):
    """PDF bytes → 文本。失败抛 PDFTextError（中文提示，直接给用户看）。"""
    if not data.startswith(b"%PDF"):
        raise PDFTextError("不是 PDF 文件（文件头不对）")
    if re.search(rb"/Encrypt\s+\d+\s+\d+\s+R", data[-4096:]) or b"/Encrypt" in data[:4096]:
        raise PDFTextError("这个 PDF 加了密，请先解除密码或导出为不加密版本")
    objs = _collect_objects(data)
    if not objs:
        raise PDFTextError("PDF 结构读不出来——请用 Word/WPS 打开后另存为 .docx 或 .txt 再传")
    pages = []
    for num in objs:
        body = objs[num]
        if re.search(rb"/Type\s*/Page\b", body) and b"/Pages" not in body[:200]:
            pages.append((num, body))
    if not pages:
        raise PDFTextError("PDF 里找不到页面对象——请转成 .docx 或 .txt 再传")
    texts = []
    for num, body in pages:
        try:
            fonts = _font_maps(objs, body)
            # /Contents 可能是单个引用或数组
            crefs = []
            cm = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", body)
            if cm:
                crefs.append(int(cm.group(1)))
            else:
                am = re.search(rb"/Contents\s*\[(.*?)\]", body, re.S)
                if am:
                    crefs = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", am.group(1))]
            content = b""
            for cr in crefs:
                if cr in objs:
                    try:
                        content += _stream_bytes(objs[cr]) + b"\n"
                    except PDFTextError:
                        continue
            if content:
                texts.append(_content_to_text(content, fonts))
        except Exception:
            texts.append("")  # 单页失败不毁整本
    text = "\n\n".join(texts)
    # NFKC 归一化：浏览器/办公软件的子集字体常把字映射到康熙部首区（"⽇"U+2F47 ≠ "日"U+65E5），
    # 看着一样但码位不同，会让后面的场头正则全部失配——必须规范回正常汉字。
    text = unicodedata.normalize("NFKC", text)
    # CJK 部首补充区（U+2E80-2EFF）没有 NFKC 映射，手工兜底最常见的形近字
    text = text.translate(_RADICAL_FIX)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if len(re.sub(r"\s", "", text)) < 100:
        raise PDFTextError("这个 PDF 提取不出文字（可能是扫描件/图片型 PDF）——请找文字版，或转成 .docx/.txt 再传")
    return text


if __name__ == "__main__":
    import sys
    with open(sys.argv[1], "rb") as f:
        print(extract_text(f.read())[:2000])
