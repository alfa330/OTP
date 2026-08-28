"""Конфигурация call_qa. Секреты берём из окружения (Render) или из .env.codex.local (dev).
Ничего секретного в коде не храним."""
import os
import json
import functools

_ENV_FILE = os.path.join(os.path.dirname(__file__), os.pardir, ".env.codex.local")


@functools.lru_cache(maxsize=1)
def _dev_env() -> dict:
    """Парсит .env.codex.local (только для локальной разработки).

    Значение может занимать НЕСКОЛЬКО СТРОК: JSON сервисного аккаунта в файле лежит
    с переносами, и построчный разбор давал по ключу
    GOOGLE_APPLICATION_CREDENTIALS_CONTENT ровно один символ «{». Из-за этого Vertex
    (эмбеддинги вики и разбора звонков, теперь ещё и оценка на Gemini) с машины
    разработчика не работал вовсе, а выглядело это как «провайдер молча отвалился».
    Поэтому у значения, начинающегося с «{», собираем строки, пока JSON не закроется.
    """
    out = {}
    try:
        with open(_ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return out
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value.startswith("{"):
            buffer = value
            while i < len(lines):
                try:
                    json.loads(buffer)
                    break
                except ValueError:
                    buffer += "\n" + lines[i]
                    i += 1
            value = buffer
        out[key.strip()] = value
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
#
# Провайдер выбирается ПО ИМЕНИ МОДЕЛИ (см. call_qa/providers.py): «gemini*» уходит в
# Vertex, «glm*» — в Z.ai, остальное — в Anthropic. Отдельного переключателя нет
# намеренно: модель уже входит в evaluation_fingerprint и в ai_review_cache, и второй
# независимый флаг означал бы оценки, подписанные не тем провайдером.
# Имена AI_QA_MODEL_* — основные; CLAUDE_MODEL_* оставлены как совместимость, потому
# что они уже заданы на Render и в скриптах. ---
#
# Умолчание — `glm-5.3-flash`. Замер 28.08.2026 на 140 звонках Основа ОП (2 520
# критериев, тем же промптом и той же схемой, эталон — сохранённые оценки Opus 4.8):
#
#   модель                штрафов  совпало  точность  полнота  медиана  выход
#   Opus 4.8 (эталон)         554        —         —        —        —  1 977
#   glm-5.3-flash (high)      476    79,5 %    67,4 %   57,9 %     91 с  3 956
#   gemini-3.7-flash          500    79,6 %    64,8 %   58,5 %     16 с  1 675
#
# GLM обходит Gemini по точности при равной полноте — то есть реже штрафует зря, а
# ложный штраф дороже пропуска: он попадает в карточку оператора и создаёт СВ работу,
# которую тот всё равно отменит. Плюс только на нём схема соблюдена на ВСЕХ 140
# звонках (у Gemini и у Claude — на 139). Месяц Основа ОП: 10 435 ₸ против 34 600 ₸.
# Оговорка, которую нельзя терять: Opus он НЕ повторяет, как и Gemini, — около трети
# его штрафов пропускает и примерно столько же ставит своих.
# Откат — одна переменная: AI_QA_MODEL_BULK=gemini-3.7-flash (и HARD), или
# claude-opus-4-8 для возврата на Anthropic.
CLAUDE_MODEL_BULK = env("AI_QA_MODEL_BULK") or env("CLAUDE_MODEL_BULK", "glm-5.3-flash")
CLAUDE_MODEL_HARD = env("AI_QA_MODEL_HARD") or env("CLAUDE_MODEL_HARD", "glm-5.3-flash")
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

# --- LLM (Vertex / Gemini). Значения по умолчанию — из замера 24.08.2026 на 24 звонках
# Основа ОП тем же промптом и той же схемой, что у Claude. ---
# Регион: модели 3.x отдаются только в `global` и `us-central1`; во Франкфурте (там прод)
# доступны лишь 2.5.x. `global` маршрутизирует сам и принимает пакетные задания на 3.x,
# тогда как us-central1 отклоняет их с MODEL_NOT_SUPPORTED_FOR_BATCH.
VERTEX_LLM_REGION = env("VERTEX_LLM_REGION", "global")
VERTEX_TEMPERATURE = float(env("VERTEX_TEMPERATURE", "0.1"))
VERTEX_TIMEOUT = float(env("VERTEX_TIMEOUT", "180"))
VERTEX_TRIES = int(env("VERTEX_TRIES", "5"))
VERTEX_RETRY_BASE_S = float(env("VERTEX_RETRY_BASE_S", "8"))
# «Мышление» тарифицируется как выход. На 3.5-flash гашение даёт ровный ноль, у
# 3.7-flash протекает ~178 токенов, у 3.1-pro не работает вовсе (3 510 токенов и 60 с
# на звонок). Пустое значение = не трогать параметр.
_VERTEX_THINKING_RAW = env("VERTEX_THINKING_BUDGET", "0")
VERTEX_THINKING_BUDGET = (int(_VERTEX_THINKING_RAW)
                          if str(_VERTEX_THINKING_RAW).strip() != "" else None)
# Неявный кеш промпта у Vertex срабатывает через раз (3 попадания из 9, включая промах
# на двух ОДИНАКОВЫХ запросах подряд), поэтому системный блок кешируется явно через
# cachedContents: замер даёт попадание 4 155 токенов из ~5 250 на каждом запросе.
# В деньгах на месяц Основа ОП это 49 тыс ₸ против 62 тыс.
VERTEX_EXPLICIT_CACHE = str(env("VERTEX_EXPLICIT_CACHE", "1")).strip().lower() in {
    "1", "true", "yes", "on",
}
VERTEX_CACHE_TTL_S = int(env("VERTEX_CACHE_TTL_S", "3600"))
# У Vertex пакетный режим — задание с файлом на GCS, а не один HTTP-запрос, как у
# Anthropic. Пока он не написан, ночной прогон на Gemini идёт последовательными
# вызовами в несколько потоков: это дороже ровно вдвое (нет скидки батча), но работает
# без нового хранилища. Потолок скромный: у Vertex общая квота на модель, и плотный
# прогон отвечает 429.
VERTEX_LOCAL_BATCH_WORKERS = int(env("VERTEX_LOCAL_BATCH_WORKERS", "4"))

# --- LLM (Z.ai / GLM). Значения по умолчанию — из замера 28.08.2026 на 140 звонках
# Основа ОП тем же промптом и той же схемой, что у Claude и Gemini. ---
ZAI_URL = env("ZAI_URL", "https://api.z.ai/api/paas/v4/chat/completions")
# Диапазон temperature у Z.ai полуоткрытый — (0, 1); ноль формально вне его, хотя на
# практике принимается. Держим то же значение, что у Vertex, чтобы прогоны сравнивались.
ZAI_TEMPERATURE = float(env("ZAI_TEMPERATURE", "0.1"))
# «Мышление» у GLM-5.3-Flash не отключается вовсе: thinking={'type':'disabled'} даёт
# 400 с кодом 1210 и подсказкой «please use low, high, or max». Ступеней ровно три —
# medium/minimal/none отвергаются тем же кодом. Умолчание вендора max ХУДШЕЕ по всем
# статьям: 388 с и 18 626 выходных токенов против 89 с и 3 956 у high, при полноте
# 47 % против 55 %. Уровень поэтому задаётся явно и НИКОГДА не остаётся вендорским.
ZAI_REASONING_EFFORT = env("ZAI_REASONING_EFFORT", "high")
# Оценка на high идёт около 91 с при p90 116 с — таймаут Vertex (180 с) здесь мал
# на длинных звонках, берём с запасом.
ZAI_TIMEOUT = float(env("ZAI_TIMEOUT", "300"))
ZAI_TRIES = int(env("ZAI_TRIES", "4"))
ZAI_RETRY_BASE_S = float(env("ZAI_RETRY_BASE_S", "8"))
# Пакетного API у glm-5.3-flash нет (проверено: /files с purpose=batch отвечает 400 со
# списком, где самая свежая модель — glm-5.1), поэтому ночной прогон идёт тем же
# локальным путём, что у Vertex. Потоков меньше, чем звонок в секунду: публичных
# лимитов RPM/TPM Z.ai не раскрывает, они видны только в кабинете.
ZAI_LOCAL_BATCH_WORKERS = int(env("ZAI_LOCAL_BATCH_WORKERS", "4"))


def anthropic_key():
    """Принимаем оба имени: ANTHROPIC_API_KEY или CLAUDE_API_KEY."""
    return env("ANTHROPIC_API_KEY") or env("CLAUDE_API_KEY")


def zai_key():
    return env("ZAI_API_KEY")

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
