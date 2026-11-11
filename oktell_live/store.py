# -*- coding: utf-8 -*-
"""Последнее, что прислал коннектор Oktell с VM шлюза, — в памяти процесса портала.

Почему память, а не таблица
---------------------------
Снимок приходит раз в десять секунд и через минуту никому не нужен. Строка JSONB на 10–15 КБ,
переписываемая 8,6 тыс. раз в сутки, — это ~100 МБ мёртвых версий и журнала в день ради
данных, которые VM пришлёт заново через секунды после любого перезапуска. Портал поднимается
одним процессом (на этом же стоят кэши табло и одноразовые номера подписи), так что второй
копии, не знающей о снимке, нет. Перезапуск переживает не этот снимок, а готовый снимок табло
в `szov_wallboard_snapshot` — как и раньше.

Когда портал разнесут на роли (deploy/yandex: api / realtime / worker), снимок останется в
памяти той роли, что принимает коннектор (api). Экран табло спрашивает её же и увидит его, а
отбивка, которую собирает worker, — нет: она пойдёт к мосту Oktell. К тому моменту снимку
понадобится общий носитель (Redis или строка в базе с редкой записью).

Свежесть считается по часам портала (момент приёма), а не по часам VM: подпись и так требует,
чтобы они сходились в пределах пяти минут, но для «не старше 45 секунд» это слишком грубо.

Рубильник: `SZOV_WALLBOARD_VM_SOURCE=0` — табло не берёт снимок VM и ходит к мосту Oktell, как
до коннектора. Коннектор при этом продолжает присылать; ручка его принимает.
"""

import logging
import math
import os
import threading
import time

from cdr import oktell_wallboard

log = logging.getLogger(__name__)

ENABLED_ENV = 'SZOV_WALLBOARD_VM_SOURCE'
MAX_AGE_ENV = 'SZOV_WALLBOARD_VM_MAX_AGE_SECONDS'
INTERVAL_ENV = 'OKTELL_LIVE_INTERVAL_SECONDS'

# Снимок старше этого табло не берёт: коннектор шлёт раз в 10 с, так что 45 с — это четыре
# пропущенных такта подряд (перезапуск портала при выкладке, обрыв связи), а не случайная
# задержка одного запроса.
DEFAULT_MAX_AGE_SECONDS = 45
MIN_MAX_AGE_SECONDS = 15
MAX_MAX_AGE_SECONDS = 600

# Такт, который портал называет коннектору в ответе. Меняется выкладкой портала (или env), VM
# трогать не нужно; коннектор держит его в своих границах.
DEFAULT_INTERVAL_SECONDS = 10
MIN_INTERVAL_SECONDS = 3
MAX_INTERVAL_SECONDS = 120

# На линии десятки операторов; тысячи строк — не снимок, а ошибка или чужая рука.
MAX_ROWS = 2000
MAX_TEXT = 200

SOURCE_VM = 'vm'
SOURCE_BRIDGE = 'bridge'

_FALSE = ('0', 'off', 'false', 'no', 'нет')


def _env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.environ.get(name) or '').strip() or default)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def enabled():
    """Берёт ли табло снимок VM. По умолчанию — да."""
    return str(os.environ.get(ENABLED_ENV) or '').strip().lower() not in _FALSE


def max_age_seconds():
    return _env_int(MAX_AGE_ENV, DEFAULT_MAX_AGE_SECONDS, MIN_MAX_AGE_SECONDS, MAX_MAX_AGE_SECONDS)


def interval_seconds():
    return _env_int(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)


def _clean_value(value):
    """Число, короткая строка или None — больше в строке снимка ничего не бывает."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_TEXT]
    return None


def clean_rows(raw):
    """Строки снимка от коннектора → только известные колонки и простые значения.

    ValueError — тело не похоже на снимок: не список, пусто, длиннее потолка или в первой
    строке нет итогов дня (значит, коннектор собран со старым запросом — такой снимок
    обнулил бы показатели на табло молча)."""
    if not isinstance(raw, list):
        raise ValueError('Поле rows — список строк снимка')
    if not raw:
        raise ValueError('Снимок пуст: запрос всегда отдаёт хотя бы строку итогов')
    if len(raw) > MAX_ROWS:
        raise ValueError('Строк в снимке больше %d' % MAX_ROWS)
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Строка снимка — объект с колонками запроса')
        rows.append({column: _clean_value(item.get(column))
                     for column in oktell_wallboard.SNAPSHOT_COLUMNS})
    missing = [column for column in oktell_wallboard.TOTALS_COLUMNS if column not in raw[0]]
    if missing:
        raise ValueError('В снимке нет колонок итогов: %s' % ', '.join(missing))
    return rows


class Store:
    """Снимок табло и состояние коннектора. Один на процесс (`store` ниже)."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._wallboard = None
        self._agent = None
        self._source = None

    # ── коннектор ────────────────────────────────────────────────────────────

    def agent_seen(self, *, hostname=None, version=None, key_id=None, db=None):
        """Коннектор вышел на связь. О смене состояния базы Oktell — строка в журнал (одна на
        смену, а не на каждый такт).

        `db.connected` — три значения: True (читается), False (не читается, причина в error) и
        None — коннектор базу ещё не пробовал (первый запрос после запуска, проверка
        установки). None прежнего знания не стирает и тревоги не поднимает."""
        db = db if isinstance(db, dict) else {}
        reported = db.get('connected')
        error = str(db.get('error') or '')[:300] or None
        now = self._clock()
        with self._lock:
            previous = self._agent
            known = previous['db_connected'] if previous is not None else None
            connected = known if reported is None else bool(reported)
            self._agent = {
                'hostname': str(hostname or '')[:120] or None,
                'version': str(version or '')[:40] or None,
                'key_id': key_id,
                'db_host': str(db.get('host') or '')[:120] or None,
                'db_connected': connected,
                'db_error': error if connected is False else None,
                'seen_at': now,
            }
        if previous is None:
            log.info('Коннектор Oktell на связи: %s, %s, база %s', hostname, version, db.get('host'))
        if connected is not None and connected != known:
            if connected:
                if known is False:
                    log.info('Коннектор Oktell: база Oktell снова отвечает')
            else:
                log.warning('Коннектор Oktell: база Oktell не отвечает — %s', error or 'причина не названа')

    def put_wallboard(self, rows, params, *, taken_at=None, query_ms=None):
        """Принятый снимок табло (строки уже прошли `clean_rows`, параметры сверены)."""
        now = self._clock()
        with self._lock:
            self._wallboard = {
                'rows': rows,
                'params': oktell_wallboard.clean_params(params),
                'received_at': now,
                'taken_at': taken_at if isinstance(taken_at, (int, float)) else None,
                'query_ms': query_ms if isinstance(query_ms, (int, float)) else None,
            }

    # ── табло ────────────────────────────────────────────────────────────────

    def wallboard_rows(self, sl_seconds):
        """Свежий снимок VM для табло или None — тогда табло идёт к мосту Oktell.

        None, если рубильник выключен, снимка нет, он старше `max_age_seconds()` или снят с
        другим порогом SL (порог сменили — следующий такт коннектора привезёт верный)."""
        if not enabled():
            return None
        with self._lock:
            snapshot = self._wallboard
        if snapshot is None:
            return None
        age = self._clock() - snapshot['received_at']
        if age > max_age_seconds():
            return None
        # Сравнение — с порогом как он есть у портала, без приведения к границам: порог вне
        # границ коннектор заменил бы своим, и SL на табло считался бы не тем, что подписан.
        try:
            wanted = int(sl_seconds)
        except (TypeError, ValueError):
            return None
        if snapshot['params']['sl_seconds'] != wanted:
            return None
        return {'rows': snapshot['rows'], 'received_at': snapshot['received_at'],
                'age_seconds': max(0.0, age)}

    def wallboard_newer_than(self, moment):
        """Привёз ли коннектор снимок новее момента `moment` (часы портала) — тогда готовый
        снимок табло пересобирается сразу, не дожидаясь конца своего TTL."""
        if not enabled():
            return False
        with self._lock:
            snapshot = self._wallboard
        if snapshot is None:
            return False
        try:
            moment = float(moment or 0.0)
        except (TypeError, ValueError):
            moment = 0.0
        return (snapshot['received_at'] > moment
                and self._clock() - snapshot['received_at'] <= max_age_seconds())

    def agent_known(self):
        """Выходил ли коннектор на связь за время жизни процесса."""
        with self._lock:
            return self._agent is not None

    def silence_note(self):
        """Почему табло не взяло снимок VM — словами, для текста ошибки и журнала. Пусто,
        если коннектора у портала не было вовсе (или рубильник выключен): тогда путь через
        мост Oktell — обычный, и говорить не о чем."""
        if not enabled():
            return ''
        now = self._clock()
        with self._lock:
            agent, snapshot = self._agent, self._wallboard
        if agent is None:
            return ''
        if now - agent['seen_at'] > max_age_seconds():
            return 'коннектор Oktell на VM шлюза молчит %d с' % int(now - agent['seen_at'])
        if agent['db_connected'] is False:
            return 'коннектор на VM шлюза не читает базу Oktell (%s)' % (agent['db_error'] or 'причина не названа')
        if snapshot is None:
            return 'коннектор на VM шлюза ещё не прислал снимок'
        return 'снимок коннектора на VM шлюза устарел на %d с' % int(now - snapshot['received_at'])

    def note_source(self, source):
        """Кто отдал строки очередного снимка табло. Смена источника — строка в журнал."""
        with self._lock:
            previous, self._source = self._source, source
        if previous == source:
            return
        if source == SOURCE_VM:
            log.info('Табло СЗоВ: снимок даёт коннектор Oktell на VM шлюза')
        elif previous == SOURCE_VM:
            log.warning('Табло СЗоВ: %s — снимок берём у моста Oktell',
                        self.silence_note() or 'снимка VM нет')

    def status(self):
        """Состояние для диагностики: что известно о коннекторе и о его последнем снимке."""
        now = self._clock()
        with self._lock:
            agent, snapshot, source = self._agent, self._wallboard, self._source
        out = {'enabled': enabled(), 'source': source, 'agent': None, 'wallboard': None}
        if agent is not None:
            out['agent'] = dict(agent, age_seconds=int(max(0, now - agent['seen_at'])))
        if snapshot is not None:
            out['wallboard'] = {
                'rows': len(snapshot['rows']),
                'age_seconds': int(max(0, now - snapshot['received_at'])),
                'query_ms': snapshot['query_ms'],
                'params': snapshot['params'],
            }
        return out

    def reset(self):
        with self._lock:
            self._wallboard = None
            self._agent = None
            self._source = None


store = Store()

# Табло зовёт эти функции локальным импортом пакета (тесты вырезают функции монолита через
# AST и исполняют в своём пространстве имён — помощников уровня монолита там нет).
wallboard_rows = store.wallboard_rows
wallboard_newer_than = store.wallboard_newer_than
agent_known = store.agent_known
silence_note = store.silence_note
note_source = store.note_source
status = store.status
