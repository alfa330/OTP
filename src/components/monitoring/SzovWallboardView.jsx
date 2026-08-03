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

const POLL_INTERVAL_MS = 10000;
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

const VALUE_TONE = {
    neutral: 'text-slate-900',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
};

// Размеры цифр задают иерархию значимости:
//   hero — то, по чему принимают решение сию секунду (очередь, ожидание, AR);
//   lg   — сколько людей на линии и сколько из них могут взять звонок;
//   sm   — итоги дня, их смотрят вблизи и без спешки.
// Разбивка по причинам перерыва сюда не входит: она живёт в тонкой полосе StatusStrip.
const VALUE_SIZE = {
    hero: [2.75, 5, 5.25],
    lg: [2, 3.2, 3.5],
    sm: [1.625, 2.2, 2.375],
};

const valueFontSize = (size, scale) => {
    const [min, mid, max] = VALUE_SIZE[size] || VALUE_SIZE.lg;
    return `clamp(${(min * scale).toFixed(3)}rem, ${(mid * scale).toFixed(2)}vw, ${(max * scale).toFixed(3)}rem)`;
};

const LABEL_CLASS = 'text-[13px] font-semibold uppercase tracking-wide text-slate-500';
const HAIRLINE = 'divide-slate-200/70';
const HAIRLINE_TOP = 'border-t border-slate-200/70';

/*
 * Показатели сгруппированы в сплошные панели: между соседними ячейками нет зазора,
 * их делит волосяная линия. Так цифрам достаётся вся ширина, а группы читаются
 * без подписей рядов — сама панель и есть группа.
 */
const Panel = ({ children }) => (
    <div className={`${iosCard} overflow-hidden`}>{children}</div>
);

const Row = ({ cols, children, divided = false }) => (
    <div className={`grid ${cols} divide-x ${HAIRLINE} ${divided ? HAIRLINE_TOP : ''}`}>
        {children}
    </div>
);

/*
 * Полупрозрачная иконка-подложка. Нужна ряду операторов: он про людей, а не про звонки,
 * и водяной знак отделяет его от остальных панелей, ничего не загораживая.
 * strokeWidth крупнее обычного — тонкий контур на такой прозрачности просто пропадает.
 */
const CellWatermark = ({ icon, scale = 1 }) => (
    <FaIcon
        className={`fas ${icon} pointer-events-none absolute -bottom-3 -right-3 text-slate-900/[0.05]`}
        strokeWidth={2.5}
        aria-hidden="true"
        style={{ fontSize: `clamp(${(4 * scale).toFixed(2)}rem, ${(7 * scale).toFixed(2)}vw, ${(7.5 * scale).toFixed(2)}rem)` }}
    />
);

// Содержимое ячейки всегда по центру: на табло так цифры выравниваются между собой
// по вертикальной оси карточки и не «липнут» к разделителям.
const CELL_CLASS = 'relative flex min-w-0 flex-col items-center gap-2 overflow-hidden px-5 py-5 text-center';

const Cell = ({ label, value, hint, tone = 'neutral', size = 'lg', scale = 1, icon = null, bg = '' }) => (
    <div className={`${CELL_CLASS} ${bg}`}>
        {icon ? <CellWatermark icon={icon} scale={scale} /> : null}
        <div className={`relative ${LABEL_CLASS}`}>{label}</div>
        <div
            className={`relative font-semibold tabular-nums leading-[1.02] ${VALUE_TONE[tone] || VALUE_TONE.neutral}`}
            style={{ fontSize: valueFontSize(size, scale) }}
        >
            {value}
        </div>
        {hint ? <div className="relative text-[12px] leading-snug text-slate-400">{hint}</div> : null}
    </div>
);

/*
 * Пара «поступило / принято» в одной ячейке. Разрыв между этими числами и есть потери,
 * поэтому принятые красим тоном AR: видно не только сколько взяли, но и укладывается ли
 * доля потерь в норму.
 */
const PairCell = ({ label, first, second, secondTone = 'neutral', hint, size = 'hero', scale = 1 }) => (
    <div className={CELL_CLASS}>
        <div className={LABEL_CLASS}>{label}</div>
        <div
            className="font-semibold tabular-nums leading-[1.02]"
            style={{ fontSize: valueFontSize(size, scale) }}
        >
            <span className="text-slate-900">{first}</span>
            <span className="mx-2 font-normal text-slate-300">/</span>
            <span className={VALUE_TONE[secondTone] || VALUE_TONE.neutral}>{second}</span>
        </div>
        {hint ? <div className="text-[12px] leading-snug text-slate-400">{hint}</div> : null}
    </div>
);

/*
 * Цвета статусов заданы владельцем: перерыв — оранжевый, тренинг — зелёный,
 * тех.причина — фиолетовый. Перезвон владелец не называл; взяли синий (акцент приложения),
 * чтобы он не сливался с тремя остальными.
 */
const STATUS_STYLE = {
    break: { label: 'Перерыв', dot: 'bg-orange-500', value: 'text-orange-600', chip: 'bg-orange-100 text-orange-700' },
    training: { label: 'Тренинг', dot: 'bg-emerald-500', value: 'text-emerald-600', chip: 'bg-emerald-100 text-emerald-700' },
    tech: { label: 'Тех.причина', dot: 'bg-violet-500', value: 'text-violet-600', chip: 'bg-violet-100 text-violet-700' },
    recall: { label: 'Перезвон', dot: 'bg-blue-500', value: 'text-blue-600', chip: 'bg-blue-100 text-blue-700' },
};

const STATUS_ORDER = ['break', 'training', 'tech', 'recall'];

/*
 * Разбивка по причинам — вспомогательная информация, а не главные цифры табло,
 * поэтому это одна тонкая полоса с разделителями, а не четыре крупные карточки.
 * Точка держит цвет статуса всегда, само число красим только когда оно не ноль:
 * ноль — это не сигнал, и подсвечивать его незачем.
 */
const StatusStrip = ({ now, scale = 1 }) => {
    const valueOf = {
        break: now.operators_on_break,
        training: now.operators_on_training,
        tech: now.operators_on_tech,
        recall: now.operators_on_recall,
    };
    return (
        <div className={`grid grid-cols-4 divide-x ${HAIRLINE} ${HAIRLINE_TOP}`}>
            {STATUS_ORDER.map((key) => {
                const style = STATUS_STYLE[key];
                const value = Number(valueOf[key]) || 0;
                return (
                    <div key={key} className="flex flex-col items-center gap-1 px-2 py-2.5">
                        <span className="flex items-center gap-1.5 text-[12px] font-medium text-slate-500">
                            <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
                            <span className="truncate">{style.label}</span>
                        </span>
                        <span
                            className={`font-semibold tabular-nums leading-none ${value > 0 ? style.value : 'text-slate-300'}`}
                            style={{ fontSize: `clamp(${(1.125 * scale).toFixed(3)}rem, ${(1.4 * scale).toFixed(2)}vw, ${(1.5 * scale).toFixed(3)}rem)` }}
                        >
                            {formatInt(value)}
                        </span>
                    </div>
                );
            })}
        </div>
    );
};

/** Список операторов в статусе: кто, по какой причине и сколько уже в ней сидит. */
const OperatorList = ({ title, icon, entries, scale = 1, showReason = false }) => {
    const items = Array.isArray(entries) ? entries : [];
    return (
        <div className={`${iosCard} flex min-h-0 flex-col p-4`}>
            <div className="mb-2.5 flex items-center justify-between gap-2">
                <div className={`flex items-center gap-2 ${LABEL_CLASS}`}>
                    <FaIcon className={`fas ${icon}`}></FaIcon>
                    <span>{title}</span>
                </div>
                <span className="text-[15px] font-semibold tabular-nums text-slate-900">{items.length}</span>
            </div>
            {items.length === 0 ? (
                <div className="py-2 text-[14px] text-slate-400">Никого</div>
            ) : (
                <ul className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => {
                        const style = STATUS_STYLE[item.reason_key] || STATUS_STYLE.break;
                        return (
                            <li
                                key={`${item.operator_id ?? item.name}-${item.since ?? ''}`}
                                className="flex items-center justify-between gap-3 py-2"
                                style={{ fontSize: `clamp(0.875rem, ${(1.05 * scale).toFixed(2)}vw, ${(1.125 * scale).toFixed(3)}rem)` }}
                            >
                                <span className="flex min-w-0 items-center gap-2">
                                    <span className="truncate text-slate-700">{item.name}</span>
                                    {showReason ? (
                                        <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium ${style.chip}`}>
                                            {item.reason}
                                        </span>
                                    ) : null}
                                </span>
                                <span className="shrink-0 font-semibold tabular-nums text-slate-500">
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

/** Само табло. Выделено в компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
const WallboardBody = ({ snapshot, scale }) => {
    const now = snapshot?.now || {};
    const today = snapshot?.today || {};
    const slThreshold = Number(snapshot?.sl_threshold_seconds) || 20;

    // Настоящая тревога — люди ждут, а взять звонок некому. Сигналим только на очереди:
    // у «Свободны» теперь свой зелёный фон, и красное число на нём читалось бы как спор
    // цветов, а дублировать одну тревогу в двух местах незачем.
    const nobodyFree = Number(now.operators_free) === 0;
    const queueTone = Number(now.queue) > 0 && nobodyFree ? 'bad' : 'neutral';

    return (
        <div className="space-y-3" style={{ fontFamily: APPLE_FONT }}>
            {/* Сейчас на линии — по этим трём цифрам принимают решение сию секунду. */}
            <Panel>
                <Row cols="grid-cols-1 sm:grid-cols-3">
                    <Cell
                        label="Звонков в очереди"
                        value={formatInt(now.queue)}
                        hint="Ждут ответа оператора"
                        tone={queueTone}
                        size="hero"
                        scale={scale}
                    />
                    <Cell
                        label="SL"
                        value={formatPercent(today.sl_ratio)}
                        hint={`Ответы за ≤ ${slThreshold} с · норма от ${Math.round(SL_GOOD_RATIO * 100)}%`}
                        tone={slTone(today.sl_ratio)}
                        size="hero"
                        scale={scale}
                    />
                    <Cell
                        label="AR на текущий момент"
                        value={formatPercent(today.ar_ratio)}
                        hint={`Норма ${String(AR_TARGET_MIN_PERCENT).replace('.', ',')}–${String(AR_TARGET_MAX_PERCENT).replace('.', ',')}%`}
                        tone={arTone(today.ar_ratio)}
                        size="hero"
                        scale={scale}
                    />
                </Row>
            </Panel>

            {/* Звонки с начала дня: первый ряд — сколько, второй — как долго ждали. */}
            <Panel>
                <Row cols="grid-cols-1 sm:grid-cols-2">
                    <PairCell
                        label="Входящих / Принято"
                        first={formatInt(today.total)}
                        second={formatInt(today.served)}
                        secondTone={arTone(today.ar_ratio)}
                        hint={`Из них ${formatInt(today.greet_drop)} сброшено на приветствии`}
                        size="hero"
                        scale={scale}
                    />
                    <Cell label="Потеряно" value={formatInt(today.lost)} hint="Не дождались в очереди" size="hero" scale={scale} />
                </Row>
                <Row cols="grid-cols-1 sm:grid-cols-2" divided>
                    <Cell
                        label="Среднее ожидание"
                        value={formatDuration(today.avg_wait_seconds)}
                        hint="В очереди, по всем дошедшим"
                        size="hero"
                        scale={scale}
                    />
                    <Cell
                        label="Среднее время разговора"
                        value={formatDuration(today.avg_talk_seconds)}
                        hint="По принятым звонкам"
                        size="hero"
                        scale={scale}
                    />
                </Row>
            </Panel>

            {/* Операторы: сколько людей и сколько из них могут взять звонок, ниже — причины. */}
            <Panel>
                <Row cols="grid-cols-1 sm:grid-cols-3">
                    <Cell label="Свободны" value={formatInt(now.operators_free)} hint="Готовы принять звонок" icon="fa-user-check" size="hero" bg="bg-emerald-50" scale={scale} />
                    <Cell label="В разговоре" value={formatInt(now.operators_talking)} hint="Заняты звонком" icon="fa-headset" size="hero" bg="bg-amber-50" scale={scale} />
                    <Cell label="Онлайн" value={formatInt(now.operators_online)} hint="Свободны + в разговоре" icon="fa-users" size="hero" scale={scale} />
                </Row>
                <StatusStrip now={now} scale={scale} />
            </Panel>

            {Number(now.operators_other) > 0 ? (
                <div className="px-1 text-[12px] text-slate-400">
                    Прочие статусы (резерв, нет на месте): {formatInt(now.operators_other)}
                </div>
            ) : null}

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <OperatorList title="Перерывы" icon="fa-mug-hot" entries={now.break_list} scale={scale} showReason />
                <OperatorList title="Перезвон" icon="fa-phone-volume" entries={now.recall_list} scale={scale} />
            </div>
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
                <h1 className="text-[22px] font-semibold text-slate-900">Табло СЗоВ</h1>
                <p className="mt-0.5 text-[13px] text-slate-500">
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
