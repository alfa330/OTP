# -*- coding: utf-8 -*-
"""Диспетчер автоинформатора на шлюзе: портал → робот (Asterisk) → портал.

Что делает
----------
Раз в несколько секунд спрашивает портал, кому звонить (POST /api/informer/agent/poll), ставит
звонок роботу через AMI Asterisk на 127.0.0.1 и ловит события звонка: вызов пошёл, водитель снял
трубку, запись доиграла, нажатия, отбой. Итог — с точными секундами — уходит в портал
(/api/informer/agent/result). Темп, окно времени, повторы и очередь решает портал; диспетчер
держит только то, что видит сам: ёмкость линий (2 звонка на линию Binotel) и предел одновременных
звонков — не больше INFORMER_MAX_ACTIVE (3) разом, набор по одному с паузой. Это решение владельца
16.10.2026 после первого боевого запуска: 46 в минуту с общего номера кабинета за 4 минуты уронили
исходящие всем операторам (оператор связи ~33 с держит вызов и отвечает «занято»), а каждый новый
залп продлевал блок — «жёстких залпов делать не надо вообще, по 3 максимум». Минутного потолка нет;
страховку можно вернуть числом INFORMER_MAX_PER_MINUTE — тогда больше него в минуту диспетчер не
наберёт, даже если портал раздал.

Робот — контейнер otp-informer (приватный репозиторий otp-gateway): Asterisk звонит с линии
удалённого КЦ без регистрации на ней (15.10.2026: Binotel пропускает исходящий по паролю линии и
так), поэтому телефон оператора на той же линии работает как обычно.

Связь с порталом — та же подпись Ed25519, что у моста «Касаний» (cdr_bridge/signing.py), и тот же
единственный выход наружу через прокси шлюза. Номер водителя приходит от портала только ради
набора и никуда не пишется: в журнал диспетчера попадает номер звонка, а не телефон.

Запуск
------
    python -m cdr_bridge.informer          рабочий цикл (служба informer-dispatcher)

Настройки — окружение (или .env.codex.local при отладке):
    CDR_BRIDGE_PORTAL, CDR_AGENT_KEY_FILE | CDR_AGENT_PRIVATE_KEY, CDR_PORTAL_PROXY, CDR_PORTAL_CA_BUNDLE
                                       — как у моста;
    INFORMER_AMI_HOST / _PORT          127.0.0.1 / 5038
    INFORMER_AMI_USER / _SECRET        dispatcher / секрет из /etc/otp-gateway/informer-ami.env
    INFORMER_LINES                     линии робота через запятую: 902,907,919,923,925,928,939,942,944
    INFORMER_CALLS_PER_LINE            2 (замер 15.10.2026: третий звонок Binotel отклоняет 480)
    INFORMER_MAX_ACTIVE                одновременных звонков не больше этого (3); 0 — без предела
    INFORMER_MAX_PER_MINUTE            наборов в минуту не больше этого; пусто или 0 — без предела
    INFORMER_DIAL_GAP_MS               пауза между наборами, мс (1000): залпом не звоним — 16.10.2026 залп из
                                       32 вызовов разом офисная сеть приняла за атаку и отрезала VM от Binotel
    INFORMER_SOUNDS_DIR                /var/lib/informer/sounds — общий с Asterisk каталог записей
    INFORMER_HEARTBEAT_FILE            файл пульса для проверки живости контейнера
"""
import base64
import hashlib
import json
import logging
import os
import socket
import sys
import threading
import time
from collections import deque

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge.agent import Bridge, _env_file_values  # noqa: E402

VERSION = 'informer/1.3.0'
log = logging.getLogger('cdr_bridge.informer')

PORTAL_TIMEOUT = (10, 60)
AMI_TIMEOUT = 10
# Сколько звонить водителю, прежде чем бросить. Дольше ~33 с, за которые Binotel сам отвечает «занято»/«недоступен»,
# когда оператор связи не пропустил вызов: при 35 с (16.10.2026) свой отбой приходил раньше его ответа, блок сети
# записывался «не ответил», пробный звонок после паузы считался удачным — и робот снова набирал пачку.
RING_TIMEOUT_MS = 40000
LINE_BUSY_WINDOW_S = 2.5         # 480 быстрее этого и без гудков — Binotel не дал линию
STUCK_MARGIN_S = 90              # звонок дольше «вызов + запись + запас» без отбоя — закрываем сами,
                                 # чтобы не висела ёмкость линии (срок свой у каждой записи)
NO_ANSWER_AFTER_S = 15           # отказ после стольких секунд вызова — недозвон, даже без 180 «звонит»
DIAL_GAP_MS = 1000               # пауза между наборами по умолчанию (INFORMER_DIAL_GAP_MS)
MAX_DIAL_GAP_MS = 30000          # шаг от портала больше этого не берём: круг не должен стоять минутами
# Таймер B SIP — 32 с: на INVITE ни одного ответа (ни 100, ни 180), Asterisk сам ставит 408 → причина 18;
# тот же срок у Binotel к оператору связи: его «занято» (603 → 21), «недоступен» (480 → 19) и «сброшен» (18)
# приходят через 32–36 с от набора (сверка 16.10.2026 с журналом Binotel: все 347 блок-звонков — BUSY/CHANUNAVAIL
# с waitsec 32–33). Отказ в это окно без гудков — сеть не пропустила, а не водитель.
INVITE_TIMEOUT_S = (31.0, 38.0)
CAUSE_NO_LINK = {17, 18, 19, 21}
# Второй вид отказа сети (16.10.2026, 19:05–19:09, сразу после возобновления рассылки): 12 вызовов подряд — 603
# через ~1 с от INVITE, без 183 и гудков; в журнале Binotel CHANUNAVAIL с waitsec 0–1; потом всё прошло. Человек не
# успевает сбросить вызов до гудков — «сброшен» быстрее этого срока без гудков значит сеть.
QUICK_REJECT_S = 3.0
CAUSE_QUICK_REJECT = {21}
# Одновременно — не больше стольких звонков, какой бы темп ни раздал портал (владелец, 16.10.2026: «жёстких
# залпов делать не надо вообще, по 3 максимум»; вечером того же дня, когда сеть снова пропускала: «давай на 10
# увеличим», затем «ещё до 15» и «до 20» — 20 разом при темпе 20 в минуту, набор по одному раз в 3 с). INFORMER_MAX_ACTIVE; 0 — без предела.
MAX_ACTIVE_CALLS = 20
LINK_TRIP = 3                    # столько вызовов подряд без связи — пауза набора
LINK_BACKOFF_S = (120, 300, 900) # пауза растёт, пока связь не вернётся; после паузы — один пробный звонок
RESULT_RETRY_S = 10
IDLE_POLL_S = 10
MAX_POLL_S = 60

# Q.850 причины отбоя (HANGUPCAUSE), как их отдаёт Asterisk на отказ SIP.
CAUSE_BUSY = {17}                            # 486/600
CAUSE_REJECTED = {21}                        # 403/603
CAUSE_UNAVAILABLE = {1, 3, 20, 22, 27, 28}   # 404, выключен, нет в сети, номер изменён
CAUSE_NO_ANSWER = {18, 19}                   # 408/480


def classify(call):
    """Итог звонка по тому, что видел робот: (результат, кто положил трубку)."""
    if call.get('answered_at'):
        if call.get('played_to_end'):
            return 'answered', 'robot'
        cause = call.get('cause')
        return 'answered', ('driver' if cause in (16, None, 0) else 'network')
    cause = call.get('cause')
    reason = call.get('originate_reason')
    elapsed = (call.get('ended_at') or 0) - (call.get('dialed_at') or 0)
    ringing = bool(call.get('ringing_at'))
    # Отказ ровно через ~32 с и без гудков — это не водитель, а сеть: либо до Binotel не дошло ни одного
    # ответа (таймер B, 408 → 18), либо сам Binotel 32 с не достучался до оператора связи и вернул «занято»
    # или «сброшен» (16.10.2026, 15:46: после ~4 минут темпа 46/мин у Binotel BUSY и CHANUNAVAIL на каждый
    # звонок, во всём кабинете ни одного дозвона). Живое «занято»/«сброс» приходит за секунды или после гудков.
    if cause in CAUSE_NO_LINK and not ringing and INVITE_TIMEOUT_S[0] <= elapsed <= INVITE_TIMEOUT_S[1]:
        return 'no_link', ''
    if cause in CAUSE_QUICK_REJECT and not ringing and 0 <= elapsed < QUICK_REJECT_S:
        return 'no_link', ''
    if cause in CAUSE_BUSY or reason == 5:
        return 'busy', ''
    if cause in CAUSE_REJECTED:
        return 'rejected', ''
    if cause in CAUSE_UNAVAILABLE:
        return 'unavailable', ''
    if cause in CAUSE_NO_ANSWER or reason == 3:
        if not ringing and 0 <= elapsed < LINE_BUSY_WINDOW_S:
            return 'line_busy', ''
        # Гудки операторы РК часто дают ранним звуком (183) без 180 «звонит» (15.10.2026, номер 778…):
        # долгий вызов — это недозвон, короткий без гудков — сообщение сети «абонент недоступен».
        if ringing or reason == 3 or elapsed >= NO_ANSWER_AFTER_S:
            return 'no_answer', ''
        return 'unavailable', ''
    # Без причины (0) и без ответа на набор, но вызов шёл весь срок — это недозвон, а не сбой: 16.10.2026
    # такой 35-секундный вызов (Binotel: CANCEL) записался «Ошибкой АТС».
    if not cause and elapsed >= NO_ANSWER_AFTER_S:
        return 'no_answer', ''
    return 'failed', ''


def result_payload(call):
    result, hangup_by = classify(call)
    listened_ms = None
    if result == 'answered' and call.get('ended_at') and call.get('answered_at'):
        listened_ms = int(round((call['ended_at'] - call['answered_at']) * 1000))
    ring_ms = None
    started = call.get('dialed_at')
    first = call.get('answered_at') or call.get('ended_at')
    if started and first:
        ring_ms = max(0, int(round((first - started) * 1000)))
    return {
        'call_id': call['call_id'], 'result': result, 'dialed_at': call.get('dialed_at'),
        'answered_at': call.get('answered_at'), 'ended_at': call.get('ended_at'), 'ring_ms': ring_ms,
        'listened_ms': listened_ms, 'played_to_end': bool(call.get('played_to_end')), 'hangup_by': hangup_by,
        'hangup_cause': call.get('cause'), 'dtmf': ''.join(call.get('dtmf') or []),
        'details': {'line': call.get('line'), 'originate_reason': call.get('originate_reason'),
                    'cause_txt': call.get('cause_txt'), 'ringing_at': call.get('ringing_at')},
    }


# ── AMI ──────────────────────────────────────────────────────────────────────

def parse_messages(buffer):
    """Байты из сокета AMI → (список сообщений-словарей, остаток). Сообщение — строки «Ключ: значение»
    до пустой строки. Ключи повторяются редко (Variable), поэтому последний выигрывает."""
    messages = []
    while True:
        end = buffer.find(b'\r\n\r\n')
        if end < 0:
            return messages, buffer
        block, buffer = buffer[:end], buffer[end + 4:]
        message = {}
        for line in block.decode('utf-8', 'replace').split('\r\n'):
            if ': ' in line:
                key, value = line.split(': ', 1)
                message[key.strip()] = value.strip()
        if message:
            messages.append(message)


class Ami:
    """Минимальный клиент AMI: вход, отправка действий, поток событий в обработчик."""

    def __init__(self, host, port, user, secret, on_event):
        self.host, self.port, self.user, self.secret = host, int(port), user, secret
        self.on_event = on_event
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False

    def connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=AMI_TIMEOUT)
        sock.settimeout(None)
        self.sock = sock
        buffer = b''
        while b'\r\n' not in buffer:           # баннер «Asterisk Call Manager/…»
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('AMI закрыл соединение до входа')
            buffer += chunk
        buffer = buffer.split(b'\r\n', 1)[1]
        self.send({'Action': 'Login', 'ActionID': 'login', 'Username': self.user, 'Secret': self.secret,
                   'Events': 'call,dtmf,user'})
        # Ответ на вход — до событий: с неверным секретом диспетчер не должен считать себя на связи.
        sock.settimeout(AMI_TIMEOUT)
        early = []
        while True:
            messages, buffer = parse_messages(buffer)
            reply = next((m for m in messages if m.get('ActionID') == 'login'), None)
            early += [m for m in messages if m is not reply]
            if reply:
                break
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('AMI закрыл соединение при входе')
            buffer += chunk
        sock.settimeout(None)
        if reply.get('Response') != 'Success':
            sock.close()
            self.sock = None
            raise ConnectionError('AMI не принял вход: %s' % reply.get('Message', ''))
        self.connected = True
        for message in early:
            self.on_event(message)
        threading.Thread(target=self._read, args=(buffer,), daemon=True, name='informer-ami').start()

    def send(self, fields, variables=None):
        lines = [f'{k}: {v}' for k, v in fields.items()]
        lines += [f'Variable: {k}={v}' for k, v in (variables or {}).items()]
        data = ('\r\n'.join(lines) + '\r\n\r\n').encode('utf-8')
        with self.lock:
            if not self.sock:
                raise ConnectionError('AMI не подключён')
            self.sock.sendall(data)

    def _read(self, buffer):
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                messages, buffer = parse_messages(buffer + chunk)
                for message in messages:
                    self.on_event(message)
        except OSError as exc:
            log.warning('Автоинформатор: AMI оборвался: %s', type(exc).__name__)
        finally:
            self.connected = False
            try:
                self.sock.close()
            except OSError:
                pass

    def close(self):
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


# ── диспетчер ────────────────────────────────────────────────────────────────

def load_config():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev = _env_file_values(os.path.join(root, '.env.codex.local'))

    def value(key, default=''):
        return (os.environ.get(key) or dev.get(key) or default).strip()

    lines = [x for x in value('INFORMER_LINES', value('INFORMER_LINE')).replace(',', ' ').split() if x.isdigit()]
    return {
        'portal': value('CDR_BRIDGE_PORTAL').rstrip('/'),
        'key_file': value('CDR_AGENT_KEY_FILE'),
        'private_key': value('CDR_AGENT_PRIVATE_KEY'),
        'proxy': value('CDR_PORTAL_PROXY'),
        'ca_bundle': value('CDR_PORTAL_CA_BUNDLE'),
        'ami_host': value('INFORMER_AMI_HOST', '127.0.0.1'),
        'ami_port': int(value('INFORMER_AMI_PORT', '5038') or 5038),
        'ami_user': value('INFORMER_AMI_USER', 'dispatcher'),
        'ami_secret': value('INFORMER_AMI_SECRET'),
        'lines': lines,
        'per_line': max(1, int(value('INFORMER_CALLS_PER_LINE', '2') or 2)),
        'max_per_minute': max(0, int(value('INFORMER_MAX_PER_MINUTE', '0') or 0)),
        'max_active': max(0, int(value('INFORMER_MAX_ACTIVE', str(MAX_ACTIVE_CALLS)) or 0)),
        'dial_gap_ms': max(0, int(value('INFORMER_DIAL_GAP_MS', str(DIAL_GAP_MS)) or 0)),
        'sounds_dir': value('INFORMER_SOUNDS_DIR', '/var/lib/informer/sounds'),
        'heartbeat_file': value('INFORMER_HEARTBEAT_FILE'),
    }


class Dispatcher:
    def __init__(self, config, session=None, ami=None, clock=time.time, sleep=time.sleep):
        self.config = config
        self.portal = config['portal']
        self.session = session or Bridge._build_session(config)
        self.signer = Bridge._build_signer(config)
        self.clock = clock
        self.sleep = sleep
        # Связь с Binotel (16.10.2026): подряд LINK_TRIP «нет связи» — пауза набора, потом пробный звонок.
        self.link_fails = 0             # «нет связи» подряд
        self.link_level = 0             # 0 — связь есть; N — N-я пауза подряд
        self.link_down_until = 0.0      # до этого момента не звоним
        self.calls = {}                 # call_id → состояние звонка
        self.pending = deque()          # итоги, которые ещё не принял портал
        self.originated = deque()       # моменты наборов — для страховки INFORMER_MAX_PER_MINUTE
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.agent_id = '%s-%d-informer' % (socket.gethostname()[:50], os.getpid())
        self.error = ''
        self.ami = ami or Ami(config['ami_host'], config['ami_port'], config['ami_user'], config['ami_secret'],
                              self.on_event)

    # ── портал ──
    def _post(self, path, payload):
        url_path = '/api/informer/agent/%s' % path
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.signer is None:
            raise RuntimeError('нет ключа подписи (CDR_AGENT_KEY_FILE)')
        headers.update(self.signer.headers('POST', url_path, body))
        response = self.session.post(self.portal + url_path, data=body, timeout=PORTAL_TIMEOUT, headers=headers)
        if response.status_code != 200:
            raise PortalError(response.status_code, response.text[:200])
        return response.json()

    def lines_state(self):
        with self.lock:
            active = {}
            for call in self.calls.values():
                active[call['line']] = active.get(call['line'], 0) + 1
            level, until = self.link_level, self.link_down_until
        state = {line: {'capacity': self.config['per_line'], 'active': active.get(line, 0)}
                 for line in self.config['lines']}
        if level:
            # Связь пропадала: пока пауза — свободных мест нет; пауза кончилась и все звонки закрыты —
            # одно место на первой линии, пробный звонок.
            probe = self.clock() >= until and not any(active.values())
            for index, info in enumerate(state.values()):
                info['capacity'] = info['active'] + (1 if probe and index == 0 else 0)
            return state
        limit = self.max_active()
        if limit:
            # Не больше limit звонков разом: свободные места раздаём по одному, начиная с самых свободных линий,
            # остальным — «занято всё». Портал видит ровно столько мест, сколько робот готов набрать.
            room = max(0, limit - sum(active.values()))
            for info in state.values():
                info['capacity'] = info['active']
            while room:
                takers = [info for info in sorted(state.values(), key=lambda info: info['capacity'])
                          if info['capacity'] < self.config['per_line']]
                if not takers:
                    break
                for info in takers[:room]:
                    info['capacity'] += 1
                room -= min(room, len(takers))
        return state

    def max_active(self):
        """Предел одновременных звонков (INFORMER_MAX_ACTIVE); 0 — без предела."""
        try:
            return max(0, int(self.config.get('max_active', MAX_ACTIVE_CALLS) or 0))
        except (TypeError, ValueError):
            return MAX_ACTIVE_CALLS

    def active_room(self):
        """Сколько звонков ещё можно начать разом; без предела — сколько угодно."""
        limit = self.max_active()
        if not limit:
            return float('inf')
        with self.lock:
            return limit - len(self.calls)

    def link_error(self):
        """Что показать руководителю в карточке робота, пока связи с Binotel нет."""
        with self.lock:
            level, until = self.link_level, self.link_down_until
        if not level:
            return ''
        left = until - self.clock()
        if left > 0:
            return 'Сеть не пропускает звонки робота — набор на паузе, проверка через %d мин' % max(1, round(left / 60))
        return 'Сеть не пропускала звонки — проверяем одним звонком'

    def _note_link(self, result):
        """Связь с Binotel по итогу звонка; вызывается под self.lock. Любой ответ Binotel (дозвон,
        недозвон, занято, «линия занята») — полный ход; «Ошибка АТС» связь не характеризует."""
        now = self.clock()
        if result == 'no_link':
            if self.link_level and now < self.link_down_until:
                return                  # хвост звонков, набранных до паузы, — паузу не продлевает
            self.link_fails += 1
            if self.link_level or self.link_fails >= LINK_TRIP:
                pause = LINK_BACKOFF_S[min(self.link_level, len(LINK_BACKOFF_S) - 1)]
                self.link_level += 1
                self.link_fails = 0
                self.link_down_until = now + pause
                log.warning('Автоинформатор: сеть не пропускает звонки — набор на паузе %d с', pause)
        elif result != 'failed':
            if self.link_level:
                log.info('Автоинформатор: звонки снова проходят — звоним дальше')
            self.link_fails = 0
            self.link_level = 0
            self.link_down_until = 0.0

    def rate_room(self):
        """Сколько ещё можно набрать в эту минуту: без страховки (0) — без предела."""
        now = self.clock()
        while self.originated and now - self.originated[0] >= 60:
            self.originated.popleft()
        cap = self.config.get('max_per_minute') or 0
        return cap - len(self.originated) if cap > 0 else float('inf')

    # ── записи ──
    def sound_path(self, sha):
        return os.path.join(self.config['sounds_dir'], '%s.wav' % sha)

    def ensure_sound(self, campaign_id, sha):
        """Запись рассылки на диске робота: скачать один раз, проверить хеш, положить атомарно."""
        path = self.sound_path(sha)
        if os.path.exists(path):
            return path
        data = self._post('audio', {'campaign_id': campaign_id})
        raw = base64.b64decode(data.get('wav_base64') or '')
        if hashlib.sha256(raw).hexdigest() != sha:
            raise RuntimeError('запись рассылки %s пришла повреждённой' % campaign_id)
        tmp = path + '.part'
        with open(tmp, 'wb') as handle:
            handle.write(raw)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return path

    # ── звонок ──
    def originate(self, job):
        call_id = int(job['call_id'])
        line = str(job['line'])
        phone = ''.join(ch for ch in str(job.get('phone') or '') if ch.isdigit())
        if line not in self.config['lines'] or len(phone) < 10:
            self.finish({'call_id': call_id, 'line': line, 'dialed_at': self.clock(), 'ended_at': self.clock(),
                         'cause': None, 'originate_reason': None})
            return
        path = self.ensure_sound(job['campaign_id'], job['audio_sha256'])
        now = self.clock()
        audio_s = max(0, int(job.get('audio_ms') or 0)) / 1000
        call = {'call_id': call_id, 'line': line, 'dialed_at': now, 'ringing_at': None, 'answered_at': None,
                'ended_at': None, 'played_to_end': False, 'dtmf': [], 'cause': None, 'cause_txt': '',
                'originate_reason': None,
                'deadline': now + RING_TIMEOUT_MS / 1000 + audio_s + STUCK_MARGIN_S}
        with self.lock:
            self.calls[call_id] = call
        self.originated.append(now)
        self.ami.send({
            'Action': 'Originate', 'ActionID': 'inf-%d' % call_id, 'ChannelId': 'inf-%d' % call_id,
            'Channel': 'PJSIP/%s@line%s' % (phone, line), 'Context': 'informer-play', 'Exten': 's',
            'Priority': '1', 'Timeout': str(RING_TIMEOUT_MS), 'Async': 'true',
        }, variables={'INFORMER_CALL': str(call_id), 'INFORMER_FILE': path[:-4]})
        log.info('Автоинформатор: звонок %s — набираем с линии %s', call_id, line)

    def on_event(self, event):
        name = event.get('Event')
        uid = event.get('Uniqueid') or ''
        call_id = None
        if uid.startswith('inf-'):
            call_id = uid[4:]
        elif name == 'OriginateResponse' and (event.get('ActionID') or '').startswith('inf-'):
            call_id = event['ActionID'][4:]
        elif name is None and event.get('Response') == 'Error' and (event.get('ActionID') or '').startswith('inf-'):
            # Asterisk не принял сам набор (нет такой линии, нет прав) — событий звонка не будет.
            call_id = event['ActionID'][4:]
            if call_id.isdigit():
                log.warning('Автоинформатор: звонок %s — Asterisk отказал в наборе: %s', call_id,
                            event.get('Message', '')[:120])
                with self.lock:
                    call = self.calls.pop(int(call_id), None)
                    if call:
                        call['ended_at'] = self._stamp(event)
                        self._queue(call)
            return
        elif name == 'UserEvent' and event.get('Call'):
            call_id = event.get('Call')
        if not call_id or not call_id.isdigit():
            return
        stamp = self._stamp(event)
        with self.lock:
            call = self.calls.get(int(call_id))
            if not call:
                return
            if name == 'Newstate':
                state = event.get('ChannelState')
                if state in ('4', '5') and not call['ringing_at']:
                    call['ringing_at'] = stamp
                elif state == '6' and not call['answered_at']:
                    call['answered_at'] = stamp
            elif name == 'UserEvent' and event.get('UserEvent') == 'InformerPlayed':
                call['played_to_end'] = True
            elif name == 'DTMFEnd' and event.get('Direction') == 'Received':
                if len(call['dtmf']) < 32:
                    call['dtmf'].append(event.get('Digit', '')[:1])
            elif name == 'OriginateResponse' and event.get('Response') == 'Failure':
                try:
                    call['originate_reason'] = int(event.get('Reason') or 0)
                except ValueError:
                    call['originate_reason'] = 0
                # Канала нет вовсе — Hangup не придёт: закрываем по ответу набора.
                if event.get('Uniqueid') in (None, '', '<null>') or event.get('Channel') in (None, '', '<null>'):
                    call['ended_at'] = stamp
                    finished = self.calls.pop(int(call_id))
                    self._queue(finished)
                return
            elif name == 'Hangup':
                try:
                    call['cause'] = int(event.get('Cause') or 0)
                except ValueError:
                    call['cause'] = 0
                call['cause_txt'] = event.get('Cause-txt', '')[:64]
                call['ended_at'] = stamp
                finished = self.calls.pop(int(call_id))
                self._queue(finished)

    def _stamp(self, event):
        try:
            return float(event.get('Timestamp'))
        except (TypeError, ValueError):
            return self.clock()

    def finish(self, call):
        with self.lock:
            self.calls.pop(int(call['call_id']), None)
            self._queue(call)

    def _queue(self, call):
        payload = result_payload(call)
        if not call.get('local'):
            self._note_link(payload['result'])
        self.pending.append(payload)
        self.wake.set()

    def close_stuck(self):
        """Звонок, который не кончился за «вызов + запись + запас», — закрыть: держать ёмкость линии
        вечно нельзя. Срок свой у каждого звонка: запись бывает и 15 секунд, и пять минут."""
        now = self.clock()
        with self.lock:
            stuck = [cid for cid, call in self.calls.items() if now > call['deadline']]
            for cid in stuck:
                call = self.calls.pop(cid)
                call['ended_at'] = now
                self._queue(call)
        for cid in stuck:
            log.warning('Автоинформатор: звонок %s не кончился в срок — закрыт', cid)

    def flush_results(self):
        while self.pending:
            payload = self.pending[0]
            try:
                self._post('result', payload)
            except PortalError as exc:
                if 400 <= exc.status < 500 and exc.status not in (401, 429):
                    log.error('Автоинформатор: портал отверг итог %s: %s', payload['call_id'], exc)
                    self.pending.popleft()
                    continue
                raise
            self.pending.popleft()

    # ── цикл ──
    def tick(self):
        """Один круг: AMI, итоги, опрос, наборы. Возвращает паузу до следующего круга."""
        if not self.ami.connected:
            try:
                self.ami.connect()
                log.info('Автоинформатор: AMI подключён')
            except OSError as exc:
                self.error = 'AMI недоступен: %s' % type(exc).__name__
        self.close_stuck()
        self.flush_results()
        answer = self._post('poll', {'agent_id': self.agent_id, 'version': VERSION, 'ami_ok': self.ami.connected,
                                     'error': self.error or self.link_error(), 'lines': self.lines_state()})
        self.error = ''
        # Шаг набора: свой нижний предел или шаг по темпу рассылки от портала (15 в минуту — 4 с), что больше.
        try:
            portal_gap = max(0, int(answer.get('dial_gap_ms') or 0))
        except (TypeError, ValueError):
            portal_gap = 0
        gap = min(MAX_DIAL_GAP_MS, max(self.config.get('dial_gap_ms') or 0, portal_gap)) / 1000
        dialed = 0
        for job in answer.get('calls') or []:
            paused = self.link_level and self.clock() < self.link_down_until
            if paused or self.rate_room() <= 0 or self.active_room() <= 0:
                # Сверх предела одновременных (INFORMER_MAX_ACTIVE), страховки INFORMER_MAX_PER_MINUTE или в паузе
                # без связи не звоним: «линия занята» вернёт водителя в очередь. Отказ свой, не Binotel, — о связи
                # он ничего не говорит.
                self.finish({'call_id': int(job['call_id']), 'line': str(job.get('line')), 'dialed_at': self.clock(),
                             'ended_at': self.clock(), 'cause': 19, 'originate_reason': None, 'local': True})
                continue
            if dialed and gap:
                self.sleep(gap)         # не залпом: итоги тем временем принимает поток AMI
            try:
                self.originate(job)
                dialed += 1
            except Exception as exc:  # noqa: BLE001 — один плохой звонок не останавливает рассылку
                log.warning('Автоинформатор: звонок %s не поставлен: %s', job.get('call_id'), type(exc).__name__)
                self.finish({'call_id': int(job['call_id']), 'line': str(job.get('line')), 'dialed_at': self.clock(),
                             'ended_at': self.clock(), 'cause': None, 'originate_reason': None, 'local': True})
        self.flush_results()
        self.beat()
        return max(1, min(int(answer.get('next_poll_sec') or IDLE_POLL_S), MAX_POLL_S))

    def beat(self):
        target = self.config.get('heartbeat_file')
        if target:
            try:
                with open(target, 'w') as handle:
                    handle.write(str(int(self.clock())))
            except OSError:
                pass

    def run(self):
        cap = self.config.get('max_per_minute') or 0
        limit = self.max_active()
        log.info('Автоинформатор %s: линии %s по %d звонка, %s, %s, пауза между наборами %d мс, портал %s', VERSION,
                 ','.join(self.config['lines']), self.config['per_line'],
                 'не больше %d разом' % limit if limit else 'без предела одновременных',
                 'не больше %d в минуту' % cap if cap > 0 else 'без минутного предела',
                 self.config.get('dial_gap_ms') or 0, self.portal)
        backoff = RESULT_RETRY_S
        while True:
            try:
                pause = self.tick()
                backoff = RESULT_RETRY_S
            except Exception as exc:  # noqa: BLE001
                log.warning('Автоинформатор: круг не вышел: %s', str(exc)[:200])
                pause = backoff
                backoff = min(backoff * 2, MAX_POLL_S)
            self.wake.wait(pause)
            self.wake.clear()


class PortalError(RuntimeError):
    def __init__(self, status, text):
        super().__init__('портал ответил %d: %s' % (status, text))
        self.status = status


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = load_config()
    if not config['portal'] or not config['lines'] or not config['ami_secret']:
        raise SystemExit('нужны CDR_BRIDGE_PORTAL, INFORMER_LINES и INFORMER_AMI_SECRET')
    Dispatcher(config).run()


if __name__ == '__main__':
    main()
