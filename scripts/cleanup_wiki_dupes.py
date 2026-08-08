# -*- coding: utf-8 -*-
"""Разовая уборка дублей, созданных повторным запуском миграции.

Первый прогон миграции шёл без идемпотентности, поэтому второй создал вторые
комплекты пространств, разделов и статей. Скрипт оставляет ПЕРВЫЙ комплект
(у его статей исходные слаги без суффикса -2) и убирает в архив остальные.

Заодно перезаписывает содержимое оставшихся статей тем же текстом: при
сохранении сервер привязывает к статье файлы, на которые она ссылается, — так
картинки, загруженные до появления привязки, перестают быть видны одному лишь
загрузившему.

По умолчанию — план. Запись: --apply
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'https://otp-2-fos4.onrender.com'


def env(name):
    text = open(os.path.join(ROOT, '.env.codex.local'),
                encoding='utf-8', errors='replace').read()
    match = re.search(r'^%s\s*=\s*(.+)$' % name, text, re.M)
    return match.group(1).strip().strip('"\'') if match else None


def login():
    payload = json.dumps({'login': env('ADMIN_LOGIN'),
                          'password': env('ADMIN_PASSWORD')}).encode()
    request = urllib.request.Request(
        BASE + '/api/login', data=payload,
        headers={'Content-Type': 'application/json',
                 'X-Auth-Transport': 'bearer', 'Origin': BASE})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode())['access_token']


def call(token, method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={'Authorization': 'Bearer ' + token, 'X-Auth-Transport': 'bearer',
                 'Content-Type': 'application/json', 'Origin': BASE})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode('utf-8', 'replace')
            return json.loads(raw) if raw.strip().startswith(('{', '[')) else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError('%s %s -> %s %s' % (method, path, error.code,
                                               error.read().decode()[:200]))


def keep_first(items, name_key='name'):
    """Первый по id для каждого имени — остальные дубли."""
    seen, duplicates = {}, []
    for item in sorted(items, key=lambda x: x['id']):
        key = (item.get(name_key) or '').strip().lower()
        if key in seen:
            duplicates.append(item)
        else:
            seen[key] = item
    return list(seen.values()), duplicates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    apply_changes = args.apply

    token = login()
    print('Режим: %s\n' % ('ЗАПИСЬ' if apply_changes else 'план'))

    # ── Статьи ───────────────────────────────────────────────────────────
    articles = [a for a in call(token, 'GET', '/api/wiki/articles?limit=200')['items']
                if a.get('status') != 'archived']
    keep, dupes = keep_first(articles, 'title')
    print('=== СТАТЬИ ===')
    for item in keep:
        print('   оставляем  id=%-4s %-20s %s' % (item['id'], item['slug'], item['title'][:40]))
    for item in dupes:
        print('   в архив    id=%-4s %-20s %s' % (item['id'], item['slug'], item['title'][:40]))
        if apply_changes:
            call(token, 'DELETE', '/api/wiki/articles/%s' % item['id'])

    # ── Перепривязка файлов оставшихся статей ────────────────────────────
    print('\n=== ПЕРЕПРИВЯЗКА КАРТИНОК ===')
    for item in keep:
        full = call(token, 'GET', '/api/wiki/articles/%s' % item['slug'])
        content = full.get('content') or ''
        refs = len(re.findall(r'/api/wiki/file/', content))
        print('   id=%-4s ссылок на файлы: %s' % (item['id'], refs))
        if apply_changes and refs:
            call(token, 'PATCH', '/api/wiki/articles/%s' % item['id'],
                 {'content': content, 'comment': 'Перепривязка картинок после уборки дублей'})

    # ── Разделы и пространства ───────────────────────────────────────────
    for label, path, endpoint in (('РАЗДЕЛЫ', '/api/wiki/sections', '/api/wiki/sections/%s'),
                                  ('ПРОСТРАНСТВА', '/api/wiki/spaces', '/api/wiki/spaces/%s')):
        items = [x for x in call(token, 'GET', path)['items'] if x.get('status') != 'archived']
        keep_items, dupe_items = keep_first(items)
        print('\n=== %s ===' % label)
        for item in keep_items:
            print('   оставляем  id=%-4s %s' % (item['id'], item['name']))
        for item in dupe_items:
            print('   в архив    id=%-4s %s' % (item['id'], item['name']))
            if apply_changes:
                call(token, 'DELETE', endpoint % item['id'])

    if not apply_changes:
        print('\nЭто был план. Для записи запустите с --apply')


if __name__ == '__main__':
    sys.exit(main())
