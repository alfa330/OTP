import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, ChevronDown, Loader2, RefreshCw, ScrollText, Search, X,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosInput, iosBtnSecondary, iosBtnGhost, IosBadge } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { dayKeyOf, groupTasksByDay } from '../tasks/boardGrouping';
import { ACTION_META, AUDIT_GROUPS, GONE_ENTITY, auditFacts, auditRest } from './auditEvents';
import useStableCallback from './useStableCallback';

/* Журнал раздела.
 *
 * В исходной вике таблиц аудита было две, почти одинаковых, и обе только
 * писались — ни API, ни интерфейс их не читали. То есть аудита фактически не
 * существовало: узнать, кто и когда выдал доступ, было неоткуда.
 *
 * Первая версия чтения показывала запись как есть: английский ключ действия,
 * имя автора и время. Восемь ключей из тридцати шести были переведены, объект
 * события не назывался вообще, а подробности из details не показывались. Плюс
 * время уезжало на пять часов вперёд (сервер отдавал местное как GMT). Всё
 * вместе читалось как список непонятных строк — переписано целиком:
 *
 *   ЧТО произошло → С ЧЕМ → КТО и подробности → КОГДА,
 *
 * с раскладкой по дням, фильтром по смыслу события и поиском. Формулировки
 * живут в auditEvents.js, чтобы этот файл остался про экран.
 */

const PAGE_SIZE = 50;

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/** Время события. Дата вынесена в заголовок дня, в строке остаются часы. */
const fmtTime = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

/** Полное значение — в подсказке: в заголовке дня нет года, а в строке секунд. */
const fmtFull = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
};

/* Одно и то же событие подряд: автосохранение статьи и повторы запросов к ИИ
   пишут по три одинаковых записи, а strict_bypass — по записи на каждое
   открытие статьи. Развёрнутым списком это заваливает ленту и обесценивает
   единственный тревожный сигнал журнала, поэтому соседние совпадения в
   пределах дня показываем одной строкой с «×N». */
const sameEvent = (a, b) => a.actor_id === b.actor_id
    && a.action === b.action
    && a.entity_id === b.entity_id
    && dayKeyOf(a.created_at) === dayKeyOf(b.created_at)
    && JSON.stringify(a.details || {}) === JSON.stringify(b.details || {});

const collapseRepeats = (list) => {
    const out = [];
    list.forEach((item) => {
        const prev = out[out.length - 1];
        if (prev && sameEvent(prev, item)) {
            prev.repeats.push(item.created_at);
            return;
        }
        out.push({ ...item, repeats: [item.created_at] });
    });
    return out;
};

const TONE_ICON = {
    green: 'bg-emerald-50 text-emerald-600',
    blue: 'bg-blue-50 text-blue-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-rose-50 text-rose-600',
    slate: 'bg-slate-100 text-slate-500',
};

const Block = ({ children }) => (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">{children}</div>
);

// ── Одна запись ─────────────────────────────────────────────────────────────
const AuditRow = ({ item, nameOf, onOpenArticle }) => {
    const [open, setOpen] = useState(false);

    const meta = ACTION_META[item.action];
    const Icon = meta?.icon || ScrollText;
    const tone = meta?.tone || 'slate';
    // Незнакомое действие показываем ключом, а не прячем: журнал, который
    // молчит о событии, хуже журнала с техническим словом.
    const label = meta?.label || item.action;

    const facts = useMemo(() => auditFacts(item, nameOf), [item, nameOf]);
    const rest = useMemo(() => auditRest(item), [item]);

    const repeats = item.repeats?.length || 1;
    const times = repeats > 1 ? item.repeats.map(fmtTime).join(', ') : '';
    const gone = item.entity_id != null && item.entity_alive === false;
    const openable = item.entity_type === 'article' && item.entity_slug && !gone && onOpenArticle;

    return (
        <li className="px-4 py-3 transition hover:bg-slate-50/70">
            <div className="flex items-start gap-3">
                <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${TONE_ICON[tone]}`}>
                    <Icon size={15} />
                </span>

                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
                        <span className="text-[13.5px] font-semibold text-slate-900"
                              title={meta ? undefined : `Действие ${item.action}`}>
                            {label}
                        </span>

                        {item.entity_name && !openable && (
                            <span className="min-w-0 truncate text-[13.5px] text-slate-700"
                                  title={item.entity_name}>
                                «{item.entity_name}»
                            </span>
                        )}
                        {item.entity_name && openable && (
                            // Из журнала видно, что случилось со статьёй, — логично
                            // тут же её и открыть, не разыскивая по названию.
                            <button
                                type="button"
                                onClick={() => onOpenArticle(item.entity_slug)}
                                title={`Открыть статью «${item.entity_name}»`}
                                className="min-w-0 max-w-full truncate text-[13.5px] text-blue-600 underline-offset-2 transition hover:underline"
                            >
                                «{item.entity_name}»
                            </button>
                        )}
                        {/* Объекта в базе больше нет — на проде так со всеми
                            записями об офисах: таблицу пересоздали миграцией.
                            Пустое место здесь читалось бы как «событие ни о чём». */}
                        {gone && (
                            <IosBadge tone="slate">{GONE_ENTITY[item.entity_type] || 'объект удалён'}</IosBadge>
                        )}
                        {repeats > 1 && (
                            <IosBadge tone="slate" title={`Времена: ${times}`}>×{repeats}</IosBadge>
                        )}
                    </div>

                    <div className="mt-0.5 text-[12px] leading-relaxed text-slate-500">
                        {/* actor_name пустеет, если человека удалили из портала:
                            в базе стоит ON DELETE SET NULL. Раньше выходило «#null». */}
                        <span className="font-medium text-slate-600">
                            {item.actor_name || (item.actor_id ? `Пользователь №${item.actor_id}` : 'Автор неизвестен')}
                        </span>
                        {/* Больше четырёх уточнений строка не несёт: остальное
                            ждёт в подробностях, где для него есть место. */}
                        {facts.slice(0, 4).map((fact) => (
                            <span key={fact}> · {fact}</span>
                        ))}
                        {/* Раскрытие — в той же строке, а не под ней: отдельной
                            строкой оно добавляло каждой записи третий этаж и
                            ломало ровный шаг ленты. */}
                        {rest.length > 0 && (
                            <>
                                {' · '}
                                <button
                                    type="button"
                                    onClick={() => setOpen((v) => !v)}
                                    aria-expanded={open}
                                    className="font-medium text-slate-400 underline-offset-2 transition hover:text-slate-600 hover:underline"
                                >
                                    {open ? 'скрыть' : 'подробности'}
                                </button>
                            </>
                        )}
                    </div>

                    {open && rest.length > 0 && (
                        <dl className="mt-1.5 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 rounded-xl bg-slate-50 px-3 py-2 text-[11.5px]">
                            {/* Ключ по номеру, а не по подписи: разные поля
                                details переводятся одним словом (summary и
                                description — оба «описание»). */}
                            {rest.map(([label, value], index) => (
                                <React.Fragment key={index}>
                                    <dt className="text-slate-500">{label}</dt>
                                    <dd className="min-w-0 break-words text-slate-700">{value}</dd>
                                </React.Fragment>
                            ))}
                        </dl>
                    )}
                </div>

                <time
                    dateTime={item.created_at || undefined}
                    title={fmtFull(item.created_at)}
                    className="shrink-0 pt-0.5 text-[11.5px] tabular-nums text-slate-500"
                >
                    {fmtTime(item.created_at)}
                </time>
            </div>
        </li>
    );
};

export default function WikiAudit({ base, headers, showToast, structure, spaceId,
                                    spaceName, onOpenArticle }) {
    const toast = useStableCallback(showToast);

    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [counts, setCounts] = useState(null);
    /* Записи, не отнесённые ни к одному пространству. Граница журнала строгая,
       и такая запись не попадает НИКУДА — молча потерянную запись аудит себе
       позволить не может. В норме здесь ноль, и подвал о них не говорит. */
    const [outside, setOutside] = useState(0);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const [appending, setAppending] = useState(false);

    const [group, setGroup] = useState('all');
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [range, setRange] = useState({ from: '', to: '' });

    /* Поиск уходит на сервер не на каждой букве: журнал фильтруется целиком,
       а не только загруженной страницей, — иначе «Показано 12 из 305» врёт. */
    useEffect(() => {
        const timer = setTimeout(() => setSearch(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);

    /* Длина уже загруженного нужна на момент запроса, а не на момент, когда
       создавался обработчик: без ref «Показать ещё» присылала бы вторую
       страницу дважды. Тот же ref гасит гонку — ответ отставшего запроса
       (например, от прошлого фильтра) не должен перетирать свежий. */
    const loadedRef = useRef(0);
    const seqRef = useRef(0);

    const load = useCallback((append = false) => {
        const seq = seqRef.current + 1;
        seqRef.current = seq;
        if (append) setAppending(true); else setBusy(true);

        axios.get(`${base}/audit`, {
            headers,
            params: {
                limit: PAGE_SIZE,
                offset: append ? loadedRef.current : 0,
                /* Журнал у пространства свой. Параметр обязателен ровно так же,
                   как у справочников: сервер по нему и границу ставит, и
                   отказывает в чужом. */
                space_id: spaceId || undefined,
                group: group === 'all' ? undefined : group,
                q: search || undefined,
                from: range.from || undefined,
                to: range.to || undefined,
            },
        })
            .then((r) => {
                if (seq !== seqRef.current) return;
                const batch = r.data?.items || [];
                setItems((prev) => {
                    const next = append ? [...(prev || []), ...batch] : batch;
                    loadedRef.current = next.length;
                    return next;
                });
                // Итоги сервер считает только на первой странице: фильтр при
                // догрузке тот же, пересчитывать его нечего.
                if (!append) {
                    setTotal(r.data?.total ?? batch.length);
                    setCounts(r.data?.counts || null);
                    setOutside(r.data?.outside || 0);
                }
                setError(null);
            })
            .catch((e) => {
                if (seq !== seqRef.current) return;
                const message = errText(e, 'Не удалось загрузить журнал');
                // Плашка вместо списка — только когда списка ещё нет: тост
                // исчезает, и пустой экран потом читается как «записей нет».
                // А вот на сбое догрузки прочитанное убирать нельзя — человек
                // потеряет то, что уже разбирал; там достаточно тоста.
                if (append) toast(message, 'error');
                else setError(message);
            })
            .finally(() => {
                if (seq !== seqRef.current) return;
                setAppending(false);
                setBusy(false);
            });
    }, [base, headers, spaceId, group, search, range.from, range.to, toast]);

    useEffect(() => { load(false); }, [load]);

    /* Названия пространств и разделов для подробностей: в details лежат
       идентификаторы, а дерево уже загружено разделом. */
    const nameOf = useMemo(() => {
        const spaces = new Map((structure?.spaces || []).map((s) => [s.id, s.name]));
        const sections = new Map((structure?.sections || []).map((s) => [s.id, s.name]));
        return (kind, id) => (kind === 'space' ? spaces.get(id) : sections.get(id)) || null;
    }, [structure]);

    const days = useMemo(
        () => groupTasksByDay(collapseRepeats(items || []), { field: 'created_at' }),
        [items]);

    const filtering = group !== 'all' || !!search || !!range.from || !!range.to;
    const reset = () => { setGroup('all'); setQuery(''); setRange({ from: '', to: '' }); };
    const chips = AUDIT_GROUPS.filter(
        (chip) => chip.key === 'all' || chip.key === group || !counts || counts[chip.key] > 0);

    return (
        <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                    <h2 className={iosGroupLabel}>Журнал изменений</h2>
                    {/* Чей это журнал — одним словом и ОДИН раз. У «Таксопарков»
                        и «Теза» он свой, и до 04.09.2026 записи двух вик
                        перемешивались; после починки признак нужен ровно затем,
                        чтобы это было видно, не листая. На каждой строке то же
                        слово было бы шумом: чужих записей здесь больше нет. */}
                    {spaceName && <IosBadge tone="slate">{spaceName}</IosBadge>}
                </div>
                <button type="button" className={iosBtnSecondary} onClick={() => load(false)}
                        disabled={busy}>
                    <RefreshCw size={14} className={busy ? 'animate-spin' : ''} /> Обновить
                </button>
            </div>

            <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                    <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} pl-10 ${query ? 'pr-10' : ''}`}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Человек, статья, раздел или кому выдали доступ"
                        aria-label="Поиск по журналу"
                    />
                    {query && (
                        <button
                            type="button"
                            aria-label="Очистить поиск"
                            onClick={() => setQuery('')}
                            className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600"
                        >
                            <X size={14} />
                        </button>
                    )}
                </div>
                    <IosDateRangePicker
                        from={range.from} to={range.to} max={isoDate(new Date())}
                        onChange={setRange}
                    />
                    {filtering && (
                        <button type="button" className={iosBtnGhost} onClick={reset}>
                            Сбросить
                        </button>
                    )}
                </div>

                <div className="flex flex-wrap gap-1.5">
                    {chips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            aria-pressed={group === chip.key}
                            onClick={() => setGroup(chip.key)}
                            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition ${
                                group === chip.key
                                    ? 'bg-slate-900 text-white'
                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
                            }`}
                        >
                            {chip.label}
                            {counts && (
                                <span className="tabular-nums opacity-60">{counts[chip.key] ?? 0}</span>
                            )}
                        </button>
                    ))}
                </div>
            </div>

            {error && (
                <div className={`${iosCard} flex items-start gap-3 p-4`}>
                    <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-500" />
                    <div className="min-w-0 flex-1">
                        <div className="text-[13.5px] font-medium text-slate-900">Журнал не загрузился</div>
                        <p className="mt-0.5 text-[12px] text-slate-500">{error}</p>
                    </div>
                    <button type="button" className={iosBtnGhost} onClick={() => load(false)}>
                        Попробовать снова
                    </button>
                </div>
            )}

            {!error && (
                <div className={`${iosCard} overflow-hidden`} aria-busy={busy}>
                    {(busy || items === null) && (
                        <Block>
                            <Loader2 size={18} className="animate-spin text-slate-400" />
                            <span className="text-[13px] text-slate-500">Загружаем…</span>
                        </Block>
                    )}

                    {!busy && items !== null && items.length === 0 && (
                        <Block>
                            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                <ScrollText size={22} />
                            </div>
                            <div className="text-[15px] font-semibold text-slate-900">
                                {filtering ? 'Ничего не найдено' : 'Записей нет'}
                            </div>
                            <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                                {filtering
                                    ? 'Под выбранные условия ничего не подошло.'
                                    : 'Здесь появятся изменения структуры, статей и выдачи прав — кто, что и когда.'}
                            </p>
                            {filtering && (
                                <button type="button" className={iosBtnSecondary} onClick={reset}>
                                    Сбросить фильтры
                                </button>
                            )}
                        </Block>
                    )}

                    {!busy && items !== null && items.length > 0 && (
                        <div className="divide-y divide-slate-100">
                            {days.map((day) => (
                                <section key={day.key}>
                                    {/* Заголовок дня липкий: пролистав сотню строк,
                                        человек всё ещё видит, какой это день. */}
                                    <div className="sticky top-0 z-10 border-b border-slate-100 bg-white/90 px-4 py-1.5 backdrop-blur">
                                        <div className={iosGroupLabel}>{day.label}</div>
                                    </div>
                                    <ul className="divide-y divide-slate-100">
                                        {day.tasks.map((item) => (
                                            <AuditRow key={item.id} item={item} nameOf={nameOf}
                                                      onOpenArticle={onOpenArticle} />
                                        ))}
                                    </ul>
                                </section>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {!error && items !== null && items.length > 0 && (
                <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                    <span className="text-[11.5px] text-slate-500">
                        Показано {items.length} из {total}
                    </span>
                    {items.length < total && (
                        <button type="button" className={iosBtnSecondary} onClick={() => load(true)}
                                disabled={appending}>
                            {appending
                                ? <><Loader2 size={13} className="animate-spin" /> Загружаем…</>
                                : <><ChevronDown size={13} /> Показать ещё ({total - items.length})</>}
                        </button>
                    )}
                </div>
            )}

            {/* Записи вне пространств. В норме их ноль, и строки нет вовсе.
                Появилась — значит завелась дверь, которая снова пишет в журнал
                не называя пространства: такая запись не видна ни в одном
                журнале, и узнать о ней больше неоткуда. */}
            {!error && items !== null && outside > 0 && (
                <p className="px-1 text-[11.5px] text-slate-400">
                    Вне пространств: {outside} — {outside === 1 ? 'эта запись не показана' : 'эти записи не показаны'} ни в одном журнале.
                </p>
            )}
        </section>
    );
}
