#!/usr/bin/env python
"""Передать разделу «Провайдер ЭДО» живую сессию кабинета Яндекс.Fleet.

Зачем это нужно. Кабинет fleet.yandex.kz не выдаёт сервисных ключей: единственный
способ туда попасть — куки живого логина Яндекс ID. Сервер сам залогиниться не
может (капча, СМС, вторая машина), поэтому вход делает человек в браузере на
своей машине, а этот скрипт относит куки в OTP. После этого выгрузки в разделе
работают сами, пока сессия не протухнет — по наблюдениям это недели, а не часы.

Что делает скрипт:
    1. Открывает Chromium с постоянным профилем (по умолчанию тот же, что
       использовался при разовых выгрузках) и заходит на fleet.yandex.kz.
    2. Если вход не выполнен — ждёт, пока человек залогинится в открывшемся окне.
    3. Забирает куки и user-agent и кладёт их в OTP через /api/fleet_edm/session.
       Сервер сразу проверяет их живым запросом и отказывается принимать
       нерабочие: молча сохранённая мёртвая сессия — это выгрузка, падающая через
       десять минут ожидания вместо честного отказа сейчас.

Playwright намеренно НЕ в requirements.txt: браузер нужен только здесь, на машине
человека. Серверу он не нужен — обход кабинета идёт обычным HTTP-клиентом.

    pip install playwright && playwright install chromium

Примеры:
    python scripts/fleet_edm_push_session.py
    python scripts/fleet_edm_push_session.py --profile "C:/pw/fleet" --wait-minutes 20
    python scripts/fleet_edm_push_session.py --base-url http://127.0.0.1:5000
    python scripts/fleet_edm_push_session.py --check          # только проверить, что лежит

Куки в консоль не печатаются и на диск не сохраняются.
"""
import argparse
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_API_BASE_URL = 'https://otp-2-fos4.onrender.com'
DEFAULT_PROFILE = os.path.expanduser(r'~\.claude\pw-profiles\fleet-yandex')
FLEET_URL = 'https://fleet.yandex.kz/'
PARKS_PATH = '/api/fleet/ui/v1/user/parks'
PROFILE_PATH = '/api/fleet/ui/v1/parks/users/profile'


def load_env(path='.env.codex.local'):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, value = stripped.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


class OtpClient:
    """Тот же способ входа, что у scripts/task_board.py: bearer, куки сбрасываем —
    иначе прод отвечает 403 «Invalid request origin»."""

    def __init__(self, base_url=None, login=None, password=None):
        self.base_url = (base_url or os.getenv('OTP_API_BASE_URL')
                         or DEFAULT_API_BASE_URL).rstrip('/')
        self.login = login or os.getenv('ADMIN_LOGIN')
        self.password = password or os.getenv('ADMIN_PASSWORD')
        self.session = requests.Session()

    def authenticate(self):
        if not self.login or not self.password:
            raise SystemExit('Нет логина/пароля: задайте ADMIN_LOGIN/ADMIN_PASSWORD '
                             'в .env.codex.local или передайте --login/--password.')
        response = self.session.post(
            '{}/api/login'.format(self.base_url),
            json={'login': self.login, 'password': self.password, 'auth_transport': 'bearer'},
            timeout=60,
        )
        if response.status_code != 200:
            raise SystemExit('Логин не удался: {} {}'.format(
                response.status_code, response.text[:300]))
        payload = response.json()
        token = payload.get('access_token')
        user = payload.get('user') or {}
        if not token or not user.get('id'):
            raise SystemExit('Логин вернул неожидаемый ответ')
        self.session.cookies.clear()
        self.session.headers.update({
            'Authorization': 'Bearer {}'.format(token),
            'X-User-Id': str(user['id']),
        })
        return self

    def session_status(self):
        response = self.session.get('{}/api/fleet_edm/session'.format(self.base_url), timeout=60)
        if response.status_code >= 400:
            raise SystemExit('GET /api/fleet_edm/session → {}: {}'.format(
                response.status_code, response.text[:300]))
        return (response.json() or {}).get('session') or {}

    def push(self, cookies, user_agent):
        response = self.session.post(
            '{}/api/fleet_edm/session'.format(self.base_url),
            json={'cookies': cookies, 'user_agent': user_agent},
            timeout=120,
        )
        if response.status_code >= 400:
            raise SystemExit('Сервер не принял сессию → {}: {}'.format(
                response.status_code, response.text[:300]))
        return (response.json() or {}).get('session') or {}


def grab_cookies(profile_dir, wait_minutes=15, headless=False):
    """Куки живого кабинета. Возвращает (cookies, user_agent, account, parks)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('Нужен playwright: pip install playwright && playwright install chromium')

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=headless,
            args=['--start-maximized'] if not headless else [],
            viewport=None if not headless else {'width': 1280, 'height': 800},
            locale='ru-RU',
            timezone_id='Asia/Almaty',
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(FLEET_URL, wait_until='domcontentloaded', timeout=120000)

            deadline = time.time() + wait_minutes * 60
            checked = None
            while True:
                checked = _probe(page)
                if checked:
                    break
                if headless:
                    raise SystemExit(
                        'Сессия в профиле мертва, а окно скрыто. Запустите без --headless '
                        'и войдите в кабинет руками.'
                    )
                if time.time() > deadline:
                    raise SystemExit('Вход не выполнен за {} минут'.format(wait_minutes))
                print('Войдите в открывшемся окне под аккаунтом с доступом ко всем '
                      'диспетчерским. Жду...', flush=True)
                time.sleep(5)

            cookies = context.cookies(FLEET_URL)
            user_agent = page.evaluate('() => navigator.userAgent')
            return cookies, user_agent, checked.get('account'), checked.get('parks')
        finally:
            context.close()


def _probe(page):
    """Жив ли логин.

    Сначала список парков — это единственная ручка, которой не нужен заголовок
    x-park-id. И только с настоящим идентификатором парка спрашиваем профиль,
    где лежит имя учётки: без заголовка он отвечает 400, а с пустым — 403.
    """
    try:
        result = page.evaluate(
            """async ([parksPath, profilePath]) => {
                const head = {'accept': 'application/json', 'x-client-version': 'fleet/21562'};
                const parksResponse = await fetch(parksPath, {headers: head, credentials: 'include'});
                if (parksResponse.status !== 200) return null;
                const parks = ((await parksResponse.json()).parks) || [];
                if (!parks.length) return null;
                const profileResponse = await fetch(profilePath, {
                    headers: Object.assign({'x-park-id': parks[0].id}, head),
                    credentials: 'include',
                });
                let account = '';
                if (profileResponse.status === 200) {
                    const user = (await profileResponse.json()).user || {};
                    account = user.login || user.name || '';
                }
                return {account: account, parks: parks.length};
            }""",
            [PARKS_PATH, PROFILE_PATH],
        )
    except Exception:
        return None
    if not result or not result.get('parks'):
        return None
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Передать разделу «Провайдер ЭДО» сессию кабинета Яндекс.Fleet')
    parser.add_argument('--profile', default=DEFAULT_PROFILE,
                        help='папка профиля Chromium (по умолчанию {})'.format(DEFAULT_PROFILE))
    parser.add_argument('--base-url', default=None, help='адрес OTP')
    parser.add_argument('--login', default=None)
    parser.add_argument('--password', default=None)
    parser.add_argument('--wait-minutes', type=int, default=15,
                        help='сколько ждать, пока человек войдёт в кабинет')
    parser.add_argument('--headless', action='store_true',
                        help='не показывать окно (годится, только если сессия уже жива)')
    parser.add_argument('--check', action='store_true',
                        help='только показать, что за сессия лежит в OTP')
    args = parser.parse_args()

    load_env()
    client = OtpClient(args.base_url, args.login, args.password).authenticate()

    if args.check:
        status = client.session_status()
        if not status.get('configured'):
            print('Сессия в OTP не настроена')
            return 0
        print('Аккаунт:        {}'.format(status.get('account') or '—'))
        print('Парков:         {}'.format(status.get('parks_count') or '—'))
        print('Обновлена:      {}'.format(status.get('updated_at') or '—'))
        print('Последний успех:{}'.format(status.get('last_ok_at') or '—'))
        if status.get('last_error'):
            print('Последняя ошибка: {}'.format(status['last_error']))
        return 0

    cookies, user_agent, account, parks = grab_cookies(
        args.profile, wait_minutes=args.wait_minutes, headless=args.headless)
    print('Кабинет открыт: {} ({} парков)'.format(account or '—', parks))

    status = client.push(cookies, user_agent)
    print('Сессия принята сервером: аккаунт {}, парков {}, обновлена {}'.format(
        status.get('account') or '—', status.get('parks_count') or '—',
        status.get('updated_at') or '—'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
