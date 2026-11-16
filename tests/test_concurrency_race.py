# tests/test_concurrency_race.py
"""v3.52.3 并发回归测试套件：抢购/竞态/字段级原子更新

背景：v3.52.3 将最后 48 处 update_player() 整行写回全部转为字段级原子更新。
本套件用真实临时 SQLite + asyncio.gather 并发，验证：

- 字段级更新互不覆盖（S1 系统性回归核心：金/修为/JSON 列并发写共存）
- 条件原子扣款（deduct_player_gold_if）永不透支、并发恰好取走可承受总量
- 条件扣款并写字段（spend_player_gold_for_fields）单语句原子（抢购竞态）
- add_player_gold/add_player_exp 并发无丢失更新
- 商店库存 decrement_shop_item_stock 并发防超卖
- 签到日期原子占位（WHERE 条件不满足 → rowcount=0 拦截双领）
- 结算门禁 claim_user_free_if_type 并发双交只发一份奖

通过标准：全部断言通过。
"""
import asyncio
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

# ---- astrbot stub（与 test_atomic_primitives 同款约定）----
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

_data_base = __import__(f"{PKG}.data.data_manager", fromlist=["x"])
DataBase = _data_base.DataBase
_migration = __import__(f"{PKG}.data.migration", fromlist=["x"])
MigrationManager = _migration.MigrationManager
_models = __import__(f"{PKG}.models", fromlist=["x"])
Player = _models.Player


def make_player(uid: str, gold: int = 100, exp: int = 10) -> Player:
    p = Player(user_id=uid, user_name=uid[:6])
    p.gold = gold
    p.experience = exp
    p.hp = 100
    p.set_pills_inventory({})
    p.set_storage_ring_items({})
    p.permanent_bonus = ""
    return p


class ConcurrencyTestBase(unittest.IsolatedAsyncioTestCase):
    """公共基建：真实临时库 + migration 全量建表"""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = DataBase(os.path.join(self._tmpdir.name, "test_race.db"))
        await self.db.connect()
        await MigrationManager(self.db.conn, None).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmpdir.cleanup()

    async def save_player(self, p: Player):
        await self.db.create_player(p)
        await self.db.update_player_fields(
            p.user_id, gold=p.gold, experience=p.experience,
            hp=p.hp, pills_inventory=p.pills_inventory,
            storage_ring_items=p.storage_ring_items,
            permanent_bonus=p.permanent_bonus,
        )

    async def fresh(self, uid: str) -> Player:
        p = await self.db.get_player_by_id(uid)
        assert p is not None, f"玩家 {uid} 不存在"
        return p


class TestFieldLevelConcurrency(ConcurrencyTestBase):
    """S1 回归核心：字段级更新在并发下互不覆盖"""

    async def test_concurrent_gold_and_exp_no_clobber(self):
        """协程A写灵石、协程B写修为，并发后两者都必须保留（旧整行写回必丢其一）"""
        p = make_player("race_a1", gold=100, exp=10)
        await self.save_player(p)

        async def write_gold():
            await asyncio.sleep(0)
            await self.db.add_player_gold(p.user_id, 500)

        async def write_exp():
            await asyncio.sleep(0)
            await self.db.update_player_fields(p.user_id, experience=999)

        await asyncio.gather(write_gold(), write_exp())

        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 600, "并发写字段后灵石丢失（整行写回回归！）")
        self.assertEqual(p2.experience, 999, "并发写字段后修为丢失（整行写回回归！）")

    async def test_concurrent_json_columns_no_clobber(self):
        """丹药背包与储物戒两个 JSON 列并发写，互不覆盖"""
        p = make_player("race_j1")
        p.set_pills_inventory({"回血丹": 3})
        p.set_storage_ring_items({"玄铁": 5})
        await self.save_player(p)

        async def write_pills():
            await asyncio.sleep(0)
            inv = {"回血丹": 2, "破境丹": 1}
            await self.db.update_player_fields(
                p.user_id, pills_inventory=json.dumps(inv, ensure_ascii=False)
            )

        async def write_ring():
            await asyncio.sleep(0)
            ring = {"玄铁": 4, "精金": 2}
            await self.db.update_player_fields(
                p.user_id, storage_ring_items=json.dumps(ring, ensure_ascii=False)
            )

        await asyncio.gather(write_pills(), write_ring())

        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.get_pills_inventory(), {"回血丹": 2, "破境丹": 1})
        self.assertEqual(p2.get_storage_ring_items(), {"玄铁": 4, "精金": 2})


class TestAtomicDeduction(ConcurrencyTestBase):
    """条件原子扣款：永不透支"""

    async def test_deduct_if_insufficient_no_change(self):
        p = make_player("ded_1", gold=50)
        await self.save_player(p)
        ok = await self.db.deduct_player_gold_if(p.user_id, 51)
        self.assertFalse(ok)
        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 50, "扣款失败不得改动余额")

    async def test_deduct_if_success_exact(self):
        p = make_player("ded_2", gold=50)
        await self.save_player(p)
        ok = await self.db.deduct_player_gold_if(p.user_id, 50)
        self.assertTrue(ok)
        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 0)

    async def test_deduct_if_concurrent_never_overdraw(self):
        """100 灵石并发 10 次各扣 30：恰好 3 次成功，余 10，绝不透支"""
        p = make_player("ded_3", gold=100)
        await self.save_player(p)

        results = await asyncio.gather(
            *[self.db.deduct_player_gold_if(p.user_id, 30) for _ in range(10)]
        )

        self.assertEqual(sum(results), 3, f"成功次数应为3，实际 {sum(results)}")
        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 10, "余额应恰好为 100 - 3*30")
        self.assertGreaterEqual(p2.gold, 0)

    async def test_spend_for_fields_conditional_atomic(self):
        """余额不足时不写字段；成功时扣款与字段写入同语句生效"""
        p = make_player("spf_1", gold=30)
        await self.save_player(p)
        ok = await self.db.spend_player_gold_for_fields(
            p.user_id, 60, storage_ring="混元戒"
        )
        self.assertFalse(ok)
        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 30)
        self.assertNotEqual(p2.storage_ring, "混元戒", "扣款失败不得写入字段")

        ok2 = await self.db.spend_player_gold_for_fields(
            p.user_id, 20, storage_ring="混元戒"
        )
        self.assertTrue(ok2)
        p3 = await self.fresh(p.user_id)
        self.assertEqual(p3.gold, 10)
        self.assertEqual(p3.storage_ring, "混元戒")

    async def test_spend_for_fields_concurrent_purchase_race(self):
        """100 灵石并发两次各花 60 抢购：恰好一次成功（单语句条件原子）"""
        p = make_player("spf_2", gold=100)
        await self.save_player(p)

        results = await asyncio.gather(
            self.db.spend_player_gold_for_fields(p.user_id, 60, storage_ring="A戒"),
            self.db.spend_player_gold_for_fields(p.user_id, 60, storage_ring="B戒"),
        )

        self.assertEqual(sum(results), 1, "两次并发购买应恰好成功一次")
        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 40)
        self.assertIn(p2.storage_ring, ("A戒", "B戒"), "应写入成功那次购买的目标")


class TestAddAtomicNoLostUpdate(ConcurrencyTestBase):
    """原子增减并发无丢失更新"""

    async def test_add_gold_concurrent_sum(self):
        """20 个并发各 +10：最终恰好 base+200（丢失更新回归）"""
        p = make_player("add_1", gold=100)
        await self.save_player(p)

        await asyncio.gather(
            *[self.db.add_player_gold(p.user_id, 10) for _ in range(20)]
        )

        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.gold, 300, "并发加款出现丢失更新！")

    async def test_add_exp_concurrent_sum(self):
        p = make_player("add_2", exp=0)
        await self.save_player(p)

        await asyncio.gather(
            *[self.db.add_player_exp(p.user_id, 7) for _ in range(15)]
        )

        p2 = await self.fresh(p.user_id)
        self.assertEqual(p2.experience, 105)


class TestShopStockConcurrency(ConcurrencyTestBase):
    """商店库存并发防超卖"""

    async def _seed_shop(self, shop_id: str, stock: int):
        items = json.dumps(
            [{"name": "玄铁剑", "type": "weapon", "stock": stock, "price": 100}],
            ensure_ascii=False,
        )
        await self.db.conn.execute(
            "INSERT INTO shop (shop_id, last_refresh_time, current_items) VALUES (?, ?, ?)",
            (shop_id, int(time.time()), items)
        )
        await self.db.conn.commit()

    async def test_concurrent_purchase_no_oversell(self):
        """库存 1，两个并发各买 1：恰好一次成功，库存归零"""
        await self._seed_shop("race_shop", stock=1)

        r1, r2 = await asyncio.gather(
            self.db.decrement_shop_item_stock("race_shop", "玄铁剑", 1),
            self.db.decrement_shop_item_stock("race_shop", "玄铁剑", 1),
        )

        ok_count = sum(1 for ok, _, _ in (r1, r2) if ok)
        self.assertEqual(ok_count, 1, "库存1被并发卖出多于一次（超卖！）")

        async with self.db.conn.execute(
            "SELECT current_items FROM shop WHERE shop_id = ?", ("race_shop",)
        ) as cur:
            row = await cur.fetchone()
        final_stock = json.loads(row[0])[0]["stock"]
        self.assertEqual(final_stock, 0)

    async def test_batch_quantity_rejects_oversell(self):
        """库存 2，一次请求 3 个：拒绝且不动库存"""
        await self._seed_shop("batch_shop", stock=2)
        ok, _, remaining = await self.db.decrement_shop_item_stock("batch_shop", "玄铁剑", 3)
        self.assertFalse(ok)
        self.assertEqual(remaining, 2)

        async with self.db.conn.execute(
            "SELECT current_items FROM shop WHERE shop_id = ?", ("batch_shop",)
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(json.loads(row[0])[0]["stock"], 2, "失败扣减不得改动库存")


class TestClaimGates(ConcurrencyTestBase):
    """原子占位/门禁类竞态"""

    async def test_check_in_date_atomic_claim(self):
        """签到日期条件占位：并发第二条被 WHERE 拦截（rowcount=0）"""
        p = make_player("gate_1")
        await self.save_player(p)
        today = "2026-11-17"

        async with self.db.conn.execute(
            "UPDATE players SET last_check_in_date = ? WHERE user_id = ? AND (last_check_in_date IS NULL OR last_check_in_date != ?)",
            (today, p.user_id, today)
        ) as cur:
            first = cur.rowcount > 0
        async with self.db.conn.execute(
            "UPDATE players SET last_check_in_date = ? WHERE user_id = ? AND (last_check_in_date IS NULL OR last_check_in_date != ?)",
            (today, p.user_id, today)
        ) as cur:
            second = cur.rowcount > 0

        self.assertTrue(first)
        self.assertFalse(second, "同日第二次签到占位应失败（防双领回归）")

    async def test_settlement_gate_only_once(self):
        """结算门禁：并发两次 claim_user_free_if_type 只有一次成功"""
        p = make_player("gate_2")
        await self.save_player(p)
        await self.db.ext.set_user_busy(p.user_id, 3, 0)

        r1, r2 = await asyncio.gather(
            self.db.ext.claim_user_free_if_type(p.user_id, 3),
            self.db.ext.claim_user_free_if_type(p.user_id, 3),
        )

        self.assertEqual(sum((r1, r2)), 1, "结算门禁被并发通过两次（双发奖回归！）")
        async with self.db.conn.execute(
            "SELECT type FROM user_cd WHERE user_id = ?", (p.user_id,)
        ) as cur:
            row = await cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 0, "结算后 user_cd.type 应回到空闲(0)")

    async def test_claim_slot_concurrent_limit(self):
        """syscfg_claim_slot 并发抢限额槽位：成功数恰好等于限额"""
        results = await asyncio.gather(
            *[self.db.ext.syscfg_claim_slot("race_slot_test", 3) for _ in range(10)]
        )
        self.assertEqual(sum(results), 3, "限额槽位被超额抢占")


if __name__ == "__main__":
    unittest.main()
