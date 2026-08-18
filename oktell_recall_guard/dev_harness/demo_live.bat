@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Демо: Перезвон дольше нормы

if not exist "dist\OktellRecallGuard.exe" (
  echo Сначала собери exe: build_exe.bat
  pause
  exit /b 1
)
if not exist "dev_harness\config.real.json" (
  echo Нет dev_harness\config.real.json — как его собрать, написано в README.
  pause
  exit /b 1
)

echo ============================================================
echo   ДЕМО НА ЖИВОМ OKTELL
echo ============================================================
echo.
echo Правило считается ПРЯМО В ОКНЕ Oktell: базу никто не опрашивает,
echo на сервер ничего не идёт. Порог и всё остальное — в
echo dev_harness\config.real.json, раздел in_window_rule.
echo.
echo Что делать:
echo   1. Дождись окна с Oktell и войди своей учёткой.
echo   2. Уйди в статус «Перезвон».
echo   3. За 30 секунд до порога поверх Oktell появится предупреждение,
echo      на пороге выбросит на экран входа.
echo.
echo Счётчик обнуляет только состоявшийся звонок: переключение статуса
echo туда-обратно и перезагрузка страницы накопленное не сбрасывают.
echo.

echo [1/2] Останавливаю прошлые копии...
taskkill /IM OktellRecallGuard.exe /F >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*OktellRecallGuard*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*mock_server*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
timeout /t 2 /nobreak >nul

echo [2/2] Поднимаю приёмник нарушений и агента, открываю Oktell...
start "OktellGuard Стенд" cmd /k python -u -X utf8 dev_harness/mock_server.py
timeout /t 2 /nobreak >nul
start "OktellGuard Агент" "dist\OktellRecallGuard.exe" --agent --config dev_harness/config.real.json

echo.
echo Готово. Окно Oktell откроется само в течение 10-15 секунд.
echo Остановить всё: demo_stop.bat
echo Логи агента: %LOCALAPPDATA%\OktellRecallGuard\agent.log
echo.
pause
