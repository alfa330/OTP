import React, { memo, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
    Check, X, Minus, AlertTriangle, ShieldAlert, Sparkles, User2, Save, Loader2,
    MessageSquare, Plus, Lock, BookMarked, RotateCcw,
} from 'lucide-react';
import { iosCard, iosInput, iosBtnPrimary, IosBadge, IosHint, IosToggle, scoreTone } from '../ui/ios';
import {
    allowedVerdicts, verdictFromAi, scoreOf, validateReview, isEmptyReview, validationLabel,
    HUMAN_VERDICT_LABEL, alignScores, alignComments, filledCount, COMMENT_REQUIRED, NEGATIVE,
} from './humanReview';

/* Панель «Моя оценка» в карточке ИИ-оценки.
 *
 * Человек оценивает разговор по мониторинговой шкале сотрудника прямо здесь,
 * не переходя в «Журнал оценок»: те же кнопки у критерия, что в журнале
 * (Корректно / Ошибка / N/A, «Недочёт» где шкала его знает, у критического —
 * «Критич. ошибка»), тот же балл, тот же обязательный комментарий к ошибке.
 *
 * Два режима одним переключателем «Учитывать в качестве»:
 *   выкл — калибровочная оценка: можно проставить часть критериев, она
 *          сравнивается с вердиктами ИИ и не трогает качество сотрудника;
 *   вкл  — оценка уходит в журнал обычной строкой и влияет на качество, поэтому
 *          обязана быть полной. Раз ушедшая в журнал, назад не выключается:
 *          из журнала строки не удаляются, правки идут переоценкой.
 *
 * Правила и формула — в humanReview.js (зеркало call_qa/human_review.py):
 * сервер считает то же самое и является истиной.
 */

const VERDICT_UI = {
    Correct:    { Icon: Check,         active: 'bg-white text-emerald-700 shadow-sm' },
    Incorrect:  { Icon: X,             active: 'bg-white text-rose-600 shadow-sm' },
    'N/A':      { Icon: Minus,         active: 'bg-white text-slate-700 shadow-sm' },
    Deficiency: { Icon: AlertTriangle, active: 'bg-white text-amber-700 shadow-sm' },
    Error:      { Icon: ShieldAlert,   active: 'bg-white text-rose-700 shadow-sm' },
};

// Подписи вердиктов ИИ — как в панели «ИИ» той же карточки.
const AI_LABEL = { Correct: 'Верно', Incorrect: 'Неверно', 'N/A': 'N/A', Deficiency: 'Недочёт', Pending: 'Ожидает' };

const fieldCls = `${iosInput} px-3 py-2 text-[12.5px]`;
const chipCls = 'inline-flex min-h-7 items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] font-medium '
    + 'transition-all active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 '
    + 'focus-visible:ring-blue-500/60 disabled:opacity-50 disabled:active:scale-100';

/* Строка «как считают другие»: вердикт ИИ (кнопка — поставить его себе) и, если
 * этот разговор уже оценил кто-то в журнале, его вердикт. Оба — справка, не
 * решение: решение человек принимает кнопками ниже. */
function ReferenceChips({ c, value, onPick, disabled, journalVerdict, journalComment }) {
    const aiVerdict = verdictFromAi(c, c.ai);
    const aiLabel = AI_LABEL[c.ai] || c.ai;
    const showAi = c.source === 'transcript' && c.ai && c.ai !== 'Pending';
    if (!showAi && journalVerdict == null) return null;
    return (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {showAi && (
                aiVerdict && aiVerdict !== value ? (
                    <button type="button" onClick={() => onPick(aiVerdict)} disabled={disabled}
                            title={`Поставить как у ИИ: ${HUMAN_VERDICT_LABEL[aiVerdict]}`}
                            className={`${chipCls} bg-blue-50 text-blue-700 ring-1 ring-blue-100 hover:bg-blue-100`}>
                        <Sparkles size={11} />ИИ: {aiLabel} · поставить
                    </button>
                ) : (
                    <span className={`${chipCls} bg-slate-100 text-slate-500`} title="Вердикт ИИ по этому критерию">
                        <Sparkles size={11} />ИИ: {aiLabel}
                    </span>
                )
            )}
            {journalVerdict != null && (
                <span className={`${chipCls} bg-slate-100 text-slate-500`}
                      title={journalComment ? `Оценка в журнале: ${journalComment}` : 'Оценка человека в журнале'}>
                    <User2 size={11} />Человек: {HUMAN_VERDICT_LABEL[journalVerdict] || journalVerdict}
                </span>
            )}
        </div>
    );
}

const MyCriterionRow = memo(function MyCriterionRow({
    c, value, comment, onPick, onComment, disabled, journalVerdict, journalComment, highlight,
}) {
    const [commentOpen, setCommentOpen] = useState(false);
    const options = allowedVerdicts(c);
    const negative = value != null && NEGATIVE.has(value);
    const needComment = value != null && COMMENT_REQUIRED.has(value) && !String(comment || '').trim();
    const showComment = negative || commentOpen || Boolean(String(comment || '').trim());
    return (
        <div className={`${iosCard} p-3 ${highlight ? 'ring-2 ring-amber-300/70' : ''}`}>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        {c.is_critical && <ShieldAlert size={13} className="shrink-0 text-rose-500" title="Критический критерий" />}
                        <span className="text-[13.5px] font-medium leading-snug text-slate-800">{c.name}</span>
                        {c.description ? <IosHint label="Описание критерия" text={c.description} /> : null}
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                        {/* Вес — как в журнале («12 pts»): без него не видно, чего стоит ошибка. */}
                        {c.is_critical
                            ? <IosBadge tone="red" className="!px-2 !py-0.5">критический</IosBadge>
                            : <IosBadge tone="slate" className="!px-2 !py-0.5 tabular-nums">{c.weight ?? '—'} б.</IosBadge>}
                        {c.source !== 'transcript' && (
                            <IosBadge tone="slate" className="!px-2 !py-0.5" title="ИИ этот критерий не проверяет — только человек">
                                только вручную
                            </IosBadge>
                        )}
                    </div>
                </div>
                <button type="button" onClick={() => setCommentOpen((o) => !o)} disabled={disabled}
                        aria-pressed={showComment} title="Комментарий к критерию"
                        className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                            showComment ? 'bg-blue-50 text-blue-600' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'}`}>
                    <MessageSquare size={15} />
                </button>
            </div>

            <ReferenceChips c={c} value={value} onPick={onPick} disabled={disabled}
                            journalVerdict={journalVerdict} journalComment={journalComment} />

            <div className="mt-2.5 flex rounded-xl bg-slate-100 p-0.5" role="group" aria-label={`Моя оценка по критерию «${c.name}»`}>
                {options.map((v) => {
                    const ui = VERDICT_UI[v];
                    const active = value === v;
                    return (
                        <button key={v} type="button" disabled={disabled} aria-pressed={active}
                                onClick={() => onPick(active ? null : v)}
                                className={`flex min-h-9 flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5 text-[12px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                    active ? ui.active : 'text-slate-500 hover:text-slate-700'}`}>
                            <ui.Icon size={12} strokeWidth={2.5} />{HUMAN_VERDICT_LABEL[v]}
                        </button>
                    );
                })}
            </div>

            {showComment && (
                <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
                            className="mt-2 overflow-hidden">
                    <textarea rows={2} value={comment || ''} disabled={disabled}
                        aria-label={`Комментарий к критерию «${c.name}»`}
                        onChange={(e) => onComment(e.target.value)}
                        placeholder={negative ? `Что именно не так по критерию «${c.name}»` : 'Комментарий (необязательно)'}
                        className={`${fieldCls} resize-y ${needComment ? 'ring-1 ring-rose-300' : ''}`} />
                    {needComment && (
                        <p className="mt-1 flex items-center gap-1 text-[11.5px] font-medium text-rose-600">
                            <AlertTriangle size={12} />Комментарий обязателен
                        </p>
                    )}
                </motion.div>
            )}
        </div>
    );
});

/* Строка настроек, как в «Настройках» iOS: подпись слева, переключатель справа. */
function SettingRow({ label, hint, checked, onChange, disabled }) {
    return (
        <div className="flex items-center justify-between gap-3 py-2">
            <span className="flex items-center gap-1.5 text-[13px] text-slate-700">
                {label}
                {hint ? <IosHint label={label} text={hint} /> : null}
            </span>
            <IosToggle checked={checked} onChange={onChange} disabled={disabled} />
        </div>
    );
}

const snapshotOf = (s) => JSON.stringify([
    s.scores, s.comments, s.comment, s.countInQuality, s.commentVisible, s.questionResolved, s.firstContact,
]);

const stateFromSaved = (criteria, saved) => ({
    scores: alignScores(criteria, saved?.scores),
    comments: alignComments(criteria, saved?.criterion_comments),
    comment: saved?.comment || '',
    countInQuality: Boolean(saved?.counted_in_quality),
    commentVisible: saved?.comment_visible_to_operator !== false,
    questionResolved: Boolean(saved?.question_resolved),
    firstContact: Boolean(saved?.resolved_first_contact),
});

export default function MyReviewPanel({ call, canCorrectJournal = false, onSave, onDirtyChange, disabled = false }) {
    const criteria = call?.criteria || [];
    const journal = call?.human_review || null;
    // Оценка человека в журнале, сделанная не мной, — справка у каждого критерия.
    const journalByOther = Boolean(journal) && !journal.is_mine;

    const [lastSaved, setLastSaved] = useState(call?.my_review || null);
    const [state, setState] = useState(() => stateFromSaved(criteria, call?.my_review));
    const [showComment, setShowComment] = useState(Boolean(call?.my_review?.comment));
    const [saving, setSaving] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const [error, setError] = useState(null);

    const savedSignature = useMemo(() => snapshotOf(stateFromSaved(criteria, lastSaved)), [criteria, lastSaved]);
    const dirty = snapshotOf(state) !== savedSignature;

    useEffect(() => { onDirtyChange?.(dirty, saving); }, [dirty, saving, onDirtyChange]);
    useEffect(() => () => onDirtyChange?.(false, false), [onDirtyChange]);

    const scaleChanged = Boolean(call?.scale_changed);
    // Раз ушедшая в журнал оценка правится только переоценкой, а её из карточки
    // делает админ или глава отдела — как в самом журнале.
    const inJournal = Boolean(lastSaved?.counted_in_quality);
    const locked = inJournal && !canCorrectJournal;
    const readOnly = disabled || saving || scaleChanged || locked;
    // Включить «учитывать в качестве» нельзя, если разговор в журнале уже оценил
    // другой человек, а переоценивать чужую оценку прав нет.
    const toggleBlockedByOther = journalByOther && !canCorrectJournal;
    const toggleDisabled = readOnly || inJournal || toggleBlockedByOther;

    const score = scoreOf(criteria, state.scores);
    const filled = filledCount(criteria, state.scores);
    const validation = validateReview(criteria, state.scores, state.comments,
                                      { completeRequired: state.countInQuality });
    const empty = isEmptyReview(state.scores, state.comments, state.comment);
    const canSave = !readOnly && dirty && validation.ok && !(empty && !state.countInQuality);
    const problemIdx = useMemo(
        () => new Set(attempted ? [...validation.missing, ...validation.commentsRequired] : []),
        [attempted, validation.missing, validation.commentsRequired],
    );

    const update = (patch) => setState((s) => ({ ...s, ...patch }));
    const pick = (i, v) => setState((s) => {
        const scores = s.scores.slice(); scores[i] = v;
        return { ...s, scores };
    });
    const setComment = (i, text) => setState((s) => {
        const comments = s.comments.slice(); comments[i] = text;
        return { ...s, comments };
    });
    // «Как у ИИ» — стартовая точка: чаще всего человек согласен почти со всем и
    // правит один-два критерия. Критерии, которые ИИ не оценивал, не трогаем.
    const fillFromAi = () => setState((s) => ({
        ...s,
        scores: criteria.map((c, i) => verdictFromAi(c, c.ai) ?? s.scores[i]),
    }));
    const allAsAi = criteria.every((c, i) => {
        const ai = verdictFromAi(c, c.ai);
        return ai == null || ai === state.scores[i];
    });

    const save = async () => {
        setAttempted(true);
        setError(null);
        if (!validation.ok) {
            setError(validationLabel(validation));
            return;
        }
        if (empty && !state.countInQuality) {
            setError('Проставьте хотя бы один критерий');
            return;
        }
        if (state.countInQuality && !inJournal
            && !window.confirm(`Отправить оценку ${score}/100 в «Журнал оценок»? Она войдёт в качество сотрудника.`)) return;
        setSaving(true);
        try {
            const result = await onSave?.({
                scores: state.scores,
                criterion_comments: state.comments,
                comment: state.comment,
                comment_visible_to_operator: state.commentVisible,
                question_resolved: state.questionResolved,
                resolved_first_contact: state.questionResolved && state.firstContact,
                count_in_quality: state.countInQuality,
            });
            if (result?.my_review) {
                setLastSaved(result.my_review);
                setState(stateFromSaved(criteria, result.my_review));
                setAttempted(false);
            }
        } finally {
            setSaving(false);
        }
    };

    if (!criteria.length) return null;

    return (
        <div className="flex min-w-0 flex-col">
            {scaleChanged && (
                <div className="mb-2.5 flex items-start gap-2 rounded-2xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-100" role="alert">
                    <RotateCcw size={14} className="mt-0.5 shrink-0" />
                    Шкала сотрудника изменилась после этой оценки ИИ. Нажмите «Переоценить» — тогда
                    свою оценку можно будет заполнить по действующей шкале.
                </div>
            )}
            {locked && (
                <div className="mb-2.5 flex items-start gap-2 rounded-2xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-slate-600 ring-1 ring-slate-200/70">
                    <Lock size={14} className="mt-0.5 shrink-0 text-slate-400" />
                    Оценка уже учтена в журнале{lastSaved?.journal_call_id ? ` (№${lastSaved.journal_call_id})` : ''}.
                    Изменить её можно переоценкой в «Журнале оценок».
                </div>
            )}

            {/* Итог и «как у ИИ» — одной строкой над критериями, без второй шапки. */}
            <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2 px-1">
                <div className="flex items-center gap-2 text-[12.5px] text-slate-500">
                    {score != null
                        ? <IosBadge tone={scoreTone(score)} className="tabular-nums"><User2 size={11} />Мой балл: {score}</IosBadge>
                        : <span>Заполнено <b className="text-slate-700">{filled}</b> из {criteria.length}</span>}
                    {lastSaved?.updated_at && !dirty && (
                        <span className="text-slate-400">· сохранено {lastSaved.updated_at}</span>
                    )}
                </div>
                {!allAsAi && (
                    <button type="button" onClick={fillFromAi} disabled={readOnly}
                            title="Проставить все критерии так, как оценил ИИ; потом поправить нужные"
                            className={`${chipCls} bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-800`}>
                        <Sparkles size={12} />Как у ИИ — все
                    </button>
                )}
            </div>

            <div className="space-y-2.5">
                {criteria.map((c, i) => (
                    <MyCriterionRow key={c.idx ?? i} c={c} value={state.scores[i]} comment={state.comments[i]}
                                    onPick={(v) => pick(i, v)} onComment={(t) => setComment(i, t)}
                                    disabled={readOnly} highlight={problemIdx.has(i)}
                                    journalVerdict={journalByOther ? c.human : null}
                                    journalComment={journalByOther ? c.human_comment : null} />
                ))}
            </div>

            {/* Общий комментарий — по чипу, а не пустым полем: нужен не всегда. */}
            <div className="mt-2.5 px-1">
                {showComment || state.comment ? (
                    <textarea rows={2} value={state.comment} disabled={readOnly}
                        aria-label="Комментарий к оценке"
                        onChange={(e) => update({ comment: e.target.value })}
                        placeholder="Комментарий к оценке в целом"
                        className={`${fieldCls} resize-y`} />
                ) : (
                    <button type="button" onClick={() => setShowComment(true)} disabled={readOnly}
                            className={`${chipCls} bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700`}>
                        <Plus size={11} strokeWidth={2.5} />Комментарий к оценке
                    </button>
                )}
            </div>

            {/* Что ещё знает строка журнала — только когда оценка туда уходит. */}
            {state.countInQuality && (
                <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                            className={`${iosCard} mt-2.5 divide-y divide-slate-100 px-3.5`}>
                    <SettingRow label="Показывать комментарии оператору" checked={state.commentVisible}
                                onChange={(v) => update({ commentVisible: v })} disabled={readOnly}
                                hint="Как в журнале: оператор увидит комментарии к критериям и к оценке в «Моих оценках». Выключите, если это заметки для супервайзера." />
                    <SettingRow label="Вопрос решён" checked={state.questionResolved}
                                onChange={(v) => update({ questionResolved: v, firstContact: v ? state.firstContact : false })}
                                disabled={readOnly}
                                hint="Отметка журнала: обращение клиента решено в этом разговоре." />
                    {state.questionResolved && (
                        <SettingRow label="С первого обращения" checked={state.firstContact}
                                    onChange={(v) => update({ firstContact: v })} disabled={readOnly} />
                    )}
                </motion.div>
            )}

            <div className="sticky bottom-0 mt-3 flex flex-col gap-2 rounded-2xl bg-white/95 px-3 py-2.5 ring-1 ring-slate-200/70 backdrop-blur-xl" aria-live="polite">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                        <IosToggle checked={state.countInQuality} disabled={toggleDisabled}
                                   onChange={(v) => { update({ countInQuality: v }); setAttempted(false); setError(null); }} />
                        <span className={`flex items-center gap-1.5 text-[12.5px] font-medium ${toggleDisabled ? 'text-slate-400' : 'text-slate-700'}`}>
                            Учитывать в качестве
                            <IosHint label="Что значит учитывать в качестве"
                                text={inJournal
                                    ? `Оценка уже в «Журнале оценок»${lastSaved?.journal_call_id ? ` (№${lastSaved.journal_call_id})` : ''} и входит в качество сотрудника. Сохранение с изменениями станет переоценкой этой строки.`
                                    : toggleBlockedByOther
                                        ? `В журнале этот разговор уже оценил ${journal?.evaluator || 'другой человек'}${journal?.datetime ? ` (${journal.datetime})` : ''}. Переоценить его из карточки может админ или глава отдела; ваша оценка сохранится как калибровочная — для сравнения с ИИ.`
                                        : 'Включено — оценка уходит в «Журнал оценок» как обычная оценка супервайзера и входит в качество сотрудника; тогда нужно проставить все критерии. Выключено — оценка калибровочная: сравнивается с ИИ, на качество не влияет, можно заполнить только часть критериев.'} />
                        </span>
                        {inJournal && (
                            <IosBadge tone="green" title="Оценка уже в журнале">
                                <BookMarked size={11} />в журнале{lastSaved?.journal_call_id ? ` №${lastSaved.journal_call_id}` : ''}
                            </IosBadge>
                        )}
                    </div>
                    <button type="button" onClick={save} disabled={!canSave} className={iosBtnPrimary}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
                        {saving ? 'Сохраняю…'
                            : state.countInQuality
                                ? (inJournal ? 'Сохранить переоценку' : 'Сохранить и учесть в качестве')
                                : 'Сохранить мою оценку'}
                    </button>
                </div>
                {(error || (attempted && !validation.ok)) && (
                    <p className="flex items-center gap-1.5 text-[12px] font-medium text-amber-700">
                        <AlertTriangle size={13} />{error || validationLabel(validation)}
                    </p>
                )}
                {!error && !dirty && lastSaved && !locked && (
                    <p className="text-[11.5px] text-slate-400">
                        {inJournal ? 'Оценка учтена в качестве сотрудника.'
                            : 'Оценка калибровочная — сравнивается с ИИ, на качество не влияет.'}
                    </p>
                )}
            </div>
        </div>
    );
}
