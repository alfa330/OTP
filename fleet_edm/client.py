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
import time

import requests

BASE = 'https://fleet.yandex.kz'
CLIENT_VERSION = 'fleet/21562'

PATH_PARKS = '/api/fleet/ui/v1/user/parks'
PATH_PROFILE = '/api/fleet/ui/v1/parks/users/profile'
PATH_PROVIDERS = '/api/fleet/contractor-profiles-manager/v1/edm-providers'
PATH_LIST = '/api/fleet/contractor-profiles-manager/v2/contractors/list'
PATH_CARD = '/api/fleet/router/v1/cards/driver/details'

# Больше сотни сервер не отдаёт: 300 и 1000 возвращают ошибку вместо списка.
MAX_BATCH = 100

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
    """Тонкая обёртка: сессия requests + троттлинг + распознавание «разлогинило».

    Темп подбирается на ходу, как в разовых прогонах: старт 0,3 с между
    запросами, после серии удачных ответов пауза сокращается до 0,12 с, на 429 —
    растёт в 1,4 раза. Ни одного 429 за все прогоны мы так и не увидели, но
    настоящий лимит Fleet неизвестен, и упереться в него на 147 тысячах строк
    хочется меньше всего.
    """

    def __init__(self, cookies, user_agent=None, *, delay=0.3, min_delay=0.12,
                 max_delay=20.0, success_streak=25, timeout=60, session=None):
        self._session = session or requests.Session()
        self._session.cookies.update(self._normalize_cookies(cookies))
        self._user_agent = (user_agent or '').strip() or DEFAULT_USER_AGENT
        self._delay = float(delay)
        self._min_delay = float(min_delay)
        self._max_delay = float(max_delay)
        self._success_streak_target = int(success_streak)
        self._streak = 0
        self._timeout = timeout
        self.requests_count = 0

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

    def _request(self, method, path, *, park_id, body=None, attempts=7):
        url = BASE + path
        last_error = None
        for attempt in range(1, attempts + 1):
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

            self.requests_count += 1

            if response.status_code == 429:
                self._streak = 0
                self._delay = min(self._max_delay, self._delay * 1.4)
                time.sleep(self._delay * attempt)
                last_error = FleetError('429 Too Many Requests')
                continue

            if response.status_code == 401:
                raise FleetSessionExpired(
                    'Кабинет Fleet не принял сессию (401). Нужен новый вход.'
                )

            self._pace()

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

    def _pace(self):
        """Пауза между запросами. После серии удачных ответов потихоньку
        ускоряемся — иначе на больших файлах теряются минуты на ровном месте."""
        self._streak += 1
        if self._streak >= self._success_streak_target and self._delay > self._min_delay:
            self._delay = max(self._min_delay, self._delay * 0.9)
            self._streak = 0
        time.sleep(self._delay)

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
                    archive=False, projection=None, limit=MAX_BATCH):
        """Список контрагентов парка под фильтр.

        Фильтр собирается здесь целиком: неизвестный ключ сервер молча
        проглатывает и отдаёт первую страницу, поэтому «прокинуть произвольный
        фильтр снаружи» — верный способ получить правдоподобный мусор.
        """
        filter_ = {}
        if contractor_ids:
            ids = list(contractor_ids)
            if len(ids) > MAX_BATCH:
                raise ValueError('За раз можно спросить не больше {} ID'.format(MAX_BATCH))
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
        payload = self._request('POST', PATH_LIST, park_id=park_id, body=body)
        return list(payload.get('contractors') or [])

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
