#!/bin/bash
# 帽制片 · 选题雷达 —— 双击启动
cd "$(dirname "$0")"

echo "正在启动 帽制片 · 选题雷达 ..."

if command -v python3 >/dev/null 2>&1; then
  # 自动挑一个没被占用的端口
  PORT=4173
  for p in 4173 4174 4175 4176 8000 8080; do
    if ! lsof -i :"$p" >/dev/null 2>&1; then PORT=$p; break; fi
  done
  ( sleep 1 && open "http://localhost:${PORT}/" ) &
  echo "浏览器将自动打开 http://localhost:${PORT}/"
  echo "（关闭本终端窗口即可停止服务）"
  python3 -m http.server "${PORT}" --directory app
else
  echo "未找到 python3，改为直接用浏览器打开 index.html ..."
  open "app/index.html"
fi
