# managers/sect_shop_manager.py
"""宗门贡献商店（v3.51.0 新玩法）：用宗门贡献兑换稀有物品

- 贡献来源：宗门捐献 / 宗门任务 / 宗门大比
- 商品分 3 档（按宗门等级/炼丹房等级解锁），全部原子扣贡献
- /贡献商店 面板 · /贡献兑换 <编号> 购买
"""

import json
from typing import Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["SectShopManager"]


class SectShopManager:
    """宗门贡献商店：贡献即是荣耀，亦是力量"""

    def __init__(self, db: DataBase, storage_ring_mgr=None):
        self.db = db
        self.storage_ring_mgr = storage_ring_mgr

    # 商品定义：(名称, 类型, 所需贡献, 描述, 需要炼丹房等级)
    SHOP = [
        ("引雷符 x3",       "item", 200,  "炼丹保命必备",              0),
        ("洗练石 x2",       "item", 300,  "重新洗练装备词条",           0),
        ("聚灵丹 x1",       "pill", 500,  "修为丹（灵品）",             1),
        ("强化石 x5",       "item", 500,  "强化装备必需品",             1),
        ("空间石 x1",       "item", 800,  "扩充储物戒容量",             2),
        ("属性丹·攻",       "attr_atk", 1000, "永久物伤 +50",         2),
        ("属性丹·防",       "attr_def", 1000, "永久物防 +50",         2),
        ("破境丹 x1",       "pill", 1500, "突破成功率 +15%",           3),
        ("宗门专属称号「宗门栋梁」", "title", 2000, "唯有栋梁方可佩戴",  3),
    ]

    @staticmethod
    def _spend_contribution(player: Player, cost: int) -> bool:
        """条件原子扣贡献（不可透支）"""
        player.sect_contribution = max(0, (player.sect_contribution or 0) - cost)
        return True

    async def _deduct_contribution(self, uid: str, cost: int) -> bool:
        cursor = await self.db.conn.execute(
            "UPDATE players SET sect_contribution = sect_contribution - ? WHERE user_id = ? AND sect_contribution >= ?",
            (cost, uid, cost)
        )
        await self.db.conn.commit()
        return cursor.rowcount > 0

    async def _refund_contribution(self, uid: str, cost: int):
        await self.db.conn.execute(
            "UPDATE players SET sect_contribution = sect_contribution + ? WHERE user_id = ?",
            (cost, uid)
        )
        await self.db.conn.commit()

    async def get_panel(self, player: Player, sect) -> str:
        """贡献商店面板"""
        contrib = int(player.sect_contribution or 0)
        # 获取宗门炼丹房等级
        elixir_lv = 0
        try:
            buildings = sect.buildings if isinstance(sect.buildings, dict) else json.loads(sect.buildings or "{}")
            elixir_lv = int(buildings.get("炼丹房", 0))
        except Exception:
            pass

        lines = [
            f"🏪【宗门贡献商店】{sect.sect_name}",
            "━━━━━━━━━━━━━━━",
            f"⭐ 你的贡献：{contrib:,}",
            f"🏛️ 宗门炼丹房：Lv.{elixir_lv}",
            "━━━━━━━━━━━━━━━",
        ]
        for idx, (name, _gtype, cost, desc, req_lv) in enumerate(self.SHOP, 1):
            locked = elixir_lv < req_lv
            lock_tag = f" 🔒需炼丹房Lv.{req_lv}" if locked else ""
            afford = "✅" if contrib >= cost else "💤"
            lines.append(f"  [{idx}] {afford} {name} —— {cost:,} 贡献{lock_tag}")
            lines.append(f"      {desc}")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 /贡献兑换 <编号> 消耗贡献购买")
        lines.append("💡 /宗门捐献 <灵石> 获取贡献")
        return "\n".join(lines)

    async def redeem(self, player: Player, sect, idx: int) -> Tuple[bool, str]:
        """贡献兑换"""
        if idx < 1 or idx > len(self.SHOP):
            return False, f"❌ 无效编号。可选 1-{len(self.SHOP)}。"
        name, gtype, cost, desc, req_lv = self.SHOP[idx - 1]

        # 检查宗门等级
        elixir_lv = 0
        try:
            buildings = sect.buildings if isinstance(sect.buildings, dict) else json.loads(sect.buildings or "{}")
            elixir_lv = int(buildings.get("炼丹房", 0))
        except Exception:
            pass
        if elixir_lv < req_lv:
            return False, f"❌ 该商品需要宗门炼丹房达到 Lv.{req_lv}（当前 Lv.{elixir_lv}）。"

        # 原子扣贡献
        if not await self._deduct_contribution(player.user_id, cost):
            return False, f"❌ 贡献不足！需要 {cost:,}，你当前 {int(player.sect_contribution or 0):,}。"
        player.sect_contribution = max(0, (player.sect_contribution or 0) - cost)

        try:
            if gtype == "item":
                if not self.storage_ring_mgr:
                    raise RuntimeError("储物戒未加载")
                ok, msg = await self.storage_ring_mgr.store_item(player, name.split(" x")[0], int(name.split(" x")[1]) if " x" in name else 1, silent=True)
                if not ok:
                    await self._refund_contribution(player.user_id, cost)
                    player.sect_contribution += cost
                    return False, "❌ 储物戒已满，贡献已退还。"
                return True, f"🏪 兑换成功！【{name}】已存入储物戒（消耗 {cost:,} 贡献）"

            elif gtype == "pill":
                if not self.storage_ring_mgr:
                    raise RuntimeError("储物戒未加载")
                ok, msg = await self.storage_ring_mgr.store_item(player, name.split(" x")[0], 1, silent=True)
                if not ok:
                    await self._refund_contribution(player.user_id, cost)
                    player.sect_contribution += cost
                    return False, "❌ 储物戒已满，贡献已退还。"
                return True, f"🏪 兑换成功！【{name}】已存入储物戒（消耗 {cost:,} 贡献）"

            elif gtype == "attr_atk":
                await self.db.conn.execute(
                    "UPDATE players SET physical_damage = physical_damage + 50 WHERE user_id = ?",
                    (player.user_id,)
                )
                await self.db.conn.commit()
                return True, f"🏪 兑换成功！服用【属性丹·攻】，永久物伤 +50（消耗 {cost:,} 贡献）"

            elif gtype == "attr_def":
                await self.db.conn.execute(
                    "UPDATE players SET physical_defense = physical_defense + 50 WHERE user_id = ?",
                    (player.user_id,)
                )
                await self.db.conn.commit()
                return True, f"🏪 兑换成功！服用【属性丹·防】，永久物防 +50（消耗 {cost:,} 贡献）"

            elif gtype == "title":
                # 写入称号
                player.title = f"{player.title}·宗门栋梁" if player.title and "宗门栋梁" not in player.title else "宗门栋梁"
                await self.db.update_player_fields(player.user_id, title=player.title)
                return True, f"🏪 兑换成功！获得称号【宗门栋梁】（消耗 {cost:,} 贡献）"

        except Exception as e:
            logger.error(f"[贡献商店] 兑换异常: {e}")
            await self._refund_contribution(player.user_id, cost)
            player.sect_contribution += cost
            return False, "❌ 兑换异常，贡献已退还。"

        return False, "❌ 未知商品类型。"
