from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import re
from functools import wraps
from datetime import datetime
import os
import uuid
from PIL import Image, ImageOps
from flask import send_from_directory
import random

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)             # 没有这文件夹就自动建
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024    # 上传最大 8MB，超了拒绝
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}   # 只允许这些图片格式
app.secret_key = "change-me"
ADMIN_USERNAME = "admin"
DAILY_DRAW_LIMIT_ON = True     # True=开启每日限制；False=关闭（无限抽）
DAILY_DRAW_LIMIT = 1           # 每天可抽几次

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("username") != ADMIN_USERNAME:
            flash("只有管理员能进")
            return redirect(url_for("board"))
        return f(*args, **kwargs)
    return wrapper

def init_db():
    conn = sqlite3.connect("users.db")        # 连接(没有就自动创建)users.db 文件
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN last_draw TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN draw_count INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            image TEXT
        )
    """)
    # 给已存在的老 comments 表补 image 列（列已存在会报错，忽略即可）
    try:
        conn.execute("ALTER TABLE comments ADD COLUMN image TEXT")
    except sqlite3.OperationalError:
        pass
    # 卡牌本体
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            attack INTEGER NOT NULL,
            defense INTEGER NOT NULL,
            image TEXT,
            rarity TEXT NOT NULL,
            drop_rate REAL NOT NULL
        )
    """)
    
    # 玩家拥有的卡（支持重复，count 记数量）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            card_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # 默认设置：没有才插入（不会覆盖你后来改的）
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('draw_limit_on', '1')")
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('draw_limit_count', '1')")

    conn.commit()                             # 保存改动
    conn.close()                              # 关闭连接

def delete_post_comments(conn, post_id):
    # 删这帖的所有评论，连它们的图片文件也删
    for row in conn.execute("SELECT image FROM comments WHERE post_id = ?", (post_id,)).fetchall():
        if row[0]:
            path = os.path.join(UPLOAD_FOLDER, row[0])
            if os.path.exists(path):
                os.remove(path)
    conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))

def enforce_post_limit(conn):
    # 找出排在第 POST_LIMIT 名之后的旧帖（就是要删的）
    rows = conn.execute(
        "SELECT id, image FROM posts ORDER BY id DESC LIMIT -1 OFFSET ?",
        (POST_LIMIT,)
    ).fetchall()
    for pid, img in rows:
        if img:                                    # 有图就把图片文件也删掉
            path = os.path.join(UPLOAD_FOLDER, img)
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM posts WHERE id = ?", (pid,))
        conn.execute("DELETE FROM comments WHERE post_id = ?", (pid,))
        delete_post_comments(conn, pid)
    conn.commit()

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
    if img.mode != "RGBA":
        # 有透明通道：先铺一层白底，避免透明区域变黑
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img).convert("RGB")
        img.thumbnail((900, 900))

    # 3. 压缩：如果太宽，按比例缩到最大宽度 1080
    max_width = 1080
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))

    # 4. 生成一个绝不重名的文件名，统一存成 png
    new_name = uuid.uuid4().hex + ".png"
    path = os.path.join(UPLOAD_FOLDER, new_name)
    img.save(path, "PNG")
    
    return new_name

#def make_card(image_name):
    # -----------------------------
    # 1. 打开人物图片
    # -----------------------------
    character_path = os.path.join(UPLOAD_FOLDER, image_name)
    character = Image.open(character_path).convert("RGBA")

    # -----------------------------
    # 2. 打开卡框
    # -----------------------------
    frame = Image.open("frames/card.png").convert("RGBA")

    # -----------------------------
    # 3. 人物缩放
    # -----------------------------
    character.thumbnail((620, 620))

    # -----------------------------
    # 4. 新建透明画布
    # -----------------------------
    card = Image.new("RGBA", frame.size, (0, 0, 0, 0))

    # -----------------------------
    # 5. 把人物放进去
    # -----------------------------
    card.paste(character, (170, 120))

    # -----------------------------
    # 6. 再盖上卡框
    # -----------------------------
    #card.alpha_composite(frame)

    # -----------------------------
    # 7. 保存
    # -----------------------------
    new_name = uuid.uuid4().hex + ".png"

    save_path = os.path.join(UPLOAD_FOLDER, new_name)

    card.save(save_path)

    return new_name


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
        # 人机验证：核对算术答案
        answer = request.form.get("captcha", "").strip()
        if answer != str(session.get("captcha_answer")):
            flash("验证码算错了，请重新计算")
            return redirect(url_for("register"))
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
    # GET：生成一道新的算术题
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    session["captcha_answer"] = a + b               # 答案存 session
    return render_template("register.html", captcha_question=f"{a} + {b}")

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
    is_admin = (session["username"] == ADMIN_USERNAME)
    return render_template("hub.html", username=session["username"], is_admin=is_admin)

POST_LIMIT = 30

@app.route("/board", methods=["GET", "POST"])
@login_required
def board():
    if request.method == "POST":                       # 用户点了"发布"
        content = request.form.get("content", "").strip()
        image_name = None
        if not content and not image_name:
            flash("说点什么，或者配张图吧")
            return redirect(url_for("board"))
        if len(content) > 500:
            flash("内容太长了（最多500字）")
            return redirect(url_for("board"))
        is_anon = 1 if request.form.get("anonymous") else 0
        # 处理图片（从 request.files 拿，不是 request.form）
        
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
        enforce_post_limit(conn)        # ← 加这行：超上限就删最老的
        conn.close()
        return redirect(url_for("board"))
        # 发完刷新回留言板

    # GET：把所有帖子查出来，按 id 倒序（新的在最上面）
    conn = sqlite3.connect("users.db")
    cur = conn.execute("SELECT username, content, created_at, is_anon, image, id FROM posts ORDER BY id DESC")
    posts = cur.fetchall()

    # 查所有评论，按 post_id 分组
    comments = {}
    for c in conn.execute(
        "SELECT post_id, username, content, created_at, id, image FROM comments ORDER BY id ASC"
    ).fetchall():
        comments.setdefault(c[0], []).append(c)
    
    # 取所有评论，按 post_id 分组成字典 {帖子id: [评论, 评论...]}
    comments = {}
    for c in conn.execute("SELECT post_id, username, content, created_at, id FROM comments ORDER BY id ASC").fetchall():
        comments.setdefault(c[0], []).append(c)
    conn.close()
    return render_template("board.html", posts=posts, username=session["username"], comments=comments)

@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
@login_required
def delete_comment(comment_id):
    conn = sqlite3.connect("users.db")
    row = conn.execute("SELECT username, image FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if row and row[0] == session["username"]: # 只能删自己的
        if row[1]:                                   # 有图就把图片文件也删了
            path = os.path.join(UPLOAD_FOLDER, row[1])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("board"))

COMMENT_LIMIT = 8

@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
def add_comment(post_id):
    content = request.form.get("content", "").strip()
    # 处理图片
    image_name = None
    file = request.files.get("image")
    if file and file.filename:
        image_name = save_image(file)
        if image_name is None:
            flash("图片格式不支持")
            return redirect(url_for("board"))

    # 文字和图片至少要有一个（这就是"允许只发图"）
    if not content and not image_name:
        flash("评论不能全空白，写点字或配张图")
        return redirect(url_for("board"))
    # 文字长度校验
    if len(content) > 200:
        flash("评论太长了（最多200字）")
        return redirect(url_for("board"))

    conn = sqlite3.connect("users.db")
    # 先数这帖已有多少评论
    count = conn.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)).fetchone()[0]
    if count >= COMMENT_LIMIT:
        conn.close()
        flash(f"这条帖子的评论已满 {COMMENT_LIMIT} 条")
        return redirect(url_for("board"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute("INSERT INTO comments (post_id, username, content, created_at) VALUES (?, ?, ?, ?)",
                 (post_id, session["username"], content, now))
    conn.commit()
    conn.close()
    return redirect(url_for("board"))

@app.route("/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    conn = sqlite3.connect("users.db")
    cur = conn.execute("SELECT username, image FROM posts WHERE id = ?", (post_id,))
    row = cur.fetchone()
    if row and row[0] == session["username"]:      # 只能删自己的（服务端二次校验）
        if row[1]:                                  # 有图就把图片文件也删了
            img_path = os.path.join(UPLOAD_FOLDER, row[1])
            if os.path.exists(img_path):
                os.remove(img_path)
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        delete_post_comments(conn, post_id) 
        conn.commit()
    conn.close()
    return redirect(url_for("board"))


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
    folder = os.path.join("static", "gallery")          # 图片文件夹
    # 列出文件夹里所有图片文件
    files = [f for f in os.listdir(folder)
             if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))]
    if not files:
        return "gallery 文件夹里还没有图片，先放几张进去～"
    pick = random.choice(files)                          # 随机抽一张
    return render_template("jzhyl.html", image=pick)


@app.route("/admin/cards", methods=["GET", "POST"])
@admin_required
def admin_cards():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rarity = request.form.get("rarity", "N").strip()
        image_name = None
        file = request.files.get("image")
        if file and file.filename:
            image_name = save_image(file)          # 复用你的图片压缩
        # 数值校验
        try:
            attack = int(request.form.get("attack", "0"))
            defense = int(request.form.get("defense", "0"))
            drop_rate = float(request.form.get("drop_rate", "1"))
        except ValueError:
            flash("攻击/防御填整数，爆率填数字")
            return redirect(url_for("admin_cards"))
        if not name:
            flash("卡牌名不能为空")
            return redirect(url_for("admin_cards"))
        conn = sqlite3.connect("users.db")
        conn.execute(
            "INSERT INTO cards (name, attack, defense, image, rarity, drop_rate) VALUES (?, ?, ?, ?, ?, ?)",
            (name, attack, defense, image_name, rarity, drop_rate))
        conn.commit()
        conn.close()
        flash("卡牌已添加")
        return redirect(url_for("admin_cards"))

    # GET：列出所有卡
    conn = sqlite3.connect("users.db")
    cards = conn.execute(
        "SELECT id, name, attack, defense, image, rarity, drop_rate FROM cards ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_cards.html", cards=cards)

@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        old_pw = request.form.get("old_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        conn = sqlite3.connect("users.db")
        row = conn.execute("SELECT password FROM users WHERE username = ?",
                           (session["username"],)).fetchone()

        # 核对原密码
        if not row or not check_password_hash(row[0], old_pw):
            conn.close()
            flash("原密码不对")
            return redirect(url_for("change_password"))
        # 新密码校验
        if len(new_pw) < 6:
            conn.close()
            flash("新密码至少 6 位")
            return redirect(url_for("change_password"))
        if new_pw != confirm:
            conn.close()
            flash("两次新密码不一致")
            return redirect(url_for("change_password"))

        # 更新（存的是加密后的）
        conn.execute("UPDATE users SET password = ? WHERE username = ?",
                     (generate_password_hash(new_pw), session["username"]))
        conn.commit()
        conn.close()
        session.clear()                          # 先清空登录状态 = 登出
        flash("密码修改成功，请用新密码登录")      # 清空后再 flash，消息才不会被一起清掉
        return redirect(url_for("login"))        # 跳到登录页

    return render_template("change_password.html")

@app.route("/draw", methods=["GET", "POST"])
@login_required
def draw():
    result = None
    conn = sqlite3.connect("users.db")
    today = datetime.now().strftime("%Y-%m-%d")

    # 读设置
    lo = conn.execute("SELECT value FROM settings WHERE key='draw_limit_on'").fetchone()
    lc = conn.execute("SELECT value FROM settings WHERE key='draw_limit_count'").fetchone()
    limit_on = (lo and lo[0] == '1')
    limit_count = int(lc[0]) if lc else 1

    # 读该用户今天抽了几次
    row = conn.execute("SELECT last_draw, draw_count FROM users WHERE username = ?",
                       (session["username"],)).fetchone()
    last_draw = row[0] if row else None
    draw_count = row[1] if row and row[1] is not None else 0
    if last_draw != today:
        draw_count = 0

    if request.method == "POST":
        # 达上限就拦
        if limit_on and draw_count >= limit_count:
            conn.close()
            flash(f"今天的抽卡次数用完啦（每天 {limit_count} 次），明天再来～")
            return redirect(url_for("draw"))

        # 取所有卡
        cards = conn.execute(
            "SELECT id, name, attack, defense, image, rarity, drop_rate FROM cards").fetchall()
        if not cards:
            conn.close()
            flash("还没有卡牌可抽")
            return redirect(url_for("draw"))

        # 加权随机抽一张（这里生成 picked）
        weights = [c[6] for c in cards]
        picked = random.choices(cards, weights=weights, k=1)[0]
        card_id = picked[0]

        # 加进收藏
        existing = conn.execute(
            "SELECT id FROM user_cards WHERE username = ? AND card_id = ?",
            (session["username"], card_id)).fetchone()
        if existing:
            conn.execute("UPDATE user_cards SET count = count + 1 WHERE id = ?", (existing[0],))
        else:
            conn.execute("INSERT INTO user_cards (username, card_id, count) VALUES (?, ?, 1)",
                         (session["username"], card_id))

        # 计数+1，记下今天
        draw_count += 1
        conn.execute("UPDATE users SET last_draw = ?, draw_count = ? WHERE username = ?",
                     (today, draw_count, session["username"]))
        conn.commit()
        result = picked

    conn.close()
    can_draw = (not limit_on) or (draw_count < limit_count)
    remaining = (limit_count - draw_count) if limit_on else None
    return render_template("draw.html", result=result, can_draw=can_draw,
                           remaining=remaining, limit_on=limit_on)

@app.route("/collection")
@login_required
def collection():
    conn = sqlite3.connect("users.db")
    rows = conn.execute("""
        SELECT c.name, c.attack, c.defense, c.image, c.rarity, uc.count
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.username = ?
        ORDER BY uc.id DESC
    """, (session["username"],)).fetchall()
    conn.close()
    return render_template("collection.html", cards=rows)

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    conn = sqlite3.connect("users.db")
    if request.method == "POST":
        limit_on = "1" if request.form.get("draw_limit_on") else "0"    # 勾了=1
        try:
            limit_count = str(max(1, int(request.form.get("draw_limit_count", "1"))))
        except ValueError:
            limit_count = "1"
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('draw_limit_on', ?)", (limit_on,))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('draw_limit_count', ?)", (limit_count,))
        conn.commit()
        conn.close()
        flash("设置已保存")
        return redirect(url_for("admin_settings"))
    lo = conn.execute("SELECT value FROM settings WHERE key='draw_limit_on'").fetchone()
    lc = conn.execute("SELECT value FROM settings WHERE key='draw_limit_count'").fetchone()
    conn.close()
    return render_template("admin_settings.html",
                           limit_on=(lo and lo[0] == '1'),
                           limit_count=int(lc[0]) if lc else 1)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=False, port=5000)