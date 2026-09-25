import React, { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, IosSegmented, iosCard, iosBtnGhost } from '../ui/ios';
import { isoDate } from '../ui/DateRangePicker';
import { formatInt, wallboardStaleNotice } from './szovWallboardShared';
import { Grid, KeyTile, Section, SegmentedSwitch, StatTile } from './SzovWallboardTiles';
import { BroadcastControls, ChatExportControls, WidgetButton } from './SzovWallboardView';
import OpStatusJournalPanel from './OpStatusJournal';
import OpChatWallboardBody from './OpChatWallboard';
import { OP_GROUP_ALL, opEntryTime, opGroupOptions, opGroupView, opHourRange, opSelectedGroup } from './opWallboardGroups';
import {
    OP_METRIC_MAP,
    OP_WALLBOARD_VIEWS,
    formatCount,
    formatSeconds,
    opClockLabel,
    opDataGapNotice,
    opFreshnessNotice,
    opMetricHint,
    opChatClockLabel,
    opChatFreshnessNotice,
    opStatusChip,
    readOpMetric,
    useOpChatWallboardSnapshot,
    useOpWallboardSnapshot,
} from './opWallboardShared';

/*
 * «Табло ОП» — отдел продаж на FreePBX. Раскладка та же, что у табло СЗоВ и Тез: ключевые
 * плитки «сейчас» и итоги дня сверху, разрез по часам, поимённый список — чтобы глазу не
 * переучиваться при переходе между стенами. Экран рассчитан на стену: обновляется сам, цветом
 * помечено только то, что несёт смысл (AR вне коридора, SL ниже порога, статусы людей), при
 * отставании данных — янтарный чип, а не пустой экран.
 *
 * Разреза «по линиям» нет нигде — ни здесь, ни в снимке, ни в отбивке (снят 16.09.2026 по
 * решению владельца): очереди станции — это внутренние номера вида 3010, они читались как шум.
 *
 * ТЗ #339: фильтр по группам пересчитывает всё табло сразу (разрезы приезжают в том же снимке),
 * в списке — время входа, а ФИО открывает журнал статусов боковой панелью здесь же.
 *
 * Задача #367: у раздела два направления, как у табло СЗоВ, — «Линия» (всё, что выше) и «Чат»
 * (чаты верификаторов в Wazzup, OpChatWallboard.jsx). Переключатель в шапке; монтируется только
 * выбранное направление, поэтому скрытое не опрашивает свой источник.
 */

const FULLSCREEN_Z = 150;
// Журнал поверх табло: на странице — ниже модальных окон (z 90), на весь экран — над полотном.
const JOURNAL_Z = 85;
const SOURCE_LABEL = 'мост «Касаний»';

// Выбранная группа — удобство зрителя (стена ОП утром включается на своей группе), а не общая
// настройка: живёт в браузере и молча отступает на «Все», если хранилище недоступно.
const GROUP_STORAGE_KEY = 'otp.opWallboard.group';

const readStoredGroup = () => {
    try {
        return window.localStorage.getItem(GROUP_STORAGE_KEY) || OP_GROUP_ALL;
    } catch (storageError) {
        return OP_GROUP_ALL;
    }
};

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

/*
 * По часам: столбики принятых, потерянных и исходящих, без библиотек — на стене нужны пропорции, а
 * не подписи к каждому делению. Ось часов одна на отдел и группы (`range` из полного снимка) и
 * доходит до текущего часа: при переключении группы столбики не прыгают по ширине и месту, а у
 * группы с двумя рабочими часами не разрастаются в две плиты на всю карточку. Пустые часы до начала
 * дня не рисуются, чтобы утро не читалось как провал.
 */
const HourlyBars = ({ snapshot, range, scale = 1 }) => {
    const hourly = snapshot?.hourly || [];
    if (!range || !hourly.some((h) => h.arrived || h.outgoing)) {
        return <div className="text-[13px] text-slate-500">Звонков за сегодня ещё не было.</div>;
    }
    const shown = hourly.slice(range.first, range.last + 1);
    const peak = Math.max(1, ...shown.map((h) => h.arrived + h.outgoing));
    const height = 132 * scale;
    const px = (n) => Math.round((n / peak) * height);
    return (
        <div>
            <div className="flex items-end gap-1.5 border-b border-slate-200/80" style={{ height: `${height}px` }}>
                {shown.map((h) => (
                    <div key={h.hour} className="flex h-full min-w-0 flex-1 items-end justify-center"
                         title={`${h.hour}:00 — входящих ${h.arrived} (принято ${h.answered}, потеряно ${h.missed}), исходящих ${h.outgoing}`}>
                        <div className="flex w-full max-w-[56px] flex-col justify-end overflow-hidden rounded-t-[6px]">
                            <div className="w-full bg-slate-300" style={{ height: `${px(h.outgoing)}px` }} />
                            <div className="w-full bg-amber-400" style={{ height: `${px(h.missed)}px` }} />
                            <div className="w-full bg-green-500" style={{ height: `${px(h.answered)}px` }} />
                        </div>
                    </div>
                ))}
            </div>
            <div className="mt-1.5 flex gap-1.5">
                {shown.map((h) => (
                    <div key={h.hour} className="min-w-0 flex-1 text-center tabular-nums text-slate-400" style={{ fontSize: `${11 * scale}px` }}>
                        {String(h.hour).padStart(2, '0')}
                    </div>
                ))}
            </div>
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

/*
 * Молчащий телефон: событий за 16 часов нет и звонков сегодня нет. Обычно это люди не на смене —
 * на стене ОП их два десятка, и список вырастал вдвое серыми строками. Они не прячутся, а
 * сворачиваются в одну строку с числом: развернуть можно одним нажатием.
 */
const isIdleRow = (row) => (
    row.in_roster !== false && row.status_key === 'unknown' && !row.answered && !row.missed && !row.outgoing
);

/*
 * Поимённый список: статус телефона, время входа и счётчики дня. Порядок — по разряду статуса.
 * Столбец «Группа» есть только в «Все»: внутри выбранной группы он повторял бы фильтр в каждой
 * строке. Строка с журналом нажимается целиком, как строка списка в macOS: подсветка при наведении,
 * шеврон справа, ФИО — кнопка для клавиатуры. Журнал есть только у тех, от чьего телефона были
 * статусы: у «Нет событий» он всегда пуст. Нули — светло-серые, чтобы глаз цеплялся за ненулевое.
 */
const OperatorsTable = ({ snapshot, scale = 1, showGroup = false, selectedId = null, onOpenJournal = null }) => {
    const [showIdle, setShowIdle] = useState(false);
    const rows = snapshot?.operators || [];
    if (!rows.length) {
        return (
            <div className="text-[13px] text-slate-500">
                {showGroup || !snapshot?.groups?.length
                    ? 'В составе отдела нет сотрудников с внутренним номером.'
                    : 'В группе нет сотрудников.'}
            </div>
        );
    }
    const idle = rows.filter(isIdleRow);
    const shown = showIdle ? [...rows.filter((row) => !isIdleRow(row)), ...idle] : rows.filter((row) => !isIdleRow(row));
    const head = 'px-3 pb-2 text-[12px] font-medium text-slate-400';
    const cell = 'px-3 py-2.5 text-right tabular-nums';
    const count = (value, tone = 'text-slate-700') => (
        <td className={`${cell} ${value ? tone : 'text-slate-300'}`}>{formatCount(value)}</td>
    );
    const toggle = idle.length ? (
        <button
            type="button"
            onClick={() => setShowIdle((value) => !value)}
            className="mt-2 inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-[12.5px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 active:scale-[0.98]"
        >
            <FaIcon className={`fas ${showIdle ? 'fa-chevron-up' : 'fa-chevron-down'} text-[10px]`} aria-hidden="true" />
            {showIdle ? 'Скрыть' : 'Показать'} без событий телефона
            <span className="tabular-nums text-slate-400">{idle.length}</span>
        </button>
    ) : null;
    if (!shown.length) {
        return (
            <div>
                <div className="text-[13px] text-slate-500">Сегодня телефоны группы молчат.</div>
                {toggle}
            </div>
        );
    }
    return (
        <div>
            <div className="-mx-3 overflow-x-auto">
                <table className="w-full text-left" style={{ fontSize: `${13 * scale}px` }}>
                    <thead>
                        <tr className="border-b border-slate-200/80">
                            <th className={head}>Сотрудник</th>
                            <th className={head}>Внутренний</th>
                            {showGroup ? <th className={head}>Группа</th> : null}
                            <th className={head}>Статус</th>
                            <th className={head}>Время входа</th>
                            <th className={`${head} text-right`}>Принято</th>
                            <th className={`${head} text-right`}>Потеряно</th>
                            <th className={`${head} text-right`}>Исходящих</th>
                            <th className={`${head} text-right`}>Разговор</th>
                            <th className="w-8 pb-2" aria-hidden="true" />
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {shown.map((row) => {
                            const chip = opStatusChip(row);
                            const entry = opEntryTime(row.entry_at, snapshot?.day);
                            const canOpen = row.id != null && row.status_key !== 'unknown' && Boolean(onOpenJournal);
                            const selected = canOpen && row.id === selectedId;
                            return (
                                <tr
                                    key={`${row.id ?? 'ext'}-${row.ext}`}
                                    onClick={canOpen ? () => onOpenJournal(row) : undefined}
                                    className={`transition-colors ${canOpen ? 'cursor-pointer' : ''} ${
                                        selected ? 'bg-blue-50' : canOpen ? 'hover:bg-slate-50' : ''} ${
                                        chip.muted ? 'text-slate-400' : 'text-slate-700'}`}
                                >
                                    <td className="px-3 py-2.5">
                                        {canOpen ? (
                                            <button
                                                type="button"
                                                onClick={(event) => { event.stopPropagation(); onOpenJournal(row); }}
                                                title="Журнал статусов"
                                                className={`rounded text-left font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                                    selected ? 'text-blue-700' : 'text-slate-900'}`}
                                            >
                                                {row.name}
                                            </button>
                                        ) : <span className="font-medium">{row.name}</span>}
                                    </td>
                                    <td className="px-3 py-2.5 tabular-nums text-slate-500">{row.ext || '—'}</td>
                                    {showGroup ? <td className="px-3 py-2.5 text-slate-500">{row.group_label || '—'}</td> : null}
                                    <td className="px-3 py-2.5">
                                        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[0.92em] font-medium ${chip.className}`}>
                                            {chip.label}
                                            {row.status_seconds ? <span className="font-normal tabular-nums opacity-70">{formatSeconds(row.status_seconds)}</span> : null}
                                        </span>
                                    </td>
                                    <td className={`px-3 py-2.5 tabular-nums ${entry.time === '—' ? 'text-slate-300' : ''}`}>
                                        {entry.time}
                                        {entry.previousDay ? <span className="ml-1.5 text-slate-400">вчера</span> : null}
                                    </td>
                                    {count(row.answered)}
                                    {count(row.missed, 'text-amber-600')}
                                    <td className={`${cell} ${row.outgoing ? 'text-slate-700' : 'text-slate-300'}`}>
                                        {formatCount(row.outgoing)}
                                        {row.outgoing ? <span className="text-slate-400"> → {formatInt(row.outgoing_answered)}</span> : null}
                                    </td>
                                    <td className={`${cell} ${row.talk_seconds ? 'text-slate-700' : 'text-slate-300'}`}>{formatSeconds(row.talk_seconds)}</td>
                                    <td className="py-2.5 pr-3 text-right text-[11px] text-slate-300">
                                        {canOpen ? <FaIcon className="fas fa-chevron-right" aria-hidden="true" /> : null}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            {toggle}
        </div>
    );
};

/** Тело табло — одно и то же встроенным и на весь экран, различается только масштабом. */
function OpWallboardBody({ snapshot, hourRange = null, scale = 1, showGroup = false, selectedId = null, onOpenJournal = null }) {
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
            <Section icon="fa-clock" title="По часам" right={<HourlyLegend />}>
                <HourlyBars snapshot={snapshot} range={hourRange} scale={scale} />
            </Section>
            <Section icon="fa-users" title="Сотрудники" right={<span className="text-[12px] text-slate-500">статусы — iCORE Phone, счётчики — касания за день</span>}>
                <OperatorsTable snapshot={snapshot} scale={scale} showGroup={showGroup}
                                selectedId={selectedId} onOpenJournal={onOpenJournal} />
            </Section>
        </div>
    );
}

const clockLabel = opClockLabel;

const BROADCAST_HINT = (
    <>
        Отклонением считаем то же, что подсвечено на табло, но за прошедший час, а не
        накопительно за день: AR вне коридора нормы, SL ниже 80 % или мост «Касаний» замолчал и
        цифры на табло замерли. Проценты считаются, когда за час накопилось хотя бы 10 входящих —
        единичные звонки поводом не считаются.
    </>
);

/** Направление «Линия»: звонки на FreePBX и статусы iCORE Phone. */
function OpLineWallboard({
    apiBaseUrl, withAccessTokenHeader, showToast, canManageBroadcast = false, widgetOpen, onToggleWidget,
    directionSwitch = null,
}) {
    const { snapshot, error, loading, refresh } = useOpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);
    const [groupKey, setGroupKey] = useState(readStoredGroup);
    const [journalOperatorId, setJournalOperatorId] = useState(null);

    const chooseGroup = useCallback((value) => {
        setGroupKey(value);
        try {
            window.localStorage.setItem(GROUP_STORAGE_KEY, value);
        } catch (storageError) {
            // Приватное окно или запрет хранилища: фильтр работает, просто не запомнится.
        }
    }, []);

    // Группа, которой больше нет в снимке, читается как «Все» — без эффекта и без мигания.
    const group = useMemo(() => opSelectedGroup(snapshot, groupKey), [groupKey, snapshot]);
    const view = useMemo(() => opGroupView(snapshot, group), [group, snapshot]);
    const groupOptions = useMemo(() => opGroupOptions(snapshot), [snapshot]);
    const hourRange = useMemo(() => opHourRange(snapshot), [snapshot]);
    const showGroupColumn = !group && groupOptions.length > 1;

    // Строку журнала берём из полного снимка, а не из отфильтрованного: смена группы на экране
    // не закрывает журнал человека, которого сейчас смотрят.
    const journalRow = useMemo(
        () => (snapshot?.operators || []).find((row) => row.id != null && row.id === journalOperatorId) || null,
        [journalOperatorId, snapshot],
    );
    const journalRefreshKey = journalRow ? `${snapshot?.day}|${journalRow.status_key}|${journalRow.status_at}` : '';
    const openJournal = useCallback((row) => setJournalOperatorId(row.id), []);
    const closeJournal = useCallback(() => setJournalOperatorId(null), []);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, SOURCE_LABEL), [error, snapshot]);
    const freshness = useMemo(() => opFreshnessNotice(snapshot), [snapshot]);
    const notice = staleNotice || freshness;
    // Прочерк в SL и ожидании без объяснения читался бы как поломка табло: причина — в данных
    // станции, и сказать об этом надо один раз, серым, рядом с заголовком.
    const dataGap = useMemo(() => opDataGapNotice(snapshot), [snapshot]);

    const groupSwitch = groupOptions.length > 1 ? (
        <IosSegmented
            value={group ? String(group.id) : OP_GROUP_ALL}
            options={groupOptions}
            onChange={chooseGroup}
            ariaLabel="Группа"
        />
    ) : null;

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
                {dataGap ? (
                    <div className="mt-1 inline-flex items-center gap-2 rounded-full bg-slate-100 px-2.5 py-0.5 text-[12px] text-slate-600">
                        <FaIcon className="fas fa-circle-info"></FaIcon>
                        {dataGap}
                    </div>
                ) : null}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
                {directionSwitch}
                {/* Фильтр — в одной строке с действиями, как сегменты в тулбаре macOS; на узком
                    экране строка переносится сама. */}
                {groupSwitch ? <div className="mr-2">{groupSwitch}</div> : null}
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
                {/* Виджет — окно поверх других программ, как у СЗоВ: остаётся открытым после
                    ухода из раздела, набор показателей выбирается в самом окне. */}
                {onToggleWidget ? <WidgetButton direction="op" widgetOpen={widgetOpen}
                                                onToggleWidget={onToggleWidget} /> : null}
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
            <OpWallboardBody snapshot={view} hourRange={hourRange} scale={1} showGroup={showGroupColumn}
                             selectedId={journalRow?.id ?? null} onOpenJournal={openJournal} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon="fa-tachometer-alt"
                    title={group ? `Табло ОП · ${group.label}` : 'Табло ОП'}
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    actions={groupSwitch}
                    // Esc при открытом журнале закрывает журнал, а не всю стену разом.
                    closeOnEscape={!journalRow}
                    onClose={() => setFullscreen(false)}
                >
                    <OpWallboardBody snapshot={view} hourRange={hourRange} scale={1.5} showGroup={showGroupColumn}
                                     selectedId={journalRow?.id ?? null} onOpenJournal={openJournal} />
                </FullscreenSheet>,
                document.body,
            ) : null}
            <OpStatusJournalPanel
                operator={journalRow}
                refreshKey={journalRefreshKey}
                apiBaseUrl={apiBaseUrl}
                withAccessTokenHeader={withAccessTokenHeader}
                onClose={closeJournal}
                z={fullscreen ? FULLSCREEN_Z + 10 : JOURNAL_Z}
            />
        </div>
    );
}

// ── Направление «Чат»: чаты верификаторов в Wazzup (задача #367) ─────────────────────────────────

// «… не отвечает» у замершего снимка — про нашу базу: переписка уже лежит в ней, и снимок
// замирает только при её сбое. Молчание самого Wazzup — отдельное предупреждение (stream.silent).
const OP_CHAT_SOURCE_LABEL = 'База iCORE';

// Потолок выгрузки — тот же, что на сервере (OP_CHAT_EXPORT_MAX_DAYS): просить больше — получить 400.
// Месяц, а не неделя, как у СЗоВ: переписка уже лежит в нашей базе, вендора никто не качает.
const OP_CHAT_EXPORT_MAX_DAYS = 31;

const opChatExportPresets = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: '7 дней', range: () => ({ from: isoDate(new Date(Date.now() - 6 * 864e5)), to: isoDate(new Date()) }) },
    { label: '30 дней', range: () => ({ from: isoDate(new Date(Date.now() - 29 * 864e5)), to: isoDate(new Date()) }) },
];

const OP_CHAT_EXPORT_HINTS = {
    single: 'Сводка, по часам и по каждому верификатору',
    multi: 'Считается из переписки в базе — несколько секунд',
};

/* Имя собирает фронт: Content-Disposition через CORS сюда не доходит. */
const opChatExportFileName = (snapshot, from, to) => {
    const day = (value) => String(value || '').replace(/-/g, '');
    if (from && to && from !== to) return `op_wallboard_chat_${day(from)}_${day(to)}.xlsx`;
    return `op_wallboard_chat_${day(from || to || snapshot?.day) || 'now'}.xlsx`;
};

const OP_CHAT_BROADCAST_HINT = (
    <>
        Отклонением считаем то же, что подсвечено на табло: первый ответ или ответ внутри чата за
        сутки дольше нормы, либо Wazzup перестал присылать сообщения и цифры замерли. Время ответа
        судим, когда за сутки набралось хотя бы 5 чатов — ночные единицы поводом не считаются.
    </>
);

/** Направление «Чат»: переписка верификаторов в Wazzup — чаты в работе, время ответа, люди. */
function OpChatBoard({
    apiBaseUrl, withAccessTokenHeader, showToast, canManageBroadcast = false, widgetOpen, onToggleWidget,
    directionSwitch = null,
}) {
    const { snapshot, error, loading, refresh } = useOpChatWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);
    const notice = useMemo(
        () => wallboardStaleNotice(snapshot, error, OP_CHAT_SOURCE_LABEL) || opChatFreshnessNotice(snapshot),
        [error, snapshot],
    );
    const clock = opChatClockLabel(snapshot);

    const header = (
        <div className={`${iosCard} flex flex-wrap items-center justify-between gap-3 p-4`}>
            <div>
                <div className="flex items-center gap-2 text-[17px] font-semibold text-slate-900">
                    <FaIcon className="fas fa-tachometer-alt text-blue-600"></FaIcon>
                    Табло ОП
                </div>
                <div className="text-[13px] text-slate-500">
                    Чаты верификаторов · Wazzup{clock ? ` · ${clock}` : ''}
                </div>
                {notice ? (
                    <div className="mt-1 inline-flex items-center gap-2 rounded-full bg-amber-50 px-2.5 py-0.5 text-[12px] text-amber-800 ring-1 ring-amber-200">
                        <FaIcon className="fas fa-triangle-exclamation"></FaIcon>
                        {notice}
                    </div>
                ) : null}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
                {directionSwitch}
                {canManageBroadcast ? (
                    <BroadcastControls
                        direction="op_chat"
                        directionLabel="ОП · Чат"
                        deviationHint={OP_CHAT_BROADCAST_HINT}
                        apiBaseUrl={apiBaseUrl}
                        withAccessTokenHeader={withAccessTokenHeader}
                        showToast={showToast}
                    />
                ) : null}
                {/* Пока снимка нет, выгружать нечего: кнопка появляется вместе с цифрами. */}
                {snapshot ? (
                    <ChatExportControls
                        apiBaseUrl={apiBaseUrl}
                        withAccessTokenHeader={withAccessTokenHeader}
                        showToast={showToast}
                        snapshot={snapshot}
                        exportPath="/api/op_wallboard/chat_export"
                        fileName={opChatExportFileName}
                        maxDays={OP_CHAT_EXPORT_MAX_DAYS}
                        presets={opChatExportPresets}
                        hints={OP_CHAT_EXPORT_HINTS}
                    />
                ) : null}
                <button type="button" className={`${iosBtnGhost} disabled:opacity-40`} disabled={loading} onClick={() => refresh()}>
                    <FaIcon className={`fas fa-rotate ${loading ? 'animate-spin' : ''}`}></FaIcon>
                    Обновить
                </button>
                {onToggleWidget ? <WidgetButton direction="op_chat" widgetOpen={widgetOpen}
                                                onToggleWidget={onToggleWidget} /> : null}
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
                    {loading ? 'Загружаем чаты верификаторов…' : (error || 'Данные недоступны')}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <OpChatWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon="fa-comments"
                    title="Табло ОП · чаты"
                    subtitle={`${clock ? `${clock} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <OpChatWallboardBody snapshot={snapshot} scale={1.35} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
}

// Выбор направления — удобство зрителя (стена верификаторов утром включается на «Чате»), поэтому
// живёт в браузере, с id пользователя в ключе, и молча отступает на «Линию», если хранилище недоступно.
const directionStorageKey = (userId) => `otp:op-wallboard-direction${userId ? `:${userId}` : ''}`;

const readStoredDirection = (userId) => {
    try {
        const stored = window.localStorage.getItem(directionStorageKey(userId));
        return OP_WALLBOARD_VIEWS.some((item) => item.key === stored) ? stored : OP_WALLBOARD_VIEWS[0].key;
    } catch (storageError) {
        return OP_WALLBOARD_VIEWS[0].key;
    }
};

export default function OpWallboardView(props) {
    const userId = props.user?.id;
    const [direction, setDirection] = useState(() => readStoredDirection(userId));

    const changeDirection = useCallback((next) => {
        setDirection(next);
        try {
            window.localStorage.setItem(directionStorageKey(userId), next);
        } catch (storageError) {
            // Приватное окно или запрет хранилища: переключение работает, просто не запомнится.
        }
    }, [userId]);

    const directionSwitch = (
        <SegmentedSwitch value={direction} options={OP_WALLBOARD_VIEWS} onChange={changeDirection} />
    );
    // Монтируется только выбранное направление: у каждого свой опрос, и скрытое молчит.
    if (direction === 'chat') {
        return <OpChatBoard {...props} directionSwitch={directionSwitch} />;
    }
    return <OpLineWallboard {...props} directionSwitch={directionSwitch} />;
}
