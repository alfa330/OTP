# -*- coding: utf-8 -*-
"""Коннектор АТС: живые события звонков станции FreePBX → портал, команды портала → станция.

Зачем, если есть мост «Касаний»
--------------------------------
Мост читает CDR и журнал очередей из базы станции раз в 20 секунд — он видит звонок, когда тот
уже записан, то есть после отбоя. Коннектор сидит на AMI станции (как коннекторы amoCRM/itgrix
и FOP2 той же учёткой) и видит звонок в момент, когда он идёт: клиент в очереди, телефон
оператора звонит, разговор, трубку положил такой-то. И умеет обратное — то, чего у моста
нет по замыслу: позвонить клиенту с телефона оператора (click-to-call), положить трубку,
подключить супервайзера к разговору (слушать, подсказывать, войти).

Как устроено (2.0.0 — без опроса)
---------------------------------
    станция 192.168.88.251:5038 ──AMI──► коннектор (VM шлюза)
                                          ├─ отправка: POST /api/pbx/agent/sync СРАЗУ по событию
                                          └─ канал команд: POST /api/pbx/agent/stream (SSE, держится)

Три потока, и ни один не крутит опрос:
* **AMI** (`cdr_bridge/ami.py`) — постоянное соединение, события `call,agent,dialing,originate`.
  Разбор в состояние звонков — общий модуль `cdr/pbx_calls.py` (его же импортирует портал).
  Событие, которое ничего не меняет на экране, наружу не выходит (отпечаток звонка).
* **Отправка** — спит, пока нечего слать. Изменение будит её; она ждёт COALESCE_SECONDS, чтобы
  всплеск событий одного звонка (десяток за 100 мс) ушёл одной пачкой, и отправляет. Без
  изменений — пульс раз в HEARTBEAT_SECONDS, раз в SNAPSHOT_SECONDS — полный снимок (портал
  закрывает звонки, чей отбой потерялся). Соединение с порталом держится открытым (keep-alive)
  — пачка не платит за TLS заново.
* **Канал команд** — открытый ответ SSE от портала: команда приходит в момент, когда человек
  нажал «позвонить», а не на следующем опросе. Портал шлёт пульс раз в 15 с; 45 с тишины —
  соединение мёртвое, переподключаемся. Пока канал лежит, отправка становится частой
  (FALLBACK_POLL_SECONDS) и забирает команды из ответа на пачку: так коннектор работает и
  тогда, когда что-то по дороге режет долгие ответы.

При старте и после каждого переподключения к AMI — `CoreShowChannels` с чистого листа и полный
снимок: звонки, начавшиеся без нас, попадают в состояние, а потерянные — закрываются порталом.

Команды портала
---------------
    {id, kind: 'originate', ext, dial, caller_name}   позвонить: телефон ext → набор dial
    {id, kind: 'spy', ext, target_channel, mode}      ext слушает/подсказывает/входит в разговор
    {id, kind: 'hangup', channel}                     положить трубку каналу
Итог уходит порталу ближайшей пачкой (`results`): accepted — станция приняла (телефон звонит),
done — телефон ответил, failed — отказ с текстом. Кому что можно, решает портал по своим
правам; коннектор исполняет только подписанное им и только эти три вида.

Чему коннектор НЕ верит
-----------------------
Порталу — не безусловно: номер набора проверяется формой `<4 цифры>*7<10 цифр>` или внутренний
номер, канал для Hangup — только из тех, что коннектор сам видел в событиях. HTTPS_PROXY и
подобные переменные не читаются — наружу только через прокси из настроек (как у моста).

Запуск
------
    python -m cdr_bridge.pbx_connector          рабочий режим (служба pbx-connector)
    python -m cdr_bridge.pbx_connector --once   вход в AMI, снимок, одна пачка, выход

Настройки — окружение (или .env.codex.local при отладке):
    CDR_BRIDGE_PORTAL, CDR_AGENT_KEY_FILE | CDR_AGENT_PRIVATE_KEY, CDR_PORTAL_PROXY,
    CDR_PORTAL_CA_BUNDLE        — как у моста;
    PBX_AMI_HOST / _PORT        станция, 5038
    PBX_AMI_USER / _SECRET      учётка AMI (на отладке — AMI_USER/AMI_SECRET из доступов)
    PBX_SNAPSHOT_SECONDS        полный снимок (60)
    PBX_RECORDING_LOOKUP        1 — спрашивать у станции имя файла записи при ответе (Getvar)
    PBX_STREAM                  0 — не держать канал команд (только запасной частый опрос)
    CDR_PBXDB_HOST/_PORT/_USER/_PASSWORD   база станции для линий (необязательно)
    PBX_HEARTBEAT_FILE          файл пульса для проверки живости контейнера
"""

import argparse
import json
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import pbx_calls  # noqa: E402
from cdr_bridge import ami as ami_mod  # noqa: E402
from cdr_bridge.agent import Bridge, _env_file_values  # noqa: E402

VERSION = 'pbx/2.0.0'
log = logging.getLogger('cdr_bridge.pbx_connector')

PORTAL_TIMEOUT = (10, 30)
# Канал команд: портал шлёт пульс раз в 15 с — три пропущенных подряд значат мёртвое соединение.
STREAM_TIMEOUT = (10, 45)
COALESCE_SECONDS = 0.15         # всплеск событий одного звонка — одной пачкой
HEARTBEAT_SECONDS = 15.0        # пульс при тишине, когда канал команд жив
FALLBACK_POLL_SECONDS = 2.0     # канала команд нет — команды забираются из ответа на пачку
SNAPSHOT_SECONDS = 60.0
LINES_REFRESH_SECONDS = 3600.0
EXPIRE_EVERY_SECONDS = 30.0
MAINTENANCE_TICK_SECONDS = 1.0
RECONNECT_SECONDS = (5, 15, 30, 60)
PORTAL_BACKOFF_SECONDS = (2, 5, 15, 30, 60)
STREAM_BACKOFF_SECONDS = (1, 3, 10, 30, 60)
# Канал выключен на портале (503 PBX_STREAM_OFF) или его нет вовсе (старый портал, 404) —
# не стучимся каждую секунду: проверяем раз в пять минут, а пока работаем запасным путём.
STREAM_OFF_RETRY_SECONDS = 300
MAX_EVENTS_PER_SYNC = 500
# Полный снимок: идущие и законченные за последнюю минуту; потолок — тот же, что принимает портал.
SNAPSHOT_ENDED_SECONDS = 60
MAX_SNAPSHOT_CALLS = 2000
# Сверка со станцией (CoreShowChannels): каналы, чей отбой потерялся, закрываются.
RECONCILE_SECONDS = 300

_DIAL_RE = re.compile(r'^\d{4}\*7\d{10}$')
_EXT_RE = pbx_calls.EXT_RE
_CHANNEL_RE = re.compile(r'^PJSIP/[0-9A-Za-z_+.\-]+-[0-9a-fA-F]+$')
_PREFIX_RE = re.compile(r'^(\d{4})\*8?$')

# Q.850 → слова для итога неудачного Originate (Reason у OriginateResponse: 0 — нет такого
# номера/канал не создан, 1 — не ответил/отменён, 3 — не ответил, 5 — занято, 8 — перегрузка).
ORIGINATE_REASONS = {
    '0': 'станция не смогла поднять канал (номер не зарегистрирован?)',
    '1': 'телефон не ответил',
    '3': 'телефон не ответил',
    '4': 'принято',
    '5': 'телефон занят',
    '8': 'перегрузка или отказ сети',
}


def load_config():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev = _env_file_values(os.path.join(root, '.env.codex.local'))

    def value(key, default=''):
        return (os.environ.get(key) or dev.get(key) or default).strip()

    def number(key, default):
        try:
            return float(value(key, str(default)) or default)
        except ValueError:
            return default

    return {
        'portal': value('CDR_BRIDGE_PORTAL').rstrip('/'),
        'key_file': value('CDR_AGENT_KEY_FILE'),
        'private_key': value('CDR_AGENT_PRIVATE_KEY'),
        'proxy': value('CDR_PORTAL_PROXY'),
        'ca_bundle': value('CDR_PORTAL_CA_BUNDLE'),
        'ami_host': value('PBX_AMI_HOST') or value('AMI_HOST', '192.168.88.251'),
        'ami_port': int(number('PBX_AMI_PORT', 0) or number('AMI_PORT', 5038)),
        'ami_user': value('PBX_AMI_USER') or value('AMI_USER'),
        'ami_secret': value('PBX_AMI_SECRET') or value('AMI_SECRET'),
        'snapshot_seconds': max(10.0, number('PBX_SNAPSHOT_SECONDS', SNAPSHOT_SECONDS)),
        'recording_lookup': value('PBX_RECORDING_LOOKUP', '1') not in ('0', 'no', 'false', ''),
        'stream': value('PBX_STREAM', '1') not in ('0', 'no', 'false', ''),
        'pbxdb_host': value('CDR_PBXDB_HOST') or value('FREEPBX_MYSQL_HOST'),
        'pbxdb_port': int(number('CDR_PBXDB_PORT', 0) or number('FREEPBX_MYSQL_PORT', 3306)),
        'pbxdb_user': value('CDR_PBXDB_USER') or value('FREEPBX_MYSQL_USER'),
        'pbxdb_password': value('CDR_PBXDB_PASSWORD') or value('FREEPBX_MYSQL_PASSWORD'),
        'heartbeat_file': value('PBX_HEARTBEAT_FILE'),
    }


# ── линии станции: префикс набора → номер ─────────────────────────────────────

def parse_outbound_lines(rows):
    """Строки (имя маршрута, префикс шаблона) → {префикс: {'number': …, 'name': …}}.

    Имя маршрута на станции начинается с номера линии (`77475550033_OP_OS`), префикс шаблона
    — `3333*` или `3333*8` (второй — набор через восьмёрку, та же линия). Маршрут без номера
    в имени линию не называет и пропускается."""
    out = {}
    for name, prefix in rows:
        match = _PREFIX_RE.match(str(prefix or '').strip())
        if not match:
            continue
        key = match.group(1)
        digits = re.match(r'^\+?(\d{10,12})', str(name or '').strip())
        number = pbx_calls.touches_mod.norm_phone(digits.group(1)) if digits else ''
        if not number:
            continue
        label = re.sub(r'^\+?\d+[_\s-]*', '', str(name or '')).replace('_', ' ').strip()
        current = out.get(key)
        if current is None or (not current['name'] and label):
            out[key] = {'number': number, 'name': label[:60]}
    return out


def read_outbound_lines(config):
    """Линии из базы станции (SELECT по двум справочным таблицам). Нет настроек, драйвера или
    ответа — {} и строка ошибки: без линий коннектор работает, просто портал не предложит,
    откуда перезвонить."""
    if not config.get('pbxdb_host') or not config.get('pbxdb_user'):
        return {}, ''
    try:
        import pymysql
    except ImportError:
        return {}, 'pymysql не установлен'
    try:
        connection = pymysql.connect(
            host=config['pbxdb_host'], port=int(config['pbxdb_port'] or 3306),
            user=config['pbxdb_user'], password=config['pbxdb_password'], database='asterisk',
            connect_timeout=8, read_timeout=15, write_timeout=15, autocommit=True, charset='utf8mb4')
    except Exception as exc:  # noqa: BLE001
        return {}, 'база станции: %s' % exc
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT r.name, p.match_pattern_prefix "
                "  FROM asterisk.outbound_routes r "
                "  JOIN asterisk.outbound_route_patterns p ON p.route_id = r.route_id "
                " WHERE p.match_pattern_prefix LIKE '____*%' LIMIT 500")
            rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        return {}, 'база станции: %s' % exc
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
    return parse_outbound_lines(rows), ''


# ── разбор SSE канала команд ─────────────────────────────────────────────────

class SseParser:
    """Байты ответа → события SSE (имя, data-строка). Комментарии (`: ping`) отдаются как
    ('', текст комментария): по ним видно, что канал жив и что портал принял подключение."""

    def __init__(self):
        self._buffer = b''
        self._event = ''
        self._data = []

    def feed(self, chunk):
        out = []
        self._buffer += chunk
        while True:
            index = self._buffer.find(b'\n')
            if index < 0:
                break
            raw, self._buffer = self._buffer[:index], self._buffer[index + 1:]
            line = raw.decode('utf-8', 'replace').rstrip('\r')
            if not line:
                if self._event or self._data:
                    out.append((self._event or 'message', '\n'.join(self._data)))
                self._event, self._data = '', []
                continue
            if line.startswith(':'):
                out.append(('', line[1:].strip()))
            elif line.startswith('event:'):
                self._event = line[6:].strip()
            elif line.startswith('data:'):
                self._data.append(line[5:].lstrip())
        return out


# ── коннектор ────────────────────────────────────────────────────────────────

class Connector:
    def __init__(self, config, session=None, stream_session=None, ami_factory=None,
                 clock=time.time, lines_reader=read_outbound_lines):
        self.config = config
        self.portal = config['portal']
        # Две сессии: отправка и канал команд работают из разных потоков, а requests.Session
        # не рассчитана на одновременные запросы; канал к тому же держит соединение часами.
        self.session = session or Bridge._build_session(config)
        self.stream_session = stream_session or Bridge._build_session(config)
        self.signer = Bridge._build_signer(config)
        self.clock = clock
        self.ami_factory = ami_factory or self._build_ami
        self.lines_reader = lines_reader
        self.lock = threading.RLock()
        self.tracker = pbx_calls.CallTracker()
        self.ami = None
        self.ami_error = ''
        self.pending = {}              # linkedid → снимок звонка, ещё не отправленный
        self.results = deque()         # итоги команд, ещё не принятые порталом
        self.recording_todo = deque()  # linkedid, у которых надо спросить имя записи
        self.recording_asked = set()   # у кого уже спрашивали (чистится вместе с expire)
        self.known_channels = set()    # имена каналов, которые мы видели (для Hangup)
        self.agent_id = '%s-%d' % (socket.gethostname()[:60], os.getpid())
        self.lines = {}
        self.lines_at = None
        self.lines_error = ''
        self.lines_sent = False
        self.last_post = 0.0
        self.last_snapshot = 0.0
        self.snapshot_due = True
        self.last_expire = 0.0
        self.portal_failures = 0
        self.portal_retry_at = 0.0
        self.ami_failures = 0
        self.ami_retry_at = 0.0
        self.stream_connected = False
        self.stream_failures = 0
        # Что портал последний раз узнал о коннекторе: (станция видна, ошибка, канал команд).
        self.reported = None
        self.events_total = 0
        # Вход в AMI: пока станция выдаёт список каналов, отбившиеся каналы запоминаются, чтобы
        # не воскресить их из списка. authoritative — состояние полное (AMI на связи, вход
        # завершён): только тогда полный снимок может закрывать на портале звонки.
        self.bootstrapping = False
        self.bootstrap_hangups = set()
        self.authoritative = False
        self.last_reconcile = 0.0
        self.stop_event = threading.Event()
        self.uplink_wake = threading.Event()
        self.main_wake = threading.Event()

    # ── служебное ────────────────────────────────────────────────────────────

    def _build_ami(self):
        return ami_mod.Ami(self.config['ami_host'], self.config['ami_port'], self.config['ami_user'],
                           self.config['ami_secret'], self.on_event, events=pbx_calls.EVENT_MASK,
                           on_disconnect=self.on_ami_disconnect)

    def _heartbeat(self):
        path = self.config.get('heartbeat_file')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(str(int(self.clock())))
        except OSError as exc:
            log.warning('пульс не записался: %s', exc)

    def _signed_headers(self, path, body):
        if self.signer is None:
            raise RuntimeError('у коннектора нет ключа подписи (CDR_AGENT_KEY_FILE)')
        headers = {'Content-Type': 'application/json'}
        headers.update(self.signer.headers('POST', path, body))
        return headers

    def _hello(self):
        return {'agent_id': self.agent_id, 'hostname': socket.gethostname(),
                'version': '%s/%s' % (VERSION, platform.python_version())}

    # ── AMI ──────────────────────────────────────────────────────────────────

    def on_event(self, event):
        name = event.get('Event')
        if name not in pbx_calls.WANTED_EVENTS:
            return
        with self.lock:
            self.events_total += 1
            if name == 'Newchannel' and event.get('Channel'):
                self.known_channels.add(str(event['Channel']))
            elif name == 'Hangup':
                if event.get('Channel'):
                    self.known_channels.discard(str(event['Channel']))
                if self.bootstrapping and event.get('Uniqueid'):
                    # Отбился, пока станция выдаёт список каналов: в списке он может ещё быть.
                    self.bootstrap_hangups.add(str(event['Uniqueid']))
            elif name == 'OriginateResponse':
                self._originate_outcome(event)
            changed = self.tracker.feed(event)
            for call in changed:
                self.pending[call['linkedid']] = call
                # Имя записи спрашиваем один раз на звонок — как только известен канал
                # оператора в разговоре (у очереди он появляется мостом после AgentConnect).
                if (self.config.get('recording_lookup') and call['state'] == 'talking'
                        and call['agent_channel'] and not call['recording_file']
                        and call['linkedid'] not in self.recording_asked):
                    self.recording_asked.add(call['linkedid'])
                    self.recording_todo.append(call['linkedid'])
                    self.main_wake.set()
        if changed or name == 'OriginateResponse':
            self.uplink_wake.set()

    def _originate_outcome(self, event):
        """Итог нашего Originate: телефон ответил (Success) или нет (Failure, Reason). Чужие
        Originate (itgrix, FOP2) узнаются по ActionID и не наши."""
        action_id = str(event.get('ActionID') or '')
        if not action_id.startswith('otp-cmd-'):
            return
        command_id = action_id[len('otp-cmd-'):]
        if str(event.get('Response') or '') == 'Success':
            self.results.append({'command_id': command_id, 'status': 'done',
                                 'message': 'телефон ответил, станция набирает',
                                 'linkedid': str(event.get('Uniqueid') or '')})
        else:
            reason = str(event.get('Reason') or '')
            self.results.append({'command_id': command_id, 'status': 'failed',
                                 'message': ORIGINATE_REASONS.get(reason, 'станция отказала (причина %s)' % reason)})

    def on_ami_disconnect(self, reason):
        self.ami_error = reason
        # Пока AMI лежит, события теряются: состояние больше не полное, и полный снимок,
        # закрывающий на портале «пропавшие» звонки, слать нельзя до нового входа.
        self.authoritative = False
        self.main_wake.set()
        self.uplink_wake.set()

    def _ami_backoff(self, moment, reason):
        self.ami_failures += 1
        self.ami_error = str(reason)[:200]
        pause = RECONNECT_SECONDS[min(self.ami_failures, len(RECONNECT_SECONDS)) - 1]
        self.ami_retry_at = moment + pause
        log.warning('AMI %s:%s: %s, повтор через %d с', self.config['ami_host'],
                    self.config['ami_port'], self.ami_error, pause)
        self.uplink_wake.set()

    def ensure_ami(self, now=None):
        if self.ami is not None and self.ami.connected:
            return True
        moment = self.clock() if now is None else now
        if moment < self.ami_retry_at:
            return False
        if self.ami is not None:
            self.ami.close()
        with self.lock:
            # Новый трекер — ДО входа: поток чтения AMI стартует внутри connect(), и события
            # нового соединения должны ложиться в новое состояние, а не в выброшенное старое.
            self.tracker = pbx_calls.CallTracker()
            self.known_channels = set()
            self.pending.clear()
            self.bootstrap_hangups = set()
            self.bootstrapping = True
            self.authoritative = False
        try:
            ami = self.ami_factory()
            ami.connect()
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.bootstrapping = False
            self._ami_backoff(moment, 'недоступен (%s)' % exc)
            return False
        self.ami = ami
        if not self.bootstrap():
            ami.close()
            self._ami_backoff(moment, self.ami_error or 'список каналов станции не дошёл целиком')
            return False
        self.ami_failures = 0
        self.ami_error = ''
        log.info('AMI %s:%s: вход принят (%s)', self.config['ami_host'], self.config['ami_port'],
                 self.config['ami_user'])
        return True

    def _channel_list(self):
        """Полный список каналов станции или None. Обрыв посреди списка, отказ, таймаут — None:
        по неполному списку нельзя ни строить состояние, ни закрывать звонки."""
        try:
            reply = self.ami.action({'Action': 'CoreShowChannels'})
        except ami_mod.AmiError as exc:
            self.ami_error = 'CoreShowChannels: %s' % exc
            return None
        if reply.get('Response') != 'Success' or not reply.get('_complete'):
            self.ami_error = 'CoreShowChannels: %s' % (reply.get('Message') or 'список не дошёл целиком')
            return None
        return reply.get('_items') or []

    def bootstrap(self):
        """Состояние с чистого листа по списку каналов станции (CoreShowChannels): звонки, идущие
        в момент входа, — с настоящим началом по длительности канала (`CallTracker.seed`). Пока
        AMI лежал, события терялись — прежнее состояние выброшено ещё до входа (ensure_ami), а
        полный снимок говорит порталу закрыть то, чего больше нет. Список не дошёл целиком —
        False: ничего не строим и снимок не шлём (он закрыл бы на портале живые звонки)."""
        items = self._channel_list()
        if items is None:
            with self.lock:
                self.bootstrapping = False
            log.warning('вход в AMI: %s — переподключаюсь', self.ami_error)
            return False
        with self.lock:
            for item in items:
                if item.get('Channel') and str(item.get('Uniqueid')) not in self.bootstrap_hangups:
                    self.known_channels.add(str(item['Channel']))
            self.tracker.seed(items, now=datetime.now(), skip_uniqueids=self.bootstrap_hangups)
            self.bootstrapping = False
            self.bootstrap_hangups = set()
            self.authoritative = True
            self.snapshot_due = True
            self.last_reconcile = self.clock()
            calls = len(self.tracker.calls)
        log.info('при входе на станции %d каналов, звонков в состоянии %d', len(items), calls)
        self.uplink_wake.set()
        return True

    def reconcile(self):
        """Сверка со станцией раз в RECONCILE_SECONDS: канал, известный до запроса списка и в нём
        отсутствующий, — закончился, а его отбой потерялся. Без этого такой звонок висел бы
        «идущим» до перезапуска коннектора, а каждый снимок подтверждал бы его порталу."""
        with self.lock:
            known_before = self.tracker.channel_ids()
            names = {uid: ch['name'] for uid, ch in self.tracker._channels.items()}
        items = self._channel_list()
        if items is None:
            log.warning('сверка со станцией не вышла: %s', self.ami_error)
            self.ami_error = ''
            return 0
        live = {str(item.get('Uniqueid')) for item in items if item.get('Uniqueid')}
        with self.lock:
            changed = self.tracker.reconcile(live, known_before)
            for uid in known_before - live:
                self.known_channels.discard(names.get(uid, ''))
            for call in changed:
                self.pending[call['linkedid']] = call
        if changed:
            log.info('сверка со станцией: закрыто %d звонков без отбоя', len(changed))
            self.uplink_wake.set()
        return len(changed)

    def lookup_recordings(self):
        """Имя файла записи у только что отвеченных звонков — Getvar CDR(recordingfile) на канале
        оператора (это и есть то имя, что потом попадёт в CDR и в касание)."""
        while self.ami is not None and self.ami.connected:
            with self.lock:
                if not self.recording_todo:
                    return
                linkedid = self.recording_todo.popleft()
                call = self.tracker.calls.get(linkedid)
                channels = [c for c in ((call or {}).get('agent_channel'), (call or {}).get('channel')) if c]
            if call is None or call['state'] == 'ended':
                continue
            for channel in channels:
                try:
                    reply = self.ami.action({'Action': 'Getvar', 'Channel': channel,
                                             'Variable': 'CDR(recordingfile)'}, timeout=5)
                except ami_mod.AmiError as exc:
                    log.debug('Getvar %s: %s', channel, exc)
                    continue
                value = str(reply.get('Value') or '').strip()
                if value and value != '(null)':
                    with self.lock:
                        for changed in self.tracker.set_recording(linkedid, value):
                            self.pending[changed['linkedid']] = changed
                    self.uplink_wake.set()
                    break

    # ── отправка ─────────────────────────────────────────────────────────────

    def _post(self, path, payload):
        url_path = '/api/pbx/agent/%s' % path
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        response = self.session.post(self.portal + url_path, data=body, timeout=PORTAL_TIMEOUT,
                                     headers=self._signed_headers(url_path, body))
        if response.status_code == 401:
            raise RuntimeError('портал не принял подпись: ключ %s не внесён в CDR_AGENT_KEYS или '
                               'разошлись часы' % self.signer.key_id)
        if response.status_code != 200:
            raise RuntimeError('портал ответил %d: %s' % (response.status_code, response.text[:200]))
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError('портал вернул не JSON: %s' % response.text[:200]) from exc

    def sync(self, snapshot=False):
        """Один обмен с порталом: изменения (или полный снимок), итоги команд, состояние станции.
        Команды в ответе приходят, только если канала команд нет (запасной путь)."""
        with self.lock:
            # Полный снимок закрывает на портале звонки, которых в нём нет, — поэтому только при
            # полном состоянии (AMI на связи, вход завершён). Иначе это обычная пачка изменений.
            snapshot = bool(snapshot and self.authoritative)
            if snapshot:
                events = self._snapshot_calls()
                if len(events) > MAX_SNAPSHOT_CALLS:
                    # Не влезает — без права закрывать: портал закрыл бы то, что не доехало.
                    events, snapshot = events[:MAX_SNAPSHOT_CALLS], False
                self.pending.clear()
            else:
                events = list(self.pending.values())[:MAX_EVENTS_PER_SYNC]
                for call in events:
                    self.pending.pop(call['linkedid'], None)
            results = list(self.results)
            report = self._ami_report()
            ami_ok, _error, stream = report
        payload = dict(self._hello())
        payload.update({
            'ami': {'connected': ami_ok, 'host': '%s:%s' % (self.config['ami_host'], self.config['ami_port']),
                    'error': self.ami_error or None, 'events_total': self.events_total},
            'snapshot': bool(snapshot),
            'stream': stream,
            'events': events,
            'results': results,
        })
        if self.lines and not self.lines_sent:
            payload['lines'] = self.lines
        try:
            reply = self._post('sync', payload)
        except Exception as exc:  # noqa: BLE001
            # Не дошло — вернём изменения в очередь (новее уже лежащих не затираем).
            with self.lock:
                for call in events:
                    current = self.pending.get(call['linkedid'])
                    if current is None or current['seq'] < call['seq']:
                        self.pending[call['linkedid']] = call
            self.portal_failures += 1
            raise RuntimeError(str(exc))
        with self.lock:
            for _ in range(len(results)):
                self.results.popleft()
        self.portal_failures = 0
        self.last_post = self.clock()
        self.reported = report
        if snapshot:
            self.last_snapshot = self.last_post
            self.snapshot_due = False
        if payload.get('lines'):
            self.lines_sent = True
        self._heartbeat()
        for command in (reply or {}).get('commands') or []:
            self.execute(command)
        return reply

    def uplink_interval(self):
        return HEARTBEAT_SECONDS if self.stream_connected else FALLBACK_POLL_SECONDS

    def _snapshot_calls(self):
        """Что входит в полный снимок: все идущие звонки и законченные за последнюю минуту.
        Законченные раньше портал уже получил своими пачками, а снимку незачем расти с пиком
        звонков (за 5 минут их бывают сотни — портал отвергал бы такую пачку целиком)."""
        edge = (datetime.now() - timedelta(seconds=SNAPSHOT_ENDED_SECONDS)).strftime('%Y-%m-%dT%H:%M:%S')
        calls = [call for call in self.tracker.live()
                 if call['state'] != 'ended' or str(call.get('ended_at') or '') >= edge]
        calls.sort(key=lambda call: call['state'] == 'ended')
        return calls

    def _ami_report(self):
        return (bool(self.ami is not None and self.ami.connected), self.ami_error or '',
                bool(self.stream_connected))

    def uplink_due(self, now):
        """Что отправлять сейчас: (нужна ли пачка, полный ли снимок). Смена того, что портал
        показывает о коннекторе (станция видна или нет, канал команд), — тоже повод сразу."""
        snapshot = self.authoritative and (
            self.snapshot_due or now - self.last_snapshot >= self.config['snapshot_seconds'])
        with self.lock:
            have = bool(self.pending or self.results)
        lines = bool(self.lines and not self.lines_sent)
        changed = self._ami_report() != self.reported
        return (snapshot or have or lines or changed
                or now - self.last_post >= self.uplink_interval()), snapshot

    def uplink_once(self, woke=False, sleep=time.sleep):
        """Один такт отправки. Возвращает, сколько ждать до следующего (если ничего не разбудит)."""
        backoff = self.portal_retry_at - self.clock()
        if backoff > 0:
            # Портал недавно не ответил: событие станции будит отправку, но пауза после отказа
            # обязательна — иначе при живой линии упавший портал получал бы запрос на каждое событие.
            return backoff
        if woke:
            # Всплеск событий одного звонка — одной пачкой.
            sleep(COALESCE_SECONDS)
        now = self.clock()
        due, snapshot = self.uplink_due(now)
        if due:
            try:
                self.sync(snapshot=snapshot)
            except Exception as exc:  # noqa: BLE001
                pause = PORTAL_BACKOFF_SECONDS[min(self.portal_failures, len(PORTAL_BACKOFF_SECONDS)) - 1]
                self.portal_retry_at = self.clock() + pause
                if self.portal_failures in (1, 5) or self.portal_failures % 20 == 0:
                    log.warning('портал недоступен (%s), повтор через %d с', exc, pause)
                return float(pause)
        with self.lock:
            if self.pending or self.results:
                return COALESCE_SECONDS
        until_beat = self.last_post + self.uplink_interval() - self.clock()
        if not self.authoritative:
            return max(0.05, until_beat)
        until_snapshot = self.last_snapshot + self.config['snapshot_seconds'] - self.clock()
        return max(0.05, min(until_beat, until_snapshot))

    def uplink_loop(self):
        wait = 0.0
        while not self.stop_event.is_set():
            woke = self.uplink_wake.wait(wait)
            self.uplink_wake.clear()
            if self.stop_event.is_set():
                return
            try:
                wait = self.uplink_once(woke=woke)
            except Exception:  # noqa: BLE001
                log.exception('отправка на портал упала')
                wait = 5.0

    # ── канал команд ─────────────────────────────────────────────────────────

    def consume_stream(self, chunks):
        """Байты канала команд → исполнение. Возвращает причину конца канала ('bye', 'eof')."""
        parser = SseParser()
        for chunk in chunks:
            if self.stop_event.is_set():
                return 'stop'
            for event, data in parser.feed(chunk):
                if event == '':
                    if data.startswith('connected') and not self.stream_connected:
                        self.stream_connected = True
                        self.stream_failures = 0
                        log.info('канал команд открыт')
                        # Портал должен узнать сразу: команды больше не ждут ответа на пачку.
                        self.uplink_wake.set()
                    continue
                if event == 'command':
                    try:
                        command = json.loads(data)
                    except ValueError:
                        log.warning('канал команд: команда не разобралась')
                        continue
                    if isinstance(command, dict):
                        self.execute(command)
                elif event == 'bye':
                    return 'bye'
        return 'eof'

    def open_stream(self):
        """Один заход канала команд: открыть, читать, пока не закроется. Возвращает паузу до
        следующего захода."""
        url_path = '/api/pbx/agent/stream'
        body = json.dumps(self._hello(), ensure_ascii=False).encode('utf-8')
        response = None
        try:
            response = self.stream_session.post(self.portal + url_path, data=body, stream=True,
                                                timeout=STREAM_TIMEOUT,
                                                headers=self._signed_headers(url_path, body))
            if response.status_code in (404, 405) or (
                    response.status_code == 503 and 'PBX_STREAM_OFF' in (response.text or '')[:300]):
                log.warning('канал команд на портале недоступен (%d) — работаю частой отправкой',
                            response.status_code)
                return float(STREAM_OFF_RETRY_SECONDS)
            if response.status_code != 200:
                raise RuntimeError('портал ответил %d: %s' % (response.status_code, (response.text or '')[:200]))
            reason = self.consume_stream(response.iter_content(chunk_size=None))
            if reason == 'bye':
                return 0.0
            raise RuntimeError('канал команд закрылся без прощания')
        except Exception as exc:  # noqa: BLE001
            self.stream_failures += 1
            pause = STREAM_BACKOFF_SECONDS[min(self.stream_failures, len(STREAM_BACKOFF_SECONDS)) - 1]
            if self.stream_failures in (1, 5) or self.stream_failures % 20 == 0:
                log.warning('канал команд оборвался (%s), повтор через %d с', exc, pause)
            return float(pause)
        finally:
            if self.stream_connected:
                self.stream_connected = False
                self.uplink_wake.set()
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001
                    pass

    def stream_loop(self):
        while not self.stop_event.is_set():
            pause = self.open_stream()
            if pause and self.stop_event.wait(pause):
                return

    # ── команды ──────────────────────────────────────────────────────────────

    def execute(self, command):
        command_id = command.get('id')
        kind = str(command.get('kind') or '')
        try:
            if self.ami is None or not self.ami.connected:
                raise ami_mod.AmiError('нет соединения со станцией')
            if kind == 'originate':
                message = self._originate(command)
            elif kind == 'spy':
                message = self._spy(command)
            elif kind == 'hangup':
                message = self._hangup(command)
            else:
                raise ValueError('неизвестная команда %r' % kind)
            result = {'command_id': command_id, 'status': 'accepted', 'message': message}
        except (ami_mod.AmiError, ValueError) as exc:
            result = {'command_id': command_id, 'status': 'failed', 'message': str(exc)[:300]}
            log.warning('команда %s (%s) не исполнена: %s', command_id, kind, exc)
        except Exception as exc:  # noqa: BLE001
            result = {'command_id': command_id, 'status': 'failed', 'message': 'сбой коннектора: %s' % exc}
            log.exception('команда %s (%s) уронила исполнение', command_id, kind)
        with self.lock:
            self.results.append(result)
        self.uplink_wake.set()

    def _action_id(self, command):
        return 'otp-cmd-%s' % command.get('id')

    def _originate(self, command):
        ext = str(command.get('ext') or '').strip()
        dial = str(command.get('dial') or '').strip()
        if not _EXT_RE.match(ext):
            raise ValueError('внутренний номер оператора не похож на номер станции')
        if not (_DIAL_RE.match(dial) or _EXT_RE.match(dial)):
            raise ValueError('набор %r не похож ни на <префикс>*7<номер>, ни на внутренний номер' % dial)
        action_id = self._action_id(command)
        fields, variables = ami_mod.originate_action(
            ext, dial, action_id, caller_name=str(command.get('caller_name') or dial)[:40],
            variables={'OTP_COMMAND': str(command.get('id') or '')})
        with self.lock:
            self.tracker.expect_originate(action_id, command.get('id'))
        # Таймаут ответа — ожидание не снимаем: станция могла принять Async-Originate, и итог
        # (OriginateResponse) придёт позже. Явный отказ — снимаем: итога не будет.
        reply = self.ami.action(fields, variables)
        if reply.get('Response') != 'Success':
            with self.lock:
                self.tracker.forget_originate(action_id)
            raise ami_mod.AmiError(reply.get('Message') or 'станция отказала')
        return 'телефон %s звонит, после ответа набор %s' % (ext, dial)

    def _spy(self, command):
        ext = str(command.get('ext') or '').strip()
        target = str(command.get('target_channel') or '').strip()
        mode = str(command.get('mode') or 'listen')
        if not _EXT_RE.match(ext):
            raise ValueError('внутренний номер супервайзера не похож на номер станции')
        target_ext = pbx_calls.ext_of_channel(target)
        if not target_ext:
            raise ValueError('канал оператора не назван')
        with self.lock:
            known = target in self.known_channels
        if not known:
            raise ValueError('канала %s на станции уже нет' % target)
        fields = ami_mod.spy_action(ext, 'PJSIP/%s-' % target_ext, mode, self._action_id(command))
        reply = self.ami.action(fields)
        if reply.get('Response') != 'Success':
            raise ami_mod.AmiError(reply.get('Message') or 'станция отказала')
        return 'телефон %s звонит: %s' % (ext, {'listen': 'прослушивание', 'whisper': 'подсказка',
                                                 'barge': 'подключение'}.get(mode, mode))

    def _hangup(self, command):
        channel = str(command.get('channel') or '').strip()
        if not _CHANNEL_RE.match(channel):
            raise ValueError('канал не назван')
        with self.lock:
            known = channel in self.known_channels
        if not known:
            raise ValueError('канала %s на станции уже нет' % channel)
        reply = self.ami.action(ami_mod.hangup_action(channel, self._action_id(command)))
        if reply.get('Response') != 'Success':
            raise ami_mod.AmiError(reply.get('Message') or 'станция отказала')
        return 'отбой отправлен'

    # ── линии и обслуживание ─────────────────────────────────────────────────

    def refresh_lines(self, now):
        if self.lines_at is not None and now - self.lines_at < LINES_REFRESH_SECONDS:
            return
        self.lines_at = now
        lines, error = self.lines_reader(self.config)
        if error:
            if error != self.lines_error:
                log.warning('линии станции не прочитались: %s', error)
            self.lines_error = error
            return
        self.lines_error = ''
        if lines != self.lines:
            self.lines = lines
            self.lines_sent = False
            log.info('линий станции: %d', len(lines))
            self.uplink_wake.set()

    def maintain_once(self, now=None):
        """Такт обслуживания: AMI жив (вход, Ping), имена записей, линии, уборка памяти."""
        moment = self.clock() if now is None else now
        if self.ensure_ami(moment):
            self.lookup_recordings()
            if self.authoritative and moment - self.last_reconcile >= RECONCILE_SECONDS:
                self.last_reconcile = moment
                self.reconcile()
            if self.ami.idle_seconds(moment) > ami_mod.KEEPALIVE_SECONDS:
                try:
                    self.ami.ping()
                except ami_mod.AmiError as exc:
                    log.warning('AMI не ответил на Ping: %s', exc)
                    self.ami.close()
        self.refresh_lines(moment)
        if moment - self.last_expire >= EXPIRE_EVERY_SECONDS:
            with self.lock:
                for linkedid in self.tracker.expire():
                    self.recording_asked.discard(linkedid)
            self.last_expire = moment

    def run(self):
        log.info('коннектор %s → %s, станция %s:%s', VERSION, self.portal, self.config['ami_host'],
                 self.config['ami_port'])
        threads = [threading.Thread(target=self.uplink_loop, name='pbx-uplink', daemon=True)]
        if self.config.get('stream', True):
            threads.append(threading.Thread(target=self.stream_loop, name='pbx-commands', daemon=True))
        for thread in threads:
            thread.start()
        while not self.stop_event.is_set():
            try:
                self.maintain_once()
            except Exception:  # noqa: BLE001
                log.exception('обслуживание коннектора упало')
            self.main_wake.wait(MAINTENANCE_TICK_SECONDS)
            self.main_wake.clear()

    def stop(self):
        self.stop_event.set()
        self.uplink_wake.set()
        self.main_wake.set()


def _duration_seconds(value):
    """`00:01:23` → 83; мусор → 0."""
    parts = str(value or '').split(':')
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return max(0, seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Коннектор АТС FreePBX → портал')
    parser.add_argument('--once', action='store_true', help='один проход и выход')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    config = load_config()
    missing = [key for key in ('portal', 'ami_user', 'ami_secret') if not config.get(key)]
    if missing:
        log.error('не заданы настройки: %s', ', '.join(missing))
        return 2
    connector = Connector(config)
    if connector.signer is None:
        log.error('нет ключа подписи: задайте CDR_AGENT_KEY_FILE или CDR_AGENT_PRIVATE_KEY')
        return 2
    if args.once:
        connector.ensure_ami()
        connector.refresh_lines(connector.clock())
        reply = connector.sync(snapshot=True)
        log.info('портал ответил: %s', json.dumps(reply, ensure_ascii=False)[:300])
        if connector.ami is not None:
            connector.ami.close()
        return 0
    connector.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
