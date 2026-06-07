#!/usr/bin/env python3
# 本地预览服务器：禁用缓存，避免反复编辑/更新时浏览器读到旧文件。仅本地用，不影响线上。
import http.server, socketserver, os, sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("", PORT), NoCache) as httpd:
    print("serving app/ (no-cache) on http://localhost:%d" % PORT)
    httpd.serve_forever()
