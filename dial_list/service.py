# -*- coding: utf-8 -*-
"""Правила обзвона из телефона: выдача порций, звонок через Binotel, исходы.

Все операции идут через `db._get_cursor()` (одна транзакция на блок `with`).
Сетевые вызовы в Binotel делаются ВНЕ транзакций: ответ АТС может идти
секунды, а держать открытой транзакцию с блокировками ради этого нельзя.

Состояния попытки (dial_list_attempts.state):

    requested     сервер попросил АТС позвонить, generalCallID известен
    failed        АТС отказала (api_error) — плечо до телефона не дойдёт
    leg_ringing   телефон сообщил: пришло входящее плечо от АТС
    leg_answered  телефон принял плечо; АТС набирает водителя
    ended         телефон сообщил, что разговор кончился; ждём исход от Binotel
    finished      исход подтверждён Binotel (webhook / poll) либо истёк таймаут

Флаг `cancelled` (при state=finished): оператор САМ завершил звонок (событие
телефона `operator_hangup` / `ended by_operator`), а Binotel сообщил, что
водитель не ответил. Такая попытка не считается ни строке выдачи, ни лиду
(решение владельца 24.09.2026): строка остаётся в работе, итог по ней
поставить нельзя. Если же водитель ответил и оператор сам положил трубку —
это обычный разговор, итог обязателен.

Строка выдачи (assignment) считается обработанной после ПЕРВОЙ попытки с
подтверждённым исходом — любым: дозвонились, занято, не ответил. Не
дозвонились — лид вернётся в общий пул через retry_after_hours, пока попыток
меньше max_attempts. Так оператор не обязан звонить одному водителю трижды
подряд, а следующая порция выдаётся ровно когда текущая закрыта.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import hmac

from binotel import client as binotel
from common.kz_phone import normalize_kz_phone  # noqa: F401 — нормализация номеров списка

log = logging.getLogger(__name__)

# Дефолты настроек отдела (строки в dial_list_department_settings может не быть).
DEFAULT_PORTION_SIZE = 20
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_AFTER_HOURS = 24
PORTION_SIZE_MAX = 100

# Сколько секунд телефон ждёт входящее плечо после ответа сервера. Binotel по
# документации к ext-to-phone звонит на внутреннюю линию до 30 с (callTimeToExt),
# плечо обычно приходит за 1–3 с.
LEG_TIMEOUT_SEC = 30
# Попытка без исхода дольше этого времени закрывается как «неизвестно»:
# вебхук мог не дойти, а call-details звонок не увидел. Разговор длиннее часа
# на обзвоне не бывает.
ATTEMPT_STALE_MINUTES = 60
# Раньше этого возраста call-details не спрашиваем: звонок ещё набирается.
POLL_MIN_AGE_SEC = 10
POLL_MAX_PER_REQUEST = 10
# Одновременно у оператора живёт одна попытка; «живая» — моложе этого порога.
ACTIVE_ATTEMPT_WINDOW_MINUTES = 3

# disposition Binotel → итог строки. Список пополняется по живым звонкам:
# в call-details 'ONLINE' — идёт разговор, пусто — ещё набирается.
ANSWERED_DISPOSITIONS = frozenset({"ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS"})
BUSY_DISPOSITIONS = frozenset({"BUSY"})
NO_ANSWER_DISPOSITIONS = frozenset({"NOANSWER", "NO ANSWER", "NO_ANSWER", "CANCEL", "CANCELLED", "CANCELED"})
# Промежуточные статусы call-details, пока звонок идёт: CALLING — АТС набирает
# (живой тест 23.09.2026: опрос увидел CALLING на 10-й секунде и закрыл попытку
# как «другое» с billsec 0, хотя разговор шёл 25 с), ONLINE — идёт разговор.
NON_FINAL_DISPOSITIONS = frozenset({"", "ONLINE", "CALLING", "RINGING", "DIALING", "INPROGRESS", "IN-PROGRESS"})
# Закрытые без настоящего исхода — их вебхук/опрос вправе дописать позже.
PROVISIONAL_DISPOSITIONS = tuple(sorted(NON_FINAL_DISPOSITIONS | {"UNKNOWN"}))

# Серверы Binotel из официального архива примеров (samples-api-call-settings.php):
# вебхук без токена принимаем только с них.
BINOTEL_SERVER_IPS = frozenset({
    "194.88.218.116", "194.88.218.114", "194.88.218.117", "194.88.218.118",
    "194.88.219.67", "194.88.219.78", "194.88.219.70", "194.88.219.71",
    "194.88.219.72", "194.88.219.79", "194.88.219.80", "194.88.219.81",
    "194.88.219.82", "194.88.219.83", "194.88.219.84", "194.88.219.85",
    "194.88.219.86", "194.88.219.87", "194.88.219.88", "194.88.219.89",
    "194.88.219.92", "194.88.218.119", "194.88.218.120",
    "185.100.66.145", "185.100.66.146", "185.100.66.147",
})
# Секреты раздела живут ТОЛЬКО в окружении сервера (панель Render), в базе и в
# интерфейсе их нет: ключ API компании Binotel — <PREFIX>_API_KEY/_API_SECRET по
# имени компании отдела (binotel_company → binotel.client.env_prefix_for), токен
# вебхука — одна переменная на сервер. Интерфейс показывает только «задан/не задан».
WEBHOOK_TOKEN_ENV = "DIAL_LIST_WEBHOOK_TOKEN"
# Вебхук принимается только с адресов серверов Binotel (список выше); выключить
# проверку можно переменной =0, если Binotel начнёт слать с новых адресов —
# отклонённые адреса при этом видны в журнале сервера.
WEBHOOK_REQUIRE_BINOTEL_IP_ENV = "DIAL_LIST_WEBHOOK_REQUIRE_BINOTEL_IP"
WEBHOOK_TOKEN_MIN_LEN = 16
WEBHOOK_LOG_KEEP_DAYS = 30
WEBHOOK_PAYLOAD_MAX_CHARS = 20000
DEFAULT_BINOTEL_COMPANY = binotel.COMPANY_REMOTE_CC
COMPANY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# operator_hangup — оператор сам нажал «Завершить» (телефон шлёт до BYE);
# ended может нести by_operator/leg_sec — страховка, если operator_hangup не дошёл.
PHONE_EVENTS = ("ringing", "answered", "ended", "no_leg", "operator_hangup")
# Для SQL `UPPER(disposition) IN %s` (psycopg2 разворачивает tuple в список).
ANSWERED_SQL = tuple(sorted(ANSWERED_DISPOSITIONS))

# Итоги звонка: стартовый набор отдела (руководитель правит в разделе), цвет —
# hex как в системной палитре iOS, requeue — «перезвонить»: водитель снова
# попадёт в порции через retry_after_hours, счётчик попыток обнуляется.
DEFAULT_OUTCOMES = (
    ("Заинтересован", "#34C759", False),
    ("Отказ", "#FF3B30", False),
    ("Перезвонить позже", "#FF9F0A", True),
    ("Неверный номер", "#8E8E93", False),
)
OUTCOME_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
OUTCOME_NAME_MAX = 64
OUTCOMES_MAX = 30
COMMENT_MAX = 500
# Скрипт разговора отдела (владелец, 25.09.2026): основной текст с лёгкой разметкой
# («# заголовок», «**жирный**», «==выделение==», «- пункт», «> примечание») и быстрые
# вопросы с ответами — их оператор открывает прямо во время звонка. Хранится по
# отделу, версия растёт при каждом сохранении: телефон по ней понимает, что пора
# перечитать.
SCRIPT_BODY_MAX = 20000
SCRIPT_QUESTION_MAX = 200
SCRIPT_ANSWER_MAX = 8000
SCRIPT_QUESTIONS_MAX = 50
# Живое состояние попытки (карточка звонка на телефоне): call-details не чаще раза
# в столько секунд на попытку — телефон опрашивает каждые 3 с, Binotel не любит частых.
LIVE_MIN_INTERVAL_SEC = 4
PERIOD_TZ = timezone(timedelta(hours=5))  # Asia/Almaty
# Отработанные водители на телефоне (владелец, 25.09.2026): вкладка на каждый итог
# и «Не дозвонились» — строки, которые АТС закрыла без разговора. На вкладку уходит
# не больше стольких последних: телефону хватает, а ответ не раздувается к концу месяца.
WORKED_NO_ANSWER = "no_answer"
WORKED_NO_ANSWER_NAME = "Не дозвонились"
WORKED_NO_ANSWER_COLOR = "#8E8E93"
WORKED_TAB_LIMIT = 300

# Отделы, чья работа — этот обзвон (удалённый колл-центр). Глава такого отдела
# управляет разделом без роли админа. Отдел считается «своим» для раздела также,
# если ему уже заведены настройки обзвона или его телефония — Binotel: код в
# карточке отдела проставляют не всегда, и одним кодом периметр не удержать.
DIAL_LIST_DEPARTMENT_CODES = frozenset({"remote_cc"})

# Пилот: пока список не пуст, раздел руководителя открыт ТОЛЬКО этим логинам —
# даже админам его не показываем. Был {"alfa330"} с 22.09.2026 (владелец смотрел
# сам); 25.09.2026 пилот снят — раздел открыт админам и главам по общим правилам.
# Ручек телефона это не касается: там свои включатели у отдела и оператора.
DIAL_LIST_PILOT_LOGINS = frozenset()

# Главы этих отделов видят весь раздел, как админы: удалённый КЦ работает под
# крылом СЗоВ (решение владельца 25.09.2026). Код отдела — в карточке отдела.
DIAL_LIST_OVERSEER_DEPARTMENT_CODES = frozenset({"szov"})


def pilot_allows(login):
    """Пуст ли пилотный список либо есть ли логин в нём."""
    if not DIAL_LIST_PILOT_LOGINS:
        return True
    return str(login or "").strip().lower() in DIAL_LIST_PILOT_LOGINS


class DialListError(Exception):
    """Ошибка для человека: текст + HTTP-статус."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = int(status)


def map_disposition(disposition):
    """disposition Binotel → результат строки ('' — исход ещё не финальный)."""
    value = str(disposition or "").strip().upper()
    if value in NON_FINAL_DISPOSITIONS:
        return ""
    if value in ANSWERED_DISPOSITIONS:
        return "answered"
    if value in BUSY_DISPOSITIONS:
        return "busy"
    if value in NO_ANSWER_DISPOSITIONS:
        return "no_answer"
    return "other"


def resolve_enabled(personal, department):
    """Эффективное включение: персональное → отдела → выключено."""
    if personal is not None:
        return bool(personal)
    if department is not None:
        return bool(department)
    return False


def webhook_env_token():
    return (os.getenv(WEBHOOK_TOKEN_ENV) or "").strip()


def webhook_requires_binotel_ip():
    return (os.getenv(WEBHOOK_REQUIRE_BINOTEL_IP_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sid(value):
    return None if value is None else str(value)


def mask_phone(phone_norm):
    """Номер для журнала руководителя: только хвост. Полный номер из раздела не
    выходит ни в одну ручку — ни оператору, ни руководителю: руководитель
    удалённого КЦ тоже удалёнщик, а файл с номерами у загрузившего и так есть."""
    digits = "".join(ch for ch in str(phone_norm or "") if ch.isdigit())
    if len(digits) < 4:
        return "•••"
    tail = digits[-4:]
    head = "+7 ••• ••• " if len(digits) == 11 and digits.startswith("7") else "••• "
    return f"{head}{tail[:2]} {tail[2:]}"


def current_period():
    """Первый день текущего месяца по Алматы — база «по умолчанию»."""
    today = datetime.now(PERIOD_TZ).date()
    return today.replace(day=1)


def parse_period(value, allow_all=False):
    """'YYYY-MM' или 'YYYY-MM-DD' → первый день месяца (date). Пусто → None
    (текущий месяц у вызывающего). 'all' → 'all', если разрешено."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if allow_all and text == "all":
        return "all"
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().replace(day=1)
        except ValueError:
            continue
    raise DialListError("period: ожидается месяц в виде YYYY-MM")


def worked_tab(state, result, outcome_id, attempt_state):
    """На какую вкладку телефона попадает строка выдачи; None — её там нет.

    Правило зеркальное к очереди на телефоне (DialList::InQueue): строка в очереди,
    пока она issued и по ней нет итога, ждущего исхода от АТС. Итог оператора важнее
    исхода АТС. Строку, которую руководитель вернул в список, выдаёт issued при уже
    завершённой попытке с итогом — она снова в очереди, а не на вкладке итога.
    Разговор без итога (answered) на вкладки не попадает: пока итог ждут
    (pending_outcome), телефон держит его в очереди.
    """
    if outcome_id and (state == "done" or attempt_state != "finished"):
        return str(outcome_id)
    if state == "done" and result != "answered":
        return WORKED_NO_ANSWER
    return None


def at_label(value, today):
    """«сегодня, 14:05» / «24.09, 14:05» по Алматы — телефону не нужно разбирать даты."""
    if not isinstance(value, datetime):
        return ""
    local = value.astimezone(PERIOD_TZ)
    day = "сегодня" if local.date() == today else local.strftime("%d.%m")
    return f"{day}, {local:%H:%M}"


def period_label(day):
    """'Сентябрь 2026' для подписей в ответах (фронт может и сам, но телефону проще так)."""
    if not day:
        return ""
    months = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
    return f"{months[day.month - 1]} {day.year}"


# Этапы лида в журнале (одно место правды — CASE в _JOURNAL_SQL, здесь подписи).
LEAD_STAGES = {
    "queue": "В очереди",
    "waiting": "Ждёт повтора",
    "issued": "У оператора",
    "answered": "Дозвонились",
    "exhausted": "Не дозвонились",
    "excluded": "Исключён",
}
JOURNAL_SORTS = {
    "activity": "activity_at DESC, created_at DESC",
    "name": "lower(full_name) ASC, created_at ASC",
    "created": "created_at DESC",
    "attempts": "attempts_total DESC, activity_at DESC",
}
JOURNAL_MAX_LIMIT = 200


class DialListService:
    """Вся логика раздела. `db` — экземпляр Database из database.py."""

    def __init__(self, db, client_factory=None):
        self.db = db
        self._client_factory = client_factory or self._default_client
        self._clients = {}
        self._clients_lock = threading.Lock()

    # ------------------------------------------------------------ доступ и отделы
    _DEPARTMENTS_SQL = """
        SELECT d.id, d.name, COALESCE(d.code, ''),
               COALESCE(dc.provider, 'asterisk'),
               (s.department_id IS NOT NULL) AS configured,
               COALESCE(s.enabled, FALSE),
               COALESCE(s.portion_size, %s),
               (SELECT COUNT(*) FROM dial_list_leads l WHERE l.department_id = d.id) AS leads_total,
               (SELECT COUNT(*) FROM dial_list_leads l
                 WHERE l.department_id = d.id AND l.status IN ('new', 'in_progress')) AS leads_open
        FROM departments d
        LEFT JOIN sip_department_config dc ON dc.department_id = d.id
        LEFT JOIN dial_list_department_settings s ON s.department_id = d.id
        WHERE COALESCE(d.is_active, TRUE)
          AND {where}
        ORDER BY d.name
    """

    @staticmethod
    def _department_row(r):
        return {
            "department_id": int(r[0]), "department_name": r[1] or "", "code": r[2] or "",
            "provider": r[3] or "asterisk", "configured": bool(r[4]), "enabled": bool(r[5]),
            "portion_size": int(r[6]), "leads_total": int(r[7] or 0), "leads_open": int(r[8] or 0),
        }

    def list_departments(self, department_ids=None):
        """Отделы раздела: ЯВНО подключённые (есть строка настроек) либо с кодом из
        DIAL_LIST_DEPARTMENT_CODES. «Отдел на Binotel» сам по себе в раздел не попадает —
        иначе здесь оказывался бы любой отдел с этой АТС (замечание владельца 23.09.2026).
        department_ids=None — все такие (админ)."""
        params = [DEFAULT_PORTION_SIZE]
        where = "(s.department_id IS NOT NULL OR LOWER(COALESCE(d.code, '')) = ANY(%s))"
        params.append(sorted(DIAL_LIST_DEPARTMENT_CODES))
        if department_ids is not None:
            where += " AND d.id = ANY(%s)"
            params.append([int(x) for x in department_ids])
        with self.db._get_cursor() as cur:
            cur.execute(self._DEPARTMENTS_SQL.format(where=where), params)
            return [self._department_row(r) for r in cur.fetchall()]

    def candidate_departments(self):
        """Отделы, которые МОЖНО подключить: телефония Binotel, но в разделе их ещё нет.
        Из них админ выбирает в кнопке «Подключить отдел»."""
        params = [DEFAULT_PORTION_SIZE, sorted(DIAL_LIST_DEPARTMENT_CODES)]
        where = ("COALESCE(dc.provider, 'asterisk') = 'binotel' AND s.department_id IS NULL "
                 "AND NOT (LOWER(COALESCE(d.code, '')) = ANY(%s))")
        with self.db._get_cursor() as cur:
            cur.execute(self._DEPARTMENTS_SQL.format(where=where), params)
            return [self._department_row(r) for r in cur.fetchall()]

    def enroll_department(self, department_id, changed_by=None):
        """Подключить отдел к разделу: завести строку настроек (режим пока выключен)."""
        department_id = int(department_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT d.id, COALESCE(dc.provider, 'asterisk')
                FROM departments d
                LEFT JOIN sip_department_config dc ON dc.department_id = d.id
                WHERE d.id = %s AND COALESCE(d.is_active, TRUE)
            """, (department_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Отдел не найден", 404)
            if row[1] != "binotel":
                raise DialListError(
                    "Телефония отдела не Binotel: сначала переключите провайдера отдела в «Настройках SIP»", 409)
            cur.execute("""
                INSERT INTO dial_list_department_settings (department_id, enabled, updated_by)
                VALUES (%s, FALSE, %s)
                ON CONFLICT (department_id) DO NOTHING
            """, (department_id, changed_by))
        log.info("dial_list: отдел %s подключён к разделу (пользователь %s)", department_id, changed_by)
        return self.department_settings(department_id)

    def manager_scope(self, is_admin, headed_department_ids, login=None):
        """Что видит руководитель: None — всё (админ; глава отдела из
        DIAL_LIST_OVERSEER_DEPARTMENT_CODES, то есть СЗоВ); список id — глава отдела
        периметра видит свои отделы; [] — раздел не его. Пилот (DIAL_LIST_PILOT_LOGINS)
        снят 25.09.2026: список пуст, правило общее для всех."""
        if not pilot_allows(login):
            return []
        if is_admin:
            return None
        headed = sorted({int(x) for x in (headed_department_ids or [])})
        if not headed:
            return []
        if self._heads_overseer(headed):
            return None
        return [d["department_id"] for d in self.list_departments(headed)]

    def _heads_overseer(self, department_ids):
        """Возглавляет ли человек отдел-куратор раздела (код из DIAL_LIST_OVERSEER_DEPARTMENT_CODES)."""
        if not department_ids or not DIAL_LIST_OVERSEER_DEPARTMENT_CODES:
            return False
        with self.db._get_cursor() as cur:
            cur.execute("SELECT LOWER(COALESCE(code, '')) FROM departments WHERE id = ANY(%s)",
                        ([int(x) for x in department_ids],))
            return any((r[0] or "") in DIAL_LIST_OVERSEER_DEPARTMENT_CODES for r in cur.fetchall())

    # ------------------------------------------------------------ линии Binotel
    # Официальный API компании отдаёт по каждой внутренней линии SIP-логин и
    # SIP-пароль (settings/list-of-employees → endpointData). Поэтому «назначить
    # линию сотруднику» — одна кнопка: сервер сам кладёт учётку линии в его
    # SIP-настройки, и телефон регистрируется по логину/паролю iCORE. Наружу
    # (в ответы ручек) ни логин, ни пароль линии не уходят — только номер и статус.

    def _binotel_employees(self, department_id):
        payload = self._client(department_id)._post("settings/list-of-employees", {})
        emps = payload.get("listOfEmployees") or {}
        return list(emps.values()) if isinstance(emps, dict) else list(emps or [])

    @staticmethod
    def _endpoint_of(record):
        ep = record.get("endpointData") if isinstance(record, dict) else None
        return ep if isinstance(ep, dict) and str(ep.get("internalNumber") or "").strip() else None

    def _line_status_hint(self, department_id, internal_number):
        """Что Binotel сам думает о линии оператора — для текста ошибки при коде 150.

        Код 150 «Can't call to the ext» приходит и у ЗАРЕГИСТРИРОВАННОГО телефона:
        Binotel помечает линию «не в сети» при снятии регистрации (смена сети,
        перерегистрация) и возвращает в сеть только своим обходом — до 30–40 минут
        (25.09.2026, линия 904: не в сети 16:43–17:17 при живом REGISTER и ответах на
        OPTIONS). Оператору нужно знать, ждать или чинить: «проверьте регистрацию» в
        первом случае отправляет его чинить исправное. Статус читаем из
        settings/list-of-employees → endpointData.status; не получили — прежняя
        подсказка про регистрацию. Подсказка, не условие: любая ошибка здесь гасится.
        """
        def hhmm(unix_ts):
            try:
                ts = int(unix_ts or 0)
            except (TypeError, ValueError):
                return "?"
            return datetime.fromtimestamp(ts, PERIOD_TZ).strftime("%H:%M") if ts > 0 else "?"

        number = str(internal_number or "").strip()
        try:
            for rec in self._binotel_employees(department_id):
                ep = self._endpoint_of(rec)
                if not ep or str(ep.get("internalNumber") or "").strip() != number:
                    continue
                st = ep.get("status") if isinstance(ep.get("status"), dict) else {}
                sip = st.get("sip") if isinstance(st.get("sip"), dict) else {}
                state = str(st.get("preparedStatus") or sip.get("status") or "").strip().lower()
                if state in ("online", "inuse"):
                    return "Binotel при этом видит линию в сети — повторите через минуту."
                return (f"Binotel считает линию не в сети с {hhmm(sip.get('updatedAt'))} "
                        f"(последний раз в сети {hhmm(ep.get('wasOnlineAt'))}). Так бывает после "
                        "смены сети или перерегистрации: Binotel обновляет статус линии сам, обычно "
                        "за 30–40 минут. Телефон зарегистрирован — подождите и повторите; нет — "
                        "проверьте регистрацию.")
        except Exception as exc:
            log.warning("dial_list: статус линии %s у Binotel не получен: %s", number, exc)
        return "Телефон не зарегистрирован в Binotel — проверьте регистрацию и повторите"

    def department_users(self, department_id):
        """Сотрудники отдела (все роли: тест ведёт и сам владелец) с их SIP-номером и
        персональным включением обзвона (None — как у отдела)."""
        # «Работает ли человек» в iCORE — это users.status ('working' / 'fired' / …),
        # а не is_active: тот у большинства живых сотрудников FALSE (так пропал из
        # списка первый же тест-оператор). Список уволенных — тот же, что у панели SIP.
        inactive = list(getattr(self.db, "_SIP_INACTIVE_STATUSES", ("fired",)))
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, COALESCE(u.login, ''), COALESCE(u.role, ''),
                       COALESCE(u.sip_number, ''), COALESCE(u.status, ''), ds.enabled
                FROM users u
                LEFT JOIN dial_list_user_settings ds ON ds.user_id = u.id
                WHERE u.department_id = %s
                  AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                ORDER BY u.name
            """, (int(department_id), inactive))
            return [{"id": r[0], "name": r[1] or "", "login": r[2], "role": r[3],
                     "sip_number": (r[4] or "").strip(), "status": r[5],
                     "dial_list_enabled": None if r[6] is None else bool(r[6])} for r in cur.fetchall()]

    def unenroll_department(self, department_id):
        """Отключить отдел от раздела. Только пока по нему нет выдач и базы: иначе
        строка настроек спрятала бы данные, которые ещё могут понадобиться."""
        department_id = int(department_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dial_list_portions WHERE department_id = %s", (department_id,))
            portions = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM dial_list_leads WHERE department_id = %s", (department_id,))
            leads = int(cur.fetchone()[0])
            if portions or leads:
                raise DialListError(
                    f"У отдела уже есть данные обзвона (выдач {portions}, водителей {leads}) — "
                    "выключите режим вместо отключения", 409)
            cur.execute("DELETE FROM dial_list_department_settings WHERE department_id = %s", (department_id,))
        with self._clients_lock:
            self._clients.pop(department_id, None)
        log.info("dial_list: отдел %s отключён от раздела", department_id)
        return {"department_id": department_id, "configured": False}

    def list_lines(self, department_id):
        """Линии компании Binotel + кто из iCORE на них сидит. Без логинов и паролей."""
        department_id = int(department_id)
        users = self.department_users(department_id)
        by_number = {}
        for u in users:
            if u["sip_number"]:
                by_number.setdefault(u["sip_number"], u)
        lines = []
        for rec in self._binotel_employees(department_id):
            ep = self._endpoint_of(rec)
            if not ep:
                continue
            number = str(ep.get("internalNumber")).strip()
            status = ep.get("status") if isinstance(ep.get("status"), dict) else {}
            prepared = str(status.get("preparedStatus") or (status.get("sip") or {}).get("status") or "").lower()
            emp_id = str(rec.get("employeeID") or "").strip()
            linked = bool(emp_id and emp_id != "0" and (rec.get("name") or rec.get("email")))
            holder = by_number.get(number)
            lines.append({
                "internal_number": number,
                "online": prepared == "online",
                "status": prepared or "unknown",
                "tls": str(ep.get("encryptedWithTls") or "0") == "1",
                "was_online_at": _to_int(ep.get("wasOnlineAt")) or None,
                "binotel_employee": {"employee_id": emp_id, "name": rec.get("name") or "",
                                     "email": rec.get("email") or "",
                                     "presence": rec.get("presenceState") or ""} if linked else None,
                "icore_user": {"id": holder["id"], "name": holder["name"], "login": holder["login"]} if holder else None,
            })
        lines.sort(key=lambda x: (len(x["internal_number"]), x["internal_number"]))
        return lines

    def department_sip_server(self, department_id):
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COALESCE(sip_server, '') FROM sip_department_config WHERE department_id = %s",
                        (int(department_id),))
            row = cur.fetchone()
        return (row[0] or "").strip() if row else ""

    def assign_line(self, department_id, user_id, internal_number, changed_by=None):
        """Посадить сотрудника отдела на линию: SIP-логин/пароль линии → его SIP-настройки."""
        department_id = int(department_id)
        internal_number = str(internal_number or "").strip()
        if not internal_number:
            raise DialListError("Укажите внутренний номер линии")
        operator = self.db.get_sip_operator(int(user_id))
        if not operator:
            raise DialListError("Сотрудник не найден", 404)
        if operator.get("department_id") != department_id:
            raise DialListError("Сотрудник из другого отдела", 400)
        if (operator.get("department_provider") or "asterisk") != "binotel":
            raise DialListError("Телефония отдела не Binotel: переключите провайдера отдела в «Настройках SIP»", 409)
        endpoint = None
        for rec in self._binotel_employees(department_id):
            ep = self._endpoint_of(rec)
            if ep and str(ep.get("internalNumber")).strip() == internal_number:
                endpoint = ep
                break
        if not endpoint:
            raise DialListError(f"Линии {internal_number} у компании Binotel нет", 404)
        login = str(endpoint.get("login") or "").strip()
        password = str(endpoint.get("password") or "")
        if not login or not password:
            raise DialListError(f"Binotel не отдал SIP-учётку линии {internal_number}", 502)
        for u in self.department_users(department_id):
            if u["sip_number"] == internal_number and u["id"] != int(user_id):
                raise DialListError(f"Линия {internal_number} уже у сотрудника {u['name']}", 409)
        try:
            self.db.save_user_sip_settings(int(user_id), {
                "sip_number": internal_number,
                "sip_login": login,
                "sip_password": password,
            }, changed_by=changed_by)
        except ValueError as exc:
            raise DialListError(str(exc), 400)
        log.info("dial_list: сотруднику %s назначена линия Binotel %s (отдел %s)", user_id, internal_number, department_id)
        return {
            "user_id": int(user_id), "internal_number": internal_number,
            "sip_server": self.department_sip_server(department_id),
        }

    def release_line(self, department_id, user_id, changed_by=None):
        """Снять сотрудника с линии: очистить его SIP-настройки."""
        operator = self.db.get_sip_operator(int(user_id))
        if not operator or operator.get("department_id") != int(department_id):
            raise DialListError("Сотрудник не найден в этом отделе", 404)
        try:
            self.db.save_user_sip_settings(int(user_id), {"sip_number": "", "sip_login": "", "sip_password": ""},
                                           changed_by=changed_by)
        except ValueError as exc:
            raise DialListError(str(exc), 400)
        return {"user_id": int(user_id), "internal_number": ""}

    # ------------------------------------------------------------ настройки
    def department_settings(self, department_id):
        """Настройки отдела. Секретов здесь нет: ключ API и токен вебхука живут в
        окружении сервера, наружу уходит только признак «задан»."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT enabled, portion_size, max_attempts, retry_after_hours,
                       caller_id_for_employee, updated_at, binotel_company, active_period
                FROM dial_list_department_settings WHERE department_id = %s
            """, (int(department_id),))
            row = cur.fetchone()
        company = ((row[6] or "").strip() if row else "") or DEFAULT_BINOTEL_COMPANY
        active_period = row[7] if row else None
        effective = active_period or current_period()
        settings = {
            "department_id": int(department_id),
            "configured": bool(row),
            "enabled": bool(row[0]) if row else False,
            "portion_size": int(row[1]) if row else DEFAULT_PORTION_SIZE,
            "max_attempts": int(row[2]) if row else DEFAULT_MAX_ATTEMPTS,
            "retry_after_hours": int(row[3]) if row else DEFAULT_RETRY_AFTER_HOURS,
            "caller_id_for_employee": (row[4] or "") if row else "",
            "updated_at": _iso(row[5]) if row else None,
            "binotel_company": company,
            # Какой месяц обзванивается: явно выбранный руководителем или текущий.
            "active_period": active_period.isoformat() if active_period else None,
            "period": effective.isoformat(),
            "period_label": period_label(effective),
            "_period": effective,
        }
        return settings

    def save_department_settings(self, department_id, payload, changed_by=None):
        """Ключа нет в payload — не менять. Секреты сюда не принимаются вовсе:
        ключ API и токен вебхука задаются в окружении сервера."""
        payload = payload or {}
        current = self.department_settings(department_id)

        def _text(key, current_value, limit):
            if payload.get(key) is None:
                return current_value
            return str(payload[key]).strip()[:limit]

        def _int(key, current_value, lo, hi):
            if payload.get(key) is None:
                return current_value
            try:
                number = int(payload[key])
            except (TypeError, ValueError):
                raise DialListError(f"Поле {key}: нужно целое число")
            if number < lo or number > hi:
                raise DialListError(f"Поле {key}: допустимо от {lo} до {hi}")
            return number

        enabled = current["enabled"] if payload.get("enabled") is None else bool(payload["enabled"])
        portion_size = _int("portion_size", current["portion_size"], 1, PORTION_SIZE_MAX)
        max_attempts = _int("max_attempts", current["max_attempts"], 1, 20)
        retry_after_hours = _int("retry_after_hours", current["retry_after_hours"], 0, 24 * 30)
        caller_id = _text("caller_id_for_employee", current["caller_id_for_employee"], 32)
        company = _text("binotel_company", current["binotel_company"], 32).lower() or DEFAULT_BINOTEL_COMPANY
        if not COMPANY_RE.match(company):
            raise DialListError("Компания Binotel: латиница, цифры и подчёркивание, от 2 до 32 символов")
        for key in ("binotel_api_key", "binotel_api_secret", "webhook_token"):
            if str(payload.get(key) or "").strip():
                raise DialListError("Это поле не настраивается в iCORE", 400)
        # active_period: 'YYYY-MM' — обзванивать этот месяц; '' — текущий календарный.
        if "active_period" in payload:
            active_period = parse_period(payload.get("active_period"))
        else:
            active_period = parse_period(current["active_period"])

        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_department_settings (
                    department_id, enabled, portion_size, max_attempts, retry_after_hours,
                    caller_id_for_employee, binotel_company, active_period, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (department_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    portion_size = EXCLUDED.portion_size,
                    max_attempts = EXCLUDED.max_attempts,
                    retry_after_hours = EXCLUDED.retry_after_hours,
                    caller_id_for_employee = EXCLUDED.caller_id_for_employee,
                    binotel_company = EXCLUDED.binotel_company,
                    active_period = EXCLUDED.active_period,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
            """, (int(department_id), enabled, portion_size, max_attempts, retry_after_hours,
                  caller_id, company, active_period, changed_by))
        with self._clients_lock:
            self._clients.pop(int(department_id), None)
        return self.department_settings(department_id)

    def user_setting(self, user_id):
        with self.db._get_cursor() as cur:
            cur.execute("SELECT enabled FROM dial_list_user_settings WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
        return None if not row or row[0] is None else bool(row[0])

    def save_user_setting(self, user_id, enabled, changed_by=None):
        """enabled: True/False — персонально; None — «как у отдела» (строка удаляется)."""
        with self.db._get_cursor() as cur:
            if enabled is None:
                cur.execute("DELETE FROM dial_list_user_settings WHERE user_id = %s", (int(user_id),))
            else:
                cur.execute("""
                    INSERT INTO dial_list_user_settings (user_id, enabled, updated_by, updated_at)
                    VALUES (%s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (user_id) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (int(user_id), bool(enabled), changed_by))
        return {"user_id": int(user_id), "enabled": enabled}

    def phone_settings(self, user_id, operator=None):
        """Блок `dial_list` для GET /api/operator/sip_settings.

        Режим имеет смысл только у Binotel: звонок инициирует АТС по API. У
        локальной АТС такого API нет, и телефон не должен показывать вкладку."""
        operator = operator or self.db.get_sip_operator(int(user_id)) or {}
        department_id = operator.get("department_id")
        provider = operator.get("department_provider") or "asterisk"
        if provider != "binotel" or department_id is None:
            return {"enabled": False}
        dept = self.department_settings(department_id)
        enabled = resolve_enabled(self.user_setting(user_id), dept["enabled"] if dept["configured"] else None)
        return {
            "enabled": enabled,
            "portion_size": dept["portion_size"],
            "leg_timeout_sec": LEG_TIMEOUT_SEC,
            # Телефон в этом режиме прячет номера везде: история, окно звонка,
            # журнал. Флаг отдельный, чтобы при разборе инцидента его можно было
            # временно снять одному оператору.
            "hide_numbers": True,
        }

    # ------------------------------------------------------------ Binotel
    def _default_client(self, department_id):
        """Клиент API компании отдела. Ключ — только из окружения сервера."""
        settings = self.department_settings(department_id)
        cfg = binotel.get_config(company=settings["binotel_company"])
        if not binotel.api_ready(cfg):
            # Имена переменных — в журнал сервера (их читает администратор), а
            # пользователю — нейтральный текст без упоминания ключей.
            log.error("dial_list: отдел %s — на сервере не заданы %s_API_KEY / %s_API_SECRET",
                      department_id, cfg["env_prefix"], cfg["env_prefix"])
            raise DialListError("Подключение к Binotel для отдела не настроено на сервере", 503)
        return binotel.BinotelApiClient.from_config(cfg)

    def _client(self, department_id):
        department_id = int(department_id)
        with self._clients_lock:
            client = self._clients.get(department_id)
        if client is None:
            client = self._client_factory(department_id)
            with self._clients_lock:
                self._clients[department_id] = client
        return client

    # ------------------------------------------------------------ лиды отдела
    def import_leads(self, department_id, uploaded_by, file_name, rows, period=None):
        """rows — из common.leads_file.parse_leads_file: (row_number, fio, phone_raw, phone_norm).
        period — база какого месяца (date, первый день); пусто — месяц, который обзванивается."""
        department_id = int(department_id)
        period = period or self.department_settings(department_id)["_period"]
        counts = {"rows_total": len(rows), "rows_new": 0, "rows_duplicate": 0, "rows_invalid": 0,
                  "period": period.isoformat()}
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_lead_batches (department_id, uploaded_by, file_name, rows_total, period)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (department_id, uploaded_by, str(file_name or "")[:255], len(rows), period))
            batch_id = str(cur.fetchone()[0])
            seen = set()
            for _row_number, fio, _phone_raw, phone_norm in rows:
                if not phone_norm:
                    counts["rows_invalid"] += 1
                    continue
                if phone_norm in seen:
                    counts["rows_duplicate"] += 1
                    continue
                seen.add(phone_norm)
                cur.execute("""
                    INSERT INTO dial_list_leads (department_id, phone_norm, full_name, first_batch_id, last_batch_id, period)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (department_id, period, phone_norm) DO UPDATE SET
                        full_name = CASE WHEN EXCLUDED.full_name <> '' THEN EXCLUDED.full_name
                                         ELSE dial_list_leads.full_name END,
                        last_batch_id = EXCLUDED.last_batch_id,
                        upload_count = dial_list_leads.upload_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING (xmax = 0) AS inserted
                """, (department_id, phone_norm, str(fio or "").strip()[:255], batch_id, batch_id, period))
                inserted = cur.fetchone()[0]
                counts["rows_new" if inserted else "rows_duplicate"] += 1
            cur.execute("""
                UPDATE dial_list_lead_batches
                SET rows_new = %s, rows_duplicate = %s, rows_invalid = %s WHERE id = %s
            """, (counts["rows_new"], counts["rows_duplicate"], counts["rows_invalid"], batch_id))
        counts["batch_id"] = batch_id
        return counts

    def leads_summary(self, department_id, period=None):
        """Сводка базы за месяц (period — date первого дня; пусто — обзваниваемый
        месяц) плюс список всех месяцев отдела для переключателя."""
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        active = settings["_period"]
        period = period or active
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT status, COUNT(*) FROM dial_list_leads
                WHERE department_id = %s AND period = %s GROUP BY status
            """, (department_id, period))
            by_status = {row[0]: int(row[1]) for row in cur.fetchall()}
            # «Доступно сейчас» имеет смысл только для обзваниваемого месяца.
            pool = 0
            if period == active:
                cur.execute(self._POOL_SQL.format(lock="").replace("LIMIT %s", ""), (
                    department_id, active, settings["retry_after_hours"], settings["max_attempts"]))
                pool = len(cur.fetchall())
            cur.execute("""
                SELECT id, file_name, rows_total, rows_new, rows_duplicate, rows_invalid, created_at
                FROM dial_list_lead_batches WHERE department_id = %s AND period = %s
                ORDER BY created_at DESC LIMIT 20
            """, (department_id, period))
            batches = [{
                "id": _sid(r[0]), "file_name": r[1], "rows_total": r[2], "rows_new": r[3],
                "rows_duplicate": r[4], "rows_invalid": r[5], "created_at": _iso(r[6]),
            } for r in cur.fetchall()]
            cur.execute("""
                SELECT period, COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'done' AND answered_at IS NOT NULL),
                       COUNT(*) FILTER (WHERE status IN ('new', 'in_progress'))
                FROM dial_list_leads WHERE department_id = %s
                GROUP BY period ORDER BY period DESC
            """, (department_id,))
            periods = [{"period": r[0].isoformat(), "label": period_label(r[0]), "total": int(r[1]),
                        "answered": int(r[2]), "open": int(r[3]), "active": r[0] == active}
                       for r in cur.fetchall()]
        if not any(p["period"] == active.isoformat() for p in periods):
            periods.insert(0, {"period": active.isoformat(), "label": period_label(active), "total": 0,
                               "answered": 0, "open": 0, "active": True})
        return {
            "department_id": department_id,
            "period": period.isoformat(),
            "period_label": period_label(period),
            "active_period": active.isoformat(),
            "is_active_period": period == active,
            "total": sum(by_status.values()),
            "by_status": {k: by_status.get(k, 0) for k in ("new", "in_progress", "done", "excluded")},
            "pool_available": pool,
            "batches": batches,
            "periods": periods,
        }

    # ------------------------------------------------------------ итоги звонка
    # Справочник отдела: оператор после каждого разговора обязан выбрать итог (и
    # может оставить комментарий). Пока итог не указан, ни следующий звонок, ни
    # следующая порция не выдаются — проверяет и телефон, и сервер. Итоги не
    # удаляются, а выключаются: на них ссылается история.

    def _outcome_rows(self, cur, department_id, active_only=False):
        cur.execute("""
            SELECT id, name, color, position, requeue, is_active,
                   (SELECT COUNT(*) FROM dial_list_attempts t WHERE t.outcome_id = o.id) AS used
            FROM dial_list_outcomes o
            WHERE department_id = %s {active}
            ORDER BY is_active DESC, position, created_at
        """.format(active="AND is_active" if active_only else ""), (int(department_id),))
        rows = cur.fetchall()
        if not rows and active_only:
            # У отдела ещё нет ни одного итога — заводим стартовый набор один раз.
            cur.execute("SELECT 1 FROM dial_list_outcomes WHERE department_id = %s LIMIT 1", (int(department_id),))
            if not cur.fetchone():
                self._seed_outcomes(cur, department_id)
                return self._outcome_rows(cur, department_id, active_only=True)
        return [{"id": _sid(r[0]), "name": r[1], "color": r[2], "position": int(r[3]),
                 "requeue": bool(r[4]), "is_active": bool(r[5]), "used": int(r[6] or 0)} for r in rows]

    def _seed_outcomes(self, cur, department_id):
        for position, (name, color, requeue) in enumerate(DEFAULT_OUTCOMES, start=1):
            cur.execute("""
                INSERT INTO dial_list_outcomes (department_id, name, color, position, requeue)
                VALUES (%s, %s, %s, %s, %s)
            """, (int(department_id), name, color, position, requeue))
        log.info("dial_list: отделу %s заведён стартовый набор итогов", department_id)

    def outcomes(self, department_id):
        """Все итоги отдела (включая выключенные) для редактора руководителя."""
        with self.db._get_cursor() as cur:
            rows = self._outcome_rows(cur, department_id)
            if not rows:
                self._seed_outcomes(cur, department_id)
                rows = self._outcome_rows(cur, department_id)
        return rows

    def save_outcomes(self, department_id, items, changed_by=None):
        """Полный список от редактора: с id — обновить, без id — добавить, отсутствующие
        в списке — выключить (не удалить). Порядок — как в списке."""
        department_id = int(department_id)
        if not isinstance(items, list):
            raise DialListError("items: ожидается список итогов")
        if len(items) > OUTCOMES_MAX:
            raise DialListError(f"Итогов не больше {OUTCOMES_MAX}")
        cleaned = []
        seen_names = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise DialListError("Каждый итог — объект {id?, name, color, requeue, is_active}")
            name = str(raw.get("name") or "").strip()[:OUTCOME_NAME_MAX]
            if not name:
                raise DialListError("У итога должно быть название")
            if name.lower() in seen_names:
                raise DialListError(f"Итог «{name}» повторяется")
            seen_names.add(name.lower())
            color = str(raw.get("color") or "#8E8E93").strip().upper()
            if not OUTCOME_COLOR_RE.match(color):
                raise DialListError(f"Цвет итога «{name}»: ожидается #RRGGBB")
            cleaned.append({
                "id": _sid(raw.get("id")) or None, "name": name, "color": color,
                "requeue": bool(raw.get("requeue")), "is_active": raw.get("is_active", True) is not False,
            })
        if not any(c["is_active"] for c in cleaned):
            raise DialListError("Хотя бы один итог должен быть включён")
        with self.db._get_cursor() as cur:
            cur.execute("SELECT id FROM dial_list_outcomes WHERE department_id = %s", (department_id,))
            existing = {str(r[0]) for r in cur.fetchall()}
            kept = set()
            for position, c in enumerate(cleaned, start=1):
                if c["id"] and c["id"] in existing:
                    cur.execute("""
                        UPDATE dial_list_outcomes
                        SET name = %s, color = %s, position = %s, requeue = %s, is_active = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND department_id = %s
                    """, (c["name"], c["color"], position, c["requeue"], c["is_active"], c["id"], department_id))
                    kept.add(c["id"])
                else:
                    cur.execute("""
                        INSERT INTO dial_list_outcomes (department_id, name, color, position, requeue, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                    """, (department_id, c["name"], c["color"], position, c["requeue"], c["is_active"]))
                    kept.add(str(cur.fetchone()[0]))
            gone = existing - kept
            if gone:
                cur.execute("""
                    UPDATE dial_list_outcomes SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE department_id = %s AND id = ANY(%s::uuid[])
                """, (department_id, list(gone)))
        log.info("dial_list: итоги отдела %s сохранены (%d, пользователь %s)", department_id, len(cleaned), changed_by)
        return self.outcomes(department_id)

    def _pending_outcome(self, cur, user_id):
        """Последняя попытка оператора, где телефон принял плечо (разговор был), а итог
        ещё не указан. Попытки до появления итогов (leg_answered_at пуст) не считаются.

        Если оператор сам положил трубку (operator_hangup_at), долг возникает только
        когда Binotel подтвердил, что водитель ответил: до финального исхода — не
        известно, был ли разговор; не ответил — попытка отменена (cancelled) и итога
        по ней не будет."""
        cur.execute("""
            SELECT t.id, l.full_name, COALESCE(t.phone_ended_at, t.finished_at, t.updated_at), t.requested_at
            FROM dial_list_attempts t
            JOIN dial_list_assignments a ON a.id = t.assignment_id
            JOIN dial_list_leads l ON l.id = a.lead_id
            WHERE t.operator_id = %s AND t.outcome_id IS NULL AND t.leg_answered_at IS NOT NULL
              AND NOT t.cancelled
              AND ((t.operator_hangup_at IS NULL AND t.state IN ('ended', 'finished'))
                   OR (t.operator_hangup_at IS NOT NULL AND t.state = 'finished'
                       AND UPPER(t.disposition) IN %s))
            ORDER BY t.requested_at DESC LIMIT 1
        """, (int(user_id), ANSWERED_SQL))
        r = cur.fetchone()
        if not r:
            return None
        return {"attempt_id": _sid(r[0]), "full_name": r[1] or "", "ended_at": _iso(r[2]),
                "requested_at": _iso(r[3])}

    def _require_outcome_done(self, cur, ctx):
        if not self._outcome_rows(cur, ctx["department_id"], active_only=True):
            return
        pending = self._pending_outcome(cur, ctx["user_id"])
        if pending:
            raise DialListError(
                f"Сначала укажите итог звонка: {pending['full_name'] or 'предыдущий разговор'}", 409)

    def set_attempt_outcome(self, user_id, attempt_id, outcome_id, comment=""):
        """Оператор указал итог разговора (и, возможно, комментарий)."""
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        outcome_id = str(outcome_id or "").strip()
        comment = str(comment or "").strip()[:COMMENT_MAX]
        if not outcome_id:
            raise DialListError("Выберите итог звонка")
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT t.state, a.lead_id, t.outcome_id, t.cancelled, t.operator_hangup_at, t.disposition
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                WHERE t.id = %s AND t.operator_id = %s FOR UPDATE OF t
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            # Оператор сам положил трубку: итог только по состоявшемуся разговору.
            # Отменённая попытка (водитель не ответил) итога не имеет — и не
            # засчитывается; до финального исхода от АТС итог тоже не принимаем,
            # иначе «повесил трубку на гудках и поставил итог» прошло бы.
            if row[3]:
                raise DialListError("Звонок завершён до ответа водителя — итог не ставится", 409)
            if row[4] is not None and not (row[0] == "finished" and map_disposition(row[5]) == "answered"):
                raise DialListError("Вы завершили звонок сами: итог можно указать только по "
                                    "состоявшемуся разговору. Дождитесь исхода от АТС", 409)
            cur.execute("""
                SELECT id, name, color, requeue FROM dial_list_outcomes
                WHERE id = %s AND department_id = %s AND is_active
            """, (outcome_id, ctx["department_id"]))
            outcome = cur.fetchone()
            if not outcome:
                raise DialListError("Такого итога нет — обновите список", 400)
            cur.execute("""
                UPDATE dial_list_attempts
                SET outcome_id = %s, operator_comment = %s, outcome_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (outcome_id, comment, attempt_id))
            if outcome[3]:
                # «Перезвонить»: лид снова в работе, попытки с нуля. Если исход от АТС
                # приедет позже, _touch_lead увидит этот итог и статус не перебьёт.
                cur.execute("""
                    UPDATE dial_list_leads
                    SET status = CASE WHEN status = 'excluded' THEN status ELSE 'in_progress' END,
                        attempts_total = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (str(row[1]),))
        log.info("dial_list: оператор %s указал итог %s по попытке %s", ctx["user_id"], outcome[1], attempt_id)
        return {"attempt_id": attempt_id,
                "outcome": {"id": _sid(outcome[0]), "name": outcome[1], "color": outcome[2], "requeue": bool(outcome[3])},
                "comment": comment}

    # ------------------------------------------------------------ журнал водителей
    # Один запрос и для списка, и для карточки: этап (stage) считается в SQL, чтобы
    # фильтр «В очереди» показывал ровно то, что выдаст _POOL_SQL. Номер выбирается
    # только ради маски — наружу уходит phone_masked.
    _JOURNAL_BASE_SQL = """
        WITH base AS (
            SELECT l.id, l.department_id, l.full_name, l.phone_norm, l.status, l.attempts_total,
                   l.last_attempt_at, l.answered_at, l.created_at, l.updated_at, l.upload_count,
                   COALESCE(l.note, '') AS note,
                   l.period,
                   lo.id AS last_outcome_id, lo.name AS last_outcome_name, lo.color AS last_outcome_color,
                   COALESCE(lt.operator_comment, '') AS last_comment,
                   COALESCE(lt.cancelled, FALSE) AS last_cancelled,
                   fb.file_name AS first_file, fb.created_at AS first_uploaded_at,
                   lb.file_name AS last_file, lb.created_at AS last_uploaded_at,
                   oa.operator_id AS open_operator_id, ou.name AS open_operator_name,
                   la.operator_id AS last_operator_id, lu.name AS last_operator_name,
                   la.result AS last_result, la.done_at AS last_done_at,
                   lt.requested_at AS last_call_at, lt.state AS last_call_state,
                   lt.disposition AS last_disposition, lt.billsec AS last_billsec,
                   lt.api_error AS last_api_error, tu.name AS last_call_operator_name,
                   GREATEST(l.created_at, COALESCE(lt.requested_at, l.created_at),
                            COALESCE(lb.created_at, l.created_at)) AS activity_at,
                   CASE
                       WHEN l.status = 'excluded' THEN 'excluded'
                       WHEN oa.operator_id IS NOT NULL THEN 'issued'
                       WHEN l.status = 'done' AND l.answered_at IS NOT NULL THEN 'answered'
                       WHEN l.status = 'done' THEN 'exhausted'
                       WHEN l.attempts_total >= %(max_attempts)s THEN 'exhausted'
                       WHEN l.last_attempt_at IS NOT NULL
                            AND l.last_attempt_at >= CURRENT_TIMESTAMP - make_interval(hours => %(retry_hours)s)
                           THEN 'waiting'
                       ELSE 'queue'
                   END AS stage,
                   l.last_attempt_at + make_interval(hours => %(retry_hours)s) AS next_retry_at
            FROM dial_list_leads l
            LEFT JOIN dial_list_lead_batches fb ON fb.id = l.first_batch_id
            LEFT JOIN dial_list_lead_batches lb ON lb.id = l.last_batch_id
            LEFT JOIN LATERAL (
                SELECT a.operator_id FROM dial_list_assignments a
                WHERE a.lead_id = l.id AND a.state = 'issued'
                ORDER BY a.created_at DESC LIMIT 1
            ) oa ON TRUE
            LEFT JOIN users ou ON ou.id = oa.operator_id
            LEFT JOIN LATERAL (
                SELECT a.operator_id, a.result, a.done_at FROM dial_list_assignments a
                WHERE a.lead_id = l.id
                ORDER BY a.created_at DESC LIMIT 1
            ) la ON TRUE
            LEFT JOIN users lu ON lu.id = la.operator_id
            LEFT JOIN LATERAL (
                SELECT t.requested_at, t.state, t.disposition, t.billsec, t.api_error, t.operator_id,
                       t.outcome_id, t.operator_comment, t.cancelled
                FROM dial_list_attempts t
                JOIN dial_list_assignments a2 ON a2.id = t.assignment_id
                WHERE a2.lead_id = l.id
                ORDER BY t.requested_at DESC LIMIT 1
            ) lt ON TRUE
            LEFT JOIN users tu ON tu.id = lt.operator_id
            LEFT JOIN dial_list_outcomes lo ON lo.id = lt.outcome_id
            WHERE {scope}
        )
    """
    # Порядок колонок строки журнала — на него опирается _journal_row (r[0]…r[36]).
    _JOURNAL_COLUMNS = """
               id, department_id, full_name, phone_norm, status, attempts_total, last_attempt_at,
               answered_at, created_at, updated_at, upload_count, note,
               first_file, first_uploaded_at, last_file, last_uploaded_at,
               open_operator_id, open_operator_name, last_operator_id, last_operator_name,
               last_result, last_done_at, last_call_at, last_call_state, last_disposition,
               last_billsec, last_api_error, last_call_operator_name, activity_at, stage, next_retry_at,
               period, last_outcome_id, last_outcome_name, last_outcome_color, last_comment,
               last_cancelled"""
    _JOURNAL_SQL = _JOURNAL_BASE_SQL + """
        SELECT""" + _JOURNAL_COLUMNS + """,
               COUNT(*) OVER () AS total
        FROM base
        WHERE {filters}
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    # Страница журнала вместе со сводкой — одним запросом. Числа на полосе этапов
    # и на чипах итогов считаются по той же выборке, что и список: этапы — со
    # всеми фильтрами, кроме этапа, итоги — со всеми, кроме итога. Тогда число на
    # чипе равно числу строк, которые покажет нажатие на него. Итог — последнего
    # звонка водителя, ровно тот, по которому фильтрует список.
    #
    # base с тремя LATERAL на каждого водителя месяца — самая дорогая часть, и
    # вторым запросом за сводкой она строилась бы дважды. Поэтому выборка по
    # общим фильтрам материализуется один раз (f), а страница и сводка читают её.
    # Сводка приходит JSON-колонкой к каждой строке страницы; LEFT JOIN от сводки
    # отдаёт одну строку и тогда, когда страница пуста.
    _JOURNAL_PAGE_SQL = _JOURNAL_BASE_SQL.rstrip() + """,
        f AS MATERIALIZED (
            SELECT * FROM base WHERE {filters}
        ),
        page AS (
            SELECT""" + _JOURNAL_COLUMNS + """,
                   COUNT(*) OVER () AS total,
                   ROW_NUMBER() OVER (ORDER BY {order}) AS rn
            FROM f
            WHERE {stage_ok} AND {outcome_ok}
            ORDER BY rn
            LIMIT %(limit)s OFFSET %(offset)s
        ),
        counts AS (
            SELECT COALESCE(json_agg(json_build_array(stage, last_outcome_id::text, for_stage, for_outcome)),
                            '[]'::json) AS summary
            FROM (
                SELECT stage, last_outcome_id,
                       COUNT(*) FILTER (WHERE {outcome_ok}) AS for_stage,
                       COUNT(*) FILTER (WHERE {stage_ok}) AS for_outcome
                FROM f
                GROUP BY stage, last_outcome_id
            ) g
        )
        SELECT page.*, counts.summary
        FROM counts LEFT JOIN page ON TRUE
        ORDER BY page.rn
    """

    def _journal_row(self, r, max_attempts):
        stage = r[29]
        responsible_id = r[16] if r[16] is not None else r[18]
        responsible_name = r[17] if r[16] is not None else r[19]
        last_call = None
        if r[22] is not None:
            state = r[23] or ""
            disposition = r[24] or ""
            result = map_disposition(disposition) if state == "finished" else ("failed" if state == "failed" else "")
            if state == "finished" and not result:
                result = "other"
            if r[36]:
                # Оператор сам завершил звонок до ответа водителя — попытка не в счёт.
                result = "cancelled"
            last_call = {
                "at": _iso(r[22]), "state": state, "disposition": disposition, "result": result,
                "billsec": int(r[25] or 0), "api_error": (r[26] or "")[:200], "operator_name": r[27] or "",
                "cancelled": bool(r[36]),
            }
        return {
            "id": _sid(r[0]), "department_id": int(r[1]), "full_name": r[2] or "",
            "phone_masked": mask_phone(r[3]), "status": r[4], "stage": stage,
            "stage_label": LEAD_STAGES.get(stage, stage), "attempts_total": int(r[5] or 0),
            "max_attempts": int(max_attempts), "last_attempt_at": _iso(r[6]), "answered_at": _iso(r[7]),
            "created_at": _iso(r[8]), "updated_at": _iso(r[9]), "upload_count": int(r[10] or 1),
            "note": r[11] or "",
            "batch": {"file_name": r[12] or "", "uploaded_at": _iso(r[13])},
            "last_batch": {"file_name": r[14] or "", "uploaded_at": _iso(r[15])},
            "responsible": ({"id": int(responsible_id), "name": responsible_name or ""}
                            if responsible_id is not None else None),
            "responsible_is_current": r[16] is not None,
            "last_result": r[20] or "",
            "last_call": last_call,
            "activity_at": _iso(r[28]),
            "next_retry_at": _iso(r[30]) if stage == "waiting" else None,
            "period": r[31].isoformat() if r[31] else None,
            "period_label": period_label(r[31]),
            # Итог и комментарий оператора по последнему звонку.
            "outcome": ({"id": _sid(r[32]), "name": r[33] or "", "color": r[34] or "#8E8E93"}
                        if r[32] is not None else None),
            "comment": r[35] or "",
        }

    def leads_journal(self, department_id, q="", stage="", operator_id=None, batch_id=None,
                      date_from=None, date_to=None, sort="activity", limit=50, offset=0,
                      period=None, outcome_id=None):
        """Журнал водителей отдела: страница строк + всего. Все фильтры необязательны.
        period — date первого дня месяца, 'all' — все месяцы, пусто — обзваниваемый."""
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        if period is None:
            period = settings["_period"]
        params = {
            "max_attempts": settings["max_attempts"], "retry_hours": settings["retry_after_hours"],
            "department_id": department_id,
            "limit": max(1, min(_to_int(limit, 50), JOURNAL_MAX_LIMIT)), "offset": max(0, _to_int(offset, 0)),
        }
        scope = "l.department_id = %(department_id)s"
        if period != "all":
            params["period"] = period
            scope += " AND l.period = %(period)s"
        filters = ["TRUE"]
        # Этап и итог — отдельно от остальных фильтров: сводка по полосе этапов и
        # по чипам итогов считается без «своего» фильтра (см. _JOURNAL_COUNTS_SQL).
        stage_ok = outcome_ok = "TRUE"
        if outcome_id:
            params["outcome_id"] = str(outcome_id)
            outcome_ok = "last_outcome_id = %(outcome_id)s::uuid"
        q = str(q or "").strip()
        if q:
            digits = "".join(ch for ch in q if ch.isdigit())
            params["q_name"] = f"%{q}%"
            if len(digits) >= 3 and len(digits) >= len(q) - 3:
                # Поиск по хвосту номера (маска показывает 4 цифры) — сам номер всё равно не отдаём.
                params["q_tail"] = f"%{digits}"
                filters.append("(full_name ILIKE %(q_name)s OR phone_norm LIKE %(q_tail)s)")
            else:
                filters.append("full_name ILIKE %(q_name)s")
        stage = str(stage or "").strip()
        if stage:
            if stage not in LEAD_STAGES:
                raise DialListError("stage: неизвестный этап")
            params["stage"] = stage
            stage_ok = "stage = %(stage)s"
        if operator_id:
            params["operator_id"] = _to_int(operator_id, 0)
            filters.append("""EXISTS (SELECT 1 FROM dial_list_assignments a
                                      WHERE a.lead_id = base.id AND a.operator_id = %(operator_id)s)""")
        if batch_id:
            params["batch_id"] = str(batch_id)
            filters.append("""EXISTS (SELECT 1 FROM dial_list_leads l2
                                      WHERE l2.id = base.id
                                        AND (l2.first_batch_id = %(batch_id)s::uuid OR l2.last_batch_id = %(batch_id)s::uuid))""")
        # «Дата звонка»: водителю звонили в эти дни (хотя бы одна попытка, дни по
        # Алматы). Обе границы — на ОДНОЙ попытке: звонок до периода и звонок после
        # него вместе не делают водителя «звонили в эти дни».
        call_bounds = []
        if date_from:
            params["date_from"] = str(date_from)
            call_bounds.append("t.requested_at >= (%(date_from)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'")
        if date_to:
            params["date_to"] = str(date_to)
            call_bounds.append("t.requested_at < (%(date_to)s::date + 1)::timestamp AT TIME ZONE 'Asia/Almaty'")
        if call_bounds:
            filters.append("""EXISTS (SELECT 1 FROM dial_list_attempts t
                                      JOIN dial_list_assignments a ON a.id = t.assignment_id
                                      WHERE a.lead_id = base.id AND {})""".format(" AND ".join(call_bounds)))
        order = JOURNAL_SORTS.get(str(sort or "activity"), JOURNAL_SORTS["activity"])
        sql = self._JOURNAL_PAGE_SQL.format(scope=scope, filters=" AND ".join(filters), order=order,
                                            stage_ok=stage_ok, outcome_ok=outcome_ok)
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # Последняя колонка — сводка, предпоследняя — rn; пустая страница
            # приходит одной строкой, где колонки страницы — NULL.
            summary = (rows[0][-1] if rows else None) or []
            page = [r for r in rows if r[0] is not None]
            total = int(page[0][37]) if page else 0
            items = [self._journal_row(r, settings["max_attempts"]) for r in page]
            by_stage = {k: 0 for k in LEAD_STAGES}
            outcome_counts = {}
            for stage_value, outcome_value, for_stage, for_outcome in summary:
                by_stage[stage_value] = by_stage.get(stage_value, 0) + int(for_stage or 0)
                if outcome_value is not None:
                    outcome_counts[outcome_value] = outcome_counts.get(outcome_value, 0) + int(for_outcome or 0)
            cur.execute("""
                SELECT id, name, color, is_active FROM dial_list_outcomes
                WHERE department_id = %(department_id)s
                ORDER BY is_active DESC, position
            """, params)
            by_outcome = [{"id": _sid(r[0]), "name": r[1], "color": r[2], "is_active": bool(r[3]),
                           "count": outcome_counts.get(_sid(r[0]), 0)} for r in cur.fetchall()]
        return {
            "department_id": department_id, "items": items, "total": total,
            "limit": params["limit"], "offset": params["offset"],
            "period": None if period == "all" else period.isoformat(),
            "period_label": "Все месяцы" if period == "all" else period_label(period),
            "active_period": settings["period"],
            "by_stage": by_stage, "stages": LEAD_STAGES, "by_outcome": by_outcome,
            "sort": sort if sort in JOURNAL_SORTS else "activity",
        }

    def lead_department(self, lead_id):
        """Отдел лида (для проверки зоны руководителя) или 404."""
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id FROM dial_list_leads WHERE id = %s", (str(lead_id),))
            row = cur.fetchone()
        if not row:
            raise DialListError("Водитель не найден в журнале", 404)
        return int(row[0])

    def attempt_department(self, attempt_id):
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT l.department_id FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE t.id = %s
            """, (str(attempt_id),))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        return int(row[0])

    def lead_card(self, lead_id):
        """Карточка водителя: строка журнала + все попытки, ручные действия, загрузки."""
        lead_id = str(lead_id)
        department_id = self.lead_department(lead_id)
        settings = self.department_settings(department_id)
        params = {"max_attempts": settings["max_attempts"], "retry_hours": settings["retry_after_hours"],
                  "lead_id": lead_id, "limit": 1, "offset": 0}
        sql = self._JOURNAL_SQL.format(scope="l.id = %(lead_id)s", filters="TRUE", order="created_at")
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            lead = self._journal_row(row, settings["max_attempts"])
            cur.execute("""
                SELECT t.id, t.requested_at, t.operator_id, COALESCE(u.name, ''), t.internal_number, t.state,
                       t.disposition, t.billsec, t.waitsec, t.api_error, t.final_source, t.finished_at,
                       t.phone_event_at, t.phone_ended_at,
                       (t.general_call_id IS NOT NULL AND t.general_call_id <> ''),
                       a.id, a.result, a.state, a.created_at,
                       o.id, o.name, o.color, COALESCE(t.operator_comment, ''), t.outcome_at,
                       t.cancelled, t.operator_hangup_at, t.leg_sec
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                LEFT JOIN users u ON u.id = t.operator_id
                LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
                WHERE a.lead_id = %s
                ORDER BY t.requested_at DESC
            """, (lead_id,))
            attempts = []
            for r in cur.fetchall():
                state = r[5] or ""
                disposition = r[6] or ""
                result = map_disposition(disposition) if state == "finished" else ("failed" if state == "failed" else "")
                if state == "finished" and not result:
                    result = "other"
                if r[24]:
                    result = "cancelled"
                attempts.append({
                    # Оператор сам завершил звонок (когда — operator_hangup_at); cancelled —
                    # водитель при этом не ответил, попытка не засчитана.
                    "cancelled": bool(r[24]), "operator_hangup_at": _iso(r[25]), "leg_sec": int(r[26] or 0),
                    "id": _sid(r[0]), "requested_at": _iso(r[1]),
                    "operator": {"id": r[2], "name": r[3]}, "internal_number": r[4] or "",
                    "state": state, "disposition": disposition, "result": result,
                    "billsec": int(r[7] or 0), "waitsec": int(r[8] or 0), "api_error": r[9] or "",
                    "final_source": r[10] or "", "finished_at": _iso(r[11]),
                    "phone_event_at": _iso(r[12]), "phone_ended_at": _iso(r[13]),
                    # Запись есть только у состоявшегося разговора; ссылку даём по запросу
                    # (attempt_recording) — она подписанная и живёт около часа.
                    "recording_available": bool(r[14]) and state == "finished" and result == "answered"
                                           and int(r[7] or 0) > 0,
                    "assignment": {"id": _sid(r[15]), "result": r[16] or "", "state": r[17] or "",
                                   "issued_at": _iso(r[18])},
                    "outcome": ({"id": _sid(r[19]), "name": r[20] or "", "color": r[21] or "#8E8E93"}
                                if r[19] is not None else None),
                    "comment": r[22] or "",
                    "outcome_at": _iso(r[23]),
                })
            cur.execute("""
                SELECT e.id, e.kind, e.note, e.created_at, e.actor_id, COALESCE(u.name, '')
                FROM dial_list_lead_events e
                LEFT JOIN users u ON u.id = e.actor_id
                WHERE e.lead_id = %s ORDER BY e.created_at DESC
            """, (lead_id,))
            events = [{"id": int(r[0]), "kind": r[1], "note": r[2] or "", "at": _iso(r[3]),
                       "actor": {"id": r[4], "name": r[5]}} for r in cur.fetchall()]
            cur.execute("""
                SELECT a.id, a.created_at, a.state, a.result, a.done_at, a.operator_id, COALESCE(u.name, ''),
                       a.attempts
                FROM dial_list_assignments a
                LEFT JOIN users u ON u.id = a.operator_id
                WHERE a.lead_id = %s ORDER BY a.created_at DESC
            """, (lead_id,))
            assignments = [{"id": _sid(r[0]), "issued_at": _iso(r[1]), "state": r[2], "result": r[3] or "",
                            "done_at": _iso(r[4]), "operator": {"id": r[5], "name": r[6]},
                            "attempts": int(r[7] or 0)} for r in cur.fetchall()]
            cur.execute("""
                SELECT b.id, b.file_name, b.created_at, COALESCE(u.name, '')
                FROM dial_list_lead_batches b
                LEFT JOIN users u ON u.id = b.uploaded_by
                WHERE b.id IN (SELECT first_batch_id FROM dial_list_leads WHERE id = %s
                               UNION SELECT last_batch_id FROM dial_list_leads WHERE id = %s)
                ORDER BY b.created_at
            """, (lead_id, lead_id))
            batches = [{"id": _sid(r[0]), "file_name": r[1] or "", "uploaded_at": _iso(r[2]),
                        "uploaded_by": r[3]} for r in cur.fetchall()]
        lead.update({"attempts": attempts, "events": events, "assignments": assignments, "batches": batches,
                     "retry_after_hours": settings["retry_after_hours"]})
        return lead

    def _log_lead_event(self, cur, lead_id, department_id, actor_id, kind, note=""):
        cur.execute("""
            INSERT INTO dial_list_lead_events (lead_id, department_id, actor_id, kind, note)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(lead_id), int(department_id), actor_id, kind, str(note or "")[:500]))

    def requeue_lead(self, lead_id, actor_id, note=""):
        """Руководитель возвращает водителя в список: попытки обнуляются, лид снова в
        пуле. Строку, которая сейчас у оператора (issued), не трогаем — она и так в списке.

        Если водителя уже обработали, а его строка лежит в ПОСЛЕДНЕЙ выдаче оператора
        (открытой или закрытой — телефон показывает её до «Ещё»), строка
        переоткрывается прямо там: иначе оператор видел бы «Перезвонить позже» и не
        мог позвонить, пока не закроет всю пачку и не возьмёт следующую (владелец,
        24.09.2026). Строка в более старой выдаче не трогается — водитель придёт с
        ближайшей порцией."""
        lead_id = str(lead_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id, status FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            cur.execute("SELECT 1 FROM dial_list_assignments WHERE lead_id = %s AND state = 'issued'", (lead_id,))
            if cur.fetchone():
                raise DialListError("Строка сейчас у оператора — она и так в списке", 409)
            cur.execute("""
                UPDATE dial_list_leads
                SET status = 'new', attempts_total = 0, last_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (lead_id,))
            reopened = self._reopen_in_latest_portion(cur, lead_id)
            self._log_lead_event(cur, lead_id, row[0], actor_id,
                                 "restore" if row[1] == "excluded" else "requeue", note)
        card = self.lead_card(lead_id)
        card["reopened_for_operator"] = reopened
        return card

    def _reopen_in_latest_portion(self, cur, lead_id):
        """Обработанная строка водителя в последней выдаче её оператора → снова issued;
        закрытая выдача открывается обратно. Возвращает True, если переоткрыли."""
        cur.execute("""
            SELECT a.id, a.portion_id, a.operator_id
            FROM dial_list_assignments a
            WHERE a.lead_id = %s AND a.state = 'done'
            ORDER BY a.created_at DESC LIMIT 1
        """, (str(lead_id),))
        last = cur.fetchone()
        if not last:
            return False
        cur.execute("""
            SELECT id FROM dial_list_portions WHERE operator_id = %s
            ORDER BY issued_at DESC LIMIT 1
        """, (int(last[2]),))
        latest = cur.fetchone()
        if not latest or str(latest[0]) != str(last[1]):
            return False
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'issued', result = '', done_at = NULL
            WHERE id = %s
        """, (str(last[0]),))
        cur.execute("UPDATE dial_list_portions SET closed_at = NULL WHERE id = %s", (str(last[1]),))
        log.info("dial_list: водитель %s возвращён в список — строка снова у оператора %s", lead_id, last[2])
        return True

    def exclude_lead(self, lead_id, actor_id, note=""):
        """Исключить водителя из обзвона. Если строка у оператора — снимаем её с его
        списка (закрываем выдачу без результата); во время идущего звонка — нельзя."""
        lead_id = str(lead_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id, status FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            if row[1] == "excluded":
                raise DialListError("Водитель уже исключён", 409)
            cur.execute("""
                SELECT a.id FROM dial_list_assignments a WHERE a.lead_id = %s AND a.state = 'issued' FOR UPDATE
            """, (lead_id,))
            open_assignment = cur.fetchone()
            if open_assignment:
                cur.execute("""
                    SELECT 1 FROM dial_list_attempts
                    WHERE assignment_id = %s AND state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')
                """, (str(open_assignment[0]),))
                if cur.fetchone():
                    raise DialListError("Оператор сейчас звонит этому водителю — дождитесь окончания", 409)
                cur.execute("""
                    UPDATE dial_list_assignments
                    SET state = 'done', result = 'other', done_at = CURRENT_TIMESTAMP
                    WHERE id = %s RETURNING portion_id
                """, (str(open_assignment[0]),))
                portion = cur.fetchone()
                if portion:
                    cur.execute("""
                        UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND closed_at IS NULL
                          AND NOT EXISTS (SELECT 1 FROM dial_list_assignments
                                          WHERE portion_id = %s AND state = 'issued')
                    """, (str(portion[0]), str(portion[0])))
            cur.execute("""
                UPDATE dial_list_leads
                SET status = 'excluded', updated_at = CURRENT_TIMESTAMP,
                    note = CASE WHEN %s <> '' THEN %s ELSE note END
                WHERE id = %s
            """, (str(note or "")[:500], str(note or "")[:500], lead_id))
            self._log_lead_event(cur, lead_id, row[0], actor_id, "exclude", note)
        return self.lead_card(lead_id)

    def set_lead_note(self, lead_id, actor_id, note):
        lead_id = str(lead_id)
        note = str(note or "").strip()[:500]
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            cur.execute("UPDATE dial_list_leads SET note = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (note, lead_id))
            self._log_lead_event(cur, lead_id, row[0], actor_id, "note", note)
        return self.lead_card(lead_id)

    def attempt_recording(self, attempt_id):
        """Ссылка на запись разговора у Binotel (подписанная, живёт ~1 час)."""
        attempt_id = str(attempt_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT t.general_call_id, t.state, t.disposition, t.billsec, l.department_id
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE t.id = %s
            """, (attempt_id,))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        general_call_id, state, disposition, billsec, department_id = row
        if not general_call_id:
            raise DialListError("У этой попытки не было звонка через АТС", 404)
        if state != "finished" or map_disposition(disposition) != "answered" or int(billsec or 0) <= 0:
            raise DialListError("Разговора не было — записи нет", 404)
        try:
            url = self._client(int(department_id)).get_call_record_url(general_call_id)
        except DialListError:
            raise
        except Exception as exc:
            log.warning("dial_list: запись %s недоступна: %s", general_call_id, exc)
            raise DialListError("АТС не отдала запись. Попробуйте позже", 502)
        if not url:
            raise DialListError("Запись ещё не готова у АТС. Попробуйте через минуту", 404)
        return {"attempt_id": attempt_id, "url": url, "billsec": int(billsec or 0)}

    # Пул: лиды отдела, которые можно выдать сейчас. Один и тот же текст для
    # выдачи (с блокировкой) и для подсчёта (без неё), чтобы цифра «доступно»
    # совпадала с тем, что реально выдаётся.
    _POOL_SQL = """
        SELECT l.id
        FROM dial_list_leads l
        WHERE l.department_id = %s
          AND l.period = %s
          AND l.status IN ('new', 'in_progress')
          AND NOT EXISTS (SELECT 1 FROM dial_list_assignments a
                          WHERE a.lead_id = l.id AND a.state = 'issued')
          AND (l.last_attempt_at IS NULL
               OR l.last_attempt_at < CURRENT_TIMESTAMP - make_interval(hours => %s))
          AND l.attempts_total < %s
        ORDER BY l.attempts_total ASC, l.last_attempt_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %s {lock}
    """

    # ------------------------------------------------------------ оператор
    def operator_context(self, user_id):
        """Кто звонит: отдел, внутренний номер, включён ли режим. DialListError, если нельзя."""
        operator = self.db.get_sip_operator(int(user_id))
        if not operator:
            raise DialListError("Сотрудник не найден", 404)
        if (operator.get("department_provider") or "asterisk") != "binotel":
            raise DialListError("Обзвон из телефона работает только у отделов на Binotel", 409)
        department_id = operator.get("department_id")
        if department_id is None:
            raise DialListError("У сотрудника не указан отдел", 409)
        settings = self.department_settings(department_id)
        enabled = resolve_enabled(self.user_setting(user_id), settings["enabled"] if settings["configured"] else None)
        if not enabled:
            raise DialListError("Обзвон из телефона для вас не включён", 403)
        internal_number = str(operator.get("sip_number") or "").strip()
        if not internal_number:
            raise DialListError("У сотрудника не указан внутренний номер Binotel (SIP-номер)", 409)
        return {
            "user_id": int(user_id),
            "name": operator.get("name") or "",
            "department_id": int(department_id),
            "internal_number": internal_number,
            "settings": settings,
        }

    def _open_portion(self, cur, user_id):
        cur.execute("""
            SELECT id, size, issued_at FROM dial_list_portions
            WHERE operator_id = %s AND closed_at IS NULL
            ORDER BY issued_at DESC LIMIT 1
        """, (int(user_id),))
        row = cur.fetchone()
        return {"id": str(row[0]), "size": int(row[1]), "issued_at": row[2], "closed_at": None} if row else None

    def _last_portion(self, cur, user_id):
        """Последняя выдача оператора, открытая или уже закрытая. Закрытую телефон
        показывает целиком с итогами, пока оператор не возьмёт следующую: иначе после
        последней строки список исчезал, и итоги пачки никто не видел (владелец, 24.09.2026)."""
        cur.execute("""
            SELECT id, size, issued_at, closed_at FROM dial_list_portions
            WHERE operator_id = %s
            ORDER BY closed_at IS NULL DESC, issued_at DESC LIMIT 1
        """, (int(user_id),))
        row = cur.fetchone()
        return {"id": str(row[0]), "size": int(row[1]), "issued_at": row[2], "closed_at": row[3]} if row else None

    def _portion_items(self, cur, portion_id):
        cur.execute("""
            SELECT a.id, a.position, l.full_name, a.state, a.result, a.attempts, a.done_at,
                   t.id, t.state, t.disposition, t.requested_at, t.general_call_id,
                   o.name, o.color, t.cancelled, t.operator_hangup_at, a.lead_id
            FROM dial_list_assignments a
            JOIN dial_list_leads l ON l.id = a.lead_id
            LEFT JOIN LATERAL (
                SELECT id, state, disposition, requested_at, general_call_id, outcome_id,
                       cancelled, operator_hangup_at
                FROM dial_list_attempts WHERE assignment_id = a.id
                ORDER BY requested_at DESC LIMIT 1
            ) t ON TRUE
            LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
            WHERE a.portion_id = %s
            ORDER BY a.position
        """, (portion_id,))
        items = []
        for r in cur.fetchall():
            items.append({
                "assignment_id": str(r[0]),
                # По нему телефон узнаёт того же водителя на вкладке итога (не номер).
                "lead_id": str(r[16]),
                "position": int(r[1]),
                "full_name": r[2] or "Без имени",
                "state": r[3],
                "result": r[4] or "",
                "attempts": int(r[5] or 0),
                "done_at": _iso(r[6]),
                "last_attempt": None if r[7] is None else {
                    "attempt_id": str(r[7]), "state": r[8], "disposition": r[9] or "",
                    "requested_at": _iso(r[10]), "general_call_id": r[11],
                    "outcome_name": r[12] or "", "outcome_color": r[13] or "",
                    # Оператор сам завершил звонок; cancelled — до ответа водителя,
                    # попытка не засчитана и строка осталась в работе.
                    "cancelled": bool(r[14]), "operator_hangup": r[15] is not None,
                },
            })
        return items

    def _active_attempt(self, cur, user_id):
        cur.execute("""
            SELECT t.id, t.assignment_id, t.state, t.general_call_id, t.requested_at, l.full_name
            FROM dial_list_attempts t
            JOIN dial_list_assignments a ON a.id = t.assignment_id
            JOIN dial_list_leads l ON l.id = a.lead_id
            WHERE t.operator_id = %s
              AND t.state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')
              AND t.requested_at > CURRENT_TIMESTAMP - make_interval(mins => %s)
            ORDER BY t.requested_at DESC LIMIT 1
        """, (int(user_id), ACTIVE_ATTEMPT_WINDOW_MINUTES))
        r = cur.fetchone()
        if not r:
            return None
        return {"attempt_id": str(r[0]), "assignment_id": str(r[1]), "state": r[2],
                "general_call_id": r[3], "requested_at": _iso(r[4]), "full_name": r[5] or ""}

    def get_state(self, user_id):
        ctx = self.operator_context(user_id)
        self.reconcile(ctx)
        settings = ctx["settings"]
        with self.db._get_cursor() as cur:
            # Открытая выдача либо последняя закрытая: обработанная пачка остаётся на
            # экране с итогами, пока оператор не возьмёт следующую («Ещё»).
            portion = self._last_portion(cur, user_id)
            items = self._portion_items(cur, portion["id"]) if portion else []
            active = self._active_attempt(cur, user_id)
            cur.execute(self._POOL_SQL.format(lock="").replace("LIMIT %s", ""), (
                ctx["department_id"], settings["_period"], settings["retry_after_hours"], settings["max_attempts"]))
            pool = len(cur.fetchall())
            outcomes = self._outcome_rows(cur, ctx["department_id"], active_only=True)
            pending_outcome = self._pending_outcome(cur, ctx["user_id"]) if outcomes else None
            # Версия скрипта отдела: телефон перечитывает скрипт, когда она меняется.
            cur.execute("SELECT version FROM dial_list_scripts WHERE department_id = %s", (ctx["department_id"],))
            sv = cur.fetchone()
            script_version = int(sv[0]) if sv else 0
        pending = sum(1 for i in items if i["state"] == "issued")
        return {
            "enabled": True,
            "operator": {"id": ctx["user_id"], "name": ctx["name"], "internal_number": ctx["internal_number"]},
            "portion_size": settings["portion_size"],
            "leg_timeout_sec": LEG_TIMEOUT_SEC,
            "period": settings["period"],
            "period_label": settings["period_label"],
            "portion": None if not portion else {
                "id": portion["id"], "size": portion["size"], "issued_at": _iso(portion["issued_at"]),
                "pending": pending, "items": items,
                "closed": portion["closed_at"] is not None, "closed_at": _iso(portion["closed_at"]),
            },
            "can_request_next": pending == 0 and pool > 0 and pending_outcome is None,
            "pool_available": pool,
            "active_attempt": active,
            # Справочник итогов для окна после разговора и попытка, по которой итог
            # ещё не указан (телефон не даст звонить дальше, сервер — тоже).
            "outcomes": [{"id": o["id"], "name": o["name"], "color": o["color"], "requeue": o["requeue"]}
                         for o in outcomes],
            "pending_outcome": pending_outcome,
            "script_version": script_version,
        }

    # ------------------------------------------------------------ скрипт разговора
    def _script_rows(self, cur, department_id, active_only=False):
        cur.execute("""
            SELECT body, version, updated_at FROM dial_list_scripts WHERE department_id = %s
        """, (int(department_id),))
        s = cur.fetchone()
        cur.execute("""
            SELECT id, position, question, answer, is_active
            FROM dial_list_script_questions
            WHERE department_id = %s {active}
            ORDER BY is_active DESC, position, created_at
        """.format(active="AND is_active" if active_only else ""), (int(department_id),))
        questions = [{"id": _sid(r[0]), "position": int(r[1]), "question": r[2], "answer": r[3] or "",
                      "is_active": bool(r[4])} for r in cur.fetchall()]
        return {
            "body": (s[0] if s else "") or "",
            "version": int(s[1]) if s else 0,
            "updated_at": _iso(s[2]) if s else None,
            "questions": questions,
        }

    def script(self, department_id):
        """Скрипт отдела для редактора руководителя (включая выключенные вопросы)."""
        with self.db._get_cursor() as cur:
            return self._script_rows(cur, department_id)

    def save_script(self, department_id, payload, changed_by=None):
        """Полный скрипт из редактора: текст + список вопросов (с id — обновить, без id —
        добавить, отсутствующие — выключить, не удалить). Версия растёт при каждом
        сохранении — телефон перечитывает скрипт по ней."""
        department_id = int(department_id)
        if not isinstance(payload, dict):
            raise DialListError("Ожидается объект {body, questions}")
        body = str(payload.get("body") or "").replace("\r\n", "\n").strip()
        if len(body) > SCRIPT_BODY_MAX:
            raise DialListError(f"Текст скрипта не длиннее {SCRIPT_BODY_MAX} символов")
        items = payload.get("questions")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise DialListError("questions: ожидается список вопросов")
        if len(items) > SCRIPT_QUESTIONS_MAX:
            raise DialListError(f"Вопросов не больше {SCRIPT_QUESTIONS_MAX}")
        cleaned = []
        for raw in items:
            if not isinstance(raw, dict):
                raise DialListError("Каждый вопрос — объект {id?, question, answer, is_active}")
            question = str(raw.get("question") or "").strip()[:SCRIPT_QUESTION_MAX]
            answer = str(raw.get("answer") or "").replace("\r\n", "\n").strip()
            if not question:
                raise DialListError("У вопроса должен быть текст")
            if len(answer) > SCRIPT_ANSWER_MAX:
                raise DialListError(f"Ответ «{question[:40]}» длиннее {SCRIPT_ANSWER_MAX} символов")
            cleaned.append({"id": _sid(raw.get("id")) or None, "question": question, "answer": answer,
                            "is_active": raw.get("is_active", True) is not False})
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_scripts (department_id, body, version, updated_by, updated_at)
                VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (department_id) DO UPDATE
                SET body = EXCLUDED.body, version = dial_list_scripts.version + 1,
                    updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
            """, (department_id, body, changed_by))
            cur.execute("SELECT id FROM dial_list_script_questions WHERE department_id = %s", (department_id,))
            existing = {str(r[0]) for r in cur.fetchall()}
            kept = set()
            for position, q in enumerate(cleaned, start=1):
                if q["id"] and q["id"] in existing:
                    cur.execute("""
                        UPDATE dial_list_script_questions
                        SET question = %s, answer = %s, position = %s, is_active = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND department_id = %s
                    """, (q["question"], q["answer"], position, q["is_active"], q["id"], department_id))
                    kept.add(q["id"])
                else:
                    cur.execute("""
                        INSERT INTO dial_list_script_questions (department_id, position, question, answer, is_active)
                        VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """, (department_id, position, q["question"], q["answer"], q["is_active"]))
                    kept.add(str(cur.fetchone()[0]))
            gone = existing - kept
            if gone:
                cur.execute("""
                    UPDATE dial_list_script_questions SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE department_id = %s AND id = ANY(%s::uuid[])
                """, (department_id, list(gone)))
            result = self._script_rows(cur, department_id)
        log.info("dial_list: скрипт отдела %s сохранён (версия %s, вопросов %d, пользователь %s)",
                 department_id, result["version"], len(cleaned), changed_by)
        return result

    def operator_script(self, user_id):
        """Скрипт для телефона: текст и только включённые вопросы. Без ФИО и номеров."""
        ctx = self.operator_context(user_id)
        with self.db._get_cursor() as cur:
            data = self._script_rows(cur, ctx["department_id"], active_only=True)
        return {"body": data["body"], "version": data["version"], "updated_at": data["updated_at"],
                "questions": [{"id": q["id"], "question": q["question"], "answer": q["answer"]}
                              for q in data["questions"]]}

    def script_ai(self, department_id, payload):
        """ИИ в редакторе скрипта: mode=generate — скрипт и вопросы по описанию кампании
        (brief); mode=polish — оформить текст (text; target=body|answer) по нашей разметке.
        Ничего не сохраняет: результат руководитель смотрит и применяет сам."""
        from . import ai as script_ai
        payload = payload if isinstance(payload, dict) else {}
        mode = str(payload.get("mode") or "").strip().lower()
        try:
            if mode == "generate":
                result = script_ai.generate_script(payload.get("brief"))
                log.info("dial_list: ИИ собрал скрипт для отдела %s (%d симв., вопросов %d)",
                         department_id, len(result["body"]), len(result["questions"]))
                return {"mode": mode, **result}
            if mode == "polish":
                what = "ответ оператора на вопрос клиента" if payload.get("target") == "answer" else "скрипт разговора"
                text = script_ai.polish_text(payload.get("text"), what=what)
                log.info("dial_list: ИИ оформил %s для отдела %s (%d симв.)", what, department_id, len(text))
                return {"mode": mode, "text": text}
        except script_ai.ScriptAIError as exc:
            raise DialListError(str(exc), exc.status)
        raise DialListError("mode: generate или polish")

    # Счётчики оператора для вкладки «Мой прогресс»: за месяц (первый день — %(month)s)
    # и за сегодня (%(today)s), дни — по Алматы. Отменённые оператором попытки в
    # попытки не входят (они не в счёт). Только числа: ни ФИО, ни номеров.
    _PROGRESS_SQL = """
        WITH att AS (
            SELECT t.state, UPPER(t.disposition) AS disp, t.billsec, t.cancelled,
                   (t.requested_at AT TIME ZONE 'Asia/Almaty')::date AS day
            FROM dial_list_attempts t
            WHERE t.operator_id = %(operator_id)s
              AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
        ), asg AS (
            SELECT a.state,
                   (a.created_at AT TIME ZONE 'Asia/Almaty')::date AS day,
                   (a.done_at AT TIME ZONE 'Asia/Almaty')::date AS done_day
            FROM dial_list_assignments a
            WHERE a.operator_id = %(operator_id)s
              AND a.created_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
        )
        SELECT
            (SELECT COUNT(*) FROM asg)                                                    AS issued,
            (SELECT COUNT(*) FROM asg WHERE state = 'done')                               AS done,
            (SELECT COUNT(*) FROM asg WHERE state = 'done' AND done_day = %(today)s)      AS done_today,
            (SELECT COUNT(*) FROM att WHERE NOT cancelled)                                AS attempts,
            (SELECT COUNT(*) FROM att WHERE NOT cancelled AND day = %(today)s)            AS attempts_today,
            (SELECT COUNT(*) FROM att WHERE state = 'finished' AND disp IN %(answered)s)  AS answered,
            (SELECT COUNT(*) FROM att WHERE state = 'finished' AND disp IN %(answered)s
                                        AND day = %(today)s)                              AS answered_today,
            (SELECT COALESCE(SUM(billsec), 0) FROM att WHERE state = 'finished')          AS talk_sec,
            (SELECT COALESCE(SUM(billsec), 0) FROM att WHERE state = 'finished'
                                                          AND day = %(today)s)            AS talk_sec_today,
            (SELECT COUNT(*) FROM att WHERE cancelled)                                    AS cancelled
    """
    _PROGRESS_OUTCOMES_SQL = """
        SELECT o.id, o.name, o.color, COUNT(*) AS cnt,
               COUNT(*) FILTER (WHERE (t.requested_at AT TIME ZONE 'Asia/Almaty')::date = %(today)s) AS cnt_today
        FROM dial_list_attempts t
        JOIN dial_list_outcomes o ON o.id = t.outcome_id
        WHERE t.operator_id = %(operator_id)s
          AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
        GROUP BY o.id, o.name, o.color, o.position
        ORDER BY cnt DESC, o.position, o.name
    """

    def operator_progress(self, user_id):
        """«Мой прогресс» на телефоне: сколько строк выдано и обработано, попыток,
        дозвонов, минут разговора и какие итоги ставил оператор — за текущий месяц
        (по Алматы) и отдельно за сегодня. Никаких ФИО и номеров — только счётчики."""
        ctx = self.operator_context(user_id)
        month = current_period()
        today = datetime.now(PERIOD_TZ).date()
        params = {"operator_id": ctx["user_id"], "month": month, "today": today, "answered": ANSWERED_SQL}
        with self.db._get_cursor() as cur:
            cur.execute(self._PROGRESS_SQL, params)
            r = cur.fetchone() or (0,) * 10
            cur.execute(self._PROGRESS_OUTCOMES_SQL, params)
            outcomes = [{"id": _sid(o[0]), "name": o[1], "color": o[2] or "#8E8E93",
                         "count": int(o[3] or 0), "count_today": int(o[4] or 0)} for o in cur.fetchall()]
        return {
            "operator": {"id": ctx["user_id"], "name": ctx["name"]},
            "period": month.isoformat(),
            "period_label": period_label(month),
            "today": today.isoformat(),
            "month": {
                "issued": int(r[0] or 0), "done": int(r[1] or 0), "attempts": int(r[3] or 0),
                "answered": int(r[5] or 0), "talk_sec": int(r[7] or 0), "cancelled": int(r[9] or 0),
            },
            "today_stats": {
                "done": int(r[2] or 0), "attempts": int(r[4] or 0),
                "answered": int(r[6] or 0), "talk_sec": int(r[8] or 0),
            },
            "outcomes": outcomes,
        }

    # Отработанные за месяц: по каждому водителю — ПОСЛЕДНЯЯ строка этого оператора
    # (пере-выданный водитель уходит туда, куда его отнесла последняя обработка) и её
    # последняя попытка. Месяц — строки, выданные или обработанные с 1-го числа, плюс
    # открытые: пачка живёт до «Ещё», и выданная 30-го, а обработанная 1-го строка иначе
    # ушла бы из очереди телефона, не появившись ни на одной вкладке.
    _WORKED_SQL = """
        WITH latest AS (
            SELECT DISTINCT ON (a.lead_id)
                   a.id, a.lead_id, a.state, a.result, a.done_at, a.created_at
            FROM dial_list_assignments a
            WHERE a.operator_id = %(operator_id)s
              AND (a.created_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
                   OR a.done_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
                   OR a.state = 'issued')
            ORDER BY a.lead_id, a.created_at DESC
        )
        SELECT s.id, s.lead_id, l.full_name, s.state, s.result, s.done_at, s.created_at,
               t.outcome_id, t.outcome_at, t.operator_comment, t.state,
               o.name, o.color, o.position, l.status
        FROM latest s
        JOIN dial_list_leads l ON l.id = s.lead_id
        LEFT JOIN LATERAL (
            SELECT outcome_id, outcome_at, operator_comment, state
            FROM dial_list_attempts WHERE assignment_id = s.id
            ORDER BY requested_at DESC LIMIT 1
        ) t ON TRUE
        LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
    """

    def operator_worked(self, user_id):
        """Вкладки итогов на телефоне: кого оператор отработал за месяц и с каким итогом.

        Вкладка на каждый включённый итог отдела (в порядке справочника, даже пустая —
        набор вкладок не прыгает), выключенный — только если по нему кто-то есть, и
        последней «Не дозвонились». Только ФИО, время и свой комментарий — номера нет.
        """
        ctx = self.operator_context(user_id)
        month = current_period()
        today = datetime.now(PERIOD_TZ).date()
        with self.db._get_cursor() as cur:
            outcomes = self._outcome_rows(cur, ctx["department_id"], active_only=True)
            cur.execute(self._WORKED_SQL, {"operator_id": ctx["user_id"], "month": month})
            rows = cur.fetchall()
        tabs = {o["id"]: {"key": o["id"], "name": o["name"], "color": o["color"], "position": o["position"],
                          "count": 0} for o in outcomes}
        extra = {}
        items = []
        for r in rows:
            tab = worked_tab(r[3], r[4], r[7], r[10])
            if tab is None:
                continue
            if tab == WORKED_NO_ANSWER and r[4] == "other" and r[14] == "excluded":
                # Строку закрыл руководитель («Исключить»), а не АТС: это не недозвон.
                continue
            if tab != WORKED_NO_ANSWER and tab not in tabs:
                # Итог выключили уже после звонка — вкладка живёт, пока по ней кто-то есть.
                extra.setdefault(tab, {"key": tab, "name": r[11] or "Итог", "color": r[12] or WORKED_NO_ANSWER_COLOR,
                                       "position": int(r[13] or 0), "count": 0})
            at = (r[8] or r[5] or r[6]) if tab != WORKED_NO_ANSWER else (r[5] or r[6])
            items.append({
                "assignment_id": str(r[0]), "lead_id": str(r[1]),
                "full_name": r[2] or "Без имени",
                "tab": tab,
                "result": r[4] or "",
                "comment": (r[9] or "") if tab != WORKED_NO_ANSWER else "",
                "at": _iso(at), "at_label": at_label(at, today), "_sort": at,
            })
        no_answer = {"key": WORKED_NO_ANSWER, "name": WORKED_NO_ANSWER_NAME, "color": WORKED_NO_ANSWER_COLOR,
                     "count": 0}
        ordered = list(tabs.values()) + sorted(extra.values(), key=lambda t: (t["position"], t["name"])) + [no_answer]
        by_key = {t["key"]: t for t in ordered}
        # Свежие сверху: только что отработанный водитель — первая строка своей вкладки.
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda i: i["_sort"] or oldest, reverse=True)
        shown = []
        for item in items:
            del item["_sort"]
            tab = by_key[item["tab"]]
            tab["count"] += 1
            if tab["count"] <= WORKED_TAB_LIMIT:
                shown.append(item)
        return {
            "period": month.isoformat(),
            "period_label": period_label(month),
            "limit": WORKED_TAB_LIMIT,
            "tabs": [{"key": t["key"], "name": t["name"], "color": t["color"], "count": t["count"]} for t in ordered],
            "items": shown,
        }

    def issue_next_portion(self, user_id):
        ctx = self.operator_context(user_id)
        settings = ctx["settings"]
        with self.db._get_cursor() as cur:
            portion = self._open_portion(cur, user_id)
            if portion:
                cur.execute("""
                    SELECT COUNT(*) FROM dial_list_assignments WHERE portion_id = %s AND state = 'issued'
                """, (portion["id"],))
                pending = int(cur.fetchone()[0])
                if pending:
                    raise DialListError(
                        f"Сначала обработайте текущий список: осталось {pending}", 409)
                # Все строки закрыты, а выдача почему-то открыта — закрываем здесь.
                cur.execute("UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP WHERE id = %s",
                            (portion["id"],))
            self._require_outcome_done(cur, ctx)
            cur.execute(self._POOL_SQL.format(lock="FOR UPDATE SKIP LOCKED"), (
                ctx["department_id"], settings["_period"], settings["retry_after_hours"], settings["max_attempts"],
                settings["portion_size"]))
            lead_ids = [str(r[0]) for r in cur.fetchall()]
            if not lead_ids:
                raise DialListError("Список пуст: водителей для обзвона сейчас нет", 404)
            cur.execute("""
                INSERT INTO dial_list_portions (operator_id, department_id, size)
                VALUES (%s, %s, %s) RETURNING id
            """, (ctx["user_id"], ctx["department_id"], len(lead_ids)))
            portion_id = str(cur.fetchone()[0])
            for position, lead_id in enumerate(lead_ids, start=1):
                cur.execute("""
                    INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position)
                    VALUES (%s, %s, %s, %s)
                """, (portion_id, lead_id, ctx["user_id"], position))
        log.info("dial_list: оператору %s выдано %d строк (порция %s)", ctx["user_id"], len(lead_ids), portion_id)
        return self.get_state(user_id)

    def start_call(self, user_id, assignment_id):
        """Клик «Позвонить»: попытка + звонок через Binotel. Возвращает попытку."""
        ctx = self.operator_context(user_id)
        settings = ctx["settings"]
        assignment_id = str(assignment_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT a.id, a.state, a.attempts, l.phone_norm, l.full_name, l.id
                FROM dial_list_assignments a
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE a.id = %s AND a.operator_id = %s
                FOR UPDATE OF a
            """, (assignment_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Строка списка не найдена", 404)
            if row[1] != "issued":
                raise DialListError("Эта строка уже обработана", 409)
            active = self._active_attempt(cur, user_id)
            if active:
                raise DialListError(
                    f"Идёт звонок ({active['full_name']}). Дождитесь его завершения", 409)
            self._require_outcome_done(cur, ctx)
            phone_norm, full_name, lead_id = row[3], row[4] or "", str(row[5])
            cur.execute("""
                INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number)
                VALUES (%s, %s, %s) RETURNING id, requested_at
            """, (assignment_id, ctx["user_id"], ctx["internal_number"]))
            attempt_id, requested_at = cur.fetchone()
            attempt_id = str(attempt_id)
            cur.execute("UPDATE dial_list_assignments SET attempts = attempts + 1 WHERE id = %s", (assignment_id,))
            attempts_now = int(row[2] or 0) + 1

        # Что линия оператора увидит во From. Без подмены Binotel ставит туда номер
        # водителя — и он виден в SIP-пакете (Wireshark), что и было главным
        # требованием закрыть. Проверено живьём 23.09.2026: callerIdForEmployee
        # подменяет From ровно на переданное значение. Поле отдела пусто — ставим
        # внутренний номер самого оператора: нейтрально и всегда есть.
        caller_id = (settings.get("caller_id_for_employee") or "").strip() or ctx["internal_number"]
        extra = {"callerIdForEmployee": caller_id}
        try:
            client = self._client(ctx["department_id"])
            general_call_id = client.originate_internal_to_external(
                ctx["internal_number"], phone_norm, **extra)
        except DialListError:
            self._fail_attempt(attempt_id, assignment_id, lead_id, "подключение к Binotel не настроено",
                               attempts_now, settings["max_attempts"])
            raise
        except Exception as exc:  # отказ АТС или сеть
            message = str(exc)[:500]
            lowered = message.lower()
            # Код 150 «Can't call to the ext» — АТС не достучалась до ЛИНИИ оператора:
            # телефон не зарегистрирован (неверный сервер/пароль, нет сети). Это не
            # вина водителя — попытку по лиду не считаем и говорим оператору, что делать.
            ext_unreachable = "code=150" in lowered or "call to the ext" in lowered
            self._fail_attempt(attempt_id, assignment_id, lead_id, message,
                               attempts_now, settings["max_attempts"],
                               count_for_lead=not ext_unreachable)
            log.warning("dial_list: Binotel отказал оператору %s: %s", ctx["user_id"], message)
            if ext_unreachable:
                hint = self._line_status_hint(ctx["department_id"], ctx["internal_number"])
                raise DialListError(
                    f"Линия {ctx['internal_number']} не на связи: АТС не смогла до неё дозвониться. {hint}", 409)
            status = 429 if "too frequent" in lowered or "часто" in lowered else 502
            raise DialListError(f"АТС не приняла звонок: {message}", status)

        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts SET general_call_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (general_call_id[:32], attempt_id))
        log.info("dial_list: оператор %s → строка %s, generalCallID=%s", ctx["user_id"], assignment_id, general_call_id)
        return {
            "attempt_id": attempt_id,
            "assignment_id": assignment_id,
            "general_call_id": general_call_id,
            "full_name": full_name,
            "requested_at": _iso(requested_at),
            "expect_leg_within_sec": LEG_TIMEOUT_SEC,
        }

    def _fail_attempt(self, attempt_id, assignment_id, lead_id, message, attempts_now, max_attempts,
                      count_for_lead=True):
        """АТС отказала: попытка failed. Строка остаётся в работе, пока есть попытки —
        оператор может нажать ещё раз; исчерпали — закрываем как failed.
        count_for_lead=False — сбой на нашей стороне (линия оператора не на связи):
        счётчик попыток строки откатываем, водителю это в минус не идёт."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = 'failed', api_error = %s, final_source = 'api_error',
                    finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (message, attempt_id))
            if not count_for_lead:
                cur.execute("UPDATE dial_list_assignments SET attempts = GREATEST(attempts - 1, 0) WHERE id = %s",
                            (assignment_id,))
                return
            if attempts_now >= int(max_attempts):
                self._close_assignment(cur, assignment_id, lead_id, "failed", answered=False, count_attempt=True)

    # ------------------------------------------------------------ исходы
    def phone_event(self, user_id, attempt_id, event, at=None, by_operator=False, leg_sec=0):
        """Подсказка телефона: плечо пришло / принято / разговор кончился / плеча не было /
        оператор сам нажал «Завершить».

        В ответе телефон получает решение по итогу: `outcome_required` — true (открывать
        окно итога), false (итог не нужен: попытка отменена или разговора не было),
        None (исход АТС ещё не известен — телефон ждёт `pending_outcome` из состояния);
        `cancelled` — попытка отменена и не засчитана."""
        event = str(event or "").strip().lower()
        if event not in PHONE_EVENTS:
            raise DialListError(f"Неизвестное событие: {event!r}")
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        by_operator = bool(by_operator) or event == "operator_hangup"
        leg_sec = max(0, min(_to_int(leg_sec, 0), 24 * 3600))
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT state, general_call_id, assignment_id, cancelled, disposition FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s FOR UPDATE
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            state, general_call_id = row[0], row[1]
            if by_operator:
                # Отбой оператора запоминаем всегда — даже по уже закрытой попытке:
                # вебхук Binotel мог обогнать телефон и досчитать попытку как «не
                # ответил». Тогда её надо откатить здесь же (см. _cancel_attempt).
                cur.execute("""
                    UPDATE dial_list_attempts
                    SET operator_hangup_at = COALESCE(operator_hangup_at, CURRENT_TIMESTAMP),
                        leg_sec = CASE WHEN %s > 0 THEN %s ELSE leg_sec END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (leg_sec, leg_sec, attempt_id))
                if state == "finished" and not row[3] and map_disposition(row[4]) not in ("", "answered"):
                    self._cancel_attempt(cur, attempt_id, str(row[2]), late=True)
            if state in ("finished", "failed"):
                cancelled, required = self._outcome_decision(cur, attempt_id)
                return {"attempt_id": attempt_id, "state": state, "final": True,
                        "cancelled": cancelled, "outcome_required": required}
            new_state = {
                "ringing": "leg_ringing", "answered": "leg_answered",
                "ended": "ended", "no_leg": state, "operator_hangup": state,
            }[event]
            # Назад по цепочке не ходим: «ringing» после «answered» ничего не значит.
            order = ["requested", "leg_ringing", "leg_answered", "ended"]
            if order.index(new_state) < order.index(state):
                new_state = state
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = %s, phone_event_at = CURRENT_TIMESTAMP,
                    phone_ended_at = CASE WHEN %s = 'ended' THEN CURRENT_TIMESTAMP ELSE phone_ended_at END,
                    leg_answered_at = CASE WHEN %s = 'answered'
                                           THEN COALESCE(leg_answered_at, CURRENT_TIMESTAMP)
                                           ELSE leg_answered_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_state, event, event, attempt_id))
        result = {"attempt_id": attempt_id, "state": new_state, "final": False}
        # Разговор кончился — сразу спрашиваем Binotel, чтобы строка закрылась
        # без ожидания вебхука. Не вышло — доберёт reconcile на следующем запросе.
        if event in ("ended", "no_leg") and general_call_id:
            try:
                details = self._client(ctx["department_id"]).call_details(general_call_id)
            except Exception as exc:
                log.info("dial_list: call-details после события телефона не удались: %s", exc)
                details = {}
            call = details.get(str(general_call_id))
            if call and map_disposition(call.get("disposition")):
                self._finish_by_call(attempt_id, call, "poll")
                result.update({"state": "finished", "final": True, "disposition": call.get("disposition")})
        with self.db._get_cursor() as cur:
            cancelled, required = self._outcome_decision(cur, attempt_id)
        result.update({"cancelled": cancelled, "outcome_required": required})
        return result

    # Живое состояние попытки для карточки звонка. Телефон принимает плечо от АТС
    # сразу, а водителя АТС набирает после — «разговор» на карточке появлялся, когда
    # его ещё не было (владелец, 25.09.2026). Знает про ответ водителя только Binotel:
    # call-details отдаёт CALLING (набор) → ONLINE (разговор) → финал. Кэш на попытку
    # с LIVE_MIN_INTERVAL_SEC, чтобы опрос телефона каждые 3 с не превращался в
    # столько же запросов к АТС.
    _live_cache = {}

    def attempt_live(self, user_id, attempt_id):
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT state, general_call_id, disposition, cancelled FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        state, general_call_id, disposition, cancelled = row
        if state in ("finished", "failed"):
            result = map_disposition(disposition) or ("failed" if state == "failed" else "other")
            return {"attempt_id": attempt_id, "state": state, "disposition": disposition or "",
                    "talking": False, "final": True, "result": result, "cancelled": bool(cancelled)}
        live = ""
        call = None
        if general_call_id:
            now = time.monotonic()
            cached = self._live_cache.get(attempt_id)
            if cached and now - cached[0] < LIVE_MIN_INTERVAL_SEC:
                live = cached[1]
            else:
                try:
                    details = self._client(ctx["department_id"]).call_details(general_call_id)
                    call = details.get(str(general_call_id)) or None
                    live = str((call or {}).get("disposition") or "").strip().upper()
                except Exception as exc:
                    log.info("dial_list: live call-details не удались: %s", exc)
                    live = cached[1] if cached else ""
                    call = None
                if len(self._live_cache) > 500:
                    for key in [k for k, v in self._live_cache.items() if now - v[0] > 600]:
                        self._live_cache.pop(key, None)
                self._live_cache[attempt_id] = (now, live)
        final = bool(map_disposition(live))
        if final and call:
            # Финал уже известен — закрываем попытку здесь же, как делает phone_event.
            self._finish_by_call(attempt_id, call, "poll")
            self._live_cache.pop(attempt_id, None)
        return {
            "attempt_id": attempt_id,
            "state": "finished" if final else state,
            "disposition": live,
            "talking": live == "ONLINE",
            "final": final,
            "result": map_disposition(live) if final else "",
            "cancelled": False,
        }

    def reconcile(self, ctx_or_user_id, limit=POLL_MAX_PER_REQUEST):
        """Добрать исходы незавершённых попыток оператора через call-details.

        Зовётся на каждом GET состояния: дёшево, когда добирать нечего (один
        SELECT), и не требует планировщика. Старые попытки без исхода
        закрываются как «неизвестно», чтобы не держать строку вечно."""
        ctx = ctx_or_user_id if isinstance(ctx_or_user_id, dict) else self.operator_context(ctx_or_user_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT id, general_call_id, requested_at FROM dial_list_attempts
                WHERE operator_id = %s
                  AND state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')
                  AND requested_at < CURRENT_TIMESTAMP - make_interval(secs => %s)
                ORDER BY requested_at ASC LIMIT %s
            """, (ctx["user_id"], POLL_MIN_AGE_SEC, int(limit)))
            pending = [(str(r[0]), r[1], r[2]) for r in cur.fetchall()]
        if not pending:
            return 0
        ids = [gid for _aid, gid, _at in pending if gid]
        details = {}
        if ids:
            try:
                details = self._client(ctx["department_id"]).call_details(ids)
            except Exception as exc:
                log.info("dial_list: reconcile call-details не удались: %s", exc)
        finished = 0
        stale_before = _utcnow() - timedelta(minutes=ATTEMPT_STALE_MINUTES)
        for attempt_id, gid, requested_at in pending:
            call = details.get(str(gid)) if gid else None
            if call and map_disposition(call.get("disposition")):
                self._finish_by_call(attempt_id, call, "poll")
                finished += 1
            elif requested_at is not None and requested_at < stale_before:
                self._finish_attempt(attempt_id, "UNKNOWN", 0, 0, "timeout")
                finished += 1
        return finished

    def _finish_by_call(self, attempt_id, call, source):
        self._finish_attempt(attempt_id, call.get("disposition") or "", call.get("billsec") or 0,
                             call.get("waitsec") or 0, source)

    def _finish_attempt(self, attempt_id, disposition, billsec, waitsec, source):
        disposition = str(disposition or "").strip().upper()[:32]
        result = map_disposition(disposition) or "other"
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = 'finished', disposition = %s, billsec = %s, waitsec = %s,
                    final_source = %s, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND state <> 'finished'
                RETURNING assignment_id
            """, (disposition, int(billsec or 0), int(waitsec or 0), source, str(attempt_id)))
            row = cur.fetchone()
            if not row:
                # Уже закрыта. Если закрыли без настоящего исхода (UNKNOWN по таймауту
                # или промежуточный статус), а теперь пришёл финальный — дописываем
                # его, чтобы журнал и запись разговора были правдой. Строку выдачи и
                # счётчик попыток второй раз не трогаем — только факт дозвона у лида.
                cur.execute("""
                    UPDATE dial_list_attempts
                    SET disposition = %s, billsec = %s, waitsec = %s, final_source = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND state = 'finished' AND UPPER(disposition) IN %s
                    RETURNING assignment_id
                """, (disposition, int(billsec or 0), int(waitsec or 0), source, str(attempt_id),
                      PROVISIONAL_DISPOSITIONS))
                late = cur.fetchone()
                if late and result == "answered":
                    cur.execute("SELECT cancelled FROM dial_list_attempts WHERE id = %s", (str(attempt_id),))
                    was_cancelled = cur.fetchone()
                    if was_cancelled and was_cancelled[0]:
                        # Попытку отменили по провизорному исходу (таймаут UNKNOWN), а
                        # теперь Binotel говорит: водитель ответил. Разговор был — итог
                        # обязателен, попытка снова в счёт.
                        self._uncancel_attempt(cur, str(attempt_id), str(late[0]))
                        return True
                    cur.execute("""
                        UPDATE dial_list_leads l
                        SET answered_at = COALESCE(l.answered_at, CURRENT_TIMESTAMP),
                            status = CASE WHEN l.status IN ('new', 'in_progress') THEN 'done' ELSE l.status END,
                            updated_at = CURRENT_TIMESTAMP
                        FROM dial_list_assignments a
                        WHERE a.id = %s AND l.id = a.lead_id
                    """, (str(late[0]),))
                return bool(late)
            assignment_id = str(row[0])
            # Оператор сам положил трубку, а водитель так и не ответил: попытка
            # отменяется — не считается ни строке, ни лиду, строка остаётся в работе.
            # (Решение владельца 24.09.2026: «повесил трубку до ответа — не засчитывать».)
            cur.execute("SELECT operator_hangup_at FROM dial_list_attempts WHERE id = %s", (str(attempt_id),))
            hung = cur.fetchone()
            if hung and hung[0] is not None and result != "answered":
                self._cancel_attempt(cur, str(attempt_id), assignment_id, late=False)
                return True
            # Оператор мог успеть выбрать итог «перезвонить» раньше, чем приехал исход
            # от АТС: тогда лид не закрываем, а оставляем на повтор.
            cur.execute("""
                SELECT COALESCE(o.requeue, FALSE) FROM dial_list_attempts t
                LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id WHERE t.id = %s
            """, (str(attempt_id),))
            picked = cur.fetchone()
            requeue = bool(picked and picked[0])
            cur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (assignment_id,))
            a = cur.fetchone()
            if a and a[1] == "issued":
                self._close_assignment(cur, assignment_id, str(a[0]), result,
                                       answered=(result == "answered"), count_attempt=True, requeue=requeue)
            elif a:
                # Строка уже закрыта прошлой попыткой — лид всё равно учитывает звонок.
                self._touch_lead(cur, str(a[0]), answered=(result == "answered"), count_attempt=True,
                                 requeue=requeue)
        return True

    def _close_assignment(self, cur, assignment_id, lead_id, result, answered, count_attempt, requeue=False):
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'done', result = %s, done_at = CURRENT_TIMESTAMP
            WHERE id = %s RETURNING portion_id
        """, (result, assignment_id))
        row = cur.fetchone()
        self._touch_lead(cur, lead_id, answered=answered, count_attempt=count_attempt, requeue=requeue)
        if row:
            portion_id = str(row[0])
            cur.execute("""
                UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND closed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM dial_list_assignments
                                  WHERE portion_id = %s AND state = 'issued')
            """, (portion_id, portion_id))

    def _touch_lead(self, cur, lead_id, answered, count_attempt, requeue=False):
        cur.execute("""
            SELECT department_id, attempts_total FROM dial_list_leads WHERE id = %s FOR UPDATE
        """, (lead_id,))
        row = cur.fetchone()
        if not row:
            return
        cur.execute("""
            SELECT max_attempts FROM dial_list_department_settings WHERE department_id = %s
        """, (row[0],))
        s = cur.fetchone()
        max_attempts = int(s[0]) if s else DEFAULT_MAX_ATTEMPTS
        attempts_total = int(row[1] or 0) + (1 if count_attempt else 0)
        if requeue:
            # Итог «перезвонить»: попытки с нуля, лид вернётся в пул через retry_after_hours.
            status, attempts_total = "in_progress", 0
        elif answered:
            status = "done"
        elif attempts_total >= max_attempts:
            status = "done"
        else:
            status = "in_progress"
        cur.execute("""
            UPDATE dial_list_leads
            SET attempts_total = %s, last_attempt_at = CURRENT_TIMESTAMP,
                answered_at = CASE WHEN %s THEN COALESCE(answered_at, CURRENT_TIMESTAMP) ELSE answered_at END,
                status = CASE WHEN status = 'excluded' THEN status ELSE %s END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (attempts_total, bool(answered), status, lead_id))

    # ------------------------------------------------------------ отмена до ответа
    def _cancel_attempt(self, cur, attempt_id, assignment_id, late=False):
        """Оператор сам завершил звонок, водитель не ответил: попытка не в счёт.

        late=False — попытка закрывается сейчас (строка ещё `issued`, лид не трогали):
        достаточно снять её со счётчика строки. late=True — исход от Binotel обогнал
        событие телефона и попытку уже досчитали как «не ответил»: откатываем закрытие
        строки, повторное открытие выдачи и счётчик лида. Всё — в транзакции вызывающего."""
        cur.execute("""
            UPDATE dial_list_attempts SET cancelled = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND NOT cancelled RETURNING id
        """, (str(attempt_id),))
        if not cur.fetchone():
            return False
        cur.execute("UPDATE dial_list_assignments SET attempts = GREATEST(attempts - 1, 0) WHERE id = %s",
                    (str(assignment_id),))
        if late:
            cur.execute("""
                SELECT a.state, a.lead_id, a.portion_id,
                       (SELECT t.id FROM dial_list_attempts t WHERE t.assignment_id = a.id
                        ORDER BY t.requested_at DESC LIMIT 1)
                FROM dial_list_assignments a WHERE a.id = %s FOR UPDATE
            """, (str(assignment_id),))
            a = cur.fetchone()
            if a and a[0] == "done" and str(a[3]) == str(attempt_id):
                # Строку закрыла именно эта попытка. Вернуть её в работу можно, только
                # если лид не успел уйти в другую открытую выдачу (уникальный индекс).
                cur.execute("SELECT 1 FROM dial_list_assignments WHERE lead_id = %s AND state = 'issued'",
                            (str(a[1]),))
                if not cur.fetchone():
                    cur.execute("""
                        UPDATE dial_list_assignments SET state = 'issued', result = '', done_at = NULL
                        WHERE id = %s
                    """, (str(assignment_id),))
                    cur.execute("UPDATE dial_list_portions SET closed_at = NULL WHERE id = %s", (str(a[2]),))
                cur.execute("""
                    UPDATE dial_list_leads
                    SET attempts_total = GREATEST(attempts_total - 1, 0),
                        status = CASE WHEN status = 'done' AND answered_at IS NULL THEN 'in_progress' ELSE status END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (str(a[1]),))
        log.info("dial_list: попытка %s отменена оператором до ответа водителя (%s)",
                 attempt_id, "с откатом закрытия строки" if late else "строка осталась в работе")
        return True

    def _uncancel_attempt(self, cur, attempt_id, assignment_id):
        """Отменённую по провизорному исходу попытку Binotel позже признал отвеченной:
        разговор был, попытка снова в счёт, итог обязателен."""
        cur.execute("""
            UPDATE dial_list_attempts SET cancelled = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND cancelled RETURNING id
        """, (str(attempt_id),))
        if not cur.fetchone():
            return False
        cur.execute("UPDATE dial_list_assignments SET attempts = attempts + 1 WHERE id = %s", (str(assignment_id),))
        cur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (str(assignment_id),))
        a = cur.fetchone()
        if a and a[1] == "issued":
            self._close_assignment(cur, str(assignment_id), str(a[0]), "answered", answered=True, count_attempt=True)
        elif a:
            self._touch_lead(cur, str(a[0]), answered=True, count_attempt=True)
        log.info("dial_list: попытка %s снова в счёте — Binotel подтвердил ответ водителя", attempt_id)
        return True

    def _outcome_decision(self, cur, attempt_id):
        """(cancelled, outcome_required) по попытке — что телефону делать после разговора.
        outcome_required: True — открыть окно итога; False — итог не нужен; None — исход
        от АТС ещё не известен (оператор сам положил трубку, ждём Binotel)."""
        cur.execute("""
            SELECT state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id
            FROM dial_list_attempts WHERE id = %s
        """, (str(attempt_id),))
        r = cur.fetchone()
        if not r:
            return False, False
        state, disposition, cancelled, hung_up, leg_answered, outcome_id = r
        if cancelled:
            return True, False
        if outcome_id is not None or leg_answered is None or state == "failed":
            return False, False
        if hung_up is None:
            return False, True
        if state == "finished":
            return False, map_disposition(disposition) == "answered"
        return False, None

    # ------------------------------------------------------------ вебхук
    def webhook_allowed(self, token, remote_addr):
        """Вебхук принимаем только с правильным токеном из окружения сервера
        (DIAL_LIST_WEBHOOK_TOKEN, не короче 16 символов, сравнение постоянного
        времени) И — по умолчанию — только с адресов серверов Binotel. Токен не
        задан → вебхук выключен целиком: исходы доберёт опрос call-details."""
        expected = webhook_env_token()
        if len(expected) < WEBHOOK_TOKEN_MIN_LEN:
            log.warning("dial_list: вебхук отключён — %s не задан или короче %d символов",
                        WEBHOOK_TOKEN_ENV, WEBHOOK_TOKEN_MIN_LEN)
            return False
        presented = str(token or "").strip()
        if not presented or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
            return False
        if webhook_requires_binotel_ip() and str(remote_addr or "").strip() not in BINOTEL_SERVER_IPS:
            log.warning("dial_list: вебхук с правильным токеном, но с чужого адреса %s — отклонён "
                        "(снять проверку: %s=0)", remote_addr, WEBHOOK_REQUIRE_BINOTEL_IP_ENV)
            return False
        return True

    def handle_webhook(self, form, remote_addr=""):
        """POST «API Call Completed»: ключевые поля плоские (generalCallID, disposition,
        billsec, waitsec, internalNumber, externalNumber, callType, startTime)."""
        data = {}
        for key in (form or {}):
            try:
                value = form.get(key)
            except AttributeError:
                value = form[key]
            data[str(key)[:64]] = str(value)[:500] if value is not None else None
        general_call_id = str(data.get("generalCallID") or data.get("generalCallId") or "").strip()[:32]
        disposition = str(data.get("disposition") or "").strip().upper()
        billsec = _to_int(data.get("billsec"))
        waitsec = _to_int(data.get("waitsec"))
        matched = False
        if general_call_id:
            with self.db._get_cursor() as cur:
                cur.execute("SELECT id, state FROM dial_list_attempts WHERE general_call_id = %s", (general_call_id,))
                row = cur.fetchone()
            if row:
                matched = True
                # Уже закрытую попытку _finish_attempt дописывает только если она
                # закрылась без настоящего исхода (таймаут/промежуточный статус).
                if map_disposition(disposition):
                    self._finish_attempt(str(row[0]), disposition, billsec, waitsec, "webhook")
        payload = json.dumps(data, ensure_ascii=False)
        if len(payload) > WEBHOOK_PAYLOAD_MAX_CHARS:
            payload = json.dumps({"_truncated": True, "generalCallID": general_call_id,
                                  "disposition": disposition}, ensure_ascii=False)
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_webhook_log (remote_addr, general_call_id, matched, payload)
                VALUES (%s, %s, %s, %s::jsonb)
            """, (str(remote_addr or "")[:64], general_call_id, matched, payload))
            cur.execute("""
                DELETE FROM dial_list_webhook_log
                WHERE received_at < CURRENT_TIMESTAMP - make_interval(days => %s)
            """, (WEBHOOK_LOG_KEEP_DAYS,))
        return {"matched": matched, "general_call_id": general_call_id, "disposition": disposition}

    # ------------------------------------------------------------ руководитель
    def overview(self, department_ids, day):
        """Сводка за день по операторам: сколько строк выдано/обработано, попыток,
        дозвонов, секунд разговора. department_ids=None — все отделы."""
        params = [day, day]
        dept_filter = ""
        if department_ids is not None:
            dept_filter = "AND u.department_id = ANY(%s)"
            params.append([int(d) for d in department_ids])
        with self.db._get_cursor() as cur:
            cur.execute(f"""
                WITH att AS (
                    SELECT t.operator_id,
                           COUNT(*) AS attempts,
                           COUNT(*) FILTER (WHERE t.state = 'finished'
                                            AND UPPER(t.disposition) IN ('ANSWER','ANSWERED','SUCCESS','VM-SUCCESS')) AS answered,
                           COALESCE(SUM(t.billsec) FILTER (WHERE t.state = 'finished'), 0) AS talk_sec,
                           COUNT(*) FILTER (WHERE t.state = 'failed') AS failed,
                           COUNT(*) FILTER (WHERE t.cancelled) AS cancelled
                    FROM dial_list_attempts t
                    WHERE (t.requested_at AT TIME ZONE 'Asia/Almaty')::date = %s
                    GROUP BY t.operator_id
                ), asg AS (
                    SELECT a.operator_id,
                           COUNT(*) AS issued,
                           COUNT(*) FILTER (WHERE a.state = 'done') AS done
                    FROM dial_list_assignments a
                    WHERE (a.created_at AT TIME ZONE 'Asia/Almaty')::date = %s
                    GROUP BY a.operator_id
                )
                SELECT u.id, u.name, u.department_id, dep.name,
                       COALESCE(asg.issued, 0), COALESCE(asg.done, 0),
                       COALESCE(att.attempts, 0), COALESCE(att.answered, 0),
                       COALESCE(att.talk_sec, 0), COALESCE(att.failed, 0), COALESCE(att.cancelled, 0)
                FROM users u
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN att ON att.operator_id = u.id
                LEFT JOIN asg ON asg.operator_id = u.id
                WHERE (att.operator_id IS NOT NULL OR asg.operator_id IS NOT NULL) {dept_filter}
                ORDER BY dep.name NULLS LAST, u.name
            """, params)
            rows = [{
                "operator_id": r[0], "operator_name": r[1] or "", "department_id": r[2],
                "department_name": r[3] or "", "issued": int(r[4]), "done": int(r[5]),
                "attempts": int(r[6]), "answered": int(r[7]), "talk_sec": int(r[8]), "failed": int(r[9]),
                # Отменённые оператором до ответа водителя — в attempts входят, но не в счёт лидам.
                "cancelled": int(r[10]),
            } for r in cur.fetchall()]
        return rows


def _to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default
