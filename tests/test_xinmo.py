# tests/test_xinmo.py
"""v3.55.0 心魔挑战回归测试：限次/复制体/胜败结算/称号/防双领/降魔榜"""
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
from astrbot_plugin_xiuxian_promax.managers.combat_manager import CombatStats  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.xinmo_manager import XinmoManager  # noqa: E402


def mkstats(uid="p1", name="道友甲", hp=5000, atk=200, element="水", effects=None,
            exp=10000, crit=0, defense=50):
    return CombatStats(
        user_id=uid, name=name, hp=hp, max_hp=hp, mp=100, max_mp=100,
        atk=atk, defense=defense, crit_rate=crit, exp=exp,
        effects=effects or {}, element=element)


class XinmoTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "xinmo.db"))
        await self.db.connect()
        await MigrationManager(self.db.conn, ConfigManager(ROOT)).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.mgr = XinmoManager(self.db)
        self.player = Player(user_id="xm_p", user_name="道友甲", gold=0, experience=10000, level_index=5)
        await self.db.create_player(self.player)

    async def asyncTearDown(self):
        try:
            await self.db.close()
        except Exception:
            pass
        self._tmp.cleanup()

    async def test_build_demon_mirror_and_counter(self):
        stats = mkstats(effects={"lifesteal": 10, "pet_damage_mult": 0.3, "crit_rate": 5})
        demon = self.mgr.build_demon_stats(stats, level_index=5, daoxin=0)
        self.assertEqual(demon.max_hp, int(5000 * (0.90 + 5 * 0.008)), "HP = 基线0.9 + 境界强化")
        self.assertEqual(demon.atk, int(200 * (0.85 + 5 * 0.006)), "ATK = 基线0.85 + 境界强化")
        self.assertEqual(demon.element, "水", "五行同源镜像")
        self.assertNotIn("pet_damage_mult", demon.effects, "心魔不能召唤玩家的灵兽")
        self.assertEqual(demon.effects.get("lifesteal"), 10, "功法词条镜像")
        self.assertEqual(demon.exp, 300 * 6, "修为奖励 = 基础×(境界+1)")

    async def test_daoxin_discount_caps(self):
        stats = mkstats()
        strong = self.mgr.build_demon_stats(stats, level_index=0, daoxin=0)
        weak = self.mgr.build_demon_stats(stats, level_index=0, daoxin=50)
        self.assertLess(weak.atk, strong.atk, "道心必须削弱心魔")
        # 上限 20%：50 点道心 → 折扣封顶 20%（atk 系数 floor 0.8）
        self.assertEqual(weak.atk, int(200 * 0.8), "道心折扣应封顶 20%")

    async def test_daily_limit(self):
        stats = mkstats(atk=999999, hp=999999)
        p = self.player
        ok1, msg1 = await self.mgr.challenge(p, stats)
        self.assertTrue(ok1, msg1[:80])
        ok2, msg2 = await self.mgr.challenge(p, stats)
        self.assertFalse(ok2, "每日 1 次，第二次必须被拒")
        self.assertIn("每日 1 次", msg2)

    async def test_win_grants_reward_title_and_wins(self):
        p = self.player
        stats = mkstats(uid="xm_p", atk=999999, hp=999999)
        ok, msg = await self.mgr.challenge(p, stats)
        self.assertTrue(ok)
        self.assertIn("心魔被斩落", msg)
        fresh = await self.db.get_player_by_id("xm_p")
        self.assertGreater(fresh.experience, 10000, "胜利必须获得修为")
        wins = await self.db.ext.syscfg_get_number("xinmo_wins_xm_p")
        self.assertEqual(wins, 1, "胜场计数 +1")
        self.assertIn("初降心魔", msg, "首次胜必须触发称号授予（condition=1）")

    async def test_lose_partial_reward_and_penalty(self):
        p = self.player
        # 弱面板：必败（心魔血量下限 10 > 我方 5 血，我方 1 攻需 10 回合，先死）
        stats = mkstats(uid="xm_p", atk=1, hp=5, exp=10000)
        ok, msg = await self.mgr.challenge(p, stats)
        self.assertTrue(ok, "受理成功（战败也是一次有效挑战）")
        self.assertIn("不敌心魔", msg)
        self.assertIn("-200", msg, "战败惩罚 = 10000×2%")
        fresh = await self.db.get_player_by_id("xm_p")
        self.assertEqual(fresh.experience, 9800, "战败只扣 2% 修为，无奖励（净损失）")

    async def test_board_ranking(self):
        other = Player(user_id="xm_q", user_name="道友乙", gold=0)
        await self.db.create_player(other)
        await self.db.ext.syscfg_adjust_number("xinmo_wins_xm_p", 3)
        await self.db.ext.syscfg_adjust_number("xinmo_wins_xm_q", 7)
        board = await self.mgr.get_board(self.player)
        self.assertIn("道友乙", board)
        self.assertIn("道友甲", board)
        idx_q = board.index("道友乙")
        idx_p = board.index("道友甲")
        self.assertLess(idx_q, idx_p, "7 胜者排名必须高于 3 胜者")
        self.assertIn("降魔 3 次", board, "应显示自己的战绩")


if __name__ == "__main__":
    unittest.main(verbosity=2)
