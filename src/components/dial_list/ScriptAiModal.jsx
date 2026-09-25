import React, { useEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosModal } from '../ui/ios';
import { ScriptView } from './scriptMarkup';

/*
 * ИИ в скрипте обзвона (запрос владельца 25.09.2026): «оформить с помощью ИИ»
 * и «сразу создавать скрипты с ИИ».
 *
 * Сервер ничего не сохраняет: ответ ИИ показывается здесь, руководитель
 * смотрит, применяет в редактор (появляются несохранённые изменения) и жмёт
 * обычное «Сохранить». Текст ИИ — в той же разметке, что и ручной, поэтому
 * предпросмотр рисует <ScriptView>, как и сама панель.
 *
 * Запрос к ИИ идёт 5–30 секунд, поэтому состояние ожидания заметное, а окно
 * можно закрыть, не дожидаясь: запрос обрывается, редактор не трогается.
 */

export const BRIEF_MIN = 10;
export const BRIEF_MAX = 2000;
export const POLISH_MIN = 10;
export const BRIEF_PLACEHOLDER = 'Звоним водителям такси, приглашаем в парк: комиссия 7 %, выплаты каждый день, помогаем с документами. Тон дружелюбный, коротко.';

export const AiIcon = ({ spinning = false, size = 12 }) => (
    <FaIcon className={spinning ? 'fas fa-spinner fa-spin' : 'fas fa-sparkles'} style={{ width: size, height: size }} />
);

const briefTone = (len) => (len > BRIEF_MAX ? 'font-semibold text-rose-600' : len > BRIEF_MAX * 0.9 ? 'text-amber-600' : 'text-slate-400');

/** Поле «Опишите кампанию» со счётчиком и подсказкой, что получится. */
export const BriefField = ({ value, onChange, disabled = false, rows = 5, autoFocus = false, hint = true }) => (
    <div className="space-y-1.5">
        <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled}
            rows={rows}
            maxLength={BRIEF_MAX}
            autoFocus={autoFocus}
            placeholder={BRIEF_PLACEHOLDER}
            aria-label="Опишите кампанию"
            spellCheck
            className={`${iosInput} resize-none leading-relaxed disabled:text-slate-500`}
        />
        <div className="flex items-start justify-between gap-3 px-1">
            {hint
                ? <span className="text-[11.5px] text-slate-500">ИИ напишет основной скрипт — приветствие и ход разговора — и 5–8 быстрых вопросов с ответами.</span>
                : <span />}
            <span className={`shrink-0 text-[11px] tabular-nums ${briefTone(value.length)}`}>{value.length.toLocaleString('ru-RU')} / {BRIEF_MAX.toLocaleString('ru-RU')}</span>
        </div>
    </div>
);

const normalizeGenerated = (list) => (Array.isArray(list) ? list : [])
    .map((q) => ({ question: String(q?.question ?? '').trim(), answer: String(q?.answer ?? '').trim() }))
    .filter((q) => q.question);

const Waiting = ({ text }) => (
    <div className="flex items-center gap-3 rounded-2xl bg-white px-4 py-4 ring-1 ring-slate-200/70">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
            <FaIcon className="fas fa-spinner fa-spin" style={{ width: 14, height: 14 }} />
        </div>
        <div className="min-w-0">
            <div className="text-[13.5px] font-medium text-slate-800">{text}</div>
            <div className="text-[12px] text-slate-500">Обычно 10–30 секунд. Окно можно закрыть — редактор не изменится.</div>
        </div>
    </div>
);

const Block = ({ title, children, tone = 'plain' }) => (
    <div className={`rounded-2xl p-4 ring-1 ${tone === 'accent' ? 'bg-blue-50/40 ring-blue-100' : 'bg-white ring-slate-200/70'}`}>
        <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {tone === 'accent' && <AiIcon size={11} />}
            {title}
        </div>
        {children}
    </div>
);

/**
 * «Создать скрипт с ИИ»: описание кампании → предпросмотр результата → в
 * редактор целиком («Заменить скрипт») или добавкой («Добавить к текущему»).
 *
 * request(payload, signal) — запрос к серверу, отдаёт result; ошибки бросает
 * с текстом для человека. onReplace / onAppend получают { body, questions }.
 */
export const ScriptGenerateModal = ({
    open, onClose, initialBrief = '', autoStart = false, request,
    onReplace, onAppend, currentQuestions = 0, bodyEmpty = true, toast,
}) => {
    const [brief, setBrief] = useState(initialBrief);
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const seq = useRef(0);       // номер живого запроса: ответы старых игнорируем
    const abortRef = useRef(null);

    const run = async (text) => {
        const b = String(text ?? '').trim();
        if (b.length < BRIEF_MIN) { setError(`Опишите кампанию подробнее — хотя бы ${BRIEF_MIN} символов`); return; }
        if (b.length > BRIEF_MAX) { setError(`Описание не длиннее ${BRIEF_MAX.toLocaleString('ru-RU')} символов`); return; }
        abortRef.current?.abort();
        const ctl = new AbortController();
        abortRef.current = ctl;
        const my = ++seq.current;
        setLoading(true);
        setError('');
        try {
            const r = await request({ mode: 'generate', brief: b }, ctl.signal);
            if (my !== seq.current) return;
            const next = { body: String(r?.body ?? '').trim(), questions: normalizeGenerated(r?.questions) };
            if (!next.body && !next.questions.length) throw new Error('ИИ вернул пустой ответ, попробуйте ещё раз');
            setResult(next);
        } catch (e) {
            if (e?.name === 'AbortError' || my !== seq.current) return;
            const msg = e.message || 'ИИ сейчас недоступен, попробуйте ещё раз';
            setError(msg);
            if (typeof toast === 'function') toast(msg, 'error');
        } finally {
            if (my === seq.current) setLoading(false);
        }
    };

    // Каждое открытие — с чистого листа и с тем описанием, что передали снаружи.
    useEffect(() => {
        if (!open) return;
        setBrief(initialBrief);
        setResult(null);
        setError('');
        if (autoStart) run(initialBrief);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    // Окно размонтировали (панель перегружается) — запрос обрывать.
    useEffect(() => () => { abortRef.current?.abort(); seq.current += 1; }, []);

    const close = () => {
        abortRef.current?.abort();
        seq.current += 1;
        setLoading(false);
        onClose?.();
    };

    const hasCurrent = !bodyEmpty || currentQuestions > 0;
    const canRun = !loading && brief.trim().length >= BRIEF_MIN && brief.length <= BRIEF_MAX;

    return (
        <IosModal
            open={open}
            onClose={close}
            title="Создать скрипт с ИИ"
            subtitle={result ? 'Проверьте результат и примените в редактор' : 'Опишите кампанию — остальное напишет ИИ'}
            maxWidth={result ? 'max-w-3xl' : 'max-w-lg'}
            footer={result ? (
                <>
                    <button type="button" onClick={close} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" onClick={() => run(brief)} disabled={!canRun} className={`${iosBtnGhost} disabled:opacity-40`}>
                        <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} style={{ width: 12, height: 12 }} />
                        {loading ? 'ИИ пишет…' : 'Ещё вариант'}
                    </button>
                    {hasCurrent && (
                        <button type="button" onClick={() => onAppend?.(result)} disabled={loading} className={iosBtnSecondary}>
                            <FaIcon className="fas fa-plus" style={{ width: 11, height: 11 }} /> Добавить к текущему
                        </button>
                    )}
                    <button type="button" onClick={() => onReplace?.(result)} disabled={loading} className={iosBtnPrimary}>
                        <AiIcon /> {hasCurrent ? 'Заменить скрипт' : 'Вставить в редактор'}
                    </button>
                </>
            ) : (
                <>
                    <button type="button" onClick={close} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" onClick={() => run(brief)} disabled={!canRun} className={iosBtnPrimary}>
                        <AiIcon spinning={loading} /> {loading ? 'ИИ пишет…' : 'Создать'}
                    </button>
                </>
            )}
        >
            <div className="space-y-3">
                <BriefField value={brief} onChange={setBrief} disabled={loading} rows={result ? 3 : 6} autoFocus={!autoStart} hint={!result} />

                {error && !loading && (
                    <div className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700">{error}</div>
                )}

                {loading && <Waiting text={result ? 'ИИ пишет ещё один вариант…' : 'ИИ пишет скрипт…'} />}

                {result && !loading && (
                    <>
                        <Block title="Скрипт" tone="accent">
                            {result.body
                                ? <ScriptView text={result.body} />
                                : <div className="text-[13px] text-slate-400">ИИ не написал основной текст — только вопросы</div>}
                        </Block>
                        {result.questions.length > 0 && (
                            <Block title={`Быстрые вопросы · ${result.questions.length}`} tone="accent">
                                <ul className="divide-y divide-slate-200/70">
                                    {result.questions.map((q, i) => (
                                        <li key={i} className="py-2.5 first:pt-0 last:pb-0">
                                            <div className="text-[14px] font-semibold leading-snug text-slate-900">{q.question}</div>
                                            {q.answer
                                                ? <div className="mt-1.5"><ScriptView text={q.answer} /></div>
                                                : <div className="mt-1 text-[12.5px] text-slate-400">Ответ пуст</div>}
                                        </li>
                                    ))}
                                </ul>
                            </Block>
                        )}
                        <div className="px-1 text-[11.5px] text-slate-500">
                            {hasCurrent ? (
                                <>
                                    <b className="font-semibold text-slate-600">Заменить скрипт</b> — основной текст станет этим,
                                    {currentQuestions > 0 && ` текущие вопросы (${currentQuestions}) уйдут из списка и после сохранения выключатся у операторов — вернуть можно из «Убранных».`}
                                    {currentQuestions === 0 && ' вопросы добавятся.'}
                                    {' '}
                                    <b className="font-semibold text-slate-600">Добавить к текущему</b> — вопросы допишутся в конец списка{bodyEmpty ? ', основной текст заполнится' : ', основной текст останется вашим'}.
                                    {' '}Ничего не сохраняется, пока вы не нажмёте «Сохранить».
                                </>
                            ) : 'Текст попадёт в редактор — проверьте его и нажмите «Сохранить».'}
                        </div>
                    </>
                )}
            </div>
        </IosModal>
    );
};

/**
 * «Оформление от ИИ»: слева как сейчас, справа предложение. «Применить»
 * подставляет предложение в редактор, сохранение — как обычно, отдельно.
 */
export const ScriptPolishModal = ({ open, onClose, current = '', proposal = '', onApply, subtitle = '' }) => (
    <IosModal
        open={open}
        onClose={onClose}
        title="Оформление от ИИ"
        subtitle={subtitle}
        maxWidth="max-w-4xl"
        footer={(
            <>
                <button type="button" onClick={onClose} className={iosBtnSecondary}>Отмена</button>
                <button type="button" onClick={onApply} className={iosBtnPrimary}>
                    <FaIcon className="fas fa-check" style={{ width: 12, height: 12 }} /> Применить
                </button>
            </>
        )}
    >
        <div className="grid gap-3 md:grid-cols-2">
            <Block title="Сейчас">
                {String(current).trim()
                    ? <ScriptView text={current} />
                    : <div className="text-[13px] text-slate-400">Пусто</div>}
            </Block>
            <Block title="Предложение ИИ" tone="accent">
                {String(proposal).trim()
                    ? <ScriptView text={proposal} />
                    : <div className="text-[13px] text-slate-400">ИИ ничего не предложил</div>}
            </Block>
        </div>
        <div className="mt-3 px-1 text-[11.5px] text-slate-500">
            ИИ расставляет заголовки, списки и выделения, но может поменять и формулировки — прочитайте перед тем, как применить. В редактор текст попадёт по «Применить», сохранение — отдельно.
        </div>
    </IosModal>
);
