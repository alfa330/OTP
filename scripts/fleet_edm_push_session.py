#!/usr/bin/env python
"""Передать порталу живую сессию кабинета Яндекс.Fleet — для «Провайдера ЭДО»
или (с --target mailings) для «Рассылок».

Аккаунтов два. «Провайдер ЭДО» ходит под служебным аккаунтом с доступом ко всем
диспетчерским; у «Рассылок» с 23.09.2026 свой аккаунт — с правом рассылать во
всех диспетчерских (у служебного оно было в пяти из девяноста). Поэтому у
каждой цели свой профиль Chromium: вход под одним аккаунтом не выбивает другой.

Зачем это нужно. Кабинет fleet.yandex.kz не выдаёт сервисных ключей: единственный
способ туда попасть — куки живого логина Яндекс ID. Сервер сам залогиниться не
может (капча, СМС, вторая машина), поэтому вход делает человек в браузере на
своей машине, а этот скрипт относит куки в OTP. После этого выгрузки в разделе
работают сами, пока сессия не протухнет — по наблюдениям это недели, а не часы.

Что делает скрипт:
    1. Открывает Chromium с постоянным профилем (по умолчанию тот же, что
       использовался при разовых выгрузках) и заходит на fleet.yandex.kz.
    2. Если вход не выполнен — ждёт, пока человек залогинится в открывшемся окне.
    3. Забирает куки и user-agent и кладёт их в OTP через /api/fleet_edm/session
       (с --target mailings — через /api/driver_mailings/session; там сервер
       заодно переспрашивает, в каких диспетчерских аккаунту разрешена рассылка).
       Сервер сразу проверяет их живым запросом и отказывается принимать
       нерабочие: молча сохранённая мёртвая сессия — это выгрузка, падающая через
       десять минут ожидания вместо честного отказа сейчас.

Playwright намеренно НЕ в requirements.txt: браузер нужен только здесь, на машине
человека. Серверу он не нужен — обход кабинета идёт обычным HTTP-клиентом.

    pip install playwright && playwright install chromium

Примеры:
    python scripts/fleet_edm_push_session.py
    python scripts/fleet_edm_push_session.py --target mailings   # аккаунт «Рассылок»
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
FLEET_URL = 'https://fleet.yandex.kz/'

# Куда нести сессию и в каком профиле Chromium держать вход. Профили разные:
# у разделов разные аккаунты, и общий профиль означал бы перелогин туда-обратно.
TARGETS = {
    'edm': {
        'title': '«Провайдер ЭДО»',
        'session_path': '/api/fleet_edm/session',
        'profile': os.path.expanduser(r'~\.claude\pw-profiles\fleet-yandex'),
        'login_hint': 'под служебным аккаунтом с доступом ко всем диспетчерским',
    },
    'mailings': {
        'title': '«Рассылки»',
        'session_path': '/api/driver_mailings/session',
        'profile': os.path.expanduser(r'~\.claude\pw-profiles\fleet-yandex-mailings'),
        'login_hint': 'под аккаунтом рассылок (с правом рассылать во всех диспетчерских)',
    },
}
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

    def __init__(self, base_url=None, login=None, password=None,
                 session_path=TARGETS['edm']['session_path']):
        self.base_url = (base_url or os.getenv('OTP_API_BASE_URL')
                         or DEFAULT_API_BASE_URL).rstrip('/')
        self.login = login or os.getenv('ADMIN_LOGIN')
        self.password = password or os.getenv('ADMIN_PASSWORD')
        self.session_path = session_path
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
        response = self.session.get('{}{}'.format(self.base_url, self.session_path), timeout=60)
        if response.status_code >= 400:
            raise SystemExit('GET {} → {}: {}'.format(
                self.session_path, response.status_code, response.text[:300]))
        return (response.json() or {}).get('session') or {}

    def push(self, cookies, user_agent):
        """Ответ сервера целиком: «Рассылки» кладут рядом с сессией итог
        опроса парков (parks_checked / parks_enabled)."""
        response = self.session.post(
            '{}{}'.format(self.base_url, self.session_path),
            json={'cookies': cookies, 'user_agent': user_agent},
            # «Рассылки» после приёма сразу опрашивают все диспетчерские —
            # это ещё секунд десять-двадцать сверх проверки.
            timeout=300,
        )
        if response.status_code >= 400:
            raise SystemExit('Сервер не принял сессию → {}: {}'.format(
                response.status_code, response.text[:300]))
        return response.json() or {}


def grab_cookies(profile_dir, wait_minutes=15, headless=False,
                 login_hint=TARGETS['edm']['login_hint']):
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
                print('Войдите в открывшемся окне {}. Жду...'.format(login_hint), flush=True)
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
        description='Передать порталу сессию кабинета Яндекс.Fleet')
    parser.add_argument('--target', choices=sorted(TARGETS), default='edm',
                        help='куда: edm — «Провайдер ЭДО» (по умолчанию), '
                             'mailings — «Рассылки» (свой аккаунт)')
    parser.add_argument('--profile', default=None,
                        help='папка профиля Chromium (по умолчанию своя у каждой цели: {})'.format(
                            ', '.join('{} → {}'.format(k, v['profile'])
                                      for k, v in sorted(TARGETS.items()))))
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

    target = TARGETS[args.target]
    load_env()
    client = OtpClient(args.base_url, args.login, args.password,
                       session_path=target['session_path']).authenticate()

    if args.check:
        status = client.session_status()
        if not status.get('configured'):
            print('Сессия {} в OTP не настроена'.format(target['title']))
            return 0
        if status.get('source') == 'shared':
            print('Своего аккаунта у «Рассылок» нет — работают на общей сессии «Провайдера ЭДО»')
        print('Аккаунт:        {}'.format(status.get('account') or '—'))
        print('Парков:         {}'.format(status.get('parks_count') or '—'))
        print('Обновлена:      {}'.format(status.get('updated_at') or '—'))
        print('Последний успех:{}'.format(status.get('last_ok_at') or '—'))
        if status.get('last_error'):
            print('Последняя ошибка: {}'.format(status['last_error']))
        return 0

    cookies, user_agent, account, parks = grab_cookies(
        args.profile or target['profile'], wait_minutes=args.wait_minutes,
        headless=args.headless, login_hint=target['login_hint'])
    print('Кабинет открыт: {} ({} парков)'.format(account or '—', parks))

    result = client.push(cookies, user_agent)
    status = result.get('session') or {}
    print('Сессия {} принята сервером: аккаунт {}, парков {}, обновлена {}'.format(
        target['title'], status.get('account') or '—', status.get('parks_count') or '—',
        status.get('updated_at') or '—'))
    if 'parks_enabled' in result:
        print('Рассылка разрешена в {} диспетчерских из {}'.format(
            result.get('parks_enabled'), result.get('parks_checked')))
    if result.get('scan_error'):
        print('Опрос диспетчерских сорвался: {} — раздел переспросит их при входе'.format(
            result['scan_error']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
