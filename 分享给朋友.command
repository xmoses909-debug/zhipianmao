#!/bin/bash
# 制片帽 · 分享给朋友（免费 Cloudflare 隧道，免备案）
#   双击运行 → 启动后端 + 开公网隧道 → 把吐出的网址发给朋友，他们打开就能用。
#   ⚠️ 运行期间：本机要开着、本窗口别关。关掉窗口 = 停止分享。
#   原理：把你本机的后端"捅"到公网（不是局域网），所以朋友在任何地方都能访问。
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"

# 选一个空闲端口（避开已占用的）
PORT=4173
for p in 4173 4174 4175 8000; do
  if ! lsof -i :"$p" >/dev/null 2>&1; then PORT=$p; break; fi
done

echo "═══════════════════════════════════════════════"
echo "   制片帽 · 分享模式（让朋友远程试用）"
echo "═══════════════════════════════════════════════"
echo "   ⚠️ 这个窗口别关——一关，朋友就打不开了。"
echo "   正在启动，请等十几秒…"
echo ""

# 1) 后台启动后端（同时供着网页 + AI 接口）
python3 backend/server.py "$PORT" >/tmp/zhipianmao_backend.log 2>&1 &
BACKEND_PID=$!
# 窗口关闭 / Ctrl+C 时，顺手把后端也停掉
trap 'echo ""; echo "已停止分享。"; kill $BACKEND_PID 2>/dev/null' EXIT HUP INT TERM

# 等后端起来 + 自检
sleep 3
if curl -s -o /dev/null "http://localhost:${PORT}/"; then
  echo "   ✓ 后端已就绪（本机端口 $PORT）"
else
  echo "   ✗ 后端没起来——多半是 backend/key.txt 还没填 key。"
  echo "     排查日志：/tmp/zhipianmao_backend.log"
fi
echo ""
echo "   正在开公网隧道，网址马上出现在下面 👇"
echo "───────────────────────────────────────────────"

# 2) 开 Cloudflare 快速隧道；盯着输出，抓到 trycloudflare 网址就高亮提示
cloudflared tunnel --url "http://localhost:${PORT}" 2>&1 | while IFS= read -r line; do
  echo "$line"
  u=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com')
  if [ -n "$u" ]; then
    echo ""
    echo "   ┌──────────────────────────────────────────────"
    echo "   │  ✅ 把下面这个网址发给朋友（手机 / 电脑都能开）："
    echo "   │"
    echo "   │       $u"
    echo "   │"
    echo "   │  朋友点「进入制片帽」后要等约 100 秒，是正常的。"
    echo "   └──────────────────────────────────────────────"
    echo ""
  fi
done
