import React, { useEffect, useMemo, useRef, useState } from 'react';

import {
    APPLE_FONT, IosBadge, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard,
    iosGroupLabel, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import FaIcon from '../common/FaIcon';
import { DIRECTIONS } from './funnelFormat';

/*
 * Сопоставление операторов внешних систем с сотрудниками портала.
 *
 * ЗАЧЕМ ЭКРАН ЕСТЬ. Имена в источниках и в портале расходятся, и расходятся
 * молча: в СРМ «Кузембаева Аяулым» — в портале «Кузембекова Аяулым»,
 * «Жұмаханбет Алдияр» — «Жуманхабет Алдияр», «Сарсенбаева Эльдана» — «Елдана
 * Сарсенбаева». У amoCRM хуже: `/api/v4/users` под нашим токеном отвечает
 * 403 «Admin access only», поэтому оттуда приезжает ЧИСЛО ответственного, а не
 * имя вовсе. Автомат, поставленный на такое, кладёт чужие цифры в чужую строку,
 * и увидеть это в отчёте нельзя — сумма по отделу сходится.
 *
 * ПОЭТОМУ ПОХОЖЕСТЬ ЗДЕСЬ — ТОЛЬКО ПОДСКАЗКА. Она поднимает кандидатов наверх и
 * подписывает, насколько написания близки, но строку заводит нажатие человека.
 * Это сказано и в самом интерфейсе: подсказка, которая знает про себя, что она
 * подсказка, — единственный честный способ показать похожесть.
 *
 * Пропсы: { api, showToast, onChanged }
 * От `api` нужны две вещи:
 *     api.mapping()                     → { mapping: [...], people: [...] }
 *     api.saveMapping({ source, externalKey, userId, isIgnored })
 * `onChanged` зовётся после каждой правки: по ней оболочка раздела освежает
 * счётчик несопоставленных в строке свежести данных.
 */

/* Подписи источников. Ключи — константы `op_funnel/schema.py`; человеку
   «crm_stream» ничего не говорит, а «СРМ · Поток» говорит, откуда взялась
   строка и где искать оригинал. */
const SOURCE_LABELS = {
    crm_stream: 'СРМ · Поток',
    crm_paid_hire: 'СРМ · Платный найм',
    amo: 'amoCRM',
    wazzup: 'Wazzup',
    manual: 'Ручная выгрузка',
};

/* Ниже этой близости кандидата не показываем: список «похожих на 30 %» — это
   не помощь, а шум, в котором настоящее совпадение теряется. */
const SUGGEST_FROM = 0.55;
const SUGGEST_LIMIT = 3;

const SAVE_NOTE = 'Сопоставление применится к новым выгрузкам. Чтобы пересчитать '
    + 'прошлые сутки, перечитайте период кнопкой «Обновить данные».';

/* Буквы, которыми написания расходятся чаще всего: казахские в русской
   раскладке и мягкие знаки. Свести их к одной форме нужно ДО сравнения, иначе
   «Жұмаханбет» и «Жуманхабет» разъезжаются на три буквы вместо одной, а
   «Эльдана» и «Елдана» — на две. */
const FOLD = {
    'ә': 'а', 'ғ': 'г', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у',
    'һ': 'х', 'і': 'и', 'ё': 'е', 'э': 'е', 'й': 'и', 'ь': '', 'ъ': '',
};

const words = (value) => String(value || '')
    .toLowerCase()
    .replace(/[әғқңөұүһіёэйьъ]/g, (letter) => FOLD[letter])
    .replace(/[^0-9a-zа-я]+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);

/* Расстояние Левенштейна по двум строкам слова. Списки здесь маленькие
   (десятки внешних имён на сотню сотрудников), поэтому считаем честно и без
   индексов: экономить тут нечего. */
const distance = (left, right) => {
    if (left === right) return 0;
    if (!left.length || !right.length) return Math.max(left.length, right.length);
    let previous = Array.from({ length: right.length + 1 }, (_, index) => index);
    for (let i = 1; i <= left.length; i += 1) {
        const row = [i];
        for (let j = 1; j <= right.length; j += 1) {
            const cost = left[i - 1] === right[j - 1] ? 0 : 1;
            row[j] = Math.min(row[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost);
        }
        previous = row;
    }
    return previous[right.length];
};

const closeness = (left, right) => {
    const longest = Math.max(left.length, right.length);
    return longest ? 1 - distance(left, right) / longest : 0;
};

/* Похожесть имён. Каждое слово внешнего написания ищет ЛУЧШЕЕ слово в имени из
   портала, результат взвешивается длиной слова: фамилия весит больше инициала.
   Порядок слов намеренно не важен — «Сарсенбаева Эльдана» и «Елдана
   Сарсенбаева» это один человек, записанный с разных концов. */
const nameScore = (external, portal) => {
    const left = words(external);
    const right = words(portal);
    if (!left.length || !right.length) return 0;
    let weighted = 0;
    let weight = 0;
    left.forEach((word) => {
        let best = 0;
        right.forEach((other) => { best = Math.max(best, closeness(word, other)); });
        weighted += best * word.length;
        weight += word.length;
    });
    return weight ? weighted / weight : 0;
};

/* Ответ приходит либо телом, либо целым ответом axios — модуль запросов волен
   отдавать и то, и другое. Разворачиваем один раз здесь. */
const unwrap = (result) => (
    result && typeof result === 'object' && result.status !== undefined && result.data !== undefined
        ? result.data
        : result);

const errText = (error, fallback) => (
    error?.response?.data?.error || error?.data?.error || error?.message || fallback);

const rowKey = (row) => `${row.source}|${row.external_key}`;

const directionTitle = (code) => (
    (DIRECTIONS || []).find((item) => item.code === code)?.title || '');

const externalTitle = (row) => {
    const name = String(row?.external_name || '').trim();
    if (name) return name;
    const key = String(row?.external_key || '').trim();
    /* У amoCRM имени нет вовсе (403 на списке пользователей), в ключе лежит
       числовой id ответственного — подписываем его честно, а не выдаём число
       за имя. */
    return /^\d+$/.test(key) ? `id ${key}` : (key || 'без имени');
};

/* Время сервер отдаёт двумя способами: роуты воронки — строкой
   «2026-09-11 14:33:00» (местное время Алматы без пояса), а сырые строки
   сопоставления Flask сериализует в RFC-1123 с хвостом «GMT», хотя в базе лежит
   то же местное время. Разбирать это одинаково нельзя: у формата с «GMT» браузер
   прибавит пояс и отметка уедет на пять часов вперёд — ровно так уже уезжал
   отчёт Chat2Desk. Поэтому у такой строки читаем UTC-части. */
const formatMoment = (value) => {
    if (!value) return '—';
    const text = String(value);
    const local = /^\d{4}-\d{2}-\d{2}[ T]/.test(text);
    const parsed = new Date(local ? text.replace(' ', 'T') : text);
    if (Number.isNaN(parsed.getTime())) return text;
    const pad = (number) => String(number).padStart(2, '0');
    const day = local ? parsed.getDate() : parsed.getUTCDate();
    const month = (local ? parsed.getMonth() : parsed.getUTCMonth()) + 1;
    const hours = local ? parsed.getHours() : parsed.getUTCHours();
    const minutes = local ? parsed.getMinutes() : parsed.getUTCMinutes();
    return `${pad(day)}.${pad(month)}, ${pad(hours)}:${pad(minutes)}`;
};

const PendingRow = ({ row, active, onSelect }) => (
    <button
        type="button"
        onClick={() => onSelect(row)}
        className={`w-full rounded-xl px-3 py-2.5 text-left transition active:scale-[0.98] ${
            active ? 'bg-blue-50 ring-1 ring-blue-200' : 'hover:bg-slate-50'
        }`}
    >
        <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-slate-900">
                {externalTitle(row)}
            </span>
            <IosBadge tone="slate" className="ml-auto shrink-0">
                {SOURCE_LABELS[row.source] || row.source}
            </IosBadge>
        </div>
        <div className="mt-0.5 text-[11.5px] text-slate-500">
            {row.hint_direction ? `${directionTitle(row.hint_direction) || row.hint_direction} · ` : ''}
            встретили {formatMoment(row.last_seen_at)}
        </div>
    </button>
);

export default function MappingPanel({ api, showToast, onChanged }) {
    /* Родитель волен пересоздавать и `api`, и `showToast` на каждом рендере.
       В зависимостях эффекта такая ссылка гоняет запрос по кругу — в проекте на
       этом уже горели дважды. Держим обоих в ref и зовём через него. */
    const apiRef = useRef(api);
    apiRef.current = api;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const changedRef = useRef(onChanged);
    changedRef.current = onChanged;

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [reload, setReload] = useState(0);
    const [selectedKey, setSelectedKey] = useState(null);
    const [choice, setChoice] = useState(null);
    const [saving, setSaving] = useState(false);
    const [search, setSearch] = useState('');
    const [note, setNote] = useState('');
    const [mappedOpen, setMappedOpen] = useState(false);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        /* Вызов через `Promise.resolve().then` намеренно: так отсутствие метода
           приезжает в `catch` сообщением на экране, а не падением рендера. */
        Promise.resolve()
            .then(() => apiRef.current.mapping())
            .then((result) => {
                if (!alive) return;
                const body = unwrap(result) || {};
                setData({ mapping: body.mapping || [], people: body.people || [] });
                setError(null);
            })
            .catch((exception) => {
                if (alive) setError(errText(exception, 'Не удалось загрузить сопоставление'));
            })
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [reload]);

    const mapping = data?.mapping || [];
    const people = data?.people || [];

    const pending = useMemo(
        () => mapping.filter((row) => !row.user_id && !row.is_ignored),
        [mapping]);
    const settled = useMemo(
        () => mapping.filter((row) => row.user_id || row.is_ignored),
        [mapping]);

    const query = search.trim().toLowerCase();
    const matches = (row) => !query
        || externalTitle(row).toLowerCase().includes(query)
        || String(row.user_name || '').toLowerCase().includes(query);

    const shownPending = pending.filter(matches);
    const shownSettled = settled.filter(matches);

    const selected = mapping.find((row) => rowKey(row) === selectedKey) || null;

    /* Варианты выбора: весь отдел продаж, а не только направление, где встретили
       имя. Человека переводят между направлениями, и в момент сопоставления он
       может числиться уже в другом — иначе его просто не найти в списке. */
    const peopleOptions = useMemo(() => {
        const sorted = [...people].sort((left, right) => {
            const group = directionTitle(left.direction_code)
                .localeCompare(directionTitle(right.direction_code), 'ru');
            return group !== 0 ? group : String(left.name).localeCompare(String(right.name), 'ru');
        });
        return sorted.map((person) => ({
            value: person.id,
            label: person.is_fired ? `${person.name} · уволен` : person.name,
            groupLabel: directionTitle(person.direction_code) || 'Без направления',
        }));
    }, [people]);

    /* Похожесть считается только для выбранной строки: гонять сотню сравнений на
       каждое имя списка незачем — подсказка нужна там, где сейчас решают. */
    const suggestions = useMemo(() => {
        if (!selected) return [];
        const source = externalTitle(selected);
        return people
            .map((person) => ({ person, score: nameScore(source, person.name) }))
            .filter((item) => item.score >= SUGGEST_FROM)
            .sort((left, right) => right.score - left.score)
            .slice(0, SUGGEST_LIMIT);
    }, [selected, people]);

    const pick = (row) => {
        setSelectedKey(rowKey(row));
        /* Кандидата НЕ подставляем в поле: подставленный выбор человек
           подтверждает не глядя, и тогда весь экран теряет смысл. */
        setChoice(row.user_id || null);
    };

    const applyMapping = (row, userId, ignored) => {
        if (!row) return;
        setSaving(true);
        Promise.resolve()
            .then(() => {
                const client = apiRef.current;
                const send = client?.saveMapping || client?.setMapping;
                if (typeof send !== 'function') {
                    throw new Error('Сохранение сопоставления не подключено');
                }
                /* Имена полей — camelCase: именно их ждёт funnelApi.saveMapping и
                   переводит в snake_case для сервера. Со snake_case здесь запрос
                   уходил пустым, сервер отвечал «не указан источник или ключ», и
                   ни одно сопоставление не сохранялось. */
                return send.call(client, {
                    source: row.source,
                    externalKey: row.external_key,
                    userId: userId || null,
                    isIgnored: Boolean(ignored),
                });
            })
            .then((result) => {
                const body = unwrap(result) || {};
                setNote(body.note || SAVE_NOTE);
                const person = people.find((item) => item.id === userId);
                toastRef.current?.(
                    ignored ? `«${externalTitle(row)}» больше не ждёт сопоставления`
                        : userId ? `«${externalTitle(row)}» — это ${person?.name || 'сотрудник портала'}`
                            : `Сопоставление «${externalTitle(row)}» снято`,
                    'success');
                setSelectedKey(null);
                setChoice(null);
                setReload((counter) => counter + 1);
                changedRef.current?.();
            })
            .catch((exception) => toastRef.current?.(
                errText(exception, 'Не удалось сохранить сопоставление'), 'error'))
            .finally(() => setSaving(false));
    };

    return (
        <section id="op-funnel-mapping" className="space-y-3" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-end justify-between gap-2">
                <div className="min-w-0">
                    <h3 className="text-[15px] font-semibold leading-tight text-slate-900">
                        Сопоставление операторов
                    </h3>
                    <p className="mt-0.5 max-w-2xl text-[12.5px] leading-snug text-slate-500">
                        Пока имя из источника не связано с сотрудником, его лиды идут в строку
                        «Не сопоставлен» — они не теряются, но и в отчёте человека нет.
                    </p>
                </div>
                {pending.length > 0 && (
                    <IosBadge tone="amber">
                        Ждут решения: <span className="tabular-nums">{pending.length}</span>
                    </IosBadge>
                )}
            </header>

            {/* Эта фраза — не украшение: она объясняет, почему экран вообще
                существует и почему нельзя нажимать не глядя. */}
            <p className="rounded-2xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-slate-600">
                <FaIcon className="fa-wand-magic-sparkles mr-1.5 align-[-2px] text-slate-400" />
                Похожесть написания — подсказка, а не решение. В СРМ человек записан
                «Кузембаева Аяулым», в портале — «Кузембекова Аяулым»: это один и тот же
                оператор, а соседняя похожая фамилия может оказаться другим человеком.
                Связывает строки тот, кто знает отдел.
            </p>

            {loading && (
                <div className={`${iosCard} flex items-center gap-2 px-4 py-6 text-[13px] text-slate-500`}>
                    <FaIcon className="fa-spinner fa-spin text-slate-400" />
                    Загружаем сопоставление…
                </div>
            )}

            {!loading && error && (
                <div className="flex items-start gap-2 rounded-2xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700 ring-1 ring-rose-100">
                    <FaIcon className="fa-triangle-exclamation mt-0.5 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {!loading && !error && (
                <>
                    {mapping.length > 6 && (
                        <div className="relative">
                            <FaIcon
                                className="fa-magnifying-glass pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                                style={{ fontSize: 13 }}
                            />
                            <input
                                type="text"
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                placeholder="Найти имя из источника или сотрудника"
                                className={`${iosInput} pl-9`}
                            />
                        </div>
                    )}

                    <div className={selected ? 'grid gap-3 md:grid-cols-2' : ''}>
                        <div className="space-y-1.5">
                            <div className={iosGroupLabel}>
                                Не сопоставлены
                                {pending.length ? ` · ${shownPending.length}` : ''}
                            </div>
                            <div className={`${iosCard} overflow-hidden`}>
                                {pending.length === 0 ? (
                                    <p className="flex items-center gap-2 px-4 py-3 text-[12.5px] text-slate-500">
                                        <FaIcon className="fa-circle-check text-emerald-500" />
                                        Все операторы источников связаны с сотрудниками.
                                    </p>
                                ) : shownPending.length === 0 ? (
                                    <p className="px-4 py-3 text-[12.5px] text-slate-500">
                                        По запросу ничего не нашлось.
                                    </p>
                                ) : (
                                    <div className="max-h-[420px] space-y-0.5 overflow-y-auto p-1.5">
                                        {shownPending.map((row) => (
                                            <PendingRow
                                                key={rowKey(row)}
                                                row={row}
                                                active={rowKey(row) === selectedKey}
                                                onSelect={pick}
                                            />
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Карточка выбора рисуется только когда есть кого
                            сопоставлять: пустая половина экрана с надписью
                            «выберите слева» — это шум. На телефоне она встаёт
                            первой, иначе после нажатия на строку человек смотрит
                            на прежний список и не понимает, что что-то открылось. */}
                        {selected && (
                            <div className="order-first space-y-1.5 md:order-none">
                                <div className={iosGroupLabel}>Кто это в портале</div>
                                <div className={`${iosCard} space-y-3 p-4`}>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className="text-[15px] font-semibold text-slate-900">
                                                {externalTitle(selected)}
                                            </span>
                                            <IosBadge tone="slate">
                                                {SOURCE_LABELS[selected.source] || selected.source}
                                            </IosBadge>
                                        </div>
                                        <div className="mt-1 text-[11.5px] text-slate-500">
                                            Ключ источника: <span className="tabular-nums">{selected.external_key}</span>
                                            {' · '}впервые {formatMoment(selected.first_seen_at)}
                                        </div>
                                    </div>

                                    {suggestions.length > 0 && (
                                        <div className="space-y-1.5">
                                            <div className="text-[11.5px] text-slate-500">
                                                Похожие написания — нажмите, чтобы подставить в выбор:
                                            </div>
                                            <div className="flex flex-wrap gap-1.5">
                                                {suggestions.map(({ person, score }) => (
                                                    <button
                                                        key={person.id}
                                                        type="button"
                                                        onClick={() => setChoice(person.id)}
                                                        className={`inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[12.5px] transition active:scale-[0.98] ${
                                                            choice === person.id
                                                                ? 'bg-blue-600 text-white'
                                                                : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                                        }`}
                                                    >
                                                        <span>{person.name}</span>
                                                        <span className={`tabular-nums ${
                                                            choice === person.id ? 'text-white/70' : 'text-slate-400'
                                                        }`}
                                                        >
                                                            {Math.round(score * 100)} %
                                                        </span>
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    <div className="space-y-1.5">
                                        <div className={iosGroupLabel}>Сотрудник отдела продаж</div>
                                        <CustomSelect
                                            value={choice}
                                            onChange={setChoice}
                                            options={peopleOptions}
                                            placeholder="Выберите сотрудника"
                                            searchable
                                            searchPlaceholder="Фамилия или имя"
                                            variant="ios"
                                            ariaLabel="Сотрудник, которому принадлежит имя из источника"
                                        />
                                    </div>

                                    <div className="flex flex-wrap items-center gap-2">
                                        <button
                                            type="button"
                                            className={iosBtnPrimary}
                                            disabled={!choice || saving}
                                            onClick={() => applyMapping(selected, choice, false)}
                                        >
                                            <FaIcon className={saving ? 'fa-spinner fa-spin' : 'fa-user-check'} />
                                            Сопоставить
                                        </button>
                                        <button
                                            type="button"
                                            className={iosBtnSecondary}
                                            disabled={saving}
                                            onClick={() => applyMapping(selected, null, true)}
                                            title="Бот, тестовая учётка или чужой отдел — такие строки больше не спрашиваем"
                                        >
                                            <FaIcon className="fa-user-slash" />
                                            Не сопоставлять
                                        </button>
                                        <button
                                            type="button"
                                            className={iosBtnGhost}
                                            onClick={() => { setSelectedKey(null); setChoice(null); }}
                                        >
                                            Отмена
                                        </button>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    {settled.length > 0 && (
                        <div className="space-y-1.5">
                            <button
                                type="button"
                                onClick={() => setMappedOpen((open) => !open)}
                                className={`${iosGroupLabel} inline-flex items-center gap-1.5 hover:text-slate-700`}
                            >
                                <FaIcon className={mappedOpen ? 'fa-chevron-down' : 'fa-chevron-right'} />
                                Уже решено · {settled.length}
                            </button>
                            {mappedOpen && (
                                <div className={`${iosCard} overflow-hidden`}>
                                    {shownSettled.length === 0 ? (
                                        <p className="px-4 py-3 text-[12.5px] text-slate-500">
                                            По запросу ничего не нашлось.
                                        </p>
                                    ) : (
                                        <div className="max-h-[360px] divide-y divide-slate-100 overflow-y-auto">
                                            {shownSettled.map((row) => (
                                                <div
                                                    key={rowKey(row)}
                                                    className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3.5 py-2.5"
                                                >
                                                    <span className="text-[13px] text-slate-700">
                                                        {externalTitle(row)}
                                                    </span>
                                                    <FaIcon className="fa-arrow-right text-slate-300" style={{ fontSize: 11 }} />
                                                    {row.user_id ? (
                                                        <span className="text-[13px] font-medium text-slate-900">
                                                            {row.user_name || `id ${row.user_id}`}
                                                        </span>
                                                    ) : (
                                                        <span className="text-[13px] text-slate-500">
                                                            служебная учётка
                                                        </span>
                                                    )}
                                                    <IosBadge tone="slate" className="ml-auto">
                                                        {SOURCE_LABELS[row.source] || row.source}
                                                    </IosBadge>
                                                    <button
                                                        type="button"
                                                        className={iosBtnGhost}
                                                        disabled={saving}
                                                        onClick={() => applyMapping(row, null, false)}
                                                        title="Вернуть строку в очередь на сопоставление"
                                                    >
                                                        <FaIcon className="fa-rotate-left" />
                                                        Снять
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Подсказка появляется только после правки: до неё говорить
                        нечего, а постоянная строка про перечитывание периода
                        превратилась бы в фон. */}
                    {note && (
                        <p className="flex items-start gap-2 rounded-2xl bg-blue-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-blue-800 ring-1 ring-blue-100">
                            <FaIcon className="fa-circle-info mt-0.5 shrink-0" />
                            <span>{note}</span>
                        </p>
                    )}
                </>
            )}
        </section>
    );
}
