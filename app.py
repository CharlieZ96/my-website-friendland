from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import re
from functools import wraps
from datetime import datetime
import os
import uuid
from PIL import Image
from flask import send_from_directory

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)             # 没有这文件夹就自动建
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024    # 上传最大 8MB，超了拒绝
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}   # 只允许这些图片格式
app.secret_key = "change-me"

def init_db():
    conn = sqlite3.connect("users.db")        # 连接(没有就自动创建)users.db 文件
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_anon INTEGER NOT NULL DEFAULT 0,
            image TEXT
        )
    """)
    conn.commit()                             # 保存改动
    conn.close()                              # 关闭连接


def validate(username, password):
    username = (username or "").strip()      # 去掉首尾空格
    password = password or ""
    if not username or not password:          # 空的不行
        return "用户名和密码不能为空"
    if len(username) < 2 or len(username) > 20:   # 长度限制
        return "用户名需 2~20 个字符"
    if len(password) < 6:
        return "密码至少 6 位"
    if not re.match(r'^[A-Za-z0-9_\u4e00-\u9fff]+$', username):   # 白名单：只准字母数字下划线
        return "用户名只能用中文、字母、数字、下划线"
    return None   # 返回 None 表示"检查通过"

def save_image(file, target_kb=120):
    # 1. 检查扩展名是不是允许的图片格式
    if "." not in file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()   # 取最后一个点后面的后缀
    if ext not in ALLOWED_EXT:
        return None                                  # 不是图片，拒绝

    # 2. 用 Pillow 打开图片
    img = Image.open(file)
    img = img.convert("RGB")     # 转成RGB，避免PNG透明通道存JPEG时报错
    img.thumbnail((900, 900))

    # 3. 压缩：如果太宽，按比例缩到最大宽度 1080
    max_width = 1080
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))

    # 4. 生成一个绝不重名的文件名，统一存成 jpg
    new_name = uuid.uuid4().hex + ".jpg"
    path = os.path.join(UPLOAD_FOLDER, new_name)

    # 从质量 85 开始，太大就一档档降，直到小于目标体积
    quality = 85
    while quality >= 30:
        img.save(path, "JPEG", quality=quality, optimize=True)
        if os.path.getsize(path) <= target_kb * 1024:
            break                 # 已经够小了，停
        quality -= 10             # 还太大，再压狠一点

    return new_name    # 返回文件名，存进数据库


init_db()                                     # 程序一启动就执行一次
# 守卫：贴在哪个页面上，哪个页面就必须登录才能进
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:          # 没登录
            flash("请先登录")
            return redirect(url_for("login"))  # 踢回登录页
        return f(*args, **kwargs)
    return wrapper

@app.route("/")          # 访问网站首页时
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])       # ← 改动1:允许 GET 和 POST
def register():
    if request.method == "POST":                       # ← 改动2:如果是"提交"来的
        username = request.form["username"]            # ← 取出用户名
        password = request.form["password"]            # ← 取出密码
         # ↓↓↓ 就在这里调用 validate，这是你缺的一步 ↓↓↓
        error = validate(username, password)
        if error:                        # 有错误信息 = 没通过
            flash(error)
            return redirect(url_for("register"))
        username = username.strip()      # 通过后用清理过的值
        # ↑↑↑ 校验通过才往下走 ↑↑↑
        hashed = generate_password_hash(password)
        conn = sqlite3.connect("users.db")
        try:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, hashed)
            )
            conn.commit()
            conn.close()
        except sqlite3.IntegrityError:                # 用户名重复会触发
            flash("这个用户名已经被注册了")
            return redirect(url_for("register"))
        flash("注册成功，请登录")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        conn = sqlite3.connect("users.db")
        cur = conn.execute("SELECT password FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row[0], password):   # 核对密码
            session["username"] = username             # 记住登录状态
            return redirect(url_for("hub"))           # 跳去第4页
        else:
            flash("用户名或密码错误")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/hub")
@login_required
def hub():
    return render_template("hub.html", username=session["username"])

@app.route("/board", methods=["GET", "POST"])
@login_required
def board():
    if request.method == "POST":                       # 用户点了"发布"
        content = request.form.get("content", "").strip()
        if not content and not image_name:
            flash("说点什么，或者配张图吧")
            return redirect(url_for("board"))
        if len(content) > 500:
            flash("内容太长了（最多500字）")
            return redirect(url_for("board"))
        is_anon = 1 if request.form.get("anonymous") else 0
        # 处理图片（从 request.files 拿，不是 request.form）
        image_name = None
        file = request.files.get("image")
        if file and file.filename:              # 用户确实选了图
            image_name = save_image(file)
            if image_name is None:              # 格式不对
                flash("图片格式不支持（只能 png/jpg/gif/webp）")
                return redirect(url_for("board"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = sqlite3.connect("users.db")
        conn.execute("INSERT INTO posts (username, content, created_at, is_anon, image) VALUES (?, ?, ?, ?, ?)",
                     (session["username"], content, now, is_anon, image_name))
        conn.commit()
        conn.close()
        return redirect(url_for("board"))              # 发完刷新回留言板

    # GET：把所有帖子查出来，按 id 倒序（新的在最上面）
    conn = sqlite3.connect("users.db")
    cur = conn.execute("SELECT username, content, created_at, is_anon, image FROM posts ORDER BY id DESC")
    posts = cur.fetchall()
    conn.close()
    return render_template("board.html", posts=posts, username=session["username"])

@app.route("/uploads/<name>")
def uploaded_file(name):
    return send_from_directory(UPLOAD_FOLDER, name)

@app.route("/logout")
def logout():
    session.clear()                # 清空登录状态
    return redirect(url_for("home"))

@app.route("/jzhyl")
@login_required
def jzhyl():
    return render_template("jzhyl.html")

if __name__ == "__main__":
    app.run(debug=True, port=8080)