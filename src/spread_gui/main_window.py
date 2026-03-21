from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QScrollArea,
    QStatusBar,
    QProgressBar,
    QLabel,
)

from spread_gui.ui.processing_tab import ProcessingTab
from spread_gui.ui.analysis_tab import AnalysisTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SPREAD Processing Pipeline")
        self.resize(1200, 850)

        self.tabs = QTabWidget()

        # Processing tab with scroll area
        self.proc_tab = ProcessingTab()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.proc_tab)
        self.tabs.addTab(scroll, "Processing")

        self.analysis_tab = AnalysisTab(self.tabs)
        self.tabs.addTab(self.analysis_tab, "Analysis")

        self.setCentralWidget(self.tabs)

        # Status bar
        sb = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        sb.addPermanentWidget(self.progress, 1)

        self.status_label = QLabel("Ready.")
        sb.addWidget(self.status_label, 3)
        self.setStatusBar(sb)

    def set_status(self, text: str, done: int, total: int):
        self.status_label.setText(text)
        if total > 0:
            pct = int(100.0 * done / total)
            self.progress.setValue(max(0, min(100, pct)))
        else:
            self.progress.setValue(0)
