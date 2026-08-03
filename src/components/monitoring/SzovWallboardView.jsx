import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosGroupLabel, iosBtnGhost } from '../ui/ios';

/*
 * «Табло СЗоВ» — онлайн-мониторинг входящей линии (задача #108).
 *
 * Экран рассчитан на вывод на стену, поэтому:
 *   - обновляется сам, без перезагрузки страницы (опрос раз в 10 с, один общий снапшот);
 *   - цифры крупные и на clamp(), чтобы читались и в сайдбаре, и на телевизоре;
 *   - цветом помечаем ТОЛЬКО отклонения (AR выше нормы, ожидание сверх порога SL).
 *     Нейтральное состояние не красим — иначе табло превращается в светофор из шума.
 *
 * Все показатели считает бэкенд (/api/szov_wallboard/snapshot) по формулам «Биллинга Oktell»,
 * чтобы табло и отчёт не расходились в цифрах.
 */

const POLL_INTERVAL_MS = 10000;
const FULLSCREEN_Z = 150;

// Пороги — отраслевые нормы входящей линии: AR до 5% норма, до 10% терпимо, выше — плохо.
const AR_WARN_RATIO = 0.05;
const AR_BAD_RATIO = 0.10;

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

const TONE_CLASS = {
    neutral: 'text-slate-900',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
};

/**
 * Плитка показателя. Размер задаётся через `scale`, чтобы одна и та же разметка работала
 * и во встроенном виде, и на полном экране (требование «не рендерить тяжёлое дважды»).
 */
const Tile = ({ label, value, hint, tone = 'neutral', scale = 1, wide = false }) => (
    <div className={`${iosCard} flex flex-col justify-between gap-1 p-4 ${wide ? 'sm:col-span-2' : ''}`}>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
        <div
            className={`font-semibold tabular-nums leading-none ${TONE_CLASS[tone] || TONE_CLASS.neutral}`}
            style={{ fontSize: `clamp(${1.5 * scale}rem, ${2.6 * scale}vw, ${3 * scale}rem)` }}
        >
            {value}
        </div>
        {hint ? <div className="text-[11px] leading-tight text-slate-400">{hint}</div> : null}
    </div>
);

/** Список операторов в статусе. Пустое состояние — прочерк, без картинок и подсказок. */
const OperatorList = ({ title, icon, entries, scale = 1 }) => {
    const items = Array.isArray(entries) ? entries : [];
    return (
        <div className={`${iosCard} flex min-h-0 flex-col p-4`}>
            <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    <FaIcon className={`fas ${icon}`}></FaIcon>
                    <span>{title}</span>
                </div>
                <span className="text-[13px] font-semibold tabular-nums text-slate-900">{items.length}</span>
            </div>
            {items.length === 0 ? (
                <div className="py-2 text-[13px] text-slate-400">Никого</div>
            ) : (
                <ul className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => (
                        <li
                            key={`${item.operator_id ?? item.name}-${item.since ?? ''}`}
                            className="flex items-center justify-between gap-3 py-1.5"
                            style={{ fontSize: `clamp(0.8125rem, ${0.95 * scale}vw, ${1.0625 * scale}rem)` }}
                        >
                            <span className="truncate text-slate-700">{item.name}</span>
                            <span className="shrink-0 tabular-nums font-medium text-slate-500">
                                {formatDuration(item.seconds)}
                            </span>
                        </li>
                    ))}
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

    const arRatio = today.ar_ratio;
    const arTone = arRatio == null ? 'neutral' : arRatio >= AR_BAD_RATIO ? 'bad' : arRatio >= AR_WARN_RATIO ? 'warn' : 'neutral';
    // Красим ожидание «сейчас» только когда порог SL уже пробит — это настоящее отклонение.
    const waitNow = Number(now.queue_max_wait_seconds) || 0;
    const waitTone = waitNow > slThreshold ? 'bad' : 'neutral';
    const queueTone = Number(now.queue) > 0 && Number(now.operators_free) === 0 ? 'bad' : 'neutral';

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            <section>
                <div className={`${iosGroupLabel} mb-2`}>Сейчас на линии</div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                    <Tile
                        label="Звонков в очереди"
                        value={formatInt(now.queue)}
                        hint="Ждут ответа оператора"
                        tone={queueTone}
                        scale={scale}
                    />
                    <Tile
                        label="Максимальное ожидание"
                        value={formatDuration(waitNow)}
                        hint={`Самый долгий звонок в очереди · порог ${slThreshold} с`}
                        tone={waitTone}
                        scale={scale}
                    />
                    <Tile
                        label="AR на текущий момент"
                        value={formatPercent(arRatio)}
                        hint="Доля потерянных от дошедших до очереди"
                        tone={arTone}
                        scale={scale}
                    />
                </div>
            </section>

            <section>
                <div className={`${iosGroupLabel} mb-2`}>Звонки с начала дня</div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <Tile
                        label="Всего входящих"
                        value={formatInt(today.total)}
                        hint={`Включая ${formatInt(today.greet_drop)} сброшенных на приветствии`}
                        scale={scale}
                    />
                    <Tile label="Принято" value={formatInt(today.served)} hint="Ответил оператор" scale={scale} />
                    <Tile label="Потеряно" value={formatInt(today.lost)} hint="Не дождались ответа" scale={scale} />
                    <Tile
                        label="Среднее ожидание"
                        value={formatDuration(today.avg_wait_seconds)}
                        hint="В очереди, по всем дошедшим"
                        scale={scale}
                    />
                    <Tile
                        label="Максимальное за день"
                        value={formatDuration(today.max_wait_seconds)}
                        hint="Самое долгое ожидание в очереди"
                        scale={scale}
                    />
                </div>
            </section>

            <section>
                <div className={`${iosGroupLabel} mb-2`}>Операторы</div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <Tile label="Онлайн" value={formatInt(now.operators_online)} hint="На линии в Oktell" scale={scale} />
                    <Tile label="В разговоре" value={formatInt(now.operators_talking)} hint="Заняты звонком" scale={scale} />
                    <Tile label="Свободны" value={formatInt(now.operators_free)} hint="Готовы принять звонок" scale={scale} />
                    <Tile label="На перерыве" value={formatInt(now.operators_on_break)} hint="Перерыв, тренинг, тех.причина" scale={scale} />
                    <Tile label="На перезвоне" value={formatInt(now.operators_on_recall)} hint="Статус «Перезвон»" scale={scale} />
                </div>
                {Number(now.operators_other) > 0 ? (
                    <div className="mt-2 px-1 text-[11px] text-slate-400">
                        Прочие статусы (резерв, нет на месте): {formatInt(now.operators_other)}
                    </div>
                ) : null}
            </section>

            <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <OperatorList title="Перерыв" icon="fa-mug-hot" entries={now.break_list} scale={scale} />
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
                    <WallboardBody snapshot={snapshot} scale={1.6} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
}
