# -*- coding: utf-8 -*-
"""Схема раздела «Новости».

Три таблицы: сама новость, её адресаты и журнал прочтений. Всё под префиксом
`news_`, как таблицы вики под `wiki_`.

Порядок операторов важен: `news_audience_rules` и `news_reads` ссылаются на
`news_posts`, а та — на `users` и `departments`. FK на таблицу, объявленную
ниже, уже роняли инициализацию вики (wiki/schema.py, шапка).
"""

# Виды адресата. Тот же язык, что у правил доступа вики
# (wiki/schema.py: SUBJECT_TYPES), но КОРОЧЕ на два вида:
#
#   'wiki_role'       — роль вики адресует людей мимо отдела и мимо должности,
#                       а новость обязана уметь ответить на вопрос «кому она
#                       ушла» списком живых сотрудников. Человек без вики роли
#                       вики не имеет вовсе — адресовать его ею нельзя.
#   'department_head' — «глава отдела» это адресат ВЫШЕ автора почти всегда,
#                       а новость идёт вниз по лестнице. Отдельный вид здесь
#                       был бы дверью в обратную сторону.
SUBJECT_TYPES = ('department', 'direction', 'group', 'otp_role', 'user')

# Статусы новости. 'draft' — черновик, виден только своим редакторам;
# 'published' — показывается адресатам; 'archived' — снята с показа, но
# журнал прочтений остаётся (по нему разбирают «был ли человек проинформирован»).
STATUSES = ('draft', 'published', 'archived')

# Задержка кнопки «Прочитал», в секундах. Ноль разрешён намеренно: короткое
# объявление на две строки читается быстрее любой задержки, и заставлять
# человека ждать ради самой задержки — тот самый шум.
#
# Потолок 600 (десять минут): всё, что дольше, — это не новость дня, а
# регламент, и для него в вике есть обязательное ознакомление со сроком
# (wiki_ack_assignments). Без потолка одна опечатка в поле («1000» вместо
# «10») запирала бы весь портал у всего отдела до конца смены.
DEFAULT_CONFIRM_DELAY_SECONDS = 10
MAX_CONFIRM_DELAY_SECONDS = 600

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS news_posts (
        id                    SERIAL PRIMARY KEY,
        title                 VARCHAR(255) NOT NULL,
        body                  TEXT NOT NULL DEFAULT '',
        author_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        -- Отдел автора НА МОМЕНТ ПУБЛИКАЦИИ, снимком. Не join к users: человек
        -- переходит в другой отдел, и «чья это новость» в журнале обязано
        -- остаться прежним ответом.
        author_department_id  INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        status                VARCHAR(20) NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'published', 'archived')),
        -- Обязательная новость показывается окном без крестика, необязательная —
        -- тем же окном, но его можно закрыть, и подтверждения от неё не ждут.
        is_mandatory          BOOLEAN NOT NULL DEFAULT TRUE,
        confirm_delay_seconds INTEGER NOT NULL DEFAULT %(default_delay)s
                              CHECK (confirm_delay_seconds BETWEEN 0 AND %(max_delay)s),
        -- ПОТОЛОК АДРЕСАТА, посчитанный при публикации из должности автора
        -- (news/access.py: publish_ceiling). Лежит на новости, а не на правиле,
        -- потому что отвечает за границу, которую автор не выбирал: «только
        -- тем, кто ниже меня». Правило отвечает за то, что автор выбрал сам.
        audience_max_role_level INTEGER,
        published_at          TIMESTAMP,
        -- До какого момента новость показывается. NULL — до подтверждения.
        expires_at            TIMESTAMP,
        created_by            INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at            TIMESTAMP NOT NULL DEFAULT %(now)s,
        updated_at            TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    # Выдача «что показать этому человеку» ходит по опубликованным и живым.
    """
    CREATE INDEX IF NOT EXISTS idx_news_posts_live
        ON news_posts(status, published_at DESC)
     WHERE status = 'published';
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_posts_author ON news_posts(author_id, created_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS news_audience_rules (
        id             SERIAL PRIMARY KEY,
        news_id        INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
        subject_type   VARCHAR(20) NOT NULL
                       CHECK (subject_type IN ('department', 'direction', 'group',
                                               'otp_role', 'user')),
        -- Числовой ключ адресата. У 'otp_role' его нет — там строка роли, как
        -- в правилах вики: роль это не строка справочника, а значение CHECK'а.
        subject_id     INTEGER,
        subject_role   VARCHAR(50),
        -- Сузить адресатов СНИЗУ: «отдел СЗоВ, но не ниже супервайзера».
        -- Сверху сужает audience_max_role_level самой новости.
        min_role_level INTEGER,
        created_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        CHECK ((subject_type = 'otp_role' AND subject_role IS NOT NULL)
            OR (subject_type <> 'otp_role' AND subject_id IS NOT NULL))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_audience_news ON news_audience_rules(news_id);",
    """
    CREATE TABLE IF NOT EXISTS news_reads (
        news_id      INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        -- Когда окно показали. Это же и точка отсчёта задержки кнопки: решает
        -- СЕРВЕР, а не таймер во фронте — иначе подтверждение «через 10 секунд»
        -- отправлялось бы из консоли за 10 миллисекунд.
        shown_at     TIMESTAMP NOT NULL DEFAULT %(now)s,
        confirmed_at TIMESTAMP,
        PRIMARY KEY (news_id, user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_reads_user ON news_reads(user_id) WHERE confirmed_at IS NULL;",
]


def init_news_schema(cursor):
    """Разворачивает схему раздела. Идемпотентно."""
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW)
                       .replace('%(default_delay)s', str(DEFAULT_CONFIRM_DELAY_SECONDS))
                       .replace('%(max_delay)s', str(MAX_CONFIRM_DELAY_SECONDS)))


def schema_is_ready(cursor):
    """Развёрнута ли схема. Отличает «раздела ещё не было» от «раздел сломан»."""
    cursor.execute("SELECT to_regclass('public.news_posts') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
