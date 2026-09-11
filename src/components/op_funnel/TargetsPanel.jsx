import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { IosBadge, IosHint, iosBtnPrimary, iosBtnGhost, iosInput } from '../ui/ios';
import { METRIC_HINTS, METRIC_LABELS, formatNumber, shortDay } from './funnelFormat';

/**
 * Нормы и пороги направления.
 *
 * Почему это данные, а не константы в коде: ТЗ прямо требует, чтобы пороги
 * раскраски настраивались, а новая причина или новый план не требовали релиза.
 *
 * Почему правка — это НОВАЯ строка с датой, а не перезапись прежней: посчитанное
 * раньше не должно поехать. Поднятый сегодня план не имеет права переписать
 * выполнение за прошлый месяц — иначе премия задним числом «испортится», и
 * объяснить это человеку будет нечем.
 *
 * Почему у супервайзера нет права правки: норма — это обязательство, а не
 * настройка экрана. Менять себе план тот, кто по нему отчитывается, не должен.
 */

const SHIFT_LABELS = { any: 'Общий', day: 'День', night: 'Ночь' };

/* Порядок метрик — по смыслу, а не по алфавиту: сначала нормы работы, потом
   таргеты качества, в конце границы цветов. Алфавит разбросал бы связанные
   числа по всему списку. */
const METRIC_ORDER = [
    'reached_per_hour', 'agreed_per_hour', 'plan_per_fte', 'target_conversion',
    'chats_per_hour', 'reply_seconds', 'quality',
    'weight_reply', 'weight_chats', 'weight_quality',
    'green_from', 'amber_from',
];

function sortKey(row) {
    const index = METRIC_ORDER.indexOf(row.metric);
    return index === -1 ? METRIC_ORDER.length : index;
}

const Row = ({ row, canEdit, saving, onSave }) => {
    const [draft, setDraft] = useState(String(row.value ?? ''));
    const [dirty, setDirty] = useState(false);

    useEffect(() => {
        if (!dirty) setDraft(String(row.value ?? ''));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [row.value]);

    const changed = dirty && String(row.value ?? '') !== draft.trim();

    return (
        <div className="flex flex-wrap items-center gap-2 sm:gap-3 py-2.5 border-t border-slate-100 first:border-t-0">
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                    <span className="text-[13px] text-slate-800">
                        {METRIC_LABELS[row.metric] || row.metric}
                    </span>
                    {METRIC_HINTS[row.metric] ? <IosHint text={METRIC_HINTS[row.metric]} /> : null}
                    {row.shift_kind && row.shift_kind !== 'any' ? (
                        <IosBadge tone="slate">{SHIFT_LABELS[row.shift_kind] || row.shift_kind}</IosBadge>
                    ) : null}
                </div>
                <div className="text-[11px] text-slate-400 mt-0.5">
                    действует с {shortDay(row.effective_from)}
                </div>
            </div>

            {canEdit ? (
                <div className="flex items-center gap-2 shrink-0">
                    <input
                        type="number"
                        step="any"
                        value={draft}
                        onChange={(event) => { setDraft(event.target.value); setDirty(true); }}
                        className={`${iosInput} w-28 text-right tabular-nums`}
                        aria-label={METRIC_LABELS[row.metric] || row.metric}
                    />
                    <button
                        type="button"
                        disabled={!changed || saving}
                        onClick={() => onSave(row, draft).then(() => setDirty(false))}
                        className={`${changed && !saving ? iosBtnPrimary : iosBtnGhost} px-3 py-1.5 text-[13px]`}
                    >
                        {saving ? '…' : 'Сохранить'}
                    </button>
                </div>
            ) : (
                <div className="text-[15px] font-medium text-slate-900 tabular-nums shrink-0 w-28 text-right">
                    {formatNumber(row.value, 2)}
                </div>
            )}
        </div>
    );
};

export default function TargetsPanel({ api, direction, canEdit = false, showToast }) {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState('');
    const [error, setError] = useState('');

    /* Тост держим в ref: родитель создаёт функцию заново на каждом рендере, и в
       зависимостях эффекта она гоняла бы загрузку по кругу. */
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const load = useCallback(async () => {
        if (!api || !direction) return;
        setLoading(true);
        setError('');
        // Гасим прежние нормы ДО запроса: иначе при переключении направления
        // человек видит нормы «Потока» под шапкой «Верификатора», а при ошибке
        // запроса они остаются на экране навсегда и выглядят настоящими.
        setRows([]);
        try {
            const data = await api.targets({ direction });
            if (data === null) return;
            setRows(data.targets || []);
        } catch (exc) {
            setError(exc?.message || 'Не удалось получить нормы');
        } finally {
            setLoading(false);
        }
    }, [api, direction]);

    useEffect(() => { load(); }, [load]);

    const onSave = async (row, draft) => {
        const value = Number(String(draft).replace(',', '.'));
        if (!Number.isFinite(value)) {
            toastRef.current?.('Значение нормы должно быть числом', 'error');
            return;
        }
        const key = `${row.metric}:${row.shift_kind}`;
        setSaving(key);
        try {
            await api.saveTarget({
                direction,
                metric: row.metric,
                shiftKind: row.shift_kind,
                value,
            });
            toastRef.current?.(
                'Норма сохранена. Она действует с сегодняшнего дня — посчитанное раньше не изменится.',
                'success',
            );
            await load();
        } catch (exc) {
            toastRef.current?.(exc?.message || 'Не удалось сохранить норму', 'error');
        } finally {
            setSaving('');
        }
    };

    /* Показываем только ДЕЙСТВУЮЩУЮ строку каждой метрики: в таблице лежит вся
       история с датами, и выкладывать её целиком значит показать по три строки
       на одну норму. История нужна редко, а путаница от неё — каждый раз. */
    const current = useMemo(() => {
        const best = new Map();
        (rows || []).forEach((row) => {
            const key = `${row.metric}:${row.shift_kind}`;
            const kept = best.get(key);
            if (!kept || String(row.effective_from) > String(kept.effective_from)) {
                best.set(key, row);
            }
        });
        return [...best.values()].sort((left, right) => (
            sortKey(left) - sortKey(right)
            || String(left.shift_kind).localeCompare(String(right.shift_kind))
        ));
    }, [rows]);

    return (
        <section className="rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-sm p-4 sm:p-5">
            <header className="mb-3">
                <h3 className="text-[15px] font-semibold text-slate-900">Нормы и пороги</h3>
                <p className="text-xs text-slate-500 mt-1">
                    {canEdit
                        ? 'Норма действует с даты сохранения. Посчитанное раньше не меняется — '
                          + 'отчёты за прошлые дни останутся такими, как их уже назвали.'
                        : 'Нормы ставит руководитель отдела: это обязательство, а не настройка экрана.'}
                </p>
            </header>

            {error ? (
                <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 p-3 text-sm text-rose-700 mb-3">
                    {error}
                    <button type="button" onClick={load} className="ml-3 underline underline-offset-2">
                        Повторить
                    </button>
                </div>
            ) : null}

            {loading && !current.length ? (
                <p className="py-6 text-center text-sm text-slate-500">Загрузка норм…</p>
            ) : null}

            {!loading && !current.length && !error ? (
                <div className="py-8 text-center">
                    <FaIcon className="fas fa-sliders text-2xl text-slate-300" />
                    <p className="mt-3 text-sm text-slate-500">
                        Для этого направления нормы ещё не заданы.
                    </p>
                </div>
            ) : null}

            {current.length ? (
                <div>
                    {current.map((row) => (
                        <Row
                            key={`${row.metric}:${row.shift_kind}`}
                            row={row}
                            canEdit={canEdit}
                            saving={saving === `${row.metric}:${row.shift_kind}`}
                            onSave={onSave}
                        />
                    ))}
                </div>
            ) : null}
        </section>
    );
}
