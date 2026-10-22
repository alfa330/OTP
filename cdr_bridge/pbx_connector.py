# -*- coding: utf-8 -*-
"""Коннектор АТС: живые события звонков станции FreePBX → портал, команды портала → станция.

Зачем, если есть мост «Касаний»
--------------------------------
Мост читает CDR и журнал очередей из базы станции раз в 20 секунд — он видит звонок, когда тот
уже записан, то есть после отбоя. Коннектор сидит на AMI станции (как коннекторы amoCRM/itgrix
и FOP2 той же учёткой) и видит звонок в момент, когда он происходит: клиент в очереди, телефон
оператора звонит, разговор идёт, трубку положил такой-то. И умеет обратное — то, чего у моста
нет по замыслу: позвонить клиенту с телефона оператора (click-to-call), положить трубку,
подключить супервайзера к разговору (слушать, подсказывать, войти).

Как устроено
------------
    станция 192.168.88.251:5038 ──AMI──► коннектор (VM шлюза) ──HTTPS, подпись Ed25519──► портал
                                 ◄──Originate/Hangup──          ◄── команды в ответе ──

* Соединение с AMI постоянное, с переподключением; события только классов call/agent/dialing/
  originate (`cdr/pbx_calls.EVENT_MASK`). Разбор событий в состояние звонков — общий модуль
  `cdr/pbx_calls.py`, тот же импортирует портал: форма записи звонка одна.
* Изменившиеся звонки уходят на портал пачкой не чаще раза в FLUSH_SECONDS; при тишине —
  пульс раз в POLL_SECONDS (по умолчанию 2 с): в ответе на любой запрос портал отдаёт команды,
  так что click-to-call ждёт не дольше двух секунд, а входящих соединений на шлюз нет вовсе.
* Раз в SNAPSHOT_SECONDS уходит полный снимок всех известных звонков — портал закрывает те,
  о чьём отбое событие потерялось.
* При старте — `CoreShowChannels`: звонки, начавшиеся до нас, тоже попадают в состояние.
* Линии станции (префикс набора → номер линии) читаются из её базы той же учёткой на чтение,
  что у моста (`CDR_PBXDB_*`), раз в LINES_REFRESH_SECONDS и уезжают порталу: по ним он
  предлагает, с какой линии перезвонить. Базы нет — коннектор работает без них.

Команды портала (в поле `commands` ответа на /api/pbx/agent/sync)
-----------------------------------------------------------------
    {id, kind: 'originate', ext, dial, caller_name}   позвонить: телефон ext → набор dial
    {id, kind: 'spy', ext, target_channel, mode}      ext слушает/подсказывает/входит в разговор
    {id, kind: 'hangup', channel}                     положить трубку каналу
Итог каждой уходит порталу в следующем запросе (`results`): accepted — станция приняла
(Originate поставлен, телефон звонит), failed — отказ с текстом. Кому что можно, решает
портал по своим правам; коннектор исполняет только то, что подписано им, и только эти три вида.

Чему коннектор НЕ верит
-----------------------
Порталу — не безусловно: номер набора проверяется формой `<4 цифры>*7<10 цифр>` или внутренний
номер, канал для Hangup — только из тех, что коннектор сам видел в событиях. HTTPS_PROXY и
подобные переменные не читаются — наружу только через прокси из настроек (как у моста).

Запуск
------
    python -m cdr_bridge.pbx_connector          рабочий цикл (служба pbx-connector)
    python -m cdr_bridge.pbx_connector --once   один цикл: вход в AMI, снимок, пульс, выход

Настройки — окружение (или .env.codex.local при отладке):
    CDR_BRIDGE_PORTAL, CDR_AGENT_KEY_FILE | CDR_AGENT_PRIVATE_KEY, CDR_PORTAL_PROXY,
    CDR_PORTAL_CA_BUNDLE        — как у моста;
    PBX_AMI_HOST / _PORT        станция, 5038
    PBX_AMI_USER / _SECRET      учётка AMI (на отладке — AMI_USER/AMI_SECRET из доступов)
    PBX_POLL_SECONDS            пульс при тишине (2)
    PBX_SNAPSHOT_SECONDS        полный снимок (60)
    PBX_RECORDING_LOOKUP        1 — спрашивать у станции имя файла записи при ответе (Getvar)
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

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import pbx_calls  # noqa: E402
from cdr_bridge import ami as ami_mod  # noqa: E402
from cdr_bridge.agent import Bridge, _env_file_values  # noqa: E402

VERSION = 'pbx/1.0.0'
log = logging.getLogger('cdr_bridge.pbx_connector')

PORTAL_TIMEOUT = (10, 30)
FLUSH_SECONDS = 0.3            # пачка изменений не чаще
POLL_SECONDS = 2.0             # пульс при тишине — он же срок ожидания команды
SNAPSHOT_SECONDS = 60.0
LINES_REFRESH_SECONDS = 3600.0
EXPIRE_EVERY_SECONDS = 30.0
RECONNECT_SECONDS = (5, 15, 30, 60)
PORTAL_BACKOFF_SECONDS = (5, 15, 30, 60, 120)
MAX_EVENTS_PER_SYNC = 500

_DIAL_RE = re.compile(r'^\d{4}\*7\d{10}$')
_EXT_RE = re.compile(r'^\d{3,5}$')
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
        'poll_seconds': max(0.5, number('PBX_POLL_SECONDS', POLL_SECONDS)),
        'snapshot_seconds': max(10.0, number('PBX_SNAPSHOT_SECONDS', SNAPSHOT_SECONDS)),
        'recording_lookup': value('PBX_RECORDING_LOOKUP', '1') not in ('0', 'no', 'false', ''),
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


# ── коннектор ────────────────────────────────────────────────────────────────

class Connector:
    def __init__(self, config, session=None, ami_factory=None, clock=time.time, sleep=None,
                 lines_reader=read_outbound_lines):
        self.config = config
        self.portal = config['portal']
        self.session = session or Bridge._build_session(config)
        self.signer = Bridge._build_signer(config)
        self.clock = clock
        self.wake = threading.Event()
        self.sleep = sleep or self._sleep
        self.ami_factory = ami_factory or self._build_ami
        self.lines_reader = lines_reader
        self.tracker = pbx_calls.CallTracker()
        self.ami = None
        self.ami_error = ''
        self.lock = threading.Lock()
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
        self.last_expire = 0.0
        self.portal_failures = 0
        self.ami_failures = 0
        self.ami_connected_at = 0.0
        self.stop = False
        self.events_total = 0

    # ── служебное ────────────────────────────────────────────────────────────

    def _sleep(self, seconds):
        self.wake.wait(max(0.0, seconds))
        self.wake.clear()

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

    # ── AMI ──────────────────────────────────────────────────────────────────

    def on_event(self, event):
        name = event.get('Event')
        if name not in pbx_calls.WANTED_EVENTS:
            return
        with self.lock:
            self.events_total += 1
            if name == 'Newchannel' and event.get('Channel'):
                self.known_channels.add(str(event['Channel']))
            elif name == 'Hangup' and event.get('Channel'):
                self.known_channels.discard(str(event['Channel']))
            elif name == 'OriginateResponse':
                self._originate_outcome(event)
            for call in self.tracker.feed(event):
                self.pending[call['linkedid']] = call
                # Имя записи спрашиваем один раз на звонок — как только известен канал
                # оператора в разговоре (у очереди он появляется мостом после AgentConnect).
                if (self.config.get('recording_lookup') and call['state'] == 'talking'
                        and call['agent_channel'] and not call['recording_file']
                        and call['linkedid'] not in self.recording_asked):
                    self.recording_asked.add(call['linkedid'])
                    self.recording_todo.append(call['linkedid'])
        self.wake.set()

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
        self.wake.set()

    def ensure_ami(self):
        if self.ami is not None and self.ami.connected:
            return True
        if self.ami is not None:
            self.ami.close()
        try:
            ami = self.ami_factory()
            ami.connect()
        except Exception as exc:  # noqa: BLE001
            self.ami_failures += 1
            self.ami_error = str(exc)[:200]
            pause = RECONNECT_SECONDS[min(self.ami_failures, len(RECONNECT_SECONDS)) - 1]
            log.warning('AMI %s:%s недоступен (%s), повтор через %d с', self.config['ami_host'],
                        self.config['ami_port'], self.ami_error, pause)
            return False
        self.ami = ami
        self.ami_failures = 0
        self.ami_error = ''
        self.ami_connected_at = self.clock()
        log.info('AMI %s:%s: вход принят (%s)', self.config['ami_host'], self.config['ami_port'],
                 self.config['ami_user'])
        self.bootstrap()
        return True

    def bootstrap(self):
        """Звонки, идущие в момент входа: CoreShowChannels → состояние, как если бы мы видели их
        с начала. Длительность канала даёт настоящее начало."""
        try:
            reply = self.ami.action({'Action': 'CoreShowChannels'})
        except ami_mod.AmiError as exc:
            log.warning('CoreShowChannels не ответил: %s', exc)
            return
        items = reply.get('_items') or []
        if not items:
            return
        now = datetime.now()
        with self.lock:
            # Первые каналы звонков раньше остальных: направление решает тот, у кого
            # Uniqueid == Linkedid.
            items.sort(key=lambda item: (item.get('Uniqueid') != item.get('Linkedid'),
                                         str(item.get('Uniqueid'))))
            for item in items:
                channel = str(item.get('Channel') or '')
                if channel:
                    self.known_channels.add(channel)
                started = now - timedelta(seconds=_duration_seconds(item.get('Duration')))
                fake = dict(item)
                fake['Event'] = 'Newchannel'
                self.tracker.feed(fake, at=started)
            for item in items:
                if item.get('BridgeId') and pbx_calls.channel_kind(item.get('Channel')) in ('ext', 'trunk'):
                    bridge = dict(item)
                    bridge['Event'] = 'BridgeEnter'
                    self.tracker.feed(bridge)
            for call in self.tracker.live():
                self.pending[call['linkedid']] = call
        log.info('при входе на станции %d каналов, звонков в состоянии %d', len(items),
                 len(self.tracker.calls))

    def lookup_recordings(self):
        """Имя файла записи у только что отвеченных звонков — Getvar CDR(recordingfile) на канале
        оператора (это и есть то имя, что потом попадёт в CDR и в касание)."""
        while self.recording_todo and self.ami is not None and self.ami.connected:
            with self.lock:
                if not self.recording_todo:
                    break
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
                    break

    # ── портал ───────────────────────────────────────────────────────────────

    def _post(self, path, payload):
        url_path = '/api/pbx/agent/%s' % path
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.signer is None:
            raise RuntimeError('у коннектора нет ключа подписи (CDR_AGENT_KEY_FILE)')
        headers.update(self.signer.headers('POST', url_path, body))
        response = self.session.post(self.portal + url_path, data=body, timeout=PORTAL_TIMEOUT,
                                     headers=headers)
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
        """Один обмен с порталом: изменения (или полный снимок), итоги команд, пульс → команды."""
        with self.lock:
            if snapshot:
                events = self.tracker.live()
                self.pending.clear()
            else:
                events = list(self.pending.values())[:MAX_EVENTS_PER_SYNC]
                for call in events:
                    self.pending.pop(call['linkedid'], None)
            results = list(self.results)
            ami_ok = bool(self.ami is not None and self.ami.connected)
        payload = {
            'agent_id': self.agent_id,
            'hostname': socket.gethostname(),
            'version': '%s/%s' % (VERSION, platform.python_version()),
            'ami': {'connected': ami_ok, 'host': '%s:%s' % (self.config['ami_host'], self.config['ami_port']),
                    'error': self.ami_error or None, 'events_total': self.events_total},
            'snapshot': bool(snapshot),
            'events': events,
            'results': results,
        }
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
        if payload.get('lines'):
            self.lines_sent = True
        for command in (reply or {}).get('commands') or []:
            self.execute(command)
        return reply

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
        reply = self.ami.action(fields, variables)
        if reply.get('Response') != 'Success':
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

    # ── линии ────────────────────────────────────────────────────────────────

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

    # ── цикл ─────────────────────────────────────────────────────────────────

    def step(self):
        """Один шаг: соединение, записи, обмен с порталом, уборка. Возвращает паузу до
        следующего шага."""
        now = self.clock()
        connected = self.ensure_ami()
        if connected:
            self.lookup_recordings()
            if self.ami.idle_seconds(now) > ami_mod.KEEPALIVE_SECONDS:
                try:
                    self.ami.ping()
                except ami_mod.AmiError as exc:
                    log.warning('AMI не ответил на Ping: %s', exc)
                    self.ami.close()
        self.refresh_lines(now)
        if now - self.last_expire >= EXPIRE_EVERY_SECONDS:
            with self.lock:
                for linkedid in self.tracker.expire():
                    self.recording_asked.discard(linkedid)
            self.last_expire = now
        snapshot = now - self.last_snapshot >= self.config['snapshot_seconds']
        with self.lock:
            have_changes = bool(self.pending or self.results)
        due = snapshot or have_changes or (now - self.last_post >= self.config['poll_seconds'])
        if due:
            try:
                self.sync(snapshot=snapshot)
                if snapshot:
                    self.last_snapshot = now
                self._heartbeat()
            except Exception as exc:  # noqa: BLE001
                pause = PORTAL_BACKOFF_SECONDS[min(self.portal_failures, len(PORTAL_BACKOFF_SECONDS)) - 1]
                if self.portal_failures in (1, 5) or self.portal_failures % 20 == 0:
                    log.warning('портал недоступен (%s), повтор через %d с', exc, pause)
                return pause
        if not connected:
            return RECONNECT_SECONDS[min(self.ami_failures, len(RECONNECT_SECONDS)) - 1]
        with self.lock:
            busy = bool(self.pending or self.results)
        return FLUSH_SECONDS if busy else self.config['poll_seconds']

    def run(self):
        log.info('коннектор %s → %s, станция %s:%s', VERSION, self.portal, self.config['ami_host'],
                 self.config['ami_port'])
        while not self.stop:
            try:
                pause = self.step()
            except Exception:  # noqa: BLE001
                log.exception('шаг коннектора упал')
                pause = 5
            self.sleep(pause)


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
    parser.add_argument('--once', action='store_true', help='один шаг и выход')
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
