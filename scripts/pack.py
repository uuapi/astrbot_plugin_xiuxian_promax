import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
version = sys.argv[1] if len(sys.argv) > 1 else "v3.51.0"
out_path = os.path.join(ROOT, f"{version}.zip")

EXCLUDE_DIRS = {"__pycache__", ".git", ".serena", ".ruff_cache", "tests", "scripts", ".github"}
EXCLUDE_EXT = {".zip", ".pyc", ".db", ".log"}

count = 0
with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for entry in sorted(os.listdir(ROOT)):
        fp = os.path.join(ROOT, entry)
        if os.path.isdir(fp):
            if entry in EXCLUDE_DIRS:
                continue
            for dirpath, dirnames, filenames in os.walk(fp):
                dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
                for fn in filenames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in EXCLUDE_EXT:
                        continue
                    fp2 = os.path.join(dirpath, fn)
                    rel = os.path.relpath(fp2, ROOT)
                    zf.write(fp2, os.path.join(os.path.basename(ROOT), rel))
                    count += 1
        else:
            ext = os.path.splitext(entry)[1].lower()
            if ext in EXCLUDE_EXT or entry.endswith(".zip"):
                continue
            zf.write(fp, os.path.join(os.path.basename(ROOT), entry))
            count += 1

print(f"PACKED {version}.zip files={count} size={os.path.getsize(out_path)}")
