/*
 * «Учет сотрудников» на телефоне — правила раскладки без React.
 *
 * Компьютер показывает таблицу, телефон — список людей и карточку. Кого
 * пропускает статус, поиск и порядок и как складываются группы «Мои
 * операторы» и направления, решает сервер (employees/page.py): телефон, как и
 * таблица, получает готовую страницу. Здесь только то, чего у таблицы нет
 * вовсе: какие метки статусов стоит показывать, какие поля карточки пустые и
 * когда статус нужен в строке. Всё закреплено tests/employees_phone_list.test.mjs.
 */

import { groupPageRows } from './employeesPaging.js';

export const plural = (n, [one, few, many]) => {
    const abs = Math.abs(Number(n) || 0) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
};

/* Страница с сервера (pageOfResponse) → то, что рисует телефон: строки по
   группам, числа на метках, итог и номер страницы. Карточку и фото в «Днях
   рождения» ищем по всему списку (rows), а строка страницы свежее — она и
   побеждает. «Все выбраны» — когда выбраны все найденные (их id «Выбрать
   всех» берёт у сервера App.jsx), а не одна страница. */
export const phonePage = ({ pageData, rows, selectedIds }) => {
    const pageRows = pageData?.rows || [];
    const total = pageData?.total || 0;
    const selected = selectedIds || new Set();
    return {
        pageRows,
        rowsById: new Map([...(Array.isArray(rows) ? rows : []), ...pageRows].map((row) => [Number(row?.id), row])),
        counts: pageData?.counts || {},
        total,
        listTotal: pageData?.listTotal || 0,
        paged: {
            groups: pageData?.groups
                ? groupPageRows(pageRows, pageData.groups)
                : [{ key: 'all', title: '', total, rows: pageRows }],
            page: pageData?.page || 1,
            pageCount: pageData?.pageCount || 1,
            total,
            from: pageData?.from || 0,
            to: pageData?.to || 0,
        },
        allSelected: total > 0 && selected.size >= total && pageRows.every((row) => selected.has(Number(row?.id))),
    };
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
            return { key: section.key, title: section.label, icon: section.icon, fields };
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
