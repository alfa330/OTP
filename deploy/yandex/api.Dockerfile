# syntax=docker/dockerfile:1
# API портала: Flask под waitress, Telegram-бот и планировщик — один процесс
# (python bot_schedule2.py, как start.sh на Render). Python 3.11 — как в runtime.txt:
# на 3.12+ не собирается aiohttp для aiogram 2.25.2.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Almaty

# weasyprint — обёртка над pango/harfbuzz: колесо ставится, импорт без библиотек падает
# (так же ставит CI, .github/workflows/tests.yml). Шрифты DejaVu — кириллица в PDF.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig fonts-dejavu-core \
        ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Корневой сертификат Yandex Cloud: с ним к Managed PostgreSQL можно ходить
# с PGSSLMODE=verify-full (см. .env.example). Для Postgres в контейнере не нужен и не мешает.
ADD --chmod=0644 https://storage.yandexcloud.net/cloud-certs/CA.pem /etc/ssl/certs/yandex-cloud-ca.pem

WORKDIR /app

# Зависимости — отдельным слоем: меняются реже кода, пересборка после правки кода
# не ставит ortools и pandas заново.
COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN useradd --system --uid 1000 --home-dir /tmp --shell /usr/sbin/nologin otp
COPY --chown=otp:otp . .
USER otp

EXPOSE 8080

# /api/health делает SELECT 1. start-period — схема базы при старте занимает до двух минут,
# раньше этого «нездоров» ничего не значит. curl в slim-образе нет — urllib.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=8)"

CMD ["python", "bot_schedule2.py"]
