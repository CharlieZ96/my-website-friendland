"""
检查英文版有没有漏翻。在项目根目录运行：python3 tools/i18n_check.py

1. 模板里 _("...") 和 __("...") 用到、但 translations_en.py 里没有的句子
2. 模板里还没包进 _() / __() 的中文（粗略检查，注释和 {% if lang == 'en' %} 的中文分支不算）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from translations_en import EN  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
CALL = re.compile(r"""(?<![A-Za-z0-9_$.])(?:_|__)\(\s*(["'])((?:\\.|(?!\1).)*)\1""", re.S)
ALLOWED = {"中文", "语言"}  # 语言切换按钮本身就该写中文
# 这些中文是程序里的“数据值”（拿来比较、当 key 用），故意不翻译；显示的地方用 __() 包着
DATA_KEYS = {"普通", "稀有", "史诗", "传奇", "下注成功", "无", "光头", "帐篷", "木制棚屋", "小屋",
             "钓具", "鱼", "江之岛", "上岛屋", "JR江之岛"}


def strip_noise(text):
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)(?<![:\"'\\])//.*$", "", text)
    # {% if lang == 'en' %} 英文 {% else %} 中文 {% endif %}：中文分支是故意写死的
    text = re.sub(r"(\{%-?\s*if lang == 'en'\s*-?%\}.*?\{%-?\s*else\s*-?%\}).*?(\{%-?\s*endif\s*-?%\})",
                  r"\1\2", text, flags=re.S)
    return text


def main():
    missing, raw = {}, {}
    tpl_dir = os.path.join(ROOT, "templates")
    for name in sorted(os.listdir(tpl_dir)):
        if not name.endswith(".html"):
            continue
        text = open(os.path.join(tpl_dir, name), encoding="utf-8").read()
        for m in CALL.finditer(text):
            key = m.group(2).encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8") \
                if "\\" in m.group(2) else m.group(2)
            if CJK.search(key) and key not in EN:
                missing.setdefault(key, set()).add(name)
        rest = CALL.sub("", strip_noise(text))
        for line_no, line in enumerate(rest.splitlines(), 1):
            if "console." in line or ".test(" in line:      # 调试输出、正则不算界面文字
                continue
            for run in re.findall(r"[^\s<>\"'`{}=;:,()\[\]+]*[\u4e00-\u9fff][^\s<>\"'`{}=;:,()\[\]+]*", line):
                if run not in ALLOWED and run not in DATA_KEYS:
                    raw.setdefault(name, []).append(run)
    for key, files in sorted(missing.items()):
        print(f"[缺英文] {key!r}  ← {', '.join(sorted(files))}")
    for name, runs in raw.items():
        print(f"[没包 _()] {name}: {' | '.join(runs[:12])}{' …' if len(runs) > 12 else ''}")
    print(f"\n缺英文 {len(missing)} 句；没包 _() 的模板 {len(raw)} 个。")
    return 1 if missing or raw else 0


if __name__ == "__main__":
    sys.exit(main())
