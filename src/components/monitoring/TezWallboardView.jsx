import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnPrimary } from '../ui/ios';
import { IosDateRangeCalendar, isoDate, rangeLabel } from '../ui/DateRangePicker';
import { canOpenWallboardWidget, formatClock, wallboardStaleNotice } from './szovWallboardShared';
import { SegmentedSwitch } from './SzovWallboardTiles';
import {
    TEZ_EXPORT_MAX_DAYS,
    TEZ_WALLBOARD_DIRECTIONS,
    TEZ_WALLBOARD_DIRECTION_LIST,
    TEZ_WALLBOARD_EXPORT_PATH,
    useTezOpWallboardSnapshot,
    useTezTpWallboardSnapshot,
} from './tezWallboardShared';
import TezTpWallboardBody from './TezTpWallboard';
import TezOpWallboardBody from './TezOpWallboard';

/*
 * «Табло Тез КЦ» — онлайн-мониторинг двух направлений отдела (задача #292): ТП (техподдержка,
 * у неё есть очередь) и ОП (отдел продаж, очереди нет). Источник у обоих один — кабинет
 * my.binotel.kz: у Тез своя телефония, не Oktell, поэтому и раздел свой, а не ветка в «Табло
 * СЗоВ» (там на каждую секцию и плитку стоит по стражу, и общий экран сломал бы чужой).
 *
 * Экран рассчитан на вывод на стену, поэтому:
 *   - обновляется сам, без перезагрузки страницы (опрос раз в 20 с, снимок общий на всех зрителей);
 *   - цветом помечаем только то, что несёт смысл: AR выше нормы, SL ниже порога и статусы людей;
 *   - когда кабинет молчит, цифры на стене НЕ гасим — вешаем янтарный чип с возрастом данных.
 *
 * Кнопок «Отбивка» и «Перерывы не по графику» здесь нет: и то и другое у Тез не заведено.
 * Кнопки «Виджет» тоже нет — окно «поверх окон» у документа одно, и оно сейчас намертво
 * привязано к направлениям СЗоВ; отдать его Тез — отдельная работа.
 */

/*
 * Своё время кабинет отдаёт только вместе с активным звонком: когда звонков нет — а ночью и
 * в тихую минуту их нет, — снимок штампуется нашими часами. Подписывать это «данные Binotel
 * на …» значит называть чужой источник; расхождение секундное, но подпись должна быть правдой.
 */
const clockLabel = (snapshot) => {
    if (!snapshot?.binotel_now) return null;
    const own = snapshot.binotel_now_source === 'cabinet';
    return `${own ? 'данные Binotel на' : 'снимок на'} ${formatClock(snapshot.binotel_now)}`;
};

const FULLSCREEN_Z = 150;

const DIRECTIONS = TEZ_WALLBOARD_DIRECTION_LIST;

/*
 * Кнопка виджета. Окно «поверх других» у документа ОДНО на всё приложение, поэтому кнопка
 * зажата только у того направления, чей виджет открыт сейчас, а нажатие на соседнем окно
 * переоткрывает. Это же значит, что виджет Тез и виджет СЗоВ не могут висеть вдвоём: состояние
 * у них общее, и открытие одного закрывает другой — так же, как это работает между «Линией» и
 * «Чатом» внутри СЗоВ.
 */
const TezWidgetButton = ({ direction, widgetOpen, onToggleWidget }) => {
    const supported = useMemo(() => canOpenWallboardWidget(), []);
    const isOpen = widgetOpen === direction;
    return (
        <button
            type="button"
            className={`${iosBtnGhost} disabled:opacity-40 ${isOpen ? 'bg-slate-100 text-slate-900' : ''}`}
            disabled={!supported}
            title={supported
                ? 'Окно поверх других программ: выберите в нём, что мониторить'
                : 'Окно поверх других программ умеют Chrome и Edge — в этом браузере недоступно'}
            onClick={() => onToggleWidget(isOpen ? null : direction)}
        >
            <FaIcon className="fas fa-picture-in-picture"></FaIcon>
            {isOpen ? 'Виджет открыт' : 'Виджет'}
        </button>
    );
};

/*
 * Период выгрузки выбирается в самой кнопке — тем же пикером, что в «Чатах» и «Посылках».
 * Отдельного поля периода в шапке нет намеренно: табло смотрят ради «сейчас», а выгрузка нужна
 * изредка, и постоянный календарь в шапке был бы шумом на стене.
 */
const exportPresets = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: '7 дней', range: () => ({ from: isoDate(new Date(Date.now() - 6 * 864e5)), to: isoDate(new Date()) }) },
    { label: '14 дней', range: () => ({ from: isoDate(new Date(Date.now() - 13 * 864e5)), to: isoDate(new Date()) }) },
];

const rangeDays = (from, to) => {
    if (!from || !to) return 1;
    return Math.round((new Date(to) - new Date(from)) / 864e5) + 1;
};

const TezExportControls = ({ apiBaseUrl, withAccessTokenHeader, showToast, direction }) => {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [range, setRange] = useState(() => ({ from: isoDate(new Date()), to: isoDate(new Date()) }));
    const ref = useRef(null);
    // showToast приходит новой функцией на каждый рендер родителя, а он перерисовывается раз в
    // 20 с по опросу табло: держим её в ref, чтобы не тянуть в зависимости эффекта.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    // Клик мимо и Esc закрывают панель — как у чипа-пикера в остальных разделах.
    useEffect(() => {
        if (!open) return undefined;
        const onDown = (event) => {
            if (ref.current && !ref.current.contains(event.target)) setOpen(false);
        };
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    const days = rangeDays(range.from, range.to);
    const tooLong = days > TEZ_EXPORT_MAX_DAYS;

    const download = useCallback(async (from, to) => {
        setOpen(false);
        setBusy(true);
        try {
            const build = withAccessTokenHeader;
            const query = new URLSearchParams({ direction: direction === 'tez_op' ? 'op' : 'tp' });
            if (from) query.set('date_from', from);
            if (to) query.set('date_to', to);
            const response = await fetch(
                `${apiBaseUrl}${TEZ_WALLBOARD_EXPORT_PATH}?${query.toString()}`,
                { credentials: 'include', headers: build ? build() : {} },
            );
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload?.error || `Сервер ответил ${response.status}`);
            }
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `tez_wallboard_${direction === 'tez_op' ? 'op' : 'tp'}_${(from || '').replace(/-/g, '')}${
                to && to !== from ? `-${to.replace(/-/g, '')}` : ''}.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            toastRef.current?.(`Не удалось выгрузить показатели: ${error?.message || error}`, 'error');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, withAccessTokenHeader, direction]);

    // Сколько ждать, говорим ДО нажатия: каждый день периода — отдельный поход в кабинет.
    const hint = tooLong
        ? `Максимум ${TEZ_EXPORT_MAX_DAYS} суток за раз — выберите период короче`
        : (days > 1
            ? `${days} суток — кабинет опрашивается по дню, это займёт до минуты`
            : 'Поимённо по дням и итоги дня');

    return (
        <div ref={ref} className="relative">
            <button type="button" className={`${iosBtnGhost} ${open ? 'bg-slate-100 text-slate-900' : ''}`}
                    onClick={() => setOpen((value) => !value)} disabled={busy}
                    title="Показатели направления в Excel за выбранный период">
                <FaIcon className={`fas ${busy ? 'fa-spinner fa-spin' : 'fa-file-excel'}`}></FaIcon>
                {busy ? 'Готовим…' : 'Выгрузить'}
            </button>
            {open ? (
                /* Панель прижата к правому краю кнопки: кнопка стоит у правого края шапки, и
                   раскрытие влево увело бы календарь за экран. */
                <div className="absolute right-0 top-full z-[60] mt-2">
                    <IosDateRangeCalendar
                        from={range.from}
                        to={range.to}
                        max={isoDate(new Date())}
                        presets={exportPresets}
                        onChange={(next) => setRange({ from: next.from || next.to, to: next.to || next.from })}
                        footer={(
                            <div className="mt-2.5 border-t border-slate-100 pt-2.5">
                                <button type="button" className={`${iosBtnPrimary} w-full`} disabled={tooLong}
                                        onClick={() => download(range.from, range.to)}>
                                    <FaIcon className="fas fa-file-excel"></FaIcon>
                                    Подтвердить
                                </button>
                                <p className={`mt-1.5 text-center text-[11px] ${
                                    tooLong ? 'text-rose-500' : 'text-slate-400'}`}>
                                    {rangeLabel(range.from, range.to)} · {hint}
                                </p>
                            </div>
                        )}
                    />
                </div>
            ) : null}
        </div>
    );
};

/*
 * Ключ хранения — свой. `otp:szov-wallboard-direction` трогать нельзя: по нему лежит выбор
 * направления чужого раздела, и чужое 'tez_op' в нём означало бы молча открытую «Линию».
 */
const directionStorageKey = (userId) => `otp:tez-wallboard-direction${userId ? `:${userId}` : ''}`;

const readStoredDirection = (userId) => {
    if (typeof window === 'undefined') return DIRECTIONS[0].key;
    try {
        const stored = window.localStorage.getItem(directionStorageKey(userId));
        return DIRECTIONS.some((item) => item.key === stored) ? stored : DIRECTIONS[0].key;
    } catch (error) {
        return DIRECTIONS[0].key;
    }
};

const writeStoredDirection = (userId, direction) => {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(directionStorageKey(userId), direction);
    } catch (error) {
        // Выбор направления — предпочтение браузера: не сохранилось, откроется ТП.
    }
};

/*
 * Шапка одна на оба направления: заголовок, переключатель, пометка о замерших данных, кнопки.
 * Различается только подпись под заголовком — она называет направление и момент данных.
 */
const TezWallboardHeader = ({
    subtitle, direction, onDirectionChange, staleNotice, loading, onRefresh, onFullscreen,
    apiBaseUrl, withAccessTokenHeader, showToast, widgetOpen, onToggleWidget,
}) => (
    <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
            <h1 className="text-[26px] font-semibold text-slate-900">Табло Тез КЦ</h1>
            <p className="mt-1 text-[14px] text-slate-500">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
            <SegmentedSwitch value={direction} options={DIRECTIONS} onChange={onDirectionChange} />
            {staleNotice ? (
                <span className="rounded-full bg-amber-50 px-3 py-1 text-[12px] font-medium text-amber-700 ring-1 ring-amber-200">
                    {staleNotice}
                </span>
            ) : null}
            <button type="button" className={iosBtnGhost} onClick={() => onRefresh()} disabled={loading}>
                <FaIcon className="fas fa-rotate"></FaIcon>
                Обновить
            </button>
            <TezExportControls
                apiBaseUrl={apiBaseUrl}
                withAccessTokenHeader={withAccessTokenHeader}
                showToast={showToast}
                direction={direction}
            />
            {/* Виджет — отдельное окно поверх других окон, и оно остаётся открытым после ухода
                из раздела: он для того и нужен, чтобы следить за отделом, занимаясь другим.
                Набор показателей выбирается в самом окне, у каждого направления свой. */}
            {onToggleWidget ? <TezWidgetButton direction={direction} widgetOpen={widgetOpen}
                                               onToggleWidget={onToggleWidget} /> : null}
            <button type="button" className={iosBtnGhost} onClick={onFullscreen}>
                <FaIcon className="fas fa-expand"></FaIcon>
                На весь экран
            </button>
        </div>
    </div>
);

/** Пустые состояния (грузим / кабинет молчит) — одни на оба направления. */
const TezWallboardPlaceholder = ({ header, message, tone = 'muted' }) => (
    <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
        {header}
        <div className={`${iosCard} p-6 text-[13px] ${tone === 'error' ? 'text-rose-600' : 'text-slate-500'}`}>
            {message}
        </div>
    </div>
);

/** Направление ТП: очередь техподдержки в кабинете Binotel. */
const TpWallboard = ({ apiBaseUrl, withAccessTokenHeader, showToast, direction, onDirectionChange,
                        widgetOpen, onToggleWidget }) => {
    const config = TEZ_WALLBOARD_DIRECTIONS.tez_tp;
    const { snapshot, error, loading, refresh } = useTezTpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, config.source), [config.source, error, snapshot]);

    const header = (
        <TezWallboardHeader
            subtitle={`Техподдержка в реальном времени${
                clockLabel(snapshot) ? ` · ${clockLabel(snapshot)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            showToast={showToast}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
        />
    );

    if (!snapshot) {
        return (
            <TezWallboardPlaceholder
                header={header}
                message={loading ? 'Загружаем данные Binotel…' : (error || 'Данные недоступны')}
                tone={loading ? 'muted' : 'error'}
            />
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <TezTpWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon={config.icon}
                    title={config.title}
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <TezTpWallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
};

/** Направление ОП: продавцы того же кабинета, но без очереди. */
const OpWallboard = ({ apiBaseUrl, withAccessTokenHeader, showToast, direction, onDirectionChange,
                        widgetOpen, onToggleWidget }) => {
    const config = TEZ_WALLBOARD_DIRECTIONS.tez_op;
    const { snapshot, error, loading, refresh } = useTezOpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, config.source), [config.source, error, snapshot]);

    const header = (
        <TezWallboardHeader
            subtitle={`Отдел продаж в реальном времени${
                clockLabel(snapshot) ? ` · ${clockLabel(snapshot)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            showToast={showToast}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
        />
    );

    if (!snapshot) {
        return (
            <TezWallboardPlaceholder
                header={header}
                message={loading ? 'Загружаем данные Binotel…' : (error || 'Данные недоступны')}
                tone={loading ? 'muted' : 'error'}
            />
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <TezOpWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon={config.icon}
                    title={config.title}
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <TezOpWallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
};

export default function TezWallboardView(props) {
    const { user, apiBaseUrl, withAccessTokenHeader, showToast, widgetOpen, onToggleWidget } = props;
    const userId = user?.id;
    // Выбор направления запоминаем: тому, кто следит за продажами, незачем каждый раз
    // переключаться с ТП — раздел открывается на том направлении, где его закрыли.
    const [direction, setDirection] = useState(() => readStoredDirection(userId));

    const changeDirection = useCallback((next) => {
        setDirection(next);
        writeStoredDirection(userId, next);
    }, [userId]);

    /*
     * Рисуем ТОЛЬКО выбранное направление, каждое своим компонентом со своим снимком: иначе
     * закрытое табло продолжало бы ходить в кабинет впустую. Кабинет у нас один на два
     * направления и одна учётная запись на всю компанию — лишние обходы там дорогие.
     */
    if (direction === 'tez_op') {
        return (
            <OpWallboard
                apiBaseUrl={apiBaseUrl}
                withAccessTokenHeader={withAccessTokenHeader}
                showToast={showToast}
                direction={direction}
                onDirectionChange={changeDirection}
                widgetOpen={widgetOpen}
                onToggleWidget={onToggleWidget}
            />
        );
    }
    return (
        <TpWallboard
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            showToast={showToast}
            direction={direction}
            onDirectionChange={changeDirection}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
        />
    );
}
