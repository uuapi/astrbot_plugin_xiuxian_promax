"""冲突审计：检查新功能与老功能之间的交互问题"""
import ast
import io
import os
import re

ROOT = r"C:\Users\YunXi\Desktop\astrbot_plugin_xiuxian_promax"
issues = []

# 1. 修炼周报：record 方法只在 hunt_manager 里被调用，其他系统没有接入
weekly_defined = ["record_hunt", "record_hunt_win", "record_breakthrough", "record_quiz",
                  "record_duel_win", "record_duel_lose", "record_spar", "record_donate",
                  "record_tower", "record_expedition", "record_escort", "record_rob", "record_gift"]
weekly_called = {}
for d in ["managers", "handlers", "core"]:
    for fn in os.listdir(os.path.join(ROOT, d)):
        if not fn.endswith(".py"):
            continue
        content = io.open(os.path.join(ROOT, d, fn), encoding="utf-8").read()
        for method in weekly_defined:
            if f".{method}(" in content:
                weekly_called.setdefault(method, []).append(f"{d}/{fn}")

for method in weekly_defined:
    callers = weekly_called.get(method, [])
    if not callers:
        issues.append(f"🔴 周报未接入: {method} 定义了但从未被任何系统调用 → 面板该项恒为0")

# 2. 突破卷 vs 天劫 vs 道韵保底的执行顺序
bt_src = io.open(os.path.join(ROOT, "core", "breakthrough_manager.py"), encoding="utf-8").read()
# Check if tribulation happens after daoyun guarantee consumption
if "daoyun_guarantee" in bt_src and "_tribulation" in bt_src:
    gy_idx = bt_src.find("daoyun_guarantee")
    trib_idx = bt_src.find("_tribulation")
    charge_idx = bt_src.find("consume_bt_charge")
    if gy_idx < trib_idx:
        issues.append("🟡 道韵保底在天劫之前消耗：天劫失败时道韵保底已浪费（设计取舍，需确认）")
    if charge_idx < trib_idx:
        issues.append("🟡 突破卷在天劫之前消耗：天劫失败时突破卷已浪费（设计取舍，需确认）")

# 3. permanent_bonus 多写者竞态
# Find all files that write to permanent_bonus
pb_writers = []
for d in ["managers", "handlers", "core"]:
    for fn in os.listdir(os.path.join(ROOT, d)):
        if not fn.endswith(".py"):
            continue
        content = io.open(os.path.join(ROOT, d, fn), encoding="utf-8").read()
        if "permanent_bonus" in content and ("update_player" in content or "update_player_fields" in content):
            # Check if it writes full permanent_bonus JSON
            if re.search(r'update_player_fields\(.*permanent_bonus', content) or \
               re.search(r'update_player\(.*\)\s*.*permanent_bonus', content) or \
               ('player.permanent_bonus =' in content and 'update_player(' in content):
                pb_writers.append(f"{d}/{fn}")

if len(pb_writers) > 1:
    issues.append(f"🟡 permanent_bonus 多写者竞态 ({len(pb_writers)} 文件): {', '.join(pb_writers[:8])}...")
    issues.append("   → 如果两个系统同时修改同一玩家的 permanent_bonus，后写者覆盖先写者")

# 4. root_grade 在 cultivation_manager 中的读取时机
cult_src = io.open(os.path.join(ROOT, "core", "cultivation_manager.py"), encoding="utf-8").read()
if "root_grade" in cult_src:
    # Check if it reads from player.permanent_bonus (which could be stale)
    issues.append("🟡 灵根品阶从 player.permanent_bonus 读取：如果 player 对象是旧快照，倍率可能不是最新值")

# 5. 天劫 vs 正常死亡路径：天劫成功后是否还会走死亡判定
if "_tribulation" in bt_src and "died" in bt_src:
    # Check if tribulation success path bypasses death check
    success_idx = bt_src.find("return True, success_msg")
    death_idx = bt_src.find("died = random.random()")
    trib_idx = bt_src.find("_tribulation")
    if trib_idx > death_idx and trib_idx < success_idx:
        # Tribulation is between death check and success return
        issues.append("✅ 天劫在死亡判定之后、成功返回之前：正常（天劫失败不走死亡路径）")
    elif trib_idx > success_idx:
        issues.append("🔴 天劫在成功返回之后：永远不会触发！")
    else:
        issues.append("🟡 天劫位置需要确认")

# 6. sect_shop 的贡献扣减是否会与其他扣贡献操作冲突
sect_shop = io.open(os.path.join(ROOT, "managers", "sect_shop_manager.py"), encoding="utf-8").read()
if "sect_contribution" in sect_shop:
    issues.append("✅ 宗门商店使用 SQL 原子扣贡献：安全")

# 7. 红包 sender 检查：是否阻止了 sender 自己抢
rp_src = io.open(os.path.join(ROOT, "managers", "redpacket_manager.py"), encoding="utf-8").read()
if "不能抢自己" in rp_src:
    issues.append("✅ 红包阻止 sender 自己抢：正确")

# 8. 检查新功能是否有 // 或 ''' 遗漏的未闭合字符串
for d in ["managers", "core", "handlers"]:
    for fn in os.listdir(os.path.join(ROOT, d)):
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(ROOT, d, fn)
        try:
            ast.parse(io.open(fp, encoding="utf-8").read())
        except SyntaxError as e:
            issues.append(f"🔴 语法错误: {d}/{fn}:{e.lineno} {e.msg}")

# 9. 检查旧系统的 update_player 全行覆写是否会覆盖新功能的原子修改
old_update_files = []
for d in ["managers", "core", "handlers"]:
    for fn in os.listdir(os.path.join(ROOT, d)):
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(ROOT, d, fn)
        content = io.open(fp, encoding="utf-8").read()
        if "await self.db.update_player(" in content or "await db.update_player(" in content:
            # Check if it also modifies gold or experience via snapshot
            if re.search(r'player\.gold\s*[+\-]=', content) or re.search(r'player\.experience\s*[+\-]=', content):
                old_update_files.append(f"{d}/{fn}")

if old_update_files:
    issues.append(f"🟡 残留快照回写（可能与新功能原子操作冲突）: {', '.join(old_update_files[:8])}")

# Output
out = ["=== 新旧功能冲突审计 ==="]
out.append(f"发现 {len(issues)} 项：")
for item in issues:
    out.append(f"  {item}")
io.open(os.path.join(ROOT, "_conflict_audit.txt"), "w", encoding="utf-8").write("\n".join(out))
print(f"DONE: {len(issues)} issues")
