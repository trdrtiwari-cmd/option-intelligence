from __future__ import annotations
import os, sys, socket, threading, time, logging
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox, QStatusBar
from PySide6.QtWebEngineWidgets import QWebEngineView
import uvicorn

APP_NAME = "Option Intelligence"
HOST = "127.0.0.1"
PORT = int(os.getenv("OI_DESKTOP_PORT", "8765"))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
PROJECT = ROOT / "option-intelligence-main"
DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "OptionIntelligence"
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_DIR / "desktop.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oi.desktop")

def wait_for_server(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5): return True
        except OSError: time.sleep(0.25)
    return False

class Backend:
    def __init__(self): self.server = None; self.thread = None
    def start(self):
        with socket.socket() as s:
            if s.connect_ex((HOST, PORT)) == 0:
                raise RuntimeError(f"Port {PORT} is already in use. Close another instance or set OI_DESKTOP_PORT.")
        os.environ["AUTH_DISABLED"] = "1"
        os.environ["DATABASE_URL"] = ""
        os.environ["SQLITE_PATH"] = str(DATA_DIR / "option_intelligence.db")
        os.environ.setdefault("LOG_LEVEL", "INFO")
        sys.path.insert(0, str(PROJECT))
        import main as app_module
        self.server = uvicorn.Server(uvicorn.Config(app_module.app, host=HOST, port=PORT, log_level="warning", access_log=False, lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, name="oi-api", daemon=True)
        self.thread.start()
        if not wait_for_server(): raise RuntimeError("The local trading engine did not start. Check desktop.log.")
    def stop(self):
        if self.server: self.server.should_exit = True
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=8)

class MainWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 920)
        self.setMinimumSize(1024, 680)
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        top = QWidget(); top.setObjectName("topbar")
        bar = QHBoxLayout(top); bar.setContentsMargins(18,10,18,10)
        title = QLabel("OPTION INTELLIGENCE"); title.setObjectName("brand")
        subtitle = QLabel("LOCAL DESKTOP  •  UPSTOX DATA"); subtitle.setObjectName("subtitle")
        bar.addWidget(title); bar.addWidget(subtitle); bar.addStretch(1)
        self.connection = QLabel("● LOCAL ENGINE RUNNING"); self.connection.setObjectName("connected"); bar.addWidget(self.connection)
        refresh = QPushButton("Refresh"); refresh.clicked.connect(lambda: self.web.reload()); bar.addWidget(refresh)
        layout.addWidget(top)
        self.web = QWebEngineView(); self.web.setUrl(QUrl(f"http://{HOST}:{PORT}/")); layout.addWidget(self.web,1)
        self.setCentralWidget(root)
        self.setStyleSheet("""
        QMainWindow { background:#0b1220; }
        QWidget#topbar { background:#111c2e; border-bottom:1px solid #26364d; }
        QLabel#brand { color:#f4f7fb; font-size:16px; font-weight:800; letter-spacing:1px; }
        QLabel#subtitle { color:#8ea2bd; font-size:10px; margin-left:14px; }
        QLabel#connected { color:#55d6a5; font-size:11px; font-weight:700; padding:0 12px; }
        QPushButton { color:#eaf1fb; background:#233650; border:1px solid #3a5271; border-radius:6px; padding:7px 14px; }
        QPushButton:hover { background:#304967; }
        QStatusBar { background:#111c2e; color:#9db0c8; }
        """)
        status=QStatusBar(); status.showMessage(f"Local data: {DATA_DIR} | Signals are decision support; no live orders are submitted by this desktop wrapper"); self.setStatusBar(status)
        self.web.loadFinished.connect(self._loaded)
    def _loaded(self, ok):
        if not ok:
            self.connection.setText("● PAGE LOAD FAILED")
            self.connection.setStyleSheet("color:#ff7777;")
            log.error("Dashboard page failed to load")
    def closeEvent(self,event):
        answer=QMessageBox.question(self,"Exit Option Intelligence","Close the desktop app and stop its local engine?",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.connection.setText("● STOPPING"); self.backend.stop(); event.accept()
        else: event.ignore()

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setOrganizationName("Option Intelligence")
    backend=Backend()
    try: backend.start()
    except Exception as exc:
        log.exception("Backend startup failed")
        QMessageBox.critical(None,APP_NAME,f"Could not start the local engine:\n\n{exc}\n\nLog: {LOG_DIR / 'desktop.log'}")
        return 1
    window=MainWindow(backend); window.show()
    result=app.exec(); backend.stop(); return result

if __name__ == "__main__": raise SystemExit(main())
