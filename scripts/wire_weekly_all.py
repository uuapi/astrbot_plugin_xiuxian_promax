"""全面接入修炼周报：将所有系统的行为记录到周报"""
import io

ROOT = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax"

# ---- 1. breakthrough_manager: 突破成功记录 ----
p = ROOT + r"\core\breakthrough_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''            # v3.47.0 突破心得录：记录成功
            await self._record_breakthrough(player, current_level_name, next_level_name, True, pill_name)
            return True, success_msg, False'''
new = '''            # v3.47.0 突破心得录：记录成功
            await self._record_breakthrough(player, current_level_name, next_level_name, True, pill_name)
            # v3.49.0 周报记录
            try:
                await self.db.ext.syscfg_adjust_number(f"weekly_breakthrough_{uid}_{__import__('time').strftime('%G-W%V')}", 1)
            except Exception:
                pass
            return True, success_msg, False'''
assert old in s, "BT record not found"
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("1.breakthrough OK")

# ---- 2. quiz_manager: 科举完赛记录 ----
p = ROOT + r"\managers\quiz_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''            await self._save_session(uid, None)'''
new = '''            await self._save_session(uid, None)
            # v3.49.0 周报记录
            try:
                await self.db.ext.syscfg_adjust_number(f"weekly_quiz_{uid}_{time.strftime('%G-W%V')}", 1)
            except Exception:
                pass'''
assert old in s, "quiz save not found"
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("2.quiz OK")

# ---- 3. combat_handlers: 决斗/切磋记录 ----
p = ROOT + r"\handlers\combat_handlers.py"
s = io.open(p, encoding="utf-8").read()

# 决斗胜利
old = '''        # v3.30.0 段位天梯：决斗积分（发起方结算）
        final_log += await self._record_ladder(user_id, result['winner'], duel=True)'''
new = '''        # v3.30.0 段位天梯：决斗积分（发起方结算）
        final_log += await self._record_ladder(user_id, result['winner'], duel=True)
        # v3.49.0 周报记录
        try:
            import time as _t
            wk = _t.strftime('%G-W%V')
            if result['winner'] == user_id:
                await self.db.ext.syscfg_adjust_number(f"weekly_duel_win_{user_id}_{wk}", 1)
            elif result['winner']:
                await self.db.ext.syscfg_adjust_number(f"weekly_duel_lose_{user_id}_{wk}", 1)
        except Exception:
            pass'''
assert old in s, "duel ladder not found"
s = s.replace(old, new, 1)

# 切磋
old = '''        # v3.30.0 段位天梯：切磋积分（发起方结算）
        final_log += await self._record_ladder(user_id, result['winner'], duel=False)'''
new = '''        # v3.30.0 段位天梯：切磋积分（发起方结算）
        final_log += await self._record_ladder(user_id, result['winner'], duel=False)
        # v3.49.0 周报记录
        try:
            import time as _t
            await self.db.ext.syscfg_adjust_number(f"weekly_spar_{user_id}_{_t.strftime('%G-W%V')}", 1)
        except Exception:
            pass'''
assert old in s, "spar ladder not found"
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("3.combat OK")

# ---- 4. tower_manager: 镇妖塔通关记录 ----
p = ROOT + r"\managers\tower_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''            self._record_breakthrough = None  # placeholder'''
if old not in s:
    # Find the success return in challenge
    old = '''            return True, "\\n".join(lines), new_record'''
new = '''            # v3.49.0 周报记录
            try:
                await self.db.ext.syscfg_adjust_number(f"weekly_tower_{uid}_{__import__('time').strftime('%G-W%V')}", 1)
            except Exception:
                pass
            return True, "\\n".join(lines), new_record'''
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("4.tower OK")
else:
    print("4.tower SKIP (pattern not found)")

# ---- 5. sect_manager: 捐献记录（已有 syscfg 调用，检查是否覆盖周报格式）----
p = ROOT + r"\managers\sect_manager.py"
s = io.open(p, encoding="utf-8").read()
if "sect_donate_user_" in s and "weekly_donate_" not in s:
    old = '''            await self.db.ext.syscfg_adjust_number(f"sect_donate_user_{user_id}_{week}", stone_amount)'''
    new = '''            await self.db.ext.syscfg_adjust_number(f"sect_donate_user_{user_id}_{week}", stone_amount)
            # v3.49.0 周报记录
            await self.db.ext.syscfg_adjust_number(f"weekly_donate_{user_id}_{week}", stone_amount)'''
    if old in s:
        s = s.replace(old, new, 1)
        io.open(p, "w", encoding="utf-8", newline="").write(s)
        print("5.donate OK")
    else:
        print("5.donate SKIP")

# ---- 6. escort_manager: 押镖完成记录 ----
p = ROOT + r"\managers\escort_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''        await self.db.add_player_exp(user_id, escort_exp)'''
new = '''        await self.db.add_player_exp(user_id, escort_exp)
        # v3.49.0 周报记录
        try:
            import time as _t
            await self.db.ext.syscfg_adjust_number(f"weekly_escort_{user_id}_{_t.strftime('%G-W%V')}", 1)
        except Exception:
            pass'''
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("6.escort OK")
else:
    print("6.escort SKIP (pattern not found)")

# ---- 7. pet_manager: 灵兽探险收取记录 ----
p = ROOT + r"\managers\pet_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''        await self.db.add_player_exp(player.user_id, exp)'''
# Find the one in collect_expedition (after add_player_gold)
idx = s.find("async def collect_expedition")
if idx > 0:
    chunk = s[idx:idx+500]
    if "await self.db.add_player_exp(player.user_id, exp)" in chunk:
        old_local = "await self.db.add_player_exp(player.user_id, exp)"
        new_local = old_local + "\n        # v3.49.0 周报记录\n        try:\n            await self.db.ext.syscfg_adjust_number(f\"weekly_expedition_{player.user_id}_{__import__('time').strftime('%G-W%V')}\", 1)\n        except Exception:\n            pass"
        s = s.replace(old_local, new_local, 1)
        io.open(p, "w", encoding="utf-8", newline="").write(s)
        print("7.expedition OK")
    else:
        print("7.expedition SKIP (pattern not found in collect)")

# ---- 8. marriage_manager: 仙侣送礼记录 ----
p = ROOT + r"\managers\marriage_manager.py"
s = io.open(p, encoding="utf-8").read()
old = '''        await self.add_bond(me, partner_id, self.GIFT_BOND)'''
new = '''        await self.add_bond(me, partner_id, self.GIFT_BOND)
        # v3.49.0 周报记录
        try:
            import time as _t
            await self.db.ext.syscfg_adjust_number(f"weekly_gift_{me}_{_t.strftime('%G-W%V')}", 1)
        except Exception:
            pass'''
if old in s:
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    print("8.gift OK")
else:
    print("8.gift SKIP (pattern not found)")

print("=== ALL WIRED ===")
