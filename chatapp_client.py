"""Клиент ChatApp API (api.chatapp.online) для «Случайного чата» отдела TEZ.

ЧТО ЭТО. У ТЭЗ переписка ТП и ОП живёт в ChatApp (WhatsApp Cloud API), а не в
Chat2Desk (СЗоВ) и не в Wazzup (Верификаторы ОП). Модуль — только HTTP-клиент,
без БД: сборкой эпизодов и снапшотами занимаются database.py/bot_schedule2.py
(по образцу `tez_binotel_calls.py`).

АВТОРИЗАЦИЯ. `POST /v1/tokens` {email, password, appId} -> accessToken (24 ч) и
refreshToken (14 дн); заголовок `Authorization: <accessToken>` БЕЗ `Bearer`.
Ключевое ограничение: **100 токенов в сутки на пару email-appId**, поэтому пару
нельзя держать в памяти воркера — рестарт Render или второй воркер сожгут квоту.
Клиент работает через `token_store` (load/save в БД) и следует рекомендованному
вики-флоу: запрос -> `ApiInvalidTokenError` -> refresh -> повтор; полный
`tokens.make` только когда refresh тоже мёртв. Rate limit — 50 req/s на IP.

ЧТО ПРОВЕРЕНО ЖИВЬЁМ (2026-07-21, аккаунт Tez, companyId 71322):
  - лицензии: 72861 «Тех отдел» (ТП) и 70651 «Отдел продаж», обе caWhatsApp;
  - у списка чатов НЕТ фильтра по датам — только курсор `nextPage` и сортировка
    по lastTime DESC, поэтому пул за период набирается листанием назад;
  - у сообщений есть `direction=next` — окно времени выбирается по возрастанию
    честно, без трюков с интерполяцией id, которые понадобились для Chat2Desk;
  - `responsible` у чата НЕ равен тому, кто реально отвечал (живые контрпримеры),
    поэтому оператора считаем по `created.id` исходящих сообщений;
  - автоприветствие приходит как `fromApp.id='bitrix'` с `created.id` владельца
    аккаунта — это не работа оператора (см. `is_autoreply`);
  - `link` у чата всегда null, `/v1/feedbacks` пуст (CSAT не собирают).

ENV (.env.codex.local или окружение):
    CHATAPP_EMAIL=<почта кабинета>
    CHATAPP_PASSWORD=<пароль кабинета>
    CHATAPP_APP_ID=<appId из кабинета, вида app_84210_1>
    CHATAPP_COMPANY_ID=71322          # опционально, иначе берётся первая компания
    CHATAPP_LICENSE_IDS=72861,70651   # опционально, иначе все активные лицензии
    CHATAPP_API_URL=https://api.chatapp.online   # опционально

Ручная сверка без БД (токен ляжет в .chatapp_token.json рядом со скриптом):
    python chatapp_client.py --licenses
    python chatapp_client.py --employees
    python chatapp_client.py --chats --days 2
    python chatapp_client.py --messages 77763068686 --license 72861 --days 2
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.chatapp.online"
HTTP_TIMEOUT = 60
PAGE_LIMIT = 100          # потолок ChatApp и для чатов, и для сообщений
MAX_PAGES = 200           # предохранитель от бесконечного листания
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Запас, с которым считаем accessToken протухшим (часы/минуты у нас и у них плывут).
TOKEN_SKEW_SECONDS = 120


class ChatAppError(RuntimeError):
    """Ошибка ChatApp API с разобранным кодом (`error.code` из тела ответа)."""

    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def get_config(env=None):
    env = env if env is not None else os.environ
    raw_licenses = str(env.get('CHATAPP_LICENSE_IDS') or '').strip()
    licenses = [int(v) for v in raw_licenses.replace(';', ',').split(',') if v.strip()]
    company_id = str(env.get('CHATAPP_COMPANY_ID') or '').strip()
    return {
        'email': str(env.get('CHATAPP_EMAIL') or '').strip(),
        'password': str(env.get('CHATAPP_PASSWORD') or '').strip(),
        'app_id': str(env.get('CHATAPP_APP_ID') or '').strip(),
        'company_id': int(company_id) if company_id.isdigit() else None,
        'license_ids': licenses,
        'api_url': (str(env.get('CHATAPP_API_URL') or DEFAULT_API_URL).strip().rstrip('/')
                    or DEFAULT_API_URL),
    }


def api_ready(cfg):
    return bool(cfg.get('email') and cfg.get('password') and cfg.get('app_id'))


class ChatAppClient:
    """HTTP-клиент ChatApp. `token_store` — объект с методами load()/save(dict);
    без него пара токенов живёт в памяти (годится только для CLI-сверки)."""

    def __init__(self, email, password, app_id, api_url=DEFAULT_API_URL,
                 token_store=None, session=None):
        self.email = email
        self.password = password
        self.app_id = app_id
        self.api_url = api_url.rstrip('/')
        self.token_store = token_store
        self.session = session or requests.Session()
        self._tokens = None

    @classmethod
    def from_config(cls, cfg, token_store=None, session=None):
        return cls(cfg['email'], cfg['password'], cfg['app_id'],
                   api_url=cfg.get('api_url') or DEFAULT_API_URL,
                   token_store=token_store, session=session)

    # ── токены ───────────────────────────────────────────────────────────────

    def _load_tokens(self):
        if self._tokens is None and self.token_store is not None:
            self._tokens = self.token_store.load() or {}
        return self._tokens or {}

    def _store_tokens(self, data):
        self._tokens = data
        if self.token_store is not None:
            self.token_store.save(data)
        return data

    def _make_tokens(self):
        """Полный вход по email/паролю. Тратит одну из 100 суточных попыток."""
        log.info("chatapp: получаем новый токен (tokens.make)")
        r = self.session.post(f'{self.api_url}/v1/tokens', json={
            'email': self.email, 'password': self.password, 'appId': self.app_id,
        }, headers={'Lang': 'ru'}, timeout=HTTP_TIMEOUT)
        body = _json_or_raise(r)
        if not body.get('success'):
            raise _error_from_body(body, r.status_code)
        return self._store_tokens(body['data'])

    def _refresh_tokens(self, refresh_token):
        r = self.session.post(f'{self.api_url}/v1/tokens/refresh',
                              headers={'Refresh': refresh_token, 'Lang': 'ru'},
                              timeout=HTTP_TIMEOUT)
        try:
            body = _json_or_raise(r)
        except ChatAppError:
            return None
        if not body.get('success'):
            return None
        log.info("chatapp: accessToken обновлён по refreshToken")
        return self._store_tokens(body['data'])

    def access_token(self, force_new=False):
        tokens = self._load_tokens()
        if not force_new:
            expires = _as_int(tokens.get('accessTokenEndTime'))
            if tokens.get('accessToken') and expires and expires - TOKEN_SKEW_SECONDS > time.time():
                return tokens['accessToken']
        refresh = tokens.get('refreshToken')
        refresh_expires = _as_int(tokens.get('refreshTokenEndTime'))
        if refresh and (not refresh_expires or refresh_expires > time.time()):
            refreshed = self._refresh_tokens(refresh)
            if refreshed:
                return refreshed['accessToken']
        return self._make_tokens()['accessToken']

    # ── низкий уровень ───────────────────────────────────────────────────────

    def _get(self, path, params=None, _retry_auth=True):
        token = self.access_token()
        last_error = None
        for attempt in range(4):
            try:
                r = self.session.get(f'{self.api_url}{path}',
                                     headers={'Authorization': token, 'Lang': 'ru'},
                                     params=params or {}, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                last_error = ChatAppError(f'ChatApp: сеть недоступна ({exc})')
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in RETRY_STATUSES:
                # 429 — упёрлись в 50 req/s, ждём и повторяем
                last_error = ChatAppError(f'ChatApp HTTP {r.status_code}', status=r.status_code)
                time.sleep(1.5 * (attempt + 1))
                continue
            body = _json_or_raise(r)
            if body.get('success'):
                return body.get('data')
            error = _error_from_body(body, r.status_code)
            if error.code == 'ApiInvalidTokenError' and _retry_auth:
                # Штатный путь по документации: протух accessToken -> refresh -> повтор
                token = self.access_token(force_new=True)
                _retry_auth = False
                continue
            raise error
        raise last_error or ChatAppError('ChatApp: запрос не удался')

    # ── справочники ──────────────────────────────────────────────────────────

    def list_licenses(self):
        return self._get('/v1/licenses') or []

    def list_companies(self):
        return ((self._get('/v1/companies') or {}).get('items')) or []

    def resolve_company_id(self, preferred=None):
        if preferred:
            return int(preferred)
        companies = self.list_companies()
        return int(companies[0]['companyId']) if companies else None

    def list_employees(self, company_id):
        data = self._get(f'/v1/companies/{int(company_id)}/employees') or {}
        return data.get('items') or []

    def active_messenger_licenses(self, only_license_ids=None):
        """[(licenseId, messengerType, licenseName, phone)] по активным лицензиям."""
        wanted = {int(v) for v in (only_license_ids or [])}
        out = []
        for lic in self.list_licenses():
            if not lic.get('active'):
                continue
            license_id = int(lic['licenseId'])
            if wanted and license_id not in wanted:
                continue
            for messenger in (lic.get('messenger') or []):
                if not messenger.get('type'):
                    continue
                out.append((license_id, messenger['type'], lic.get('licenseName'),
                            (messenger.get('info') or {}).get('phone')))
        return out

    # ── чаты и сообщения ─────────────────────────────────────────────────────

    def iter_chats(self, company_id, license_ids=None, since_ts=None):
        """Чаты по убыванию lastTime. Фильтра по датам у API нет, поэтому листаем
        назад и останавливаемся, когда страница целиком старше since_ts."""
        next_page = None
        for _page in range(MAX_PAGES):
            params = {'companyId': int(company_id), 'limit': PAGE_LIMIT}
            if license_ids:
                params['filter[licenseIds]'] = ','.join(str(int(v)) for v in license_ids)
            if next_page:
                params['nextPage'] = next_page
            data = self._get('/v1/chats', params) or {}
            items = data.get('items') or []
            if not items:
                return
            for item in items:
                yield item
            next_page = data.get('nextPage')
            if not next_page:
                return
            if since_ts is not None:
                oldest = min(_as_int(i.get('lastTime')) or 0 for i in items)
                if oldest < int(since_ts):
                    return

    def fetch_messages(self, license_id, messenger_type, chat_id,
                       since_ts, until_ts=None, include_system=True):
        """Сообщения чата в окне [since_ts, until_ts] по возрастанию времени.

        `direction=next` + курсор `nextPage` — единственный способ, который на
        живых данных отдаёт окно без дублей и пропусков (проверено)."""
        path = (f'/v1/licenses/{int(license_id)}/messengers/{messenger_type}'
                f'/chats/{chat_id}/messages')
        out, seen, next_page = [], set(), None
        for _page in range(MAX_PAGES):
            params = {'limit': PAGE_LIMIT, 'direction': 'next',
                      'includeSystemMessages': 1 if include_system else 0}
            if next_page:
                params['nextPage'] = next_page
            else:
                params['lastTime'] = int(since_ts)
            data = self._get(path, params) or {}
            items = data.get('items') or []
            for item in items:
                key = item.get('id') or item.get('internalId')
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
            next_page = data.get('nextPage')
            if not items or not next_page:
                break
            if until_ts is not None and max(_as_int(i.get('time')) or 0 for i in items) > int(until_ts):
                break
        out.sort(key=lambda m: (_as_int(m.get('time')) or 0, str(m.get('id') or '')))
        if until_ts is None:
            return [m for m in out if (_as_int(m.get('time')) or 0) >= int(since_ts)]
        return [m for m in out
                if int(since_ts) <= (_as_int(m.get('time')) or 0) <= int(until_ts)]


# ── разбор сообщений ─────────────────────────────────────────────────────────

def is_autoreply(message):
    """Приветствие/рассылка робота, а не ответ оператора.

    В ChatApp такие сообщения приходят от интеграции (`fromApp.id` = 'bitrix'),
    но с `created.id` владельца аккаунта — если этого не отсечь, робот «съест»
    атрибуцию эпизода. Ручные ответы операторов идут с `fromApp.id='webchat'`.
    Аналог `is_bot` у Wazzup."""
    app = message.get('fromApp') or {}
    if str(app.get('sender') or '') == 'system':
        return True
    # fromApp может отсутствовать вовсе — такое исходящее считаем ручным:
    # потерять работу оператора хуже, чем лишний раз показать автоответ.
    return str(app.get('id') or '') not in ('webchat', '')


def employee_id(message):
    """id сотрудника-автора исходящего сообщения (None у входящих и роботов)."""
    if str(message.get('side') or '') != 'out':
        return None
    if is_autoreply(message):
        return None
    created = message.get('created') or {}
    return _as_int(created.get('id'))


def message_row(message, license_id, messenger_type, chat_id):
    """Сообщение API -> плоская строка для chatapp_messages."""
    content = message.get('message') or {}
    file_info = content.get('file') or {}
    app = message.get('fromApp') or {}
    created = message.get('created') or {}
    from_user = message.get('fromUser') or {}
    ts = _as_int(message.get('time')) or 0
    text = str(content.get('text') or '') or str(content.get('caption') or '')
    return {
        'license_id': int(license_id),
        'messenger_type': str(messenger_type),
        'chat_id': str(chat_id),
        'message_id': str(message.get('id') or message.get('internalId') or ''),
        'dt': datetime.fromtimestamp(ts, timezone.utc),
        'side': str(message.get('side') or ''),
        'type': str(message.get('type') or ''),
        'subtype': message.get('subtype'),
        'text': text,
        'file_link': file_info.get('link'),
        'file_name': file_info.get('name'),
        'file_content_type': file_info.get('contentType'),
        'employee_id': _as_int(created.get('id')),
        'app_id': app.get('id'),
        'app_sender': app.get('sender'),
        'client_name': from_user.get('name'),
        'is_deleted': bool(message.get('destroyed')),
    }


def chat_row(chat):
    """Чат API -> плоская строка для chatapp_chats."""
    responsible = chat.get('responsible') or {}
    last_time = _as_int(chat.get('lastTime'))
    return {
        'license_id': _as_int(chat.get('licenseId')),
        'messenger_type': str(chat.get('messengerType') or ''),
        'chat_id': str(chat.get('id') or ''),
        'internal_id': str(chat.get('internalId') or '') or None,
        'name': chat.get('name'),
        'phone': chat.get('phone'),
        'responsible_employee_id': _as_int(responsible.get('id')),
        'status': chat.get('status'),
        'chat_type': chat.get('type'),
        'last_time': datetime.fromtimestamp(last_time, timezone.utc) if last_time else None,
    }


# ── вспомогательное ──────────────────────────────────────────────────────────

def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_or_raise(response):
    try:
        return response.json() or {}
    except ValueError:
        raise ChatAppError(
            f'ChatApp HTTP {response.status_code}: ответ не JSON ({str(response.text)[:200]})',
            status=response.status_code)


def _error_from_body(body, status):
    error = body.get('error') if isinstance(body.get('error'), dict) else {}
    code = error.get('code')
    message = error.get('message') or body.get('message') or 'неизвестная ошибка'
    return ChatAppError(f'ChatApp API: {message}', code=code, status=status)


class _FileTokenStore:
    """Хранилище токенов для CLI-сверки. В приложении вместо него — БД."""

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def save(self, data):
        with open(self.path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)


def _load_dotenv(path='.env.codex.local'):
    """Только для CLI: подтянуть CHATAPP_* из локального файла доступов."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip().startswith('CHATAPP_'):
                os.environ.setdefault(key.strip(), value.strip())


def main():
    # Консоль Windows по умолчанию cp1251 — казахские буквы в ФИО её роняют.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description='Ручная сверка ChatApp API')
    parser.add_argument('--licenses', action='store_true')
    parser.add_argument('--employees', action='store_true')
    parser.add_argument('--chats', action='store_true')
    parser.add_argument('--messages', metavar='CHAT_ID')
    parser.add_argument('--license', type=int)
    parser.add_argument('--messenger', default='caWhatsApp')
    parser.add_argument('--days', type=int, default=1)
    args = parser.parse_args()

    _load_dotenv()
    cfg = get_config()
    if not api_ready(cfg):
        raise SystemExit('CHATAPP_EMAIL / CHATAPP_PASSWORD / CHATAPP_APP_ID не заданы')
    store = _FileTokenStore(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         '.chatapp_token.json'))
    client = ChatAppClient.from_config(cfg, token_store=store)
    company_id = client.resolve_company_id(cfg.get('company_id'))
    since = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())

    if args.licenses:
        for row in client.active_messenger_licenses(cfg.get('license_ids')):
            print('license', row)
    if args.employees:
        for e in client.list_employees(company_id):
            print(f"{e['id']:<8}{str(e.get('fullName'))[:32]:<34}"
                  f"{str((e.get('role') or {}).get('name')):<18}{e.get('email')}")
    if args.chats:
        n = 0
        for chat in client.iter_chats(company_id, cfg.get('license_ids'), since_ts=since):
            n += 1
            print(json.dumps(chat_row(chat), ensure_ascii=False, default=str))
        print('всего чатов:', n)
    if args.messages:
        msgs = client.fetch_messages(args.license or (cfg.get('license_ids') or [None])[0],
                                     args.messenger, args.messages, since_ts=since)
        for m in msgs:
            row = message_row(m, args.license, args.messenger, args.messages)
            print(f"{row['dt']:%m-%d %H:%M} {row['side']:<4}{row['type']:<8}"
                  f"emp={employee_id(m)} auto={is_autoreply(m)} {str(row['text'])[:70]}")
        print('всего сообщений:', len(msgs))


if __name__ == '__main__':
    main()
