from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def reveal_in_file_manager(path: str) -> tuple[bool, str]:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return False, "路径不存在或已被移动。"
        p = p.resolve()
    except OSError as exc:
        return False, str(exc)

    try:
        if sys.platform == "win32":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                subprocess.Popen(["explorer", str(p)])
        elif sys.platform == "darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p)])
        else:
            target = p.parent if p.is_file() else p
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        return False, str(exc)
    return True, ""
