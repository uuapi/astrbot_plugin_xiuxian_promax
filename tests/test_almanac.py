# tests/test_almanac.py
"""v3.54.0 仙界黄历回归测试：生成/幂等/CAS 并发首掷/乘数/永不抛异常/广播一次性"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
PKG = ROOT.name

# stub astrbot（与 test_atomic_primitives.py 同款约定）
_astrbot = types.ModuleType("astrbot")
_api = types.ModuleType("astrbot.api")
_api.logger = types.SimpleNamespace(
    info=lambda *a: None, warning=lambda *a: None,
    error=lambda *a: None, exception=lambda *a: None,
)
_api.AstrBotConfig = dict
_event = types.ModuleType("astrbot.api.event")
_event.AstrMessageEvent = object
_api.event = _event
_all = types.ModuleType("astrbot.api.all")
_all.At = object
_all.Plain = object
_api.all = _all
sys.modules["astrbot"] = _astrbot
sys.modules["astrbot.api"] = _api
sys.modules["astrbot.api.event"] = _event
sys.modules["astrbot.api.all"] = _all

from astrbot_plugin_xiuxian_promax.data.data_manager import DataBase  # noqa: E402
from astrbot_plugin_xiuxian_promax.data.migration import MigrationManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.config_manager import ConfigManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.almanac_manager import AlmanacManager  # noqa: E402


class AlmanacTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "almanac.db"))
        await self.db.connect()
        await MigrationManager(self.db.conn, ConfigManager(ROOT)).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.alm = AlmanacManager(self.db)

    async def asyncTearDown(self):
        try:
            await self.db.close()
        except Exception:
            pass
        self._tmp.cleanup()

    async def test_generation_shape(self):
        data = await self.alm.ensure_today()
        self.assertEqual(data.get("date"), self.alm._today_str())
        self.assertEqual(len(data.get("fortune", [])), AlmanacManager.FORTUNE_COUNT)
        self.assertEqual(len(data.get("avoid", [])), AlmanacManager.AVOID_COUNT)
        fortune, avoid = set(data["fortune"]), set(data["avoid"])
        self.assertFalse(fortune & avoid, "宜忌场景不得重复")
        for s in fortune:
            mult = data["effects"][s]
            expect = 1.05 if s == "breakthrough" else 1.10
            self.assertEqual(mult, expect, f"宜场景 {s} 倍率应为 {expect}")
        for s in avoid:
            mult = data["effects"][s]
            expect = 0.95 if s == "breakthrough" else 0.90
            self.assertEqual(mult, expect, f"忌场景 {s} 倍率应为 {expect}")
        self.assertTrue(data.get("god"))
        self.assertTrue(data.get("direction"))

    async def test_same_day_idempotent(self):
        first = await self.alm.ensure_today()
        second = await self.alm.ensure_today()
        self.assertEqual(first, second, "同日两次获取必须完全一致")

    async def test_concurrent_first_roll_single_generation(self):
        results = await asyncio.gather(*[self.alm.ensure_today() for _ in range(5)])
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r, first, "并发首掷只能产生一份黄历（CAS 抢占）")

    async def test_get_multiplier_and_resilience(self):
        data = await self.alm.ensure_today()
        for scene, val in data["effects"].items():
            self.assertEqual(await self.alm.get_multiplier(scene), val)
        neutral = [s for s in AlmanacManager.SCENES if s not in data["effects"]]
        for scene in neutral:
            self.assertEqual(await self.alm.get_multiplier(scene), 1.0)
        self.assertEqual(await self.alm.get_multiplier("未知场景"), 1.0)
        # 腐化数据：永不抛异常
        await self.db.ext.set_system_config(self.alm._today_key(), "@@@corrupted@@@")
        self.assertEqual(await self.alm.get_multiplier("cultivate"), 1.0)

    async def test_broadcast_once(self):
        text1 = await self.alm.ensure_daily_broadcast()
        self.assertIsNotNone(text1, "首日首播必须返回文本")
        self.assertIn("仙界黄历", text1)
        self.assertIsNone(await self.alm.ensure_daily_broadcast(), "当日第二次播报必须为空（幂等）")

    async def test_text_contains_panels(self):
        text = await self.alm.get_almanac_text()
        self.assertIn("宜：", text)
        self.assertIn("值神：", text)
        self.assertIn("吉方：", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
