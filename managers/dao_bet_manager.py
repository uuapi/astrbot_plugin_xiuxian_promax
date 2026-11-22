# managers/dao_bet_manager.py
"""天道盘（v3.36.0 新玩法）：全服每日赌盘

- 每日开盘：押「赤」或「玄」，100 - 50000 灵石，每人每色可多押（累计）
- 当日 24 点（惰性触发：次日首次交互时结算）天道掷骰定胜负
- 赢方按注额比例瓜分总奖池的 90%（10% 天道抽成，灵石回收口）
- 池体 JSON 读-改-写全部走 syscfg_cas（同 tribbet 模式），扣款条件原子、CAS 失败退款
"""

import json
import random
import time
from typing import Tuple, Optional

from astrbot.api import logger

from ..data.data_manager import DataBase

__all__ = ["DaoBetManager"]


class DaoBetManager:
    """天道盘：孤注一掷，还是落袋为安？"""

    STATE_PREFIX = "daopan_"
    MIN_BET, MAX_BET = 100, 50000
    HOUSE_CUT = 0.10
    SIDES = {"红": "red", "赤": "red", "玄": "blue", "蓝": "blue"}

    def __init__(self, db: DataBase):
        self.db = db

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _key(self, date: str) -> str:
        return f"{self.STATE_PREFIX}{date}"

    async def _load_raw(self, date: str) -> str:
        return await self.db.ext.get_system_config(self._key(date)) or ""

    @staticmethod
    def _parse(raw: str) -> Optional[dict]:
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    async def _get_or_create(self) -> Tuple[str, dict]:
        date = self._today()
        raw = await self._load_raw(date)
        state = self._parse(raw)
        if not state:
            state = {"date": date, "red_pool": 0, "blue_pool": 0, "bets": {}, "settled": False}
        return date, state

    async def _settle_if_due(self) -> str:
        """次日惰性结算：天道掷骰定胜负，赢方按比例瓜分 90% 奖池

        v3.52.8 修复(奖金滞留)：原实现先 CAS 置 settled 再逐个发奖，中途崩溃
        则奖金永久滞留（settled 挡住一切重入）。现在结算时把待发清单与开奖
        参数写入状态（pending_payouts/win_side/win_pool/prize_pool），发放
        逐笔 CAS 落盘 paid 清单；已结算但 pending 未清的盘自动进入补发流程
        （sweep 标记 + 5 分钟过期防并发双发）。
        """
        yesterday = self._today()
        # 检查"昨天"及之前未结算的盘（含停服跨日）+ 已结算但未发完的盘
        try:
            async with self.db.conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE ?",
                (self.STATE_PREFIX + "%",)
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception as e:
            logger.error(f"[天道盘] 扫描未结算盘失败: {e}")
            return ""

        results = []
        for key, raw in rows:
            state = self._parse(raw)
            if not state or state.get("date") >= yesterday:
                continue

            if not state.get("settled"):
                # CAS 抢占结算权
                claimed = await self.db.ext.syscfg_cas(
                    key, json.dumps({**state, "settled": True}, ensure_ascii=False), raw
                )
                if not claimed:
                    continue
                state = {**state, "settled": True}
                total = int(state.get("red_pool", 0)) + int(state.get("blue_pool", 0))
                if total <= 0:
                    continue
                win_side = "red" if random.random() < 0.5 else "blue"
                win_pool = int(state.get(f"{win_side}_pool", 0))
                prize_pool = int(total * (1 - self.HOUSE_CUT))
                date_str = state.get("date", "?")

                results.append(f"🎲【天道盘 · {date_str}】开盤！天意落在【{'赤龙' if win_side == 'red' else '玄虎'}】！")
                if win_pool <= 0:
                    results.append("💸 押中者：无。总奖池被天道收讫（全押错了……）")
                    continue
                # 待发清单落盘（断点续发的依据）
                pending = {
                    uid: {"amount": int(b.get("amount", 0)), "name": b.get("name", uid[:8])}
                    for uid, b in (state.get("bets") or {}).items()
                    if b.get("side") == win_side and int(b.get("amount", 0)) > 0
                }
                state = {**state, "pending_payouts": pending, "paid": {},
                         "win_side": win_side, "win_pool": win_pool, "prize_pool": prize_pool}
                await self.db.ext.set_system_config(key, json.dumps(state, ensure_ascii=False))
                results.append(f"💰 总奖池 {total:,} 灵石（天道抽成 {int(self.HOUSE_CUT * 100)}%）")
            else:
                # 补发路径：已结算但 pending 未清（上次发放中途崩溃）
                if not (state.get("pending_payouts") or {}):
                    continue
                # sweep 锁：5 分钟内他人正在补发则跳过，防并发双发
                sweep_ts = int(state.get("sweep_ts", 0) or 0)
                now = int(time.time())
                if sweep_ts and now - sweep_ts < 300:
                    continue
                if not await self.db.ext.syscfg_cas(
                    key, json.dumps({**state, "sweep_ts": now}, ensure_ascii=False), raw
                ):
                    continue
                results.append("🛠️【天道盘】检测到未完成的奖金发放，正在补发……")

            # ---- 发放（新结算与补发共用）----
            pending = state.get("pending_payouts") or {}
            paid = dict(state.get("paid") or {})
            win_pool = int(state.get("win_pool", 0) or 0)
            prize_pool = int(state.get("prize_pool", 0) or 0)
            for uid, info in list(pending.items()):
                if uid in paid:
                    continue
                amount = int(info.get("amount", 0))
                payout = int(prize_pool * amount / win_pool) if win_pool > 0 else 0
                if payout > 0:
                    await self.db.add_player_gold(uid, payout)
                    results.append(
                        f"🎉 {info.get('name', uid[:8])} 押中 {amount:,} → 赢得 {payout:,} 灵石")
                paid[uid] = payout
                # 逐笔 CAS 落盘（崩溃断点；补发路径下 3 连败则留给下轮补发）
                for _ in range(3):
                    cur_raw = await self.db.ext.get_system_config(key)
                    cur = self._parse(cur_raw)
                    if not cur or uid in (cur.get("paid") or {}):
                        break  # 已被他人记录，跳过
                    if await self.db.ext.syscfg_cas(
                        key, json.dumps({**cur, "paid": paid}, ensure_ascii=False), cur_raw or None
                    ):
                        break
            # 全部发完：清 pending 与 sweep 标记（尽力而为）
            cur_raw = await self.db.ext.get_system_config(key)
            cur = self._parse(cur_raw)
            if cur and len(cur.get("paid") or {}) >= len(pending):
                await self.db.ext.syscfg_cas(
                    key,
                    json.dumps({**cur, "pending_payouts": {}, "sweep_ts": 0}, ensure_ascii=False),
                    cur_raw or None,
                )

        # v3.52.8：清理 3 天前已结清的旧盘键（daopan_YYYY-MM-DD 不匹配
        # purge_dated_config_keys 的 _YYYYMMDD 模式，此前会无限膨胀）。
        # 注意逐键重读最新值——本调用可能刚把某盘结清，扫描快照已过期。
        try:
            cutoff_ts = int(time.mktime(time.strptime(self._today(), "%Y-%m-%d"))) - 3 * 86400
            async with self.db.conn.execute(
                "SELECT key, value FROM system_config WHERE key LIKE ?",
                (self.STATE_PREFIX + "%",)
            ) as cursor:
                fresh_rows = await cursor.fetchall()
            for key, raw in fresh_rows:
                state = self._parse(raw)
                if not state or not state.get("settled") or (state.get("pending_payouts") or {}):
                    continue
                try:
                    ts = int(time.mktime(time.strptime(state.get("date", ""), "%Y-%m-%d")))
                except (ValueError, TypeError):
                    continue
                if ts < cutoff_ts:
                    await self.db.conn.execute("DELETE FROM system_config WHERE key = ?", (key,))
            await self.db.conn.commit()
        except Exception as e:
            logger.warning(f"[天道盘] 旧盘键清理失败（不影响玩法）: {e}")
        return "\n".join(results)

    async def bet(self, player, side_str: str, amount_str: str) -> Tuple[bool, str]:
        settle_msg = await self._settle_if_due()
        uid = player.user_id
        side = self.SIDES.get((side_str or "").strip())
        if not side:
            return False, (settle_msg + "\n❌ 用法：/天道盘 押 <红|玄> <灵石>（100 - 50000）").strip()
        try:
            amount = int(str(amount_str).strip().translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        except (TypeError, ValueError):
            return False, "❌ 押注金额须为整数。"
        if not (self.MIN_BET <= amount <= self.MAX_BET):
            return False, f"❌ 单注需在 {self.MIN_BET:,} - {self.MAX_BET:,} 灵石之间。"

        date, state = await self._get_or_create()
        bets = dict(state.get("bets") or {})
        my = bets.get(uid) or {}
        if my.get("side") and my.get("side") != side:
            return False, "❌ 本日你已押过另一边，天意不可三心二意（明日再来）。"

        # 条件原子扣款
        cursor = await self.db.conn.execute(
            "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
            (amount, uid, amount)
        )
        await self.db.conn.commit()
        if cursor.rowcount == 0:
            return False, f"❌ 灵石不足！你当前持有 {player.gold:,}。"

        # CAS 更新池体（失败退款重试）
        for _ in range(3):
            raw = await self._load_raw(date)
            cur = self._parse(raw)
            if not cur:
                cur = {"date": date, "red_pool": 0, "blue_pool": 0, "bets": {}, "settled": False}
            if cur.get("settled"):
                await self.db.add_player_gold(uid, amount)
                return False, "⏰ 本日天道盘已结算，押注资金已退还。明日请早！"
            new_bets = dict(cur.get("bets") or {})
            mine = new_bets.get(uid) or {}
            new_bets[uid] = {
                "side": side,
                "amount": int(mine.get("amount", 0)) + amount,
                "name": player.user_name or uid[:8],
            }
            new_state = {**cur,
                         f"{side}_pool": int(cur.get(f"{side}_pool", 0)) + amount,
                         "bets": new_bets}
            if await self.db.ext.syscfg_cas(self._key(date), json.dumps(new_state, ensure_ascii=False), raw or None):
                state = new_state
                break
            # v3.52.7 修复(资金增发)：CAS 失败只重试，不在此退款——
            # 原实现每败一次退一次，3 连败净赚 2 倍注额；或"先退款后成功"白得免费上桌
        else:
            await self.db.add_player_gold(uid, amount)  # 仅最终放弃时退款一次（扣款1次=退款1次）
            return False, "⚠️ 天道盘正忙，请稍后再试。"

        red, blue = int(state.get("red_pool", 0)), int(state.get("blue_pool", 0))
        my_amt = int((state.get("bets") or {}).get(uid, {}).get("amount", 0))
        odds = f"{red / max(1, blue):.2f}" if side == "red" else f"{blue / max(1, red):.2f}"
        lines = [
            "🎲【天道盘】押注成功！",
            "━━━━━━━━━━━━━━━",
            f"你押了【{'赤龙' if side == 'red' else '玄虎'}】{amount:,} 灵石（本日累计 {my_amt:,}）",
            f"📊 当前盘口：赤龙 {red:,} vs 玄虎 {blue:,}",
            f"⚖️ 若天意降临你方，预计赔率约 ×{odds}",
        ]
        if settle_msg:
            lines.insert(0, settle_msg)
            lines.insert(1, "━━━━━━━━━━━━━━━")
        return True, "\n".join(lines)

    async def get_board(self, player) -> str:
        settle_msg = await self._settle_if_due()
        date, state = await self._get_or_create()
        red, blue = int(state.get("red_pool", 0)), int(state.get("blue_pool", 0))
        uid = player.user_id
        my = (state.get("bets") or {}).get(uid) or {}
        lines = [
            "🎲【天道盘 · 今日】",
            "━━━━━━━━━━━━━━━",
            f"🐉 赤龙盘：{red:,} 灵石",
            f"🐅 玄虎盘：{blue:,} 灵石",
            "⚖️ 赔率随两盘比例浮动，赢方瓜分总奖池 90%",
        ]
        if my:
            lines.append(f"🪙 我的押注：{'赤龙' if my.get('side') == 'red' else '玄虎'} × {int(my.get('amount', 0)):,}")
        else:
            lines.append("🪙 我还未押注")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 /天道盘 押 <红|玄> <灵石>（100-50000，当日 24 点开奖）")
        lines.append("💡 押中按注额比例瓜分奖池，押错血本无归——孤注一掷，还是落袋为安？")
        if settle_msg:
            lines.insert(0, settle_msg)
        return "\n".join(lines)
