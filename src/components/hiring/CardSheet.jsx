import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
    ArrowRight, ExternalLink, Info, Loader2, MessageCircle, MessageSquareText, Pencil, Phone, PhoneIncoming, PhoneMissed,
    PhoneOutgoing, Play, Repeat, UserCog, X,
} from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import IosTimePicker from '../ui/TimePicker';
import { IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosGroupLabel } from '../ui/ios';
import {
    ASSIGNMENT_LABELS, CALL_DIRECTION_LABELS, CALL_RESULT_LABELS, CALL_RESULT_TONES, FINAL_STAGES, MOTIVATION_LABELS,
    MOTIVATION_TONES, POOL_STAGES, STAGE_LABELS, STAGE_TONES, SUBSTAGE_LABELS, SUBSTAGE_TONES, TOUCH_LABELS, addDays,
    codeOf, errorOf, fmtDuration, fullDate, joinDateTime, longTime, openICorePhoneCall, reasonKindFor, shortTime,
    splitDateTime, todayIso,
} from './hiringMeta';
import { DATE_TRIGGER, FIELD, ICON_BTN, Label, Loading, Pill, ReasonDialog, TIME_INPUT, TOOLBAR_BTN } from './hiringUi';

/*
 * Карточка кандидата (ТЗ 6): одно окно на весь раздел. Внутри — поля по группам ТЗ 6.1,
 * лента касаний и входов в этапы, переходы по правилам ТЗ 4.2 с их обязательными полями,
 * смены на линии и подтверждение «Отработал 7 дней» (руководитель отдела), мотивация.
 *
 * Связь с кандидатом — кнопками «Позвонить» (icore-phone:call — звонок уходит в iCore Phone на
 * этом компьютере) и «Написать в WhatsApp» (WhatsApp Web). Касание пишется в ленту при нажатии:
 * звонок — с результатом, который спрашивается сразу (дозвон / недозвон), переписка — фактом
 * открытия чата; звонок из телефонии потом дописывает отметку исходом, длительностью и записью.
 * Телефон удалённому рекрутеру виден только в своих карточках — сервер его иначе не присылает.
 */

const InfoRow = ({ label, children }) => (
    <div className="flex items-start justify-between gap-3 py-1.5 text-[13px]">
        <span className="shrink-0 text-slate-500">{label}</span>
        <span className="min-w-0 text-right text-slate-900">{children ?? '—'}</span>
    </div>
);

const Section = ({ title, right, children }) => (
    <section className="space-y-1.5">
        {(title || right) && (
            <div className="flex items-end justify-between gap-2">
                <div className={iosGroupLabel}>{title}</div>
                {right}
            </div>
        )}
        <div className="rounded-2xl bg-slate-50 px-4 py-2 ring-1 ring-slate-200/60">{children}</div>
    </section>
);

const DateTimeField = ({ date, time, onDate, onTime }) => (
    <div className="grid grid-cols-[1fr_112px] gap-2">
        <IosDatePicker value={date} onChange={onDate} allowEmpty triggerClassName={DATE_TRIGGER} ariaLabel="Дата" />
        <IosTimePicker value={time} onChange={onTime} className="w-full" inputClassName={TIME_INPUT} ariaLabel="Время" />
    </div>
);

/* Переход с его обязательными полями (ТЗ 4.1, 4.2). */
const MoveDialog = ({ open, onClose, card, target, boot, caps, api, toast, onDone }) => {
    const [form, setForm] = useState({});
    const [saving, setSaving] = useState(false);
    useEffect(() => {
        if (!open) return;
        const initial = { office_date: '', office_time: '10:00', group_id: '', initiator: '', reason_id: '', comment: '', day: '',
            date: '', recruiter_id: card?.recruiter_id ? String(card.recruiter_id) : '' };
        if (target === 'on_training') initial.date = card?.group_start_date || todayIso();
        if (target === 'on_line') initial.date = todayIso();
        if (target === 'refused' && card?.stage === 'invited_office' && card?.substage === 'no_show') initial.presetReason = 'Не пришел в офис';
        if (target === 'refused' && card?.stage === 'invited_training') initial.presetReason = 'Не пришел на обучение';
        if (target === 'refused' && card?.no_answer_suggest) initial.presetReason = 'Не выходит на связь';
        setForm(initial);
    }, [open, target, card]);
    const reasons = boot.dictionaries?.reasons || [];
    const groups = (boot.dictionaries?.groups || []).filter((g) => g.status === 'active');
    const kind = reasonKindFor(target, form.initiator);
    const reasonOptions = reasons.filter((r) => r.kind === kind && r.is_active !== false).map((r) => ({ value: String(r.id), label: r.name }));
    useEffect(() => {
        if (!form.presetReason || form.reason_id || !form.initiator) return;
        const preset = reasons.find((r) => r.kind === kind && r.name === form.presetReason);
        if (preset) setForm((f) => ({ ...f, reason_id: String(preset.id) }));
    }, [form.initiator, form.presetReason, form.reason_id, kind, reasons]);
    useEffect(() => {
        if (target === 'refused' && form.presetReason && !form.initiator) setForm((f) => ({ ...f, initiator: 'candidate' }));
    }, [target, form.presetReason, form.initiator]);
    const reason = reasons.find((r) => String(r.id) === String(form.reason_id));
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));

    const submit = async () => {
        const payload = { target };
        if (target === 'invited_office') payload.office_visit_at = joinDateTime(form.office_date, form.office_time);
        if (target === 'invited_training') payload.group_id = form.group_id ? Number(form.group_id) : null;
        if (target === 'on_training') payload.training_started_at = form.date;
        if (target === 'on_line') payload.line_started_at = form.date;
        if (target === 'refused' || target === 'dropped') {
            payload.initiator = form.initiator;
            payload.reason_id = form.reason_id ? Number(form.reason_id) : null;
            payload.comment = form.comment || null;
            if (target === 'dropped') payload.day = form.day ? Number(form.day) : null;
        }
        if (target === 'in_work') { payload.reactivate = true; payload.recruiter_id = form.recruiter_id ? Number(form.recruiter_id) : null; }
        setSaving(true);
        try {
            const { data } = await api.post(`/cards/${card.id}/move`, payload);
            toast(`Карточка — «${STAGE_LABELS[target]}»`, 'success');
            onDone(data);
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Переход не выполнен'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const title = `В «${STAGE_LABELS[target] || target}»`;
    const subtitle = {
        invited_office: 'Дата и время собеседования в офисе', invited_training: 'Группа на обучение и дата старта',
        refused: 'Сначала инициатор, затем причина из его списка', dropped: 'Инициатор, причина и день отсева',
        on_training: 'Дата первого дня обучения', on_line: 'Дата выхода на линию',
        hired: `Семь отработанных смен подтверждает руководитель отдела — отмечено ${card?.shifts_done || 0} из ${boot.meta.shifts_to_hire}`,
        in_work: 'Повторная активация: закрепление сохраняется или назначается заново',
    }[target];
    const recruiters = boot.dictionaries?.recruiters || [];
    return (
        <IosModal open={open} onClose={onClose} title={title} subtitle={subtitle}
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={submit} disabled={saving}>Перевести</button>
                      </div>
                  )}>
            <div className="space-y-3">
                {target === 'invited_office' && (
                    <div>
                        <Label required>Собеседование в офисе</Label>
                        <DateTimeField date={form.office_date} time={form.office_time} onDate={set('office_date')} onTime={set('office_time')} />
                    </div>
                )}
                {target === 'invited_training' && (
                    <div>
                        <Label required hint="Группа задаёт дату старта и тренера, который отметит выход на обучение.">Группа на обучение</Label>
                        <CustomSelect variant="ios" value={form.group_id} onChange={set('group_id')} placeholder="Выберите группу" invalid={!form.group_id}
                                      options={groups.map((g) => ({ value: String(g.id), label: `${g.name} · ${fullDate(g.start_date)}`, meta: g.trainer_name || '' }))} />
                        {groups.length === 0 && <div className="mt-1 text-[12px] text-rose-600">Активных групп нет — их заводит руководитель рекрутинга в «Настройках»</div>}
                    </div>
                )}
                {(target === 'on_training' || target === 'on_line') && (
                    <div>
                        <Label required>{target === 'on_training' ? 'Первый день обучения' : 'Дата выхода на линию'}</Label>
                        <IosDatePicker value={form.date} onChange={set('date')} triggerClassName={DATE_TRIGGER} ariaLabel="Дата" />
                    </div>
                )}
                {(target === 'refused' || target === 'dropped') && (
                    <>
                        <div>
                            <Label required hint={target === 'refused'
                                ? 'Наш отказ — не подошёл нам; отказ кандидата — не подошли мы. У каждого свой список причин.'
                                : 'Сам — кандидат ушёл по своей инициативе; мы — отчислен нами. У каждого свой список причин.'}>Инициатор</Label>
                            <IosSegmented stretch value={form.initiator} onChange={(v) => setForm((f) => ({ ...f, initiator: v, reason_id: '' }))} ariaLabel="Инициатор"
                                          options={(boot.meta.substages[target] || []).map((item) => ({ value: item.code, label: item.label }))} />
                        </div>
                        <div>
                            <Label required>Причина</Label>
                            <CustomSelect variant="ios" value={form.reason_id} onChange={set('reason_id')} placeholder={form.initiator ? 'Выберите причину' : 'Сначала инициатор'}
                                          options={reasonOptions} disabled={!form.initiator} invalid={Boolean(form.initiator) && !form.reason_id} />
                        </div>
                        {target === 'dropped' && (
                            <div>
                                <Label required hint="Номер дня обучения или день на линии, на котором кандидат ушёл.">День отсева</Label>
                                <input className={FIELD} inputMode="numeric" value={form.day} onChange={(e) => set('day')(e.target.value.replace(/\D/g, '').slice(0, 3))} placeholder="например, 3" />
                            </div>
                        )}
                        <div>
                            <Label required={Boolean(reason?.requires_comment)}>Комментарий</Label>
                            <textarea className={`${FIELD} min-h-[72px]`} value={form.comment} onChange={set('comment')} />
                        </div>
                    </>
                )}
                {target === 'hired' && (
                    <div className="text-[13px] text-slate-700">
                        После подтверждения найм закрыт: кандидат засчитывается в факт месячной задачи, мотивация засчитывается
                        закреплённому рекрутеру{card?.recruiter_name ? ` — ${card.recruiter_name}` : ''}.
                    </div>
                )}
                {target === 'in_work' && (
                    <div>
                        <Label required>Закрепить за рекрутером</Label>
                        <CustomSelect variant="ios" value={form.recruiter_id} onChange={set('recruiter_id')} placeholder="Выберите рекрутера" searchable
                                      options={recruiters.map((r) => ({ value: String(r.id), label: r.name }))} invalid={!form.recruiter_id} />
                    </div>
                )}
            </div>
        </IosModal>
    );
};

/* Панель звонка в ленте касаний — после «Позвонить» (владелец 30.10.2026: исход подтягивается из
   телефонии сам, рекрутер дописывает недостающее). Пока телефония молчит, касание «ждёт исход»;
   здесь — комментарий, следующий контакт и, если звонок прошёл мимо телефонии, исход руками. */
const CallPanel = ({ touch, call, api, toast, card, onDone, onClose }) => {
    const [comment, setComment] = useState('');
    const [next, setNext] = useState({ date: '', time: '10:00' });
    const [saving, setSaving] = useState(false);
    const waiting = !touch.call_id && !touch.result;
    // Исход ждём из телефонии; ручной выбор показывается, только если за 45 секунд ничего не пришло
    // (рекрутер не на линии удалённого КЦ или звонок не прошёл).
    const [showManual, setShowManual] = useState(false);
    useEffect(() => {
        if (!waiting) return undefined;
        const timer = setTimeout(() => setShowManual(true), 45000);
        return () => clearTimeout(timer);
    }, [waiting]);
    const patch = async (payload, closeAfter) => {
        setSaving(true);
        try {
            await api.patch(`/cards/${card.id}/touches/${touch.id}`, payload);
            onDone();
            if (closeAfter) onClose();
        } catch (error) {
            toast(errorOf(error, 'Не сохранилось'), 'error');
        } finally {
            setSaving(false);
        }
    };
    const save = () => {
        const payload = {};
        // У звонка из телефонии первая строка тела — его исход; комментарий дописывается под ней.
        if (comment.trim()) payload.body = touch.call_id && touch.body ? `${touch.body.split('\n')[0]}\n${comment.trim()}` : comment.trim();
        if (next.date) payload.next_contact_at = joinDateTime(next.date, next.time);
        if (!Object.keys(payload).length) { onClose(); return; }
        patch(payload, true);
    };
    const result = call ? (call.result || 'other') : null;
    return (
        <div className="mb-3 rounded-2xl bg-white p-3 ring-1 ring-blue-200">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
                <Phone size={14} className="text-slate-500" />
                <span className="font-medium text-slate-900">{call ? CALL_DIRECTION_LABELS[call.direction] || 'Звонок' : 'Звонок'}</span>
                {waiting ? (
                    <span className="inline-flex items-center gap-1.5 text-slate-500"><Loader2 size={13} className="animate-spin" /> ждём исход из телефонии</span>
                ) : call ? (
                    <>
                        <Pill tone={CALL_RESULT_TONES[result]}>{CALL_RESULT_LABELS[result] || result}</Pill>
                        {call.billsec > 0 && <span className="tabular-nums text-slate-500">{fmtDuration(call.billsec)}</span>}
                    </>
                ) : (
                    <Pill tone={touch.result === 'dial' ? 'green' : 'amber'}>{SUBSTAGE_LABELS[touch.result]}</Pill>
                )}
                <button type="button" className={`${ICON_BTN} ml-auto`} aria-label="Закрыть" onClick={onClose}><X size={14} /></button>
            </div>
            <div className="mt-2 space-y-3">
                <div>
                    <Label>Комментарий</Label>
                    <input className={FIELD} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="О чём договорились"
                           onKeyDown={(e) => { if (e.key === 'Enter') save(); }} />
                </div>
                <div>
                    <Label hint="Наступит дата — напомним в колоколе и покажем в задачах на сегодня.">Следующий контакт</Label>
                    <div className="flex flex-wrap items-center gap-1.5">
                        {[['Завтра', 1], ['Через 3 дня', 3], ['Через неделю', 7]].map(([label, days]) => (
                            <button key={label} type="button" className={TOOLBAR_BTN} onClick={() => setNext((n) => ({ ...n, date: addDays(todayIso(), days) }))}>{label}</button>
                        ))}
                        <div className="w-full sm:w-72">
                            <DateTimeField date={next.date} time={next.time} onDate={(v) => setNext((n) => ({ ...n, date: v }))} onTime={(v) => setNext((n) => ({ ...n, time: v }))} />
                        </div>
                    </div>
                </div>
                {waiting && showManual && (
                    <div>
                        <Label hint="Исход и длительность приходят из телефонии сами, если рекрутер посажен на линию удалённого КЦ. Звонок прошёл мимо неё — отметьте исход здесь.">Если телефония не увидела звонок</Label>
                        <IosSegmented value="" onChange={(value) => patch({ result: value }, false)} ariaLabel="Исход звонка" size="xs"
                                      options={[{ value: 'dial', label: 'Дозвон' }, { value: 'no_answer', label: 'Недозвон' }]} />
                    </div>
                )}
            </div>
            <div className="mt-3 flex justify-end gap-2">
                <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={saving}>Закрыть</button>
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>Сохранить</button>
            </div>
        </div>
    );
};

const EditDialog = ({ open, onClose, api, toast, card, boot, onDone }) => {
    const [form, setForm] = useState({});
    const [saving, setSaving] = useState(false);
    useEffect(() => {
        if (!open || !card) return;
        const next = splitDateTime(card.next_contact_at);
        const office = splitDateTime(card.office_visit_at);
        setForm({ full_name: card.full_name || '', whatsapp: card.whatsapp || '', city: card.city || '',
            vacancy_id: card.vacancy_id ? String(card.vacancy_id) : '', referred_by: card.referred_by || '', comment: card.comment || '',
            next_date: next.date, next_time: next.time || '10:00', office_date: office.date, office_time: office.time || '10:00' });
    }, [open, card]);
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
    const vacancies = (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false || String(v.id) === form.vacancy_id);
    const submit = async () => {
        setSaving(true);
        try {
            const payload = { full_name: form.full_name, whatsapp: form.whatsapp || null, city: form.city || null,
                vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null, referred_by: form.referred_by || null, comment: form.comment || null,
                next_contact_at: form.next_date ? joinDateTime(form.next_date, form.next_time) : null };
            if (card.stage === 'invited_office' && form.office_date) payload.office_visit_at = joinDateTime(form.office_date, form.office_time);
            await api.patch(`/cards/${card.id}`, payload);
            onDone();
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Не сохранилось'), 'error');
        } finally {
            setSaving(false);
        }
    };
    return (
        <IosModal open={open} onClose={onClose} title="Изменить карточку"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={submit} disabled={saving}>Сохранить</button>
                      </div>
                  )}>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2"><Label>ФИО</Label><input className={FIELD} value={form.full_name || ''} onChange={set('full_name')} /></div>
                <div><Label>Город</Label><input className={FIELD} value={form.city || ''} onChange={set('city')} /></div>
                <div><Label>WhatsApp</Label><input className={FIELD} value={form.whatsapp || ''} onChange={set('whatsapp')} placeholder="если отличается" inputMode="tel" /></div>
                <div>
                    <Label>Вакансия</Label>
                    <CustomSelect variant="ios" value={form.vacancy_id || ''} onChange={set('vacancy_id')} placeholder="Не указана"
                                  options={[{ value: '', label: 'Не указана' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
                </div>
                <div><Label>Кто привёл</Label><input className={FIELD} value={form.referred_by || ''} onChange={set('referred_by')} /></div>
                <div className="sm:col-span-2">
                    <Label>Следующий контакт</Label>
                    <DateTimeField date={form.next_date} time={form.next_time} onDate={set('next_date')} onTime={set('next_time')} />
                </div>
                {card?.stage === 'invited_office' && (
                    <div className="sm:col-span-2">
                        <Label hint="Не пришёл — назначьте новую дату: подстатус снова станет «Ожидается».">Визит в офис</Label>
                        <DateTimeField date={form.office_date} time={form.office_time} onDate={set('office_date')} onTime={set('office_time')} />
                    </div>
                )}
                <div className="sm:col-span-2"><Label>Комментарий</Label><textarea className={`${FIELD} min-h-[72px]`} value={form.comment || ''} onChange={set('comment')} /></div>
            </div>
        </IosModal>
    );
};

const ReassignDialog = ({ open, onClose, api, toast, card, boot, onDone }) => {
    const [recruiterId, setRecruiterId] = useState('');
    const [comment, setComment] = useState('');
    const [saving, setSaving] = useState(false);
    useEffect(() => { if (open) { setRecruiterId(''); setComment(''); } }, [open]);
    const submit = async (toPool) => {
        if (!comment.trim()) return toast('Укажите причину', 'error');
        if (!toPool && !recruiterId) return toast('Выберите рекрутера', 'error');
        setSaving(true);
        try {
            await api.post(`/cards/${card.id}/reassign`, { recruiter_id: toPool ? null : Number(recruiterId), comment });
            toast(toPool ? 'Карточка возвращена в пул' : 'Карточка переназначена', 'success');
            onDone();
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Не удалось'), 'error');
        } finally {
            setSaving(false);
        }
        return undefined;
    };
    const canReturn = ['in_work', 'invited_office', 'invited_training'].includes(card?.stage);
    return (
        <IosModal open={open} onClose={onClose} title="Переназначить" subtitle="История закреплений сохраняется, причина — обязательна"
                  footer={(
                      <div className="flex flex-wrap justify-end gap-2">
                          {canReturn && <button type="button" className={iosBtnSecondary} onClick={() => submit(true)} disabled={saving}>Вернуть в пул</button>}
                          <button type="button" className={iosBtnPrimary} onClick={() => submit(false)} disabled={saving}>Переназначить</button>
                      </div>
                  )}>
            <div className="space-y-3">
                <div>
                    <Label>Новый рекрутер</Label>
                    <CustomSelect variant="ios" value={recruiterId} onChange={setRecruiterId} placeholder="Выберите рекрутера" searchable
                                  options={(boot.dictionaries?.recruiters || []).filter((r) => r.id !== card?.recruiter_id).map((r) => ({ value: String(r.id), label: r.name }))} />
                </div>
                <div>
                    <Label required>Причина</Label>
                    <textarea className={`${FIELD} min-h-[64px]`} value={comment} onChange={(e) => setComment(e.target.value)} />
                </div>
            </div>
        </IosModal>
    );
};

/* Звонок из телефонии в ленте — как в журнале «Удаленного КЦ»: направление, исход, длительность,
   линия, запись по нажатию (ссылка у Binotel живёт около часа, поэтому спрашивается на клик). */
const CallRow = ({ item, call, api }) => {
    const [rec, setRec] = useState({});
    const load = async () => {
        setRec({ loading: true });
        try {
            const { data } = await api.get(`/calls/${call.id}/recording`);
            setRec({ url: data.url });
        } catch (error) {
            setRec({ error: errorOf(error, 'АТС не отдала запись') });
        }
    };
    const result = call.result || 'other';
    return (
        <div className="text-[13px] text-slate-800">
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                <span className="font-medium">{CALL_DIRECTION_LABELS[call.direction] || 'Звонок'}</span>
                <Pill tone={CALL_RESULT_TONES[result]}>{CALL_RESULT_LABELS[result] || result}</Pill>
                {call.billsec > 0 && <span className="tabular-nums text-slate-500">{fmtDuration(call.billsec)}</span>}
                <span className="text-slate-400">· линия {call.internal_number}{item.author ? ` · ${item.author}` : ''}</span>
            </div>
            {item.body && item.body.split('\n').slice(1).join('\n') && (
                <div className="whitespace-pre-wrap text-slate-600">{item.body.split('\n').slice(1).join('\n')}</div>
            )}
            {call.recording === 'available' && (
                <div className="mt-1.5">
                    {rec.url ? (
                        <audio controls autoPlay preload="auto" src={rec.url} className="h-9 w-full max-w-md" aria-label="Запись разговора" />
                    ) : (
                        <button type="button" onClick={load} disabled={rec.loading}
                                className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12.5px] font-medium text-slate-700 transition hover:bg-slate-200 active:scale-[0.98] disabled:opacity-60">
                            {rec.loading ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} className="fill-current" />}
                            {rec.loading ? 'Запрашиваем у АТС…' : 'Прослушать запись'}
                        </button>
                    )}
                    {rec.error && <div className="mt-1 text-[12px] text-rose-600">{rec.error}</div>}
                </div>
            )}
        </div>
    );
};

/* Значок события ленты — серый, чтобы лента читалась с одного взгляда: звонок (и куда он
   прошёл), переписка, комментарий, повторный отклик. Цвет остаётся за исходом. */
const timelineIcon = (item, call) => {
    if (item.kind === 'call') {
        if (call) return call.direction === 'in' ? PhoneIncoming : (call.result === 'answered' ? PhoneOutgoing : PhoneMissed);
        return item.result === 'no_answer' ? PhoneMissed : PhoneOutgoing;
    }
    if (item.kind === 'whatsapp') return MessageCircle;
    if (item.kind === 'comment') return MessageSquareText;
    if (item.kind === 'repeat_response') return Repeat;
    return Info;
};

const SMALL_BTN = 'inline-flex h-7 items-center rounded-lg bg-white px-2.5 text-[12px] font-medium text-slate-700 ring-1 ring-slate-200/70 transition hover:bg-slate-50 active:scale-[0.98]';

/* Лента касаний: свежие сверху. Звонок без исхода ждёт телефонию; если звонок прошёл мимо неё,
   закреплённый рекрутер ставит исход прямо в строке. */
const Timeline = ({ touches, calls = [], api, canEdit, onSetResult }) => {
    const callsByTouch = useMemo(() => Object.fromEntries(calls.filter((c) => c.touch_id).map((c) => [c.touch_id, c])), [calls]);
    const items = useMemo(() => [...touches].sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : b.id - a.id)), [touches]);
    if (!items.length) return <div className="py-4 text-center text-[12.5px] text-slate-500">Касаний пока нет</div>;
    return (
        <ul className="divide-y divide-slate-200/70">
            {items.map((item) => {
                const call = item.kind === 'call' ? callsByTouch[item.id] : null;
                const pending = item.kind === 'call' && !call && !item.result;
                const Icon = timelineIcon(item, call);
                return (
                    <li key={item.id} className="flex gap-3 py-2">
                        <span className="w-[88px] shrink-0 text-[12px] tabular-nums text-slate-400">{shortTime(item.at)}</span>
                        <span className="mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white text-slate-400 ring-1 ring-slate-200/70">
                            <Icon size={11} strokeWidth={2.25} />
                        </span>
                        <div className="min-w-0 grow">
                            {call ? <CallRow item={item} call={call} api={api} /> : (
                                <div className="text-[13px] text-slate-800">
                                    <span className="font-medium">{TOUCH_LABELS[item.kind] || item.kind}</span>
                                    {item.kind === 'call' && item.result ? <Pill tone={item.result === 'dial' ? 'green' : 'amber'} className="ml-1.5">{SUBSTAGE_LABELS[item.result]}</Pill> : null}
                                    {pending && <Pill className="ml-1.5">ждём исход из телефонии</Pill>}
                                    {item.author_name ? <span className="text-slate-400"> · {item.author_name}</span> : null}
                                    {item.body && <div className="whitespace-pre-wrap text-slate-600">{item.body}</div>}
                                    {pending && canEdit && (
                                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px] text-slate-500">
                                            <span>Звонок прошёл мимо телефонии — исход:</span>
                                            <button type="button" className={SMALL_BTN} onClick={() => onSetResult(item.id, 'dial')}>Дозвон</button>
                                            <button type="button" className={SMALL_BTN} onClick={() => onSetResult(item.id, 'no_answer')}>Недозвон</button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </li>
                );
            })}
        </ul>
    );
};

/* Вкладка «История»: входы в этапы и закрепления — то, что в ленте касаний только мешало бы. */
const StageHistory = ({ stages, assignments }) => (
    <div className="grid gap-4 md:grid-cols-2">
        <Section title="Этапы">
            {stages.length === 0 ? <div className="py-3 text-center text-[12.5px] text-slate-500">Переходов ещё не было</div> : (
                <ul className="divide-y divide-slate-200/70">
                    {[...stages].reverse().map((row) => (
                        <li key={row.id} className="flex gap-3 py-2 text-[13px]">
                            <span className="w-[88px] shrink-0 text-[12px] tabular-nums text-slate-400">{shortTime(row.entered_at)}</span>
                            <div className="min-w-0 text-slate-700">
                                <span className="font-medium">{STAGE_LABELS[row.stage]}</span>
                                {row.substage ? ` · ${SUBSTAGE_LABELS[row.substage]}` : ''}
                                {row.by_user_name ? <span className="text-slate-400"> · {row.by_user_name}</span> : null}
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </Section>
        <Section title="Закрепления">
            {assignments.length === 0 ? <div className="py-3 text-center text-[12.5px] text-slate-500">Карточку ещё не брали в работу</div> : (
                <ul className="divide-y divide-slate-200/70">
                    {[...assignments].reverse().map((row) => (
                        <li key={row.id} className="flex gap-3 py-2 text-[13px]">
                            <span className="w-[88px] shrink-0 text-[12px] tabular-nums text-slate-400">{shortTime(row.assigned_at)}</span>
                            <div className="min-w-0 text-slate-700">
                                <span className="font-medium">{ASSIGNMENT_LABELS[row.reason] || row.reason}</span>
                                {row.recruiter_name ? ` · ${row.recruiter_name}` : ''}
                                {row.comment ? <div className="text-slate-500">{row.comment}</div> : null}
                            </div>
                        </li>
                    ))}
                </ul>
            )}
        </Section>
    </div>
);

const CardSheet = ({ api, toast, boot, caps, cardId, onClose, onChanged, initialMove = null, onMoveConsumed, initialCall = false, onCallConsumed }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState('card');
    const [move, setMove] = useState(null);
    const [editing, setEditing] = useState(false);
    const [reassigning, setReassigning] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const [comment, setComment] = useState('');
    const [savingComment, setSavingComment] = useState(false);
    const [shiftDate, setShiftDate] = useState(todayIso);
    // Касание звонка, с которым рекрутер работает сейчас (панель в ленте).
    const [activeCallId, setActiveCallId] = useState(null);

    const load = useCallback(() => {
        if (!cardId) return undefined;
        return api.get(`/cards/${cardId}`)
            .then(({ data: payload }) => { setData(payload); setError(''); })
            .catch((requestError) => setError(errorOf(requestError, 'Карточка не открылась')));
    }, [api, cardId]);

    useEffect(() => {
        // Новая карточка — чистое окно: вкладка, черновик комментария, дата смены и открытые диалоги
        // прошлой карточки не должны переехать в следующую (окно смонтировано всегда).
        setData(null);
        setError('');
        setTab('card');
        setMove(null);
        setComment('');
        setShiftDate(todayIso());
        setEditing(false);
        setReassigning(false);
        setCancelling(false);
        setActiveCallId(null);
        if (cardId) load();
    }, [cardId, load]);

    useEffect(() => {
        if (initialMove && data?.card) { setMove(initialMove); onMoveConsumed?.(); }
    }, [initialMove, data?.card, onMoveConsumed]);

    const changed = useCallback(() => { load(); onChanged(); }, [load, onChanged]);

    const card = data?.card;
    const actor = data?.actor || [];
    const isOwnerOrLead = actor.includes('owner') || actor.includes('lead');
    const canMoveTo = (target) => {
        if (!card) return false;
        if (!(card.targets || []).includes(target)) return false;
        if (target === 'hired') return actor.includes('dept_head');
        if (target === 'in_work') return POOL_STAGES.includes(card.stage) ? caps.take : actor.includes('lead');
        if (target === 'on_training') return actor.includes('trainer') || isOwnerOrLead;
        if (target === 'on_line' || (target === 'dropped' && card.stage === 'on_training')) return actor.includes('trainer') || isOwnerOrLead;
        if (target === 'dropped' && card.stage === 'on_line') return actor.includes('dept_head') || isOwnerOrLead;
        return isOwnerOrLead;
    };
    const targets = card ? (card.targets || []).filter(canMoveTo) : [];

    // Сам вызов уже ушёл в iCore Phone (кнопкой здесь или со стола/доски). В ленте появляется касание
    // «ждём исход из телефонии», а панель над лентой — для комментария и следующего контакта.
    const startCall = useCallback(async () => {
        if (!card) return;
        try {
            const { data: payload } = await api.post(`/cards/${card.id}/touches`, { kind: 'call' });
            const mine = [...(payload.touches || [])].reverse().find((t) => t.kind === 'call' && !t.result && !t.call_id);
            setActiveCallId(mine?.id || null);
            setTab('touches');
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Звонок не записан'), 'error');
        }
    }, [api, card, changed, toast]);
    useEffect(() => {
        if (initialCall && data?.card) { onCallConsumed?.(); startCall(); }
    }, [initialCall, data?.card, onCallConsumed, startCall]);

    const activeTouch = useMemo(() => (data?.touches || []).find((t) => t.id === activeCallId) || null, [data, activeCallId]);
    const activeCall = useMemo(() => (activeTouch?.call_id ? (data?.calls || []).find((c) => c.id === activeTouch.call_id) || null : null), [data, activeTouch]);
    const waitingTelephony = Boolean(activeTouch && !activeTouch.call_id && !activeTouch.result);
    // Пока ждём исход, карточка перечитывается раз в восемь секунд — не дольше пятнадцати минут.
    useEffect(() => {
        if (!waitingTelephony) return undefined;
        const startedAt = Date.now();
        const timer = setInterval(() => {
            if (Date.now() - startedAt > 15 * 60 * 1000) { clearInterval(timer); return; }
            load();
        }, 8000);
        return () => clearInterval(timer);
    }, [waitingTelephony, load]);

    const call = () => {
        if (!card?.phone) return;
        // Внешнюю схему браузер пускает только из обработчика клика; телефон должен быть запущен,
        // вход выполнен, рекрутер — на линии. Исход звонка придёт из телефонии сам.
        try {
            openICorePhoneCall(card.phone);
        } catch (requestError) {
            toast(requestError.message || 'Номер для звонка не разобран', 'error');
            return;
        }
        startCall();
    };
    const whatsapp = async () => {
        if (!card?.whatsapp_link) return;
        window.open(card.whatsapp_link, '_blank', 'noopener');
        try {
            await api.post(`/cards/${card.id}/touches`, { kind: 'whatsapp' });
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Переписка не записана'), 'error');
        }
    };
    const addComment = async () => {
        if (!comment.trim()) return;
        setSavingComment(true);
        try {
            await api.post(`/cards/${card.id}/touches`, { kind: 'comment', body: comment.trim() });
            setComment('');
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Комментарий не записан'), 'error');
        } finally {
            setSavingComment(false);
        }
    };
    const setResult = async (touchId, result) => {
        try {
            await api.patch(`/cards/${card.id}/touches/${touchId}`, { result });
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Исход не записан'), 'error');
        }
    };
    const setSubstage = async (substage) => {
        try {
            await api.put(`/cards/${card.id}/substage`, { substage });
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Подстатус не изменён'), 'error');
        }
    };
    const take = async () => {
        try {
            await api.post(`/cards/${card.id}/take`);
            toast('Карточка в работе у вас', 'success');
            changed();
        } catch (requestError) {
            toast(codeOf(requestError) === 'HIRING_ALREADY_TAKEN' ? 'Уже в работе' : errorOf(requestError, 'Не удалось взять в работу'), 'error');
            load();
        }
    };
    const markShift = async (undo) => {
        try {
            await api.post(`/cards/${card.id}/shifts`, { date: shiftDate, undo });
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Смена не отмечена'), 'error');
        }
    };
    const motivation = async (status, reason = null) => {
        try {
            await api.put(`/cards/${card.id}/motivation`, { status, reason });
            changed();
        } catch (requestError) {
            toast(errorOf(requestError, 'Статус начисления не изменён'), 'error');
            throw requestError;
        }
    };

    const open = Boolean(cardId);
    const title = card ? (card.full_name || 'Без имени') : 'Карточка';
    const subtitle = card ? [card.candidate_no ? `ID кандидата ${card.candidate_no}` : (card.kind === 'contact' ? 'Контакт' : 'Отклик'),
        card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ') : '';
    const substages = card ? (boot.meta.substages[card.stage] || []) : [];
    const shiftsDone = data?.shifts?.length || 0;
    const touchCount = (data?.touches || []).length;
    const showHire = Boolean(card && (card.stage === 'on_line' || data?.motivation));
    // Вкладки карточки (владелец 30.10.2026: карточка перегружена — поделить хотя бы на табы).
    const tabs = card ? [
        { value: 'card', label: 'Карточка' },
        { value: 'touches', label: touchCount ? `Касания · ${touchCount}` : 'Касания' },
        ...(showHire ? [{ value: 'hire', label: 'Смены и мотивация' }] : []),
        { value: 'history', label: 'История' },
    ] : [];
    const activeTab = tabs.some((item) => item.value === tab) ? tab : 'card';

    return (
        <>
            <IosModal open={open} onClose={onClose} title={title} subtitle={subtitle} maxWidth="max-w-3xl" sizeKey={card ? `ready-${activeTab}` : 'loading'}
                      headerActions={card && isOwnerOrLead && !card.is_closed ? (
                          <button type="button" className={TOOLBAR_BTN} onClick={() => setEditing(true)}><Pencil size={14} /> Изменить</button>
                      ) : null}>
                {error && <div className="py-10 text-center text-[13px] text-rose-600">{error}</div>}
                {!card && !error && <Loading text="Открываем карточку…" />}
                {card && (
                    <div className="space-y-4">
                        {/* Этап и действия — всегда на виду, над вкладками. */}
                        <div className="flex flex-wrap items-center gap-2">
                            <Pill tone={STAGE_TONES[card.stage]} className="text-[13px]">{STAGE_LABELS[card.stage]}</Pill>
                            {card.substage && <Pill tone={SUBSTAGE_TONES[card.substage]}>{SUBSTAGE_LABELS[card.substage]}</Pill>}
                            {card.repeat_of_card_id && <Pill tone="blue"><Repeat size={11} /> повторное обращение</Pill>}
                            {card.no_answer_suggest && <Pill tone="amber">3 недозвона подряд — предложить «Отказ: не выходит на связь»</Pill>}
                            <div className="ml-auto flex flex-wrap items-center gap-2">
                                {POOL_STAGES.includes(card.stage) && caps.take && !card.is_closed && (
                                    <button type="button" className={iosBtnPrimary} onClick={take}>Взять в работу</button>
                                )}
                                {card.phone && !POOL_STAGES.includes(card.stage) && isOwnerOrLead && !card.is_closed && (
                                    <>
                                        <button type="button" className={TOOLBAR_BTN} onClick={call}><Phone size={14} /> Позвонить</button>
                                        <button type="button" className={TOOLBAR_BTN} onClick={whatsapp}><MessageCircle size={14} /> Написать в WhatsApp</button>
                                    </>
                                )}
                                {caps.manage && !card.is_closed && !POOL_STAGES.includes(card.stage) && (
                                    <button type="button" className={TOOLBAR_BTN} onClick={() => setReassigning(true)}><UserCog size={14} /> Переназначить</button>
                                )}
                            </div>
                        </div>

                        <IosSegmented value={activeTab} onChange={setTab} options={tabs} ariaLabel="Раздел карточки" />

                        {activeTab === 'card' && (
                            <>
                                {targets.length > 0 && (
                                    <div className="flex flex-wrap items-center gap-1.5">
                                        {/* Шаги вперёд — первыми, завершения («Отказ», «Отсев») — в конце и красным контуром. */}
                                        {[...targets].sort((a, b) => Number(FINAL_STAGES.includes(a) && a !== 'hired') - Number(FINAL_STAGES.includes(b) && b !== 'hired')).map((target) => (
                                            <button key={target} type="button" onClick={() => setMove(target)}
                                                    className={`inline-flex h-8 items-center gap-1 rounded-xl px-3 text-[12.5px] font-medium ring-1 transition active:scale-[0.98] ${
                                                        FINAL_STAGES.includes(target) && target !== 'hired' ? 'bg-white text-rose-700 ring-rose-200 hover:bg-rose-50'
                                                            : target === 'hired' ? 'bg-emerald-600 text-white ring-emerald-600 hover:bg-emerald-700'
                                                                : 'bg-white text-slate-700 ring-slate-200/70 hover:bg-slate-50'}`}>
                                                <ArrowRight size={13} className="opacity-60" /> {STAGE_LABELS[target]}
                                            </button>
                                        ))}
                                    </div>
                                )}

                                {substages.length > 0 && isOwnerOrLead && !card.is_closed && (
                                    <IosSegmented value={card.substage || ''} onChange={setSubstage} ariaLabel="Подстатус" size="xs"
                                                  options={substages.map((item) => ({ value: item.code, label: item.label }))} />
                                )}

                                <div className="grid gap-4 md:grid-cols-2">
                                    <Section title="Основное">
                                        <InfoRow label="Телефон">
                                            {card.phone_hidden ? <span className="text-slate-400">скрыт до взятия в работу</span>
                                                : card.phone ? <span className="tabular-nums">{card.phone_pretty || card.phone}</span> : '—'}
                                        </InfoRow>
                                        {card.whatsapp && card.whatsapp !== card.phone && <InfoRow label="WhatsApp"><span className="tabular-nums">{card.whatsapp}</span></InfoRow>}
                                        <InfoRow label="Город">{card.city}</InfoRow>
                                        <InfoRow label="Вакансия">{card.vacancy_name || card.vacancy_text}</InfoRow>
                                        <InfoRow label="Тип">{card.kind === 'contact' ? 'Контакт' : 'Отклик'}</InfoRow>
                                        <InfoRow label="Источник">{card.source_name}{card.batch_file ? ` · ${card.batch_file}` : ''}</InfoRow>
                                        {card.campaign && <InfoRow label="Кампания">{card.campaign}</InfoRow>}
                                        {card.referred_by && <InfoRow label="Кто привёл">{card.referred_by}</InfoRow>}
                                        <InfoRow label="Поступил">{longTime(card.received_at)}</InfoRow>
                                        {card.platform_ref && /^https?:/.test(card.platform_ref) && (
                                            <InfoRow label="Площадка">
                                                <a href={card.platform_ref} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-blue-700 hover:underline">
                                                    открыть <ExternalLink size={12} />
                                                </a>
                                            </InfoRow>
                                        )}
                                        {data.repeat_of && (
                                            <InfoRow label="Прошлая карточка">
                                                №{data.repeat_of.candidate_no || data.repeat_of.id} · {STAGE_LABELS[data.repeat_of.stage]}
                                                {data.repeat_of.close_reason_text ? ` · ${data.repeat_of.close_reason_text}` : ''}
                                            </InfoRow>
                                        )}
                                        {card.comment && <div className="whitespace-pre-wrap py-1.5 text-[13px] text-slate-700">{card.comment}</div>}
                                    </Section>

                                    <Section title="Закрепление и воронка">
                                        <InfoRow label="Рекрутер">{card.recruiter_name || <span className="text-slate-400">в общем пуле</span>}</InfoRow>
                                        {card.taken_at && <InfoRow label="В работе с">{longTime(card.taken_at)}</InfoRow>}
                                        {card.next_contact_at && !card.is_closed && <InfoRow label="Следующий контакт">{longTime(card.next_contact_at)}</InfoRow>}
                                        {card.office_visit_at && <InfoRow label="Визит в офис">{longTime(card.office_visit_at)}</InfoRow>}
                                        {card.group_name && <InfoRow label="Группа">{card.group_name}{card.group_start_date ? ` · с ${fullDate(card.group_start_date)}` : ''}</InfoRow>}
                                        {card.training_started_at && <InfoRow label="Вышел на обучение">{fullDate(card.training_started_at)}</InfoRow>}
                                        {card.line_started_at && <InfoRow label="Вышел на линию">{fullDate(card.line_started_at)}</InfoRow>}
                                        {card.hired_at && <InfoRow label="7 смен подтверждены">{longTime(card.hired_at)}</InfoRow>}
                                        {card.is_closed && card.stage !== 'hired' && (
                                            <>
                                                <InfoRow label={card.stage === 'refused' ? 'Отказ' : 'Отсев'}>
                                                    {SUBSTAGE_LABELS[card.close_initiator]} · {card.close_reason_text}
                                                    {card.close_stage ? ` · ${card.close_stage === 'line' ? 'линия' : 'обучение'}, день ${card.close_day}` : ''}
                                                </InfoRow>
                                                {card.close_comment && <div className="whitespace-pre-wrap py-1 text-[13px] text-slate-700">{card.close_comment}</div>}
                                            </>
                                        )}
                                    </Section>
                                </div>
                            </>
                        )}

                        {activeTab === 'touches' && (
                            <Section>
                                {activeTouch && (
                                    <CallPanel touch={activeTouch} call={activeCall} api={api} toast={toast} card={card} onDone={changed}
                                               onClose={() => setActiveCallId(null)} />
                                )}
                                <div className="flex gap-2 py-2">
                                    <input className={FIELD} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Комментарий в ленту"
                                           onKeyDown={(e) => { if (e.key === 'Enter') addComment(); }} />
                                    <button type="button" className={iosBtnSecondary} onClick={addComment} disabled={savingComment || !comment.trim()}>Записать</button>
                                </div>
                                <Timeline touches={data.touches || []} calls={data.calls || []} api={api}
                                          canEdit={isOwnerOrLead && !card.is_closed} onSetResult={setResult} />
                            </Section>
                        )}

                        {activeTab === 'hire' && (
                            <div className="space-y-4">
                                {/* Смены на линии (ТЗ 7.2): отмечает руководитель отдела, куда был найм. */}
                                {card.stage === 'on_line' && (
                                    <Section title={`Смены на линии · ${shiftsDone} из ${boot.meta.shifts_to_hire}`}>
                                        <div className="flex flex-wrap items-center gap-2 py-1">
                                            {(data.shifts || []).map((shift) => <Pill key={shift.shift_date} tone="green">{fullDate(shift.shift_date)}</Pill>)}
                                            {shiftsDone === 0 && <span className="text-[12.5px] text-slate-500">Смен пока не отмечено</span>}
                                        </div>
                                        {actor.includes('dept_head') && (
                                            <div className="flex flex-wrap items-center gap-2 py-1.5">
                                                <div className="w-44"><IosDatePicker value={shiftDate} onChange={setShiftDate} max={todayIso()} triggerClassName={DATE_TRIGGER} ariaLabel="Дата смены" /></div>
                                                <button type="button" className={TOOLBAR_BTN} onClick={() => markShift(false)}>Отметить смену</button>
                                                {(data.shifts || []).some((s) => s.shift_date === shiftDate) && (
                                                    <button type="button" className={TOOLBAR_BTN} onClick={() => markShift(true)}>Снять отметку</button>
                                                )}
                                                {shiftsDone >= boot.meta.shifts_to_hire && canMoveTo('hired') && (
                                                    <button type="button" className={iosBtnPrimary} onClick={() => setMove('hired')}>Подтвердить «Отработал 7 дней»</button>
                                                )}
                                            </div>
                                        )}
                                    </Section>
                                )}
                                {data.motivation && (
                                    <Section title="Мотивация" right={caps.manage && data.motivation.status === 'counted' ? (
                                        <div className="flex gap-1.5">
                                            <button type="button" className={TOOLBAR_BTN} onClick={() => motivation('paid').catch(() => {})}>Выплачено</button>
                                            <button type="button" className={TOOLBAR_BTN} onClick={() => setCancelling(true)}>Отменить</button>
                                        </div>
                                    ) : null}>
                                        <InfoRow label="Статус"><Pill tone={MOTIVATION_TONES[data.motivation.status]}>{MOTIVATION_LABELS[data.motivation.status]}</Pill></InfoRow>
                                        <InfoRow label="Рекрутер">{data.motivation.recruiter_name}</InfoRow>
                                        {data.motivation.confirmed_at && <InfoRow label="Подтверждено">{longTime(data.motivation.confirmed_at)}</InfoRow>}
                                        {data.motivation.paid_at && <InfoRow label="Выплачено">{longTime(data.motivation.paid_at)}</InfoRow>}
                                        {data.motivation.cancel_reason && <InfoRow label="Отмена">{data.motivation.cancel_reason}</InfoRow>}
                                    </Section>
                                )}
                            </div>
                        )}

                        {activeTab === 'history' && <StageHistory stages={data.stages || []} assignments={data.assignments || []} />}
                    </div>
                )}
            </IosModal>
            {card && <MoveDialog open={Boolean(move)} onClose={() => setMove(null)} card={card} target={move} boot={boot} caps={caps} api={api} toast={toast} onDone={changed} />}
            {card && <EditDialog open={editing} onClose={() => setEditing(false)} api={api} toast={toast} card={card} boot={boot} onDone={changed} />}
            {card && <ReassignDialog open={reassigning} onClose={() => setReassigning(false)} api={api} toast={toast} card={card} boot={boot} onDone={changed} />}
            {card && (
                <ReasonDialog open={cancelling} onClose={() => setCancelling(false)} title="Отменить начисление" danger confirmLabel="Отменить начисление"
                              subtitle="Причина останется в карточке — её увидит рекрутер" label="Причина отмены"
                              onSubmit={(reason) => motivation('cancelled', reason)} />
            )}
        </>
    );
};

export default CardSheet;
