"""Слепок «Классификатора авто» в базе и его сверка с базой знаний Яндекс Про.

Справочник собирается разбором страниц auto-list всех городов Казахстана
(wiki/classifier_source.py) и лежит одной строкой в wiki_classifier_snapshot.
Его читают все: калькулятор в статье и бар машины в поиске
(wiki/routes_classifier.py), помощник вики и партнёрский API
(wiki/classifier.py), отбор «Наличия авто» (rental/tariffs.py). Второй копии
нет: обновился слепок — обновилось везде.

Пока слепка в базе нет (свежая база, первый запуск после выкладки), читается
файл wiki/data/classifier-data.json — тот же справочник, собранный тем же
разбором на дату коммита (scripts/classifier_refresh.py).

ПОРЯДОК СВЕРКИ (sync):
  1. страница Алматы — по ней же берётся список городов Казахстана у Яндекса;
  2. остальные города — по одному, с паузой: 25 запросов подряд Яндекс
     встречает страницей без __NEXT_DATA__ (ловушка из yandex_sync.read_source);
  3. всё качается БЕЗ открытого соединения: пул вики — сорок мест, и держать
     одно на минуту сети нельзя (шапка wiki/importer.py); курсор — только на
     короткую запись в конце.

ЗАЩИТА ОТ ПОЛУПУСТОГО ОТВЕТА. Город не открылся (кроме 404 — тогда страницы
у города больше нет) — слепок не трогаем вовсе: неполный справочник хуже
вчерашнего, потому что тихо отнял бы город у калькулятора. Новый справочник
меньше прежнего больше чем на MIN_KEEP_SHARE по строкам или городам — тоже
отказ: так выглядит заглушка, а не правка Яндекса. Причина пишется в строку
слепка (error), прежний справочник остаётся.

Неизменившийся источник не трогает ни данные, ни changed_at — только
checked_at: «обновлено» на экране — дата настоящего изменения, а не прогона.
"""

import json
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import classifier_source as source
from . import yandex_sync

logger = logging.getLogger(__name__)

ALMATY = ZoneInfo('Asia/Almaty')
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data',
                         'classifier-data.json')

PAUSE_SECONDS = 1.0
RETRY_PAUSE = 2.0
# Сверка раз в сутки; догоняющий прогон после деплоя берёт слепок старше этого.
MAX_AGE_HOURS = 20
# Новый справочник обязан сохранить не меньше этой доли строк и городов прежнего.
MIN_KEEP_SHARE = 0.6

_TABLE = 'wiki_classifier_snapshot'


class FetchError(Exception):
    """Страница города не открылась. gone=True — 404: страницы больше нет."""

    def __init__(self, message, gone=False):
        super().__init__(message)
        self.gone = gone


# ── Файл ────────────────────────────────────────────────────────────────────

def read_bundled(path=None):
    with open(path or DATA_PATH, encoding='utf-8') as handle:
        return json.load(handle)


def write_bundled(data, path=None):
    """Файл справочника — одной строкой, ключи по порядку: дифф видно по сути."""
    target = path or DATA_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        handle.write('\n')


# ── База ────────────────────────────────────────────────────────────────────

def table_exists(cursor):
    # Без схемы — как все запросы к таблице: имя ищется по search_path.
    cursor.execute('SELECT to_regclass(%s)', (_TABLE,))
    row = cursor.fetchone()
    return bool(row and row[0])


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def snapshot_state(cursor):
    """Отпечаток и время слепка без самих данных — дёшево, для проверки кэша.

    None — слепка нет (или таблицы ещё нет: схема не прогнана)."""
    if not table_exists(cursor):
        return None
    cursor.execute('SELECT digest, source_updated, checked_at, changed_at, error '
                   'FROM wiki_classifier_snapshot WHERE id = 1')
    row = cursor.fetchone()
    if not row:
        return None
    return {'digest': row[0], 'source_updated': row[1], 'checked_at': _iso(row[2]),
            'changed_at': _iso(row[3]), 'error': row[4]}


def load_snapshot(cursor):
    """Слепок целиком: state + data. None — слепка нет."""
    state = snapshot_state(cursor)
    if state is None:
        return None
    cursor.execute('SELECT data FROM wiki_classifier_snapshot WHERE id = 1')
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return dict(state, data=data)


def store(cursor, data, *, warnings=()):
    """Записать справочник. 'changed' — содержимое новое, 'same' — как было."""
    from psycopg2.extras import Json
    digest = source.fingerprint(data)
    cursor.execute('SELECT digest FROM wiki_classifier_snapshot WHERE id = 1 FOR UPDATE')
    row = cursor.fetchone()
    source_updated = (data.get('source') or {}).get('updated') or None
    if row and row[0] == digest:
        cursor.execute(
            "UPDATE wiki_classifier_snapshot SET error = NULL, warnings = %s, "
            "checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = 1",
            (Json(list(warnings)),))
        return 'same'
    if row:
        cursor.execute(
            """
            UPDATE wiki_classifier_snapshot
               SET data = %s, digest = %s, source_updated = %s, error = NULL, warnings = %s,
                   checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                   changed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = 1
            """,
            (Json(data), digest, source_updated, Json(list(warnings))))
    else:
        cursor.execute(
            'INSERT INTO wiki_classifier_snapshot (id, data, digest, source_updated, warnings) '
            'VALUES (1, %s, %s, %s, %s)',
            (Json(data), digest, source_updated, Json(list(warnings))))
    return 'changed'


def mark_error(cursor, error):
    """Сверка не удалась: причина — в строку слепка, данные не трогаем."""
    if not table_exists(cursor):
        return
    cursor.execute(
        "UPDATE wiki_classifier_snapshot SET error = %s, "
        "checked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = 1",
        (str(error)[:1000],))


def is_due(cursor, *, max_age_hours=MAX_AGE_HOURS):
    """Пора ли сверять: слепка нет или он проверялся давно."""
    if not table_exists(cursor):
        return False
    cursor.execute(
        "SELECT 1 FROM wiki_classifier_snapshot WHERE id = 1 "
        "AND checked_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') "
        "- make_interval(hours => %s)", (int(max_age_hours),))
    return cursor.fetchone() is None


def current_data(cursor=None):
    """(справочник, откуда): слепок из базы, а без него — файл из репозитория."""
    if cursor is not None:
        snapshot = load_snapshot(cursor)
        if snapshot and snapshot.get('data'):
            return snapshot['data'], 'snapshot'
    return read_bundled(), 'file'


# ── Сеть ────────────────────────────────────────────────────────────────────

def fetch_page(url):
    """HTML страницы — тем же загрузчиком, что у сверки статей Яндекс Про.

    404 — FetchError(gone=True): страницы города больше нет, это не сбой."""
    try:
        return yandex_sync.fetch_page(url)
    except yandex_sync.SyncError as error:
        raise FetchError(str(error), gone='(404)' in str(error))


def _get(fetch, url):
    """HTML страницы любым загрузчиком; ошибки загрузчика вики — в FetchError."""
    try:
        return fetch(url)
    except yandex_sync.SyncError as error:
        raise FetchError(str(error), gone='(404)' in str(error))


def read_city(code, *, fetch=None, sleep=time.sleep):
    """Состояние страницы города. Одна повторная попытка на «страницу без
    __NEXT_DATA__» — так Яндекс отвечает на частые обращения."""
    fetch = fetch or fetch_page
    url = source.city_url(code)
    try:
        return source.extract_state(_get(fetch, url))
    except source.ClassifierSourceError:
        pass
    sleep(RETRY_PAUSE)
    try:
        return source.extract_state(_get(fetch, url))
    except source.ClassifierSourceError as error:
        raise FetchError('%s: %s' % (code, error))


def fetch_all(*, fetch=None, pause=PAUSE_SECONDS, sleep=time.sleep):
    """Страницы всех городов: (pages, cities, failed, gone).

    pages — {код: состояние}; cities — список городов со страницы Алматы;
    failed — {код: причина} по городам, которые не открылись; gone — коды с 404.
    Страница Алматы обязательна: без неё нет списка городов, и FetchError летит
    наружу.
    """
    try:
        almaty = read_city('almaty', fetch=fetch, sleep=sleep)
    except FetchError:
        raise
    except Exception as error:                         # noqa: BLE001
        # Подменённый загрузчик (тесты, скрипт) бросает что угодно — для сверки
        # это одно и то же: страница Алматы не получена.
        raise FetchError('almaty: %s' % (str(error)[:200] or type(error).__name__))
    cities = source.kazakhstan_cities(almaty)
    if not cities:
        cities = [{'code': code, 'name': name, 'yandex_id': None}
                  for code, name in source.FALLBACK_CITIES]
    pages, failed, gone = {'almaty': almaty}, {}, []
    for city in cities:
        code = city['code']
        if code in pages:
            continue
        if pause:
            sleep(pause)
        try:
            pages[code] = read_city(code, fetch=fetch, sleep=sleep)
        except FetchError as error:
            if error.gone:
                gone.append(code)
            else:
                failed[code] = str(error)
        except Exception as error:                     # noqa: BLE001
            logger.exception('Классификатор: страница %s не разобралась', code)
            failed[code] = str(error)[:200] or 'Ошибка разбора страницы'
    return pages, cities, failed, gone


def rows_total(data):
    return sum(len(item.get('rows') or ()) for item in (data or {}).get('lists') or ())


def shrink_reason(new, old):
    """Почему новый справочник подозрительно меньше прежнего — или None."""
    if not old:
        return None
    old_rows, new_rows = rows_total(old), rows_total(new)
    old_cities, new_cities = len(old.get('cities') or ()), len(new.get('cities') or ())
    if old_rows and new_rows < old_rows * MIN_KEEP_SHARE:
        return 'строк стало %d вместо %d' % (new_rows, old_rows)
    if old_cities and new_cities < old_cities * MIN_KEEP_SHARE:
        return 'городов стало %d вместо %d' % (new_cities, old_cities)
    return None


def build_now(*, fetch=None, pause=PAUSE_SECONDS, sleep=time.sleep, now=None):
    """Скачать и собрать: (справочник, предупреждения, failed). Без базы."""
    moment = now or datetime.now(ALMATY)
    pages, cities, failed, gone = fetch_all(fetch=fetch, pause=pause, sleep=sleep)
    data, warnings = source.build(pages, today=moment.date(),
                                  checked_at=moment.replace(microsecond=0).isoformat(),
                                  cities=[city for city in cities if city['code'] not in gone])
    if gone:
        warnings.append('Страниц больше нет (404): %s' % ', '.join(sorted(gone)))
    return data, warnings, failed


def sync(db, *, fetch=None, pause=PAUSE_SECONDS, sleep=time.sleep, now=None, on_change=None):
    """Сверка с Яндексом: скачать всё без курсора, записать коротким тактом.

    db — объект с _get_cursor() (тот же, что у Blueprint вики). on_change —
    вызывается после записи изменившегося справочника (сброс кэша книги).
    Возвращает сводку для журнала: {'status', 'cities', 'rows', 'error', 'warnings'}.
    """
    summary = {'status': None, 'cities': 0, 'rows': 0, 'error': None, 'warnings': 0}
    try:
        data, warnings, failed = build_now(fetch=fetch, pause=pause, sleep=sleep, now=now)
    except (FetchError, source.ClassifierSourceError) as error:
        reason = 'Страница Алматы не открылась: %s' % str(error)[:300]
        logger.warning('Классификатор: %s', reason)
        with db._get_cursor() as cursor:
            mark_error(cursor, reason)
        return dict(summary, status='error', error=reason)
    except Exception as error:                         # noqa: BLE001
        # Ошибка разбора — это уже наш код, а не сеть: трейсбек в журнал, причина
        # в строку слепка, прежний справочник остаётся.
        logger.exception('Классификатор: справочник не собрался')
        reason = 'Справочник не собрался: %s' % str(error)[:300]
        with db._get_cursor() as cursor:
            mark_error(cursor, reason)
        return dict(summary, status='error', error=reason)
    summary.update(cities=len(data['cities']), rows=rows_total(data), warnings=len(warnings))
    if failed:
        reason = 'Не открылись страницы: %s' % '; '.join(
            '%s — %s' % (code, text) for code, text in sorted(failed.items()))
        with db._get_cursor() as cursor:
            mark_error(cursor, reason[:1000])
        return dict(summary, status='error', error=reason)
    with db._get_cursor() as cursor:
        previous = load_snapshot(cursor)
        reason = shrink_reason(data, (previous or {}).get('data'))
        if reason:
            mark_error(cursor, 'Справочник подозрительно уменьшился (%s) — не записан' % reason)
            return dict(summary, status='error', error=reason)
        status = store(cursor, data, warnings=warnings)
    if status == 'changed' and on_change:
        on_change()
    for line in warnings:
        logger.info('Классификатор: %s', line)
    return dict(summary, status=status)
