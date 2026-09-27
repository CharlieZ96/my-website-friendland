# -*- coding: utf-8 -*-
"""
江世战争 —— 江之岛洞窟里的实时对战推塔（皇室战争式）。

架构：服务器权威模拟。
  · 所有对局都在服务器上按 10 次/秒 推进（移动、索敌、攻击、塔、瓜拉娜）；
  · 客户端只负责「出牌」和「画画」，通过 websocket 命名空间 /jiangshi 收发；
  · 匹配：真人排队，等 QUEUE_WAIT 秒没人就派 AI（洞窟守卫）陪打；
  · 奖励：打完有效对局发金币（复用 casino 的金币账户），真人胜场进排行榜。

接入 app.py 只需要：
    import jiangshi
    jiangshi.init(socketio)          # 建表 + 注册 websocket 事件
  以及 4 个 HTTP 路由（页面 / 大厅数据 / 保存卡组 / 解锁卡牌），见交付说明。
"""
import json
import math
import random
import threading
import time
import traceback
from datetime import datetime

from flask import request, session

import casino
import pet

# ======================================================================
# 可调参数（想改平衡/奖励，改这里就行）
# ======================================================================
NS = "/jiangshi"            # websocket 命名空间（和 /wstest 那套互不干扰）
TICK = 0.1                  # 模拟步长：每 0.1 秒推进一次
QUEUE_WAIT = 30             # 排队多少秒没匹配到真人，就派 AI

MATCH_TIME = 180            # 常规时间 3:00
DOUBLE_AT = 120             # 打到 2:00 后瓜拉娜双倍
OVERTIME = 120              # 加时最多 2:00（先拆一座塔的赢）
ELIXIR_START = 5.0
ELIXIR_MAX = 10.0
ELIXIR_EVERY = 2.8          # 每 2.8 秒 +1 瓜拉娜（双倍/三倍按倍率加速）
DEPLOY_TIME = 1.0           # 兵落地后 1 秒才开始行动
SPELL_TOWER_RATE = 0.35     # 法术打塔只吃 35% 伤害

# —— 奖励 ——
REWARD_PVP_WIN = 200        # 真人对战赢
REWARD_PVP_LOSE = 5         # 真人对战输（参与奖）
REWARD_PVP_DRAW = 5
REWARD_AI_WIN = 8           # 打 AI 赢
PVP_DAILY_WINS = 5          # 每天最多几局真人胜利能领奖
PVP_SAME_OPP_DAILY = 3      # 对同一个对手每天最多几局胜利能领奖/进榜
AI_DAILY_COINS = 40         # 打 AI 每天最多拿多少金币
VALID_MIN_SECONDS = 60      # 至少打满这么久才算有效对局
VALID_MIN_PLAYS = 3         # 双方（真人）都至少出过几张牌才算有效

# —— 卡牌开放 / 解锁价格 ——
OPEN_CARDS = ["chicks", "archer", "gamblers", "arrows", "knight", "watchtower",
              "zap", "musket", "fireball", "valk", "charger", "giant",
              "mage", "dragon", "egret", "assassin"]
UNLOCK_PRICE = {"tesla": 150, "trio": 300, "mole": 300,
                "hammer": 500, "golem": 500, "lord": 3000}
DEFAULT_DECK = ["knight", "archer", "musket", "giant", "fireball", "arrows", "valk", "gamblers"]
DECK_SIZE = 8

AI_NAME = "洞窟守卫"
AI_DECKS = [
    ["giant", "musket", "archer", "knight", "fireball", "arrows", "valk", "chicks"],
    ["charger", "valk", "musket", "zap", "gamblers", "mage", "knight", "watchtower"],
    ["giant", "dragon", "egret", "archer", "fireball", "zap", "gamblers", "knight"],
    ["charger", "assassin", "mage", "chicks", "arrows", "knight", "musket", "watchtower"],
]

# ======================================================================
# 场地（单位：格；x 向右 0~18，y 向下 0~32；side 0 在下半场，side 1 在上半场）
# ======================================================================
W, H = 18.0, 32.0
RIVER_TOP, RIVER_BOT = 15.0, 17.0     # 河道 y∈(15,17)
BRIDGES = (3.5, 14.5)                  # 左右两座桥的中线 x
BRIDGE_HALF = 1.0                      # 桥宽的一半
SIGHT = 5.5                            # 兵的视野：这个距离内有敌人就去打

SPEED = {"慢": 0.75, "中": 1.0, "快": 1.5, "极快": 2.0}
MELEE = 0.7

# ======================================================================
# 22 张卡（数值照《江世战争设计草案》）
#   type: troop / building / spell
#   tgt : ground=只打地面  both=对空对地  buildings=只打建筑  none=不攻击
# ======================================================================
CARDS = {
    "zap":       dict(name="麻电符", cost=1, rarity="稀有", type="spell", emoji="⚡",
                      dmg=90, radius=1.5, stun=0.5,
                      desc="小范围电一下，眩晕 0.5 秒，打断攻击、清残血小兵。"),
    "chicks":    dict(name="鸡群", cost=1, rarity="普通", type="troop", emoji="🐔",
                      count=4, hp=60, dmg=45, hs=0.7, rng=MELEE, spd="极快", tgt="ground", r=0.3,
                      desc="一次出 4 只小鸡，铺场、分推、挡刀。"),
    "archer":    dict(name="阿弓", cost=2, rarity="普通", type="troop", emoji="🏹",
                      count=1, hp=120, dmg=55, hs=0.9, rng=5.0, spd="中", tgt="both", r=0.4, shot="arrow",
                      desc="稳定的远程输出，能打飞行单位。"),
    "gamblers":  dict(name="赌徒三人组", cost=2, rarity="普通", type="troop", emoji="🎲",
                      count=3, hp=90, dmg=70, hs=1.1, rng=MELEE, spd="快", tgt="ground", r=0.38,
                      desc="3 个赌徒一拥而上，包夹单体、克大坦克。"),
    "arrows":    dict(name="箭雨", cost=2, rarity="普通", type="spell", emoji="🌧️",
                      dmg=120, radius=2.5,
                      desc="大范围箭雨，清一片小兵，顺带啃塔。"),
    "knight":    dict(name="小卒", cost=3, rarity="普通", type="troop", emoji="🛡️",
                      count=1, hp=650, dmg=90, hs=1.1, rng=MELEE, spd="中", tgt="ground", r=0.45,
                      desc="便宜耐打的肉盾，挡先锋、护输出。"),
    "musket":    dict(name="桑妹", cost=3, rarity="稀有", type="troop", emoji="🔫",
                      count=1, hp=220, dmg=130, hs=1.0, rng=6.0, spd="中", tgt="both", r=0.42, shot="bullet",
                      desc="高单体伤害的神枪手，但很脆，需要保护。"),
    "mole":      dict(name="地鼠", cost=3, rarity="史诗", type="troop", emoji="🐹",
                      count=1, hp=300, dmg=110, hs=1.2, rng=MELEE, spd="快", tgt="buildings", r=0.4,
                      anywhere=True,
                      desc="能从地底钻到场上任意位置，专挑建筑和塔。"),
    "watchtower":dict(name="望楼", cost=3, rarity="普通", type="building", emoji="🗼",
                      count=1, hp=500, dmg=80, hs=0.8, rng=6.0, tgt="both", r=0.7, life=30, shot="arrow",
                      desc="防守箭楼，能打空中，存在 30 秒，还能吸引兵线。"),
    "fireball":  dict(name="火喵球", cost=4, rarity="稀有", type="spell", emoji="🔥",
                      dmg=340, radius=2.0,
                      desc="中范围高伤火球，清一团中血量单位、收残塔。"),
    "valk":      dict(name="阿豹", cost=4, rarity="稀有", type="troop", emoji="🐆",
                      count=1, hp=750, dmg=150, hs=1.5, rng=MELEE, spd="中", tgt="ground", r=0.48,
                      spin=1.6,
                      desc="近战 360° 旋风斩，身边一圈都挨打，专克地面群。"),
    "tesla":     dict(name="特斯拉望楼", cost=4, rarity="普通", type="building", emoji="🔌",
                      count=1, hp=650, dmg=90, hs=0.9, rng=6.0, tgt="both", r=0.7, life=35, shot="zap",
                      hides=True,
                      desc="没有敌人时缩进地里，法术打不到它。存在 35 秒。"),
    "charger":   dict(name="阿飙", cost=4, rarity="稀有", type="troop", emoji="🏎️",
                      count=1, hp=900, dmg=240, hs=1.6, rng=MELEE, spd="极快", tgt="buildings", r=0.5,
                      first_x2=True,
                      desc="冲锋车直扑建筑，撞上的第一下伤害翻倍。"),
    "mage":      dict(name="紫萝", cost=5, rarity="史诗", type="troop", emoji="🔮",
                      count=1, hp=340, dmg=130, hs=1.4, rng=5.0, spd="中", tgt="both", r=0.42,
                      splash=1.0, shot="magic",
                      desc="远程法术小范围溅射，团控输出核心。"),
    "giant":     dict(name="铁墩", cost=5, rarity="稀有", type="troop", emoji="🗿",
                      count=1, hp=2400, dmg=130, hs=1.5, rng=MELEE, spd="慢", tgt="buildings", r=0.75,
                      desc="超级肉盾，只拆建筑，扛在前面开路。"),
    "assassin":  dict(name="影一", cost=5, rarity="传奇", type="troop", emoji="🥷",
                      count=1, hp=500, dmg=200, hs=1.1, rng=MELEE, spd="快", tgt="ground", r=0.42,
                      dash=True, first_x2=True,
                      desc="落地后突进到附近最远的敌人身边，第一刀双倍伤害。"),
    "dragon":    dict(name="焰姬", cost=6, rarity="史诗", type="troop", emoji="🐉",
                      count=1, hp=900, dmg=160, hs=1.3, rng=4.5, spd="快", tgt="both", r=0.55,
                      fly=True, splash=0.8, shot="fire",
                      desc="飞行单位，喷火小范围溅射；只打地面的兵摸不到她。"),
    "egret":     dict(name="白鹭", cost=6, rarity="史诗", type="troop", emoji="🕊️",
                      count=1, hp=700, dmg=0, hs=1.0, rng=0, spd="中", tgt="none", r=0.45,
                      heal=45, aura=4.0,
                      desc="不攻击，跟着队友走，持续治疗周围 4 格内的友军。"),
    "hammer":    dict(name="苏铁", cost=7, rarity="史诗", type="troop", emoji="🔨",
                      count=1, hp=3200, dmg=520, hs=2.4, rng=MELEE, spd="慢", tgt="both", r=0.75,
                      death_dmg=350, death_r=2.0,
                      desc="超肉超痛的重锤，倒下时砸地，对周围造成伤害。"),
    "trio":      dict(name="极速三射手", cost=7, rarity="稀有", type="troop", emoji="🎯",
                      count=3, hp=220, dmg=130, hs=1.0, rng=6.0, spd="中", tgt="both", r=0.42, shot="bullet",
                      desc="一次 3 个神枪手，火力全开，但怕法术。"),
    "golem":     dict(name="山王", cost=8, rarity="史诗", type="troop", emoji="⛰️",
                      count=1, hp=4000, dmg=300, hs=1.7, rng=MELEE, spd="慢", tgt="buildings", r=0.85,
                      death_dmg=200, death_r=2.0, death_spawn=2,
                      desc="巨型推塔机，倒下后分裂成 2 个小石人继续冲。"),
    "lord":      dict(name="江主", cost=9, rarity="传奇", type="troop", emoji="👑",
                      count=1, hp=3000, dmg=240, hs=1.5, rng=MELEE, spd="中", tgt="ground", r=0.7,
                      splash=1.2, hero=True,
                      desc="英雄。大招『岛之怒』：前方扇形重击并无敌 2 秒（自动释放，12 秒一次）。全场只能有一个。"),
}
GOLEMITE = dict(name="小石人", emoji="🪨", hp=900, dmg=60, hs=1.5, rng=MELEE, spd="慢",
                tgt="buildings", r=0.5, death_dmg=100, death_r=1.5)
ALL_KEYS = list(CARDS.keys())

# 塔（side 0 的位置；side 1 上下镜像）
PRINCESS_HP, PRINCESS_DMG, PRINCESS_HS, PRINCESS_RNG, PRINCESS_R = 2400, 90, 0.8, 7.5, 1.5
KING_HP, KING_DMG, KING_HS, KING_RNG, KING_R = 4000, 90, 1.0, 7.0, 2.0
TOWER_POS = {"L": (3.5, 25.5), "R": (14.5, 25.5), "K": (9.0, 29.0)}


def _mirror(x, y, side):
    return (x, y) if side == 0 else (x, H - y)


# ======================================================================
# 单位
# ======================================================================
_uid = 0


def _next_id():
    global _uid
    _uid += 1
    return _uid


class U:
    __slots__ = ("id", "key", "side", "kind", "x", "y", "r", "hp", "maxhp", "dmg", "hs", "rng",
                 "spd", "tgt", "fly", "cd", "target", "retarget", "deploy", "stun", "life",
                 "splash", "spin", "first_x2", "hero", "ability_cd", "shield", "heal", "aura",
                 "dash", "dash_t", "hides", "hidden", "active", "fx", "fy", "shot", "slot",
                 "death_dmg", "death_r", "death_spawn", "dead")

    def __init__(self, key, side, x, y, spec, kind="troop"):
        self.id = _next_id()
        self.key, self.side, self.kind = key, side, kind
        self.x, self.y = float(x), float(y)
        self.r = spec.get("r", 0.45)
        self.hp = self.maxhp = float(spec.get("hp", 100))
        self.dmg = float(spec.get("dmg", 0))
        self.hs = float(spec.get("hs", 1.0))
        self.rng = float(spec.get("rng", MELEE))
        self.spd = SPEED.get(spec.get("spd"), 0.0) if kind == "troop" else 0.0
        self.tgt = spec.get("tgt", "ground")
        self.fly = bool(spec.get("fly"))
        self.cd = 0.0
        self.target = None
        self.retarget = 0.0
        self.deploy = DEPLOY_TIME if kind in ("troop", "building") else 0.0
        self.stun = 0.0
        self.life = spec.get("life")
        self.splash = spec.get("splash", 0.0)
        self.spin = spec.get("spin", 0.0)
        self.first_x2 = bool(spec.get("first_x2"))
        self.hero = bool(spec.get("hero"))
        self.ability_cd = 3.0
        self.shield = 0.0
        self.heal = spec.get("heal", 0)
        self.aura = spec.get("aura", 0)
        self.dash = bool(spec.get("dash"))
        self.dash_t = 0.0
        self.hides = bool(spec.get("hides"))
        self.hidden = False
        self.active = True               # 国王塔一开始是 False（被打或公主塔倒了才醒）
        self.fx, self.fy = 0.0, (-1.0 if side == 0 else 1.0)   # 朝向（江主大招用）
        self.shot = spec.get("shot", "")
        self.slot = ""                   # 塔的位置 L/R/K
        self.death_dmg = spec.get("death_dmg", 0)
        self.death_r = spec.get("death_r", 0)
        self.death_spawn = spec.get("death_spawn", 0)
        self.dead = False

    @property
    def is_building(self):
        return self.kind in ("tower", "building")

    @property
    def targetable(self):
        return (not self.dead) and self.deploy <= 0 and self.hp > 0


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def _edge(a, b):
    """两个单位边缘之间的距离。"""
    return _dist(a, b) - a.r - b.r


def _region(y):
    if y >= RIVER_BOT:
        return 0          # 下半场
    if y <= RIVER_TOP:
        return 1          # 上半场
    return -1             # 河里


def _near_bridge(x):
    return min(BRIDGES, key=lambda b: abs(b - x))


# ======================================================================
# 一局对战
# ======================================================================
class Player:
    def __init__(self, name, deck, ai=False, sid=None):
        self.name = name
        self.ai = ai
        self.sid = sid
        self.deck = list(deck)
        order = list(deck)
        random.shuffle(order)
        self.hand = order[:4]
        self.queue = order[4:]
        self.elixir = ELIXIR_START
        self.crowns = 0
        self.played = 0
        self.brain = AIBrain() if ai else None


class Match:
    def __init__(self, p0, p1, pvp):
        self.id = _next_id()
        self.players = [p0, p1]
        self.pvp = pvp
        self.t = 0.0
        self.ot = False
        self.over = False
        self.settled = False       # 结算（发奖励、推送结果）只做一次
        self.ended_at = None
        self.result = None
        self.units = {}
        self.events = []
        self.spawn_q = []
        for side in (0, 1):
            for slot, (x, y) in TOWER_POS.items():
                x, y = _mirror(x, y, side)
                if slot == "K":
                    spec = dict(hp=KING_HP, dmg=KING_DMG, hs=KING_HS, rng=KING_RNG, r=KING_R, tgt="both")
                    u = U("king", side, x, y, spec, kind="tower")
                    u.active = False
                else:
                    spec = dict(hp=PRINCESS_HP, dmg=PRINCESS_DMG, hs=PRINCESS_HS, rng=PRINCESS_RNG,
                                r=PRINCESS_R, tgt="both")
                    u = U("princess", side, x, y, spec, kind="tower")
                u.slot = slot
                u.shot = "tower"
                u.deploy = 0
                self.units[u.id] = u

    # ---------------- 查询 ----------------
    def tower(self, side, slot):
        for u in self.units.values():
            if u.kind == "tower" and u.side == side and u.slot == slot and not u.dead:
                return u
        return None

    def mult(self):
        if self.ot:
            return 3.0
        return 2.0 if self.t >= DOUBLE_AT else 1.0

    def time_left(self):
        if self.ot:
            return max(0, MATCH_TIME + OVERTIME - self.t)
        return max(0, MATCH_TIME - self.t)

    # ---------------- 出牌 ----------------
    def valid_spot(self, side, key, x, y):
        spec = CARDS[key]
        if not (0.4 <= x <= W - 0.4 and 0.4 <= y <= H - 0.4):
            return False
        if spec["type"] == "spell":
            return True
        if RIVER_TOP < y < RIVER_BOT:
            return False
        for u in self.units.values():             # 不能压在活着的塔上
            if u.kind == "tower" and not u.dead and math.hypot(u.x - x, u.y - y) < u.r + 0.3:
                return False
        if spec.get("anywhere"):
            return True
        own = (y >= RIVER_BOT) if side == 0 else (y <= RIVER_TOP)
        if own:
            return True
        # 口袋区：拆掉对方某一路的公主塔后，可以下到那一路的对方半场
        enemy = 1 - side
        lane = "L" if x < W / 2 else "R"
        if self.tower(enemy, lane) is None:
            return (y >= 9.0) if side == 0 else (y <= H - 9.0)
        return False

    def play(self, side, idx, x, y):
        """出牌：成功返回 None，失败返回原因。"""
        if self.over:
            return "对局已结束"
        p = self.players[side]
        if not (0 <= idx < len(p.hand)):
            return "没有这张牌"
        key = p.hand[idx]
        spec = CARDS[key]
        if p.elixir + 1e-6 < spec["cost"]:
            return "瓜拉娜不够"
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            return "位置不对"
        if not self.valid_spot(side, key, x, y):
            return "这里不能下"
        if spec.get("hero"):
            for u in self.units.values():
                if u.side == side and u.key == key and not u.dead:
                    return "场上已经有江主了"
        p.elixir -= spec["cost"]
        p.played += 1
        p.hand[idx] = p.queue.pop(0)
        p.queue.append(key)
        if spec["type"] == "spell":
            self._cast(side, key, x, y)
        else:
            self._spawn_card(side, key, x, y)
        self.events.append({"k": "play", "s": side, "c": key, "x": round(x, 2), "y": round(y, 2)})
        return None

    def _spawn_card(self, side, key, x, y):
        spec = CARDS[key]
        n = spec.get("count", 1)
        offs = {1: [(0, 0)], 2: [(-0.45, 0), (0.45, 0)],
                3: [(0, -0.5), (-0.5, 0.35), (0.5, 0.35)],
                4: [(-0.35, -0.35), (0.35, -0.35), (-0.35, 0.35), (0.35, 0.35)]}[n]
        for dx, dy in offs:
            px = min(W - 0.4, max(0.4, x + dx))
            py = min(H - 0.4, max(0.4, y + dy))
            u = U(key, side, px, py, spec, kind="building" if spec["type"] == "building" else "troop")
            self.units[u.id] = u

    def _cast(self, side, key, x, y):
        spec = CARDS[key]
        rad = spec["radius"]
        self.events.append({"k": "spell", "c": key, "x": round(x, 2), "y": round(y, 2), "r": rad})
        for u in list(self.units.values()):
            if u.side == side or u.dead or u.deploy > 0:
                continue
            if u.hidden:
                continue
            if math.hypot(u.x - x, u.y - y) - u.r > rad:
                continue
            dmg = spec["dmg"] * (SPELL_TOWER_RATE if u.kind == "tower" else 1.0)
            self._hurt(u, dmg)
            if spec.get("stun") and u.kind != "tower":
                u.stun = max(u.stun, spec["stun"])
                u.cd = u.hs
            if key == "fireball" and u.kind == "troop" and not u.fly and u.maxhp < 1500:
                # 火球小小击退
                d = math.hypot(u.x - x, u.y - y) or 1.0
                u.x += (u.x - x) / d * 0.5
                u.y += (u.y - y) / d * 0.5

    # ---------------- 伤害 ----------------
    def _hurt(self, u, dmg):
        if u.dead or dmg <= 0:
            return
        if u.shield > 0:
            return
        u.hp -= dmg
        if u.kind == "tower" and u.slot == "K" and not u.active:
            u.active = True
            self.events.append({"k": "king", "s": u.side, "x": round(u.x, 2), "y": round(u.y, 2)})

    def _splash(self, src, cx, cy, rad, dmg, include_air=True):
        for v in list(self.units.values()):
            if v.side == src.side or not v.targetable:
                continue
            if v.fly and not include_air:
                continue
            if math.hypot(v.x - cx, v.y - cy) - v.r <= rad:
                self._hurt(v, dmg)

    # ---------------- 索敌 ----------------
    def _can_hit(self, u, v):
        if v.side == u.side or not v.targetable:
            return False
        if u.tgt == "buildings":
            return v.is_building
        if u.tgt == "ground" and v.fly:
            return False
        return True

    def _find_target(self, u):
        best, bestd = None, 1e9
        if u.kind in ("tower", "building"):
            for v in self.units.values():          # 塔和防御建筑只打兵
                if v.kind != "troop" or not self._can_hit(u, v):
                    continue
                d = _edge(u, v)
                if d <= u.rng and d < bestd:
                    best, bestd = v, d
            return best
        sight = max(u.rng, SIGHT)
        for v in self.units.values():         # 视野内最近的
            if not self._can_hit(u, v):
                continue
            d = _edge(u, v)
            if d <= sight and d < bestd:
                best, bestd = v, d
        if best:
            return best
        for v in self.units.values():         # 否则走向最近的建筑/塔
            if not v.is_building or not self._can_hit(u, v):
                continue
            d = _edge(u, v)
            if d < bestd:
                best, bestd = v, d
        return best

    # ---------------- 移动 ----------------
    def _move_to(self, u, tx, ty, dt):
        if not u.fly:
            ru, rt = _region(u.y), _region(ty)
            if ru != rt or ru == -1:
                b = min(BRIDGES, key=lambda bx: abs(u.x - bx) + abs(tx - bx))
                going_up = ty < u.y
                if ru == -1:
                    tx, ty = b, (RIVER_TOP - 0.5 if going_up else RIVER_BOT + 0.5)
                elif abs(u.x - b) > 0.4:
                    tx, ty = b, (RIVER_BOT + 0.6 if ru == 0 else RIVER_TOP - 0.6)
                else:
                    tx, ty = b, (RIVER_TOP - 0.6 if ru == 0 else RIVER_BOT + 0.6)
        dx, dy = tx - u.x, ty - u.y
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return
        spd = u.spd * (4.0 if u.dash_t > 0 else 1.0)
        step = min(d, spd * dt)
        nx, ny = u.x + dx / d * step, u.y + dy / d * step
        if not u.fly and RIVER_TOP < ny < RIVER_BOT and abs(nx - _near_bridge(nx)) > BRIDGE_HALF:
            b = _near_bridge(u.x)                        # 别掉进河里：沿岸往桥头挪
            nx = u.x + math.copysign(min(step, abs(b - u.x)), b - u.x)
            ny = u.y
        u.x, u.y = nx, ny
        u.fx, u.fy = dx / d, dy / d

    # ---------------- 攻击 ----------------
    def _attack(self, u, v):
        dmg = u.dmg
        if u.first_x2:
            dmg *= 2
            u.first_x2 = False
        if u.shot:
            self.events.append({"k": "shot", "t": u.shot, "a": [round(u.x, 2), round(u.y, 2)],
                                "b": [round(v.x, 2), round(v.y, 2)]})
        else:
            self.events.append({"k": "hit", "x": round(v.x, 2), "y": round(v.y, 2)})
        if u.spin:
            self._splash(u, u.x, u.y, u.spin, dmg, include_air=False)
        elif u.splash:
            self._splash(u, v.x, v.y, u.splash, dmg, include_air=(u.tgt == "both"))
        else:
            self._hurt(v, dmg)

    def _lord_ability(self, u):
        """江主大招：前方扇形 3 格重击 + 2 秒无敌。"""
        u.ability_cd = 12.0
        u.shield = 2.0
        self.events.append({"k": "rage", "x": round(u.x, 2), "y": round(u.y, 2),
                            "fx": round(u.fx, 2), "fy": round(u.fy, 2)})
        for v in list(self.units.values()):
            if v.side == u.side or not v.targetable:
                continue
            dx, dy = v.x - u.x, v.y - u.y
            d = math.hypot(dx, dy)
            if d - v.r > 3.0:
                continue
            if d > 0.1 and (dx * u.fx + dy * u.fy) / d < math.cos(math.radians(55)):
                continue
            self._hurt(v, 400 * (SPELL_TOWER_RATE if v.kind == "tower" else 1.0))

    # ---------------- 推进一步 ----------------
    def step(self, dt):
        if self.over:
            return
        self.t += dt
        m = self.mult()
        for p in self.players:
            p.elixir = min(ELIXIR_MAX, p.elixir + dt * m / ELIXIR_EVERY)
            if p.brain:
                p.brain.think(self, self.players.index(p), dt)

        units = list(self.units.values())
        for u in units:
            if u.dead or u.hp <= 0:
                continue
            if u.deploy > 0:                         # 刚落地，还在准备
                u.deploy -= dt
                if u.deploy <= 0 and u.dash:
                    self._start_dash(u)
                continue
            if u.stun > 0:                           # 被电晕
                u.stun -= dt
                continue
            if u.shield > 0:
                u.shield -= dt
            if u.hero:
                u.ability_cd -= dt
            if u.life:                               # 建筑随时间掉血
                u.hp -= u.maxhp / u.life * dt
                if u.hp <= 0:
                    continue
            if u.heal:                               # 治疗兵：只治疗、跟队
                self._do_heal(u, dt)
                self._follow(u, dt)
                continue
            if u.kind == "tower" and not u.active:   # 国王塔还没醒
                continue
            if u.dash_t > 0:
                u.dash_t -= dt
            u.cd = max(0.0, u.cd - dt)
            u.retarget -= dt

            # 索敌：打得着的目标就一直锁着打；打不着了（或到了重新索敌的时间）再换
            tgt = u.target
            in_range = (tgt is not None and tgt.targetable and self._can_hit(u, tgt)
                        and _edge(u, tgt) <= u.rng + 0.05)
            if not in_range and (tgt is None or not tgt.targetable or u.retarget <= 0
                                 or u.kind != "troop"):
                tgt = u.target = self._find_target(u)
                u.retarget = 0.5
                in_range = tgt is not None and _edge(u, tgt) <= u.rng + 0.05
            if u.hides:
                u.hidden = tgt is None
            if tgt is None:
                continue
            if in_range:
                dx, dy = tgt.x - u.x, tgt.y - u.y
                d = math.hypot(dx, dy) or 1.0
                u.fx, u.fy = dx / d, dy / d
                if u.hero and u.ability_cd <= 0:
                    self._lord_ability(u)
                if u.cd <= 0:
                    self._attack(u, tgt)
                    u.cd = u.hs
            elif u.kind == "troop":
                self._move_to(u, tgt.x, tgt.y, dt)

        self._separate()
        self._reap()
        self._check_end()

    def _start_dash(self, u):
        """影一落地：找 7 格内最远的敌方地面兵，突进过去。"""
        far, fd = None, -1
        for v in self.units.values():
            if v.side == u.side or not v.targetable or v.is_building or v.fly:
                continue
            d = _dist(u, v)
            if d <= 7.0 and d > fd:
                far, fd = v, d
        if far:
            u.target = far
            u.retarget = 1.5
            u.dash_t = 1.2
            self.events.append({"k": "dash", "x": round(u.x, 2), "y": round(u.y, 2)})

    def _do_heal(self, u, dt):
        for v in self.units.values():
            if v.side != u.side or v.dead or v is u or v.kind != "troop" or v.deploy > 0:
                continue
            if _dist(u, v) <= u.aura:
                v.hp = min(v.maxhp, v.hp + u.heal * dt)

    def _follow(self, u, dt):
        """治疗兵：跟着最近的友军走，没人就朝敌塔走但停在 6 格外。"""
        best, bd = None, 1e9
        for v in self.units.values():
            if v.side != u.side or v.dead or v is u or v.kind != "troop" or v.heal:
                continue
            d = _dist(u, v)
            if d < bd:
                best, bd = v, d
        if best and bd < 12:
            if bd > 1.6:
                self._move_to(u, best.x, best.y, dt)
            return
        tgt = None
        for v in self.units.values():
            if v.side != u.side and v.kind == "tower" and not v.dead:
                if tgt is None or _dist(u, v) < _dist(u, tgt):
                    tgt = v
        if tgt and _edge(u, tgt) > 6.0:
            self._move_to(u, tgt.x, tgt.y, dt)

    def _separate(self):
        movers = [u for u in self.units.values() if not u.dead and u.kind == "troop"]
        statics = [u for u in self.units.values() if not u.dead and u.kind != "troop"]
        n = len(movers)
        for i in range(n):
            a = movers[i]
            for j in range(i + 1, n):
                b = movers[j]
                if a.fly != b.fly:
                    continue
                dx, dy = b.x - a.x, b.y - a.y
                d = math.hypot(dx, dy)
                need = a.r + b.r
                if d < need:
                    if d < 1e-4:
                        dx, dy, d = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
                        d = math.hypot(dx, dy)
                    push = (need - d) / d * 0.5
                    wa = b.maxhp / (a.maxhp + b.maxhp)   # 轻的被推得多
                    a.x -= dx * push * 2 * wa
                    a.y -= dy * push * 2 * wa
                    b.x += dx * push * 2 * (1 - wa)
                    b.y += dy * push * 2 * (1 - wa)
            if a.fly:
                continue
            for s in statics:
                dx, dy = a.x - s.x, a.y - s.y
                d = math.hypot(dx, dy)
                need = a.r + s.r
                if d < need and d > 1e-4:
                    a.x = s.x + dx / d * need
                    a.y = s.y + dy / d * need
        for u in movers:
            u.x = min(W - 0.3, max(0.3, u.x))
            u.y = min(H - 0.3, max(0.3, u.y))
            # 地面兵被挤进河里（不在桥上）→ 推回最近的岸边
            if not u.fly and RIVER_TOP < u.y < RIVER_BOT and abs(u.x - _near_bridge(u.x)) > BRIDGE_HALF:
                u.y = RIVER_TOP if u.y < (RIVER_TOP + RIVER_BOT) / 2 else RIVER_BOT

    def _reap(self):
        for u in list(self.units.values()):
            if u.dead or u.hp > 0:
                continue
            u.dead = True
            self.events.append({"k": "die", "x": round(u.x, 2), "y": round(u.y, 2),
                                "big": u.kind != "troop" or u.maxhp >= 2000})
            if u.death_dmg:
                self.events.append({"k": "boom", "x": round(u.x, 2), "y": round(u.y, 2), "r": u.death_r})
                self._splash(u, u.x, u.y, u.death_r, u.death_dmg)
            if u.death_spawn:
                for k in range(u.death_spawn):
                    g = U("golemite", u.side, u.x + (k - 0.5) * 0.9, u.y, GOLEMITE)
                    g.deploy = 0
                    self.units[g.id] = g
            if u.kind == "tower":
                other = 1 - u.side
                if u.slot == "K":
                    self.players[other].crowns = 3
                else:
                    self.players[other].crowns += 1
                    king = self.tower(u.side, "K")
                    if king:
                        king.active = True
                self.events.append({"k": "tower", "s": u.side, "slot": u.slot,
                                    "x": round(u.x, 2), "y": round(u.y, 2)})
        for uid in [k for k, u in self.units.items() if u.dead]:
            del self.units[uid]

    def _check_end(self):
        c0, c1 = self.players[0].crowns, self.players[1].crowns
        if c0 >= 3 or c1 >= 3:
            return self._finish(0 if c0 >= 3 else 1, "king")
        if not self.ot and self.t >= MATCH_TIME:
            if c0 != c1:
                return self._finish(0 if c0 > c1 else 1, "time")
            self.ot = True
            self.events.append({"k": "ot"})
        elif self.ot:
            if c0 != c1:
                return self._finish(0 if c0 > c1 else 1, "ot")
            if self.t >= MATCH_TIME + OVERTIME:
                k0 = self.tower(0, "K")
                k1 = self.tower(1, "K")
                h0 = k0.hp if k0 else 0
                h1 = k1.hp if k1 else 0
                if abs(h0 - h1) < 1:
                    return self._finish(None, "draw")
                return self._finish(0 if h0 > h1 else 1, "kinghp")

    def surrender(self, side):
        if not self.over:
            self.players[1 - side].crowns = 3
            self._finish(1 - side, "surrender")

    def _finish(self, winner, reason):
        self.over = True
        self.ended_at = time.monotonic()
        self.result = {"winner": winner, "reason": reason}

    # ---------------- 给某个玩家的画面快照 ----------------
    def snapshot(self, side, events):
        p = self.players[side]
        us = []
        for u in self.units.values():
            flags = 0
            if u.deploy > 0:
                flags |= 1
            if u.stun > 0:
                flags |= 2
            if u.shield > 0:
                flags |= 4
            if u.hidden:
                flags |= 8
            if u.kind == "tower" and not u.active:
                flags |= 16
            if u.fly:
                flags |= 32
            us.append([u.id, u.key, u.side, round(u.x, 2), round(u.y, 2),
                       int(max(0, u.hp)), int(u.maxhp), flags])
        return {
            "t": round(self.time_left(), 1), "ot": self.ot, "m": self.mult(),
            "u": us, "ev": events,
            "me": {"e": round(p.elixir, 2), "hand": p.hand, "next": p.queue[0]},
            "cr": [p.crowns, self.players[1 - side].crowns],
        }


# ======================================================================
# AI（洞窟守卫）：守家优先，瓜拉娜攒够了就推一波
# ======================================================================
class AIBrain:
    def __init__(self):
        self.cd = 2.0
        self.push_lane = random.choice(BRIDGES)

    def think(self, m, side, dt):
        self.cd -= dt
        if self.cd > 0 or m.over:
            return
        self.cd = random.uniform(0.7, 1.3)
        p = m.players[side]
        own = (lambda y: y >= RIVER_BOT) if side == 0 else (lambda y: y <= RIVER_TOP)
        enemies = [u for u in m.units.values()
                   if u.side != side and u.kind == "troop" and not u.dead]
        threats = [u for u in enemies if own(u.y) or abs(u.y - (RIVER_BOT if side == 0 else RIVER_TOP)) < 3]
        hand = [(i, k) for i, k in enumerate(p.hand) if CARDS[k]["cost"] <= p.elixir + 1e-6]
        if not hand:
            return
        if threats:
            # 一团就丢法术
            for i, k in hand:
                spec = CARDS[k]
                if spec["type"] != "spell":
                    continue
                for c in threats:
                    near = [v for v in threats if math.hypot(v.x - c.x, v.y - c.y) <= spec["radius"]]
                    worth = sum(min(v.hp, spec["dmg"]) for v in near)
                    if len(near) >= 3 or worth >= spec["cost"] * 110:
                        if m.play(side, i, c.x, c.y) is None:
                            return
            # 派兵防守：放在威胁和自家塔之间
            th = min(threats, key=lambda v: abs(v.y - (H if side == 0 else 0)))
            need_air = th.fly
            cands = [(i, k) for i, k in hand
                     if CARDS[k]["type"] != "spell" and CARDS[k].get("tgt") != "buildings"
                     and CARDS[k].get("tgt") != "none"
                     and (not need_air or CARDS[k].get("tgt") == "both")]
            if cands:
                i, k = random.choice(cands)
                lane = "L" if th.x < W / 2 else "R"
                tw = m.tower(side, lane) or m.tower(side, "K")
                x = tw.x + (th.x - tw.x) * 0.5 if tw else th.x
                y = tw.y + (th.y - tw.y) * 0.5 if tw else th.y
                y = max(RIVER_BOT + 0.8, y) if side == 0 else min(RIVER_TOP - 0.8, y)
                x = min(W - 1, max(1, x + random.uniform(-0.8, 0.8)))
                if m.play(side, i, x, y) is None:
                    return
            return
        enemy = 1 - side
        # 收残塔：对面哪座塔的血量一发法术就能带走，直接砸
        for i, k in hand:
            spec = CARDS[k]
            if spec["type"] != "spell":
                continue
            for slot in ("L", "R", "K"):
                tw = m.tower(enemy, slot)
                if tw and tw.hp <= spec["dmg"] * SPELL_TOWER_RATE:
                    if m.play(side, i, tw.x, tw.y) is None:
                        return
        # 跟推：己方已经有兵在往前压，就在后面补支援（治疗、远程）
        mine = [u for u in m.units.values() if u.side == side and u.kind == "troop" and not u.dead]
        if side == 0:
            pushing = [u for u in mine if u.y < RIVER_BOT + 4]
        else:
            pushing = [u for u in mine if u.y > RIVER_TOP - 4]
        if pushing and p.elixir >= 4:
            lead = max(pushing, key=lambda u: u.maxhp)
            sup = [(i, k) for i, k in hand if CARDS[k]["type"] == "troop"
                   and CARDS[k].get("tgt") != "buildings"]
            if sup:
                i, k = random.choice(sup)
                y = min(H - 1, lead.y + 2) if side == 0 else max(1, lead.y - 2)
                y = max(RIVER_BOT + 0.6, y) if side == 0 else min(RIVER_TOP - 0.6, y)
                if m.play(side, i, lead.x + random.uniform(-0.6, 0.6), y) is None:
                    return
        # 进攻：瓜拉娜攒够了就推一波（坦克优先）
        if p.elixir >= 7.5 or (p.elixir >= 5 and random.random() < 0.35):
            troops = [(i, k) for i, k in hand if CARDS[k]["type"] == "troop" and not CARDS[k].get("heal")]
            if not troops:
                return
            tanks = [(i, k) for i, k in troops if CARDS[k].get("tgt") == "buildings"
                     or CARDS[k].get("hp", 0) >= 700]
            i, k = random.choice(tanks or troops)
            # 挑对面血少的那一路打
            hl = m.tower(enemy, "L")
            hr = m.tower(enemy, "R")
            if hl and hr:
                lane = BRIDGES[0] if hl.hp < hr.hp else BRIDGES[1]
                if abs(hl.hp - hr.hp) < 200:
                    lane = self.push_lane
            else:
                lane = BRIDGES[0] if hl is None else BRIDGES[1]
            if CARDS[k].get("anywhere"):
                tw = m.tower(enemy, "L" if lane < W / 2 else "R") or m.tower(enemy, "K")
                if tw:
                    x, y = tw.x, tw.y + (tw.r + 0.8) * (1 if enemy == 1 else -1)
                    if m.play(side, i, x, y) is None:
                        return
            if CARDS[k]["spd"] == "慢":
                y = RIVER_BOT + 7 if side == 0 else RIVER_TOP - 7    # 慢坦克从后场出，攒一波
            else:
                y = RIVER_BOT + 1.2 if side == 0 else RIVER_TOP - 1.2
            m.play(side, i, lane + random.uniform(-0.5, 0.5), y)


# ======================================================================
# 数据库：卡组 / 解锁 / 战绩 / 每日奖励额度
# ======================================================================
def ensure_tables():
    conn = pet._connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jiangshi_players (
                owner TEXT PRIMARY KEY,
                deck TEXT,
                unlocked TEXT,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                draws INTEGER NOT NULL DEFAULT 0,
                ai_wins INTEGER NOT NULL DEFAULT 0,
                day TEXT,
                day_pvp_rewards INTEGER NOT NULL DEFAULT 0,
                day_ai_coins INTEGER NOT NULL DEFAULT 0,
                day_vs TEXT,
                updated_at REAL
            )""")
        conn.commit()
    finally:
        conn.close()


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _load_player(conn, owner):
    row = conn.execute("SELECT deck, unlocked, wins, losses, draws, ai_wins, day, day_pvp_rewards, "
                       "day_ai_coins, day_vs FROM jiangshi_players WHERE owner=?", (owner,)).fetchone()
    if not row:
        return {"deck": list(DEFAULT_DECK), "unlocked": [], "wins": 0, "losses": 0, "draws": 0,
                "ai_wins": 0, "day": _today(), "day_pvp_rewards": 0, "day_ai_coins": 0, "day_vs": {}}
    d = {"deck": json.loads(row[0] or "null") or list(DEFAULT_DECK),
         "unlocked": json.loads(row[1] or "[]"), "wins": row[2], "losses": row[3], "draws": row[4],
         "ai_wins": row[5], "day": row[6], "day_pvp_rewards": row[7], "day_ai_coins": row[8],
         "day_vs": json.loads(row[9] or "{}")}
    if d["day"] != _today():                      # 过了一天，每日额度清零
        d.update(day=_today(), day_pvp_rewards=0, day_ai_coins=0, day_vs={})
    return d


def _save_player(conn, owner, d):
    conn.execute("""INSERT OR REPLACE INTO jiangshi_players
        (owner, deck, unlocked, wins, losses, draws, ai_wins, day, day_pvp_rewards, day_ai_coins, day_vs, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (owner, json.dumps(d["deck"]), json.dumps(d["unlocked"]), d["wins"], d["losses"],
                  d["draws"], d["ai_wins"], d["day"], d["day_pvp_rewards"], d["day_ai_coins"],
                  json.dumps(d["day_vs"], ensure_ascii=False), time.time()))


def _owned(d):
    return set(OPEN_CARDS) | set(k for k in d["unlocked"] if k in CARDS)


def _card_meta():
    out = []
    for k in ALL_KEYS:
        c = CARDS[k]
        out.append({"key": k, "name": c["name"], "cost": c["cost"], "rarity": c["rarity"],
                    "type": c["type"], "emoji": c["emoji"], "desc": c["desc"],
                    "count": c.get("count"), "hp": c.get("hp"), "dmg": c.get("dmg"),
                    "hs": c.get("hs"), "rng": c.get("rng"), "spd": c.get("spd"),
                    "tgt": c.get("tgt"), "radius": c.get("radius"), "r": c.get("r"),
                    "anywhere": bool(c.get("anywhere")), "price": UNLOCK_PRICE.get(k, 0)})
    return out


def leaderboard(conn, limit=20):
    rows = conn.execute("SELECT owner, wins, losses FROM jiangshi_players WHERE wins > 0 "
                        "ORDER BY wins DESC, losses ASC, owner LIMIT ?", (limit,)).fetchall()
    return [{"name": r[0], "wins": r[1], "losses": r[2]} for r in rows]


# ---------------- HTTP 用的三个函数 ----------------
def lobby_data(owner):
    conn = pet._connect()
    try:
        d = _load_player(conn, owner)
        owned = _owned(d)
        deck = [k for k in d["deck"] if k in owned][:DECK_SIZE]
        return {
            "me": owner,
            "coins": casino._coins_get(conn, owner),
            "cards": _card_meta(),
            "owned": sorted(owned),
            "deck": deck,
            "deck_size": DECK_SIZE,
            "stats": {"wins": d["wins"], "losses": d["losses"], "draws": d["draws"], "ai_wins": d["ai_wins"]},
            "today": {"pvp_rewards_left": max(0, PVP_DAILY_WINS - d["day_pvp_rewards"]),
                      "ai_coins_left": max(0, AI_DAILY_COINS - d["day_ai_coins"])},
            "rewards": {"pvp_win": REWARD_PVP_WIN, "pvp_lose": REWARD_PVP_LOSE, "ai_win": REWARD_AI_WIN},
            "board": leaderboard(conn),
        }
    finally:
        conn.close()


def save_deck(owner, deck):
    if not isinstance(deck, list):
        return {"ok": False, "msg": "卡组格式不对"}
    deck = [str(k) for k in deck]
    if len(deck) != DECK_SIZE or len(set(deck)) != DECK_SIZE:
        return {"ok": False, "msg": f"卡组要正好 {DECK_SIZE} 张不同的卡"}
    conn = pet._connect()
    try:
        d = _load_player(conn, owner)
        owned = _owned(d)
        bad = [k for k in deck if k not in owned]
        if bad:
            return {"ok": False, "msg": "有卡还没解锁"}
        d["deck"] = deck
        _save_player(conn, owner, d)
        conn.commit()
        return {"ok": True, "deck": deck}
    finally:
        conn.close()


def unlock_card(owner, key):
    key = str(key)
    if key not in UNLOCK_PRICE:
        return {"ok": False, "msg": "这张卡不需要解锁"}
    price = UNLOCK_PRICE[key]
    conn = pet._connect()
    try:
        d = _load_player(conn, owner)
        if key in d["unlocked"]:
            return {"ok": False, "msg": "已经解锁过了", "coins": casino._coins_get(conn, owner)}
        bal = casino._coins_get(conn, owner)
        if bal < price:
            return {"ok": False, "msg": f"金币不够（需要 {price}，你有 {bal}）", "coins": bal}
        newbal = casino._coins_add(conn, owner, -price)
        d["unlocked"].append(key)
        _save_player(conn, owner, d)
        conn.commit()
        return {"ok": True, "coins": newbal, "card": key}
    finally:
        conn.close()


def _settle(m):
    """对局结束：发奖励、记战绩。返回 {name: 结算信息}。"""
    out = {}
    humans = [p for p in m.players if not p.ai]
    valid = m.t >= VALID_MIN_SECONDS and all(p.played >= VALID_MIN_PLAYS for p in humans)
    w = m.result["winner"]
    conn = pet._connect()
    try:
        for side, p in enumerate(m.players):
            if p.ai:
                continue
            opp = m.players[1 - side]
            res = "draw" if w is None else ("win" if w == side else "lose")
            info = {"result": res, "coins": 0, "note": ""}
            d = _load_player(conn, p.name)
            if not valid:
                info["note"] = f"对局太短或出牌太少（需打满 {VALID_MIN_SECONDS} 秒、每人至少出 {VALID_MIN_PLAYS} 张），本局不计奖励"
            elif m.pvp:
                if res == "win":
                    vs = d["day_vs"].get(opp.name, 0)
                    if vs >= PVP_SAME_OPP_DAILY:
                        info["note"] = f"今天赢 {opp.name} 已经 {PVP_SAME_OPP_DAILY} 次了，这局不计奖励和排行"
                    else:
                        d["wins"] += 1
                        d["day_vs"][opp.name] = vs + 1
                        if d["day_pvp_rewards"] < PVP_DAILY_WINS:
                            d["day_pvp_rewards"] += 1
                            info["coins"] = REWARD_PVP_WIN
                        else:
                            info["note"] = f"今天的 {PVP_DAILY_WINS} 次胜利奖励已领完，胜场照样进排行"
                elif res == "lose":
                    d["losses"] += 1
                    info["coins"] = REWARD_PVP_LOSE
                else:
                    d["draws"] += 1
                    info["coins"] = REWARD_PVP_DRAW
            else:
                if res == "win":
                    d["ai_wins"] += 1
                    left = AI_DAILY_COINS - d["day_ai_coins"]
                    gain = max(0, min(REWARD_AI_WIN, left))
                    d["day_ai_coins"] += gain
                    info["coins"] = gain
                    if gain == 0:
                        info["note"] = f"今天打 AI 的 {AI_DAILY_COINS} 金币额度已满"
            if info["coins"]:
                info["balance"] = casino._coins_add(conn, p.name, info["coins"])
            _save_player(conn, p.name, d)
            out[p.name] = info
        conn.commit()
    except Exception:
        traceback.print_exc()
    finally:
        conn.close()
    return out


# ======================================================================
# 房间 / 匹配 / websocket
# ======================================================================
_sio = None
_lock = threading.RLock()
_queue = []                 # [{name, sid, deck, since}]
_matches = {}               # match_id -> Match
_by_name = {}               # username -> match_id（进行中的对局）
_sid_name = {}              # sid -> username
_pending_end = {}           # username -> (结算数据, 时间)：对局结束时人不在线，等他回来补发
PENDING_KEEP = 1800         # 错过的结算最多保留 30 分钟
_loop_started = False


def init(socketio):
    """在 app.py 里调用一次：建表 + 注册 websocket 事件。"""
    global _sio
    _sio = socketio
    ensure_tables()
    socketio.on_event("connect", _on_connect, namespace=NS)
    socketio.on_event("disconnect", _on_disconnect, namespace=NS)
    socketio.on_event("js_queue", _on_queue, namespace=NS)
    socketio.on_event("js_cancel", _on_cancel, namespace=NS)
    socketio.on_event("js_play", _on_play, namespace=NS)
    socketio.on_event("js_surrender", _on_surrender, namespace=NS)


def _emit(event, data, sid):
    if sid:
        _sio.emit(event, data, to=sid, namespace=NS)


def _ensure_loop():
    global _loop_started
    if not _loop_started:
        _loop_started = True
        _sio.start_background_task(_game_loop)


def _on_connect(auth=None):
    name = session.get("username")
    if not name:
        return False                      # 没登录不让连
    missed = None
    with _lock:
        _sid_name[request.sid] = name
        mid = _by_name.get(name)
        m = _matches.get(mid) if mid else None
        start = None
        if m:
            side = 0 if m.players[0].name == name else 1
            m.players[side].sid = request.sid     # 先把人接上（哪怕这局刚好在结算）
            if not m.over:                        # 断线重连：接回原来的对局
                start = _start_payload(m, side)
        if not start:
            pend = _pending_end.pop(name, None)   # 对局在你离开时已经结束了 → 补发结算
            if pend and time.monotonic() - pend[1] < PENDING_KEEP:
                missed = dict(pend[0], missed=True)
    _ensure_loop()
    if start:
        _emit("js_start", start, request.sid)
    if missed:
        _emit("js_end", missed, request.sid)


def _on_disconnect(*args):
    sid = request.sid
    with _lock:
        _sid_name.pop(sid, None)
        _queue[:] = [q for q in _queue if q["sid"] != sid]
        for m in _matches.values():
            for p in m.players:
                if p.sid == sid:
                    p.sid = None          # 对局照常进行，回来还能接上


def _on_queue(data=None):
    sid = request.sid
    name = _sid_name.get(sid)
    if not name:
        return
    conn = pet._connect()
    try:
        d = _load_player(conn, name)
    finally:
        conn.close()
    owned = _owned(d)
    deck = [k for k in (d["deck"] or []) if k in owned]
    if len(deck) != DECK_SIZE or len(set(deck)) != DECK_SIZE:
        _emit("js_error", {"msg": "先组好 8 张卡的卡组再开战"}, sid)
        return
    starts = []
    with _lock:
        mid = _by_name.get(name)
        if mid and mid in _matches and not _matches[mid].over:
            m = _matches[mid]
            side = 0 if m.players[0].name == name else 1
            m.players[side].sid = sid
            starts.append((sid, _start_payload(m, side)))
        else:
            _queue[:] = [q for q in _queue if q["name"] != name]
            other = next((q for q in _queue if q["name"] != name), None)
            if other:
                _queue.remove(other)
                a = Player(other["name"], other["deck"], sid=other["sid"])
                b = Player(name, deck, sid=sid)
                if random.random() < 0.5:
                    a, b = b, a
                m = Match(a, b, pvp=True)
                _register(m)
                starts += [(a.sid, _start_payload(m, 0)), (b.sid, _start_payload(m, 1))]
            else:
                _queue.append({"name": name, "sid": sid, "deck": deck, "since": time.monotonic()})
    _ensure_loop()
    if starts:
        for s, payload in starts:
            _emit("js_start", payload, s)
    else:
        _emit("js_queued", {"wait": QUEUE_WAIT}, sid)


def _on_cancel(data=None):
    sid = request.sid
    with _lock:
        _queue[:] = [q for q in _queue if q["sid"] != sid]
    _emit("js_cancelled", {}, sid)


def _find(sid):
    name = _sid_name.get(sid)
    m = _matches.get(_by_name.get(name)) if name else None
    if not m:
        return None, None
    side = 0 if m.players[0].name == name else 1
    return m, side


def _on_play(data=None):
    data = data or {}
    sid = request.sid
    with _lock:
        m, side = _find(sid)
        if not m:
            err = "你不在对局里"
        else:
            try:
                err = m.play(side, int(data.get("i", -1)), data.get("x"), data.get("y"))
            except Exception:
                traceback.print_exc()
                err = "出牌失败"
    if err:
        _emit("js_error", {"msg": err}, sid)


def _on_surrender(data=None):
    with _lock:
        m, side = _find(request.sid)
        if m:
            m.surrender(side)


def _register(m):
    _matches[m.id] = m
    for p in m.players:
        if not p.ai:
            _by_name[p.name] = m.id


def _start_payload(m, side):
    opp = m.players[1 - side]
    return {"match": m.id, "side": side, "opp": opp.name, "opp_ai": opp.ai,
            "pvp": m.pvp, "cards": _card_meta(), "time": MATCH_TIME,
            "extra": {"golemite": {"name": GOLEMITE["name"], "emoji": GOLEMITE["emoji"], "r": GOLEMITE["r"]}},
            "field": {"w": W, "h": H, "river": [RIVER_TOP, RIVER_BOT], "bridges": BRIDGES,
                      "bridge_half": BRIDGE_HALF,
                      "towers": [{"side": s, "slot": slot,
                                  "x": _mirror(x, y, s)[0], "y": _mirror(x, y, s)[1],
                                  "r": KING_R if slot == "K" else PRINCESS_R}
                                 for s in (0, 1) for slot, (x, y) in TOWER_POS.items()]}}


def _game_loop():
    last = time.monotonic()
    while True:
        _sio.sleep(TICK)
        now = time.monotonic()
        dt = min(0.3, now - last)
        last = now
        out = []
        finished = []
        with _lock:
            # 排队太久 → 派 AI
            for q in list(_queue):
                if now - q["since"] >= QUEUE_WAIT:
                    _queue.remove(q)
                    human = Player(q["name"], q["deck"], sid=q["sid"])
                    bot = Player(AI_NAME, random.choice(AI_DECKS), ai=True)
                    m = Match(human, bot, pvp=False)
                    _register(m)
                    out.append(("js_start", _start_payload(m, 0), human.sid))
            for m in list(_matches.values()):
                if not m.over:
                    try:
                        m.step(dt)
                    except Exception:
                        traceback.print_exc()
                        m._finish(None, "error")
                    ev, m.events = m.events, []
                    for side, p in enumerate(m.players):
                        if p.sid and not p.ai:
                            out.append(("js_state", m.snapshot(side, ev), p.sid))
                if m.over and not m.settled:          # 不管是打完、投降还是出错，都结算一次
                    m.settled = True
                    finished.append(m)
                elif m.over and m.ended_at and now - m.ended_at > 30:
                    del _matches[m.id]
            for m in finished:
                for p in m.players:
                    if _by_name.get(p.name) == m.id:
                        del _by_name[p.name]
            for name in [n for n, (_, t) in _pending_end.items() if now - t > PENDING_KEEP]:
                del _pending_end[name]
        for m in finished:                 # 写数据库放在锁外面
            settle = _settle(m)
            for side, p in enumerate(m.players):
                if p.ai:
                    continue
                w = m.result["winner"]
                payload = {
                    "result": "draw" if w is None else ("win" if w == side else "lose"),
                    "reason": m.result["reason"],
                    "cr": [p.crowns, m.players[1 - side].crowns],
                    "opp": m.players[1 - side].name,
                    "settle": settle.get(p.name, {}),
                }
                if p.sid:
                    out.append(("js_end", payload, p.sid))
                else:                                  # 人不在线：先存着，回来再补发
                    with _lock:
                        _pending_end[p.name] = (payload, now)
        for event, data, sid in out:
            try:
                _emit(event, data, sid)
            except Exception:
                traceback.print_exc()
