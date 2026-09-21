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

# Тест в окне новости («Вопросы операторов», задача #321; пределы пересмотрены
# по ТЗ #300, п.4). Постановка #321 говорила про «небольшой тест из 2–3
# вопросов», ТЗ #300 — «количество вопросов зависит от сложности информации»,
# то есть автор решает сам. Потолок остаётся, но десятикратный: он защищает от
# опечатки в цикле, а не от замысла автора. Нижняя граница — один вопрос: тест
# из одного вопроса это законная проверка одного изменения, и требовать второй
# ради круглого числа значило бы просить автора выдумать вопрос.
QUIZ_MIN_QUESTIONS = 1
QUIZ_MAX_QUESTIONS = 10
QUIZ_MIN_OPTIONS = 2
QUIZ_MAX_OPTIONS = 4
QUIZ_MAX_PROMPT_LENGTH = 300
QUIZ_MAX_OPTION_LENGTH = 200

# ПРОХОДНОЙ РЕЗУЛЬТАТ теста, в процентах (ТЗ #300, п.4: «установить проходной
# результат… для критичных изменений проходной результат должен иметь
# возможность устанавливаться на уровне 100%»).
#
# Сто по умолчанию — ровно то, как тест вёл себя до этой задачи: любая ошибка
# не засчитывала попытку. Значит старым новостям колонка ничего не меняет, и
# журнал по ним читается как прежде.
#
# Ноль запрещён: тест, который проходит любой набор ответов, — это не проверка
# знаний, а лишний экран перед кнопкой.
DEFAULT_PASS_SCORE_PERCENT = 100
MIN_PASS_SCORE_PERCENT = 1

# ── ТИП НОВОСТИ (ТЗ #300, п.5) ───────────────────────────────────────────────
#
# Три типа вместо двух несвязанных тумблеров («обязательно к прочтению» и
# «пройти обязательно»). Тип — это ОДИН вопрос автору на языке постановки, а
# тумблеры остаются тем, чем были: способом исполнения. Правило «тип → что
# включено» живёт в одном месте (news/access.py: KIND_RULES), потому что его
# читают и сервер, и форма, и журнал.
NEWS_KINDS = ('info', 'important', 'critical')
DEFAULT_NEWS_KIND = 'important'

# ── ПЛАНИРОВЩИК ПУБЛИКАЦИИ (ТЗ #300, п.8) ────────────────────────────────────
#
# 'now'    — опубликовать сразу всей аудитории (как было до этой задачи);
# 'later'  — отложить до указанного времени;
# 'spread' — растянуть: аудитория делится на волны и активируется порциями.
PUBLISH_MODES = ('now', 'later', 'spread')
DEFAULT_PUBLISH_MODE = 'now'

# Границы растяжки. Периоды и интервалы в ТЗ даны примерами («2, 4, 8 или 24
# часа», «каждые 5, 10, 15 или 30 минут»), а не списком, поэтому проверяем
# диапазон, а не набор: закрытый список пришлось бы править на каждую просьбу
# «а можно три часа».
#
# Пятиминутный минимум у интервала — не вкус: он же держит потолок числа волн
# (сутки / 5 минут = 288). Без него «24 часа по 10 секунд» построило бы 8640
# строк расписания на каждого адресата.
MIN_SPREAD_MINUTES = 5
MAX_SPREAD_MINUTES = 24 * 60
MIN_WAVE_INTERVAL_MINUTES = 5
MAX_WAVES = MAX_SPREAD_MINUTES // MIN_WAVE_INTERVAL_MINUTES

# Ключ тренажёра вики (src/components/wiki/trainers/registry.js). Сценарии
# живут в коде, а не в базе, поэтому сервер знает только форму ключа: латиница,
# цифры и дефис. Длина — как у wiki_trainer_runs.trainer_key.
TRAINER_KEY_MAX_LENGTH = 64

_NOW ="(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

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
    # ── Тест в окне ──────────────────────────────────────────────────────────
    # Есть у новости вопросы — «Прочитал» становится «Подтвердить» и
    # принимается только с верными ответами (news/queries.py: confirm_read).
    """
    CREATE TABLE IF NOT EXISTS news_quiz_questions (
        id            SERIAL PRIMARY KEY,
        news_id       INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
        position      SMALLINT NOT NULL DEFAULT 0,
        prompt        TEXT NOT NULL,
        -- Варианты массивом строк, верный — индексом. Своя таблица вариантов
        -- ради трёх строк означала бы ещё один подзапрос на /pending, а
        -- спрашивают варианты всегда целиком.
        options       JSONB NOT NULL CHECK (jsonb_typeof(options) = 'array'),
        correct_index SMALLINT NOT NULL CHECK (correct_index >= 0),
        created_at    TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_quiz_post ON news_quiz_questions(news_id, position, id);",
    # ── Тренажёр и обязательность прохождения (задача #342) ─────────────────
    # «При создании новости должна быть возможность прикрепить тренажёр или
    # тест… предусмотреть вариант, при котором тест проходить необязательно».
    #
    # pass_required — ОДИН признак на тест и тренажёр: постановка говорит об
    # одной настройке обязательности, а два тумблера рядом спрашивали бы автора
    # о различии, которого он не задумывал. TRUE по умолчанию: тесты, выпущенные
    # до этой задачи, были обязательными, и журнал по ним обязан читаться как
    # прежде.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS pass_required BOOLEAN NOT NULL DEFAULT TRUE;",
    # Ключ тренажёра, без внешнего ключа: сценарии в коде (как у wiki_trainer_runs).
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS trainer_key VARCHAR(%(trainer_key_max)s);",
    # Кто прошёл тест и тренажёр — в той же строке, что «открыл» и «подтвердил»:
    # журнал отвечает на все четыре вопроса одним чтением. Отдельно от
    # confirmed_at, потому что необязательный тест проходят и после
    # подтверждения, и не проходят вовсе.
    "ALTER TABLE news_reads ADD COLUMN IF NOT EXISTS quiz_passed_at TIMESTAMP;",
    "ALTER TABLE news_reads ADD COLUMN IF NOT EXISTS trainer_passed_at TIMESTAMP;",
    # ── Пространство новости (решение владельца 18.09.2026) ──────────────────
    # «Чтобы по пространствам новости Таксопарков и Тез не смешивались».
    # До этого новость не принадлежала никакой вике вовсе: объявление для
    # Тез КЦ стояло в списке Таксопарков и наоборот, а «все операторы» от
    # супер-админа доставали до обеих компаний разом.
    #
    # Пространство здесь — ВТОРАЯ граница адресата, рядом с потолком должности
    # и границей отдела: колонка отвечает не «где показать в списке», а «чьей
    # компании эта новость» (news/access.py: SPACE_MATCH_TEMPLATE).
    #
    # БЕЗ ВНЕШНЕГО КЛЮЧА на wiki_spaces, намеренно — по той же причине, по
    # которой у вики нет ключа на news_posts (wiki/schema.py, kb_news_id):
    # пакеты разворачиваются раздельно, и ссылка связала бы судьбу раздела с
    # чужой миграцией. Сорвись схема вики — новости обязаны работать как
    # вчера, только без границы пространства.
    #
    # NULL — законное значение: так лежат новости, выпущенные до этой колонки
    # (их разбирает backfill_space_ids) и пришедшие мимо вкладки. Ничья
    # новость видна из любого пространства: спрятать её значило бы оставить
    # обязательное окно без единого человека, который вправе его снять.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS space_id INTEGER;",
    # Список редактора всегда спрашивает «новости ЭТОГО пространства», и он же
    # ищет ничьи при бэкфилле.
    "CREATE INDEX IF NOT EXISTS idx_news_posts_space ON news_posts(space_id);",
    # ── Куда отправлено объявление (решение владельца 18.09.2026) ────────────
    # 'icore' — окно портала, 'oktell' — окно поверх клиента АТС, которое рисует
    # «Ограничитель Перезвона» (news/access.py: CHANNELS). Умолчание — портал,
    # и им же становятся все новости, выпущенные до этой колонки: они и
    # показывались в портале.
    #
    # БЕЗ CHECK намеренно, в отличие от status: там ограничение объявлено внутри
    # CREATE TABLE, а добавить его к существующей таблице идемпотентно нечем —
    # ALTER TABLE ADD CONSTRAINT без IF NOT EXISTS упал бы на втором старте и
    # утащил бы за собой весь SAVEPOINT схемы. Значение приводит
    # access.normalize_channel: незнакомое становится порталом, а не отказом.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS "
    "channel VARCHAR(16) NOT NULL DEFAULT 'icore';",
    # ── ТИП НОВОСТИ И ПРОХОДНОЙ БАЛЛ (ТЗ #300, п.4–5) ───────────────────────
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS kind VARCHAR(16);",
    # Тип у прежних новостей ВЫВОДИТСЯ из того, как они себя вели, а не
    # ставится умолчанием колонки. Умолчание сказало бы, что все объявления за
    # год были «важными», — в том числе те, что закрывались крестиком. Тип
    # читают журнал и список, и соврать в нём задним числом нельзя.
    #
    # Бэкфилл идёт на КАЖДОМ старте и это намеренно: трогает он только строки
    # без типа, а появиться они могут и после деплоя — например от вкладки со
    # старым бандлом, которая про колонку ещё не знает (так же устроен
    # backfill_space_ids). CHECK на значения не ставим: тип приводит
    # access.normalize_kind, и незнакомое становится «важной», а не отказом.
    """
    UPDATE news_posts
       SET kind = CASE
               WHEN NOT is_mandatory THEN 'info'
               WHEN pass_required AND EXISTS (SELECT 1 FROM news_quiz_questions z
                                               WHERE z.news_id = news_posts.id)
                    THEN 'critical'
               ELSE 'important'
           END
     WHERE kind IS NULL;
    """,
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS pass_score_percent SMALLINT "
    "NOT NULL DEFAULT %(default_score)s "
    "CHECK (pass_score_percent BETWEEN %(min_score)s AND 100);",
    # ── ПЛАНИРОВЩИК ПУБЛИКАЦИИ (ТЗ #300, п.8) ──────────────────────────────
    #
    # СВОЕГО СТАТУСА У ЗАПЛАНИРОВАННОЙ НОВОСТИ НЕТ, и это решение. Статус лежит
    # под CHECK'ом, объявленным внутри CREATE TABLE, а расширить его
    # идемпотентно нечем: ALTER TABLE ADD CONSTRAINT без IF NOT EXISTS упал бы
    # на втором старте и утащил бы за собой весь SAVEPOINT схемы (ровно та же
    # причина, по которой без CHECK живёт channel).
    #
    # «Запланирована» — это ЧЕРНОВИК С ВЗВЕДЁННЫМ scheduled_at, и выводится она
    # одинаково в списке, в карточке и в кроне (access.post_state). Заодно это
    # снимает вопрос «что можно делать с запланированной»: черновик правится и
    # удаляется, людям он ещё не показан, и права проверять заново не нужно.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS "
    "publish_mode VARCHAR(16) NOT NULL DEFAULT '%(default_mode)s';",
    # Когда запускать. NULL — запуск не взведён: у режима 'now' поле пустое
    # всегда, и крон о такой новости не знает вовсе.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP;",
    # ВЗВЕДЁН ЛИ ЗАПУСК. Отдельно от scheduled_at, и это не лишний флаг.
    #
    # Время запуска автор выбирает В ФОРМЕ, и оно обязано пережить кнопку «В
    # черновики»: иначе, вернувшись к объявлению завтра, он нашёл бы пустое
    # поле и решил, что запуск сбросился. Но черновик — это «ещё не отправлял»,
    # и выпустить его по этому же времени нельзя: автор нажал «в черновики»
    # ровно затем, чтобы объявление никуда не ушло.
    #
    # Поэтому взводит запуск ТОЛЬКО нажатие «Опубликовать» (queries.schedule_post),
    # и только про взведённые знает крон. Снимается признак сам, когда автор
    # переключает запуск на «Сразу» (update_post) и когда новость наконец
    # вышла (publish_scheduled).
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS "
    "scheduled_armed BOOLEAN NOT NULL DEFAULT FALSE;",
    # Период растяжки и интервал между волнами, в минутах. Без CHECK — их
    # держит access.spread_refusal, а колонка обязана уметь принять и старое
    # значение, если пределы когда-нибудь подвинут.
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS spread_minutes INTEGER;",
    "ALTER TABLE news_posts ADD COLUMN IF NOT EXISTS wave_interval_minutes INTEGER;",
    # Крон спрашивает «кого пора выпускать» каждую минуту. Частичный: новостей
    # со взведённым запуском единицы, и индекс остаётся крошечным.
    "CREATE INDEX IF NOT EXISTS idx_news_posts_scheduled ON news_posts(scheduled_at) "
    "WHERE status = 'draft' AND scheduled_armed;",
    # ── ВОЛНЫ: кому и когда новость становится видна ────────────────────────
    #
    # Расписание СНИМКОМ, посчитанным в момент выпуска, а не правилом «каждому
    # n-й минуте по остатку от id». Правило считало бы волну заново на каждый
    # запрос, и человек, сменивший отдел, переезжал бы из волны в волну — а ТЗ
    # (п.8.3) требует обратного: «сотрудник не должен попадать более чем в одну
    # волну одной и той же публикации». Здесь это не оговорка в комментарии, а
    # первичный ключ.
    #
    # Кого в снимке НЕТ — тому новость видна СРАЗУ (queries: _wave_gate). Так
    # попадает вышедший на работу после выпуска, переведённый в адресуемый
    # отдел и дописанный в адресаты правкой. Обратное правило («нет строки —
    # не показываем») означало бы объявление, которое молча не доехало, — а это
    # ровно тот отказ, который в этом разделе ловили уже трижды.
    """
    CREATE TABLE IF NOT EXISTS news_waves (
        news_id      INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        wave_no      SMALLINT NOT NULL,
        -- Когда волна ДОЛЖНА включиться. По нему и только по нему решает
        -- выдача: крон может опоздать или не запуститься вовсе, а объявление
        -- обязано открыться вовремя.
        planned_at   TIMESTAMP NOT NULL,
        -- Когда включилась ФАКТИЧЕСКИ (ставит крон). Нужно отчётности
        -- (ТЗ п.8.5) и разбору «почему человек увидел позже плана»; на показ
        -- не влияет ничем.
        activated_at TIMESTAMP,
        PRIMARY KEY (news_id, user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_waves_due ON news_waves(planned_at) "
    "WHERE activated_at IS NULL;",
    # ── ПОПЫТКИ ТЕСТА (ТЗ #300, п.4.2) ──────────────────────────────────────
    #
    # «Количество попыток не ограничивать, но обязательно фиксировать в
    # системе». До этой таблицы в журнале стоял только ИТОГ (news_reads
    # .quiz_passed_at): сдал или нет. По нему нельзя ответить ни «сколько раз
    # пробовал», ни «на чём спотыкаются» — а это два из трёх вопросов, ради
    # которых тест и заводят.
    #
    # Строка на КАЖДУЮ попытку, включая удачную: «сдал с третьего раза» — это
    # ровно то, что отличает понятную инструкцию от непонятной.
    """
    CREATE TABLE IF NOT EXISTS news_quiz_attempts (
        id         BIGSERIAL PRIMARY KEY,
        news_id    INTEGER NOT NULL REFERENCES news_posts(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        -- Порядковый номер попытки ЭТОГО человека по ЭТОЙ новости. Считается
        -- при вставке одним запросом: отдельный SELECT ради счётчика открыл бы
        -- окно между «посчитали» и «записали».
        attempt_no SMALLINT NOT NULL,
        correct    SMALLINT NOT NULL,
        total      SMALLINT NOT NULL,
        -- Порог, который действовал В МОМЕНТ попытки. Снимком, а не ссылкой на
        -- news_posts.pass_score_percent: у выпущенной новости он не меняется,
        -- но у снятой и выпущенной заново — может, и тогда старые попытки
        -- читались бы по новому правилу.
        needed     SMALLINT NOT NULL,
        passed     BOOLEAN NOT NULL,
        -- Что человек выбрал: {id вопроса: индекс варианта}. Нужно аналитике
        -- (п.12): «ошиблись в третьем» говорит ГДЕ, а выбранный вариант —
        -- ПОЧЕМУ, если восемь человек из десяти выбрали один и тот же неверный.
        answers    JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- Вопросы с ошибкой, посчитанные ОДИН раз при записи. Иначе аналитика
        -- сверяла бы ответы с верными на каждой строке журнала.
        wrong_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMP NOT NULL DEFAULT %(now)s
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_quiz_attempts "
    "ON news_quiz_attempts(news_id, user_id, attempt_no);",
]

# Колонки задачи #342 — по ним pass_ready отвечает, можно ли их читать.
PASS_COLUMNS = (
    ('news_posts', 'pass_required'),
    ('news_posts', 'trainer_key'),
    ('news_reads', 'quiz_passed_at'),
    ('news_reads', 'trainer_passed_at'),
)

# Колонки ТЗ #300 (тип, проходной балл, планировщик) — по ним отвечает
# plan_ready. ОДНА защёлка на все пять и на таблицу волн, а не пять отдельных:
# приезжают они одним деплоем, и порознь их состояния не бывает. Разложить их
# по разным защёлкам значило бы завести пять веток поведения, из которых
# четыре не случатся никогда.
PLAN_COLUMNS = (
    ('news_posts', 'kind'),
    ('news_posts', 'pass_score_percent'),
    ('news_posts', 'publish_mode'),
    ('news_posts', 'scheduled_at'),
    ('news_posts', 'scheduled_armed'),
    ('news_posts', 'spread_minutes'),
    ('news_posts', 'wave_interval_minutes'),
)


def init_news_schema(cursor):
    """Разворачивает схему раздела. Идемпотентно."""
    for statement in _STATEMENTS:
        cursor.execute(statement.replace('%(now)s', _NOW)
                       .replace('%(default_delay)s', str(DEFAULT_CONFIRM_DELAY_SECONDS))
                       .replace('%(max_delay)s', str(MAX_CONFIRM_DELAY_SECONDS))
                       .replace('%(trainer_key_max)s', str(TRAINER_KEY_MAX_LENGTH))
                       .replace('%(default_score)s', str(DEFAULT_PASS_SCORE_PERCENT))
                       .replace('%(min_score)s', str(MIN_PASS_SCORE_PERCENT))
                       .replace('%(default_mode)s', DEFAULT_PUBLISH_MODE))


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


def quiz_ready(cursor):
    """Развёрнута ли таблица теста. Отдельно — по той же причине, что кадры.

    Нет таблицы — у новостей просто нет тестов: /pending не должен отвечать
    пятисоткой каждому вошедшему на тот единственный деплой, когда код уже
    приехал, а DDL ещё не отработал.
    """
    cursor.execute("SELECT to_regclass('public.news_quiz_questions') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])


def space_ready(cursor):
    """Можно ли считать границу пространства. Отдельно — как кадры и тест.

    Спрашиваем ДВЕ вещи разом, потому что граница держится на них обеих:
      * колонка news_posts.space_id — своя, приезжает этим деплоем;
      * таблица wiki_space_departments — ЧУЖАЯ, из пакета вики. Ею
        пространство и связано с отделами, а другого способа ответить, «чьи
        это люди», нет.

    Сказать «нет» здесь означает ровно «границы пространства пока нет»: раздел
    работает как до задачи — новость видна всем своим адресатам, список
    редактора показывает всё. Уронить окно у всего портала из-за чужой
    несостоявшейся миграции нельзя — ради этого пакет news/ и вынесен из wiki/.
    """
    cursor.execute(
        """
        SELECT to_regclass('public.wiki_space_departments') IS NOT NULL
           AND EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'news_posts'
                          AND column_name = 'space_id')
        """
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def backfill_space_ids(cursor):
    """Проставляет пространство новостям, выпущенным до появления колонки.

    Правило одно — то же, что у создания новости: пространство берётся по
    ОТДЕЛУ АВТОРА на момент публикации (news_posts.author_department_id), а
    отдел с пространством связывает wiki_space_departments. Другого следа, из
    какой вики выпущено старое объявление, в базе нет, и он честный: список
    редактора и так стоит на отделе автора.

    Идемпотентен и повторяется на каждом старте намеренно: трогает только
    ничьи строки, а появиться они могут и после деплоя — например от вкладки
    со старым бандлом, которая ещё не знает про space_id. Так ничья новость
    сама находит своё пространство к следующему рестарту.

    Отделу, не выданному ни одному пространству, соответствия нет — такая
    новость остаётся ничьей и видна из любого пространства (см. шапку колонки).
    """
    if not space_ready(cursor):
        return 0
    cursor.execute(
        """
        UPDATE news_posts p
           SET space_id = (
                   SELECT sd.space_id
                     FROM wiki_space_departments sd
                     JOIN wiki_spaces sp ON sp.id = sd.space_id AND sp.status = 'active'
                    WHERE sd.department_id = p.author_department_id
                    ORDER BY sp.position, sp.id
                    LIMIT 1
               )
         WHERE p.space_id IS NULL
           AND p.author_department_id IS NOT NULL
        """
    )
    return cursor.rowcount or 0


def channel_ready(cursor):
    """Развёрнута ли колонка канала. Отдельно — как кадры, тест и пространство.

    Нет колонки — у новостей нет каналов: объявление уходит в портал, как до
    этой задачи, а не встречает каждого вошедшего пятисоткой.
    """
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'news_posts'
           AND column_name = 'channel'
        """)
    row = cursor.fetchone()
    return bool(row and int(row[0]) == 1)


def pass_ready(cursor):
    """Развёрнуты ли колонки тренажёра и обязательности прохождения (#342).

    Отдельно — по той же причине, что кадры и тест. Схема раздела применяется
    одним SAVEPOINT'ом: сорвись одна инструкция, откатятся и новые колонки, а
    таблицы прошлых деплоев останутся. Тогда выдача, читающая p.pass_required,
    ответила бы пятисоткой каждому вошедшему в портал. Без колонок у новостей
    просто нет тренажёров, а тест, как и раньше, обязателен.
    """
    cursor.execute(
        """
        SELECT COUNT(*) FROM information_schema.columns
         WHERE table_schema = 'public'
           AND (table_name, column_name) IN (%s)
        """ % ', '.join(['(%s, %s)'] * len(PASS_COLUMNS)),
        [value for pair in PASS_COLUMNS for value in pair],
    )
    row = cursor.fetchone()
    return bool(row and int(row[0]) == len(PASS_COLUMNS))


def attempts_ready(cursor):
    """Развёрнута ли таблица попыток (ТЗ #300, п.4.2).

    Отдельно — по той же причине, что кадры, тест и планировщик. Здесь это
    важнее прочего: запись попытки стоит на ПОДТВЕРЖДЕНИИ новости, и ссылка на
    несуществующую таблицу уронила бы кнопку «Прочитал» у всех, кому пришло
    обязательное объявление, — то есть заперла бы смену.

    Нет таблицы — попытки просто не считаются: тест работает как до задачи.
    """
    cursor.execute("SELECT to_regclass('public.news_quiz_attempts') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])


def plan_ready(cursor):
    """Развёрнуты ли тип, проходной балл, планировщик и волны (ТЗ #300).

    Спрашиваем колонки И таблицу волн разом: граница волны без таблицы не
    считается, а тип без колонки не читается — порознь эти половины бесполезны.

    Ответ «нет» означает ровно «раздел работает как до этой задачи»: новость
    выпускается сразу и всей аудитории, тип выводится из обязательности, тест
    засчитывается только при всех верных ответах. Уронить окно у всего портала
    из-за не доехавшего DDL нельзя — это самый горячий запрос раздела.
    """
    cursor.execute(
        """
        SELECT to_regclass('public.news_waves') IS NOT NULL
           AND (SELECT COUNT(*) FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND (table_name, column_name) IN (%s)) = %%s
        """ % ', '.join(['(%s, %s)'] * len(PLAN_COLUMNS)),
        [value for pair in PLAN_COLUMNS for value in pair] + [len(PLAN_COLUMNS)],
    )
    row = cursor.fetchone()
    return bool(row and row[0])
