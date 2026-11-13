import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { FileSpreadsheet, Loader2, Search, Settings2, Shirt, SlidersHorizontal, Trash2 } from 'lucide-react';
import {
    IosBadge, IosHint, IosLightbox, IosModal, IosSegmented,
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import {
    AI_LABELS, DECISION_LABELS, QUALITY_LABELS, REVIEW_FILTERS, aiTone, decisionTone, filterParams,
    fmtDay, fmtDayFull, fmtTime, ofTotal,
} from './dressCode';

/*
 * Вкладка «Дресс-код» в «Отметках» (ТЗ #418, п. 6–8): фото сотрудников, вывод ИИ и
 * решение руководителя. Вход — тот же, что у раздела: глава отдела видит своих,
 * администраторы и отдел кадров — всех.
 *
 * По умолчанию — очередь «ждут решения»: ради неё сюда и заходят. Предварительный
 * вывод ИИ и итог руководителя — разными бейджами и разными числами сводки (ТЗ п. 5,
 * 7: не смешивать). Каждое решение пишется в журнал, вывод ИИ при этом не меняется.
 *
 * Суперадмину сверх этого (сервер говорит is_super_admin): «Стенд камеры» — проверить и
 * настроить автоснимок (DressCodeStand.jsx), и «Удалить» в карточке фото — убрать пробные
 * снимки совсем.
 */

// Стенд тянет за собой камеру и нейросеть — грузится, только когда его открыли.
const DressCodeStand = lazy(() => import('./DressCodeStand'));

const errText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;
const asArray = (value) => (Array.isArray(value) ? value : []);
/* Выбранное в кнопке — названиями, а не «Выбрано: 2»: так видно, кто снимает фото. */
const namesOf = (options, values) => {
    const labels = new Map(options.map((option) => [String(option.value), option.label]));
    return asArray(values).map((value) => labels.get(String(value))).filter(Boolean).join(', ') || 'Никто';
};

const DECISION_OPTIONS = [
    { value: 'confirmed', label: 'Нарушение' },
    { value: 'no_violation', label: 'Нарушения нет' },
    { value: 'retake', label: 'Переснять' },
];

const Tile = ({ label, value, hint }) => (
    <div className={`${iosCard} min-w-0 px-3.5 py-2.5`}>
        <div className="truncate text-[11.5px] font-medium text-slate-500">{label}</div>
        <div className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-[19px] font-semibold tabular-nums text-slate-900">{value}</span>
            {hint && <span className="truncate text-[12px] tabular-nums text-slate-400">{hint}</span>}
        </div>
    </div>
);

const DetailRow = ({ label, children }) => (
    <div className="flex items-start justify-between gap-4 px-4 py-2.5">
        <span className="shrink-0 text-[13px] text-slate-500">{label}</span>
        <span className="min-w-0 text-right text-[13.5px] text-slate-900">{children}</span>
    </div>
);

export default function DressCodeReviewTab({ apiBaseUrl, headers, showToast, focusCheckId = null, onFocusHandled }) {
    const base = `${apiBaseUrl}/api/dress_code`;
    const today = isoDate(new Date());
    const [filters, setFilters] = useState({ from: today, to: today, view: 'waiting', city: '', q: '' });
    const [search, setSearch] = useState('');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [detail, setDetail] = useState(null);
    const [decision, setDecision] = useState({ value: '', comment: '' });
    const [saving, setSaving] = useState(false);
    const [photo, setPhoto] = useState(null);
    const [settings, setSettings] = useState(null);
    const [exporting, setExporting] = useState(false);
    const [stand, setStand] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const request = useRef(0);
    const searchTimer = useRef(null);

    const params = useMemo(() => ({
        date_from: filters.from, date_to: filters.to, city: filters.city || undefined,
        q: filters.q || undefined, ...filterParams(filters.view),
    }), [filters]);

    const load = useCallback(() => {
        const id = request.current + 1;
        request.current = id;
        setError('');
        axios.get(`${base}/checks`, { headers: headers(), params })
            .then((r) => { if (request.current === id) setData(r.data); })
            .catch((e) => { if (request.current === id) { setData(null); setError(errText(e, 'Не удалось загрузить фото')); } });
    }, [base, headers, params]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => () => clearTimeout(searchTimer.current), []);

    const openCheck = useCallback((checkId) => {
        axios.get(`${base}/checks/${checkId}`, { headers: headers() })
            .then((r) => {
                setDetail(r.data.check);
                setConfirmDelete(false);
                setDecision({ value: r.data.check.decision || '', comment: r.data.check.decision_comment || '' });
            })
            .catch((e) => showToast?.(errText(e, 'Фото не найдено'), 'error'));
    }, [base, headers, showToast]);

    // Ссылка из Telegram (?dc_check=<id>) открывает фото сразу.
    useEffect(() => {
        if (!focusCheckId) return;
        openCheck(focusCheckId);
        onFocusHandled?.();
    }, [focusCheckId, openCheck, onFocusHandled]);

    const saveDecision = async () => {
        if (!detail || !decision.value) return;
        setSaving(true);
        try {
            const { data: result } = await axios.post(`${base}/checks/${detail.id}/decision`,
                { decision: decision.value, comment: decision.comment }, { headers: headers() });
            setDetail(result.check);
            showToast?.('Решение сохранено', 'success');
            load();
        } catch (e) {
            showToast?.(errText(e, 'Не удалось сохранить решение'), 'error');
        } finally {
            setSaving(false);
        }
    };

    /* Суперадмин убирает пробное фото совсем: строку, повторные к ней, журнал и файлы. */
    const deleteCheck = async () => {
        if (!detail) return;
        setDeleting(true);
        try {
            await axios.delete(`${base}/checks/${detail.id}`, { headers: headers() });
            setDetail(null);
            setConfirmDelete(false);
            showToast?.('Фото удалено', 'success');
            load();
        } catch (e) {
            showToast?.(errText(e, 'Не удалось удалить фото'), 'error');
        } finally {
            setDeleting(false);
        }
    };

    const openSettings = () => {
        axios.get(`${base}/settings`, { headers: headers() })
            .then((r) => setSettings({
                ...r.data,
                draft: {
                    rules_body: r.data.settings.rules.body || '',
                    department_ids: r.data.settings.department_ids || [],
                    roles: r.data.settings.roles || [],
                },
            }))
            .catch((e) => showToast?.(errText(e, 'Не удалось открыть настройку'), 'error'));
    };

    const saveSettings = async () => {
        if (!settings) return;
        setSaving(true);
        try {
            const body = { rules_body: settings.draft.rules_body };
            if (settings.can_edit_scope) {
                body.department_ids = settings.draft.department_ids;
                body.roles = settings.draft.roles;
            }
            await axios.put(`${base}/settings`, body, { headers: headers() });
            setSettings(null);
            showToast?.('Настройка сохранена', 'success');
        } catch (e) {
            showToast?.(errText(e, 'Не удалось сохранить настройку'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const exportExcel = async () => {
        setExporting(true);
        try {
            const response = await axios.get(`${base}/export`, { headers: headers(), params, responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = `dress_code_${filters.from}_${filters.to}.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            showToast?.(errText(e, 'Не удалось выгрузить'), 'error');
        } finally {
            setExporting(false);
        }
    };

    const onSearch = (value) => {
        setSearch(value);
        clearTimeout(searchTimer.current);
        searchTimer.current = setTimeout(() => setFilters((prev) => ({ ...prev, q: value.trim() })), 350);
    };

    const summary = data?.summary || {};
    const rows = asArray(data?.rows);
    const missing = asArray(data?.missing);
    const cities = asArray(data?.cities);
    const showMissing = filters.view === 'missing';

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="max-w-full overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
                    <IosSegmented value={filters.view} options={REVIEW_FILTERS}
                                  onChange={(view) => setFilters((prev) => ({ ...prev, view }))} ariaLabel="Какие фото показать" />
                </div>
                <IosDateRangePicker from={filters.from} to={filters.to} max={today}
                                    onChange={({ from, to }) => setFilters((prev) => ({ ...prev, from, to }))} />
                {cities.length > 1 && (
                    <div className="w-[170px]">
                        <CustomSelect variant="ios" value={filters.city} ariaLabel="Город"
                                      onChange={(city) => setFilters((prev) => ({ ...prev, city }))}
                                      options={[{ value: '', label: 'Все города' }, ...cities.map((city) => ({ value: city, label: city }))]} />
                    </div>
                )}
                <div className="relative min-w-[180px] flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={search} onChange={(e) => onSearch(e.target.value)} placeholder="Сотрудник"
                           className={`${iosInput} !pl-9`} />
                </div>
                <button type="button" onClick={exportExcel} disabled={exporting} className={iosBtnGhost} title="Выгрузить в Excel">
                    {exporting ? <Loader2 size={15} className="animate-spin" /> : <FileSpreadsheet size={15} />} Excel
                </button>
                <button type="button" onClick={openSettings} className={iosBtnGhost} title="Правила и кто снимает фото">
                    <Settings2 size={15} /> Правила
                </button>
                {data?.is_super_admin && (
                    <button type="button" onClick={() => setStand(true)} className={iosBtnGhost}
                            title="Проверить камеру и настроить автоснимок — ничего не сохраняется">
                        <SlidersHorizontal size={15} /> Стенд камеры
                    </button>
                )}
            </div>

            {data && (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Tile label="Фото" value={summary.photos || 0} />
                    <Tile label="Ждут решения" value={summary.waiting || 0} />
                    <Tile label="Подтверждено нарушений" value={summary.confirmed || 0}
                          hint={summary.repeat ? `повторных ${summary.repeat}` : ''} />
                    <div className={`${iosCard} min-w-0 px-3.5 py-2.5`}>
                        <div className="flex items-center gap-1 text-[11.5px] font-medium text-slate-500">
                            <span className="truncate">Не прислали фото</span>
                            <IosHint label="Как считается"
                                     text="Из смен по графику, с начала которых прошло 30 минут. Ниже в «Без фото» — кто именно." />
                        </div>
                        <div className="mt-0.5 text-[19px] font-semibold tabular-nums text-slate-900">
                            {ofTotal(missing.length, data.shifts || 0)}
                        </div>
                    </div>
                </div>
            )}

            {error && <div className={`${iosCard} px-4 py-3.5 text-[13.5px] text-rose-600`}>{error}</div>}
            {!data && !error && (
                <div className="flex justify-center py-14 text-slate-400"><Loader2 size={22} className="animate-spin" /></div>
            )}

            {data && showMissing && (
                missing.length === 0 ? (
                    <div className={`${iosCard} px-4 py-6 text-center text-[13.5px] text-slate-500`}>
                        Все, кто работал по графику, фото прислали
                    </div>
                ) : (
                    <div className={`${iosCard} divide-y divide-slate-100`}>
                        {missing.map((row) => (
                            <div key={`${row.day}:${row.user_id}`} className="flex items-center gap-3 px-4 py-2.5">
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-[14px] font-medium text-slate-900">{row.employee}</div>
                                    <div className="truncate text-[12.5px] text-slate-500">
                                        {[row.city, row.shift_start ? `смена с ${row.shift_start}` : null].filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                                <span className="shrink-0 text-[13px] tabular-nums text-slate-500">{fmtDay(row.day)}</span>
                            </div>
                        ))}
                    </div>
                )
            )}

            {data && !showMissing && (
                rows.length === 0 ? (
                    <div className={`${iosCard} flex flex-col items-center gap-2 px-4 py-8 text-center text-[13.5px] text-slate-500`}>
                        <Shirt size={20} className="text-slate-300" />
                        {filters.view === 'waiting' ? 'Спорных фото нет' : 'Фото за период нет'}
                    </div>
                ) : (
                    <div className={`${iosCard} divide-y divide-slate-100`}>
                        {rows.map((row) => (
                            <button key={row.id} type="button" onClick={() => openCheck(row.id)}
                                    className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50/80 active:bg-slate-100">
                                {row.photo_url ? (
                                    <img src={row.photo_url} alt="" className="h-12 w-9 shrink-0 rounded-lg object-cover ring-1 ring-slate-200/70" />
                                ) : (
                                    <span className="grid h-12 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-400"><Shirt size={15} /></span>
                                )}
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-[14px] font-medium text-slate-900">{row.employee}</div>
                                    <div className="truncate text-[12.5px] tabular-nums text-slate-500">
                                        {[row.city, `${fmtDay(row.day)} ${fmtTime(row.taken_at)}`, row.retake_of ? 'повторное фото' : null]
                                            .filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                                <div className="flex shrink-0 flex-col items-end gap-1">
                                    {row.decision
                                        ? <IosBadge tone={decisionTone(row.decision)}>{DECISION_LABELS[row.decision]}</IosBadge>
                                        : <IosBadge tone={aiTone(row.ai_status)}>{AI_LABELS[row.ai_status] || row.ai_status}</IosBadge>}
                                    {row.is_repeat && row.decision === 'confirmed' && <IosBadge tone="red">Повторное</IosBadge>}
                                </div>
                            </button>
                        ))}
                    </div>
                )
            )}

            <IosModal
                open={Boolean(detail)}
                onClose={() => setDetail(null)}
                title="Фото для дресс-кода"
                subtitle={detail ? `${detail.employee} · ${fmtDayFull(detail.day)}` : ''}
                footer={detail ? (
                    <>
                        {data?.is_super_admin && (confirmDelete ? (
                            <span className="mr-auto flex items-center gap-2">
                                <span className="text-[13px] text-slate-600">Удалить фото совсем?</span>
                                <button type="button" onClick={deleteCheck} disabled={deleting}
                                        className={`${iosBtnSecondary} !text-rose-600`}>
                                    {deleting && <Loader2 size={14} className="animate-spin" />} Удалить
                                </button>
                                <button type="button" onClick={() => setConfirmDelete(false)} disabled={deleting} className={iosBtnGhost}>
                                    Отмена
                                </button>
                            </span>
                        ) : (
                            <button type="button" onClick={() => setConfirmDelete(true)} className={`${iosBtnGhost} mr-auto !text-rose-600`}
                                    title="Убрать пробный снимок: фото, повторные к нему и журнал">
                                <Trash2 size={15} /> Удалить
                            </button>
                        ))}
                        <button type="button" onClick={() => setDetail(null)} className={iosBtnSecondary}>Закрыть</button>
                        <button type="button" onClick={saveDecision} className={iosBtnPrimary}
                                disabled={saving || !decision.value
                                    || (decision.value === detail.decision && (decision.comment || '') === (detail.decision_comment || ''))}>
                            {saving && <Loader2 size={14} className="animate-spin" />} Сохранить решение
                        </button>
                    </>
                ) : null}
            >
                {detail && (
                    <div className="space-y-4">
                        <div className="flex gap-4">
                            {detail.photo_url && (
                                <button type="button" onClick={() => setPhoto(detail.photo_url)}
                                        className="shrink-0 overflow-hidden rounded-2xl ring-1 ring-slate-200/70 transition active:scale-[0.98]"
                                        aria-label="Открыть фото крупно">
                                    <img src={detail.photo_url} alt="" className="h-44 w-32 object-cover" />
                                </button>
                            )}
                            <div className="min-w-0 flex-1 space-y-2">
                                <div className="flex flex-wrap gap-1.5">
                                    <IosBadge tone={aiTone(detail.ai_status)}>ИИ: {AI_LABELS[detail.ai_status] || detail.ai_status}</IosBadge>
                                    {detail.decision && (
                                        <IosBadge tone={decisionTone(detail.decision)}>{DECISION_LABELS[detail.decision]}</IosBadge>
                                    )}
                                    {detail.is_repeat && detail.decision === 'confirmed' && <IosBadge tone="red">Повторное за 10 дней</IosBadge>}
                                </div>
                                {asArray(detail.ai_reasons).length > 0 && (
                                    <div className="space-y-1">
                                        {detail.ai_reasons.map((item, index) => (
                                            <div key={index} className="text-[13.5px] text-slate-900">
                                                {item.sign}{item.rule ? <span className="text-slate-500"> — {item.rule}</span> : null}
                                            </div>
                                        ))}
                                    </div>
                                )}
                                {detail.ai_note && <div className="text-[13px] text-slate-600">{detail.ai_note}</div>}
                                {asArray(detail.ai_visible).length > 0 && (
                                    <div className="text-[12.5px] text-slate-500">Видно: {detail.ai_visible.join(', ')}</div>
                                )}
                                {asArray(detail.ai_remarks).length > 0 && (
                                    <div className="text-[12.5px] text-slate-500">Замечания: {detail.ai_remarks.join('; ')}</div>
                                )}
                            </div>
                        </div>

                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            <DetailRow label="Время">{fmtDayFull(detail.day)} {fmtTime(detail.taken_at)}</DetailRow>
                            <DetailRow label="Отдел">{[detail.department, detail.city].filter(Boolean).join(' · ') || '—'}</DetailRow>
                            {detail.ai_quality && detail.ai_quality !== 'ok' && (
                                <DetailRow label="Кадр">{QUALITY_LABELS[detail.ai_quality] || detail.ai_quality}</DetailRow>
                            )}
                            {detail.rules_version && <DetailRow label="Правила">версия {detail.rules_version}</DetailRow>}
                            {detail.decision === 'confirmed' && (
                                <DetailRow label="Сотрудник">{detail.acknowledged_at ? 'ознакомлен' : 'ещё не ознакомлен'}</DetailRow>
                            )}
                            {(detail.retake_of || detail.retake_id) && (
                                <DetailRow label="Повторное фото">
                                    <button type="button" className="font-medium text-blue-600"
                                            onClick={() => openCheck(detail.retake_id || detail.retake_of)}>
                                        {detail.retake_id ? 'Открыть повторное' : 'Открыть исходное'}
                                    </button>
                                </DetailRow>
                            )}
                        </div>

                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Решение</div>
                            <div className="space-y-2">
                                <IosSegmented value={decision.value} options={DECISION_OPTIONS} stretch
                                              onChange={(value) => setDecision((prev) => ({ ...prev, value }))} ariaLabel="Решение" />
                                <textarea value={decision.comment} rows={2} maxLength={1000}
                                          onChange={(e) => setDecision((prev) => ({ ...prev, comment: e.target.value }))}
                                          placeholder={decision.value === 'retake' ? 'Что исправить на фото' : 'Комментарий для сотрудника'}
                                          className={`${iosInput} resize-none`} />
                            </div>
                        </div>

                        {asArray(detail.journal).length > 0 && (
                            <div>
                                <div className={`${iosGroupLabel} mb-1.5`}>Журнал</div>
                                <div className={`${iosCard} divide-y divide-slate-100`}>
                                    {detail.journal.map((item, index) => (
                                        <div key={index} className="px-4 py-2.5">
                                            <div className="flex items-center justify-between gap-3 text-[13px]">
                                                <span className="font-medium text-slate-900">{DECISION_LABELS[item.decision] || item.decision}</span>
                                                <span className="shrink-0 tabular-nums text-slate-400">{fmtDay(item.at)} {fmtTime(item.at)}</span>
                                            </div>
                                            <div className="text-[12.5px] text-slate-500">
                                                {[item.actor, item.comment].filter(Boolean).join(' · ')}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </IosModal>

            <IosModal
                open={Boolean(settings)}
                onClose={() => setSettings(null)}
                title="Правила дресс-кода"
                subtitle={settings?.settings?.rules?.version
                    ? `Версия ${settings.settings.rules.version}${settings.settings.rules.created_by ? ` · ${settings.settings.rules.created_by}` : ''}`
                    : ''}
                footer={settings ? (
                    <>
                        <button type="button" onClick={() => setSettings(null)} className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={saveSettings} disabled={saving || !settings.draft.rules_body.trim()}
                                className={iosBtnPrimary}>
                            {saving && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                ) : null}
            >
                {settings && (
                    <div className="space-y-4">
                        <div>
                            <div className="mb-1.5 flex items-center gap-1">
                                <span className={iosGroupLabel}>Положение о внешнем виде</span>
                                <IosHint label="Как работает"
                                         text="По этому тексту ИИ проверяет каждое фото. Сохранение создаёт новую версию: уже проверенные фото остаются со своей версией. Чего ИИ не делает никогда — не узнаёт человека, не судит о гигиене и не считает нарушением головной убор по религиозным или медицинским причинам."/>
                            </div>
                            <textarea value={settings.draft.rules_body} rows={12} maxLength={6000}
                                      onChange={(e) => setSettings((prev) => ({ ...prev, draft: { ...prev.draft, rules_body: e.target.value } }))}
                                      className={`${iosInput} resize-y font-normal leading-relaxed`} />
                        </div>
                        {settings.can_edit_scope && (
                            <div className="space-y-3">
                                <div>
                                    <div className={`${iosGroupLabel} mb-1.5`}>Кто снимает фото — отделы</div>
                                    <CustomSelect variant="ios" multiple value={settings.draft.department_ids.map(String)}
                                                  ariaLabel="Отделы"
                                                  renderValue={(values) => namesOf(asArray(settings.departments).map((d) => ({ value: String(d.id), label: d.name })), values)}
                                                  onChange={(values) => setSettings((prev) => ({ ...prev, draft: { ...prev.draft, department_ids: asArray(values).map(Number) } }))}
                                                  options={asArray(settings.departments).map((d) => ({ value: String(d.id), label: d.name }))} />
                                </div>
                                <div>
                                    <div className={`${iosGroupLabel} mb-1.5`}>Роли</div>
                                    <CustomSelect variant="ios" multiple value={settings.draft.roles} ariaLabel="Роли"
                                                  renderValue={(values) => namesOf(asArray(settings.roles), values)}
                                                  onChange={(values) => setSettings((prev) => ({ ...prev, draft: { ...prev.draft, roles: asArray(values) } }))}
                                                  options={asArray(settings.roles)} />
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </IosModal>

            <IosLightbox url={photo} alt="Фото для дресс-кода" onClose={() => setPhoto(null)} />
            {stand && (
                <Suspense fallback={null}>
                    <DressCodeStand open onClose={() => setStand(false)} base={base} headers={headers} showToast={showToast} />
                </Suspense>
            )}
        </div>
    );
}
