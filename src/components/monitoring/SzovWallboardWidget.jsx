import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT } from '../ui/ios';
import {
    WALLBOARD_TONE_TEXT,
    formatClock,
    formatDuration,
    readWallboardMetric,
    sanitizeWidgetMetrics,
    wallboardDirection,
    wallboardStaleNotice,
} from './szovWallboardShared';

/*
 * Виджет «Табло СЗоВ» — то же табло отдельным окном поверх других окон (картинка в картинке)
 * и с личным набором показателей.
 *
 * Чем отличается от раздела: раздел — экран на стену, там весь набор и крупные цифры. Виджет
 * смотрят краем глаза, пока работают в другом разделе или вовсе в другой программе, поэтому:
 *   - живёт на уровне приложения, а не внутри раздела: уйдёшь в «Задачи» — окно останется;
 *   - показывает ТОЛЬКО отмеченные показатели, а набор правится в самом окне: сайта из PiP-окна
 *     не видно, и отправлять человека настраивать виджет в раздел — значит его закрыть;
 *   - цифры и цвета берёт из общего каталога, а снимок — из общего опроса, так что разойтись
 *     с разделом в цифрах не может.
 *
 * Виджет существует ТОЛЬКО как окно поверх других (решение владельца): встроенной в страницу
 * карточки нет — в разделе для этого есть сам раздел. Document Picture-in-Picture умеют
 * Chrome и Edge; где его нет, кнопка «Виджет» в разделе выключена и объясняет причину.
 */

const PIP_WIDTH = 460;

const TILE_MIN_WIDTH = 132;
const TILE_GAP = 10;

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

// Ключ хранения включает направление: наборы у линии и у чатов разные, и общий ключ означал бы,
// что открытие второго виджета молча стирает набор первого.
const storageKey = (userId, direction) => (
    `otp:szov-wallboard-widget:${direction}${userId ? `:${userId}` : ''}`
);

const readStoredMetrics = (userId, config) => {
    if (typeof window === 'undefined') return config.defaultMetrics;
    try {
        const raw = window.localStorage.getItem(storageKey(userId, config.key));
        const parsed = raw ? JSON.parse(raw) : null;
        const stored = sanitizeWidgetMetrics(parsed?.metrics, config.metricMap);
        return stored.length > 0 ? stored : config.defaultMetrics;
    } catch (error) {
        return config.defaultMetrics;
    }
};

const writeStoredMetrics = (userId, config, metrics) => {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(storageKey(userId, config.key), JSON.stringify({ metrics }));
    } catch (error) {
        // Набор показателей — предпочтение браузера, без него виджет просто откроется по умолчанию.
    }
};

/*
 * Стили PiP-окна. Это отдельный документ: без переноса таблиц стилей там будет голый HTML.
 * href клонированной ссылки подставляем уже разрешённым — базовый адрес у PiP-окна свой,
 * и относительный путь до бандла в нём не нашёлся бы.
 */
const cloneDocumentStyles = (targetWindow) => {
    Array.from(document.querySelectorAll('link[rel="stylesheet"], style')).forEach((node) => {
        const clone = node.cloneNode(true);
        if (clone.tagName === 'LINK' && node.href) clone.href = node.href;
        targetWindow.document.head.appendChild(clone);
    });
};

/** Сколько плиток влезает в ряд: считаем по ширине окна виджета, а не по ширине экрана. */
const columnsFor = (width, tileCount) => {
    if (!Number.isFinite(width) || width <= 0) return Math.min(2, Math.max(1, tileCount));
    const fits = Math.floor((width + TILE_GAP) / (TILE_MIN_WIDTH + TILE_GAP));
    // Больше колонок, чем выбрано плиток, не заводим — иначе в широком окне последний ряд
    // остаётся с одинокой плиткой, а остальные растянуты на пол-экрана.
    return clamp(fits, 1, Math.max(1, Math.min(6, tileCount)));
};

/*
 * Размер цифр — от ширины плитки, а не в vw: PiP-окно пользователь тянет за угол, и цифры должны
 * расти вместе с ним, как на стене.
 */
const tileTypography = (width, columns) => {
    const tileWidth = (width - (columns - 1) * TILE_GAP) / columns;
    return {
        tileWidth,
        valuePx: Math.round(clamp(tileWidth * 0.24, 19, 40)),
        labelPx: Math.round(clamp(tileWidth * 0.088, 11, 13.5) * 10) / 10,
    };
};

/*
 * Ширины плитки хватает не на любое значение: «3» и «1 301/1 380» при одном кегле — разница
 * вчетверо. Поэтому кегль подрезаем ещё и под длину: цифра в tabular-nums занимает примерно
 * 0,6 кегля, приглушённое второе число идёт долей от первого. Без этого длинные значения
 * вылезали за края плитки и наезжали на соседнюю.
 */
const SECONDARY_EM = 0.58;
const DIGIT_TO_FONT = 0.6;

const fittedValuePx = (basePx, tileWidth, value, secondary) => {
    const units = String(value).length + (secondary === null ? 0 : (String(secondary).length + 1) * SECONDARY_EM);
    if (units <= 0) return basePx;
    const available = Math.max(24, tileWidth - 16);
    return Math.round(clamp(available / (units * DIGIT_TO_FONT), 13, basePx));
};

/*
 * Высота окна под уже выбранный набор: окно на шесть плиток и окно со списками операторов —
 * это разные окна, и открывать оба одинаковыми значит либо оставить полэкрана пустым, либо
 * сразу спрятать часть показателей под прокрутку. Размеры взяты с готовой вёрстки (шапка,
 * отступы, высота плитки и блока списка), то есть это оценка, а не подгонка попиксельно —
 * дальше окно всё равно тянет пользователь, и Chrome запоминает его размер.
 */
const estimatePipHeight = (metricKeys, metricMap) => {
    const chosen = metricKeys.map((key) => metricMap[key]).filter(Boolean);
    const tiles = chosen.filter((metric) => metric.kind !== 'list').length;
    const lists = chosen.filter((metric) => metric.kind === 'list').length;
    const rows = Math.ceil(tiles / Math.max(1, columnsFor(PIP_WIDTH - 20, tiles)));
    const height = 44 + 20
        + rows * 92 + Math.max(0, rows - 1) * TILE_GAP
        + lists * (132 + TILE_GAP);
    return Math.round(clamp(height, 260, 760));
};

const MetricTile = ({ metric, snapshot, typography }) => {
    const { value, secondary, tone } = readWallboardMetric(metric, snapshot);
    return (
        <div
            className="flex min-w-0 flex-col items-center justify-center gap-1.5 overflow-hidden rounded-2xl bg-white px-2.5 py-3 text-center ring-1 ring-slate-900/5"
            title={metric.hint || metric.label}
        >
            {/* Подпись переносим, а не режем: «Сброс на прив…» на табло бесполезен. */}
            <div
                className="line-clamp-2 max-w-full font-medium leading-tight text-slate-500"
                style={{ fontSize: `${typography.labelPx}px` }}
            >
                {metric.label}
            </div>
            <div
                className={`font-semibold tabular-nums leading-none ${WALLBOARD_TONE_TEXT[tone] || WALLBOARD_TONE_TEXT.neutral}`}
                style={{ fontSize: `${fittedValuePx(typography.valuePx, typography.tileWidth, value, secondary)}px` }}
            >
                {value}
                {secondary === null ? null : (
                    <span className="font-normal text-slate-400" style={{ fontSize: `${SECONDARY_EM}em` }}>/{secondary}</span>
                )}
            </div>
        </div>
    );
};

/*
 * Список статусов. Причину показываем чипом ТОЛЬКО когда это не обычный перерыв: иначе тренинг
 * и тех.причина молча смешались бы с перерывом, а лишних чипов на экране не будет.
 */
const MetricList = ({ metric, snapshot }) => {
    const { items } = readWallboardMetric(metric, snapshot);
    const entries = items || [];
    return (
        <div className="rounded-2xl bg-white px-3 py-2.5 ring-1 ring-slate-900/5">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-slate-500">
                <FaIcon className={`fas ${metric.icon || 'fa-list-ul'}`} style={{ fontSize: 12 }}></FaIcon>
                <span>{metric.label}</span>
                {entries.length > 0 ? (
                    <span className="ml-auto tabular-nums text-slate-400">{entries.length}</span>
                ) : null}
            </div>
            {entries.length === 0 ? (
                <div className="pt-1.5 text-[12.5px] text-slate-400">Никого</div>
            ) : (
                <ul className="mt-1 divide-y divide-slate-100">
                    {entries.map((item) => {
                        // Какой чип рисовать, знает каталог показателей: у линии это причина
                        // перерыва, у чатов — статус чатника.
                        const chip = metric.chip?.(item) || null;
                        return (
                            <li
                                key={`${item.operator_id ?? item.name}-${item.since ?? ''}`}
                                className="flex items-center gap-2 py-1.5"
                            >
                                <span className="min-w-0 flex-1 truncate text-[13px] text-slate-800">{item.name}</span>
                                {chip ? (
                                    <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium ${chip.className}`}>
                                        {chip.label}
                                    </span>
                                ) : null}
                                <span className="shrink-0 text-[12.5px] font-medium tabular-nums text-slate-400">
                                    {formatDuration(item.seconds)}
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
};

/** Кнопка панели виджета: одинаковая для обновления, шестерёнки и закрытия. */
const ToolButton = ({ icon, label, active = false, tone = 'default', onClick }) => (
    <button
        type="button"
        title={label}
        aria-label={label}
        onClick={onClick}
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg transition active:scale-95 ${
            active
                ? 'bg-slate-900 text-white'
                : tone === 'danger'
                    ? 'text-slate-400 hover:bg-rose-50 hover:text-rose-500'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
        }`}
    >
        <FaIcon className={`fas ${icon}`} style={{ fontSize: 13 }}></FaIcon>
    </button>
);

/** Что показывать: список каталога с галочками, сгруппированный так же, как на табло. */
const SettingsPanel = ({ config, selected, onToggle, onReset, onDone }) => {
    const chosen = new Set(selected);
    return (
        <div className="space-y-2.5">
            <div className="flex items-center justify-between px-1">
                <span className="text-[12px] text-slate-500">
                    Отмечено {chosen.size} из {config.metrics.length}
                </span>
                <button
                    type="button"
                    className="rounded-lg px-2 py-1 text-[12px] font-medium text-slate-500 transition hover:bg-slate-200/70"
                    onClick={onReset}
                >
                    По умолчанию
                </button>
            </div>
            {config.metricGroups.map((group) => {
                const metrics = config.metrics.filter((metric) => metric.group === group.key);
                if (metrics.length === 0) return null;
                return (
                    <div key={group.key} className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-900/5">
                        <div className="px-3 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">
                            {group.title}
                        </div>
                        <div className="divide-y divide-slate-100">
                            {metrics.map((metric) => {
                                const on = chosen.has(metric.key);
                                return (
                                    <button
                                        key={metric.key}
                                        type="button"
                                        role="switch"
                                        aria-checked={on}
                                        title={metric.hint || metric.label}
                                        onClick={() => onToggle(metric.key)}
                                        className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition hover:bg-slate-50"
                                    >
                                        <span
                                            className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full transition ${
                                                on ? 'bg-blue-500 text-white' : 'ring-1 ring-inset ring-slate-300'
                                            }`}
                                        >
                                            {on ? <FaIcon className="fas fa-check" style={{ fontSize: 10 }}></FaIcon> : null}
                                        </span>
                                        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-800">
                                            {metric.label}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                );
            })}
            <button
                type="button"
                className="w-full rounded-xl bg-slate-900 py-2 text-[13px] font-semibold text-white transition active:scale-[0.99]"
                onClick={onDone}
            >
                Готово
            </button>
        </div>
    );
};

export default function SzovWallboardWidget({
    user,
    direction = 'osnova',
    apiBaseUrl,
    withAccessTokenHeader,
    showToast,
    onClose,
}) {
    const userId = user?.id || 0;
    /*
     * Направление у окна одно и на всю его жизнь: смена направления в разделе не переключает
     * уже открытый виджет, а открывает новый (в App.jsx компонент монтируется с key по
     * направлению). Иначе пришлось бы менять хук опроса на лету, чего React не допускает.
     */
    const config = wallboardDirection(direction);
    const { snapshot, error, loading, refresh } = config.useSnapshot({ apiBaseUrl, withAccessTokenHeader });

    const [pipContainer, setPipContainer] = useState(null);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [frameWidth, setFrameWidth] = useState(PIP_WIDTH - 20);
    const [metrics, setMetrics] = useState(() => readStoredMetrics(userId, config));

    const bodyRef = useRef(null);
    const pipWindowRef = useRef(null);
    // onClose и showToast приходят новыми функциями на каждый рендер родителя (а он
    // перерисовывается раз в 15 с по опросу табло). В зависимостях эффекта открытия это
    // переоткрывало бы окно.
    const closeRef = useRef(onClose);
    closeRef.current = onClose;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    /*
     * Просим окно поверх других — один раз за открытие виджета. Chrome разрешает документу
     * ровно одно такое окно: если оно уже занято (например, закреплённой задачей), новый запрос
     * отобрал бы его молча — поэтому вместо кражи объясняем, почему не открылось.
     */
    useEffect(() => {
        let cancelled = false;
        const fail = (message) => {
            if (cancelled) return;
            toastRef.current?.(message, 'error');
            closeRef.current?.();
        };
        const pipApi = typeof window === 'undefined' ? null : window.documentPictureInPicture;
        if (!pipApi?.requestWindow) {
            fail('Этот браузер не умеет открывать окно поверх других — нужен Chrome или Edge');
            return undefined;
        }
        if (pipApi.window) {
            fail('Окно поверх других уже занято другим виджетом — закройте его и откройте табло снова');
            return undefined;
        }
        pipApi.requestWindow({
            width: PIP_WIDTH,
            height: estimatePipHeight(readStoredMetrics(userId, config), config.metricMap),
        })
            .then((pipWindow) => {
                if (cancelled) {
                    pipWindow.close?.();
                    return;
                }
                pipWindow.document.title = config.title;
                cloneDocumentStyles(pipWindow);
                pipWindow.document.body.style.margin = '0';
                pipWindow.document.body.style.background = '#f1f5f9';
                const root = pipWindow.document.createElement('div');
                pipWindow.document.body.appendChild(root);
                pipWindowRef.current = pipWindow;
                // Закрыли окно системным крестиком — виджет закрыт, а не «висит невидимым».
                pipWindow.addEventListener('pagehide', () => {
                    pipWindowRef.current = null;
                    closeRef.current?.();
                });
                setPipContainer(root);
            })
            .catch(() => {
                // Браузер вправе отказать: например, запрос ушёл без свежего жеста пользователя.
                fail('Не удалось открыть окно поверх других — попробуйте ещё раз');
            });
        return () => { cancelled = true; };
        // Ровно один раз за жизнь виджета: пользователь за это время не меняется (при выходе
        // виджет размонтируется), а повторный запрос отобрал бы окно у самого себя.
    }, []);

    useEffect(() => () => {
        try {
            pipWindowRef.current?.close?.();
        } catch (closeError) {
            // Гонки при закрытии браузера нас уже не касаются.
        }
        pipWindowRef.current = null;
    }, []);

    /*
     * Ширина окна правит и число колонок, и размер цифр, поэтому её надо мерить.
     *
     * ResizeObserver берём из ТОГО окна, где живёт узел. Наблюдатель, созданный в основном
     * документе, за элементом PiP-окна отдаёт только первое измерение и потом молчит: у PiP-окна
     * свой цикл отрисовки. Из-за этого виджет запоминал ширину, с которой открылся, и после
     * уменьшения окна раскладывал плитки на несуществующую ширину — цифры наезжали друг на
     * друга. Слушателя resize оставляем страховкой, если наблюдатель всё же промолчит.
     */
    useEffect(() => {
        const node = bodyRef.current;
        const pipWindow = pipWindowRef.current;
        if (!node || !pipWindow) return undefined;
        const measure = () => {
            const width = node.clientWidth;
            if (Number.isFinite(width) && width > 0) setFrameWidth(width);
        };
        measure();
        const Observer = pipWindow.ResizeObserver;
        const observer = Observer ? new Observer(measure) : null;
        observer?.observe(node);
        pipWindow.addEventListener('resize', measure);
        return () => {
            observer?.disconnect();
            pipWindow.removeEventListener('resize', measure);
        };
    }, [pipContainer]);

    // Список показателей длиннее окна: вернувшись из настройки, надо видеть плитки с начала,
    // а не то место списка, где стояла прокрутка.
    useEffect(() => {
        if (bodyRef.current) bodyRef.current.scrollTop = 0;
    }, [settingsOpen]);

    const persistMetrics = useCallback((next) => {
        setMetrics(next);
        writeStoredMetrics(userId, config, next);
    }, [userId, config]);

    const toggleMetric = useCallback((key) => {
        // Порядок плиток — всегда порядок каталога: иначе набор перетасовывался бы от каждой галочки.
        persistMetrics(
            metrics.includes(key)
                ? metrics.filter((item) => item !== key)
                : config.metrics.filter((metric) => metric.key === key || metrics.includes(metric.key))
                    .map((metric) => metric.key)
        );
    }, [metrics, persistMetrics, config]);

    const resetMetrics = useCallback(() => persistMetrics([...config.defaultMetrics]), [persistMetrics, config]);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, config.source),
                                [snapshot, error, config]);
    const visibleMetrics = useMemo(
        () => metrics.map((key) => config.metricMap[key]).filter(Boolean),
        [metrics, config]
    );

    if (!pipContainer) return null;

    const tiles = visibleMetrics.filter((metric) => metric.kind !== 'list');
    const lists = visibleMetrics.filter((metric) => metric.kind === 'list');
    const columns = columnsFor(frameWidth, tiles.length);
    const typography = tileTypography(frameWidth, columns);
    const clock = snapshot?.[config.clockField];
    const subtitle = staleNotice
        || (clock ? `Данные ${config.source} на ${formatClock(clock)}` : config.hint);

    return createPortal(
        // Ровно высота окна, а не минимум: иначе при длинном наборе прокручивался бы весь
        // документ вместе с панелью, а не только содержимое.
        <section
            aria-label={`Виджет: ${config.title}`}
            className="flex h-[100vh] flex-col overflow-hidden bg-slate-100"
            style={{ fontFamily: APPLE_FONT }}
        >
            <header className="flex shrink-0 items-center gap-2 border-b border-slate-200/70 bg-white/85 px-2.5 py-2 backdrop-blur-xl">
                <div className="min-w-0 flex-1 select-none">
                    <div className="truncate text-[12.5px] font-semibold leading-tight text-slate-900">
                        {settingsOpen ? 'Что показывать' : config.title}
                    </div>
                    <div className={`truncate text-[11px] leading-tight ${staleNotice ? 'text-amber-600' : 'text-slate-400'}`}>
                        {settingsOpen ? 'Отметьте показатели для виджета' : subtitle}
                    </div>
                </div>
                <ToolButton icon="fa-rotate" label="Обновить" onClick={() => refresh()} />
                <ToolButton
                    icon="fa-sliders"
                    label={settingsOpen ? 'Вернуться к показателям' : 'Выбрать показатели'}
                    active={settingsOpen}
                    onClick={() => setSettingsOpen((prev) => !prev)}
                />
                <ToolButton icon="fa-times" label="Закрыть виджет" tone="danger" onClick={() => onClose?.()} />
            </header>

            <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2.5">
                {settingsOpen ? (
                    <SettingsPanel
                        config={config}
                        selected={metrics}
                        onToggle={toggleMetric}
                        onReset={resetMetrics}
                        onDone={() => setSettingsOpen(false)}
                    />
                ) : !snapshot && loading ? (
                    <div className="px-1 py-4 text-[12.5px] text-slate-500">Загружаем данные {config.source}…</div>
                ) : !snapshot ? (
                    <div className="px-1 py-4 text-[12.5px] text-rose-600">{error || 'Данные недоступны'}</div>
                ) : visibleMetrics.length === 0 ? (
                    <div className="px-1 py-4 text-[12.5px] text-slate-500">
                        Показатели не выбраны — нажмите
                        <FaIcon className="fas fa-sliders mx-1" style={{ fontSize: 12 }}></FaIcon>
                        и отметьте, что мониторить.
                    </div>
                ) : (
                    <div className="space-y-2.5">
                        {tiles.length > 0 ? (
                            <div
                                className="grid"
                                style={{
                                    gap: `${TILE_GAP}px`,
                                    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                                }}
                            >
                                {tiles.map((metric) => (
                                    <MetricTile
                                        key={metric.key}
                                        metric={metric}
                                        snapshot={snapshot}
                                        typography={typography}
                                    />
                                ))}
                            </div>
                        ) : null}
                        {lists.map((metric) => (
                            <MetricList key={metric.key} metric={metric} snapshot={snapshot} />
                        ))}
                    </div>
                )}
            </div>
        </section>,
        pipContainer,
    );
}
