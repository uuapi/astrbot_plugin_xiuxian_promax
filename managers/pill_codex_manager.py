# managers/pill_codex_manager.py
"""丹药图鉴 v3.53.0：服用的丹药自动收录，按收集比例自动发放档位奖励

- 目录：ConfigManager 四源丹药（pills/exp/utility + items.json type=丹药）
- 档案：system_config 键 pillcodex_{uid} = JSON 已服用丹药名列表（无日期后缀，
  不会被 purge_dated_config_keys 清理）
- 档位：默认 25/50/75/100% 四档（灵石+修为），查看图鉴时自动幂等发放——
  与妖兽图鉴/收集图鉴的"查看即领"体验一致
- 记录点：PillManager.use_pill 成功路径（丹药锁内调用，天然串行防并发覆盖）
"""
import json
from typing import List

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["PillCodexManager"]


class PillCodexManager:
    """丹药图鉴管理器"""

    KEY_PREFIX = "pillcodex_"
    CLAIM_PREFIX = "pillcodex_claim_"

    # 档位表（percent, gold, exp），可被 WebUI 配置 PILL_CODEX.TIER_REWARDS 覆盖
    DEFAULT_TIERS = [
        {"percent": 25, "gold": 3000, "exp": 8000},
        {"percent": 50, "gold": 8000, "exp": 20000},
        {"percent": 75, "gold": 20000, "exp": 50000},
        {"percent": 100, "gold": 50000, "exp": 120000},
    ]

    def __init__(self, db: DataBase, config: dict = None, config_manager=None):
        self.db = db
        self.config = config or {}
        self.config_manager = config_manager
        self._catalog_override = None  # 测试注入用

    # ================= 目录 =================

    def catalog(self) -> List[str]:
        """四源丹药总目录（去重排序；测试可注入 _catalog_override）"""
        if self._catalog_override is not None:
            return list(self._catalog_override)
        try:
            return sorted(n for n in self.config_manager.get_all_pill_names() if n)
        except Exception as e:
            logger.warning(f"【丹药图鉴】目录加载失败: {e}")
            return []

    # ================= 档案存取 =================

    async def _load_taken(self, user_id: str) -> List[str]:
        try:
            raw = await self.db.ext.get_system_config(f"{self.KEY_PREFIX}{user_id}")
            data = json.loads(raw) if raw else []
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def _save_taken(self, user_id: str, taken: List[str]):
        await self.db.ext.set_system_config(
            f"{self.KEY_PREFIX}{user_id}", json.dumps(taken, ensure_ascii=False)
        )

    async def record(self, player: Player, pill_name: str) -> bool:
        """服用成功后收录（use_pill 丹药锁内调用）。返回是否为新收录。"""
        taken = await self._load_taken(player.user_id)
        if pill_name in taken:
            return False
        taken.append(pill_name)
        await self._save_taken(player.user_id, taken)
        return True

    # ================= 档位奖励 =================

    def _tiers(self) -> List[dict]:
        raw = (self.config.get("PILL_CODEX", {}) or {}).get("TIER_REWARDS")
        if isinstance(raw, list) and raw:
            out = []
            for item in raw:
                try:
                    out.append({"percent": int(item[0]), "gold": int(item[1]), "exp": int(item[2])})
                except (TypeError, ValueError, IndexError):
                    continue
            if out:
                return out
        return self.DEFAULT_TIERS

    @staticmethod
    def _pct(count: int, total: int) -> int:
        return min(100, int(count * 100 / total)) if total > 0 else 0

    async def _claim_reached_tiers(self, player: Player, pct: int) -> str:
        """自动发放已达未领档位。领取标记 syscfg_cas 占坑（幂等，防双领）：
        占坑成功才发奖，中途崩溃奖励暂扣（安全方向），绝不双发。"""
        msgs = []
        for tier in self._tiers():
            p = tier["percent"]
            if pct < p:
                continue
            claim_key = f"{self.CLAIM_PREFIX}{player.user_id}_{p}"
            if not await self.db.ext.syscfg_cas(claim_key, "1", None):
                continue  # 已领取过
            if tier["gold"] > 0:
                await self.db.add_player_gold(player.user_id, tier["gold"])
            if tier["exp"] > 0:
                await self.db.add_player_exp(player.user_id, tier["exp"])
            msgs.append(
                f"🏆 丹药图鉴 {p}% 档达成！奖励灵石 {tier['gold']:,} + 修为 {tier['exp']:,}（已发放）"
            )
        return "\n".join(msgs)

    # ================= 视图 =================

    async def view_data(self, player: Player) -> dict:
        """图鉴视图数据（文本与图片卡片共用）"""
        catalog = self.catalog()
        taken = set(await self._load_taken(player.user_id))
        total = len(catalog)
        collected = [n for n in catalog if n in taken]
        pct = self._pct(len(collected), total)

        cm = self.config_manager
        categories = [
            {"name": "突破丹", "names": sorted(cm.pills_data.keys()),
             "taken": [n for n in sorted(cm.pills_data.keys()) if n in taken]},
            {"name": "修为丹", "names": sorted(cm.exp_pills_data.keys()),
             "taken": [n for n in sorted(cm.exp_pills_data.keys()) if n in taken]},
            {"name": "功能丹", "names": sorted(cm.utility_pills_data.keys()),
             "taken": [n for n in sorted(cm.utility_pills_data.keys()) if n in taken]},
        ]

        claimed = []
        for tier in self._tiers():
            key = f"{self.CLAIM_PREFIX}{player.user_id}_{tier['percent']}"
            if await self.db.ext.get_system_config(key):
                claimed.append(tier["percent"])

        return {
            "total": total,
            "collected": collected,
            "pct": pct,
            "categories": categories,
            "tiers": self._tiers(),
            "claimed": claimed,
        }

    def format_view(self, data: dict) -> str:
        """文本视图"""
        lines = [
            "📖【丹药图鉴】",
            "━━━━━━━━━━━━━━━",
            f"收录进度：{len(data['collected'])}/{data['total']}（{data['pct']}%）",
        ]
        for cat in data["categories"]:
            lines.append(f"  · {cat['name']}：{len(cat['taken'])}/{len(cat['names'])}")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("【档位奖励】")
        for tier in data["tiers"]:
            mark = "✅已领" if tier["percent"] in data["claimed"] else (
                "🎁可领" if data["pct"] >= tier["percent"] else "🔒")
            lines.append(
                f"  {mark} {tier['percent']}% → 灵石 {tier['gold']:,} + 修为 {tier['exp']:,}")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 服用新丹药即自动收录（/服用丹药 <名>），查看图鉴自动发放已达档位")
        if data["collected"]:
            show = "、".join(data["collected"][:12])
            more = f" 等{len(data['collected'])}种" if len(data["collected"]) > 12 else ""
            lines.append(f"📖 已收录：{show}{more}")
        return "\n".join(lines)

    async def view(self, player: Player) -> str:
        """文本视图 + 自动发放已达档位，返回合并文本"""
        data = await self.view_data(player)
        text = self.format_view(data)
        try:
            claim_msg = await self._claim_reached_tiers(player, data["pct"])
        except Exception as e:
            logger.warning(f"【丹药图鉴】档位发放失败: {e}")
            claim_msg = ""
        if claim_msg:
            text = claim_msg + "\n" + text
        return text
