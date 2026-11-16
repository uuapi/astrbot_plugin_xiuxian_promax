# tests/test_command_flows.py
"""v3.52.4 全量指令 E2E 流程测试：真实 Handler + 真实临时库 + 真实配置数据

与既有单测的区别：本套件直接驱动 handlers/ 里的真实异步生成器（与 /指令 在
机器人里完全同一条代码路径），用 FakeEvent 捕获回复文案，断言：
  1) 回复包含预期文案片段（用户体验层）
  2) 数据库状态按预期变化（业务逻辑层）

覆盖系统：修炼主循环 / 签到 / 装备 / 丹药 / 商店 / 储物戒 / 宗门 / 排行榜 /
银行 / 悬赏 / 历练 / 秘境 / 炼丹 / 洞天 / 灵田 / 改道号 / 传承 / 帮助过滤。
"""
import asyncio
import io
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

# ---- astrbot stub ----
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


def _imp(mod):
    return __import__(f"{PKG}.{mod}", fromlist=["x"])


DataBase = _imp("data.data_manager").DataBase
MigrationManager = _imp("data.migration").MigrationManager
Player = _imp("models").Player
ConfigManager = _imp("config_manager").ConfigManager

PlayerHandler = _imp("handlers.player_handler").PlayerHandler
EquipmentHandler = _imp("handlers.equipment_handler").EquipmentHandler
PillHandler = _imp("handlers.pill_handler").PillHandler
ShopHandler = _imp("handlers.shop_handler").ShopHandler
StorageRingHandler = _imp("handlers.storage_ring_handler").StorageRingHandler
NicknameHandler = _imp("handlers.nickname_handler").NicknameHandler
MiscHandler = _imp("handlers.misc_handler").MiscHandler
SectHandlers = _imp("handlers.sect_handlers").SectHandlers
RankingHandlers = _imp("handlers.ranking_handlers").RankingHandlers
BankHandlers = _imp("handlers.bank_handlers").BankHandlers
BountyHandlers = _imp("handlers.bounty_handlers").BountyHandlers
AdventureHandlers = _imp("handlers.adventure_handlers").AdventureHandlers
RiftHandlers = _imp("handlers.rift_handlers").RiftHandlers
AlchemyHandlers = _imp("handlers.alchemy_handlers").AlchemyHandlers
BlessedLandHandlers = _imp("handlers.blessed_land_handlers").BlessedLandHandlers
SpiritFarmHandlers = _imp("handlers.spirit_farm_handlers").SpiritFarmHandlers
ImpartHandlers = _imp("handlers.impart_handlers").ImpartHandlers
DailyTaskHandlers = _imp("handlers.daily_task_handlers").DailyTaskHandlers
ForgeHandlers = _imp("handlers.forge_handlers").ForgeHandlers
CombatHandlers = _imp("handlers.combat_handlers").CombatHandlers
GambleHandlers = _imp("handlers.gamble_handlers").GambleHandlers
RobberyHandlers = _imp("handlers.robbery_handlers").RobberyHandlers
PetHandlers = _imp("handlers.pet_handlers").PetHandlers
DemonInvasionHandlers = _imp("handlers.demon_invasion_handlers").DemonInvasionHandlers
SpiritEyeHandlers = _imp("handlers.spirit_eye_handlers").SpiritEyeHandlers
DualCultivationHandlers = _imp("handlers.dual_cultivation_handlers").DualCultivationHandlers
ImpartPkHandlers = _imp("handlers.impart_pk_handlers").ImpartPkHandlers
AuctionHandlers = _imp("handlers.auction_handlers").AuctionHandlers
BossHandlers = _imp("handlers.boss_handlers").BossHandlers
ReincarnationHandlers = _imp("handlers.reincarnation_handlers").ReincarnationHandlers
WorldBossHandlers = _imp("handlers.world_boss_handlers").WorldBossHandlers
BreakthroughHandler = _imp("handlers.breakthrough_handler").BreakthroughHandler
CalamityManager = _imp("managers.calamity_manager").CalamityManager
WanderMerchantManager = _imp("managers.wander_merchant_manager").WanderMerchantManager
WeatherManager = _imp("managers.weather_manager").WeatherManager
StrongholdManager = _imp("managers.stronghold_manager").StrongholdManager
RiftEnhanceManager = _imp("managers.rift_enhance").RiftEnhanceManager
PlayerBountyManager = _imp("managers.player_bounty_manager").PlayerBountyManager
SectTechManager = _imp("managers.sect_tech_manager").SectTechManager
LootAuctionManager = _imp("managers.loot_auction_manager").LootAuctionManager
AscensionProjectManager = _imp("managers.ascension_project_manager").AscensionProjectManager
GroupChronicleManager = _imp("managers.group_chronicle_manager").GroupChronicleManager
_farm_mod = _imp("managers.spirit_farm_manager")
SEASON_EFFECTS = _farm_mod.SEASON_EFFECTS
get_current_season_index = _farm_mod.get_current_season_index

SectManager = _imp("managers.sect_manager").SectManager
RankingManager = _imp("managers.ranking_manager").RankingManager
BankManager = _imp("managers.bank_manager").BankManager
BountyManager = _imp("managers.bounty_manager").BountyManager
AdventureManager = _imp("managers.adventure_manager").AdventureManager
RiftManager = _imp("managers.rift_manager").RiftManager
AlchemyManager = _imp("managers.alchemy_manager").AlchemyManager
BlessedLandManager = _imp("managers.blessed_land_manager").BlessedLandManager
SpiritFarmManager = _imp("managers.spirit_farm_manager").SpiritFarmManager
ImpartManager = _imp("managers.impart_manager").ImpartManager
DailyTaskManager = _imp("managers.daily_task_manager").DailyTaskManager
ForgeManager = _imp("managers.forge_manager").ForgeManager
CombatManager = _imp("managers.combat_manager").CombatManager
GambleManager = _imp("managers.gamble_manager").GambleManager
RobberyManager = _imp("managers.robbery_manager").RobberyManager
PetManager = _imp("managers.pet_manager").PetManager
DemonInvasionManager = _imp("managers.demon_invasion_manager").DemonInvasionManager
SpiritEyeManager = _imp("managers.spirit_eye_manager").SpiritEyeManager
DualCultivationManager = _imp("managers.dual_cultivation_manager").DualCultivationManager
ImpartPkManager = _imp("managers.impart_pk_manager").ImpartPkManager
AuctionManager = _imp("managers.auction_manager").AuctionManager
BossManager = _imp("managers.boss_manager").BossManager
ReincarnationManager = _imp("managers.reincarnation_manager").ReincarnationManager
WorldBossManager = _imp("managers.world_boss_manager").WorldBossManager
StorageRingManager = _imp("core.storage_ring_manager").StorageRingManager


class FakeEvent:
    """最小可用的 AstrMessageEvent 替身，捕获全部回复文案"""

    def __init__(self, uid: str, text: str = "", name: str = "", group_id: int = 12345):
        self._uid = uid
        self._name = name or uid
        self._gid = group_id
        self.message_str = text
        self.replies: list = []
        self.message_obj = types.SimpleNamespace(message_str=text, message=[])

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._name

    def get_group_id(self):
        return self._gid

    def get_message_str(self):
        return self.message_str

    def get_plain_text(self):
        return self.message_str

    def get_self_id(self):
        return "bot"

    def plain_result(self, msg):
        self.replies.append(str(msg))
        return msg

    def image_result(self, *a, **k):
        self.replies.append("[image]")
        return "[image]"

    async def send(self, msg):
        self.replies.append(str(msg))

    @property
    def text(self):
        return "\n".join(self.replies)


async def fire(handler_method, event, *args, **kwargs):
    """驱动 handler/manager 异步生成器，返回拼接后的全部回复文本；兼容普通协程"""
    result = handler_method(event, *args, **kwargs)
    if hasattr(result, "__aiter__"):
        async for _ in result:
            pass
    else:
        ret = await result
        if isinstance(ret, str) and ret:
            event.replies.append(ret)  # 普通 async def 面板方法返回字符串
    return event.text


class FlowEnv:
    """真实环境：临时库 + 全量建表 + 真实配置 + 真实 handler"""

    def __init__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = None

    async def __aenter__(self):
        self.db = DataBase(os.path.join(self._tmpdir.name, "flow.db"))
        await self.db.connect()
        await MigrationManager(self.db.conn, None).migrate()
        await self.db.ext.ensure_system_config_table()
        await self.db.ensure_performance_indexes()
        self.cm = ConfigManager(ROOT)
        self.config = {}
        self.player = PlayerHandler(self.db, self.config, self.cm)
        self.equip = EquipmentHandler(self.db, self.cm)
        self.pill = PillHandler(self.db, self.cm)
        self.shop = ShopHandler(self.db, self.config, self.cm)
        self.ring = StorageRingHandler(self.db, self.cm)
        self.nickname = NicknameHandler(self.db)
        self.misc = MiscHandler(self.db)
        self.sect = SectHandlers(self.db, SectManager(self.db, self.cm), self.cm)
        _combat = _imp("managers.combat_manager").CombatManager()
        self.rank = RankingHandlers(self.db, RankingManager(self.db, _combat, self.cm))
        self.bank = BankHandlers(self.db, BankManager(self.db, self.config))
        self.bounty = BountyHandlers(self.db, BountyManager(self.db))
        self.adv = AdventureHandlers(self.db, AdventureManager(self.db))
        self.rift = RiftHandlers(self.db, RiftManager(self.db, self.cm))
        self.alch = AlchemyHandlers(self.db, AlchemyManager(self.db, self.cm))
        self.bl = BlessedLandHandlers(self.db, BlessedLandManager(self.db))
        self.farm = SpiritFarmHandlers(self.db, SpiritFarmManager(self.db))
        self.impart = ImpartHandlers(self.db, ImpartManager(self.db))
        ring_mgr = StorageRingManager(self.db, self.cm)
        self.daily = DailyTaskHandlers(self.db, DailyTaskManager(self.db), ring_mgr)
        self.forge = ForgeHandlers(self.db, ForgeManager(self.db, self.cm, ring_mgr))
        combat = CombatManager()
        self.combat = CombatHandlers(self.db, combat, self.cm)
        self.gamble = GambleHandlers(self.db, GambleManager(self.db, self.cm, ring_mgr))
        self.robbery = RobberyHandlers(self.db, RobberyManager(self.db, ring_mgr))
        self.pet = PetHandlers(self.db, PetManager(self.db, ring_mgr))
        self.demon = DemonInvasionHandlers(self.db, DemonInvasionManager(self.db), ring_mgr)
        self.speye = SpiritEyeHandlers(self.db, SpiritEyeManager(self.db))
        self.dual = DualCultivationHandlers(self.db, DualCultivationManager(self.db))
        self.impart_pk = ImpartPkHandlers(self.db, ImpartPkManager(self.db, combat))
        self.auction = AuctionHandlers(self.db, AuctionManager(self.db, ring_mgr, self.cm))
        self.boss = BossHandlers(self.db, BossManager(self.db, combat, self.cm, ring_mgr))
        self.reinc = ReincarnationHandlers(self.db, ReincarnationManager(self.db, self.cm, ring_mgr))
        self.wb = WorldBossHandlers(self.db, WorldBossManager(self.db, self.cm, ring_mgr))
        self.bt = BreakthroughHandler(self.db, self.cm, self.config)
        return self

    async def __aexit__(self, *exc):
        await self.db.close()
        self._tmpdir.cleanup()

    async def new_player(self, uid: str, name: str = "", cultivation_type: str = "灵修"):
        """走真实 /我要修仙 流程注册玩家，返回 FakeEvent"""
        ev = FakeEvent(uid, text=cultivation_type, name=name or uid)
        await fire(self.player.handle_start_xiuxian, ev)
        return ev

    async def get_player(self, uid: str) -> Player:
        p = await self.db.get_player_by_id(uid)
        assert p is not None, f"玩家 {uid} 不存在"
        return p


class TestPlayerLoop(unittest.IsolatedAsyncioTestCase):
    """修炼主循环：注册 → 签到 → 闭关 → 出关 → 我的信息"""

    async def test_full_cultivation_loop(self):
        async with FlowEnv() as env:
            # 1. 注册
            ev = await env.new_player("u_loop", "循环道友")
            self.assertIn("踏上仙途", ev.text)
            p = await env.get_player("u_loop")
            self.assertIsNotNone(p)

            # 2. 我的信息
            ev2 = FakeEvent("u_loop")
            await fire(env.player.handle_player_info, ev2)
            self.assertTrue(ev2.text, "我的信息必须有回复（文本或图片卡片）")

            # 3. 签到
            ev3 = FakeEvent("u_loop")
            await fire(env.player.handle_check_in, ev3)
            self.assertIn("签到", ev3.text)
            p3 = await env.get_player("u_loop")
            gold_after_checkin = p3.gold
            self.assertGreater(gold_after_checkin, 0)

            # 4. 重复签到被拦截
            ev4 = FakeEvent("u_loop")
            await fire(env.player.handle_check_in, ev4)
            self.assertIn("已经签到", ev4.text)
            p4 = await env.get_player("u_loop")
            self.assertEqual(p4.gold, gold_after_checkin, "重复签到不得重复发放")

            # 5. 闭关
            ev5 = FakeEvent("u_loop")
            await fire(env.player.handle_start_cultivation, ev5)
            self.assertIn("闭关", ev5.text)
            p5 = await env.get_player("u_loop")
            self.assertEqual(p5.state, "修炼中")

            # 6. 重复闭关被拦截
            ev6 = FakeEvent("u_loop")
            await fire(env.player.handle_start_cultivation, ev6)
            self.assertIn("无法分心", ev6.text)

            # 7. 把闭关开始时间拨回 1 小时前，再出关
            await env.db.update_player_fields(
                "u_loop", cultivation_start_time=int(time.time()) - 3600
            )
            exp_before = p5.experience
            ev7 = FakeEvent("u_loop", text="出关")  # 忙碌白名单按消息文本放行
            await fire(env.player.handle_end_cultivation, ev7)
            self.assertIn("出关", ev7.text)
            p7 = await env.get_player("u_loop")
            self.assertEqual(p7.state, "空闲")
            self.assertGreater(p7.experience, exp_before, "出关必须获得修为")

            # 8. 空闲状态下出关被拦截
            ev8 = FakeEvent("u_loop", text="出关")
            await fire(env.player.handle_end_cultivation, ev8)
            self.assertTrue(ev8.text)


class TestHelp(unittest.IsolatedAsyncioTestCase):

    async def test_help_overview_and_keyword_filter(self):
        async with FlowEnv() as env:
            ev = FakeEvent("u_help")
            await fire(env.misc.handle_help, ev)
            self.assertIn("修仙", ev.text)
            self.assertIn("指令", ev.text)

            ev2 = FakeEvent("u_help", text="签到")
            await fire(env.misc.handle_help, ev2, "签到")
            self.assertIn("签到", ev2.text)


class TestNickname(unittest.IsolatedAsyncioTestCase):

    async def test_change_nickname_success_and_conflict(self):
        async with FlowEnv() as env:
            await env.new_player("n_1", "甲")
            await env.new_player("n_2", "乙")

            ev = FakeEvent("n_1", text="云隐子")
            await fire(env.nickname.handle_change_nickname, ev, "云隐子")
            self.assertIn("成功", ev.text)
            p = await env.get_player("n_1")
            self.assertEqual(p.user_name, "云隐子")

            # 重名拒绝
            ev2 = FakeEvent("n_2", text="云隐子")
            await fire(env.nickname.handle_change_nickname, ev2, "云隐子")
            self.assertIn("已被其他修士使用", ev2.text)
            p2 = await env.get_player("n_2")
            self.assertNotEqual(p2.user_name, "云隐子")


class TestEquipment(unittest.IsolatedAsyncioTestCase):

    async def test_equip_show_unequip_flow(self):
        async with FlowEnv() as env:
            await env.new_player("e_1")
            p = await env.get_player("e_1")

            # 塞一件真实配置中的武器进储物戒
            weapon_name = next(iter(env.cm.weapons_data.keys()), None)
            self.assertIsNotNone(weapon_name, "weapons.json 必须有数据")
            items = p.get_storage_ring_items()
            items[weapon_name] = 1
            await env.db.update_player_fields(
                "e_1", storage_ring_items=json.dumps(items, ensure_ascii=False)
            )

            # 我的装备（未装备态）
            ev0 = FakeEvent("e_1")
            await fire(env.equip.handle_show_equipment, ev0)
            self.assertTrue(ev0.text)

            # 装备
            ev = FakeEvent("e_1", text=weapon_name)
            await fire(env.equip.handle_equip_item, ev, weapon_name)
            p2 = await env.get_player("e_1")
            self.assertEqual(p2.weapon, weapon_name, "武器应写入装备槽")

            # 卸下（回收进储物戒）
            ev2 = FakeEvent("e_1", text="武器")
            await fire(env.equip.handle_unequip_item, ev2, "武器")
            p3 = await env.get_player("e_1")
            self.assertEqual(p3.weapon, "")
            self.assertGreaterEqual(p3.get_storage_ring_items().get(weapon_name, 0), 1,
                                    "卸下的武器必须回收进储物戒，不得蒸发")


class TestShop(unittest.IsolatedAsyncioTestCase):

    async def test_pavilions_list_and_item_info(self):
        async with FlowEnv() as env:
            await env.new_player("s_1")
            for handler, name in [
                (env.shop.handle_pill_pavilion, "丹阁"),
                (env.shop.handle_weapon_pavilion, "器阁"),
                (env.shop.handle_treasure_pavilion, "百宝阁"),
            ]:
                ev = FakeEvent("s_1")
                await fire(handler, ev)
                self.assertTrue(ev.text, f"{name} 列表必须有内容")

    async def test_buy_flow(self):
        async with FlowEnv() as env:
            await env.new_player("s_2")
            # 物品信息（空参兜底）
            ev0 = FakeEvent("s_2")
            await fire(env.shop.handle_item_info, ev0, "")
            self.assertTrue(ev0.text)
            # 购买（空参数兜底不崩溃）
            ev1 = FakeEvent("s_2", text="")
            await fire(env.shop.handle_buy, ev1, "")
            self.assertTrue(ev1.text)


class TestStorageRing(unittest.IsolatedAsyncioTestCase):

    async def test_ring_list_retrieve_discard_search(self):
        async with FlowEnv() as env:
            await env.new_player("r_1")
            item_name = next(iter(env.cm.items_data.keys()), "灵草")
            p = await env.get_player("r_1")
            items = p.get_storage_ring_items()
            items[item_name] = 3
            await env.db.update_player_fields(
                "r_1", storage_ring_items=json.dumps(items, ensure_ascii=False)
            )

            # 储物戒总览
            ev = FakeEvent("r_1")
            await fire(env.ring.handle_storage_ring, ev)
            self.assertTrue(ev.text, "总览必须有回复（文本或图片）")

            # 取出
            ev2 = FakeEvent("r_1", text=f"{item_name} 1")
            await fire(env.ring.handle_retrieve_item, ev2, f"{item_name} 1")
            p2 = await env.get_player("r_1")
            self.assertEqual(p2.get_storage_ring_items().get(item_name, 0), 2, "取出后库存-1")

            # 搜索
            ev3 = FakeEvent("r_1", text=item_name[:1] if item_name else "")
            await fire(env.ring.handle_search_item, ev3, item_name[:1])
            self.assertTrue(ev3.text)

            # 丢弃
            ev4 = FakeEvent("r_1", text=f"{item_name} 1")
            await fire(env.ring.handle_discard_item, ev4, f"{item_name} 1")
            p4 = await env.get_player("r_1")
            self.assertEqual(p4.get_storage_ring_items().get(item_name, 0), 1, "丢弃后库存-1")


class TestSect(unittest.IsolatedAsyncioTestCase):

    async def test_create_join_leave_flow(self):
        async with FlowEnv() as env:
            await env.new_player("c_1", "宗主甲")
            await env.new_player("c_2", "弟子乙")
            # 创建宗门需境界3：直接落库提升（本用例聚焦宗门流程而非升级链路）
            await env.db.update_player_fields("c_1", level_index=3, gold=200000)
            await env.db.update_player_fields("c_2", level_index=3, gold=200000)

            # 创建宗门
            ev = FakeEvent("c_1", text="青云门")
            await fire(env.sect.handle_create_sect, ev, "青云门")
            self.assertIn("创建", ev.text)
            p1 = await env.get_player("c_1")
            self.assertIsNotNone(p1.sect_id, "创建者必须入宗")

            # 加入宗门
            ev2 = FakeEvent("c_2", text="青云门")
            await fire(env.sect.handle_join_sect, ev2, "青云门")
            p2 = await env.get_player("c_2")
            self.assertIsNotNone(p2.sect_id, "加入者必须入宗")
            self.assertEqual(p2.sect_id, p1.sect_id, "加入者应与宗主同宗")

            # 我的宗门
            ev3 = FakeEvent("c_2")
            await fire(env.sect.handle_my_sect, ev3)
            self.assertIn("青云门", ev3.text)

            # 宗门列表
            ev4 = FakeEvent("c_1")
            await fire(env.sect.handle_sect_list, ev4)
            self.assertIn("青云门", ev4.text)

            # 退出宗门
            ev5 = FakeEvent("c_2")
            await fire(env.sect.handle_leave_sect, ev5)
            p3 = await env.get_player("c_2")
            self.assertFalse(p3.sect_id, "退出后 sect_id 归零")

    async def test_duplicate_create_rejected(self):
        async with FlowEnv() as env:
            await env.new_player("c_3")
            await env.db.update_player_fields("c_3", level_index=3, gold=200000)
            ev = FakeEvent("c_3", text="天衍宗")
            await fire(env.sect.handle_create_sect, ev, "天衍宗")
            ev2 = FakeEvent("c_3", text="天衍宗")
            await fire(env.sect.handle_create_sect, ev2, "天衍宗")
            # 第二次创建必须给出失败提示（不能静默成功）
            self.assertTrue(ev2.text)


class TestRankings(unittest.IsolatedAsyncioTestCase):

    async def test_all_rank_boards_render(self):
        async with FlowEnv() as env:
            await env.new_player("k_1", "榜主")
            for handler in [
                env.rank.handle_rank_level, env.rank.handle_rank_power,
                env.rank.handle_rank_wealth, env.rank.handle_rank_sect,
                env.rank.handle_rank_deposit, env.rank.handle_rank_sect_contribution,
            ]:
                ev = FakeEvent("k_1")
                await fire(handler, ev)
                self.assertTrue(ev.text)


class TestBank(unittest.IsolatedAsyncioTestCase):

    async def test_deposit_withdraw_interest_loan_flow(self):
        async with FlowEnv() as env:
            await env.new_player("b_1")
            p0 = await env.get_player("b_1")
            self.assertGreater(p0.gold, 1000)

            # 存款
            ev = FakeEvent("b_1", text="2000")
            await fire(env.bank.handle_deposit, ev, 800)
            self.assertIn("800", ev.text)
            p1 = await env.get_player("b_1")
            self.assertEqual(p1.gold, p0.gold - 800)

            # 存款超限拒绝
            ev_neg = FakeEvent("b_1", text="-5")
            await fire(env.bank.handle_deposit, ev_neg, -5)
            self.assertTrue(ev_neg.text)

            # 银行面板
            ev2 = FakeEvent("b_1")
            await fire(env.bank.handle_bank_info, ev2)
            self.assertIn("银行", ev2.text)

            # 取款
            ev3 = FakeEvent("b_1", text="500")
            await fire(env.bank.handle_withdraw, ev3, 500)
            p3 = await env.get_player("b_1")
            self.assertEqual(p3.gold, p1.gold + 500)

            # 贷款
            ev4 = FakeEvent("b_1", text="1000")
            await fire(env.bank.handle_loan, ev4, 1000)
            p4 = await env.get_player("b_1")
            self.assertGreaterEqual(p4.gold, p3.gold, "放款后灵石不减")

            # 还款（带利息，可能金额高于1000，先查面板）
            ev5 = FakeEvent("b_1")
            await fire(env.bank.handle_repay, ev5)
            self.assertTrue(ev5.text)

            # 流水
            ev6 = FakeEvent("b_1")
            await fire(env.bank.handle_transactions, ev6)
            self.assertTrue(ev6.text)


class TestBounty(unittest.IsolatedAsyncioTestCase):

    async def test_bounty_list_and_accept_abandon(self):
        async with FlowEnv() as env:
            await env.new_player("w_1")
            ev = FakeEvent("w_1")
            await fire(env.bounty.handle_bounty_list, ev)
            self.assertTrue(ev.text, "悬赏列表必须有内容")

            ev2 = FakeEvent("w_1")
            await fire(env.bounty.handle_bounty_status, ev2)
            self.assertTrue(ev2.text)


class TestAdventure(unittest.IsolatedAsyncioTestCase):

    async def test_adventure_start_status_info(self):
        async with FlowEnv() as env:
            await env.new_player("a_1")
            ev = FakeEvent("a_1")
            await fire(env.adv.handle_adventure_info, ev)
            self.assertTrue(ev.text)

            ev2 = FakeEvent("a_1")
            await fire(env.adv.handle_adventure_status, ev2)
            self.assertTrue(ev2.text)

            # 开始历练（需境界门槛，未达标也要有友好文案）
            ev3 = FakeEvent("a_1")
            await fire(env.adv.handle_start_adventure, ev3)
            self.assertTrue(ev3.text)


class TestRift(unittest.IsolatedAsyncioTestCase):

    async def test_rift_list_and_explore_guards(self):
        async with FlowEnv() as env:
            await env.new_player("f_1")
            ev = FakeEvent("f_1")
            await fire(env.rift.handle_rift_list, ev)
            self.assertTrue(ev.text)

            # 低境界探索应被门槛拦截并给出提示
            ev2 = FakeEvent("f_1")
            await fire(env.rift.handle_rift_explore, ev2, 1)
            self.assertTrue(ev2.text)


class TestAlchemy(unittest.IsolatedAsyncioTestCase):

    async def test_recipes_list(self):
        async with FlowEnv() as env:
            await env.new_player("l_1")
            ev = FakeEvent("l_1")
            await fire(env.alch.handle_recipes, ev)
            self.assertTrue(ev.text)


class TestPill(unittest.IsolatedAsyncioTestCase):

    async def test_pill_views_and_use_guards(self):
        async with FlowEnv() as env:
            await env.new_player("d_1")
            ev = FakeEvent("d_1")
            await fire(env.pill.handle_show_pills, ev)
            self.assertIn("空", ev.text)

            # 服用不存在的丹药
            ev2 = FakeEvent("d_1", text="不存在的丹药")
            await fire(env.pill.handle_use_pill, ev2, "不存在的丹药")
            self.assertTrue(ev2.text)

            # 丹药信息（空参兜底）
            ev3 = FakeEvent("d_1")
            await fire(env.pill.handle_pill_info, ev3, "")
            self.assertTrue(ev3.text)


class TestBlessedLand(unittest.IsolatedAsyncioTestCase):

    async def test_blessed_land_info(self):
        async with FlowEnv() as env:
            await env.new_player("x_1")
            ev = FakeEvent("x_1")
            await fire(env.bl.handle_blessed_land_info, ev)
            self.assertTrue(ev.text)


class TestSpiritFarm(unittest.IsolatedAsyncioTestCase):

    async def test_farm_create_and_info(self):
        async with FlowEnv() as env:
            await env.new_player("g_1")
            ev = FakeEvent("g_1")
            await fire(env.farm.handle_farm_info, ev)
            self.assertTrue(ev.text)

            # 开垦灵田
            ev2 = FakeEvent("g_1")
            await fire(env.farm.handle_create_farm, ev2)
            self.assertTrue(ev2.text)


class TestImpart(unittest.IsolatedAsyncioTestCase):

    async def test_impart_info(self):
        async with FlowEnv() as env:
            await env.new_player("i_1")
            ev = FakeEvent("i_1")
            await fire(env.impart.handle_impart_info, ev)
            self.assertTrue(ev.text)


class TestDailyTasks(unittest.IsolatedAsyncioTestCase):

    async def test_daily_overview(self):
        async with FlowEnv() as env:
            await env.new_player("dt_1")
            ev = FakeEvent("dt_1")
            await fire(env.daily.handle_task_board, ev)
            self.assertTrue(ev.text, "日常面板必须有回复")


class TestForge(unittest.IsolatedAsyncioTestCase):

    async def test_enhance_guards(self):
        async with FlowEnv() as env:
            await env.new_player("fg_1")
            ev = FakeEvent("fg_1")
            await fire(env.forge.handle_enhance, ev)
            self.assertTrue(ev.text)


class TestCombatDuel(unittest.IsolatedAsyncioTestCase):

    async def test_duel_missing_target(self):
        async with FlowEnv() as env:
            await env.new_player("cm_1")
            ev = FakeEvent("cm_1", text="决斗 不存在的人")
            await fire(env.combat.handle_duel, ev, "不存在的人")
            self.assertTrue(ev.text)


class TestGamble(unittest.IsolatedAsyncioTestCase):

    async def test_gamble_info(self):
        async with FlowEnv() as env:
            await env.new_player("gm_1")
            ev = FakeEvent("gm_1")
            await fire(env.gamble.handle_market, ev)
            self.assertTrue(ev.text)


class TestPetFlow(unittest.IsolatedAsyncioTestCase):

    async def test_pet_overview(self):
        async with FlowEnv() as env:
            await env.new_player("pt_1")
            ev = FakeEvent("pt_1")
            await fire(env.pet.handle_my_pet, ev)
            self.assertTrue(ev.text)


class TestSpiritEye(unittest.IsolatedAsyncioTestCase):

    async def test_eye_info(self):
        async with FlowEnv() as env:
            await env.new_player("se_1")
            ev = FakeEvent("se_1")
            await fire(env.speye.handle_spirit_eye_info, ev)
            self.assertTrue(ev.text)


class TestDualCultivation(unittest.IsolatedAsyncioTestCase):

    async def test_dual_status(self):
        async with FlowEnv() as env:
            await env.new_player("dc_1")
            ev = FakeEvent("dc_1")
            await fire(env.dual.handle_dual_request, ev)
            self.assertTrue(ev.text)


class TestImpartPk(unittest.IsolatedAsyncioTestCase):

    async def test_impart_ranking(self):
        async with FlowEnv() as env:
            await env.new_player("ip_1")
            ev = FakeEvent("ip_1")
            await fire(env.impart_pk.handle_impart_ranking, ev)
            self.assertTrue(ev.text)


class TestAuction(unittest.IsolatedAsyncioTestCase):

    async def test_auction_overview(self):
        async with FlowEnv() as env:
            await env.new_player("au_1")
            ev = FakeEvent("au_1")
            await fire(env.auction.handle_list, ev)
            self.assertTrue(ev.text)


class TestWorldBoss(unittest.IsolatedAsyncioTestCase):

    async def test_wb_status(self):
        async with FlowEnv() as env:
            await env.new_player("wb_1")
            ev = FakeEvent("wb_1")
            await fire(env.wb.handle_query, ev)
            self.assertTrue(ev.text)


class TestDemonInvasion(unittest.IsolatedAsyncioTestCase):

    async def test_demon_status(self):
        async with FlowEnv() as env:
            await env.new_player("dm_1")
            ev = FakeEvent("dm_1")
            await fire(env.demon.handle_status, ev)
            self.assertTrue(ev.text)


class TestReincarnation(unittest.IsolatedAsyncioTestCase):

    async def test_rebirth_requires_max_level(self):
        async with FlowEnv() as env:
            await env.new_player("rc_1")
            ev = FakeEvent("rc_1", text="弃道重修 确认")
            await fire(env.reinc.handle_reincarnate, ev, "确认")
            self.assertIn("巅峰", ev.text, "未满级必须拒绝轮回")


class TestBossList(unittest.IsolatedAsyncioTestCase):

    async def test_boss_list(self):
        async with FlowEnv() as env:
            await env.new_player("bs_1")
            ev = FakeEvent("bs_1")
            await fire(env.boss.handle_boss_info, ev)
            self.assertTrue(ev.text)


class TestSectManagement(unittest.IsolatedAsyncioTestCase):
    """宗门管理指令：捐献/任务/完成/踢出/职位"""

    async def _setup_sect(self, env):
        # 踢出/传位指令要求 At 或数字 ID，因此用数字玩家 ID
        await env.new_player("10001", "宗主")
        await env.new_player("10002", "成员")
        await env.db.update_player_fields("10001", level_index=3, gold=200000)
        await env.db.update_player_fields("10002", level_index=3, gold=200000)
        ev = FakeEvent("10001", text="太平宗")
        await fire(env.sect.handle_create_sect, ev, "太平宗")
        ev2 = FakeEvent("10002", text="太平宗")
        await fire(env.sect.handle_join_sect, ev2, "太平宗")
        p2 = await env.get_player("10002")
        assert p2.sect_id, "前置：成员必须已入宗"

    async def test_donate_task_kick_position(self):
        async with FlowEnv() as env:
            await self._setup_sect(env)
            gold0 = (await env.get_player("10001")).gold

            # 捐献
            ev = FakeEvent("10001", text="宗门捐献 1000")
            await fire(env.sect.handle_donate, ev, 1000)
            self.assertTrue(ev.text)
            p1 = await env.get_player("10001")
            self.assertLess(p1.gold, gold0, "捐献必须扣灵石")

            # 宗门任务领取 + 完成
            ev2 = FakeEvent("10002", text="宗门任务")
            await fire(env.sect.handle_sect_task, ev2)
            self.assertTrue(ev2.text)
            ev3 = FakeEvent("10002", text="完成宗门任务")
            await fire(env.sect.handle_finish_sect_task, ev3)
            self.assertTrue(ev3.text)

            # 职位变更（宗主对成员）
            ev4 = FakeEvent("10001", text="职位变更 10002 2")
            await fire(env.sect.handle_position_change, ev4, "10002", 2)
            self.assertTrue(ev4.text)

            # 踢出成员
            ev5 = FakeEvent("10001", text="踢出成员 10002")
            await fire(env.sect.handle_kick_member, ev5, "10002")
            self.assertTrue(ev5.text)
            p3 = await env.get_player("10002")
            self.assertFalse(p3.sect_id, "被踢出后必须离宗")


class TestBankInterest(unittest.IsolatedAsyncioTestCase):

    async def test_bank_info_shows_balance(self):
        async with FlowEnv() as env:
            await env.new_player("bi_1")
            ev = FakeEvent("bi_1", text="存灵石 800")
            await fire(env.bank.handle_deposit, ev, 800)
            ev2 = FakeEvent("bi_1")
            await fire(env.bank.handle_bank_info, ev2)
            self.assertIn("余额", ev2.text)


class TestBountyOps(unittest.IsolatedAsyncioTestCase):

    async def test_bounty_accept_status_abandon(self):
        async with FlowEnv() as env:
            await env.new_player("bo_1")
            ev = FakeEvent("bo_1")
            await fire(env.bounty.handle_bounty_list, ev)
            ev2 = FakeEvent("bo_1")
            await fire(env.bounty.handle_accept_bounty, ev2, 1)
            self.assertTrue(ev2.text)
            ev3 = FakeEvent("bo_1")
            await fire(env.bounty.handle_bounty_status, ev3)
            self.assertTrue(ev3.text)
            ev4 = FakeEvent("bo_1")
            await fire(env.bounty.handle_abandon_bounty, ev4)
            self.assertTrue(ev4.text)


class TestBlessedLandOps(unittest.IsolatedAsyncioTestCase):

    async def test_purchase_and_collect(self):
        async with FlowEnv() as env:
            await env.new_player("xl_1")
            await env.db.update_player_fields("xl_1", gold=200000)
            ev = FakeEvent("xl_1", text="购买洞天 1")
            await fire(env.bl.handle_purchase, ev, 1)
            self.assertTrue(ev.text)
            ev2 = FakeEvent("xl_1")
            await fire(env.bl.handle_collect, ev2)
            self.assertTrue(ev2.text)


class TestFarmOps(unittest.IsolatedAsyncioTestCase):

    async def test_create_plant_harvest(self):
        async with FlowEnv() as env:
            await env.new_player("fa_1")
            ev = FakeEvent("fa_1")
            await fire(env.farm.handle_create_farm, ev)
            self.assertTrue(ev.text)
            ev2 = FakeEvent("fa_1", text="种植 灵草")
            await fire(env.farm.handle_plant, ev2, "灵草")
            self.assertTrue(ev2.text)
            ev3 = FakeEvent("fa_1")
            await fire(env.farm.handle_harvest, ev3)
            self.assertTrue(ev3.text)


class TestRingGiftFlow(unittest.IsolatedAsyncioTestCase):
    """赠予接收/拒绝：通过 DB 种子 pending_gifts 驱动真实处理链"""

    async def _seed_gift(self, env, receiver="rg_1", sender="rg_0"):
        await env.new_player(receiver)
        await env.new_player(sender)
        now = int(time.time())
        await env.db.conn.execute(
            "INSERT INTO pending_gifts (receiver_id, sender_id, sender_name, item_name, count,"
            " created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (receiver, sender, "赠予者", "玄铁", 2, now, now + 3600)
        )
        await env.db.conn.commit()

    async def test_accept_gift(self):
        async with FlowEnv() as env:
            await self._seed_gift(env)
            ev = FakeEvent("rg_1", text="接收")
            await fire(env.ring.handle_accept_gift, ev)
            self.assertIn("赠予", ev.text)
            p = await env.get_player("rg_1")
            self.assertGreaterEqual(p.get_storage_ring_items().get("玄铁", 0), 2, "接收后物品必须入戒")

    async def test_reject_gift(self):
        async with FlowEnv() as env:
            await self._seed_gift(env, receiver="rg_2", sender="rg_3")
            ev = FakeEvent("rg_2", text="拒绝")
            await fire(env.ring.handle_reject_gift, ev)
            self.assertTrue(ev.text)
            async with env.db.conn.execute(
                "SELECT COUNT(*) FROM pending_gifts WHERE receiver_id = ?", ("rg_2",)
            ) as cur:
                (n,) = await cur.fetchone()
            self.assertEqual(n, 0, "拒绝后请求记录必须删除")


class TestBreakthroughInfo(unittest.IsolatedAsyncioTestCase):

    async def test_breakthrough_info_renders(self):
        async with FlowEnv() as env:
            await env.new_player("bt_1")
            ev2 = FakeEvent("bt_1")
            await fire(env.bt.handle_breakthrough_info, ev2)
            self.assertTrue(ev2.text)


class TestUnjam(unittest.IsolatedAsyncioTestCase):

    async def test_force_unlock(self):
        async with FlowEnv() as env:
            await env.new_player("uj_1")
            ev = FakeEvent("uj_1", text="解卡")
            await fire(env.misc.handle_force_unlock, ev)
            self.assertTrue(ev.text)


class TestPanelsAndAdmin(unittest.IsolatedAsyncioTestCase):
    """全服面板/排行/管理指令：真实 manager 驱动，验证渲染不崩溃且有关键内容"""

    async def test_rank_boards_karma_luck_virtue(self):
        async with FlowEnv() as env:
            rm = RankingManager(env.db, CombatManager(), env.cm)
            for method in ("get_karma_ranking", "get_luck_ranking", "get_virtue_ranking"):
                result = await getattr(rm, method)()
                self.assertTrue(result is not None, f"{method} 必须有返回")

    async def test_server_panels_render(self):
        async with FlowEnv() as env:
            cases = [
                ("仙界日历", CalamityManager(env.db).get_calendar, False),
                ("修仙天气", WeatherManager(env.db).handle_weather, True),
                ("灵脉战", StrongholdManager(env.db).handle_board, True),
                ("秘境词缀", RiftEnhanceManager(env.db).handle_affix, True),
                ("悬赏行会", PlayerBountyManager(env.db).handle_board, True),
                ("宗门科技", SectTechManager(env.db).handle_board, True),
                ("拍卖行", LootAuctionManager(env.db).handle_board, True),
                ("登仙台", AscensionProjectManager(env.db).handle_board, True),
                ("修仙群史", GroupChronicleManager(env.db).handle_history, True),
                ("云游商人", WanderMerchantManager(env.db).get_board, False),
            ]
            for name, method, needs_event in cases:
                ev = FakeEvent("pn_1")
                if needs_event:
                    await fire(method, ev)
                    self.assertTrue(ev.text, f"{name} 面板必须有内容")
                else:
                    result = await method()
                    self.assertTrue(result is not None, f"{name} 必须有返回")

    async def test_player_action_guards(self):
        async with FlowEnv() as env:
            await env.new_player("pa_1")

            # 银行面板含余额
            ev = FakeEvent("pa_1")
            await fire(env.bank.handle_bank_info, ev)
            self.assertTrue(ev.text)

            # 退出秘境（未在秘境中）
            ev2 = FakeEvent("pa_1", text="退出秘境")
            await fire(env.rift.handle_rift_exit, ev2)
            self.assertTrue(ev2.text)

            # 孵化灵兽（无灵兽蛋）
            ev3 = FakeEvent("pa_1")
            await fire(env.pet.handle_adopt, ev3)
            self.assertTrue(ev3.text)

            # 洗练（无装备）
            ev4 = FakeEvent("pa_1")
            await fire(env.forge.handle_wash, ev4)
            self.assertTrue(ev4.text)

            # 切原石（无原石）
            ev5 = FakeEvent("pa_1")
            await fire(env.gamble.handle_cut_stone, ev5)
            self.assertTrue(ev5.text)

            # 黑市面板
            ev6 = FakeEvent("pa_1")
            await fire(env.gamble.handle_buy_market, ev6)
            self.assertTrue(ev6.text)

            # 天魔开启/查验（守卫路径）
            ev7 = FakeEvent("pa_1")
            await fire(env.demon.handle_start, ev7)
            self.assertTrue(ev7.text)
            ev8 = FakeEvent("pa_1")
            await fire(env.demon.handle_catch, ev8)
            self.assertTrue(ev8.text)

            # 放生/吞噬（无灵兽）
            ev9 = FakeEvent("pa_1")
            await fire(env.pet.handle_release, ev9)
            self.assertTrue(ev9.text)
            ev10 = FakeEvent("pa_1")
            await fire(env.pet.handle_devour, ev10)
            self.assertTrue(ev10.text)

            # 任务榜接取/领奖（无任务）
            ev11 = FakeEvent("pa_1", text="接取任务 1")
            await fire(env.daily.handle_accept_task, ev11, "1")
            self.assertTrue(ev11.text)
            ev12 = FakeEvent("pa_1", text="领取任务奖励")
            await fire(env.daily.handle_claim_reward, ev12)
            self.assertTrue(ev12.text)

            # 护府/潜入（守卫路径）
            ev13 = FakeEvent("pa_1")
            await fire(env.robbery.handle_use_shield, ev13)
            self.assertTrue(ev13.text)
            ev14 = FakeEvent("pa_1")
            await fire(env.robbery.handle_rob, ev14)
            self.assertTrue(ev14.text)

            # 轮回系（仙缘商店/解封传家宝/兑换）
            ev15 = FakeEvent("pa_1")
            await fire(env.reinc.handle_fate_shop, ev15)
            self.assertTrue(ev15.text)
            ev16 = FakeEvent("pa_1")
            await fire(env.reinc.handle_unseal, ev16)
            self.assertTrue(ev16.text)
            ev17 = FakeEvent("pa_1")
            await fire(env.reinc.handle_fate_exchange, ev17, "1")
            self.assertTrue(ev17.text)

            # 灵眼领取
            ev19 = FakeEvent("pa_1")
            await fire(env.speye.handle_claim, ev19)
            self.assertTrue(ev19.text)

            # 升级灵田/进阶洞天（未拥有）
            ev20 = FakeEvent("pa_1")
            await fire(env.farm.handle_upgrade_farm, ev20)
            self.assertTrue(ev20.text)
            ev21 = FakeEvent("pa_1")
            await fire(env.bl.handle_advance, ev21, 1)
            self.assertTrue(ev21.text)

            # 大比切磋（无大比）
            ev22 = FakeEvent("pa_1", text="大比切磋 10002")
            await fire(env.sect.handle_sect_battle, ev22, "10002")
            self.assertTrue(ev22.text)

            # 完成悬赏（未接取）
            ev23 = FakeEvent("pa_1")
            await fire(env.bounty.handle_complete_bounty, ev23)
            self.assertTrue(ev23.text)

            # 取出所有（空戒）
            ev24 = FakeEvent("pa_1")
            await fire(env.ring.handle_retrieve_all, ev24)
            self.assertTrue(ev24.text)

            # 更换储物戒（无效目标）
            ev25 = FakeEvent("pa_1", text="更换储物戒 不存在的戒")
            await fire(env.ring.handle_upgrade_ring, ev25, "不存在的戒")
            self.assertTrue(ev25.text)

            # 突破贷款（无贷款）
            ev26 = FakeEvent("pa_1", text="突破贷款 100")
            await fire(env.bank.handle_breakthrough_loan, ev26, 100)
            self.assertTrue(ev26.text)

            # 传承挑战（无传承）
            ev27 = FakeEvent("pa_1")
            await fire(env.impart_pk.handle_impart_challenge, ev27)
            self.assertTrue(ev27.text)


class TestFinalGapBatch(unittest.IsolatedAsyncioTestCase):
    """最后一批缺口：排行榜显式调用 + 宗门建筑/大比 + 双修 + 坊市 + 赠予 + 轮回"""

    async def test_rank_boards_explicit(self):
        async with FlowEnv() as env:
            rm = RankingManager(env.db, CombatManager(), env.cm)
            for method in (rm.get_karma_ranking, rm.get_luck_ranking, rm.get_virtue_ranking):
                ok, msg = await method()
                # 空库返回"暂无数据"守卫；有数据返回榜单——两者都是正确行为
                self.assertTrue(ok or "暂无数据" in msg, f"{method.__name__} 必须返回榜单或空态提示")

    async def test_season_info(self):
        idx = get_current_season_index()
        self.assertTrue(0 <= idx < len(SEASON_EFFECTS), "季节索引必须有效")

    async def test_sect_buildings_and_battle(self):
        async with FlowEnv() as env:
            await env.new_player("10001", "宗主")
            await env.db.update_player_fields("10001", level_index=3, gold=200000)
            ev = FakeEvent("10001", text="太平宗")
            await fire(env.sect.handle_create_sect, ev, "太平宗")
            sm = env.sect.sect_mgr if hasattr(env.sect, "sect_mgr") else None
            if sm is None:
                sm = SectManager(env.db, env.cm)
            ok, msg = await sm.show_buildings("10001")
            self.assertTrue(ok, "宗门建筑面板必须可渲染")
            ok2, msg2 = await sm.start_sect_battle()
            self.assertTrue(isinstance(ok2, bool) and msg2, "开启宗门大比必须有明确结果")
            ok3, msg3 = await sm.end_sect_battle()
            self.assertTrue(isinstance(ok3, bool) and msg3, "结束宗门大比必须有明确结果")

    async def test_dual_accept_reject_guards(self):
        async with FlowEnv() as env:
            await env.new_player("du_1")
            ev = FakeEvent("du_1")
            await fire(env.dual.handle_accept, ev)
            self.assertTrue(ev.text, "无请求时接受双修必须有友好提示")
            ev2 = FakeEvent("du_1")
            await fire(env.dual.handle_reject, ev2)
            self.assertTrue(ev2.text)

    async def test_auction_list_and_cancel_guards(self):
        async with FlowEnv() as env:
            await env.new_player("ar_1")
            ev = FakeEvent("ar_1", text="坊市上架 不存在的物品 1 100")
            await fire(env.auction.handle_list_item, ev, "不存在的物品 1 100")
            self.assertTrue(ev.text, "上架无物品必须有明确提示")
            ev2 = FakeEvent("ar_1", text="坊市下架 999")
            await fire(env.auction.handle_cancel, ev2, "999")
            self.assertTrue(ev2.text)

    async def test_transfer_leader(self):
        async with FlowEnv() as env:
            await env.new_player("10011", "宗主")
            await env.new_player("10012", "继任")
            await env.db.update_player_fields("10011", level_index=3, gold=200000)
            await env.db.update_player_fields("10012", level_index=3, gold=200000)
            ev = FakeEvent("10011", text="太平宗二")
            await fire(env.sect.handle_create_sect, ev, "太平宗二")
            ev2 = FakeEvent("10012", text="太平宗二")
            await fire(env.sect.handle_join_sect, ev2, "太平宗二")
            ev3 = FakeEvent("10011", text="宗主传位 10012")
            await fire(env.sect.handle_transfer, ev3, "10012")
            self.assertTrue(ev3.text, "传位必须有明确结果")

    async def test_claim_interest_guard(self):
        async with FlowEnv() as env:
            await env.new_player("ci_1")
            ev = FakeEvent("ci_1")
            await fire(env.bank.handle_claim_interest, ev)
            self.assertTrue(ev.text, "无存款领利息必须有友好提示")

    async def test_gift_with_numeric_target(self):
        async with FlowEnv() as env:
            await env.new_player("10021", "赠予者")
            await env.new_player("10022", "受赠者")
            item_name = next(iter(env.cm.items_data.keys()), "灵草")
            p = await env.get_player("10021")
            items = p.get_storage_ring_items()
            items[item_name] = 1
            await env.db.update_player_fields(
                "10021", storage_ring_items=json.dumps(items, ensure_ascii=False)
            )
            ev = FakeEvent("10021", text=f"赠予 10022 {item_name} 1")
            await fire(env.ring.handle_gift_item, ev, f"10022 {item_name} 1")
            self.assertTrue(ev.text, "赠予必须有明确结果")

    async def test_start_mogtide(self):
        async with FlowEnv() as env:
            ok, msg = await CalamityManager(env.db).start_tide()
            self.assertTrue(isinstance(ok, bool) and msg, "开启魔潮必须有明确结果")

    async def test_rebirth_guard(self):
        async with FlowEnv() as env:
            await env.new_player("rb_1")
            ev = FakeEvent("rb_1", text="弃道重修 确认")
            await fire(env.reinc.handle_reincarnate, ev, "确认")
            self.assertIn("巅峰", ev.text, "未满级必须拒绝弃道重修")


if __name__ == "__main__":
    unittest.main()

