# tests/test_pill_codex.py
"""v3.53.0 丹药图鉴回归测试：收录/幂等/档位自动发放/防双领/use_pill 集成"""
import json
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

from astrbot_plugin_xiuxian_promax.models import Player  # noqa: E402
from astrbot_plugin_xiuxian_promax.data.data_manager import DataBase  # noqa: E402
from astrbot_plugin_xiuxian_promax.data.migration import MigrationManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.config_manager import ConfigManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.core.pill_manager import PillManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.pill_codex_manager import PillCodexManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.handlers.pill_handler import PillHandler  # noqa: E402


class FakeEvent:
    """最小事件替身（含 image_result 支持）"""

    def __init__(self, uid):
        self._uid = uid
        self.replies = []
        self.message_obj = types.SimpleNamespace(message_str="", message=[])

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return "图鉴道友"

    def get_group_id(self):
        return "12345"

    def get_message_str(self):
        return ""

    def plain_result(self, text):
        self.replies.append(str(text))
        return text

    def image_result(self, path):
        self.replies.append(f"IMAGE:{path}")
        return f"IMAGE:{path}"


async def drain(gen):
    return [r async for r in gen]


class PillCodexTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "codex.db"))
        await self.db.connect()
        self.cm = ConfigManager(ROOT)
        await MigrationManager(self.db.conn, self.cm).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.codex = PillCodexManager(self.db, {}, self.cm)
        self.pills = PillManager(self.db, self.cm)

    async def asyncTearDown(self):
        try:
            await self.db.close()
        except Exception:
            pass
        self._tmp.cleanup()

    async def new_player(self, uid, gold=0, **kw):
        p = Player(user_id=uid, user_name=f"道友{uid[-3:]}", gold=gold, **kw)
        await self.db.create_player(p)
        return p

    async def test_catalog_matches_config(self):
        catalog = self.codex.catalog()
        expect = {n for n in self.cm.get_all_pill_names() if n}
        self.assertTrue(catalog, "丹药目录不得为空")
        self.assertEqual(set(catalog), expect, "目录必须与四源丹药配置一致")

    async def test_record_idempotent_and_persistent(self):
        p = await self.new_player("cx_1")
        self.assertTrue(await self.codex.record(p, "测试丹A"))
        self.assertFalse(await self.codex.record(p, "测试丹A"), "重复收录必须返回 False")
        taken = json.loads(await self.db.ext.get_system_config("pillcodex_cx_1"))
        self.assertEqual(taken, ["测试丹A"], "档案必须持久化")

    async def test_tier_auto_grant_once(self):
        self.codex._catalog_override = ["丹A", "丹B", "丹C", "丹D"]
        self.codex.DEFAULT_TIERS.clear()
        self.codex.DEFAULT_TIERS.append({"percent": 100, "gold": 500, "exp": 1000})
        try:
            p = await self.new_player("cx_2", gold=0)
            for name in ("丹A", "丹B", "丹C", "丹D"):
                await self.codex.record(p, name)
            text = await self.codex.view(p)
            self.assertIn("100% 档达成", text)
            fresh = await self.db.get_player_by_id("cx_2")
            self.assertEqual(fresh.gold, 500, "100% 档灵石奖励必须发放")
            self.assertEqual(fresh.experience, 1000, "100% 档修为奖励必须发放")
            # 再次查看：不得双发
            await self.codex.view(p)
            fresh = await self.db.get_player_by_id("cx_2")
            self.assertEqual(fresh.gold, 500, "档位奖励必须幂等（防双领）")
        finally:
            self.codex.DEFAULT_TIERS.clear()
            self.codex.DEFAULT_TIERS.extend(PillCodexManager.DEFAULT_TIERS)

    async def test_tier_not_reached_no_grant(self):
        self.codex._catalog_override = ["丹A", "丹B", "丹C", "丹D"]
        p = await self.new_player("cx_3", gold=0)
        await self.codex.record(p, "丹A")
        await self.codex.view(p)
        fresh = await self.db.get_player_by_id("cx_3")
        self.assertEqual(fresh.gold, 0, "25% 未达不得发放任何档位")

    async def test_use_pill_records_codex(self):
        names = [n for n, c in self.cm.exp_pills_data.items() if isinstance(c, dict)]
        self.assertTrue(names, "需要修为丹样本")
        name = names[0]
        self.pills.codex_mgr = self.codex
        p = await self.new_player("cx_4")
        await self.pills.add_pill_to_inventory(p, name, 1)
        p2 = await self.db.get_player_by_id("cx_4")
        ok, msg = await self.pills.use_pill(p2, name)
        self.assertTrue(ok, msg[:60])
        taken = json.loads(await self.db.ext.get_system_config("pillcodex_cx_4"))
        self.assertIn(name, taken, "服用的丹药必须自动收录")

    async def test_handler_pill_codex_command(self):
        handler = PillHandler(self.db, self.cm)
        handler.pill_manager.codex_mgr = self.codex
        self.codex._catalog_override = ["丹A", "丹B"]
        p = await self.new_player("cx_5")
        await self.codex.record(p, "丹A")
        ev = FakeEvent("cx_5")
        outs = await drain(handler.handle_pill_codex(ev))
        self.assertTrue(outs, "图鉴指令必须有产出")
        joined = "\n".join(str(o) for o in outs)
        # 环境有 Pillow 时产出图片卡片（IMAGE: 前缀），否则降级文本含收集进度
        self.assertTrue(
            joined.startswith("IMAGE:") or "1/2" in joined,
            f"回复必须是图片卡片或含收集进度的文本: {joined[:120]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
