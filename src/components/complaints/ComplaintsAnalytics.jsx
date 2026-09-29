import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, SlidersHorizontal, X } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, IosBadge, IosModal,
    IosSegmented,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import {
    activeFilterCount, analyticsQuery, bucketLabel, isRepeated, percent,
} from './complaintRules';

/* Аналитика жалоб — «аналитическая функция» раздела из ТЗ #297.
 *
 * Что показываем — список ТЗ: общее количество; по направлениям; основные
 * причины; динамику; подтверждённые и неподтверждённые; отработано / в работе;
 * жалобы на каждого сотрудника и повторные; сколько потребовало ОС или
 * обучения; повторяющиеся причины. Отдельно — причины недовольства парком:
 * «особенно важно собирать жалобы, связанные с самим таксопарком».
 *
 * Форма — по данным, а не по вкусу: заголовочные числа — плитками, одна серия
 * во времени — столбиками одного цвета, разбивки по целям и причинам —
 * горизонтальными полосами с числом на конце (длинные подписи причин по
 * вертикали не лезут), сотрудники — таблицей. Цвет один (синий), потому что
 * серия одна: раскрашивать цели разными цветами значило бы кодировать цветом
 * то, что уже написано словами.
 *
 * Фильтры — одной строкой над всем: период и «на кого» на виду, остальные
 * девять — за кнопкой «Фильтры» с числом включённых. Все блоки считаются по
 * одному срезу, поэтому числа между ними всегда сходятся. */

const SERIES = '#2a78d6';   // слот 1 палитры; проверен validate_palette.js
const GRID = '#e2e8f0';     // slate-200 — сетка на шаг от поверхности

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const isoDay = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

const defaultPeriod = () => {
    const to = new Date();
    const from = new Date(to);
    from.setDate(from.getDate() - 29);
    return { date_from: isoDay(from), date_to: isoDay(to) };
};

const PRESETS = [
    { label: '7 дней', days: 7 },
    { label: '30 дней', days: 30 },
    { label: '90 дней', days: 90 },
].map((item) => ({
    label: item.label,
    range: () => {
        const to = new Date();
        const from = new Date(to);
        from.setDate(from.getDate() - (item.days - 1));
        return { from: isoDay(from), to: isoDay(to) };
    },
}));

const YES_NO = [{ value: '', label: 'Все' }, { value: 'yes', label: 'Да' }, { value: 'no', label: 'Нет' }];

const StatTile = ({ label, value, note }) => (
    <div className={`${iosCard} px-4 py-3`}>
        <div className="text-[12px] text-slate-500">{label}</div>
        <div className="mt-1 text-[24px] font-semibold leading-none tracking-tight text-slate-900">{value}</div>
        {note && <div className="mt-1 text-[11.5px] text-slate-400">{note}</div>}
    </div>
);

/* Горизонтальные полосы: подпись слева, полоса, число на конце. Полоса ≤ 24 px,
 * скругление только у конца данных — как положено по спеке меток. */
const BarList = ({ rows, empty = 'Нет данных', max = null }) => {
    const top = max ?? Math.max(1, ...rows.map((row) => Number(row.total) || 0));
    if (!rows.length) return <div className="px-4 py-6 text-center text-[12.5px] text-slate-400">{empty}</div>;
    return (
        <div className="space-y-2.5 px-4 py-3.5">
            {rows.map((row) => (
                <div key={row.key} title={`${row.label}: ${row.total}`}>
                    <div className="mb-1 flex items-baseline justify-between gap-3">
                        <span className="min-w-0 truncate text-[12.5px] text-slate-700">
                            {row.label}
                            {row.note && <span className="text-slate-400"> · {row.note}</span>}
                        </span>
                        <span className="shrink-0 text-[12.5px] font-semibold tabular-nums text-slate-900">
                            {row.total}
                        </span>
                    </div>
                    <div className="h-2 rounded-r-[4px] bg-slate-100">
                        <div className="h-2 rounded-r-[4px]"
                             style={{ width: `${Math.max(2, (Number(row.total) / top) * 100)}%`, background: SERIES }} />
                    </div>
                </div>
            ))}
        </div>
    );
};

const Card = ({ title, children, right = null }) => (
    <section className="space-y-1.5">
        <div className="flex items-end justify-between gap-2">
            <div className={iosGroupLabel}>{title}</div>
            {right}
        </div>
        <div className={iosCard}>{children}</div>
    </section>
);

const ChartTooltip = ({ active, payload, label, bucket }) => {
    if (!active || !payload?.length) return null;
    return (
        <div className="rounded-xl bg-white px-3 py-2 shadow-lg ring-1 ring-slate-200/70">
            <div className="text-[14px] font-semibold tabular-nums text-slate-900">{payload[0].value}</div>
            <div className="text-[11.5px] text-slate-500">жалоб · {bucketLabel(label, bucket)}</div>
        </div>
    );
};

export default function ComplaintsAnalytics({ apiBaseUrl, headers, showToast, meta }) {
    const [filters, setFilters] = useState(() => ({ ...defaultPeriod(), target: '' }));
    const [draft, setDraft] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [downloading, setDownloading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/complaints/analytics?${analyticsQuery(filters)}`, { headers: headers() });
            setData(response.data);
            setError(null);
        } catch (err) {
            setError(errorText(err, 'Не удалось посчитать аналитику'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, filters]);

    useEffect(() => { load(); }, [load]);

    const setFilter = (key, value) => setFilters((prev) => {
        const next = { ...prev, [key]: value };
        // Причина принадлежит цели: сменили цель — причина другой цели не подходит.
        if (key === 'target') next.reason = '';
        return next;
    });

    const download = () => {
        setDownloading(true);
        axios.get(`${apiBaseUrl}/api/complaints/export?${analyticsQuery(filters)}`,
            { headers: headers(), responseType: 'blob' })
            .then((response) => {
                const url = URL.createObjectURL(response.data);
                const link = document.createElement('a');
                link.href = url;
                link.download = `Жалобы ${filters.date_from}–${filters.date_to}.xlsx`;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch(async (err) => {
                let message = 'Не удалось собрать выгрузку';
                try {
                    const text = await err?.response?.data?.text?.();
                    message = JSON.parse(text || '{}').error || message;
                } catch (_) { /* останется общая фраза */ }
                showToast?.(message, 'error');
            })
            .finally(() => setDownloading(false));
    };

    const targets = meta?.targets || [];
    const target = targets.find((item) => item.code === filters.target) || null;
    const draftTarget = targets.find((item) => item.code === (draft?.target ?? filters.target)) || null;
    const totals = data?.totals || {};
    const hiddenCount = activeFilterCount(filters);

    const byTarget = useMemo(() => (data?.by_target || []).map((row) => ({
        key: row.target, label: row.title, total: row.total,
        note: row.confirmed ? `подтверждено ${row.confirmed}` : null,
    })), [data]);
    const byReason = useMemo(() => (data?.by_reason || [])
        // Причины недовольства парком стоят своей карточкой (ТЗ выделяет их
        // особо) — в общем списке они повторяли бы её строка в строку.
        .filter((row) => row.target !== 'taxi_park' || filters.target === 'taxi_park')
        .slice(0, 10)
        .map((row) => ({ key: `${row.target}:${row.reason_code}`, label: row.title,
                         note: filters.target ? null : row.target_title, total: row.total })), [data, filters.target]);
    const parkReasons = useMemo(() => (data?.by_reason || [])
        .filter((row) => row.target === 'taxi_park')
        .map((row) => ({ key: row.reason_code, label: row.title, total: row.total })), [data]);
    const byUnit = useMemo(() => (data?.by_unit || []).slice(0, 10).map((row) => ({
        key: `${row.target}:${row.unit_name}`, label: row.unit_name, note: row.target_title, total: row.total,
    })), [data]);
    const byCity = useMemo(() => (data?.by_city || []).slice(0, 10).map((row) => ({
        key: row.city, label: row.city, total: row.total,
    })), [data]);
    const dynamics = data?.dynamics || [];
    const options = data?.options || {};
    const employees = data?.by_employee || [];

    const confirmedTotal = (totals.confirmed || 0) + (totals.partial || 0);

    return (
        <div className={`space-y-4 transition-opacity ${loading && data ? 'opacity-60' : ''}`}>
            {/* Фильтры — одной строкой над всем, что они режут. */}
            <div className="flex flex-wrap items-center gap-2 px-1">
                <IosDateRangePicker from={filters.date_from} to={filters.date_to} max={isoDay(new Date())}
                                    presets={PRESETS} portal
                                    onChange={({ from, to }) => setFilters((prev) => ({
                                        ...prev, date_from: from, date_to: to }))} />
                <CustomSelect className="w-52" variant="ios" value={filters.target || ''}
                              onChange={(value) => setFilter('target', value)}
                              options={[{ value: '', label: 'На кого — все' },
                                  ...targets.map((item) => ({ value: item.code, label: item.title }))]}
                              ariaLabel="На кого жалоба" />
                <button type="button" onClick={() => setDraft({ ...filters })} className={iosBtnGhost}>
                    <SlidersHorizontal size={14} /> Фильтры
                    {hiddenCount > 0 && (
                        <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-blue-600 px-1 text-[11px] font-semibold text-white">
                            {hiddenCount}
                        </span>
                    )}
                </button>
                {hiddenCount > 0 && (
                    <button type="button"
                            onClick={() => setFilters((prev) => ({ date_from: prev.date_from, date_to: prev.date_to,
                                                                   target: prev.target }))}
                            className={iosBtnGhost}>
                        <X size={13} /> Сбросить
                    </button>
                )}
                <button type="button" onClick={download} disabled={downloading}
                        className={`${iosBtnSecondary} ml-auto !py-2`}>
                    {downloading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    Выгрузить в Excel
                </button>
            </div>

            {error && !data && (
                <div className="py-16 text-center text-[13px] text-rose-500">{error}</div>
            )}
            {loading && !data && (
                <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-400">
                    <Loader2 size={15} className="animate-spin" /> Считаем…
                </div>
            )}

            {data && (
                <>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
                        <StatTile label="Всего жалоб" value={totals.total || 0}
                                  note={totals.recorded_only ? `из них зафиксировано ${totals.recorded_only}` : null} />
                        <StatTile label="В работе" value={totals.open || 0} />
                        {/* Процент — от жалоб, которые вообще разбирали: зафиксированные
                            (Яндекс, парк без разбора) отработанными не бывают. */}
                        <StatTile label="Отработано" value={totals.closed || 0}
                                  note={(totals.total - (totals.recorded_only || 0)) > 0
                                      ? `${percent(totals.closed, totals.total - (totals.recorded_only || 0))}% разобранных`
                                      : null} />
                        <StatTile label="Подтверждено" value={confirmedTotal}
                                  note={totals.partial ? `из них частично ${totals.partial}` : null} />
                        <StatTile label="Не подтверждено" value={totals.not_confirmed || 0} />
                        <StatTile label="ОС или обучение" value={totals.needed_work || 0}
                                  note={`ОС ${totals.feedback_done || 0} · тренинг ${totals.training_done || 0}`} />
                    </div>

                    <Card title="Динамика">
                        {dynamics.length ? (
                            <div className="h-[220px] px-2 pb-2 pt-4">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={dynamics} margin={{ top: 4, right: 12, left: -18, bottom: 0 }}>
                                        <CartesianGrid vertical={false} stroke={GRID} />
                                        <XAxis dataKey="bucket" tickLine={false} axisLine={{ stroke: GRID }}
                                               tick={{ fontSize: 11, fill: '#64748b' }}
                                               tickFormatter={(value) => bucketLabel(value, data.bucket)}
                                               minTickGap={12} />
                                        <YAxis allowDecimals={false} tickLine={false} axisLine={false}
                                               tick={{ fontSize: 11, fill: '#64748b' }} width={40} />
                                        <Tooltip cursor={{ fill: 'rgba(148,163,184,0.12)' }}
                                                 content={<ChartTooltip bucket={data.bucket} />} />
                                        {/* Без анимации: перечитка по фильтру должна держать кадр, а не
                                            выращивать столбики заново при каждом клике. */}
                                        <Bar dataKey="total" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={24}
                                             isAnimationActive={false} />
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        ) : (
                            <div className="px-4 py-10 text-center text-[12.5px] text-slate-400">За период жалоб нет</div>
                        )}
                    </Card>

                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        {!target && (
                            <Card title="На кого жалуются">
                                <BarList rows={byTarget} />
                            </Card>
                        )}
                        <Card title={target ? `Причины · ${target.title}` : 'Основные причины'}>
                            <BarList rows={byReason} />
                        </Card>
                        {!target && !!parkReasons.length && (
                            <Card title="Недовольство таксопарком">
                                <BarList rows={parkReasons} />
                            </Card>
                        )}
                        {!!byUnit.length && (
                            <Card title="Подразделения, офисы и парки">
                                <BarList rows={byUnit} />
                            </Card>
                        )}
                        <Card title="Города">
                            <BarList rows={byCity} />
                        </Card>
                    </div>

                    <Card title="Сотрудники"
                          right={employees.some(isRepeated) ? (
                              <span className="px-1 text-[11px] text-slate-400">
                                  повторные — больше одной жалобы за период
                              </span>
                          ) : null}>
                        {employees.length ? (
                            <div className="overflow-x-auto">
                                <table className="w-full text-[12.5px]">
                                    <thead>
                                        <tr className="border-b border-slate-100 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                            <th className="px-4 py-2.5">Сотрудник</th>
                                            <th className="px-3 py-2.5">Подразделение</th>
                                            <th className="px-3 py-2.5 text-right">Жалоб</th>
                                            <th className="px-3 py-2.5 text-right">Подтверждено</th>
                                            <th className="px-3 py-2.5 text-right">ОС</th>
                                            <th className="px-3 py-2.5 text-right">Тренинг</th>
                                            <th className="px-4 py-2.5 text-right">В работе</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {employees.map((row) => (
                                            <tr key={row.employee_id}>
                                                <td className="px-4 py-2 text-slate-900">
                                                    <span className="font-medium">{row.employee_name}</span>
                                                    {isRepeated(row) && (
                                                        <IosBadge tone="amber" className="ml-2 !py-0 !text-[10.5px]">повторные</IosBadge>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2 text-slate-500">{row.department_name || '—'}</td>
                                                <td className="px-3 py-2 text-right tabular-nums text-slate-900">{row.total}</td>
                                                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.confirmed}</td>
                                                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.feedback}</td>
                                                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{row.training}</td>
                                                <td className="px-4 py-2 text-right tabular-nums text-slate-600">{row.pending || ''}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        ) : (
                            <div className="px-4 py-6 text-center text-[12.5px] text-slate-400">
                                Жалоб на определённых сотрудников за период нет
                            </div>
                        )}
                    </Card>
                </>
            )}

            {/* Остальные фильтры ТЗ — отдельным окном: девять полей постоянно на
                экране — это не панель, а анкета. */}
            <IosModal open={!!draft} onClose={() => setDraft(null)} title="Фильтры"
                      footer={(
                          <>
                              <button type="button" onClick={() => setDraft({
                                  date_from: filters.date_from, date_to: filters.date_to, target: filters.target })}
                                      className={iosBtnSecondary}>
                                  Сбросить
                              </button>
                              <button type="button" onClick={() => { setFilters(draft); setDraft(null); }}
                                      className={iosBtnPrimary}>
                                  Показать
                              </button>
                          </>
                      )}>
                {draft && (
                    <div className="space-y-3.5">
                        {[
                            // Варианты — только то, что встречалось за период, и без учёта
                            // уже выбранных фильтров (queries.filter_options): иначе список
                            // сужался бы сам собой до выбранного значения.
                            ['city', 'Город', [{ value: '', label: 'Все города' },
                                ...(options.cities || []).map((city) => ({ value: city, label: city }))], true],
                            ['reason', 'Причина', [{ value: '', label: draftTarget ? 'Все причины' : 'Сначала выберите «на кого»' },
                                ...((draftTarget?.reasons) || []).map((item) => ({ value: item.code, label: item.title }))]],
                            ['unit', 'Подразделение, офис или парк', [{ value: '', label: 'Все' },
                                ...(options.units || []).map((unit) => ({ value: unit, label: unit }))], true],
                            ['employee_id', 'Сотрудник', [{ value: '', label: 'Все' },
                                ...(options.employees || []).map((row) => ({ value: String(row.id), label: row.name }))], true],
                            ['status', 'Статус', [{ value: '', label: 'Все' }, { value: 'open', label: 'В работе' },
                                { value: 'closed', label: 'Отработанные' }, { value: 'recorded', label: 'Зафиксированные' }]],
                            ['result', 'Итог проверки', [{ value: '', label: 'Любой' },
                                ...((meta?.results || []).map((item) => ({ value: item.code, label: item.title })))]],
                        ].map(([key, label, options, searchable]) => (
                            <div key={key}>
                                <div className={`${iosGroupLabel} mb-1.5`}>{label}</div>
                                <CustomSelect variant="ios" value={draft[key] || ''} searchable={Boolean(searchable)}
                                              disabled={key === 'reason' && !draftTarget}
                                              onChange={(value) => setDraft((prev) => ({ ...prev, [key]: value }))}
                                              options={options} ariaLabel={label} />
                            </div>
                        ))}
                        {[
                            ['confirmed', 'Подтверждённые', [{ value: '', label: 'Все' }, { value: 'yes', label: 'Подтверждённые' },
                                { value: 'no', label: 'Неподтверждённые' }]],
                            ['feedback', 'Обратная связь проведена', YES_NO],
                            ['training', 'Тренинг проведён', YES_NO],
                        ].map(([key, label, options]) => (
                            <div key={key} className="flex flex-wrap items-center justify-between gap-2">
                                <span className="text-[13px] text-slate-800">{label}</span>
                                <IosSegmented size="xs" value={draft[key] || ''} options={options}
                                              onChange={(value) => setDraft((prev) => ({ ...prev, [key]: value }))}
                                              ariaLabel={label} />
                            </div>
                        ))}
                    </div>
                )}
            </IosModal>
        </div>
    );
}
