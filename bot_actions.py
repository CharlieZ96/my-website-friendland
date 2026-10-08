# bot_actions.py —— (JZH) bot 的所有行为逻辑
# 放在项目根目录，和 app.py、ai_bot.py 同一层。
# app.py 只需要 import 这里的几个函数并在几处调用即可。

import sqlite3
import json
import random
import re
import time
import threading
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash

from ai_bot import ask_ai, build_persona  # 复用你写好的调用函数（里面有人设 PERSONA）

DB = "users.db"
BOT_NAME = "JZH"            # bot 的账号名 / 大家 @ 的名字

# ====== 各种限额（想调就改这里）======
BOT_DAILY_LIMIT = 3000          # 全站每天最多调用 AI 多少次
BOT_MIN_INTERVAL = 2           # 两次 AI 调用至少间隔几秒
BOT_CHAT_DAILY_PER_USER = 200   # 每个用户每天最多找 JZH 聊几句
BOARD_BUBBLE_CHANCE = 0.15     # 发新帖时，JZH 随机冒泡回复的概率（0.15 = 15%）
BATTLE_WAIT_SECONDS = 8        # 匹配等待超过几秒，JZH 才可能来陪打
BATTLE_JOIN_CHANCE = 0.5       # 超时后每次轮询，JZH 加入的概率
COMMENT_LIMIT = 8              # 和 app.py 里的一致：每帖评论上限

# @JZH 的写法：@JZH、@jzh、＠JZH（中文输入法的全角＠）、@ JZH 都算
_SUMMON_RE = re.compile(r"[@＠]\s*jzh", re.I)


def is_summon(content):
    return bool(content) and bool(_SUMMON_RE.search(content))


def _connect():
    # timeout 大一点，避免后台线程写库时偶尔撞锁报错
    return sqlite3.connect(DB, timeout=15)


# ============ 启动时确保 bot 账号存在 ============
def ensure_bot():
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password, score) VALUES (?, ?, 0)",
            (BOT_NAME, generate_password_hash(uuid.uuid4().hex)))
        # 给 users 表补两列，记录每人每天的聊天次数（已存在会报错，忽略）
        for sql in (
            "ALTER TABLE users ADD COLUMN bot_chat_day TEXT",
            "ALTER TABLE users ADD COLUMN bot_chat_count INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ============ 限速闸（全站共用）============
def _getv(conn, k, d=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


def _setv(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))


def bot_gate(conn):
    """允许现在调用 AI 就返回 True，否则 False（同时管每日上限和最小间隔）。"""
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    day = _getv(conn, "bot_day")
    try:
        cnt = int(_getv(conn, "bot_count", "0") or 0)
    except ValueError:
        cnt = 0
    try:
        last = float(_getv(conn, "bot_last", "0") or 0)
    except ValueError:
        last = 0.0

    if day != today:          # 跨天了，计数清零
        day, cnt = today, 0

    if cnt >= BOT_DAILY_LIMIT:                 # 今天调用够多了
        _setv(conn, "bot_day", day); _setv(conn, "bot_count", cnt); conn.commit()
        return False
    if now - last < BOT_MIN_INTERVAL:          # 离上次太近，护住每分钟额度
        return False

    _setv(conn, "bot_day", day)
    _setv(conn, "bot_count", cnt + 1)
    _setv(conn, "bot_last", now)
    conn.commit()
    return True


def _gate_wait(conn, tries):
    """被点名时别因为"离上次太近"就放弃：最多等 tries 次、每次 1.2 秒再试。"""
    for i in range(tries):
        if bot_gate(conn):
            return True
        if i + 1 < tries:
            time.sleep(1.2)
    return False


def run_bg(fn, *args):
    """把一个函数丢到后台线程去跑，主请求不用等它。"""
    threading.Thread(target=fn, args=args, daemon=True).start()


# ============ 功能一：留言板 ============
def _do_board_reply(post_id, summoned, caller=None):
    """真正生成并写入 JZH 的评论。在后台线程里运行。
    caller = 点名 JZH 的人（发帖 / 评论的那个用户名），JZH 会按跟这个人的关系说话。"""
    conn = _connect()
    try:
        post = conn.execute("SELECT username, content, is_anon FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return
        author, content, is_anon = post
        if author == BOT_NAME:               # 不回自己发的帖
            return
        cnt = conn.execute("SELECT COUNT(*) FROM comments WHERE post_id=?", (post_id,)).fetchone()[0]
        # 评论满了就别插了；但被点名时，哪怕点名那条正好是第 8 条，也让 JZH 回最后一句
        if cnt >= COMMENT_LIMIT + (1 if summoned else 0):
            return
        # 随机冒泡时：如果这帖 JZH 已经回过，就不重复冒泡（被 @ 召唤则照回）
        if not summoned:
            already = conn.execute(
                "SELECT 1 FROM comments WHERE post_id=? AND username=? LIMIT 1",
                (post_id, BOT_NAME)).fetchone()
            if already:
                return
        if not _gate_wait(conn, 8 if summoned else 1):   # 撞限速：被点名就等一等，随机冒泡就算了
            print(f"【bot留言跳过】帖子{post_id} 撞上限速/每日上限")
            return

        # 跟谁说话：评论里点名 → 点名的人；否则是发帖人。匿名帖绝不透露发帖人是谁
        target = caller or author
        if is_anon and target == author:
            target = None
        recent = conn.execute(
            "SELECT username, content FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 4",
            (post_id,)).fetchall()
        ctx = " / ".join(f"{u}: {c}" for u, c in reversed(recent) if c) or "（暂无评论）"
        poster = "一个匿名网友" if is_anon else author
        if summoned:
            who = f"「{target}」在论坛点名 @JZH 找你。" if target else "有个匿名网友在论坛点名 @JZH 找你。"
        else:
            who = f"{poster}在论坛发了个帖子。"
        prompt = (f"{who}\n帖子（发帖人：{poster}）内容：「{content}」\n最近的评论：{ctx}\n"
                  f"请你以JZH的口吻，回一条简短的评论（只回评论本身）。")

        reply = ask_ai(prompt, system_prompt=build_persona(target))
        if not reply:
            print(f"【bot留言没生成出来】帖子{post_id}（往上看是哪家 AI 出错）")
            return
        reply = reply.strip()[:200]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute(
            "INSERT INTO comments (post_id, username, content, created_at, image) VALUES (?,?,?,?,?)",
            (post_id, BOT_NAME, reply, now, None))
        conn.commit()
    except Exception as e:
        print("【bot留言出错】", repr(e))
    finally:
        conn.close()


def on_new_post(post_id, content="", username=None):
    # 正贴里 @JZH 就必回；否则按概率随机冒泡
    if is_summon(content):
        run_bg(_do_board_reply, post_id, True, username)      # True=被召唤，必回
    elif random.random() < BOARD_BUBBLE_CHANCE:
        run_bg(_do_board_reply, post_id, False, username)     # False=随机冒泡


def on_new_comment(post_id, content, username=None):
    """发新评论后调用：如果 @了JZH 就让它回复（username = 发这条评论的人）。"""
    if is_summon(content) and username != BOT_NAME:
        run_bg(_do_board_reply, post_id, True, username)


# ============ 功能三：聊天气泡 ============
MEMORY_TURNS = 6      # 回复时带上该用户最近多少条对话（6 条 = 最近 3 来 3 回）
MEMORY_KEEP = 40       # 每个用户最多存多少条记忆（超了删最老的）

def chat(username, message, history):
    message = (message or "").strip()
    if not message:
        return {"reply": None, "error": "说点什么吧"}
    if len(message) > 200:
        message = message[:200]

    conn = _connect()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute("SELECT bot_chat_day, bot_chat_count FROM users WHERE username=?", (username,)).fetchone()
        day = row[0] if row else None
        used = row[1] if row and row[1] is not None else 0
        if day != today:
            used = 0
        if used >= BOT_CHAT_DAILY_PER_USER:
            return {"reply": None, "error": f"找JZH聊天（每天{BOT_CHAT_DAILY_PER_USER}条），明天再来～"}
        if not _gate_wait(conn, 3):
            return {"reply": None, "error": "JZH没空理你，找真的JZH去"}

        # 从数据库读这个用户最近的对话（跨刷新、跨设备都记得）
        mem = conn.execute(
            "SELECT role, text FROM bot_memory WHERE username=? ORDER BY id DESC LIMIT ?",
            (username, MEMORY_TURNS)).fetchall()
        hist = [{"role": r, "text": t} for r, t in reversed(mem)]
        while hist and hist[0]["role"] == "model":     # 对话记录从"用户说"开始，模型更不容易乱
            hist.pop(0)

        # 用"针对这个人的人设"来回复
        system = build_persona(username)
        reply = ask_ai(message, system_prompt=system, history=hist)
        if not reply:
            return {"reply": None, "error": "JZH没搭理你，再试一次？"}
        reply = reply.strip()[:300]

        # 把这轮对话存进记忆
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO bot_memory (username, role, text, created_at) VALUES (?,?,?,?)",
                     (username, "user", message, now))
        conn.execute("INSERT INTO bot_memory (username, role, text, created_at) VALUES (?,?,?,?)",
                     (username, "model", reply, now))
        # 每人只保留最近 MEMORY_KEEP 条，超出的删掉
        old = conn.execute(
            "SELECT id FROM bot_memory WHERE username=? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (username, MEMORY_KEEP)).fetchall()
        for (i,) in old:
            conn.execute("DELETE FROM bot_memory WHERE id=?", (i,))

        used += 1
        conn.execute("UPDATE users SET bot_chat_day=?, bot_chat_count=? WHERE username=?",
                     (today, used, username))
        conn.commit()
        return {"reply": reply, "remaining": BOT_CHAT_DAILY_PER_USER - used}
    except Exception as e:
        print("【bot聊天出错】", repr(e))
        return {"reply": None, "error": "出了点小问题，稍后再试"}
    finally:
        conn.close()


def chat_history(username, limit=20):
    """聊天框打开时，把和这个人最近的几句对话拿出来显示（不调用 AI）。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, text FROM bot_memory WHERE username=? ORDER BY id DESC LIMIT ?",
            (username, limit)).fetchall()
    finally:
        conn.close()
    return {"items": [{"role": r, "text": t} for r, t in reversed(rows)]}

# ============ 功能二：对战陪玩 ============
def maybe_bot_join(conn, mid, me):
    """等待中的局，超时且随机命中，就让 JZH 当对手。返回是否加入。"""
    row = conn.execute(
        "SELECT player1, player2, status, created_at FROM matches WHERE id=?", (mid,)).fetchone()
    if not row:
        return False
    p1, p2, status, created = row
    if status != "waiting" or p2 is not None or p1 != me or p1 == BOT_NAME:
        return False
    if _getv(conn, "bot_battle_on", "1") != "1":   # 管理员可在 settings 里设为 0 关闭
        return False
    try:
        waited = (datetime.now() - datetime.strptime(created, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except Exception:
        waited = 0
    if waited < BATTLE_WAIT_SECONDS:
        return False
    if random.random() > BATTLE_JOIN_CHANCE:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE matches SET player2=?, status='building', last_seen=? WHERE id=?",
                 (BOT_NAME, now, mid))
    conn.commit()
    return True


def _bot_build_deck(conn):
    """从 JZH 拥有的卡里组 7 张，优先用稀有卡（爆率越低权重越高）；每张不超过拥有数量。"""
    rows = conn.execute(
        "SELECT uc.card_id, SUM(uc.count), c.drop_rate "
        "FROM user_cards uc JOIN cards c ON uc.card_id = c.id "
        "WHERE uc.username = ? GROUP BY uc.card_id", (BOT_NAME,)).fetchall()
    owned = [(cid, int(cnt), (float(rate) if rate is not None else 1.0))
             for cid, cnt, rate in rows if cnt and cnt > 0]

    if owned:
        # 爆率越低 → 权重越高（越优先选）。+0.5 防止除零、也压一下极端值
        weight = {cid: 1.0 / (rate + 0.5) for cid, cnt, rate in owned}
        avail = {cid: cnt for cid, cnt, _ in owned}
        deck = []
        for _ in range(7):
            cands = [cid for cid in avail if avail[cid] > 0]     # 还没用完的卡
            if not cands:
                break
            pick = random.choices(cands, weights=[weight[c] for c in cands], k=1)[0]
            deck.append(pick)
            avail[pick] -= 1
        # 拥有总数不足 7 张：按权重（仍偏稀有）补满
        while len(deck) < 7:
            cids = [c for c, _, _ in owned]
            deck.append(random.choices(cids, weights=[weight[c] for c in cids], k=1)[0])
        return deck

    # 兜底：JZH 一张卡都没有时，从所有卡里随机抽，保证对战还能进行
    allcards = [r[0] for r in conn.execute("SELECT id FROM cards").fetchall()]
    if not allcards:
        return None
    return [random.choice(allcards) for _ in range(7)]


def bot_fill_deck_if_needed(conn, mid, p1, p2):
    """如果对手是 JZH 且还没出牌，就用它自己的卡册组 7 张（不消耗 AI 额度）。"""
    if BOT_NAME not in (p1, p2):
        return False
    bot_is_p1 = (p1 == BOT_NAME)
    col = "deck1" if bot_is_p1 else "deck2"
    existing = conn.execute(f"SELECT {col} FROM matches WHERE id=?", (mid,)).fetchone()[0]
    if existing:
        return False
    deck = _bot_build_deck(conn)
    if not deck:
        return False
    conn.execute(f"UPDATE matches SET {col}=? WHERE id=?", (json.dumps(deck), mid))
    conn.commit()
    return True
