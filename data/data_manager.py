# data/data_manager.py

import asyncio

import aiosqlite
import json
from dataclasses import fields
from pathlib import Path
from typing import Tuple, List, Optional
from astrbot.api import logger
from ..models import Player
from .database_extended import DatabaseExtended

# 获取 Player 模型的所有字段名（用于过滤数据库中的多余字段，作为迁移未完成时的兼容）
PLAYER_FIELDS = {f.name for f in fields(Player)}

# ================= v3.52.9 写门闸（消除共享连接"事务裹挟"） =================
# 问题：全项目共用一条 aiosqlite 连接（isolation_level=None 真 autocommit），
# 协程 A 经 begin_immediate 开启显式事务期间，协程 B 的普通写语句会落入同一
# 连接的打开事务——随 A 提交/回滚（B 的语义被裹挟，回滚可吞掉 B 已承诺的扣款）。
# 方案：代理连接拦截 execute/commit/rollback——
#   · BEGIN*        → 获取门闸锁并标记持有者（事务开启）
#   · COMMIT/ROLLBACK → 持有者提交后释放锁（事务结束）
#   · 其他语句      → 持有者直通；非持有者先排队获取锁再执行（绝不落入他人事务窗口）
# 等待上限 15 秒，超时快速失败（防长事务泄漏演变为全局挂死）。

_GATE_WAIT_TIMEOUT = 15.0


class _GateOp:
    """门闸感知的 awaitable（延迟创建底层 aiosqlite 操作：过闸后才发起，
    排队超时路径不泄漏未 await 的操作对象）

    同时支持两种消费方式（与 aiosqlite.Cursor 行为对齐）：
      cur = await conn.execute(...)          # __await__
      async with conn.execute(...) as cur:   # __aenter__/__aexit__（全仓 109 处）
    """

    __slots__ = ("_gate", "_conn", "_sql", "_params", "_kind", "_cursor")

    def __init__(self, gate: "_WriteGate", conn, sql: str, params, kind: str):
        self._gate = gate
        self._conn = conn
        self._sql = sql
        self._params = params
        self._kind = kind
        self._cursor = None

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        self._cursor = await self._run()
        return self._cursor

    async def __aexit__(self, exc_type, exc, tb):
        if self._cursor is not None:
            try:
                await self._cursor.close()
            except Exception:
                pass
        return False

    async def _acquire_gate(self):
        """排队获取门闸（非持有者路径）；超时快速失败"""
        try:
            await asyncio.wait_for(self._gate._lock.acquire(), timeout=_GATE_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError("数据库写门闸等待超时(15s)：存在未及时提交的长事务")

    async def _run(self):
        gate = self._gate
        if self._kind == "begin":
            task = asyncio.current_task()
            if gate._owner is task:
                # 同协程嵌套开启事务：直通让底层抛 "within a transaction"（沿用旧行为）
                return await self._conn.execute(self._sql, self._params)
            await self._acquire_gate()
            try:
                result = await self._conn.execute(self._sql, self._params)
            except Exception:
                gate._lock.release()
                raise
            gate._owner = task
            return result
        if self._kind == "end":
            task = asyncio.current_task()
            if gate._owner is task:
                try:
                    if self._sql == "COMMIT":
                        return await self._conn.commit()
                    return await self._conn.rollback()
                finally:
                    gate._owner = None
                    if gate._lock.locked():
                        gate._lock.release()
            # 非持有者的 commit/rollback 也必须排队：若直接直通，其 COMMIT 可能
            # 在线程队列中落到他人刚 BEGIN 的事务上，把对方事务提前提交
            await self._acquire_gate()
            try:
                if self._sql == "COMMIT":
                    return await self._conn.commit()
                return await self._conn.rollback()
            finally:
                gate._lock.release()
        # 普通语句：持有者直通；其余排队占闸执行，绝不落入他人事务窗口
        if gate._owner is asyncio.current_task():
            return await self._conn.execute(self._sql, self._params)
        await self._acquire_gate()
        try:
            return await self._conn.execute(self._sql, self._params)
        finally:
            gate._lock.release()


class _WriteGate:
    """门闸状态：asyncio.Lock + 事务持有者协程标记"""

    __slots__ = ("_lock", "_owner")

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner = None

    def execute(self, conn, sql: str, parameters=()) -> _GateOp:
        u = sql.lstrip(" \t\r\n;").upper()
        if u.startswith("BEGIN"):
            return _GateOp(self, conn, sql, parameters, "begin")
        if u.startswith("COMMIT") or u.startswith("ROLLBACK") or u.startswith("END"):
            return _GateOp(self, conn, "COMMIT" if u.startswith("COMMIT") else "ROLLBACK", (), "end")
        return _GateOp(self, conn, sql, parameters, "write")

    def commit(self, conn) -> _GateOp:
        return _GateOp(self, conn, "COMMIT", (), "end")

    def rollback(self, conn) -> _GateOp:
        return _GateOp(self, conn, "ROLLBACK", (), "end")


class _ConnProxy:
    """aiosqlite 连接门闸代理：除 execute/commit/rollback 外全部属性透传"""

    __slots__ = ("_gate", "_conn")

    def __init__(self, gate: _WriteGate, conn):
        object.__setattr__(self, "_gate", gate)
        object.__setattr__(self, "_conn", conn)

    def execute(self, sql, parameters=()):
        return self._gate.execute(self._conn, sql, parameters)

    def commit(self):
        return self._gate.commit(self._conn)

    def rollback(self):
        return self._gate.rollback(self._conn)

    async def close(self):
        return await self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


class DataBase:
    """数据库管理类，提供基础玩家操作"""

    def __init__(self, db_file: str = "xiuxian_data_lite.db"):
        self.db_path = Path(db_file)
        self.conn = None  # _ConnProxy（门闸代理）
        self.ext: Optional[DatabaseExtended] = None  # 扩展操作类

    async def begin_immediate(self, retries: int = 20):
        """v3.25.5：共享单连接上的安全显式事务入口（全部 BEGIN IMMEDIATE 统一走此方法）。

        v3.52.9：写门闸接管 BEGIN 的互斥——协程 A 的事务期间，其他协程的
        BEGIN 与写语句都会在门闸排队等待，事务段互不裹挟（此前他人协程的
        autocommit 写会落进 A 的打开事务随其提交/回滚）。等待上限 15 秒，
        超时快速失败防长事务泄漏演变为全局挂死。"""
        for attempt in range(retries):
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                return
            except aiosqlite.OperationalError as e:
                if "within a transaction" not in str(e):
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))
        raise RuntimeError("数据库事务开启超时：并发事务冲突重试耗尽（可能有事务泄漏）")

    async def connect(self):
        """连接数据库"""
        # isolation_level=None = 真 autocommit：单条写自动原子提交，显式 BEGIN IMMEDIATE 事务段
        # 才能正常开启（legacy 隐式事务模式下与显式 BEGIN 叠加会报 "transaction within a transaction"）
        conn = await aiosqlite.connect(self.db_path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        # WAL 模式 + busy_timeout：降低读写互斥，写锁竞争时等待而非立即抛 database is locked
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=NORMAL")
        # v3.52.9：门闸代理接管（PRAGMA 在代理创建前直连执行）
        self.conn = _ConnProxy(_WriteGate(), conn)
        self.ext = DatabaseExtended(self.conn)  # 初始化扩展操作

    async def ensure_performance_indexes(self):
        """v3.24.0 高频查询索引（必须在迁移建表之后调用；
        v3.23 曾放在 connect() 中导致全新安装因 players 表不存在而启动崩溃）"""
        indexes = [
            ("idx_player_sect", "players(sect_id)"),
            ("idx_player_name", "players(user_name)"),
            ("idx_wbd_user", "world_boss_damage(user_id)"),
            ("idx_tasks_user_type", "daily_tasks(user_id, target_type)"),
        ]
        for name, cols in indexes:
            try:
                await self.conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {cols}")
            except Exception as e:
                logger.warning(f"[database] 创建索引 {name} 失败（表可能未建）: {e}")
        await self.conn.commit()

    async def close(self):
        """关闭数据库连接"""
        if self.conn:
            try:
                await self.conn.close()
            finally:
                self.conn = None
                self.ext = None

    async def reconnect(self):
        """重连数据库（用于连接意外断开时）"""
        await self.close()
        await self.connect()

    def _connection_alive(self) -> bool:
        """检测底层aiosqlite连接是否仍然可用（依赖 aiosqlite 的 _connection 私有属性，仅作快速预判；
        真正的可用性由 ensure_connection 中的 SELECT 1 探活兜底）"""
        if not self.conn:
            return False
        return getattr(self.conn, "_connection", None) is not None

    async def ensure_connection(self):
        """确保数据库连接可用，必要时自动重连"""
        if self.conn and self._connection_alive():
            # 私有属性仍在时再做一次真实探活，捕获底层已断开的情况
            try:
                await self.conn.execute("SELECT 1")
                return
            except Exception:
                logger.warning("[database] 数据库探活失败，正在自动重连...")
        elif self.conn:
            logger.warning("[database] 检测到数据库连接断开，正在自动重连...")
        await self.reconnect()

    async def create_player(self, player: Player):
        """创建新玩家"""
        await self.conn.execute(
            """
            INSERT INTO players (
                user_id, level_index, spiritual_root, cultivation_type, user_name, title, lifespan,
                experience, gold, state, cultivation_start_time, last_check_in_date, level_up_rate,
                weapon, armor, main_technique, techniques,
                hp, mp, atk, atkpractice,
                spiritual_qi, max_spiritual_qi, blood_qi, max_blood_qi,
                magic_damage, physical_damage, magic_defense, physical_defense, mental_power,
                sect_id, sect_position, sect_contribution, sect_task, sect_elixir_get,
                blessed_spot_flag, blessed_spot_name,
                active_pill_effects, permanent_pill_gains, has_resurrection_pill, has_debuff_shield, pills_inventory,
                storage_ring, storage_ring_items,
                daily_pill_usage, last_daily_reset, shield_expire_time,
                tutorial_step, reincarnation_count, fate_points, permanent_bonus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player.user_id,
                player.level_index,
                player.spiritual_root,
                player.cultivation_type,
                player.user_name,
                player.title,
                player.lifespan,
                player.experience,
                player.gold,
                player.state,
                player.cultivation_start_time,
                player.last_check_in_date,
                player.level_up_rate,
                player.weapon,
                player.armor,
                player.main_technique,
                player.techniques,
                player.hp,
                player.mp,
                player.atk,
                player.atkpractice,
                player.spiritual_qi,
                player.max_spiritual_qi,
                player.blood_qi,
                player.max_blood_qi,
                player.magic_damage,
                player.physical_damage,
                player.magic_defense,
                player.physical_defense,
                player.mental_power,
                player.sect_id,
                player.sect_position,
                player.sect_contribution,
                player.sect_task,
                player.sect_elixir_get,
                player.blessed_spot_flag,
                player.blessed_spot_name,
                player.active_pill_effects,
                player.permanent_pill_gains,
                int(player.has_resurrection_pill),
                int(player.has_debuff_shield),
                player.pills_inventory,
                player.storage_ring,
                player.storage_ring_items,
                player.daily_pill_usage,
                player.last_daily_reset,
                player.shield_expire_time,
                player.tutorial_step,
                player.reincarnation_count,
                player.fate_points,
                player.permanent_bonus
            )
        )
        await self.conn.commit()

    async def get_player_by_id(self, user_id: str) -> Player:
        """根据用户ID获取玩家信息"""
        async with self.conn.execute(
            "SELECT * FROM players WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                filtered_data = {k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}
                return Player(**filtered_data)
            return None

    async def get_player_by_name(self, user_name: str) -> Player:
        """根据道号获取玩家信息"""
        async with self.conn.execute(
            "SELECT * FROM players WHERE user_name = ?",
            (user_name,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                filtered_data = {k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}
                return Player(**filtered_data)
            return None

    async def update_player(self, player: Player, commit: bool = True):
        """更新玩家信息（支持事务传递）"""
        await self.conn.execute(
            """
            UPDATE players SET
                level_index = ?,
                spiritual_root = ?,
                cultivation_type = ?,
                user_name = ?,
                title = ?,
                lifespan = ?,
                experience = ?,
                gold = ?,
                state = ?,
                cultivation_start_time = ?,
                last_check_in_date = ?,
                level_up_rate = ?,
                weapon = ?,
                armor = ?,
                main_technique = ?,
                techniques = ?,
                hp = ?,
                mp = ?,
                atk = ?,
                atkpractice = ?,
                spiritual_qi = ?,
                max_spiritual_qi = ?,
                blood_qi = ?,
                max_blood_qi = ?,
                magic_damage = ?,
                physical_damage = ?,
                magic_defense = ?,
                physical_defense = ?,
                mental_power = ?,
                sect_id = ?,
                sect_position = ?,
                sect_contribution = ?,
                sect_task = ?,
                sect_elixir_get = ?,
                blessed_spot_flag = ?,
                blessed_spot_name = ?,
                active_pill_effects = ?,
                permanent_pill_gains = ?,
                has_resurrection_pill = ?,
                has_debuff_shield = ?,
                pills_inventory = ?,
                storage_ring = ?,
                storage_ring_items = ?,
                daily_pill_usage = ?,
                last_daily_reset = ?,
                shield_expire_time = ?,
                tutorial_step = ?,
                reincarnation_count = ?,
                fate_points = ?,
                permanent_bonus = ?,
                karma = ?,
                life_skill = ?,
                virtue_points = ?
            WHERE user_id = ?
            """,
            (
                player.level_index,
                player.spiritual_root,
                player.cultivation_type,
                player.user_name,
                player.title,
                player.lifespan,
                player.experience,
                player.gold,
                player.state,
                player.cultivation_start_time,
                player.last_check_in_date,
                player.level_up_rate,
                player.weapon,
                player.armor,
                player.main_technique,
                player.techniques,
                player.hp,
                player.mp,
                player.atk,
                player.atkpractice,
                player.spiritual_qi,
                player.max_spiritual_qi,
                player.blood_qi,
                player.max_blood_qi,
                player.magic_damage,
                player.physical_damage,
                player.magic_defense,
                player.physical_defense,
                player.mental_power,
                player.sect_id,
                player.sect_position,
                player.sect_contribution,
                player.sect_task,
                player.sect_elixir_get,
                player.blessed_spot_flag,
                player.blessed_spot_name,
                player.active_pill_effects,
                player.permanent_pill_gains,
                int(player.has_resurrection_pill),
                int(player.has_debuff_shield),
                player.pills_inventory,
                player.storage_ring,
                player.storage_ring_items,
                player.daily_pill_usage,
                player.last_daily_reset,
                player.shield_expire_time,
                player.tutorial_step,
                player.reincarnation_count,
                player.fate_points,
                player.permanent_bonus,
                getattr(player, "karma", 0),
                getattr(player, "life_skill", ""),
                getattr(player, "virtue_points", 0),
                player.user_id
            )
        )
        if commit:
            await self.conn.commit()

    # ===== 增量更新工具（避免全字段覆盖导致的并发回滚）=====

    async def update_player_fields(self, user_id: str, commit: bool = True, **fields):
        """只更新指定字段（字段名必须在 Player 模型中存在），其余字段不受影响"""
        if not fields:
            return
        valid = {k: v for k, v in fields.items() if k in PLAYER_FIELDS}
        if not valid:
            return
        set_clause = ", ".join(f"{k} = ?" for k in valid)
        await self.conn.execute(
            f"UPDATE players SET {set_clause} WHERE user_id = ?",
            (*valid.values(), user_id)
        )
        if commit:
            await self.conn.commit()

    async def add_player_gold(self, user_id: str, delta: int, commit: bool = True):
        """原子增减灵石（不会覆盖其他字段的并发修改，负数结果钳为0）"""
        await self.conn.execute(
            "UPDATE players SET gold = MAX(0, gold + ?) WHERE user_id = ?",
            (delta, user_id)
        )
        if commit:
            await self.conn.commit()

    async def add_player_exp(self, user_id: str, delta: int, commit: bool = True):
        """原子增减修为（负数结果钳为0）"""
        await self.conn.execute(
            "UPDATE players SET experience = MAX(0, experience + ?) WHERE user_id = ?",
            (delta, user_id)
        )
        if commit:
            await self.conn.commit()

    async def deduct_player_gold_if(self, user_id: str, amount: int, commit: bool = True) -> bool:
        """条件原子扣款：仅当 gold >= amount 时扣款并返回 True，否则不动数据返回 False"""
        if amount <= 0:
            return True
        cursor = await self.conn.execute(
            "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
            (amount, user_id, amount)
        )
        if commit:
            await self.conn.commit()
        return cursor.rowcount > 0

    async def spend_player_gold_for_fields(self, user_id: str, cost: int, commit: bool = True, **fields) -> bool:
        """条件扣款并写字段（单语句原子）：仅当 gold >= cost 时扣款并写入 fields，返回是否成功"""
        if cost < 0:
            return False
        valid = {k: v for k, v in fields.items() if k in PLAYER_FIELDS}
        if not valid:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in valid)
        if cost == 0:
            cursor = await self.conn.execute(
                f"UPDATE players SET {set_clause} WHERE user_id = ?",
                (*valid.values(), user_id)
            )
        else:
            cursor = await self.conn.execute(
                f"UPDATE players SET gold = gold - ?, {set_clause} WHERE user_id = ? AND gold >= ?",
                (cost, *valid.values(), user_id, cost)
            )
        if commit:
            await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_player(self, user_id: str):
        """删除玩家"""
        await self.conn.execute(
            "DELETE FROM players WHERE user_id = ?",
            (user_id,)
        )
        await self.conn.commit()

    async def delete_player_cascade(self, user_id: str):
        """级联删除玩家及所有关联数据"""
        async def safe_execute(sql: str, params: tuple):
            try:
                await self.conn.execute(sql, params)
            except Exception as e:
                sql_preview = sql.strip().split(" ")[0]
                logger.warning(f"[delete_player_cascade] 忽略执行 {sql_preview}: {e}")

        statements = [
            ("UPDATE spirit_eyes SET owner_id = NULL, owner_name = NULL, claim_time = NULL WHERE owner_id = ?", (user_id,)),
            ("DELETE FROM blessed_lands WHERE user_id = ?", (user_id,)),
            ("DELETE FROM spirit_farms WHERE user_id = ?", (user_id,)),
            ("DELETE FROM bank_accounts WHERE user_id = ?", (user_id,)),
            ("UPDATE bank_loans SET status = 'bad_debt' WHERE user_id = ? AND status = 'active'", (user_id,)),
            ("DELETE FROM bounty_tasks WHERE user_id = ?", (user_id,)),
            ("DELETE FROM dual_cultivation WHERE user_id = ?", (user_id,)),
            ("DELETE FROM dual_cultivation_requests WHERE from_id = ? OR target_id = ?", (user_id, user_id)),
            ("DELETE FROM user_cd WHERE user_id = ?", (user_id,)),
            ("DELETE FROM buff_info WHERE user_id = ?", (user_id,)),
            ("DELETE FROM impart_info WHERE user_id = ?", (user_id,)),
            ("DELETE FROM combat_cooldowns WHERE user_id = ?", (user_id,)),
            # 赠予：返还给对方侧处理——直接删除会导致物品蒸发，先退款式的物归原主太复杂，
            # 这里统一退化为"删除请求"，但只删该用户相关的（过期返还逻辑会兜住 sender 侧物品）
            ("DELETE FROM pending_gifts WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id)),
            ("DELETE FROM player_pets WHERE user_id = ?", (user_id,)),
            # 补全：此前遗漏的关联表，删除后留下孤儿数据
            ("DELETE FROM daily_tasks WHERE user_id = ?", (user_id,)),
            ("DELETE FROM world_boss_damage WHERE user_id = ?", (user_id,)),
            ("DELETE FROM npc_partners WHERE user_id = ?", (user_id,)),
            ("DELETE FROM master_relations WHERE disciple_id = ? OR master_id = ?", (user_id, user_id)),
            ("DELETE FROM auction_items WHERE seller_id = ?", (user_id,)),
            ("DELETE FROM escort_records WHERE user_id = ?", (user_id,)),
            ("DELETE FROM reincarnation_records WHERE user_id = ?", (user_id,)),
            ("DELETE FROM system_config WHERE key LIKE ?", (f"firstweek_{user_id}",)),
        ]

        for sql, params in statements:
            await safe_execute(sql, params)

        # 其所建宗门转给系统（避免宗门悬空指向已删除玩家）
        await safe_execute(
            "UPDATE sects SET sect_owner = 'system' WHERE sect_owner = ?", (user_id,)
        )
        # 其他玩家在该宗门/师徒里的引用由各自逻辑按 ID 容错（找不到玩家即忽略）

        await self.conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def get_all_players(self):
        """获取所有玩家"""
        async with self.conn.execute("SELECT * FROM players") as cursor:
            rows = await cursor.fetchall()
            return [Player(**{k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}) for row in rows]

    async def get_all_player_ids(self) -> List[str]:
        """v3.24.0 轻量查询：全服玩家 ID 列表（不发整行对象，供全服发奖/统计场景）"""
        async with self.conn.execute("SELECT user_id FROM players") as cursor:
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # ===== 商店数据操作 =====

    async def get_shop_data(self, shop_id: str = "global") -> Tuple[int, List[dict]]:
        """获取商店数据"""
        async with self.conn.execute(
            "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
            (shop_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                last_refresh_time = row[0]
                try:
                    current_items = json.loads(row[1]) if row[1] else []
                except (json.JSONDecodeError, TypeError, ValueError):
                    current_items = []
                return last_refresh_time, current_items
            return 0, []

    async def update_shop_data(self, shop_id: str, last_refresh_time: int, current_items: List[dict]):
        """更新商店数据"""
        items_json = json.dumps(current_items, ensure_ascii=False)
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO shop (shop_id, last_refresh_time, current_items)
            VALUES (?, ?, ?)
            """,
            (shop_id, last_refresh_time, items_json)
        )
        await self.conn.commit()

    async def decrement_shop_item_stock(self, shop_id: str, item_name: str, quantity: int = 1, external_transaction: bool = False) -> tuple[bool, int, int]:
        """尝试扣减指定商店物品的库存（原子操作，可批量）"""
        quantity = max(1, int(quantity))
        if not external_transaction:
            await self.begin_immediate()
        try:
            async with self.conn.execute(
                "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
                (shop_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                if not external_transaction:
                    await self.conn.rollback()
                return False, 0, 0

            last_refresh_time = row[0]
            try:
                current_items = json.loads(row[1]) if row[1] else []
            except (json.JSONDecodeError, TypeError, ValueError):
                current_items = []

            target_index = -1
            for idx, item in enumerate(current_items):
                if item.get('name') == item_name:
                    target_index = idx
                    break

            if target_index == -1:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, 0

            stock = current_items[target_index].get('stock', 0)
            if stock is None or stock <= 0:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, max(stock or 0, 0)

            if stock < quantity:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, stock

            new_stock = stock - quantity
            current_items[target_index]['stock'] = new_stock

            items_json = json.dumps(current_items, ensure_ascii=False)
            await self.conn.execute(
                "UPDATE shop SET current_items = ?, last_refresh_time = ? WHERE shop_id = ?",
                (items_json, last_refresh_time, shop_id)
            )
            if not external_transaction:
                await self.conn.commit()
            return True, last_refresh_time, new_stock
        except Exception:
            if not external_transaction:
                await self.conn.rollback()
            raise

    async def increment_shop_item_stock(self, shop_id: str, item_name: str, quantity: int = 1):
        """回滚库存（在购买失败时恢复库存），支持批量"""
        quantity = max(1, int(quantity))
        await self.begin_immediate()
        try:
            async with self.conn.execute(
                "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
                (shop_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await self.conn.rollback()
                return

            last_refresh_time = row[0]
            try:
                current_items = json.loads(row[1]) if row[1] else []
            except (json.JSONDecodeError, TypeError, ValueError):
                current_items = []

            for item in current_items:
                if item.get('name') == item_name:
                    current_stock = item.get('stock', 0) or 0
                    item['stock'] = current_stock + quantity
                    break

            items_json = json.dumps(current_items, ensure_ascii=False)
            await self.conn.execute(
                "UPDATE shop SET current_items = ?, last_refresh_time = ? WHERE shop_id = ?",
                (items_json, last_refresh_time, shop_id)
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise