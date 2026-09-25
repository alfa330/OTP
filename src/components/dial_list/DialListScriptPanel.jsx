import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosHint, IosToggle } from '../ui/ios';
import { LINE_PREFIXES, ScriptView } from './scriptMarkup';

/*
 * Скрипт разговора (запрос владельца 25.09.2026): руководитель пишет здесь
 * основной текст (приветствие, ход разговора) и быстрые вопросы с ответами.
 * Оператор в iCORE Phone видит скрипт под контролами звонка, а вопросы —
 * кнопками под ним: нажал — открылся ответ, прочитал водителю.
 *
 * Разметка общая с телефоном и живёт в scriptMarkup.jsx: предпросмотр справа
 * рисует ровно то, что покажет телефон.
 *
 * Вопросы не удаляются, а выключаются: на них может ссылаться история. Убранный
 * из списка вопрос сервер помечает выключенным, и его можно вернуть.
 */

const BODY_MAX = 20000;
const QUESTION_MAX = 200;
const ANSWER_MAX = 8000;
const QUESTIONS_MAX = 50;

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

let tempSeq = 0;
const tempId = () => `new-${Date.now()}-${tempSeq++}`;
const isNew = (id) => String(id).startsWith('new-');

const fmtUpdated = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
};

const normalizeQuestion = (q) => ({
    id: q.id,
    question: String(q.question ?? ''),
    answer: String(q.answer ?? ''),
    is_active: q.is_active !== false,
});

const snapshotOf = (body, questions) => JSON.stringify({
    body,
    questions: questions.map(({ id, question, answer, is_active }) => ({ id: isNew(id) ? undefined : id, question, answer, is_active })),
});

/* ---------- правки текста из панели инструментов ---------- */

const stripPrefix = (line) => {
    const p = LINE_PREFIXES.find((x) => line.startsWith(x));
    return p ? line.slice(p.length) : line;
};

// Строчный префикс на все строки выделения. Если он уже на каждой — снимаем.
const applyLinePrefix = (value, start, end, prefix) => {
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    let lineEnd = value.indexOf('\n', Math.max(end, start));
    if (lineEnd === -1) lineEnd = value.length;
    const lines = value.slice(lineStart, lineEnd).split('\n');
    const all = lines.every((l) => l.startsWith(prefix));
    const out = lines.map((l) => (all ? l.slice(prefix.length) : prefix + stripPrefix(l))).join('\n');
    return { value: value.slice(0, lineStart) + out + value.slice(lineEnd), start: lineStart, end: lineStart + out.length };
};

// Обёртка выделения в **…** или ==…==; повторное нажатие снимает.
const wrapInline = (value, start, end, mark, placeholder) => {
    const n = mark.length;
    const sel = value.slice(start, end);
    if (sel.length >= n * 2 && sel.startsWith(mark) && sel.endsWith(mark)) {
        const inner = sel.slice(n, -n);
        return { value: value.slice(0, start) + inner + value.slice(end), start, end: start + inner.length };
    }
    if (start >= n && value.slice(start - n, start) === mark && value.slice(end, end + n) === mark) {
        return { value: value.slice(0, start - n) + sel + value.slice(end + n), start: start - n, end: start - n + sel.length };
    }
    const inner = sel || placeholder;
    return { value: value.slice(0, start) + mark + inner + mark + value.slice(end), start: start + n, end: start + n + inner.length };
};

const TOOLS = [
    { key: 'h', label: 'Заголовок', title: 'Заголовок строки: «# »', glyph: <span className="text-[12px] font-bold">H</span>, run: (v, s, e) => applyLinePrefix(v, s, e, '# ') },
    { key: 'b', label: 'Жирный', title: 'Жирный: **текст**', glyph: <span className="text-[12px] font-bold">Ж</span>, run: (v, s, e) => wrapInline(v, s, e, '**', 'жирный') },
    { key: 'm', label: 'Выделить', title: 'Жёлтое выделение: ==текст==', glyph: <span className="rounded bg-amber-200 px-1 text-[11px] font-semibold text-slate-900">Аб</span>, run: (v, s, e) => wrapInline(v, s, e, '==', 'важное') },
    { key: 'li', label: 'Пункт', title: 'Пункт списка: «- »', glyph: <FaIcon className="fas fa-list-ul" style={{ width: 12, height: 12 }} />, run: (v, s, e) => applyLinePrefix(v, s, e, '- ') },
    { key: 'q', label: 'Примечание', title: 'Серая плашка: «> »', glyph: <FaIcon className="fas fa-quote-left" style={{ width: 12, height: 12 }} />, run: (v, s, e) => applyLinePrefix(v, s, e, '> ') },
];

const Counter = ({ len, max }) => (
    <span className={`text-[11px] tabular-nums ${len > max ? 'font-semibold text-rose-600' : len > max * 0.9 ? 'text-amber-600' : 'text-slate-400'}`}>
        {len.toLocaleString('ru-RU')} / {max.toLocaleString('ru-RU')}
    </span>
);

/**
 * Textarea с панелью разметки. Растёт под текст (не ниже minRows, не выше
 * maxHeight, если задан). Кнопки панели держат фокус в поле — иначе выделение
 * терялось бы по клику.
 */
const MarkupEditor = ({ value, onChange, disabled = false, minRows = 4, maxHeight = 0, maxLength, placeholder, compact = false, ariaLabel, invalid = false }) => {
    const ref = useRef(null);
    const pendingSel = useRef(null);

    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        el.style.height = 'auto';
        const h = maxHeight ? Math.min(el.scrollHeight, maxHeight) : el.scrollHeight;
        el.style.height = `${h}px`;
        el.style.overflowY = maxHeight && el.scrollHeight > maxHeight ? 'auto' : 'hidden';
    }, [value, maxHeight]);

    useEffect(() => {
        const el = ref.current;
        if (!el || !pendingSel.current) return;
        const [s, e] = pendingSel.current;
        pendingSel.current = null;
        el.focus();
        el.setSelectionRange(s, e);
    }, [value]);

    const run = (tool) => {
        const el = ref.current;
        if (!el || disabled) return;
        const r = tool.run(value, el.selectionStart, el.selectionEnd);
        if (r.value.length > maxLength) return;
        pendingSel.current = [r.start, r.end];
        onChange(r.value);
    };

    return (
        <div className="space-y-1.5">
            {!disabled && (
                <div className="flex flex-wrap items-center gap-1">
                    {TOOLS.map((t) => (
                        <button
                            key={t.key}
                            type="button"
                            title={t.title}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => run(t)}
                            className={`${iosBtnGhost} ${compact ? 'px-2 py-1 text-[12px]' : 'px-2.5 py-1.5 text-[12.5px]'} gap-1.5 bg-slate-100/70 text-slate-600 hover:bg-slate-200`}
                        >
                            {t.glyph}
                            {!compact && <span>{t.label}</span>}
                        </button>
                    ))}
                </div>
            )}
            <textarea
                ref={ref}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                disabled={disabled}
                rows={minRows}
                maxLength={maxLength}
                placeholder={placeholder}
                aria-label={ariaLabel}
                spellCheck
                className={`${iosInput} resize-none font-normal leading-relaxed ${compact ? 'text-[13.5px]' : ''} ${invalid ? 'ring-2 ring-rose-300' : ''} disabled:text-slate-600`}
            />
        </div>
    );
};

const HELP_ROWS = [
    ['# Заголовок', 'крупный заголовок'],
    ['## Подзаголовок', 'заголовок поменьше'],
    ['- пункт', 'список'],
    ['> примечание', 'серая плашка с пояснением'],
    ['---', 'разделительная линия'],
    ['**текст**', 'жирный'],
    ['==текст==', 'жёлтое выделение'],
    ['пустая строка', 'новый абзац; соседние строки склеиваются'],
];

const MarkupHelp = () => {
    const [open, setOpen] = useState(false);
    return (
        <div className="rounded-xl bg-slate-50 px-3 py-2">
            <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-3 text-[12.5px] font-medium text-slate-600" aria-expanded={open}>
                <span className="inline-flex items-center gap-1.5"><FaIcon className="fas fa-circle-question" style={{ width: 12, height: 12 }} /> Как размечать</span>
                <FaIcon className={open ? 'fas fa-chevron-up' : 'fas fa-chevron-down'} style={{ width: 11, height: 11 }} />
            </button>
            {open && (
                <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
                    {HELP_ROWS.map(([code, meaning]) => (
                        <React.Fragment key={code}>
                            <dt><code className="rounded-md bg-white px-1.5 py-0.5 font-mono text-[11.5px] text-slate-700 ring-1 ring-slate-200">{code}</code></dt>
                            <dd className="text-slate-500">{meaning}</dd>
                        </React.Fragment>
                    ))}
                </dl>
            )}
        </div>
    );
};

const PreviewCard = ({ text, title = 'Так увидит оператор', sticky = false }) => (
    <div className={`${iosCard} p-4 ${sticky ? 'lg:sticky lg:top-4 lg:self-start' : ''}`}>
        <div className="mb-3 flex items-center gap-2 text-[12px] font-medium text-slate-500">
            <FaIcon className="fas fa-mobile-alt" style={{ width: 12, height: 12 }} />
            {title}
        </div>
        {String(text || '').trim()
            ? <ScriptView text={text} />
            : <div className="py-6 text-center text-[13px] text-slate-400">Пока пусто — начните печатать слева</div>}
    </div>
);

const DialListScriptPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const [working, setWorking] = useState(null);               // { body, questions } — рабочая копия
    const [serverQuestions, setServerQuestions] = useState([]); // все вопросы с сервера, включая выключенные
    const [saved, setSaved] = useState(null);                   // снимок сохранённого (для «есть изменения»)
    const [meta, setMeta] = useState({ version: 0, updated_at: null });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [invalidId, setInvalidId] = useState(null);
    const [previewIds, setPreviewIds] = useState(() => new Set());
    const [archiveOpen, setArchiveOpen] = useState(false);
    const [focusId, setFocusId] = useState(null);
    const questionRefs = useRef({});

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    // keepIds — вопросы, которые остаются в списке даже выключенными: те, что
    // человек только что выключил переключателем, не должны прыгать в «Убранные»
    // сразу после «Сохранить». При первой загрузке в списке только включённые.
    const applyServer = useCallback((script, keepIds = null) => {
        const all = (Array.isArray(script?.questions) ? script.questions : [])
            .map((q, i) => ({ ...normalizeQuestion(q), position: Number.isFinite(q.position) ? q.position : i }))
            .sort((a, b) => a.position - b.position)
            .map(({ position, ...q }) => q);
        const shown = all.filter((q) => q.is_active || (keepIds && keepIds.has(q.id)));
        const body = String(script?.body ?? '');
        setServerQuestions(all);
        setWorking({ body, questions: shown });
        setSaved(snapshotOf(body, shown));
        setMeta({ version: Number(script?.version) || 0, updated_at: script?.updated_at || null });
        setInvalidId(null);
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        setWorking(null);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/script`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            applyServer(data.script);
        } catch (e) {
            const msg = e.message || 'Не удалось загрузить скрипт';
            setError(msg);
            toast(msg, 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId, applyServer, toast]);

    useEffect(() => { load(); }, [load]);

    // Фокус в только что добавленный вопрос — после того, как карточка отрисована.
    useEffect(() => {
        if (!focusId) return;
        const el = questionRefs.current[focusId];
        if (el) {
            el.focus();
            el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
        setFocusId(null);
    }, [focusId, working]);

    const snapshot = useMemo(() => (working ? snapshotOf(working.body, working.questions) : null), [working]);
    const dirty = !!working && snapshot !== saved;

    const archived = useMemo(() => (working
        ? serverQuestions.filter((q) => !working.questions.some((w) => w.id === q.id))
        : []), [serverQuestions, working]);

    const setBody = (body) => setWorking((w) => ({ ...w, body }));
    const patch = (idx, changes) => setWorking((w) => ({ ...w, questions: w.questions.map((q, i) => (i === idx ? { ...q, ...changes } : q)) }));
    const move = (idx, dir) => setWorking((w) => {
        const next = [...w.questions];
        const j = idx + dir;
        if (j < 0 || j >= next.length) return w;
        [next[idx], next[j]] = [next[j], next[idx]];
        return { ...w, questions: next };
    });
    const add = () => {
        const id = tempId();
        setWorking((w) => ({ ...w, questions: [...w.questions, { id, question: '', answer: '', is_active: true }] }));
        setFocusId(id);
    };
    const remove = (idx) => setWorking((w) => ({ ...w, questions: w.questions.filter((_, i) => i !== idx) }));
    const restore = (q) => {
        setWorking((w) => ({ ...w, questions: [...w.questions, { ...q, is_active: true }] }));
        setFocusId(q.id);
    };
    const togglePreview = (id) => setPreviewIds((s) => {
        const next = new Set(s);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    // Назад к сохранённому: тело и состав списка — из снимка, тексты вопросов —
    // с сервера (снимок хранит их же).
    const reset = () => {
        if (!working || !saved) return;
        const snap = JSON.parse(saved);
        const byId = new Map(serverQuestions.map((q) => [q.id, q]));
        const questions = snap.questions.map((q) => ({ ...(byId.get(q.id) || q) }));
        setWorking({ body: snap.body, questions });
        setInvalidId(null);
    };

    const failCard = (id, msg) => {
        setInvalidId(id);
        toast(msg, 'error');
        const el = questionRefs.current[id];
        if (el) {
            el.focus();
            el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
    };

    const save = async () => {
        if (!working || saving) return;
        const body = working.body;
        const qs = working.questions.map((q) => ({ ...q, question: q.question.trim() }));
        const empty = qs.find((q) => !q.question);
        if (empty) { failCard(empty.id, 'У каждого вопроса должен быть текст'); return; }
        const longQ = qs.find((q) => q.question.length > QUESTION_MAX);
        if (longQ) { failCard(longQ.id, `Вопрос не длиннее ${QUESTION_MAX} символов`); return; }
        const longA = qs.find((q) => q.answer.length > ANSWER_MAX);
        if (longA) { failCard(longA.id, `Ответ не длиннее ${ANSWER_MAX.toLocaleString('ru-RU')} символов`); return; }
        if (body.length > BODY_MAX) { toast(`Основной скрипт не длиннее ${BODY_MAX.toLocaleString('ru-RU')} символов`, 'error'); return; }
        if (qs.length > QUESTIONS_MAX) { toast(`Не больше ${QUESTIONS_MAX} вопросов`, 'error'); return; }
        setInvalidId(null);
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/script`, {
                method: 'PUT', credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    body,
                    questions: qs.map(({ id, question, answer, is_active }) => ({ id: isNew(id) ? undefined : id, question, answer, is_active })),
                }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            applyServer(data.script, new Set(qs.map((q) => q.id)));
            toast('Скрипт сохранён', 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить скрипт', 'error');
        } finally {
            setSaving(false);
        }
    };

    const updated = fmtUpdated(meta.updated_at);
    const versionLine = meta.version
        ? `Версия ${meta.version}${updated ? ` · обновлено ${updated}` : ''}`
        : 'Ещё не сохранялся';

    if (loading || (!working && !error)) {
        return (
            <div className="grid gap-4 lg:grid-cols-2">
                {[0, 1].map((k) => (
                    <div key={k} className={`${iosCard} animate-pulse space-y-3 p-4`}>
                        <div className="h-3 w-1/3 rounded bg-slate-100" />
                        <div className="h-3 w-full rounded bg-slate-100" />
                        <div className="h-3 w-5/6 rounded bg-slate-100" />
                        <div className="h-3 w-2/3 rounded bg-slate-100" />
                        <div className="h-24 w-full rounded-xl bg-slate-50" />
                    </div>
                ))}
            </div>
        );
    }

    if (!working) {
        return (
            <div className={`${iosCard} flex flex-wrap items-center justify-between gap-3 p-4`}>
                <div className="text-[13px] text-rose-600">{error}</div>
                <button type="button" onClick={load} className={`${iosBtnSecondary} py-1.5`}>
                    <FaIcon className="fas fa-rotate" /> Повторить
                </button>
            </div>
        );
    }

    return (
        <div className="space-y-5">
            {/* Основной скрипт */}
            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-3 px-1">
                    <div className="flex items-center gap-2">
                        <div className={iosGroupLabel}>Основной скрипт</div>
                        <IosHint text="Показывается оператору на всё время разговора: приветствие, ход беседы, что обязательно сказать. Выделяйте важное — ==так== — чтобы взгляд цеплялся." />
                    </div>
                    <span className="text-[11.5px] text-slate-500">{versionLine}</span>
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                    <div className={`${iosCard} space-y-3 p-4`}>
                        <MarkupEditor
                            value={working.body}
                            onChange={setBody}
                            disabled={!canEdit}
                            minRows={12}
                            maxLength={BODY_MAX}
                            placeholder={'# Приветствие\nЗдравствуйте, меня зовут **Имя**, компания …\n\n- уточнить, удобно ли говорить\n- ==назвать предложение==\n\n> Если водитель занят — договориться, когда перезвонить'}
                            ariaLabel="Основной скрипт"
                        />
                        <div className="flex items-center justify-between gap-2">
                            {canEdit ? <MarkupHelp /> : <span />}
                            <Counter len={working.body.length} max={BODY_MAX} />
                        </div>
                    </div>
                    <PreviewCard text={working.body} sticky />
                </div>
            </section>

            {/* Быстрые вопросы */}
            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-3 px-1">
                    <div className="flex items-center gap-2">
                        <div className={iosGroupLabel}>Быстрые вопросы</div>
                        <IosHint text="Кнопки под скриптом в телефоне. Водитель спросил — оператор нажал вопрос и прочитал ответ. Порядок кнопок такой же, как здесь." />
                    </div>
                    <span className="text-[11.5px] tabular-nums text-slate-500">{working.questions.length} из {QUESTIONS_MAX}</span>
                </div>

                {working.questions.length === 0 ? (
                    <div className={`${iosCard} p-8 text-center`}>
                        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <FaIcon className="fas fa-circle-question" style={{ width: 18, height: 18 }} />
                        </div>
                        <div className="mt-3 text-[14px] font-semibold text-slate-800">Вопросов пока нет</div>
                        <div className="mt-1 text-[12.5px] text-slate-500">
                            {canEdit ? 'Добавьте первый: то, что водители спрашивают чаще всего.' : 'Руководитель ещё не добавил вопросы.'}
                        </div>
                    </div>
                ) : (
                    <ul className="space-y-2.5">
                        {working.questions.map((q, idx) => {
                            const invalid = invalidId === q.id;
                            const showPreview = previewIds.has(q.id);
                            return (
                                <li
                                    key={q.id}
                                    className={`rounded-2xl p-3.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 transition ${
                                        invalid ? 'bg-rose-50/40 ring-rose-300' : q.is_active ? 'bg-white ring-slate-200/70' : 'bg-slate-50/70 ring-slate-200/70'
                                    }`}
                                >
                                    <div className="flex items-start gap-2.5">
                                        <div className={`mt-1.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[12px] font-semibold ${q.is_active ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-400'}`}>
                                            {idx + 1}
                                        </div>
                                        <div className="min-w-0 flex-1 space-y-2">
                                            <input
                                                ref={(el) => { questionRefs.current[q.id] = el; }}
                                                value={q.question}
                                                onChange={(e) => { patch(idx, { question: e.target.value }); if (invalid) setInvalidId(null); }}
                                                disabled={!canEdit}
                                                maxLength={QUESTION_MAX}
                                                placeholder="Вопрос водителя, например: «Сколько я буду получать?»"
                                                aria-label={`Вопрос ${idx + 1}`}
                                                className={`${iosInput} py-2 font-medium ${invalid ? 'ring-2 ring-rose-300' : ''} ${q.is_active ? '' : 'text-slate-500'} disabled:text-slate-700`}
                                            />
                                            <div className="flex items-center justify-between gap-2 px-1">
                                                <span className="text-[11px] text-slate-400">Вопрос</span>
                                                <Counter len={q.question.length} max={QUESTION_MAX} />
                                            </div>
                                            {showPreview ? (
                                                <div className="rounded-xl bg-slate-50 px-3.5 py-3">
                                                    {q.answer.trim()
                                                        ? <ScriptView text={q.answer} />
                                                        : <div className="text-[13px] text-slate-400">Ответ пока пуст</div>}
                                                </div>
                                            ) : (
                                                <MarkupEditor
                                                    value={q.answer}
                                                    onChange={(v) => patch(idx, { answer: v })}
                                                    disabled={!canEdit}
                                                    minRows={3}
                                                    maxHeight={360}
                                                    maxLength={ANSWER_MAX}
                                                    compact
                                                    placeholder="Что ответить оператору — можно с разметкой"
                                                    ariaLabel={`Ответ на вопрос ${idx + 1}`}
                                                />
                                            )}
                                            <div className="flex items-center justify-between gap-2 px-1">
                                                <span className="text-[11px] text-slate-400">Ответ</span>
                                                <Counter len={q.answer.length} max={ANSWER_MAX} />
                                            </div>
                                        </div>
                                        {canEdit && (
                                            <div className="flex shrink-0 flex-col items-center gap-0.5">
                                                <button type="button" onClick={() => move(idx, -1)} disabled={idx === 0} className={`${iosBtnGhost} px-2 py-1.5 disabled:opacity-30`} aria-label="Выше" title="Выше">
                                                    <FaIcon className="fas fa-chevron-up" style={{ width: 11, height: 11 }} />
                                                </button>
                                                <button type="button" onClick={() => move(idx, 1)} disabled={idx === working.questions.length - 1} className={`${iosBtnGhost} px-2 py-1.5 disabled:opacity-30`} aria-label="Ниже" title="Ниже">
                                                    <FaIcon className="fas fa-chevron-down" style={{ width: 11, height: 11 }} />
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                    <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-2.5">
                                        <label className="flex items-center gap-2 text-[12px] text-slate-600">
                                            <IosToggle checked={q.is_active} disabled={!canEdit} onChange={(v) => patch(idx, { is_active: v })} />
                                            Показывать оператору
                                            {!q.is_active && <IosBadge tone="slate">скрыт</IosBadge>}
                                        </label>
                                        <div className="flex items-center gap-1">
                                            <button type="button" onClick={() => togglePreview(q.id)} className={`${iosBtnGhost} px-2.5 py-1.5 text-[12.5px] ${showPreview ? 'bg-slate-100 text-slate-800' : ''}`}>
                                                <FaIcon className={showPreview ? 'fas fa-pen' : 'fas fa-eye'} style={{ width: 12, height: 12 }} />
                                                {showPreview ? 'Редактировать' : 'Предпросмотр'}
                                            </button>
                                            {canEdit && (
                                                <button type="button" onClick={() => remove(idx)} className={`${iosBtnGhost} px-2.5 py-1.5 text-[12.5px] text-rose-500 hover:bg-rose-50`}>
                                                    <FaIcon className="fas fa-xmark" style={{ width: 12, height: 12 }} />
                                                    Убрать
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>
                )}

                {canEdit && (
                    <div className="px-1 pt-1">
                        <button type="button" onClick={add} disabled={working.questions.length >= QUESTIONS_MAX} className={iosBtnSecondary}>
                            <FaIcon className="fas fa-plus" style={{ width: 11, height: 11 }} /> Добавить вопрос
                        </button>
                    </div>
                )}
                <div className="px-1 text-[11.5px] text-slate-500">
                    Убранный вопрос пропадает у операторов, но остаётся в истории — его можно вернуть ниже. Выключенный переключателем остаётся в списке, но в телефоне не показывается.
                </div>

                {archived.length > 0 && (
                    <div className={`${iosCard} overflow-hidden`}>
                        <button type="button" onClick={() => setArchiveOpen((v) => !v)} className="flex w-full items-center justify-between px-4 py-3 text-left text-[13px] font-medium text-slate-600" aria-expanded={archiveOpen}>
                            <span className="inline-flex items-center gap-2">
                                <FaIcon className="fas fa-box-archive" style={{ width: 12, height: 12 }} />
                                Убранные вопросы
                                <IosBadge tone="slate">{archived.length}</IosBadge>
                            </span>
                            <FaIcon className={archiveOpen ? 'fas fa-chevron-up' : 'fas fa-chevron-down'} style={{ width: 11, height: 11 }} />
                        </button>
                        {archiveOpen && (
                            <ul className="divide-y divide-slate-100 border-t border-slate-100">
                                {archived.map((q) => (
                                    <li key={q.id} className="flex items-center gap-3 px-4 py-2.5">
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-[13px] text-slate-700">{q.question || '(без текста)'}</div>
                                            {q.is_active && <div className="text-[11.5px] text-amber-600">Будет выключен после сохранения</div>}
                                        </div>
                                        {canEdit && (
                                            <button type="button" onClick={() => restore(q)} disabled={working.questions.length >= QUESTIONS_MAX} className={`${iosBtnGhost} px-2.5 py-1.5 text-[12.5px] text-blue-600 hover:bg-blue-50`}>
                                                <FaIcon className="fas fa-rotate-left" style={{ width: 12, height: 12 }} />
                                                Вернуть
                                            </button>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                )}
            </section>

            {/* Липкая панель сохранения */}
            {canEdit && (
                <div className="sticky bottom-3 z-20">
                    <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-white/90 px-4 py-2.5 shadow-[0_8px_30px_rgba(15,23,42,0.10)] ring-1 ring-slate-200/70 backdrop-blur-xl">
                        <div className="text-[12px] text-slate-500">
                            {dirty ? <span className="text-amber-600">Есть несохранённые изменения</span> : versionLine}
                        </div>
                        <div className="flex items-center gap-2">
                            <button type="button" onClick={reset} disabled={!dirty || saving} className={`${iosBtnGhost} disabled:opacity-40`}>
                                Отменить изменения
                            </button>
                            <button type="button" onClick={save} disabled={!dirty || saving} className={iosBtnPrimary}>
                                <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                Сохранить
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default DialListScriptPanel;
