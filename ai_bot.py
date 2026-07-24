# ai_bot.py —— 负责调用 AI，并存放 bot 的人设
# 以后想换模型（比如换成 DeepSeek），只要改这个文件，app.py 完全不用动。
 
import os
import requests
import time
import re

import re

# 去 emoji / 符号表情
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF\U0001F1E6-\U0001F1FF\uFE0F\u200d]",
    flags=re.UNICODE)

# 去括号/星号里的动作、神态、心理活动：（翻个白眼）(叹气)*笑*【无奈】
_ACT_RE = re.compile(r"（[^（）]*）|\([^()]*\)|\*[^*]*\*|【[^】]*】")

def _clean(text):
    text = _EMOJI_RE.sub("", text)     # 去 emoji
    text = _ACT_RE.sub("", text)       # 去括号/星号里的动作
    return text.strip()

# ====== DeepSeek 备用接口 ======
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

# 熔断：Gemini 连续报错超过几次，就暂时改用 DeepSeek
GEM_FAIL_THRESHOLD = 3      # 连续错几次触发切换
GEM_COOLDOWN = 300          # 切换后多少秒内不再碰 Gemini
_gem_fails = 0
_gem_block_until = 0.0
 
# ============ 1. 配置 ============
# API key 从环境变量读，绝对不要把 key 直接写在代码里（会被 push 到 GitHub 泄露）
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
 
# 模型名。免费额度每天 1500 次。
# 如果调用报错 "model not found / 404"，去 aistudio.google.com 点 "Get code"，
# 把它示例里的模型名字复制过来替换这里就行。
MODEL = "gemini-3.6-flash"
 
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
 
 
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
    """在基础人设后追加'正在跟谁说话、对TA的了解'，让回复更拟真。"""
    if not username:
        return PERSONA
    note = PEOPLE.get(username.lower())
    extra = f"\n\n【你正在和「{username}」对话】"
    if note:
        extra += f"\n你对TA的了解：{note}\n请用符合你们关系的语气回应。"
    else:
        extra += "\n（这个人你不太熟，正常唠就行。）"
    return PERSONA + extra
 
 
# ============ 3. 调用函数（全项目共用这一个）============

def _ask_gemini(user_message, system_prompt, history):
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
            "maxOutputTokens": 300,
            "temperature": 0.9,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    r = requests.post(API_URL, params={"key": GEMINI_API_KEY}, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    cands = data.get("candidates")
    if not cands:
        return None
    parts = cands[0].get("content", {}).get("parts", [])
    reply = "".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip()
    return reply or None


def _ask_deepseek(user_message, system_prompt, history):
    if not DEEPSEEK_API_KEY:
        return None
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        for h in history:
            role = "assistant" if h.get("role") == "model" else "user"
            messages.append({"role": role, "content": h.get("text", "")})
    messages.append({"role": "user", "content": user_message})
    body = {"model": DEEPSEEK_MODEL, "messages": messages,
            "max_tokens": 300, "temperature": 0.9}
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
               "Content-Type": "application/json"}
    r = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip() or None


def ask_ai(user_message, system_prompt=PERSONA, history=None):
    # 优先 DeepSeek（中文好、已付费）；它出错或没返回，再退回 Gemini 兜底
    try:
        reply = _ask_deepseek(user_message, system_prompt, history)
        if reply:
            return _clean(reply)          # ← 改这里
        print("【DeepSeek空回复，改试Gemini】")
    except Exception as e:
        print("【DeepSeek出错，改试Gemini】", repr(e))
    try:
        reply = _ask_gemini(user_message, system_prompt, history)
        if reply:
            return _clean(reply)          # ← 改这里
        print("【Gemini也没返回】")
    except Exception as e:
        print("【Gemini出错】", repr(e))
    return None