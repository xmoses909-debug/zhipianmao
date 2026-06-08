#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 制片帽 · 数据层（SQLite，纯标准库 sqlite3）—— 生产版"真账号 + 收藏"的底座
#   为什么是 SQLite：Python 自带、零依赖、单文件、小规模够用；将来量大可平滑换 Postgres。
#   存什么：用户(密码加盐哈希)、登录会话、收藏、点赞反馈、口味偏好。
#   线程安全：http.server 是多线程的，这里用一把全局锁 + 每次开/关连接，简单稳妥。
#   ⚠️ data.db 是用户数据，已加入 .gitignore，不进 git。
import sqlite3
import os
import hashlib
import secrets
import time
import json
import threading
import contextlib

# 数据库位置：默认 backend/data.db；可用环境变量 ZPM_DB 覆盖（测试/部署时指定）
DB_PATH = os.environ.get("ZPM_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")
_lock = threading.Lock()


@contextlib.contextmanager
def _db():
    """开连接→干活→提交→关闭，全程上锁。用法：with _db() as c: c.execute(...)"""
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    """建表（幂等）。服务启动时调一次。"""
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT UNIQUE NOT NULL,
            pw_hash   TEXT NOT NULL,
            salt      TEXT NOT NULL,
            profile   TEXT,            -- 口味偏好 JSON
            created   REAL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token     TEXT PRIMARY KEY,
            user_id   INTEGER NOT NULL,
            created   REAL
        );
        CREATE TABLE IF NOT EXISTS favorites(
            user_id   INTEGER NOT NULL,
            book_id   TEXT NOT NULL,
            book      TEXT,            -- 书的完整 JSON（便于"我的收藏"直接展示）
            created   REAL,
            PRIMARY KEY(user_id, book_id)
        );
        CREATE TABLE IF NOT EXISTS feedback(
            user_id   INTEGER NOT NULL,
            book_id   TEXT NOT NULL,
            value     TEXT,            -- 'up' / 'down'
            created   REAL,
            PRIMARY KEY(user_id, book_id)
        );
        """)


# ---------- 密码：pbkdf2 加盐哈希（标准库，安全够用）----------
def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), 120000).hex()


# ---------- 账号 ----------
def register(username, pw):
    """注册。成功返回 ({token,username}, None)；失败返回 (None, 错误说明)。"""
    username = (username or "").strip()
    if not username or not pw:
        return None, "用户名和密码都要填"
    if len(username) > 30:
        return None, "用户名太长了"
    if len(pw) < 4:
        return None, "密码太短（至少 4 位）"
    salt = secrets.token_hex(16)
    with _db() as c:
        try:
            c.execute("INSERT INTO users(username,pw_hash,salt,profile,created) VALUES(?,?,?,?,?)",
                      (username, _hash(pw, salt), salt, "", time.time()))
        except sqlite3.IntegrityError:
            return None, "这个用户名已经被注册了，换一个吧"
    return login(username, pw)


def login(username, pw):
    """登录校验。成功返回 ({token,username}, None)；失败返回 (None, 错误)。"""
    username = (username or "").strip()
    with _db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not u or u["pw_hash"] != _hash(pw or "", u["salt"]):
            return None, "用户名或密码不对"
        token = secrets.token_hex(24)
        c.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)",
                  (token, u["id"], time.time()))
    return {"token": token, "username": username}, None


def logout(token):
    with _db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_by_token(token):
    """凭会话 token 取用户（dict）；无效返回 None。"""
    if not token:
        return None
    with _db() as c:
        r = c.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,)).fetchone()
    return dict(r) if r else None


# ---------- 口味偏好 ----------
def save_profile(user_id, profile):
    with _db() as c:
        c.execute("UPDATE users SET profile=? WHERE id=?",
                  (json.dumps(profile, ensure_ascii=False), user_id))


def get_profile(user_id):
    with _db() as c:
        r = c.execute("SELECT profile FROM users WHERE id=?", (user_id,)).fetchone()
    try:
        return json.loads(r["profile"]) if r and r["profile"] else None
    except Exception:
        return None


# ---------- 收藏 / 点赞 ----------
def set_favorite(user_id, book_id, book, on):
    with _db() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO favorites(user_id,book_id,book,created) VALUES(?,?,?,?)",
                      (user_id, book_id, json.dumps(book or {}, ensure_ascii=False), time.time()))
        else:
            c.execute("DELETE FROM favorites WHERE user_id=? AND book_id=?", (user_id, book_id))


def set_feedback(user_id, book_id, value):
    with _db() as c:
        if value in ("up", "down"):
            c.execute("INSERT OR REPLACE INTO feedback(user_id,book_id,value,created) VALUES(?,?,?,?)",
                      (user_id, book_id, value, time.time()))
        else:
            c.execute("DELETE FROM feedback WHERE user_id=? AND book_id=?", (user_id, book_id))


def get_user_state(user_id):
    """取该用户的收藏 + 点赞，给前端还原界面。"""
    with _db() as c:
        favs = {}
        for r in c.execute("SELECT book_id,book FROM favorites WHERE user_id=?", (user_id,)):
            try:
                favs[r["book_id"]] = json.loads(r["book"]) if r["book"] else {}
            except Exception:
                favs[r["book_id"]] = {}
        fb = {r["book_id"]: r["value"]
              for r in c.execute("SELECT book_id,value FROM feedback WHERE user_id=?", (user_id,))}
    return {"fav": favs, "feedback": fb}


def stats():
    """简单统计（给你后台看：多少用户、多少收藏）。"""
    with _db() as c:
        nu = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        nf = c.execute("SELECT COUNT(*) n FROM favorites").fetchone()["n"]
    return {"users": nu, "favorites": nf}


if __name__ == "__main__":
    init()
    print("✓ 数据库已初始化：", DB_PATH)
    print("  当前统计：", stats())
