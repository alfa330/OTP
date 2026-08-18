@echo off
chcp 65001 >nul
taskkill /IM OktellRecallGuard.exe /F >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*OktellRecallGuard*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*mock_server*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
echo Демо остановлено (агент, управляемый Chrome и стенд закрыты).
pause
