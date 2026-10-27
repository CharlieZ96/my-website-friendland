"""
siteadmin.py —— 系统公告 + 注册审批

  · 系统公告：管理员在后台发公告（标题 + 正文 + 可选长图），所有玩家在 hub 右上角 🔔 看到，
    有未读时铃铛上显示红色数字；点开一条就算已读，也可以一键"全部已读"；
    图片直接显示在公告里，点一下全屏看长图。
  · 注册审批：新玩家提交注册后先进"待审批"名单，管理员通过后才会写进 users 表、才能登录。
    后台可以切换三种模式：需审批（默认）/ 直接开放 / 关闭注册。

只会新建 3 张表（site_notices / site_notice_reads / pending_users），
不改动任何旧表结构；审批通过时往 users 表插入一行，和原来注册时做的事一模一样。
"""
import os
import sqlite3
import i18n
import uuid
from datetime import datetime
from functools import wraps

from flask import (Blueprint, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from PIL import Image, ImageOps

DB = "users.db"
UPLOAD_FOLDER = "uploads"
ADMIN_USERNAME = "admin"
MAX_PENDING = 100                 # 待审批名单最多这么多人，防止被人刷注册
NOTICE_IMG_MAX_W = 1080           # 公告图最大宽度（长图只按宽度缩，高度保留）
NOTICE_KEEP = 50                  # 最多保留多少条公告（更早的连图片一起删）
MODES = {"approve": "需要管理员审批", "open": "直接开放注册", "closed": "暂停注册"}

bp = Blueprint("siteadmin", __name__)


def _connect():
    return sqlite3.connect(DB, timeout=15)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 建表
def ensure_tables():
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS site_notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                image TEXT,
                pinned INTEGER NOT NULL DEFAULT 0,
                author TEXT,
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS site_notice_reads (
                username TEXT NOT NULL,
                notice_id INTEGER NOT NULL,
                PRIMARY KEY (username, notice_id)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('register_mode', 'approve')")
        conn.commit()
    finally:
        conn.close()


def init(app, admin_username="admin", upload_folder="uploads"):
    """在 app.py 里调用一次：建表 + 注册公告/审批的路由"""
    global ADMIN_USERNAME, UPLOAD_FOLDER
    ADMIN_USERNAME, UPLOAD_FOLDER = admin_username, upload_folder
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    ensure_tables()
    app.register_blueprint(bp)


# ---------------------------------------------------------------- 权限
def _login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "username" not in session:
            if request.path.startswith("/notice/"):
                return jsonify(ok=False, msg="请先登录"), 401
            flash("请先登录")
            return redirect("/login")
        return f(*a, **k)
    return w


def _admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if session.get("username") != ADMIN_USERNAME:
            flash("只有管理员能进")
            return redirect("/hub")
        return f(*a, **k)
    return w


# ================================================================ 注册审批
def get_mode(conn=None):
    own = conn is None
    conn = conn or _connect()
    try:
        r = conn.execute("SELECT value FROM settings WHERE key='register_mode'").fetchone()
        return r[0] if r and r[0] in MODES else "approve"
    finally:
        if own:
            conn.close()


def handle_register(username, hashed_password, note=""):
    """
    代替原来 register() 里的 INSERT INTO users。
    返回 (ok, 提示文字, 下一步跳转到哪个页面: 'login' 或 'register')
    """
    conn = _connect()
    try:
        mode = get_mode(conn)
        if mode == "closed":
            return False, "注册暂时关闭了，请联系管理员", "register"
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return False, "这个用户名已经被注册了", "register"
        if mode == "open":
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            conn.commit()
            return True, "注册成功，请登录", "login"
        # 需要审批
        if conn.execute("SELECT 1 FROM pending_users WHERE username=?", (username,)).fetchone():
            return False, "这个用户名已经在等待审批了，请耐心等管理员通过", "register"
        n = conn.execute("SELECT COUNT(*) FROM pending_users").fetchone()[0]
        if n >= MAX_PENDING:
            return False, "申请的人太多了，请稍后再试", "register"
        conn.execute("INSERT INTO pending_users (username, password, note, created_at) VALUES (?,?,?,?)",
                     (username, hashed_password, (note or "")[:100], _now()))
        conn.commit()
        return True, "申请已提交 ✓ 管理员审批通过后就能登录了", "login"
    except sqlite3.IntegrityError:
        return False, "这个用户名已经被注册了", "register"
    finally:
        conn.close()


def pending_message(username):
    """登录失败时调用：如果这个名字还在等审批，返回提示；否则返回 None"""
    conn = _connect()
    try:
        r = conn.execute("SELECT 1 FROM pending_users WHERE username=?", ((username or "").strip(),)).fetchone()
        return "你的注册申请还在等管理员审批，通过后才能登录" if r else None
    finally:
        conn.close()


@bp.route("/admin/registrations", methods=["GET", "POST"])
@_admin_required
def admin_registrations():
    conn = _connect()
    try:
        if request.method == "POST":
            act = request.form.get("act")
            if act == "mode":
                mode = request.form.get("mode")
                if mode in MODES:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('register_mode', ?)", (mode,))
                    conn.commit()
                    flash(i18n.t("注册方式已改为：{mode}", mode=i18n.t(MODES[mode])))
            elif act in ("approve", "reject"):
                ids = request.form.getlist("pid") or []
                done, skipped = [], []
                for pid in ids:
                    row = conn.execute("SELECT username, password FROM pending_users WHERE id=?", (pid,)).fetchone()
                    if not row:
                        continue
                    name, pw = row
                    if act == "approve":
                        try:
                            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (name, pw))
                        except sqlite3.IntegrityError:
                            skipped.append(name)            # 这个名字已经有人了
                    conn.execute("DELETE FROM pending_users WHERE id=?", (pid,))
                    if name not in skipped:
                        done.append(name)
                conn.commit()
                if done:
                    flash(i18n.t("已通过：{names}" if act == "approve" else "已拒绝：{names}", names="、".join(done)))
                if skipped:
                    flash(i18n.t("这些名字已经被注册，申请已移除：{names}", names="、".join(skipped)))
            return redirect(url_for("siteadmin.admin_registrations"))
        rows = conn.execute("SELECT id, username, note, created_at FROM pending_users ORDER BY id").fetchall()
        mode = get_mode(conn)
    finally:
        conn.close()
    return render_template("admin_registrations.html", pending=rows, mode=mode, modes=MODES)


# ================================================================ 系统公告
def _save_notice_image(file):
    """长图友好：只把宽度缩到 1080，高度按比例保留；存成 JPG（长图用 PNG 太大）"""
    if not file or not file.filename or "." not in file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        return None
    try:
        img = Image.open(file)
        img = ImageOps.exif_transpose(img)        # 手机照片的方向
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img)
        img = img.convert("RGB")
        if img.width > NOTICE_IMG_MAX_W:
            img = img.resize((NOTICE_IMG_MAX_W, round(img.height * NOTICE_IMG_MAX_W / img.width)), Image.LANCZOS)
        name = "notice_" + uuid.uuid4().hex + ".jpg"
        img.save(os.path.join(UPLOAD_FOLDER, name), "JPEG", quality=85, optimize=True, progressive=True)
        return name
    except Exception:
        return None


def _remove_image(name):
    if name:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, name))
        except OSError:
            pass


@bp.route("/admin/registrations/count")
@_admin_required
def admin_registrations_count():
    """只给管理员后台设置页用：显示有几个人在等审批"""
    conn = _connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM pending_users").fetchone()[0]
    finally:
        conn.close()
    return jsonify(ok=True, pending=n)


@bp.route("/admin/notices", methods=["GET", "POST"])
@_admin_required
def admin_notices():
    conn = _connect()
    try:
        if request.method == "POST":
            act = request.form.get("act")
            if act == "create":
                title = (request.form.get("title") or "").strip()[:60]
                body = (request.form.get("body") or "").strip()[:5000]
                if not title:
                    flash("标题不能为空")
                    return redirect(url_for("siteadmin.admin_notices"))
                img = None
                f = request.files.get("image")
                if f and f.filename:
                    img = _save_notice_image(f)
                    if not img:
                        flash("图片没传成功（只支持 png/jpg/gif/webp）")
                        return redirect(url_for("siteadmin.admin_notices"))
                conn.execute("INSERT INTO site_notices (title, body, image, pinned, author, created_at) VALUES (?,?,?,?,?,?)",
                             (title, body, img, 1 if request.form.get("pinned") else 0, session["username"], _now()))
                # 只保留最近 NOTICE_KEEP 条
                old = conn.execute("SELECT id, image FROM site_notices ORDER BY id DESC LIMIT -1 OFFSET ?",
                                   (NOTICE_KEEP,)).fetchall()
                for oid, oimg in old:
                    conn.execute("DELETE FROM site_notices WHERE id=?", (oid,))
                    conn.execute("DELETE FROM site_notice_reads WHERE notice_id=?", (oid,))
                    _remove_image(oimg)
                conn.commit()
                flash("公告已发布，所有玩家都能看到了")
            elif act == "delete":
                row = conn.execute("SELECT image FROM site_notices WHERE id=?", (request.form.get("nid"),)).fetchone()
                if row:
                    conn.execute("DELETE FROM site_notices WHERE id=?", (request.form.get("nid"),))
                    conn.execute("DELETE FROM site_notice_reads WHERE notice_id=?", (request.form.get("nid"),))
                    conn.commit()
                    _remove_image(row[0])
                    flash("公告已删除")
            elif act == "pin":
                conn.execute("UPDATE site_notices SET pinned = 1 - pinned WHERE id=?", (request.form.get("nid"),))
                conn.commit()
            return redirect(url_for("siteadmin.admin_notices"))
        rows = conn.execute("SELECT id, title, body, image, pinned, created_at FROM site_notices "
                            "ORDER BY pinned DESC, id DESC").fetchall()
    finally:
        conn.close()
    return render_template("admin_notices.html", notices=rows)


@bp.route("/notice/list")
@_login_required
def notice_list():
    me = session["username"]
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, title, body, image, pinned, created_at FROM site_notices "
                            "ORDER BY pinned DESC, id DESC LIMIT 30").fetchall()
        read = {r[0] for r in conn.execute("SELECT notice_id FROM site_notice_reads WHERE username=?", (me,))}
        out = {"ok": True,
               "notices": [{"id": i, "title": t, "body": b, "pinned": bool(p), "time": c,
                            "image": ("/uploads/" + img) if img else None, "unread": i not in read}
                           for i, t, b, img, p, c in rows]}
    finally:
        conn.close()
    out["unread"] = sum(1 for n in out["notices"] if n["unread"])
    return jsonify(out)


@bp.route("/notice/read", methods=["POST"])
@_login_required
def notice_read():
    """{"id": 3} 标记一条已读；{"all": true} 全部已读"""
    me = session["username"]
    d = request.get_json(silent=True) or {}
    conn = _connect()
    try:
        if d.get("all"):
            ids = [r[0] for r in conn.execute("SELECT id FROM site_notices")]
        else:
            try:
                ids = [int(d.get("id"))]
            except (TypeError, ValueError):
                ids = []
        conn.executemany("INSERT OR IGNORE INTO site_notice_reads (username, notice_id) "
                         "SELECT ?, id FROM site_notices WHERE id=?", [(me, i) for i in ids])
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)
