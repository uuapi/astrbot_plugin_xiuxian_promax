# handlers/ranking_handlers.py
from astrbot.api.event import AstrMessageEvent
from ..managers.ranking_manager import RankingManager
from ..data.data_manager import DataBase

# v3.52.5 排行榜图片卡片：前三名科举功名 + 磨砂玻璃卡片；关闭/PIL缺失时降级纯文本
# （🥇🥈🥉 在 OpenMoji-black-glyf 字体中缺字渲染为豆腐块，故改用 CJK 功名前缀）
_MEDALS = {1: "[状元]", 2: "[榜眼]", 3: "[探花]"}


class RankingHandlers:
    def __init__(self, db: DataBase, rank_mgr: RankingManager):
        self.db = db
        self.rank_mgr = rank_mgr

    async def _rank_reply(self, event: AstrMessageEvent, kind: str, title: str,
                          bg: str, sect_id: int = None, card_key: str = "CARD_RANK"):
        """榜单统一出口：优先图片卡片，失败/关闭降级为 manager 文本"""
        # 文本路径（同时也是降级路径）
        if kind == "contribution":
            success, msg = await self.rank_mgr.get_contribution_ranking(sect_id)
        else:
            success, msg = await getattr(self.rank_mgr, f"get_{kind}_ranking")()

        rows = None
        try:
            from ..utils.image_generator import render_card_async, card_enabled
            if success and card_enabled(card_key):
                rows = await self.rank_mgr.get_rank_rows(kind, 10, sect_id)
                if rows:
                    # 整行布局：名次+名称+数据一行呈现，避免标签列被压缩截断
                    rank_lines = [
                        f"{_MEDALS.get(idx, '')} {idx}. {name} · {detail}".strip()
                        for idx, name, detail in rows
                    ]
                    card_path = await render_card_async(
                        bg, title, f"TOP {len(rows)} · 全服实时",
                        sections=[{"name": title, "lines": rank_lines}],
                        footer="模拟修仙 · 数据每小时随玩法实时变动",
                    )
                    if card_path:
                        yield event.image_result(card_path)
                        return
        except Exception as e:
            from astrbot.api import logger
            logger.warning(f"{title}卡片生成失败（已降级为文本）: {e}")

        yield event.plain_result(msg)

    async def handle_rank_level(self, event: AstrMessageEvent):
        """境界排行"""
        async for r in self._rank_reply(event, "level", "境界排行榜", "combat"):
            yield r

    async def handle_rank_power(self, event: AstrMessageEvent):
        """战力排行"""
        async for r in self._rank_reply(event, "power", "战力排行榜", "combat"):
            yield r

    async def handle_rank_wealth(self, event: AstrMessageEvent):
        """财富排行"""
        async for r in self._rank_reply(event, "wealth", "财富排行榜", "info"):
            yield r

    async def handle_rank_sect(self, event: AstrMessageEvent):
        """宗门排行"""
        async for r in self._rank_reply(event, "sect", "宗门排行榜", "sect"):
            yield r

    async def handle_rank_deposit(self, event: AstrMessageEvent):
        """存款排行"""
        async for r in self._rank_reply(event, "deposit", "存款排行榜", "info"):
            yield r

    async def handle_rank_sect_contribution(self, event: AstrMessageEvent):
        """宗门贡献排行（当前所在宗门）"""
        user_id = str(event.get_sender_id())
        player = await self.db.get_player_by_id(user_id)

        if not player:
            yield event.plain_result("❌ 你还未踏入修仙之路，请先发送「我要修仙」开始修行。")
            return

        if not player.sect_id or player.sect_id == 0:
            yield event.plain_result("❌ 你尚未加入任何宗门，无法查看宗门贡献排行。")
            return

        async for r in self._rank_reply(event, "contribution", "宗门贡献排行", "sect",
                                        sect_id=player.sect_id, card_key="CARD_RANK_SECT"):
            yield r
