"""Зеркало Google-таблицы: раздел «Термокороба» сам читает лист и обновляется.

С 24.11.2026 источник данных — лист «Условия выдачи коробов» таблицы «Кол-во
термопакетов и термокоробов» (её ведут фронт-офисы). Раздел только показывает:
правка в iCore выключена (access.SHEET_MIRROR), иначе следующее чтение молча
затёрло бы её листом.

Как читается. Скачивается CSV одного этого листа — выгрузкой docs.google.com
по gid. Не весь файл: в соседних листах реестр посылок и журналы выдачи с ФИО
и телефонами курьеров, и на сервер они не попадают. Кто читает — по порядку:

  * аккаунт Google, которому открыта таблица, — его OAuth-токен в
    THERMOBOXES_GOOGLE_OAUTH (адрес аккаунта — там же, не в коде). Пароль
    серверу не нужен: аккаунт один раз разрешает «просмотр файлов Google
    Диска» (scripts/thermoboxes_google_auth.py), сервер хранит только токен. Так
    устроено потому, что управлять доступом к файлу может лишь его владелец, и
    открыть таблицу сервисной учётке редактор не может;
  * иначе — сервисная учётка портала (ключ облачного хранилища,
    GOOGLE_APPLICATION_CREDENTIALS_CONTENT), если владелец файла откроет его
    адресу client_email.

Что делает прогон (run):
  1. скачивает лист — без открытого соединения с базой;
  2. разбирает (sheet.read_csv_text) — лист не разобран → ошибка, таблица
     раздела не тронута;
  3. одной транзакцией приводит таблицу к листу (sheet.plan_mirror): числа и
     условия, новые офисы, офисы, пропавшие из листа, памятка; каждое
     изменение строки пишется в её историю от имени «Google-таблица»;
  4. записывает итог в thermobox_sync — экран показывает, когда лист читали,
     когда цифры менялись и что из листа перенести не удалось.

Повтор без изменений в листе ничего не пишет, кроме отметки «проверено».
"""

import json
import logging
import os

from . import queries, rules, schema, sheet

SHEET_FILE_ID = os.getenv('THERMOBOXES_SHEET_FILE_ID', '1zRWcaoZpZxAVY_QVZwEumVOKUfj-0_tl')
SHEET_GID = os.getenv('THERMOBOXES_SHEET_GID', '2015863297')
EXPORT_URL = 'https://docs.google.com/spreadsheets/d/%s/export'
SHEET_URL = 'https://docs.google.com/spreadsheets/d/%s/edit#gid=%s'

# Только чтение файлов Drive: ничего другого учётке здесь не нужно.
SCOPES = ('https://www.googleapis.com/auth/drive.readonly',)
OAUTH_ENV = 'THERMOBOXES_GOOGLE_OAUTH'
TOKEN_URI = 'https://oauth2.googleapis.com/token'
AUTH_SCRIPT = 'scripts/thermoboxes_google_auth.py'
TIMEOUT_SECONDS = 30
MAX_BYTES = 2 * 1024 * 1024     # лист условий — пара килобайт; мегабайты — не тот лист

ACTOR = {'user_id': None, 'name': 'Google-таблица'}


class SyncError(RuntimeError):
    """Лист не прочитан. message — для человека, на экран раздела."""


def sheet_url():
    return SHEET_URL % (SHEET_FILE_ID, SHEET_GID)


def service_account_info():
    """Ключ сервисной учётки из окружения — общий разбор портала."""
    from call_qa.config import google_sa_info

    return google_sa_info()


def oauth_info():
    """Вход аккаунта Google из окружения: JSON {"account", "client_id",
    "client_secret", "refresh_token"} (его пишет scripts/thermoboxes_google_auth.py).
    Не настроен — None; настроен с ошибкой — SyncError: молча уйти на сервисную
    учётку значило бы спрятать поломку за чужим «нет доступа».

    Читается тем же путём, что и ключ сервисной учётки (call_qa.config.env):
    на Render — переменная окружения, на машине разработчика — ещё и
    .env.codex.local, куда вход записывает скрипт. Иначе локально сервер
    не видел бы записанный вход и читал бы лист сервисной учёткой."""
    from call_qa.config import env

    raw = (env(OAUTH_ENV) or '').strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise SyncError('%s на сервере — не JSON' % OAUTH_ENV)
    missing = [key for key in ('client_id', 'client_secret', 'refresh_token')
               if not isinstance(data, dict) or not data.get(key)]
    if missing:
        raise SyncError('В %s на сервере нет: %s' % (OAUTH_ENV, ', '.join(missing)))
    return data


def reader():
    """Чем читать лист: (сессия, кто читает, вид — 'oauth' или 'service')."""
    from google.auth.transport.requests import AuthorizedSession

    oauth = oauth_info()
    if oauth:
        from google.oauth2.credentials import Credentials

        credentials = Credentials(None, refresh_token=oauth['refresh_token'], client_id=oauth['client_id'],
                                  client_secret=oauth['client_secret'], token_uri=TOKEN_URI, scopes=list(SCOPES))
        return AuthorizedSession(credentials), oauth.get('account') or 'аккаунт Google', 'oauth'
    info = service_account_info()
    if not info:
        raise SyncError('Серверу нечем читать Google-таблицу: нет ни входа аккаунта (%s), ни ключа '
                        'сервисной учётки (GOOGLE_APPLICATION_CREDENTIALS_CONTENT)' % OAUTH_ENV)
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(info, scopes=list(SCOPES))
    return AuthorizedSession(credentials), info.get('client_email') or 'сервисная учётка', 'service'


def _no_access(who, kind):
    if kind == 'oauth':
        return 'Нет доступа к Google-таблице у %s — проверьте, что таблица ему открыта' % who
    # Сервисной учётке таблицу может открыть только владелец файла, поэтому
    # основной путь — вход аккаунта, которому она уже открыта.
    return ('Нет доступа к Google-таблице: сервер читает её сервисной учёткой %s, а ей таблица не открыта. '
            'Нужен вход аккаунта, которому таблица открыта (%s), или доступ на чтение этому адресу '
            'от владельца файла' % (who, AUTH_SCRIPT))


def _api_disabled(response):
    """403 «API выключен в проекте» — не то же, что «нет доступа к файлу»:
    чинится не доступом, а одной кнопкой в консоли."""
    try:
        text = response.content[:4000].decode('utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        return False
    return any(marker in text for marker in ('accessNotConfigured', 'SERVICE_DISABLED',
                                             'has not been used in project'))


def fetch_csv(session=None, who='', kind='service'):
    """CSV листа условий. Ошибка — SyncError с понятной причиной."""
    if session is None:
        session, who, kind = reader()
    from google.auth.exceptions import RefreshError

    try:
        response = session.get(EXPORT_URL % SHEET_FILE_ID, params={'format': 'csv', 'gid': SHEET_GID},
                               timeout=TIMEOUT_SECONDS)
    except RefreshError as exc:
        if kind == 'oauth':
            raise SyncError('Вход %s в Google больше не действует (%s) — подтвердите его заново: %s'
                            % (who, str(exc)[:80], AUTH_SCRIPT))
        raise SyncError('Ключ сервисной учётки не принят Google: %s' % str(exc)[:120])
    except Exception as exc:  # noqa: BLE001 — сеть, таймаут: причина уходит на экран
        raise SyncError('Google-таблица не ответила: %s' % str(exc)[:160])
    content_type = (response.headers.get('content-type') or '').lower()
    if response.status_code == 403 and _api_disabled(response):
        raise SyncError('В проекте Google Cloud выключен Google Drive API — включите его, и лист начнёт читаться')
    if response.status_code in (401, 403, 404) or (response.ok and 'text/html' in content_type):
        raise SyncError(_no_access(who, kind))
    if not response.ok:
        raise SyncError('Google-таблица ответила %s' % response.status_code)
    if 'csv' not in content_type and 'text/plain' not in content_type:
        raise SyncError('Google-таблица вернула не лист, а %s' % (content_type or 'пустой ответ'))
    if len(response.content) > MAX_BYTES:
        raise SyncError('Лист слишком большой (%d КБ) — проверьте номер листа (gid)' % (len(response.content) // 1024))
    return response.content.decode('utf-8-sig', errors='replace')


def _apply(cursor, plan, memo, spaces):
    """Пишет план. Возвращает сводку; ничего не поменялось — все нули."""
    done = {'updated': 0, 'created': 0, 'hidden': 0, 'shown': 0, 'memo': False, 'offices': []}
    for item in plan['updates']:
        queries.update_row(cursor, item['row_id'], item['fields'], ACTOR)
        queries.insert_event(cursor, item['row_id'], 'edited', item['diff'], ACTOR)
        done['updated'] += 1
        done['offices'].append(item.get('city'))
    for item in plan['keys']:
        queries.set_sheet_key(cursor, item['row_id'], item['sheet_key'])
    for item in plan['creates']:
        row_id = queries.create_row(cursor, item['office'], item['fields'], ACTOR)
        queries.set_sheet_key(cursor, row_id, item['sheet_key'])
        queries.insert_event(cursor, row_id, 'created', [], ACTOR)
        done['created'] += 1
        done['offices'].append(item['office'].get('city'))
    for row_id in plan['show']:
        queries.set_active(cursor, row_id, True, ACTOR)
        queries.insert_event(cursor, row_id, 'shown', [], ACTOR)
        done['shown'] += 1
    for row_id in plan['hide']:
        queries.set_active(cursor, row_id, False, ACTOR)
        queries.insert_event(cursor, row_id, 'hidden', [], ACTOR)
        done['hidden'] += 1
    if memo is not None:
        queries.save_memo(cursor, memo, ACTOR)
        done['memo'] = True
    return done


def changed(done):
    return bool(done['updated'] or done['created'] or done['hidden'] or done['shown'] or done['memo'])


def run(db, *, triggered_by='scheduler', fetch=None):
    """Один прогон зеркала. Возвращает итог (как в thermobox_sync) и не бросает:
    ошибка листа записывается в состояние, данные раздела остаются прежними.
    Ошибка базы посреди записи откатывает весь план и тоже записывается в
    состояние — иначе экран показывал бы прошлое «сверено», а каждый прогон
    падал бы молча."""
    try:
        return _run(db, triggered_by=triggered_by, fetch=fetch or fetch_csv)
    except Exception as exc:  # noqa: BLE001 — причина уходит на экран администратору
        error = 'Лист прочитан, но не записан: %s' % (str(exc).strip().splitlines() or [''])[0][:160]
        repeated = False
        try:
            with db._get_cursor() as cursor:
                previous = queries.get_sync_state(cursor)
                repeated = previous.get('status') == 'error' and previous.get('error') == error
                queries.save_sync_state(cursor, status='error', error=error,
                                        summary={'triggered_by': triggered_by})
        except Exception:  # noqa: BLE001 — база недоступна: остаётся лог ниже
            logging.exception('thermoboxes sync: состояние ошибки не записано')
        # Как и с ошибкой чтения: та же поломка каждую минуту — не новость.
        if repeated:
            logging.debug('thermoboxes sync (%s): %s', triggered_by, error)
        else:
            logging.error('thermoboxes sync (%s): лист не записан', triggered_by, exc_info=exc)
        return {'status': 'error', 'error': error}


def _run(db, *, triggered_by, fetch):
    error = None
    rows = memo = None
    memo_problems = []
    try:
        text = fetch()
        rows, sheet_memo = sheet.read_csv_text(text)
    except (SyncError, sheet.SheetError) as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        logging.exception('thermoboxes sync: лист не прочитан')
        error = 'Лист не прочитан: %s' % str(exc)[:160]
    else:
        # Памятка отдельно от офисов: слишком длинный пункт не должен
        # останавливать цифры — он попадает в «не перенесено».
        if sheet_memo.get('items'):
            try:
                memo = rules.clean_memo(sheet_memo)
            except rules.FieldError as exc:
                memo_problems.append('Памятка: %s' % exc.message)

    with db._get_cursor() as cursor:
        if not schema.schema_is_ready(cursor):
            return {'status': None, 'error': 'Раздел ещё разворачивается'}
        previous = queries.get_sync_state(cursor, lock=True)
        if error is None:
            spaces = queries.section_spaces(cursor)
            existing = queries.list_rows(cursor, spaces=spaces, include_hidden=True)
            taken = queries.taken_office_ids(cursor)
            directory = [office for office in queries.directory(cursor, spaces=spaces)
                         if office['id'] not in taken]
            try:
                plan = sheet.plan_mirror(rows, existing, directory)
            except sheet.SheetError as exc:
                error = str(exc)
        if error is not None:
            queries.save_sync_state(cursor, status='error', error=error,
                                    summary={'triggered_by': triggered_by})
            # Та же ошибка, что и в прошлый раз, — не новость: плановое чтение
            # идёт каждую минуту, и лог иначе забила бы одна и та же строка.
            repeated = previous.get('status') == 'error' and previous.get('error') == error
            (logging.debug if repeated else logging.warning)('thermoboxes sync (%s): %s', triggered_by, error)
            return {'status': 'error', 'error': error}

        if memo is not None and not sheet.memo_differs(queries.get_memo(cursor), memo):
            memo = None
        plan['problems'] = plan['problems'] + memo_problems
        done = _apply(cursor, plan, memo, spaces)
        summary = dict(done, rows=len(rows), triggered_by=triggered_by)
        queries.save_sync_state(cursor, status='ok', problems=plan['problems'], summary=summary,
                                read=True, changed=changed(done))
    if changed(done) or plan['problems']:
        logging.info('thermoboxes sync (%s): %s, не перенесено %d', triggered_by,
                     {key: value for key, value in done.items() if key != 'offices'}, len(plan['problems']))
    return {'status': 'ok', 'error': None, 'problems': plan['problems'], 'summary': summary}
