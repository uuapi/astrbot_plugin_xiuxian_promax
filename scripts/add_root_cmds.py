"""main.py 注册灵根品阶指令 + 初始化 + 帮助"""
import io

# 1. main.py
p = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax\main.py"
src = io.open(p, encoding="utf-8").read()

# init
a = "        # v3.51.0 宗门贡献商店"
b = """        # v3.52.0 灵根品阶
        from .managers.root_grade_manager import RootGradeManager
        self.root_grade_mgr = RootGradeManager(self.db)

""" + a
assert a in src, "INIT anchor missing"
src = src.replace(a, b, 1)

# command
a2 = '    @filter.command("仙缘突破卷", "仙缘点兑突破成功率卷（150点/张,+8%）")'
b2 = '''    # ===== v3.52.0 灵根品阶 =====
    @filter.command("洗髓灵根", "查看/提升灵根品阶（影响修炼速度倍率）")
    @require_whitelist
    async def handle_root_grade(self, event: AstrMessageEvent, action: str = ""):
        player = await self.db.get_player_by_id(event.get_sender_id())
        if not player:
            yield event.plain_result("❌ 道友尚未踏入仙途。")
            return
        if action.strip() in ("升级", "洗髓", "提升"):
            _ok, msg = await self.root_grade_mgr.upgrade(player)
        else:
            msg = await self.root_grade_mgr.get_panel(player)
        yield event.plain_result(msg)

''' + a2
assert a2 in src, "CMD anchor missing"
src = src.replace(a2, b2, 1)
io.open(p, "w", encoding="utf-8", newline="").write(src)
print("MAIN_OK")

# 2. managers/__init__.py
p2 = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax\managers\__init__.py"
src2 = io.open(p2, encoding="utf-8").read()
a3 = "from .title_auction_manager import TitleAuctionManager"
b3 = a3 + "\n# v3.52.0 灵根品阶\nfrom .root_grade_manager import RootGradeManager"
assert a3 in src2
src2 = src2.replace(a3, b3, 1)
src2 = src2.replace('"TitleAuctionManager"', '"TitleAuctionManager", "RootGradeManager"')
io.open(p2, "w", encoding="utf-8", newline="").write(src2)
print("INIT_OK")

# 3. help
p3 = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax\handlers\misc_handler.py"
src3 = io.open(p3, encoding="utf-8").read()
a4 = '            "  /仙侣送礼 <物品名> (结契道侣互赠,羁绊+2)\\n"'
b4 = a4 + '\n            "  /洗髓灵根 (灵根品阶:凡品→天品,影响修炼倍率)\\n"'
assert a4 in src3
src3 = src3.replace(a4, b4)
io.open(p3, "w", encoding="utf-8", newline="").write(src3)
print("HELP_OK")
