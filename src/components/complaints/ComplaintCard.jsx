import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, ArrowLeft, CalendarClock, Check, CheckCircle2, Circle, Copy, History, Loader2,
    Paperclip, RefreshCw, Send, Trash2, UserRoundPen, X,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosModal,
} from '../ui/ios';
import InfoHint from '../common/InfoHint';
import { ReviewPanel, ReviewResolveModal } from '../common/SupervisorReview';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import {
    ACTION_HELD, ACTION_OTHER, ACTION_PLAN, MESSAGE_KIND_LABELS, REVIEW_PENDING, REVIEW_RESOLVED,
    REVIEW_SENT,
    activePlan, clockOf, driverAnswers, eventText, heldDraft, isPickedShift, isRecorded, openQuestion,
    pickShift, planText, shiftKey, shiftLabel, statusView, workButtons, workPayload, workProblems,
    workSteps,
} from './complaintRules';
import { DATE_TRIGGER, TIME_INPUT } from './styles';

/* Карточка жалобы.
 *
 * Порядок блоков — порядок того, что человеку нужно СЕЙЧАС. Оператор открывает
 * жалобу ради ответа для водителя, поэтому он первый; вопрос группы — сразу
 * за ним: его ждут. Суть жалобы и данные водителя — ниже, это справка. Итог и
 * работа с сотрудником — для разбирающего; оператору из работы с сотрудником
 * не показывается ничего: «внутренние детали обратной связи и обучения не
 * должны уходить оператору».
 *
 * Цвет — только у того, что ждёт действия: вопрос группы (янтарный) и ответ
 * для водителя (голубой). Остальное — нейтральные iOS-секции. */

const iosBtnDanger = `${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`;

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const fmtDateTime = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit' })
    : '—');

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
    : '—');

const fmtShort = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit',
        minute: '2-digit' })
    : '');

// Подписи событий истории — в complaintRules.eventText: там же и смысл события.

const UNIT_LABELS = { department: 'Подразделение', office: 'Офис', park: 'Таксопарк' };

const Row = ({ label, children, action = null }) => (
    <div className="flex items-start gap-3 px-4 py-2.5">
        <div className="w-[128px] shrink-0 pt-px text-[12.5px] text-slate-500">{label}</div>
        <div className="min-w-0 flex-1 text-[13.5px] leading-snug text-slate-900">{children}</div>
        {action}
    </div>
);

const Section = ({ title, right = null, children, className = '' }) => (
    <section className={`space-y-1.5 ${className}`}>
        {(title || right) && (
            <div className="flex items-end justify-between gap-2">
                <div className={iosGroupLabel}>{title}</div>
                {right}
            </div>
        )}
        <div className={`${iosCard} divide-y divide-slate-100`}>{children}</div>
    </section>
);

const CopyButton = ({ text, showToast, label = 'Скопировать' }) => (
    <button type="button" title={label}
            onClick={async () => {
                try {
                    await navigator.clipboard.writeText(String(text || ''));
                    showToast?.('Скопировано', 'success');
                } catch (_) {
                    showToast?.('Не удалось скопировать', 'error');
                }
            }}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
        <Copy size={13} />
    </button>
);

/* Вложение из группы. Файл живёт в Telegram, ссылка короткоживущая и требует
 * авторизации — поэтому скачиваем в память и отдаём объектной ссылкой, как в
 * «Обращениях», а не держим адрес. */
const Attachment = ({ message, complaintId, apiBaseUrl, headers, showToast }) => {
    const [busy, setBusy] = useState(false);
    const open = async () => {
        setBusy(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/attachments/${message.id}`,
                { headers: headers(), responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = message.attachment?.name || 'вложение';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            showToast?.('Telegram не отдал файл', 'error');
        } finally {
            setBusy(false);
        }
    };
    return (
        <button type="button" onClick={open} disabled={busy}
                className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg bg-white/70 px-2 py-1 text-[12px] text-slate-600 ring-1 ring-slate-200 transition hover:bg-white">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Paperclip size={12} />}
            {message.attachment?.name || 'Вложение'}
        </button>
    );
};

/* ─── Работа с сотрудником: три окна ──────────────────────────────────────────
 *
 * «Назначить тренинг», «Проведён тренинг», «Приняты другие меры» — три кнопки
 * внизу блока (владелец, 30.09.2026, вместо одной «Записать работу»). Окно
 * одно, содержимое — по кнопке: проверка и тело запроса живут в
 * complaintRules.js под node --test. */

const WORK_TITLES = {
    [ACTION_PLAN]: { title: 'Назначить тренинг', save: 'Назначить', done: 'Тренинг назначен' },
    [ACTION_HELD]: { title: 'Проведён тренинг', save: 'Записать',
                     done: 'Записано — занятие добавлено в «Тренинги»' },
    [ACTION_OTHER]: { title: 'Приняты другие меры', save: 'Записать', done: 'Записано' },
};

const FieldLabel = ({ children }) => (
    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">{children}</label>
);

const Problem = ({ text }) => (text ? <div className="text-[11.5px] text-rose-600">{text}</div> : null);

const WorkModal = ({ code: liveCode, onClose, complaint, apiBaseUrl, headers, showToast, onSaved }) => {
    const open = Boolean(liveCode);
    // На телефоне окно ещё ~300 мс уезжает после закрытия (IosModal) — и всё
    // это время рисуется с текущими пропсами. Без памяти о последнем режиме
    // уезжающий экран на глазах менял бы заголовок на «Приняты другие меры» и
    // пустел. Тот же приём, что в SupervisorDayMarksModal.
    const lastCode = useRef(liveCode);
    if (liveCode) lastCode.current = liveCode;
    const code = liveCode || lastCode.current;
    const [draft, setDraft] = useState({});
    const [touched, setTouched] = useState(false);
    const [busy, setBusy] = useState(false);
    // Ближайшие смены — только окну «Назначить тренинг». null — ещё грузятся.
    const [shifts, setShifts] = useState(null);
    // «Сегодня» и «сейчас» — на момент открытия окна: проверка не должна
    // менять мнение, пока человек выбирает время.
    const [clock, setClock] = useState(clockOf);
    const plan = activePlan(complaint);

    useEffect(() => {
        if (!open) return;
        const now = clockOf();
        setClock(now);
        setTouched(false);
        setDraft(liveCode === ACTION_HELD ? heldDraft(complaint, now)
            : liveCode === ACTION_PLAN ? { date: now.today } : {});
    }, [open, liveCode]); // eslint-disable-line react-hooks/exhaustive-deps

    const complaintId = complaint?.id;
    useEffect(() => {
        if (liveCode !== ACTION_PLAN || !complaintId) return undefined;
        let cancelled = false;
        setShifts(null);
        axios.get(`${apiBaseUrl}/api/complaints/complaints/${complaintId}/shifts`,
            { headers: headers() })
            .then((response) => { if (!cancelled) setShifts(response.data.items || []); })
            .catch(() => { if (!cancelled) setShifts([]); });
        return () => { cancelled = true; };
    }, [liveCode, complaintId, apiBaseUrl, headers]);

    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));
    const problems = workProblems(code, draft, clock);
    const shown = (key) => (touched ? problems[key] : null);
    const titles = WORK_TITLES[code] || WORK_TITLES[ACTION_OTHER];

    // Что подставляет смена и где галочка — complaintRules.pickShift.
    const chooseShift = (shift) => setDraft((prev) => pickShift(prev, shift, clock));

    const save = async () => {
        setTouched(true);
        if (Object.keys(problems).length) return;
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaint.id}/work`,
                workPayload(code, draft), { headers: headers() });
            showToast?.(titles.done, 'success');
            onSaved?.(response.data);
            onClose();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось записать'), 'error');
        } finally {
            setBusy(false);
        }
    };

    return (
        <IosModal open={open} onClose={onClose} title={titles.title}
                  subtitle={complaint?.employee_name || 'Сотрудник не определён'}
                  maxWidth="max-w-md"
                  footer={(
                      <>
                          <button type="button" onClick={onClose} className={iosBtnSecondary} disabled={busy}>
                              Отмена
                          </button>
                          <button type="button" onClick={save} disabled={busy} className={iosBtnPrimary}>
                              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                              {titles.save}
                          </button>
                      </>
                  )}>
            {code === ACTION_PLAN && (
                <div className="space-y-4">
                    <div>
                        <div className="mb-1.5 flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Ближайшие смены</span>
                            <InfoHint side="left">
                                В день тренинга супервайзерам отдела и самому сотруднику придёт
                                уведомление. Работа с сотрудником останется открытой, пока тренинг
                                не проведут.
                            </InfoHint>
                        </div>
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {shifts === null && (
                                <div className="flex items-center gap-2 px-4 py-2.5 text-[12.5px] text-slate-400">
                                    <Loader2 size={13} className="animate-spin" /> Загрузка смен…
                                </div>
                            )}
                            {shifts && !shifts.length && (
                                <div className="px-4 py-2.5 text-[12.5px] text-slate-400">
                                    Ближайших смен в графике нет
                                </div>
                            )}
                            {(shifts || []).map((shift) => (
                                <button key={shiftKey(shift)} type="button"
                                        onClick={() => chooseShift(shift)}
                                        className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50">
                                    <CalendarClock size={15} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1 text-[13.5px] tabular-nums text-slate-900">
                                        {shiftLabel(shift)}
                                        {shift.ongoing && <span className="text-slate-500"> · идёт сейчас</span>}
                                    </span>
                                    {isPickedShift(draft, shift, clock) && (
                                        <Check size={15} className="shrink-0 text-blue-600" />
                                    )}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <FieldLabel>День</FieldLabel>
                            <IosDatePicker value={draft.date || ''} min={clock.today} className="w-full"
                                           triggerClassName={DATE_TRIGGER}
                                           onChange={(value) => set('date', value)} ariaLabel="День тренинга" />
                        </div>
                        <div>
                            <FieldLabel>Время</FieldLabel>
                            <IosTimePicker value={draft.time || ''} onChange={(value) => set('time', value)}
                                           step={5} className="w-full" inputClassName={TIME_INPUT}
                                           ariaLabel="Время тренинга" />
                        </div>
                    </div>
                    <Problem text={shown('date') || shown('time')} />
                </div>
            )}

            {code === ACTION_HELD && (
                <div className="space-y-3">
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Занятие</span>
                        <InfoHint side="left">
                            Запись сразу появится в «Тренингах» — по сотруднику, с номером и темой
                            жалобы — и пойдёт в его часы, как обратная связь по оценке звонка.
                        </InfoHint>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                        <div>
                            <FieldLabel>Дата</FieldLabel>
                            <IosDatePicker value={draft.date || ''} max={clock.today} className="w-full"
                                           triggerClassName={DATE_TRIGGER}
                                           onChange={(value) => set('date', value)} ariaLabel="Дата занятия" />
                        </div>
                        <div>
                            <FieldLabel>Начало</FieldLabel>
                            <IosTimePicker value={draft.start || ''} onChange={(value) => set('start', value)}
                                           step={5} className="w-full" inputClassName={TIME_INPUT}
                                           ariaLabel="Начало занятия" />
                        </div>
                        <div>
                            <FieldLabel>Конец</FieldLabel>
                            <IosTimePicker value={draft.end || ''} onChange={(value) => set('end', value)}
                                           step={5} className="w-full" inputClassName={TIME_INPUT}
                                           ariaLabel="Окончание занятия" />
                        </div>
                    </div>
                    <Problem text={shown('date') || shown('time')} />
                </div>
            )}

            {code === ACTION_OTHER && (
                <div className="space-y-3">
                    <div>
                        <div className={`${iosGroupLabel} mb-1.5`}>Что сделано</div>
                        <textarea value={draft.comment || ''} rows={4}
                                  onChange={(e) => set('comment', e.target.value)}
                                  className={`${iosInput} resize-y`} />
                        <Problem text={shown('comment')} />
                    </div>
                    {/* Последствие называем до нажатия: «другие меры» снимают
                        назначенный тренинг (catalog.TRAINING_CLEARING_ACTIONS). */}
                    {complaint?.training_required && (
                        <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-slate-600">
                            {plan
                                ? `Назначенный на ${planText(plan)} тренинг будет снят, и работа с сотрудником завершится.`
                                : 'Требование тренинга будет снято, и работа с сотрудником завершится.'}
                        </div>
                    )}
                </div>
            )}
        </IosModal>
    );
};

/* Жалоба на проверке: панель решения и окно «Решено» с итогом — общие с
   обращениями (common/SupervisorReview.jsx): проверка у них одна и та же. */

/* ─── Сотрудник: определить или поправить ──────────────────────────────────── */

const EmployeeModal = ({ open, onClose, complaint, meta, apiBaseUrl, headers, showToast, onSaved }) => {
    const departments = useMemo(() => {
        const list = meta?.departments || [];
        return complaint?.target === 'front_office'
            ? list.filter((item) => item.code === 'front_office')
            : list.filter((item) => item.call_center);
    }, [meta, complaint?.target]);
    const [departmentId, setDepartmentId] = useState('');
    const [employeeId, setEmployeeId] = useState('');
    const [people, setPeople] = useState([]);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setDepartmentId(String(complaint?.target_department_id || departments[0]?.id || ''));
        setEmployeeId(complaint?.employee_id ? String(complaint.employee_id) : '');
    }, [open, complaint, departments]);

    useEffect(() => {
        if (!open || !departmentId) return undefined;
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/complaints/employees?department_id=${departmentId}`,
            { headers: headers() })
            .then((response) => { if (!cancelled) setPeople(response.data.items || []); })
            .catch(() => { if (!cancelled) setPeople([]); });
        return () => { cancelled = true; };
    }, [open, departmentId, apiBaseUrl, headers]);

    const save = async () => {
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaint.id}/employee`,
                { employee_id: employeeId ? Number(employeeId) : null }, { headers: headers() });
            showToast?.(employeeId ? 'Сотрудник определён' : 'Сотрудник снят', 'success');
            onSaved?.(response.data);
            onClose();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось сохранить'), 'error');
        } finally {
            setBusy(false);
        }
    };

    return (
        <IosModal open={open} onClose={onClose} title="Сотрудник, на которого жалоба"
                  subtitle="Определите после проверки: по записи звонка, чату или сменам"
                  footer={(
                      <>
                          <button type="button" onClick={onClose} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                          <button type="button" onClick={save} disabled={busy} className={iosBtnPrimary}>
                              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                              Сохранить
                          </button>
                      </>
                  )}>
            <div className="space-y-3">
                {departments.length > 1 && (
                    <div className="flex flex-wrap gap-1.5">
                        {departments.map((item) => (
                            <button key={item.id} type="button"
                                    onClick={() => { setDepartmentId(String(item.id)); setEmployeeId(''); }}
                                    className={`rounded-xl px-3 py-1.5 text-[12.5px] font-medium transition-all active:scale-[0.98] ${
                                        String(departmentId) === String(item.id)
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                    }`}>
                                {item.label}
                            </button>
                        ))}
                    </div>
                )}
                <CustomSelect variant="ios" searchable value={employeeId}
                              onChange={setEmployeeId}
                              options={[{ value: '', label: 'Не определён' },
                                  ...people.map((person) => ({ value: String(person.id), label: person.name,
                                                              groupLabel: person.group_name || 'Без группы' }))]}
                              placeholder="Не определён" searchPlaceholder="Поиск по ФИО"
                              ariaLabel="Сотрудник" />
                {complaint?.employee_id && (
                    <div className="px-1 text-[12px] leading-snug text-slate-500">
                        Смена сотрудника обнуляет проведённую работу: она была с другим человеком.
                        Записи журнала остаются.
                    </div>
                )}
            </div>
        </IosModal>
    );
};

/* ─── Карточка ─────────────────────────────────────────────────────────────── */

export const COMPLAINT_GONE = 'Жалоба удалена или больше вам не видна';

/* Карточка раздела «Жалобы» — для тех, кто разбирает: итог, работа с
 * сотрудником, внутреннее обсуждение (по правам). Автор видит свою жалобу в
 * «Обращениях» — карточкой в формате обращения (CrmTicketsView:
 * ComplaintThreadCard). */
export default function ComplaintCard({
    complaintId, meta, apiBaseUrl, headers, showToast, onBack, onChanged, onSeen,
    onDeleted, pulse,
}) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [reply, setReply] = useState('');
    const [replyFile, setReplyFile] = useState(null);
    const [sending, setSending] = useState(false);
    const [resultDraft, setResultDraft] = useState(null);
    const [savingResult, setSavingResult] = useState(false);
    // Какое окно работы с сотрудником открыто: код действия или null.
    const [workCode, setWorkCode] = useState(null);
    const [resolveOpen, setResolveOpen] = useState(false);
    const [reviewBusy, setReviewBusy] = useState(false);
    const [employeeOpen, setEmployeeOpen] = useState(false);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [events, setEvents] = useState(null);
    const [showInternal, setShowInternal] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [resending, setResending] = useState(false);

    const load = useCallback(async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/complaints/complaints/${complaintId}`,
                { headers: headers() });
            setData(response.data);
            setError(null);
            onSeen?.(complaintId);
        } catch (err) {
            setError(err?.response?.status === 404 || err?.response?.status === 403
                ? COMPLAINT_GONE : errorText(err, 'Не удалось открыть жалобу'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, complaintId, onSeen]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { if (pulse) load(true); }, [pulse]); // eslint-disable-line react-hooks/exhaustive-deps

    const item = data?.item;
    const permissions = data?.permissions || {};
    const messages = data?.messages || [];
    const answers = useMemo(() => driverAnswers(messages), [messages]);
    const question = useMemo(() => openQuestion(item, messages), [item, messages]);
    const thread = useMemo(() => messages.filter((m) => showInternal || m.kind !== 'internal'),
        [messages, showInternal]);
    const internalCount = messages.filter((m) => m.kind === 'internal').length;
    const status = statusView(item);
    const target = (meta?.targets || []).find((t) => t.code === item?.target);
    const results = meta?.results || [];

    const applyItem = (next) => {
        setData((prev) => (prev ? { ...prev, ...next, item: next.item || prev.item } : prev));
        onChanged?.();
    };

    const send = async () => {
        if (!reply.trim() && !replyFile) return;
        setSending(true);
        try {
            let body = { body: reply.trim() };
            if (replyFile) {
                body = new FormData();
                body.append('body', reply.trim());
                body.append('attachment', replyFile);
            }
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/messages`, body,
                { headers: headers() });
            setReply('');
            setReplyFile(null);
            applyItem(response.data);
            showToast?.('Отправлено в группу', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось отправить'), 'error');
        } finally {
            setSending(false);
        }
    };

    const saveResult = async () => {
        if (!resultDraft?.code) return;
        setSavingResult(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/result`,
                { result_code: resultDraft.code, result_note: resultDraft.note || '' },
                { headers: headers() });
            applyItem(response.data);
            setResultDraft(null);
            showToast?.('Итог записан', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось записать итог'), 'error');
        } finally {
            setSavingResult(false);
        }
    };

    const openHistory = async () => {
        setHistoryOpen(true);
        setEvents(null);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/events`, { headers: headers() });
            setEvents(response.data.events || []);
        } catch (err) {
            setEvents([]);
            showToast?.(errorText(err, 'История недоступна'), 'error');
        }
    };

    const resend = async () => {
        setResending(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/resend`, {}, { headers: headers() });
            applyItem(response.data);
            showToast?.('Жалоба отправлена в группу', 'success');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось отправить'), 'error');
        } finally {
            setResending(false);
        }
    };

    /* Решение по жалобе на проверке (Яндекс). «В группу» может лечь, а
       доставка — нет (бота выгнали, группа не выбрана): тогда жалоба уже
       отправлена супервайзером, а повтор — обычной «Отправить ещё раз». */
    const review = async (decision, note = '') => {
        setReviewBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/complaints/complaints/${complaintId}/review`,
                { decision, note }, { headers: headers() });
            applyItem(response.data);
            setResolveOpen(false);
            if (decision === 'resolve') showToast?.('Решено — жалоба закрыта', 'success');
            else if (response.data.delivered) showToast?.('Жалоба отправлена в группу', 'success');
            else showToast?.(`В группу не ушла: ${response.data.delivery_error || 'ошибка Telegram'}`, 'error');
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось сохранить решение'), 'error');
            // Решение уже принял другой: окно «Решено» закрываем — его
            // «Сохранить» отдало бы тот же отказ ещё раз.
            if (err?.response?.status === 409) { setResolveOpen(false); load(true); }
        } finally {
            setReviewBusy(false);
        }
    };

    const remove = async () => {
        try {
            await axios.delete(`${apiBaseUrl}/api/complaints/complaints/${complaintId}`, { headers: headers() });
            showToast?.('Жалоба удалена', 'success');
            setConfirmDelete(false);
            onDeleted?.(complaintId);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось удалить'), 'error');
        }
    };

    if (loading && !data) {
        return (
            <div className="flex h-full items-center justify-center gap-2 text-[13px] text-slate-400">
                <Loader2 size={15} className="animate-spin" /> Загрузка…
            </div>
        );
    }
    if (error || !item) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                <AlertCircle size={20} className="text-slate-300" />
                <div className="text-[13px] text-slate-500">{error || COMPLAINT_GONE}</div>
                <button type="button" onClick={onBack} className={iosBtnSecondary}>К списку</button>
            </div>
        );
    }

    const employeeTarget = Boolean(target?.employee);
    const steps = workSteps(item);
    /* Итог разбирающему нужен всегда, кроме жалоб «только зафиксировать» —
       их никто не проверяет. Оператору — только сам итог, когда он есть. */
    const showResult = permissions.can_handle
        ? (item.requires_processing !== false || Boolean(item.result_code))
        : Boolean(item.result_code);
    const underReview = item.review_state === REVIEW_PENDING;
    const buttons = workButtons(meta, item);

    return (
        <div className="flex h-full min-h-0 flex-col">
            {/* Шапка */}
            <div className="flex shrink-0 items-start gap-2 border-b border-slate-100 px-4 py-3">
                <button type="button" onClick={onBack} aria-label="К списку"
                        className="-ml-1 grid h-8 w-8 shrink-0 place-items-center rounded-xl text-slate-500 transition hover:bg-slate-100 lg:hidden">
                    <ArrowLeft size={16} />
                </button>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[15px] font-semibold tracking-tight text-slate-900">
                            Жалоба №{item.id}
                        </span>
                        <IosBadge tone={status.tone} className="!py-0.5">{status.label}</IosBadge>
                    </div>
                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                        {item.target_title} · {item.reason_title}
                    </div>
                </div>
                {permissions.can_handle && (
                    <button type="button" onClick={openHistory} title="История" className={iosBtnGhost}>
                        <History size={14} />
                    </button>
                )}
                {permissions.can_delete && (
                    <button type="button" onClick={() => setConfirmDelete(true)} title="Удалить"
                            className="grid h-8 w-8 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-500">
                        <Trash2 size={14} />
                    </button>
                )}
            </div>

            <div className="crm-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
                {/* Ответ для водителя — ради него оператор и открывает жалобу. */}
                {!!answers.length && (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Ответ для водителя</div>
                        {answers.map((message, index) => (
                            <div key={message.id}
                                 className={`rounded-2xl px-4 py-3 ${index === 0
                                     ? 'bg-blue-50 ring-1 ring-blue-100' : 'bg-slate-50'}`}>
                                <div className="flex items-start gap-2">
                                    <div className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-slate-900">
                                        {message.body}
                                    </div>
                                    <CopyButton text={message.body} showToast={showToast} />
                                </div>
                                {message.attachment && (
                                    <Attachment message={message} complaintId={item.id} apiBaseUrl={apiBaseUrl}
                                                headers={headers} showToast={showToast} />
                                )}
                                <div className="mt-1.5 text-[11.5px] text-slate-500">
                                    {message.author_name || 'Группа'} · {fmtShort(message.created_at)}
                                </div>
                            </div>
                        ))}
                    </section>
                )}

                {/* Вопрос группы ждёт оператора — единственный янтарный блок. */}
                {question && (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Группа просит уточнить</div>
                        <div className="rounded-2xl bg-amber-50 px-4 py-3 ring-1 ring-amber-100">
                            <div className="whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-slate-900">
                                {question.body}
                            </div>
                            <div className="mt-1.5 text-[11.5px] text-amber-800/80">
                                {question.author_name || 'Группа'} · {fmtShort(question.created_at)}
                            </div>
                        </div>
                    </section>
                )}

                {/* Цель и причина уже стоят в шапке — второй раз их здесь нет. */}
                <Section title="Жалоба">
                    {item.unit_name && <Row label={UNIT_LABELS[item.unit_kind] || 'Подразделение'}>{item.unit_name}</Row>}
                    {employeeTarget && (
                        <Row label="Сотрудник"
                             action={permissions.can_set_employee ? (
                                 <button type="button" onClick={() => setEmployeeOpen(true)}
                                         title={item.employee_id ? 'Изменить' : 'Определить'}
                                         className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                                     <UserRoundPen size={14} />
                                 </button>
                             ) : null}>
                            {item.employee_name || <span className="text-slate-400">Не определён</span>}
                            {/* «Кто был определён» и «кто фактически» (ТЗ): если СВ
                                по итогам проверки поставил другого — видно обоих. */}
                            {permissions.can_handle && item.reported_employee_name
                                && item.reported_employee_name !== item.employee_name && (
                                <span className="mt-0.5 block text-[12px] text-slate-500">
                                    Оператор указал: {item.reported_employee_name}
                                </span>
                            )}
                        </Row>
                    )}
                    {employeeTarget && permissions.can_handle && item.employee_id && (
                        <Row label="Ответственный">
                            {item.responsible_name || (
                                <span className="text-slate-500">
                                    Не назначен — у сотрудника нет ни СВ, ни главы отдела
                                </span>
                            )}
                        </Row>
                    )}
                    {item.event_at && (
                        <Row label="Когда произошло">
                            {item.event_time_known ? fmtDateTime(item.event_at) : fmtDate(item.event_at)}
                        </Row>
                    )}
                    <Row label="Что произошло">
                        <span className="whitespace-pre-wrap break-words">{item.description}</span>
                    </Row>
                </Section>

                <Section title="Водитель">
                    <Row label="ФИО">{item.driver_name}</Row>
                    <Row label="Телефон" action={<CopyButton text={item.driver_phone} showToast={showToast} />}>
                        <span className="tabular-nums">{item.driver_phone}</span>
                    </Row>
                    {item.driver_ref && <Row label="ID / ВУ"><span className="tabular-nums">{item.driver_ref}</span></Row>}
                    <Row label="Город">{item.city}</Row>
                </Section>

                {/* Итог проверки: ставит тот, кто проверял. Оператору — только
                    сам итог, без принятых мер. */}
                {showResult ? (
                    <Section title="Итог проверки"
                             right={item.result_at ? (
                                 <span className="px-1 text-[11px] text-slate-400">
                                     {item.result_by_name ? `${item.result_by_name} · ` : ''}{fmtShort(item.result_at)}
                                     {item.result_via === 'telegram' ? ' · в группе' : ''}
                                 </span>
                             ) : null}>
                        {permissions.can_handle ? (
                            <div className="space-y-2.5 p-4">
                                <CustomSelect variant="ios"
                                              value={(resultDraft?.code ?? item.result_code) || ''}
                                              onChange={(value) => setResultDraft((prev) => ({
                                                  code: value, note: prev?.note ?? item.result_note ?? '' }))}
                                              options={results.map((r) => ({ value: r.code, label: r.title }))}
                                              placeholder="Выберите итог" ariaLabel="Итог проверки" />
                                <textarea rows={2} value={resultDraft?.note ?? item.result_note ?? ''}
                                          onChange={(e) => setResultDraft((prev) => ({
                                              code: prev?.code ?? item.result_code ?? '', note: e.target.value }))}
                                          placeholder="Принятые меры — если были"
                                          className={`${iosInput} resize-y`} />
                                {resultDraft && (
                                    <div className="flex justify-end gap-2">
                                        <button type="button" onClick={() => setResultDraft(null)}
                                                className={iosBtnGhost} disabled={savingResult}>Отмена</button>
                                        <button type="button" onClick={saveResult}
                                                disabled={savingResult || !resultDraft.code} className={iosBtnPrimary}>
                                            {savingResult ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                                            Сохранить
                                        </button>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <Row label="Итог">{item.result_title}</Row>
                        )}
                    </Section>
                ) : null}

                {/* Работа с сотрудником — лесенка фактов, журнал и три кнопки
                    внизу (владелец, 30.09.2026, вместо «Записать работу»). */}
                {employeeTarget && permissions.can_handle && (
                    <Section title="Работа с сотрудником">
                        <div className="space-y-2 px-4 py-3">
                            {steps.map((step) => (
                                <div key={step.key} className="flex items-center gap-2.5">
                                    {step.done
                                        ? <CheckCircle2 size={16} className="shrink-0 text-emerald-500" />
                                        : <Circle size={16} className="shrink-0 text-slate-300" />}
                                    <span className={`text-[13px] ${step.done ? 'text-slate-900' : 'text-slate-500'}`}>
                                        {step.label}
                                    </span>
                                </div>
                            ))}
                            {!item.employee_id && permissions.can_set_employee && (
                                <button type="button" onClick={() => setEmployeeOpen(true)}
                                        className={`${iosBtnSecondary} mt-1 !py-2`}>
                                    <UserRoundPen size={14} /> Определить сотрудника
                                </button>
                            )}
                        </div>
                        {(data?.work || []).map((entry) => (
                            <div key={entry.id} className="px-4 py-2.5">
                                <div className="flex items-baseline justify-between gap-2">
                                    <span className="text-[13px] font-medium text-slate-900">{entry.action_title}</span>
                                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                                        {fmtShort(entry.created_at)}
                                    </span>
                                </div>
                                <div className="mt-0.5 whitespace-pre-wrap text-[12.5px] leading-snug text-slate-600">
                                    {entry.comment}
                                </div>
                                {entry.outcome && (
                                    <div className="mt-0.5 text-[12.5px] text-slate-600">Результат: {entry.outcome}</div>
                                )}
                                <div className="mt-1 text-[11.5px] text-slate-400">
                                    {entry.created_by_name}
                                    {/* Запись о работе с ДРУГИМ сотрудником (его сменили
                                        после проверки) — с кем она была, пишем прямо, иначе
                                        её легко принять за работу с нынешним. */}
                                    {entry.employee_name && Number(entry.employee_id) !== Number(item.employee_id)
                                        ? ` · с сотрудником ${entry.employee_name}` : ''}
                                    {/* Даты занятия — если их нет в самой записи: у
                                        «Проведён тренинг» с 30.09.2026 они уже в тексте. */}
                                    {entry.training_id ? (() => {
                                        const day = entry.training_date
                                            ? new Date(entry.training_date).toLocaleDateString('ru-RU') : '';
                                        return day && !String(entry.comment || '').includes(day)
                                            ? ` · в «Тренингах»: ${day} ${entry.start_time}–${entry.end_time}`
                                            : ' · в «Тренингах»';
                                    })() : ''}
                                    {entry.need_training ? ' · нужен тренинг' : ''}
                                </div>
                            </div>
                        ))}
                        {permissions.can_record_work && (
                            <div className="grid grid-cols-1 gap-2 px-4 py-3 sm:grid-cols-3">
                                {buttons.map((action) => (
                                    <button key={action.code} type="button"
                                            onClick={() => setWorkCode(action.code)}
                                            disabled={action.disabled} title={action.hint || undefined}
                                            className={`${iosBtnSecondary} !px-2 !py-2 !text-[12.5px]`}>
                                        {action.button}
                                    </button>
                                ))}
                            </div>
                        )}
                    </Section>
                )}

                {/* Переписка с группой. Внутреннее обсуждение — только разбирающему
                    и по кнопке: по умолчанию видно то же, что видит оператор. */}
                {item.requires_processing !== false && (
                    <Section title="Переписка с группой"
                             right={permissions.can_see_internal && internalCount ? (
                                 <button type="button" onClick={() => setShowInternal((v) => !v)}
                                         className="px-1 text-[12px] font-medium text-slate-500 transition hover:text-slate-700">
                                     {showInternal ? 'Скрыть внутреннее' : `Внутреннее обсуждение · ${internalCount}`}
                                 </button>
                             ) : null}>
                        {!thread.length && (
                            <div className="px-4 py-3 text-[12.5px] text-slate-400">
                                {item.delivery_status === 'sent'
                                    ? 'Группа ещё не отвечала'
                                    : 'Жалоба ещё не в группе'}
                            </div>
                        )}
                        {thread.map((message) => (
                            <div key={message.id}
                                 className={`px-4 py-2.5 ${message.kind === 'internal' ? 'bg-slate-50/70' : ''}`}>
                                <div className="flex items-baseline justify-between gap-2">
                                    <span className="min-w-0 truncate text-[12px] font-medium text-slate-500">
                                        {message.author_name || 'Группа'}
                                        <span className="text-slate-400"> · {MESSAGE_KIND_LABELS[message.kind] || ''}</span>
                                    </span>
                                    <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                                        {fmtShort(message.created_at)}
                                    </span>
                                </div>
                                {message.body && (
                                    <div className="mt-0.5 whitespace-pre-wrap break-words text-[13px] leading-snug text-slate-800">
                                        {message.body}
                                    </div>
                                )}
                                {message.attachment && (
                                    <Attachment message={message} complaintId={item.id} apiBaseUrl={apiBaseUrl}
                                                headers={headers} showToast={showToast} />
                                )}
                            </div>
                        ))}
                    </Section>
                )}

                {/* Служебное: кто принял, куда ушло. Мелко, внизу — это справка. */}
                <div className="space-y-1 px-1 pb-2 text-[11.5px] leading-snug text-slate-400">
                    <div>Принял {item.created_by_name || '—'} · {fmtDateTime(item.created_at)}</div>
                    {/* Решённую при проверке подписывает блок «Итог проверки»
                        (кто и когда) — второй строкой о том же моменте здесь
                        она не повторяется. */}
                    {item.review_state === REVIEW_SENT && item.review_by_name && item.review_at && (
                        <div>
                            Проверил {item.review_by_name} · {fmtDateTime(item.review_at)} — отправил в группу
                        </div>
                    )}
                    {underReview ? (
                        // У проверяющего внизу панель решения — там всё сказано.
                        permissions.can_review ? null : (
                            <div>Ждёт проверки супервайзером — в группу уйдёт, только если он решит</div>
                        )
                    ) : item.requires_processing === false ? (
                        isRecorded(item)
                            ? <div>Жалоба зафиксирована для аналитики — в группу не отправлялась</div>
                            : null
                    ) : item.delivery_status === 'sent' ? (
                        <div>В группе «{item.tg_chat_title || 'Жалобы'}»</div>
                    ) : (
                        <div className="flex flex-wrap items-center gap-2 text-rose-500">
                            <span>Не ушла в группу{item.delivery_error ? `: ${item.delivery_error}` : ''}</span>
                            <button type="button" onClick={resend} disabled={resending}
                                    className="inline-flex items-center gap-1 font-semibold text-blue-600 hover:text-blue-700">
                                {resending ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
                                Отправить ещё раз
                            </button>
                        </div>
                    )}
                    {item.closed_at && item.status === 'closed' && !isRecorded(item)
                        && item.review_state !== REVIEW_RESOLVED && (
                        <div>Отработана {fmtDateTime(item.closed_at)}</div>
                    )}
                </div>
            </div>

            {/* Решение по жалобе на проверке (Яндекс, владелец 30.09.2026): стоит
                внимания — в группу, нет — «Решено» с итогом. Внизу, на месте поля
                ответа: это главное, что здесь можно сделать. */}
            {permissions.can_review && (
                <ReviewPanel busy={reviewBusy}
                             hint="Стоит внимания — отправьте в группу: дальше она пойдёт как любая жалоба. Нет — «Решено» с итогом: жалоба закроется и в группу не уйдёт."
                             sendBlocked={meta?.group && !meta.group.ready
                                 ? 'Telegram-группа для жалоб не выбрана' : null}
                             onResolve={() => setResolveOpen(true)}
                             onSend={() => review('send')} />
            )}

            {/* Ответ в группу: на открытый вопрос — или дополнение. */}
            {permissions.can_write && (
                <div className="shrink-0 border-t border-slate-100 px-3 py-2.5">
                    {replyFile && (
                        <div className="mb-2 flex items-center gap-2 rounded-xl bg-slate-50 px-3 py-1.5 text-[12px] text-slate-600">
                            <Paperclip size={12} /> <span className="min-w-0 flex-1 truncate">{replyFile.name}</span>
                            <button type="button" onClick={() => setReplyFile(null)} aria-label="Убрать файл">
                                <X size={13} className="text-slate-400" />
                            </button>
                        </div>
                    )}
                    <div className="flex items-end gap-2">
                        <label className="grid h-10 w-10 shrink-0 cursor-pointer place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                               title="Приложить файл">
                            <Paperclip size={16} />
                            <input type="file" className="hidden"
                                   onChange={(e) => setReplyFile(e.target.files?.[0] || null)} />
                        </label>
                        <textarea rows={1} value={reply} onChange={(e) => setReply(e.target.value)}
                                  onKeyDown={(e) => {
                                      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
                                  }}
                                  placeholder={question ? 'Ответ на вопрос группы' : 'Написать в группу'}
                                  className={`${iosInput} max-h-40 min-h-[40px] resize-none`} />
                        <button type="button" onClick={send} disabled={sending || (!reply.trim() && !replyFile)}
                                aria-label="Отправить"
                                className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-600 text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.97] disabled:opacity-40">
                            {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                        </button>
                    </div>
                </div>
            )}

            <WorkModal code={workCode} onClose={() => setWorkCode(null)} complaint={item}
                       apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                       onSaved={(next) => applyItem(next)} />
            <ReviewResolveModal open={resolveOpen} onClose={() => setResolveOpen(false)}
                                busy={reviewBusy}
                                subtitle="Жалоба закроется с вашим итогом и в группу не уйдёт"
                                onResolve={(note) => review('resolve', note)} />
            <EmployeeModal open={employeeOpen} onClose={() => setEmployeeOpen(false)} complaint={item}
                           meta={meta} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                           onSaved={(next) => { applyItem(next); load(true); }} />

            <IosModal open={historyOpen} onClose={() => setHistoryOpen(false)} title="История жалобы"
                      subtitle={`№${item.id}`}>
                {!events && (
                    <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-slate-400">
                        <Loader2 size={14} className="animate-spin" /> Загрузка…
                    </div>
                )}
                {events && !events.length && (
                    <div className="py-8 text-center text-[13px] text-slate-400">Событий нет</div>
                )}
                {events && !!events.length && (
                    <div className={`${iosCard} divide-y divide-slate-100`}>
                        {events.map((event) => (
                            <div key={event.id} className="flex items-baseline gap-3 px-4 py-2.5">
                                <span className="w-[92px] shrink-0 text-[11.5px] tabular-nums text-slate-400">
                                    {fmtShort(event.created_at)}
                                </span>
                                <span className="min-w-0 flex-1 text-[13px] text-slate-800">
                                    {eventText(event, meta)}
                                    {event.actor_name && <span className="text-slate-500"> · {event.actor_name}</span>}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </IosModal>

            <IosModal open={confirmDelete} onClose={() => setConfirmDelete(false)}
                      title={`Удалить жалобу №${item.id}?`} maxWidth="max-w-md"
                      footer={(
                          <>
                              <button type="button" onClick={() => setConfirmDelete(false)} className={iosBtnSecondary}>
                                  Отмена
                              </button>
                              <button type="button" onClick={remove} className={iosBtnDanger}>
                                  <Trash2 size={14} /> Удалить
                              </button>
                          </>
                      )}>
                <div className="text-[13px] leading-relaxed text-slate-600">
                    Жалоба пропадёт из списка, аналитики и выгрузки вместе с перепиской и историей.
                    Сообщение в Telegram-группе останется, а занятия в «Тренингах» — тоже: они были
                    проведены и идут в часы.
                </div>
            </IosModal>
        </div>
    );
}

