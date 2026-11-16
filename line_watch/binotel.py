# -*- coding: utf-8 -*-
"""Binotel для сторожа линий: чтение состояния линий и смена presence сотрудника.

Чтение — официальный API (settings/list-of-employees, «лёгкая» ручка): одним запросом presence
каждого сотрудника и то, видит ли Binotel его линию. В ответе лежат SIP-логины и пароли линий —
отсюда наружу выходит только белый список полей.

Запись — кабинет my.binotel.kz учёткой самого сотрудника, та же последовательность, что у
iCORE Phone (BinotelClient.cpp): вход → своя карточка → сохранение карточки с новым
presenceState. Учёткой администратора карточку трогать нельзя: у его формы другой набор полей,
и сохранение затёрло бы то, чего мы не вернули.
"""
import logging

import requests

from . import rules

log = logging.getLogger(__name__)

EMPLOYEES_ENDPOINT = 'settings/list-of-employees'
# Сколько ждать своей очереди к Binotel: сторож — фоновая сверка, лишний раз в очереди не стоит.
READ_MAX_WAIT = 5
READ_TIMEOUT = (5, 15)

CABINET_TIMEOUT = (5, 20)
OWN_RECORD_PATH = '/main/?module=ajax&action=listOfEmployeesForBce&mbav=1'
SAVE_PATH = '/main/?module=users&action=add&userID=%s&mbav=1'
# Группы, по которым кабинет раскладывает сотрудников в ответе listOfEmployeesForBce.
_RECORD_GROUPS = (
    'listOfEmployeesWorkingPresenceStates',
    'listOfEmployeesBreakPresenceStates',
    'listOfEmployeesInactivePresenceStates',
    'listOfEmployeesWithoutPresenceStates',
)


def _to_int(value):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_lines(payload):
    """Ответ settings/list-of-employees → {внутренний номер: состояние линии}.

    Состояние: presence, presence_at (unix), sip (online/inuse/offline), sip_at, inuse_at,
    employee_id. Сотрудник без линии в ответ не попадает — сверять у него нечего."""
    employees = (payload or {}).get('listOfEmployees') or {}
    records = employees.values() if isinstance(employees, dict) else employees
    out = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        endpoint = record.get('endpointData')
        if not isinstance(endpoint, dict):
            continue
        number = str(endpoint.get('internalNumber') or '').strip()
        if not number:
            continue
        status = endpoint.get('status') if isinstance(endpoint.get('status'), dict) else {}
        sip = status.get('sip') if isinstance(status.get('sip'), dict) else {}
        out[number] = {
            'presence': rules.normalize_presence(record.get('presenceState')),
            'presence_at': _to_int(record.get('presenceStateUpdatedAt')),
            # preparedStatus — сводный статус линии; sip.status — он же по каналу регистрации.
            'sip': str(status.get('preparedStatus') or sip.get('status') or '').strip().lower(),
            'sip_at': _to_int(sip.get('updatedAt')),
            'inuse_at': _to_int(sip.get('wasLastInInuseAt')),
            'employee_id': str(record.get('employeeID') or '').strip(),
        }
    return out


def read_lines(client, max_wait=READ_MAX_WAIT, timeout=READ_TIMEOUT):
    """Состояние всех линий кабинета одним запросом (client — binotel.client.BinotelApiClient)."""
    return parse_lines(client._post(EMPLOYEES_ENDPOINT, {}, max_wait=max_wait, timeout=timeout))


class CabinetError(RuntimeError):
    """Кабинет не дал сменить presence: не вошли, нет карточки, не подтвердил запись."""


class CabinetDenied(CabinetError):
    """Кабинет не принял учётку сотрудника. Повторять с теми же логином и паролем бесполезно —
    пока учётку не поправят в «Настройках SIP», статус этого сотрудника не меняет и его телефон."""


class CabinetPresence(object):
    """Presence одного сотрудника в кабинете Binotel — его собственной учёткой.

    Сессия живёт, пока жив объект: вход один, дальше карточка и запись. Параллельный вход той
    же учёткой (сам телефон оператора) гасит cookie — тогда карточка приходит страницей входа,
    и мы входим ещё раз; телефон поступает так же."""

    def __init__(self, base_url, login, password, session=None, timeout=CABINET_TIMEOUT):
        self.base_url = str(base_url or '').rstrip('/')
        self.login = str(login or '').strip()
        self.password = password or ''
        if not self.base_url or not self.login or not self.password:
            raise CabinetError('учётка кабинета Binotel не задана')
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': 'iCORE Phone',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept-Language': 'ru-RU,ru;q=0.9',
        })
        self._logged_in = False

    def _enter(self):
        try:
            response = self.session.post(
                self.base_url + '/',
                data={'logining[email]': self.login, 'logining[password]': self.password},
                timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise CabinetError('нет связи с кабинетом Binotel (%s)' % type(exc).__name__)
        # При отказе кабинет уводит на /?action=badPassword — признак надёжный; по разметке
        # страницы не судим: проверкой входа служит сама карточка.
        if 'action=badPassword' in (response.url or '') or 'action=badPassword' in (response.text or ''):
            raise CabinetDenied('кабинет Binotel не принял пароль сотрудника')
        self._logged_in = True

    def record(self):
        """Своя карточка сотрудника из кабинета (словарь полей) — с одним повторным входом."""
        for attempt in (0, 1):
            if not self._logged_in:
                self._enter()
            try:
                response = self.session.get(self.base_url + OWN_RECORD_PATH, timeout=self.timeout)
                payload = response.json()
            except requests.RequestException as exc:
                raise CabinetError('нет связи с кабинетом Binotel (%s)' % type(exc).__name__)
            except ValueError:
                payload = None                      # страница входа вместо JSON — сессия истекла
            found = self._own(payload)
            if found is not None:
                return found
            self._logged_in = False
        raise CabinetError('кабинет Binotel не отдал карточку сотрудника')

    def _own(self, payload):
        if not isinstance(payload, dict):
            return None
        wanted = self.login.lower()
        for group in _RECORD_GROUPS:
            block = payload.get(group)
            records = block.values() if isinstance(block, dict) else (block or [])
            for record in records:
                if (isinstance(record, dict)
                        and str(record.get('email') or '').strip().lower() == wanted
                        and str(record.get('employeeID') or '').strip()):
                    return record
        return None

    def current(self):
        """Presence, который стоит в кабинете сейчас (active / work in crm / …)."""
        return rules.normalize_presence(self.record().get('presenceState'))

    def set(self, presence):
        """Поставить presence. True — записали, False — в кабинете уже стоит он же.

        Сохранение идёт через форму карточки: остальные поля возвращаем как прочитали, пустой
        пароль значит «не менять». Кабинет на запись того же значения отвечает не success —
        поэтому сначала сверяемся и лишний раз не пишем."""
        presence = rules.normalize_presence(presence)
        code = rules.PRESENCE_CODES.get(presence)
        if code is None:
            raise CabinetError('неизвестный presence: %r' % presence)
        record = self.record()
        if rules.normalize_presence(record.get('presenceState')) == presence:
            return False
        employee_id = str(record.get('employeeID') or '').strip()

        def field(name):
            value = record.get(name)
            return '' if value is None else str(value)

        # Порядок и набор полей — как у телефона; multipart, а не urlencoded: так шлёт кабинет.
        form = [
            ('email', (None, field('email'))),
            ('password', (None, '')),
            ('name', (None, field('name'))),
            ('extID', (None, field('extID'))),
            ('language', (None, field('language'))),
            ('mobilePhoneNumber', (None, field('mobilePhoneNumber'))),
            ('wirePbxNumber', (None, field('wirePbxNumber'))),
            ('employeeID', (None, employee_id)),
            ('presenceState', (None, str(code))),
        ]
        try:
            response = self.session.post(self.base_url + SAVE_PATH % employee_id, files=form,
                                         timeout=self.timeout)
        except requests.RequestException as exc:
            raise CabinetError('нет связи с кабинетом Binotel (%s)' % type(exc).__name__)
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if (response.status_code != 200 or not isinstance(answer, dict)
                or str(answer.get('status') or '').strip().lower() != 'success'):
            raise CabinetError('кабинет Binotel не подтвердил смену статуса (HTTP %s)'
                               % response.status_code)
        return True
