# -*- coding: utf-8 -*-
"""Клиент CDR станции. Работает ИЗНУТРИ корпоративной сети.

Ходит напрямую в FastAPI станции (`http://192.168.17.44:8000/api/v1`), минуя
nginx: внешнюю обёртку с basic-auth 25.08.2026 закрыли после того, как сервис лёг.

ЧТО ЗДЕСЬ ЗАПРЕЩЕНО И ПОЧЕМУ
----------------------------
У станции 24 ручки, и три из них трогают не базу CDR, а живое состояние
Asterisk через AMI: `/freepbx/load/queues`, `/freepbx/load/agents`,
`/agents/stream`. 25.08.2026 три вызова первых двух **зависли без ответа**, и в
течение минут станция перестала отвечать целиком. Причинно-следственная связь не
доказана, но это единственное, что трогали.

Поэтому здесь белый список: клиент физически умеет ходить только по четырём
адресам (`ALLOWED_PATHS`), и попытка позвать что-то ещё — ошибка в коде, а не
запрос в сеть. Плюс к этому: одновременных запросов не больше одного, паузa
между страницами, повтор только на обрыв уже установленного соединения.

Проверенные факты о станции (25.08.2026):
  * страница 5000 строк отдаётся за ~1,9 с (2,3 МБ), больше 5000 нельзя — 422;
  * сутки отдела продаж это ~23 тысячи строк, то есть пять страниц;
  * `/freepbx/cdr` авторизации не требует, а `/auth/me` и `/dialplan` требуют
    Bearer — поэтому логин здесь опционален и включается, только если станция
    вдруг начнёт отвечать 401 на чтение CDR.
"""

import logging
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger('cdr_bridge.station')

# Ровно то, что нужно для касаний. Ничего, что трогает AMI, здесь нет и не будет.
ALLOWED_PATHS = (
    '/freepbx/cdr',
    '/freepbx/cdr/count',
    '/agents/map',
    '/health',
)

PAGE_LIMIT = 5000

# Пауза между страницами. Станция низкоконкурентная, а мы у неё не единственный
# клиент: в рабочее время по ней идут живые звонки.
PAGE_PAUSE_SECONDS = 0.4

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 120

REQUEST_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


class StationError(Exception):
    def __init__(self, message, code='unavailable', status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def _dropped_connection(exc):
    """Обрыв уже установленного соединения: requests → urllib3 → http.client."""
    seen, stack = set(), [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if type(current).__name__ in ('RemoteDisconnected', 'ProtocolError',
                                      'IncompleteRead'):
            return True
        stack.append(getattr(current, '__cause__', None))
        stack.append(getattr(current, '__context__', None))
        stack.extend(a for a in getattr(current, 'args', ()) if isinstance(a, BaseException))
    return False


class Station:
    """session и креды — параметры, а не глобальные: иначе тест на машине
    разработчика молча пойдёт в боевую станцию."""

    def __init__(self, base_url, username=None, password=None, session=None):
        self.base = (base_url or '').rstrip('/')
        self.username = username or ''
        self.password = password or ''
        self.session = session or self._build_session()
        self._token = None

    @staticmethod
    def _build_session():
        session = requests.Session()
        # Пул на одно соединение: параллельных запросов у нас нет по замыслу.
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=2)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    def _url(self, path, params=None):
        if path not in ALLOWED_PATHS:
            # Не сетевая ошибка, а ошибка программиста: список закрыт осознанно.
            raise StationError('Ручка %s не разрешена мосту' % path, code='forbidden_path')
        url = '%s/api/v1%s' % (self.base, path)
        return '%s?%s' % (url, urlencode(params)) if params else url

    def _headers(self):
        return {'Accept': 'application/json',
                **({'Authorization': 'Bearer ' + self._token} if self._token else {})}

    def _get(self, path, params=None, _relogin=True):
        url = self._url(path, params)
        last_error = None
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                response = self.session.get(url, headers=self._headers(),
                                            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except requests.exceptions.ConnectTimeout as exc:
                # Рукопожатия не было — станцию мы не побеспокоили, повторить можно.
                last_error = exc
                if attempt < REQUEST_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise StationError('Станция не отвечает на соединение', 'unreachable') from exc
            except requests.exceptions.Timeout as exc:
                # Запрос принят и обрабатывается — повтор только добавит работы.
                raise StationError('Станция не ответила за %d с' % READ_TIMEOUT,
                                   'timeout') from exc
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if _dropped_connection(exc) and attempt < REQUEST_RETRIES:
                    log.warning('Соединение оборвалось, повтор %d/%d', attempt, REQUEST_RETRIES)
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise StationError('Станция недоступна: %s' % exc, 'unavailable') from exc

            if response.status_code == 401 and _relogin and self.username:
                # Чтение CDR авторизации не требует, но если станцию однажды
                # закроют целиком — залогинимся и повторим ровно один раз.
                self.login()
                return self._get(path, params, _relogin=False)
            if response.status_code != 200:
                raise StationError('Станция ответила %d' % response.status_code,
                                   'bad_status', response.status_code)
            try:
                return response.json()
            except ValueError as exc:
                raise StationError('Станция вернула не JSON', 'bad_payload') from exc
        raise StationError('Станция недоступна: %s' % last_error, 'unavailable')

    def login(self):
        """Bearer у FastAPI. Логин — ФОРМА, не JSON: OAuth2PasswordRequestForm,
        на JSON отвечает 422."""
        if not self.username:
            return None
        response = self.session.post(
            '%s/api/v1/auth/login' % self.base,
            data={'username': self.username, 'password': self.password},
            timeout=(CONNECT_TIMEOUT, 60))
        if response.status_code != 200:
            raise StationError('Станция не выдала токен: %d' % response.status_code,
                               'unauthorized', response.status_code)
        self._token = (response.json() or {}).get('access_token')
        return self._token

    def health(self):
        return self._get('/health')

    def count(self, from_dt, to_dt):
        """Сколько строк в окне — дешёвый запрос, им меряем работу до её начала.
        Без `to_dt` ручка молча отдаёт нули, поэтому он обязателен."""
        return self._get('/freepbx/cdr/count', {'from_dt': from_dt, 'to_dt': to_dt})

    def iter_cdr(self, from_dt, to_dt, on_page=None):
        """Все строки окна страницами по 5000.

        Через offset, потому что курсора у API нет. Признак конца — короткая
        страница: пустую станция отдаёт, только когда строк ровно кратно пяти
        тысячам, и ждать её было бы лишним запросом.
        """
        offset = 0
        while True:
            chunk = self._get('/freepbx/cdr', {
                'from_dt': from_dt, 'to_dt': to_dt,
                'limit': PAGE_LIMIT, 'offset': offset,
                'order_by': 'calldate', 'order_dir': 'asc'})
            if not isinstance(chunk, list):
                raise StationError('Станция вернула не список строк', 'bad_payload')
            if not chunk:
                return
            for row in chunk:
                yield row
            offset += len(chunk)
            if on_page:
                on_page(offset)
            if len(chunk) < PAGE_LIMIT:
                return
            time.sleep(PAGE_PAUSE_SECONDS)

    def agents_map(self):
        """ext → имя сотрудника по данным станции (транслит, без отчеств).

        Единственный источник правды о ТЕКУЩЕМ владельце номера: в нашей базе
        номер уволившегося остаётся висеть на нём же.
        """
        data = self._get('/agents/map')
        if not isinstance(data, dict):
            raise StationError('Станция вернула неожиданный справочник', 'bad_payload')
        return {str(k): str(v or '') for k, v in data.items()}
