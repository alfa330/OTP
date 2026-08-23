/* Лента событий интерфейса.
 *
 * ЗАЧЕМ. Разбор говорит стажёру «ты не посмотрел Ведомость, а ответ лежал
 * там» — и это единственное, за чем он вернётся во второй раз. Речь показывает,
 * что он ГОВОРИЛ; лента показывает, что он ДЕЛАЛ. Без неё такой фразы не
 * получится.
 *
 * Имена кодов — общий словарь с разбором на той стороне. Новое имя заводится
 * правкой этой таблицы, а не выдумкой на месте: разошедшиеся словари означают,
 * что разбор молча перестанет видеть половину действий.
 *
 * Тренажёр обязан работать без сервера: нет отправки — лента просто копится в
 * памяти. Ошибки сети гасятся молча, как в useTrainerRun: учёт никогда не
 * мешает уроку.
 */

export const EVENT_CODES = [
    // Okapp
    'okt.login', 'okt.callcenter_in', 'okt.callcenter_out', 'okt.status',
    // звонок
    'call.incoming', 'call.answer', 'call.reject', 'call.hold', 'call.unhold',
    'call.transfer', 'call.end',
    // интерфейс
    'ui.tab', 'ui.search', 'ui.open_contractor', 'ui.open_tab', 'ui.filter',
    'ui.sort', 'ui.columns', 'ui.open_order', 'ui.action',
    // CRM
    'crm.save',
];

const KNOWN = new Set(EVENT_CODES);

/* Пачка уходит раз в пять секунд. Чаще незачем: ленту читают после попытки,
   а не в реальном времени. */
export const FLUSH_MS = 5000;

/* Против шума. Ввод в поле событием не считается вовсе, но подряд идущие
   одинаковые действия (открыл вкладку карточки, вернулся, открыл ту же) всё
   равно случаются. Схлопываем ПОВТОР того же кода с тем же payload в пределах
   секунды: лента должна читаться человеком, а не грепаться. */
const REPEAT_MS = 1000;

const same = (a, b) => a && b && a.code === b.code
    && JSON.stringify(a.payload) === JSON.stringify(b.payload);

/**
 * Создать ленту.
 *
 * send — необязательная отправка пачки наружу: `(items) => Promise|void`.
 * Нет отправки — лента живёт только в памяти, и это рабочий режим, а не
 * поломка: тренажёр должен проходиться и без сервера.
 */
export const createEventLog = ({ send = null, now = () => Date.now() } = {}) => {
    const items = [];
    let pending = [];
    let dropped = 0;
    let last = null;

    const emit = (code, payload = {}) => {
        if (!KNOWN.has(code)) {
            // Неизвестный код — ошибка автора, а не пользователя. Молча глотать
            // нельзя: разбор потом не поймёт, почему события нет.
            if (typeof console !== 'undefined' && console.warn) {
                console.warn(`[тренажёр] неизвестный код события: ${code}`);
            }
            return null;
        }
        const at = now();
        const item = { code, payload, at };
        if (same(item, last) && at - last.at < REPEAT_MS) {
            dropped += 1;
            return null;
        }
        last = item;
        items.push(item);
        pending.push(item);
        return item;
    };

    const flush = () => {
        if (!send || !pending.length) return Promise.resolve(false);
        const batch = pending;
        pending = [];
        try {
            return Promise.resolve(send(batch)).then(() => true).catch(() => false);
        } catch {
            return Promise.resolve(false);
        }
    };

    return {
        emit,
        flush,
        /** Вся лента попытки — для разбора, статистики и отладочной панели. */
        all: () => items.slice(),
        count: () => items.length,
        /** Сколько повторов схлопнули — видно в отладочной панели. */
        droppedCount: () => dropped,
    };
};

/** Человеческая расшифровка кода — для отладочной панели и отчёта. */
export const EVENT_TITLES = {
    'okt.login': 'вошёл в Okapp',
    'okt.callcenter_in': 'встал на линию',
    'okt.callcenter_out': 'ушёл с линии',
    'okt.status': 'поставил статус',
    'call.incoming': 'входящий звонок',
    'call.answer': 'ответил',
    'call.reject': 'отклонил',
    'call.hold': 'удержание',
    'call.unhold': 'снял с удержания',
    'call.transfer': 'перевёл',
    'call.end': 'звонок завершён',
    'ui.tab': 'сменил вкладку',
    'ui.search': 'искал',
    'ui.open_contractor': 'открыл карточку',
    'ui.open_tab': 'вкладка карточки',
    'ui.filter': 'фильтр',
    'ui.sort': 'сортировка',
    'ui.columns': 'колонки',
    'ui.open_order': 'открыл заказ',
    'ui.action': 'действие с данными',
    'crm.save': 'сохранил обращение',
};
