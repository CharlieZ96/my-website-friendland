# highway.py —— JZH高速（公路之王）后端：TOYOBA 车行买车 + 车库(存在 homestead.cars) + 里程排行榜
# 复用 pet 的 users.db / _connect / homestead / 金币（pets.data.coins）。
# 设计：买车扣金币、车塞进 homestead.cars（前端车库直接展示）；开车里程上报到 road_scores，
#       同时记「单次最高」best 和「累计总里程」total。里程由前端上报，服务器按时间+极速做上限夹取防作弊。

import json
import time
import uuid
import pet

def _now():
    return time.time()

def _today():
    return time.strftime("%Y-%m-%d", time.localtime())

def _connect():
    return pet._connect()

# ====== 车辆目录（分档；价格/极速/加速/操控 都在这里，随时可调）======
# top  = 极速(km/h)；accel = 加速快慢(0~1，越大越快到极速)；grip = 操控/转向跟手(1~10)
VEHICLES = [
    {"id": "v1", "tier": 1, "name": "破面包",   "emoji": "🚐", "color": "#9aa4b2",
     "price": 200,   "top": 110, "accel": 0.35, "grip": 4, "desc": "TOYOBA 二手代步，能跑就行。"},
    {"id": "v2", "tier": 2, "name": "家用轿车", "emoji": "🚗", "color": "#4a90e2",
     "price": 700,   "top": 150, "accel": 0.50, "grip": 6, "desc": "买菜接娃两不误，稳当。"},
    {"id": "v3", "tier": 3, "name": "性能钢炮", "emoji": "🚙", "color": "#2ecc71",
     "price": 1800,  "top": 190, "accel": 0.65, "grip": 7, "desc": "小钢炮，弯道有点意思。"},
    {"id": "v4", "tier": 4, "name": "跑车",     "emoji": "🏎️", "color": "#e74c3c",
     "price": 4500,  "top": 240, "accel": 0.80, "grip": 8, "desc": "推背感拉满，压得住。"},
    {"id": "v5", "tier": 5, "name": "超跑",     "emoji": "🚘", "color": "#f5c542",
     "price": 12000, "top": 320, "accel": 1.00, "grip": 10, "desc": "JZH高速的速度之王。"},
]
VMAP = {v["id"]: v for v in VEHICLES}

# 里程上报的防作弊夹取
ROAD_MIN_GAP     = 2          # 两次上报最少间隔秒
ROAD_MAX_ELAPSED = 6 * 3600  # 单次最多按 6 小时算
ROAD_FUDGE       = 1.15       # 里程允许比“极速×时间”多一点点（起步/波动余量）
ROAD_ABS_CAP     = 100000.0   # 单次里程绝对上限(km)，兜底

# 开车沿途金币 → 真金币入账的防作弊上限
ROAD_COIN_PER_KM = 50         # 每公里最多结算的金币（真人实收≈20/km，留 2.5 倍余量）
ROAD_COIN_BASE   = 20         # 起步补偿（跑一小段也有点）
ROAD_COIN_ABS    = 1000       # 单次绝对上限，兜底
ROAD_DAILY_COIN_CAP = 3000    # ★每人每天靠开车能入账的金币上限（挂机脚本刷了也白刷；可调）

# 一局令牌（防脚本直接构造上报）：开局向服务器领令牌，结算必须带回，服务器自己计时、一局只结算一次
ROAD_TOKEN_TTL   = 3 * 3600   # 令牌有效期（秒）
ROAD_START_GAP   = 1.0        # 同一人两次开局的最小间隔（秒）
ROAD_RUN_MIN_S   = 2.0        # 一局至少要真的过 2 秒才算数（挡瞬间上报）
ROAD_MAX_OPEN    = 8          # 单人未结算的令牌上限（挡狂刷开局）


def ensure_highway_tables():
    conn = _connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS road_scores (
            owner TEXT PRIMARY KEY,
            best REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL DEFAULT 0,
            runs INTEGER NOT NULL DEFAULT 0,
            best_car TEXT,
            updated_at REAL NOT NULL DEFAULT 0)""")
        # 一局令牌：开局领、结算销；服务器凭 started_at 自己算耗时
        conn.execute("""CREATE TABLE IF NOT EXISTS road_runs (
            token TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            car_id TEXT NOT NULL,
            started_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_road_runs_owner ON road_runs(owner)")
        # 每人每天靠开车入账的金币累计（用于每日封顶）
        conn.execute("""CREATE TABLE IF NOT EXISTS road_coin_day (
            day TEXT NOT NULL,
            owner TEXT NOT NULL,
            coins INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, owner))""")
        conn.commit()
    finally:
        conn.close()


# ---------- 金币（复用 pets.data.coins；和赌场/家园同一份）----------
def _coins_get(conn, owner):
    row = conn.execute("SELECT data FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return 0
    try:
        return int(json.loads(row[0]).get("coins", 0))
    except Exception:
        return 0


def _coins_spend(conn, owner, amount):
    """扣金币（夹在 >=0）。返回扣后余额；余额不足返回 None（不扣）。"""
    row = conn.execute("SELECT data, version, status FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return None
    try:
        s = json.loads(row[0])
    except Exception:
        return None
    have = int(s.get("coins", 0))
    if have < amount:
        return None
    s["coins"] = have - int(amount)
    conn.execute("INSERT OR REPLACE INTO pets (owner, version, status, data, updated_at) VALUES (?,?,?,?,?)",
                 (owner, s.get("version", "v1"), s.get("status", "home"),
                  json.dumps(s, ensure_ascii=False), _now()))
    return s["coins"]


def _coins_add(conn, owner, amount):
    """给钱包加金币（开车沿途收集）。返回加后余额；没有存档返回 None。"""
    amount = int(amount)
    row = conn.execute("SELECT data, version, status FROM pets WHERE owner=?", (owner,)).fetchone()
    if not row:
        return None
    try:
        s = json.loads(row[0])
    except Exception:
        return None
    if amount <= 0:
        return int(s.get("coins", 0))
    s["coins"] = int(s.get("coins", 0)) + amount
    conn.execute("INSERT OR REPLACE INTO pets (owner, version, status, data, updated_at) VALUES (?,?,?,?,?)",
                 (owner, s.get("version", "v1"), s.get("status", "home"),
                  json.dumps(s, ensure_ascii=False), _now()))
    return s["coins"]


# ---------- 每日开车金币累计（每日封顶用）----------
def _coin_day_get(conn, owner):
    r = conn.execute("SELECT coins FROM road_coin_day WHERE day=? AND owner=?",
                     (_today(), owner)).fetchone()
    return int(r[0]) if r else 0


def _coin_day_add(conn, owner, amount):
    if amount <= 0:
        return
    conn.execute(
        "INSERT INTO road_coin_day (day, owner, coins) VALUES (?,?,?) "
        "ON CONFLICT(day, owner) DO UPDATE SET coins = coins + ?",
        (_today(), owner, int(amount), int(amount)))


# ---------- 车行 / 车库 ----------
def _owned_ids(d):
    out = set()
    for c in d.get("cars", []):
        if isinstance(c, dict) and c.get("id"):
            out.add(c["id"])
    return out


def catalog():
    """给前端展示的车辆目录（不含内部字段变动风险，直接返回副本）。"""
    return [dict(v) for v in VEHICLES]


def toyoba_data(owner):
    """TOYOBA 车行页数据：金币 + 每辆车(带是否已拥有)。"""
    conn = _connect()
    try:
        d = pet.homestead_get(conn, owner)
        owned = _owned_ids(d)
        coins = _coins_get(conn, owner)
    finally:
        conn.close()
    cars = []
    for v in VEHICLES:
        item = dict(v)
        item["owned"] = v["id"] in owned
        cars.append(item)
    return {"coins": coins, "cars": cars}


def buy_car(owner, car_id):
    """买车：扣金币、把车存进 homestead.cars（车库直接展示）。已拥有/钱不够/无此车都会拒绝。"""
    v = VMAP.get((car_id or "").strip())
    if not v:
        return {"ok": False, "msg": "没有这辆车"}
    conn = _connect()
    try:
        d = pet.homestead_get(conn, owner)
        if v["id"] in _owned_ids(d):
            return {"ok": False, "msg": "你已经有这辆「%s」啦" % v["name"], "coins": _coins_get(conn, owner)}
        coins = _coins_get(conn, owner)
        if coins < v["price"]:
            return {"ok": False, "msg": "金币不够（「%s」要 %d，你有 %d）" % (v["name"], v["price"], coins),
                    "coins": coins}
        left = _coins_spend(conn, owner, v["price"])
        if left is None:
            return {"ok": False, "msg": "金币不够", "coins": coins}
        d.setdefault("cars", []).append({
            "id": v["id"], "name": v["name"], "emoji": v["emoji"],
            "tier": v["tier"], "color": v["color"]})
        pet.homestead_save(conn, owner, d)
        conn.commit()
        return {"ok": True, "msg": "🚗 提车成功！「%s」已入库" % v["name"],
                "coins": left, "car_id": v["id"]}
    finally:
        conn.close()


def owned_cars(owner):
    """玩家已拥有的车（带完整数值，供游戏选车用）。以目录 VMAP 为准取最新数值。"""
    conn = _connect()
    try:
        d = pet.homestead_get(conn, owner)
    finally:
        conn.close()
    out = []
    seen = set()
    for c in d.get("cars", []):
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid or cid in seen:
            continue
        seen.add(cid)
        v = VMAP.get(cid)
        if v:
            out.append(dict(v))
    # 按档位排序，方便选车
    out.sort(key=lambda x: x["tier"])
    return out


# ---------- 里程排行榜 ----------
def _score_row(conn, owner):
    r = conn.execute("SELECT best,total,runs,best_car,updated_at FROM road_scores WHERE owner=?",
                     (owner,)).fetchone()
    if not r:
        return {"best": 0.0, "total": 0.0, "runs": 0, "best_car": None, "updated_at": 0.0}
    return {"best": float(r[0] or 0), "total": float(r[1] or 0), "runs": int(r[2] or 0),
            "best_car": r[3], "updated_at": float(r[4] or 0)}


def road_data(owner):
    """游戏页初始化：玩家已有车(带数值) + 自己的最高/累计里程。"""
    conn = _connect()
    try:
        me = _score_row(conn, owner)
    finally:
        conn.close()
    return {"cars": owned_cars(owner),
            "me": {"best": round(me["best"], 2), "total": round(me["total"], 2), "runs": me["runs"]}}


def road_start(owner, car_id):
    """开局：向服务器领一枚“一局令牌”。服务器记下开始时间，结算时必须带回。
    这样脚本没法伪造耗时（服务器自己计时），也没法一份成绩反复上报。"""
    car_id = (car_id or "").strip()
    v = VMAP.get(car_id)
    conn = _connect()
    try:
        d = pet.homestead_get(conn, owner)
        if not v or v["id"] not in _owned_ids(d):
            return {"ok": False, "msg": "你没有这辆车"}
        now = _now()
        # 清理：过期的、以及本人已结算的旧令牌
        conn.execute("DELETE FROM road_runs WHERE used=1 OR started_at < ?", (now - ROAD_TOKEN_TTL,))
        # 开局太频繁 / 未结算令牌太多 → 挡住（防脚本狂刷开局）
        row = conn.execute("SELECT COUNT(*), MAX(started_at) FROM road_runs WHERE owner=? AND used=0",
                           (owner,)).fetchone()
        open_cnt = int(row[0] or 0)
        last_start = float(row[1] or 0)
        if now - last_start < ROAD_START_GAP or open_cnt >= ROAD_MAX_OPEN:
            conn.commit()
            return {"ok": False, "msg": "开得太快啦，缓一下"}
        token = uuid.uuid4().hex
        conn.execute("INSERT INTO road_runs (token, owner, car_id, started_at, used) VALUES (?,?,?,?,0)",
                     (token, owner, v["id"], now))
        conn.commit()
        return {"ok": True, "token": token}
    finally:
        conn.close()


def road_submit(owner, car_id, dist_km, coins=0, token=""):
    """结算：必须带回开局领到的令牌。服务器用「令牌记录的开始时间」自己算耗时，
    再按 极速×耗时×余量 夹取里程、按里程夹取金币、并套每日封顶；令牌一次性作废。"""
    car_id = (car_id or "").strip()
    token = (token or "").strip()
    v = VMAP.get(car_id)
    conn = _connect()
    try:
        d = pet.homestead_get(conn, owner)
        if not v or v["id"] not in _owned_ids(d):
            return {"ok": False, "msg": "你没有这辆车，成绩不算"}

        now = _now()
        # —— 校验并“消费”令牌：必须是本人、未用过、未过期、车型一致 ——
        run = conn.execute(
            "SELECT started_at, used, car_id FROM road_runs WHERE token=? AND owner=?",
            (token, owner)).fetchone()
        if not run:
            return {"ok": False, "msg": "这局没登记（请从游戏里正常开始）"}
        started_at, used, run_car = float(run[0]), int(run[1]), run[2]
        if used:
            return {"ok": False, "msg": "这局已经结算过了"}
        # 原子作废：只有这次 UPDATE 真的改到 1 行，才算抢到结算权（防并发重复上报）
        got_lock = conn.execute("UPDATE road_runs SET used=1 WHERE token=? AND used=0", (token,)).rowcount
        if not got_lock:
            conn.commit()
            return {"ok": False, "msg": "这局已经结算过了"}
        if run_car != v["id"]:
            v = VMAP.get(run_car) or v          # 以开局登记的车为准
        # ★服务器自己算耗时（不信客户端）
        elapsed = now - started_at
        if elapsed < ROAD_RUN_MIN_S:
            conn.commit()
            return {"ok": False, "msg": "这局太短，不计分"}
        elapsed = min(elapsed, ROAD_MAX_ELAPSED)

        try:
            dist = float(dist_km)
        except (TypeError, ValueError):
            dist = 0.0
        if dist < 0:
            dist = 0.0
        # 里程上限：极速(km/h) × 真实耗时(h) × 余量
        cap = min(v["top"] * (elapsed / 3600.0) * ROAD_FUDGE, ROAD_ABS_CAP)
        accepted = round(max(0.0, min(dist, cap)), 2)

        # 沿途金币：按里程夹取；再套「每日封顶」
        try:
            got = int(float(coins))
        except (TypeError, ValueError):
            got = 0
        coin_cap = min(int(accepted * ROAD_COIN_PER_KM) + ROAD_COIN_BASE, ROAD_COIN_ABS)
        day_used = _coin_day_get(conn, owner)
        day_left = max(0, ROAD_DAILY_COIN_CAP - day_used)
        coins_earned = max(0, min(got, coin_cap, day_left))
        daily_capped = (min(got, coin_cap) > coins_earned)   # 因为到达每日上限被削

        wallet = _coins_add(conn, owner, coins_earned)
        if wallet is None:
            wallet = _coins_get(conn, owner)
            coins_earned = 0
        if coins_earned > 0:
            _coin_day_add(conn, owner, coins_earned)

        prev = _score_row(conn, owner)
        new_total = prev["total"] + accepted
        new_best = max(prev["best"], accepted)
        new_runs = prev["runs"] + 1
        best_car = v["id"] if accepted >= prev["best"] else prev["best_car"]
        conn.execute("""INSERT INTO road_scores (owner,best,total,runs,best_car,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(owner) DO UPDATE SET best=?,total=?,runs=?,best_car=?,updated_at=?""",
            (owner, new_best, new_total, new_runs, best_car, now,
             new_best, new_total, new_runs, best_car, now))
        conn.commit()
        return {"ok": True, "accepted": accepted, "capped": accepted < round(dist, 2),
                "best": round(new_best, 2), "total": round(new_total, 2), "runs": new_runs,
                "is_best": accepted >= prev["best"] and accepted > 0,
                "coins_earned": coins_earned,
                "coins_capped": coins_earned < got, "daily_capped": daily_capped,
                "day_left": max(0, ROAD_DAILY_COIN_CAP - _coin_day_get(conn, owner)),
                "wallet": wallet}
    finally:
        conn.close()


def _car_label(cid):
    v = VMAP.get(cid or "")
    return (v["emoji"] + v["name"]) if v else ""


def road_leaderboard(limit=20, owner=None):
    """双榜：单次最高里程榜 + 累计总里程榜。"""
    limit = max(1, min(50, int(limit)))
    conn = _connect()
    try:
        best_rows = conn.execute(
            "SELECT owner,best,best_car FROM road_scores WHERE best>0 ORDER BY best DESC, updated_at ASC LIMIT ?",
            (limit,)).fetchall()
        total_rows = conn.execute(
            "SELECT owner,total,runs FROM road_scores WHERE total>0 ORDER BY total DESC, updated_at ASC LIMIT ?",
            (limit,)).fetchall()
        me = _score_row(conn, owner) if owner else None
    finally:
        conn.close()
    best = [{"owner": o, "km": round(float(b or 0), 2), "car": _car_label(c)} for o, b, c in best_rows]
    total = [{"owner": o, "km": round(float(t or 0), 2), "runs": int(r or 0)} for o, t, r in total_rows]
    out = {"best": best, "total": total}
    if owner:
        out["me"] = {"best": round(me["best"], 2), "total": round(me["total"], 2), "runs": me["runs"]}
    return out
