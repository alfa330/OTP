# -*- coding: utf-8 -*-
"""Перенос статей старой корпоративной вики (Wiki.js 2) в раздел «Вики» портала.

ЧТО ЭТО ПЕРЕНОСИТ

Источник — `http://192.168.88.186:3000` (Wiki.js 2, заголовок «Яндекс GO»), 248
страниц. Это НЕ `C:\\python\\wiki2.0`: тот отдельный проект переехал в портал
ещё 09.08.2026 скриптом `migrate_wiki.py`, и его 34 статьи в проде. Путать их
легко — у обоих в названии «вики».

ЧЕРЕЗ НАШ API, А НЕ ПРЯМЫМИ INSERT

Это принципиально: так контент проходит серверную санитизацию, получает варианты
написания для поиска, первую версию в истории, запись в журнал и — главное —
проверку на дубль тем же кодом, каким её делает редактор. Прямые вставки всё это
обошли бы, и раздел получил бы содержимое, которое ведёт себя не так, как статья,
написанная руками.

НИЧЕГО НЕ ПУБЛИКУЕТСЯ

Каждая статья приезжает ЧЕРНОВИКОМ и попадает в очередь модерации
(`wiki_article_imports`). Отдельного параметра «опубликовать» у эндпоинта
переноса нет вовсе — см. шапку wiki/routes_migration.py. Решение по каждой
статье принимает человек на половине «Перенос» вкладки «Статьи».

ДВА ПРОХОДА, А НЕ ОДИН

Проход 1 — создать статьи. Проход 2 — переписать в них картинки и внутренние
ссылки. Разделены не ради красоты: 367 ссылок в корпусе ведут на страницы той же
старой вики, и переписать их можно только когда известны слаги ВСЕХ статей
приёмника. В один проход ссылка на ещё не перенесённую страницу осталась бы
битой.

    python scripts/migrate_wikijs.py                     # план, ничего не пишет
    python scripts/migrate_wikijs.py --apply             # перенос
    python scripts/migrate_wikijs.py --apply --limit 5   # пробный прогон на пяти
    python scripts/migrate_wikijs.py --apply --links-only  # только второй проход
    python scripts/migrate_wikijs.py --refresh           # заново снять слепок
"""

import argparse
import base64
import html
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OLD_WIKI = 'http://192.168.88.186:3000'

# Слепок источника — ВНЕ репозитория: это данные компании, и в git им не место.
# Та же папка-схема, что у вложений задач (%TEMP%/otp_task_files).
SNAPSHOT_DIR = os.path.join(tempfile.gettempdir(), 'otp_wikijs_snapshot')

# Куда кладём перенесённое. Раздел заводится ЗАКРЫТЫМ и без единого правила,
# кроме поимённого для тех, кто модерирует: доступ расширить можно в любой
# момент, отозвать прочитанное — нельзя.
ROOT_SECTION = 'Старая вика'

# Подразделы делаем только под КРУПНЫЕ ветви источника. У старой вики 70 ветвей
# верхнего уровня, и 60 из них — по одной странице: подраздел на каждую превратил
# бы дерево в плоский список с отступами.
MIN_BRANCH = 3

# Кто модерирует перенос. Правило поимённое, потому что задачу поставила она и
# решение по каждой статье принимает она же (задача #234).
MODERATORS = (202,)          # Кастек Гаухар

# Адреса, по которым в статьях встречается та же старая вика. Ссылки на них —
# внутренние, и переписывать надо все четыре, иначе часть текста продолжит
# указывать на то, что выключат.
OLD_WIKI_HOSTS = ('192.168.88.186:3000', '192.168.88.214:3000',
                  '84.252.157.158:3000', '217.11.79.62:3000')

# Картинки, которые ТЯНЕМ к себе. Остальные (относительные пути вроде «6.jpg»)
# перетащить нельзя — их источника уже нет, и они останутся битыми, как были.
IMAGE_HOSTS = ('wiki.itaxi.kz', 'storage.yandexcloud.net') + OLD_WIKI_HOSTS


def log(*args):
    print(*args, flush=True)


def env(name):
    path = os.path.join(ROOT, '.env.codex.local')
    with open(path, encoding='utf-8-sig', errors='replace') as handle:
        match = re.search(r'^%s\s*=\s*(.+)$' % re.escape(name), handle.read(), re.M)
    return match.group(1).strip().strip('"\'') if match else None


# ─────────────────────────────────────────────────────────────────────────────
# Источник: старая вика
# ─────────────────────────────────────────────────────────────────────────────

_PAGE_ATTR = re.compile(r'([:a-zA-Z-]+)="([^"]*)"')
_CONTENTS = '<template slot="contents">'


class OldWiki:
    """Клиент старой вики. GraphQL для списка, веб-страница для текста.

    Почему текст не через GraphQL: `pages { single(id) }` отвечает
    `PageViewForbidden 6013` — у учётки нет `manage:pages`. Права просить не
    нужно: страница отдаёт готовый отрендеренный текст внутри
    `<template slot="contents">`, и это ровно то, что читают операторы.
    """

    def __init__(self):
        self.token = None

    def _gql(self, query, variables=None, anonymous=False):
        body = json.dumps({'query': query, 'variables': variables or {}}).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.token and not anonymous:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(OLD_WIKI + '/graphql', data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))

    def login(self):
        """Вход по ДОМЕННОЙ учётке.

        Стратегия `local` отвечает `AuthLoginFailed`: логин `z.sherzad` живёт в
        Active Directory. Ключ LDAP-стратегии спрашиваем у сервера, а не
        зашиваем: он свой на каждой установке.
        """
        strategies = self._gql(
            '{ authentication { activeStrategies(enabledOnly: true) '
            '{ key strategy { key } } } }', anonymous=True)
        ldap = next(
            (s['key'] for s in
             (((strategies.get('data') or {}).get('authentication') or {})
              .get('activeStrategies') or [])
             if (s.get('strategy') or {}).get('key') == 'ldap'), None)
        if not ldap:
            raise SystemExit('У старой вики нет LDAP-стратегии — вход невозможен')

        login_value, password = env('WIKI_KORP_LOGIN'), env('WIKI_KORP_PASSWORD')
        if not login_value or not password:
            raise SystemExit('Нет WIKI_KORP_LOGIN/WIKI_KORP_PASSWORD в .env.codex.local')

        answer = self._gql(
            '''mutation ($u: String!, $p: String!, $s: String!) {
                 authentication { login(username: $u, password: $p, strategy: $s) {
                     responseResult { succeeded message } jwt } } }''',
            {'u': login_value, 'p': password, 's': ldap}, anonymous=True)
        node = (((answer.get('data') or {}).get('authentication') or {}).get('login') or {})
        result = node.get('responseResult') or {}
        if not result.get('succeeded'):
            raise SystemExit('Старая вика не пустила: %s' % result.get('message'))
        self.token = node.get('jwt')
        return self.token

    def list_pages(self):
        answer = self._gql(
            '''{ pages { list(orderBy: PATH) {
                   id path locale title description isPublished contentType
                   createdAt updatedAt tags } } }''')
        return ((answer.get('data') or {}).get('pages') or {}).get('list') or []

    def page(self, path):
        """Текст страницы + метаданные из атрибутов тега <page>."""
        url = OLD_WIKI + '/ru/' + urllib.parse.quote(path, safe='/')
        request = urllib.request.Request(url, headers={
            'Cookie': 'jwt=' + (self.token or ''), 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode('utf-8', 'replace')

        start = text.find('<page ')
        if start < 0:
            return None
        head_end = text.index('>', start)
        attrs = dict(_PAGE_ATTR.findall(text[start:head_end]))
        body_at = text.find(_CONTENTS, head_end)
        if body_at < 0:
            return None
        body = text[body_at + len(_CONTENTS):text.find('</template>', body_at)]
        # Wiki.js оборачивает содержимое в один служебный <div> — снимаем, чтобы
        # в приёмник приехал текст, а не лишний контейнер.
        if body.startswith('<div>') and body.endswith('</div>'):
            body = body[5:-6]
        try:
            tags = [str(t) for t in json.loads((attrs.get(':tags') or '[]')
                                               .replace('&quot;', '"'))]
        except Exception:
            tags = []
        return {
            'source_id': int(attrs.get(':page-id') or 0) or None,
            'path': attrs.get('path') or path,
            'title': html.unescape(attrs.get('title') or ''),
            'description': html.unescape(attrs.get('description') or ''),
            'tags': tags,
            'content': body,
        }


def snapshot(refresh=False):
    """Слепок источника с кешем на диске.

    Кеш обязателен, а не удобство: перенос из 248 страниц запускается несколько
    раз (план, пробный прогон, полный), и каждый прогон заново обходить чужой
    сервис незачем. `--refresh` снимает заново.
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, 'pages.json')
    if os.path.exists(path) and not refresh:
        with open(path, encoding='utf-8') as handle:
            pages = json.load(handle)
        log('Слепок     : %s (%d страниц, из кеша)' % (path, len(pages)))
        return pages

    old = OldWiki()
    old.login()
    listing = old.list_pages()
    log('Слепок     : снимаю заново, страниц в источнике %d' % len(listing))
    pages, failed = [], []
    for number, meta in enumerate(listing, 1):
        try:
            page = old.page(meta['path'])
        except Exception as error:                       # noqa: BLE001
            failed.append((meta['id'], meta['path'], str(error)[:100]))
            continue
        if not page:
            failed.append((meta['id'], meta['path'], 'нет блока contents'))
            continue
        page['source_id'] = page['source_id'] or meta['id']
        page['source_status'] = 'published' if meta.get('isPublished') else 'draft'
        pages.append(page)
        if number % 25 == 0:
            log('             %d/%d' % (number, len(listing)))
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(pages, handle, ensure_ascii=False, indent=1)
    if failed:
        log('             НЕ СНЯЛОСЬ %d:' % len(failed))
        for row in failed:
            log('               #%s %s — %s' % row)
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# Приёмник: наш API
# ─────────────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self, base_url, login_value, password, dry_run=True):
        self.base = base_url.rstrip('/')
        self.login_value = login_value
        self.password = password
        self.dry_run = dry_run
        self.token = None
        # В холостом прогоне выдаём синтетические id, иначе план показывает нули
        # вместо связей и по нему нельзя проверить сопоставление разделов.
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
                   # Origin обязателен: state-changing запросы отсекает
                   # enforce_api_origin_protection, а без него — 403.
                   'Origin': self.base}
        headers.update(extra or {})
        return headers

    def call(self, method, path, payload=None, files=None, label='', quiet=False):
        if self.dry_run and method != 'GET':
            if not quiet:
                log('   [план] %s %s %s' % (method, path, label))
            self._fake_id += 1
            return {'id': self._fake_id, 'slug': 'dry-run-%d' % self._fake_id,
                    'url': '/api/wiki/file/dry-%d' % self._fake_id, 'created': True}

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
            headers = self._headers(
                {'Content-Type': 'multipart/form-data; boundary=' + boundary})
        else:
            data = (json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
                    if payload is not None else None)
            headers = self._headers({'Content-Type': 'application/json'})

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = response.read().decode('utf-8', 'replace')
                return json.loads(raw) if raw.strip().startswith(('{', '[')) else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', 'replace')[:300]
            raise RuntimeError('%s %s -> %s %s' % (method, path, error.code, detail))


# ─────────────────────────────────────────────────────────────────────────────
# Разделы приёмника
# ─────────────────────────────────────────────────────────────────────────────

def branch_of(path):
    """Верхняя ветвь пути источника: «yandexservice/standarts/microsip» → «yandexservice»."""
    return (str(path or '').split('/')[0] or '').strip()


def plan_sections(pages):
    """Какие подразделы нужны: только под ветви от MIN_BRANCH страниц."""
    counts = {}
    for page in pages:
        counts[branch_of(page['path'])] = counts.get(branch_of(page['path']), 0) + 1
    return {name: count for name, count in sorted(counts.items())
            if name and count >= MIN_BRANCH}


def ensure_structure(api, pages):
    """Раздел «Старая вика», подразделы под крупные ветви и правило модератора.

    Идемпотентно: существующие разделы переиспользуются по имени внутри
    родителя, правило пишется upsert'ом на стороне сервера.
    """
    spaces = api.call('GET', '/api/wiki/spaces').get('items') or []
    active = [s for s in spaces if s.get('status') != 'archived']
    if not active:
        raise SystemExit('В приёмнике нет ни одного живого пространства')
    space = active[0]
    log('\n=== РАЗДЕЛЫ (пространство «%s») ===' % space['name'])

    existing = api.call('GET', '/api/wiki/sections').get('items') or []
    def find(name, parent_id):
        key = str(name).strip().lower()
        for item in existing:
            if (item.get('status') != 'archived'
                    and (item.get('name') or '').strip().lower() == key
                    and (item.get('parent_section_id') or None) == (parent_id or None)):
                return item['id']
        return None

    root_id = find(ROOT_SECTION, None)
    if root_id:
        log('   %-30s уже есть, id=%s' % (ROOT_SECTION, root_id))
    else:
        root_id = api.call('POST', '/api/wiki/sections', {
            'space_id': space['id'], 'name': ROOT_SECTION,
            'visibility_scope': 'restricted',
        }, label='«%s»' % ROOT_SECTION).get('id')
        log('   %-30s -> id=%s (закрыт)' % (ROOT_SECTION, root_id))

    branches = plan_sections(pages)
    section_of_branch = {}
    for name, count in branches.items():
        found = find(name, root_id)
        if found:
            section_of_branch[name] = found
            log('   %-30s уже есть, id=%-5s (%d стр.)' % (name[:30], found, count))
            continue
        new_id = api.call('POST', '/api/wiki/sections', {
            'space_id': space['id'], 'parent_section_id': root_id,
            'name': name, 'visibility_scope': 'restricted',
        }, label='«%s»' % name).get('id')
        section_of_branch[name] = new_id
        log('   %-30s -> id=%-5s (%d стр.)' % (name[:30], new_id, count))

    # Правило модератора — на КОРЕНЬ с grant_subsections: подразделы наследуют,
    # и добавление новой ветви не потребует второго правила.
    log('\n=== КТО МОДЕРИРУЕТ ===')
    for user_id in MODERATORS:
        api.call('POST', '/api/wiki/access/section-rules', {
            'section_id': root_id, 'subject_type': 'user', 'subject_id': user_id,
            'can_read': True, 'can_edit': True, 'can_publish': True,
            'can_delete': True, 'grant_subsections': True,
        }, label='user %s -> «%s»' % (user_id, ROOT_SECTION))
        log('   пользователь %-6s читает, правит, публикует и убирает' % user_id)

    return root_id, section_of_branch


# ─────────────────────────────────────────────────────────────────────────────
# Картинки и ссылки
# ─────────────────────────────────────────────────────────────────────────────

_IMG = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)


def _fetch_bytes(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read(), response.headers.get('Content-Type', 'image/png')


def rewrite_images(api, content, article_id, stats):
    """base64 и картинки с чужих хостов — к нам в GCS, ссылки переписываются.

    Относительные пути (`/6.jpg`, `/ильяс.png`) не трогаем: файлов по ним нет уже
    и в источнике, а выдумывать за автора нечего. Битой картинкой они и были.
    """
    result = content
    for match in list(_IMG.finditer(content)):
        source = match.group(1)
        blob = content_type = None
        # В холостом прогоне картинки НЕ качаем: 154 обращения к чужим хостам —
        # это работа, а не план, и план из-за неё шёл минутами вместо секунд.
        # Посчитать, сколько их и какие переносимы, можно и без загрузки.
        if api.dry_run:
            if (source.startswith('data:image/')
                    or any(host in source for host in IMAGE_HOSTS)):
                stats['images'] += 1
            continue
        if source.startswith('data:image/'):
            try:
                head, payload = source.split(',', 1)
                blob = base64.b64decode(payload)
                content_type = head[5:].split(';')[0] or 'image/png'
            except Exception:                            # noqa: BLE001
                stats['image_failed'] += 1
                continue
            name = 'image.%s' % (content_type.split('/')[-1] or 'png')
        elif any(host in source for host in IMAGE_HOSTS):
            try:
                blob, content_type = _fetch_bytes(source)
            except Exception:                            # noqa: BLE001
                stats['image_failed'] += 1
                continue
            name = urllib.parse.unquote(source.rsplit('/', 1)[-1])[:60] or 'image.png'
        else:
            continue

        answer = api.call('POST', '/api/wiki/upload',
                          payload={'article_id': str(article_id)},
                          files={'file': (name, blob, content_type)},
                          label='(картинка %d КБ)' % (len(blob) // 1024), quiet=True)
        url = answer.get('url')
        if url:
            result = result.replace(source, url)
            stats['images'] += 1
        else:
            stats['image_failed'] += 1
    return result


def link_map(pages, slug_of_source):
    """Путь в источнике → слаг статьи приёмника, во всех написаниях.

    Ключи держим в нижнем регистре и без локали: в корпусе одна и та же страница
    адресуется и как `/ru/Тарифы`, и как `/Тарифы`, и абсолютным адресом по
    четырём разным хостам.
    """
    mapping = {}
    for page in pages:
        slug = slug_of_source.get(page['source_id'])
        if not slug:
            continue
        path = str(page['path']).strip('/')
        for variant in (path, 'ru/' + path):
            mapping[variant.lower()] = slug
            mapping[urllib.parse.quote(variant, safe='/').lower()] = slug
    return mapping


def rewrite_links(content, mapping, stats):
    """Ссылки на страницы источника — на статьи приёмника.

    Адрес статьи в портале — `?view=wiki&article=<слаг>`, относительный: фронт
    живёт на GitHub Pages с базовым путём, и абсолютный '/?view=wiki' увёл бы
    человека на корень домена (та же причина в src/components/wiki/articleLink.js).
    """
    def target(url):
        raw = str(url or '').strip()
        for host in OLD_WIKI_HOSTS:
            for scheme in ('http://', 'https://'):
                prefix = scheme + host
                if raw.lower().startswith(prefix.lower()):
                    raw = raw[len(prefix):]
                    break
        if not raw.startswith('/'):
            return None
        path = raw.split('#')[0].split('?')[0].strip('/')
        return mapping.get(path.lower()) or mapping.get(
            urllib.parse.unquote(path).lower())

    result, moved = content, 0
    for match in set(_HREF.findall(content)):
        slug = target(match)
        if not slug:
            continue
        result = result.replace('href="%s"' % match,
                                'href="?view=wiki&article=%s"'
                                % urllib.parse.quote(slug))
        moved += 1
    stats['links'] += moved
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Перенос
# ─────────────────────────────────────────────────────────────────────────────

def plain_text(content):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', content or '')).strip()


def transfer(api, pages, sections, only=None, links_only=False):
    stats = {'created': 0, 'reused': 0, 'skipped': 0, 'images': 0,
             'image_failed': 0, 'links': 0, 'patched': 0, 'failed': 0,
             'duplicate': 0, 'similar': 0}
    root_id, section_of_branch = sections
    slug_of_source, id_of_source = {}, {}

    log('\n=== ПРОХОД 1: СТАТЬИ ===')
    for page in pages:
        if only and page['source_id'] not in only:
            stats['skipped'] += 1
            continue
        target_section = section_of_branch.get(branch_of(page['path']), root_id)
        summary = (page.get('description')
                   or plain_text(page['content'])[:300] or None)
        try:
            answer = api.call('POST', '/api/wiki/migration/import', {
                'source': 'wikijs',
                'source_id': page['source_id'],
                'source_slug': page['path'],
                'source_title': page['title'],
                'source_status': page.get('source_status'),
                'title': page['title'] or page['path'],
                'summary': summary,
                # Текст заливаем вторым проходом: до него ещё не известны слаги
                # всех статей, а без них ссылки внутри переписать нечем.
                'content': '',
                'section_ids': [target_section] if target_section else [],
                'tags': page.get('tags') or [],
            }, label='«%s»' % (page['title'] or page['path'])[:40], quiet=True)
        except RuntimeError as error:
            stats['failed'] += 1
            log('   ОШИБКА #%-5s %-44s %s' % (
                page['source_id'], (page['title'] or page['path'])[:44], error))
            continue

        article_id, slug = answer.get('id'), answer.get('slug')
        id_of_source[page['source_id']] = article_id
        slug_of_source[page['source_id']] = slug
        if answer.get('created') is False:
            stats['reused'] += 1
            continue
        stats['created'] += 1
        verdict = (answer.get('dedup') or {}).get('verdict')
        if verdict in ('duplicate', 'similar'):
            stats[verdict] += 1
        mark = {'duplicate': 'ДУБЛЬ ', 'similar': 'похоже', 'nearby': 'рядом '}.get(
            verdict, '      ')
        log('   #%-5s %s %-44s -> id=%-6s %s' % (
            page['source_id'], mark, (page['title'] or page['path'])[:44],
            article_id, page['path'][:30]))

    if links_only and not id_of_source:
        # Второй проход отдельным запуском: статьи уже есть, а карту слагов надо
        # собрать заново — из очереди переноса, а не из ответов первого прохода.
        log('   (статьи не создавались, беру карту из очереди переноса)')
        queue = api.call('GET', '/api/wiki/migration?all=1').get('items') or []
        for row in queue:
            if row.get('source_id') is not None:
                id_of_source[row['source_id']] = row['article_id']
                slug_of_source[row['source_id']] = row['slug']

    log('\n=== ПРОХОД 2: ТЕКСТ, КАРТИНКИ, ССЫЛКИ ===')
    mapping = link_map(pages, slug_of_source)
    log('   карта ссылок: %d написаний путей источника' % len(mapping))
    for page in pages:
        article_id = id_of_source.get(page['source_id'])
        if not article_id or (only and page['source_id'] not in only):
            continue
        content = rewrite_images(api, page['content'], article_id, stats)
        content = rewrite_links(content, mapping, stats)
        try:
            api.call('PATCH', '/api/wiki/articles/%s' % article_id, {
                'content': content,
                'comment': 'Перенос из старой вики (%s)' % page['path'],
            }, label='(текст %d КБ)' % (len(content) // 1024), quiet=True)
            stats['patched'] += 1
        except RuntimeError as error:
            stats['failed'] += 1
            log('   ОШИБКА текста #%-5s %s' % (page['source_id'], error))
    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Перенос старой корпоративной вики (Wiki.js) в раздел портала')
    parser.add_argument('--apply', action='store_true',
                        help='выполнить запись (по умолчанию — только план)')
    parser.add_argument('--limit', type=int,
                        help='взять только первые N страниц — для пробного прогона')
    parser.add_argument('--only', help='id страниц источника через запятую')
    parser.add_argument('--links-only', action='store_true',
                        help='только второй проход: текст, картинки и ссылки')
    parser.add_argument('--refresh', action='store_true',
                        help='снять слепок источника заново, минуя кеш')
    args = parser.parse_args()

    base_url = env('WIKI_MIGRATION_API_URL') or 'https://otp-2-fos4.onrender.com'
    login_value, password = env('ADMIN_LOGIN'), env('ADMIN_PASSWORD')
    if not login_value or not password:
        raise SystemExit('Нет ADMIN_LOGIN/ADMIN_PASSWORD в .env.codex.local')

    pages = snapshot(refresh=args.refresh)
    pages.sort(key=lambda p: (p['path'] or ''))
    only = None
    if args.only:
        only = {int(x) for x in args.only.split(',') if x.strip().isdigit()}
    if args.limit:
        only = {p['source_id'] for p in pages[:args.limit]}

    log('Источник   : %s' % OLD_WIKI)
    log('Портал     : %s' % base_url)
    log('Режим      : %s' % ('ЗАПИСЬ' if args.apply
                             else 'холостой прогон (ничего не создаётся)'))
    log('К переносу : %d страниц%s' % (
        len(only) if only else len(pages),
        ' (фильтр из %d)' % len(pages) if only else ''))
    log('Статус     : ВСЁ ЧЕРНОВИКАМИ, в очередь модерации')

    api = Api(base_url, login_value, password, dry_run=not args.apply)
    user = api.authenticate()
    log('Вошли как  : %s (%s)' % (user.get('name'), user.get('role')))

    sections = ensure_structure(api, pages)
    stats = transfer(api, pages, sections, only=only, links_only=args.links_only)

    log('\n=== ИТОГ ===')
    for key, label in (('created', 'статей перенесено'),
                       ('reused', 'уже были перенесены'),
                       ('duplicate', 'из них ИИ считает дублями'),
                       ('similar', 'из них похожи на существующие'),
                       ('patched', 'текст залит'),
                       ('images', 'картинок перенесено к нам'),
                       ('image_failed', 'картинок не перенеслось'),
                       ('links', 'ссылок переписано на наши статьи'),
                       ('skipped', 'пропущено фильтром'),
                       ('failed', 'ошибок')):
        log('   %-34s %s' % (label, stats[key]))

    if not args.apply:
        log('\nЭто был холостой прогон. Для записи запустите с --apply')
    else:
        log('\nДальше — человек: вкладка «Статьи» → половина «Перенос».')
    return 1 if stats['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
