# tests/test_bugfix_v3_52_9.py
"""v3.52.9 写门闸（Write Gate）回归测试

核心语义：共享单连接上，协程 A 的显式事务（begin_immediate）开启期间，
协程 B 的写语句必须在门闸排队——A 回滚时绝不能吞掉 B 已执行完毕的写。

修复前：B 的写落入 A 的打开事务，随 A 的 rollback 一并消失（事务裹挟）。
修复后：B 等待 A 提交/回滚后再独立 autocommit 执行。
"""
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

from astrbot_plugin_xiuxian_promax.models import Player  # noqa: E402
from astrbot_plugin_xiuxian_promax.data import data_manager as _dm  # noqa: E402
from astrbot_plugin_xiuxian_promax.data.data_manager import DataBase  # noqa: E402
from astrbot_plugin_xiuxian_promax.data.migration import MigrationManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.config_manager import ConfigManager  # noqa: E402


class WriteGateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "gate.db"))
        await self.db.connect()
        await MigrationManager(self.db.conn, ConfigManager(ROOT)).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        p = Player(user_id="gate_u", user_name="门闸道友", gold=1000)
        await self.db.create_player(p)

    async def asyncTearDown(self):
        try:
            await self.db.close()
        except Exception:
            pass
        self._tmp.cleanup()

    async def test_gate_foreign_write_survives_rollback(self):
        """【核心】A 事务回滚不得吞掉 B 的 autocommit 写（修复前会吞掉）"""
        async def task_a():
            await self.db.begin_immediate()
            await self.db.conn.execute("UPDATE players SET gold = gold - 500 WHERE user_id='gate_u'")
            # 让出控制权：B 的写此时尝试执行，必须在门闸排队而非落入本事务
            await asyncio.sleep(0.15)
            await self.db.conn.rollback()  # A 回滚自己的 -500

        async def task_b():
            await asyncio.sleep(0.03)  # 确保 A 的事务已开启
            await self.db.conn.execute("UPDATE players SET gold = gold + 1 WHERE user_id='gate_u'")
            await self.db.conn.commit()

        await asyncio.gather(task_a(), task_b())
        fresh = await self.db.get_player_by_id("gate_u")
        self.assertEqual(fresh.gold, 1001,
                         f"A 的 -500 必须回滚、B 的 +1 必须生效（修复前 B 被裹挟回滚）: {fresh.gold}")

    async def test_gate_owner_writes_commit_together(self):
        """持有者事务内多次写随 commit 一体生效"""
        async def task_a():
            await self.db.begin_immediate()
            await self.db.conn.execute("UPDATE players SET gold = gold + 100 WHERE user_id='gate_u'")
            await self.db.conn.execute("UPDATE players SET gold = gold + 50 WHERE user_id='gate_u'")
            await self.db.conn.commit()

        await task_a()
        fresh = await self.db.get_player_by_id("gate_u")
        self.assertEqual(fresh.gold, 1150, "事务内两笔写应一体提交")

    async def test_gate_rollback_discards_owner_writes(self):
        """持有者 rollback 丢弃事务内全部写（基础语义不回退）"""
        await self.db.begin_immediate()
        await self.db.conn.execute("UPDATE players SET gold = gold + 777 WHERE user_id='gate_u'")
        await self.db.conn.rollback()
        fresh = await self.db.get_player_by_id("gate_u")
        self.assertEqual(fresh.gold, 1000, "回滚后不得有残留")

    async def test_gate_timeout_fails_fast(self):
        """长事务不提交：其他协程的写有等待上限（测试用 0.3s 缩短）快速失败，而非永久挂死"""
        self._patched = _dm._GATE_WAIT_TIMEOUT
        _dm._GATE_WAIT_TIMEOUT = 0.3
        try:
            await self.db.begin_immediate()
            await self.db.conn.execute("UPDATE players SET gold = gold + 1 WHERE user_id='gate_u'")

            foreign_err = []

            async def foreign_write():
                try:
                    await self.db.conn.execute(
                        "UPDATE players SET gold = gold + 1 WHERE user_id='gate_u'")
                    await self.db.conn.commit()
                except RuntimeError as e:
                    foreign_err.append(e)

            await asyncio.wait_for(asyncio.gather(
                foreign_write(), asyncio.sleep(0.6)), timeout=5)
            self.assertTrue(foreign_err, "外部协程的写必须超时快速失败（RuntimeError）")
            await self.db.conn.rollback()
        finally:
            _dm._GATE_WAIT_TIMEOUT = self._patched

    async def test_gate_concurrent_transactions_serialize(self):
        """两个协程先后 begin_immediate：串行执行而非相互回滚"""
        async def add_n(tag, n):
            await self.db.begin_immediate()
            await self.db.conn.execute(f"UPDATE players SET gold = gold + {n} WHERE user_id='gate_u'")
            await asyncio.sleep(0.02)
            await self.db.conn.commit()

        await asyncio.gather(add_n("A", 10), add_n("B", 20))
        fresh = await self.db.get_player_by_id("gate_u")
        self.assertEqual(fresh.gold, 1030, f"两笔事务都应完整提交（10+20）: {fresh.gold}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
