import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Pencil, Trash2 } from 'lucide-react';
import { IosMenu, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';

/*
 * Окно дня супервайзера в «Учёте часов» (задача #352).
 *
 * Часы СВ считаются из отметок Clockster: пары «приход → уход» минус перерыв.
 * Окно показывает, из чего сложилось число: отметки дня, какие вошли в пары и
 * почему остальные не засчитаны. РОП дописывает пропущенную отметку и меняет
 * любую отметку дня. В Clockster ничего не уходит: ручная отметка и
 * исправление живут у нас и учитываются ночным пересчётом. Исправленная
 * отметка Clockster остаётся видна зачёркнутой — снятие исправления её
 * возвращает. Сам СВ окно только смотрит.
 */

const TZ = 'Asia/Almaty';

const REASON_LABELS = {
    unpaired_in: 'без ухода',
    unpaired_out: 'без прихода',
    repeat_touch: 'повторное касание',
    too_long: 'дольше 16 ч — не засчитано',
};

const KIND_OPTIONS = [
    { value: 'in', label: 'Приход' },
    { value: 'out', label: 'Уход' },
];

const kindLabel = (kind) => (kind === 'in' ? 'Приход' : 'Уход');

const formatTime = (iso) => {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: TZ });
};

// «YYYY-MM-DD» и «HH:MM» отметки по Алматы — для полей формы правки.
const localParts = (iso) => {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return { date: '', time: '' };
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).map(part => [part.type, part.value]));
    return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
};

const formatDay = (dateStr) => {
    const date = new Date(`${dateStr}T12:00:00+05:00`);
    if (!dateStr || Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', timeZone: TZ });
};

const formatDuration = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds || 0) / 60));
    const hours = Math.floor(total / 60);
    const minutes = total % 60;
    if (!hours) return `${minutes} мин`;
    return minutes ? `${hours} ч ${minutes} мин` : `${hours} ч`;
};

const formatHours = (hours) => Math.max(0, Number(hours || 0)).toFixed(2).replace('.', ',');

const emptyForm = (dateStr) => ({ kind: 'out', date: dateStr || '', time: '', comment: '' });

export default function SupervisorDayMarksModal({ open, onClose, viewerId, apiBaseUrl, operator, dateStr, onChanged }) {
    // На телефоне окно уезжает с анимацией уже после закрытия: держим последние
    // человека и день, иначе уезжающий экран остался бы без шапки.
    const lastShownRef = useRef({ operator: null, dateStr: '' });
    if (open && operator && dateStr) lastShownRef.current = { operator, dateStr };
    const shownOperator = open ? operator : lastShownRef.current.operator;
    const shownDate = open ? dateStr : lastShownRef.current.dateStr;

    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [form, setForm] = useState(() => emptyForm(dateStr));
    // null — дописать новую; { type: 'manual', id } или { type: 'clockster', at, kind } — правка.
    const [editing, setEditing] = useState(null);

    const userId = shownOperator?.operator_id;
    // Ответ, пришедший для другого открытия (другой день, окно уже закрыто),
    // не должен лечь в текущее окно.
    const requestKey = open && userId && dateStr ? `${userId}|${dateStr}` : '';
    const requestKeyRef = useRef(requestKey);
    requestKeyRef.current = requestKey;

    const headers = { 'X-User-Id': viewerId };

    const load = useCallback(async () => {
        const key = requestKeyRef.current;
        if (!key) return;
        setLoading(true);
        setError('');
        try {
            const resp = await axios.get(`${apiBaseUrl}/api/sv/supervisor_day`, {
                params: { user_id: userId, date: dateStr },
                headers,
            });
            if (requestKeyRef.current === key) setDetail(resp.data || null);
        } catch (err) {
            if (requestKeyRef.current !== key) return;
            setDetail(null);
            setError(err?.response?.data?.error || 'Не удалось загрузить отметки');
        } finally {
            if (requestKeyRef.current === key) setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, userId, dateStr, viewerId]);

    useEffect(() => {
        setDetail(null);
        setEditing(null);
        setError('');
        setForm(emptyForm(dateStr));
        if (requestKey) load();
    }, [requestKey, load, dateStr]);

    const send = async (request) => {
        const key = requestKeyRef.current;
        setSaving(true);
        setError('');
        try {
            const resp = await request();
            if (typeof onChanged === 'function') onChanged();
            if (requestKeyRef.current !== key) return;
            setDetail(resp.data || null);
            setEditing(null);
            setForm(emptyForm(dateStr));
        } catch (err) {
            if (requestKeyRef.current === key) setError(err?.response?.data?.error || 'Не удалось сохранить отметку');
        } finally {
            if (requestKeyRef.current === key) setSaving(false);
        }
    };

    const submit = () => {
        if (!form.date || !form.time) {
            setError('Укажите дату и время отметки');
            return;
        }
        const body = {
            at: `${form.date}T${form.time}`,
            kind: form.kind,
            comment: form.comment,
            view_date: dateStr,
        };
        if (editing?.type === 'manual') {
            send(() => axios.patch(`${apiBaseUrl}/api/sv/supervisor_marks/${editing.id}`, body, { headers }));
            return;
        }
        send(() => axios.post(`${apiBaseUrl}/api/sv/supervisor_marks`, {
            ...body,
            user_id: userId,
            ...(editing?.type === 'clockster' ? { replaces_clockster: { at: editing.at, kind: editing.kind } } : {}),
        }, { headers }));
    };

    const removeMark = (markId) => send(() => axios.delete(`${apiBaseUrl}/api/sv/supervisor_marks/${markId}`, {
        params: { view_date: dateStr },
        headers,
    }));

    const startEdit = (mark) => {
        const parts = localParts(mark.at);
        setError('');
        setEditing(mark.source === 'manual'
            ? { type: 'manual', id: mark.id, at: mark.at, kind: mark.kind }
            : { type: 'clockster', at: mark.at, kind: mark.kind });
        setForm({ kind: mark.kind, date: parts.date, time: parts.time, comment: mark.source === 'manual' ? (mark.comment || '') : '' });
    };

    const cancelEdit = () => {
        setEditing(null);
        setError('');
        setForm(emptyForm(dateStr));
    };

    const marks = Array.isArray(detail?.marks) ? detail.marks : [];
    const canEdit = Boolean(detail?.can_edit);
    const hasCard = Boolean(detail?.card?.ext_id);
    const worked = Number(detail?.worked_seconds || 0) / 3600;
    const storedDiffers = detail?.stored_work_time != null
        && Math.abs(Number(detail.stored_work_time) - worked) > 0.01;
    const currentMonthStart = `${localParts(new Date().toISOString()).date.slice(0, 8)}01`;
    const isClosedMonth = Boolean(shownDate) && shownDate < currentMonthStart;

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={shownOperator?.name || 'Супервайзер'}
            subtitle={shownDate ? `${formatDay(shownDate)} · отметки Clockster` : 'Отметки Clockster'}
            maxWidth="max-w-md"
        >
            {loading && !detail ? (
                <div className="py-8 text-center text-[13px] text-slate-500">Загрузка…</div>
            ) : (
                <div className="space-y-4">
                    {detail && (
                        <div className={`${iosCard} flex divide-x divide-slate-100 text-center`}>
                            <div className="min-w-0 flex-1 px-2 py-3">
                                <div className="text-[11px] text-slate-500">На месте</div>
                                <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-slate-900">{formatDuration(detail.presence_seconds)}</div>
                            </div>
                            <div className="min-w-0 flex-1 px-2 py-3">
                                <div className="text-[11px] text-slate-500">Перерыв</div>
                                <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-slate-900">{formatDuration(detail.break_seconds)}</div>
                                <div className="text-[10.5px] text-slate-400">{detail.break_source === 'schedule' ? 'по графику' : 'общий'}</div>
                            </div>
                            <div className="min-w-0 flex-1 px-2 py-3">
                                <div className="text-[11px] text-slate-500">Итог, ч</div>
                                <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-slate-900">{formatHours(worked)}</div>
                            </div>
                        </div>
                    )}

                    {detail?.operator_day && (
                        <div className="text-[12px] text-slate-500">
                            В этот день сотрудник работал оператором — часы считаются по статусам линии.
                        </div>
                    )}
                    {detail && !detail.day_built && !detail.operator_day && (
                        <div className="text-[12px] text-slate-500">
                            Отметки этого дня ещё не собраны — в учёт часов день попадёт после ночного пересчёта.
                        </div>
                    )}
                    {detail && detail.day_built && !detail.operator_day && storedDiffers && (
                        <div className="text-[12px] text-slate-500">
                            В учёте часов сейчас <span className="tabular-nums">{formatHours(detail.stored_work_time)}</span> ч
                            {isClosedMonth
                                ? ' — в закрытом месяце сохраняется прежний перерыв.'
                                : ' — обновится при ночном пересчёте.'}
                        </div>
                    )}

                    {detail && (
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Отметки</div>
                            {marks.length === 0 ? (
                                <div className={`${iosCard} px-4 py-3 text-[13px] text-slate-500`}>
                                    {hasCard ? 'За этот день отметок нет.' : 'Карточка Clockster не привязана — отметок терминала нет.'}
                                </div>
                            ) : (
                                <div className={`${iosCard} divide-y divide-slate-100`}>
                                    {marks.map((mark, index) => {
                                        const ignored = mark.status === 'ignored';
                                        const replaced = mark.status === 'replaced';
                                        const isManual = mark.source === 'manual';
                                        const isEdited = editing && (
                                            (editing.type === 'manual' && isManual && editing.id === mark.id)
                                            || (editing.type === 'clockster' && !isManual && editing.at === mark.at && editing.kind === mark.kind)
                                        );
                                        const sourceText = isManual
                                            ? [
                                                'Вручную',
                                                mark.created_by_name,
                                                mark.replaces_at ? `вместо ${formatTime(mark.replaces_at)} ${kindLabel(mark.replaces_kind).toLowerCase()}` : null,
                                            ].filter(Boolean).join(' · ')
                                            : 'Clockster';
                                        return (
                                            <div
                                                key={`${mark.source}-${mark.id ?? index}-${mark.at}-${mark.status}`}
                                                className={`flex items-start gap-3 px-4 py-2.5 ${isEdited ? 'bg-slate-50' : ''}`}
                                            >
                                                <div className="w-14 shrink-0 pt-0.5">
                                                    <div className={`text-[14px] font-semibold tabular-nums ${ignored || replaced ? 'text-slate-400' : 'text-slate-900'} ${replaced ? 'line-through' : ''}`}>
                                                        {formatTime(mark.at)}
                                                    </div>
                                                    {mark.next_day && <div className="text-[10.5px] text-slate-400">след. день</div>}
                                                </div>
                                                {/* grow, а не flex-1: на телефоне оболочка ставит колонкой
                                                    любой .flex.items-start с .flex-1 внутри (каркасы экранов). */}
                                                <div className="min-w-0 grow basis-0">
                                                    <div className={`text-[13px] ${replaced ? 'text-slate-400' : 'text-slate-800'}`}>{kindLabel(mark.kind)}</div>
                                                    <div className="text-[11.5px] text-slate-500">{sourceText}</div>
                                                    {isManual && mark.comment && (
                                                        <div className="mt-0.5 whitespace-pre-wrap break-words text-[12px] text-slate-600">{mark.comment}</div>
                                                    )}
                                                </div>
                                                <div className="flex shrink-0 items-center gap-1 pt-0.5">
                                                    {ignored && (
                                                        <span className="text-[11.5px] text-amber-700">{REASON_LABELS[mark.reason] || 'не засчитано'}</span>
                                                    )}
                                                    {replaced && <span className="text-[11.5px] text-slate-400">исправлена</span>}
                                                    {mark.status === 'previous_day' && (
                                                        <span className="text-[11.5px] text-slate-400">смена накануне</span>
                                                    )}
                                                    {canEdit && !replaced && (
                                                        <IosMenu
                                                            label="Действия с отметкой"
                                                            disabled={saving}
                                                            items={[
                                                                { key: 'edit', label: 'Изменить', icon: Pencil, onSelect: () => startEdit(mark) },
                                                                isManual && mark.id && {
                                                                    key: 'remove',
                                                                    label: mark.replaces_at ? 'Снять исправление' : 'Снять отметку',
                                                                    icon: Trash2,
                                                                    danger: true,
                                                                    separatorBefore: true,
                                                                    onSelect: () => removeMark(mark.id),
                                                                },
                                                            ]}
                                                        />
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}

                    {canEdit && detail && (
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>
                                {editing
                                    ? `Изменить отметку ${formatTime(editing.at)} · ${kindLabel(editing.kind).toLowerCase()}`
                                    : 'Дописать отметку'}
                            </div>
                            <div className={`${iosCard} space-y-3 p-4`}>
                                {editing?.type === 'clockster' && (
                                    <div className="text-[12px] text-slate-500">
                                        В Clockster отметка не меняется: у нас она заменится исправленной, а исходная останется видна.
                                    </div>
                                )}
                                <IosSegmented
                                    ariaLabel="Тип отметки"
                                    stretch
                                    value={form.kind}
                                    onChange={(value) => setForm(prev => ({ ...prev, kind: value }))}
                                    options={KIND_OPTIONS}
                                />
                                <div className="grid grid-cols-2 gap-2">
                                    <input
                                        type="date"
                                        className={iosInput}
                                        value={form.date}
                                        onChange={(e) => setForm(prev => ({ ...prev, date: e.target.value }))}
                                        aria-label="Дата отметки"
                                    />
                                    <input
                                        type="time"
                                        className={`${iosInput} tabular-nums`}
                                        value={form.time}
                                        onChange={(e) => setForm(prev => ({ ...prev, time: e.target.value }))}
                                        aria-label="Время отметки"
                                    />
                                </div>
                                <textarea
                                    rows={2}
                                    className={`${iosInput} resize-none`}
                                    value={form.comment}
                                    maxLength={500}
                                    placeholder="Причина, например: не отметил уход"
                                    onChange={(e) => setForm(prev => ({ ...prev, comment: e.target.value }))}
                                    aria-label="Причина"
                                />
                                <div className="flex gap-2">
                                    {editing && (
                                        <button type="button" className={`${iosBtnSecondary} flex-1`} onClick={cancelEdit} disabled={saving}>
                                            Отмена
                                        </button>
                                    )}
                                    <button type="button" className={`${iosBtnPrimary} flex-1`} onClick={submit} disabled={saving}>
                                        {saving ? 'Сохраняем…' : (editing ? 'Сохранить' : 'Добавить отметку')}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}

                    {error && <div className="text-[12.5px] text-rose-600">{error}</div>}
                </div>
            )}
        </IosModal>
    );
}
