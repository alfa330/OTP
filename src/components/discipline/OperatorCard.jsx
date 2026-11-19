import React, { useEffect, useRef, useState } from 'react';
import { ChevronRight, FileText, Plus, ShieldAlert } from 'lucide-react';
import { IosSegmented } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import { apiError } from './disciplineApi';
import {
    Chip, dBtnPrimary, dBtnSecondary, EmptyState, FileList, FileName, Group, LoadingRows, OVERDUE_ROW, StateBadge,
    StepChip, T,
} from './disciplineUi';
import { daysLeftText, fmtDate, fmtMoney, fmtShort, fmtTime, pluralViolations } from './disciplineRules';

/*
 * Карточка оператора (ТЗ п. 11): открывается кликом по ФИО из любого списка.
 * Шапка — ФИО, фото, группа и линия, дата приёма, руководитель и крупная
 * плашка: «На особом контроле до ДД.ММ» (красная) либо «Нарушений за период: N»
 * (нейтральная, если нарушений нет, иначе жёлтая). Ниже — вкладки «Нарушения»,
 * «Документы», «Меры», «Особый контроль». Табельного номера в портале нет —
 * его нет ни в одной карточке сотрудника, поэтому и здесь его не показываем.
 * Каждое открытие карточки пишется в журнал действий (п. 14).
 *
 * «Меры» — все применённые меры (п. 11): по записям, решением по акту и
 * решением по окончании особого контроля — тот же список, что идёт в выгрузку
 * на удержания.
 *
 * Карточка — уровень окна-стопки раздела (DisciplineView): useOperatorCard
 * отдаёт шапку, подвал и тело, окно одно на все уровни.
 */

const initials = (name) => String(name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase();

const measureValue = (row) => (row.measure_amount ? fmtMoney(row.measure_amount)
    : row.measure_percent ? `${Number(row.measure_percent)} %` : null);

const currentFile = (file) => !file.superseded && !file.invalid;

/* Файлы записи для раскрытой строки (п. 11): действующие версии объяснительной
   (или акта об отказе) и подписанного акта. Документы карточки приходят одним
   списком — свои у записи по record_id, у акта по act_id. */
export const recordFiles = (item, documents = []) => {
    const own = documents.filter((f) => currentFile(f) && f.record_id === item.id
        && (f.kind === 'explanation' || f.kind === 'refusal_act'));
    const actId = item.act?.id;
    const act = actId ? documents.filter((f) => currentFile(f) && f.act_id === actId && f.kind === 'act') : [];
    return [...own, ...act];
};

/* Запись во вкладке «Нарушения» (п. 11): «каждая запись сворачиваемая: дата,
   тип, кто зафиксировал, статус, ссылки на файлы объяснительной и акта».
   Свёрнутая — дата, тип и ступень, как строка журнала; раскрытая — остальное и
   переход к самой записи. */
const CardRecord = ({ item, documents, internal, onOpenRecord, onOpenFile }) => {
    const [open, setOpen] = useState(false);
    const cancelled = item.state === 'cancelled';
    const files = internal ? recordFiles(item, documents) : [];
    return (
        <li>
            <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100 xl:px-5 2xl:py-3.5 ${item.explanation_overdue ? OVERDUE_ROW : ''}`}>
                <span className={`w-[50px] shrink-0 text-[12.5px] tabular-nums text-slate-500 xl:w-[64px] ${T.sub}`}>
                    {fmtShort(item.occurred_at)}
                    <span className="block text-[11px] text-slate-400 2xl:text-[12px]">{fmtTime(item.occurred_at)}</span>
                </span>
                <span className={`min-w-0 flex-1 truncate text-[13px] text-slate-800 ${T.sub} ${cancelled ? 'line-through opacity-60' : ''}`}>
                    {item.type_title}
                    {item.explanation_overdue ? <span className="ml-1.5 font-medium text-rose-600">· срок объяснительной прошёл</span> : null}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                    {item.under_control && !cancelled ? <Chip tone="red">Контроль</Chip> : null}
                    <StepChip item={item} />
                </span>
                <ChevronRight size={15} className={`shrink-0 text-slate-300 transition-transform ${open ? 'rotate-90' : ''}`} />
            </button>
            {open && (
                <div className="space-y-2.5 px-4 pb-3.5 xl:px-5 sm:pl-[78px] xl:pl-[96px]">
                    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px] text-slate-600 ${T.sub}`}>
                        <StateBadge state={item.state} />
                        <span className="tabular-nums text-slate-400">{item.number}</span>
                        {internal && item.created_by_name ? <span>Зафиксировал {item.created_by_name}</span> : null}
                    </div>
                    {files.length > 0 && (
                        <ul className="flex flex-wrap gap-1.5">
                            {files.map((file) => (
                                <li key={file.id} className="min-w-0 max-w-full">
                                    <button type="button" onClick={() => onOpenFile?.(file)}
                                            className="flex min-w-0 max-w-full items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-slate-700 hover:bg-slate-200">
                                        <FileText size={12} className="shrink-0 text-slate-400" />
                                        <span className="shrink-0 text-slate-500">{file.kind_label}:</span>
                                        <FileName name={file.name} className="min-w-0" />
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                    <button type="button" className={`${dBtnSecondary} !py-1.5 !text-[12.5px]`} onClick={() => onOpenRecord(item.id)}>
                        Открыть запись
                    </button>
                </div>
            )}
        </li>
    );
};

export const useOperatorCard = ({ operatorId, open = true, api, version, onOpenRecord, onOpenFile, onCreate }) => {
    const mobile = useIsMobileShell();
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [tab, setTab] = useState('records');

    // Другой сотрудник — с первой вкладки; уход на уровень записи карточку не
    // сбрасывает: «назад» вернёт её сразу.
    const lastOperator = useRef(null);
    useEffect(() => { if (!open) lastOperator.current = null; }, [open]);
    useEffect(() => {
        if (!operatorId || lastOperator.current === operatorId) return;
        lastOperator.current = operatorId;
        setData((prev) => (prev && prev.operator?.id === operatorId ? prev : null));
        setError(null);
        setTab('records');
    }, [operatorId]);

    useEffect(() => {
        if (!operatorId) return undefined;
        let alive = true;
        api.operatorCard(operatorId)
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Карточка не открылась')); });
        return () => { alive = false; };
    }, [api, operatorId, version]);

    // Пока ответ по новому сотруднику не пришёл, прежнего не показываем.
    const shown = data && data.operator?.id === operatorId ? data : null;
    const operator = shown?.operator;
    const period = shown?.period;
    const measures = shown?.measures || [];
    const footer = shown && onCreate ? (
        <div className="flex justify-end">
            <button type="button" className={dBtnPrimary} onClick={() => onCreate({ id: operator.id, name: operator.name })}>
                <Plus size={16} />
                Зафиксировать нарушение
            </button>
        </div>
    ) : null;

    const body = error ? <EmptyState title={error} /> : !shown ? <LoadingRows rows={5} /> : (
        <div key={operator.id} className="space-y-4 xl:space-y-5">
            <div className="flex items-start gap-4">
                {operator.photo ? (
                    <img src={operator.photo} alt="" className="h-16 w-16 shrink-0 rounded-full object-cover ring-1 ring-slate-200 xl:h-20 xl:w-20" />
                ) : (
                    <span className="grid h-16 w-16 shrink-0 place-items-center rounded-full bg-slate-200 text-[18px] font-semibold text-slate-600 xl:h-20 xl:w-20">
                        {initials(operator.name)}
                    </span>
                )}
                <div className="min-w-0 flex-1 space-y-1">
                    <div className={`text-[13px] text-slate-600 ${T.sub}`}>
                        {[operator.role === 'trainee' ? 'Стажёр' : 'Оператор', operator.group_name, operator.direction_name]
                            .filter(Boolean).join(' · ')}
                    </div>
                    <div className={`text-[12.5px] text-slate-500 ${T.note}`}>
                        {[operator.hire_date ? `Принят ${fmtDate(operator.hire_date)}` : null,
                            operator.supervisor_name ? `Руководитель: ${operator.supervisor_name}` : null].filter(Boolean).join(' · ')}
                    </div>
                </div>
            </div>

            {shown.control ? (
                <div className="flex items-start gap-3 rounded-2xl border-l-4 border-rose-500 bg-rose-50 px-4 py-3 ring-1 ring-rose-200/70">
                    <ShieldAlert size={18} className="mt-0.5 shrink-0 text-rose-600" />
                    <div>
                        <div className={`text-[15px] font-semibold text-rose-900 ${T.body}`}>На особом контроле до {fmtDate(shown.control.end_date)}</div>
                        <div className={`text-[12.5px] text-rose-800/90 ${T.sub}`}>{shown.control.source_label}: {shown.control.reason}</div>
                    </div>
                </div>
            ) : (
                <div className={`rounded-2xl px-4 py-3 ring-1 ${period?.count ? 'bg-amber-50 text-amber-900 ring-amber-200/70' : 'bg-slate-50 text-slate-700 ring-slate-200/70'}`}>
                    <div className={`text-[15px] font-semibold ${T.body}`}>
                        Нарушений за период: <span className="tabular-nums">{period?.count ?? 0}</span>
                    </div>
                    <div className={`text-[12.5px] opacity-80 ${T.sub}`}>
                        {period?.kind === 'month' ? 'С начала месяца' : `За ${period?.days || 30} дней`}
                        {period?.next_title ? ` · следующее — ${period.next_title.toLowerCase()}` : ''}
                    </div>
                </div>
            )}

            <IosSegmented value={tab} onChange={setTab} stretch slide ariaLabel="Вкладки карточки"
                          options={[
                              { value: 'records', label: 'Нарушения', count: (shown.records || []).length },
                              { value: 'documents', label: mobile ? 'Файлы' : 'Документы', count: (shown.documents || []).length },
                              { value: 'measures', label: 'Меры', count: measures.length },
                              // На телефоне четыре вкладки входят в ширину только с короткой подписью.
                              { value: 'controls', label: mobile ? 'Контроль' : 'Особый контроль', count: (shown.controls || []).length },
                          ]} />

            {tab === 'records' && (
                <Group cardClassName="p-0 overflow-hidden">
                    {(shown.records || []).length ? (
                        <ul className="divide-y divide-slate-100">
                            {shown.records.map((item) => (
                                <CardRecord key={item.id} item={item} documents={shown.documents || []}
                                            internal={'description' in item} onOpenRecord={onOpenRecord} onOpenFile={onOpenFile} />
                            ))}
                        </ul>
                    ) : <EmptyState title="Нарушений нет" />}
                </Group>
            )}
            {tab === 'documents' && (
                <Group>
                    <FileList files={shown.documents || []} onOpen={onOpenFile} emptyText="Документов нет" />
                </Group>
            )}
            {tab === 'measures' && (
                <Group cardClassName="p-0 overflow-hidden">
                    {measures.length ? (
                        <ul className="divide-y divide-slate-100">
                            {measures.map((row, index) => {
                                const value = measureValue(row);
                                const content = (
                                    <>
                                        <span className="min-w-0 flex-1">
                                            <span className={`block truncate text-[13.5px] font-medium text-slate-900 ${T.body}`}>{row.measure_title}</span>
                                            <span className={`block truncate text-[12px] text-slate-500 ${T.note}`}>
                                                {[fmtDate(row.approved_at), row.basis, row.approved_by].filter(Boolean).join(' · ')}
                                            </span>
                                        </span>
                                        {value ? <span className="shrink-0 text-[13px] tabular-nums text-slate-700">{value}</span> : null}
                                    </>
                                );
                                return (
                                    <li key={`${row.basis}-${index}`}>
                                        {row.record_id ? (
                                            <button type="button" onClick={() => onOpenRecord(row.record_id)}
                                                    className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50">
                                                {content}
                                            </button>
                                        ) : <div className="flex items-center gap-3 px-4 py-3">{content}</div>}
                                    </li>
                                );
                            })}
                        </ul>
                    ) : <EmptyState title="Мер не применялось" />}
                </Group>
            )}
            {tab === 'controls' && (
                <Group cardClassName="p-0 overflow-hidden">
                    {(shown.controls || []).length ? (
                        <ul className="divide-y divide-slate-100">
                            {shown.controls.map((row) => (
                                <li key={row.id} className="space-y-0.5 px-4 py-3">
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <span className={`text-[13.5px] font-medium text-slate-900 ${T.body}`}>{row.source_label}</span>
                                        {row.decided_at
                                            ? <Chip tone="slate">{row.decision_label}</Chip>
                                            : <Chip tone={row.tone === 'red' ? 'red' : 'amber'}>{daysLeftText(row.days_left)}</Chip>}
                                    </div>
                                    <div className={`text-[12.5px] text-slate-600 ${T.sub}`}>{row.reason}</div>
                                    <div className={`text-[12px] tabular-nums text-slate-400 ${T.note}`}>
                                        {fmtDate(row.start_date)} – {fmtDate(row.end_date)}
                                        {row.violations ? ` · ${row.violations} ${pluralViolations(row.violations)} в период` : ''}
                                        {row.decided_at ? ` · ${row.decided_by_name || ''}: ${row.decision_detail || row.decision_label}` : ''}
                                    </div>
                                </li>
                            ))}
                        </ul>
                    ) : <EmptyState title="На контроле не был" />}
                </Group>
            )}
        </div>
    );

    return {
        title: operator?.name || 'Сотрудник',
        subtitle: operator?.department_name || '',
        footer,
        body,
        sizeKey: shown ? `ready-${tab}` : (error ? 'error' : 'loading'),
    };
};
