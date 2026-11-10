"""Entry point for hosts that start `uvicorn main:app` from the repository ROOT (Render with an empty Root Directory).

The application lives in option-intelligence-main/. This file loads it from there, so the service starts whether Render's
Root Directory is empty, set to option-intelligence-main, or the start command does `cd option-intelligence-main`."""
import importlib.util
import os
import sys

_APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "option-intelligence-main")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)          # its own modules (indicators, accounts, ...) import as usual

_spec = importlib.util.spec_from_file_location("oi_app_main", os.path.join(_APP_DIR, "main.py"))
_module = importlib.util.module_from_spec(_spec)
sys.modules["oi_app_main"] = _module
_spec.loader.exec_module(_module)
app = _module.app
