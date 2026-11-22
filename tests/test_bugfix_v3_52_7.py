# tests/test_bugfix_v3_52_7.py
"""v3.52.7 并发与资金安全加固回归测试

对应 README 更新日志 v3.52.7 修复的缺陷，逐项锁定行为不回退：
- H1/H2 坊市购买/下架物品必须落库（原 store_item(external) 只改内存 → 物品蒸发）
- H3 天道盘 CAS 重试不得循环退款（原 3 连败净赚 2 倍注额）
- H4 丹药并发服用不得白嫖（原快照整包写回丢更新）
- M1 红包并发抢占：份额不烧毁、恰好发完、同人不可双抢
- M3 赠予并发接收/拒绝：物品只交付一次，不复制
- M4 Boss 拍卖托管款并发开拍只退一次
- M6 银行逾期删号前必须复核贷款仍 active（防误杀已还款玩家）
- L1 config_manager 先导入不触发循环 ImportError
- L3 满级调用 calculate_breakthrough_success_rate 不 IndexError
- L4 储物戒 manager 层拒绝非正数量
"""
import asyncio
import json
import os
import subprocess
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
from astrbot_plugin_xiuxian_promax.core.breakthrough_manager import BreakthroughManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.redpacket_manager import RedPacketManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.auction_manager import AuctionManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.dao_bet_manager import DaoBetManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.loot_auction_manager import LootAuctionManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.managers.bank_manager import BankManager  # noqa: E402
from astrbot_plugin_xiuxian_promax.handlers.storage_ring_handler import StorageRingHandler  # noqa: E402


class FakeEvent:
    """最小可用的 AstrMessageEvent 替身（与 test_command_flows 同约定）"""

    def __init__(self, uid: str):
        self._uid = uid
        self.replies: list = []
        self.message_obj = types.SimpleNamespace(message_str="", message=[])

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._uid

    def get_group_id(self):
        return "12345"

    def get_message_str(self):
        return ""

    def plain_result(self, text):
        self.replies.append(text)
        return text


async def drain(gen):
    """消费异步生成器，收集全部产出"""
    return [r async for r in gen]


class BugFixV3527(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmp.name, "bugfix.db"))
        await self.db.connect()
        self.cm = ConfigManager(ROOT)
        await MigrationManager(self.db.conn, self.cm).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.ring = StorageRingManager(self.db, self.cm)
        self.rp = RedPacketManager(self.db)
        self.auction = AuctionManager(self.db, self.ring, self.cm)
        self.daobet = DaoBetManager(self.db)
        self.loot = LootAuctionManager(self.db)
        self.bank = BankManager(self.db, {})
        self.pills = PillManager(self.db, self.cm)
        self.bt = BreakthroughManager(self.db, self.cm, {})
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

    # ---------- H1 坊市购买落库 ----------

    async def test_h1_market_buy_delivers_item(self):
        await self.new_player("mk_s", gold=0)
        seller = await self.db.get_player_by_id("mk_s")
        await self.ring.store_item(seller, "测试丹材", 1, silent=True)
        seller = await self.db.get_player_by_id("mk_s")
        ok, _ = await self.auction.list_item(seller, "测试丹材", 1, 100)
        self.assertTrue(ok)
        cur = await self.db.conn.execute("SELECT id FROM auction_items ORDER BY id DESC LIMIT 1")
        aid = (await cur.fetchone())[0]

        await self.new_player("mk_b", gold=1000)
        buyer = await self.db.get_player_by_id("mk_b")
        ok, msg = await self.auction.buy_item(buyer, aid)
        self.assertTrue(ok, msg)

        # 关键断言：物品必须落库（DB 中的买家背包），而非只在内存
        fresh = await self.db.get_player_by_id("mk_b")
        items = json.loads(fresh.storage_ring_items)
        self.assertEqual(items.get("测试丹材", 0), 1, "购买成功后物品必须写入数据库背包")
        self.assertEqual(fresh.gold, 900)
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM auction_items WHERE id=?", (aid,))
        self.assertEqual((await cur.fetchone())[0], 0, "成交后商品必须下架")

    # ---------- H2 坊市下架返还落库 ----------

    async def test_h2_market_cancel_returns_item(self):
        await self.new_player("cc_s", gold=0)
        seller = await self.db.get_player_by_id("cc_s")
        await self.ring.store_item(seller, "测试丹材", 2, silent=True)
        seller = await self.db.get_player_by_id("cc_s")
        ok, _ = await self.auction.list_item(seller, "测试丹材", 1, 100)
        self.assertTrue(ok)
        cur = await self.db.conn.execute("SELECT id FROM auction_items ORDER BY id DESC LIMIT 1")
        aid = (await cur.fetchone())[0]

        ok, msg = await self.auction.cancel_item(seller, aid)
        self.assertTrue(ok, msg)
        fresh = await self.db.get_player_by_id("cc_s")
        items = json.loads(fresh.storage_ring_items)
        self.assertEqual(items.get("测试丹材", 0), 2, "下架后物品必须回到数据库背包（1+1返还）")
        cur = await self.db.conn.execute("SELECT COUNT(*) FROM auction_items WHERE id=?", (aid,))
        self.assertEqual((await cur.fetchone())[0], 0)

    # ---------- H3 天道盘资金守恒 ----------

    async def test_h3_daobet_concurrent_conserves_money(self):
        for uid in ("db_A", "db_B"):
            await self.new_player(uid, gold=5000)
        a = await self.db.get_player_by_id("db_A")
        b = await self.db.get_player_by_id("db_B")
        r = await asyncio.gather(
            self.daobet.bet(a, "红", "1000"),
            self.daobet.bet(b, "红", "1000"),
        )
        self.assertTrue(all(x[0] for x in r), f"并发押注应都成功: {[x[1][:40] for x in r]}")
        fa = await self.db.get_player_by_id("db_A")
        fb = await self.db.get_player_by_id("db_B")
        spent = 10000 - fa.gold - fb.gold
        rows = await self.db.conn.execute("SELECT key, value FROM system_config WHERE key LIKE 'daopan%'")
        pool = 0
        for row in await rows.fetchall():
            try:
                st = json.loads(row[1])
                pool += int(st.get("red_pool", 0)) + int(st.get("blue_pool", 0))
            except Exception:
                pass
        self.assertEqual(pool, spent, f"盘口记录({pool})必须等于实际扣款({spent})，不得凭空增发")
        self.assertEqual(spent, 2000)

    # ---------- H4 丹药并发服用 ----------

    async def test_h4_pill_concurrent_use_no_dup(self):
        names = [n for n, c in self.cm.exp_pills_data.items() if isinstance(c, dict)][:2]
        self.assertGreaterEqual(len(names), 2, "需要两个修为丹做并发样本")
        p = await self.new_player("pl_u", gold=0)
        await self.pills.add_pill_to_inventory(p, names[0], 1)
        await self.pills.add_pill_to_inventory(p, names[1], 1)
        p1 = await self.db.get_player_by_id("pl_u")
        p2 = await self.db.get_player_by_id("pl_u")
        r = await asyncio.gather(
            self.pills.use_pill(p1, names[0]),
            self.pills.use_pill(p2, names[1]),
        )
        self.assertTrue(all(x[0] for x in r), f"并发服用都应成功: {[x[1][:40] for x in r]}")
        fresh = await self.db.get_player_by_id("pl_u")
        inv = json.loads(fresh.pills_inventory)
        left = inv.get(names[0], 0) + inv.get(names[1], 0)
        self.assertEqual(left, 0, f"服 2 颗后背包必须为空，不得白嫖（剩 {left}）")

    # ---------- M1 红包并发不烧份额 + 不可双抢 ----------

    async def test_m1_redpacket_full_claim_under_race(self):
        sender = await self.new_player("rp_s", gold=5000)
        ok, msg = await self.rp.send(sender, 1000, 5)
        self.assertTrue(ok, msg)
        import re as _re
        pid = _re.search(r"红包编号：\s*([0-9a-f]+)", msg).group(1)

        grabbers = [f"rp_g{i}" for i in range(10)]
        for g in grabbers:
            await self.new_player(g, gold=0)
        objs = [await self.db.get_player_by_id(g) for g in grabbers]
        results = await asyncio.gather(*[self.rp.grab(o, pid) for o in objs])
        won = [i for i, r in enumerate(results) if r[0]]
        self.assertEqual(len(won), 5, f"5 份红包 10 人并发抢应恰好 5 人成功，实际 {len(won)}")
        got = 0
        for i in won:
            f = await self.db.get_player_by_id(grabbers[i])
            self.assertGreater(f.gold, 0, "中奖者必须到账")
            got += f.gold
        self.assertEqual(got, 1000, "到账合计必须等于红包总额")

    async def test_m1b_redpacket_same_user_cannot_double(self):
        sender = await self.new_player("rp_s2", gold=3000)
        ok, msg = await self.rp.send(sender, 300, 3)
        self.assertTrue(ok)
        import re as _re
        pid = _re.search(r"红包编号：\s*([0-9a-f]+)", msg).group(1)
        await self.new_player("rp_gx", gold=0)
        obj = await self.db.get_player_by_id("rp_gx")
        r1 = await self.rp.grab(obj, pid)
        r2 = await self.rp.grab(obj, pid)
        self.assertTrue(r1[0])
        self.assertFalse(r2[0], "同一玩家第二次抢必须失败")

    # ---------- M3 赠予并发接收/拒绝不复制 ----------

    async def test_m3_gift_accept_vs_reject_no_copy(self):
        await self.new_player("gf_s", gold=0)
        await self.new_player("gf_r", gold=0)  # 接收者
        sender = await self.db.get_player_by_id("gf_s")
        await self.ring.store_item(sender, "测试丹材", 1, silent=True)
        sender = await self.db.get_player_by_id("gf_s")
        ok, _ = await self.ring.retrieve_item(sender, "测试丹材", 1)
        self.assertTrue(ok, "赠予前置：物品应从发送者包中扣除进入 pending")

        await self.db.ext.create_pending_gift(
            receiver_id="gf_r", sender_id="gf_s", sender_name="送出者",
            item_name="测试丹材", count=1, expires_hours=24)

        # 同一接收者同时点 接收 + 拒绝
        ev_a = FakeEvent("gf_r")
        ev_r = FakeEvent("gf_r")
        ra, rr = await asyncio.gather(
            drain(self.sr_handler.handle_accept_gift(ev_a)),
            drain(self.sr_handler.handle_reject_gift(ev_r)),
        )
        # 无论谁赢：物品总量守恒（接收→在接收者包；拒绝→回发送者包），且只交付一次
        r_player = await self.db.get_player_by_id("gf_r")
        s_player = await self.db.get_player_by_id("gf_s")
        got = json.loads(r_player.storage_ring_items).get("测试丹材", 0)
        back = json.loads(s_player.storage_ring_items).get("测试丹材", 0)
        self.assertEqual(got + back, 1, f"物品只能交付一次（接收者得 {got} + 发送者收回 {back} ≠ 1）")
        gifts = await self.db.conn.execute("SELECT COUNT(*) FROM pending_gifts WHERE receiver_id='gf_r'")
        self.assertEqual((await gifts.fetchone())[0], 0, "赠予行必须被占行消费")

    # ---------- M4 拍卖托管款只退一次 ----------

    async def test_m4_loot_auction_no_double_refund(self):
        await self.new_player("lo_1", gold=0)
        await self.new_player("lo_2", gold=0)
        now = int(time.time())
        state = {
            "open": False, "boss_name": "旧魔龙", "tier": 1,
            "loot": {"name": "魔核残片", "exp": 100, "gold": 100},
            # 结算崩溃遗留态：open 已关但托管未退，超过 1 小时进入接管窗口
            "end_ts": now - 4000,
            "bids": {"lo_1": {"amount": 5000, "name": "甲"}, "lo_2": {"amount": 3000, "name": "乙"}},
        }
        await self.db.ext.set_system_config("loot_auction_state", json.dumps(state, ensure_ascii=False))

        r = await asyncio.gather(
            self.loot.open_auction("新魔龙", 1),
            self.loot.open_auction("新魔龙2", 1),
        )
        self.assertEqual(sum(1 for x in r if x[0]), 1, "并发开拍只能成功一次")
        f1 = await self.db.get_player_by_id("lo_1")
        f2 = await self.db.get_player_by_id("lo_2")
        self.assertEqual(f1.gold, 5000, "托管款必须恰好退一次")
        self.assertEqual(f2.gold, 3000, "托管款必须恰好退一次")

    # ---------- M6 银行逾期删号前复核 ----------

    async def _insert_loan(self, uid, due_days_ago):
        now = int(time.time())
        await self.db.conn.execute(
            "INSERT INTO bank_loans (user_id, principal, interest_rate, borrowed_at, due_at, status, loan_type) "
            "VALUES (?, ?, ?, ?, ?, 'active', 'normal')",
            (uid, 1000, 0.005, now - (due_days_ago + 5) * 86400, now - due_days_ago * 86400))

    async def test_m6_bank_overdue_recheck_skips_repaid(self):
        # 已还款（status=closed）的逾期贷款：即使仍在调度器的旧快照里也绝不能误杀
        await self.new_player("bk_safe", gold=0)
        await self._insert_loan("bk_safe", due_days_ago=10)
        await self.db.conn.execute("UPDATE bank_loans SET status='closed' WHERE user_id='bk_safe'")
        await self.db.conn.commit()
        await self.bank.check_and_process_overdue_loans()
        fresh = await self.db.get_player_by_id("bk_safe")
        self.assertIsNotNone(fresh, "已还款玩家绝不能被银行误杀删号")

    async def test_m6b_bank_overdue_still_kills_active(self):
        # 对照组：仍 active 的真实逾期（超宽限期）依旧注销——行为不回退
        await self.new_player("bk_dead", gold=0)
        await self._insert_loan("bk_dead", due_days_ago=10)
        await self.db.conn.commit()
        await self.bank.check_and_process_overdue_loans()
        gone = await self.db.get_player_by_id("bk_dead")
        self.assertIsNone(gone, "真实逾期未还的玩家应被注销（原行为保留）")

    # ---------- L3 满级突破计算守卫 ----------

    async def test_l3_breakthrough_rate_max_level_guard(self):
        p = await self.new_player("bt_max", level_index=35, experience=999_999_999)
        ok, _ = self.bt.check_breakthrough_requirements(p)
        self.assertFalse(ok, "满级应被前置检查拦截")
        try:
            rate, msg = self.bt.calculate_breakthrough_success_rate(p)
        except IndexError as e:
            self.fail(f"满级计算不得 IndexError（守卫缺失）: {e}")
        self.assertEqual(rate, 0.0)
        self.assertIn("最高境界", msg)

    # ---------- L4 储物戒非正数量纵深校验 ----------

    async def test_l4_storage_rejects_nonpositive_count(self):
        p = await self.new_player("rg_q", gold=0)
        await self.ring.store_item(p, "测试矿", 5, silent=True)
        for fn in (self.ring.store_item, self.ring.retrieve_item, self.ring.discard_item):
            for bad in (0, -1):
                ok, _ = await fn(p, "测试矿", bad)
                self.assertFalse(ok, f"{fn.__name__}(count={bad}) 必须拒绝")
        fresh = await self.db.get_player_by_id("rg_q")
        cnt = json.loads(fresh.storage_ring_items).get("测试矿", 0)
        self.assertEqual(cnt, 5, "非法数量操作不得改动库存")
        ok, _ = await self.ring.retrieve_item(p, "测试矿", -3)
        self.assertFalse(ok, "负数取出必须拒绝（原缺陷：越取越多）")

    # ---------- L1 循环导入 ----------

    def test_l1_import_config_manager_first(self):
        """config_manager / core 先于 data 导入不得崩溃（子进程隔离验证）"""
        stub = (
            "import sys, types;"
            "m=types.ModuleType('astrbot');api=types.ModuleType('astrbot.api');"
            "api.logger=types.SimpleNamespace(info=lambda *a:None,warning=lambda *a:None,"
            "error=lambda *a:None,exception=lambda *a:None);"
            "api.AstrBotConfig=dict;"
            "ev=types.ModuleType('astrbot.api.event');ev.AstrMessageEvent=object;api.event=ev;"
            "al=types.ModuleType('astrbot.api.all');al.At=object;al.Plain=object;api.all=al;"
            "sys.modules['astrbot']=m;sys.modules['astrbot.api']=api;"
            "sys.modules['astrbot.api.event']=ev;sys.modules['astrbot.api.all']=al;"
            f"sys.path.insert(0, {str(ROOT.parent)!r});"
            "import astrbot_plugin_xiuxian_promax.config_manager;"
            "import astrbot_plugin_xiuxian_promax.core;"
            "print('IMPORT_OK')"
        )
        out = subprocess.run([sys.executable, "-c", stub], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, f"循环导入仍存在: {out.stderr[-400:]}")
        self.assertIn("IMPORT_OK", out.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
