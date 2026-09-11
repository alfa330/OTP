import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, IosBadge, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import { createFunnelApi } from './funnelApi';
import {
    DATE_PRESETS, formatMoment, formatNumber, isLoadDirection, presetRange, shortDay,
} from './funnelFormat';
import AnomaliesPanel from './AnomaliesPanel';
import DynamicsChart from './DynamicsChart';
import FunnelSteps from './FunnelSteps';
import HeatmapGrid from './HeatmapGrid';
import LeadsSheet from './LeadsSheet';
import ManualImportPanel from './ManualImportPanel';
import MappingPanel from './MappingPanel';
import OperatorsTable from './OperatorsTable';
import ReasonsPanel from './ReasonsPanel';
import SummaryTiles from './SummaryTiles';
import TargetsPanel from './TargetsPanel';

/**
 * Раздел «Воронка ОП»: ежедневная воронка обзвона по направлениям отдела продаж.
 *
 * Четыре супервайзера вели одно и то же в Excel, каждый свой файл (задачи #301,
 * #302, #303, #305). Раздел переносит те же показатели и те же формулы, но
 * данные подтягиваются сами, а к срезу «на сегодня» добавлены динамика по дням,
 * сравнение с прошлым периодом и разбор причин с выходом на конкретных людей.
 *
 * Почему свежесть данных вынесена в шапку, а не спрятана в настройки. СРМ
 * переписывает прошлое: замерено, что за десять дней «дозвон» по одним и тем же
 * суткам вырос с 499 до 534, а у одного оператора лиды открепили целиком.
 * Суточный итог у нас фиксируется, но человек обязан видеть, когда была
 * выгрузка, сколько операторов ещё не сопоставлено и были ли расхождения после
 * пересчёта — иначе он поверит цифре, у которой под ногами ничего нет.
 *
 * Данные вкладок тянутся ЛЕНИВО, при первом открытии: «Сводка» и «Операторы»
 * нужны всегда, а тепловая карта и разбор причин — далеко не каждый раз, и
 * тянуть их сразу значит четыре запроса вместо одного на каждое переключение
 * направления.
 */

const TABS = [
    { key: 'summary', label: 'Сводка' },
    { key: 'operators', label: 'Операторы' },
    { key: 'dynamics', label: 'Динамика' },
    { key: 'reasons', label: 'Причины' },
    { key: 'settings', label: 'Настройки' },
];

const DEFAULT_PRESET = 'week7';

const Skeleton = ({ text = 'Загрузка…' }) => (
    <div className="py-16 text-center text-sm text-slate-500">{text}</div>
);

const Failed = ({ text, onRetry }) => (
    <div className="rounded-2xl bg-rose-50 ring-1 ring-rose-200 p-4 text-sm text-rose-700">
        {text}
        {onRetry ? (
            <button type="button" onClick={onRetry} className="ml-3 underline underline-offset-2">
                Повторить
            </button>
        ) : null}
    </div>
);

export default function OpFunnelView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const api = useMemo(
        () => createFunnelApi({ apiBaseUrl, withAccessTokenHeader }),
        [apiBaseUrl, withAccessTokenHeader],
    );

    /* Тост держим в ref, а не в зависимостях эффектов: родитель создаёт функцию
       заново на каждом рендере, и в зависимостях она гоняла бы загрузку по кругу
       (в проекте этим уже ломали и перезапрос данных, и задержку поиска). */
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const toast = useCallback((text, kind) => toastRef.current?.(text, kind), []);

    const [meta, setMeta] = useState(null);
    const [metaError, setMetaError] = useState('');
    const [direction, setDirection] = useState('');
    const [tab, setTab] = useState('summary');

    const [preset, setPreset] = useState(DEFAULT_PRESET);
    const [range, setRange] = useState(() => presetRange(DEFAULT_PRESET, new Date()));
    const [customOpen, setCustomOpen] = useState(false);

    const [overview, setOverview] = useState(null);
    const [operators, setOperators] = useState(null);
    const [daysData, setDaysData] = useState(null);
    const [reasons, setReasons] = useState(null);
    const [loading, setLoading] = useState({});
    const [errors, setErrors] = useState({});

    const [syncing, setSyncing] = useState(false);
    const [force, setForce] = useState(false);
    const [exporting, setExporting] = useState(false);

    const [sheet, setSheet] = useState(null);

    const kind = isLoadDirection(direction) ? 'load' : 'funnel';
    const tabs = useMemo(
        () => (kind === 'load' ? TABS.filter((item) => item.key !== 'reasons') : TABS),
        [kind],
    );

    // ── /meta ───────────────────────────────────────────────────────────────

    const loadMeta = useCallback(async () => {
        setMetaError('');
        try {
            const data = await api.meta();
            if (data === null) return;
            setMeta(data);
            setDirection((current) => {
                const codes = (data.directions || []).map((item) => item.code);
                return codes.includes(current) ? current : (codes[0] || '');
            });
        } catch (exc) {
            setMetaError(exc?.message || 'Не удалось получить состав раздела');
        }
    }, [api]);

    useEffect(() => { loadMeta(); }, [loadMeta]);

    // ── загрузка вкладок ────────────────────────────────────────────────────

    const mark = (key, value) => setLoading((prev) => ({ ...prev, [key]: value }));
    const fail = (key, text) => setErrors((prev) => ({ ...prev, [key]: text }));

    const loadOverview = useCallback(async () => {
        if (!direction) return;
        mark('overview', true);
        fail('overview', '');
        try {
            const data = await api.overview({ direction, ...range });
            if (data !== null) setOverview(data);
        } catch (exc) {
            fail('overview', exc?.message || 'Не удалось получить сводку');
        } finally {
            mark('overview', false);
        }
    }, [api, direction, range]);

    const loadOperators = useCallback(async () => {
        if (!direction) return;
        mark('operators', true);
        fail('operators', '');
        try {
            const data = await api.operators({ direction, ...range });
            if (data !== null) setOperators(data);
        } catch (exc) {
            fail('operators', exc?.message || 'Не удалось получить таблицу операторов');
        } finally {
            mark('operators', false);
        }
    }, [api, direction, range]);

    const loadDays = useCallback(async () => {
        if (!direction) return;
        mark('days', true);
        fail('days', '');
        try {
            const data = await api.days({ direction, ...range });
            if (data !== null) setDaysData(data);
        } catch (exc) {
            fail('days', exc?.message || 'Не удалось получить динамику');
        } finally {
            mark('days', false);
        }
    }, [api, direction, range]);

    const loadReasons = useCallback(async () => {
        if (!direction) return;
        mark('reasons', true);
        fail('reasons', '');
        try {
            const data = await api.reasons({ direction, ...range });
            if (data !== null) setReasons(data);
        } catch (exc) {
            fail('reasons', exc?.message || 'Не удалось получить разбор причин');
        } finally {
            mark('reasons', false);
        }
    }, [api, direction, range]);

    /* Смена направления или периода: сбрасываем УЖЕ ЗАГРУЖЕННОЕ, чтобы на экране
       не осталась чужая таблица под новой шапкой. Именно сброс в одном проходе, а
       не догрузка поверх: иначе между ответами человек видит числа одного
       направления с подписью другого. */
    const scope = `${direction}|${range.from}|${range.to}`;
    const lastScope = useRef('');
    useEffect(() => {
        if (!direction) return;
        if (lastScope.current === scope) return;
        lastScope.current = scope;
        setOverview(null);
        setOperators(null);
        setDaysData(null);
        setReasons(null);
        setSheet(null);
        loadOverview();
        if (tab === 'operators') loadOperators();
        if (tab === 'dynamics') loadDays();
        if (tab === 'reasons') loadReasons();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope]);

    /* Первое открытие вкладки — тогда и запрос. Повторное переключение туда-сюда
       данных не перезапрашивает. */
    useEffect(() => {
        if (!direction) return;
        if (tab === 'operators' && !operators && !loading.operators) loadOperators();
        if (tab === 'dynamics' && !daysData && !loading.days) loadDays();
        if (tab === 'reasons' && !reasons && !loading.reasons) loadReasons();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tab, direction, operators, daysData, reasons]);

    // ── действия ────────────────────────────────────────────────────────────

    const applyPreset = (key) => {
        const next = presetRange(key, new Date());
        if (!next) return;
        setPreset(key);
        setCustomOpen(false);
        setRange(next);
    };

    /* Пикер даты в браузере отдаёт значение на КАЖДОЕ нажатие: набирая год, человек
       успевает прислать «0002-09-11» и «0202-09-11». Без проверки года раздел на
       каждую цифру уходил в запрос и получал 400 «Период больше года», показывая
       ошибку прямо во время набора. Принимаем только правдоподобную дату. */
    const SANE_YEAR = /^(20\d{2})-\d{2}-\d{2}$/;

    const applyCustom = (from, to) => {
        if (!SANE_YEAR.test(from || '') || !SANE_YEAR.test(to || '')) return;
        if (from > to) {
            toast('Начало периода позже конца', 'warning');
            return;
        }
        setPreset('custom');
        setRange({ from, to });
    };

    const runSync = async () => {
        setSyncing(true);
        try {
            const result = await api.sync({ direction, ...range, force });
            const frozen = Number(result?.days_frozen || 0);
            const redone = Number(result?.days_redone || 0);
            const drift = Number(result?.drift_rows || 0);
            if (result?.status === 'ok') {
                const parts = [`лидов ${formatNumber(result.leads_seen)}`];
                if (frozen) parts.push(`новых суток ${frozen}`);
                if (redone) parts.push(`перечитано ${redone}`);
                if (drift) parts.push(`расхождений ${drift}`);
                toast(`Данные обновлены: ${parts.join(', ')}`, 'success');
            } else {
                toast(result?.error || 'Выгрузка не удалась', 'error');
            }
            lastScope.current = '';   // заставит перезагрузить вкладки
            setForce(false);
            await loadMeta();
            setOverview(null);
            setOperators(null);
            setDaysData(null);
            setReasons(null);
            await loadOverview();
        } catch (exc) {
            toast(exc?.message || 'Не удалось запустить выгрузку', 'error');
        } finally {
            setSyncing(false);
        }
    };

    const runExport = async () => {
        setExporting(true);
        try {
            const { filename } = await api.exportFile({ direction, ...range });
            toast(`Файл собран: ${filename}`, 'success');
        } catch (exc) {
            toast(exc?.message || 'Не удалось собрать выгрузку', 'error');
        } finally {
            setExporting(false);
        }
    };

    // ── шапка ───────────────────────────────────────────────────────────────

    const caps = meta?.capabilities || {};
    const directions = meta?.directions || [];
    const freshness = meta?.freshness?.[direction] || overview?.freshness || {};
    const currentTitle = directions.find((item) => item.code === direction)?.title || '';

    if (metaError) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-4">
                <Failed text={metaError} onRetry={loadMeta} />
            </div>
        );
    }
    if (!meta) {
        return <div style={{ fontFamily: APPLE_FONT }}><Skeleton text="Загрузка раздела…" /></div>;
    }
    if (!directions.length) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-4">
                <div className="rounded-2xl bg-white ring-1 ring-slate-200/70 p-8 text-center">
                    <FaIcon className="fas fa-filter text-2xl text-slate-300" />
                    <p className="mt-3 text-sm text-slate-600">
                        Ни одно направление отдела продаж вам не открыто.
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                        Направления выдаются по группам: обратитесь к руководителю отдела.
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <header className="rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-sm p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h1 className="text-lg font-semibold text-slate-900">Воронка ОП</h1>
                        <p className="text-xs text-slate-500 mt-0.5">
                            Ежедневная воронка обзвона по направлениям отдела продаж
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {caps.can_sync ? (
                            <button
                                type="button"
                                onClick={runSync}
                                disabled={syncing}
                                className={`${syncing ? iosBtnSecondary : iosBtnPrimary} px-3.5 py-2 text-[13px]`}
                            >
                                <FaIcon className={`fas fa-rotate mr-1.5 ${syncing ? 'animate-spin' : ''}`} />
                                {syncing ? 'Обновляем…' : 'Обновить данные'}
                            </button>
                        ) : null}
                        {caps.can_export ? (
                            <button
                                type="button"
                                onClick={runExport}
                                disabled={exporting}
                                className={`${iosBtnSecondary} px-3.5 py-2 text-[13px]`}
                            >
                                <FaIcon className="fas fa-file-arrow-down mr-1.5" />
                                {exporting ? 'Собираем…' : 'Выгрузить'}
                            </button>
                        ) : null}
                    </div>
                </div>

                {/* Направления */}
                <div className="mt-3 overflow-x-auto">
                    <IosSegmented
                        value={direction}
                        onChange={setDirection}
                        options={directions.map((item) => ({ value: item.code, label: item.title }))}
                        ariaLabel="Направление отдела продаж"
                    />
                </div>

                {/* Период */}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <IosSegmented
                        value={preset}
                        onChange={applyPreset}
                        size="sm"
                        options={DATE_PRESETS.map((item) => ({ value: item.key, label: item.label }))}
                        ariaLabel="Период"
                    />
                    <button
                        type="button"
                        onClick={() => setCustomOpen((open) => !open)}
                        className={`${preset === 'custom' ? iosBtnSecondary : iosBtnGhost} px-3 py-1.5 text-[13px]`}
                    >
                        <FaIcon className="fas fa-calendar-days mr-1.5" />
                        {preset === 'custom'
                            ? `${shortDay(range.from)} — ${shortDay(range.to)}`
                            : 'Свой период'}
                    </button>
                </div>

                {customOpen ? (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                        <input
                            type="date"
                            value={range.from}
                            onChange={(event) => applyCustom(event.target.value, range.to)}
                            className={`${iosInput} w-auto`}
                            aria-label="Начало периода"
                        />
                        <span className="text-slate-400">—</span>
                        <input
                            type="date"
                            value={range.to}
                            onChange={(event) => applyCustom(range.from, event.target.value)}
                            className={`${iosInput} w-auto`}
                            aria-label="Конец периода"
                        />
                    </div>
                ) : null}

                {/* Свежесть данных. Не украшение: без неё человек поверит цифре,
                    под которой ничего нет. */}
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
                    <span>
                        {freshness.last_run_at
                            ? <>Обновлено {formatMoment(freshness.last_run_at)}</>
                            : <>Данные ещё не выгружались</>}
                    </span>
                    {freshness.status === 'error' ? (
                        <IosBadge tone="rose">Последняя выгрузка не удалась</IosBadge>
                    ) : null}
                    {Number(freshness.unmapped) > 0 ? (
                        <button
                            type="button"
                            onClick={() => setTab('settings')}
                            className="underline underline-offset-2 text-amber-700"
                        >
                            Не сопоставлено операторов: {formatNumber(freshness.unmapped)}
                        </button>
                    ) : null}
                    {Number(freshness.drift_rows) > 0 ? (
                        <span className="text-amber-700">
                            После пересчёта изменилось значений: {formatNumber(freshness.drift_rows)}
                        </span>
                    ) : null}
                    {overview?.hours_source === 'schedule' ? (
                        <span>Часы взяты по графику смен</span>
                    ) : null}
                </div>

                {caps.can_edit_targets && caps.can_sync ? (
                    <label className="mt-2 flex items-start gap-2 text-[12px] text-slate-500">
                        <input
                            type="checkbox"
                            checked={force}
                            onChange={(event) => setForce(event.target.checked)}
                            className="mt-0.5"
                        />
                        <span>
                            Перечитать уже зафиксированные сутки. Цифры за эти дни могут
                            измениться — их уже могли назвать на планёрке; все расхождения
                            попадут в журнал.
                        </span>
                    </label>
                ) : null}
            </header>

            <div className="overflow-x-auto">
                <IosSegmented
                    value={tab}
                    onChange={setTab}
                    options={tabs.map((item) => ({ value: item.key, label: item.label }))}
                    ariaLabel="Раздел воронки"
                />
            </div>

            {tab === 'summary' ? (
                <div className="space-y-4">
                    {errors.overview ? <Failed text={errors.overview} onRetry={loadOverview} /> : null}
                    {loading.overview && !overview ? <Skeleton text="Считаем сводку…" /> : null}
                    {overview ? (
                        <>
                            <SummaryTiles
                                summary={overview.summary}
                                compare={overview.compare}
                                kind={kind}
                            />
                            <AnomaliesPanel anomalies={overview.anomalies} />
                            <FunnelSteps steps={overview.funnel} />
                        </>
                    ) : null}
                </div>
            ) : null}

            {tab === 'operators' ? (
                <div className="space-y-3">
                    {errors.operators ? <Failed text={errors.operators} onRetry={loadOperators} /> : null}
                    {loading.operators && !operators ? <Skeleton text="Собираем операторов…" /> : null}
                    {operators ? (
                        <OperatorsTable
                            operators={operators.operators}
                            total={operators.total}
                            targets={operators.targets}
                            kind={kind}
                            onOpenOperator={(row) => setSheet({
                                title: `Лиды — ${row.name}`,
                                filters: { userId: row.user_id },
                            })}
                        />
                    ) : null}
                </div>
            ) : null}

            {tab === 'dynamics' ? (
                <div className="space-y-4">
                    {errors.days ? <Failed text={errors.days} onRetry={loadDays} /> : null}
                    {loading.days && !daysData ? <Skeleton text="Строим динамику…" /> : null}
                    {daysData ? (
                        <>
                            <DynamicsChart
                                days={daysData.days}
                                targets={daysData.targets}
                                operators={daysData.heatmap}
                                kind={kind}
                            />
                            <HeatmapGrid
                                heatmap={daysData.heatmap}
                                days={daysData.days}
                                onOpenCell={({ userId, day }) => setSheet({
                                    title: `Лиды за ${shortDay(day)}`,
                                    filters: { userId, workDay: day },
                                })}
                            />
                        </>
                    ) : null}
                </div>
            ) : null}

            {tab === 'reasons' ? (
                <div className="space-y-3">
                    {errors.reasons ? <Failed text={errors.reasons} onRetry={loadReasons} /> : null}
                    {loading.reasons && !reasons ? <Skeleton text="Разбираем причины…" /> : null}
                    {reasons ? (
                        <ReasonsPanel
                            buckets={reasons.buckets}
                            onOpenReason={({ bucket, code, title }) => setSheet({
                                title: `Лиды — ${title}`,
                                filters: { bucket, reasonCode: code },
                            })}
                        />
                    ) : null}
                </div>
            ) : null}

            {tab === 'settings' ? (
                <div className="space-y-4">
                    {caps.can_map_operators ? (
                        <MappingPanel
                            api={api}
                            showToast={showToast}
                            onChanged={() => loadMeta()}
                        />
                    ) : null}
                    <TargetsPanel
                        api={api}
                        direction={direction}
                        canEdit={Boolean(caps.can_edit_targets)}
                        showToast={showToast}
                    />
                    {kind === 'load' && caps.can_import_manual ? (
                        <ManualImportPanel
                            api={api}
                            direction={direction}
                            showToast={showToast}
                            onImported={() => { lastScope.current = ''; loadMeta(); }}
                        />
                    ) : null}
                </div>
            ) : null}

            <LeadsSheet
                open={Boolean(sheet)}
                onClose={() => setSheet(null)}
                api={api}
                direction={direction}
                period={range}
                filters={sheet?.filters || null}
                title={sheet?.title || `Лиды — ${currentTitle}`}
            />
        </div>
    );
}
