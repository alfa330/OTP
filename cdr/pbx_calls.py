# -*- coding: utf-8 -*-
"""Живое состояние звонков станции по событиям AMI — общий модуль коннектора и портала.

Зачем
-----
«Касания» и «Табло ОП» живут на CDR: строка звонка появляется ПОСЛЕ отбоя, и даже живой хвост
моста (опрос базы раз в 20 с) видит звонок, когда он уже кончился. Коннектор АТС
(`cdr_bridge/pbx_connector.py`) сидит на AMI станции и видит звонок в момент, когда он
происходит: кто звонит, в какой очереди ждёт, у кого звонит телефон, кто снял трубку, кто
положил. Это тот же протокол, на котором работают коннекторы amoCRM (itgrix) и FOP2 — они
сидят на том же AMI той же учёткой.

Модуль чистый: на вход — словари событий AMI, на выход — состояние звонков. Ни сокетов, ни
базы: его одинаково читают тесты, коннектор на шлюзе и портал (форма записи звонка — здесь).

Как Asterisk показывает звонок (сверено на станции 22.10.2026)
---------------------------------------------------------------
Все каналы одного звонка связаны `Linkedid` — это uniqueid ПЕРВОГО канала звонка; тот же
`linkedid`, что в CDR и в журнале очередей, поэтому живой звонок потом сходится с касанием.

* **Входящий**: первый канал — транк `PJSIP/<имя линии>-<hex>` (контекст `from-trunk`/
  `ext-did…`, `CallerIDNum` — номер клиента, `Exten` — набранный номер парка, DID). Дальше
  `QueueCallerJoin` (очередь), `AgentCalled` на каждого оператора, которому звонят
  (`Interface: Local/6726@from-queue/n`), `AgentConnect` (снял трубку: `MemberName`,
  `HoldTime` — ожидание), `BridgeEnter` канала оператора `PJSIP/6726-<hex>`, `Hangup`.
  Кто положил трубку — `AgentComplete.Reason` (`caller`/`agent`), а без очереди —
  `HangupRequest`: его получает канал той стороны, которая отбилась сама (BYE от телефона
  или от оператора связи), второму каналу мост шлёт `SoftHangupRequest`. Порядок самих
  `Hangup` ничего не значит: когда трубку кладёт оператор, первым отбивается транк
  (снято на станции 22.10.2026).
* **Исходящий**: первый канал — оператор `PJSIP/6726-<hex>` в `from-internal`, `Exten` —
  набор `3333*77XXXXXXXXX`: четыре цифры префикса выбирают исходящий маршрут (линию парка),
  после звёздочки номер. `DialBegin` → транк, `DialEnd` с `DialStatus: ANSWER` — клиент
  снял трубку, `BridgeEnter`, `Hangup`.
* **Заявка автообзвона**: первый канал `Local/<очередь>@ext-to-queue-…;1`, дальше как у
  исходящего через транк, а ответивший клиент попадает в очередь к операторам.
* **Внутренний** (оператор оператору): первый канал оператора, `Exten` — внутренний номер.

Границы, проведённые осознанно
------------------------------
* Звонок кончается, когда отбился его первый канал ИЛИ не осталось ни одного канала: у
  автообзвона первый канал (Local;1) живёт, пока клиент в очереди; у входящего транк — пока
  клиент на линии.
* Сторона отбоя ставится только у состоявшегося разговора: отбой телефона оператора, который
  просто звонил (очередь сняла вызов, причина 26 «ответили в другом месте»), — не его решение.
* Переводы и конференции не разбираются: такой звонок остаётся за тем, кто его принял.
  В сутках ОП их единицы ([[freepbx-outgoing-hangup-side-cel]]), ради них усложнять нечего.
* Законченные звонки держатся KEEP_ENDED_SECONDS: портал успевает их получить и показать
  «только что закончился», робот пропущенных — увидеть ответ клиенту; потом они уходят, а
  правдой становится CDR.
"""

import re
from datetime import datetime

from cdr import touches as touches_mod

__all__ = ['CallTracker', 'WANTED_EVENTS', 'EVENT_MASK', 'STATES', 'parse_dial', 'dial_string',
           'ext_of_channel', 'ext_of_interface', 'channel_kind']

# Классы событий AMI, которые коннектор просит у станции при входе (`Events: …`): звонки и
# очереди. Класс `reporting` (RTCP на каждый пакет статистики), `dialplan` (VarSet, Newexten —
# сотни на звонок) и `system` остаются на станции: за 45 с тихого дня их пришло больше, чем
# всего остального вместе.
EVENT_MASK = 'call,agent,dialing,originate'

# Какие события меняют состояние. Остальные (QueueMemberStatus, DeviceStateChange, RTCP…)
# коннектор отбрасывает, не разбирая.
WANTED_EVENTS = frozenset((
    'Newchannel', 'Newstate', 'DialBegin', 'DialEnd', 'BridgeEnter', 'HangupRequest', 'Hangup',
    'QueueCallerJoin', 'QueueCallerLeave', 'QueueCallerAbandon',
    'AgentCalled', 'AgentRingNoAnswer', 'AgentConnect', 'AgentComplete',
    'OriginateResponse',
))

STATES = ('new', 'ringing', 'queued', 'talking', 'ended')

DIRECTION_IN = 'in'
DIRECTION_OUT = 'out'
DIRECTION_INTERNAL = 'internal'
DIRECTION_AUTODIAL = 'autodial'

# Сколько держать законченный звонок после отбоя.
KEEP_ENDED_SECONDS = 300

# Канал оператора: `PJSIP/6726-00001a98`. Внутренние номера на станции трёх–пятизначные; транк —
# `PJSIP/<имя линии>-<hex>`, и имя линии содержит буквы или знаки (`+77…_jana`, `yVnnduj2_70…`).
_EXT_CHANNEL_RE = re.compile(r'^PJSIP/(\d{3,5})-[0-9a-fA-F]+$')
_PJSIP_RE = re.compile(r'^PJSIP/')
# `Local/6726@from-queue-00001a36;1`, `Local/3016@ext-to-queue-00001a34;1`.
_LOCAL_RE = re.compile(r'^Local/([^@/]+)@(.+?)(?:-[0-9a-fA-F]{6,})?(?:;[12])?(?:/n)?$')
# Набор исходящего: четыре цифры префикса маршрута, звёздочка, номер (с плюсом или без).
_DIAL_RE = re.compile(r'^(\d{4})\*(\+?\d{7,15})$')
_EXT_RE = re.compile(r'^\d{3,5}$')
_QUEUE_RE = re.compile(r'^3\d{3}$')

KIND_EXT, KIND_TRUNK, KIND_LOCAL, KIND_OTHER = 'ext', 'trunk', 'local', 'other'


def channel_kind(name):
    """`PJSIP/6726-…` → ext, `PJSIP/<линия>-…` → trunk, `Local/…` → local, прочее → other."""
    name = str(name or '')
    if _EXT_CHANNEL_RE.match(name):
        return KIND_EXT
    if _PJSIP_RE.match(name):
        return KIND_TRUNK
    if name.startswith('Local/'):
        return KIND_LOCAL
    return KIND_OTHER


def ext_of_channel(name):
    """Внутренний номер из имени канала оператора или ''."""
    match = _EXT_CHANNEL_RE.match(str(name or ''))
    return match.group(1) if match else ''


def ext_of_interface(interface):
    """`Local/6726@from-queue/n` → '6726'; `PJSIP/6726` → '6726'; иное → ''."""
    value = str(interface or '')
    match = _LOCAL_RE.match(value)
    if match and _EXT_RE.match(match.group(1)):
        return match.group(1)
    if value.startswith('PJSIP/'):
        tail = value[6:].split('-', 1)[0]
        return tail if _EXT_RE.match(tail) else ''
    return ''


def _local_parts(name):
    """(exten, context) у Local-канала или ('', '')."""
    match = _LOCAL_RE.match(str(name or ''))
    return (match.group(1), match.group(2)) if match else ('', '')


def parse_dial(exten):
    """`3333*77015550001` → ('3333', '7015550001'); не набор клиента → ('', '')."""
    match = _DIAL_RE.match(str(exten or '').strip())
    if not match:
        return '', ''
    phone = touches_mod.norm_phone(match.group(2))
    return (match.group(1), phone) if phone else ('', '')


def dial_string(prefix, phone):
    """Что набрать на станции, чтобы позвонить клиенту с линии: `<префикс>*7<10 цифр>`.

    Так же набирает телефон оператора (сверено с CDR 22.10.2026: `3333*+77…`, `5050*77…`);
    маршрут станции сам ставит «+» перед 77XXXXXXXXX. ValueError — если префикс не четыре
    цифры или номер не казахстанский мобильный/городской в десять цифр."""
    prefix = str(prefix or '').strip()
    digits = touches_mod.norm_phone(phone)
    if not re.match(r'^\d{4}$', prefix):
        raise ValueError('префикс линии — четыре цифры исходящего маршрута станции')
    if not digits:
        raise ValueError('номер клиента должен содержать десять цифр')
    return '%s*7%s' % (prefix, digits)


def _stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S')


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _new_call(linkedid, started_at):
    return {
        'linkedid': linkedid,
        'direction': '',
        'phone': '',
        'did': '',
        'queue': '',
        'ext': '',
        'ringing': [],
        'state': 'new',
        'started_at': started_at,
        'queued_at': '',
        'ringing_at': '',
        'answered_at': '',
        'ended_at': '',
        'wait_seconds': None,
        'hangup_cause': None,
        'hangup_side': '',
        'dial_prefix': '',
        'dial_status': '',
        'channel': '',
        'agent_channel': '',
        'recording_file': '',
        'origin': '',
        'command_id': None,
        'seq': 0,
    }


class CallTracker:
    """Состояние звонков станции. `feed(событие)` → список изменившихся звонков (копии).

    `now` — функция времени (datetime), подменяется в тестах. Время в записях — местное
    время машины, где работает коннектор (на шлюзе TZ=Asia/Almaty, как у CDR станции).
    """

    def __init__(self, now=None, keep_ended_seconds=KEEP_ENDED_SECONDS):
        self._now = now or datetime.now
        self.keep_ended_seconds = keep_ended_seconds
        self.calls = {}        # linkedid → звонок
        self._channels = {}    # uniqueid → {'name', 'linkedid', 'kind'}
        self._seq = 0
        self._at = None
        # ActionID нашего Originate → id команды портала: OriginateResponse приходит с ним и
        # с Uniqueid нового канала, по которому звонок получает origin='portal'.
        self._pending_originates = {}

    # ── снаружи ──────────────────────────────────────────────────────────────

    def feed(self, event, at=None):
        """Одно событие AMI → список изменившихся звонков (снимки). Чужие события — [].

        `at` — момент события, если он известен точнее «сейчас»: так при входе на станцию
        каналы, живущие уже минуту, получают настоящее начало (CoreShowChannels.Duration)."""
        name = str(event.get('Event') or '')
        handler = getattr(self, '_on_' + name.lower(), None)
        if handler is None:
            return []
        self._at = at
        try:
            changed = handler(event)
        finally:
            self._at = None
        if not changed:
            return []
        out = []
        for call in changed:
            self._seq += 1
            call['seq'] = self._seq
            out.append(self.snapshot_of(call))
        return out

    def live(self):
        """Все звонки, которые ещё помним: идущие и только что законченные."""
        return [self.snapshot_of(call) for call in self.calls.values()]

    def active(self):
        return [self.snapshot_of(call) for call in self.calls.values() if call['state'] != 'ended']

    def expire(self, now=None):
        """Забыть законченные звонки старше KEEP_ENDED_SECONDS. Возвращает их linkedid."""
        moment = now or self._now()
        gone = []
        for linkedid, call in list(self.calls.items()):
            if call['state'] != 'ended' or not call['ended_at']:
                continue
            ended = datetime.strptime(call['ended_at'], '%Y-%m-%dT%H:%M:%S')
            if (moment - ended).total_seconds() >= self.keep_ended_seconds:
                gone.append(linkedid)
                del self.calls[linkedid]
        # Каналы законченных звонков уже сняты в Hangup; здесь — на случай потерянного события.
        for uniqueid, channel in list(self._channels.items()):
            if channel['linkedid'] in gone:
                del self._channels[uniqueid]
        return gone

    def expect_originate(self, action_id, command_id):
        """Запомнить наш Originate: ответ придёт событием OriginateResponse с этим ActionID."""
        self._pending_originates[str(action_id)] = command_id

    def set_recording(self, linkedid, filename):
        call = self.calls.get(linkedid)
        if not call or not filename or call['recording_file'] == filename:
            return []
        call['recording_file'] = str(filename)[:200]
        return self._bump([call])

    @staticmethod
    def snapshot_of(call):
        out = dict(call)
        out['ringing'] = sorted(set(call['ringing']))
        return out

    # ── внутреннее ───────────────────────────────────────────────────────────

    def _bump(self, calls):
        out = []
        for call in calls:
            self._seq += 1
            call['seq'] = self._seq
            out.append(self.snapshot_of(call))
        return out

    def _stamp_now(self):
        return _stamp(self._at or self._now())

    def _call_of(self, event, create=True):
        linkedid = str(event.get('Linkedid') or event.get('Uniqueid') or '').strip()
        if not linkedid:
            return None
        call = self.calls.get(linkedid)
        if call is None and create:
            call = _new_call(linkedid, self._stamp_now())
            self.calls[linkedid] = call
        return call

    def _channels_of(self, linkedid):
        return [c for c in self._channels.values() if c['linkedid'] == linkedid]

    @staticmethod
    def _ring(call, ext, now_stamp):
        if ext and ext not in call['ringing']:
            call['ringing'].append(ext)
        if ext and not call['ringing_at']:
            call['ringing_at'] = now_stamp
        if call['state'] in ('new', 'queued', 'ringing'):
            call['state'] = 'ringing'

    @staticmethod
    def _unring(call, ext):
        if ext in call['ringing']:
            call['ringing'].remove(ext)
        if call['state'] == 'ringing' and not call['ringing']:
            # Телефоны замолчали, а звонок жив: клиент ждёт в очереди или снова слушает IVR.
            call['state'] = 'queued' if call['queue'] else 'new'

    def _answered(self, call, ext='', agent_channel=''):
        now_stamp = self._stamp_now()
        if ext:
            call['ext'] = ext
            if ext in call['ringing']:
                call['ringing'].remove(ext)
        if agent_channel:
            call['agent_channel'] = agent_channel
        if not call['answered_at']:
            call['answered_at'] = now_stamp
        call['ringing'] = []
        if call['state'] != 'ended':
            call['state'] = 'talking'

    # ── события ──────────────────────────────────────────────────────────────

    def _on_newchannel(self, event):
        name = str(event.get('Channel') or '')
        uniqueid = str(event.get('Uniqueid') or '')
        linkedid = str(event.get('Linkedid') or uniqueid)
        if not uniqueid or not name:
            return []
        kind = channel_kind(name)
        self._channels[uniqueid] = {'name': name, 'linkedid': linkedid, 'kind': kind}
        call = self.calls.get(linkedid)
        created = call is None
        if created:
            call = _new_call(linkedid, self._stamp_now())
            self.calls[linkedid] = call
        exten = str(event.get('Exten') or '').strip()
        if uniqueid == linkedid:
            # Первый канал звонка решает направление.
            call['channel'] = name
            if kind == KIND_TRUNK:
                call['direction'] = DIRECTION_IN
                call['phone'] = touches_mod.norm_phone(event.get('CallerIDNum'))
                call['did'] = exten if exten.isdigit() or exten.startswith('+') else ''
            elif kind == KIND_EXT:
                call['agent_channel'] = name
                call['ext'] = ext_of_channel(name)
                prefix, phone = parse_dial(exten)
                if phone:
                    call['direction'] = DIRECTION_OUT
                    call['dial_prefix'], call['phone'] = prefix, phone
                elif _EXT_RE.match(exten):
                    call['direction'] = DIRECTION_INTERNAL
                    call['did'] = exten
                # Пустой Exten — канал поднят Originate: направление скажет DialBegin.
            elif kind == KIND_LOCAL:
                local_exten, context = _local_parts(name)
                if context == 'ext-to-queue' and _QUEUE_RE.match(local_exten):
                    call['direction'] = DIRECTION_AUTODIAL
                    call['queue'] = local_exten
            return [call]
        if kind == KIND_EXT:
            # Телефон оператора в чужом звонке — ему звонят (очередь или прямой вызов).
            ext = ext_of_channel(name)
            if call['state'] != 'ended' and ext != call['ext']:
                self._ring(call, ext, self._stamp_now())
                return [call]
        return [call] if created else []

    def _on_newstate(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        name = str(event.get('Channel') or '')
        state = _int(event.get('ChannelState'))
        kind = channel_kind(name)
        if state == 5 and kind == KIND_EXT:   # Ringing — телефон оператора звонит
            ext = ext_of_channel(name)
            if ext and ext != call['ext'] and ext not in call['ringing']:
                self._ring(call, ext, self._stamp_now())
                return [call]
        if state == 5 and kind == KIND_TRUNK and call['direction'] in (DIRECTION_OUT, DIRECTION_AUTODIAL) \
                and call['state'] in ('new', 'ringing') and not call['ringing_at']:
            call['ringing_at'] = self._stamp_now()
            call['state'] = 'ringing'
            return [call]
        return []

    def _on_dialbegin(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        dest = str(event.get('DestChannel') or '')
        dest_kind = channel_kind(dest)
        changed = False
        if dest_kind == KIND_TRUNK:
            # Набор в сторону клиента: исходящий оператора или заявка автообзвона.
            if not call['direction'] or call['direction'] == DIRECTION_INTERNAL:
                call['direction'] = DIRECTION_OUT
            # Набор целиком (`3333*+77…`) станция показывает в DestCallerIDNum транка (а у
            # заявки автообзвона — в Exten); DialString — `+77…@<транк>` без префикса.
            prefix, phone = parse_dial(event.get('DestCallerIDNum'))
            if not phone:
                prefix, phone = parse_dial(event.get('Exten'))
            if not phone:
                phone = touches_mod.norm_phone(event.get('DestCallerIDNum')) \
                    or touches_mod.norm_phone(str(event.get('DialString') or '').split('@')[0])
            call['phone'] = call['phone'] or phone
            call['dial_prefix'] = call['dial_prefix'] or prefix
            if call['state'] in ('new',):
                call['state'] = 'ringing'
                call['ringing_at'] = call['ringing_at'] or self._stamp_now()
            changed = True
        elif dest_kind in (KIND_EXT, KIND_LOCAL):
            ext = ext_of_channel(dest) if dest_kind == KIND_EXT else ext_of_interface(dest)
            if ext and ext != call['ext']:
                self._ring(call, ext, self._stamp_now())
                changed = True
        return [call] if changed else []

    def _on_dialend(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        dest = str(event.get('DestChannel') or '')
        dest_kind = channel_kind(dest)
        status = str(event.get('DialStatus') or '').upper()
        if dest_kind == KIND_EXT:
            ext = ext_of_channel(dest)
            if status == 'ANSWER':
                self._answered(call, ext, dest)
            else:
                self._unring(call, ext)
            return [call]
        if dest_kind == KIND_TRUNK:
            if status == 'ANSWER':
                self._answered(call)
            else:
                call['dial_status'] = status[:16]
            return [call]
        if dest_kind == KIND_LOCAL and status != 'ANSWER':
            # Плечо очереди к оператору сняли (ответил другой, таймаут) — он больше не звонит.
            self._unring(call, ext_of_interface(dest))
            return [call]
        return []

    def _on_bridgeenter(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        name = str(event.get('Channel') or '')
        kind = channel_kind(name)
        if kind == KIND_EXT:
            ext = ext_of_channel(name)
            if call['direction'] in (DIRECTION_OUT, DIRECTION_INTERNAL) and name == call['channel']:
                # Оператор-инициатор вошёл в мост — клиент ответил.
                self._answered(call)
            else:
                self._answered(call, ext, name)
            return [call]
        if kind == KIND_TRUNK and call['direction'] == DIRECTION_IN and call['ext']:
            self._answered(call)
            return [call]
        return []

    def _on_queuecallerjoin(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        call['queue'] = str(event.get('Queue') or '')[:8]
        call['queued_at'] = call['queued_at'] or self._stamp_now()
        if not call['answered_at']:
            call['state'] = 'ringing' if call['ringing'] else 'queued'
        if call['direction'] == DIRECTION_AUTODIAL:
            # Клиент автообзвона ответил и встал в очередь к операторам.
            call['answered_at'] = call['answered_at'] or call['queued_at']
        return [call]

    def _on_queuecallerleave(self, event):
        return []

    def _on_queuecallerabandon(self, event):
        call = self._call_of(event, create=False)
        if call is None:
            return []
        call['dial_status'] = 'ABANDON'
        return [call]

    def _on_agentcalled(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        ext = ext_of_interface(event.get('Interface')) or ext_of_channel(event.get('DestChannel'))
        if not ext:
            return []
        self._ring(call, ext, self._stamp_now())
        return [call]

    def _on_agentringnoanswer(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        ext = ext_of_interface(event.get('Interface')) or ext_of_channel(event.get('DestChannel'))
        if ext not in call['ringing']:
            return []
        self._unring(call, ext)
        return [call]

    def _on_agentconnect(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        ext = ext_of_interface(event.get('Interface')) or ext_of_channel(event.get('DestChannel'))
        self._answered(call, ext)
        wait = _int(event.get('HoldTime'))
        if wait is not None:
            call['wait_seconds'] = wait
        return [call]

    def _on_agentcomplete(self, event):
        call = self._call_of(event, create=False)
        if call is None:
            return []
        reason = str(event.get('Reason') or '').lower()
        side = {'caller': 'client', 'agent': 'operator'}.get(reason, '')
        if side and not call['hangup_side']:
            call['hangup_side'] = side
            return [call]
        return []

    def _on_hanguprequest(self, event):
        """Кто положил трубку: запрос отбоя приходит на канал стороны, которая отбилась сама."""
        call = self._call_of(event, create=False)
        if call is None or call['state'] != 'talking' or call['hangup_side']:
            return []
        name = str(event.get('Channel') or '')
        kind = channel_kind(name)
        if kind == KIND_EXT and name in (call['agent_channel'], call['channel']):
            call['hangup_side'] = 'operator'
        elif kind == KIND_TRUNK:
            call['hangup_side'] = 'client'
        else:
            return []
        return [call]

    def _on_hangup(self, event):
        uniqueid = str(event.get('Uniqueid') or '')
        channel = self._channels.pop(uniqueid, None)
        call = self._call_of(event, create=False)
        if call is None:
            return []
        name = str(event.get('Channel') or (channel or {}).get('name') or '')
        kind = channel_kind(name)
        changed = False
        if kind == KIND_EXT and call['state'] != 'ended':
            ext = ext_of_channel(name)
            if ext in call['ringing']:
                call['ringing'].remove(ext)
                changed = True
        remaining = self._channels_of(call['linkedid'])
        if call['state'] != 'ended' and (uniqueid == call['linkedid'] or not remaining):
            call['state'] = 'ended'
            call['ended_at'] = self._stamp_now()
            call['ringing'] = []
            cause = _int(event.get('Cause'))
            if cause is not None:
                call['hangup_cause'] = cause
            changed = True
        elif uniqueid == call['linkedid'] or not remaining:
            # Повторный отбой уже законченного звонка (дошли хвосты каналов) — причина точнее.
            cause = _int(event.get('Cause'))
            if cause is not None and call['hangup_cause'] in (None, 0):
                call['hangup_cause'] = cause
                changed = True
        return [call] if changed else []

    def _on_originateresponse(self, event):
        action_id = str(event.get('ActionID') or '')
        command_id = self._pending_originates.pop(action_id, None)
        if command_id is None:
            return []
        uniqueid = str(event.get('Uniqueid') or '')
        if str(event.get('Response') or '') != 'Success' or not uniqueid or uniqueid == '<null>':
            return []
        call = self.calls.get(uniqueid)
        if call is None:
            # Канал ещё не объявлен (Newchannel придёт следом) — заведём пустой, Newchannel дополнит.
            call = _new_call(uniqueid, self._stamp_now())
            self.calls[uniqueid] = call
        call['origin'] = 'portal'
        call['command_id'] = command_id
        return [call]
