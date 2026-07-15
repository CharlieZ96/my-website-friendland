from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/")          # 访问网站首页时
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])       # ← 改动1:允许 GET 和 POST
def register():
    if request.method == "POST":                       # ← 改动2:如果是"提交"来的
        username = request.form["username"]            # ← 取出用户名
        password = request.form["password"]            # ← 取出密码
        print("有人注册 → 用户名:", username, " 密码:", password)   # ← 打印到终端看看
        return f"注册收到啦!你填的用户名是:{username}"           # ← 回一句话给浏览器
    return render_template("register.html")            # ← 否则(是GET/打开页面):显示表单

if __name__ == "__main__":
    app.run(debug=True, port=8080)