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
# Канонические (стабильные) id направлений: живая строка направления держит id
# навсегда (переименования и правки критериев его не меняют — см. save_directions).
OP_DIRECTION_IDS = [72, 73, 74]  # Яндекс Регистрация / Основа / Поток


def op_direction_id_family(cur) -> list[int]:
    """Все направления отдела продаж: живые строки + архивные версии шкалы.

    Исторические оценки (calls) при смене критериев перевешиваются на архивные
    строки directions (canonical_id -> живая строка), поэтому выборки должны
    фильтровать по всей семье id, а не только по каноническим.

    Семья выводится из ОТДЕЛА, а не из литерального списка id: раньше здесь были
    захардкожены 72/73/74, и «Верификатор» (71) молча выпадал из allowlist
    разборов, rollout и случайной выборки. Список OP_DIRECTION_IDS остался
    аварийным значением, если запрос не удался."""
    try:
        cur.execute(
            """SELECT d.id FROM directions d
                WHERE d.department_id = %s
                   OR d.canonical_id IN (SELECT id FROM directions
                                          WHERE department_id = %s)""",
            (OP_DEPARTMENT_ID, OP_DEPARTMENT_ID))
        ids = [int(r[0]) for r in cur.fetchall()]
    except Exception:
        ids = []
    if ids:
        return ids
    cur.execute(
        "SELECT id FROM directions WHERE id = ANY(%s) OR canonical_id = ANY(%s)",
        (OP_DIRECTION_IDS, OP_DIRECTION_IDS))
    ids = [int(r[0]) for r in cur.fetchall()]
    return ids or list(OP_DIRECTION_IDS)

# --- ASR (Soniox) ---
SONIOX_BASE = "https://api.soniox.com"
SONIOX_MODEL = "stt-async-v5"
SONIOX_LANGS = ["kk", "ru"]
ASR_CONF_SOFT = 0.70   # подсветка неуверенного токена
ASR_CONF_HARD = 0.50   # «реальный» неуверенный спан

# --- Эмбеддинги / retrieval ---
EMBEDDINGS_PROVIDER = str(env("EMBEDDINGS_PROVIDER", "vertex")).strip().lower()  # vertex | selfhost
VERTEX_REGION = env("VERTEX_REGION", "asia-southeast1")
VERTEX_EMBED_MODEL = env("VERTEX_EMBED_MODEL", "text-multilingual-embedding-002")
SELFHOST_EMBED_MODEL = env("SELFHOST_EMBED_MODEL", "intfloat/multilingual-e5-small")
# Размерность является частью контракта индекса. Провайдер с другой размерностью
# отклоняется до обращения к pgvector, а не даёт позднюю/непонятную ошибку БД.
EMBED_DIM = int(env("EMBED_DIM", "768"))

# Транскрипт режется перекрывающимися окнами. При очень длинном звонке окна
# выбираются равномерно по всей временной оси (начало/середина/конец), а не только
# из головы и хвоста.
EMBED_CHUNK_CHARS = int(env("EMBED_CHUNK_CHARS", "3200"))
EMBED_CHUNK_OVERLAP = int(env("EMBED_CHUNK_OVERLAP", "480"))
EMBED_MAX_CHUNKS = int(env("EMBED_MAX_CHUNKS", "16"))

# --- LLM (Claude). По умолчанию одна модель (Opus) на всё: бенч 2026-07-07 показал,
# что Opus точнее Sonnet в разы (MAE 5 vs 18-24), а двухуровневая схема с разборами
# эскалирует ~все звонки и выходит ДОРОЖЕ чистого Opus. Механизм эскалации сохранён:
# задайте CLAUDE_MODEL_BULK дешевле HARD — и двухуровневость включится сама. ---
CLAUDE_MODEL_BULK = env("CLAUDE_MODEL_BULK", "claude-opus-4-8")     # первый проход
CLAUDE_MODEL_HARD = env("CLAUDE_MODEL_HARD", "claude-opus-4-8")     # эскалация (если отличается от BULK)
ESCALATE_CONF = float(env("CLAUDE_ESCALATE_CONF", "0.6"))          # не выше порога — критерий уходит на HARD-модель
# Тег для кэша/меты (при смене моделей меняется → старые кэш-оценки не подмешиваются).
CLAUDE_MODEL = env("CLAUDE_MODEL", f"{CLAUDE_MODEL_BULK}+{CLAUDE_MODEL_HARD}")
CLAUDE_EFFORT = env("CLAUDE_EFFORT", "high")
# TTL prompt-кеша системного блока (промпт оценщика + критерии, 4.6-6.3 тыс токенов).
# Пакетный прогон держит запись час: Batch API не гарантирует порядок обработки, и при
# дефолтных 5 минутах запись протухает раньше следующего звонка того же направления —
# замер 2026-08 дал 25% попаданий (27 записей на 9 чтений) и +35% к счёту.
# Запись по 1h стоит 2x против 1.25x, но происходит раз в час на направление.
# Пустое значение возвращает дефолтные 5 минут (для интерактивной оценки так и надо:
# одиночный вызов не окупает удвоенную запись).
CLAUDE_CACHE_TTL_BATCH = env("CLAUDE_CACHE_TTL_BATCH", "1h") or None


def anthropic_key():
    """Принимаем оба имени: ANTHROPIC_API_KEY или CLAUDE_API_KEY."""
    return env("ANTHROPIC_API_KEY") or env("CLAUDE_API_KEY")

# --- Субъекты оценки ---
# Раздел начинался со звонков; у Верификаторов ОП единица оценки — эпизод
# переписки Wazzup. Значения совпадают с колонкой subject_kind в schema.sql.
SUBJECT_CALL = "call"
SUBJECT_WZ_EPISODE = "wz_episode"
SUBJECT_KINDS = (SUBJECT_CALL, SUBJECT_WZ_EPISODE)

# --- Эпизоды чатов Wazzup (Верификаторы) ---
# Направления Верификаторов не хардкодятся: как и кнопка «Случайный чат», они
# определяются кодом отдела + маркером в названии направления, поэтому новое или
# переименованное направление подхватывается без правки кода.
WZ_DEPARTMENT_CODE = str(env("WZ_RANDOM_CHAT_DEPARTMENT_CODE", "op")).strip().lower()
WZ_DIRECTION_MARKER = str(env("WZ_RANDOM_CHAT_DIRECTION_MARKER", "верификатор")).strip().lower()
# Порог атрибуции: в одном эпизоде могут отвечать несколько операторов, и оценка
# «в одни руки» тогда несправедлива. Оцениваем только если доля ответов
# доминирующего оператора не ниже порога (см. wazzup_episodes.operator_share).
WZ_MIN_OPERATOR_SHARE = float(env("WZ_MIN_OPERATOR_SHARE", "0.9"))
# Минимум сообщений оператора: два «ок» невозможно оценить по 15 критериям.
WZ_MIN_OPERATOR_MESSAGES = int(env("WZ_MIN_OPERATOR_MESSAGES", "2"))

# --- Вложения чатов: изображения (Claude vision) и голосовые (Soniox) ---
# Транскрипт эпизода содержит только заглушки ([фото], [голосовое]); без их
# содержания оценка слепа. Описание считается один раз и хранится в
# wz_media_annotations (переживает 45-дневный ретеншн сырых сообщений).
CLAUDE_MODEL_VISION = env("CLAUDE_MODEL_VISION", "claude-sonnet-5")
CLAUDE_VISION_EFFORT = env("CLAUDE_VISION_EFFORT", "low")
# Плотный документ (паспорт, договор) не должен обрезаться на середине: обрезанный
# ответ считается неудачей и повторяется, поэтому лимит взят с запасом.
VISION_MAX_TOKENS = int(env("CLAUDE_VISION_MAX_TOKENS", "2500"))
# Anthropic принимает изображения до 5 МБ на картинку; крупнее не тянем вовсе,
# чтобы не ловить 413 после платной загрузки.
MEDIA_MAX_BYTES = int(env("WZ_MEDIA_MAX_BYTES", str(5 * 1024 * 1024)))
MEDIA_HTTP_TIMEOUT = float(env("WZ_MEDIA_HTTP_TIMEOUT", "30"))
MEDIA_IMAGE_TYPES = ("image",)
MEDIA_AUDIO_TYPES = ("audio",)
MEDIA_DOCUMENT_TYPES = ("document",)
# PDF уходит в запрос целиком. Предел запроса Anthropic — 32 МБ, поэтому сырой
# файл ограничиваем с запасом на base64 (+33%). Ограничение по страницам (600)
# проверяет сама модель — считать их локально нечем (PDF-библиотек в проекте нет),
# и отказ придёт понятной ошибкой, а не молчанием.
MEDIA_PDF_MAX_BYTES = int(env("WZ_MEDIA_PDF_MAX_BYTES", str(16 * 1024 * 1024)))
# Документ длиннее картинки: ответ должен помещаться целиком (обрезанный = неудача).
DOCUMENT_MAX_TOKENS = int(env("CLAUDE_DOCUMENT_MAX_TOKENS", "3000"))
# Сколько вложений максимум расшифровываем на один эпизод (страховка от чата,
# где клиент прислал 200 фото: и по деньгам, и по времени открытия карточки).
MEDIA_MAX_PER_EPISODE = int(env("WZ_MEDIA_MAX_PER_EPISODE", "24"))
# Бюджет ОТКРЫТИЯ КАРТОЧКИ: расшифровать 24 вложения по очереди — это минуты
# ожидания в одном HTTP-запросе. Остальное доберёт ночной пакетный прогон.
MEDIA_MAX_INTERACTIVE = int(env("WZ_MEDIA_MAX_INTERACTIVE", "8"))
# Batch API ограничивает размер запроса; картинка в base64 весит мегабайты,
# поэтому «весь месяц одним POST» упёрся бы в предел и в память процесса.
MEDIA_BATCH_MAX_BYTES = int(env("WZ_MEDIA_BATCH_MAX_BYTES", str(64 * 1024 * 1024)))
MEDIA_BATCH_MAX_ITEMS = int(env("WZ_MEDIA_BATCH_MAX_ITEMS", "200"))
# Предел ожидания батча: без него подвисший батч молча вешал бы ночной прогон.
MEDIA_BATCH_DEADLINE_S = int(env("WZ_MEDIA_BATCH_DEADLINE_S", str(6 * 3600)))

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
RETRIEVAL_TOP_K = int(env("RETRIEVAL_TOP_K", "3"))
# Ноль подходящих правил — штатный результат. Ближайший вектор ниже порога в
# промпт не попадает.
RETRIEVAL_MIN_SIMILARITY = float(env("RETRIEVAL_MIN_SIMILARITY", "0.68"))
# Для наблюдаемости сохраняем несколько кандидатов за пределами итогового top-k.
RETRIEVAL_CANDIDATE_MULTIPLIER = int(env("RETRIEVAL_CANDIDATE_MULTIPLIER", "4"))
RETRIEVAL_LEXICAL_MIN_SCORE = float(env("RETRIEVAL_LEXICAL_MIN_SCORE", "0.05"))
# Lexical match only rescues a dense candidate close to the semantic gate; it
# cannot inject an unrelated rule solely because a common word matched.
RETRIEVAL_LEXICAL_DENSE_MARGIN = float(env("RETRIEVAL_LEXICAL_DENSE_MARGIN", "0.08"))

# Controlled production rollout.  ``shadow`` keeps the user-facing verdict on
# the no-RAG path while collecting a paired RAG run; canary selection is stable
# by call ID.  Set ``active`` only after the benchmark gates are met.
RAG_MODE = str(env("RAG_MODE", "shadow")).strip().lower()
RAG_CANARY_PERCENT = max(0, min(100, int(env("RAG_CANARY_PERCENT", "10"))))
RAG_TRACE_REQUIRED = str(env("RAG_TRACE_REQUIRED", "true")).strip().lower() in {
    "1", "true", "yes", "on",
}
RAG_REINDEX_MAX_ATTEMPTS = max(1, int(env("RAG_REINDEX_MAX_ATTEMPTS", "5")))
EVALUATOR_CODE_VERSION = str(env("AI_QA_CODE_VERSION", "ai-qa-2026-07-v3"))  # v3: вердикт Deficiency («Недочёт»)
