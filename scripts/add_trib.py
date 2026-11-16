"""Add tribulation system to breakthrough_manager.py"""
import io

p = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax\core\breakthrough_manager.py"
src = io.open(p, encoding="utf-8").read()

old1 = "self._record_breakthrough(player, current_level_name, next_level_name, True, pill_name)\n            return True, success_msg, False"
new1 = """# v3.51.0 天劫系统：重大境界突破触发天劫
            major_realm_boundaries = [12, 18, 24, 30, 36, 42, 48]
            if next_level_index in major_realm_boundaries:
                trib_result, trib_msg = await self._tribulation(player, next_level_index)
                if not trib_result:
                    player.experience = max(0, player.experience - int(player.experience * 0.20))
                    player.hp = max(1, player.hp // 5)
                    await self.db.update_player(player)
                    await self._record_breakthrough(player, current_level_name, next_level_name, False, pill_name)
                    return False, trib_msg, False
                success_msg += trib_msg

            self._record_breakthrough(player, current_level_name, next_level_name, True, pill_name)
            return True, success_msg, False"""
assert old1 in src, "success return not found"
src = src.replace(old1, new1, 1)

old2 = "    async def _handle_breakthrough_loan_repay(self, player: Player) -> str:"
new2 = '''    async def _tribulation(self, player, next_level_index: int):
        """v3.51.0 天劫系统：重大境界突破触发天劫，3 道天雷依次判定"""
        import random as _rnd
        waves = min(3, max(1, (next_level_index - 12) // 6 + 1))
        base_survive = 0.55 + 0.10 * min(5, player.level_index // 12)
        talisman_used = 0
        try:
            from .storage_ring_manager import StorageRingManager
            ring = StorageRingManager(self.db, self.config_manager)
            while talisman_used < 2 and ring.has_item(player, "护体符", 1):
                await ring.retrieve_item(player, "护体符", 1)
                talisman_used += 1
        except Exception:
            pass
        talisman_bonus = 0.15 * talisman_used
        lines = ["⚡⚡⚡【天劫降临】⚡⚡⚡", "━━━━━━━━━━━━━━━",
                 "天空骤然阴沉，紫色雷云凝聚——重大境界突破引来天道试炼！"]
        if talisman_used:
            lines.append(f"🛡️ 你祭出 {talisman_used} 张【护体符】，灵光护罩笼罩全身！")
        all_ok = True
        for wave in range(1, waves + 1):
            rate = min(0.95, base_survive + talisman_bonus - (wave - 1) * 0.10)
            lines.append(f"⚡ 第 {wave}/{waves} 道天雷轰然落下——")
            if _rnd.random() < rate:
                lines.append("   你咬牙硬抗，雷光入体淬炼筋骨！（存活）")
            else:
                lines.append("   雷光撕裂护罩，你被轰得倒飞出去！（被击中）")
                all_ok = False
                break
        lines.append("━━━━━━━━━━━━━━━")
        if all_ok:
            lines.append("🌟 天劫淬体成功！雷光洗涤经脉，境界稳固，属性额外提升！")
            atk = max(1, int((player.physical_damage or 0) * 0.05))
            dfn = max(1, int((player.physical_defense or 0) * 0.05))
            mag = max(1, int((player.magic_damage or 0) * 0.05))
            hpx = max(1, int((player.max_blood_qi or 0) * 0.05))
            await self.db.conn.execute(
                "UPDATE players SET physical_damage=physical_damage+?,physical_defense=physical_defense+?,magic_damage=magic_damage+?,max_blood_qi=max_blood_qi+? WHERE user_id=?",
                (atk, dfn, mag, hpx, player.user_id))
            await self.db.conn.commit()
            lines.append(f"📈 淬体奖励：物伤+{atk} 物防+{dfn} 法伤+{mag} 气血+{hpx}")
            return True, "\n".join(lines)
        lines.append("💀 天劫失败……雷光重创道体，境界突破中止。")
        lines.append("💔 修为 -20%，气血大损。养伤后再战！")
        return False, "\n".join(lines)

    async def _handle_breakthrough_loan_repay(self, player: Player) -> str:'''
assert old2 in src, "loan_repay not found"
src = src.replace(old2, new2, 1)

io.open(p, "w", encoding="utf-8", newline="").write(src)
print("TRIBULATION_OK")
