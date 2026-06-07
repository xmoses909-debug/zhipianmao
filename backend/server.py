#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 本地 AI 后端
#   - 服务静态站点 app/（同源，无 CORS）
#   - POST /api/discover：把用户口味+需求 + 候选库 喂给 DeepSeek，返回真实的选品+改编分析
# key 从环境变量 DEEPSEEK_API_KEY 或 backend/.env 读取，绝不写进代码/仓库。
import http.server, socketserver, os, sys, json, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(os.path.dirname(ROOT), "app")
CORPUS_PATH = os.path.join(ROOT, "corpus.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


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
    "- 特别重视用户的「自定义需求」，据此挑选并解释；若候选库里实在没有贴合的，宁可少推、并在 note 里说明。\n"
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
        "genres": w.get("genres"), "status": w.get("status"), "wordCount": w.get("wordCount"),
        "tone": w.get("tone"), "synopsis": w.get("synopsis"),
    } for w in corpus]
    return (
        "制片人口味：\n"
        + "喜欢题材：" + "、".join(p.get("likes", []) or ["(未填)"]) + "\n"
        + "不感冒：" + "、".join(p.get("dislikes", []) or ["(无)"]) + "\n"
        + "体量偏好：" + "、".join(p.get("scale", []) or ["(不限)"]) + "；完结状态：" + str(p.get("status", "不限")) + "\n"
        + "【自定义需求 · 最重要】：" + (p.get("customWants", "").strip() or "（无，按题材口味挑即可）") + "\n\n"
        + "请挑选最多 " + str(count) + " 部，按综合分从高到低。\n\n"
        + "候选作品库(JSON)：\n" + json.dumps(lib, ensure_ascii=False)
    )


def call_deepseek(key, system, user):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=150) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def discover(profile, count=3):
    key = load_key()
    if not key:
        return {"ok": False, "error": "未配置 DEEPSEEK_API_KEY —— 请在 backend/.env 里填好你的 key"}
    corpus = load_corpus()
    if not corpus:
        return {"ok": False, "error": "候选库 corpus.json 为空或读取失败"}
    try:
        raw = call_deepseek(key, SYSTEM_PROMPT, build_user_msg(profile, corpus, count))
        out = json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        return {"ok": False, "error": "DeepSeek 接口报错 %d：%s" % (e.code, detail)}
    except Exception as e:
        return {"ok": False, "error": "调用失败：" + str(e)}

    byid = {w["id"]: w for w in corpus}
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
    return {"ok": True, "picks": picks, "note": out.get("note", ""), "model": MODEL}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=APP_DIR, **k)

    def log_message(self, fmt, *args):
        pass  # 安静

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/discover":
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            payload = {}
        print(">> /api/discover  自定义需求:", (payload.get("profile") or {}).get("customWants", "")[:40])
        result = discover(payload.get("profile", {}), int(payload.get("count", 3) or 3))
        if result.get("ok"):
            print("   ✓ 返回", len(result.get("picks", [])), "部")
        else:
            print("   ✗", result.get("error"))
        out = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200 if result.get("ok") else 400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
    has_key = "✓ 已配置" if load_key() else "✗ 未配置(去 backend/.env 填)"
    print("制片帽 · AI 后端启动")
    print("  网址      http://localhost:%d" % PORT)
    print("  模型      %s" % MODEL)
    print("  DEEPSEEK_API_KEY  %s" % has_key)
    print("  候选库    %d 部" % len(load_corpus()))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
