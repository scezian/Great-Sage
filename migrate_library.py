import os, shutil
from pathlib import Path

ROOT = Path.home() / "Documents" / "great sage"
LIBRARY = ROOT / "library"

txt_files = [p for p in ROOT.glob("*.txt") if p.is_file()]
if not txt_files:
    print("No .txt files found — nothing to migrate.")
else:
    print(f"Found {len(txt_files)} file(s):\n")
    for src in txt_files:
        dest_dir = LIBRARY / src.stem
        dest = dest_dir / src.name
        if dest.exists():
            print(f"  SKIP   {src.name}")
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            print(f"  MOVED  {src.name}  →  library/{src.stem}/{src.name}")
        except Exception as e:
            print(f"  FAIL   {src.name}  →  {e}")
