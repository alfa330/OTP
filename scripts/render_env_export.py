"""Переменные окружения сервиса Render → .env для docker compose (deploy/yandex).

    python scripts/render_env_export.py [--out deploy/yandex/.env]

Берёт RENDER_API_KEY и RENDER_SERVICE_ID из окружения или .env.codex.local, забирает
все переменные сервиса (API отдаёт по 100 с курсором — BOT_TOKEN лежит на второй
странице) и ДОПИСЫВАЕТ их в файл: ключи, которые в файле уже есть, не трогает, так что
PUBLIC_HOST, POSTGRES_* и прочее из .env.example остаются своими.

Значения не печатает. Две особенности Render учтены:
  * «$$» в значении Render сворачивает в «$» — процесс на Render видел одиночный «$»,
    столько же должен видеть и контейнер;
  * многострочный JSON (GOOGLE_APPLICATION_CREDENTIALS_CONTENT) кладётся одной строкой.
Значения пишутся в одинарных кавычках: compose не подставляет в них ${…}.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV_ENV_FILE = os.path.join(ROOT, '.env.codex.local')
API = 'https://api.render.com/v1'
# Render выставляет их сам при запуске, в списке переменных сервиса их нет;
# на всякий случай — в контейнер они всё равно не нужны (PORT задаёт compose).
SKIP_KEYS = {'PORT', 'RENDER', 'RENDER_EXTERNAL_URL', 'RENDER_EXTERNAL_HOSTNAME',
             'RENDER_SERVICE_ID', 'RENDER_API_KEY'}


def _dev_env_value(name):
    """Значение из .env.codex.local — только однострочное, этого хватает для двух ключей."""
    try:
        with open(DEV_ENV_FILE, encoding='utf-8') as handle:
            for line in handle:
                if line.startswith(name + '='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ''


def _setting(name):
    value = (os.getenv(name) or '').strip() or _dev_env_value(name)
    if not value:
        sys.exit(f'{name} не задан ни в окружении, ни в .env.codex.local')
    return value


def fetch_env_vars(api_key, service_id):
    """Все переменные сервиса: список (key, value), страницы по курсору."""
    found = []
    cursor = None
    while True:
        query = {'limit': 100}
        if cursor:
            query['cursor'] = cursor
        request = urllib.request.Request(
            f'{API}/services/{service_id}/env-vars?{urllib.parse.urlencode(query)}',
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.loads(response.read().decode('utf-8'))
        if not page:
            return found
        for item in page:
            env_var = item.get('envVar') or {}
            if env_var.get('key'):
                found.append((env_var['key'], env_var.get('value') or ''))
        cursor = page[-1].get('cursor')
        if not cursor or len(page) < 100:
            return found


def container_value(raw):
    """Значение таким, каким его видел процесс на Render, одной строкой."""
    value = raw.replace('$$', '$')
    stripped = value.strip()
    if stripped.startswith('{'):
        try:
            value = json.dumps(json.loads(stripped), ensure_ascii=False, separators=(',', ':'))
        except ValueError:
            pass
    return value.replace('\r\n', '\n').replace('\n', '\\n')


def env_line(key, value):
    """KEY='value' — одинарные кавычки, одиночную кавычку внутри закрываем и открываем заново."""
    return f"{key}='{value.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"


def existing_keys(path):
    keys = set()
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    keys.add(line.split('=', 1)[0].strip())
    except FileNotFoundError:
        pass
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default=os.path.join(ROOT, 'deploy', 'yandex', '.env'),
                        help='куда дописать (по умолчанию deploy/yandex/.env)')
    args = parser.parse_args()

    api_key = _setting('RENDER_API_KEY')
    service_id = _setting('RENDER_SERVICE_ID')
    env_vars = fetch_env_vars(api_key, service_id)

    present = existing_keys(args.out)
    written, skipped = [], []
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'a', encoding='utf-8', newline='\n') as handle:
        handle.write('\n# ── Перенесено из Render (scripts/render_env_export.py) ──\n')
        for key, value in sorted(env_vars):
            if key in SKIP_KEYS or key in present:
                skipped.append(key)
                continue
            handle.write(env_line(key, container_value(value)))
            written.append(key)
    try:
        os.chmod(args.out, 0o600)
    except OSError:
        pass

    print(f'Render отдал {len(env_vars)} переменных; дописано {len(written)}, '
          f'пропущено {len(skipped)} (уже в файле или служебные Render).')
    print(f'Файл: {os.path.abspath(args.out)}')
    if 'BOT_TOKEN' not in {k for k, _ in env_vars}:
        print('ВНИМАНИЕ: BOT_TOKEN среди переменных Render не найден — проверить вручную.')


if __name__ == '__main__':
    main()
