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
NON_FINAL_DISPOSITIONS = frozenset({"", "ONLINE"})

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

PHONE_EVENTS = ("ringing", "answered", "ended", "no_leg")

# Отделы, чья работа — этот обзвон (удалённый колл-центр). Глава такого отдела
# управляет разделом без роли админа. Отдел считается «своим» для раздела также,
# если ему уже заведены настройки обзвона или его телефония — Binotel: код в
# карточке отдела проставляют не всегда, и одним кодом периметр не удержать.
DIAL_LIST_DEPARTMENT_CODES = frozenset({"remote_cc"})

# Пилот: пока список не пуст, раздел руководителя открыт ТОЛЬКО этим логинам —
# даже админам его не показываем (решение владельца 22.09.2026: сначала смотрит
# сам). Опустошить список = открыть раздел по обычным правилам (админы и главы).
# Ручек телефона это не касается: там свои включатели у отдела и оператора.
DIAL_LIST_PILOT_LOGINS = frozenset({"alfa330"})


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
        """Что видит руководитель: None — всё (админ без своего отдела); список id —
        только свои отделы из периметра раздела; [] — раздел не его.
        Пока идёт пилот (DIAL_LIST_PILOT_LOGINS), всем, кроме списка, — []."""
        if not pilot_allows(login):
            return []
        headed = sorted({int(x) for x in (headed_department_ids or [])})
        if is_admin and not headed:
            return None
        if not headed:
            return []
        return [d["department_id"] for d in self.list_departments(headed)]

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

    def department_users(self, department_id):
        """Сотрудники отдела (все роли: тест ведёт и сам владелец) с их SIP-номером."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, COALESCE(u.login, ''), COALESCE(u.role, ''),
                       COALESCE(u.sip_number, ''), COALESCE(u.status, '')
                FROM users u
                WHERE u.department_id = %s AND COALESCE(u.is_active, TRUE)
                ORDER BY u.name
            """, (int(department_id),))
            return [{"id": r[0], "name": r[1] or "", "login": r[2], "role": r[3],
                     "sip_number": (r[4] or "").strip(), "status": r[5]} for r in cur.fetchall()]

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
                       caller_id_for_employee, updated_at, binotel_company
                FROM dial_list_department_settings WHERE department_id = %s
            """, (int(department_id),))
            row = cur.fetchone()
        company = ((row[6] or "").strip() if row else "") or DEFAULT_BINOTEL_COMPANY
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

        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_department_settings (
                    department_id, enabled, portion_size, max_attempts, retry_after_hours,
                    caller_id_for_employee, binotel_company, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (department_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    portion_size = EXCLUDED.portion_size,
                    max_attempts = EXCLUDED.max_attempts,
                    retry_after_hours = EXCLUDED.retry_after_hours,
                    caller_id_for_employee = EXCLUDED.caller_id_for_employee,
                    binotel_company = EXCLUDED.binotel_company,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
            """, (int(department_id), enabled, portion_size, max_attempts, retry_after_hours,
                  caller_id, company, changed_by))
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
    def import_leads(self, department_id, uploaded_by, file_name, rows):
        """rows — из common.leads_file.parse_leads_file: (row_number, fio, phone_raw, phone_norm)."""
        department_id = int(department_id)
        counts = {"rows_total": len(rows), "rows_new": 0, "rows_duplicate": 0, "rows_invalid": 0}
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_lead_batches (department_id, uploaded_by, file_name, rows_total)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (department_id, uploaded_by, str(file_name or "")[:255], len(rows)))
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
                    INSERT INTO dial_list_leads (department_id, phone_norm, full_name, first_batch_id, last_batch_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (department_id, phone_norm) DO UPDATE SET
                        full_name = CASE WHEN EXCLUDED.full_name <> '' THEN EXCLUDED.full_name
                                         ELSE dial_list_leads.full_name END,
                        last_batch_id = EXCLUDED.last_batch_id,
                        upload_count = dial_list_leads.upload_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING (xmax = 0) AS inserted
                """, (department_id, phone_norm, str(fio or "").strip()[:255], batch_id, batch_id))
                inserted = cur.fetchone()[0]
                counts["rows_new" if inserted else "rows_duplicate"] += 1
            cur.execute("""
                UPDATE dial_list_lead_batches
                SET rows_new = %s, rows_duplicate = %s, rows_invalid = %s WHERE id = %s
            """, (counts["rows_new"], counts["rows_duplicate"], counts["rows_invalid"], batch_id))
        counts["batch_id"] = batch_id
        return counts

    def leads_summary(self, department_id):
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT status, COUNT(*) FROM dial_list_leads WHERE department_id = %s GROUP BY status
            """, (department_id,))
            by_status = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(self._POOL_SQL.format(lock="") .replace("LIMIT %s", ""), (
                department_id, settings["retry_after_hours"], settings["max_attempts"]))
            pool = len(cur.fetchall())
            cur.execute("""
                SELECT id, file_name, rows_total, rows_new, rows_duplicate, rows_invalid, created_at
                FROM dial_list_lead_batches WHERE department_id = %s
                ORDER BY created_at DESC LIMIT 20
            """, (department_id,))
            batches = [{
                "id": _sid(r[0]), "file_name": r[1], "rows_total": r[2], "rows_new": r[3],
                "rows_duplicate": r[4], "rows_invalid": r[5], "created_at": _iso(r[6]),
            } for r in cur.fetchall()]
        return {
            "department_id": department_id,
            "total": sum(by_status.values()),
            "by_status": {k: by_status.get(k, 0) for k in ("new", "in_progress", "done", "excluded")},
            "pool_available": pool,
            "batches": batches,
        }

    # Пул: лиды отдела, которые можно выдать сейчас. Один и тот же текст для
    # выдачи (с блокировкой) и для подсчёта (без неё), чтобы цифра «доступно»
    # совпадала с тем, что реально выдаётся.
    _POOL_SQL = """
        SELECT l.id
        FROM dial_list_leads l
        WHERE l.department_id = %s
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
        return {"id": str(row[0]), "size": int(row[1]), "issued_at": row[2]} if row else None

    def _portion_items(self, cur, portion_id):
        cur.execute("""
            SELECT a.id, a.position, l.full_name, a.state, a.result, a.attempts, a.done_at,
                   t.id, t.state, t.disposition, t.requested_at, t.general_call_id
            FROM dial_list_assignments a
            JOIN dial_list_leads l ON l.id = a.lead_id
            LEFT JOIN LATERAL (
                SELECT id, state, disposition, requested_at, general_call_id
                FROM dial_list_attempts WHERE assignment_id = a.id
                ORDER BY requested_at DESC LIMIT 1
            ) t ON TRUE
            WHERE a.portion_id = %s
            ORDER BY a.position
        """, (portion_id,))
        items = []
        for r in cur.fetchall():
            items.append({
                "assignment_id": str(r[0]),
                "position": int(r[1]),
                "full_name": r[2] or "Без имени",
                "state": r[3],
                "result": r[4] or "",
                "attempts": int(r[5] or 0),
                "done_at": _iso(r[6]),
                "last_attempt": None if r[7] is None else {
                    "attempt_id": str(r[7]), "state": r[8], "disposition": r[9] or "",
                    "requested_at": _iso(r[10]), "general_call_id": r[11],
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
            portion = self._open_portion(cur, user_id)
            items = self._portion_items(cur, portion["id"]) if portion else []
            active = self._active_attempt(cur, user_id)
            cur.execute(self._POOL_SQL.format(lock="").replace("LIMIT %s", ""), (
                ctx["department_id"], settings["retry_after_hours"], settings["max_attempts"]))
            pool = len(cur.fetchall())
        pending = sum(1 for i in items if i["state"] == "issued")
        return {
            "enabled": True,
            "operator": {"id": ctx["user_id"], "name": ctx["name"], "internal_number": ctx["internal_number"]},
            "portion_size": settings["portion_size"],
            "leg_timeout_sec": LEG_TIMEOUT_SEC,
            "portion": None if not portion else {
                "id": portion["id"], "size": portion["size"], "issued_at": _iso(portion["issued_at"]),
                "pending": pending, "items": items,
            },
            "can_request_next": pending == 0 and pool > 0,
            "pool_available": pool,
            "active_attempt": active,
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
            cur.execute(self._POOL_SQL.format(lock="FOR UPDATE SKIP LOCKED"), (
                ctx["department_id"], settings["retry_after_hours"], settings["max_attempts"],
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
            phone_norm, full_name, lead_id = row[3], row[4] or "", str(row[5])
            cur.execute("""
                INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number)
                VALUES (%s, %s, %s) RETURNING id, requested_at
            """, (assignment_id, ctx["user_id"], ctx["internal_number"]))
            attempt_id, requested_at = cur.fetchone()
            attempt_id = str(attempt_id)
            cur.execute("UPDATE dial_list_assignments SET attempts = attempts + 1 WHERE id = %s", (assignment_id,))
            attempts_now = int(row[2] or 0) + 1

        extra = {}
        caller_id = settings.get("caller_id_for_employee") or ""
        if caller_id:
            extra["callerIdForEmployee"] = caller_id
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
            self._fail_attempt(attempt_id, assignment_id, lead_id, message,
                               attempts_now, settings["max_attempts"])
            log.warning("dial_list: Binotel отказал оператору %s: %s", ctx["user_id"], message)
            status = 429 if "too frequent" in message.lower() or "часто" in message.lower() else 502
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

    def _fail_attempt(self, attempt_id, assignment_id, lead_id, message, attempts_now, max_attempts):
        """АТС отказала: попытка failed. Строка остаётся в работе, пока есть попытки —
        оператор может нажать ещё раз; исчерпали — закрываем как failed."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = 'failed', api_error = %s, final_source = 'api_error',
                    finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (message, attempt_id))
            if attempts_now >= int(max_attempts):
                self._close_assignment(cur, assignment_id, lead_id, "failed", answered=False, count_attempt=True)

    # ------------------------------------------------------------ исходы
    def phone_event(self, user_id, attempt_id, event, at=None):
        """Подсказка телефона: плечо пришло / принято / разговор кончился / плеча не было."""
        event = str(event or "").strip().lower()
        if event not in PHONE_EVENTS:
            raise DialListError(f"Неизвестное событие: {event!r}")
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT state, general_call_id, assignment_id FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s FOR UPDATE
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            state, general_call_id = row[0], row[1]
            if state in ("finished", "failed"):
                return {"attempt_id": attempt_id, "state": state, "final": True}
            new_state = {
                "ringing": "leg_ringing", "answered": "leg_answered",
                "ended": "ended", "no_leg": state,
            }[event]
            # Назад по цепочке не ходим: «ringing» после «answered» ничего не значит.
            order = ["requested", "leg_ringing", "leg_answered", "ended"]
            if order.index(new_state) < order.index(state):
                new_state = state
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = %s, phone_event_at = CURRENT_TIMESTAMP,
                    phone_ended_at = CASE WHEN %s = 'ended' THEN CURRENT_TIMESTAMP ELSE phone_ended_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_state, event, attempt_id))
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
        return result

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
                return False
            assignment_id = str(row[0])
            cur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (assignment_id,))
            a = cur.fetchone()
            if a and a[1] == "issued":
                self._close_assignment(cur, assignment_id, str(a[0]), result,
                                       answered=(result == "answered"), count_attempt=True)
            elif a:
                # Строка уже закрыта прошлой попыткой — лид всё равно учитывает звонок.
                self._touch_lead(cur, str(a[0]), answered=(result == "answered"), count_attempt=True)
        return True

    def _close_assignment(self, cur, assignment_id, lead_id, result, answered, count_attempt):
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'done', result = %s, done_at = CURRENT_TIMESTAMP
            WHERE id = %s RETURNING portion_id
        """, (result, assignment_id))
        row = cur.fetchone()
        self._touch_lead(cur, lead_id, answered=answered, count_attempt=count_attempt)
        if row:
            portion_id = str(row[0])
            cur.execute("""
                UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND closed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM dial_list_assignments
                                  WHERE portion_id = %s AND state = 'issued')
            """, (portion_id, portion_id))

    def _touch_lead(self, cur, lead_id, answered, count_attempt):
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
        if answered:
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
                if row[1] != "finished" and map_disposition(disposition):
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
                           COUNT(*) FILTER (WHERE t.state = 'failed') AS failed
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
                       COALESCE(att.talk_sec, 0), COALESCE(att.failed, 0)
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
            } for r in cur.fetchall()]
        return rows


def _to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default
