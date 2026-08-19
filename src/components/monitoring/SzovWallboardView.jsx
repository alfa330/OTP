import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosBadge, IosModal, IosToggle } from '../ui/ios';
import {
    AR_MAX_PERCENT,
    AR_MIN_PERCENT,
    SL_GOOD_RATIO,
    STATUS_STYLE,
    WALLBOARD_METRIC_MAP,
    WALLBOARD_TONE_TEXT,
    canOpenWallboardWidget,
    formatClock,
    formatDuration,
    formatInt,
    readWallboardMetric,
    useSzovChatWallboardSnapshot,
    useSzovWallboardSnapshot,
    wallboardStaleNotice,
} from './szovWallboardShared';
import SzovChatWallboardBody from './SzovChatWallboard';
import { IosDateRangeCalendar, isoDate, rangeLabel } from '../ui/DateRangePicker';
import { Grid, KeyTile, Section, SegmentedSwitch, StatTile } from './SzovWallboardTiles';

/*
 * «Табло СЗоВ» — онлайн-мониторинг входящей линии (задача #108) и чатов.
 *
 * Направлений два, переключатель в шапке: «Основа» (входящая линия Oktell, весь код этого
 * файла) и «Чат» (Chat2Desk, SzovChatWallboard.jsx). Общие у них шапка, полноэкранный режим
 * и механика опроса; всё остальное у каждого своё, потому что и источники разные.
 *
 * Экран рассчитан на вывод на стену, поэтому:
 *   - обновляется сам, без перезагрузки страницы (опрос раз в 15 с, один общий снапшот);
 *   - показатели собраны в сплошные панели без зазоров: соседние ячейки делит волосяная
 *     линия, поэтому цифрам достаётся вся ширина и подписи рядов не нужны;
 *   - цветом помечаем только то, что несёт смысл: AR вне целевого коридора и статусы
 *     операторов (у них цвет задан владельцем, чтобы различать причины с расстояния).
 *
 * Все показатели считает бэкенд (/api/szov_wallboard/snapshot) по формулам «Биллинга Oktell»,
 * чтобы табло и отчёт не расходились в цифрах. Названия, значения и цвета живут в каталоге
 * szovWallboardShared — тот же каталог питает виджет «поверх окон», где каждый выбирает свой
 * набор показателей; здесь остаётся только раскладка стены.
 */

const FULLSCREEN_Z = 150;

/*
 * Плитки берут показатель из каталога: подпись, значение и тон приходят оттуда, а раскладку
 * (размер цифр, фон, отступы) держат общие кирпичи табло — те же, что у направления «Чат».
 * Поэтому цифра на стене и та же цифра в виджете разойтись не могут, а два направления
 * одного экрана не разъедутся в оформлении.
 */
const MetricKeyTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = WALLBOARD_METRIC_MAP[metricKey];
    const { value, tone } = readWallboardMetric(metric, snapshot);
    return <KeyTile label={metric.label} value={value} hint={metric.hint} tone={tone} scale={scale} />;
};

/** Плитка дня/операторов. Показатель-пара («Принято / входящих») отдаёт второе число приглушённым. */
const MetricStatTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = WALLBOARD_METRIC_MAP[metricKey];
    const { value, secondary, tone } = readWallboardMetric(metric, snapshot);
    return <StatTile label={metric.label} value={value} secondary={secondary} tone={tone} scale={scale} />;
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

/*
 * Режимы получателя. Оба стоят в одном расписании — разница только в том, при каких
 * показателях сообщение уходит. «Только при отклонениях» нужен руководству: писать ему
 * каждые четыре часа, когда всё в норме, — тот же шум, что и молчать, когда всё плохо.
 */
const BROADCAST_MODES = [
    { key: 'always', label: 'Каждую отбивку', hint: 'Во все часы расписания' },
    { key: 'deviations', label: 'Только при отклонениях', hint: 'То же расписание, но письмо уходит, только если показатели вне нормы' },
];

const modeLabel = (key) => (BROADCAST_MODES.find((mode) => mode.key === key) || BROADCAST_MODES[0]).label;

const ModeSwitch = (props) => <SegmentedSwitch options={BROADCAST_MODES} {...props} />;

/** Строка истории «кто менял» человеческим языком. */
const historyLine = (settings) => {
    const title = settings?.chat_title || settings?.chat_id || 'чат';
    const mode = modeLabel(settings?.mode).toLowerCase();
    const state = settings?.is_enabled ? 'отправка включена' : 'отправка выключена';
    if (settings?.action === 'added') return `добавил чат «${title}» — ${mode}`;
    if (settings?.action === 'removed') return `убрал чат «${title}»`;
    if (settings?.action === 'changed') return `изменил чат «${title}» — ${mode}, ${state}`;
    // Записи, сделанные до перехода на список получателей: там был один чат и один тумблер.
    return `${settings?.is_enabled ? 'включил отправку' : 'выключил отправку'}`
        + (settings?.chat_title ? `, чат «${settings.chat_title}»` : '');
};

/*
 * Настройка отбивки показателей в Telegram. Живёт в модалке, а не на самом табло:
 * табло смотрят, а не настраивают, и форма поверх экрана, который висит на стене, — лишний шум.
 */
const BroadcastModal = ({ open, onClose, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const [state, setState] = useState(null);
    const [busy, setBusy] = useState(false);
    const [draftChat, setDraftChat] = useState('');
    const [draftMode, setDraftMode] = useState('always');
    const [historyOpen, setHistoryOpen] = useState(false);
    const headersRef = useRef(withAccessTokenHeader);
    headersRef.current = withAccessTokenHeader;
    // showToast приходит новой функцией на каждый рендер родителя (а он перерисовывается
    // раз в 15 с по опросу табло). В зависимостях эффекта это дало бы лишний GET на каждый
    // тик, поэтому держим её в ref.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const request = useCallback(async (method, body) => {
        const build = headersRef.current;
        const headers = build ? build({ Accept: 'application/json' }) : { Accept: 'application/json' };
        if (body) headers['Content-Type'] = 'application/json';
        const response = await fetch(`${apiBaseUrl}/api/szov_wallboard/broadcast`, {
            method,
            headers,
            credentials: 'include',
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `Сервер ответил ${response.status}`);
        return data;
    }, [apiBaseUrl]);

    // Список тянем при каждом открытии: бота могли добавить в новую группу, пока модалка закрыта.
    useEffect(() => {
        if (!open) return undefined;
        let cancelled = false;
        request('GET')
            .then((data) => { if (!cancelled) setState(data); })
            .catch((error) => {
                if (!cancelled) toastRef.current?.(error.message || 'Не удалось загрузить настройку', 'error');
            });
        return () => { cancelled = true; };
    }, [open, request]);

    const save = async (method, body, successText) => {
        setBusy(true);
        try {
            setState(await request(method, body));
            if (successText) showToast?.(successText, 'success');
        } catch (error) {
            showToast?.(error.message || 'Не удалось сохранить', 'error');
        } finally {
            setBusy(false);
        }
    };

    const sendNow = async (chatId) => {
        setBusy(true);
        try {
            const build = headersRef.current;
            const headers = build ? build({ Accept: 'application/json' }) : { Accept: 'application/json' };
            headers['Content-Type'] = 'application/json';
            const response = await fetch(`${apiBaseUrl}/api/szov_wallboard/broadcast_test`, {
                method: 'POST',
                headers,
                credentials: 'include',
                body: JSON.stringify({ chat_id: chatId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data?.error || `Сервер ответил ${response.status}`);
            showToast?.('Отбивка отправлена', 'success');
        } catch (error) {
            showToast?.(error.message || 'Не удалось отправить', 'error');
        } finally {
            setBusy(false);
        }
    };

    const recipients = state?.recipients || [];
    const chats = state?.chats || [];
    const sendTimes = state?.send_times || [];

    // Чат, который уже получает отбивку, второй раз не предлагаем: дублей быть не должно.
    const available = useMemo(() => {
        const taken = new Set(recipients.map((item) => String(item.chat_id)));
        return chats.filter((chat) => !taken.has(String(chat.chat_id)));
    }, [chats, recipients]);

    const addRecipient = () => {
        const picked = available.find((chat) => String(chat.chat_id) === draftChat);
        if (!picked) return;
        setDraftChat('');
        setDraftMode('always');
        save('POST', {
            chat_id: picked.chat_id,
            chat_title: picked.title || picked.username || String(picked.chat_id),
            mode: draftMode,
            is_enabled: true,
        }, 'Группа добавлена');
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Отбивка показателей"
            subtitle={`Расписание ${sendTimes.join(', ') || '—'} · Алматы`}
            maxWidth="max-w-3xl"
            footer={<button type="button" className={iosBtnSecondary} onClick={onClose}>Готово</button>}
        >
            {!state ? (
                <div className="py-6 text-center text-[13px] text-slate-500">Загружаем настройку…</div>
            ) : (
                <div className="space-y-5">
                    <div className={`${iosCard} divide-y divide-slate-100`}>
                        {recipients.length === 0 ? (
                            <div className="px-4 py-5 text-[13.5px] text-slate-500">
                                Отбивка никуда не уходит: получателей пока нет.
                            </div>
                        ) : recipients.map((item) => (
                            <div key={item.chat_id} className="flex flex-wrap items-center gap-3 px-4 py-3.5">
                                <div className="min-w-[10rem] flex-1">
                                    <div className="flex items-center gap-2">
                                        <span className="truncate text-[14px] font-medium text-slate-900">
                                            {item.chat_title || item.chat_id}
                                        </span>
                                        {item.is_enabled ? null : <IosBadge tone="slate">выключена</IosBadge>}
                                    </div>
                                    <div className="mt-0.5 text-[12px] text-slate-400">
                                        {[item.updated_by_name, String(item.updated_at || '').replace('T', ' ').slice(0, 16)]
                                            .filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                                <ModeSwitch
                                    value={item.mode}
                                    disabled={busy}
                                    onChange={(mode) => save('POST', { chat_id: item.chat_id, mode }, 'Режим изменён')}
                                />
                                <IosToggle
                                    checked={Boolean(item.is_enabled)}
                                    disabled={busy}
                                    onChange={(next) => save(
                                        'POST',
                                        { chat_id: item.chat_id, is_enabled: next },
                                        next ? 'Отправка включена' : 'Отправка выключена',
                                    )}
                                />
                                <button
                                    type="button"
                                    className={iosBtnGhost}
                                    disabled={busy}
                                    title="Отправить в этот чат прямо сейчас"
                                    onClick={() => sendNow(item.chat_id)}
                                >
                                    <FaIcon className="fas fa-paper-plane"></FaIcon>
                                </button>
                                <button
                                    type="button"
                                    className={`${iosBtnGhost} text-rose-500 hover:bg-rose-50`}
                                    disabled={busy}
                                    title="Убрать группу из получателей"
                                    onClick={() => save('DELETE', { chat_id: item.chat_id }, 'Группа убрана')}
                                >
                                    <FaIcon className="fas fa-trash"></FaIcon>
                                </button>
                            </div>
                        ))}
                    </div>

                    <div className={`${iosCard} space-y-3 p-4`}>
                        <div className="text-[13px] font-semibold text-slate-500">Добавить группу</div>
                        <div className="flex flex-wrap items-end gap-3">
                            <label className="min-w-[14rem] flex-1">
                                <div className="mb-1.5 text-[12.5px] text-slate-500">Чат</div>
                                <select
                                    className={iosInput}
                                    disabled={busy || available.length === 0}
                                    value={draftChat}
                                    onChange={(event) => setDraftChat(event.target.value)}
                                >
                                    <option value="">Выберите чат</option>
                                    {available.map((chat) => (
                                        <option key={chat.chat_id} value={chat.chat_id}>
                                            {chat.title || chat.username || chat.chat_id}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <div>
                                <div className="mb-1.5 text-[12.5px] text-slate-500">Когда отправлять</div>
                                <ModeSwitch value={draftMode} disabled={busy} onChange={setDraftMode} />
                            </div>
                            <button type="button" className={iosBtnPrimary} disabled={busy || !draftChat} onClick={addRecipient}>
                                <FaIcon className="fas fa-plus"></FaIcon>
                                Добавить
                            </button>
                        </div>
                        {chats.length === 0 ? (
                            <div className="text-[12.5px] text-amber-700">
                                Список пуст: добавьте бота в нужную группу — он появится здесь сам.
                            </div>
                        ) : available.length === 0 ? (
                            <div className="text-[12.5px] text-slate-400">
                                Все известные боту группы уже получают отбивку.
                            </div>
                        ) : null}
                    </div>

                    <div className="px-1 text-[12.5px] leading-relaxed text-slate-500">
                        Отклонением считаем то же, что подсвечено на табло: AR вне коридора{' '}
                        {AR_MIN_PERCENT}–{AR_MAX_PERCENT}%, SL ниже {Math.round(SL_GOOD_RATIO * 100)}%
                        {' '}или Oktell не отвечает и цифры на табло замерли.
                    </div>

                    <div>
                        <button type="button" className={iosBtnGhost} onClick={() => setHistoryOpen((value) => !value)}>
                            <FaIcon className={`fas ${historyOpen ? 'fa-chevron-up' : 'fa-chevron-down'}`}></FaIcon>
                            Кто менял
                        </button>
                        {historyOpen ? (
                            (state.history || []).length === 0 ? (
                                <div className="px-1 py-2 text-[13px] text-slate-400">Изменений пока не было</div>
                            ) : (
                                <ul className="mt-1 divide-y divide-slate-100 text-[13px]">
                                    {(state.history || []).map((item) => (
                                        <li key={`${item.changed_at}-${item.changed_by}`} className="flex flex-wrap justify-between gap-2 py-2">
                                            <span className="text-slate-700">
                                                {item.changed_by_name || 'Неизвестно'}
                                                <span className="text-slate-400">{' · '}{historyLine(item.settings)}</span>
                                            </span>
                                            <span className="tabular-nums text-slate-400">
                                                {String(item.changed_at || '').replace('T', ' ').slice(0, 16)}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )
                        ) : null}
                    </div>
                </div>
            )}
        </IosModal>
    );
};

/** Само табло. Выделено в компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
const WallboardBody = ({ snapshot, scale }) => {
    const now = snapshot?.now || {};

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
                    <MetricKeyTile metricKey="queue" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="ar_ratio" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="operators_online" snapshot={snapshot} scale={scale} />
                    <MetricKeyTile metricKey="operators_on_break" snapshot={snapshot} scale={scale} />
                </Grid>
            </Section>

            <Section icon="fa-chart-bar" title="Показатели за день">
                <Grid>
                    <MetricStatTile metricKey="served_pair" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="lost" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="sl_ratio" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="avg_wait_seconds" snapshot={snapshot} scale={scale} />
                </Grid>
            </Section>

            <Section icon="fa-headset" title="Операторы">
                <Grid>
                    <MetricStatTile metricKey="operators_free" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="operators_talking" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="avg_talk_seconds" snapshot={snapshot} scale={scale} />
                    <MetricStatTile metricKey="operators_on_recall" snapshot={snapshot} scale={scale} />
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

/*
 * Направления табло. «Основа» — входящая линия Oktell, «Чат» — Chat2Desk: разные источники,
 * разный темп опроса и разный набор показателей, поэтому каждое направление живёт своим
 * компонентом со своим снимком. Рисуется всегда только выбранное — иначе закрытое направление
 * продолжало бы опрашивать свой источник впустую (а квота Chat2Desk общая на компанию).
 */
const DIRECTIONS = [
    { key: 'osnova', label: 'Основа', hint: 'Входящая линия: Oktell' },
    { key: 'chat', label: 'Чат', hint: 'Чаты: Chat2Desk' },
];

const directionStorageKey = (userId) => `otp:szov-wallboard-direction${userId ? `:${userId}` : ''}`;

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
        // Выбор направления — предпочтение браузера: не сохранилось, откроется «Основа».
    }
};

/*
 * Шапка у обоих направлений одна: заголовок, переключатель, пометка о замерших данных, кнопки.
 * Различаются только подпись под заголовком и кнопки, которые есть лишь у «Основы» (отбивка и
 * виджет собраны по показателям линии).
 */
const WallboardHeader = ({
    subtitle, direction, onDirectionChange, staleNotice, loading, onRefresh, onFullscreen,
    widgetOpen, onToggleWidget, children,
}) => (
    <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
            <h1 className="text-[26px] font-semibold text-slate-900">Табло СЗоВ</h1>
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
            {children}
            {/* Виджет — отдельное окно поверх других окон, и оно остаётся открытым после ухода
                из раздела: он для того и нужен, чтобы следить за линией или чатами, занимаясь
                другим. Набор показателей выбирается в самом окне, у каждого направления свой. */}
            {onToggleWidget ? <WidgetButton direction={direction} widgetOpen={widgetOpen}
                                            onToggleWidget={onToggleWidget} /> : null}
            <button type="button" className={iosBtnGhost} onClick={onFullscreen}>
                <FaIcon className="fas fa-expand"></FaIcon>
                На весь экран
            </button>
        </div>
    </div>
);

/*
 * Кнопка виджета. Открытое окно всегда одно, поэтому кнопка «зажата» только у того направления,
 * чей виджет сейчас открыт; нажатие на соседнем направлении переоткрывает окно под него.
 */
const WidgetButton = ({ direction, widgetOpen, onToggleWidget }) => {
    // Умеет ли браузер окно поверх других, выясняем здесь, чтобы кнопка не обещала того, чего не будет.
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

/** Пустые состояния (грузим / источник молчит) — одни на оба направления. */
const WallboardPlaceholder = ({ header, message, tone = 'muted' }) => (
    <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
        {header}
        <div className={`${iosCard} p-6 text-[13px] ${tone === 'error' ? 'text-rose-600' : 'text-slate-500'}`}>
            {message}
        </div>
    </div>
);

/** Направление «Основа»: входящая линия Oktell. */
const LineWallboard = ({
    apiBaseUrl, withAccessTokenHeader, showToast, canManageBroadcast, widgetOpen, onToggleWidget,
    direction, onDirectionChange,
}) => {
    // Снимок и опрос общие с виджетом: один запрос к Oktell на оба экрана, одни цифры в обоих.
    const { snapshot, error, loading, refresh } = useSzovWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);
    const [broadcastOpen, setBroadcastOpen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error), [error, snapshot]);

    const header = (
        <WallboardHeader
            subtitle={`Входящая линия в реальном времени${
                snapshot?.oktell_now ? ` · данные Oktell на ${formatClock(snapshot.oktell_now)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
        >
            {canManageBroadcast ? (
                <button type="button" className={iosBtnGhost} onClick={() => setBroadcastOpen(true)}>
                    <FaIcon className="fas fa-paper-plane"></FaIcon>
                    Отбивка
                </button>
            ) : null}
        </WallboardHeader>
    );

    /* Через портал, как и полноэкранный режим: модалка не должна зависеть от вертикальных
       отступов шапки, а шапка рисуется во всех трёх состояниях экрана. */
    const broadcastModal = canManageBroadcast ? createPortal(
        <BroadcastModal
            open={broadcastOpen}
            onClose={() => setBroadcastOpen(false)}
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            showToast={showToast}
        />,
        document.body,
    ) : null;

    if (!snapshot) {
        return (
            <>
                <WallboardPlaceholder
                    header={header}
                    message={loading ? 'Загружаем данные Oktell…' : (error || 'Данные недоступны')}
                    tone={loading ? 'muted' : 'error'}
                />
                {broadcastModal}
            </>
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            {broadcastModal}
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
};

/*
 * Имя файла выгрузки: одна дата на однодневной выгрузке, обе — на периоде. У сегодняшнего дня
 * в имени стоит ещё и момент ДАННЫХ, а не скачивания: две выгрузки подряд в пределах кэша
 * содержат один и тот же снимок и различаться именами не должны. Имя собирает фронт, а не
 * сервер: Content-Disposition через CORS сюда не доходит (в Expose-Headers его нет).
 */
const chatExportFileName = (snapshot, from, to) => {
    const day = (value) => String(value || '').replace(/-/g, '');
    if (from && to && from !== to) return `szov_wallboard_chat_${day(from)}_${day(to)}.xlsx`;
    const single = day(from || to || snapshot?.day);
    const clock = single === day(snapshot?.day)
        ? String(snapshot?.chat2desk_now || '').slice(11, 16).replace(':', '')
        : '';
    const stamp = [single, clock].filter(Boolean).join('_');
    return `szov_wallboard_chat_${stamp || 'now'}.xlsx`;
};

/*
 * Пресеты периода. «Весь период» здесь не годится: выгрузка качает Chat2Desk по дню на день,
 * и пустой диапазон означал бы «за всё время», чего никто не дождётся.
 */
const chatExportPresets = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: '7 дней', range: () => ({ from: isoDate(new Date(Date.now() - 6 * 864e5)), to: isoDate(new Date()) }) },
    { label: '30 дней', range: () => ({ from: isoDate(new Date(Date.now() - 29 * 864e5)), to: isoDate(new Date()) }) },
];

// Тот же потолок, что на сервере (SZOV_CHAT_EXPORT_MAX_DAYS): просить больше бессмысленно —
// придёт 400. Проверяем и здесь, чтобы кнопка гасла до запроса, а не после минуты ожидания.
const CHAT_EXPORT_MAX_DAYS = 31;

const rangeDays = (from, to) => {
    if (!from || !to) return 1;
    return Math.round((new Date(to) - new Date(from)) / 864e5) + 1;
};

/*
 * Выгрузка показателей направления «Чат» в Excel за выбранный период. Календарь — тот же, что в
 * «Чатах ChatApp», но раскрывается из самой кнопки «Выгрузить», а под ним стоит «Подтвердить»
 * (так попросил владелец): на стене табло чипу с датой места нет, а выгрузка — действие редкое,
 * и период выбирается ровно в момент, когда он нужен.
 * Сегодняшний день сервер берёт из кэша табло (цифры совпадают с экраном и ни одного лишнего
 * запроса к Chat2Desk), прошедшие дни качает по дню на день — поэтому долгий период честно
 * предупреждает о времени под кнопкой.
 */
const ChatExportControls = ({ apiBaseUrl, withAccessTokenHeader, showToast, snapshot }) => {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [range, setRange] = useState(() => ({ from: isoDate(new Date()), to: isoDate(new Date()) }));
    const ref = useRef(null);
    // showToast приходит новой функцией на каждый рендер родителя, а он перерисовывается раз в
    // 15 с по опросу табло: держим её в ref, чтобы не тянуть в зависимости.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const snapshotRef = useRef(snapshot);
    snapshotRef.current = snapshot;

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
    const tooLong = days > CHAT_EXPORT_MAX_DAYS;

    const download = useCallback(async (from, to) => {
        setOpen(false);
        setBusy(true);
        try {
            const build = withAccessTokenHeader;
            const query = new URLSearchParams();
            if (from) query.set('date_from', from);
            if (to) query.set('date_to', to);
            const response = await fetch(
                `${apiBaseUrl}/api/szov_wallboard/chat_export?${query.toString()}`,
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
            link.download = chatExportFileName(snapshotRef.current, from, to);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            toastRef.current?.(`Не удалось выгрузить показатели: ${error?.message || error}`, 'error');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, withAccessTokenHeader]);

    // Сколько ждать, говорим ДО нажатия: сегодня отдаётся из кэша мгновенно, а каждый прошедший
    // день — это отдельная выкачка из Chat2Desk, и на месяце это минуты, а не секунды.
    const hint = tooLong
        ? `Период больше ${CHAT_EXPORT_MAX_DAYS} суток — выберите короче`
        : (days > 1
            ? `Прошедшие дни качаются из Chat2Desk по дню — ${
                days > 7 ? 'это займёт несколько минут' : 'это займёт до минуты'}`
            : 'Сводка, по часам, люди на линии и состав смены');

    return (
        <div ref={ref} className="relative">
            <button type="button" className={`${iosBtnGhost} ${open ? 'bg-slate-100 text-slate-900' : ''}`}
                    onClick={() => setOpen((value) => !value)} disabled={busy}
                    title="Показатели табло в Excel за выбранный период">
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
                        presets={chatExportPresets}
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

/** Направление «Чат»: Chat2Desk. */
const ChatWallboard = ({ apiBaseUrl, withAccessTokenHeader, showToast, direction, onDirectionChange,
                        widgetOpen, onToggleWidget }) => {
    const { snapshot, error, loading, refresh } = useSzovChatWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, 'Chat2Desk'), [error, snapshot]);

    const header = (
        <WallboardHeader
            subtitle={`Чаты в реальном времени${
                snapshot?.chat2desk_now ? ` · данные Chat2Desk на ${formatClock(snapshot.chat2desk_now)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
        >
            {/* Пока снимка нет, выгружать нечего: пикер и кнопка появляются вместе с цифрами. */}
            {snapshot ? (
                <ChatExportControls
                    apiBaseUrl={apiBaseUrl}
                    withAccessTokenHeader={withAccessTokenHeader}
                    showToast={showToast}
                    snapshot={snapshot}
                />
            ) : null}
        </WallboardHeader>
    );

    if (!snapshot) {
        return (
            <WallboardPlaceholder
                header={header}
                message={loading ? 'Загружаем данные Chat2Desk…' : (error || 'Данные недоступны')}
                tone={loading ? 'muted' : 'error'}
            />
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <SzovChatWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon="fa-comments"
                    title="Табло СЗоВ · чаты"
                    subtitle={`${snapshot?.chat2desk_now ? `Данные Chat2Desk на ${formatClock(snapshot.chat2desk_now)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <SzovChatWallboardBody snapshot={snapshot} scale={1.35} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
};

export default function SzovWallboardView(props) {
    const { user, apiBaseUrl, withAccessTokenHeader, showToast, canManageBroadcast, widgetOpen, onToggleWidget } = props;
    const userId = user?.id;
    // Выбор направления запоминаем: чат-менеджеру незачем каждый раз переключаться с линии.
    const [direction, setDirection] = useState(() => readStoredDirection(userId));

    const changeDirection = useCallback((next) => {
        setDirection(next);
        writeStoredDirection(userId, next);
    }, [userId]);

    if (direction === 'chat') {
        return (
            <ChatWallboard
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
        <LineWallboard
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            showToast={showToast}
            canManageBroadcast={canManageBroadcast}
            widgetOpen={widgetOpen}
            onToggleWidget={onToggleWidget}
            direction={direction}
            onDirectionChange={changeDirection}
        />
    );
}
