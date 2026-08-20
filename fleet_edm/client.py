"""HTTP-клиент кабинета Яндекс.Fleet.

ЧТО ЭТО ЗА API. Публичного партнёрского метода, отдающего провайдера ЭДО, не
существует: в документированном Fleet API такого поля нет вообще. Работаем с тем
же внутренним API, которым пользуется сам кабинет (fleet.yandex.kz/api/fleet/*).
Авторизация — куки живого логина Яндекс ID, CSRF-токена в нём нет, парк
выбирается ТОЛЬКО заголовком `x-park-id`.

ПОЧЕМУ БЕЗ БРАУЗЕРА. Разовая выгрузка в августе шла через Playwright: запросы
уходили из контекста открытой страницы. Измерено 20.08.2026 — этого не требуется:
обычный requests с теми же куками и user-agent отдаёт 200 и на GET, и на POST.
Значит на сервере не нужен ни Chromium, ни графика. Браузер нужен ровно один раз
и на чужой машине — чтобы человек залогинился и отдал куки (scripts/fleet_edm_push_session.py).

ПОЧЕМУ ПРОВАЙДЕР ДОБЫВАЕТСЯ ПЕРЕСЕЧЕНИЕМ. В списке контрагентов поля ЭДО нет:
любое имя в projection (edm_provider, edm, document_provider и ещё десяток) даёт
400 «invalid field». Значением провайдер лежит только в карточке водителя, а она
стоит один запрос на человека. Зато список УМЕЕТ фильтроваться по провайдеру.
Отсюда приём: спрашиваем «кто из этой сотни у провайдера X» — и ответ сразу даёт
провайдера сотне людей за один запрос. Семь провайдеров = максимум семь проходов,
причём каждый следующий идёт по остатку.

ГРАБЛИ, ЗАПЕЧЁННЫЕ В КОД:
* Неизвестный ключ фильтра НЕ ошибка: сервер отвечает 200 и первой страницей.
  Поэтому фильтры собираются здесь, а не приходят строками снаружи.
* limit больше 100 сервер отклоняет.
* Архив — отдельный сегмент: по умолчанию список отдаёт new+active+churn, и без
  прохода с groups=['archive'] у части водителей провайдер «не находится».
* Имена провайдеров приходят с хвостовым переводом строки («Partners Pay\\n»).
* Карточка водителя привязана к парку: чужой x-park-id даёт 404, а не пустоту.
"""

import json
import logging
import threading
import time

import requests

BASE = 'https://fleet.yandex.kz'
CLIENT_VERSION = 'fleet/21562'

PATH_PARKS = '/api/fleet/ui/v1/user/parks'
PATH_PROFILE = '/api/fleet/ui/v1/parks/users/profile'
PATH_PROVIDERS = '/api/fleet/contractor-profiles-manager/v1/edm-providers'
PATH_LIST = '/api/fleet/contractor-profiles-manager/v2/contractors/list'
PATH_CARD = '/api/fleet/router/v1/cards/driver/details'

# Больше сотни в ОТВЕТЕ сервер не отдаёт: limit 300 и 1000 возвращают ошибку.
# Это ограничение страницы, а не запроса — см. MAX_FILTER_IDS.
MAX_BATCH = 100

# А вот СПРОСИТЬ можно про сколько угодно: замерено 20.08.2026 — фильтр принял
# 10 000 идентификаторов за раз и отдал первую сотню совпавших с курсором на
# остальные. Это меняет всю экономику обхода: цена запроса теперь зависит от
# числа НАЙДЕННЫХ, а не от числа спрошенных. Раньше «сотня водителей = запрос»,
# теперь «сотня совпадений = запрос», а пустой парк стоит один запрос независимо
# от размера списка. Держим 10 000 как проверенный потолок: тело запроса при этом
# около 340 КБ, дальше не измеряли и наугад не лезем.
MAX_FILTER_IDS = 10000

# Потолок одновременных запросов. Замер ступенями: 1 поток — 170 запросов/мин,
# 3 — 439, 6 — 591 при неизменной медиане ответа 0,35 с, 10 — 663, но медиана уже
# 0,93 с, то есть очередь на их стороне. Выше шести смысла нет.
MAX_CONCURRENCY = 6

# А НАЧИНАЕМ с четырёх. Короткий замер 429 не показывал, но на длинных прогонах
# кабинет их шлёт, и первым же залпом в шесть потоков мы сами себе устраиваем
# просадку на старте. Четыре — это ~440 запросов/мин, почти весь выигрыш, и до
# шести клиент дорастает сам, если кабинет молчит.
DEFAULT_CONCURRENCY = 4

# Через столько удачных ответов подряд прибавляем один поток (вниз — сразу,
# вверх — по чуть-чуть). Двести — это меньше минуты работы: достаточно, чтобы не
# дёргать кабинет сразу после его же просьбы, и достаточно быстро, чтобы длинный
# прогон не доезжал на пониженной передаче.
THROTTLE_RECOVERY_AFTER = 200

DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
)


class FleetError(Exception):
    """Общая ошибка обращения к кабинету."""


class FleetSessionExpired(FleetError):
    """Куки протухли — нужен новый логин человеком. Отдельный класс, потому что
    это единственная ошибка, которую чинит не разработчик, а живой человек в
    браузере, и интерфейс обязан сказать об этом прямым текстом."""


class FleetClient:
    """Тонкая обёртка: сессии по потоку + темп + распознавание «разлогинило».

    Темп задаётся не паузами между запросами, а числом одновременных: шесть
    потоков — измеренная точка, где кабинет ещё не начинает копить очередь.
    Своей сессии requests у каждого потока не случайно: один объект Session на
    всех не рассчитан на параллельное использование, а мьютекс вокруг сетевого
    вызова отменил бы саму параллельность.

    Лимит у кабинета всё-таки есть — на длинных прогонах 429 приходит. Реакция
    двойная: общая пауза для всех потоков (просят помедленнее нас целиком, а не
    отдельный поток) плюс минус один поток. Возврат — после двух сотен удачных
    ответов и не выше измеренного потолка в шесть.
    """

    def __init__(self, cookies, user_agent=None, *, concurrency=DEFAULT_CONCURRENCY,
                 max_delay=10.0, timeout=60, session=None):
        self._cookies = self._normalize_cookies(cookies)
        self._user_agent = (user_agent or '').strip() or DEFAULT_USER_AGENT
        self._max_delay = float(max_delay)
        self._timeout = timeout
        self.concurrency = max(1, int(concurrency))
        # Расти можно до измеренного потолка, а не только до стартового значения.
        self._concurrency_ceiling = max(self.concurrency, MAX_CONCURRENCY)
        self._since_throttle = 0
        self.requests_count = 0

        # Соединения по одному на поток: requests.Session не рассчитан на
        # одновременное использование из нескольких потоков, а держать общий
        # мьютекс вокруг сетевого вызова означало бы отменить параллельность.
        self._local = threading.local()
        self._shared_session = session          # для тестов: подменённый транспорт
        self._lock = threading.Lock()
        # Общий на всех тормоз: 429 приходит не отдельному потоку, а нам целиком,
        # поэтому и притормаживают все разом.
        self._pause_until = 0.0
        self._backoff = 0.0

    # ── низкий уровень ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_cookies(cookies):
        """Принимаем и список браузерных кук, и готовый словарь: скрипт с машины
        отдаёт первый вид, тесты — второй."""
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items()}
        jar = {}
        for item in (cookies or []):
            if isinstance(item, dict) and item.get('name'):
                jar[str(item['name'])] = str(item.get('value') or '')
        return jar

    def _headers(self, park_id, json_body=False):
        # x-park-id либо настоящий, либо ЕГО НЕТ ВОВСЕ. Пустая строка и выдуманный
        # идентификатор дают 403 — измерено 20.08.2026. Без заголовка список парков
        # отдаётся (200), а вот профиль требует настоящий парк (400 без заголовка).
        headers = {
            'accept': 'application/json',
            'x-client-version': CLIENT_VERSION,
            'user-agent': self._user_agent,
            'referer': BASE + '/home',
            'origin': BASE,
            'accept-language': 'ru-RU,ru;q=0.9',
        }
        if park_id:
            headers['x-park-id'] = str(park_id)
        if json_body:
            headers['content-type'] = 'application/json'
        return headers

    @property
    def _session(self):
        if self._shared_session is not None:
            return self._shared_session
        session = getattr(self._local, 'session', None)
        if session is None:
            session = requests.Session()
            session.cookies.update(self._cookies)
            self._local.session = session
        return session

    def _wait_if_paused(self):
        while True:
            with self._lock:
                pause = self._pause_until - time.time()
            if pause <= 0:
                return
            time.sleep(min(pause, 1.0))

    def _note_throttled(self):
        """429 — притормаживают все потоки сразу, а не только пострадавший, и
        заодно убираем один поток: пауза лечит текущую минуту, а не причину.

        Ключевая тонкость — ОДИН эпизод, а не один ответ. Когда кабинет просит
        помедленнее, отказ прилетает всем запросам, уже летящим по проводу: при
        шести потоках это шесть 429 подряд. Если наращивать паузу на каждый, она
        растёт в полтора раза шесть раз кряду — так на проде и получилось
        20 секунд простоя вместо секунды. Поэтому отказы, пришедшие во время уже
        объявленной паузы, — это эхо, и мы просто дослушиваем её.
        """
        with self._lock:
            now = time.time()
            if now < self._pause_until:
                return self._pause_until - now
            self._backoff = min(self._max_delay, max(1.0, self._backoff * 1.5))
            self._pause_until = now + self._backoff
            self.concurrency = max(2, self.concurrency - 1)
            self._since_throttle = 0
            return self._backoff

    def _note_success(self):
        """Поток возвращаем осторожно: убавляем сразу, прибавляем через сотни
        удачных ответов.

        Иначе один случайный 429 в начале прогона стоил бы скорости до самого
        конца: на проде так и вышло — темп после единственной просадки застрял на
        277 запросов/мин вместо 440. Схема обычная для сетевых клиентов: вниз
        резко, вверх по чуть-чуть, и никогда выше исходного значения.
        """
        with self._lock:
            if self._backoff:
                self._backoff = max(0.0, self._backoff * 0.7)
            self.requests_count += 1
            self._since_throttle += 1
            if (self._since_throttle >= THROTTLE_RECOVERY_AFTER
                    and self.concurrency < self._concurrency_ceiling):
                self.concurrency += 1
                self._since_throttle = 0

    def _request(self, method, path, *, park_id, body=None, attempts=7):
        url = BASE + path
        last_error = None
        for attempt in range(1, attempts + 1):
            self._wait_if_paused()
            try:
                response = self._session.request(
                    method, url,
                    headers=self._headers(park_id, json_body=body is not None),
                    data=(json.dumps(body) if body is not None else None),
                    timeout=self._timeout,
                )
            except requests.RequestException as error:
                # Сеть моргнула — ждём с нарастанием. В логе только тип ошибки:
                # тело запроса содержит ID водителей, ему в логах не место.
                last_error = error
                logging.warning('Провайдер ЭДО: сеть недоступна (%s), попытка %s',
                                type(error).__name__, attempt)
                time.sleep(min(self._max_delay, 3.0 * attempt))
                continue

            if response.status_code == 429:
                backoff = self._note_throttled()
                logging.warning('Провайдер ЭДО: кабинет просит помедленнее, пауза %.1f с', backoff)
                last_error = FleetError('429 Too Many Requests')
                continue

            if response.status_code == 401:
                raise FleetSessionExpired(
                    'Кабинет Fleet не принял сессию (401). Нужен новый вход.'
                )

            self._note_success()

            if response.status_code >= 500:
                last_error = FleetError('HTTP {}'.format(response.status_code))
                time.sleep(min(self._max_delay, 2.0 * attempt))
                continue

            try:
                payload = response.json()
            except ValueError:
                # Вместо JSON пришла страница — это логин-редирект Яндекс ID.
                # Именно так выглядит протухшая сессия: код 200 и HTML.
                raise FleetSessionExpired(
                    'Кабинет Fleet ответил страницей входа вместо данных. '
                    'Сессия протухла, нужен новый вход.'
                )

            if response.status_code == 403:
                # 403 здесь — это «нет прав», а НЕ протухшая сессия: тот же ответ
                # приходит на выдуманный или пустой x-park-id и на эндпоинты, куда
                # учётке хода нет. Путать их нельзя: человека пошлют логиниться
                # заново вместо того, чтобы починить настоящую причину.
                raise FleetError(
                    'Кабинет отказал (403): нет прав на этот запрос или неверный '
                    'парк. Ответ: {}'.format(str(payload)[:200])
                )

            if response.status_code != 200:
                raise FleetError('Fleet вернул {}: {}'.format(
                    response.status_code, str(payload)[:200]))
            return payload

        raise FleetError('Fleet не ответил после {} попыток: {}'.format(attempts, last_error))

    # ── методы кабинета ──────────────────────────────────────────────────────

    def parks(self, park_id=None):
        """Все диспетчерские аккаунта — единственная ручка, которой не нужен
        известный парк. С неё и начинается любой обход."""
        payload = self._request('GET', PATH_PARKS, park_id=park_id)
        return list(payload.get('parks') or [])

    def profile(self, park_id=None):
        return self._request('GET', PATH_PROFILE, park_id=park_id or '')

    def edm_providers(self, park_id):
        """Справочник провайдеров. Одинаков во всех парках — берём из первого.

        strip() обязателен: у части имён в кабинете хвостовой перевод строки, и
        без него «Partners Pay\\n» не сходится сам с собой при сверке.
        """
        payload = self._request('GET', PATH_PROVIDERS, park_id=park_id)
        providers = []
        for item in (payload.get('providers') or []):
            code = str(item.get('id') or '').strip()
            if code:
                providers.append({'id': code, 'name': str(item.get('name') or '').strip()})
        return providers

    def contractors(self, park_id, *, contractor_ids=None, edm_provider=None,
                    archive=False, projection=None, limit=MAX_BATCH, cursor=None):
        """Одна страница списка контрагентов парка под фильтр.

        Фильтр собирается здесь целиком: неизвестный ключ сервер молча
        проглатывает и отдаёт первую страницу, поэтому «прокинуть произвольный
        фильтр снаружи» — верный способ получить правдоподобный мусор.

        Возвращает (записи, курсор_следующей_страницы).
        """
        filter_ = {}
        if contractor_ids:
            ids = list(contractor_ids)
            if len(ids) > MAX_FILTER_IDS:
                raise ValueError('За раз можно спросить не больше {} ID'.format(MAX_FILTER_IDS))
            filter_['contractor_ids'] = ids
        if edm_provider:
            filter_['edm_providers'] = [edm_provider]
        if archive:
            filter_['groups'] = ['archive']
        body = {
            'filter': filter_,
            'projection': list(projection or ['id']),
            'limit': min(int(limit or MAX_BATCH), MAX_BATCH),
        }
        if cursor:
            body['cursor'] = cursor
        payload = self._request('POST', PATH_LIST, park_id=park_id, body=body)
        return list(payload.get('contractors') or []), payload.get('cursor')

    def contractors_all(self, park_id, *, contractor_ids=None, edm_provider=None,
                        archive=False, projection=None, max_pages=400):
        """Все совпадения по фильтру, сколько бы страниц ни потребовалось.

        Спрашиваем сразу обо всех, кто ещё не определился — хоть о десяти тысячах.
        Пустой ответ стоит ОДИН запрос независимо от длины списка, а дальше цена
        растёт только числом найденных. Именно на этом держится вся скорость
        раздела: раньше сотня водителей = запрос, теперь сотня совпадений = запрос.
        """
        ids = list(contractor_ids or [])
        found, cursor, pages = [], None, 0
        while True:
            chunk = ids[:MAX_FILTER_IDS] if ids else None
            batch, cursor = self.contractors(
                park_id, contractor_ids=chunk, edm_provider=edm_provider,
                archive=archive, projection=projection, cursor=cursor,
            )
            found.extend(batch)
            pages += 1
            # Пустая страница и отсутствие курсора одинаково означают «всё».
            if not batch or not cursor or pages >= max_pages:
                break
        if len(ids) > MAX_FILTER_IDS:
            # Список длиннее проверенного потолка — добираем остаток отдельно,
            # рекурсией по хвосту, а не молча теряем его.
            found.extend(self.contractors_all(
                park_id, contractor_ids=ids[MAX_FILTER_IDS:], edm_provider=edm_provider,
                archive=archive, projection=projection, max_pages=max_pages,
            ))
        return found

    def driver_card(self, park_id, driver_id):
        """Карточка водителя — единственное место, где провайдер лежит значением.

        Возвращает None, если водителя в этом парке нет (кабинет отвечает 404):
        для перебора парков это нормальный ответ, а не сбой.
        """
        try:
            payload = self._request('POST', PATH_CARD, park_id=park_id,
                                    body={'driver_id': str(driver_id)}, attempts=4)
        except FleetError as error:
            if '404' in str(error):
                return None
            raise
        return ((payload.get('driver') or {}).get('driver_profile')) or None

    @staticmethod
    def card_provider(profile):
        """Провайдер из карточки. Пусто — это не «нет провайдера», а «поле не про
        него»: у сотрудников парка (не ИП и не самозанятых) его нет вовсе."""
        if not profile:
            return ''
        return str(profile.get('edm_provider') or '').strip()

    def check(self):
        """Жива ли сессия. Возвращает {account, parks_count} либо кидает
        FleetSessionExpired.

        Порядок важен: сперва список парков (единственная ручка без парка), и уже
        с настоящим идентификатором — профиль, где лежит имя учётки. Наоборот не
        получится: профиль без заголовка отвечает 400.
        """
        parks = self.parks()
        if not parks:
            raise FleetSessionExpired(
                'Кабинет не вернул ни одной диспетчерской — сессия недействительна.'
            )
        payload = self.profile(str(parks[0].get('id')))
        user = payload.get('user') or {}
        return {
            'account': str(user.get('login') or user.get('name') or '').strip(),
            'parks_count': len(user.get('parks') or parks),
        }
