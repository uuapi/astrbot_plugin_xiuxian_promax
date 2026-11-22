# managers/almanac_manager.py
"""仙界黄历 v3.54.0 - 每日全服宜忌，影响闭关/历练/奇遇/突破收益

与天象（weather_manager）同模式的全服日状态机：
- 状态存 system_config：almanac_{YYYYMMDD} = JSON（带日期后缀，由
  purge_dated_config_keys 自动清理，不泄漏）
- 跨天自动重掷：syscfg_cas 抢占生成权（仅空位可占），并发首掷只产生一份
- 核心接口 get_multiplier(scene) 无副作用、永不抛异常，供结算点接入：
  宜事收益 ×1.10，忌事收益 ×0.90；突破为成功率微调 ±5%
- 每日首次生成后由主控聚合循环广播（almanac_bc_{date} 一次性日键）
"""
import json
import random
import time
from typing import Optional

from ..data.data_manager import DataBase

__all__ = ["AlmanacManager"]


class AlmanacManager:
    """仙界黄历：每日宜忌与收益乘数"""

    # 黄历生效场景（与天象 scene 键对齐，接入点复用天象的结算位置）
    SCENES = ["cultivate", "adventure", "encounter", "breakthrough", "farm"]
    SCENE_NAMES = {
        "cultivate": "闭关修炼",
        "adventure": "出外历练",
        "encounter": "奇遇触发",
        "breakthrough": "突破成功率",
        "farm": "灵田产出",
    }

    # 宜/忌倍率；突破是成功率场景，效果减半（±5%）避免大起大落
    BONUS = 0.10
    PENALTY = 0.10
    FORTUNE_COUNT = 2   # 每日宜数
    AVOID_COUNT = 1     # 每日忌数

    GODS = ["青龙", "明堂", "金匮", "天德", "玉堂", "司命", "宝光", "玉宇"]
    DIRECTIONS = ["东方", "南方", "西方", "北方", "中央", "东南", "西北"]
    FORTUNE_WORDS = ["诸事可为", "大势顺遂", "天道垂青", "吉星拱照", "灵机盎然"]
    AVOID_WORDS = ["诸事谨慎", "气机晦暗", "天威难测", "煞气当值", "宜静不宜动"]

    def __init__(self, db: DataBase):
        self.db = db

    # ================= 内部工具 =================

    def _today_key(self) -> str:
        return f"almanac_{time.strftime('%Y%m%d')}"

    def _today_str(self) -> str:
        return time.strftime("%Y%m%d")

    def _generate(self) -> dict:
        """重掷今日黄历：随机 2 宜 + 1 忌（互不重复），其余场景中性"""
        scenes = self.SCENES[:]
        random.shuffle(scenes)
        fortune = scenes[:self.FORTUNE_COUNT]
        avoid = scenes[self.FORTUNE_COUNT:self.FORTUNE_COUNT + self.AVOID_COUNT]

        effects = {}
        for s in fortune:
            mult = 1.0 + (self.BONUS / 2 if s == "breakthrough" else self.BONUS)
            effects[s] = round(mult, 2)
        for s in avoid:
            mult = 1.0 - (self.PENALTY / 2 if s == "breakthrough" else self.PENALTY)
            effects[s] = round(mult, 2)

        return {
            "fortune": fortune,
            "avoid": avoid,
            "effects": effects,
            "god": random.choice(self.GODS),
            "direction": random.choice(self.DIRECTIONS),
            "fortune_word": random.choice(self.FORTUNE_WORDS),
            "avoid_word": random.choice(self.AVOID_WORDS),
            "date": self._today_str(),
        }

    # ================= 核心接口 =================

    async def ensure_today(self) -> dict:
        """读取今日黄历，无则重掷并写入（CAS 抢占，跨天自动重生成）。"""
        key = self._today_key()
        today_str = self._today_str()
        try:
            raw = await self.db.ext.get_system_config(key)
        except Exception:
            raw = None
        try:
            today = json.loads(raw) if raw else None
        except Exception:
            today = None
        if isinstance(today, dict) and today.get("date") == today_str:
            return today

        new = self._generate()
        try:
            # 仅空位可占：并发首掷只产生一份黄历
            if await self.db.ext.syscfg_cas(key, json.dumps(new, ensure_ascii=False), None):
                return new
        except Exception:
            pass
        # 他人已生成：读取现值
        try:
            raw = await self.db.ext.get_system_config(key)
            existing = json.loads(raw) if raw else None
            if isinstance(existing, dict) and existing.get("date") == today_str:
                return existing
        except Exception:
            existing = None
        # v3.55.1 自愈：现值腐化（或缺失）时强制重建——否则 get_multiplier 恒 1.0，
        # 界面显示"宜+10%"而结算无加成（显示与结算脱钩）
        try:
            await self.db.ext.set_system_config(key, json.dumps(new, ensure_ascii=False))
        except Exception:
            pass
        return new

    async def get_multiplier(self, scene: str) -> float:
        """今日黄历对指定场景的收益倍率（1.0 为无效果）。
        无副作用、永不抛异常，供闭关/历练/奇遇/突破等系统接入。"""
        try:
            raw = await self.db.ext.get_system_config(self._today_key())
            today = json.loads(raw) if raw else None
            if not isinstance(today, dict) or today.get("date") != self._today_str():
                return 1.0
            effects = today.get("effects") or {}
            val = effects.get(scene, 1.0)
            return float(val) if isinstance(val, (int, float)) else 1.0
        except Exception:
            return 1.0

    async def ensure_daily_broadcast(self) -> Optional[str]:
        """每日首次生成后返回播报文本（日键一次性，主控聚合循环调用）"""
        data = await self.ensure_today()
        bkey = f"almanac_bc_{data.get('date')}"
        if await self.db.ext.get_system_config(bkey):
            return None
        if not await self.db.ext.syscfg_cas(bkey, "1", None):
            return None
        return await self.get_almanac_text()

    # ================= 展示 =================

    async def get_almanac_text(self) -> str:
        """黄历播报文本（用于广播/查询）"""
        try:
            data = await self.ensure_today()
        except Exception:
            return "📜 黄历蒙尘，暂时无法推演。"

        effects = data.get("effects") or {}
        fortune_names, avoid_lines = [], []
        for scene, val in effects.items():
            if not isinstance(val, (int, float)):
                continue  # v3.55.1：与 get_multiplier 同款类型防御，腐化值跳过
            name = self.SCENE_NAMES.get(scene, scene)
            pct = (float(val) - 1.0) * 100
            if pct > 0:
                fortune_names.append(f"{name}（+{pct:.0f}%）")
            else:
                avoid_lines.append(f"{name}（{pct:.0f}%）")

        lines = [
            f"📜【仙界黄历 · {data.get('date', '')}】",
            "━━━━━━━━━━━━━━━",
            f"值神：{data.get('god', '天道')}当值｜吉方：{data.get('direction', '四方')}",
            f"势：{data.get('fortune_word', '诸事可为')}",
        ]
        lines.append("宜：" + "、".join(fortune_names) if fortune_names else "宜：百无禁忌")
        if avoid_lines:
            lines.append("忌：" + "、".join(avoid_lines) + f"（{data.get('avoid_word', '诸事谨慎')}）")
        else:
            lines.append("忌：无")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 黄历每日更迭，全服同览。宜事收益乘 1.1，忌事打九折；突破场景减半（±5%）。")
        return "\n".join(lines)

    async def handle_almanac(self, event):
        """/仙界黄历 指令：输出今日宜忌。"""
        text = await self.get_almanac_text()
        yield event.plain_result(text)
