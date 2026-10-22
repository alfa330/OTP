# -*- coding: utf-8 -*-
"""Клиент AMI (Asterisk Manager Interface) для коннектора АТС: события в обработчик, действия с
ответом по ActionID, переподключение.

Чем отличается от `Ami` диспетчера автоинформатора (cdr_bridge/informer.py): тому достаточно
слать Originate в свой робот на loopback и ловить события; этому нужно ждать ответ на каждое
действие (Originate с Async даёт ответ сразу, но Getvar, Hangup, Ping — по смыслу запрос-ответ),
держать соединение к чужой станции сутками и восстанавливать его самому. Поэтому — отдельный
модуль, а не вторая правка диспетчера посреди рассылок.

Протокол (Asterisk Call Manager/5.0.4 на станции): строки `Ключ: значение`, сообщение кончается
пустой строкой; ответ на действие несёт тот же `ActionID`. У списковых действий
(`CoreShowChannels`) ответ `Response: Success` + события с `EventList: start … Complete`;
здесь нужен только CoreShowChannels при старте — чтобы знать о звонках, начавшихся до нас.

Безопасность: учётка в окружении контейнера, в журнал не пишется; события приходят только
нужных классов (`Events: call,agent,dialing,originate`, см. cdr/pbx_calls.py). Что именно
коннектору можно делать на станции (Originate, Hangup), решает портал — этот модуль лишь
транспорт.
"""

import logging
import socket
import threading
import time
import uuid

log = logging.getLogger('cdr_bridge.ami')

CONNECT_TIMEOUT = 10
ACTION_TIMEOUT = 10
# Ping раз в столько секунд тишины: NAT/фаервол между шлюзом и станцией не должен считать
# соединение мёртвым, а мы — узнаём об обрыве за минуту, а не когда понадобится Originate.
KEEPALIVE_SECONDS = 30


class AmiError(Exception):
    """Станция не ответила или ответила отказом."""


def parse_messages(buffer):
    """Байты сокета → (список сообщений-словарей, остаток). Повторяющиеся ключи (у списковых
    ответов — `Variable`) последний выигрывает; коннектору они не нужны."""
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
            elif line.endswith(':'):
                message[line[:-1].strip()] = ''
        if message:
            messages.append(message)


def encode_action(fields, variables=None):
    """Словарь действия → байты. Значения приводятся к строке; перевод строки в значении
    обрезается: он разорвал бы сообщение и подставил бы чужие поля."""
    lines = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace('\r', ' ').replace('\n', ' ')
        lines.append('%s: %s' % (key, text))
    for key, value in (variables or {}).items():
        text = str(value).replace('\r', ' ').replace('\n', ' ')
        lines.append('Variable: %s=%s' % (key, text))
    return ('\r\n'.join(lines) + '\r\n\r\n').encode('utf-8')


class Ami:
    """Одно соединение с AMI. `connect()` — вход; события уходят в `on_event` из потока чтения;
    `action()` — запрос с ожиданием ответа; `connected` — живо ли соединение."""

    def __init__(self, host, port, user, secret, on_event, events='call,agent,dialing,originate',
                 on_disconnect=None):
        self.host, self.port, self.user, self.secret = host, int(port), user, secret
        self.on_event = on_event
        self.on_disconnect = on_disconnect
        self.events = events
        self.sock = None
        self.connected = False
        self._send_lock = threading.Lock()
        self._waiters = {}          # ActionID → {'event': Event, 'reply': dict}
        self._waiters_lock = threading.Lock()
        self._reader = None
        self.last_rx = 0.0

    # ── соединение ───────────────────────────────────────────────────────────

    def connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        sock.settimeout(CONNECT_TIMEOUT)
        buffer = b''
        while b'\r\n' not in buffer:          # баннер «Asterisk Call Manager/…»
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise AmiError('AMI закрыл соединение до входа')
            buffer += chunk
        buffer = buffer.split(b'\r\n', 1)[1]
        sock.sendall(encode_action({'Action': 'Login', 'ActionID': 'login', 'Username': self.user,
                                    'Secret': self.secret, 'Events': self.events}))
        early = []
        reply = None
        while reply is None:
            messages, buffer = parse_messages(buffer)
            for message in messages:
                if message.get('ActionID') == 'login':
                    reply = message
                else:
                    early.append(message)
            if reply is None:
                chunk = sock.recv(4096)
                if not chunk:
                    sock.close()
                    raise AmiError('AMI закрыл соединение при входе')
                buffer += chunk
        if reply.get('Response') != 'Success':
            sock.close()
            raise AmiError('AMI не принял вход: %s' % reply.get('Message', ''))
        sock.settimeout(None)
        self.sock = sock
        self.connected = True
        self.last_rx = time.time()
        for message in early:
            self._dispatch(message)
        self._reader = threading.Thread(target=self._read, args=(buffer,), daemon=True,
                                        name='pbx-ami-reader')
        self._reader.start()

    def close(self):
        self.connected = False
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _read(self, buffer):
        sock = self.sock
        reason = 'соединение закрыто станцией'
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                self.last_rx = time.time()
                messages, buffer = parse_messages(buffer + chunk)
                for message in messages:
                    self._dispatch(message)
        except OSError as exc:
            reason = 'обрыв: %s' % type(exc).__name__
        finally:
            was_connected = self.connected
            self.connected = False
            try:
                sock.close()
            except OSError:
                pass
            # Ждущих ответа отпускаем: ответа не будет.
            with self._waiters_lock:
                waiters = list(self._waiters.values())
                self._waiters.clear()
            for waiter in waiters:
                waiter['reply'] = {'Response': 'Error', 'Message': 'AMI оборвался'}
                waiter['event'].set()
            if was_connected:
                log.warning('AMI: %s', reason)
                if self.on_disconnect is not None:
                    try:
                        self.on_disconnect(reason)
                    except Exception:  # noqa: BLE001
                        log.exception('AMI: обработчик обрыва упал')

    def _dispatch(self, message):
        action_id = message.get('ActionID')
        # Ответ на действие — сообщение БЕЗ поля Event. У события OriginateResponse есть и
        # ActionID нашего Originate, и поле Response («Success»/«Failure»): приди оно раньше,
        # чем вызывающий снял ожидание, оно подменило бы ответ и не дошло бы до обработчика —
        # итог команды потерял бы причину отказа.
        if action_id and 'Response' in message and 'Event' not in message:
            with self._waiters_lock:
                waiter = self._waiters.get(action_id)
            if waiter is not None:
                # Списковый ответ: Response приходит сразу, элементы — событиями с тем же
                # ActionID; собираем их до *Complete.
                if message.get('EventList') == 'start':
                    waiter['reply'] = message
                    waiter['items'] = []
                    return
                waiter['reply'] = message
                waiter['event'].set()
                return
        if action_id and 'Event' in message:
            with self._waiters_lock:
                waiter = self._waiters.get(action_id)
            if waiter is not None and 'items' in waiter:
                if message.get('EventList') == 'Complete':
                    # Список дошёл целиком: только такому вызывающий может верить как полному.
                    waiter['complete'] = True
                    waiter['event'].set()
                else:
                    waiter['items'].append(message)
                return
        if 'Event' in message:
            try:
                self.on_event(message)
            except Exception:  # noqa: BLE001
                log.exception('AMI: обработчик события %s упал', message.get('Event'))

    # ── действия ─────────────────────────────────────────────────────────────

    def send(self, fields, variables=None):
        """Отправить без ожидания ответа (Logoff и подобное)."""
        data = encode_action(fields, variables)
        with self._send_lock:
            if not self.sock or not self.connected:
                raise AmiError('AMI не подключён')
            self.sock.sendall(data)

    def action(self, fields, variables=None, timeout=ACTION_TIMEOUT):
        """Отправить и дождаться ответа. Возвращает словарь ответа (у списковых — с ключом
        `_items`). AmiError — нет соединения или ответа в срок; отказ станции (Response: Error)
        возвращается как есть — решает вызывающий."""
        fields = dict(fields)
        action_id = fields.get('ActionID') or ('otp-%s' % uuid.uuid4().hex[:12])
        fields['ActionID'] = action_id
        waiter = {'event': threading.Event(), 'reply': None}
        with self._waiters_lock:
            self._waiters[action_id] = waiter
        try:
            self.send(fields, variables)
            if not waiter['event'].wait(timeout):
                raise AmiError('AMI не ответил на %s за %d с' % (fields.get('Action'), timeout))
        finally:
            with self._waiters_lock:
                self._waiters.pop(action_id, None)
        reply = dict(waiter['reply'] or {})
        if 'items' in waiter:
            reply['_items'] = waiter['items']
            # Обрыв посреди списка отпускает ожидание с частью элементов — это не «весь список».
            reply['_complete'] = bool(waiter.get('complete'))
        return reply

    def ping(self):
        reply = self.action({'Action': 'Ping'}, timeout=ACTION_TIMEOUT)
        return reply.get('Response') == 'Success'

    def idle_seconds(self, now=None):
        return (now or time.time()) - self.last_rx


# ── сборка действий: одна форма на обе стороны (тесты проверяют именно её) ────

def originate_action(ext, dial, action_id, caller_name='', context='from-internal',
                     timeout_ms=30000, variables=None):
    """Позвонить клиенту с телефона оператора: сначала звонит телефон `PJSIP/<ext>`, оператор
    снимает трубку — станция набирает `dial` (`<префикс>*7XXXXXXXXXX`) тем же путём, что при
    ручном наборе. CallerID: номер — внутренний номер оператора (так FreePBX находит его
    учётку и правила маршрута, а CDR пишет src = оператор, как у ручного набора), имя — что
    набираем: телефон покажет клиента, пока звонит."""
    fields = {
        'Action': 'Originate',
        'ActionID': action_id,
        'Channel': 'PJSIP/%s' % ext,
        'Context': context,
        'Exten': dial,
        'Priority': 1,
        'CallerID': '"%s" <%s>' % ((caller_name or dial).replace('"', ''), ext),
        'Timeout': int(timeout_ms),
        'Async': 'true',
    }
    return fields, dict(variables or {})


SPY_MODES = {
    # Слушать молча: без сигнала (q), выйти, когда прослушиваемый канал отбился (E), не ждать
    # новых каналов, если подходящего нет (S). Разговор для сторон не меняется.
    'listen': 'qES',
    # Шептать оператору: клиент супервайзера не слышит.
    'whisper': 'qESw',
    # Войти в разговор: слышат оба.
    'barge': 'qESB',
}


def spy_action(supervisor_ext, target_channel_prefix, mode, action_id):
    """Подключение супервайзера к разговору: его телефон звонит, снял — ChanSpy на канал
    оператора. `target_channel_prefix` — `PJSIP/<ext>-` (ChanSpy сравнивает по началу имени;
    без дефиса `PJSIP/672` подошёл бы и к 6720)."""
    options = SPY_MODES.get(mode)
    if options is None:
        raise ValueError('режим подключения: listen, whisper или barge')
    fields = {
        'Action': 'Originate',
        'ActionID': action_id,
        'Channel': 'PJSIP/%s' % supervisor_ext,
        'Application': 'ChanSpy',
        'Data': '%s,%s' % (target_channel_prefix, options),
        'CallerID': '"%s" <%s>' % ({'listen': 'Прослушка', 'whisper': 'Подсказка',
                                     'barge': 'Подключение'}[mode], supervisor_ext),
        'Timeout': 30000,
        'Async': 'true',
    }
    return fields


def hangup_action(channel, action_id):
    """Положить трубку конкретному каналу (по точному имени, не по шаблону)."""
    return {'Action': 'Hangup', 'ActionID': action_id, 'Channel': channel, 'Cause': 16}
