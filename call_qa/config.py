"""Конфигурация call_qa. Секреты берём из окружения (Render) или из .env.codex.local (dev).
Ничего секретного в коде не храним."""
import os
import json
import functools

_ENV_FILE = os.path.join(os.path.dirname(__file__), os.pardir, ".env.codex.local")


@functools.lru_cache(maxsize=1)
def _dev_env() -> dict:
    """Парсит .env.codex.local (только для локальной разработки)."""
    out = {}
    try:
        with open(_ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def env(key: str, default=None):
    """os.environ имеет приоритет; иначе — dev-файл."""
    return os.environ.get(key) or _dev_env().get(key) or default


def google_sa_info() -> dict | None:
    """Сервис-аккаунт GCP из GOOGLE_APPLICATION_CREDENTIALS_CONTENT (один JSON-объект)."""
    raw = env("GOOGLE_APPLICATION_CREDENTIALS_CONTENT")
    if not raw:
        return None
    raw = raw.lstrip()
    if raw[:1] in ("'", '"'):
        raw = raw[1:]
    return json.JSONDecoder().raw_decode(raw[raw.find("{"):])[0]


# --- ОП (отдел продаж) ---
OP_DEPARTMENT_ID = 367
OP_DIRECTION_IDS = [72, 73, 74]  # Яндекс Регистрация / Основа / Поток

# --- ASR (Soniox) ---
SONIOX_BASE = "https://api.soniox.com"
SONIOX_MODEL = "stt-async-v5"
SONIOX_LANGS = ["kk", "ru"]
ASR_CONF_SOFT = 0.70   # подсветка неуверенного токена
ASR_CONF_HARD = 0.50   # «реальный» неуверенный спан

# --- Эмбеддинги (Vertex) ---
EMBEDDINGS_PROVIDER = env("EMBEDDINGS_PROVIDER", "vertex")  # vertex | selfhost
VERTEX_REGION = env("VERTEX_REGION", "asia-southeast1")
VERTEX_EMBED_MODEL = env("VERTEX_EMBED_MODEL", "text-multilingual-embedding-002")
EMBED_DIM = 768

# --- LLM (Claude) ---
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-opus-4-8")
CLAUDE_EFFORT = env("CLAUDE_EFFORT", "high")


def anthropic_key():
    """Принимаем оба имени: ANTHROPIC_API_KEY или CLAUDE_API_KEY."""
    return env("ANTHROPIC_API_KEY") or env("CLAUDE_API_KEY")

# --- Хранилища ---
GCS_BUCKET = env("GCS_BUCKET", "my-app-audio-uploads")


def _pg_kwargs():
    """Параметры подключения, как у приложения (POSTGRES_*). None — если не заданы."""
    host = env("POSTGRES_HOST")
    if not host:
        return None
    return dict(dbname=env("POSTGRES_DB"), user=env("POSTGRES_USER"),
                password=env("POSTGRES_PASSWORD"), host=host, port=env("POSTGRES_PORT", 5432))


def _connect_pg():
    import psycopg2
    kw = _pg_kwargs()
    if not kw:
        raise RuntimeError("нет настроек БД: задайте POSTGRES_* (как в приложении) или DATABASE_URL(_READONLY)")
    return psycopg2.connect(**kw)


def connect_ro():
    """Чтение: локально DATABASE_URL_READONLY, на проде POSTGRES_* (полный доступ, сессия read-only)."""
    import psycopg2
    url = env("DATABASE_URL_READONLY")
    conn = psycopg2.connect(url) if url else _connect_pg()
    conn.set_session(readonly=True, autocommit=True)
    return conn


def connect_rw():
    """Запись: DATABASE_URL или POSTGRES_* (полный доступ). Локально (только RO) бросит ошибку."""
    import psycopg2
    url = env("DATABASE_URL")
    return psycopg2.connect(url) if url else _connect_pg()

# --- Ревью ---
REVIEW_MODEL_CONF = 0.60   # ниже — на ревью
RETRIEVAL_TOP_K = 3        # сколько разборов подтягивать на критерий
