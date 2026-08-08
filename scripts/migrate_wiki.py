# -*- coding: utf-8 -*-
"""Перенос содержимого Wiki 2.0 в раздел «Вики» портала.

Работает ЧЕРЕЗ НАШ API, а не прямыми INSERT. Это принципиально: так контент
проходит серверную санитизацию, получает варианты написания для поиска, первую
версию в истории и запись в журнал — то есть ровно тот путь, что и статья,
написанная руками. Прямые вставки всё это обошли бы, и раздел получил бы
содержимое, которое ведёт себя не так, как остальное.

Источник — дамп прод-базы вики (см. C:\\python\\wiki2.0_backup). Сама вика
больше не опрашивается: её free-план на Render истекает, и полагаться на
доступность сервиса во время переноса нельзя.

По умолчанию — ХОЛОСТОЙ ПРОГОН: ничего не создаётся, печатается план.
Запись включается флагом --apply.

    python scripts/migrate_wiki.py                 # план
    python scripts/migrate_wiki.py --apply         # перенос
    python scripts/migrate_wiki.py --apply --only 20,21   # только эти статьи
"""

import argparse
import base64
import glob
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = r'C:\python\wiki2.0_backup'

# ─────────────────────────────────────────────────────────────────────────────
# Карта соответствий
# ─────────────────────────────────────────────────────────────────────────────

# ПО УМОЛЧАНИЮ ВСЕ РАЗДЕЛЫ ПЕРЕНОСЯТСЯ ЗАКРЫТЫМИ: visibility_scope='restricted'
# и НИ ОДНОГО правила доступа. Значит сразу после переноса содержимое видно
# только администратору вики (и автору), а кому его открыть — решается потом,
# на вкладке «Доступы»: правило выдаётся на отдел, направление, группу, роль,
# роль в вики или конкретного человека, и при необходимости уточняется
# правилами на отдельную статью.
#
# Это решение владельца, и оно же безопасное по умолчанию: доступ расширить
# можно в любой момент, отозвать прочитанное — нельзя.
#
# Карта ниже применяется ТОЛЬКО с флагом --open-by-role и оставлена как
# заготовка: разделы вики построены на должностях, которых у нас нет, а отделы
# вики (Коммерческий, IT, Бухгалтерия, HR, Общий) не совпадают ни с одним нашим
# (szov, op, tez, front_office, marketing).
SECTION_RULES = {
    'Коммерческий директор': ('role', 'super_admin'),
    'Руководитель группы': ('role', 'admin'),
    'Супервайзер': ('role', 'sv'),
    'Оператор': ('role', 'operator'),
    'Общий сотрудник': ('public',),
    'Системный администратор': ('admin',),
    'Бухгалтер': ('admin',),
    'HR-менеджер': ('admin',),
}

# Всё закрыто — состояние по умолчанию.
CLOSED = ('admin',)

YANDEX_CDN = 'storage.yandexcloud.net'


def log(*args):
    print(*args, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Доступ к API
# ─────────────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self, base_url, login, password, dry_run=True):
        self.base = base_url.rstrip('/')
        self.login_value = login
        self.password = password
        self.dry_run = dry_run
        self.token = None
        # В холостом прогоне выдаём синтетические id, иначе план показывает
        # нули вместо связей и по нему нельзя проверить сопоставление разделов.
        self._fake_id = 0

    def authenticate(self):
        payload = json.dumps({'login': self.login_value,
                              'password': self.password}).encode('utf-8')
        request = urllib.request.Request(
            self.base + '/api/login', data=payload,
            headers={'Content-Type': 'application/json',
                     'X-Auth-Transport': 'bearer'})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode())
        self.token = data.get('access_token')
        if not self.token:
            raise SystemExit('Не удалось получить токен: в ответе нет access_token')
        return data.get('user') or data

    def _headers(self, extra=None):
        headers = {'Authorization': 'Bearer ' + (self.token or ''),
                   'X-Auth-Transport': 'bearer',
                   # Origin обязателен: state-changing запросы с куками отсекает
                   # enforce_api_origin_protection, а без него — 403.
                   'Origin': self.base}
        headers.update(extra or {})
        return headers

    def call(self, method, path, payload=None, files=None, label=''):
        if self.dry_run and method != 'GET':
            log('   [план] %s %s %s' % (method, path, label))
            self._fake_id += 1
            return {'id': self._fake_id, 'slug': 'dry-run-%d' % self._fake_id,
                    'url': '/api/wiki/file/dry-%d' % self._fake_id}

        url = self.base + path
        if files:
            boundary = '----wiki%d' % int(time.time() * 1000)
            body = io.BytesIO()
            for name, (filename, content, content_type) in files.items():
                body.write(('--%s\r\n' % boundary).encode())
                body.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                            % (name, filename)).encode('utf-8'))
                body.write(('Content-Type: %s\r\n\r\n' % content_type).encode())
                body.write(content)
                body.write(b'\r\n')
            for name, value in (payload or {}).items():
                body.write(('--%s\r\n' % boundary).encode())
                body.write(('Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                            % (name, value)).encode('utf-8'))
            body.write(('--%s--\r\n' % boundary).encode())
            data = body.getvalue()
            headers = self._headers({'Content-Type': 'multipart/form-data; boundary=' + boundary})
        else:
            data = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8') if payload is not None else None
            headers = self._headers({'Content-Type': 'application/json'})

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode('utf-8', 'replace')
                return json.loads(raw) if raw.strip().startswith(('{', '[')) else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', 'replace')[:300]
            raise RuntimeError('%s %s -> %s %s' % (method, path, error.code, detail))


# ─────────────────────────────────────────────────────────────────────────────
# Загрузка дампа
# ─────────────────────────────────────────────────────────────────────────────

def load_dump():
    candidates = sorted(glob.glob(os.path.join(BACKUP_DIR, 'wiki_dump_*.json.gz')))
    if not candidates:
        raise SystemExit('Дамп не найден в %s' % BACKUP_DIR)
    with gzip.open(candidates[-1], 'rt', encoding='utf-8') as handle:
        return candidates[-1], json.load(handle)


def live_articles(dump):
    """Что переносим: только живое.

    Отбрасываем архивные заглушки «[Пример] …» (их 10), две статьи «тест» и
    сид-статью auto-list — интерфейс вики её и так прятал, а классификатор
    переезжает отдельным разделом.
    """
    result = []
    for article in dump['articles']:
        if not article.get('is_visible'):
            continue
        if (article.get('title') or '').strip().lower() == 'тест':
            continue
        if (article.get('slug') or '').startswith('auto-list'):
            continue
        result.append(article)
    return sorted(result, key=lambda a: a['id'])


# ─────────────────────────────────────────────────────────────────────────────
# Картинки
# ─────────────────────────────────────────────────────────────────────────────

_BASE64_IMG = re.compile(r'<img[^>]+src="(data:image/([a-zA-Z0-9+]+);base64,([^"]+))"', re.I)
_CDN_IMG = re.compile(r'<img[^>]+src="(https://%s[^"]+)"' % re.escape(YANDEX_CDN), re.I)


def rewrite_images(api, html, article_id, stats):
    """base64 и картинки с чужого CDN -> наш GCS, ссылки переписываются.

    Битые (внутренние адреса 192.168.* и потерянная /uploads/) остаются как
    есть — по решению владельца их поправят вручную.
    """
    result = html

    for match in list(_BASE64_IMG.finditer(html)):
        whole, fmt, payload = match.group(1), match.group(2), match.group(3)
        try:
            blob = base64.b64decode(payload)
        except Exception:
            stats['image_failed'] += 1
            continue
        content_type = 'image/%s' % ('jpeg' if fmt.lower() in ('jpg', 'jpeg') else fmt.lower())
        response = api.call('POST', '/api/wiki/upload',
                            payload={'article_id': str(article_id)},
                            files={'file': ('image.%s' % fmt, blob, content_type)},
                            label='(base64 %d КБ)' % (len(blob) // 1024))
        url = response.get('url')
        if url:
            result = result.replace(whole, url)
            stats['image_base64'] += 1

    for match in list(_CDN_IMG.finditer(html)):
        source = match.group(1)
        try:
            request = urllib.request.Request(source, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=120) as response:
                blob = response.read()
                content_type = response.headers.get('Content-Type', 'image/png')
        except Exception:
            stats['image_failed'] += 1
            continue
        name = source.rsplit('/', 1)[-1][:60] or 'image.png'
        response = api.call('POST', '/api/wiki/upload',
                            payload={'article_id': str(article_id)},
                            files={'file': (name, blob, content_type)},
                            label='(с CDN %d КБ)' % (len(blob) // 1024))
        url = response.get('url')
        if url:
            result = result.replace(source, url)
            stats['image_cdn'] += 1

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Перенос
# ─────────────────────────────────────────────────────────────────────────────

def existing_by_name(api, path, key='items'):
    """Что уже есть в разделе — чтобы повторный запуск не плодил дубли."""
    try:
        data = api.call('GET', path)
    except RuntimeError:
        return {}
    return {(item.get('name') or '').strip().lower(): item.get('id')
            for item in (data.get(key) or [])}


def existing_by_name(api, path, key='items'):
    """Что уже есть в разделе — чтобы повторный запуск не плодил дубли.

    Архивные пропускаем: если пространство убрали в архив осознанно, повторный
    перенос не должен молча его воскрешать и складывать туда статьи.
    """
    try:
        data = api.call('GET', path)
    except RuntimeError:
        return {}
    result = {}
    for item in (data.get(key) or []):
        if item.get('status') == 'archived':
            continue
        result[(item.get('name') or '').strip().lower()] = item.get('id')
    return result


def existing_slugs(api):
    try:
        data = api.call('GET', '/api/wiki/articles?limit=200')
    except RuntimeError:
        return {}
    return {(item.get('slug') or ''): item.get('id')
            for item in (data.get('items') or [])
            if item.get('status') != 'archived'}


def migrate(api, dump, only=None, open_by_role=False):
    stats = {'spaces': 0, 'sections': 0, 'rules': 0, 'articles': 0, 'published': 0,
             'image_base64': 0, 'image_cdn': 0, 'image_failed': 0, 'skipped': 0,
             'reused': 0}

    spaces = {s['id']: s for s in dump['spaces']}
    sections = {s['id']: s for s in dump['sections']}
    tags_by_article = {}
    for row in dump['article_tags']:
        tags_by_article.setdefault(row['article_id'], []).append(row['tag_name'])
    sections_by_article = {}
    for row in dump['article_sections']:
        sections_by_article.setdefault(row['article_id'], []).append(row['section_id'])

    # 1. Пространства
    log('\n=== ПРОСТРАНСТВА ===')
    have_spaces = existing_by_name(api, '/api/wiki/spaces')
    space_map = {}
    for old_id, space in sorted(spaces.items()):
        key = space['name'].strip().lower()
        if key in have_spaces:
            space_map[old_id] = have_spaces[key]
            stats['reused'] += 1
            log('   %-26s уже есть, id=%s' % (space['name'], have_spaces[key]))
            continue
        response = api.call('POST', '/api/wiki/spaces',
                            {'name': space['name'], 'description': space.get('description')},
                            label='«%s»' % space['name'])
        space_map[old_id] = response.get('id')
        stats['spaces'] += 1
        log('   %-26s -> id=%s' % (space['name'], response.get('id')))

    # 2. Разделы
    log('\n=== РАЗДЕЛЫ ===')
    have_sections = existing_by_name(api, '/api/wiki/sections')
    section_map = {}
    for old_id, section in sorted(sections.items()):
        key = section['name'].strip().lower()
        if key in have_sections:
            section_map[old_id] = have_sections[key]
            stats['reused'] += 1
            log('   %-26s уже есть, id=%s' % (section['name'], have_sections[key]))
            continue
        rule = SECTION_RULES.get(section['name'], CLOSED) if open_by_role else CLOSED
        response = api.call('POST', '/api/wiki/sections', {
            'space_id': space_map.get(section['space_id']),
            'name': section['name'],
            'visibility_scope': 'public' if rule[0] == 'public' else 'restricted',
        }, label='«%s»' % section['name'])
        section_map[old_id] = response.get('id')
        stats['sections'] += 1
        log('   %-26s -> id=%-6s %s' % (section['name'], response.get('id'),
                                        'публичный' if rule[0] == 'public'
                                        else ('роль %s' % rule[1]) if rule[0] == 'role'
                                        else 'только администратор'))

    # 3. Правила доступа
    log('\n=== ПРАВИЛА ДОСТУПА ===')
    for old_id, section in sorted(sections.items()):
        rule = SECTION_RULES.get(section['name'], CLOSED) if open_by_role else CLOSED
        if rule[0] != 'role':
            continue
        api.call('POST', '/api/wiki/access/section-rules', {
            'section_id': section_map.get(old_id),
            'subject_type': 'otp_role',
            'subject_role': rule[1],
            'can_read': True,
            'grant_subsections': True,
        }, label='%s -> %s' % (section['name'], rule[1]))
        stats['rules'] += 1
        log('   %-26s читают: %s и выше' % (section['name'], rule[1]))

    # 4. Статьи
    log('\n=== СТАТЬИ ===')
    have_slugs = existing_slugs(api)
    for article in live_articles(dump):
        if only and article['id'] not in only:
            stats['skipped'] += 1
            continue
        if article.get('slug') in have_slugs:
            stats['reused'] += 1
            log('   #%-4s %-46s уже перенесена, id=%s' % (
                article['id'], article['title'][:46], have_slugs[article['slug']]))
            continue

        target_sections = [section_map[s] for s in sections_by_article.get(article['id'], [])
                           if section_map.get(s)]
        created = api.call('POST', '/api/wiki/articles', {
            'title': article['title'],
            'slug': article.get('slug'),
            'summary': article.get('summary'),
            'content': '',          # тело зальём вторым шагом, после картинок
            'section_ids': target_sections,
            'tags': tags_by_article.get(article['id'], []),
        }, label='«%s»' % article['title'][:40])

        new_id = created.get('id')
        content = rewrite_images(api, article.get('content') or '', new_id, stats)

        payload = {'content': content, 'comment': 'Перенос из Wiki 2.0'}
        if article.get('status') == 'published':
            payload['status'] = 'published'
            stats['published'] += 1
        api.call('PATCH', '/api/wiki/articles/%s' % new_id, payload,
                 label='(тело %d КБ)' % (len(content) // 1024))

        stats['articles'] += 1
        log('   #%-4s %-46s -> id=%-6s разделов=%s' % (
            article['id'], article['title'][:46], new_id, len(target_sections)))

    return stats


def main():
    parser = argparse.ArgumentParser(description='Перенос Wiki 2.0 в раздел портала')
    parser.add_argument('--apply', action='store_true',
                        help='выполнить запись (по умолчанию — только план)')
    parser.add_argument('--only', help='список id статей вики через запятую')
    parser.add_argument('--open-by-role', action='store_true',
                        help='выдать правила по ролям из карты SECTION_RULES '
                             '(по умолчанию всё закрыто)')
    args = parser.parse_args()

    env_path = os.path.join(ROOT, '.env.codex.local')
    env_text = open(env_path, encoding='utf-8', errors='replace').read()

    def env(name):
        match = re.search(r'^%s\s*=\s*(.+)$' % name, env_text, re.M)
        return match.group(1).strip().strip('"\'') if match else None

    # ADMIN_BASE_URL — адрес ФРОНТЕНДА (GitHub Pages), туда ходить нельзя:
    # API живёт на Render. Origin для проверки происхождения берём тот же.
    base_url = env('WIKI_MIGRATION_API_URL') or 'https://otp-2-fos4.onrender.com'
    login_value, password = env('ADMIN_LOGIN'), env('ADMIN_PASSWORD')
    if not login_value or not password:
        raise SystemExit('Нет ADMIN_LOGIN/ADMIN_PASSWORD в .env.codex.local')

    path, dump = load_dump()
    articles = live_articles(dump)
    only = None
    if args.only:
        only = {int(x) for x in args.only.split(',') if x.strip().isdigit()}

    log('Дамп     : %s' % path)
    log('Портал   : %s' % base_url)
    log('Режим    : %s' % ('ЗАПИСЬ' if args.apply else 'холостой прогон (ничего не создаётся)'))
    log('К переносу: %d статей%s' % (len(articles),
                                     ' (фильтр: %s)' % sorted(only) if only else ''))
    log('Доступ   : %s' % ('правила по ролям' if args.open_by_role
                           else 'ВСЁ ЗАКРЫТО — открывать вручную после переноса'))

    api = Api(base_url, login_value, password, dry_run=not args.apply)
    user = api.authenticate()
    log('Вошли как: %s (%s)' % (user.get('name'), user.get('role')))

    stats = migrate(api, dump, only=only, open_by_role=args.open_by_role)

    log('\n=== ИТОГ ===')
    for key, label in (('spaces', 'пространств'), ('sections', 'разделов'),
                       ('rules', 'правил доступа'), ('articles', 'статей'),
                       ('published', 'из них опубликовано'),
                       ('image_base64', 'картинок из base64 в GCS'),
                       ('image_cdn', 'картинок с чужого CDN в GCS'),
                       ('image_failed', 'картинок не перенеслось'),
                       ('skipped', 'пропущено фильтром'),
                       ('reused', 'уже было — переиспользовано')):
        log('   %-32s %s' % (label, stats[key]))

    if not args.apply:
        log('\nЭто был холостой прогон. Для записи запустите с --apply')


if __name__ == '__main__':
    sys.exit(main())
