"""Перенос листа «Условия выдачи коробов» в раздел «Термокороба» (#363).

С 24.11.2026 прод сам читает лист (thermoboxes/sync.py) — скрипт нужен для
стенда и разового переноса. Лист выгружается из Google-таблицы «Кол-во
термопакетов и термокоробов» в CSV (Файл → Скачать → CSV, текущий лист). Сам
файл в репозиторий не кладётся: это рабочие данные, а не код.

    python scripts/import_thermoboxes.py --csv лист.csv --dsn postgresql://…          # стенд, сухой прогон
    python scripts/import_thermoboxes.py --csv лист.csv --dsn … --office Туркестан=63 # офис вручную
    python scripts/import_thermoboxes.py --csv лист.csv --dsn … --apply               # записать

    # прод — через ручки раздела, под учёткой с правом заводить офисы (глава ФО / админ):
    THERMO_API_LOGIN=… THERMO_API_PASSWORD=… \
    python scripts/import_thermoboxes.py --csv лист.csv --api https://api.icorehub.kz --office … --apply

Что делает:
  * строку листа сопоставляет с офисом справочника вики (тот же справочник, что
    у раздела): город — точно, адрес — по словам и номеру дома со свёрткой
    казахских букв («Азаттык» = «Азаттық»). Не нашёл или нашёл два — строка не
    пишется, пока офис не указан вручную через --office;
  * числа и условия проверяет теми же правилами, что и раздел (rules.py);
  * повторный запуск ничего не дублирует и ничего не затирает: офис уже в
    таблице — строка не трогается, ведь после переноса цифры правят в разделе.
    Перезаписать строки значениями листа — только явно, флагом --overwrite;
  * памятку пишет, только если в разделе она ещё пустая (или с --memo-overwrite):
    после переноса её правит супервайзер, и затирать его правки нельзя.

Куда писать — только явно: --dsn (база стенда) или --api (прод, через ручки
раздела). Скрипт не читает .env и не может по ошибке уйти в боевую базу.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from thermoboxes import queries, rules  # noqa: E402
# Разбор листа — общий с зеркалом раздела (thermoboxes/sync.py): один и тот же
# лист не должен читаться двумя разными способами. memo_kind и memo_text здесь
# не зовутся — их проверяют тесты переноса через этот модуль.
from thermoboxes.sheet import (  # noqa: E402,F401
    SheetError, match_office, memo_kind, memo_text, parse_value,
)
from thermoboxes.sheet import fold as _fold  # noqa: E402
from thermoboxes.sheet import read_csv_text  # noqa: E402

ACTOR = {'user_id': None, 'name': 'Перенос из Google-таблицы'}


def read_sheet(path):
    """(строки офисов, памятка) из CSV листа."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        text = handle.read()
    try:
        return read_csv_text(text)
    except SheetError as exc:
        raise SystemExit(str(exc))


class ApiStore:
    """Запись через ручки раздела (/api/thermoboxes) — путь для прода.

    Пишущего подключения к боевой базе нет, и это правильно: через ручки
    срабатывают те же права и проверки, что у людей, а в истории каждой строки
    остаётся автор — тот, под чьей учёткой запущен перенос. Учётка нужна с
    правом «состав таблицы» (глава фронт-офисов или админ), для памятки — СВ
    или админ. Логин и пароль — из окружения (THERMO_API_LOGIN /
    THERMO_API_PASSWORD), а не аргументами: так они не остаются в истории
    командной строки.
    """

    def __init__(self, base_url):
        import requests

        login = os.environ.get('THERMO_API_LOGIN')
        password = os.environ.get('THERMO_API_PASSWORD')
        if not login or not password:
            raise SystemExit('Задайте THERMO_API_LOGIN и THERMO_API_PASSWORD в окружении')
        self.base = base_url.rstrip('/')
        self.session = requests.Session()
        response = self.session.post(self.base + '/api/login', json={
            'login': login, 'password': password, 'auth_transport': 'bearer'}, timeout=60)
        if response.status_code != 200:
            raise SystemExit('Логин не удался: %s %s' % (response.status_code, response.text[:200]))
        payload = response.json()
        user = payload.get('user') or {}
        if not payload.get('access_token') or not user.get('id'):
            raise SystemExit('Логин вернул неожиданный ответ')
        # Куки логина включают Origin-защиту — работаем только по bearer, как CLI задач.
        self.session.cookies.clear()
        self.session.headers.update({
            'Authorization': 'Bearer %s' % payload['access_token'], 'X-User-Id': str(user['id'])})
        self.actor = user.get('name') or login
        self.screen = self._call('GET', '')

    def _call(self, method, path, body=None):
        response = self.session.request(method, self.base + '/api/thermoboxes' + path, json=body, timeout=120)
        if response.status_code >= 400:
            raise SystemExit('%s /api/thermoboxes%s → %s: %s' % (method, path, response.status_code, response.text[:300]))
        return response.json() if response.content else {}

    def offices(self):
        """Справочник для сопоставления: свободные офисы и офисы уже заведённых строк."""
        found = {office['id']: office for office in self.screen.get('directory') or []}
        for row in self.screen.get('rows') or []:
            if row.get('office_id'):
                found.setdefault(row['office_id'], {
                    'id': row['office_id'], 'city': row['city'], 'name': row['name'], 'address': row['address']})
        return list(found.values())

    def existing(self):
        return {row['office_id']: row for row in self.screen.get('rows') or [] if row.get('office_id')}

    def memo(self):
        return self.screen.get('memo') or {'items': []}

    def apply(self, creates, updates, memo):
        caps = self.screen.get('capabilities') or {}
        if creates and not caps.get('can_manage'):
            raise SystemExit('У учётки %s нет права заводить офисы в таблицу' % self.actor)
        if memo is not None and not caps.get('can_edit_memo'):
            raise SystemExit('У учётки %s нет права править памятку' % self.actor)
        for office, fields in creates:
            self._call('POST', '/rows', dict(fields, office_id=office['id']))
        if updates:
            # Одной пачкой: сервер примет её целиком или не примет вовсе.
            self._call('PUT', '/rows', {'items': [
                dict({change['field']: change['to'] for change in diff}, id=row['id'], version=row['version'])
                for row, diff in updates]})
        if memo is not None:
            self._call('PUT', '/memo', memo)

    def close(self):
        self.session.close()


class DbStore:
    """Запись прямо в базу — для стенда (у прода пишущего подключения нет)."""

    def __init__(self, dsn):
        import psycopg2

        self.connection = psycopg2.connect(dsn)
        self.cursor = self.connection.cursor()
        self.spaces = queries.section_spaces(self.cursor)
        self.actor = ACTOR['name']

    def offices(self):
        return queries.directory(self.cursor, spaces=self.spaces)

    def existing(self):
        return {row['office_id']: row
                for row in queries.list_rows(self.cursor, spaces=self.spaces, include_hidden=True)}

    def memo(self):
        return queries.get_memo(self.cursor)

    def apply(self, creates, updates, memo):
        for office, fields in creates:
            row_id = queries.create_row(self.cursor, office, fields, ACTOR)
            queries.insert_event(self.cursor, row_id, 'created', [], ACTOR)
        for row, diff in updates:
            queries.update_row(self.cursor, row['id'], {change['field']: change['to'] for change in diff}, ACTOR)
            queries.insert_event(self.cursor, row['id'], 'edited', diff, ACTOR)
        if memo is not None:
            queries.save_memo(self.cursor, memo, ACTOR)
        self.connection.commit()

    def close(self):
        self.connection.close()


def build_plan(rows, offices, overrides):
    """[(строка листа, офис, поля, пометка)] и список того, что не переносится."""
    by_id = {office['id']: office for office in offices}
    plan, problems = [], []
    for row in rows:
        override = overrides.get(_fold('%s|%s' % (row['city'], row['address']))) or overrides.get(_fold(row['city']))
        note = None
        if override:
            office = by_id.get(override)
            reason = None if office else 'офиса id %d нет в справочнике раздела' % override
            note = 'указан вручную' if office else None
        else:
            office, reason, note = match_office(row['city'], row['address'], offices)
        try:
            fields = rules.clean_fields({field: parse_value(field, text)
                                         for field, text in row['raw'].items() if field in rules.EDITABLE_FIELDS})
        except rules.FieldError as exc:
            problems.append('строка %d (%s): %s' % (row['line'], row['city'], exc.message))
            continue
        if not office:
            problems.append('строка %d (%s, %s): %s' % (row['line'], row['city'], row['address'], reason))
            continue
        plan.append((row, office, fields, note))

    taken = [office['id'] for _, office, _, _ in plan]
    for row, office, _, _ in plan:
        if taken.count(office['id']) > 1:
            problems.append('строка %d (%s): офис id %d выбран для двух строк листа' % (row['line'], row['city'], office['id']))
    return plan, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--csv', required=True, help='CSV листа «Условия выдачи коробов»')
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--dsn', default=os.environ.get('THERMO_IMPORT_DSN'),
                        help='строка подключения к Postgres (или THERMO_IMPORT_DSN) — стенд')
    target.add_argument('--api', help='адрес API портала, например https://api.icorehub.kz — прод')
    parser.add_argument('--office', action='append', default=[],
                        help='офис вручную: «Город=id» или «Город|адрес из листа=id»')
    parser.add_argument('--apply', action='store_true', help='записать (без него — сухой прогон)')
    parser.add_argument('--overwrite', action='store_true',
                        help='перезаписать значениями листа строки, которые уже есть в разделе')
    parser.add_argument('--memo-overwrite', action='store_true', help='переписать непустую памятку')
    args = parser.parse_args()
    if not args.dsn and not args.api:
        raise SystemExit('Укажите --dsn (стенд) или --api (прод): скрипт не выбирает базу сам')

    rows, memo = read_sheet(args.csv)
    overrides = {}
    for item in args.office:
        key, _, value = item.rpartition('=')
        if not key or not value.strip().isdigit():
            raise SystemExit('--office ждёт «Город=id» или «Город|адрес=id»: %s' % item)
        overrides[_fold(key)] = int(value)

    store = ApiStore(args.api) if args.api else DbStore(args.dsn)
    try:
        existing = store.existing()
        plan, problems = build_plan(rows, store.offices(), overrides)

        print('Куда: %s · от имени: %s' % (args.api or 'база по --dsn', store.actor))
        print('Строк в листе: %d, сопоставлено: %d' % (len(rows), len(plan)))
        for row, office, fields, note in plan:
            if office['id'] not in existing:
                state = 'добавить'
            elif not args.overwrite:
                state = 'уже в таблице — не трогаю'
            else:
                state = 'перезаписать' if rules.changes(existing[office['id']], fields) else 'совпадает'
            print('  %-12s %-40s → id %-3d %-45s [%s%s] %s' % (
                row['city'], row['address'][:40], office['id'], (office.get('address') or '')[:45], state,
                ', ' + note if note else '',
                ' '.join('%s=%s' % (key, value) for key, value in fields.items())))
        if memo['items']:
            print('Памятка: %d пунктов%s' % (len(memo['items']), ', подзаголовок «%s»' % memo['title'] if memo['title'] else ''))
        if problems:
            print('\nНЕ ПЕРЕНОСИТСЯ, пока не решено:')
            for problem in problems:
                print('  ' + problem)
            if args.apply:
                raise SystemExit('Запись отменена целиком: сначала решите строки выше (--office Город=id)')

        creates = [(office, fields) for _, office, fields, _ in plan if office['id'] not in existing]
        updates = []
        for _, office, fields, _ in plan:
            current = existing.get(office['id'])
            if current is not None and args.overwrite:
                diff = rules.changes(current, fields)
                if diff:
                    updates.append((current, diff))
        memo_to_write = None
        if memo['items'] and (not store.memo().get('items') or args.memo_overwrite):
            memo_to_write = rules.clean_memo(memo)

        if not args.apply:
            print('\nСухой прогон: будет добавлено %d, обновлено %d, памятка %s. Записать — с --apply.' % (
                len(creates), len(updates), 'будет записана' if memo_to_write else 'не тронута'))
            return

        store.apply(creates, updates, memo_to_write)
        print('\nЗаписано: добавлено %d, обновлено %d, памятка %s.' % (
            len(creates), len(updates), 'записана' if memo_to_write else 'не тронута'))
    finally:
        store.close()


if __name__ == '__main__':
    main()
