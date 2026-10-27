"""
extract_private.py —— 从【旧版本】代码里把真实人设和台词提取出来，生成 private/jzh_private.py

只在第一次升级到新版本时用一次（新版本的代码里已经没有这些内容了）。

用法（在网站目录里，升级之前运行）：
    python3 extract_private.py --from-dir . --out ~/jzh-backup/jzh_private.py

会读取旧的 ai_bot.py（PERSONA、PEOPLE）、pet.py（SAYINGS）、templates/board.html（戳立绘的 LINES），
写成新版本 jzh_content.py 能直接读取的格式。缺哪一项就跳过哪一项，不会报错退出。
"""
import argparse
import ast
import json
import os
import re
import sys


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def assigned_literal(src, name):
    """在 Python 源码里找 `name = <字面量>`，返回它的值。"""
    if not src:
        return None
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


def poke_lines(html):
    """从旧 board.html 里取出 var LINES = [ ['smug','...'], ... ];"""
    if not html:
        return None
    m = re.search(r"var LINES = \[(.*?)\n\s*\];", html, re.S)
    if not m:
        return None
    out = []
    for mood, text in re.findall(r"\[\s*'(\w+)'\s*,\s*'((?:\\.|[^'\\])*)'\s*\]", m.group(1)):
        out.append([mood, text.replace("\\'", "'")])
    return out or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-dir", default=".", help="旧版本网站所在的目录")
    ap.add_argument("--out", required=True, help="输出文件，例如 ~/jzh-backup/jzh_private.py")
    a = ap.parse_args()
    d = a.from_dir

    data = {
        "PERSONA": assigned_literal(read(os.path.join(d, "ai_bot.py")), "PERSONA"),
        "PEOPLE": assigned_literal(read(os.path.join(d, "ai_bot.py")), "PEOPLE"),
        "PET_SAYINGS": assigned_literal(read(os.path.join(d, "pet.py")), "SAYINGS"),
        "POKE_LINES": poke_lines(read(os.path.join(d, "templates", "board.html"))),
    }
    found = {k: v for k, v in data.items() if v}
    if not found:
        print("一项都没找到。确认 --from-dir 指向的是【旧版本】的网站目录。")
        return 1

    out = os.path.expanduser(a.out)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# JZH 的私密内容（从旧版本提取）。这个文件只放在服务器上，不要提交到 Git。\n\n")
        for k, v in found.items():
            if isinstance(v, str):
                f.write(f'{k} = """{v}"""\n\n' if '"""' not in v else f"{k} = {v!r}\n\n")
            else:
                f.write(f"{k} = " + json.dumps(v, ensure_ascii=False, indent=4) + "\n\n")
    # 检查写出来的文件能不能正常读取
    ns = {}
    exec(compile(open(out, encoding="utf-8").read(), out, "exec"), ns)
    for k in found:
        assert ns[k] == found[k], k
    print("已生成", out)
    for k, v in found.items():
        print(f"  {k}: {len(v)} {'字' if isinstance(v, str) else '项'}")
    missing = [k for k in data if k not in found]
    if missing:
        print("  没找到：", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
