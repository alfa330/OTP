"""Внутренний API окна чатов Wazzup — для аккаунта, чью историю иначе не достать.

Пользовательский API v3 сообщений не читает (`/v3/messages`, `/v3/chats` — 404),
экспорт `messages_dump` есть только у техпартнёров, а вебхук аккаунта «Поток»
занят другой системой. Зато `POST /v3/iframe` выписывает окно чатов, и само
это окно ходит за данными в `app.wazzup24.com/api/v2/*` с JWT из своей ссылки.
Ровно эти два вызова мы и делаем сами, без браузера:

    GET /api/v2/chats?limit=100&offset=N       — чаты, свежие первыми
    GET /api/v2/messages?chatId=…&chatType=…&channelId=…&limit=1000&offset=N
                                               — сообщения чата, свежие первыми

Проверено 23.09.2026: `limit` у списка чатов режется до 100, у сообщений — до
~1000; из параметров пагинации работает только `offset` (before/page/dateTo
игнорируются). Заголовки: `Authorization: <jwt без Bearer>`, `X-Account-ID`,
`x-wazzup-webapp: true`.

ЛОВУШКА, стоившая одного бесполезного прогона: без `chatType` сервер МОЛЧА
игнорирует `chatId` и отдаёт ленту последних сообщений всего аккаунта — одну и
ту же на любой чат. Поэтому `chatType` обязателен, `channelId` (ровно 36
символов, иначе 400) передаём тоже, а полученные сообщения сверяем с
запрошенным чатом и чужие отбрасываем.

Окно выписывается ОТ ИМЕНИ пользователя аккаунта, и видит он то, что видел бы
в интерфейсе: у части людей список урезан (Жанетова видела 73 чата из 100).
Поэтому пользователь задаётся явно (WAZZUP_OP_POTOK_VIEWER_USER_ID) — тот, у
кого доступ ко всем чатам канала.

API недокументированный: при смене контракта падаем громко, а не молча
отдаём пустоту — see WazzupInternalError.
"""
import logging
import time
import urllib.parse

import requests

API_V3 = 'https://api.wazzup24.com/v3/'
APP_API = 'https://app.wazzup24.com/api/'

CHATS_PAGE = 100       # больше сервер не отдаёт (limit=200 → 100 строк)
MESSAGES_PAGE = 1000   # фактически ~998; дальше листаем offset'ом

# Страница считается «короткой» (последней), если строк заметно меньше запрошенного:
# сервер отдаёт 998 на limit=1000, и точное сравнение здесь не годится.
_SHORT_PAGE_RATIO = 0.5

_RETRY_DELAYS = (2, 5, 10)


class WazzupInternalError(RuntimeError):
    """Окно чатов ответило не так, как мы умеем читать."""


class WazzupInternalClient:
    def __init__(self, api_key, account_id, viewer_user_id, viewer_name=None,
                 min_interval=0.3, timeout=60, session=None, logger=None):
        if not api_key:
            raise WazzupInternalError('нет ключа API аккаунта')
        self.api_key = api_key
        self.account_id = str(account_id)
        self.viewer_user_id = str(viewer_user_id)
        self.viewer_name = viewer_name
        self.min_interval = float(min_interval)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.log = logger or logging.getLogger(__name__)
        self._token = None
        self._last_call = 0.0
        self.requests_made = 0

    # ── пользовательский API v3 ─────────────────────────────────────────────
    def _v3(self, method, path, json=None):
        r = self.session.request(
            method, API_V3 + path, json=json, timeout=self.timeout,
            headers={'Authorization': f'Bearer {self.api_key}',
                     'Content-Type': 'application/json'})
        self.requests_made += 1
        if r.status_code >= 400:
            raise WazzupInternalError(f'v3 {path}: HTTP {r.status_code} {r.text[:200]}')
        return r.json()

    def resolve_viewer_name(self):
        """Имя пользователя для iframe: без него Wazzup отвечает INVALID_USER."""
        if self.viewer_name:
            return self.viewer_name
        users = self._v3('GET', 'users')
        users = users.get('data') if isinstance(users, dict) else users
        for u in users or []:
            if str(u.get('id')) == self.viewer_user_id:
                self.viewer_name = u.get('name') or 'OTP'
                return self.viewer_name
        raise WazzupInternalError(
            f'пользователь {self.viewer_user_id} не найден среди /v3/users аккаунта')

    def issue_token(self):
        """JWT окна чатов — из ссылки, которую отдаёт POST /v3/iframe."""
        name = self.resolve_viewer_name()
        frame = self._v3('POST', 'iframe', json={
            'user': {'id': self.viewer_user_id, 'name': name}, 'scope': 'global'})
        url = (frame or {}).get('url') or ''
        token = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('token', [''])[0]
        if not token:
            raise WazzupInternalError(f'iframe без token: {str(frame)[:200]}')
        self._token = token
        return token

    # ── внутренний API окна ─────────────────────────────────────────────────
    def _throttle(self):
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path, params):
        if not self._token:
            self.issue_token()
        refreshed = False
        for attempt in range(len(_RETRY_DELAYS) + 1):
            self._throttle()
            r = self.session.get(
                APP_API + path, params=params, timeout=self.timeout,
                headers={'Authorization': self._token,
                         'X-Account-ID': self.account_id,
                         'x-wazzup-webapp': 'true',
                         'Accept': 'application/json'})
            self.requests_made += 1
            if r.status_code in (401, 403) and not refreshed:
                # токен окна живёт ограниченно — перевыписываем один раз
                self.log.info('wazzup potok: токен окна отклонён (%s), выписываю новый', r.status_code)
                self.issue_token()
                refreshed = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < len(_RETRY_DELAYS):
                    time.sleep(_RETRY_DELAYS[attempt])
                    continue
            if r.status_code != 200:
                raise WazzupInternalError(f'{path}: HTTP {r.status_code} {r.text[:200]}')
            try:
                return r.json()
            except ValueError as error:
                raise WazzupInternalError(f'{path}: не JSON ({error})')
        raise WazzupInternalError(f'{path}: исчерпаны повторы')

    def list_chats(self, offset=0, limit=CHATS_PAGE):
        data = self._get('v2/chats', {'limit': limit, 'offset': offset, 'filterChannels': ''})
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise WazzupInternalError(f'v2/chats: неожиданный ответ {str(data)[:200]}')
        return rows

    def list_messages(self, chat_id, chat_type, channel_id=None, offset=0, limit=MESSAGES_PAGE):
        if not chat_type:
            # см. ловушку в докстринге модуля: без типа придёт лента всего аккаунта
            raise WazzupInternalError(f'v2/messages: у чата {chat_id} нет chatType')
        params = {'chatId': chat_id, 'chatType': chat_type, 'limit': limit, 'offset': offset}
        if channel_id and len(str(channel_id)) == 36:
            params['channelId'] = channel_id
        data = self._get('v2/messages', params)
        rows = data.get('messages') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise WazzupInternalError(f'v2/messages: неожиданный ответ {str(data)[:200]}')
        foreign = [m for m in rows if str(m.get('chatId') or chat_id) != str(chat_id)]
        if foreign:
            # сервер отдал не тот чат — контракт поменялся, шумим, а не пишем чужое
            raise WazzupInternalError(
                f'v2/messages: для чата {chat_id} пришли сообщения других чатов '
                f'({len(foreign)} из {len(rows)}) — контракт параметров изменился')
        return rows

    @staticmethod
    def chat_last_ms(chat):
        return int((chat.get('lastMessage') or {}).get('datetime') or 0)

    def iter_chats(self, since_ms):
        """Чаты с последним сообщением не старше since_ms. Список отсортирован
        свежими вперёд, поэтому первая страница, зашедшая за границу, — последняя."""
        offset = 0
        while True:
            rows = self.list_chats(offset=offset)
            if not rows:
                return
            oldest = None
            for row in rows:
                last = self.chat_last_ms(row)
                oldest = last if oldest is None else min(oldest, last)
                if last >= since_ms:
                    yield row
            if oldest is not None and oldest < since_ms:
                return
            if len(rows) < CHATS_PAGE * _SHORT_PAGE_RATIO:
                return
            offset += len(rows)

    @staticmethod
    def chat_identity(chat):
        """(chatId, chatType, channelId) строки списка чатов. channelId лежит во
        вложенном chats[] — по каналу на запись; у аккаунта «Поток» канал один."""
        chat_id = chat.get('chatId')
        chat_type = chat.get('chatType')
        channel_id = None
        for sub in chat.get('chats') or []:
            if sub.get('channelId'):
                channel_id = sub['channelId']
                chat_type = chat_type or sub.get('chatType')
                break
        return chat_id, chat_type, channel_id

    def iter_messages(self, chat_id, since_ms, chat_type=None, channel_id=None, page=MESSAGES_PAGE):
        """Сообщения чата не старше since_ms, свежие первыми."""
        offset = 0
        while True:
            rows = self.list_messages(chat_id, chat_type, channel_id, offset=offset, limit=page)
            if not rows:
                return
            oldest = None
            for m in rows:
                dt = int(m.get('datetime') or 0)
                oldest = dt if oldest is None else min(oldest, dt)
                if dt >= since_ms:
                    yield m
            if oldest is not None and oldest < since_ms:
                return
            if len(rows) < page * _SHORT_PAGE_RATIO:
                return
            offset += len(rows)
