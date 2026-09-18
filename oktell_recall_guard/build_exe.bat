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
REM Ресурс версии: по нему машинная копия из MSI узнаёт версию копии
REM пользователя, не запуская её, а IT видит издателя в свойствах файла.
python -X utf8 version_info.py
if errorlevel 1 (
  del _build_token.py >nul 2>nul
  echo Build failed: version_info.py
  pause
  exit /b 1
)
REM setuptools/pkg_resources агенту не нужны — их НЕ импортирует ни одна строка
REM исходников, PyInstaller тянет их сам, раз они стоят в окружении сборки.
REM 07.09.2026 из-за них сборка выросла на 2,2 МБ и начала падать: хук
REM pyi_rth_pkgres читает вшитый файл setuptools\_vendor\...\Lorem ipsum.txt, и
REM когда onefile-процесс попадает на ЧУЖУЮ недораспакованную папку _MEI (такие
REM остаются после убитых копий, имя считается от PID и повторяется), файла на
REM месте нет — процесс умирает с модальным окном «Unhandled exception in
REM script». Без этих модулей у падения нет и повода.
REM Значок — тот же, что у iCORE Phone (microsip-src\res\icore.ico): человек
REM видит его на ярлыке, в панели задач и в «Программах и компонентах», и две
REM наши программы должны выглядеть одной семьёй. Без --icon PyInstaller ставит
REM свой, и ярлык «Oktell» на рабочем столе читается как чужая программа —
REM ярлык берёт значок из самого exe (IconLocation=<exe>,0).
REM --add-data кладёт тот же .ico ВНУТРЬ сборки: программа раскладывает его
REM рядом с собой, и ярлык ссылается на файл, а не на exe. Проводник кэширует
REM картинку по строке пути, и у ярлыка она не менялась с первой установки —
REM значит значок в exe сам по себе до уже работающих машин не доехал бы.
python -m PyInstaller --onefile --noconsole --name OktellRecallGuard ^
  --icon icore.ico ^
  --add-data "icore.ico;." ^
  --hidden-import websocket ^
  --hidden-import win32gui ^
  --hidden-import _build_token ^
  --exclude-module pkg_resources ^
  --exclude-module setuptools ^
  --exclude-module pip ^
  --version-file version_info.txt ^
  agent.py
set BUILD_RESULT=%errorlevel%

REM Файл с токеном рядом с исходниками не оставляем.
del _build_token.py >nul 2>nul

if not "%BUILD_RESULT%"=="0" (
  echo Build failed.
  pause
  exit /b 1
)

REM Подпись — ДО публикации и до упаковки в MSI: иначе на машины уедет
REM неподписанный файл. Сертификат задаётся OKTELL_GUARD_SIGN_THUMBPRINT или
REM OKTELL_GUARD_SIGN_PFX (см. sign_file.py); без них шаг только предупреждает.
python -X utf8 sign_file.py dist\OktellRecallGuard.exe
if errorlevel 1 (
  echo Подпись не удалась — не публикую.
  pause
  exit /b 1
)

REM Выкладываем свежую сборку на сервер сами: сотрудники обновятся автоматически,
REM никому не нужно ничего загружать руками. Без токена публикации шаг пропустится.
python -X utf8 publish_release.py
if errorlevel 1 (
  echo Публикация не удалась.
  pause
  exit /b 1
)

REM Пакет для IT: раскатка групповой политикой (назначить компьютерам).
python -X utf8 build_msi.py
if errorlevel 1 (
  echo MSI не собран.
  pause
  exit /b 1
)

echo.
echo Готово: dist\OktellRecallGuard.exe и dist\OktellRecallGuard-*.msi
echo exe — для скачивания из iCORE, msi — для IT (групповая политика).
pause
