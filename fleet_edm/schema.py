"""Схема раздела «Провайдер ЭДО». Все таблицы с префиксом fleet_edm_.

Идемпотентно (CREATE TABLE/INDEX IF NOT EXISTS), вызывается один раз при старте
из Database._init_db через init_fleet_edm_schema(cursor) — как init_crm_schema и
init_oktell_guard_schema.

Модель:

    fleet_edm_jobs       карточка выгрузки: кто, когда, сколько строк, прогресс,
                         итоговая статистика, ошибка.
    fleet_edm_job_files  тела файлов (исходник и результат) — отдельной таблицей.
    fleet_edm_session    сессия кабинета Fleet: куки живого логина. Одна строка.

Почему файлы отдельной таблицей. Список выгрузок читается при каждом входе в
раздел, а файл нужен только когда его скачивают. Держать рядом означало бы
таскать десятки мегабайт BYTEA в каждом запросе списка — та же причина, по
которой в «Боте опозданий» разведены glb_reports и glb_report_files.

Почему файлы в базе, а не в GCS. Диск на Render эфемерный, это отпадает сразу.
GCS подошёл бы, но здесь файл читает ровно один человек ровно один раз, объёмы
единицы мегабайт, и в отличие от exe агента раздача не идёт на сотню машин.
Зато в базе есть ретеншн и права, а лишний бакет — лишняя настройка окружения.

Про куки в fleet_edm_session. Это по сути пароль от кабинета: полный доступ ко
всем 86 диспетчерским от имени сотрудника. Поэтому: значение НИКОГДА не уходит
наружу через API (ручка отдаёт только метаданные — аккаунт, дату обновления,
жива ли сессия), в логи не пишется, а обновлять её может только глобальный админ
(fleet_edm.access.can_manage_session). Шифровать в базе не стали: ключ лежал бы в
том же окружении, что и приложение, читающее эту же базу, — защита от того, кто
и так внутри, получилась бы декоративной. Настоящая граница здесь — доступ к
самой базе.

Ретеншн: тела файлов подчищаются fleet_edm_cleanup() (по умолчанию 60 дней, как
у отчётов «Бота опозданий»), карточки остаются — по ним видно историю выгрузок.
"""

FLEET_EDM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fleet_edm_jobs (
    id                BIGSERIAL PRIMARY KEY,
    created_by        INTEGER,
    created_by_name   TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'done', 'error')),
    source_name       TEXT,
    source_size       INTEGER,
    rows_total        INTEGER NOT NULL DEFAULT 0,
    rows_resolved     INTEGER NOT NULL DEFAULT 0,
    rows_failed       INTEGER NOT NULL DEFAULT 0,
    requests_count    INTEGER NOT NULL DEFAULT 0,
    progress_percent  INTEGER NOT NULL DEFAULT 0,
    progress_note     TEXT,
    duration_ms       INTEGER,
    error             TEXT,
    error_code        TEXT,
    stats             JSONB,
    file_name         TEXT,
    file_size         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fleet_edm_jobs_created ON fleet_edm_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_edm_jobs_status ON fleet_edm_jobs(status);

-- Когда карточка последний раз подавала признаки жизни. Не то же самое, что
-- started_at: выгрузка живёт в памяти инстанса, и деплой посреди работы убивает
-- поток молча. По времени старта такую карточку не отличить от честно идущей
-- долгой выгрузки, а по «давно ничего не писала» — отличить сразу.
ALTER TABLE fleet_edm_jobs ADD COLUMN IF NOT EXISTS progress_at TIMESTAMPTZ;

-- kind: 'source' — то, что загрузил человек, 'result' — то, что собрал робот.
-- Исходник храним, чтобы выгрузку можно было повторить, не прося файл заново.
CREATE TABLE IF NOT EXISTS fleet_edm_job_files (
    job_id    BIGINT NOT NULL REFERENCES fleet_edm_jobs(id) ON DELETE CASCADE,
    kind      TEXT   NOT NULL CHECK (kind IN ('source', 'result')),
    file_name TEXT,
    content   BYTEA  NOT NULL,
    PRIMARY KEY (job_id, kind)
);

CREATE TABLE IF NOT EXISTS fleet_edm_session (
    id           INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    cookies      TEXT NOT NULL,
    user_agent   TEXT,
    account      TEXT,
    parks_count  INTEGER,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   INTEGER,
    last_ok_at   TIMESTAMPTZ,
    last_error   TEXT
);
"""


def init_fleet_edm_schema(cursor):
    """Создаёт таблицы раздела. Курсор приходит снаружи: инициализация схемы идёт
    одной транзакцией со всеми остальными разделами."""
    cursor.execute(FLEET_EDM_SCHEMA_SQL)
