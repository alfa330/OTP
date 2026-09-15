import React, { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost } from '../ui/ios';
import { formatClock, formatInt, wallboardStaleNotice } from './szovWallboardShared';
import { Grid, KeyTile, Section, StatTile } from './SzovWallboardTiles';
import { BroadcastControls } from './SzovWallboardView';
import {
    OP_METRIC_MAP,
    formatCount,
    formatRatio,
    formatSeconds,
    opArTone,
    opFreshnessNotice,
    opMetricHint,
    opStatusChip,
    readOpMetric,
    useOpWallboardSnapshot,
} from './opWallboardShared';

/*
 * «Табло ОП» — отдел продаж на FreePBX. Раскладка та же, что у табло СЗоВ и Тез: ключевые
 * плитки «сейчас» и итоги дня сверху, разрезы по линиям и часам, поимённый список — чтобы
 * глазу не переучиваться при переходе между стенами. Экран рассчитан на стену: обновляется
 * сам, цветом помечено только то, что несёт смысл (AR вне коридора, SL ниже порога, статусы
 * людей), при отставании данных — янтарный чип, а не пустой экран.
 */

const FULLSCREEN_Z = 150;
const SOURCE_LABEL = 'мост «Касаний»';

const MetricKeyTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = OP_METRIC_MAP[metricKey];
    const { value, tone } = readOpMetric(metric, snapshot);
    return <KeyTile label={metric.label} value={value} hint={opMetricHint(metric, snapshot)} tone={tone} scale={scale} />;
};

const MetricStatTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = OP_METRIC_MAP[metricKey];
    const { value, secondary, tone } = readOpMetric(metric, snapshot);
    return <StatTile label={metric.label} value={value} secondary={secondary} tone={tone} scale={scale} />;
};

const toneText = { good: 'text-green-700', warn: 'text-amber-700', bad: 'text-rose-700', neutral: 'text-slate-800', info: 'text-blue-700' };

/** Разрез по линиям (очередям станции): та же арифметика, что в итогах, на каждую строку. */
const QueuesTable = ({ snapshot, scale = 1 }) => {
    const rows = snapshot?.queues || [];
    if (!rows.length) {
        return <div className="text-[13px] text-slate-500">Сегодня входящих на линии ещё не было.</div>;
    }
    const cell = 'px-3 py-2 text-right tabular-nums';
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left" style={{ fontSize: `${13 * scale}px` }}>
                <thead className="text-slate-500">
                    <tr>
                        <th className="px-3 py-2">Линия</th>
                        <th className={cell}>Входящих</th>
                        <th className={cell}>Принято</th>
                        <th className={cell}>Потеряно</th>
                        <th className={cell}>AR</th>
                        <th className={cell}>SL</th>
                        <th className={cell}>Разговор</th>
                        <th className={cell}>Ожидание</th>
                        <th className={cell}>Исходящих</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.queue} className="border-t border-slate-100">
                            <td className="px-3 py-2 font-medium text-slate-800">{row.queue}</td>
                            <td className={cell}>{formatCount(row.arrived)}</td>
                            <td className={cell}>{formatCount(row.answered)}</td>
                            <td className={`${cell} ${row.missed ? 'text-amber-700' : ''}`}>{formatCount(row.missed)}</td>
                            <td className={`${cell} font-semibold ${toneText[opArTone(row.ar, snapshot)]}`}>{formatRatio(row.ar)}</td>
                            <td className={cell}>{formatRatio(row.sl)}</td>
                            <td className={cell}>{formatSeconds(row.avg_talk_seconds)}</td>
                            <td className={cell}>{formatSeconds(row.avg_wait_seconds)}</td>
                            <td className={cell}>
                                {formatCount(row.outgoing)}
                                {row.outgoing ? <span className="text-slate-400"> → {formatInt(row.outgoing_answered)}</span> : null}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

/*
 * По часам: столбики принятых и потерянных, без библиотек — на стене нужны пропорции, а не
 * подписи к каждому делению. Пустые часы до начала дня не рисуются, чтобы утро не читалось
 * как провал.
 */
const HourlyBars = ({ snapshot, scale = 1 }) => {
    const hourly = snapshot?.hourly || [];
    const active = hourly.filter((h) => h.arrived || h.outgoing);
    if (!active.length) {
        return <div className="text-[13px] text-slate-500">Звонков за сегодня ещё не было.</div>;
    }
    const first = Math.min(...active.map((h) => h.hour));
    const last = Math.max(...active.map((h) => h.hour));
    const shown = hourly.slice(first, last + 1);
    const peak = Math.max(1, ...shown.map((h) => h.arrived + h.outgoing));
    const height = 120 * scale;
    return (
        <div className="flex items-end gap-1" style={{ height: `${height + 28}px` }}>
            {shown.map((h) => {
                const total = h.arrived + h.outgoing;
                const px = (n) => Math.round((n / peak) * height);
                return (
                    <div key={h.hour} className="flex flex-1 flex-col items-center justify-end" title={`${h.hour}:00 — входящих ${h.arrived} (принято ${h.answered}, потеряно ${h.missed}), исходящих ${h.outgoing}`}>
                        <div className="flex w-full flex-col justify-end overflow-hidden rounded-t" style={{ height: `${height}px` }}>
                            <div className="w-full bg-slate-300" style={{ height: `${px(h.outgoing)}px` }} />
                            <div className="w-full bg-amber-400" style={{ height: `${px(h.missed)}px` }} />
                            <div className="w-full bg-green-500" style={{ height: `${px(h.answered)}px` }} />
                        </div>
                        <div className="mt-1 text-slate-500 tabular-nums" style={{ fontSize: `${11 * scale}px` }}>
                            {total ? `${h.hour}` : ''}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

const HourlyLegend = () => (
    <div className="flex items-center gap-4 text-[12px] text-slate-500">
        <span className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-green-500" />принято</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-amber-400" />потеряно</span>
        <span className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-slate-300" />исходящих</span>
    </div>
);

/** Поимённый список: статус телефона и счётчики дня. Порядок — по разряду статуса. */
const OperatorsTable = ({ snapshot, scale = 1 }) => {
    const rows = snapshot?.operators || [];
    if (!rows.length) {
        return <div className="text-[13px] text-slate-500">В составе отдела нет сотрудников с внутренним номером.</div>;
    }
    const cell = 'px-3 py-2 text-right tabular-nums';
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left" style={{ fontSize: `${13 * scale}px` }}>
                <thead className="text-slate-500">
                    <tr>
                        <th className="px-3 py-2">Сотрудник</th>
                        <th className="px-3 py-2">Внутренний</th>
                        <th className="px-3 py-2">Статус</th>
                        <th className={cell}>Принято</th>
                        <th className={cell}>Потеряно</th>
                        <th className={cell}>Исходящих</th>
                        <th className={cell}>Разговор</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => {
                        const chip = opStatusChip(row);
                        return (
                            <tr key={`${row.id ?? 'ext'}-${row.ext}`} className={`border-t border-slate-100 ${chip.muted ? 'text-slate-400' : ''}`}>
                                <td className="px-3 py-2 font-medium">{row.name}</td>
                                <td className="px-3 py-2 tabular-nums">{row.ext || '—'}</td>
                                <td className="px-3 py-2">
                                    <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-0.5 ${chip.className}`}>
                                        {chip.label}
                                        {row.status_seconds ? <span className="opacity-70 tabular-nums">{formatSeconds(row.status_seconds)}</span> : null}
                                    </span>
                                </td>
                                <td className={cell}>{formatCount(row.answered)}</td>
                                <td className={`${cell} ${row.missed ? 'text-amber-700' : ''}`}>{formatCount(row.missed)}</td>
                                <td className={cell}>
                                    {formatCount(row.outgoing)}
                                    {row.outgoing ? <span className="text-slate-400"> → {formatInt(row.outgoing_answered)}</span> : null}
                                </td>
                                <td className={cell}>{formatSeconds(row.talk_seconds)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};

/** Тело табло — одно и то же встроенным и на весь экран, различается только масштабом. */
function OpWallboardBody({ snapshot, scale = 1 }) {
    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            <Section icon="fa-bolt" title="Ключевые показатели · сейчас">
                <Grid>
                    <MetricKeyTile metricKey="op_online" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="op_talking" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="op_free" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="op_break" snapshot={snapshot} scale={scale} />
                </Grid>
            </Section>
            <Section icon="fa-chart-bar" title="Показатели за день">
                <Grid>
                    <MetricStatTile metricKey="op_arrived" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="op_answered" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="op_missed" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="op_ar" snapshot={snapshot} scale={scale} />
                </Grid>
                <div className="mt-3">
                    <Grid>
                        <MetricStatTile metricKey="op_sl" snapshot={snapshot} scale={scale} />
                        <MetricStatTile metricKey="op_avg_talk" snapshot={snapshot} scale={scale} />
                        <MetricStatTile metricKey="op_avg_wait" snapshot={snapshot} scale={scale} />
                        <MetricStatTile metricKey="op_outgoing" snapshot={snapshot} scale={scale} />
                    </Grid>
                </div>
            </Section>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <Section icon="fa-phone" title="По линиям">
                    <QueuesTable snapshot={snapshot} scale={scale} />
                </Section>
                <Section icon="fa-clock" title="По часам" right={<HourlyLegend />}>
                    <HourlyBars snapshot={snapshot} scale={scale} />
                </Section>
            </div>
            <Section icon="fa-users" title="Сотрудники" right={<span className="text-[12px] text-slate-500">статусы — iCORE Phone, счётчики — касания за день</span>}>
                <OperatorsTable snapshot={snapshot} scale={scale} />
            </Section>
        </div>
    );
}

const clockLabel = (snapshot) => {
    const at = snapshot?.bridge?.live_at || snapshot?.captured_at;
    return at ? `данные на ${formatClock(at)}` : null;
};

const BROADCAST_HINT = (
    <>
        Отклонением считаем то же, что подсвечено на табло: AR вне коридора нормы, SL ниже 80 %
        или мост «Касаний» замолчал и цифры на табло замерли. Проценты считаются, когда за день
        накопилось хотя бы 20 входящих — утренние единицы звонков поводом не считаются.
    </>
);

export default function OpWallboardView({ apiBaseUrl, withAccessTokenHeader, showToast, canManageBroadcast = false }) {
    const { snapshot, error, loading, refresh } = useOpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, SOURCE_LABEL), [error, snapshot]);
    const freshness = useMemo(() => opFreshnessNotice(snapshot), [snapshot]);
    const notice = staleNotice || freshness;

    const header = (
        <div className={`${iosCard} flex flex-wrap items-center justify-between gap-3 p-4`}>
            <div>
                <div className="flex items-center gap-2 text-[17px] font-semibold text-slate-900">
                    <FaIcon className="fas fa-tachometer-alt text-blue-600"></FaIcon>
                    Табло ОП
                </div>
                <div className="text-[13px] text-slate-500">
                    Отдел продаж в реальном времени{clockLabel(snapshot) ? ` · ${clockLabel(snapshot)}` : ''}
                </div>
                {notice ? (
                    <div className="mt-1 inline-flex items-center gap-2 rounded-full bg-amber-50 px-2.5 py-0.5 text-[12px] text-amber-800 ring-1 ring-amber-200">
                        <FaIcon className="fas fa-triangle-exclamation"></FaIcon>
                        {notice}
                    </div>
                ) : null}
            </div>
            <div className="flex items-center gap-2">
                {canManageBroadcast ? (
                    <BroadcastControls
                        direction="op"
                        directionLabel="ОП"
                        deviationHint={BROADCAST_HINT}
                        apiBaseUrl={apiBaseUrl}
                        withAccessTokenHeader={withAccessTokenHeader}
                        showToast={showToast}
                    />
                ) : null}
                <button type="button" className={`${iosBtnGhost} disabled:opacity-40`} disabled={loading} onClick={() => refresh()}>
                    <FaIcon className={`fas fa-rotate ${loading ? 'animate-spin' : ''}`}></FaIcon>
                    Обновить
                </button>
                <button type="button" className={iosBtnGhost} onClick={() => setFullscreen(true)} title="На весь экран — для вывода на стену">
                    <FaIcon className="fas fa-expand"></FaIcon>
                    На стену
                </button>
            </div>
        </div>
    );

    if (!snapshot) {
        return (
            <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
                {header}
                <div className={`${iosCard} p-6 text-[13px] ${loading ? 'text-slate-500' : 'text-rose-600'}`}>
                    {loading ? 'Загружаем касания за сегодня…' : (error || 'Данные недоступны')}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <OpWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon="fa-tachometer-alt"
                    title="Табло ОП"
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <OpWallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
}
