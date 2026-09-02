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

# Сколько фотографий у одной новости (постановка владельца 02.09.2026).
# Столько же, сколько у посылки и у задачи, и это не совпадение: больше десяти
# — уже не объявление, а альбом, а окно «Новость дня» листают стоя, между
# звонками. Число проверяется И здесь, И в форме: правило, живущее только во
# фронте, держится до первого запроса мимо него.
MAX_PHOTOS_PER_POST = 10

# Сколько «ничьих» кадров человек может держать одновременно.
#
# Кадр грузится ДО того, как у новости появился id, и это не прихоть формы:
# news_post_create отвергает пустой набор адресатов (news/routes.py), то есть
# черновика, в который можно было бы грузить, не существует, пока автор не
# выбрал «Кому». Значит кадр обязан уметь полежать ничьим — а раз так, нужен
# потолок, иначе цикл в консоли набьёт бакет молча.
MAX_LOOSE_PHOTOS_PER_USER = 30

# Через сколько «ничьи» кадры считаются брошенными и убираются вместе с
# блобами. Сутки, а не час: форму закрывают и возвращаются к ней завтра.
LOOSE_PHOTO_TTL_HOURS = 24

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
    # ── Фотографии объявления ────────────────────────────────────────────────
    # Своя таблица, а не wiki_files: тот файл отдаётся роутом /api/wiki/file/<id>,
    # который стоит за тумблером отдела и QR-подтверждением сессии. Оператор без
    # вики получил бы на каждый кадр 403 — ровно тот случай, ради которого весь
    # пакет news/ и вынесен из wiki/ (см. шапку news/routes.py).
    """
    CREATE TABLE IF NOT EXISTS news_photos (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

        -- NULL — ЗАКОННОЕ состояние, и это главный приём таблицы (см.
        -- MAX_LOOSE_PHOTOS_PER_USER выше). Ловушки logo_file_id из вики здесь
        -- нет: там у файла два возможных читателя и роут отдачи вынужден
        -- гадать, а здесь роута отдачи нет вовсе — ничью строку читает ровно
        -- тот, кто её загрузил и уже получил подписанный адрес ответом.
        news_id       INTEGER REFERENCES news_posts(id) ON DELETE CASCADE,

        -- Где лежат байты. Наружу эти две колонки НЕ отдаются никогда: фронт
        -- получает подписанный адрес, а путь в бакете служил бы подсказкой,
        -- что искать. Собирает ответ news/photos.py: sign_urls.
        bucket        VARCHAR(255) NOT NULL,
        blob_path     TEXT NOT NULL,

        -- Тип ПОСЛЕ конвертера, а не тот, что прислал браузер: to_webp вправе
        -- вернуть исходные байты (ветка «пережали, а стало тяжелее» в
        -- wiki/images.py), и записать всем 'image/webp' значило бы соврать про
        -- содержимое.
        content_type  VARCHAR(100) NOT NULL DEFAULT 'image/webp',
        -- Вес и размеры — ПОСЛЕ пережатия: оригинал в бакет не попадает.
        -- Ширина и высота нужны не для отчётности: без них <img> до загрузки
        -- не имеет размеров, и вся арифметика карусели (какой кадр открыт,
        -- куда прокрутить) считается по нулям — это уже ловили в вики.
        file_size     BIGINT NOT NULL DEFAULT 0,
        width         INTEGER,
        height        INTEGER,
        original_name VARCHAR(255),

        -- Порядок показа = порядок в карусели. Двух кадров, загруженных в одну
        -- секунду, хватает, чтобы сортировка по created_at стала случайной, а
        -- в карусели порядок и есть смысл («шаг 1, шаг 2»).
        sort_order    INTEGER NOT NULL DEFAULT 0,

        uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    # «Кадры новости N по порядку» — оба чтения звучат одинаково. Порядковые
    # колонки внутри индекса снимают сортировку в каждом из ≤20 исполнений
    # подзапроса в /pending.
    """
    CREATE INDEX IF NOT EXISTS idx_news_photos_post
        ON news_photos(news_id, sort_order, id);
    """,
    # Уборка брошенных и потолок «ничьих» на человека. Частичный: привязанных
    # кадров в индексе нет вовсе, он остаётся крошечным.
    """
    CREATE INDEX IF NOT EXISTS idx_news_photos_loose
        ON news_photos(created_at, uploaded_by) WHERE news_id IS NULL;
    """,
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


def photos_ready(cursor):
    """Развёрнута ли таблица кадров.

    Отдельно от schema_is_ready НАМЕРЕННО. Тот отвечает за весь раздел: скажи
    он «нет» — и окно новости пропадёт у всех вошедших в портал. Отсутствие
    одной этой таблицы обязано значить «у новостей нет фотографий», а не
    «раздел разворачивается».

    Нужно это ровно один деплой: код выдачи приезжает раньше, чем DDL успевает
    отработать на старте, и подзапрос по несуществующей news_photos ответил бы
    пятисоткой КАЖДОМУ вошедшему — на самом горячем роуте портала.
    """
    cursor.execute("SELECT to_regclass('public.news_photos') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])
