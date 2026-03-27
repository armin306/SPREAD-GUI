from __future__ import annotations

import os
import sys
from PyQt6.QtWidgets import QApplication

from spread_gui.main_window import MainWindow


def main() -> int:
    # Qt/X11 robustness for DLS / remote X11 sessions
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")

    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
