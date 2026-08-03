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
 *   - три уровня размера цифр (hero / md / sm) задают иерархию: что важно оперативно,
 *     видно с другого конца зала, итоги дня — спокойнее;
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

// Размеры цифр. Иерархия важнее абсолютных значений: hero читается через зал,
// sm — это итоги дня, которые смотрят вблизи.
const VALUE_SIZE = {
    hero: [2.5, 4.6, 4.75],
    md: [2, 3.2, 3.5],
    sm: [1.625, 2.2, 2.375],
};

const valueFontSize = (size, scale) => {
    const [min, mid, max] = VALUE_SIZE[size] || VALUE_SIZE.md;
    return `clamp(${(min * scale).toFixed(3)}rem, ${(mid * scale).toFixed(2)}vw, ${(max * scale).toFixed(3)}rem)`;
};

const LABEL_CLASS = 'text-[12px] font-semibold uppercase tracking-wide text-slate-500';
const SECTION_LABEL_CLASS = 'mb-2.5 px-0.5 text-[13px] font-semibold uppercase tracking-wider text-slate-400';

const Tile = ({ label, value, hint, tone = 'neutral', size = 'md', scale = 1 }) => (
    <div className={`${iosCard} flex flex-col gap-1.5 p-4`}>
        <div className={LABEL_CLASS}>{label}</div>
        <div
            className={`font-semibold tabular-nums leading-[1.05] ${VALUE_TONE[tone] || VALUE_TONE.neutral}`}
            style={{ fontSize: valueFontSize(size, scale) }}
        >
            {value}
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
    break: { label: 'Перерыв', card: 'bg-orange-50 ring-orange-200/80', value: 'text-orange-600', chip: 'bg-orange-100 text-orange-700' },
    training: { label: 'Тренинг', card: 'bg-emerald-50 ring-emerald-200/80', value: 'text-emerald-600', chip: 'bg-emerald-100 text-emerald-700' },
    tech: { label: 'Тех.причина', card: 'bg-violet-50 ring-violet-200/80', value: 'text-violet-600', chip: 'bg-violet-100 text-violet-700' },
    recall: { label: 'Перезвон', card: 'bg-blue-50 ring-blue-200/80', value: 'text-blue-600', chip: 'bg-blue-100 text-blue-700' },
};

const StatusTile = ({ statusKey, value, scale = 1 }) => {
    const style = STATUS_STYLE[statusKey];
    return (
        <div className={`rounded-2xl p-4 ring-1 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${style.card}`}>
            <div className="text-[12px] font-semibold uppercase tracking-wide text-slate-600">{style.label}</div>
            <div
                className={`mt-1.5 font-semibold tabular-nums leading-[1.05] ${style.value}`}
                style={{ fontSize: valueFontSize('md', scale) }}
            >
                {formatInt(value)}
            </div>
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

    // Красим ожидание «сейчас» только когда порог SL уже пробит — это настоящее отклонение.
    const waitNow = Number(now.queue_max_wait_seconds) || 0;
    const waitTone = waitNow > slThreshold ? 'bad' : 'neutral';
    const queueTone = Number(now.queue) > 0 && Number(now.operators_free) === 0 ? 'bad' : 'neutral';

    return (
        <div className="space-y-6" style={{ fontFamily: APPLE_FONT }}>
            <section>
                <div className={SECTION_LABEL_CLASS}>Сейчас на линии</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <Tile
                        label="Звонков в очереди"
                        value={formatInt(now.queue)}
                        hint="Ждут ответа оператора"
                        tone={queueTone}
                        size="hero"
                        scale={scale}
                    />
                    <Tile
                        label="Максимальное ожидание"
                        value={formatDuration(waitNow)}
                        hint={`Самый долгий в очереди · порог ${slThreshold} с`}
                        tone={waitTone}
                        size="hero"
                        scale={scale}
                    />
                    <Tile
                        label="AR на текущий момент"
                        value={formatPercent(today.ar_ratio)}
                        hint={`Норма ${String(AR_TARGET_MIN_PERCENT).replace('.', ',')}–${String(AR_TARGET_MAX_PERCENT).replace('.', ',')}%`}
                        tone={arTone(today.ar_ratio)}
                        size="hero"
                        scale={scale}
                    />
                </div>
            </section>

            <section>
                <div className={SECTION_LABEL_CLASS}>Операторы</div>
                <div className="grid grid-cols-3 gap-3">
                    <Tile label="Онлайн" value={formatInt(now.operators_online)} hint="Всего на линии" scale={scale} />
                    <Tile label="В разговоре" value={formatInt(now.operators_talking)} hint="Заняты звонком" scale={scale} />
                    <Tile label="Свободны" value={formatInt(now.operators_free)} hint="Готовы принять" scale={scale} />
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <StatusTile statusKey="break" value={now.operators_on_break} scale={scale} />
                    <StatusTile statusKey="training" value={now.operators_on_training} scale={scale} />
                    <StatusTile statusKey="tech" value={now.operators_on_tech} scale={scale} />
                    <StatusTile statusKey="recall" value={now.operators_on_recall} scale={scale} />
                </div>
                {Number(now.operators_other) > 0 ? (
                    <div className="mt-2 px-1 text-[12px] text-slate-400">
                        Прочие статусы (резерв, нет на месте): {formatInt(now.operators_other)}
                    </div>
                ) : null}
            </section>

            <section>
                <div className={SECTION_LABEL_CLASS}>Звонки с начала дня</div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <Tile
                        label="Всего входящих"
                        value={formatInt(today.total)}
                        hint={`Из них ${formatInt(today.greet_drop)} сброшено на приветствии`}
                        size="sm"
                        scale={scale}
                    />
                    <Tile label="Принято" value={formatInt(today.served)} hint="Ответил оператор" size="sm" scale={scale} />
                    <Tile label="Потеряно" value={formatInt(today.lost)} hint="Не дождались в очереди" size="sm" scale={scale} />
                    <Tile
                        label="Среднее ожидание"
                        value={formatDuration(today.avg_wait_seconds)}
                        hint="В очереди, по всем дошедшим"
                        size="sm"
                        scale={scale}
                    />
                    <Tile
                        label="Максимальное за день"
                        value={formatDuration(today.max_wait_seconds)}
                        hint="Самое долгое ожидание"
                        size="sm"
                        scale={scale}
                    />
                </div>
            </section>

            <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <OperatorList title="Перерывы" icon="fa-mug-hot" entries={now.break_list} scale={scale} showReason />
                <OperatorList title="Перезвон" icon="fa-phone-volume" entries={now.recall_list} scale={scale} />
            </section>
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
