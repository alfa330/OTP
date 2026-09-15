/*
 * «Учет сотрудников» на телефоне — правила списка без React.
 *
 * Компьютер показывает таблицу, телефон — список людей и карточку. Кого
 * пропускает статус, поиск и порядок, решают те же функции, что у таблицы:
 * App.jsx передаёт их аргументами. Здесь только то, чего у таблицы нет вовсе:
 * какие метки статусов стоит показывать, как собрать группы «Мои операторы» и
 * направления, какие поля карточки пустые и когда статус нужен в строке.
 * Всё закреплено tests/employees_phone_list.test.mjs.
 */

const collator = (a, b) => String(a || '').localeCompare(String(b || ''), 'ru', { sensitivity: 'base' });

export const plural = (n, [one, few, many]) => {
    const abs = Math.abs(Number(n) || 0) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
};

export const countByStatus = (rows, tabs, isVisible) => {
    const counts = {};
    (tabs || []).forEach((tab) => { counts[tab.key] = 0; });
    (rows || []).forEach((row) => {
        (tabs || []).forEach((tab) => {
            if (isVisible(row?.status, tab.key)) counts[tab.key] += 1;
        });
    });
    return counts;
};

/* Метки статусов над списком. На компьютере их шесть всегда, и на телефоне
   половина из них — «Б/С 0», «Отпуск 0»: кнопки, которые никого не отберут.
   Остаются те, за которыми есть люди, и выбранная — иначе её нечем снять.
   «Работает» при равенстве с «Активными» — те же люди второй раз. Если выбрать
   не из чего, полосы нет вовсе. */
export const visibleStatusChips = (tabs, counts, activeKey) => {
    const chips = (tabs || []).filter((tab) => {
        if (tab.key === activeKey) return true;
        const count = counts[tab.key] || 0;
        if (count === 0) return false;
        if (tab.key === 'working' && count === (counts.active || 0)) return false;
        return true;
    });
    if (chips.length === 1 && chips[0].key === activeKey) return [];
    return chips;
};

export const filterEmployees = (rows, { statusTab, isVisible, query, matches }) => {
    const q = String(query || '').trim();
    return (rows || []).filter((row) => isVisible(row?.status, statusTab) && (!q || matches(row, q)));
};

/* «Операторы» у СВ и тренера: сначала свои, потом по направлениям — так же,
   как таблица (App.jsx, view === 'manage_operators'). Порядок «по
   направлению» переставляет сами группы, а внутри группы идёт по имени; любой
   другой работает внутри группы, а группы стоят по алфавиту. */
export const groupOperators = (rows, { isMine, directionOf, compare, sortField, sortDir }) => {
    const byDirection = sortField === 'direction';
    const sign = sortDir === 'desc' ? -1 : 1;
    const inGroup = byDirection ? (a, b) => collator(a?.name, b?.name) * sign : compare;
    const mine = [];
    const byDir = new Map();
    (rows || []).forEach((row) => {
        if (isMine(row)) {
            mine.push(row);
            return;
        }
        const name = directionOf(row);
        if (!byDir.has(name)) byDir.set(name, []);
        byDir.get(name).push(row);
    });
    const groups = [];
    if (mine.length) groups.push({ key: 'mine', title: 'Мои операторы', rows: [...mine].sort(inGroup) });
    [...byDir.keys()]
        .sort((a, b) => collator(a, b) * (byDirection ? sign : 1))
        .forEach((name) => groups.push({ key: `dir:${name}`, title: name, rows: byDir.get(name).sort(inGroup) }));
    return groups;
};

export const isEmptyValue = (value) => (
    value == null
    || value === false
    || (typeof value === 'string' && ['', '-', '—'].includes(value.trim()))
);

/* Карточка сотрудника — те же наборы колонок, что у переключателя таблицы
   («Общее», «Данные», «Контакты», «Корпоративное»), только все сразу и
   строками «подпись — значение». Имя и статус стоят в шапке карточки;
   поле, уже показанное выше (дата найма есть и в «Общем», и в
   «Корпоративном»), второй раз не повторяется. Пустые поля не рисуются:
   строка «Инстаграм —» в карточке — шум, заполняют их в «Изменить». */
export const buildCardSections = (sectionList, columnsFor, valueOf, skipKeys = ['name', 'status']) => {
    const seen = new Set(skipKeys);
    return (sectionList || [])
        .map((section) => {
            const fields = [];
            (columnsFor(section.key) || []).forEach((column) => {
                if (seen.has(column.key)) return;
                seen.add(column.key);
                const value = valueOf(column);
                if (isEmptyValue(value)) return;
                fields.push({ key: column.key, label: column.label, value });
            });
            return { key: section.key, title: section.label, fields };
        })
        .filter((section) => section.fields.length > 0);
};

/* Статус в строке. «Работает» — норма, про неё строка молчит; метка
   выбранного статуса повторяла бы полосу над списком. Окрашено только то,
   ради чего в список смотрят: отсутствие (Б/С, больничный, отпуск) и
   увольнение. `code` — нормализованный код статуса (dismissal отдельно от
   fired: во вкладке «Уволенные» «Увольнение» — новость, а «Уволен» — нет). */
export const rowStatusNote = ({ code, statusTab, label, blacklist }) => {
    const danger = code === 'fired' || code === 'dismissal';
    if (blacklist) {
        return { text: statusTab === code ? 'ЧС' : `${label} · ЧС`, tone: 'danger' };
    }
    if (!code || code === 'working' || code === statusTab) return null;
    return { text: label, tone: danger ? 'danger' : 'warn' };
};

const SORT_LABELS = {
    name: { label: 'По имени', short: 'Имя', dir: 'asc' },
    status: { label: 'По статусу', short: 'Статус', dir: 'asc' },
    hire_date: { label: 'Сначала новые', short: 'Дата найма', dir: 'desc' },
    direction: { label: 'По направлению', short: 'Направление', dir: 'asc' },
    supervisor: { label: 'По супервайзеру', short: 'Супервайзер', dir: 'asc' },
    rate: { label: 'Сначала полная ставка', short: 'Ставка', dir: 'desc' },
};

/* Порядок — только по колонкам, по которым сортирует таблица этого же
   списка: у бэк-офиса нет «Направления», и пункт «По направлению» переставлял
   бы пустые значения. Направление задаётся пунктом целиком, как в «Сессиях»:
   «по имени от Я к А» в листе никто не ищет. */
export const sortOptionsFromColumns = (columns) => (columns || [])
    .filter((column) => column.sortField && SORT_LABELS[column.sortField])
    .map((column) => ({ field: column.sortField, ...SORT_LABELS[column.sortField] }));
