"""Фасад базы данных портала.

Класс Database собран из миксинов db/<раздел>.py — по разделам, как и пакеты бэкенда
(вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026). Тела методов перенесены без изменений. Всё модульное — пул, константы,
помощники — живёт в db/core.py и реэкспортируется отсюда: код и тесты по-прежнему пишут
`from database import db, IT_TICKET_CATALOG`. Текст «как одним файлом» для тестов собирает
tests/source_cache.py."""
import logging

from db.core import (  # noqa: F401 — реэкспорт модульных имён
    contextmanager,
    pbkdf2_sha256,
    datetime,
    timedelta,
    date,
    dt_time,
    dt_timezone,
    ZoneInfo,
    BytesIO,
    ThreadPoolExecutor,
    ThreadedConnectionPool,
    execute_values,
    execute_batch,
    Json,
    Workbook,
    PatternFill,
    Font,
    Border,
    Side,
    Alignment,
    CellRichText,
    TextBlock,
    InlineFont,
    Table,
    TableStyleInfo,
    get_column_letter,
    Fill,
    defaultdict,
    List,
    Dict,
    Any,
    Tuple,
    Optional,
    SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES,
    task_production_queries,
    task_production_schema,
    _UNSET,
    _UNSET_GROUP,
    _env_int,
    _env_float,
    _is_schema_lock_conflict,
    get_gcs_client,
    MIN_CONN,
    MAX_CONN,
    POOL_ACQUIRE_TIMEOUT_SECONDS,
    POOL_WAIT_WARN_SECONDS,
    POOL,
    POOL_LOCK,
    POOL_SEMAPHORE,
    STATUS_IMPORT_INSERT_PAGE_SIZE,
    STATUS_EVENTS_RETENTION_DAYS,
    RECRUITING_RUNS_KEEP_DAYS,
    SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL,
    BELL_EVENTS_NOTIFY_CHANNEL,
    SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS,
    SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS,
    SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE,
    SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE_LOCK,
    SHIFT_AUCTION_PARTICIPANT_CACHE,
    SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK,
    _shift_auction_snapshot_cache_slot,
    _shift_auction_participant_cache_slot,
    SHIFT_AUCTION_DIRECTION_NAME,
    SHIFT_AUCTION_DEPARTMENT_CODE,
    SHIFT_AUCTION_ACTIVE_OPERATOR_STATUS,
    SHIFT_AUCTION_MODE_LINE,
    SHIFT_AUCTION_MODE_CHAT,
    SHIFT_AUCTION_MODES,
    SHIFT_AUCTION_CHAT_DIRECTION_PATTERN,
    SHIFT_AUCTION_SETTINGS_ROW_ID,
    normalize_shift_auction_mode,
    shift_auction_settings_row_id,
    shift_auction_direction_scope_sql,
    shift_auction_mode_matches_direction,
    FRONT_OFFICE_DEPARTMENT_CODE,
    LIVE_STATUS_TRACK_DEPARTMENT_CODES,
    PROXY_STATUS_VALUES,
    _normalize_shift_auction_scope_value,
    _is_shift_auction_operational_participant_row,
    _coerce_four_you_annotations,
    PROXY_STATUS_LABELS,
    normalize_proxy_status_value,
    SIP_IDENTIFIER_RE,
    SIP_PASSWORD_PLACEHOLDER_RE,
    build_sip_password,
    SIP_PROVIDERS,
    BINOTEL_CABINET_URL_DEFAULT,
    SIP_PROVIDER_DEFAULT,
    normalize_sip_provider,
    SIP_FLAG_FALSE,
    parse_sip_flag,
    SIP_AUTO_ANSWER_DELAY_DEFAULT,
    SIP_AUTO_ANSWER_DELAY_MAX,
    parse_sip_delay,
    SIP_INHERIT_TOKEN,
    is_sip_inherit,
    parse_sip_flag_tri,
    parse_sip_delay_tri,
    resolve_sip_auto_answer,
    resolve_sip_auto_answer_delay,
    SIP_RING_TIMEOUT_RINGS_DEFAULT,
    SIP_RING_SECONDS_PER_RING,
    SIP_RING_TIMEOUT_RINGS_MAX,
    SIP_RING_TIMEOUT_LEAD_SEC,
    parse_sip_ring_timeout_rings,
    resolve_sip_ring_timeout_rings,
    sip_ring_timeout_seconds,
    normalize_sip_identifier,
    _invalidate_shift_auction_runtime_caches,
    ROLE_ALIASES,
    ROLE_HIERARCHY,
    BACK_OFFICE_EMPLOYEE_ROLES,
    TASK_PERSONAL_SCOPE_ROLES,
    TASK_RECIPIENT_ROLES,
    _TASK_RECIPIENT_ROLES_SQL,
    TASK_TAG_CODES,
    CALCULATION_MODEL_OPERATOR,
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_TEZ_LINE,
    CALCULATION_MODEL_TEZ_OP,
    CALCULATION_MODEL_TEZ_CODES,
    CALCULATION_MODEL_OP_VERIFICATOR,
    CALCULATION_MODEL_OP_YANDEX_REG,
    CALCULATION_MODEL_OP_OSNOVA,
    CALCULATION_MODEL_OP_POTOK,
    CALCULATION_MODEL_OP_SALES_CODES,
    CALCULATION_MODEL_ALLOWED,
    CALCULATION_MODEL_DESCRIPTIONS,
    normalize_role_value,
    role_has_min,
    role_is_any,
    _normalize_direction_name_key,
    normalize_calculation_model_code,
    get_calculation_model_catalog,
    _calc_metric,
    _CALC_METRICS_HEAD,
    _CALC_METRICS_TAIL,
    _CALC_METRICS_OP_SALES,
    CALCULATION_MODEL_METRICS,
    get_calculation_model_metrics,
    _normalize_phone,
    _CALL_END_PARTIES,
    _normalize_call_end_party,
    _parse_datetime_raw,
    _time_to_minutes,
    _minutes_to_time,
    WORK_SHIFT_TYPE_REGULAR,
    WORK_SHIFT_TYPE_OFFICE_PRACTICE,
    WORK_SHIFT_TYPE_PHONE_SHIFT,
    WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT,
    WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
    WORK_SHIFT_TYPE_ALLOWED,
    WORK_SHIFT_TYPE_PRIORITY,
    SHIFT_BREAK_PLANNING_BUFFER_MINUTES,
    SHIFT_BREAK_MIN_EDGE_MARGIN_MINUTES,
    SHIFT_BREAK_MIN_GAP_MINUTES,
    WORK_SHIFT_CHANGE_ACTIONS,
    WORK_SHIFT_CHANGE_SOURCES,
    SHIFT_BREAK_MAX_CONCURRENT,
    SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES,
    SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES,
    SHIFT_BREAK_PAIR_OVERLAP_SHARE,
    SHIFT_BREAK_WEIGHT_CONCURRENCY,
    SHIFT_BREAK_WEIGHT_PAIR_EXCESS,
    SHIFT_BREAK_WEIGHT_CROSS_GAP,
    SHIFT_BREAK_WEIGHT_OVERLAP,
    SHIFT_BREAK_WEIGHT_EVENNESS,
    SHIFT_BREAK_WEIGHT_KEEP_PLACE,
    SHIFT_BREAK_EVEN_WINDOW_SHARE,
    SHIFT_BREAK_LAYOUT_PASSES,
    _normalize_work_shift_type_value,
    _work_shift_type_priority,
    _merge_shifts_for_date,
    SCHEDULE_SPECIAL_STATUS_META,
    SCHEDULE_DISMISSAL_REASONS,
    USER_STATUS_ALLOWED_VALUES,
    SCHEDULE_STATUS_TO_USER_STATUS,
    SCHEDULE_AUTO_AGGREGATION_START_DATE,
    SCHEDULE_AUTO_FLAG_PENDING,
    SCHEDULE_AUTO_FLAG_CONFIRMED,
    SCHEDULE_AUTO_FLAG_REJECTED,
    SCHEDULE_AUTO_FLAG_ALLOWED,
    SCHEDULE_AUTO_WORK_STATUS_KEYS,
    SCHEDULE_AUTO_TALK_STATUS_KEYS,
    SCHEDULE_AUTO_BREAK_STATUS_KEYS,
    SCHEDULE_AUTO_NO_PHONE_STATUS_KEY,
    SCHEDULE_AUTO_TRAINING_STATUS_KEY,
    CHAT_MANAGER_WORK_STATUS_KEYS,
    CHAT_MANAGER_BREAK_STATUS_KEYS,
    CHAT_MANAGER_NON_WORK_STATUS_KEYS,
    CHAT_MANAGER_TRAINING_STATUS_KEYS,
    CHAT_MANAGER_LOGIN_STATUS_KEYS,
    CHAT_MANAGER_LOGOUT_STATUS_KEYS,
    CHAT_MANAGER_ACTION_STATUS_KEYS,
    TEZ_WORK_STATUS_KEYS,
    TEZ_BREAK_STATUS_KEYS,
    TEZ_IGNORED_STATUS_KEYS,
    SCHEDULE_STATUS_KEY_LABELS,
    SCHEDULE_AUTO_FINE_RATE_PER_MINUTE,
    TECHNICAL_ISSUE_REASONS,
    TECHNICAL_ISSUE_REASONS_SET,
    TECHNICAL_ISSUE_REASON_COMMENT_RULES,
    technical_issue_comment_rule,
    technical_issue_comment_rules_payload,
    technical_issue_comment_required_error,
    TECHNICAL_ISSUE_WORKPLACE_MAX,
    IT_TICKET_CATALOG,
    IT_TICKET_DEFAULT_PROFILE,
    resolve_it_ticket_profile,
    resolve_it_ticket_profile_strict,
    get_pool,
    MEMBERSHIP_MONTH_OVERLAP_SQL,
    MEMBERSHIP_DAY_DISTANCE_SQL,
    LowRatingAttributionConflict,
)
from db.core import CoreMixin
from db.schema import SchemaMixin
from db.shift_auction import ShiftAuctionMixin
from db.shift_swaps import ShiftSwapsMixin
from db.breaks import BreaksMixin
from db.shifts import ShiftsMixin
from db.schedules import SchedulesMixin
from db.group_late import GroupLateMixin
from db.tasks import TasksMixin
from db.tez import TezMixin
from db.operator_status import OperatorStatusMixin
from db.supervisors import SupervisorsMixin
from db.groups import GroupsMixin
from db.surveys import SurveysMixin
from db.chat_ratings import ChatRatingsMixin
from db.wazzup import WazzupMixin
from db.chatapp import ChatappMixin
from db.szov_wallboard import SzovWallboardMixin
from db.calls import CallsMixin
from db.it_tickets import ItTicketsMixin
from db.trainings import TrainingsMixin
from db.events import EventsMixin
from db.sip import SipMixin
from db.reg_contest import RegContestMixin
from db.work_hours import WorkHoursMixin
from db.resource_fte import ResourceFteMixin
from db.lms import LmsMixin
from db.recruiting import RecruitingMixin
from db.front_office import FrontOfficeMixin
from db.reports import ReportsMixin
from db.departments import DepartmentsMixin
from db.users import UsersMixin
from db.operators import OperatorsMixin
from db.salary import SalaryMixin
from db.dial_list import DialListMixin
from db.amo_leads import AmoLeadsMixin
from db.driver_chats import DriverChatsMixin
from db.telephony import TelephonyMixin
from db.ai_feedback import AiFeedbackMixin
from db.baiga import BaigaMixin


class Database(CoreMixin, SchemaMixin, ShiftAuctionMixin, ShiftSwapsMixin, BreaksMixin, ShiftsMixin, SchedulesMixin, GroupLateMixin, TasksMixin, TezMixin, OperatorStatusMixin, SupervisorsMixin, GroupsMixin, SurveysMixin, ChatRatingsMixin, WazzupMixin, ChatappMixin, SzovWallboardMixin, CallsMixin, ItTicketsMixin, TrainingsMixin, EventsMixin, SipMixin, RegContestMixin, WorkHoursMixin, ResourceFteMixin, LmsMixin, RecruitingMixin, FrontOfficeMixin, ReportsMixin, DepartmentsMixin, UsersMixin, OperatorsMixin, SalaryMixin, DialListMixin, AmoLeadsMixin, DriverChatsMixin, TelephonyMixin, AiFeedbackMixin, BaigaMixin):
    SURVEY_OTHER_ANSWER_MAX_LENGTH = 500
    SCHEMA_INIT_LOCK_KEY = 915904137
    SCHEMA_INIT_LOCK_TIMEOUT_SEC = 120
    SCHEMA_INIT_RETRY_ATTEMPTS = 6
    SCHEMA_DDL_LOCK_TIMEOUT_MS = 2000
    SHIFT_AUCTION_OPERATOR_LOCK_NAMESPACE = 915904138
    SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE = 915904139
    TEZ_LEAD_PERIOD_LOCK_NAMESPACE = 915904140
    STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE = 915904142

    def __init__(self):
        self._init_db_with_retry()

    # ── Автоотчёт по проведённым тренингам (задача #261) ────────────────────
    #
    # Кому вообще можно подписаться: админ, супер-админ и глава активного
    # отдела. Условие выписано один раз и переиспользуется тремя методами —
    # разойдясь, они дали бы «настройка сохранилась, а отчёт не приходит».
    TRAINING_REPORT_SUBSCRIBER_SQL = """
        LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
        OR EXISTS (
            SELECT 1 FROM departments d
             WHERE d.head_user_id = u.id
               AND COALESCE(d.is_active, TRUE) = TRUE
        )
    """

    # Периодичность -> колонка. Ключи совпадают с trainings.reports.PERIODS;
    # неизвестный ключ до SQL не доходит.
    TRAINING_REPORT_COLUMNS = {
        'daily': 'training_report_daily_enabled',
        'weekly': 'training_report_weekly_enabled',
        'monthly': 'training_report_monthly_enabled',
    }

    # Вид смены в сохранённом графике. Телефонная смена ставится руками поверх
    # сгенерированного графика, не участвует в расчёте ресурсов и на всех экранах
    # (планировщик, аукцион) рисуется зелёным.
    RESOURCE_SHIFT_KIND_PHONE = 'phone'

    # Части смен, взятые ПРЯМО В ХОДЕ аукциона, лежат в shift_auction_historical_claims
    # со стадией 'auction' (у пост-аукционного добора стадия 'post_auction'). Лот при
    # этом остаётся 'available', пока не разобран целиком, поэтому норму нельзя считать
    # по одним только claimed-лотам — иначе чат-оператор набрал бы сверх ставки.
    SHIFT_AUCTION_CLAIM_STAGE_AUCTION = 'auction'
    SHIFT_AUCTION_CLAIM_STAGE_POST = 'post_auction'

    # Дата, в которой взятый КУСОК смены реально начинается. Кусок хранится двумя
    # временами без даты, а дату ему даёт исходная смена — и у ночи 20:00–08:00
    # хвост 00:00–08:00 принадлежит уже СЛЕДУЮЩЕМУ дню. Без этого сдвига ночь
    # уезжает в график на сутки назад: 16.09.2026 так вышло две ночи в среду и
    # пустая ночь на четверг. Алиасы sh/hc обязаны совпадать с запросом.
    SHIFT_AUCTION_CLAIM_DATE_SQL = (
        "(sh.shift_date + CASE WHEN sh.end_time <= sh.start_time "
        "AND hc.claimed_start_time < sh.start_time THEN 1 ELSE 0 END)"
    )

    SHIFT_AUCTION_TIME_GROUP_LIMIT = 10

    # Minimal length (minutes) of a leftover segment that is worth re-offering to
    # other operators. Matches the frontend's >=15-min availability filter.
    POST_AUCTION_MIN_REMAINDER_MINUTES = 15

    # Canonical shift length (minutes) per auction rate. When a supervisor/admin
    # adds an extra shift via the "+" button, the start time is free but the
    # length is fixed by the rate, so the segment "соответствует ставке". The
    # night shift (20:00→08:00) is validated separately as a fixed 12h window.
    SHIFT_AUCTION_RATE_SHIFT_MINUTES = {1.0: 540, 0.75: 390, 0.5: 240}
    SHIFT_AUCTION_NIGHT_SHIFT_MINUTES = 720
    # How far above their norm a "свой график" group may go, in minutes (10 hours).
    SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES = 600

    # Ночная смена — РОВНО 20:00–08:00 (решение владельца 29.08.2026: «смена 20*08»).
    # Вечерние 19:30–02:00, 22:00–02:00 и прочие «за полночь» ночными не считаются:
    # их владелец ночью не называл, и запрет на них закрыл бы половину графика.
    SHIFT_AUCTION_NIGHT_START_HHMM = '20:00'
    SHIFT_AUCTION_NIGHT_END_HHMM = '08:00'

    # Порядок возглавляемых отделов. Глава не обязана числиться в отделе, который
    # возглавляет, и отделов у неё может быть несколько (решение владельца
    # 08.10.2026). «Первым» — а на него опирается всё, что работает с одним отделом
    # главы: отдел нового сотрудника по умолчанию, её группы, учёт часов — идёт тот,
    # в котором она числится сама, и только потом остальные по названию. Иначе
    # назначение главой ещё одного отдела молча переключало бы её основной отдел,
    # стоило новому названию оказаться раньше по алфавиту.
    # Параметр — id пользователя (второй раз, после head_user_id). У человека без
    # отдела сравнение даёт NULL у всех строк — остаётся порядок по названию.
    _HEADED_DEPARTMENTS_ORDER_SQL = (
        "(id = (SELECT u.department_id FROM users u WHERE u.id = %s)) DESC NULLS LAST, name, id"
    )

    # Кого табло Тез КЦ не показывает. Только увольнение: «Б/С» (bs) — это отпуск без
    # сохранения, человек остаётся сотрудником и остаётся в Binotel. Отфильтруй мы его —
    # состав ОП на экране разошёлся бы с составом в кабинете (6 против 7 на 08.09.2026),
    # а счётчики кабинета всё равно считают его. dismissal — псевдоним fired.
    TEZ_WALLBOARD_EXCLUDED_STATUSES = ('fired', 'dismissal')

    # Текст ошибки уходит в тост как есть — поэтому по-русски.
    LOW_RATING_ATTRIBUTION_REQUIRED = "Отметьте хотя бы одного менеджера, которому засчитать оценку"
    LOW_RATING_ATTRIBUTION_CHANGED = (
        "Пока карточка была открыта, другой проверяющий изменил, кому засчитана оценка. "
        "Отметки обновлены — проверьте их и сохраните ещё раз"
    )

    # Ключ идентичности оценки Chat2Desk, тот же, что считает
    # _chat2desk_rating_source_key в bot_schedule2.py. Формула читаемая, а не
    # хеш, именно чтобы миграция и синк не могли разъехаться.
    _C2D_RATING_KEY_SQL = (
        "'c2d-rating:' || COALESCE(lr.raw_payload->>'valuation_request_id', '')"
        " || ':' || COALESCE(lr.raw_payload->>'request_id', '')"
        " || ':' || COALESCE(lr.raw_payload->>'rating_scale_id', '')"
        " || ':' || COALESCE(lr.raw_payload->>'operator_id', '')"
    )
    _C2D_RATING_KEY_SCOPE_SQL = (
        "lr.source = 'chat2desk_rating'"
        " AND (COALESCE(lr.raw_payload->>'valuation_request_id', '') <> ''"
        "      OR COALESCE(lr.raw_payload->>'request_id', '') <> '')"
    )

    # Успешка принадлежит группе через оператора и ДАТУ ПОЕЗДКИ: членство в группе
    # хранится интервалами, поэтому берём ту группу, в которой оператор состоял в
    # день успешки (тот же принцип, что у стемпинга daily_hours.group_id).
    _TEZ_GROUP_FILTER_SQL = """
              AND EXISTS (
                  SELECT 1 FROM group_operator_memberships gom
                  WHERE gom.operator_id = s.operator_id
                    AND gom.group_id = %s
                    AND s.success_date >= gom.start_date
                    AND (gom.end_date IS NULL OR s.success_date <= gom.end_date)
              )
    """

    # Авторы сообщений, отправленных из iCORE: ключ 'icore:<id сотрудника>'
    # (wazzup.access.ICORE_AUTHOR_PREFIX).
    _WAZZUP_ICORE_AUTHOR_LIKE = 'icore:%'

    # Направления отдела продаж, по которым раздел «Чаты ОП» делит показатели.
    # Порядок здесь — порядок групп на экране; названия направлений в базе
    # единственного числа («Верификатор»), а на экране нужен коллектив —
    # поэтому ярлык задаём рядом, а не берём из directions.name.
    WAZZUP_ANALYTICS_DIRECTIONS = (
        {'direction_id': 71, 'key': 'verifier', 'label': 'Верификаторы'},
        {'direction_id': 74, 'key': 'potok', 'label': 'Поток'},
    )

    # Общий «скелет» аналитики: окно сообщений → человеческие исходящие →
    # первый ответ на чат. Используется и построчным, и итоговым запросом,
    # чтобы обе цифры считались по одному и тому же определению.
    _WAZZUP_ANALYTICS_CTE = """
        WITH msgs AS (
            SELECT m.channel_id, m.chat_id, m.dt,
                   (m.dt AT TIME ZONE 'Asia/Almaty')::date AS local_date,
                   m.is_echo,
                   m.author_id, m.author_name,
                   map.user_id, COALESCE(map.is_bot, FALSE) AS is_bot,
                   u.direction_id
              FROM wazzup_messages m
              LEFT JOIN wazzup_operator_map map ON map.author_id = m.author_id
              LEFT JOIN users u ON u.id = map.user_id
             WHERE NOT m.is_deleted AND {window}
        ),
        agent_msgs AS (
            -- исходящие живого менеджера; grp = оператор (если привязан), иначе автор,
            -- NULL — сообщение, которое некому засчитать (но «первым ответом» быть может)
            SELECT channel_id, chat_id, dt, local_date,
                   author_id, author_name, user_id, direction_id,
                   COALESCE('u:' || user_id::text, 'a:' || author_id) AS grp
              FROM msgs
             WHERE is_echo AND NOT is_bot
        ),
        first_client AS (
            SELECT channel_id, chat_id, MIN(dt) AS at
              FROM msgs WHERE NOT is_echo
             GROUP BY channel_id, chat_id
        ),
        first_reply AS (
            -- одно значение на чат: кто ответил клиенту первым, тому и время
            SELECT DISTINCT ON (a.channel_id, a.chat_id)
                   a.grp, a.direction_id, EXTRACT(EPOCH FROM (a.dt - c.at)) AS secs
              FROM agent_msgs a
              JOIN first_client c
                ON c.channel_id = a.channel_id AND c.chat_id = a.chat_id
             WHERE a.dt > c.at
             ORDER BY a.channel_id, a.chat_id, a.dt
        )
    """

    # ── Wazzup: сборка эпизодов (единица ИИ-оценки) ──────────────────────────

    WAZZUP_EPISODE_LOCK_KEY = 915904140
    _WAZZUP_MEDIA_RU = {
        'image': '[фото]', 'video': '[видео]', 'audio': '[голосовое]',
        'document': '[документ]', 'geo': '[геолокация]', 'vcard': '[контакт]',
        'missing_call': '[пропущенный звонок]', 'unsupported': '[вложение]',
    }

    _C2D_REQUEST_COLUMNS = """
        r.request_id, r.day, r.request_start, r.request_end, r.request_time,
        r.reaction_time, r.request_type, r.transport, r.channel_id, r.channel_name,
        r.client_id, r.client_name, r.client_phone, r.c2d_operator_id,
        r.c2d_operator_name, r.operator_id, u.name, r.incoming_messages,
        r.outgoing_messages, r.rating_score, r.rating_text
    """

    # ── ChatApp (ТП/ОП ТЭЗ): пул, эпизоды, случайный чат ─────────────────────
    # Механика повторяет Wazzup, но данных не приносит вебхук — их тянет ночной
    # синк через API (chatapp_client.py). Оператор эпизода считается по
    # created.id исходящих: `responsible` у чата ChatApp регулярно расходится с
    # тем, кто реально отвечал (проверено на живых данных).

    CHATAPP_EPISODE_LOCK_KEY = 915904141

    # Что считается ручным ответом оператора, а не роботом: приветствие и
    # рассылки приходят от интеграции (fromApp.id='bitrix') под аккаунтом
    # владельца, системные события — с sender='system'. Условие держим
    # синхронным с chatapp_client.is_autoreply.
    _CHATAPP_HUMAN_OUT = ("m.side = 'out' AND COALESCE(m.app_sender, '') <> 'system' "
                          "AND COALESCE(m.app_id, '') IN ('webchat', '')")

    # ---- Часы супервайзеров по Clockster (задача #352) ----
    # СВ не сидят на линии, статусов у них нет, и операторский пересчёт «смена ×
    # статусы» даёт им ноль. У СВ отделов SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES
    # часы считаются из отметок Clockster, которые раздел «Отметки» уже копит в
    # glb_attendance_rows, минус перерыв СВ из supervisor_hours_settings.
    #
    # Роль берётся НА ДАТУ, а не текущая: день, на который у человека есть
    # операторское членство в группе, — операторский (он ещё работал на линии), и
    # его считает операторский пересчёт по статусам. По Clockster — только дни СВ.

    SUPERVISOR_ROLES_SQL = "lower(COALESCE(u.role, '')) IN ('sv', 'supervisor')"
    SUPERVISOR_CLOCKSTER_DEPARTMENT_SQL = (
        "u.department_id IN (SELECT dd.id FROM departments dd WHERE lower(COALESCE(dd.code, '')) = ANY(%s))"
    )
    SUPERVISOR_NO_CLOCKSTER_CARD = '__none__'

    SUPERVISOR_CLOCKSTER_MARKS_RETENTION_DAYS = 150

    SUPERVISOR_MANUAL_MARK_MAX_AGE_DAYS = 62

    # --- Отбивка показателей «Табло СЗоВ» в Telegram ---------------------------------------

    # Режимы получателя: каждая отбивка или только та, где есть отклонения от нормы.
    SZOV_BROADCAST_MODES = ('always', 'deviations')
    # Направления табло: у каждого свой список получателей, свои показатели и своё
    # расписание. 'osnova' — историческое имя направления «Линия» (на экране подпись
    # сменили, ключ остался: по нему лежат строки на проде). 'op' — «Табло ОП»,
    # 'tez' — «Табло Тез КЦ» (там отбивка одна на оба направления отдела и пишет
    # только о перерывах вне графика), 'op_chat' — чаты верификаторов «Табло ОП» (#367).
    # Список ОБЯЗАН совпадать с SZOV_BROADCAST_DIRECTIONS в bot_schedule2 и с CHECK
    # на szov_wallboard_broadcast_chats: 16.09.2026 'op' добавили в ручку и в CHECK, а
    # здесь забыли — кнопка «Отбивка» на табло ОП отвечала 500 из этого ValueError.
    SZOV_BROADCAST_DIRECTIONS = ('osnova', 'chat', 'op', 'tez', 'op_chat')
    SZOV_BROADCAST_DIRECTION_DEFAULT = 'osnova'

    # Направления, у которых отбивку можно ограничить группой: у табло ОП есть фильтр
    # групп, у остальных табло групп нет, и хранить там что-то кроме NULL незачем.
    SZOV_BROADCAST_GROUP_DIRECTIONS = ('op',)

    _SZOV_BREAK_VIOLATION_COLUMNS = """
        v.id, v.operator_id, u.name, v.violation_date, v.started_at, v.ended_at,
        v.duration_minutes, v.kind, v.planned_start_minutes, v.planned_end_minutes,
        v.deviation_minutes, v.detected_at, v.reported_at, v.direction
    """

    # Статусы, при которых оператор НЕ считается на смене. Список копирует
    # PLANNER_IMPORTED_NOT_ON_SHIFT_STATUS_KEYS из src/App.jsx: правило «на смене» —
    # это ВСЁ, кроме отключения, «нет на месте» и «без телефона». Перерыв, перезвон,
    # тренинг и тех.причина сменой считаются: человек на смене, просто не на линии.
    # «Офлайн» iCORE Phone (автовыброс ОП за простой без звонков) — тоже не на смене:
    # по ТЗ это время не отработано, хотя телефон и остаётся запущенным.
    # «Без телефона» (Oktell State 7) — вошёл в Oktell, но веб-телефон не подключил:
    # ещё до смены или не вышел после неё. Звонков не принимает, поэтому в факт смены
    # не идёт (задача #380).
    _HOURLY_NOT_ON_SHIFT_STATUS_KEYS = {
        'нет статуса', 'отключен', 'отключена', 'отключено',
        'logout', 'выход', 'выход из системы', 'отключение',
        'выключен', 'нет на месте',
        'inactive', 'неактивен',
        'офлайн',
        'без телефона',
    }
    # Сколько минут часа оператор должен пробыть на смене, чтобы час ему зачёлся.
    _HOURLY_FACT_MIN_MINUTES = 30

    # ── Персональные SIP-аккаунты сотрудников (раздел «Настройки SIP») ──────────
    #
    # Основной номер живёт в users.sip_number (по нему матчатся звонки Binotel/
    # Oktell), персональные пароль/домен и второй номер для автодозвона — в
    # user_sip_settings. Пустая строка = «как у всех»: домен из sip_config,
    # пароль «база + номер».

    _SIP_OPERATOR_ROLES = ('operator', 'trainee')
    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    _SIP_OPERATOR_SELECT = """
        SELECT
            u.id,
            u.name,
            LOWER(COALESCE(u.role, '')) AS role,
            u.status,
            u.department_id,
            dep.name AS department_name,
            u.supervisor_id,
            grp.group_name,
            COALESCE(u.sip_number, '') AS sip_number,
            COALESCE(s.sip_password, '') AS sip_password,
            COALESCE(s.sip_domain, '') AS sip_domain,
            COALESCE(s.autodial_number, '') AS autodial_number,
            COALESCE(s.autodial_password, '') AS autodial_password,
            COALESCE(s.autodial_domain, '') AS autodial_domain,
            s.updated_at,
            upd.name AS updated_by_name,
            COALESCE(dc.sip_server, '') AS department_sip_server,
            COALESCE(dc.base_password, '') AS department_base_password,
            COALESCE(dc.autodial_code, '') AS department_autodial_code,
            COALESCE(dc.autodial_server, '') AS department_autodial_server,
            COALESCE(dc.autodial_base_password, '') AS department_autodial_base_password,
            COALESCE(s.fop2_enabled, TRUE) AS fop2_enabled,
            COALESCE(dc.provider, 'asterisk') AS department_provider,
            COALESCE(s.sip_login, '') AS sip_login,
            COALESCE(b.cabinet_login, '') AS binotel_cabinet_login,
            COALESCE(b.employee_id, '') AS binotel_employee_id,
            COALESCE(b.cabinet_url, '') AS binotel_cabinet_url,
            -- Автопринятие входящего: персональная настройка самого телефона,
            -- поэтому от провайдера отдела не зависит (нужна и Тезу, и таксопаркам).
            -- Отдаём СЫРЫМ, без COALESCE: NULL здесь значит «как у отдела», и
            -- схлопывать его с «выключено» нельзя — панель и телефон обязаны
            -- различать личную настройку и унаследованную.
            s.auto_answer AS auto_answer,
            s.auto_answer_delay AS auto_answer_delay,
            -- Ярус отдела для того же автопринятия (NULL = отдел не задавал).
            dc.auto_answer AS department_auto_answer,
            dc.auto_answer_delay AS department_auto_answer_delay,
            COALESCE(dc.binotel_cabinet_url, '') AS department_binotel_cabinet_url,
            -- Направление сотрудника: ось фильтра в разделе. Источник —
            -- users.direction_id, а не группа: у группы направление бывает
            -- архивной версией того же самого (canonical_id), и фильтр по ней
            -- терял бы людей.
            u.direction_id AS direction_id,
            COALESCE(dir.name, '') AS direction_name,
            -- Пароль кабинета наружу не отдаём никогда — только признак «задан».
            -- Новые колонки дописываются ТОЛЬКО В КОНЕЦ: _sip_operator_row читает
            -- row по индексам, и вставка в середину сдвинула бы всё после неё.
            (NULLIF(b.cabinet_password, '') IS NOT NULL) AS has_binotel_cabinet_password,
            -- Учётка кабинета Oktell: логин видно, пароль — только фактом.
            COALESCE(ok.cabinet_login, '') AS oktell_cabinet_login,
            (NULLIF(ok.cabinet_password, '') IS NOT NULL) AS has_oktell_cabinet_password,
            -- Код отдела. По нему панель узнаёт отдел, чья телефония — кабинет
            -- Oktell (СЗоВ): там у сотрудника нет ни регистрации по SIP, ни
            -- автодозвона, ни FOP2, и карточка показывает только учётку кабинета
            -- (departmentCodeUsesOktellCabinet в src/utils/departmentViews.js).
            -- Имени отдела для этого мало: оно меняется, а код — нет.
            COALESCE(dep.code, '') AS department_code,
            -- Автосброс исходящего без ответа — ярус только отдела, личного нет.
            -- Сырым, как и ярус автоприёма: NULL = «не задано» (действуют 5
            -- гудков), 0 = не сбрасывать, и COALESCE схлопнул бы одно с другим.
            dc.ring_timeout_rings AS department_ring_timeout_rings
        FROM users u
        LEFT JOIN departments dep ON dep.id = u.department_id
        LEFT JOIN user_sip_settings s ON s.user_id = u.id
        LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
        LEFT JOIN binotel_user_accounts b ON b.user_id = u.id
        LEFT JOIN oktell_user_accounts ok ON ok.user_id = u.id
        -- Только LEFT: у стажёра направления нет вовсе, и INNER выкинул бы его
        -- из раздела вместе с его номером.
        LEFT JOIN directions dir ON dir.id = u.direction_id
        LEFT JOIN users upd ON upd.id = s.updated_by
        LEFT JOIN LATERAL (
            SELECT g.name AS group_name
            FROM group_operator_memberships gom
            JOIN groups g ON g.id = gom.group_id
            WHERE gom.operator_id = u.id AND gom.end_date IS NULL
            ORDER BY gom.start_date DESC, gom.id DESC
            LIMIT 1
        ) grp ON TRUE
    """

    _SIP_OVERRIDE_FIELDS = ('sip_password', 'sip_domain', 'autodial_password', 'autodial_domain')

    # ── Релизы iCORE Phone ──────────────────────────────────────────────────
    # Онлайн-инсталлятор раздаётся автообновлением, офлайн — только ссылкой на
    # первичную установку, поэтому «текущий» релиз считается по варианту.
    ICORE_PHONE_VARIANTS = ('online', 'offline')

    _ICORE_PHONE_RELEASE_COLUMNS = (
        "id, version, variant, filename, sha256, size_bytes, gcs_bucket, gcs_path, "
        "notes, mandatory, is_current, uploaded_by, uploaded_at"
    )

    SENSITIVE_ACCESS_SQL = """
                SELECT sensitive_data_unlocked
                FROM user_sessions
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                  AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                LIMIT 1
    """

    # Разбор user-agent живёт в SQL, потому что по нему и фильтруют, и считают
    # плашки. Второй копии в Python нет намеренно: разъехавшиеся правила давали
    # бы «Телефон 230», а в списке — пусто.
    _UA_BOT_SQL = "ua ~* 'bot|crawl|spider|slurp|bingpreview|facebookexternalhit|linkedinbot|twitterbot'"
    _UA_TABLET_SQL = (
        "("
        "ua ~* 'ipad|tablet|kindle|playbook|silk' "
        "OR (ua ~* 'android' AND ua !~* 'mobile') "
        "OR (ua ~* 'windows' AND ua !~* 'phone' AND ua ~* 'touch') "
        "OR (ua ~* 'puffin' AND ua !~* 'ip|ap|wp')"
        ")"
    )
    _UA_MOBILE_SQL = "ua ~* 'mobi|android|iphone|ipod|blackberry|iemobile|opera mini|windows phone'"

    ACTIVE_SESSION_ROLE_FILTERS = ('admin', 'sv', 'operator')
    ACTIVE_SESSION_DEVICE_FILTERS = ('desktop', 'mobile', 'tablet', 'bot', 'unknown')
    # Отдел, в отличие от роли и устройства, белым списком не задать: отделы
    # заводят в портале. Поэтому id уходит в SQL ЗНАЧЕНИЕМ параметра, а строку
    # с клиента разбирает один разборщик на все места — `_department_filter`.
    ACTIVE_SESSION_DEPARTMENT_NONE = 'none'
    # Сортировка людей, а не сессий: список раздела — по одной строке на человека.
    ACTIVE_SESSION_SORT_KEYS = {
        'user_name': 'p.user_name',
        'sessions_count': 'p.sessions_count',
        'last_seen_at': 'p.last_seen_at',
        'first_seen_at': 'p.first_seen_at',
        'sensitive': 'p.sensitive_open_count'
    }

    _ROLE_FILTER_VALUES = {
        # super_admin считается админом всюду в разделе, supervisor — СВ.
        'admin': ('admin', 'super_admin'),
        'sv': ('sv', 'supervisor'),
        'operator': ('operator',)
    }

    # Одна строка на человека: сколько у него живых сессий, где и когда он был
    # последний раз, на чём сидит и открыты ли ему чувствительные данные.
    _PEOPLE_CTE = """
        people AS (
            SELECT
                user_id,
                MIN(user_name) AS user_name,
                MIN(user_role) AS user_role,
                MIN(user_login) AS user_login,
                MIN(supervisor_id) AS supervisor_id,
                MIN(supervisor_name) AS supervisor_name,
                MIN(department_id) AS department_id,
                MIN(department_name) AS department_name,
                MIN(avatar_bucket) AS avatar_bucket,
                MIN(avatar_blob_path) AS avatar_blob_path,
                COUNT(*) AS sessions_count,
                MAX(last_seen_at) AS last_seen_at,
                MIN(created_at) AS first_seen_at,
                MAX(expires_at) AS expires_at,
                COUNT(DISTINCT ip_address) AS ip_count,
                COUNT(*) FILTER (WHERE device = 'desktop') AS desktop_count,
                COUNT(*) FILTER (WHERE device = 'mobile') AS mobile_count,
                COUNT(*) FILTER (WHERE device = 'tablet') AS tablet_count,
                COUNT(*) FILTER (WHERE device = 'bot') AS bot_count,
                COUNT(*) FILTER (WHERE device = 'unknown') AS unknown_count,
                COUNT(*) FILTER (WHERE sensitive_data_unlocked) AS sensitive_open_count,
                MAX(sensitive_data_unlocked_at) FILTER (WHERE sensitive_data_unlocked)
                    AS sensitive_last_granted_at,
                (ARRAY_AGG(unlocked_by_name ORDER BY sensitive_data_unlocked_at DESC NULLS LAST)
                    FILTER (WHERE sensitive_data_unlocked))[1] AS sensitive_last_granted_by_name,
                (ARRAY_AGG(unlocked_by_role ORDER BY sensitive_data_unlocked_at DESC NULLS LAST)
                    FILTER (WHERE sensitive_data_unlocked))[1] AS sensitive_last_granted_by_role
            FROM searched
            GROUP BY user_id
        )
    """

    _PEOPLE_COLUMNS = (
        'user_id', 'user_name', 'user_role', 'user_login', 'supervisor_id', 'supervisor_name',
        'department_id', 'department_name', 'avatar_bucket', 'avatar_blob_path',
        'sessions_count', 'last_seen_at', 'first_seen_at', 'expires_at', 'ip_count',
        'desktop_count', 'mobile_count', 'tablet_count', 'bot_count', 'unknown_count',
        'sensitive_open_count', 'sensitive_last_granted_at',
        'sensitive_last_granted_by_name', 'sensitive_last_granted_by_role'
    )

    # Отделы для пикера считаются по тому же `people`, что и плашки: по поиску,
    # но БЕЗ выбранного фильтра. Иначе, выбрав отдел, пользователь увидел бы в
    # пикере один этот отдел и не смог бы переключиться обратно.
    #
    # Второй половиной UNION в список возвращается ВЫБРАННЫЙ отдел, даже когда
    # поиск не оставил в нём ни одного человека: без неё пикер показывал бы
    # «Все отделы», хотя фильтр включён.
    _DEPARTMENTS_CTE = """
        dept_list AS (
            SELECT p.department_id,
                   MIN(p.department_name) AS department_name
            FROM people p
            GROUP BY p.department_id
            UNION ALL
            SELECT d.id, d.name
            FROM departments d
            WHERE d.id = %s::int
              AND NOT EXISTS (SELECT 1 FROM people p WHERE p.department_id = d.id)
        )
    """

    # Порядок — по названию, а не по числу людей: список у пикера должен
    # стоять на месте между открытиями, а число людей меняется от каждой буквы
    # в поиске. «Без отдела» (department_id IS NULL) — в конце.
    _DEPARTMENTS_JSON_SQL = """
        (SELECT COALESCE(JSON_AGG(JSON_BUILD_OBJECT(
                    'department_id', dl.department_id,
                    'department_name', dl.department_name
                ) ORDER BY (dl.department_id IS NULL), dl.department_name), '[]'::json)
           FROM dept_list dl)
    """

    _SUMMARY_COLUMNS_SQL = """
        COUNT(*) AS total_people,
        COALESCE(SUM(sessions_count), 0) AS total_sessions,
        COUNT(*) FILTER (WHERE user_role IN ('admin', 'super_admin')) AS admin_people,
        COUNT(*) FILTER (WHERE user_role IN ('sv', 'supervisor')) AS sv_people,
        COUNT(*) FILTER (WHERE user_role = 'operator') AS operator_people,
        COUNT(*) FILTER (WHERE desktop_count > 0) AS desktop_people,
        COUNT(*) FILTER (WHERE mobile_count > 0) AS mobile_people,
        COUNT(*) FILTER (WHERE tablet_count > 0) AS tablet_people,
        COUNT(*) FILTER (WHERE bot_count > 0) AS bot_people,
        COUNT(*) FILTER (WHERE unknown_count > 0) AS unknown_people,
        COUNT(*) FILTER (WHERE sensitive_open_count > 0) AS sensitive_people,
        COALESCE(SUM(sensitive_open_count), 0) AS sensitive_sessions
    """
    _SUMMARY_COLUMN_COUNT = 12
    # Плюс matched_people, matched_sessions и список отделов: столько колонок
    # идёт в строке ответа ДО колонок человека.
    _SUMMARY_HEAD_COLUMNS = _SUMMARY_COLUMN_COUNT + 3

    _SESSION_COLUMNS = (
        'session_id', 'user_id', 'user_agent', 'ip_address', 'created_at', 'last_seen_at',
        'expires_at', 'sensitive_data_unlocked', 'sensitive_data_unlocked_at',
        'sensitive_data_unlocked_by', 'sensitive_data_unlocked_by_name',
        'sensitive_data_unlocked_by_role', 'device_type'
    )

    _OPERATOR_PROFILE_FIELDS = frozenset([
        'direction_id', 'supervisor_id', 'rate', 'sip_number',
        'has_proxy', 'proxy_card_number', 'proxy_status', 'has_driver_license',
        'internship_in_company', 'front_office_training', 'front_office_training_date', 'taxipro_id'
    ])
    _HR_PROFILE_FIELDS = frozenset([
        'phone', 'email', 'personal_email', 'instagram', 'telegram_nick',
        'company_name', 'employment_type',
        'study_place', 'study_specialty', 'study_course', 'study_completed', 'study_completion_year',
        'close_contact_1_relation', 'close_contact_1_full_name', 'close_contact_1_phone',
        'close_contact_2_relation', 'close_contact_2_full_name', 'close_contact_2_phone',
        'card_number', 'city', 'job_title'
    ])

    # Коды ошибок понижения: ручка переводит их в понятный текст и свой HTTP-код.
    DEMOTE_USER_NOT_FOUND = "DEMOTE_USER_NOT_FOUND"
    DEMOTE_NOT_A_SUPERVISOR = "DEMOTE_NOT_A_SUPERVISOR"
    DEMOTE_HEADS_DEPARTMENT = "DEMOTE_HEADS_DEPARTMENT"
    DEMOTE_ACTIVE_STATUS_PERIOD = "DEMOTE_ACTIVE_STATUS_PERIOD"
    DEMOTE_GROUP_NOT_FOUND = "DEMOTE_GROUP_NOT_FOUND"
    DEMOTE_GROUP_ARCHIVED = "DEMOTE_GROUP_ARCHIVED"
    DEMOTE_GROUP_OTHER_DEPARTMENT = "DEMOTE_GROUP_OTHER_DEPARTMENT"
    DEMOTE_DIRECTION_REQUIRED = "DEMOTE_DIRECTION_REQUIRED"
    DEMOTE_NAME_TAKEN = "DEMOTE_NAME_TAKEN"

    # ─── IT-ticket channels & tickets ──────────────────────────────────────────
    _IT_CHANNEL_COLUMNS = (
        "id, chat_id, title, chat_type, username, is_active, source, "
        "added_by, created_at, updated_at, last_seen_at"
    )

    # ─── IT-ticket AI instructions ─────────────────────────────────────────────
    IT_TICKET_INSTRUCTION_PROFILES = ('common', 'op', 'szov')

    # ──────────────────────────── ГРУППЫ: CRUD и членство ────────────────────────────
    # Модель расчёта группы задаётся ТОЛЬКО при создании и не меняется (смена модели =
    # архив + создание/переиспользование). Принадлежность операторов/СВ — историческая
    # по датам; один оператор не может иметь две активные основные группы на одну дату.

    # Действующее направление группы — его получает оператор при переводе в группу
    # (_sync_operator_direction_from_group_tx). Сырой groups.direction_id правим
    # в двух случаях, оба есть на проде:
    #  - группа осталась на архивной версии направления (у «Тез КЦ - Тех поддержка»
    #    79 при живой 83) — берём живую строку по canonical_id;
    #  - у части групп Отдела продаж направление не задано, но модель расчёта
    #    указывает ровно на одно живое направление отдела (op_potok → «Поток»).
    #    Если у отдела на модель приходится несколько направлений (у СЗоВ и Теза
    #    это 'operator'), не угадываем.
    # У группы без модели сравнивать нечего, и направление берётся, только когда
    # оно в отделе одно («Регионы» у фронт-офисов): спутать его не с чем. Так
    # группа, заведённая без модели и без направления, даёт оператору то же
    # направление, что давала, пока форма ставила ей «Операторскую».
    # Выражение ссылается на группу через алиас g.
    _GROUP_EFFECTIVE_DIRECTION_SQL = """
        COALESCE(
            (SELECT CASE WHEN gd.is_active THEN gd.id
                         WHEN live.is_active THEN live.id
                    END
               FROM directions gd
               LEFT JOIN directions live ON live.id = gd.canonical_id
              WHERE gd.id = g.direction_id),
            (SELECT MIN(md.id)
               FROM directions md
              WHERE g.direction_id IS NULL
                AND md.is_active
                AND md.department_id = g.department_id
                AND (g.calculation_model_code IS NULL
                     OR md.calculation_model_code = g.calculation_model_code)
             HAVING COUNT(*) = 1)
        )
    """

    # Модель расчёта сотрудника в группе: своя модель группы, а если группе её не
    # задали — модель направления сотрудника. Лестница та же, что у
    # _load_operator_calculation_models_tx(as_of=...): иначе «Учёт часов» считал бы
    # человека операторской моделью, а статусы и синхронизации — моделью направления.
    # Направление, как и там, берётся ТЕКУЩЕЕ: сменили сотруднику направление —
    # его незакрытые месяцы в группе без модели пересчитаются по новому. Закрытые
    # заморожены в group_operator_month_snapshots и не меняются. Группе, где это
    # важно, модель задают явно — тогда решает она.
    # Подзапрос выполняется только для групп без модели (COALESCE ленив).
    # Выражение ссылается на группу через алиас gr, на членство — через gom.
    _GROUP_MEMBER_MODEL_SQL = """
        COALESCE(
            gr.calculation_model_code,
            (SELECT od.calculation_model_code
               FROM users ou
               JOIN directions od ON od.id = ou.direction_id
              WHERE ou.id = gom.operator_id)
        )
    """

    _GROUP_SELECT = """
        SELECT g.id, g.name, g.department_id, g.direction_id, g.calculation_model_code,
               g.table_url, g.status, g.archived_at, g.reused_from_group_id,
               g.created_at, g.updated_at, d.name AS direction_name,
               (SELECT COUNT(*) FROM group_operator_memberships gom
                 WHERE gom.group_id = g.id AND gom.end_date IS NULL) AS active_operators,
               (SELECT COALESCE(json_agg(json_build_object('id', su.id, 'name', su.name)
                        ORDER BY su.name), '[]'::json)
                  FROM group_supervisor_memberships gsm
                  JOIN users su ON su.id = gsm.supervisor_id
                 WHERE gsm.group_id = g.id AND gsm.end_date IS NULL) AS supervisors,
               """ + _GROUP_EFFECTIVE_DIRECTION_SQL + """ AS effective_direction_id
        FROM groups g
        LEFT JOIN directions d ON d.id = g.direction_id
    """

    # «День не пустой»: в нём есть хоть какие-то учтённые данные, а не строка-заглушка.
    _DAILY_HOURS_NON_EMPTY = (
        "work_time > 0 OR training_time > 0 OR talk_time > 0 "
        "OR break_time > 0 OR calls > 0 OR fine_amount > 0"
    )

    # ── Живой хвост статусов (задача #330) ─────────────────────────────
    #
    # Отрезок в operator_status_segments строится МЕЖДУ двумя событиями, а хвост
    # до «сейчас» дописывается только в момент пересборки, то есть в момент
    # прихода события. Значит статуса, который идёт прямо сейчас, в сегментах
    # нет вовсе: человек час в «Готов» — этого часа на полосе не будет, пока он
    # не переключится, и никакое обновление страницы его не покажет. Поэтому
    # текущий статус строим отдельно, из сырого события.
    #
    # Только для отделов из LIVE_STATUS_TRACK_DEPARTMENT_CODES: там телефон шлёт
    # событие сразу. У Oktell забор идёт несколько раз в сутки, и дорисованный
    # до «сейчас» хвост был бы не «реальным временем», а выдумкой.

    # Запас на ранний вход: статус, поставленный за час до смены, — всё ещё
    # статус этой смены (см. пол в _live_status_tail_window_tx).
    LIVE_STATUS_TAIL_EARLY_LOGIN_MARGIN = timedelta(hours=1)

    # ------------------------------------------------------------------
    # Заявки на изменение своей смены (оператор -> руководитель), задача #17
    # ------------------------------------------------------------------
    #
    # Первые два вида сводятся к одной операции над интервалами дня:
    #   shorten — у существующего куска смены двигаются границы ВНУТРЬ
    #             (прийти позже и/или уйти раньше);
    #   extra   — в день добавляется ещё один кусок, в том числе в выходной.
    #
    # Арифметику берём ту же, что у обменов: окно из трёх суток (вчера/сегодня/
    # завтра) и склейка интервалов. Иначе ночная смена 17:00-02:00 распадалась бы
    # на два несвязанных куска, и «уйти на час раньше» упиралось бы в полночь.
    #
    # Ещё два вида (задача #349) пишутся ТОЧЕЧНО, без перезаписи окна:
    #   change  — кусок смены переезжает на любые другие начало и конец, в том
    #             числе в другой день; короче он станет, длиннее или останется
    #             прежним — не важно;
    #   day_off — снимаются все смены дня, в графике встаёт выходной.
    # Окно здесь не годится: оно склеивает смены, соприкасающиеся в полночь, в
    # одну строку, а опустевший соседний день помечает выходным. У сокращения
    # новые границы лежат внутри старых и до соседей не дотягиваются; перенос
    # двигает их свободно, и такой стык стал бы обычным делом.

    SHIFT_CHANGE_REQUEST_KINDS = ('shorten', 'extra', 'change', 'day_off')
    # Виды, которые не уживаются с другой ожидающей заявкой на тот же день:
    # оба сверяют смену со снимком точно, и чужое одобрение раньше своего
    # оставило бы такую заявку висеть с ошибкой «смена изменилась».
    SHIFT_CHANGE_EXCLUSIVE_KINDS = ('change', 'day_off')
    SHIFT_CHANGE_REQUEST_STATUSES = ('pending', 'approved', 'rejected', 'cancelled')
    # Окно, внутри которого заявка вообще имеет смысл. Смена дальше чем через
    # полтора месяца ещё не факт что останется в графике, а прошедшую менять
    # поздно: часы за неё уже посчитаны.
    SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS = 45

    SHIFT_CHANGE_REQUEST_SELECT = """
        SELECT
            r.id,
            r.operator_id,
            op.name AS operator_name,
            r.department_id,
            dep.name AS department_name,
            r.supervisor_id,
            sv.name AS supervisor_name,
            r.request_kind,
            r.shift_date,
            r.payload_json,
            r.status,
            r.request_comment,
            r.response_comment,
            r.reviewed_by,
            rev.name AS reviewed_by_name,
            r.reviewed_at,
            r.operator_seen_at,
            r.created_at,
            r.updated_at,
            dir.name AS direction_name,
            grp.id AS group_id,
            grp.name AS group_name,
            grp_sv.id AS group_supervisor_id,
            grp_sv.name AS group_supervisor_name
        FROM work_shift_change_requests r
        JOIN users op ON op.id = r.operator_id
        LEFT JOIN departments dep ON dep.id = r.department_id
        LEFT JOIN users sv ON sv.id = r.supervisor_id
        LEFT JOIN users rev ON rev.id = r.reviewed_by
        LEFT JOIN directions dir ON dir.id = op.direction_id
        -- Группа оператора на дату смены: по ней очередь «Запросы» делится на
        -- группы СВ. Правило то же, что у записи дня часов
        -- (_get_operator_group_id_tx): покрывающее членство, иначе ближайшее
        -- членство того же месяца.
        LEFT JOIN LATERAL (
            SELECT gr.id, gr.name
            FROM group_operator_memberships gom
            JOIN groups gr ON gr.id = gom.group_id
            WHERE gom.operator_id = r.operator_id
              AND """ + MEMBERSHIP_MONTH_OVERLAP_SQL.format(m='gom', day='r.shift_date') + """
            ORDER BY """ + MEMBERSHIP_DAY_DISTANCE_SQL.format(m='gom', day='r.shift_date') + """, gom.start_date DESC
            LIMIT 1
        ) grp ON TRUE
        LEFT JOIN LATERAL (
            SELECT gsv.id, gsv.name
            FROM group_supervisor_memberships gsm
            JOIN users gsv ON gsv.id = gsm.supervisor_id
            WHERE gsm.group_id = grp.id
              AND gsm.start_date <= r.shift_date
              AND (gsm.end_date IS NULL OR gsm.end_date >= r.shift_date)
            ORDER BY gsm.start_date DESC
            LIMIT 1
        ) grp_sv ON TRUE
    """

    # ── «Уведомления об изменениях»: подписка и данные суточной сводки ───────
    #
    # Кому можно подписаться. Правило РАЗДЕЛА, а не правило остальных
    # Telegram-отчётов портала, и это не описка. У отчётов условие «роль admin
    # -> все отделы», но на бою ВСЕ шесть глав отделов имеют роль admin — по
    # тому правилу каждый из них получал бы сводку по всей компании, хотя в
    # самом разделе видит только свой отдел (_is_global_admin_requester:
    # админ глобален, только если он супер-админ ИЛИ не возглавляет отдел).
    # Сводка обязана совпадать с тем, что человек видит на экране.
    #
    # Тренер исключён явно: графики ему доступны только на чтение, и звать его
    # к чужим правкам, по которым он ничего не решает, — чистый шум. То же
    # решение принято для подписки на заявки (_shift_change_can_watch_department).
    #
    # Роль сравнивается вместе с написаниями из ROLE_ALIASES: в базе встречается
    # и 'superadmin', маршрут его нормализует, и SQL не должен от маршрута
    # отличаться.
    SCHEDULE_CHANGE_REPORT_SUBSCRIBER_SQL = """
        LOWER(COALESCE(u.role, '')) <> 'trainer'
        AND (
            LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin', 'superadmin', 'super-admin', 'super admin')
            OR EXISTS (
                SELECT 1 FROM departments d
                 WHERE d.head_user_id = u.id
                   AND COALESCE(d.is_active, TRUE) = TRUE
            )
        )
    """

    # Колонки журнала для сводки — одним списком на оба запроса: окно сводки и
    # правки, внесённые после окна. Откат складывает их строки в одну цепочку,
    # и разойтись по составу полей им нельзя.
    SCHEDULE_CHANGE_REPORT_COLUMNS = """
                       c.id, c.operator_id, op.name, c.shift_date, c.action, c.source,
                       c.start_time, c.end_time, c.prev_start_time, c.prev_end_time,
                       c.shift_type, c.prev_shift_type,
                       c.actor_id,
                       COALESCE(NULLIF(c.actor_name, ''), actor.name, ''),
                       COALESCE(c.actor_role, ''),
                       c.changed_at,
                       c.day_was_empty"""

    # ── Тест по расписанию ────────────────────────────────────────────────
    # Статус выводится из окна starts_at..ends_at и нигде не хранится: одно
    # «время сейчас» — один ответ, рассинхрона стороннего поля не бывает.
    SURVEY_TEST_STATUS_SCHEDULED = 'scheduled'
    SURVEY_TEST_STATUS_ACTIVE = 'active'
    SURVEY_TEST_STATUS_FINISHED = 'finished'

    # ── Архив опросов ─────────────────────────────────────────────────────
    # Опрос старше двух недель уходит в архив: перестаёт считаться в бейдже
    # раздела и в колоколе, но остаётся доступен на отдельной вкладке.
    # Тест — тоже, но только с закрытым (или незаданным) окном.
    SURVEY_ARCHIVE_AFTER_DAYS = 14
    SURVEY_PAGE_SIZE_DEFAULT = 20
    SURVEY_PAGE_SIZE_MAX = 100

    # Один список колонок на все выборки опросов: индексы читаются в
    # _serialize_survey_list, и расходиться между тремя запросами им нельзя.
    _SURVEY_LIST_COLUMNS = """
                        s.id,
                        s.title,
                        s.description,
                        s.created_at,
                        s.updated_at,
                        s.created_by,
                        s.direction_ids,
                        s.tenure_weeks_min,
                        s.tenure_weeks_max,
                        creator.id,
                        creator.name,
                        creator.role,
                        s.repeat_root_id,
                        s.repeat_iteration,
                        s.is_test,
                        s.group_ids,
                        s.starts_at,
                        s.ends_at,
                        s.single_attempt,
                        s.affects_quality,
                        s.archived_at
    """
    _SURVEY_QUESTION_COLUMNS = """
                    q.id,
                    q.survey_id,
                    q.position,
                    q.question_text,
                    q.question_type,
                    q.is_required,
                    q.allow_other,
                    q.options_json,
                    q.correct_options_json,
                    q.points,
                    q.partial_credit
    """

    # Заголовок записи журнала для оценки «Тестирование знаний»: он же
    # отличает такие строки в выгрузках, где нет технических полей.
    KNOWLEDGE_TEST_EVALUATION_PREFIX = 'Тестирование знаний: '

    # Потолок состава. Держится и во фронте (src/components/tasks/taskAssignees.js):
    # задача на пятнадцать человек — это рассылка, а не задача, и такой список
    # уже не читается ни в карточке, ни в выгрузке.
    TASK_MAX_ASSIGNEES = 10

    # Участие в задаче одним предикатом на весь файл. Именно EXISTS, а не JOIN:
    # джойн размножил бы задачу по числу исполнителей, и разом поехали бы COUNT(*),
    # FILTER-агрегаты сводки и пагинация страницы.
    #
    # Скалярная колонка стоит вторым основанием намеренно. В окне деплоя новый
    # процесс уже прогнал бэкфилл и принимает запросы, а старый ещё дорабатывает
    # и создаёт задачи прежним кодом — без строки состава. Такая задача пропала
    # бы у своего исполнителя из «моих задач», доски и колокола до следующего
    # перезапуска, причём молча. assigned_to заполнен всегда (NOT NULL), поэтому
    # проверка по нему закрывает и это окно, и любой будущий путь записи, который
    # забудет позвать _sync_task_assignees_tx.
    #
    # ДВА плейсхолдера, оба — id одного и того же человека.
    _TASK_ASSIGNEE_EXISTS_SQL = (
        "(t.assigned_to = %s OR EXISTS (SELECT 1 FROM task_assignees ta_f"
        " WHERE ta_f.task_id = t.id AND ta_f.user_id = %s))"
    )

    # Задачу поставил СВ того же отдела, что и смотрящий СВ (задача #298: видеть
    # задачи коллег, чтобы не ставить одну и ту же дважды). Отдел — по
    # users.department_id обоих: своей колонки отдела у задачи нет. Один
    # плейсхолдер — id смотрящего.
    _TASK_COLLEAGUE_SV_SQL = (
        "EXISTS (SELECT 1 FROM users task_sv_creator"
        " JOIN users task_sv_viewer ON task_sv_viewer.id = %s"
        " WHERE task_sv_creator.id = t.created_by"
        " AND task_sv_creator.role = 'sv'"
        " AND task_sv_viewer.department_id IS NOT NULL"
        " AND task_sv_creator.department_id = task_sv_viewer.department_id)"
    )

    # «Максимум за день до» — жёсткий предел, дальше напоминание теряет смысл.
    TASK_REMINDER_MAX_MINUTES = 24 * 60
    TASK_REMINDER_DEFAULT_MINUTES = 24 * 60

    _TASK_REPORT_SELECT = """
        SELECT
            r.id, r.task_id, r.author_id, author.name, r.kind, r.body,
            r.spent_minutes, r.created_at, r.updated_at
        FROM task_reports r
        LEFT JOIN users author ON author.id = r.author_id
    """

    # ─── Уточнения по задаче ───
    #
    # Постановщик дополняет постановку в любой момент, исполнитель просит то,
    # чего не хватает. Лента только пополняется: слова по постановке — это то,
    # на что человек уже сослался в работе, переписывать их нельзя.

    TASK_MESSAGE_KINDS = ('note', 'request', 'answer')

    _TASK_MESSAGE_SELECT = """
        SELECT
            m.id, m.task_id, m.author_id, author.name, m.kind, m.body,
            m.reply_to_id, m.resolved_at, m.resolved_by, resolver.name, m.created_at
        FROM task_messages m
        LEFT JOIN users author ON author.id = m.author_id
        LEFT JOIN users resolver ON resolver.id = m.resolved_by
    """

    # Жёсткий потолок строк в одном ответе /api/tasks: защита от «отдай всё».
    TASKS_MAX_ROWS = 500
    # У выгрузки потолок свой и выше: файл забирают целиком, страниц в нём нет.
    # Строка выгрузки в разы легче карточки — без истории, вложений и чек-листа.
    TASKS_EXPORT_MAX_ROWS = 5000

    TASK_ACTION_NEED_KINDS = ('overdue', 'returned', 'info', 'review', 'fresh', 'accepted')

    # ------------------------------------------------------------------
    # Раздел «Бот опозданий» (group_late_bot). Пишет сюда сам бот на Render,
    # сайт читает те же таблицы и правит настройки — бот перечитывает их на
    # каждом опросе, поэтому изменения с сайта применяются без его рестарта.
    # ------------------------------------------------------------------

    GLB_EVENT_TYPES = ('late', 'missing', 'early_out', 'missing_out', 'late_out', 'suspicious')
    GLB_MUTE_KINDS = ('all', 'user', 'dept')

    # Workpace пишет ФИО как в удостоверении («ҚҰРМАНОВ ҚАЙРАТ»), у нас в карточке
    # сотрудника — обиходное написание («Курманов Кайрат»). Складываем казахские
    # буквы к близким русским и убираем мягкий/твёрдый знак: без этого город к
    # сотруднику не подобрать, а транслитерация тут однозначная.
    _GLB_NAME_FOLD = str.maketrans({
        'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у',
        'һ': 'х', 'і': 'и', 'ё': 'е', 'й': 'и', 'ь': '', 'ъ': '',
    })

    GLB_STATS_COUNTERS = ('late_count', 'late_minutes', 'early_out_count',
                          'early_out_minutes', 'missing_count', 'suspicious_count')

    # Периоды, на время которых смены оператора не считаются планом: человек в
    # отпуске, на больничном, за свой счёт или уволен. Тот же набор, что и в
    # синхронизации статуса пользователя (`temporary_schedule_statuses`).
    GLB_PLAN_BLOCKING_STATUSES = ('bs', 'sick_leave', 'annual_leave', 'dismissal')

    # ── Живой статус оператора по событиям iCORE Phone ──────────────────────────
    # Кто в каком статусе «прямо сейчас» — по НАШИМ событиям телефона
    # (POST /api/operator/status_event), а не по presence телефонии. У Binotel
    # градаций всего четыре, «Тренинг» и «Техническая пауза» схлопнуты в «Перерыв»,
    # а отдел продаж статусы там вовсе не переключает (08.09.2026 все семеро
    # висели inactive при полусотне звонков у одного номера). Телефон же статус
    # проставляет всегда: каждый вход начинается с «Исхода».
    OPERATOR_LIVE_STATUS_LOOKBACK_HOURS = 16

    # ─────────────────────────────────────────────────────────────────────────
    # Раздел «Отметки»: кэш дней, справочники фильтров и свой график (ТЗ #307)
    # ─────────────────────────────────────────────────────────────────────────

    # Глубина хранения кэша отметок. Строк около сотни в день, то есть даже год
    # — это меньше сорока тысяч; чистка нужна не ради места, а чтобы таблица не
    # росла бесконечно после ухода людей из обеих систем.
    GLB_ATTENDANCE_RETENTION_DAYS = 400

    # Колонки кэша в порядке вставки. Один список на запись и на чтение: разъехавшись,
    # они дали бы молча перепутанные местами поля, а не ошибку.
    GLB_ATTENDANCE_COLUMNS = (
        'day', 'employee_id', 'seq', 'employee_name', 'department_name', 'position_name',
        'location_name', 'schedule_name', 'system', 'plan_in', 'plan_out', 'fact_in',
        'fact_out', 'late_minutes', 'early_out_minutes', 'work_seconds', 'present_seconds',
        'lunch_seconds', 'status', 'plan_mode', 'plan_source', 'hours_norm', 'marks',
    )

    # На сколько дней назад правка графика возвращает день «Отметок» в пересборку.
    # Глубже не идём намеренно: пересборка перечитывает день из Workpace и Clockster
    # целиком, а Clockster со временем обезличивает прошлое — давний день после
    # пересборки потерял бы строки центрального офиса. Такой день пересчитывают
    # руками, кнопкой «Обновить», уже понимая, что делают.
    GLB_ATTENDANCE_REOPEN_DAYS = 31

    # ─── Свой график смен (ТЗ #307, п. 5) ────────────────────────────────────

    GLB_PLAN_RULE_SCOPES = ('department', 'employee')
    GLB_PLAN_RULE_MODES = ('schedule', 'hours')

    # --- Отбивка табло лично себе («Тез КЦ», «Табло ОП · Чат») --------------------------------
    #
    # Имена методов исторические: личная отбивка появилась у «Тез КЦ», направление — параметр.
    # Колонки берутся ТОЛЬКО из этого словаря (в SQL они подставляются строкой), поэтому
    # незнакомое направление — ошибка, а не запрос к несуществующей колонке.
    BROADCAST_PERSONAL_COLUMNS = {
        'tez': ('tez_broadcast_personal_enabled', 'tez_broadcast_personal_mode'),
        'op_chat': ('op_chat_broadcast_personal_enabled', 'op_chat_broadcast_personal_mode'),
    }

    # ── Wazzup: опоздавшие входящие, сохранённые до правила доставки ──────────

    # Кто держит эпизод Wazzup по id: (таблица, колонка id субъекта). Эпизод, на
    # который ссылается хоть одна из них, — уже часть чьей-то оценки или выборки,
    # и пересборка его не удаляет. Внешних ключей на wazzup_episodes нет, связь
    # только через subject_kind='wz_episode', поэтому перечень явный.
    WAZZUP_EPISODE_REFERENCES = (
        ('ai_evaluation_runs', 'call_id'),
        ('ai_evaluation_meta', 'call_id'),
        ('ai_review_cache', 'call_id'),
        ('ai_transcript_cache', 'call_id'),
        ('ai_human_reviews', 'call_id'),
        ('qa_adjudications', 'call_id'),
        ('qa_adjudication_cases', 'call_id'),
        ('qa_gold_labels', 'call_id'),
        ('qa_retrieval_runs', 'call_id'),
        ('ai_qa_daily_samples', 'subject_id'),
        ('qa_subject_deals', 'call_id'),
        # «Исключить оценку» (call_qa/exclusions.py): исключение держит причину и
        # автора — эпизод с ним удалять нельзя, как и с самой оценкой.
        ('ai_qa_exclusions', 'call_id'),
    )

    WAZZUP_RETIME_CHATS_PER_READ = 200


# Initialize database
db = Database()

# call_qa: применение схемы ИИ-оценки звонков при старте. Без read-write
# подключения миграция пропускается; в обязательном trace-режиме частичная схема
# останавливает запуск, чтобы приложение не работало с ложной наблюдаемостью.
try:
    from call_qa.rag.migrate import apply_on_startup as _apply_call_qa_schema
    _apply_call_qa_schema()
except Exception:
    logging.exception("call_qa schema init skipped")
    from call_qa import config as _call_qa_config
    if _call_qa_config.RAG_TRACE_REQUIRED:
        raise
