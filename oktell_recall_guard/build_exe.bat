@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Токен агента вшивается в exe на сборке: у сотрудника нет ни конфига, ни
REM возможности что-то вводить. Берём его из той же переменной окружения, что
REM стоит на сервере. Пусто — соберётся сборка без токена, она годится, пока
REM переменная на сервере тоже пуста.
if "%OKTELL_GUARD_AGENT_TOKEN%"=="" (
  echo AGENT_TOKEN = "" > _build_token.py
  echo [!] OKTELL_GUARD_AGENT_TOKEN не задан — собираю без токена.
) else (
  echo AGENT_TOKEN = "%OKTELL_GUARD_AGENT_TOKEN%" > _build_token.py
  echo [ok] Токен агента вшит в сборку.
)
REM Адрес сервера тоже вшивается: у сотрудника нет конфига, и без адреса агент
REM не знает, куда обращаться — первая установка на живой машине на это и села.
echo SERVER_URL = "%OKTELL_GUARD_SERVER%" >> _build_token.py

python -m pip install -r requirements.txt
python -m pip install pyinstaller
REM setuptools/pkg_resources агенту не нужны — их НЕ импортирует ни одна строка
REM исходников, PyInstaller тянет их сам, раз они стоят в окружении сборки.
REM 07.09.2026 из-за них сборка выросла на 2,2 МБ и начала падать: хук
REM pyi_rth_pkgres читает вшитый файл setuptools\_vendor\...\Lorem ipsum.txt, и
REM когда onefile-процесс попадает на ЧУЖУЮ недораспакованную папку _MEI (такие
REM остаются после убитых копий, имя считается от PID и повторяется), файла на
REM месте нет — процесс умирает с модальным окном «Unhandled exception in
REM script». Без этих модулей у падения нет и повода.
python -m PyInstaller --onefile --noconsole --name OktellRecallGuard ^
  --hidden-import websocket ^
  --hidden-import win32gui ^
  --hidden-import _build_token ^
  --exclude-module pkg_resources ^
  --exclude-module setuptools ^
  --exclude-module pip ^
  agent.py
set BUILD_RESULT=%errorlevel%

REM Файл с токеном рядом с исходниками не оставляем.
del _build_token.py >nul 2>nul

if not "%BUILD_RESULT%"=="0" (
  echo Build failed.
  pause
  exit /b 1
)

REM Выкладываем свежую сборку на сервер сами: сотрудники обновятся автоматически,
REM никому не нужно ничего загружать руками. Без токена публикации шаг пропустится.
python -X utf8 publish_release.py

echo.
echo Готово: dist\OktellRecallGuard.exe
echo Один файл. Сотрудник запускает его двойным кликом — программа ставит себя сама.
pause
