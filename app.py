from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import re
from functools import wraps

app = Flask(__name__)
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
    conn.commit()                             # 保存改动
    conn.close()                              # 关闭连接
import re

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
        return "用户名只能用字母、数字、下划线"
    return None   # 返回 None 表示"检查通过"

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

@app.route("/board")
@login_required
def board():
    return "留言板开发中……（下一阶段来做）"

@app.route("/logout")
def logout():
    session.clear()                # 清空登录状态
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, port=8080)