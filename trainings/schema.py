# -*- coding: utf-8 -*-
"""Схема раздела «Тренинги»: справочник корпоративных тем (training_topics),
привязка проведённого тренинга к теме (trainings.topic_id) и контрольные точки
по сотруднику (operator_checkpoints) — вкладка «Контроль».

Идемпотентно: CREATE TABLE / INDEX IF NOT EXISTS + ALTER ... ADD COLUMN IF NOT
EXISTS. Вызывается один раз при старте из Database._init_db через
init_trainings_schema(cursor).

Зачем отдельная таблица тем, а не расширение CHECK на trainings.reason.
Причина тренинга сидит в `trainings.reason` под именованным констрейнтом
`trainings_reason_check` со списком из 11 литералов. Корпоративную тему
придумывает человек — её название нельзя перечислить в CHECK ни сейчас, ни
потом. Поэтому:

* тема — строка в `training_topics` (название, тип, отдел, автор);
* проведённый корпоративный тренинг — обычная строка в `trainings` c
  заполненным `topic_id`, а в `reason` кладётся НАЗВАНИЕ темы;
* CHECK расширяется не списком, а условием: `topic_id IS NOT NULL OR reason IN
  (...)`. Все существующие 1648 строк остаются валидными, а `reason` остаётся
  человекочитаемым — на него смотрят семь мест вне раздела, включая экран
  «Мои часы» у самого сотрудника и лист «Тренинги» в выгрузке.

Ключевые решения, которые видны в DDL:

* **Корпоративная тема не идёт в оплачиваемые часы.** `count_in_hours` у темы
  по умолчанию FALSE, и раскатка ставит его в самой записи тренинга. Решение
  владельца: информационная тема — «просто факт прохождения», не оплата. Это
  же снимает с новой механики весь зарплатный риск: строки с
  `count_in_hours = FALSE` не попадают в `_load_training_hours_by_operator_tx`.
* **Тип пока один — 'info' («Информационный»).** Набор в CHECK, но набор
  осознанно короткий: второй тип без реального запроса означал бы поле в
  форме, которое никто не заполняет осмысленно.
* **Охват считается на лету, а не хранится.** Знаменатель — активные
  сотрудники отдела, а он меняется каждым приёмом и увольнением;
  материализованный счётчик пришлось бы пересчитывать на каждое кадровое
  событие. Числитель — COUNT(DISTINCT operator_id) по `trainings.topic_id`.
  Объём раздела это позволяет: 1648 строк за всё время.
* **Отдельной таблицы «раскатки» нет.** Раскатка пачками — это просто
  несколько строк в `trainings` с одной темой. Отдельная таблица назначений
  (как `survey_assignments` c UNIQUE(entity, user)) запретила бы повторить
  тему новому сотруднику и завела бы вторую точку правды о том, кому
  тренинг проведён.
* **Название темы уникально внутри отдела**, и только среди неархивных: одна
  и та же тема в двух отделах — норма, а два «Новые правила отмены» в одном
  отделе — почти всегда опечатка.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Тип корпоративной темы. Пока один — «Информационный»: донести новость, важен
# охват, а не отработка навыка.
TOPIC_KINDS = ('info',)

TOPIC_KIND_LABELS = {
    'info': 'Информационный',
}

# Базовые темы — те самые 11 литералов из trainings_reason_check. Порядок =
# порядок показа в списке выбора.
DEFAULT_REASONS = (
    'Обратная связь',
    'Собрание',
    'Мотивационная беседа',
    'Дисциплинарный тренинг',
    'Тренинг по качеству. Разбор ошибок',
    'Тренинг по качеству. Объяснение МШ',
    'Тренинг по продукту',
    'Мониторинг',
    'Практика в офисе таксопарка',
    'Тех. сбой',
    'Другое',
)

# Архивные базовые темы: на старых записях показываются и редактируются, для
# НОВОГО тренинга не предлагаются.
#
# «Тех. сбой» — 164 записи с сентября 2025. Раздела «Тех. сбои» тогда не
# существовало (первая запись operator_technical_issues — 03.03.2026), и сбои
# писали тренингом, потому что писать их было некуда. Переносить нельзя: все
# 164 идут в оплачиваемые часы, и перенос пересчитал бы зарплату за
# сентябрь 2025 — август 2026.
ARCHIVED_REASONS = ('Тех. сбой',)

# Причина, под которой в trainings попадает разбор звонка из «Журнала оценок».
# Держим рядом, чтобы список базовых тем и запись из журнала не разъехались.
CALL_FEEDBACK_REASON = 'Обратная связь'


# ── Контрольные точки по сотруднику ─────────────────────────────────────────
# Вид контроля. Три значения — ровно те, что назвал заказчик задачи #86; шире
# набор не делаем: тип, которого никто не выбирает, это лишняя строка в форме.
CHECKPOINT_KINDS = ('quality', 'probation', 'recheck')

CHECKPOINT_KIND_LABELS = {
    'quality': 'Контроль качества',
    'probation': 'Испытательный срок',
    'recheck': 'Повторная проверка качества',
}

# Жизненный цикл точки. 'open' — ждёт проверки, 'done' — проверку провели,
# 'cancelled' — контроль сняли, не проводя. Отдельного 'overdue' НЕТ намеренно:
# просрочка это не состояние записи, а сравнение due_date с сегодняшним днём,
# и хранить её означало бы вечно догонять календарь ночным заданием.
CHECKPOINT_STATUSES = ('open', 'done', 'cancelled')

CHECKPOINT_STATUS_LABELS = {
    'open': 'Ждёт проверки',
    'done': 'Проверено',
    'cancelled': 'Контроль снят',
}


def checkpoint_kind_label(kind):
    return CHECKPOINT_KIND_LABELS.get(str(kind or '').strip(), 'Контроль качества')


def active_default_reasons():
    """Базовые темы, доступные для НОВОГО тренинга."""
    return tuple(reason for reason in DEFAULT_REASONS if reason not in ARCHIVED_REASONS)


def reason_check_sql():
    """Условие CHECK для trainings.reason.

    Не список значений, а «либо тема из справочника, либо базовая причина».
    Собирается из DEFAULT_REASONS, чтобы список литералов жил в одном месте.
    """
    literals = ', '.join("'%s'" % reason.replace("'", "''") for reason in DEFAULT_REASONS)
    return "topic_id IS NOT NULL OR reason IN (%s)" % literals


_STATEMENTS = [

    # ──────────────────────────────────────────────────────────────────────
    # КОРПОРАТИВНЫЕ ТЕМЫ
    #
    # department_id — и область видимости темы, и знаменатель охвата. NULL
    # означает «общая для всей компании»; ON DELETE SET NULL, потому что
    # удаление отдела не должно уносить историю проведённых тренингов.
    # ──────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS training_topics (
        id             SERIAL PRIMARY KEY,
        title          VARCHAR(255) NOT NULL,
        kind           VARCHAR(16)  NOT NULL DEFAULT 'info',
        department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        description    TEXT,
        count_in_hours BOOLEAN NOT NULL DEFAULT FALSE,
        is_archived    BOOLEAN NOT NULL DEFAULT FALSE,
        created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT training_topics_kind_check CHECK (kind IN ('info'))
    )
    """ % {'now': _NOW},

    # Одна тема на отдел. Только среди живых: архивная не мешает завести тему
    # с тем же названием заново. COALESCE — потому что NULL в обычном UNIQUE
    # не конфликтует сам с собой, и две общекорпоративные темы с одним
    # названием прошли бы.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_training_topics_title_dept
        ON training_topics (lower(title), COALESCE(department_id, 0))
     WHERE is_archived = FALSE
    """,

    # Список тем раздела: отдел + живые сверху.
    """
    CREATE INDEX IF NOT EXISTS idx_training_topics_dept
        ON training_topics (department_id, is_archived, id DESC)
    """,

    # Охват темы и её сессии за месяц. Частичный — корпоративных строк в
    # trainings меньшинство, и незачем держать в индексе 1648 NULL'ов.
    """
    CREATE INDEX IF NOT EXISTS idx_trainings_topic
        ON trainings (topic_id, training_date DESC)
     WHERE topic_id IS NOT NULL
    """,

    # ──────────────────────────────────────────────────────────────────────
    # КОНТРОЛЬНЫЕ ТОЧКИ ПО СОТРУДНИКУ (задача #86)
    #
    # Вкладка «Контроль» живёт в «Журнале оценок» (там же, где их и ставят —
    # из окна «Дать ОС»). Таблица осталась в схеме раздела «Тренинги», потому
    # что разворачивается тем же init_trainings_schema; на пользователя это
    # никак не смотрит.
    #
    # Супервайзер провёл обратную связь по оценке и решил поставить человека
    # на контроль: назначить дату повторной проверки и не потерять срок.
    #
    # Почему СВОЯ таблица, а не строка в `trainings` на будущую дату.
    # `trainings` — журнал ПРОВЕДЁННЫХ занятий: его строки идут в оплачиваемые
    # часы (_load_training_hours_by_operator_tx), в квоту звонков и в лист
    # «Тренинги» выгрузки, и сотрудник видит их у себя в «Моих часах».
    # Запланированная проверка не занятие: она ещё не состоялась, часов не
    # даёт, а половина её полей — служебные и сотруднику не показываются
    # вовсе. Плюс `trainings` требует start_time/end_time и держит
    # UNIQUE(operator_id, date, start, end) — у точки времени нет, и его
    # пришлось бы выдумывать, конфликтуя с настоящими занятиями того дня.
    #
    # Что видно сотруднику, а что нет (требование постановки):
    #   видно      — дата проверки и `focus` («что нужно исправить»);
    #   НЕ видно   — `kind` (в том числе «испытательный срок»), `reason`
    #                и `internal_comment`.
    # Разделение держится не комментарием, а разными наборами полей в двух
    # ветках выдачи (см. bot_schedule2::_checkpoint_payload).
    """
    CREATE TABLE IF NOT EXISTS operator_checkpoints (
        id                 SERIAL PRIMARY KEY,
        operator_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        supervisor_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        feedback_id        INTEGER REFERENCES call_feedbacks(id) ON DELETE CASCADE,
        call_id            INTEGER,
        kind               VARCHAR(16) NOT NULL,
        reason             TEXT NOT NULL,
        due_date           DATE NOT NULL,
        focus              TEXT NOT NULL,
        internal_comment   TEXT,
        notify_operator    BOOLEAN NOT NULL DEFAULT TRUE,
        status             VARCHAR(16) NOT NULL DEFAULT 'open',
        resolved_at        TIMESTAMP,
        resolved_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
        resolution_comment TEXT,
        created_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by         INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at         TIMESTAMP NOT NULL DEFAULT %(now)s,
        CONSTRAINT operator_checkpoints_kind_check
            CHECK (kind IN ('quality', 'probation', 'recheck')),
        CONSTRAINT operator_checkpoints_status_check
            CHECK (status IN ('open', 'done', 'cancelled'))
    )
    """ % {'now': _NOW},

    # Одна ОТКРЫТАЯ точка на одну обратную связь. Блок живёт ВНУТРИ окна
    # «Дать ОС»: повторное сохранение той же ОС обязано править прежнюю точку,
    # а не плодить вторую на каждое нажатие «Обновить ОС».
    #
    # Почему условие по статусу, а не просто UNIQUE(feedback_id). Одного и того
    # же человека по одному и тому же разбору берут на контроль повторно:
    # проверили, не выправилось — назначили ещё одну. С безусловным UNIQUE
    # вторая проверка могла бы появиться только вместо первой, то есть стёрла
    # бы факт, что первая была проведена.
    #
    # UNIQUE-индекс, а не UNIQUE-колонка: у пакетной ОС точка одна на всю
    # пачку, остальные обратные связи остаются с NULL, а NULL в UNIQUE сам с
    # собой не спорит.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_operator_checkpoints_feedback
        ON operator_checkpoints (feedback_id)
     WHERE feedback_id IS NOT NULL AND status = 'open'
    """,

    # Главный экран раздела — «что горит»: открытые точки по возрастанию срока.
    # Частичный: закрытые точки в списке не нужны, а составляют со временем
    # его большую часть.
    """
    CREATE INDEX IF NOT EXISTS idx_operator_checkpoints_open
        ON operator_checkpoints (due_date, id)
     WHERE status = 'open'
    """,

    # История по сотруднику: карточка «сколько раз ставили на контроль».
    """
    CREATE INDEX IF NOT EXISTS idx_operator_checkpoints_operator
        ON operator_checkpoints (operator_id, due_date DESC)
    """,
]


# Столбцы и констрейнты, которых нет на боевой базе: CREATE TABLE IF NOT
# EXISTS на существующей таблице — no-op, поэтому догоняем отдельно.
def _migrations():
    return [
        # Привязка проведённого тренинга к корпоративной теме.
        "ALTER TABLE trainings ADD COLUMN IF NOT EXISTS topic_id INTEGER",

        # FK отдельным шагом и идемпотентно: ADD CONSTRAINT IF NOT EXISTS в
        # Postgres нет, а повторный ADD упал бы на втором старте.
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'trainings_topic_id_fkey'
                   AND conrelid = 'trainings'::regclass
            ) THEN
                ALTER TABLE trainings
                    ADD CONSTRAINT trainings_topic_id_fkey
                    FOREIGN KEY (topic_id) REFERENCES training_topics(id) ON DELETE SET NULL;
            END IF;
        END $$
        """,

        # Расширение CHECK на reason. Снимаем по имени и ставим тем же именем —
        # идемпотентно и повторяемо. Имя проверено на проде:
        # pg_get_constraintdef отдаёт именно trainings_reason_check.
        "ALTER TABLE trainings DROP CONSTRAINT IF EXISTS trainings_reason_check",
        """
        ALTER TABLE trainings
            ADD CONSTRAINT trainings_reason_check CHECK (%s)
        """ % reason_check_sql(),
    ]


def _is_table(statement):
    return 'CREATE TABLE' in statement.upper()


def init_trainings_schema(cursor):
    """Разворачивает схему раздела. Курсор приходит из _init_db, транзакцией
    управляет вызывающий.

    Порядок — таблицы, потом ALTER'ы, и только потом индексы и констрейнты по
    новым столбцам. Он не для красоты: на пустой базе всё равно, а на уже
    развёрнутой частичный индекс idx_trainings_topic создался бы раньше, чем
    появился столбец topic_id, — и упал. Падение внутри SAVEPOINT молчаливое:
    откатывается ВЕСЬ разворот схемы раздела, включая расширение CHECK, и
    раздел остаётся на старой структуре. Так 17.08.2026 лёг прод в разделе
    «Обращения» (uq_crm_queues_code до ALTER ... ADD COLUMN code).
    """
    for statement in _STATEMENTS:
        if _is_table(statement):
            cursor.execute(statement)
    for statement in _migrations():
        cursor.execute(statement)
    for statement in _STATEMENTS:
        if not _is_table(statement):
            cursor.execute(statement)


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздел ещё не поднялся» от «тем пока нет»."""
    cursor.execute("SELECT to_regclass('public.training_topics') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
