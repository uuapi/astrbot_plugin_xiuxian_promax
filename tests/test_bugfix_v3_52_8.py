# tests/test_bugfix_v3_52_8.py
"""v3.52.8 第二轮加固回归测试

- R2-1 loot_auction.bid：净额单语句托管 + CAS 失败成对回滚（无窄窗增发/短少）
- R2-2 赠予链路：发送"扣物→建单"原子化；过期清理先占行后返还（不复制不双退）
- R2-5 dao_bet 结算：奖金断点续发——崩溃遗留态自动补发，且不双发
- R2-6 每日限丹机制落地：daily_limit 达限拒服 + 跨日重置
- R2-7 过期红包坑位键清理
"""
import json
import os
import sys
import tempfile
import time
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
from astrbot_plugin_xiuxian_promax.core.storage_ring_manager import StorageRingManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.core.pill_manager import PillManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.loot_auction_manager import LootAuctionManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.dao_bet_manager import DaoBetManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.handlers.storage_ring_handler import StorageRingHandler  # noqa: E402

from test_bugfix_v3_52_7 import FakeEvent, drain  # noqa: E402  复用同款替身


class BugFixV3528(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "bugfix8.db"))
        await self.db.connect()
        self.cm = ConfigManager(ROOT)
        await MigrationManager(self.db.conn, self.cm).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.ring = StorageRingManager(self.db, self.cm)
        self.loot = LootAuctionManager(self.db)
        self.daobet = DaoBetManager(self.db)
        self.pills = PillManager(self.db, self.cm)
        self.sr_handler = StorageRingHandler(self.db, self.cm)

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

    # ---------- R2-1 拍卖净额托管 ----------

    async def test_r2_loot_bid_escrow_exact(self):
        await self.new_player("lb_1", gold=10000)
        await self.loot.open_auction("测试魔龙", 1)
        p = await self.db.get_player_by_id("lb_1")
        ok, msg = await self.loot.bid(p, 3000)
        self.assertTrue(ok, msg)
        fresh = await self.db.get_player_by_id("lb_1")
        self.assertEqual(fresh.gold, 7000, "首次出价必须全额托管")
        # 加价：退旧扣新净额结算
        ok, msg = await self.loot.bid(fresh, 5000)
        self.assertTrue(ok, msg)
        fresh = await self.db.get_player_by_id("lb_1")
        self.assertEqual(fresh.gold, 5000, "加价后应恰为新托管额（净额结算）")
        state = json.loads(await self.db.ext.get_system_config("loot_auction_state"))
        self.assertEqual(state["bids"]["lb_1"]["amount"], 5000)

    async def test_r2_loot_bid_insufficient_no_partial(self):
        await self.new_player("lb_2", gold=500)
        await self.loot.open_auction("测试魔龙", 1)
        p = await self.db.get_player_by_id("lb_2")
        ok, _ = await self.loot.bid(p, 3000)
        self.assertFalse(ok, "余额不足必须失败")
        fresh = await self.db.get_player_by_id("lb_2")
        self.assertEqual(fresh.gold, 500, "失败出价不得扣款")
        state = json.loads(await self.db.ext.get_system_config("loot_auction_state"))
        self.assertEqual(state.get("bids") or {}, {}, "失败出价不得入注单")

    # ---------- R2-2 赠予原子化与过期清理 ----------

    async def test_r2_gift_send_atomic(self):
        # 注入 At/Plain 替身（handler 从消息链解析赠予目标与文本）
        from astrbot_plugin_xiuxian_promax.handlers import storage_ring_handler as _srh

        class _FakeAt:
            def __init__(self, qq):
                self.qq = qq

        class _FakePlain:
            def __init__(self, text):
                self.text = text

        _srh.At, _srh.Plain = _FakeAt, _FakePlain
        await self.new_player("gs_s", gold=0)
        await self.new_player("gs_r", gold=0)
        sender = await self.db.get_player_by_id("gs_s")
        await self.ring.store_item(sender, "测试矿", 3, silent=True)
        sender = await self.db.get_player_by_id("gs_s")
        ev = FakeEvent("gs_s")
        ev.message_obj.message = [_FakeAt("gs_r"), _FakePlain("测试矿 2")]
        await drain(self.sr_handler.handle_gift_item(ev, ""))
        fresh = await self.db.get_player_by_id("gs_s")
        items = json.loads(fresh.storage_ring_items)
        self.assertEqual(items.get("测试矿", 0), 1, "扣物后应剩 1")
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM pending_gifts WHERE receiver_id='gs_r'")
        self.assertEqual((await cur.fetchone())[0], 1, "赠予单必须已建立（扣物与建单同事务）")

    async def test_r2_gift_expire_cleanup_once(self):
        await self.new_player("ge_s", gold=0)
        sender = await self.db.get_player_by_id("ge_s")
        await self.ring.store_item(sender, "测试矿", 5, silent=True)
        sender = await self.db.get_player_by_id("ge_s")
        ok, _ = await self.ring.retrieve_item(sender, "测试矿", 2)
        self.assertTrue(ok)
        # 手工插入一条已过期赠予
        now = int(time.time())
        await self.db.conn.execute(
            "INSERT INTO pending_gifts (receiver_id, sender_id, sender_name, item_name, count, created_at, expires_at) "
            "VALUES ('ge_r', 'ge_s', '送出者', '测试矿', 2, ?, ?)",
            (now - 100, now - 50))
        await self.db.conn.commit()

        await self.db.ext.cleanup_expired_gifts()
        fresh = await self.db.get_player_by_id("ge_s")
        self.assertEqual(json.loads(fresh.storage_ring_items).get("测试矿", 0), 5, "过期应返还 2（5=3+2）")
        # 第二次清理：不得双退
        await self.db.ext.cleanup_expired_gifts()
        fresh = await self.db.get_player_by_id("ge_s")
        self.assertEqual(json.loads(fresh.storage_ring_items).get("测试矿", 0), 5, "清理必须幂等，不得双退")
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM pending_gifts")
        self.assertEqual((await cur.fetchone())[0], 0)

    # ---------- R2-5 天道盘结算断点续发 ----------

    async def _make_settled_state(self, date_str):
        state = {
            "date": date_str, "red_pool": 1000, "blue_pool": 0, "bets": {},
            "settled": True,
            "win_side": "red", "win_pool": 1000, "prize_pool": 1800,
            "pending_payouts": {"w1": {"amount": 1000, "name": "甲"}},
            "paid": {},
        }
        await self.db.ext.set_system_config(f"daopan_{date_str}", json.dumps(state, ensure_ascii=False))

    async def test_r2_daobet_recovery_pays_pending(self):
        await self.new_player("w1", gold=0)
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        await self._make_settled_state(yesterday)
        # 模拟崩溃遗留：settled=True 但奖金未发 → 调用补发
        msg = await self.daobet._settle_if_due()
        fresh = await self.db.get_player_by_id("w1")
        self.assertEqual(fresh.gold, 1800, f"遗留奖金必须补发（payout=1800）：{msg[:80]}")
        # 再跑一次：不得双发
        await self.daobet._settle_if_due()
        fresh = await self.db.get_player_by_id("w1")
        self.assertEqual(fresh.gold, 1800, "补发必须幂等")

    async def test_r2_daobet_no_leak_of_stale_keys(self):
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        await self._make_settled_state(yesterday)
        await self.daobet._settle_if_due()
        # 新结算完成且无 pending → 3 天内保留
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM system_config WHERE key LIKE 'daopan_%'")
        self.assertGreaterEqual((await cur.fetchone())[0], 1, "近期盘口键应保留")
        # 构造 5 天前的已结清盘 → 应被清理
        old = time.strftime("%Y-%m-%d", time.localtime(time.time() - 5 * 86400))
        await self._make_settled_state(old)
        await self.daobet._settle_if_due()
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM system_config WHERE key = ?", (f"daopan_{old}",))
        self.assertEqual((await cur.fetchone())[0], 0, "3 天前已结清的盘口键必须被清理")

    # ---------- R2-6 每日限丹机制 ----------

    async def test_r2_daily_pill_limit_and_reset(self):
        # 注入一枚带 daily_limit 的测试丹（配置层无此字段，机制由配置激活）
        self.cm.utility_pills_data["测试限用丹"] = {
            "id": "qa_limit", "name": "测试限用丹", "rank": "凡品",
            "subtype": "qi_restore", "effect_type": "instant",
            "spiritual_qi_restore": 10, "price": 10, "daily_limit": 1,
        }
        p = await self.new_player("dl_u", gold=0)
        await self.pills.add_pill_to_inventory(p, "测试限用丹", 2)
        r1 = await self.pills.use_pill(await self.db.get_player_by_id("dl_u"), "测试限用丹")
        self.assertTrue(r1[0], f"第一次服用应成功: {r1[1][:60]}")
        r2 = await self.pills.use_pill(await self.db.get_player_by_id("dl_u"), "测试限用丹")
        self.assertFalse(r2[0], "同日第二次服用必须被拒（daily_limit=1）")
        self.assertIn("上限", r2[1])
        # 跨日重置：把 last_daily_reset 改成昨天 → 恢复可服
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        await self.db.update_player_fields("dl_u", last_daily_reset=yesterday)
        r3 = await self.pills.use_pill(await self.db.get_player_by_id("dl_u"), "测试限用丹")
        self.assertTrue(r3[0], "跨日后计数重置，应可再服")

    # ---------- R2-7 过期红包坑位键清理 ----------

    async def test_r2_redpacket_slot_keys_purged(self):
        sender = await self.new_player("rp_s", gold=5000)
        ok, msg = await self.rp_ref.send(sender, 200, 2)
        self.assertTrue(ok)
        import re as _re
        pid = _re.search(r"红包编号：\s*([0-9a-f]+)", msg).group(1)
        # 把红包改为过期态后触发清理
        state = json.loads(await self.db.ext.get_system_config(f"redpacket_{pid}"))
        state["expire_ts"] = int(time.time()) - 10
        await self.db.ext.set_system_config(f"redpacket_{pid}", json.dumps(state, ensure_ascii=False))
        grabber = await self.db.get_player_by_id("rp_s_grabber") if await self.db.get_player_by_id("rp_s_grabber") \
            else await self.new_player("rp_s_grabber", gold=0)
        await self.rp_ref.grab(grabber, pid)  # 触发惰性过期退款 + 清理
        cur = await self.db.conn.execute(
            "SELECT COUNT(*) FROM system_config WHERE key LIKE ?", (f"rp_{pid}_%",))
        self.assertEqual((await cur.fetchone())[0], 0, "过期红包的份额占坑键必须清理")

    async def asyncSetUpExtra(self):
        pass

    @property
    def rp_ref(self):
        if not hasattr(self, "_rp"):
            from astrbot_plugin_xiuxian_promax.managers.redpacket_manager import RedPacketManager
            self._rp = RedPacketManager(self.db)
        return self._rp


if __name__ == "__main__":
    unittest.main(verbosity=2)
