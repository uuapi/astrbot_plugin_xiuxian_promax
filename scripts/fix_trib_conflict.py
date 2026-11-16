"""修复：道韵保底被天劫浪费的问题（道韵护体→天劫自动通过）"""
import io

p = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax\core\breakthrough_manager.py"
src = io.open(p, encoding="utf-8").read()

# 1. 在道韵判定后添加标记变量
old = '''        guarantee = int(pity_data.get("daoyun_guarantee", 0))
        if guarantee:
            breakthrough_success = True
            cost_note += "\\n🌠【道韵爆发】此前凝聚的道韵轰然引爆——这一步，天命所归！"
        else:
            random_value = random.random()
            breakthrough_success = random_value < success_rate'''
new = '''        guarantee = int(pity_data.get("daoyun_guarantee", 0))
        daoyun_auto_pass = False  # v3.51.0：道韵护体→天劫自动通过
        if guarantee:
            breakthrough_success = True
            daoyun_auto_pass = True
            cost_note += "\\n🌠【道韵爆发】此前凝聚的道韵轰然引爆——这一步，天命所归！"
        else:
            random_value = random.random()
            breakthrough_success = random_value < success_rate'''
assert old in src, "daoyun anchor missing"
src = src.replace(old, new, 1)

# 2. 修改天劫调用：传入 auto_pass 参数
old2 = '''            if next_level_index in major_realm_boundaries:
                trib_result, trib_msg = await self._tribulation(player, next_level_index)'''
new2 = '''            if next_level_index in major_realm_boundaries:
                trib_result, trib_msg = await self._tribulation(player, next_level_index, auto_pass=daoyun_auto_pass)'''
assert old2 in src, "trib call anchor missing"
src = src.replace(old2, new2, 1)

# 3. 修改 _tribulation 签名：接受 auto_pass 参数
old3 = '''    async def _tribulation(self, player, next_level_index: int):'''
new3 = '''    async def _tribulation(self, player, next_level_index: int, auto_pass: bool = False):'''
assert old3 in src, "trib signature missing"
src = src.replace(old3, new3, 1)

# 4. 在天劫逻辑开头添加 auto_pass 检查
old4 = '''        lines = ["⚡⚡⚡【天劫降临】⚡⚡⚡", "━━━━━━━━━━━━━━━",
                 "天空骤然阴沉，紫色雷云凝聚——重大境界突破引来天道试炼！"]
        if talisman_used:'''
new4 = '''        lines = ["⚡⚡⚡【天劫降临】⚡⚡⚡", "━━━━━━━━━━━━━━━",
                 "天空骤然阴沉，紫色雷云凝聚——重大境界突破引来天道试炼！"]
        if auto_pass:
            # v3.51.0 道韵护体：天韵爆发时天劫自动通过（防止道韵保底被天劫浪费）
            lines.append("🌠 道韵爆发形成的护体金光令天劫自行溃散——天道认可了你的积累！")
            lines.append("━━━━━━━━━━━━━━━")
            atk = max(1, int((player.physical_damage or 0) * 0.05))
            dfn = max(1, int((player.physical_defense or 0) * 0.05))
            mag = max(1, int((player.magic_damage or 0) * 0.05))
            hpx = max(1, int((player.max_blood_qi or 0) * 0.05))
            await self.db.conn.execute(
                "UPDATE players SET physical_damage=physical_damage+?,physical_defense=physical_defense+?,magic_damage=magic_damage+?,max_blood_qi=max_blood_qi+? WHERE user_id=?",
                (atk, dfn, mag, hpx, player.user_id))
            await self.db.conn.commit()
            lines.append(f"📈 淬体奖励：物伤+{atk} 物防+{dfn} 法伤+{mag} 气血+{hpx}")
            return True, "\\n".join(lines)
        if talisman_used:'''
assert old4 in src, "auto_pass insert anchor missing"
src = src.replace(old4, new4, 1)

io.open(p, "w", encoding="utf-8", newline="").write(src)
print("DAOYUN_TRIB_FIXED")
