import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost } from '../ui/ios';

/*
 * «Табло СЗоВ» — онлайн-мониторинг входящей линии (задача #108).
 *
 * Экран рассчитан на вывод на стену, поэтому:
 *   - обновляется сам, без перезагрузки страницы (опрос раз в 10 с, один общий снапшот);
 *   - показатели собраны в сплошные панели без зазоров: соседние ячейки делит волосяная
 *     линия, поэтому цифрам достаётся вся ширина и подписи рядов не нужны;
 *   - цветом помечаем только то, что несёт смысл: AR вне целевого коридора и статусы
 *     операторов (у них цвет задан владельцем, чтобы различать причины с расстояния).
 *
 * Все показатели считает бэкенд (/api/szov_wallboard/snapshot) по формулам «Биллинга Oktell»,
 * чтобы табло и отчёт не расходились в цифрах.
 */

// Опрос под TTL серверного кэша (13 с). Прокси Oktell низкоконкурентный и иногда подвисает
// на установке соединения, поэтому лишний раз его не дёргаем: 15 с для стены достаточно.
const POLL_INTERVAL_MS = 15000;
const FULLSCREEN_Z = 150;

/*
 * AR — не «чем меньше, тем лучше», а коридор (правило владельца):
 *   зелёный  — 4,0 … 4,9 %  (норма)
 *   красный  — ниже 3,9 % или выше 5,0 %
 *   янтарный — узкие зоны 3,9…4,0 и 4,9…5,0: ещё не нарушение, но уже у границы.
 * Слишком низкий AR — тоже сигнал (перезаложены операторы), поэтому он красный.
 */
const AR_RED_BELOW_PERCENT = 3.9;
const AR_TARGET_MIN_PERCENT = 4.0;
const AR_TARGET_MAX_PERCENT = 4.9;
const AR_RED_ABOVE_PERCENT = 5.0;

const arTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    const percent = Number(ratio) * 100;
    if (percent < AR_RED_BELOW_PERCENT || percent > AR_RED_ABOVE_PERCENT) return 'bad';
    if (percent >= AR_TARGET_MIN_PERCENT && percent <= AR_TARGET_MAX_PERCENT) return 'good';
    return 'warn';
};

/*
 * SL — доля звонков, отвеченных в пределах порога ожидания, ко ВСЕМ попавшим в очередь.
 * Пороги те же, что в отчёте «Расчёт ресурсов -> Биллинг» (ResourceFteView), иначе одна и та
 * же цифра горела бы на табло и в отчёте разными цветами.
 */
const SL_GOOD_RATIO = 0.8;
const SL_WARN_RATIO = 0.6;

const slTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    const value = Number(ratio);
    if (value >= SL_GOOD_RATIO) return 'good';
    if (value >= SL_WARN_RATIO) return 'warn';
    return 'bad';
};

const formatInt = (value) => (Number.isFinite(Number(value)) ? Number(value).toLocaleString('ru-RU') : '—');

const formatPercent = (ratio, digits = 1) => (
    ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))
        ? '—'
        : `${(Number(ratio) * 100).toFixed(digits).replace('.', ',')}%`
);

/** Секунды -> «М:СС» (или «Ч:ММ:СС» для долгих ожиданий). Компактно, читаемо с расстояния. */
const formatDuration = (seconds) => {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total < 0) return '—';
    const whole = Math.round(total);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const secs = whole % 60;
    if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    return `${minutes}:${String(secs).padStart(2, '0')}`;
};

/** «12:07:18» из «2026-08-03 12:07:18» — на табло дата не нужна, только время источника. */
const formatClock = (value) => {
    const text = String(value || '').trim();
    const match = text.match(/(\d{2}):(\d{2}):(\d{2})/);
    return match ? match[0] : '—';
};

/*
 * Ключевые плитки цветные: два «оценочных» цвета (очередь и AR — по ним видно, всё ли в норме)
 * и два «опознавательных» (онлайн синий, перерыв оранжевый — это идентичность статуса, а не
 * оценка). Плитки дня и операторов остаются белыми: цвет там только у смысловых величин.
 */
const KEY_PALETTE = {
    good: { bg: 'bg-emerald-100/70', text: 'text-emerald-700', hint: 'text-emerald-600/80' },
    warn: { bg: 'bg-amber-100/70', text: 'text-amber-700', hint: 'text-amber-600/80' },
    bad: { bg: 'bg-rose-100/70', text: 'text-rose-700', hint: 'text-rose-600/80' },
    info: { bg: 'bg-blue-100/70', text: 'text-blue-700', hint: 'text-blue-600/80' },
    slate: { bg: 'bg-slate-100', text: 'text-slate-700', hint: 'text-slate-500' },
};

const STAT_TONE = {
    neutral: 'text-slate-900',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
};

// Два размера цифр: ключевые показатели читаются через зал, показатели дня и операторов — рядом.
const VALUE_SIZE = { key: [3, 4.7, 5], stat: [2.375, 3.5, 3.75] };

const valueFontSize = (size, scale) => {
    const [min, mid, max] = VALUE_SIZE[size] || VALUE_SIZE.stat;
    return `clamp(${(min * scale).toFixed(3)}rem, ${(mid * scale).toFixed(2)}vw, ${(max * scale).toFixed(3)}rem)`;
};

/** Секция с подписью и иконкой; внутри — сетка плиток с отступами. */
const Section = ({ icon, title, children }) => (
    <div className={`${iosCard} p-5`}>
        <div className="mb-4 flex items-center gap-2.5 px-0.5 text-[15px] font-semibold text-slate-500">
            <FaIcon className={`fas ${icon}`}></FaIcon>
            <span>{title}</span>
        </div>
        {children}
    </div>
);

const Grid = ({ children }) => (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{children}</div>
);

const KeyTile = ({ label, value, hint, tone = 'slate', scale = 1 }) => {
    const palette = KEY_PALETTE[tone] || KEY_PALETTE.slate;
    return (
        <div className={`flex flex-col items-center gap-2 rounded-2xl px-4 py-6 text-center ${palette.bg}`}>
            <div className={`text-[15px] font-semibold ${palette.text}`}>{label}</div>
            <div
                className={`font-semibold tabular-nums leading-none ${palette.text}`}
                style={{ fontSize: valueFontSize('key', scale) }}
            >
                {value}
            </div>
            {hint ? <div className={`text-[13px] leading-tight ${palette.hint}`}>{hint}</div> : null}
        </div>
    );
};

const StatTile = ({ label, value, tone = 'neutral', scale = 1 }) => (
    <div className="flex flex-col items-center gap-2.5 rounded-2xl border border-slate-200/80 px-4 py-5 text-center">
        <div className="text-[14px] font-medium text-slate-500">{label}</div>
        <div
            className={`font-semibold tabular-nums leading-none ${STAT_TONE[tone] || STAT_TONE.neutral}`}
            style={{ fontSize: valueFontSize('stat', scale) }}
        >
            {value}
        </div>
    </div>
);

/*
 * «Принято / входящих» одной плиткой: принятые — главное число, общий поток приглушён.
 * Разрыв между ними и есть потери, а насколько он допустим, показывает плитка AR.
 */
const PairTile = ({ label, first, second, scale = 1 }) => (
    <div className="flex flex-col items-center gap-2.5 rounded-2xl border border-slate-200/80 px-4 py-5 text-center">
        <div className="text-[14px] font-medium text-slate-500">{label}</div>
        <div
            className="font-semibold tabular-nums leading-none text-slate-900"
            style={{ fontSize: valueFontSize('stat', scale) }}
        >
            {first}
            <span className="font-normal text-slate-400" style={{ fontSize: '0.6em' }}>/{second}</span>
        </div>
    </div>
);

/*
 * Цвета статусов заданы владельцем: перерыв — оранжевый, тренинг — зелёный,
 * тех.причина — фиолетовый. Перезвон владелец не называл; взяли синий (акцент приложения),
 * чтобы он не сливался с тремя остальными.
 */
const STATUS_STYLE = {
    break: { label: 'Перерыв', chip: 'bg-orange-100 text-orange-700' },
    training: { label: 'Тренинг', chip: 'bg-emerald-100 text-emerald-700' },
    tech: { label: 'Тех.причина', chip: 'bg-violet-100 text-violet-700' },
    recall: { label: 'Перезвон', chip: 'bg-blue-100 text-blue-700' },
};

/*
 * Блок статуса: подпись с иконкой и список «имя, под ним время в статусе».
 * Счётчик в подписи не дублируем — он уже стоит крупной плиткой в ключевых показателях.
 * Причину показываем чипом ТОЛЬКО когда это не обычный перерыв: иначе тренинг и
 * тех.причина молча смешались бы с перерывом, а лишних чипов на экране не будет.
 */
const StatusBlock = ({ title, icon, entries, scale = 1 }) => {
    const items = Array.isArray(entries) ? entries : [];
    const nameSize = `clamp(1rem, ${(1.25 * scale).toFixed(2)}vw, ${(1.375 * scale).toFixed(3)}rem)`;
    return (
        <div className="flex min-h-0 flex-col">
            <div className="mb-2 flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className={`fas ${icon}`}></FaIcon>
                <span>{title}</span>
            </div>
            {items.length === 0 ? (
                <div className="py-1.5 text-[15px] text-slate-400">Никого</div>
            ) : (
                <ul className="min-h-0 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => {
                        const style = STATUS_STYLE[item.reason_key];
                        const showReason = Boolean(style) && item.reason_key !== 'break';
                        return (
                            <li key={`${item.operator_id ?? item.name}-${item.since ?? ''}`} className="py-3">
                                <div className="flex items-start gap-2">
                                    <span className="min-w-0 leading-snug text-slate-800" style={{ fontSize: nameSize }}>
                                        {item.name}
                                    </span>
                                    {showReason ? (
                                        <span className={`mt-0.5 shrink-0 rounded-md px-2 py-0.5 text-[12px] font-medium ${style.chip}`}>
                                            {item.reason}
                                        </span>
                                    ) : null}
                                </div>
                                <div className="mt-0.5 text-[14px] font-medium tabular-nums text-slate-400">
                                    {formatDuration(item.seconds)}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
};

/** Правая колонка табло: перерывы сверху, перезвон прижат к низу карточки. */
const StatusColumn = ({ now, scale = 1 }) => (
    <div className={`${iosCard} flex flex-col p-5`}>
        <StatusBlock title="На перерыве" icon="fa-list-ul" entries={now.break_list} scale={scale} />
        <div className="mt-auto border-t border-slate-200/70 pt-4">
            <StatusBlock title="Перезвон" icon="fa-phone-volume" entries={now.recall_list} scale={scale} />
        </div>
    </div>
);

/** Само табло. Выделено в компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
/** Само табло. Выделено в компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
const WallboardBody = ({ snapshot, scale }) => {
    const now = snapshot?.now || {};
    const today = snapshot?.today || {};

    // Очередь оцениваем: пусто — хорошо; есть очередь и никто не свободен — тревога.
    const queue = Number(now.queue) || 0;
    const nobodyFree = Number(now.operators_free) === 0;
    const queueTone = queue === 0 ? 'good' : nobodyFree ? 'bad' : 'warn';

    // Тренинг и тех.причина отдельных плиток не имеют — чтобы люди в этих статусах не
    // пропадали из виду, показываем их приглушённой строкой, но только когда они есть.
    const asideParts = [
        [Number(now.operators_on_training) || 0, 'на тренинге'],
        [Number(now.operators_on_tech) || 0, 'по тех.причине'],
        [Number(now.operators_other) || 0, 'в прочих статусах (резерв, нет на месте)'],
    ].filter(([count]) => count > 0).map(([count, label]) => `${formatInt(count)} ${label}`);

    return (
        // Две колонки: показатели слева, статусы операторов узкой колонкой справа во всю высоту.
        // На узком экране колонка уезжает вниз.
        <div
            className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]"
            style={{ fontFamily: APPLE_FONT }}
        >
            <div className="space-y-4">
            <Section icon="fa-bolt" title="Ключевые показатели · сейчас">
                <Grid>
                    <KeyTile
                        label="В очереди"
                        value={formatInt(queue)}
                        hint="Ждут ответа"
                        tone={queueTone}
                        scale={scale}
                    />
                    <KeyTile
                        label="AR"
                        value={formatPercent(today.ar_ratio)}
                        hint={`Норма ${String(AR_TARGET_MIN_PERCENT).replace('.', ',')}–${String(AR_TARGET_MAX_PERCENT).replace('.', ',')}%`}
                        tone={arTone(today.ar_ratio)}
                        scale={scale}
                    />
                    <KeyTile
                        label="Онлайн"
                        value={formatInt(now.operators_online)}
                        hint="Сотрудников"
                        tone="info"
                        scale={scale}
                    />
                    <KeyTile
                        label="Перерыв"
                        value={formatInt(now.operators_on_break)}
                        hint="Сотрудников"
                        tone="warn"
                        scale={scale}
                    />
                </Grid>
            </Section>

            <Section icon="fa-chart-bar" title="Показатели за день">
                <Grid>
                    <PairTile
                        label="Принято / входящих"
                        first={formatInt(today.served)}
                        second={formatInt(today.total)}
                        scale={scale}
                    />
                    <StatTile label="Потеряно" value={formatInt(today.lost)} tone="bad" scale={scale} />
                    <StatTile label="SL" value={formatPercent(today.sl_ratio)} tone={slTone(today.sl_ratio)} scale={scale} />
                    <StatTile label="Ср. ожидание" value={formatDuration(today.avg_wait_seconds)} scale={scale} />
                </Grid>
            </Section>

            <Section icon="fa-headset" title="Операторы">
                <Grid>
                    <StatTile label="Свободны" value={formatInt(now.operators_free)} scale={scale} />
                    <StatTile label="В разговоре" value={formatInt(now.operators_talking)} scale={scale} />
                    <StatTile label="Ср. разговор" value={formatDuration(today.avg_talk_seconds)} scale={scale} />
                    <StatTile label="Перезвон" value={formatInt(now.operators_on_recall)} scale={scale} />
                </Grid>
                {asideParts.length > 0 ? (
                    <div className="mt-3 px-1 text-[14px] text-slate-400">
                        Ещё {asideParts.join(' · ')}
                    </div>
                ) : null}
            </Section>
            </div>

            <StatusColumn now={now} scale={scale} />
        </div>
    );
};

export default function SzovWallboardView(props) {
    const { apiBaseUrl, withAccessTokenHeader } = props;

    const [snapshot, setSnapshot] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [fullscreen, setFullscreen] = useState(false);

    const inFlightRef = useRef(false);
    const abortRef = useRef(null);
    const headersRef = useRef(withAccessTokenHeader);
    headersRef.current = withAccessTokenHeader;

    const load = useCallback(async ({ silent = false } = {}) => {
        // Защита от наложения запросов: табло опрашивается по таймеру и по фокусу окна.
        if (inFlightRef.current) return;
        inFlightRef.current = true;
        abortRef.current?.abort?.();
        const controller = new AbortController();
        abortRef.current = controller;
        if (!silent) setLoading(true);
        try {
            const buildHeaders = headersRef.current;
            const response = await fetch(`${apiBaseUrl}/api/szov_wallboard/snapshot`, {
                headers: buildHeaders ? buildHeaders({ Accept: 'application/json' }) : { Accept: 'application/json' },
                credentials: 'include',
                signal: controller.signal,
            });
            if (!response.ok) {
                let detail = '';
                try {
                    const body = await response.json();
                    detail = body?.error || body?.detail || '';
                } catch (parseError) {
                    detail = '';
                }
                throw new Error(detail || `Сервер ответил ${response.status}`);
            }
            const data = await response.json();
            setSnapshot(data);
            setError(null);
        } catch (requestError) {
            if (requestError?.name === 'AbortError') return;
            // Пока есть последний снимок — экран на стене не гасим, просто помечаем расхождение.
            setError(requestError?.message || 'Не удалось получить данные');
        } finally {
            inFlightRef.current = false;
            setLoading(false);
        }
    }, [apiBaseUrl]);

    const loadRef = useRef(load);
    loadRef.current = load;

    useEffect(() => {
        let cancelled = false;
        const isActive = () => {
            const visible = typeof document.visibilityState === 'string'
                ? document.visibilityState === 'visible'
                : !document.hidden;
            return visible;
        };
        loadRef.current?.({ silent: false });
        const timer = window.setInterval(() => {
            // Скрытую вкладку не опрашиваем: незачем дёргать прокси Oktell впустую.
            if (!cancelled && isActive()) loadRef.current?.({ silent: true });
        }, POLL_INTERVAL_MS);
        const onWake = () => {
            if (!cancelled && isActive()) loadRef.current?.({ silent: true });
        };
        document.addEventListener('visibilitychange', onWake);
        window.addEventListener('focus', onWake);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
            document.removeEventListener('visibilitychange', onWake);
            window.removeEventListener('focus', onWake);
            abortRef.current?.abort?.();
        };
    }, []);

    const staleNotice = useMemo(() => {
        if (error) return error;
        if (snapshot?.stale) {
            const age = Number(snapshot.age_seconds);
            return `Oktell не отвечает, данные ${Number.isFinite(age) ? `${formatDuration(age)} назад` : 'устарели'}`;
        }
        return null;
    }, [error, snapshot]);

    const header = (
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
                <h1 className="text-[26px] font-semibold text-slate-900">Табло СЗоВ</h1>
                <p className="mt-1 text-[14px] text-slate-500">
                    Входящая линия в реальном времени
                    {snapshot?.oktell_now ? ` · данные Oktell на ${formatClock(snapshot.oktell_now)}` : ''}
                </p>
            </div>
            <div className="flex items-center gap-2">
                {staleNotice ? (
                    <span className="rounded-full bg-amber-50 px-3 py-1 text-[12px] font-medium text-amber-700 ring-1 ring-amber-200">
                        {staleNotice}
                    </span>
                ) : null}
                <button type="button" className={iosBtnGhost} onClick={() => load({ silent: true })} disabled={loading}>
                    <FaIcon className="fas fa-rotate"></FaIcon>
                    Обновить
                </button>
                <button type="button" className={iosBtnGhost} onClick={() => setFullscreen(true)}>
                    <FaIcon className="fas fa-expand"></FaIcon>
                    На весь экран
                </button>
            </div>
        </div>
    );

    if (!snapshot && loading) {
        return (
            <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
                {header}
                <div className={`${iosCard} p-6 text-[13px] text-slate-500`}>Загружаем данные Oktell…</div>
            </div>
        );
    }

    if (!snapshot) {
        return (
            <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
                {header}
                <div className={`${iosCard} p-6 text-[13px] text-rose-600`}>
                    {error || 'Данные недоступны'}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <WallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon="fa-tachometer-alt"
                    title="Табло СЗоВ"
                    subtitle={`${snapshot?.oktell_now ? `Данные Oktell на ${formatClock(snapshot.oktell_now)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <WallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
}
