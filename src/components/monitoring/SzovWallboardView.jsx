import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosBadge, IosModal, IosToggle } from '../ui/ios';

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
 * AR — не «чем меньше, тем лучше», а коридор (правило владельца): норма 3…5 %.
 * Ниже 3 % — тоже отклонение, а не успех: значит операторов на линии больше, чем нужно.
 * Коридор сплошной, промежуточного цвета нет: либо в норме, либо нет.
 */
const AR_MIN_PERCENT = 3;
const AR_MAX_PERCENT = 5;

const arTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    const percent = Number(ratio) * 100;
    return percent >= AR_MIN_PERCENT && percent <= AR_MAX_PERCENT ? 'good' : 'bad';
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
 * «Принято / входящих» одной плиткой: принятые — главное число, входящие приглушены.
 * Входящими считаем только дошедших до очереди — сбросившие трубку на приветствии до
 * оператора не доходили, и записывать их во входящие некорректно. За счёт этого разрыв
 * между числами равен ровно потерянным, а плитка AR показывает, допустим ли он.
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

/** Сегментированный переключатель режима — тот же, что в остальных разделах. */
const ModeSwitch = ({ value, disabled, onChange }) => (
    <div className="flex rounded-xl bg-slate-100 p-1">
        {BROADCAST_MODES.map((mode) => (
            <button
                key={mode.key}
                type="button"
                disabled={disabled}
                onClick={() => { if (value !== mode.key) onChange(mode.key); }}
                title={mode.hint}
                className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all disabled:opacity-50 ${
                    value === mode.key
                        ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                        : 'text-slate-500 hover:text-slate-700'}`}
            >
                {mode.label}
            </button>
        ))}
    </div>
);

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
                        hint={`Норма ${AR_MIN_PERCENT}–${AR_MAX_PERCENT}%`}
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
                        second={formatInt(today.arrived)}
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
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;

    const [snapshot, setSnapshot] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [fullscreen, setFullscreen] = useState(false);
    const [broadcastOpen, setBroadcastOpen] = useState(false);

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
                <button type="button" className={iosBtnGhost} onClick={() => setBroadcastOpen(true)}>
                    <FaIcon className="fas fa-paper-plane"></FaIcon>
                    Отбивка
                </button>
                <button type="button" className={iosBtnGhost} onClick={() => setFullscreen(true)}>
                    <FaIcon className="fas fa-expand"></FaIcon>
                    На весь экран
                </button>
            </div>
            {/* Через портал, как и полноэкранный режим: модалка не должна зависеть от
                вертикальных отступов шапки, а шапка рисуется во всех трёх состояниях экрана. */}
            {createPortal(
                <BroadcastModal
                    open={broadcastOpen}
                    onClose={() => setBroadcastOpen(false)}
                    apiBaseUrl={apiBaseUrl}
                    withAccessTokenHeader={withAccessTokenHeader}
                    showToast={showToast}
                />,
                document.body,
            )}
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
