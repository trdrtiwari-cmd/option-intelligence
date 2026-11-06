# Option Intelligence — Windows Desktop

## What this adds
A Windows desktop shell using PySide6 + Qt WebEngine. It starts the existing FastAPI engine locally on 127.0.0.1 and displays the existing dashboard inside a desktop window. The existing analysis, diagnostics, option-chain and signal logic remain unchanged.

## Requirements
- Windows 10/11, 64-bit
- Python 3.11 (recommended) and the Python launcher
- Internet access for Upstox market data and OAuth
- Valid Upstox API configuration/token

## Run from source
From the repository root:
1. Create and activate a virtual environment.
2. Install option-intelligence-main/requirements.txt.
3. Install desktop/windows/requirements.txt.
4. Run: python desktop/windows/launcher.py

The launcher sets AUTH_DISABLED=1 only for its local process, uses SQLite at %LOCALAPPDATA%/OptionIntelligence/option_intelligence.db, and binds only to 127.0.0.1. It does not use the hosted PostgreSQL database.

## Build
Double-click desktop/windows/build_windows.bat. PyInstaller creates an onedir distribution under dist/Option Intelligence/. Keep the entire generated folder together; do not copy only the EXE.

## Security and trading safety
- The local API binds to loopback only, not the LAN or public internet.
- AUTH_DISABLED=1 is used only for this local desktop process. Never expose this local server on 0.0.0.0.
- Never commit .env files, API keys, access tokens, passwords, SQLite databases or logs.
- Configure Upstox credentials locally through the existing supported connection flow or local environment. Do not put secrets in this repository.
- This wrapper does not submit orders or enable live order execution. Signals are decision support; verify data freshness before acting.
- Closing the window asks for confirmation and stops the local API server.

## Local files
- Database: %LOCALAPPDATA%/OptionIntelligence/option_intelligence.db
- Log: %LOCALAPPDATA%/OptionIntelligence/logs/desktop.log

## Implementation boundary
This first version uses a native Qt desktop window and embeds the existing HTML/JavaScript dashboard in Qt WebEngine. The desktop window and process lifecycle are native; the dashboard UI itself is reused to avoid duplicating or changing trading rules. Individual dashboard pages can be migrated to fully native Qt widgets in a later phase.
