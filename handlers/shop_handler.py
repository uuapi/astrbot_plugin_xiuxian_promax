# handlers/shop_handler.py

import time
import re
from typing import Tuple
from astrbot.api.event import AstrMessageEvent
from astrbot.api import AstrBotConfig, logger
from ..data import DataBase
from ..core import ShopManager, EquipmentManager, PillManager, StorageRingManager
from ..models import Player
from ..config_manager import ConfigManager
from .utils import player_required

__all__ = ["ShopHandler"]

class ShopHandler:
    """商店处理器"""
    
    ITEM_ACQUIRE_HINTS = {
        'pill': "丹阁刷新、秘境稀有掉落",
        'exp_pill': "丹阁、炼丹系统、历练/秘境奖励",
        'utility_pill': "丹阁稀有、秘境/Boss 掉落",
        'legacy_pill': "百宝阁限量，购买后立即生效",
        'weapon': "器阁、Boss 掉落",
        'armor': "器阁、Boss 掉落",
        'accessory': "器阁、Boss 掉落",
        'main_technique': "百宝阁稀有刷新",
        'technique': "百宝阁、Boss 掉落",
        'material': "历练、秘境、悬赏、灵田收获与百宝阁限量",
    }

    def __init__(self, db: DataBase, config: AstrBotConfig, config_manager: ConfigManager):
        self.db = db
        self.config = config
        self.config_manager = config_manager
        self.shop_manager = ShopManager(config, config_manager)
        self.storage_ring_manager = StorageRingManager(db, config_manager)
        self.equipment_manager = EquipmentManager(db, config_manager, self.storage_ring_manager)
        self.pill_manager = PillManager(db, config_manager)
        access_control = self.config.get("ACCESS_CONTROL", {})
        self.shop_manager_ids = {
            str(user_id)
            for user_id in access_control.get("SHOP_MANAGERS", [])
        }

    def _cfg(self, key: str, default):
        """读取 VALUES 分组下的配置（schema 中 PAVILION_*/SHOP_* 嵌套在 VALUES 下），兼容顶层旧写法"""
        return self.config.get("VALUES", {}).get(key, self.config.get(key, default))

    async def _ensure_pavilion_refreshed(self, pavilion_id: str, item_getter, count: int) -> None:
        """确保阁楼已刷新"""
        last_refresh_time, current_items = await self.db.get_shop_data(pavilion_id)
        if current_items:
            updated = self.shop_manager.ensure_items_have_stock(current_items)
            if updated:
                await self.db.update_shop_data(pavilion_id, last_refresh_time, current_items)
        refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 1)
        if not current_items or self.shop_manager.should_refresh_shop(last_refresh_time, refresh_hours):
            new_items = self.shop_manager.generate_pavilion_items(item_getter, count)
            await self.db.update_shop_data(pavilion_id, int(time.time()), new_items)

    async def auto_restock(self) -> Tuple[int, str]:
        """v6.17 自动上货（定时任务调用）：
        1) 到刷新周期照常换货；2) 未到周期但货架售罄率≥80% 时提前补货（不变更刷新周期锚点）
        返回 (补货阁数, 描述)"""
        import time as _time
        restocked = 0
        details = []
        jobs = [
            ("pill_pavilion", "丹阁", self.shop_manager.get_pills_for_display, self._cfg("PAVILION_PILL_COUNT", 20)),
            ("weapon_pavilion", "器阁", self.shop_manager.get_weapons_for_display, self._cfg("PAVILION_WEAPON_COUNT", 20)),
            ("treasure_pavilion", "百宝阁", self.shop_manager.get_all_items_for_display, self._cfg("PAVILION_TREASURE_COUNT", 25)),
        ]
        for pavilion_id, name, getter, cnt in jobs:
            try:
                last_refresh, items = await self.db.get_shop_data(pavilion_id)
                refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 1)
                due = self.shop_manager.should_refresh_shop(last_refresh, refresh_hours)
                if not items:
                    # 空货架（从未初始化）：立即上货
                    new_items = self.shop_manager.generate_pavilion_items(getter, cnt)
                    await self.db.update_shop_data(pavilion_id, int(_time.time()), new_items)
                    restocked += 1
                    details.append(name)
                    continue
                if due:
                    new_items = self.shop_manager.generate_pavilion_items(getter, cnt)
                    await self.db.update_shop_data(pavilion_id, int(_time.time()), new_items)
                    restocked += 1
                    details.append(name)
                    continue
                # 售罄检测：有货商品占比 < 20% 则补货
                total = len(items)
                on_sale = sum(1 for it in items if it.get('stock', 0) > 0)
                if total > 0 and on_sale / total < 0.2:
                    new_items = self.shop_manager.generate_pavilion_items(getter, cnt)
                    await self.db.update_shop_data(pavilion_id, last_refresh, new_items)  # 保持刷新锚点不变
                    restocked += 1
                    details.append(f"{name}(售罄补货)")
            except Exception as e:
                from astrbot.api import logger
                logger.warning(f"自动上货 {name} 异常: {e}")
        return restocked, "、".join(details) if details else ""

    async def handle_pill_pavilion(self, event: AstrMessageEvent):
        """处理丹阁命令 - 展示丹药列表"""
        count = self._cfg("PAVILION_PILL_COUNT", 20)
        await self._ensure_pavilion_refreshed("pill_pavilion", self.shop_manager.get_pills_for_display, count)
        last_refresh, items = await self.db.get_shop_data("pill_pavilion")
        if not items:
            yield event.plain_result("丹阁暂无丹药出售。")
            return
        refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 1)
        display = self.shop_manager.format_pavilion_display("丹阁", items, refresh_hours, last_refresh)
        # 卡片成功只发图，失败/关闭降级为文本
        card = await self._pavilion_card("丹阁", items)
        if card:
            yield event.image_result(card)
        else:
            yield event.plain_result(display)

    async def handle_weapon_pavilion(self, event: AstrMessageEvent):
        """处理器阁命令 - 展示武器列表"""
        count = self._cfg("PAVILION_WEAPON_COUNT", 20)
        await self._ensure_pavilion_refreshed("weapon_pavilion", self.shop_manager.get_weapons_for_display, count)
        last_refresh, items = await self.db.get_shop_data("weapon_pavilion")
        if not items:
            yield event.plain_result("器阁暂无武器出售。")
            return
        refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 1)
        display = self.shop_manager.format_pavilion_display("器阁", items, refresh_hours, last_refresh)
        card = await self._pavilion_card("器阁", items)
        if card:
            yield event.image_result(card)
        else:
            yield event.plain_result(display)

    async def handle_treasure_pavilion(self, event: AstrMessageEvent):
        """处理百宝阁命令 - 展示所有物品"""
        count = self._cfg("PAVILION_TREASURE_COUNT", 25)
        await self._ensure_pavilion_refreshed("treasure_pavilion", self.shop_manager.get_all_items_for_display, count)
        last_refresh, items = await self.db.get_shop_data("treasure_pavilion")
        if not items:
            yield event.plain_result("百宝阁暂无物品出售。")
            return
        refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 1)
        display = self.shop_manager.format_pavilion_display("百宝阁", items, refresh_hours, last_refresh)
        card = await self._pavilion_card("百宝阁", items)
        if card:
            yield event.image_result(card)
        else:
            yield event.plain_result(display)

    async def _pavilion_card(self, pavilion_name: str, items: list):
        """阁楼列表的磨砂玻璃卡片（关闭或失败时不产出任何消息）"""
        try:
            from ..utils.image_generator import render_card_async, card_enabled
            if not card_enabled("CARD_SHOP"):
                return None
            lines = []
            for item in items[:14]:
                stock = item.get('stock', 0)
                if stock <= 0:
                    continue
                discount = item.get('discount', 1.0)
                price_tag = f"{item['price']:,}灵石"
                if discount < 1.0:
                    price_tag += f"({round(discount * 10, 1)}折)"
                element = item.get('element', '')
                elem_tag = f"[{element}]" if element else ""
                # v3.24.2 卡片补功效简述（此前卡片只有名字/品阶/价格）
                effect = self.shop_manager._get_item_effect_short(item)
                effect_tag = f" · {effect[:26]}" if effect else ""
                lines.append(f"{item['name']}{elem_tag} - {price_tag} · 库存{stock}{effect_tag}")
            if not lines:
                return None
            card_path = await render_card_async(
                "shop",
                f"{pavilion_name} · 今日货架",
                f"共 {len(lines)} 种在售",
                sections=[{"name": "在售清单", "lines": lines}],
                footer="购买 <物品名> [数量] · 售罄商品不显示",
            )
            if card_path:
                return card_path
            return None
        except Exception:
            return None

    async def _find_item_in_pavilions(self, item_name: str):
        """在所有阁楼中查找物品"""
        for pavilion_id in ["pill_pavilion", "weapon_pavilion", "treasure_pavilion"]:
            _, items = await self.db.get_shop_data(pavilion_id)
            if items:
                for item in items:
                    if item['name'] == item_name and item.get('stock', 0) > 0:
                        return pavilion_id, item
        return None, None

    @player_required
    async def handle_buy(self, player: Player, event: AstrMessageEvent, item_name: str = ""):
        """处理购买物品命令"""
        if not item_name or item_name.strip() == "":
            yield event.plain_result("请指定要购买的物品名称，例如：购买 青铜剑")
            return

        # 兼容全角空格/数字与“x10”写法
        normalized = item_name.strip().replace("　", " ")
        normalized = normalized.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        quantity = 1
        item_part = normalized

        def parse_qty(text: str):
            text = re.sub(r"\s+", " ", text)
            m = re.match(r"^(.*?)(?:\s+(\d+)|[xX＊*]\s*(\d+))$", text)
            if m:
                part = m.group(1).strip()
                qty_str = m.group(2) or m.group(3)
                return part, max(1, int(qty_str))
            return text.strip(), 1

        item_part, quantity = parse_qty(normalized)

        if quantity == 1:
            try:
                raw_msg = event.get_message_str().strip()
                raw_msg = raw_msg.lstrip('/').strip()  # 兼容 /购买 斜杠前缀，防止物品名带上命令前缀
                # v3.25.5 修复：先剥离长前缀"购买物品"，否则 compact 入口"购买物品玄铁"
                # 会被剥成"物品玄铁"导致必然查无此物
                if raw_msg.startswith("购买物品"):
                    raw_msg = raw_msg[len("购买物品"):].strip()
                elif raw_msg.startswith("购买"):
                    raw_msg = raw_msg[len("购买"):].strip()
                raw_msg = raw_msg.replace("　", " ")
                raw_msg = raw_msg.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
                parsed_name, parsed_qty = parse_qty(raw_msg)
                # 仅当重新解析得到有效物品名时才采用（防御性，避免前缀残留污染名称）
                if parsed_name and not parsed_name.startswith("/"):
                    item_part, quantity = parsed_name, parsed_qty
            except Exception:
                pass

        item_name = item_part

        pavilion_id, target_item = await self._find_item_in_pavilions(item_name)
        if not target_item:
            yield event.plain_result(
                f"没有找到【{item_name}】。\n"
                "💡 物品名需与货架完全一致，且该物品当前在售（每 1 小时刷新）：\n"
                "  · 丹药 → /丹阁    武器 → /器阁    材料/符箓等 → /百宝阁\n"
                "💡 也可先发 /物品信息 <名称> 查询物品详情与获取途径"
            )
            return

        stock = target_item.get('stock', 0)
        if quantity > stock:
            yield event.plain_result(f"【{item_name}】库存不足，当前库存: {stock}。")
            return

        price = target_item['price']
        total_price = price * quantity
        if player.gold < total_price:
            yield event.plain_result(
                f"灵石不足！\n【{target_item['name']}】价格: {price} 灵石\n"
                f"购买数量: {quantity}\n需要灵石: {total_price}\n你的灵石: {player.gold}"
            )
            return

        item_type = target_item['type']
        result_lines = []

        # ===== 品阶→境界门槛（与事务内判断共用一张表）=====
        RANK_REQ_LEVEL = {
            "玄阶": 3, "地阶": 9, "天阶": 18,
            "灵品": 3, "地品": 9, "天品": 18, "皇品": 24,
            "帝品": 30, "道品": 36, "仙品": 42, "混元先天": 48,
        }

        def _norm_type(t: str) -> str:
            if t in ['weapon', '武器', '法器']:
                return 'weapon'
            if t in ['armor', '防具']:
                return 'armor'
            if t in ['main_technique', 'technique', '心法', '功法']:
                return 'technique'
            if t in ['pill', 'exp_pill', 'utility_pill', '丹药']:
                return 'legacy_pill'
            if t in ['material', '材料', '符箓', '杂物']:
                return 'material'
            return t

        norm_type = _norm_type(item_type)

        # ===== 门槛前置检查（事务前，无任何扣除，附当前可买清单）=====
        if norm_type in ['weapon', 'armor', 'technique']:
            rank = target_item.get('rank', '黄阶')
            req_level = RANK_REQ_LEVEL.get(rank, 0)
            if player.level_index < req_level:
                # 收集同一阁楼中玩家当前买得起的同类物品
                _, shelf_items = await self.db.get_shop_data(pavilion_id)
                affordable = []
                for it in (shelf_items or []):
                    if it.get('stock', 0) <= 0:
                        continue
                    if _norm_type(it.get('type', '')) != norm_type:
                        continue
                    it_req = RANK_REQ_LEVEL.get(it.get('rank', ''), 0)
                    if player.level_index >= it_req:
                        affordable.append(f"【{it['name']}】({it.get('rank', '')} · {it['price']:,}灵石)")
                affordable_text = "\n".join(f"  · {a}" for a in affordable[:8]) if affordable else "  · 暂无（等货架刷新或先提升境界）"
                yield event.plain_result(
                    f"❌ 境界不足，无法购买！\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"【{target_item['name']}】为 {rank} 品阶，需要达到境界等级 {req_level}\n"
                    f"你当前境界等级：{player.level_index}（还差 {req_level - player.level_index} 级）\n"
                    f"💡 通过 /闭关 /历练 /探索秘境 提升修为后 /突破 提高境界\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"✅ 你当前可购买的同类物品：\n{affordable_text}"
                )
                return

        await self.db.begin_immediate()
        try:
            player = await self.db.get_player_by_id(event.get_sender_id())
            # v3.52.8 修复(旧价成交)：事务内复核货架现价与库存——
            # target_item/price 来自事务外快照，购买执行期间货架刷新（整点换货/售罄补货）
            # 会按旧价成交。以货架当前数据为准，物品已不在架上则拒绝。
            _, shelf_items = await self.db.get_shop_data(pavilion_id)
            shelf_item = next(
                (it for it in (shelf_items or []) if it.get('name') == item_name), None)
            if not shelf_item:
                await self.db.conn.rollback()
                yield event.plain_result(f"【{item_name}】已从货架下架（刚刷新），请重新查询。")
                return
            current_stock = shelf_item.get('stock', 0)
            if current_stock < quantity:
                await self.db.conn.rollback()
                yield event.plain_result(f"【{item_name}】库存不足，当前库存: {current_stock}。")
                return
            price = shelf_item['price']
            total_price = price * quantity
            if player.gold < total_price:
                await self.db.conn.rollback()
                yield event.plain_result(
                    f"灵石不足！\n【{target_item['name']}】价格: {price} 灵石\n"
                    f"购买数量: {quantity}\n需要灵石: {total_price}\n你的灵石: {player.gold}"
                )
                return

            reserved, _, remaining = await self.db.decrement_shop_item_stock(pavilion_id, item_name, quantity, external_transaction=True)
            if not reserved:
                await self.db.conn.rollback()
                yield event.plain_result(f"【{item_name}】已售罄，请等待刷新。")
                return

            # ===== 核心修复1：极其健壮的类型兼容映射 =====
            item_type = norm_type  # 已在事务前归一化

            # ===== 门槛防线（前置检查已拦截，此处仅事务内兜底）=====
            if item_type in ['weapon', 'armor', 'technique']:
                rank = target_item.get('rank', '黄阶')
                req_level = RANK_REQ_LEVEL.get(rank, 0)

                if player.level_index < req_level:
                    await self.db.conn.rollback()
                    yield event.plain_result(f"❌ 你的境界不足以驾驭此等神物！购买【{rank}】物品需要达到境界等级 {req_level}。")
                    return
            # ==========================================

            if item_type in ['weapon', 'armor', 'technique']:
                success, msg = await self.storage_ring_manager.store_item(player, target_item['name'], quantity, external_transaction=True)
                if success:
                    type_name = {"weapon": "武器", "armor": "防具", "technique": "功法"}.get(item_type, "装备")
                    result_lines.append(f"成功购买{type_name}【{target_item['name']}】x{quantity}，已存入储物戒。")
                else:
                    # v3.52.7 修复(收钱不给货)：入库失败必须整体回滚——库存与灵石一并还原，
                    # 原实现照常扣款扣库存且不发货
                    await self.db.conn.rollback()
                    yield event.plain_result(
                        f"❌ 购买失败：无法将【{target_item['name']}】存入储物戒（{msg}）\n"
                        f"灵石与库存未扣除。"
                    )
                    return
            
            elif item_type == 'legacy_pill':
                # v6.18 修复：购买的丹药存入丹药背包由玩家自行使用（不再立即生效）
                inventory = player.get_pills_inventory()
                inventory[target_item['name']] = inventory.get(target_item['name'], 0) + quantity
                player.set_pills_inventory(inventory)
                result_lines.append(
                    f"成功购买丹药【{target_item['name']}】x{quantity}，已存入丹药背包。\n"
                    f"💡 使用 /服用丹药 {target_item['name']} 自行服用"
                )
            
            elif item_type == 'material':
                success, msg = await self.storage_ring_manager.store_item(player, target_item['name'], quantity, external_transaction=True)
                if success:
                    result_lines.append(f"成功购买材料【{target_item['name']}】x{quantity}，已存入储物戒。")
                else:
                    # v3.52.7 修复(收钱不给货)：同装备分支，整体回滚
                    await self.db.conn.rollback()
                    yield event.plain_result(
                        f"❌ 购买失败：无法将【{target_item['name']}】存入储物戒（{msg}）\n"
                        f"灵石与库存未扣除。"
                    )
                    return
            else:
                await self.db.conn.rollback()
                yield event.plain_result(f"未知的物品类型：{item_type}")
                return

            player.gold -= total_price
            # 原子扣款 + 字段级写回储物戒/丹药背包（事务内，统一提交）
            await self.db.add_player_gold(player.user_id, -total_price, commit=False)
            await self.db.update_player_fields(
                player.user_id,
                storage_ring_items=player.storage_ring_items,
                pills_inventory=player.pills_inventory,
                commit=False
            )
            await self.db.conn.commit()
            
            result_lines.append(f"花费灵石: {total_price}，剩余: {player.gold}")
            result_lines.append(f"剩余库存: {remaining}" if remaining > 0 else "该物品已售罄！")
            yield event.plain_result("\n".join(result_lines))
        except Exception as e:
            await self.db.conn.rollback()
            logger.error(f"购买异常: {e}")
            raise

    def _get_acquire_hint(self, item_type: str) -> str:
        """根据类型返回获取提示"""
        return self.ITEM_ACQUIRE_HINTS.get(item_type, "商店刷新或活动奖励")

    async def handle_item_info(self, event: AstrMessageEvent, item_name: str = ""):
        """查询物品/丹药的具体效果与获取方式"""
        if not item_name or item_name.strip() == "":
            yield event.plain_result(
                "请指定要查询的物品名称\n"
                "用法：物品信息 <名称>\n"
                "示例：物品信息 筑基丹"
            )
            return

        item = self.shop_manager.find_item_by_name(item_name.strip())
        if not item:
            yield event.plain_result(f"未找到物品【{item_name}】，请检查名称或等待刷新。")
            return

        detail_text = self.shop_manager.get_item_details(item)
        acquire_hint = self._get_acquire_hint(item.get('type', ''))

        lines = [
            detail_text,
            f"获取途径：{acquire_hint}",
            "💡 使用 /丹阁、/器阁、/百宝阁 查看当前售卖物品"
        ]
        yield event.plain_result("\n".join(lines))

    # v3.52.8 清理：删除死代码 _apply_legacy_pill_effects（v6.18 起购买丹药一律
    # 入丹药背包由 /服用丹药 生效，此函数已无任何调用方）