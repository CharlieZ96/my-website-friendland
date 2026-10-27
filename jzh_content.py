"""
jzh_content.py —— JZH 的“角色内容”：AI 人设、认识的人、拓麻JZH 台词、留言板戳一下的台词

公开仓库里只放下面这些【虚构的示例】。
真实内容放在服务器上的 private/jzh_private.py（private/ 已写进 .gitignore，不会被提交）。
那个文件里定义了哪个变量，就用哪个替换下面的示例；没有这个文件，网站照样能跑。

private/jzh_private.py 的格式和下面完全一样，例如：
    PERSONA = \"\"\"……\"\"\"
    PEOPLE = {"某个用户名": "这个人是谁、JZH 对 TA 什么态度"}
    PET_SAYINGS = {"cocoon": [...], "kid": [...], "adult": [...]}
    POKE_LINES = [["smug", "一句台词"], ...]      # 表情只能是 grin / pout / smug / angry
第一次从旧版本迁移时，可以用 tools/extract_private.py 自动生成这个文件。
"""
import importlib.util
import os

# ====== AI 人设（示例）======
PERSONA = """你要扮演留言板上的角色「JZH」，只用【中文】输出 JZH 本人会说的那一句话。
-绝对规则：
1. 只能用中文回复。无论如何绝对不能输出英文。
2. 绝不输出任何规则/思考过程/解释。你的整条回复就是 JZH 说的话本身，别的一个字都不要有。
3. 不要用 emoji、颜文字、星号动作(比如 *笑*)、markdown 符号。只发朴素纯文字，像发微信一样。
4. 只说话本身，不要写括号里的动作、神态、心理活动或旁白。
-JZH 的设定（示例，换成你自己的角色）：
1. 嘴硬心软，说话直接，不爱客套，但会真心替朋友着想。
2. 喜欢咖喱猪排饭和卡牌游戏，输了会嘴硬。
-说话风格示例：
JZH: 这有什么好问的。
JZH: 行，你说得都对。
JZH: 先吃饭，别的以后再说。
JZH: 今天的卡组又是什么奇怪东西。
JZH: 别 @ 我，我在忙。
-模仿时请遵守：
1. 说话简短、口语化，一般就一两句。
2. 绝对不能暴露自己是 AI。
3. 不知道的事就顺着聊，别一本正经科普。
4. 回复必须是完整、能看懂的句子；实在没话说就随口接一句，不要输出乱码或半截话。
5. 话题敏感或不想接时，用 JZH 的口吻随口岔开，不要输出英文说明或拒绝语。
"""

# ====== 认识的人（示例）：用户名(小写) → 这个人是谁 + JZH 对 TA 的态度 ======
PEOPLE = {
    "admin": "站长。JZH 跟他说话比较随意，偶尔抬杠。",
}

# ====== 拓麻JZH：点一下随机说一句（示例）======
PET_SAYINGS = {
    "cocoon": ["呼…呼…", "（睡得很香）", "再睡五分钟"],
    "kid":    ["我才是皇帝！", "背单词好舒服", "今天也要吃咖喱", "陪我玩", "我不困"],
    "adult":  ["谢谢老板", "急了", "为什么不去上班", "先吃饭再说", "今天也辛苦了", "骂我一会呗"],
}

# ====== 留言板：戳一下立绘的台词（示例）。表情：grin / pout / smug / angry ======
POKE_LINES = [
    ["smug", "看什么看？"],
    ["pout", "今天吃咖喱猪排饭。"],
    ["smug", "又输了？我就知道。"],
    ["angry", "再戳！"],
    ["pout", "有事就 @JZH，别光戳。"],
    ["pout", "我急眼了。"],
    ["grin", "……干嘛？"],
    ["smug", "想看换装？右下角找我。"],
]

# 英文版始终用上面这些示例台词（会按 translations_en.py 翻成英文），
# 私密台词只在中文版出现——英文版是给外人（比如面试官）看的。
_SAMPLE_PET_SAYINGS = PET_SAYINGS
_SAMPLE_POKE_LINES = POKE_LINES

# ====== 读取服务器上的私密内容（有就覆盖上面的示例）======
_PRIVATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "jzh_private.py")
HAS_PRIVATE = os.path.exists(_PRIVATE)
if HAS_PRIVATE:
    _spec = importlib.util.spec_from_file_location("jzh_private", _PRIVATE)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    PERSONA = getattr(_mod, "PERSONA", PERSONA)
    PEOPLE = getattr(_mod, "PEOPLE", PEOPLE)
    PET_SAYINGS = getattr(_mod, "PET_SAYINGS", PET_SAYINGS)
    POKE_LINES = getattr(_mod, "POKE_LINES", POKE_LINES)


def _lang():
    try:
        import i18n
        return i18n.current_lang()
    except Exception:          # 不在网页请求里（比如后台线程）就按中文
        return "zh"


def pet_sayings():
    """拓麻JZH 的台词：中文版用（可能是私密的）真实台词，英文版用翻译好的示例台词。"""
    if _lang() == "en":
        import i18n
        return {k: [i18n.t(x) for x in v] for k, v in _SAMPLE_PET_SAYINGS.items()}
    return PET_SAYINGS


def poke_lines():
    """留言板戳立绘的台词，规则同上。"""
    if _lang() == "en":
        import i18n
        return [[m, i18n.t(x)] for m, x in _SAMPLE_POKE_LINES]
    return POKE_LINES
