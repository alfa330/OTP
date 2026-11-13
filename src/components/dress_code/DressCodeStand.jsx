import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Loader2, Minus, Plus, SlidersHorizontal } from 'lucide-react';
import DressCodeCamera from './DressCodeCamera';
import { AI_LABELS, QUALITY_LABELS, employeeStatus, pct } from './dressCode';
import { releaseFaceDetector, warmFaceDetector } from './faceDetector';
import { DEFAULT_RULES, RULE_LIMITS, cleanRules, largestFace } from './faceMath';

/*
 * Стенд камеры «Дресс-кода» — только суперадмину (владелец 13.11.2026: «авто
 * фотографирование сработало даже без условия по пояс… добавь суперадмину, т. е. мне,
 * тестовый стенд, чтобы я проверял и корректировал»).
 *
 * Та же камера, что у сотрудника, но:
 *   — поверх кадра видно, что нашла нейросеть: рамка лица, линия макушки, линия пояса и
 *     четыре измеренных числа (зелёное — в правилах, красное — нет);
 *   — под кадром правила автоснимка с кнопками «−» и «+» (на телефоне точнее ползунка и
 *     вдвое ниже): камера слушается их сразу, а сотрудникам они уходят только после
 *     «Сохранить для всех»;
 *   — снимок никуда не отправляется и нигде не сохраняется. «Проверить ИИ» показывает, что
 *     ответил бы сервер на такой кадр (годен ли он, что увидел бы сотрудник), — без записи.
 *
 * Открывается из вкладки «Дресс-код» раздела «Отметки»; сервер пускает сюда только
 * суперадмина (dress_code/routes.py: /stand/analyze, PUT /settings с camera).
 */

const errText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;
const num = (value, digits) => Number(value).toFixed(digits).replace('.', ',');

const FIELDS = [
    { key: 'waist', label: 'Пояс ниже линии волос на', step: 0.1, show: (value) => `${num(value, 1)} лица` },
    { key: 'min_face', label: 'Лицо не мельче', step: 0.01, show: pct },
    { key: 'max_top', label: 'Макушка не ниже', step: 0.01, show: pct },
    { key: 'max_off_center', label: 'От центра не дальше', step: 0.01, show: pct },
    { key: 'hold_ms', label: 'Стоять неподвижно', step: 100, show: (value) => `${num(value / 1000, 1)} с` },
];

const sameRules = (a, b) => FIELDS.every(({ key }) => a?.[key] === b?.[key]);

const stepBtn = 'grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/12 text-white transition hover:bg-white/20 active:scale-95 disabled:opacity-30';

const Segments = ({ value, options, onChange, ariaLabel }) => (
    <div role="tablist" aria-label={ariaLabel} className="inline-flex shrink-0 rounded-lg bg-white/10 p-0.5">
        {options.map((option) => (
            <button key={String(option.value)} type="button" role="tab" aria-selected={value === option.value}
                    onClick={() => onChange(option.value)}
                    className={`rounded-md px-3 py-1 text-[13px] font-medium transition ${value === option.value ? 'bg-white text-slate-900' : 'text-white/75 hover:text-white'}`}>
                {option.label}
            </button>
        ))}
    </div>
);

const ResultRow = ({ label, children }) => (
    <div className="flex items-start justify-between gap-4">
        <span className="shrink-0 text-white/55">{label}</span>
        <span className="min-w-0 text-right text-white">{children}</span>
    </div>
);

export default function DressCodeStand({ open, onClose, base, headers, showToast }) {
    const [saved, setSaved] = useState(null);            // правила на сервере — те, что у сотрудников
    const [draft, setDraft] = useState({ ...DEFAULT_RULES });
    const [autoShot, setAutoShot] = useState(true);
    const [panelOpen, setPanelOpen] = useState(false);
    const [hasShot, setHasShot] = useState(false);
    const [checking, setChecking] = useState(false);
    const [result, setResult] = useState(null);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState('');            // строка под кнопками: сохранено или ошибка

    useEffect(() => {
        if (!open) return undefined;
        warmFaceDetector();
        let alive = true;
        axios.get(`${base}/settings`, { headers: headers() })
            .then((response) => {
                if (!alive) return;
                const rules = cleanRules(response.data?.settings?.camera);
                setSaved(rules);
                setDraft(rules);
            })
            .catch((error) => { if (alive) setStatus(errText(error, 'Не удалось прочитать правила камеры')); });
        return () => { alive = false; };
    }, [open, base, headers]);
    useEffect(() => releaseFaceDetector, []);

    const onShotChange = useCallback((has) => {
        setHasShot(has);
        if (!has) setResult(null);
    }, []);

    /* «Проверить ИИ»: кадр уходит на тот же ИИ, что фото сотрудника, но ничего не сохраняется. */
    const analyze = async (blob) => {
        setChecking(true);
        setResult(null);
        try {
            const form = new FormData();
            form.append('photo', blob, 'stand.jpg');
            const { data } = await axios.post(`${base}/stand/analyze`, form, { headers: headers() });
            setResult(data.result);
        } catch (error) {
            setResult({ error: errText(error, 'ИИ не ответил') });
        } finally {
            setChecking(false);
        }
    };

    const save = async () => {
        setSaving(true);
        setStatus('');
        try {
            const { data } = await axios.put(`${base}/settings`, { camera: draft }, { headers: headers() });
            const rules = cleanRules(data?.settings?.camera);
            setSaved(rules);
            setDraft(rules);
            setStatus('Сохранено — так теперь снимает камера у всех');
            showToast?.('Правила камеры сохранены', 'success');
        } catch (error) {
            setStatus(errText(error, 'Не удалось сохранить правила камеры'));
        } finally {
            setSaving(false);
        }
    };

    const change = (key, value) => {
        setStatus('');
        setDraft((prev) => cleanRules({ ...prev, [key]: value }));
    };

    const dirty = Boolean(saved) && !sameRules(draft, saved);

    let panel = null;
    if (hasShot) {
        panel = (
            <div className="shrink-0 border-t border-white/10 bg-neutral-900 px-4 py-3 text-[13.5px]">
                <div className="mx-auto max-h-[38vh] max-w-xl space-y-1.5 overflow-y-auto">
                    {!result && (
                        <div className="text-white/70">
                            {checking ? 'ИИ смотрит кадр…'
                                : 'Кадр снят и никуда не отправлен. «Проверить ИИ» — что ответил бы сервер; ничего не сохраняется.'}
                        </div>
                    )}
                    {result?.error && <div className="text-rose-300">{result.error}</div>}
                    {result && !result.error && (
                        <>
                            <ResultRow label="Кадр">
                                <span className={result.quality === 'ok' ? 'text-emerald-300' : 'text-rose-300'}>
                                    {QUALITY_LABELS[result.quality] || result.quality}
                                </span>
                                <span className="tabular-nums text-white/55"> · {result.width}×{result.height}</span>
                            </ResultRow>
                            <ResultRow label="Сотрудник увидит">
                                {result.employee === 'retake'
                                    ? `Просьбу переснять: «${result.hint || 'Переснимите фото'}»`
                                    : employeeStatus(result.employee).label}
                            </ResultRow>
                            <ResultRow label="Вывод ИИ">{AI_LABELS[result.verdict] || result.verdict}</ResultRow>
                            {result.visible?.length > 0 && <ResultRow label="Видно">{result.visible.join(', ')}</ResultRow>}
                            {result.violations?.length > 0 && (
                                <ResultRow label="Нарушения">{result.violations.map((item) => item.sign).join('; ')}</ResultRow>
                            )}
                            {result.remarks?.length > 0 && <ResultRow label="Замечания">{result.remarks.join('; ')}</ResultRow>}
                            {result.note && <ResultRow label="Пояснение">{result.note}</ResultRow>}
                        </>
                    )}
                </div>
            </div>
        );
    } else if (panelOpen) {
        panel = (
            <div className="shrink-0 border-t border-white/10 bg-neutral-900">
                {/* Действия — над ползунками и всегда на виду: до них не надо листать. */}
                <div className="mx-auto max-w-xl px-4 pt-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                        <div className="flex items-center gap-2">
                            <span className="text-[13px] font-medium text-white/85">Автоснимок</span>
                            <Segments value={autoShot} onChange={setAutoShot} ariaLabel="Автоснимок в стенде"
                                      options={[{ value: true, label: 'Вкл' }, { value: false, label: 'Выкл' }]} />
                        </div>
                        <div className="ml-auto flex items-center gap-1.5">
                            <button type="button" onClick={() => { setStatus(''); setDraft({ ...DEFAULT_RULES }); }}
                                    disabled={sameRules(draft, DEFAULT_RULES)}
                                    className="rounded-xl px-2.5 py-1.5 text-[13px] font-medium text-white/75 transition hover:bg-white/10 disabled:opacity-40">
                                По умолчанию
                            </button>
                            <button type="button" onClick={save} disabled={saving || !dirty}
                                    className="inline-flex items-center gap-1.5 rounded-xl bg-white px-3 py-1.5 text-[13px] font-semibold text-slate-900 transition hover:bg-white/90 active:scale-[0.98] disabled:opacity-40">
                                {saving && <Loader2 size={14} className="animate-spin" />} Сохранить для всех
                            </button>
                        </div>
                    </div>
                    <div className="mt-1 min-h-[16px] text-right text-[12px] text-white/60">
                        {status || (dirty ? 'Изменения пока действуют только в стенде' : '')}
                    </div>
                </div>
                <div className="mx-auto max-h-[34vh] max-w-xl space-y-1.5 overflow-y-auto px-4 pb-3">
                    {FIELDS.map(({ key, label, step, show }) => (
                        <div key={key}>
                            <div className="flex items-center justify-between gap-3">
                                <span id={`dc-stand-${key}-label`} className="min-w-0 text-[13px] font-medium leading-tight text-white/85">{label}</span>
                                <div className="flex shrink-0 items-center gap-1.5" role="group" aria-labelledby={`dc-stand-${key}-label`}>
                                    <button type="button" aria-label={`${label}: меньше`} className={stepBtn}
                                            disabled={draft[key] <= RULE_LIMITS[key][0]} onClick={() => change(key, draft[key] - step)}>
                                        <Minus size={15} />
                                    </button>
                                    <output id={`dc-stand-${key}`} className="w-[68px] text-center text-[13.5px] font-semibold tabular-nums text-white">
                                        {show(draft[key])}
                                    </output>
                                    <button type="button" aria-label={`${label}: больше`} className={stepBtn}
                                            disabled={draft[key] >= RULE_LIMITS[key][1]} onClick={() => change(key, draft[key] + step)}>
                                        <Plus size={15} />
                                    </button>
                                </div>
                            </div>
                            {key === 'waist' && (
                                <div className="text-[12px] leading-snug text-white/55">
                                    Пояс должен помещаться в кадр: лицо не крупнее {pct(largestFace(draft))} кадра. Больше число — стоять дальше.
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    const leftSlot = (
        <button type="button" onClick={() => setPanelOpen((value) => !value)} aria-label="Правила автоснимка" aria-pressed={panelOpen}
                className={`relative grid h-11 w-11 place-items-center rounded-full transition active:scale-95 ${panelOpen ? 'bg-white text-slate-900' : 'bg-white/15 text-white hover:bg-white/25'}`}>
            <SlidersHorizontal size={19} />
            {dirty && <span className="absolute right-0.5 top-0.5 h-2.5 w-2.5 rounded-full bg-amber-400 ring-2 ring-black" />}
        </button>
    );

    return (
        <DressCodeCamera
            open={open}
            onClose={onClose}
            onCapture={analyze}
            busy={checking}
            rules={draft}
            autoShot={autoShot}
            autoSend={false}
            sendLabel="Проверить ИИ"
            debug
            panel={panel}
            leftSlot={leftSlot}
            onShotChange={onShotChange}
        />
    );
}
