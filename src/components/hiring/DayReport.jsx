import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Plus } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosHint, iosBtnGhost, iosCard } from '../ui/ios';
import ReportCardsModal from './ReportCardsModal';
import { addDays, errorOf, sourceTone, todayIso } from './hiringMeta';
import { Loading, TOOLBAR_BTN } from './hiringUi';
import {
    NOTE_MAX, cellKey, cellOpens, dayReportQuery, firstPartIndex, noteCounter, personKey, scopeLabel, splitNotes, talkClock,
    totalKey, withMyNote,
} from './dayReportMeta';

/*
 * «Отчёты → За день» (задача HR-3): отчёт рекрутера за день одним листом — его делают скриншотом и отправляют
 * руководству. Цифры считает сервер (hiring/day_report.py) из работы в разделе, руками в таблицу ничего не вносится
 * (решение владельца); руками — только «Другая работа за день».
 *
 * Лист: воронка дня по каналам владельца (hh, «Таргет», WhatsApp, «Вручную»): поступило, отработано и где отработанные
 * оказались к концу дня (строки «из них» дают «Отработано»), провели собеседование; у руководителя — разрез по
 * рекрутерам; эффективность — звонки, разговоры, среднее время разговора, WhatsApp; «Другая работа» — текст за день.
 * Рекрутеру сервер отдаёт только его отчёт, руководителю — команду или выбранного рекрутера. Цифра раскрывается списком
 * карточек, как в дашборде. Цвет — только точка источника в шапке столбца (тот же, что на карточках).
 */

/* Файл приходит ответом API — ссылка на объект в этом же окне, как у выгрузок дашборда. */
const saveBlob = (blob, name) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const DaySwitch = ({ value, max, onChange }) => (
    <div className="inline-flex h-9 items-center rounded-xl bg-white ring-1 ring-slate-200/70">
        <button type="button" aria-label="Предыдущий день" onClick={() => onChange(addDays(value, -1))}
                className="grid h-9 w-9 place-items-center rounded-l-xl text-slate-500 hover:bg-slate-50">
            <ChevronLeft size={16} />
        </button>
        <IosDatePicker value={value} max={max} ariaLabel="День отчёта"
                       onChange={(next) => { if (next) onChange(next > max ? max : next); }}
                       triggerClassName="flex h-9 items-center gap-1.5 px-2 text-[13px] font-medium tabular-nums text-slate-800 transition hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60" />
        <button type="button" aria-label="Следующий день" disabled={value >= max} onClick={() => onChange(addDays(value, 1))}
                className="grid h-9 w-9 place-items-center rounded-r-xl text-slate-500 hover:bg-slate-50 disabled:opacity-40">
            <ChevronRight size={16} />
        </button>
    </div>
);

/* Число отчёта: ноль — бледный, ненулевое — кнопкой со списком карточек (подчёркивание по наведению, без синего:
   лист уходит скриншотом, синие цифры на нём — шум). */
const Num = ({ value, onOpen, strong = false }) => {
    const text = value || 0;
    if (!onOpen) return <span className={`px-1 ${value ? 'text-slate-900' : 'text-slate-300'} ${strong ? 'font-semibold' : ''}`}>{text}</span>;
    return (
        <button type="button" onClick={onOpen}
                className={`rounded-md px-1 tabular-nums text-slate-900 underline-offset-2 transition hover:bg-slate-50 hover:underline ${strong ? 'font-semibold' : ''}`}>
            {text}
        </button>
    );
};

const Section = ({ title, aside, children }) => (
    <div className="border-t border-slate-100">
        <div className="flex items-center justify-between gap-2 px-4 pb-1.5 pt-3 sm:px-5">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{title}</div>
            {aside}
        </div>
        {children}
    </div>
);

const TH = 'px-3 py-2 text-right font-medium';
const HEAD_ROW = 'border-y border-slate-100 bg-slate-50/70 text-[12px] text-slate-500';
/* На широком экране таблицы помещаются целиком — без прокрутки: подсказки «i» тогда не обрезаются её краем. */
const SCROLL = 'overflow-x-auto pb-1 lg:overflow-visible';

const FunnelTable = ({ data, caps, onCell }) => {
    const channels = data.channels || [];
    const partsFrom = firstPartIndex(data.rows);
    return (
        <div className={SCROLL}>
            <table className="w-full min-w-[620px] text-[13px]">
                <thead>
                    <tr className={HEAD_ROW}>
                        <th className="py-2 pl-4 pr-3 text-left font-medium sm:pl-5">Показатель</th>
                        {channels.map((channel) => (
                            <th key={channel.code} className={TH}>
                                <span className="inline-flex items-center justify-end gap-1.5">
                                    {channel.color && <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${sourceTone(channel.color).dot}`} />}
                                    {channel.label}
                                    {channel.hint && <IosHint text={channel.hint} label="Что это" align="right" />}
                                </span>
                            </th>
                        ))}
                        <th className="py-2 pl-3 pr-4 text-right font-semibold text-slate-700 sm:pr-5">Итого</th>
                    </tr>
                </thead>
                <tbody>
                    {data.rows.map((row, index) => {
                        const ruled = row.key === 'done' || row.key === 'interviews';
                        const open = (code, value, label) => (cellOpens(row.key, value, caps) ? () => onCell(cellKey(row.key, code), label) : null);
                        return (
                            <React.Fragment key={row.key}>
                                {index === partsFrom && (
                                    <tr><td colSpan={channels.length + 2} className="pb-0.5 pl-9 pt-0.5 text-[11px] text-slate-400 sm:pl-10">из них</td></tr>
                                )}
                                <tr className={ruled ? 'border-t border-slate-100' : ''}>
                                    <td className={`py-1.5 pr-3 ${row.part ? 'pl-9 text-slate-600 sm:pl-10' : 'pl-4 text-slate-900 sm:pl-5'} ${row.key === 'done' ? 'font-semibold' : ''}`}>
                                        <span className="inline-flex items-center gap-1">
                                            {row.label}
                                            {row.hint && <IosHint text={row.hint} label="Как считается" />}
                                        </span>
                                    </td>
                                    {channels.map((channel) => (
                                        <td key={channel.code} className="px-3 py-1.5 text-right tabular-nums">
                                            <Num value={row.cells[channel.code]} strong={row.key === 'done'}
                                                 onOpen={open(channel.code, row.cells[channel.code], `${row.label} · ${channel.label}`)} />
                                        </td>
                                    ))}
                                    <td className="py-1.5 pl-3 pr-4 text-right tabular-nums sm:pr-5">
                                        <Num value={row.cells.all} strong onOpen={open('all', row.cells.all, row.label)} />
                                    </td>
                                </tr>
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

/* Таблица по рекрутерам: строка на человека и «Итого», если людей больше одного. cell(row, column, isTotal) — содержимое. */
const PeopleTable = ({ columns, people, totals, cell, minWidth }) => (
    <div className={SCROLL}>
        <table className={`w-full text-[13px] ${minWidth}`}>
            <thead>
                <tr className={HEAD_ROW}>
                    <th className="py-2 pl-4 pr-3 text-left font-medium sm:pl-5">Рекрутер</th>
                    {columns.map((column, index) => (
                        <th key={column.key} className={`${TH} ${index === columns.length - 1 ? 'pr-4 sm:pr-5' : ''}`}>
                            <span className="inline-flex items-center justify-end gap-1">
                                {column.label}
                                {column.hint && <IosHint text={column.hint} label="Что это" align="right" />}
                            </span>
                        </th>
                    ))}
                </tr>
            </thead>
            <tbody>
                {[...people.map((person) => ({ person, total: false })), ...(people.length > 1 ? [{ person: totals, total: true }] : [])]
                    .map(({ person, total }) => (
                        <tr key={total ? 'total' : person.id} className={total ? 'border-t border-slate-100 font-semibold' : ''}>
                            <td className="truncate py-1.5 pl-4 pr-3 text-slate-900 sm:pl-5">{total ? 'Итого' : person.name}</td>
                            {columns.map((column, index) => (
                                <td key={column.key} className={`px-3 py-1.5 text-right tabular-nums ${index === columns.length - 1 ? 'pr-4 sm:pr-5' : ''}`}>
                                    {cell(person, column, total)}
                                </td>
                            ))}
                        </tr>
                    ))}
            </tbody>
        </table>
    </div>
);

const AUTOSAVE_MS = 900;
const STATUS_TEXT = { saving: 'Сохраняем…', saved: 'Сохранено', error: 'Не сохранилось' };

/*
 * «Другая работа за день» — свой текст, сохраняется сам: через секунду после ввода, при уходе из поля и при закрытии
 * вкладки. Текст поля не перезаписывается ответом сервера и фоновым обновлением отчёта — окно ключевано днём, а
 * значение живёт здесь (иначе курсор прыгал бы, а допечатанное пропадало). На листе запись выглядит как чужие —
 * текстом в мягкой плашке (лист уходит скриншотом), правится щелчком по ней.
 */
const NoteEditor = ({ api, toast, day, initial, name, autoFocus = false, onSaved, onEmptyBlur }) => {
    const [text, setText] = useState(initial);
    const [status, setStatus] = useState('');
    const textRef = useRef(initial);
    const savedRef = useRef(initial);
    const timerRef = useRef(null);
    const busyRef = useRef(false);
    const areaRef = useRef(null);

    const flush = useCallback(() => {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        if (busyRef.current) {
            // Предыдущее сохранение ещё в пути — следующее уйдёт за ним, а не наперегонки.
            timerRef.current = setTimeout(flush, 300);
            return;
        }
        const body = textRef.current;
        if (body === savedRef.current) return;
        busyRef.current = true;
        setStatus('saving');
        api.put('/reports/day/note', { day, body })
            .then(() => {
                savedRef.current = body;
                setStatus(textRef.current === body ? 'saved' : 'saving');
                onSaved?.(body);
            })
            .catch((error) => {
                setStatus('error');
                toast(errorOf(error, '«Другая работа» не сохранилась — попробуйте ещё раз'), 'error');
            })
            .finally(() => {
                busyRef.current = false;
                if (textRef.current !== savedRef.current && !timerRef.current) timerRef.current = setTimeout(flush, AUTOSAVE_MS);
            });
    }, [api, day, toast, onSaved]);

    // Ушли со вкладки или сменили день, не дождавшись автосохранения, — дописанное всё равно уходит на сервер.
    useEffect(() => () => {
        clearTimeout(timerRef.current);
        if (textRef.current !== savedRef.current) api.put('/reports/day/note', { day, body: textRef.current }).catch(() => {});
    }, [api, day]);

    useLayoutEffect(() => {
        const area = areaRef.current;
        if (!area) return;
        area.style.height = 'auto';
        area.style.height = `${Math.min(area.scrollHeight, 320)}px`;
    }, [text]);

    const onChange = (event) => {
        const value = event.target.value.slice(0, NOTE_MAX);
        setText(value);
        textRef.current = value;
        if (status === 'saved' || status === 'error') setStatus('');
        clearTimeout(timerRef.current);
        timerRef.current = setTimeout(flush, AUTOSAVE_MS);
    };

    const onBlur = () => {
        flush();
        if (!textRef.current.trim()) onEmptyBlur?.();
    };

    const counter = noteCounter(text);
    return (
        <div className="cursor-text rounded-xl bg-slate-50 px-3.5 py-2.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70"
             onClick={() => areaRef.current?.focus()}>
            {(name || status || counter) && (
                <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12px] font-medium text-slate-500">{name}</span>
                    <span className="flex gap-3 text-[11.5px] tabular-nums">
                        {counter && <span className="text-slate-400">{counter}</span>}
                        {status && <span className={status === 'error' ? 'text-rose-600' : 'text-slate-400'}>{STATUS_TEXT[status]}</span>}
                    </span>
                </div>
            )}
            <textarea ref={areaRef} value={text} onChange={onChange} onBlur={onBlur} rows={1} maxLength={NOTE_MAX}
                      autoFocus={autoFocus} aria-label="Другая работа за день"
                      placeholder="Что ещё сделали за день, кроме работы с кандидатами: задачи, встречи, помощь отделам"
                      className="mt-0.5 block w-full resize-none border-0 bg-transparent p-0 text-[13.5px] leading-snug text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-0" />
        </div>
    );
};

const DayReport = ({ api, toast, boot, caps, onOpenCard, version, switcher }) => {
    const today = todayIso();
    const [day, setDay] = useState(today);
    const [recruiterId, setRecruiterId] = useState('');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [request, setRequest] = useState(null);
    // «Другая работа» пустая — на листе её нет (лист уходит скриншотом); кнопка на панели открывает поле.
    const [composing, setComposing] = useState(false);
    useEffect(() => { setComposing(false); }, [day, recruiterId]);

    const params = useMemo(() => dayReportQuery(day, caps.reports ? recruiterId : null), [day, recruiterId, caps.reports]);
    useEffect(() => {
        let alive = true;
        setLoading(true);
        api.get('/reports/day', params)
            .then(({ data: payload }) => { if (alive) setData(payload); })
            .catch((error) => toast(errorOf(error, 'Отчёт не загрузился'), 'error'))
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [api, params, toast, version]);

    const download = async () => {
        try {
            const response = await api.blob('/reports/day/export', params);
            const header = response.headers?.['content-disposition'] || '';
            const match = /filename\*=UTF-8''([^;]+)/.exec(header);
            saveBlob(response.data, match ? decodeURIComponent(match[1]) : `Отчёт за день ${day}.xlsx`);
        } catch (error) {
            toast(errorOf(error, 'Выгрузка не удалась'), 'error');
        }
    };

    const onCell = (key, label) => setRequest({ key, label });
    const me = boot.me;
    const noteSaved = useCallback((body) => {
        setData((prev) => (prev ? { ...prev, notes: withMyNote(prev.notes, body, me) } : prev));
    }, [me]);
    const stopComposing = useCallback(() => setComposing(false), []);
    const recruiters = (boot.dictionaries?.recruiters || []).map((item) => ({ value: String(item.id), label: item.name }));
    const team = Boolean(data && !data.recruiter);
    const people = data?.people || [];
    const { mine, others } = splitNotes(data?.notes);
    const ownText = String(mine?.body || '');
    const showOwn = Boolean(data?.can_note && (composing || ownText.trim()));

    return (
        <div className="max-w-[1120px] space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                {switcher}
                <DaySwitch value={day} max={today} onChange={setDay} />
                {day !== today && <button type="button" className={iosBtnGhost} onClick={() => setDay(today)}>Сегодня</button>}
                {caps.reports && (
                    <CustomSelect variant="ios" value={recruiterId} onChange={setRecruiterId} className="w-56" ariaLabel="Рекрутер" searchable
                                  options={[{ value: '', label: 'Все рекрутеры' }, ...recruiters]} />
                )}
                <div className="ml-auto flex items-center gap-2">
                    {data?.can_note && !showOwn && (
                        <button type="button" className={TOOLBAR_BTN} onClick={() => setComposing(true)}>
                            <Plus size={14} /> Другая работа
                        </button>
                    )}
                    <button type="button" className={TOOLBAR_BTN} onClick={download}><Download size={14} /> Excel</button>
                </div>
            </div>

            {!data ? <Loading text="Считаем…" /> : (
                <section className={`${iosCard} transition-opacity ${loading ? 'opacity-60' : ''}`} aria-busy={loading}>
                    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-4 pb-2.5 pt-3.5 sm:px-5">
                        <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">Отчёт за {data.title}</h2>
                        <span className="text-[13px] tabular-nums text-slate-500">{scopeLabel(data)}</span>
                    </header>
                    <FunnelTable data={data} caps={caps} onCell={onCell} />

                    {team && people.length > 0 && (
                        <Section title="По рекрутерам">
                            <PeopleTable columns={data.result_columns} people={people} totals={data.totals} minWidth="min-w-[680px]"
                                         cell={(person, column, total) => {
                                             const value = person[column.key];
                                             const key = total ? totalKey(column.key) : personKey(person.id, column.key);
                                             const label = total ? column.label : `${person.name} · ${column.label}`;
                                             return <Num value={value} strong={column.key === 'done' || total} onOpen={value > 0 ? () => onCell(key, label) : null} />;
                                         }} />
                        </Section>
                    )}

                    <Section title="Эффективность">
                        <PeopleTable columns={data.efficiency_columns}
                                     people={people.length ? people : [{ ...data.totals, id: 'all', name: data.recruiter?.name || 'Все рекрутеры' }]}
                                     totals={data.totals} minWidth="min-w-[560px]"
                                     cell={(person, column, total) => {
                                         const value = person[column.key];
                                         if (column.key === 'avg_talk_sec' || column.key === 'talk_sec') {
                                             const clock = talkClock(value);
                                             return <span className={clock ? `px-1 text-slate-900 ${total ? 'font-semibold' : ''}` : 'px-1 text-slate-300'}>{clock || '—'}</span>;
                                         }
                                         return <Num value={value} strong={total} />;
                                     }} />
                    </Section>

                    {(showOwn || others.length > 0) ? (
                        <Section title="Другая работа за день">
                            <div className="space-y-2 px-4 pb-4 sm:px-5">
                                {showOwn && (
                                    <NoteEditor key={data.day} api={api} toast={toast} day={data.day} initial={ownText}
                                                name={team ? me?.name : ''} autoFocus={composing && !ownText.trim()}
                                                onSaved={noteSaved} onEmptyBlur={stopComposing} />
                                )}
                                {others.map((note) => (
                                    <div key={note.user_id} className="rounded-xl bg-slate-50 px-3.5 py-2.5">
                                        {team && <div className="text-[12px] font-medium text-slate-500">{note.name}</div>}
                                        <div className="mt-0.5 whitespace-pre-wrap break-words text-[13.5px] leading-snug text-slate-800">{note.body}</div>
                                    </div>
                                ))}
                            </div>
                        </Section>
                    ) : <div className="h-2" />}
                </section>
            )}

            <ReportCardsModal api={api} toast={toast} request={request} onClose={() => setRequest(null)} onOpenCard={onOpenCard}
                              path="/reports/day/cards" params={params} />
        </div>
    );
};

export default DayReport;
