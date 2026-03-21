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

# from __future__ import annotations
# 
# import os
# import sys
# from PyQt6.QtWidgets import QApplication


#def main() -> int:
#    # Qt/X11 robustness for DLS / remote X11 sessions
#    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
#    os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")
#
#    app = QApplication(sys.argv)
#
#
#    # Temporary: run legacy GUI until UI split is finished
#    from . import spread_gui_legacy as legacy
#
#    w = legacy.MainWindow()
#    w.show()
#    return app.exec()
#
#
#if __name__ == "__main__":
#    raise SystemExit(main())
#
