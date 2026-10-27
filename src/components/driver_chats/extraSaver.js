/* Очередь сохранения добавок во вкладке «Настройки» раздела «Чаты водителей».
 *
 * Степпер жмут очередью («+ + +»), а отвечает сервер не мгновенно. Правила:
 *
 * * по человеку летит ОДИН запрос: параллельные могли бы прийти в обратном
 *   порядке — на экране +3, в базе +2;
 * * уходит ИТОГ, набранный к моменту отправки, а не «+1»: повтор ничего не
 *   удваивает, а три быстрых нажатия стоят двух запросов, а не трёх;
 * * вместе с итогом уходит `expected` — сколько было, по мнению экрана.
 *   Список мог устареть, пока был открыт, и «+» по старому нулю снял бы чужие
 *   «+3»; сервер на такое отвечает 409 и настоящим числом;
 * * очередь живёт в модуле, а не в экране: ушёл с вкладки посреди сохранения и
 *   вернулся — новые нажатия встанут за летящим запросом, а не обгонят его;
 * * серия нажатий — один запрос: первый уходит через `settleMs` после
 *   последнего нажатия. Каждое изменение пишется в журнал раздела, и «+» шесть
 *   раз подряд давали шесть строк «+1, было 0», «+2, было +1»… про одно
 *   решение. Экран меняется сразу — ждёт только сервер.
 *
 * Без React и без fetch — их подставляет экран (configure), поэтому правила
 * проверяет node --test (tests/driver_chats_settings.test.mjs).
 */

const ROW_FIELDS = ['extra_today', 'granted_by_name', 'granted_at'];

const pickRow = (source) => {
    const row = {};
    ROW_FIELDS.forEach((field) => { row[field] = source?.[field] ?? null; });
    row.extra_today = Number.isInteger(row.extra_today) ? row.extra_today : 0;
    if (Number.isInteger(source?.used_today)) row.used_today = source.used_today;
    return row;
};

export const createExtraSaver = ({ settleMs = 0 } = {}) => {
    const wanted = new Map();
    const confirmed = new Map();
    const running = new Set();
    const timers = new Map();
    let send = null;
    let listener = null;

    const emit = (event) => { if (listener) listener(event); };

    const run = async (userId) => {
        running.add(userId);
        try {
            for (;;) {
                const extra = wanted.get(userId);
                const expected = confirmed.get(userId)?.extra_today ?? 0;
                let answer;
                try {
                    answer = await send(userId, extra, expected);
                } catch {
                    wanted.delete(userId);
                    emit({ type: 'failed', userId, row: confirmed.get(userId) || null, message: '' });
                    return;
                }
                const { status, payload } = answer || {};
                if (status === 200 || status === 409) {
                    const row = pickRow(payload);
                    confirmed.set(userId, row);
                    if (status === 409) {
                        wanted.delete(userId);
                        emit({ type: 'conflict', userId, row, message: payload?.error || '' });
                        return;
                    }
                    if (wanted.get(userId) === extra) {
                        wanted.delete(userId);
                        emit({ type: 'saved', userId, row });
                        return;
                    }
                    // Пока летел запрос, нажали ещё — отправляем набранное.
                    continue;
                }
                wanted.delete(userId);
                emit({ type: 'failed', userId, row: confirmed.get(userId) || null,
                       message: payload?.error || '' });
                return;
            }
        } finally {
            running.delete(userId);
        }
    };

    return {
        /* send(userId, extra, expected) -> Promise<{status, payload}>; listener
           получает события 'saved' | 'conflict' | 'failed' с подтверждённой
           строкой. Экран подставляет себя при каждом показе. */
        configure({ send: nextSend, listener: nextListener }) {
            if (nextSend) send = nextSend;
            listener = nextListener || null;
        },

        /* Свежий список с сервера. Подтверждённое заменяется им — кроме людей,
           по которым ещё летит запрос, и строк, которые сервер на наш PUT уже
           вернул новее: GET мог прочитать базу раньше, чем PUT её поменял. */
        seed(people) {
            (people || []).forEach((person) => {
                const id = person.user_id;
                if (running.has(id)) return;
                const fresh = pickRow(person);
                const known = confirmed.get(id);
                if (known?.granted_at && (!fresh.granted_at || fresh.granted_at < known.granted_at)) {
                    confirmed.set(id, { ...known, used_today: fresh.used_today ?? known.used_today });
                    return;
                }
                confirmed.set(id, fresh);
            });
        },

        /* Что показывать в строке: набранное поверх подтверждённого. */
        view(person) {
            const id = person.user_id;
            const known = confirmed.get(id);
            const row = known ? { ...person, ...known } : person;
            return wanted.has(id) ? { ...row, extra_today: wanted.get(id) } : row;
        },

        set(userId, extra) {
            wanted.set(userId, extra);
            // Летит запрос — набранное уйдёт следом за ним, ждать нечего.
            if (running.has(userId)) return;
            if (!settleMs) {
                run(userId);
                return;
            }
            clearTimeout(timers.get(userId));
            timers.set(userId, setTimeout(() => {
                timers.delete(userId);
                if (!running.has(userId) && wanted.has(userId)) run(userId);
            }, settleMs));
        },

        pending: (userId) => wanted.get(userId),
    };
};

/* Одна очередь на страницу: переживает уход с вкладки и возврат. 400 мс —
   пауза между нажатиями серии у человека заметно короче, а «сохранилось ли»
   за это время никто не успевает спросить: число на экране уже новое. */
export const extraSaver = createExtraSaver({ settleMs: 400 });
