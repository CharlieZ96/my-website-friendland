# ai_bot.py —— 负责调用 AI，并存放 bot 的人设
# 以后想换模型（比如换成 DeepSeek），只要改这个文件，app.py 完全不用动。
#
# 出错时会在日志里打印「是哪家、HTTP 几号错误、对方说了什么」，查问题用：
#   journalctl -u myflask --since "30 min ago" --no-pager | grep 【
# 常见错误号：401 = key 不对；402 = DeepSeek 余额用完；429 = 调用太频繁 / 免费额度用完；
#            404 = 模型名不存在；400 = 请求参数不对

import os
import re
import requests

# 去 emoji / 符号表情
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF️‍]",
    flags=re.UNICODE)

# 去括号/星号里的动作、神态、心理活动：（翻个白眼）(叹气)*笑*【无奈】
_ACT_RE = re.compile(r"（[^（）]*）|\([^()]*\)|\*[^*]*\*|【[^】]*】")

def _clean(text):
    text = _EMOJI_RE.sub("", text)     # 去 emoji
    text = _ACT_RE.sub("", text)       # 去括号/星号里的动作
    return text.strip()

# ============ 1. 配置 ============
# API key 从环境变量读，绝对不要把 key 直接写在代码里（会被 push 到 GitHub 泄露）
# key 前后不小心带了空格、换行、引号也照样能用
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip().strip('"').strip("'")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'")

# ====== DeepSeek（主力）======
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"

# ====== Gemini（备用）======
# 如果调用报错 "model not found / 404"，去 aistudio.google.com 点 "Get code"，
# 把它示例里的模型名字复制过来替换这里就行。
MODEL = "gemini-3.6-flash"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

TIMEOUT = (8, 25)      # (连上服务器最多等几秒, 等它回答最多等几秒)


# ============ 2. 人设（重点：把你朋友的真实发言样本填进来）============
# 样本越多、越真实，模仿得越像。先随便放几条占位，等下你换成真的。
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

# ====== 认识的人 & 该怎么对他们（记忆库，随时加）======
PEOPLE = {
    # 用户名(小写) : 这个人是谁 + JZH该用什么态度对TA（示例）
    "admin":  "站长。JZH 跟他说话比较随意，偶尔抬杠。"
}

def build_persona(username=None):
    tail = "\n\n【务必记住】每次只回1~2句短话，像发微信，别解释、别客气、别铺垫。保持你一贯怼人、阴阳怪气、爱抬杠的攻击性，该损就损。"
    if not username:
        return PERSONA + tail
    note = PEOPLE.get(username.lower())
    extra = f"\n\n【你正在和「{username}」对话】"
    if note:
        extra += f"\n你对TA的了解：{note}\n请用符合你们关系的语气回应。"
    else:
        extra += "\n（这个人你不太熟，正常唠就行。）"
    return PERSONA + extra + tail


# ============ 3. 调用函数（全项目共用这一个）============

def _hide_keys(text):
    """日志里绝不出现 key 本身"""
    for k in (DEEPSEEK_API_KEY, GEMINI_API_KEY):
        if k:
            text = text.replace(k, "***")
    return text


def _post(name, url, **kw):
    """发请求；不是 200 就抛出带「HTTP 号 + 对方原话」的错误，日志里一眼能看懂"""
    r = requests.post(url, timeout=TIMEOUT, **kw)
    if r.status_code != 200:
        raise RuntimeError(_hide_keys(f"{name} HTTP {r.status_code}: {r.text[:300]}"))
    return r.json()


def _ask_gemini(user_message, system_prompt, history, max_out=200):
    if not GEMINI_API_KEY:
        print("【Gemini没有key】环境变量 GEMINI_API_KEY 是空的")
        return None
    contents = []
    if history:
        for h in history:
            role = "model" if h.get("role") == "model" else "user"
            contents.append({"role": role, "parts": [{"text": h.get("text", "")}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_out,
            "temperature": 0.9,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    data = _post("Gemini", API_URL, params={"key": GEMINI_API_KEY}, json=body)
    cands = data.get("candidates")
    if not cands:
        # 被安全过滤拦下时没有候选回复，把原因打出来
        print("【Gemini没给回复】", _hide_keys(str(data.get("promptFeedback", data))[:300]))
        return None
    parts = cands[0].get("content", {}).get("parts", [])
    reply = "".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip()
    if not reply:
        print("【Gemini回复是空的】finishReason =", cands[0].get("finishReason"))
    return reply or None


def _ask_deepseek(user_message, system_prompt, history, max_tokens=150):
    if not DEEPSEEK_API_KEY:
        print("【DeepSeek没有key】环境变量 DEEPSEEK_API_KEY 是空的")
        return None
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for h in history:
            role = "assistant" if h.get("role") == "model" else "user"
            messages.append({"role": role, "content": h.get("text", "")})
    messages.append({"role": "user", "content": user_message})
    body = {"model": DEEPSEEK_MODEL, "messages": messages,
            "max_tokens": max_tokens, "temperature": 0.9,
            "thinking": {"type": "disabled"}}          # 关掉深度思考：回得快，也不会把字数用在思考上
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
               "Content-Type": "application/json"}
    data = _post("DeepSeek", DEEPSEEK_URL, headers=headers, json=body)
    choice = (data.get("choices") or [{}])[0]
    reply = ((choice.get("message") or {}).get("content") or "").strip()
    if not reply:
        print("【DeepSeek回复是空的】finish_reason =", choice.get("finish_reason"))
    return reply or None


def ask_ai(user_message, system_prompt=PERSONA, history=None):
    # 优先 DeepSeek（中文好、已付费）；它出错或没返回，再退回 Gemini 兜底
    try:
        reply = _ask_deepseek(user_message, system_prompt, history)
        if reply:
            return _clean(reply)
        print("【DeepSeek空回复，改试Gemini】")
    except Exception as e:
        print("【DeepSeek出错，改试Gemini】", _hide_keys(str(e)))
    try:
        reply = _ask_gemini(user_message, system_prompt, history)
        if reply:
            return _clean(reply)
        print("【Gemini也没返回】")
    except Exception as e:
        print("【Gemini出错】", _hide_keys(str(e)))
    return None
