# casino.py —— JZH 赌场（服务器端状态：金币共用宠物那份 / 房间 / 机器人 / 轮盘赌）
# 放项目根目录，和 app.py、pet.py 同层。app.py 里 import 它并加几个路由即可（见文末说明）。
# 联机方式和 JZH 对撞一致：状态存 sqlite，前端定时轮询，不用 WebSocket。

import json
import time
import random
import pet          # 复用同一个 users.db、_connect、_now，以及“金币”这份数据

DB = pet.DB

# ====== 可调参数 ======
WELCOME_BONUS = 30        # 首次进赌场赠送
BET_SECS      = 60        # 一轮下注窗口（秒），到点自动开转（也可手动“开始转动”）
RESULT_SECS   = 8         # 没人点“继续下注”时，结果最多展示这么久就自动进下一轮
RESULT_MIN    = 5         # 轮盘动画时长（秒）；转完了才允许提前点“继续下注”进下一轮
MIN_BET       = 5         # 单笔最小下注
MAX_SEATS     = 6         # 一桌最多几个真人
MIN_PLAYERS   = 3         # 一桌至少几人；真人不够就用机器人补到这个数（2人也能开，配1个机器人）
BOT_NAMES     = ["玩机器", "洗衣机", "楼外楼", "喵一闻"]   # 机器人名字池（按需取用）
ROOM_IDLE_KILL = 1800     # 房间多久没人轮询就清理（秒）——30 分钟没人动就删
DAILY_KEEP_DAYS = 3       # "今日大赢家"记录保留最近几天，更早的自动清掉

# 轮盘：欧式单零 0-36
RED_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
# 下注类型 → (中文名, 赔率倍数含本金)。数字类单独处理（36 倍）。
EVEN_BETS = {
    "red":  ("红", 2),  "black": ("黑", 2),
    "odd":  ("单", 2),  "even":  ("双", 2),
    "low":  ("小 1-18", 2), "high": ("大 19-36", 2),
}

# ====== 饮品（用宠物"精神值"购买；一次性，喝下的那一轮开奖时触发）======
# 规则：酒保处花精神值买入背包（最多带 DRINK_HOLD_MAX 瓶）；进房下注阶段手动"喝"才生效；
#       只有房里凑够 2 个真人玩家才能喝（单人/纯机器人房禁用）；同桌看不到谁喝了什么。
DRINKS = {
    "calm":     {"name": "定心茶",   "emoji": "🍵", "cost": 40,
                 "desc": "这一轮若没中，退还你的本金，不亏币（保证不会输）"},
    "double":   {"name": "翻倍酒",   "emoji": "🍶", "cost": 30,
                 "desc": "这一轮若赢，赢的收益翻倍"},
    "sabotage": {"name": "醉拳烈酒", "emoji": "🥃", "cost": 50,
                 "desc": "这一轮，同桌输钱的对手损失翻倍（对手金币不够只扣到 0，不扣成负）"},
}
DRINK_HOLD_MAX = 2        # 一个人最多同时带几瓶饮品

# ====== 老虎机（单人小赌，3 或 5 金币一把）======
SLOT_BETS    = [3, 5]                                   # 每把可押的金额
SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]      # 图案（越靠后越稀有、赔得越多）
SLOT_WEIGHTS = [24, 24, 22, 16, 9, 5]                  # 每格出现的权重（和=100）
SLOT_PAY3    = {"🍒": 3, "🍋": 5, "🔔": 8, "⭐": 13, "💎": 22, "7️⃣": 55}  # 三个同 → ×注
SLOT_PAY2    = {"🍒": 2, "🍋": 1, "🔔": 1, "⭐": 1,  "💎": 2,  "7️⃣": 3}   # 恰好两个同 → ×注（1=退本金）

# ====== 21点（多人一桌：和轮盘一样有 3人房/单人房；单人房不限时）======
BJ_MIN_BET     = 5        # 单笔最小下注
BJ_BET_SECS    = 30       # 公共房下注时间（单人房不限时）
BJ_TURN_SECS   = 25       # 公共房每人决策时间（超时自动停牌；单人房不限时）
BJ_RESULT_SECS = 12       # 公共房结果展示后自动开下一局（单人房等玩家点“再来一局”）
BJ_SUITS = ["♠", "♥", "♦", "♣"]
BJ_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _now(): return time.time()
def _connect(): return pet._connect()


def ensure_casino_tables():
    conn = _connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS casino_meta (
            owner TEXT PRIMARY KEY, welcomed INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS casino_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
            data TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        # 今日大赢家：按天累计每个人的净输赢
        conn.execute("""CREATE TABLE IF NOT EXISTS casino_daily (
            day TEXT NOT NULL, owner TEXT NOT NULL, net INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, owner))""")
        # 饮品背包：每人手里带着的饮品（JSON 列表）
        conn.execute("""CREATE TABLE IF NOT EXISTS casino_buffs (
            owner TEXT PRIMARY KEY, drinks TEXT NOT NULL DEFAULT '[]')""")
        conn.commit()
    finally:
        conn.close()


# ---------- 金币：直接读写 pets 表里那份 coins，赌场和养成共用 ----------
def _coins_get(conn, owner):
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return 0
    try:
        return int(json.loads(row[0]).get("coins", 0))
    except Exception:
        return 0


def _coins_add(conn, owner, delta):
    """给某用户加/减金币（会夹在 >=0）。没有宠物存档就新建一个，保证金币能存住。"""
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    s = json.loads(row[0]) if row else pet._migrate(pet._fresh())
    s["coins"] = max(0, int(s.get("coins", 0)) + int(delta))
    conn.execute(
        "INSERT OR REPLACE INTO pets (owner, version, status, data, updated_at) VALUES (?,?,?,?,?)",
        (owner, s.get("version", "v1"), s.get("status", "home"),
         json.dumps(s, ensure_ascii=False), _now()))
    return s["coins"]


# ---------- 精神值：读写 pets.data 里那份 spirit（买饮品用它付账）----------
def _spirit_get(conn, owner):
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return 0
    try:
        return int(json.loads(row[0]).get("spirit", 0))
    except Exception:
        return 0


def _spirit_spend(conn, owner, cost):
    """扣精神值买饮品；够就扣、返回 True，不够原样不动、返回 False。"""
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return False
    try:
        s = json.loads(row[0])
    except Exception:
        return False
    cur = int(s.get("spirit", 0))
    if cur < int(cost):
        return False
    s["spirit"] = cur - int(cost)
    conn.execute(
        "INSERT OR REPLACE INTO pets (owner, version, status, data, updated_at) VALUES (?,?,?,?,?)",
        (owner, s.get("version", "v1"), s.get("status", "home"),
         json.dumps(s, ensure_ascii=False), _now()))
    return True


# ---------- 饮品背包：存 casino_buffs 表，一个人一行，drinks 是 JSON 列表 ----------
def _drinks_get(conn, owner):
    row = conn.execute("SELECT drinks FROM casino_buffs WHERE owner=?", (owner,)).fetchone()
    if not row:
        return []
    try:
        d = json.loads(row[0])
        return [x for x in d if x in DRINKS] if isinstance(d, list) else []
    except Exception:
        return []


def _drinks_set(conn, owner, drinks):
    drinks = [x for x in drinks if x in DRINKS][:DRINK_HOLD_MAX]
    conn.execute("INSERT OR REPLACE INTO casino_buffs (owner, drinks) VALUES (?,?)",
                 (owner, json.dumps(drinks, ensure_ascii=False)))


def _drinks_catalog():
    """给前端展示用的饮品清单（含 kind）。"""
    return [{"kind": k, "name": v["name"], "emoji": v["emoji"],
             "cost": v["cost"], "desc": v["desc"]} for k, v in DRINKS.items()]


def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _bump_daily(conn, owner, net):
    """把某人今天的净输赢累加（net 可正可负）。"""
    conn.execute(
        "INSERT INTO casino_daily (day, owner, net) VALUES (?,?,?) "
        "ON CONFLICT(day, owner) DO UPDATE SET net = net + ?",
        (_today(), owner, int(net), int(net)))


def top_winner(conn):
    """今天净赢最多的人（净>0 才算）。没有就返回 None。"""
    r = conn.execute(
        "SELECT owner, net FROM casino_daily WHERE day=? AND net>0 ORDER BY net DESC, owner LIMIT 1",
        (_today(),)).fetchone()
    return {"owner": r[0], "net": int(r[1])} if r else None


def top_loser(conn):
    """今天净亏最多的人（净<0 才算）。没有就返回 None。附带外观，用来在角落画个坐着的小人。"""
    r = conn.execute(
        "SELECT owner, net FROM casino_daily WHERE day=? AND net<0 ORDER BY net ASC, owner LIMIT 1",
        (_today(),)).fetchone()
    if not r:
        return None
    return {"owner": r[0], "net": int(r[1]), "sprite": _sprite_of(conn, r[0])}


def _sprite_of(conn, owner):
    """取某用户 JZH 的外观（在赌场里一律按成年展示）。"""
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    if row:
        try:
            s = json.loads(row[0])
            return {"version": s.get("version", "v1"), "hat": s.get("hat", "光头"),
                    "outfit": s.get("outfit", 0), "hair": s.get("hair", 0),
                    "skin": s.get("skin", 0), "clothes": s.get("clothes", "无")}
        except Exception:
            pass
    return {"version": "v1", "hat": "光头", "outfit": 0, "hair": 0, "skin": 0, "clothes": "无"}


# ---------- 进赌场：发新手礼金 ----------
def enter_casino(owner):
    conn = _connect()
    try:
        r = conn.execute("SELECT welcomed FROM casino_meta WHERE owner=?", (owner,)).fetchone()
        welcomed_now = False
        if not r or not r[0]:
            _coins_add(conn, owner, WELCOME_BONUS)
            conn.execute("INSERT OR REPLACE INTO casino_meta (owner, welcomed) VALUES (?,1)", (owner,))
            welcomed_now = True
        # 顺手清掉过期的大赢家记录 + 僵尸房，控制数据库体积
        cutoff = time.strftime("%Y-%m-%d", time.localtime(_now() - DAILY_KEEP_DAYS * 86400))
        conn.execute("DELETE FROM casino_daily WHERE day < ?", (cutoff,))
        conn.execute("DELETE FROM casino_rooms WHERE updated_at < ?", (_now() - ROOM_IDLE_KILL,))
        conn.commit()
        return {"coins": _coins_get(conn, owner), "welcomed_now": welcomed_now, "bonus": WELCOME_BONUS}
    finally:
        conn.close()


def lobby_data(owner):
    conn = _connect()
    try:
        sp = _sprite_of(conn, owner)
        coins = _coins_get(conn, owner)
        return {"me": owner, "coins": coins, "sprite": sp, "min_bet": MIN_BET,
                "top_winner": top_winner(conn),
                "top_loser": top_loser(conn),                 # 今日最大输家（坐角落椅子上）
                "spirit": _spirit_get(conn, owner),
                "my_drinks": _drinks_get(conn, owner),        # 背包里已有的饮品
                "drinks_catalog": _drinks_catalog(),          # 酒保菜单
                "drink_hold_max": DRINK_HOLD_MAX}
    finally:
        conn.close()


# ---------- 房间存取 ----------
def _load(conn, room_id):
    row = conn.execute("SELECT data FROM casino_rooms WHERE id=?", (room_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _save(conn, room_id, room):
    humans = [s for s in room["seats"] if not s.get("isBot")]
    status = "empty" if not humans else room.get("phase", "betting")
    conn.execute("UPDATE casino_rooms SET status=?, data=?, updated_at=? WHERE id=?",
                 (status, json.dumps(room, ensure_ascii=False), _now(), room_id))
    conn.commit()


def _new_bot_seat(name):
    ver = random.choice(pet.PET_VERSIONS)
    return {"owner": "#bot:" + name, "name": name, "isBot": True,
            "version": ver, "hat": random.choice(["光头", "墨镜", "贝雷帽", "鸭舌帽"]),
            "outfit": random.randint(0, pet.OUTFIT_N - 1), "hair": random.randint(0, pet.HAIR_N - 1),
            "skin": random.randint(0, pet.SKIN_N - 1), "clothes": random.choice(["无", "西装领带", "条纹衫"]),
            "bets": {}, "last_net": 0, "last_bets": {}, "drinks": [], "chips": 500}


def _new_human_seat(conn, owner):
    sp = _sprite_of(conn, owner)
    return {"owner": owner, "name": owner, "isBot": False,
            "version": sp["version"], "hat": sp["hat"], "outfit": sp["outfit"],
            "hair": sp["hair"], "skin": sp["skin"], "clothes": sp["clothes"],
            "bets": {}, "last_net": 0, "last_bets": {}, "drinks": [], "ready": False}


def _human_count(room):
    """房里真人数量（饮品只有 >=2 个真人才能喝）。"""
    return len([s for s in room["seats"] if not s.get("isBot")])


def _fresh_room(conn, owner, mode):
    room = {
        "game": "roulette", "mode": mode,
        "round": 1, "phase": "betting", "result": None, "result_color": None,
        "resolved_at": 0, "deadline": _now() + BET_SECS, "start_requested": False,
        "seats": [_new_human_seat(conn, owner)],
        "log": [f"🎲房间开好了，{'单人场（人不够用机器人补到3人）' if mode=='solo' else '等朋友进来，人不够会用机器人补齐~'}"],
    }
    _ensure_fill(room)      # 补机器人到至少 3 人
    _bots_bet(room)
    return room


def create_room(owner, mode="solo"):
    conn = _connect()
    try:
        _cleanup(conn)                         # 顺手清掉没人的僵尸房，避免房间数据堆积
        room = _fresh_room(conn, owner, mode)
        cur = conn.execute(
            "INSERT INTO casino_rooms (game, mode, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("roulette", mode, "betting", json.dumps(room, ensure_ascii=False), _now(), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def quick_join(owner):
    """进公共房：找一个还有空位的公共轮盘房加入，没有就新建一个。"""
    conn = _connect()
    try:
        _cleanup(conn)
        rows = conn.execute(
            "SELECT id, data FROM casino_rooms WHERE game='roulette' AND mode='public' ORDER BY id DESC LIMIT 20").fetchall()
        for rid, data in rows:
            try:
                room = json.loads(data)
            except Exception:
                continue
            if any(s.get("owner") == owner for s in room["seats"]):
                return rid   # 已经在里面
            humans = [s for s in room["seats"] if not s.get("isBot")]
            if len(humans) < MAX_SEATS and len(humans) >= 1:
                room["seats"].append(_new_human_seat(conn, owner))
                _ensure_fill(room)               # 真人到3人就撤掉机器人；2人则留1个机器人
                if room.get("phase") == "betting":
                    _bots_bet(room, only_empty=True)
                room.setdefault("log", []).append(f"👋{owner} 进房了")
                _save(conn, rid, room)
                return rid
        # 没有可加入的 → 新建一个公共房
        room = _fresh_room(conn, owner, "public")
        cur = conn.execute(
            "INSERT INTO casino_rooms (game, mode, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("roulette", "public", "betting", json.dumps(room, ensure_ascii=False), _now(), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _cleanup(conn):
    """清理很久没人动过的房间。活跃的房间每 1.6 秒被轮询一次、updated_at 一直刷新；
    一旦所有人都关掉网页（哪怕没点离开），就没人轮询了，updated_at 变老，超过 ROOM_IDLE_KILL 秒即删。
    这样"僵尸房"不会一直占着数据库。"""
    conn.execute("DELETE FROM casino_rooms WHERE updated_at < ?", (_now() - ROOM_IDLE_KILL,))
    conn.commit()


# ---------- 让一桌补齐到至少 MIN_PLAYERS 人（真人不够补机器人，够了撤机器人）----------
def _ensure_fill(room):
    humans = [s for s in room["seats"] if not s.get("isBot")]
    need = max(0, MIN_PLAYERS - len(humans))
    need = min(need, MAX_SEATS - len(humans))     # 不超座位上限
    # 先保留全部真人 + 前 need 个机器人，多余机器人撤掉
    kept, keep_bots = [], 0
    for s in room["seats"]:
        if s.get("isBot"):
            if keep_bots < need:
                kept.append(s); keep_bots += 1
        else:
            kept.append(s)
    room["seats"] = kept
    # 机器人不够就补
    used = {s["name"] for s in room["seats"] if s.get("isBot")}
    pool = [n for n in BOT_NAMES if n not in used] + [f"机器人·{i}" for i in range(1, 9)]
    pi = 0
    while len([s for s in room["seats"] if s.get("isBot")]) < need:
        room["seats"].append(_new_bot_seat(pool[pi])); pi += 1


# ---------- 机器人下注 ----------
def _bots_bet(room, only_empty=False):
    for s in room["seats"]:
        if not s.get("isBot"):
            continue
        if only_empty and s.get("bets"):          # 只给还没下注的机器人补下注（中途进来的）
            continue
        s["bets"] = {}
        picks = random.sample(list(EVEN_BETS.keys()), k=random.randint(1, 2))
        for p in picks:
            s["bets"][p] = random.choice([5, 10, 10, 20])
        if random.random() < 0.4:                       # 偶尔押个数字
            s["bets"]["num:" + str(random.randint(0, 36))] = 5


# ---------- 轮盘结算 ----------
def _payout(bet_type, stake, result):
    """返回这笔注的“返还额（含本金）”，没中返 0。"""
    if bet_type.startswith("num:"):
        try:
            n = int(bet_type.split(":", 1)[1])
        except ValueError:
            return 0
        return stake * 36 if result == n else 0
    if bet_type not in EVEN_BETS:
        return 0
    mult = EVEN_BETS[bet_type][1]
    if result == 0:
        return 0                                         # 0 号：所有等赔注全输
    win = {
        "red":  result in RED_NUMBERS,
        "black": result not in RED_NUMBERS,
        "odd":  result % 2 == 1,
        "even": result % 2 == 0,
        "low":  1 <= result <= 18,
        "high": 19 <= result <= 36,
    }[bet_type]
    return stake * mult if win else 0


def _resolve(conn, room):
    result = random.randint(0, 36)
    color = "green" if result == 0 else ("red" if result in RED_NUMBERS else "black")
    room["result"] = result
    room["result_color"] = color
    room["phase"] = "result"
    room["resolved_at"] = _now()
    cn = "绿" if color == "green" else ("红" if color == "red" else "黑")
    room.setdefault("log", []).insert(0, f"🎡开出 {result}（{cn}）")

    # 谁喝了「醉拳烈酒」——只有真人、且这一轮真的喝了才算。有它在场，本轮净亏的真人对手亏损翻倍。
    saboteurs = [s for s in room["seats"]
                 if not s.get("isBot") and "sabotage" in s.get("drinks", [])]

    # 第一遍：算基础输赢，并应用只影响自己的饮品（定心茶 / 翻倍酒）
    for s in room["seats"]:
        bets = s.get("bets", {})
        stake = sum(bets.values())
        ret = sum(_payout(bt, amt, result) for bt, amt in bets.items())
        net = ret - stake
        drinks = s.get("drinks", []) if not s.get("isBot") else []
        if "calm" in drinks and net < 0:      # 定心茶：没中就退本金，净变 0（保证不亏）
            ret = stake
            net = 0
        if "double" in drinks and net > 0:    # 翻倍酒：赢了收益翻倍（多返一份净收益）
            ret += net
            net = ret - stake
        s["_ret"] = ret
        s["last_net"] = net
        s["last_bets"] = dict(bets)
        s["_sab_extra"] = 0

    # 第二遍：醉拳烈酒——只要有「别的真人」喝了它，本轮净亏的真人多扣一份亏损（夹在 >=0）
    if saboteurs:
        for s in room["seats"]:
            if s.get("isBot"):
                continue
            if s["last_net"] < 0 and any(o is not s for o in saboteurs):
                s["_sab_extra"] = -s["last_net"]      # 想额外再扣掉一份亏损

    # 第三遍：真正发钱 / 记账 / 打日志，并清空本轮饮品
    for s in room["seats"]:
        ret = s.pop("_ret", 0)
        extra = s.pop("_sab_extra", 0)
        net = s["last_net"]
        had_bet = bool(s.get("last_bets"))
        if s.get("isBot"):
            if had_bet:
                s["chips"] = s.get("chips", 500) + net
        else:
            if ret > 0:
                _coins_add(conn, s["owner"], ret)         # 本金下注时已扣，这里返还中奖额/退本金
            if extra > 0:                                  # 被醉拳烈酒制裁：再多扣一份（不扣成负）
                before = _coins_get(conn, s["owner"])
                after = _coins_add(conn, s["owner"], -extra)
                net -= (before - after)                    # 实际扣到的才计入亏损
                s["last_net"] = net
            if had_bet or extra > 0:
                _bump_daily(conn, s["owner"], net)         # 计入今日大赢家榜
                who = s["name"]
                room["log"].insert(0, f"　{who}：{'赢 +' if net>0 else ('保本 ' if net==0 else '输 ')}{net if net<0 else abs(net)}")
        s["bets"] = {}       # 清空本轮下注（last_bets 留着显示）
        s["drinks"] = []     # 饮品一次性：这一轮用完就没了，下一轮不再触发
    room["log"] = room["log"][:40]


def _start_new_round(room):
    room["round"] += 1
    room["phase"] = "betting"
    room["result"] = None
    room["result_color"] = None
    room["resolved_at"] = 0
    room["start_requested"] = False
    room["deadline"] = _now() + BET_SECS
    for s in room["seats"]:
        s["bets"] = {}
        s["drinks"] = []     # 新一轮清空饮品状态（饮品只在喝下的那一轮生效）
        s["ready"] = False   # 新一轮重置“准备好了”
    _ensure_fill(room)       # 新一轮开始，按当前真人数把机器人补/撤到位
    _bots_bet(room)
    room.setdefault("log", []).insert(0, f"—— 第 {room['round']} 轮，下注吧 ——")
    room["log"] = room["log"][:40]


def _tick(conn, room):
    """惰性推进：到点/有人点开转就结算；结果展示够久就开下一轮。"""
    t = _now()
    if room["phase"] == "betting":
        if room.get("start_requested") or t >= room.get("deadline", t + 999):
            _resolve(conn, room)
    elif room["phase"] == "result":
        if t - room.get("resolved_at", t) >= RESULT_SECS:
            _start_new_round(room)


def _view(room, room_id, owner):
    """给前端的房间视图。别人的下注/饮品都藏起来——同桌看不到谁喝了什么、押了什么。"""
    t = _now()
    v = dict(room)
    v["id"] = room_id
    v["bet_secs_left"] = max(0, int(room.get("deadline", t) - t)) if room["phase"] == "betting" else 0
    v["result_secs_left"] = max(0, int(RESULT_SECS - (t - room.get("resolved_at", t)))) if room["phase"] == "result" else 0
    v["me"] = owner
    v["red_numbers"] = sorted(RED_NUMBERS)
    v["min_bet"] = MIN_BET
    v["bet_labels"] = {k: EVEN_BETS[k][0] for k in EVEN_BETS}

    # 座位脱敏：自己的座位原样给；别人的只留外观 + 结果，藏掉当前下注和饮品
    safe_seats = []
    for s in room["seats"]:
        if s.get("owner") == owner:
            safe_seats.append(s)
        else:
            safe_seats.append({
                "owner": s.get("owner"), "name": s.get("name"), "isBot": s.get("isBot", False),
                "version": s.get("version", "v1"), "hat": s.get("hat", "光头"),
                "outfit": s.get("outfit", 0), "hair": s.get("hair", 0),
                "skin": s.get("skin", 0), "clothes": s.get("clothes", "无"),
                "last_net": s.get("last_net", 0),
                "has_bet": bool(s.get("bets")),          # 只告诉别人“下没下”，不告诉下了啥
                "had_bet_last": bool(s.get("last_bets")),  # 上一轮是否参与（开奖结算榜要用）
                "ready": bool(s.get("ready")),           # 别人准备好了没（3 人房显示“已准备”）
            })
    v["seats"] = safe_seats

    mine = next((s for s in room["seats"] if s.get("owner") == owner), None)
    v["my_bets"] = mine.get("bets", {}) if mine else {}
    v["my_staked"] = sum(mine.get("bets", {}).values()) if mine else 0
    v["my_active"] = mine.get("drinks", []) if mine else []     # 我这一轮已经喝下的饮品
    hc = _human_count(room)
    v["human_count"] = hc
    v["can_drink"] = hc >= 2                                    # 只有 >=2 个真人才允许喝饮品
    # 开奖触发方式：单人房（只有你 + 机器人）可以直接点“开始转动”；
    # 多真人房要等“下注时间到”或“所有真人都点了准备好”。
    humans = [s for s in room["seats"] if not s.get("isBot")]
    v["can_start_now"] = len(humans) <= 1
    v["ready_total"] = len(humans)
    v["ready_count"] = sum(1 for s in humans if s.get("ready"))
    v["my_ready"] = bool(mine.get("ready")) if mine else False
    return v


def _augment(conn, v, owner):
    """给前端视图补上金币/精神值/背包饮品/菜单/今日大赢家——每个接口返回都带上，避免前端闪烁。"""
    v["coins"] = _coins_get(conn, owner)
    v["spirit"] = _spirit_get(conn, owner)
    v["my_drinks"] = _drinks_get(conn, owner)              # 我背包里带进来的饮品
    v["drinks_catalog"] = _drinks_catalog()
    v["top_winner"] = top_winner(conn)
    return v


def get_room(owner, room_id):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在或已解散"}
        _tick(conn, room)
        _save(conn, room_id, room)
        return _augment(conn, _view(room, room_id, owner), owner)
    finally:
        conn.close()


def place_bet(owner, room_id, bet_type, amount):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _tick(conn, room)
        msg = None
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        if not seat:
            msg = "你不在这个房间里"
        elif room["phase"] != "betting":
            msg = "本轮已封盘，等下一轮"
        else:
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                amount = 0
            ok_type = (bet_type in EVEN_BETS) or (
                bet_type.startswith("num:") and bet_type.split(":", 1)[1].isdigit()
                and 0 <= int(bet_type.split(":", 1)[1]) <= 36)
            if amount < MIN_BET:
                msg = f"最少下 {MIN_BET} 币"
            elif not ok_type:
                msg = "下注类型不对"
            elif _coins_get(conn, owner) < amount:
                msg = "金币不够啦，去打工赚点~"
            else:
                _coins_add(conn, owner, -amount)          # 立即扣本金
                seat["bets"][bet_type] = seat["bets"].get(bet_type, 0) + amount
                seat["ready"] = False                     # 又下注了，就取消“准备好”状态
                msg = "下注成功"
        _save(conn, room_id, room)
        v = _augment(conn, _view(room, room_id, owner), owner)
        v["msg"] = msg
        return v
    finally:
        conn.close()


def clear_bets(owner, room_id):
    """撤回本轮我的下注（退还金币）。"""
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _tick(conn, room)
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        msg = None
        if seat and room["phase"] == "betting" and seat.get("bets"):
            refund = sum(seat["bets"].values())
            _coins_add(conn, owner, refund)
            seat["bets"] = {}
            seat["ready"] = False                        # 撤注了，取消“准备好”状态
            msg = f"已撤回，退还 {refund} 币"
        else:
            msg = "现在没有可撤回的下注"
        _save(conn, room_id, room)
        v = _augment(conn, _view(room, room_id, owner), owner)
        v["msg"] = msg
        return v
    finally:
        conn.close()


def start_spin(owner, room_id):
    """开转/准备。单人房（只有你 + 机器人）点一下就立刻开转；
    多真人房则是切换自己的“准备好了”，等所有真人都准备好（或下注时间到）才开转。"""
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        msg = None
        if room["phase"] == "result":
            # 结果阶段点“继续下注”：轮盘已转完(结果展示够了)就立刻开下一轮，不用干等
            if (_now() - room.get("resolved_at", 0)) >= RESULT_MIN:
                _start_new_round(room)
        elif room["phase"] == "betting":
            humans = [s for s in room["seats"] if not s.get("isBot")]
            if len(humans) <= 1:
                room["start_requested"] = True             # 单人房：立刻开转
            else:
                seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
                if seat:
                    seat["ready"] = not seat.get("ready", False)   # 切换“准备好了”
                    msg = "已准备好，等其他人~" if seat["ready"] else "已取消准备"
                if all(s.get("ready") for s in humans):    # 所有真人都准备好 → 开转
                    room["start_requested"] = True
            _tick(conn, room)                              # 满足条件就地结算
        _save(conn, room_id, room)
        v = _augment(conn, _view(room, room_id, owner), owner)
        if msg:
            v["msg"] = msg
        return v
    finally:
        conn.close()


def leave_room(owner, room_id):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"ok": True}
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        if seat:
            if room["phase"] == "betting" and seat.get("bets"):
                _coins_add(conn, owner, sum(seat["bets"].values()))   # 退还未结算的注
            room["seats"] = [s for s in room["seats"] if s.get("owner") != owner]
        humans = [s for s in room["seats"] if not s.get("isBot")]
        if not humans:
            conn.execute("DELETE FROM casino_rooms WHERE id=?", (room_id,))
            conn.commit()
        else:
            _ensure_fill(room)               # 有人走了，人不够就补机器人回到3人
            if room.get("phase") == "betting":
                _bots_bet(room, only_empty=True)
            _save(conn, room_id, room)
        return {"ok": True}
    finally:
        conn.close()


# ---------- 老虎机：单人小赌，一把一结算 ----------
def _slot_roll():
    return [random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=1)[0] for _ in range(3)]


def _slot_payout(reels, bet):
    """三个同 → 按 SLOT_PAY3；恰好两个同 → 按 SLOT_PAY2；全不同 → 0。返回“返还额”。"""
    a, b, c = reels
    if a == b == c:
        return int(bet * SLOT_PAY3.get(a, 0))
    for sym in set(reels):
        if reels.count(sym) == 2:
            return int(bet * SLOT_PAY2.get(sym, 1))
    return 0


def slots_paytable():
    """给前端展示的赔付表。"""
    return {"bets": SLOT_BETS, "symbols": SLOT_SYMBOLS, "pay3": SLOT_PAY3, "pay2": SLOT_PAY2}


def spin_slots(owner, bet):
    """拉一把老虎机：先扣注，摇 3 个图案，中奖返奖，净输赢计入今日榜。结果丢给前端做动画。"""
    conn = _connect()
    try:
        try:
            bet = int(bet)
        except (TypeError, ValueError):
            bet = 0
        if bet not in SLOT_BETS:
            return {"ok": False, "msg": "每把只能压 3 或 5 金币", "coins": _coins_get(conn, owner)}
        if _coins_get(conn, owner) < bet:
            return {"ok": False, "msg": "金币不够啦，去打工赚点~", "coins": _coins_get(conn, owner)}
        _coins_add(conn, owner, -bet)                     # 先扣注
        reels = _slot_roll()
        ret = _slot_payout(reels, bet)                    # 返还额
        if ret > 0:
            _coins_add(conn, owner, ret)
        net = ret - bet
        _bump_daily(conn, owner, net)                     # 计入今日大赢家/大输家
        conn.commit()
        if ret == 0:
            msg = "差一点～再来一把"
        elif net > 0:
            msg = f"中啦！赢了 {net} 金币"
        elif net == 0:
            msg = "回本，不亏不赚"
        else:
            msg = f"没中，亏了 {-net}"
        return {"ok": True, "reels": reels, "ret": ret, "net": net, "bet": bet,
                "win": net > 0, "coins": _coins_get(conn, owner), "msg": msg}
    finally:
        conn.close()


# ---------- 饮品：酒保处购买（花精神值） ----------
def buy_drink(owner, kind):
    """在酒保那儿买一瓶饮品，用宠物精神值付账，放进背包（最多 DRINK_HOLD_MAX 瓶）。"""
    conn = _connect()
    try:
        if kind not in DRINKS:
            return {"ok": False, "msg": "没有这种饮品"}
        held = _drinks_get(conn, owner)
        if len(held) >= DRINK_HOLD_MAX:
            return {"ok": False, "msg": f"背包最多带 {DRINK_HOLD_MAX} 瓶，先去房间里喝掉一些吧",
                    "spirit": _spirit_get(conn, owner), "my_drinks": held}
        cost = DRINKS[kind]["cost"]
        if _spirit_get(conn, owner) < cost:
            return {"ok": False, "msg": f"精神值不够（需要 {cost}）",
                    "spirit": _spirit_get(conn, owner), "my_drinks": held}
        if not _spirit_spend(conn, owner, cost):
            return {"ok": False, "msg": "精神值不够", "spirit": _spirit_get(conn, owner), "my_drinks": held}
        held.append(kind)
        _drinks_set(conn, owner, held)
        conn.commit()
        return {"ok": True, "msg": f"买到「{DRINKS[kind]['name']}」啦",
                "spirit": _spirit_get(conn, owner), "my_drinks": _drinks_get(conn, owner)}
    finally:
        conn.close()


# ---------- 饮品：房间下注阶段喝掉（触发本轮效果） ----------
def use_drink(owner, room_id, kind):
    """下注阶段把背包里的一瓶饮品喝掉，这一轮开奖时生效。只有房里有 >=2 个真人才允许喝。"""
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _tick(conn, room)
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        msg = None
        if not seat:
            msg = "你不在这个房间里"
        elif room["phase"] != "betting":
            msg = "现在不能喝，等下一轮下注阶段"
        elif _human_count(room) < 2:
            msg = "一个人的房间不能喝饮品，凑够 2 个真人玩家才行哦"
        elif kind not in DRINKS:
            msg = "没有这种饮品"
        else:
            held = _drinks_get(conn, owner)
            if kind not in held:
                msg = "背包里没有这瓶饮品"
            else:
                held.remove(kind)                 # 从背包扣掉一瓶
                _drinks_set(conn, owner, held)
                seat.setdefault("drinks", []).append(kind)   # 挂到座位上，本轮生效
                _save(conn, room_id, room)
                conn.commit()
                msg = f"你喝下了「{DRINKS[kind]['name']}」，这一轮生效"
        v = _augment(conn, _view(room, room_id, owner), owner)
        v["msg"] = msg
        return v
    finally:
        conn.close()


# ============================================================
#  21点（Blackjack）——多人一桌，和轮盘一样有 3人房/单人房；单人房不限时
#  复用 casino_rooms 表（game='blackjack'）与 _save/_cleanup（僵尸房自动清理）
# ============================================================
def _bj_new_deck():
    deck = [{"r": r, "s": s} for s in BJ_SUITS for r in BJ_RANKS]
    random.shuffle(deck)
    return deck


def _bj_pop(room):
    if not room.get("deck"):
        room["deck"] = _bj_new_deck()
    return room["deck"].pop()


def _bj_value(cards):
    """算点数：A 先按 11，爆了再按 1；J/Q/K/10 算 10。忽略暗牌。"""
    total, aces = 0, 0
    for c in cards:
        if c.get("hidden"):
            continue
        r = c.get("r")
        if r == "A":
            total += 11; aces += 1
        elif r in ("J", "Q", "K", "10"):
            total += 10
        elif r:
            total += int(r)
    while total > 21 and aces > 0:
        total -= 10; aces -= 1
    return total


def _bj_is_blackjack(cards):
    return len(cards) == 2 and _bj_value(cards) == 21


def _bj_bot_seat(name):
    ver = random.choice(pet.PET_VERSIONS)
    return {"owner": "#bot:" + name, "name": name, "isBot": True,
            "version": ver, "hat": random.choice(["光头", "墨镜", "贝雷帽", "鸭舌帽"]),
            "outfit": random.randint(0, pet.OUTFIT_N - 1), "hair": random.randint(0, pet.HAIR_N - 1),
            "skin": random.randint(0, pet.SKIN_N - 1), "clothes": random.choice(["无", "西装领带", "条纹衫"]),
            "bet": 0, "cards": [], "done": False, "doubled": False, "result": "", "last_net": 0,
            "ready": False, "chips": 500}


def _bj_human_seat(conn, owner):
    sp = _sprite_of(conn, owner)
    return {"owner": owner, "name": owner, "isBot": False,
            "version": sp["version"], "hat": sp["hat"], "outfit": sp["outfit"],
            "hair": sp["hair"], "skin": sp["skin"], "clothes": sp["clothes"],
            "bet": 0, "cards": [], "done": False, "doubled": False, "result": "", "last_net": 0, "ready": False}


def _bj_ensure_fill(room):
    """真人不够就补机器人到 MIN_PLAYERS；够了撤机器人（和轮盘一致）。"""
    humans = [s for s in room["seats"] if not s.get("isBot")]
    need = max(0, MIN_PLAYERS - len(humans))
    need = min(need, MAX_SEATS - len(humans))
    kept, kb = [], 0
    for s in room["seats"]:
        if s.get("isBot"):
            if kb < need:
                kept.append(s); kb += 1
        else:
            kept.append(s)
    room["seats"] = kept
    used = {s["name"] for s in room["seats"] if s.get("isBot")}
    pool = [n for n in BOT_NAMES if n not in used] + [f"机器人·{i}" for i in range(1, 9)]
    pi = 0
    while len([s for s in room["seats"] if s.get("isBot")]) < need:
        room["seats"].append(_bj_bot_seat(pool[pi])); pi += 1


def _bj_bots_bet(room, only_empty=False):
    for s in room["seats"]:
        if not s.get("isBot"):
            continue
        if only_empty and s.get("bet"):
            continue
        s["bet"] = random.choice([5, 10, 10, 20, 50])


def _bj_next_turn(room, after):
    """下一个还需要行动的活跃座位（下过注、还没 done）。没有就返回 -1。"""
    for i in range(after + 1, len(room["seats"])):
        s = room["seats"][i]
        if s.get("bet", 0) > 0 and not s.get("done"):
            return i
    return -1


def _bj_humans(room):
    return [s for s in room["seats"] if not s.get("isBot")]


def _bj_timed(room):
    """是否启用倒计时：只有“公共房 且 至少 2 个真人”时才计时；
    否则（单人房，或公共房里只有 1 个真人）完全由玩家自己掌握节奏、不限时。"""
    return room.get("mode") == "public" and len(_bj_humans(room)) >= 2


def _bj_fresh_room(conn, owner, mode):
    room = {
        "game": "blackjack", "mode": mode, "round": 1, "phase": "betting",
        "deadline": 0,     # 倒计时由 _bj_tick 按“是否够2真人”动态管理
        "turn": -1, "turn_deadline": 0, "start_requested": False, "resolved_at": 0,
        "dealer": {"cards": [], "reveal": False},
        "deck": _bj_new_deck(),
        "seats": [_bj_human_seat(conn, owner)],
        "log": [f"🃏房间开好了，{'单人场（不限时；人不够用机器人补齐）' if mode=='solo' else '等朋友来，人不够用机器人补齐~'}"],
    }
    _bj_ensure_fill(room)
    _bj_bots_bet(room)
    return room


def create_bj_room(owner, mode="solo"):
    conn = _connect()
    try:
        _cleanup(conn)                      # 顺手清僵尸房，控制数据库体积
        room = _bj_fresh_room(conn, owner, mode)
        cur = conn.execute(
            "INSERT INTO casino_rooms (game, mode, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("blackjack", mode, "betting", json.dumps(room, ensure_ascii=False), _now(), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def quick_join_bj(owner):
    conn = _connect()
    try:
        _cleanup(conn)
        rows = conn.execute(
            "SELECT id, data FROM casino_rooms WHERE game='blackjack' AND mode='public' ORDER BY id DESC LIMIT 20").fetchall()
        for rid, data in rows:
            try:
                room = json.loads(data)
            except Exception:
                continue
            if any(s.get("owner") == owner for s in room["seats"]):
                return rid
            humans = [s for s in room["seats"] if not s.get("isBot")]
            # 只在“下注阶段”让新人进，避免打断正在进行的一局
            if room.get("phase") == "betting" and 1 <= len(humans) < MAX_SEATS:
                room["seats"].append(_bj_human_seat(conn, owner))
                _bj_ensure_fill(room)
                _bj_bots_bet(room, only_empty=True)
                room.setdefault("log", []).append(f"👋{owner} 进房了")
                _save(conn, rid, room)
                return rid
        room = _bj_fresh_room(conn, owner, "public")
        cur = conn.execute(
            "INSERT INTO casino_rooms (game, mode, status, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("blackjack", "public", "betting", json.dumps(room, ensure_ascii=False), _now(), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _bj_deal_round(room):
    room["deck"] = _bj_new_deck()
    room["dealer"] = {"cards": [_bj_pop(room), _bj_pop(room)], "reveal": False}
    for s in room["seats"]:
        if s.get("bet", 0) > 0:
            s["cards"] = [_bj_pop(room), _bj_pop(room)]
            s["done"] = False; s["doubled"] = False
            if _bj_is_blackjack(s["cards"]):
                s["result"] = "bj"; s["done"] = True   # 天生21点，自动停
            else:
                s["result"] = ""
        else:
            s["cards"] = []; s["done"] = True; s["result"] = "sit"   # 没下注，这局旁观
    room["phase"] = "playing"
    room["turn"] = _bj_next_turn(room, -1)
    room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
    room.setdefault("log", []).insert(0, "—— 发牌！——"); room["log"] = room["log"][:40]


def _bj_bot_play(room, seat):
    while _bj_value(seat["cards"]) < 17:      # 简单策略：<17 要牌
        seat["cards"].append(_bj_pop(room))
    seat["done"] = True
    if _bj_value(seat["cards"]) > 21:
        seat["result"] = "bust"


def _bj_progress(conn, room):
    """连续推进机器人回合；轮到真人就停下等操作；全部结束就庄家补牌+结算。"""
    guard = 0
    while room.get("phase") == "playing":
        guard += 1
        if guard > 50:
            break
        i = room.get("turn", -1)
        if i < 0:
            _bj_finish(conn, room)
            return
        s = room["seats"][i]
        if s.get("isBot"):
            _bj_bot_play(room, s)
            room["turn"] = _bj_next_turn(room, i)
            room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
        else:
            return   # 等真人操作


def _bj_finish(conn, room):
    d = room["dealer"]; d["reveal"] = True
    alive = any(s.get("bet", 0) > 0 and s.get("result") != "sit" and _bj_value(s["cards"]) <= 21
                for s in room["seats"])
    if alive:
        while _bj_value(d["cards"]) < 17:     # 庄家补到 17 停（软硬17都停）
            d["cards"].append(_bj_pop(room))
    _bj_settle(conn, room)
    room["phase"] = "result"; room["resolved_at"] = _now(); room["turn"] = -1


def _bj_settle(conn, room):
    d = room["dealer"]; dv = _bj_value(d["cards"])
    d_bj = _bj_is_blackjack(d["cards"]); d_bust = dv > 21
    for s in room["seats"]:
        bet = s.get("bet", 0)
        if bet <= 0:
            s["last_net"] = 0; continue
        pv = _bj_value(s["cards"]); p_bj = (s.get("result") == "bj")
        if pv > 21:
            res, ret = "bust", 0
        elif p_bj and not d_bj:
            res, ret = "bj", bet + (bet * 3) // 2          # 黑杰克赔 1.5 倍（向下取整）
        elif p_bj and d_bj:
            res, ret = "push", bet
        elif d_bj:
            res, ret = "lose", 0
        elif d_bust or pv > dv:
            res, ret = "win", bet * 2
        elif pv == dv:
            res, ret = "push", bet
        else:
            res, ret = "lose", 0
        net = ret - bet
        s["result"] = res; s["last_net"] = net
        if s.get("isBot"):
            s["chips"] = s.get("chips", 500) + net
        else:
            if ret > 0:
                _coins_add(conn, s["owner"], ret)
            _bump_daily(conn, s["owner"], net)
            tag = {"bj": "黑杰克! +", "win": "赢 +", "push": "平 ", "lose": "输 ", "bust": "爆牌 输 "}.get(res, "")
            room.setdefault("log", []).insert(0, f"　{s['name']}：{tag}{net if net < 0 else abs(net)}")
    room["log"] = room["log"][:40]


def _bj_start_new_round(room):
    room["round"] += 1; room["phase"] = "betting"
    room["dealer"] = {"cards": [], "reveal": False}
    room["deck"] = _bj_new_deck()
    room["turn"] = -1; room["turn_deadline"] = 0
    room["deadline"] = 0                                  # 由 _bj_tick 按是否够2真人动态管理
    room["start_requested"] = False
    for s in room["seats"]:
        s["bet"] = 0; s["cards"] = []; s["done"] = False; s["doubled"] = False; s["result"] = ""; s["ready"] = False
    _bj_ensure_fill(room)
    _bj_bots_bet(room)
    room.setdefault("log", []).insert(0, f"—— 第 {room['round']} 局，下注吧 ——"); room["log"] = room["log"][:40]


def _bj_tick(conn, room):
    """惰性状态机：下注→发牌→轮流决策→庄家结算→展示→下一局。
    计时只在“公共房且≥2真人”时才跑；只有 1 个真人（或单人房）时不限时、等玩家自己操作。"""
    t = _now(); ph = room.get("phase")
    timed = _bj_timed(room)
    if ph == "betting":
        humans = _bj_humans(room)
        # 倒计时管理：够2真人才开表；不够就停表（deadline=0），等其他真人 / 等唯一真人点发牌
        if timed:
            if not room.get("deadline"):
                room["deadline"] = t + BJ_BET_SECS
        else:
            room["deadline"] = 0
        ready = bool(humans) and all(s.get("ready") for s in humans)
        due = timed and t >= room.get("deadline", t + 999)
        if ready or due:
            if any(s.get("bet", 0) > 0 for s in room["seats"]):
                _bj_deal_round(room)
                _bj_progress(conn, room)
            elif timed:
                room["deadline"] = t + BJ_BET_SECS      # 计时房到点却没人下注 → 顺延
    elif ph == "playing":
        _bj_progress(conn, room)
        if room.get("phase") == "playing" and timed:     # 只有多真人房才有决策超时
            i = room.get("turn", -1)
            if i >= 0:
                s = room["seats"][i]
                if not s.get("isBot") and t >= room.get("turn_deadline", t + 999):
                    s["done"] = True                    # 超时自动停牌
                    room["turn"] = _bj_next_turn(room, i)
                    room["turn_deadline"] = t + BJ_TURN_SECS
                    _bj_progress(conn, room)
    elif ph == "result":
        if room.get("start_requested") or (timed and t - room.get("resolved_at", t) >= BJ_RESULT_SECS):
            room["start_requested"] = False
            _bj_start_new_round(room)


def _bj_view(room, room_id, owner):
    t = _now(); solo = room["mode"] == "solo"; ph = room.get("phase")
    timed = _bj_timed(room); hc = len(_bj_humans(room))
    v = {"id": room_id, "game": "blackjack", "mode": room["mode"], "round": room.get("round", 1),
         "phase": ph, "me": owner, "min_bet": BJ_MIN_BET, "solo": solo, "log": room.get("log", [])[:40]}
    v["human_count"] = hc
    v["timed"] = timed
    # 公共房里只有 1 个真人、还在下注 → 等其他玩家（不限时；也可直接发牌和机器人玩）
    v["waiting_for_players"] = (room["mode"] == "public" and hc < 2 and ph == "betting")
    v["bet_secs_left"] = (max(0, int(room.get("deadline", t) - t)) if (ph == "betting" and timed) else 0)
    v["result_secs_left"] = (max(0, int(BJ_RESULT_SECS - (t - room.get("resolved_at", t)))) if (ph == "result" and timed) else 0)
    # 庄家：未开牌时藏第二张
    d = room.get("dealer", {"cards": [], "reveal": False})
    if d.get("reveal"):
        v["dealer"] = {"cards": d["cards"], "value": _bj_value(d["cards"]), "reveal": True,
                       "blackjack": _bj_is_blackjack(d["cards"]), "bust": _bj_value(d["cards"]) > 21}
    else:
        shown = d["cards"][:1]
        cards = shown + ([{"hidden": True}] if len(d["cards"]) >= 2 else [])
        v["dealer"] = {"cards": cards, "value": _bj_value(shown), "reveal": False}
    turn_i = room.get("turn", -1)
    turn_owner = room["seats"][turn_i]["owner"] if ph == "playing" and 0 <= turn_i < len(room["seats"]) else None
    v["turn_owner"] = turn_owner
    seats = []
    for idx, s in enumerate(room["seats"]):
        seats.append({"owner": s.get("owner"), "name": s.get("name"), "isBot": s.get("isBot", False),
                      "version": s.get("version", "v1"), "hat": s.get("hat", "光头"), "outfit": s.get("outfit", 0),
                      "hair": s.get("hair", 0), "skin": s.get("skin", 0), "clothes": s.get("clothes", "无"),
                      "bet": s.get("bet", 0), "cards": s.get("cards", []),
                      "value": _bj_value(s.get("cards", [])) if s.get("cards") else 0,
                      "done": s.get("done", False), "doubled": s.get("doubled", False),
                      "result": s.get("result", ""), "last_net": s.get("last_net", 0),
                      "ready": s.get("ready", False), "is_turn": (idx == turn_i and ph == "playing")})
    v["seats"] = seats
    mine = next((s for s in room["seats"] if s.get("owner") == owner), None)
    v["my_bet"] = mine.get("bet", 0) if mine else 0
    v["my_ready"] = bool(mine.get("ready")) if mine else False
    v["my_turn"] = (turn_owner == owner)
    v["can_double"] = bool(v["my_turn"] and mine and len(mine.get("cards", [])) == 2)
    humans = [s for s in room["seats"] if not s.get("isBot")]
    v["ready_total"] = len(humans)
    v["ready_count"] = sum(1 for s in humans if s.get("ready"))
    return v


def _bj_augment(conn, v, owner):
    v["coins"] = _coins_get(conn, owner)
    v["top_winner"] = top_winner(conn)
    return v


def get_bj_room(owner, room_id):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在或已解散"}
        _bj_tick(conn, room)
        _save(conn, room_id, room)
        return _bj_augment(conn, _bj_view(room, room_id, owner), owner)
    finally:
        conn.close()


def bj_bet(owner, room_id, amount):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _bj_tick(conn, room)
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        msg = None
        if not seat:
            msg = "你不在这个房间里"
        elif room.get("phase") != "betting":
            msg = "本局已开始，等下一局"
        else:
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                amount = 0
            if amount < BJ_MIN_BET:
                msg = f"最少下 {BJ_MIN_BET} 币"
            elif _coins_get(conn, owner) < amount:
                msg = "金币不够啦，去打工赚点~"
            else:
                _coins_add(conn, owner, -amount)
                seat["bet"] = seat.get("bet", 0) + amount
                seat["ready"] = False
                msg = "下注成功"
        _save(conn, room_id, room)
        v = _bj_augment(conn, _bj_view(room, room_id, owner), owner); v["msg"] = msg
        return v
    finally:
        conn.close()


def bj_clear(owner, room_id):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _bj_tick(conn, room)
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        msg = "现在没有可撤回的下注"
        if seat and room.get("phase") == "betting" and seat.get("bet", 0) > 0:
            _coins_add(conn, owner, seat["bet"])
            msg = f"已撤回，退还 {seat['bet']} 币"
            seat["bet"] = 0; seat["ready"] = False
        _save(conn, room_id, room)
        v = _bj_augment(conn, _bj_view(room, room_id, owner), owner); v["msg"] = msg
        return v
    finally:
        conn.close()


def bj_deal(owner, room_id):
    """开始发牌：切换自己的“准备好了”。所有真人都准备好（或公共房到点）就发牌。"""
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        msg = None
        if room.get("phase") == "betting":
            seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
            if seat and seat.get("bet", 0) <= 0:
                msg = "先下注再开始~"
            elif seat:
                seat["ready"] = not seat.get("ready", False)
                msg = "准备好了，等发牌~" if seat["ready"] else "已取消准备"
            _bj_tick(conn, room)
        _save(conn, room_id, room)
        v = _bj_augment(conn, _bj_view(room, room_id, owner), owner)
        if msg:
            v["msg"] = msg
        return v
    finally:
        conn.close()


def bj_action(owner, room_id, action):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        _bj_tick(conn, room)
        msg = None
        if room.get("phase") != "playing":
            msg = "现在不能操作"
        else:
            i = room.get("turn", -1)
            seat = room["seats"][i] if 0 <= i < len(room["seats"]) else None
            if not seat or seat.get("owner") != owner:
                msg = "还没轮到你~"
            elif action == "hit":
                seat["cards"].append(_bj_pop(room))
                if _bj_value(seat["cards"]) >= 21:
                    if _bj_value(seat["cards"]) > 21:
                        seat["result"] = "bust"
                    seat["done"] = True
                    room["turn"] = _bj_next_turn(room, i)
                    room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
                    _bj_progress(conn, room)
                else:
                    room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
                msg = "要牌"
            elif action == "stand":
                seat["done"] = True
                room["turn"] = _bj_next_turn(room, i)
                room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
                _bj_progress(conn, room)
                msg = "停牌"
            elif action == "double":
                if len(seat.get("cards", [])) != 2:
                    msg = "只有前两张牌时才能双倍"
                elif _coins_get(conn, owner) < seat.get("bet", 0):
                    msg = "金币不够，双倍要再压一注"
                else:
                    _coins_add(conn, owner, -seat["bet"])
                    seat["bet"] *= 2; seat["doubled"] = True
                    seat["cards"].append(_bj_pop(room))
                    if _bj_value(seat["cards"]) > 21:
                        seat["result"] = "bust"
                    seat["done"] = True
                    room["turn"] = _bj_next_turn(room, i)
                    room["turn_deadline"] = 0 if room["mode"] == "solo" else _now() + BJ_TURN_SECS
                    _bj_progress(conn, room)
                    msg = "双倍！再来一张"
            else:
                msg = "未知操作"
        _save(conn, room_id, room)
        v = _bj_augment(conn, _bj_view(room, room_id, owner), owner)
        if msg:
            v["msg"] = msg
        return v
    finally:
        conn.close()


def bj_next(owner, room_id):
    """结果阶段点“再来一局”：展示够 ~2 秒就立刻开下一局。"""
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"error": "房间不存在"}
        if room.get("phase") == "result" and (_now() - room.get("resolved_at", 0)) >= 2:
            _bj_start_new_round(room)
        else:
            _bj_tick(conn, room)
        _save(conn, room_id, room)
        return _bj_augment(conn, _bj_view(room, room_id, owner), owner)
    finally:
        conn.close()


def leave_bj_room(owner, room_id):
    conn = _connect()
    try:
        room = _load(conn, room_id)
        if not room:
            return {"ok": True}
        seat = next((s for s in room["seats"] if s.get("owner") == owner), None)
        if seat:
            if room.get("phase") == "betting" and seat.get("bet", 0) > 0:
                _coins_add(conn, owner, seat["bet"])   # 退还未开局的注
            room["seats"] = [s for s in room["seats"] if s.get("owner") != owner]
        humans = [s for s in room["seats"] if not s.get("isBot")]
        if not humans:
            conn.execute("DELETE FROM casino_rooms WHERE id=?", (room_id,)); conn.commit()
        else:
            _bj_ensure_fill(room)
            if room.get("phase") == "betting":
                _bj_bots_bet(room, only_empty=True)
            elif room.get("phase") == "playing":
                room["turn"] = _bj_next_turn(room, -1)   # 有人走了，重算轮到谁
                _bj_progress(conn, room)
            _save(conn, room_id, room)
        return {"ok": True}
    finally:
        conn.close()
