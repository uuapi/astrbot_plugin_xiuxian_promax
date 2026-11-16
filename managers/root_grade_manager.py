# managers/root_grade_manager.py
"""灵根品阶系统（v3.52.0 新玩法）：灵根在五行属性之上叠加品阶

- 五阶：凡品(×1.0) → 黄品(×1.1) → 玄品(×1.2) → 地品(×1.35) → 天品(×1.5)
- 品阶影响闭关修炼速度（乘法叠加在灵根倍率之上）
- /洗髓灵根 消耗灵石洗髓，有成功率，失败不降级
- 品阶存 permanent_bonus["root_grade"] (0-4)，跨周目保留
"""

import json
import random
from typing import Tuple


from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["RootGradeManager"]


class RootGradeManager:
    """灵根品阶：天道不公，洗髓逆天"""

    GRADES = ["凡品", "黄品", "玄品", "地品", "天品"]
    MULTIPLIERS = [1.0, 1.1, 1.2, 1.35, 1.5]
    SUCCESS_RATES = [1.0, 0.80, 0.60, 0.40]  # 凡→黄 100%, 黄→玄 80%, 玄→地 60%, 地→天 40%
    COST_BASE = 50000  # 基础费用，每阶 ×2

    def __init__(self, db: DataBase):
        self.db = db

    def _get_grade(self, player: Player) -> int:
        """读取当前灵根品阶 (0-4)"""
        try:
            data = json.loads(player.permanent_bonus) if player.permanent_bonus else {}
            return min(4, max(0, int(data.get("root_grade", 0))))
        except Exception:
            return 0

    def _get_multiplier(self, grade: int) -> float:
        return self.MULTIPLIERS[min(4, grade)]

    def _cost(self, grade: int) -> int:
        """升到 grade+1 需要的灵石"""
        return self.COST_BASE * (2 ** grade)

    async def get_panel(self, player: Player) -> str:
        grade = self._get_grade(player)
        mult = self._get_multiplier(grade)
        root_name = player.spiritual_root or "未知"

        lines = [
            "🧬【灵根品阶】",
            "━━━━━━━━━━━━━━━",
            f"🌿 灵根：{root_name}",
            f"⭐ 品阶：{self.GRADES[grade]}（修炼速度 ×{mult}）",
            "━━━━━━━━━━━━━━━",
        ]

        if grade >= 4:
            lines.append("✅ 灵根品阶已臻天品之境，无法再进一步。")
        else:
            cost = self._cost(grade)
            rate = int(self.SUCCESS_RATES[grade] * 100)
            lines.append(f"⏭️ 下一阶：{self.GRADES[grade + 1]}（修炼速度 ×{self.MULTIPLIERS[grade + 1]}）")
            lines.append(f"💰 洗髓费用：{cost:,} 灵石 · 成功率：{rate}%")
            lines.append("💡 /洗髓灵根 升级 消耗灵石尝试提升品阶")
            lines.append("💡 洗髓失败不降级，但灵石不退——天道考验，需有准备")

        # 全链倍率预览
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📊 品阶倍率表：")
        for i, (g, m) in enumerate(zip(self.GRADES, self.MULTIPLIERS)):
            mark = "→" if i == grade else "  "
            lines.append(f"  {mark} {g}：×{m}")
        return "\n".join(lines)

    async def upgrade(self, player: Player) -> Tuple[bool, str]:
        """洗髓升级灵根品阶"""
        grade = self._get_grade(player)
        if grade >= 4:
            return False, "✅ 灵根品阶已臻天品之境，无法再进一步。"

        cost = self._cost(grade)
        if player.gold < cost:
            return False, f"❌ 洗髓费用不足！需要 {cost:,} 灵石（你持有 {player.gold:,}）。"

        # 条件原子扣灵石
        cursor = await self.db.conn.execute(
            "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
            (cost, player.user_id, cost)
        )
        await self.db.conn.commit()
        if cursor.rowcount == 0:
            return False, f"❌ 洗髓费用不足！需要 {cost:,} 灵石。"
        player.gold -= cost

        rate = self.SUCCESS_RATES[grade]
        if random.random() < rate:
            # 成功 → 品阶 +1
            try:
                data = json.loads(player.permanent_bonus) if player.permanent_bonus else {}
            except Exception:
                data = {}
            data["root_grade"] = grade + 1
            player.permanent_bonus = json.dumps(data, ensure_ascii=False)
            await self.db.update_player_fields(player.user_id, permanent_bonus=player.permanent_bonus)

            return True, (
                f"🧬【洗髓成功】！\n"
                f"━━━━━━━━━━━━━━━\n"
                f"灵根品阶从 {self.GRADES[grade]} 升至 {self.GRADES[grade + 1]}！\n"
                f"⚡ 修炼速度倍率：×{self._get_multiplier(grade)} → ×{self._get_multiplier(grade + 1)}\n"
                f"💰 消耗 {cost:,} 灵石\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💡 洗髓效果在下次 /闭关 /出关 时自动生效"
            )
        else:
            return False, (
                f"🧬【洗髓失败】……\n"
                f"━━━━━━━━━━━━━━━\n"
                f"灵根品阶维持 {self.GRADES[grade]} 不变（不降级）。\n"
                f"💰 消耗 {cost:,} 灵石（天道考验，不退）。\n"
                f"💡 成功率 {int(rate * 100)}%，多试几次总会成功的"
            )
