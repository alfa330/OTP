import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, History, Loader2, Lock, Printer, Unlock } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import { blobErrorText, cellTone, dateText, errorText, monthBounds, monthLabel, shiftMonth, todayIso } from './hrOffice';
import { Empty, FIELD, Loading, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY, useSheetState } from './hrUi';
import HrJournalSheet from './HrJournalSheet';

/*
 * Табель учёта рабочего времени (ТЗ п. 6–10). Ячейка — как в табеле HR: часы по графику
 * у «Работает», «В» — выходной или праздник, «О»/«А»/«Б» — по статусу. Под ячейкой —
 * минуты опоздания по «Отметкам» (со знаком минус, как в образце), переработка за месяц —
 * в комментарии. Любую ячейку HR правит руками (правка помечена точкой и главнее расчёта),
 * закрытый месяц отдаётся из снимка и не меняется.
 */
const TONE = {
    work: 'text-[12.5px] font-medium text-slate-700',
    off: 'text-[11.5px] text-slate-300',
    leave: 'rounded-md bg-emerald-50 px-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-100',
    unpaid: 'rounded-md bg-amber-50 px-1 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-100',
    sick: 'rounded-md bg-sky-50 px-1 text-[11px] font-semibold text-sky-700 ring-1 ring-sky-100',
    empty: 'text-[11px] text-slate-200',
};
const QUICK = ['8', 'В', 'О', 'А', 'Б'];

function defaultMonth() {
    const today = todayIso();
    // В первые дни месяца табель собирают за прошлый.
    return Number(today.slice(8, 10)) <= 10 ? shiftMonth(today.slice(0, 7), -1) : today.slice(0, 7);
}

function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

/* Печать — отдельной страницей с одной таблицей: печатать весь портал с меню незачем. */
function printModel(model, people) {
    const win = window.open('', '_blank');
    if (!win) return false;
    const days = model.days;
    const head = days.map((day) => `<th class="${day.weekend || day.holiday ? 'we' : ''}">${day.day}<br>${day.weekday}</th>`).join('');
    let number = 0;
    const body = model.groups.map((group) => {
        const rows = group.people.filter((person) => people.has(person.user_id)).map((person) => {
            number += 1;
            const top = person.cells.map((cell, index) => `<td class="${days[index].weekend || days[index].holiday ? 'we' : ''} c-${escapeHtml(cell.v)}">${escapeHtml(cell.v)}</td>`).join('');
            const bottom = person.cells.map((cell) => `<td class="m">${cell.m === null || cell.m === undefined ? '' : escapeHtml(cell.m)}</td>`).join('');
            return `<tr><td rowspan="2">${number}</td><td rowspan="2" class="l">${escapeHtml(person.job_title)}</td><td rowspan="2" class="l">${escapeHtml(person.name)}</td>`
                + `<td rowspan="2" class="l">${escapeHtml(person.contract_label)}</td><td rowspan="2">${escapeHtml(person.schedule)}</td>`
                + `<td rowspan="2">${person.plan_days}</td><td rowspan="2">${person.plan_hours}</td>${top}`
                + `<td rowspan="2">${person.totals.shifts}</td><td rowspan="2">${person.totals.hours}</td><td rowspan="2">${person.totals.absences}</td>`
                + `<td rowspan="2">${person.totals.minutes}</td><td rowspan="2" class="l">${escapeHtml(person.note || person.note_auto)}</td></tr><tr>${bottom}</tr>`;
        }).join('');
        return rows ? `<tr><td colspan="${days.length + 12}" class="g">${escapeHtml(group.name)}</td></tr>${rows}` : '';
    }).join('');
    win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Табель ${escapeHtml(model.project)} ${escapeHtml(model.title)}</title>
<style>@page{size:A4 landscape;margin:8mm}body{font:9px -apple-system,Segoe UI,Arial,sans-serif;color:#111}
h1{font-size:13px;margin:0 0 6px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:1px 2px;text-align:center}
th{font-weight:600;background:#f3f3f3}.we{background:#f2f2f2}.l{text-align:left}.m{color:#c00;font-size:8px}.g{text-align:left;font-weight:700;background:#fafafa}
.c-О{background:#92d050}.c-А{background:#ffff00}.c-Б{background:#f4b6b6}</style></head><body>
<h1>Табель учёта рабочего времени · ${escapeHtml(model.project)} · ${escapeHtml(model.title)}</h1>
<table><thead><tr><th>№</th><th>Должность</th><th>Ф.И.О</th><th>ТД/ГПХ</th><th>График</th><th>План дн.</th><th>План ч</th>${head}
<th>Смен</th><th>Часов</th><th>Пропусков</th><th>Мин.</th><th>Комментарий</th></tr></thead><tbody>${body}</tbody></table>
<p>В — выходной/праздник · О — отпуск · А — без содержания / приостановление · Б — больничный · под ячейкой — минуты опоздания</p>
<script>window.onload=()=>{window.print()}</script></body></html>`);
    win.document.close();
    return true;
}

function CellEditor({ open = true, api, toast, target, onClose, onSaved }) {
    const { person, day, cell, month } = target;
    const [value, setValue] = useState(cell.v ?? '');
    const [minutes, setMinutes] = useState(cell.m === null || cell.m === undefined ? '' : String(cell.m));
    const [busy, setBusy] = useState(false);
    const changedValue = String(value) !== String(cell.v ?? '');
    const changedMinutes = minutes !== (cell.m === null || cell.m === undefined ? '' : String(cell.m));

    const send = async (body) => api.put('/timesheet/cell', { user_id: person.user_id, day: day.date, ...body });
    const save = async () => {
        setBusy(true);
        try {
            if (changedValue) await send({ field: 'value', value, computed: cell.cv });
            if (changedMinutes) await send({ field: 'minutes', value: minutes, computed: cell.cm });
            onSaved();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
            setBusy(false);
        }
    };
    const reset = async () => {
        setBusy(true);
        try {
            await send({ field: 'value', reset: true, computed: cell.cv });
            await send({ field: 'minutes', reset: true, computed: cell.cm });
            onSaved();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не получилось'), 'error');
            setBusy(false);
        }
    };
    const computedText = [cell.cv || 'пусто', cell.cm ? `${cell.cm} мин` : ''].filter(Boolean).join(' · ');
    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-sm" title={person.name}
                  subtitle={`${dateText(day.date)} · ${day.weekday}${month ? '' : ''}`}
                  footer={(
                      <div className="flex items-center justify-between gap-2">
                          {cell.edited
                              ? <button type="button" className={iosBtnGhost} onClick={reset} disabled={busy}>Вернуть расчёт</button>
                              : <span />}
                          <div className="flex gap-2">
                              <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>Отмена</button>
                              <button type="button" className={iosBtnPrimary} onClick={save} disabled={busy || (!changedValue && !changedMinutes)}>
                                  {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                              </button>
                          </div>
                      </div>
                  )}>
            <div className="space-y-3">
                <div className="space-y-1">
                    <div className="px-1 text-[12px] font-medium text-slate-500">Значение дня</div>
                    <div className="flex gap-1.5">
                        {QUICK.map((item) => (
                            <button key={item} type="button" onClick={() => setValue(item)}
                                    className={`h-9 flex-1 rounded-xl text-[13.5px] font-semibold transition ${String(value) === item ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}>
                                {item}
                            </button>
                        ))}
                    </div>
                    <input className={FIELD} value={value} onChange={(event) => setValue(event.target.value)}
                           placeholder="Часы числом или буква" aria-label="Значение дня" />
                </div>
                <div className="space-y-1">
                    <div className="px-1 text-[12px] font-medium text-slate-500">Под ячейкой</div>
                    <input className={FIELD} value={minutes} onChange={(event) => setMinutes(event.target.value)}
                           placeholder="Минуты: −15 опоздание, +40 переработка" aria-label="Минуты" />
                </div>
                <div className="px-1 text-[12px] text-slate-500">По расчёту: {computedText}</div>
                {cell.edited && (cell.edited_by || cell.edited_at) && (
                    <div className="px-1 text-[12px] text-slate-500">
                        Изменено: {[cell.edited_by, dateText(cell.edited_at)].filter(Boolean).join(' · ')}
                        {cell.edited_at ? ` ${String(cell.edited_at).slice(11, 16)}` : ''}
                    </div>
                )}
            </div>
        </IosModal>
    );
}

function NoteEditor({ open = true, api, toast, target, onClose, onSaved }) {
    const { person, month } = target;
    const [note, setNote] = useState(person.note || '');
    const [busy, setBusy] = useState(false);
    const save = async () => {
        setBusy(true);
        try {
            await api.put('/timesheet/note', { user_id: person.user_id, month, note });
            onSaved();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
            setBusy(false);
        }
    };
    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-sm" title="Комментарий" subtitle={person.name}
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={save} disabled={busy}>Сохранить</button>
                      </div>
                  )}>
            <textarea className={`${FIELD} min-h-[90px]`} value={note} onChange={(event) => setNote(event.target.value)}
                      placeholder={person.note_auto || 'Например, переработка или причина неявки'} maxLength={300} />
            {person.note_auto && <div className="mt-2 px-1 text-[12px] text-slate-500">Без своего текста: {person.note_auto}</div>}
        </IosModal>
    );
}

export default function HrTimesheetTab({ api, boot, toast }) {
    const [month, setMonth] = useState(defaultMonth);
    const [project, setProject] = useState('ЦО');
    const [until, setUntil] = useState('');
    const [chosen, setChosen] = useState([]);
    const [model, setModel] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    // Окна держатся в дереве, пока доигрывают уход (hrUi.useSheetState): закрываются плавно.
    const [editCell, keptCell, openCell, closeCell, , cellToken] = useSheetState(null);
    const [editNote, keptNote, openNote, closeNote, , noteToken] = useSheetState(null);
    const [confirmClose, setConfirmClose] = useState(false);
    const [journal, keptJournal, openJournal, closeJournal, , journalToken] = useSheetState(false);

    const params = useMemo(() => ({ month, project, ...(until ? { until } : {}) }), [month, project, until]);
    // Ответ применяется, только если он на последний запрос: быстрое листание месяцев иначе
    // ставило бы под «Сентябрём» октябрьскую таблицу, и правки уходили бы не в тот месяц.
    const requestRef = useRef(0);
    const load = useCallback(() => {
        const request = requestRef.current + 1;
        requestRef.current = request;
        setLoading(true);
        api.get('/timesheet', params)
            .then(({ data }) => { if (requestRef.current === request) { setModel(data); setError(''); } })
            .catch((requestError) => {
                if (requestRef.current !== request) return;
                setModel(null);
                setError(errorText(requestError, 'Табель не собрался'));
            })
            .finally(() => { if (requestRef.current === request) setLoading(false); });
    }, [api, params]);
    useEffect(() => { load(); }, [load]);
    // Месяц и проект меняются вместе с отбором людей и датой «по» — одним обновлением, чтобы
    // не уходил запрос со старой датой «по», которая уже вне нового месяца.
    const changeMonth = (delta) => { setChosen([]); setUntil(''); setMonth((value) => shiftMonth(value, delta)); };
    const changeProject = (value) => { setChosen([]); setUntil(''); setProject(value); };

    const everyone = useMemo(() => (model?.groups || []).flatMap((group) => group.people), [model]);
    const shown = useMemo(() => new Set(chosen.length ? chosen : everyone.map((person) => person.user_id)), [chosen, everyone]);
    const bounds = monthBounds(month);
    const closed = model?.closed;
    /* Пока собирается другой месяц или проект, на экране ещё прежняя таблица: править её ячейки,
       комментарии и закрывать месяц нельзя — правка ушла бы в новый месяц, а «Закрыть» решало бы
       по чужому «закрыт / не закрыт». Действия берут месяц и проект из самой таблицы. */
    const stale = loading || !model || model.month !== month || model.project !== project;

    const exportExcel = async () => {
        setBusy('excel');
        try {
            const query = new URLSearchParams(params);
            chosen.forEach((id) => query.append('user_id', id));
            await api.download('get', `/timesheet/export?${query.toString()}`, { fallback: `Табель ${project} ${monthLabel(month)}.xlsx` });
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Файл не собрался'), 'error');
        } finally {
            setBusy('');
        }
    };
    const toggleClose = async () => {
        setBusy('close');
        try {
            if (closed) await api.del('/timesheet/close', { month: model.month, project: model.project });
            else await api.post('/timesheet/close', { month: model.month, project: model.project });
            setConfirmClose(false);
            load();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy('');
        }
    };

    const projectOptions = (boot.projects || []).map((item) => ({ value: item, label: item }));
    const peopleOptions = everyone.map((person) => ({ value: person.user_id, label: person.name, meta: person.job_title }));
    const legend = (boot.legend || []).map((item) => `${item.code} — ${item.text}`).join('\n')
        + '\nПод ячейкой — минуты опоздания по «Отметкам». Точка — ручная правка HR; янтарная — в «Отметках» нет отметок.';

    return (
        /* Столбик — flex/gap, а не space-y: окна ниже — его дети, и space-y дал бы им margin-top —
           затемнение начиналось бы на 12 px ниже верха экрана, над ним белая полоса. */
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="flex h-9 items-center rounded-xl bg-white ring-1 ring-slate-200/70">
                    <button type="button" className="grid h-9 w-9 place-items-center text-slate-500 hover:text-slate-800"
                            onClick={() => changeMonth(-1)} aria-label="Прошлый месяц">
                        <ChevronLeft size={16} />
                    </button>
                    <span className="min-w-[124px] text-center text-[13.5px] font-medium text-slate-800">{monthLabel(month)}</span>
                    <button type="button" className="grid h-9 w-9 place-items-center text-slate-500 hover:text-slate-800"
                            onClick={() => changeMonth(1)} aria-label="Следующий месяц">
                        <ChevronRight size={16} />
                    </button>
                </div>
                <CustomSelect variant="ios" value={project} onChange={changeProject} options={projectOptions} className="w-40" ariaLabel="Проект" />
                <IosDatePicker value={until} onChange={setUntil} min={bounds.from} max={bounds.to} allowEmpty
                               placeholder="По конец месяца" ariaLabel="По какую дату" className="w-48" />
                <CustomSelect variant="ios" multiple value={chosen} onChange={setChosen} options={peopleOptions}
                              placeholder="Все сотрудники" searchable bulkActions className="w-56" ariaLabel="Сотрудники"
                              renderValue={(values) => (values.length ? `Выбрано: ${values.length}` : 'Все сотрудники')} />
                <InfoHint title="Обозначения" text={legend} />
                <div className="ml-auto flex flex-wrap gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => model && !printModel(model, shown) && toast?.('Разрешите всплывающие окна для печати', 'warning')}
                            disabled={!model}>
                        <Printer size={15} /> Печать
                    </button>
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openJournal(true)}>
                        <History size={15} /> Журнал
                    </button>
                    <button type="button" className={TOOLBAR_BTN} onClick={exportExcel} disabled={!model || busy === 'excel'}>
                        {busy === 'excel' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />} Excel
                    </button>
                    {!chosen.length && (
                        <button type="button" className={closed ? TOOLBAR_BTN : TOOLBAR_BTN_PRIMARY}
                                onClick={() => (closed ? toggleClose() : setConfirmClose(true))} disabled={!model || stale || busy === 'close'}>
                            {closed ? <Unlock size={15} /> : <Lock size={15} />} {closed ? 'Открыть заново' : 'Закрыть месяц'}
                        </button>
                    )}
                </div>
            </div>

            {closed && (
                <div className={`${iosCard} px-4 py-2.5 text-[13px] text-slate-600`}>
                    Табель закрыт{closed.by ? ` · ${closed.by}` : ''}{closed.at ? ` · ${dateText(closed.at)}` : ''}: показан снимок, правки выключены.
                </div>
            )}
            {model && model.facts_ok === false && (
                <div className={`${iosCard} px-4 py-2.5 text-[13px] text-amber-700`}>
                    «Отметки» сейчас не прочитались — опозданий и личных графиков в табеле нет. Закрыть месяц можно, когда они загрузятся.
                </div>
            )}
            {model?.pending_days?.length > 0 && (
                <div className={`${iosCard} px-4 py-2.5 text-[13px] text-amber-700`}>
                    «Отметки» ещё не собрали {model.pending_days.length} дн. — опоздания за них появятся позже.
                </div>
            )}
            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!model && loading && <Loading text="Собираем табель…" />}
            {model && (
                /* Другой месяц или проект — таблица проявляется заново (key), а не подменяется за кадр;
                   пока грузится — прежняя чуть гаснет. */
                <div key={`${model.month}|${model.project}`}
                     className={`${iosCard} relative overflow-hidden transition-opacity duration-200 motion-safe:animate-fade-in-soft ${loading ? 'opacity-60' : ''}`}>
                    <div className="max-h-[calc(100vh-230px)] overflow-auto">
                        <table className="border-separate border-spacing-0 text-left">
                            <thead className="sticky top-0 z-20 bg-white">
                                <tr className="text-[11px] font-semibold text-slate-500">
                                    <th className="sticky left-0 z-30 min-w-[230px] border-b border-r border-slate-100 bg-white px-3 py-2">Сотрудник</th>
                                    <th className="border-b border-slate-100 px-2 py-2 text-center" title="Плановых рабочих дней / часов">План</th>
                                    {model.days.map((day) => (
                                        <th key={day.date} className={`min-w-[40px] border-b border-l border-slate-100 px-0.5 py-1.5 text-center ${day.weekend || day.holiday ? 'bg-slate-50 text-slate-400' : ''}`}
                                            title={day.holiday ? 'Праздник' : undefined}>
                                            <div className={`text-[12.5px] ${day.holiday ? 'text-rose-400' : 'text-slate-700'}`}>{day.day}</div>
                                            <div className="text-[10px] font-medium">{day.weekday}</div>
                                        </th>
                                    ))}
                                    {['Смен', 'Часов', 'Пропуск.', 'Минут', 'Комментарий'].map((label) => (
                                        <th key={label} className="border-b border-l border-slate-100 px-2 py-2 text-center">{label}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {model.groups.map((group) => {
                                    const people = group.people.filter((person) => shown.has(person.user_id));
                                    if (!people.length) return null;
                                    return (
                                        <React.Fragment key={group.name}>
                                            <tr>
                                                <td colSpan={model.days.length + 7}
                                                    className="sticky left-0 border-b border-slate-100 bg-slate-50/80 px-3 py-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-slate-500">
                                                    {group.name} · {people.length}
                                                </td>
                                            </tr>
                                            {people.map((person) => (
                                                <tr key={person.user_id} className="align-top">
                                                    <td className="sticky left-0 z-10 border-b border-r border-slate-100 bg-white px-3 py-2">
                                                        <div className="max-w-[230px] truncate text-[13px] font-medium text-slate-900">{person.name}</div>
                                                        <div className="max-w-[230px] truncate text-[11.5px] text-slate-500">
                                                            {[person.job_title, person.contract_label].filter(Boolean).join(' · ')}
                                                        </div>
                                                    </td>
                                                    <td className="whitespace-nowrap border-b border-slate-100 px-2 py-2 text-center text-[12px] text-slate-600">
                                                        <div>{person.plan_days} дн</div>
                                                        <div className="text-[11px] text-slate-400">{person.plan_hours} ч · {person.schedule || '—'}</div>
                                                    </td>
                                                    {person.cells.map((cell, index) => {
                                                        const day = model.days[index];
                                                        const tone = cellTone(cell.v);
                                                        const clickable = !closed && !stale;
                                                        return (
                                                            <td key={day.date}
                                                                className={`relative border-b border-l border-slate-100 p-0 text-center ${day.weekend || day.holiday ? 'bg-slate-50/70' : ''}`}>
                                                                <button type="button" disabled={!clickable}
                                                                        onClick={() => openCell({ person, day, cell, month: model.month })}
                                                                        className={`flex h-full min-h-[40px] w-full flex-col items-center justify-center gap-0.5 leading-none ${clickable ? 'hover:bg-blue-50/60' : 'cursor-default'}`}>
                                                                    {cell.edited && <span className="absolute left-1 top-1 h-1.5 w-1.5 rounded-full bg-blue-500" />}
                                                                    {cell.no_marks && !cell.edited && <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-400" title="Нет отметок в «Отметках»" />}
                                                                    <span className={TONE[tone]}>{cell.v || '·'}</span>
                                                                    {cell.m !== null && cell.m !== undefined && cell.m !== '' && (
                                                                        <span className={`text-[10.5px] tabular-nums ${typeof cell.m === 'number' && cell.m < 0 ? 'text-rose-500' : 'text-slate-500'}`}>{cell.m}</span>
                                                                    )}
                                                                </button>
                                                            </td>
                                                        );
                                                    })}
                                                    <td className="border-b border-l border-slate-100 px-2 py-2 text-center text-[12.5px] font-medium text-slate-800">{person.totals.shifts}</td>
                                                    <td className="border-b border-l border-slate-100 px-2 py-2 text-center text-[12.5px] text-slate-700">{person.totals.hours}</td>
                                                    <td className={`border-b border-l border-slate-100 px-2 py-2 text-center text-[12.5px] ${person.totals.absences ? 'text-slate-800' : 'text-slate-300'}`}>{person.totals.absences}</td>
                                                    <td className={`border-b border-l border-slate-100 px-2 py-2 text-center text-[12.5px] tabular-nums ${person.totals.minutes < 0 ? 'font-semibold text-rose-500' : 'text-slate-300'}`}>{person.totals.minutes}</td>
                                                    <td className="border-b border-l border-slate-100 p-0">
                                                        <button type="button" disabled={!!closed || stale} onClick={() => openNote({ person, month: model.month })}
                                                                className="block h-full min-h-[40px] w-full min-w-[160px] max-w-[220px] px-2 py-2 text-left text-[12px] text-slate-600 hover:bg-blue-50/60 disabled:hover:bg-transparent">
                                                            <span className="line-clamp-2">{person.note || <span className="text-slate-400">{person.note_auto}</span>}</span>
                                                        </button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                        {everyone.length === 0 && <Empty>В проекте «{project}» за этот месяц никого нет</Empty>}
                    </div>
                </div>
            )}

            {keptCell && (
                <CellEditor key={cellToken} open={editCell === keptCell} api={api} toast={toast} target={keptCell} onClose={closeCell}
                            onSaved={() => { closeCell(); load(); }} />
            )}
            {keptNote && (
                <NoteEditor key={noteToken} open={editNote === keptNote} api={api} toast={toast} target={keptNote} onClose={closeNote}
                            onSaved={() => { closeNote(); load(); }} />
            )}
            {keptJournal && (
                <HrJournalSheet key={journalToken} open={journal} api={api} section="timesheet" today={boot.today} onClose={closeJournal} />
            )}
            {/* Подтверждение всегда в дереве: IosModal сам проявляет его и гасит по open. */}
            <IosModal open={confirmClose} onClose={() => setConfirmClose(false)} maxWidth="max-w-sm" title="Закрыть табель?"
                      subtitle={`${project} · ${monthLabel(month)}`}
                      footer={(
                          <div className="flex justify-end gap-2">
                              <button type="button" className={iosBtnSecondary} onClick={() => setConfirmClose(false)}>Отмена</button>
                              <button type="button" className={iosBtnPrimary} onClick={toggleClose} disabled={busy === 'close'}>
                                  {busy === 'close' && <Loader2 size={14} className="animate-spin" />} Закрыть
                              </button>
                          </div>
                      )}>
                <div className="text-[13.5px] text-slate-600">
                    Табель сохранится снимком: поздние отметки и статусы его не изменят, правки выключатся.
                    Открыть заново можно в любой момент.
                </div>
            </IosModal>
        </div>
    );
}
