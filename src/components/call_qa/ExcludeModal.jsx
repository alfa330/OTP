import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Ban, Check, Loader2, AlertTriangle } from 'lucide-react';
import { iosBtnSecondary, iosGroupLabel, IosHint, IosModal } from '../ui/ios';
import { subjectTitle } from './subjects';
import {
    EXCLUSION_REASON_MAX, EXCLUSION_REASON_MIN, exclusionReady, isOtherCategory, visibleCategories,
} from './exclusionRules';

/* Окно «Исключить оценку».
 *
 * Причина обязательна, и дать её можно двумя путями: выбрать категорию одним
 * нажатием или написать своими словами. Категории собирает ИИ из того, что люди
 * пишут (call_qa.exclusions): своя причина без категории уходит ему на разбор, и
 * в следующий раз она — или похожая — уже стоит в списке. Частые — первыми.
 * С выбранной категорией поле становится необязательным комментарием.
 *
 * Что значит «исключить» (уходит из очереди, списков, «Обзора» и «Сводки»;
 * вернуть можно) — под «i» у «Причины»: пояснение нужно один раз, а место
 * строкой занимало бы всегда. */

// Кнопка подтверждения — красная целиком: это последний шаг разрушающего
// действия, как «Удалить» в окне подтверждения iOS. Своя строка классов, а не
// iosBtnPrimary с утилитой цвета поверх: утилита в Tailwind его не перебьёт.
export const DANGER_BTN = 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 '
    + 'text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] '
    + 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/60 '
    + 'disabled:cursor-not-allowed disabled:opacity-50';

const FIELD = 'w-full resize-none rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 text-[14px] leading-snug '
    + 'text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/70 disabled:opacity-60';

const CHIP = 'inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium '
    + 'transition-all active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 '
    + 'focus-visible:ring-blue-500/60 disabled:opacity-50';
const CHIP_IDLE = 'bg-slate-100 text-slate-700 hover:bg-slate-200';
const CHIP_ON = 'bg-blue-600 text-white shadow-sm hover:bg-blue-700';

const HINT = 'Исключённая оценка пропадает из очереди ревью, «Звонков» и «Чатов», «Обзора» и «Сводки» — '
    + 'для нецелевых разговоров: ошибся номером, тест, звонок сорвался. Журнал оценок она не меняет. '
    + 'Вернуть её можно в любой момент: «Исключённые» на вкладке со списком. '
    + 'Своими словами можно написать любую причину — ИИ сам разложит такие причины по категориям, '
    + 'и в следующий раз их можно будет выбрать одним нажатием.';

export default function ExcludeModal({ open, call, department, apiBaseUrl, headers, onClose, onExcluded }) {
    const [categories, setCategories] = useState(null);   // null — загружаются
    const [categoriesFailed, setCategoriesFailed] = useState(false);
    const [categoryId, setCategoryId] = useState(null);
    const [reason, setReason] = useState('');
    const [expanded, setExpanded] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const fieldRef = useRef(null);
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const kind = call?.subject_kind || 'call';

    /* Каждое открытие — с чистого листа и со свежим списком: пока окно было
       закрыто, ИИ мог завести новую категорию из чужой причины. */
    useEffect(() => {
        if (!open) return undefined;
        setCategoryId(null); setReason(''); setExpanded(false); setAttempted(false);
        setBusy(false); setError(''); setCategories(null); setCategoriesFailed(false);
        if (!apiBaseUrl) { setCategories([]); return undefined; }
        const controller = new AbortController();
        axios.get(`${apiBaseUrl}/api/ai-qa/exclusion-categories`, {
            params: { ...(department ? { department } : {}), subject: kind },
            headers: headersRef.current?.() || {}, signal: controller.signal,
        })
            .then((r) => setCategories(r.data?.items || []))
            .catch((e) => {
                if (axios.isCancel(e)) return;
                // Без списка исключить всё равно можно — своими словами.
                setCategories([]); setCategoriesFailed(true);
            });
        return () => controller.abort();
    }, [open, apiBaseUrl, department, kind]);

    // Категорий нет (первые исключения отдела) — сразу в поле: писать больше нечего.
    useEffect(() => {
        if (open && categories && !categories.length) fieldRef.current?.focus();
    }, [open, categories]);

    const { shown, hidden } = useMemo(
        () => visibleCategories(categories || [], { expanded, selectedId: categoryId }),
        [categories, expanded, categoryId]);
    const selected = (categories || []).find((c) => c.id === categoryId) || null;
    const otherPicked = Boolean(selected) && isOtherCategory(selected.title);
    const ready = exclusionReady({ categoryId, categoryTitle: selected?.title, reason });

    const close = () => { if (!busy) onClose?.(); };

    const submit = async () => {
        setAttempted(true);
        if (!ready || busy || !call) return;
        setBusy(true); setError('');
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/exclusion`, {
                subject_kind: kind, call_id: call.id,
                ...(categoryId != null ? { category_id: categoryId } : {}),
                reason: reason.trim(),
            }, { headers: headersRef.current?.() || {} });
            onExcluded?.(r.data?.exclusion || null, { category: selected?.title || null });
        } catch (e) {
            const data = e?.response?.data || {};
            // Кто-то успел исключить раньше — его причина важнее нашей: карточка
            // покажет её, а не затрёт своей.
            if (data.code === 'already_excluded' && data.exclusion) {
                onExcluded?.(data.exclusion, { already: true });
                return;
            }
            setError(data.error || 'Не удалось исключить оценку — проверьте связь и повторите');
            setBusy(false);
        }
    };

    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            if (event.key !== 'Escape' || event.defaultPrevented) return;
            // Сперва Escape закрывает раскрытую подсказку «i» (она сама гасится по
            // Escape на своей кнопке), и только следующий — окно.
            if (event.target?.closest?.('[aria-expanded="true"]')) return;
            close();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    });

    const subtitle = call
        ? [subjectTitle(kind, call.id), call.operator, call.datetime].filter(Boolean).join(' · ')
        : '';
    const placeholder = otherPicked ? 'Опишите причину своими словами'
        : selected ? 'Комментарий — необязательно'
        : categories && categories.length ? 'Или опишите своими словами'
            : 'Почему оценка не нужна? Например: водитель ошибся номером';

    return (
        <IosModal open={open} onClose={close} title="Исключить оценку" subtitle={subtitle} maxWidth="max-w-md"
                  sizeKey={categories === null ? 'loading' : 'ready'}
                  footer={(
                      <>
                          <button type="button" onClick={close} disabled={busy} className={iosBtnSecondary}>
                              Отмена
                          </button>
                          <button type="button" onClick={submit} disabled={busy || (attempted && !ready)}
                                  className={DANGER_BTN}>
                              {busy ? <Loader2 size={15} className="animate-spin" /> : <Ban size={15} />}
                              {busy ? 'Исключаю…' : 'Исключить оценку'}
                          </button>
                      </>
                  )}>
            <div className="space-y-2.5">
                <div className="flex items-center gap-1.5">
                    <span id="qa-exclude-reason" className={iosGroupLabel}>Причина</span>
                    <IosHint label="Что значит исключить оценку" text={HINT} />
                </div>

                {categories === null ? (
                    <div className="flex flex-wrap gap-2" aria-hidden="true">
                        {[7, 9, 6].map((w) => (
                            <span key={w} className="h-8 animate-pulse rounded-full bg-slate-100" style={{ width: `${w}rem` }} />
                        ))}
                    </div>
                ) : shown.length > 0 && (
                    <div role="group" aria-labelledby="qa-exclude-reason" className="flex flex-wrap gap-2">
                        {shown.map((c) => {
                            const on = c.id === categoryId;
                            return (
                                <button key={c.id} type="button" aria-pressed={on} disabled={busy}
                                        title={c.description || undefined}
                                        onClick={() => { setCategoryId(on ? null : c.id); setError(''); }}
                                        className={`${CHIP} ${on ? CHIP_ON : CHIP_IDLE}`}>
                                    {on && <Check size={13} strokeWidth={2.75} className="shrink-0" aria-hidden="true" />}
                                    <span className="truncate">{c.title}</span>
                                </button>
                            );
                        })}
                        {hidden > 0 && (
                            <button type="button" onClick={() => setExpanded(true)} disabled={busy}
                                    className={`${CHIP} bg-transparent text-blue-600 hover:bg-blue-50`}>
                                Ещё {hidden}
                            </button>
                        )}
                    </div>
                )}

                <textarea ref={fieldRef} rows={3} value={reason} disabled={busy} maxLength={EXCLUSION_REASON_MAX}
                          aria-label={selected && !otherPicked ? 'Комментарий к причине' : 'Причина своими словами'}
                          aria-invalid={attempted && !ready}
                          onChange={(e) => { setReason(e.target.value); setError(''); }}
                          onKeyDown={(e) => {
                              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
                          }}
                          placeholder={placeholder}
                          className={`${FIELD} ${attempted && !ready ? 'ring-1 ring-amber-300' : ''}`} />

                {attempted && !ready && (
                    <p className="flex items-center gap-1.5 px-0.5 text-[12px] font-medium text-amber-700" role="alert">
                        <AlertTriangle size={13} className="shrink-0" aria-hidden="true" />
                        {reason.trim() && reason.trim().length < EXCLUSION_REASON_MIN
                            ? 'Напишите причину чуть подробнее'
                            : otherPicked ? `Для «${selected.title}» опишите причину своими словами`
                                : 'Выберите причину или опишите её своими словами'}
                    </p>
                )}
                {categoriesFailed && (
                    <p className="px-0.5 text-[12px] text-slate-500">
                        Список причин не загрузился — опишите причину своими словами.
                    </p>
                )}
                {error && (
                    <p className="flex items-start gap-1.5 px-0.5 text-[12px] font-medium text-rose-600" role="alert">
                        <AlertTriangle size={13} className="mt-px shrink-0" aria-hidden="true" />{error}
                    </p>
                )}
            </div>
        </IosModal>
    );
}
