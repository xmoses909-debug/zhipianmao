#!/bin/bash
# 制片帽 · AI 模式启动（带 DeepSeek 大模型后端）
cd "$(dirname "$0")"

if grep -q "粘贴你的DeepSeek" backend/.env 2>/dev/null; then
  echo "⚠️  backend/.env 里还是占位符——请先打开 backend/.env 把你的 DeepSeek key 填进去，再启动。"
  echo ""
fi

PORT=4173
for p in 4173 4174 4175 8000; do if ! lsof -i :"$p" >/dev/null 2>&1; then PORT=$p; break; fi; done
( sleep 1 && open "http://localhost:${PORT}/" ) &
echo "浏览器将打开 http://localhost:${PORT}/  （关闭本窗口即停止）"
echo ""
python3 backend/server.py "$PORT"
