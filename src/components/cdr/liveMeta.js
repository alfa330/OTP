/* Подписи живых звонков станции (панель «Сейчас на линии» в «Касаниях»).
 *
 * Чистые функции без React: их проверяет tests/pbx_live_meta.test.mjs. Форма звонка — та же,
 * что отдаёт /api/pbx/live (см. cdr/pbx_calls.py на сервере): state, direction, started_at,
 * answered_at, ended_at — местное время станции строкой «YYYY-MM-DDTHH:MM:SS».
 */

export const STATE_LABEL = {
    new: 'Соединение',
    queued: 'В очереди',
    ringing: 'Звонит',
    talking: 'Разговор',
    ended: 'Завершён',
};

const STATE_TONE = { new: 'slate', queued: 'amber', ringing: 'blue', talking: 'green', ended: 'slate' };

export const stateTone = (state) => STATE_TONE[state] || 'slate';

export const DIRECTION_LABEL = {
    in: 'Входящий',
    out: 'Исходящий',
    internal: 'Внутренний',
    autodial: 'Автообзвон',
};

/* «YYYY-MM-DDTHH:MM:SS» → секунды от эпохи КАК БУДТО это UTC. Обе отметки в одной шкале
   (время станции), так что разница честная, а пояс браузера не участвует. */
export const wallSeconds = (value) => {
    const text = String(value || '');
    if (text.length < 19) return null;
    const stamp = Date.parse(`${text.slice(0, 19)}Z`);
    return Number.isNaN(stamp) ? null : Math.floor(stamp / 1000);
};

export const elapsedSeconds = (fromIso, nowIso) => {
    const from = wallSeconds(fromIso);
    const now = wallSeconds(nowIso);
    if (from === null || now === null) return null;
    return Math.max(0, now - from);
};

/* 83 → «1:23», 3725 → «1:02:05»; пусто/мусор → «». */
export const mmss = (seconds) => {
    if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds)) || Number(seconds) < 0) return '';
    const total = Math.floor(Number(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const two = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
};

/* Что тикает у звонка: разговор — с момента ответа, ожидание и звон — с начала звонка,
   законченный — сколько говорили (или пусто, если разговора не было). */
export const callClock = (call, nowIso) => {
    if (!call) return '';
    if (call.state === 'ended') {
        if (!call.answered_at || !call.ended_at) return '';
        return mmss(elapsedSeconds(call.answered_at, call.ended_at));
    }
    if (call.state === 'talking' && call.answered_at) return mmss(elapsedSeconds(call.answered_at, nowIso));
    return mmss(elapsedSeconds(call.started_at, nowIso));
};

/* Чем кончилось — одной короткой фразой. */
export const endedNote = (call) => {
    if (!call || call.state !== 'ended') return '';
    if (call.answered_at) {
        const side = call.hangup_side === 'client' ? 'положил клиент'
            : call.hangup_side === 'operator' ? 'положил оператор' : '';
        return side ? `Разговор · ${side}` : 'Разговор';
    }
    if (call.dial_status === 'ABANDON') return 'Клиент не дождался';
    if (call.dial_status === 'BUSY') return 'Занято';
    if (call.direction === 'out' || call.direction === 'autodial') return 'Не ответил';
    return 'Не приняли';
};

/* Вторая строка карточки: кто говорит / кому звонит / где ждёт. */
export const callSubtitle = (call) => {
    if (!call) return '';
    if (call.operator) return call.operator;
    if (call.ringing_operators?.length) return `звонит: ${call.ringing_operators.join(', ')}`;
    if (call.park) return call.park;
    if (call.queue) return `очередь ${call.queue}`;
    return DIRECTION_LABEL[call.direction] || '';
};

/* Пилюля состояния коннектора: онлайн — только когда он на связи И видит станцию. */
export const connectorStatus = (connector) => {
    if (!connector || !connector.last_seen_at) {
        return { tone: 'slate', label: 'коннектор не подключён', online: false };
    }
    if (!connector.connected) return { tone: 'amber', label: 'коннектор не на связи', online: false };
    if (!connector.ami_connected) return { tone: 'amber', label: 'станция недоступна', online: false };
    return { tone: 'green', label: 'на связи', online: true };
};

/* Активные выше законченных, внутри — свежие первыми. */
export const sortCalls = (calls) => [...(calls || [])].sort((a, b) => {
    const rank = (c) => (c.state === 'ended' ? 1 : 0);
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    return String(b.started_at || '').localeCompare(String(a.started_at || ''));
});

/* Линия для перезвона: по номеру линии звонка, иначе — последняя выбранная. */
export const pickLine = (lines, lineNumber, lastPrefix) => {
    const list = lines || [];
    if (lineNumber) {
        const hit = list.find((line) => line.number === lineNumber);
        if (hit) return hit.prefix;
    }
    if (lastPrefix && list.some((line) => line.prefix === lastPrefix)) return lastPrefix;
    return list.length === 1 ? list[0].prefix : '';
};
