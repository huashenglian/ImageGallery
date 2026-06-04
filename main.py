from __future__ import annotations

import sys

from splash import show_splash, close_splash

_splash = show_splash()

from PySide6.QtWidgets import QApplication

from paths import ensure_dirs, migrate_old_files
from ui.main_window import MainWindow


def main() -> int:
    ensure_dirs()
    migrate_old_files()

    app = QApplication(sys.argv)
    app.setApplicationName("ImageGallery")
    win = MainWindow()
    win.show()

    close_splash(_splash)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
