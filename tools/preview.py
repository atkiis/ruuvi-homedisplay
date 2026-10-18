"""Render every e-paper layout to PNG files for quick visual checks.

Usage: python tools/preview.py [output_dir]   (default: ./preview, git-ignored)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import epaper  # noqa: E402

LAYOUTS = ["e1001", "e1002", "e1002-dashboard"]


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    out.mkdir(parents=True, exist_ok=True)
    for layout in LAYOUTS:
        path = out / f"{layout}.png"
        epaper.render(layout).save(path)
        print(path)


if __name__ == "__main__":
    main()
