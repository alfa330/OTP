@echo off
REM Отладочная сборка с консолью: видно вывод --status и --logout-now.
cd /d "%~dp0"
python -m PyInstaller --onefile --console --name OktellRecallGuardDebug ^
  --hidden-import websocket ^
  --hidden-import win32gui ^
  agent.py
pause
