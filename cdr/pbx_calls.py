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

* **Входящий**: первый канал — транк `PJSIP/<имя линии>-<hex>` (контекст `from-pstn`,
  `CallerIDNum` — номер клиента, `Exten` — набранный номер парка, DID). Дальше
  `QueueCallerJoin` (очередь), `AgentCalled` — очередь предлагает звонок оператору
  (`Interface: Local/6726@from-queue/n`); телефон при этом звонит, только если у звонка
  появился его канал `PJSIP/6726-<hex>` (занятому очередь «предлагает» каждые 2 с, а Local
  сразу уходит в BUSY, телефон не набирается). `AgentConnect` (снял трубку: `MemberName`,
  `HoldTime` — ожидание), `BridgeEnter` канала оператора, `Hangup`.
* **Исходящий**: первый канал — оператор `PJSIP/6726-<hex>` в `from-internal`, `Exten` —
  набор `3333*+77XXXXXXXXX`: четыре цифры префикса выбирают исходящий маршрут (линию парка),
  после звёздочки номер. `DialBegin` → транк (набор целиком — в `DestCallerIDNum`),
  `DialEnd` с `DialStatus: ANSWER` — клиент снял трубку, `BridgeEnter`, `Hangup`.
* **Заявка автообзвона**: первый канал `Local/<очередь>@ext-to-queue-…;1`, вторая половина
  (`;2`) встаёт в очередь с номером клиента в `CallerIDNum` и ЖДЁТ СВОБОДНОГО ОПЕРАТОРА
  (у занятого телефон «BUSY» каждые 5 с — заявка висит и ночью до ~15 мин). Нашёлся —
  `AgentConnect`, телефон оператора снимает трубку сам, и только ПОТОМ `;1` набирает клиента
  через транк. Ответ клиента — `DialEnd ANSWER`/`BridgeEnter` транка; до него оператор
  слушает гудки. Сверено с CDR 22.10.2026: 31 заявка из 31 без ответа клиента, а первая
  версия этого модуля по `AgentConnect` писала им «ответил».
* **Внутренний** (оператор оператору): первый канал оператора, `Exten` — внутренний номер;
  ответивший коллега — `peer_ext`, оба в разговоре.

Кто положил трубку — `AgentComplete.Reason` (`caller`/`agent`), а без очереди —
`HangupRequest`: его получает канал той стороны, которая отбилась сама (BYE от телефона или
от оператора связи), второму каналу мост шлёт `SoftHangupRequest`. Порядок самих `Hangup`
ничего не значит: когда трубку кладёт оператор, первым отбивается транк. Сверено с журналом
очередей и CEL на 187 разговорах из 187 (22.10.2026, 12:16–15:20).

Границы, проведённые осознанно
------------------------------
* Звонок кончается, когда отбился его первый канал ИЛИ не осталось ни одного канала. Кроме
  первого Local (заявка автообзвона): после ответа клиента станция сводит пару Local накоротко,
  Local-каналы отбиваются, а разговор продолжается — такой звонок кончается с последним каналом.
* Восстановление из списка каналов при входе в AMI (`seed`) помечает звонок `partial`: точных
  моментов (вход в очередь, ответ) список не знает, и портал не даёт им затереть известное.
* Канал, известный до запроса списка и в списке отсутствующий, — закончился без события
  (`reconcile`): коннектор сверяется со станцией раз в несколько минут.
* Сторона отбоя ставится только у состоявшегося разговора: отбой телефона оператора, который
  просто звонил (очередь сняла вызов, причина 26 «ответили в другом месте»), — не его решение.
* У заявки автообзвона операторам не «звонит»: их телефоны снимают трубку сами, а занятый
  получает попытку раз в пять секунд — это не звонок человеку, а шум.
* Переводы и конференции не разбираются: такой звонок остаётся за тем, кто его принял.
* Изменение без нового смысла (то же состояние, тот же оператор) наружу не выходит: `feed`
  сравнивает отпечаток звонка до и после события. Иначе каждая попытка дозвона до занятого
  оператора была бы запросом к порталу и строкой на табло.
* Законченные звонки держатся KEEP_ENDED_SECONDS: портал успевает их получить и показать
  «только что закончился»; потом они уходят, а правдой становится CDR.
"""

import re
from datetime import datetime

from cdr import touches as touches_mod

__all__ = ['CallTracker', 'WANTED_EVENTS', 'EVENT_MASK', 'STATES', 'parse_dial', 'dial_string',
           'ext_of_channel', 'ext_of_interface', 'channel_kind', 'CALL_FIELDS']

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
DIRECTIONS = ('', DIRECTION_IN, DIRECTION_OUT, DIRECTION_INTERNAL, DIRECTION_AUTODIAL)

# Поля записи звонка — в этом порядке их знает и портал (pbx/queries.py).
CALL_FIELDS = ('linkedid', 'direction', 'phone', 'did', 'queue', 'ext', 'peer_ext', 'ringing',
               'state', 'started_at', 'queued_at', 'ringing_at', 'answered_at', 'ended_at',
               'wait_seconds', 'hangup_cause', 'hangup_side', 'dial_prefix', 'dial_status',
               'channel', 'agent_channel', 'recording_file', 'origin', 'command_id', 'seq')

# Сколько держать законченный звонок после отбоя.
KEEP_ENDED_SECONDS = 300

# Канал оператора: `PJSIP/6726-00001a98`. Внутренние номера на станции трёх–пятизначные; транк —
# `PJSIP/<имя линии>-<hex>`, и имя линии содержит буквы или знаки (`+77…_jana`, `yVnnduj2_70…`).
_EXT_CHANNEL_RE = re.compile(r'^PJSIP/(\d{3,5})-[0-9a-fA-F]+$')
_PJSIP_RE = re.compile(r'^PJSIP/')
# `Local/6726@from-queue-00001a36;1`, `Local/3016@ext-to-queue-00001a34;1`, `Local/6726@from-queue/n`.
_LOCAL_RE = re.compile(r'^Local/([^@/]+)@(.+?)(?:-[0-9a-fA-F]{6,})?(?:;[12])?(?:/n)?$')
# Набор исходящего: четыре цифры префикса маршрута, звёздочка, номер (с плюсом или без).
_DIAL_RE = re.compile(r'^(\d{4})\*(\+?\d{7,15})$')
EXT_RE = re.compile(r'^\d{3,5}$')
_EXT_RE = EXT_RE
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


def _duration_seconds(value):
    """`00:01:23` → 83; мусор → 0."""
    parts = str(value or '').split(':')
    try:
        parts = [int(part) for part in parts]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return max(0, seconds)


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
        'peer_ext': '',
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


def fingerprint(call):
    """Всё, что видно снаружи, кроме порядкового номера: по нему решается, изменился ли звонок."""
    return tuple(tuple(sorted(set(call[f]))) if f == 'ringing' else call[f]
                 for f in CALL_FIELDS if f != 'seq')


class CallTracker:
    """Состояние звонков станции. `feed(событие)` → список изменившихся звонков (копии).

    `now` — функция времени (datetime), подменяется в тестах. Время в записях — местное
    время машины, где работает коннектор (на шлюзе TZ=Asia/Almaty, как у CDR станции).
    """

    def __init__(self, now=None, keep_ended_seconds=KEEP_ENDED_SECONDS):
        self._now = now or datetime.now
        self.keep_ended_seconds = keep_ended_seconds
        self.calls = {}        # linkedid → звонок
        self._prints = {}      # linkedid → отпечаток, ушедший наружу последним
        self._channels = {}    # uniqueid → {'name', 'linkedid', 'kind'}
        self._seq = 0
        self._at = None
        # ActionID нашего Originate → id команды портала: OriginateResponse приходит с ним и
        # с Uniqueid нового канала, по которому звонок получает origin='portal'.
        self._pending_originates = {}

    # ── снаружи ──────────────────────────────────────────────────────────────

    def feed(self, event, at=None):
        """Одно событие AMI → список изменившихся звонков (снимки). Чужие и пустые события — [].

        `at` — момент события, если он известен точнее «сейчас»: так при входе на станцию
        каналы, живущие уже минуту, получают настоящее начало (CoreShowChannels.Duration)."""
        name = str(event.get('Event') or '')
        handler = getattr(self, '_on_' + name.lower(), None)
        if handler is None:
            return []
        self._at = at
        try:
            touched = handler(event) or []
        finally:
            self._at = None
        return self._emit(touched)

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
                self._prints.pop(linkedid, None)
        # Каналы законченных звонков уже сняты в Hangup; здесь — на случай потерянного события.
        for uniqueid, channel in list(self._channels.items()):
            if channel['linkedid'] in gone:
                del self._channels[uniqueid]
        return gone

    def expect_originate(self, action_id, command_id):
        """Запомнить наш Originate: ответ придёт событием OriginateResponse с этим ActionID."""
        self._pending_originates[str(action_id)] = command_id

    def forget_originate(self, action_id):
        """Станция отказала сразу — OriginateResponse не будет, ждать нечего."""
        self._pending_originates.pop(str(action_id), None)

    def seed(self, items, now=None, skip_uniqueids=()):
        """Состояние из списка каналов станции (CoreShowChannels) — при входе в AMI.

        Список — это каналы, а не события: очереди, набора и ответа в нём нет. Поэтому по
        каналам восстанавливается то, что из них следует: клиент на транке в контексте очереди
        без моста — ждёт в очереди; канал оператора с транком в том же звонке — исходящий с
        набором из CallerIDNum транка; каналы в мосту — разговор. Время начала — по Duration.
        Точные моменты (вход в очередь, ответ) из списка не узнать, поэтому такие звонки
        помечены `partial`: портал не даст им затереть то, что уже знает о звонке.

        skip_uniqueids — каналы, отбой которых пришёл, пока список выдавался: их не воскрешаем.
        Каналы, уже известные по живым событиям, не перезаписываются. Возвращает изменения."""
        from datetime import timedelta
        moment = now or self._now()
        skip = {str(uid) for uid in skip_uniqueids or ()}
        rows = [dict(item) for item in items or []
                if str(item.get('Uniqueid') or '') and str(item.get('Uniqueid')) not in skip]
        rows.sort(key=lambda item: (item.get('Uniqueid') != item.get('Linkedid'), str(item.get('Uniqueid'))))
        starts = {}
        touched = []
        for item in rows:
            uniqueid = str(item['Uniqueid'])
            if uniqueid in self._channels:
                continue
            started = moment - timedelta(seconds=_duration_seconds(item.get('Duration')))
            starts[uniqueid] = started
            self._at = started
            try:
                touched += self._on_newchannel(dict(item, Event='Newchannel')) or []
            finally:
                self._at = None
        groups = {}
        for item in rows:
            groups.setdefault(str(item.get('Linkedid') or item['Uniqueid']), []).append(item)
        for linkedid, group in groups.items():
            call = self.calls.get(linkedid)
            if call is None:
                continue
            call['partial'] = True
            first = next((item for item in group if str(item['Uniqueid']) == linkedid), None)
            trunks = [item for item in group if channel_kind(item.get('Channel')) == KIND_TRUNK
                      and str(item['Uniqueid']) != linkedid]
            if first is not None and channel_kind(first.get('Channel')) in (KIND_EXT, KIND_LOCAL) \
                    and trunks and not call['phone']:
                trunk = trunks[0]
                self._at = starts.get(str(trunk['Uniqueid']))
                try:
                    touched += self._on_dialbegin({
                        'Linkedid': linkedid, 'Uniqueid': first['Uniqueid'], 'Exten': first.get('Exten'),
                        'DestChannel': trunk.get('Channel'),
                        'DestCallerIDNum': trunk.get('CallerIDNum') or trunk.get('Exten')}) or []
                finally:
                    self._at = None
            if first is not None and channel_kind(first.get('Channel')) == KIND_TRUNK \
                    and first.get('Context') == 'ext-queues' and not first.get('BridgeId') \
                    and _QUEUE_RE.match(str(first.get('Exten') or '')):
                self._at = starts.get(linkedid)
                try:
                    touched += self._on_queuecallerjoin({'Linkedid': linkedid, 'Uniqueid': linkedid,
                                                         'Queue': first.get('Exten')}) or []
                finally:
                    self._at = None
            for item in group:
                if item.get('BridgeId') and channel_kind(item.get('Channel')) in (KIND_EXT, KIND_TRUNK):
                    touched += self._on_bridgeenter(dict(item, Event='BridgeEnter')) or []
        return self._emit(touched)

    def reconcile(self, live_uniqueids, known_before):
        """Сверка со списком каналов станции: канал, известный ДО запроса списка и в списке
        отсутствующий, — закончился, а его отбой потерялся (переподключение, обрыв). Ему
        проигрывается Hangup — иначе звонок висел бы «идущим» до перезапуска коннектора, а
        каждый снимок подтверждал бы его порталу. Возвращает изменения."""
        live = {str(uid) for uid in live_uniqueids or ()}
        touched = []
        for uniqueid in [uid for uid in known_before if uid not in live]:
            channel = self._channels.get(uniqueid)
            if channel is None:
                continue
            touched += self._on_hangup({'Event': 'Hangup', 'Uniqueid': uniqueid, 'Linkedid': channel['linkedid'],
                                        'Channel': channel['name']}) or []
        return self._emit(touched)

    def channel_ids(self):
        return set(self._channels)

    def set_recording(self, linkedid, filename):
        call = self.calls.get(linkedid)
        if not call or not filename or call['recording_file'] == filename:
            return []
        call['recording_file'] = str(filename)[:200]
        return self._emit([call])

    @staticmethod
    def snapshot_of(call):
        out = dict(call)
        out['ringing'] = sorted(set(call['ringing']))
        return out

    # ── внутреннее ───────────────────────────────────────────────────────────

    def _emit(self, touched):
        """Звонки, у которых изменилось видимое снаружи, — с новым порядковым номером."""
        out, seen = [], set()
        for call in touched:
            linkedid = call['linkedid']
            if linkedid in seen:
                continue
            seen.add(linkedid)
            print_ = fingerprint(call)
            if self._prints.get(linkedid) == print_:
                continue
            self._prints[linkedid] = print_
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
    def _offered(call):
        """Звонок, у которого «звонит» оператору человек: входящий и внутренний. Заявке
        автообзвона телефоны снимают трубку сами, исходящий звонит клиенту, а не оператору."""
        return call['direction'] in (DIRECTION_IN, DIRECTION_INTERNAL, '')

    def _ring(self, call, ext):
        if not ext or ext in (call['ext'], call['peer_ext']) or not self._offered(call):
            return False
        if ext not in call['ringing']:
            call['ringing'].append(ext)
        if not call['ringing_at']:
            call['ringing_at'] = self._stamp_now()
        if call['state'] in ('new', 'queued', 'ringing') and not call['answered_at']:
            call['state'] = 'ringing'
        return True

    @staticmethod
    def _unring(call, ext):
        if ext in call['ringing']:
            call['ringing'].remove(ext)
        if call['state'] == 'ringing' and not call['ringing'] and not call['answered_at'] \
                and call['direction'] in (DIRECTION_IN, DIRECTION_INTERNAL, ''):
            # Телефоны замолчали, а звонок жив: клиент ждёт в очереди или снова слушает IVR.
            call['state'] = 'queued' if call['queue'] else 'new'

    def _answered(self, call, ext='', agent_channel=''):
        """Разговор начался: ext — кто снял трубку (у входящего), пусто — у исходящего клиент
        ответил оператору-инициатору."""
        now_stamp = self._stamp_now()
        if ext:
            if call['direction'] == DIRECTION_INTERNAL and call['ext'] and ext != call['ext']:
                call['peer_ext'] = ext
            else:
                call['ext'] = ext
        if agent_channel and not (call['direction'] == DIRECTION_INTERNAL and call['agent_channel']):
            call['agent_channel'] = agent_channel
        if not call['answered_at']:
            call['answered_at'] = now_stamp
        call['ringing'] = []
        if call['state'] != 'ended':
            call['state'] = 'talking'

    def _reserve(self, call, ext, agent_channel=''):
        """Заявка автообзвона нашла оператора: его телефон уже на линии, клиента сейчас наберут.
        Это «соединение», а не разговор: ответа клиента ещё нет."""
        if ext:
            call['ext'] = ext
        if agent_channel:
            call['agent_channel'] = agent_channel
        call['ringing'] = []
        if call['state'] in ('new', 'queued') and not call['answered_at']:
            call['state'] = 'ringing'

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
                if _QUEUE_RE.match(exten):
                    # Снимок CoreShowChannels при входе: клиент уже в очереди, и Exten транка —
                    # её номер (контекст ext-queues), а не DID. DID четырёхзначным не бывает.
                    call['queue'] = exten
                else:
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
        if kind == KIND_EXT and call['state'] != 'ended':
            # Телефон оператора в чужом звонке — ему звонят (очередь или прямой вызов).
            self._ring(call, ext_of_channel(name))
        return [call]

    def _on_newstate(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        name = str(event.get('Channel') or '')
        state = _int(event.get('ChannelState'))
        kind = channel_kind(name)
        if state == 5 and kind == KIND_EXT:   # Ringing — телефон оператора звонит
            self._ring(call, ext_of_channel(name))
            return [call]
        if state == 5 and kind == KIND_TRUNK and call['direction'] in (DIRECTION_OUT, DIRECTION_AUTODIAL) \
                and not call['answered_at'] and not call['ringing_at']:
            call['ringing_at'] = self._stamp_now()
            if call['state'] in ('new', 'queued'):
                call['state'] = 'ringing'
            return [call]
        return []

    def _on_dialbegin(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        dest = str(event.get('DestChannel') or '')
        dest_kind = channel_kind(dest)
        if dest_kind == KIND_TRUNK:
            # Набор в сторону клиента: исходящий оператора или заявка автообзвона.
            if call['direction'] in ('', DIRECTION_INTERNAL):
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
            if not call['answered_at']:
                call['ringing_at'] = call['ringing_at'] or self._stamp_now()
                if call['state'] in ('new', 'queued'):
                    call['state'] = 'ringing'
            return [call]
        if dest_kind == KIND_EXT:
            # Звонит именно телефон. Набор плеча очереди `Local/<ext>@from-queue` — ещё не звонок:
            # к занятому оператору очередь пробует каждые 2 с, и Local сразу уходит в BUSY, так и
            # не набрав телефон (снято 22.10.2026) — это не «звонит» и не изменение звонка.
            self._ring(call, ext_of_channel(dest))
            return [call]
        return []

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
                if call['direction'] == DIRECTION_AUTODIAL:
                    self._reserve(call, ext, dest)
                else:
                    self._answered(call, ext, dest)
            else:
                self._unring(call, ext)
            return [call]
        if dest_kind == KIND_TRUNK:
            if status == 'ANSWER':
                self._answered(call)
            elif status:
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
            if call['direction'] == DIRECTION_AUTODIAL:
                # Телефон оператора снял трубку сам — клиента ещё только наберут.
                self._reserve(call, ext, name)
            elif call['direction'] in (DIRECTION_OUT, DIRECTION_INTERNAL) and name == call['channel']:
                # Оператор-инициатор вошёл в мост — клиент (или коллега) ответил.
                self._answered(call)
            else:
                self._answered(call, ext, name)
            return [call]
        if kind == KIND_TRUNK:
            if call['direction'] in (DIRECTION_OUT, DIRECTION_AUTODIAL):
                self._answered(call)       # клиент ответил
                return [call]
            if call['direction'] == DIRECTION_IN and call['ext']:
                self._answered(call)
                return [call]
        return []

    def _on_queuecallerjoin(self, event):
        call = self._call_of(event, create=False)
        if call is None or call['state'] == 'ended':
            return []
        call['queue'] = str(event.get('Queue') or '')[:8]
        call['queued_at'] = call['queued_at'] or self._stamp_now()
        if call['direction'] == DIRECTION_AUTODIAL and not call['phone']:
            # Заявка встаёт в очередь с номером клиента: его наберут, когда найдётся оператор.
            call['phone'] = touches_mod.norm_phone(event.get('CallerIDNum'))
        if not call['answered_at'] and call['state'] in ('new', 'queued', 'ringing'):
            call['state'] = 'ringing' if call['ringing'] else 'queued'
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
        """Очередь предлагает звонок оператору — это ещё не звонок телефона: занятому очередь
        «предлагает» каждые 2 с, и телефон при этом не набирается вовсе. «Звонит» ставится по
        каналу самого телефона (Newchannel/Newstate PJSIP/<ext>, DialBegin на него)."""
        return []

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
        wait = _int(event.get('HoldTime'))
        if call['direction'] == DIRECTION_AUTODIAL:
            self._reserve(call, ext)
            return [call]
        self._answered(call, ext)
        if wait is not None:
            call['wait_seconds'] = wait
        return [call]

    def _on_agentcomplete(self, event):
        call = self._call_of(event, create=False)
        if call is None or not call['answered_at']:
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
        operator_channels = {call['agent_channel'], call['channel']} - {''}
        if kind == KIND_EXT and (name in operator_channels
                                 or ext_of_channel(name) in (call['ext'], call['peer_ext'])):
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
        if channel_kind(name) == KIND_EXT and call['state'] != 'ended':
            ext = ext_of_channel(name)
            if ext in call['ringing']:
                self._unring(call, ext)
        remaining = self._channels_of(call['linkedid'])
        cause = _int(event.get('Cause'))
        first_gone = uniqueid == call['linkedid']
        if first_gone and channel_kind(call['channel']) == KIND_LOCAL \
                and any(c['kind'] in (KIND_EXT, KIND_TRUNK) for c in remaining):
            # Первый канал заявки автообзвона — Local. Когда клиент ответил, станция сводит пару
            # Local накоротко (LocalOptimization: Yes у ext-to-queue): Local-каналы отбиваются, а
            # клиент на транке и оператор продолжают разговор в одном мосту с тем же linkedid.
            first_gone = False
        if call['state'] != 'ended' and (first_gone or not remaining):
            call['state'] = 'ended'
            call['ended_at'] = self._stamp_now()
            call['ringing'] = []
            if cause is not None:
                call['hangup_cause'] = cause
        elif call['state'] == 'ended' and cause is not None and call['hangup_cause'] in (None, 0) \
                and (first_gone or not remaining):
            # Повторный отбой уже законченного звонка (дошли хвосты каналов) — причина точнее.
            call['hangup_cause'] = cause
        return [call]

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
