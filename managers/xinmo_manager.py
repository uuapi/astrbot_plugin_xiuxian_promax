# managers/xinmo_manager.py
"""心魔挑战 v3.55.0 - PVE 影子对决：以自身战斗面板生成心魔复制体

- 每日 1 次（syscfg_claim_slot 日键 xinmo_cnt_{uid}_{YYYYMMDD}，自动清理）
- 心魔 = 玩家战斗面板的镜像强化体：
    · 五行同源镜像（公平对局，玩家保持先手优势）
    · HP/ATK 基线略低于本体、随境界线性渐强（低境界轻松可胜，高境界需硬实力）
    · 道心削弱心魔（每点 -1%，上限 -20%："道心高者心魔弱"）
    · 功法词条镜像（心魔知你套路；灵兽协战除外）
- 奖惩：胜得全额修为（300×境界），败仅损失 2% 修为（道心蒙尘，最低保底不删号）
- 胜场累计计数 + title_hooks 颁发称号（xinmo_kill 路径，title_data 注册）
- 全服降魔榜：扫描 xinmo_wins_{uid} 计数键（ladder 榜同款扫描模式）
"""
import random
import time
from typing import List, Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from .combat_manager import CombatManager, CombatStats

__all__ = ["XinmoManager"]


class XinmoManager:
    """心魔挑战管理器"""

    DAILY_LIMIT = 1                      # 每日次数
    BASE_REWARD_EXP = 300                # 基础修为奖励 ×（境界+1）
    HP_BASE = 0.90                       # 心魔血量基线（低境界弱于本体，先手可胜）
    HP_PER_LEVEL = 0.015                 # 心魔血量强化 / 境界
    ATK_BASE = 0.85                      # 心魔攻击基线
    ATK_PER_LEVEL = 0.010                # 心魔攻击强化 / 境界
    DAOXIN_DISCOUNT_PER_POINT = 0.01     # 道心削弱心魔 / 点
    DAOXIN_DISCOUNT_CAP = 0.20           # 道心削弱上限
    COEFF_FLOOR = 0.8                    # 强化系数下限
    LOSE_EXP_PENALTY_RATIO = 0.02        # 战败修为损失比例
    WINS_PREFIX = "xinmo_wins_"
    DEMON_NAMES = ["贪", "嗔", "痴", "怨", "怠", "傲", "疑"]

    def __init__(self, db: DataBase):
        self.db = db

    # ================= 每日限次 =================

    def _cnt_key(self, uid: str) -> str:
        return f"xinmo_cnt_{uid}_{time.strftime('%Y%m%d')}"

    async def _claim_daily(self, uid: str) -> bool:
        return await self.db.ext.syscfg_claim_slot(self._cnt_key(uid), self.DAILY_LIMIT)

    # ================= 心魔生成 =================

    def build_demon_stats(self, player_stats: CombatStats, level_index: int,
                          daoxin: int = 0) -> CombatStats:
        """从玩家战斗面板生成心魔复制体（纯函数，不触库）

        平衡设计：五行同源镜像（公平），血量/攻击基线低于本体（玩家先手可胜），
        随境界线性渐强（高境界心魔反超本体）；道心每点 -1% 削弱心魔（上限 20%）。
        """
        discount = min(self.DAOXIN_DISCOUNT_CAP, max(0, daoxin) * self.DAOXIN_DISCOUNT_PER_POINT)
        hp_k = max(self.COEFF_FLOOR, self.HP_BASE + level_index * self.HP_PER_LEVEL - discount)
        atk_k = max(self.COEFF_FLOOR, self.ATK_BASE + level_index * self.ATK_PER_LEVEL - discount)

        hp = max(10, int(player_stats.max_hp * hp_k))
        atk = max(1, int(player_stats.atk * atk_k))
        # 镜像玩家功法词条（心魔知你套路），但心魔不能召唤玩家的灵兽
        effects = {k: v for k, v in (player_stats.effects or {}).items() if k != "pet_damage_mult"}

        demon_name = f"心魔·{random.choice(self.DEMON_NAMES)}"
        return CombatStats(
            user_id="xinmo",
            name=f"{demon_name}（{player_stats.name}之影）",
            hp=hp, max_hp=hp,
            mp=player_stats.mp, max_mp=player_stats.max_mp,
            atk=atk, defense=player_stats.defense,
            crit_rate=player_stats.crit_rate,
            exp=max(1, self.BASE_REWARD_EXP * (level_index + 1)),
            effects=effects,
            element=player_stats.element,
        )

    # ================= 挑战 =================

    async def challenge(self, player, player_stats: CombatStats) -> Tuple[bool, str]:
        """发起心魔挑战。返回 (是否成功受理, 战报文本)。"""
        uid = player.user_id
        if not await self._claim_daily(uid):
            return False, (
                "🧠 你今日已照见过心魔（每日 1 次）。\n"
                "💡 心魔无处不在，明日再来；道行越高，心魔越强。"
            )

        # 道心削弱心魔
        daoxin = 0
        try:
            from .enlighten_manager import EnlightenManager
            daoxin = await EnlightenManager(self.db).get_daoxin(uid)
        except Exception:
            daoxin = 0

        demon = self.build_demon_stats(player_stats, player.level_index, daoxin)
        result = CombatManager.player_vs_boss(player_stats, demon)
        won = result.get("winner") == player_stats.user_id

        lines = list(result.get("combat_log") or [])
        # 战报过长时保留头尾（100 回合上限）
        if len(lines) > 30:
            lines = lines[:2] + ["……（中略）……"] + lines[-24:]

        discount_pct = min(self.DAOXIN_DISCOUNT_CAP, max(0, daoxin) * self.DAOXIN_DISCOUNT_PER_POINT)
        head = (
            "🧠【心魔挑战】\n"
            "━━━━━━━━━━━━━━━\n"
            f"心魔强度：HP {demon.max_hp} · ATK {demon.atk}"
            f"（道心 {daoxin} 削弱 -{discount_pct:.0%}）\n"
            f"心魔五行：{demon.element or '无'}（与你同源）"
        )

        if won:
            reward = int(result.get("reward") or 0)
            if reward > 0:
                await self.db.add_player_exp(uid, reward)
            # 胜场累计 + 称号计数（xinmo_kill 路径）
            wins = await self.db.ext.syscfg_adjust_number(f"{self.WINS_PREFIX}{uid}", 1)
            title_msg = ""
            try:
                from .title_hooks import TitleHooks
                granted = await TitleHooks(self.db).bump(player, "xinmo_kill")
                if granted:
                    title_msg = f"\n🏅 获得称号：【{granted}】"
            except Exception as e:
                logger.warning(f"【心魔挑战】称号计数失败: {e}")
            msg = (
                f"{head}\n━━━━━━━━━━━━━━━\n" + "\n".join(lines) +
                f"\n━━━━━━━━━━━━━━━\n"
                f"✅ 心魔被斩落！道心更明。\n"
                f"📈 获得修为：{reward:,}（累计降魔 {int(wins)} 次）{title_msg}"
            )
            return True, msg

        # 战败：无奖励（胜才得修为），道心受挫损失 2% 修为
        penalty = int(max(0, player.experience) * self.LOSE_EXP_PENALTY_RATIO)
        if penalty > 0:
            await self.db.add_player_exp(uid, -penalty)
        msg = (
            f"{head}\n━━━━━━━━━━━━━━━\n" + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━\n"
            f"💫 不敌心魔，道心蒙尘。\n"
            f"📉 道心受挫，修为 -{penalty:,}（{self.LOSE_EXP_PENALTY_RATIO:.0%}）\n"
            f"💡 提升装备、修炼道心，或换一日心境再来。"
        )
        return True, msg

    # ================= 全服降魔榜 =================

    async def get_ranking(self, limit: int = 10) -> List[Tuple[str, int]]:
        """扫描 xinmo_wins_{uid} 计数键（ladder 榜同款扫描模式），降序"""
        try:
            async with self.db.conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE ?",
                (f"{self.WINS_PREFIX}%",)
            ) as cursor:
                rows = await cursor.fetchall()
            ranked = []
            for key, value in rows:
                uid = key[len(self.WINS_PREFIX):]
                try:
                    wins = int(value)
                except (TypeError, ValueError):
                    continue
                if wins > 0:
                    ranked.append((uid, wins))
            ranked.sort(key=lambda x: -x[1])
            return ranked[:limit]
        except Exception as e:
            logger.warning(f"【心魔榜】扫描失败: {e}")
            return []

    async def get_board(self, player=None) -> str:
        """全服降魔榜文本"""
        ranked = await self.get_ranking()
        lines = ["🧠【全服降魔榜】", "━━━━━━━━━━━━━━━"]
        if not ranked:
            lines.append("  暂无人降魔。/心魔挑战 打响第一战！")
        else:
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid, wins) in enumerate(ranked, start=1):
                p = await self.db.get_player_by_id(uid)
                name = (p.user_name if p and p.user_name else f"道友{uid[:6]}")
                medal = medals[i - 1] if i <= 3 else f"{i}."
                lines.append(f"  {medal} {name} — 降魔 {wins} 次")
        lines.append("━━━━━━━━━━━━━━━")
        if player is not None:
            wins = await self.db.ext.syscfg_get_number(f"{self.WINS_PREFIX}{player.user_id}")
            if wins > 0:
                lines.append(f"你的战绩：降魔 {wins} 次")
        lines.append("💡 /心魔挑战 每日 1 次，胜得修为，败损修行；道心可削弱心魔。")
        return "\n".join(lines)
