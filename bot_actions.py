# bot_actions.py —— (JZH) bot 的所有行为逻辑
# 放在项目根目录，和 app.py、ai_bot.py 同一层。
# app.py 只需要 import 这里的几个函数并在几处调用即可。
 
import sqlite3
import json
import random
import time
import threading
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash
 
from ai_bot import ask_ai   # 复用你写好的调用函数（里面有人设 PERSONA）
 
DB = "users.db"
BOT_NAME = "JZH"            # bot 的账号名 / 大家 @ 的名字
 
# ====== 各种限额（想调就改这里）======
BOT_DAILY_LIMIT = 300          # 全站每天最多调用 AI 多少次（免费额度 ~1500，留足余量）
BOT_MIN_INTERVAL = 4           # 两次 AI 调用至少间隔几秒（4 秒 ≈ 每分钟 15 次，卡在免费上限内）
BOT_CHAT_DAILY_PER_USER = 20   # 每个用户每天最多找 JZH 聊几句
BOARD_BUBBLE_CHANCE = 0.15     # 发新帖时，JZH 随机冒泡回复的概率（0.15 = 15%）
BATTLE_WAIT_SECONDS = 8        # 匹配等待超过几秒，JZH 才可能来陪打
BATTLE_JOIN_CHANCE = 0.5       # 超时后每次轮询，JZH 加入的概率
COMMENT_LIMIT = 8              # 和 app.py 里的一致：每帖评论上限
 
 
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
 
 
def run_bg(fn, *args):
    """把一个函数丢到后台线程去跑，主请求不用等它。"""
    threading.Thread(target=fn, args=args, daemon=True).start()
 
 
# ============ 功能一：留言板 ============
def _do_board_reply(post_id, summoned):
    """真正生成并写入 JZH 的评论。在后台线程里运行。"""
    conn = _connect()
    try:
        post = conn.execute("SELECT username, content FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return
        author, content = post
        if author == BOT_NAME:               # 不回自己发的帖
            return
        cnt = conn.execute("SELECT COUNT(*) FROM comments WHERE post_id=?", (post_id,)).fetchone()[0]
        if cnt >= COMMENT_LIMIT:             # 评论满了就别插了
            return
        # 随机冒泡时：如果这帖 JZH 已经回过，就不重复冒泡（被 @ 召唤则照回）
        if not summoned:
            already = conn.execute(
                "SELECT 1 FROM comments WHERE post_id=? AND username=? LIMIT 1",
                (post_id, BOT_NAME)).fetchone()
            if already:
                return
        if not bot_gate(conn):               # 撞限速就安静放弃
            return
 
        recent = conn.execute(
            "SELECT username, content FROM comments WHERE post_id=? ORDER BY id DESC LIMIT 4",
            (post_id,)).fetchall()
        ctx = " / ".join(f"{u}: {c}" for u, c in reversed(recent) if c) or "（暂无评论）"
        who = "有人在论坛点名 @JZH 找你。" if summoned else "有人在论坛发了个帖子。"
        prompt = (f"{who}\n帖子内容：「{content}」\n最近的评论：{ctx}\n"
                  f"请你以JZH的口吻，回一条简短的评论（只回评论本身）。")
 
        reply = ask_ai(prompt)
        if not reply:
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
 
 
def on_new_post(post_id):
    """发新帖后调用：按概率让 JZH 冒泡回复。"""
    if random.random() < BOARD_BUBBLE_CHANCE:
        run_bg(_do_board_reply, post_id, False)
 
 
def on_new_comment(post_id, content):
    """发新评论后调用：如果 @了JZH 就让它回复。"""
    if content and ("@JZH" in content.upper() or "@JZH" in content):
        run_bg(_do_board_reply, post_id, True)
 
 
# ============ 功能三：聊天气泡 ============
def chat(username, message, history):
    """返回 dict 给前端。reply=回复内容；error=给用户看的提示。"""
    message = (message or "").strip()
    if not message:
        return {"reply": None, "error": "说点什么吧"}
    if len(message) > 200:
        message = message[:200]
 
    conn = _connect()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT bot_chat_day, bot_chat_count FROM users WHERE username=?", (username,)).fetchone()
        day = row[0] if row else None
        used = row[1] if row and row[1] is not None else 0
        if day != today:
            used = 0
        if used >= BOT_CHAT_DAILY_PER_USER:
            return {"reply": None, "error": f"今天跟JZH聊得够多啦（每天{BOT_CHAT_DAILY_PER_USER}条），明天再来～"}
 
        if not bot_gate(conn):
            return {"reply": None, "error": "JZH正忙，没空理你，找真的JZH去"}
 
        hist = []
        if isinstance(history, list):
            for h in history[-6:]:                 # 只带最近 6 条，省 token
                t = (h.get("text") or "").strip()
                if t:
                    hist.append({"role": "model" if h.get("role") == "model" else "user",
                                 "text": t[:200]})
 
        reply = ask_ai(message, history=hist)
        if not reply:
            return {"reply": None, "error": "JZH没搭理你，再试一次？"}
        reply = reply.strip()[:300]
 
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
    """从 JZH 账户真实拥有的卡里组一副 7 张的牌，规则和真人一样：每张不超过拥有数量。"""
    rows = conn.execute(
        "SELECT card_id, SUM(count) FROM user_cards WHERE username=? GROUP BY card_id",
        (BOT_NAME,)).fetchall()
    owned = [(cid, int(cnt)) for cid, cnt in rows if cnt and cnt > 0]
 
    if owned:
        # 把拥有的卡按数量展开成"一张张实体牌"，再随机抽 7 张（自动满足"不超过拥有数"）
        pool = []
        for cid, cnt in owned:
            pool.extend([cid] * cnt)
        if len(pool) >= 7:
            return random.sample(pool, 7)
        # 拥有总数不足 7 张：就用它拥有的这几种，随便凑够 7 张（可能重复）
        types = [cid for cid, _ in owned]
        return [random.choice(types) for _ in range(7)]
 
    # 兜底：JZH 一张卡都没有时，从所有卡里抽，保证对战还能正常进行
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
 