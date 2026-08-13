---
name: verify
description: Поднять OTP локально (Flask API + собранный фронт) и прогнать изменение через реальный интерфейс. Использовать, когда нужно проверить правку в работающем приложении, а не тестами.
---

# Локальный прогон OTP

Прод — Python 3.11 (`runtime.txt`), Postgres с расширением `vector`, Flask + собранный
Vite-бандл. На 3.13 не собирается `aiohttp` для `aiogram==2.25.2` — venv только на 3.11.

## База

Нужен именно образ с pgvector, иначе схема `call_qa` падает на `extension "vector" is not available`:

```bash
docker run -d --name otp-verify -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=otp_verify \
  -p 55433:5432 pgvector/pgvector:pg16
```

Схема создаётся сама при первом импорте `database.py` (~40 секунд, много WARNING про
пропущенные триггеры — это норма для пустой базы). Отдел `szov` схема заводит сама,
поэтому сидировать его повторно нельзя — `departments_code_key` упадёт.

## Приложение

```bash
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt   # ortools/weasyprint могут не встать, они не нужны для API
export POSTGRES_DB=otp_verify POSTGRES_USER=postgres POSTGRES_PASSWORD=verify
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55433
export BOT_TOKEN="123456789:AAFake..."   # обязателен, polling не запускаем
export JWT_SECRET="local-secret"         # обязателен
PYTHONPATH=$PWD venv/bin/python -c "import bot_schedule2 as B; B.app.run(port=5055)"
```

`bot_schedule2.py` под `__main__` стартует polling бота — импортировать модуль и звать
`app.run()` самому, а не запускать файл.

## Доступ к API

Эндпоинты за `require_auth` (JWT в куке или `Authorization: Bearer`). Токен брать
настоящим логином, пароли — `passlib.pbkdf2_sha256`:

```bash
curl -s -X POST localhost:5055/api/login -H 'Content-Type: application/json' \
  -d '{"login":"...","password":"..."}'   # -> access_token
```

Гоча: `X-User-Id` обязан совпадать с `sub` токена, иначе 403 «X-User-Id does not match».

## Фронт

`API_BASE_URL` в `src/App.jsx` захардкожен на прод. Не править исходник — собрать и
подменить строку в копии бандла:

```bash
npm run build && cp -r dist /tmp/web
sed -i '' 's|https://otp-2-fos4\.onrender\.com|http://127.0.0.1:5055|g' /tmp/web/assets/*.js
python -m http.server 5077 --bind 127.0.0.1   # CORS уже пускает localhost:любой порт
```

Драйвить браузером — `puppeteer-core` + системный Chrome
(`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`), ставить в scratchpad,
чтобы не трогать `package.json` репозитория. Вход: два первых `input` на странице —
логин и пароль. Разделы в сайдбаре ищутся по точному тексту листового элемента.

## Внешние интеграции

Живые API (CRM yataxi, Oktell, Binotel…) читать можно — они read-only на чтении.
Ломаные ответы удобно проверять заглушкой на `http.server`, подменив `CRM_API_URL`
(и подобные) в окружении: `get_config()` читает env раньше `.env.codex.local`.
