# pet.py —— 像素JZH养成 P1（服务器端状态：发JZH / 唤醒 / 喂食 / 洗澡 / 摸头 / 互动 / 说话）
# 放在项目根目录，和 app.py 同层。app.py 里 import 它并加几个路由即可。
 
import sqlite3
import json
import time
import random
 
DB = "users.db"
 
# ====== 版本"口子" ======
# 初始只有 v1（就是你给的那张像素图）。以后你生成新像素JZH，只要：
#   1) 在 pet.html 的 VERSIONS 里加一张新的字符网格（比如 "v2": [...]）
#   2) 把这里的名字加上 "v2"
# 新版本就自动进入随机池，老逻辑一律不用动。
PET_VERSIONS = ["v1", "v2", "v3", "v4", "v5", "v6", "v7"]
 
# ====== 说话库（点击JZH随机说一句，随时增删）======
SAYINGS = {
    "cocoon": ["呼…呼…", "（睡得很香）", "再睡五分钟"],
    "kid":    ["我才是皇帝！", "背单词好舒服", "今天也要吃咖喱", "陪我玩", "我不困"],
    "adult":  ["谢谢老板", "急了", "为什么不去上班", "先吃饭再说", "今天也辛苦了", "骂我一会呗"],
}
 
# ====== 节奏参数（随时调）======
WAKE_GAP        = 30      # 唤醒间隔：4分钟（秒）
WAKES_NEED      = 2       # 唤醒几次进幼年
FEED_GAP        = 1200     # 喂食间隔：20分钟（秒）
GROWTH_TO_ADULT = 100      # 幼年→成年所需成长值（每次喂食 +20，约 5 次）
SPIRIT_MAX      = 100
PET_GAP         = 60       # 摸头间隔：60 秒（不能猛点）
PLAY_GAP        = 120      # 互动间隔：120 秒（不能猛点）
AFFECTION_MAX   = 100      # 好感满值（满了 JZH 偶尔会叫姐姐）
DROP_GAP        = 3600    # 掉落间隔：1小时（秒）——隔这么久能领一次掉落
DROP_HAT_CHANCE = 0.1     # 掉落时出"装饰(帽子)"的概率，其余出颜色（越小帽子越稀有）
WORK_SEC        = 2400     # 打工外出时长：40分钟（秒）
SHOP_SEC        = 1200     # 购物外出时长：20分钟（秒）
SHOP_COST       = 30       # 出去购物花费的金币
WORK_COINS      = (30, 50) # 打工收入区间（随机取一个）
WORK_DECO_CHANCE = 0.03    # 打工顺路捡到装饰的概率
PARTNER_GAP      = 3600    # 结伴后多久诞生下一代：1 小时（秒）
PLAZA_TICK_GAP   = 600     # 牧场事件最短间隔：10分钟（避免每次刷新都触发）
LOG_KEEP         = 150     # 每只JZH自己的记录最多保留多少条
LIVES_KEEP       = 40      # 记录册最多保留多少"代"JZH的生平（超出丢最老的）
ADULT_LIFESPAN  = 4 * 86400   # 成年多久自然变老：12 天（秒）
BRED_TO_OLD     = 43200        # 生了后代后多久变老：半天（秒）
OLD_LIFESPAN    = 43200        # 老年持续多久化云：1 天（秒）
NEGLECT_LOW     = 8            # 健康(饱食/清洁/精神取最低者)低于此值算濒危
NEGLECT_DEATH   = 43200        # 濒危持续多久死亡：12 小时（秒）
REL_PARTNER_MIN = 10           # 结伴所需的最低好感
 
# 可解锁的装扮池（帽子名字要和 pet.html 里 HATS 的键完全一致）
HAT_POOL = ["光头", "蝴蝶结", "小花", "贝雷帽", "耳机", "毛线帽", "小丑鼻",
            "墨镜", "小皇冠", "天使环", "猫耳", "鸭舌帽", "头巾", "呆毛", "花环", "恶魔角", "眼罩",
            "独角兽角", "星星发卡", "西瓜帽", "派对尖帽"]
OUTFIT_N, HAIR_N, SKIN_N = 8, 5, 7   # 上衣/发色/肤色各有几种（和 pet_sprite.js 里的 OUTFITS/HAIRS/SKINS 数量一致）

# 衣服款式与解锁价格（用打工赚的金币购买；名字要和 pet_sprite.js 的 CLOTHES 完全一致）
# 想加新衣服：这里加一行价格，再去 pet_sprite.js 的 CLOTHES 里加对应图层即可。
CLOTHES_PRICE = {"连帽衫": 50, "条纹衫": 50, "背带裤": 70, "西装领带": 200}
 
def _now():   return time.time()
def _clamp(v, a, b): return max(a, min(b, v))
def _connect(): return sqlite3.connect(DB, timeout=15)
 
 
def ensure_pet_tables():
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pets (
                owner TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'home',
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()
 
 
def _fresh():
    """新出生的JZH（睡袋态）。版本随机，出生只带一套随机配色，其余靠掉落解锁。"""
    t = _now()
    ofit = random.randint(0, OUTFIT_N - 1)
    hr   = random.randint(0, HAIR_N - 1)
    sk   = random.randint(0, SKIN_N - 1)
    ver  = random.choice(PET_VERSIONS)
    return {
        "version": ver,
        "name": "",                 # 空=还没起名，进养成页会先让用户取名
        "stage": "cocoon",          # cocoon睡袋 / kid幼年 / adult成年
        "wake": 0,
        "last_wake": 0,
        "growth": 0,
        "hunger": 70, "clean": 70, "spirit": 60, "affection": 0,
        "hat": "光头",
        "outfit": ofit, "hair": hr, "skin": sk,
        "clothes": "无",            # 当前穿的衣服款式（"无"=只穿基础上衣）
        "status": "home", "out_until": 0,
        "coins": 0,
        # 已解锁的装扮：一开始只有光头 + 出生带的这套配色 + 衣服只有"无"
        "owned": {"hat": ["光头"], "outfit": [ofit], "hair": [hr], "skin": [sk], "clothes": ["无"]},
        "last_drop": 0,             # 0 = 出生后第一次掉落立刻可领
        "born": t, "last": t, "last_fed": 0,
        # 每只JZH一份独立生平：名字/版本/出生/化云原因 + 它自己的成长记录 log
        "lives": [{"no": 1, "name": "", "version": ver, "born": t,
                   "born_str": _datestr(), "died": 0, "cause": "", "log": []}],
    }
 
 
def _migrate(s):
    """老存档没有 owned/last_drop 字段时补上（把身上穿的算作已解锁）。"""
    if "owned" not in s:
        s["owned"] = {
            "hat": list({"光头", s.get("hat", "光头")}),
            "outfit": [s.get("outfit", 0)],
            "hair": [s.get("hair", 0)],
            "skin": [s.get("skin", 0)],
        }
    # 衣服款式：老存档补默认"无"，并保证 owned 里有 clothes 分类
    if "clothes" not in s:
        s["clothes"] = "无"
    if "clothes" not in s.setdefault("owned", {}):
        s["owned"]["clothes"] = ["无"]
    if "无" not in s["owned"]["clothes"]:
        s["owned"]["clothes"].insert(0, "无")
    if "last_drop" not in s:
        s["last_drop"] = 0
    if "coins" not in s:
        s["coins"] = 0
    if "out_until" not in s:
        s["out_until"] = 0
    if "status" not in s:
        s["status"] = "home"
    if "log" not in s:
        s["log"] = []
    if "family" not in s:
        s["family"] = []
    if "friends" not in s:
        s["friends"] = []
    if "partner" not in s:
        s["partner"] = None
    if "partner_since" not in s:
        s["partner_since"] = 0
    if "rel" not in s:
        s["rel"] = {}
    if "sick_since" not in s:
        s["sick_since"] = 0
    if "bred_at" not in s:
        s["bred_at"] = 0
    if "adult_at" not in s:
        s["adult_at"] = s.get("born", 0)
    if "old_at" not in s:
        s["old_at"] = 0
    if "old_cause" not in s:
        s["old_cause"] = "natural"
    if "death_cause" not in s:
        s["death_cause"] = ""
    if "heir" not in s:
        s["heir"] = None
    if "last_pet" not in s:
        s["last_pet"] = 0
    if "last_play" not in s:
        s["last_play"] = 0
    # 老存档没有"生平"结构：把它现有的流水记录，整体归到"当前这只JZH"名下
    if "lives" not in s:
        s["lives"] = [{
            "no": 1,
            "name": s.get("name", ""),
            "version": s.get("version", "v1"),
            "born": s.get("born", 0),
            "born_str": "",
            "died": (s.get("died_at", 0) if s.get("stage") == "cloud" else 0),
            "cause": (s.get("death_cause", "") if s.get("stage") == "cloud" else ""),
            "log": list(s.get("log", [])),
        }]
    return s
 
 
def _datestr():
    return time.strftime("%m-%d %H:%M", time.localtime())
 
 
def _current_life(s):
    """取"当前这只JZH"的生平条目（没有就补一个，兼容老存档）。"""
    lives = s.setdefault("lives", [])
    if not lives:
        lives.append({"no": 1, "name": s.get("name", ""), "version": s.get("version", "v1"),
                      "born": s.get("born", 0), "born_str": "", "died": 0, "cause": "",
                      "log": list(s.get("log", []))})
    return lives[-1]


def _addlog(s, text):
    """记到"当前这只JZH"自己的记录里（相邻重复不记，避免刷屏）。"""
    life = _current_life(s)
    log = life.setdefault("log", [])
    if log and log[0].get("text") == text:
        return
    log.insert(0, {"t": _datestr(), "text": text})
    life["log"] = log[:LOG_KEEP]
 
 
def _getset(conn, k, d=""):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else d
 
 
def _setset(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, str(v)))
 
 
def _child_version(a, b):
    """后代/重生的版本：随机选一个（有多版本时自然刷出不同版本）。"""
    return random.choice(PET_VERSIONS)
 
 
def _rel(s, other, delta):
    r = s.setdefault("rel", {})
    r[other] = max(-30, min(60, r.get(other, 0) + delta))
 
 
def _relv(s, other):
    return s.get("rel", {}).get(other, 0)
 
 
def _die(s, cause):
    """JZH化云。cause: neglect(养死) / natural(自然老死) / bred(生育后衰老)。"""
    s["stage"] = "cloud"
    s["death_cause"] = cause
    s["died_at"] = _now()
    life = _current_life(s)          # 给这只JZH的生平盖上"化云"的时间与原因
    life["died"] = _now()
    life["cause"] = cause
 
 
def _progress(s):
    """按时间推进生命阶段：濒危致死、成年变老、老年化云。睡袋/云/牧场托管不推进。"""
    t = _now()
    st = s.get("stage")
    if st in ("cocoon", "cloud") or s.get("status") == "plaza":
        return
    # 疏于照顾致死：健康(三项取最低)持续过低超过 NEGLECT_DEATH
    health = min(s.get("hunger", 100), s.get("clean", 100), s.get("spirit", 100))
    if health <= NEGLECT_LOW:
        if not s.get("sick_since"):
            s["sick_since"] = t
        elif t - s["sick_since"] >= NEGLECT_DEATH:
            _die(s, "neglect")
            return
    else:
        s["sick_since"] = 0
    # 成年 → 老年（生育过的 1 天，没生育的 12 天）
    if st == "adult":
        if s.get("bred_at") and t - s["bred_at"] >= BRED_TO_OLD:
            s["stage"] = "old"; s["old_at"] = t; s["old_cause"] = "bred"
        elif t - s.get("adult_at", s.get("born", t)) >= ADULT_LIFESPAN:
            s["stage"] = "old"; s["old_at"] = t; s["old_cause"] = "natural"
    # 老年 → 云
    elif st == "old":
        if t - s.get("old_at", t) >= OLD_LIFESPAN:
            _die(s, s.get("old_cause", "natural"))
 
 
def _unlock_random(s, cats=None):
    """随机解锁一样还没有的装扮（cats 可限定类别，如 {"hat"}），返回友好名字；没有可解锁返回 None。"""
    locked = _locked(s, cats)
    if not locked:
        return None
    cat, val = random.choice(locked)
    s.setdefault("owned", {}).setdefault(cat, []).append(val)
    if cat == "hat":
        return str(val)                                   # 帽子：val 是名字
    prefix = {"outfit": "上衣配色#", "hair": "发色#", "skin": "肤色#"}[cat]
    return prefix + str(val + 1)                          # 颜色：val 是数字，才 +1
 
 
def _locked(s, cats=None):
    """返回还没解锁的装扮列表 [(类别, 值), ...]；cats 可限定只看某些类别（如 {"hat"}）。"""
    cats = cats or {"hat", "outfit", "hair", "skin"}
    o = s.get("owned", {})
    locked = []
    if "hat" in cats:
        for h in HAT_POOL:
            if h not in o.get("hat", []):
                locked.append(("hat", h))
    if "outfit" in cats:
        for i in range(OUTFIT_N):
            if i not in o.get("outfit", []):
                locked.append(("outfit", i))
    if "hair" in cats:
        for i in range(HAIR_N):
            if i not in o.get("hair", []):
                locked.append(("hair", i))
    if "skin" in cats:
        for i in range(SKIN_N):
            if i not in o.get("skin", []):
                locked.append(("skin", i))
    return locked
 
 
def _save(conn, owner, s):
    conn.execute(
        "INSERT OR REPLACE INTO pets (owner, version, status, data, updated_at) VALUES (?,?,?,?,?)",
        (owner, s["version"], s.get("status", "home"), json.dumps(s, ensure_ascii=False), _now()))
    conn.commit()
 
 
def _decay(s):
    """按离上次结算过了多久，扣饱食/清洁/精神。睡袋期不衰减。"""
    t = _now()
    mins = (t - s.get("last", t)) / 60.0
    s["last"] = t
    # 睡袋期、以及在牧场托管期间都不衰减（托管=安全寄存）
    if s["stage"] == "cocoon" or s.get("status") == "plaza" or mins < 0.2:
        return
    d = min(mins, 720)   # 离线再久，最多按 12 小时算，避免一次扣爆
    s["hunger"] = _clamp(s["hunger"] - d * 0.06, 0, 100)
    s["clean"]  = _clamp(s["clean"]  - d * 0.08, 0, 100)
    s["spirit"] = _clamp(s["spirit"] - d * 0.03, 0, SPIRIT_MAX)
 
 
def _public(s):
    """整理成给前端的视图，附带能否唤醒/喂食和倒计时。"""
    t = _now()
    wake_in = max(0, int(WAKE_GAP - (t - s.get("last_wake", 0))))
    feed_in = max(0, int(FEED_GAP - (t - s.get("last_fed", 0))))
    v = dict(s)
    v["can_wake"]    = (s["stage"] == "cocoon" and wake_in == 0)
    v["can_feed"]    = (s["stage"] != "cocoon" and feed_in == 0)
    v["wake_in"]     = wake_in
    v["feed_in"]     = feed_in
    v["wakes_need"]  = WAKES_NEED
    v["growth_need"] = GROWTH_TO_ADULT
    v["spirit_max"]  = SPIRIT_MAX
    v["owned"]       = s.get("owned", {})
    v["can_drop"]    = (s["stage"] != "cocoon" and (t - s.get("last_drop", 0)) >= DROP_GAP)
    v["drop_in"]     = max(0, int(DROP_GAP - (t - s.get("last_drop", 0))))
    v["has_locked"]  = len(_locked(s)) > 0
    v["coins"]       = s.get("coins", 0)
    v["status"]      = s.get("status", "home")
    v["shop_cost"]   = SHOP_COST
    v["out_in"]      = max(0, int(s.get("out_until", 0) - t)) if s.get("status", "home") != "home" else 0
    v["can_collect"] = (s.get("status", "home") != "home" and t >= s.get("out_until", 0))
    v["can_pet"]     = (t - s.get("last_pet", 0)) >= PET_GAP
    v["pet_in"]      = max(0, int(PET_GAP - (t - s.get("last_pet", 0))))
    v["can_play"]    = (t - s.get("last_play", 0)) >= PLAY_GAP
    v["play_in"]     = max(0, int(PLAY_GAP - (t - s.get("last_play", 0))))
    v["lives"]       = s.get("lives", [])          # 每只JZH的生平（记录册页面用）
    v["log"]         = _current_life(s).get("log", [])   # 兼容：当前这只的记录
    v["family"]      = s.get("family", [])
    v["friends_n"]   = len(s.get("friends", []))
    v["death_cause"] = s.get("death_cause", "")
    v["clothes"]     = s.get("clothes", "无")
    v["clothes_price"] = CLOTHES_PRICE            # 前端画价格/买衣服弹框用
    v["needs_name"]  = (not s.get("name")) and s.get("stage") != "cloud"
    return v
 
 
def get_state(owner):
    """读取（或首次自动创建）某用户的JZH，结算衰减后返回视图。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
        s = _migrate(json.loads(row[0])) if row else _fresh()
        _decay(s)
        _progress(s)
        _save(conn, owner, s)
        return _public(s)
    finally:
        conn.close()
 
 
def _process_offspring(conn, pets, t):
    """结伴满 1 小时、双方都还在牧场，就各生一个后代。"""
    by_owner = {o: s for o, s in pets}
    done = set()
    for owner, s in pets:
        if owner in done:
            continue
        p = s.get("partner")
        if not p or (t - s.get("partner_since", 0)) < PARTNER_GAP:
            continue
        ps = by_owner.get(p)
        if not ps or ps.get("partner") != owner:      # 伴侣不在牧场/关系不成立→解除
            s["partner"] = None; s["partner_since"] = 0
            _save(conn, owner, s)
            continue
        ver = _child_version(s, ps)
        # parents 用真实用户名（owner），谁和谁养育一目了然
        s.setdefault("family", []).append({"name": "", "version": ver, "parents": [owner, p], "born": _datestr()})
        ps.setdefault("family", []).append({"name": "", "version": ver, "parents": [owner, p], "born": _datestr()})
        _addlog(s,  f"🍼 你和 {p} 的JZH有了一个后代（{ver}），它将在 1 天后步入老年")
        _addlog(ps, f"🍼 你和 {owner} 的JZH有了一个后代（{ver}），它将在 1 天后步入老年")
        s["partner"] = None; s["partner_since"] = 0; s["bred_at"] = t
        ps["partner"] = None; ps["partner_since"] = 0; ps["bred_at"] = t
        # 记下"继承人"：本体化云后，重生的就是这个后代（用它的版本 + 记下父母用户名）
        s["heir"]  = {"version": ver, "parents": [owner, p]}
        ps["heir"] = {"version": ver, "parents": [owner, p]}
        _save(conn, owner, s); _save(conn, p, ps)
        done.add(owner); done.add(p)
 
 
# ====== 牧场事件库（随时增删，改完不用动别的逻辑）======
# 成对事件：(权重, 好感变化, A的记录, B的记录)。{o} 会替换成对方主人的用户名。
PLAZA_PAIR_EVENTS = [
    (6,  2, "在牧场遇到了 {o} 的JZH",            "在牧场遇到了 {o} 的JZH"),
    (5,  3, "和 {o} 的JZH一起晒了会儿太阳 ☀️",   "和 {o} 的JZH一起晒了会儿太阳 ☀️"),
    (4,  4, "把零食分给了 {o} 的JZH 🍪",          "{o} 的JZH分了零食给它 🍪"),
    (4,  5, "帮 {o} 的JZH理了理毛发",             "{o} 的JZH帮它理了理毛发，好舒服"),
    (4,  4, "和 {o} 的JZH玩起了捉迷藏",           "和 {o} 的JZH玩起了捉迷藏"),
    (3,  5, "夜里和 {o} 的JZH一起看星星 ✨",      "夜里和 {o} 的JZH一起看星星 ✨"),
    (3,  3, "教 {o} 的JZH念了个新词儿",           "{o} 的JZH教了它一个新词儿"),
    (3,  2, "和 {o} 的JZH比赛谁跑得快",           "和 {o} 的JZH比赛谁跑得快"),
    (3,  5, "摘了朵小花送给 {o} 的JZH 🌼",        "收到了 {o} 的JZH送的小花 🌼"),
    (2,  6, "和 {o} 的JZH合唱了一首歌 🎵",        "和 {o} 的JZH合唱了一首歌 🎵"),
    (2,  4, "和 {o} 的JZH背靠背发了会儿呆",       "和 {o} 的JZH背靠背发了会儿呆"),
    (3, -3, "和 {o} 的JZH抢零食，拌了两句嘴",     "和 {o} 的JZH抢零食，拌了两句嘴"),
    (2, -4, "被 {o} 的JZH抢走了玩具，气鼓鼓",     "抢了 {o} 的JZH的玩具，得意了一下"),
    (2, -6, "和 {o} 的JZH闹了别扭 😾",            "和 {o} 的JZH闹了别扭 😾"),
]
# 单只小事件：随机挑一只，纯氛围、不改好感。
PLAZA_SOLO_EVENTS = [
    "在牧场追了会儿蝴蝶 🦋",
    "找了个角落打了个盹 💤",
    "对着水面照了半天镜子",
    "捡到一片好看的叶子 🍃",
    "打了个大喷嚏，把自己吓一跳",
    "在草地上打了几个滚",
    "蹲在原地数天上的云",
    "自个儿哼起了小曲儿 🎶",
    "被一只路过的猫盯了很久",
    "挖了个小坑又把它填上",
]


def plaza_tick(conn):
    """牧场后台事件：限频运行。随机遇见/交朋友，成年结伴，结伴满时生后代。"""
    t = _now()
    try:
        last = float(_getset(conn, "plaza_last_tick", "0") or 0)
    except ValueError:
        last = 0
    if t - last < PLAZA_TICK_GAP:
        return
    _setset(conn, "plaza_last_tick", t); conn.commit()
 
    rows = conn.execute("SELECT owner, data FROM pets WHERE status='plaza'").fetchall()
    pets = []
    for owner, data in rows:
        try:
            pets.append([owner, _migrate(json.loads(data))])
        except Exception:
            pass
    if len(pets) >= 2:
        n = len(pets)
        pair_w = [e[0] for e in PLAZA_PAIR_EVENTS]
        for _ in range(min(4, n)):          # 每轮最多 4 次事件
            # 两成概率是"某只自己的小事件"，其余是"两只互动"
            if random.random() < 0.2:
                _, s_solo = pets[random.randrange(n)]
                _addlog(s_solo, random.choice(PLAZA_SOLO_EVENTS))
                continue
            a, b = random.sample(range(n), 2)
            oa, sa = pets[a]; ob, sb = pets[b]
            # 还不是朋友、又已经比较熟：有机会正式成为朋友（好感大涨）
            if ob not in sa.get("friends", []) and _relv(sa, ob) >= 6 and random.random() < 0.35:
                sa.setdefault("friends", []).append(ob)
                sb.setdefault("friends", []).append(oa)
                _rel(sa, ob, 8); _rel(sb, oa, 8)
                _addlog(sa, f"和 {ob} 的JZH成为了朋友 🎏")
                _addlog(sb, f"和 {oa} 的JZH成为了朋友 🎏")
            else:                                              # 从事件库里按权重随机挑一件
                _, delta, ta, tb = random.choices(PLAZA_PAIR_EVENTS, weights=pair_w, k=1)[0]
                _rel(sa, ob, delta); _rel(sb, oa, delta)
                _addlog(sa, ta.format(o=ob))
                _addlog(sb, tb.format(o=oa))
            # 结伴：都成年、都没伴侣、双方好感达标；概率随好感升高，吵过架的很难成
            if (sa.get("stage") == "adult" and sb.get("stage") == "adult"
                    and not sa.get("partner") and not sb.get("partner")):
                r = min(_relv(sa, ob), _relv(sb, oa))
                if r >= REL_PARTNER_MIN and random.random() < min(0.9, r / 40.0):
                    sa["partner"] = ob; sa["partner_since"] = t
                    sb["partner"] = oa; sb["partner_since"] = t
                    _addlog(sa, f"💞 和 {ob} 的JZH两情相悦，结伴了")
                    _addlog(sb, f"💞 和 {oa} 的JZH两情相悦，结伴了")
        for owner, s in pets:
            _save(conn, owner, s)
    _process_offspring(conn, pets, t)
 
 
def plaza_data(me, limit=40):
    """返回牧场里所有托管的JZH（给前端画），外加说话库。"""
    conn = _connect()
    try:
        plaza_tick(conn)                     # 顺便推进牧场互动
        rows = conn.execute(
            "SELECT owner, data FROM pets WHERE status='plaza' LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    pets = []
    for owner, data in rows:
        try:
            s = json.loads(data)
        except Exception:
            continue
        pets.append({
            "owner": owner,
            "name": s.get("name", "JZH"),
            "version": s.get("version", "v1"),
            "stage": s.get("stage", "adult"),
            "hat": s.get("hat", "光头"),
            "outfit": s.get("outfit", 0),
            "hair": s.get("hair", 0),
            "skin": s.get("skin", 0),
        })
    return {"pets": pets, "sayings": SAYINGS, "me": me}
 
 
def act(owner, action):
    """执行一个养成动作，返回更新后的视图（含一句提示 msg）。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
        s = _migrate(json.loads(row[0])) if row else _fresh()
        _decay(s)
        _progress(s)
        t = _now()
        msg = None
 
        # 已化云：除了"迎接新JZH"，其它动作一律不处理
        if s.get("stage") == "cloud" and action != "reborn":
            _save(conn, owner, s)
            r = _public(s); r["msg"] = "它已经化作云了…迎接新的JZH吧"
            return r
 
        if action == "wake":
            if s["stage"] == "cocoon" and (t - s.get("last_wake", 0)) >= WAKE_GAP:
                s["wake"] += 1
                s["last_wake"] = t
                if s["wake"] >= WAKES_NEED:
                    s["stage"] = "kid"; s["born"] = t
                    msg = "☀️ 终于起床了！"
                else:
                    msg = f"叫醒了一次（{s['wake']}/{WAKES_NEED}）"
            else:
                msg = "还没到叫醒的时候…"
 
        elif action == "feed":
            if s["stage"] == "cocoon":
                msg = "还在睡，喂不了"
            elif (t - s.get("last_fed", 0)) >= FEED_GAP:
                s["last_fed"] = t
                s["hunger"] = _clamp(s["hunger"] + 30, 0, 100)
                s["growth"] += 20
                msg = "吃饱啦～"
                if s["stage"] == "kid" and s["growth"] >= GROWTH_TO_ADULT:
                    s["stage"] = "adult"; s["growth"] = 0; s["adult_at"] = t
                    msg = "✨ 长成成年态啦！"
            else:
                msg = "还不饿，过会儿再喂"
 
        elif action == "wash":
            s["clean"] = 100
            msg = "洗得干干净净～"
 
        elif action == "pet":       # 摸头，慢慢加好感（有冷却，不能猛点）
            if (t - s.get("last_pet", 0)) >= PET_GAP:
                s["last_pet"] = t
                s["affection"] = _clamp(s.get("affection", 0) + 4, 0, AFFECTION_MAX)
                msg = "JZH锤了锤你"
            else:
                msg = "JZH还在害羞呢～"
 
        elif action == "play":      # 互动，涨精神 + 少量好感（有冷却）
            if (t - s.get("last_play", 0)) >= PLAY_GAP:
                s["last_play"] = t
                s["spirit"] = _clamp(s["spirit"] + 15, 0, SPIRIT_MAX)
                s["clean"]  = _clamp(s["clean"] - 5, 0, 100)
                s["affection"] = _clamp(s.get("affection", 0) + 2, 0, AFFECTION_MAX)
                msg = "玩得好开心！"
            else:
                msg = "玩累了，歇会儿再玩～"
 
        elif action == "say":       # 点击JZH，随机说一句；好感满了偶尔叫姐姐
            if s.get("affection", 0) >= AFFECTION_MAX and random.random() < 0.35:
                msg = random.choice(["姐姐~", "姐姐最好了", "就喜欢姐姐嘿嘿", "姐姐再摸摸我嘛"])
            else:
                pool = SAYINGS.get(s["stage"], SAYINGS["adult"])
                msg = random.choice(pool)
 
        elif action == "drop":      # 领取掉落，随机解锁一样装扮
            if s["stage"] == "cocoon":
                msg = "还在睡呢"
            elif (t - s.get("last_drop", 0)) >= DROP_GAP:
                # 大概率出颜色，小概率(DROP_HAT_CHANCE)才出装饰帽子；某类没得出就退而求其次
                if random.random() < DROP_HAT_CHANCE:
                    got = _unlock_random(s, {"hat"}) or _unlock_random(s, {"outfit", "hair", "skin"})
                else:
                    got = _unlock_random(s, {"outfit", "hair", "skin"}) or _unlock_random(s, {"hat"})
                s["last_drop"] = t
                msg = ("🎁 掉落解锁：「" + got + "」") if got else "装扮已经集齐啦～"
            else:
                msg = "还没到掉落时间"
 
        elif action == "work":      # 出去打工赚金币
            if s["stage"] == "cocoon":
                msg = "还在睡呢"
            elif s.get("status", "home") != "home":
                msg = "它已经在外面啦"
            else:
                s["status"] = "working"; s["out_until"] = t + WORK_SEC
                msg = "去打工了，过会儿回来收工～"
 
        elif action == "shop":      # 出去购物淘装饰（花金币）
            if s["stage"] == "cocoon":
                msg = "还在睡呢"
            elif s.get("status", "home") != "home":
                msg = "它已经在外面啦"
            elif s.get("coins", 0) < SHOP_COST:
                msg = f"金币不够（需要 {SHOP_COST}）"
            else:
                s["coins"] -= SHOP_COST
                s["status"] = "shopping"; s["out_until"] = t + SHOP_SEC
                msg = "去逛街了，看看能淘到啥～"
 
        elif action == "collect":   # 外出结束，收获
            if s.get("status", "home") == "home":
                msg = "它在家呢"
            elif t < s.get("out_until", 0):
                msg = "还没回来"
            else:
                st = s["status"]
                s["status"] = "home"; s["out_until"] = 0
                if st == "working":
                    earn = random.randint(*WORK_COINS)
                    s["coins"] = s.get("coins", 0) + earn
                    s["hunger"] = _clamp(s["hunger"] - 10, 0, 100)
                    s["spirit"] = _clamp(s["spirit"] - 10, 0, SPIRIT_MAX)
                    msg = f"💰 打工回来，赚了 {earn} 金币"
                    if random.random() < WORK_DECO_CHANCE:
                        got = _unlock_random(s, {"hat"})   # 打工顺手捡到的算装饰（帽子）
                        if got:
                            msg += f"，还顺手捡到装饰「{got}」！"
                else:  # shopping：一定带回一件装饰（帽子）；帽子都齐了就退钱
                    got = _unlock_random(s, {"hat"})
                    if got:
                        msg = f"🛍️ 淘到了新装饰「{got}」！"
                    else:
                        s["coins"] = s.get("coins", 0) + SHOP_COST
                        msg = "🛍️ 逛了一圈，装饰都集齐了，金币退你～"
 
        elif action == "consign":   # 送去牧场托管
            if s["stage"] == "cocoon":
                msg = "还在睡，先叫醒它"
            elif s.get("status", "home") not in ("home",):
                msg = "它现在没在家，没法托管"
            else:
                s["status"] = "plaza"
                msg = "🌿 送到牧场托管啦，去牧场能看到它"
        elif action == "retrieve":  # 从牧场领回
            if s.get("status") == "plaza":
                s["status"] = "home"
                msg = "🏠 把JZH领回来啦"
            else:
                msg = "它不在牧场"
 
        elif action == "reborn":    # 化云之后，迎接新JZH
            if s.get("stage") != "cloud":
                msg = "它还好好的呢"
            else:
                cause = s.get("death_cause", "natural")
                inherit = (cause != "neglect")           # 只有"养死"才不继承
                heir = s.get("heir") if cause == "bred" else None   # 生育死→由后代接替
                ns = _fresh()
                ns["version"] = heir["version"] if heir else _child_version(s, s)
                ns["coins"] = s.get("coins", 0)           # 金币始终保留
                ns["family"] = s.get("family", [])
                ns["rel"] = s.get("rel", {})
                # 历代生平全部带过来，并给"这一只新的"开一段全新的生平（记录另起一段）
                prev_lives = s.get("lives", [])
                next_no = (prev_lives[-1]["no"] + 1) if prev_lives else 1
                ns["lives"] = (prev_lives + [{
                    "no": next_no, "name": "", "version": ns["version"],
                    "born": _now(), "born_str": _datestr(), "died": 0, "cause": "", "log": [],
                }])[-LIVES_KEEP:]
                if inherit:
                    ns["owned"] = s.get("owned", ns["owned"])   # 继承已解锁的装饰+颜色
                    for k in ("outfit", "hair", "skin"):
                        if s.get(k) in ns["owned"].get(k, []):
                            ns[k] = s[k]
                    if s.get("hat") in ns["owned"].get("hat", []):
                        ns["hat"] = s["hat"]
                    if s.get("clothes") in ns["owned"].get("clothes", ["无"]):
                        ns["clothes"] = s["clothes"]   # 买过的衣服也一并继承
                if heir:
                    pa = heir.get("parents", ["?", "?"])
                    _addlog(ns, f"🌱 由 {pa[0]} 和 {pa[1]} 养育的后代来接替啦（{ns['version']}），继承了上一辈的装扮，快给它起名")
                elif inherit:
                    _addlog(ns, "🐣 新的JZH来了，继承了上一只的装饰和颜色")
                else:
                    _addlog(ns, "🐣 新的JZH来了。上一只疏于照顾没能留住，装饰和颜色都被回收了")
                s = ns
                msg = "🌱 迎接新的JZH！"
 
        elif action.startswith("name:"):   # 给JZH起名
            newname = action.split(":", 1)[1].strip()[:8]
            if newname:
                s["name"] = newname
                _current_life(s)["name"] = newname   # 名字同时记进它自己的生平
                msg = f"就叫「{newname}」啦！"
            else:
                msg = "名字不能是空的"
 
        elif action.startswith("buy_clothes:"):   # 花金币购买/穿上一件衣服款式
            name = action.split(":", 1)[1]
            owned_clo = s.setdefault("owned", {}).setdefault("clothes", ["无"])
            price = CLOTHES_PRICE.get(name)
            if name == "无":
                s["clothes"] = "无"; msg = "脱下啦～"
            elif price is None:
                msg = "没有这件衣服"
            elif name in owned_clo:
                s["clothes"] = name                # 已买过就直接穿上
                msg = f"换上「{name}」～"
            elif s.get("coins", 0) < price:
                msg = f"金币不够（需要 {price}，先去打工赚点）"
            else:
                s["coins"] = s.get("coins", 0) - price
                owned_clo.append(name)
                s["clothes"] = name                # 买了直接穿上
                _addlog(s, f"🛒 花 {price} 金币买了新衣服「{name}」")
                msg = f"🛒 购买成功！花了 {price} 金币，穿上「{name}」"

        # 换装（前端传 dress:类别:值）——只能换已解锁的
        elif action.startswith("dress:"):
            _, key, val = action.split(":", 2)
            o = s.get("owned", {})
            if key == "hat":
                if val in o.get("hat", []):
                    s["hat"] = val
                else:
                    msg = "这个还没解锁"
            elif key == "clothes":
                if val == "无" or val in o.get("clothes", ["无"]):
                    s["clothes"] = val
                else:
                    msg = "这件还没买"
            elif key in ("outfit", "hair", "skin"):
                try:
                    iv = int(val)
                except ValueError:
                    iv = None
                if iv is not None and iv in o.get(key, []):
                    s[key] = iv
                else:
                    msg = "这个还没解锁"
 
        _save(conn, owner, s)
        r = _public(s)
        r["msg"] = msg
        return r
    finally:
        conn.close()