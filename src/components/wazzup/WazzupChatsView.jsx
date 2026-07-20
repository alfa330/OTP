import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Search, RefreshCw, Loader2, AlertCircle, MessageSquare, ExternalLink,
    ChevronUp, Headset, FileText, MapPin, Ban, Users, Bot, Wand2, Link2,
    Contact2, PhoneMissed, BarChart3, Download, Timer, ArrowUpDown,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel, iosBtnGhost,
    IosBadge, IosToggle, IosModal,
} from '../ui/ios';

/* Чаты Wazzup (Верификаторы): просмотр переписки «как в мессенджере» +
 * вкладка «Операторы» (привязка авторов Wazzup к нашим операторам).
 * Данные копятся вебхуком с 2026-07-17, ретеншн 45 дней — более ранняя
 * история доступна только в самом Wazzup (кнопка «Открыть в Wazzup»). */

const PAGE_SIZE = 30;
const THREAD_PAGE = 50;

// Воркспейс аккаунта в веб-приложении Wazzup; конкретный чат открывается по
// /chat/<chatType>/<chatId>/<channelId> внутри него.
const WAZZUP_APP_BASE = 'https://app.wazzup24.com/6757-7677';
const wazzupChatUrl = (chat) =>
    `${WAZZUP_APP_BASE}/chat/${chat.chatType || 'whatsapp'}/${encodeURIComponent(chat.chatId)}/${chat.channelId}`;

const MEDIA_LABELS = {
    image: 'Фото', video: 'Видео', audio: 'Голосовое', document: 'Документ',
    geo: 'Геолокация', vcard: 'Контакт', missing_call: 'Пропущенный звонок',
    unsupported: 'Вложение',
};

const MEDIA_ICONS = {
    document: FileText, geo: MapPin, vcard: Contact2, missing_call: PhoneMissed,
};

const STATUS_LABELS = { sent: 'Отправлено', delivered: 'Доставлено', read: 'Прочитано', error: 'Ошибка' };

const fmtTime = (iso) => {
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

const fmtDay = (iso) => {
    if (!iso) return '';
    return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
};

const fmtListDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    const today = new Date();
    return d.toDateString() === today.toDateString()
        ? fmtTime(iso)
        : d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

// '[image]' из сводки чата → человеческий label
const previewText = (text) => {
    const m = /^\[(\w+)\]$/.exec(text || '');
    if (m) return MEDIA_LABELS[m[1]] || m[1];
    return text || '—';
};

// Длительность ответа: секунды → «45 с» / «5 м 30 с» / «2 ч 15 м»
const fmtDur = (secs) => {
    if (secs === null || secs === undefined) return '—';
    const s = Math.round(secs);
    if (s < 60) return `${s} с`;
    if (s < 3600) {
        const m = Math.floor(s / 60);
        return s % 60 ? `${m} м ${s % 60} с` : `${m} м`;
    }
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h} ч ${m} м` : `${h} ч`;
};

const isoDate = (d) => {
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

const daysAgo = (n) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return isoDate(d);
};

// CSV для Excel: BOM + ';' (ru-локаль сама не делит по запятой)
const csvCell = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[";\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

const downloadCsv = (filename, rows) => {
    const body = rows.map((r) => r.map(csvCell).join(';')).join('\r\n');
    const blob = new Blob([`﻿${body}`], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
};

const initials = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
};

// Детерминированный пастельный градиент аватарки по имени
const AVATAR_HUES = [
    'from-sky-400 to-blue-500', 'from-emerald-400 to-teal-500',
    'from-violet-400 to-purple-500', 'from-amber-400 to-orange-500',
    'from-rose-400 to-pink-500', 'from-cyan-400 to-sky-500',
];
const avatarClass = (name) => {
    let h = 0;
    for (const ch of String(name || '')) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return AVATAR_HUES[h % AVATAR_HUES.length];
};

const Avatar = ({ name, size = 'h-9 w-9', muted = false, icon: Icon = null }) => (
    <div className={`${size} flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-[12px] font-semibold text-white shadow-sm ${
        muted ? 'from-slate-300 to-slate-400' : avatarClass(name)}`}>
        {Icon ? <Icon size={15} /> : initials(name)}
    </div>
);

/* Медиа-содержимое пузыря: фото с лайтбоксом, аудио/видео плееры,
 * для остального — аккуратный чип со ссылкой. Битая ссылка → чип. */
function MediaContent({ msg, light }) {
    const [failed, setFailed] = useState(false);
    const [zoom, setZoom] = useState(false);
    const uri = msg.contentUri;

    if (uri && !failed && msg.type === 'image') {
        return (
            <>
                <img src={uri} alt="" loading="lazy" onError={() => setFailed(true)}
                     onClick={() => setZoom(true)}
                     className="max-h-64 w-auto max-w-full cursor-zoom-in rounded-xl" />
                <IosModal open={zoom} onClose={() => setZoom(false)} title="Фото" maxWidth="max-w-3xl">
                    <img src={uri} alt="" className="mx-auto max-h-[72vh] w-auto rounded-2xl" />
                    <div className="mt-3 text-center">
                        <a href={uri} target="_blank" rel="noopener noreferrer"
                           className="text-[13px] font-medium text-blue-600 hover:underline">
                            Открыть оригинал
                        </a>
                    </div>
                </IosModal>
            </>
        );
    }
    if (uri && !failed && msg.type === 'video') {
        return <video controls preload="metadata" src={uri} onError={() => setFailed(true)}
                      className="max-h-64 w-auto max-w-full rounded-xl" />;
    }
    if (uri && !failed && msg.type === 'audio') {
        return <audio controls preload="none" src={uri} onError={() => setFailed(true)}
                      className="h-10 w-64 max-w-full" />;
    }
    const label = MEDIA_LABELS[msg.type] || msg.type || 'Вложение';
    const Icon = MEDIA_ICONS[msg.type] || FileText;
    const chip = `inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12.5px] font-medium ${
        light ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`;
    return uri
        ? <a href={uri} target="_blank" rel="noopener noreferrer" className={`${chip} hover:opacity-80`}>
              <Icon size={13} /> {label}
          </a>
        : <span className={chip}><Icon size={13} /> {label}</span>;
}

/* Пузырь в стиле iMessage: исходящие — синие справа, входящие — белые слева. */
function MessageBubble({ msg }) {
    const out = msg.isEcho;
    const hasMedia = Boolean(MEDIA_LABELS[msg.type]) || (msg.type && msg.type !== 'text');
    return (
        <div className={`flex ${out ? 'justify-end' : 'justify-start'} px-4`}>
            <div className={`max-w-[75%] rounded-2xl px-3 py-2 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${
                out ? 'rounded-br-md bg-blue-500 text-white'
                    : 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60'
            } ${msg.isDeleted ? 'opacity-70' : ''}`}>
                {out && msg.authorName && (
                    <div className="mb-0.5 flex items-center gap-1 text-[11px] font-semibold text-blue-100">
                        <Headset size={11} /> {msg.authorName}
                    </div>
                )}
                {hasMedia && <div className={msg.text ? 'mb-1' : ''}><MediaContent msg={msg} light={out} /></div>}
                {msg.text && <div className="whitespace-pre-wrap break-words">{msg.text}</div>}
                {!msg.text && !hasMedia && (
                    <div className={`italic ${out ? 'text-blue-100' : 'text-slate-400'}`}>
                        [{msg.type || 'сообщение'}]
                    </div>
                )}
                <div className={`mt-0.5 flex items-center justify-end gap-1.5 text-[10px] ${
                    out ? 'text-blue-100/90' : 'text-slate-400'}`}>
                    {msg.isDeleted && (
                        <span className={`flex items-center gap-0.5 ${out ? 'text-blue-50' : 'text-rose-500'}`}>
                            <Ban size={10} /> удалено
                        </span>
                    )}
                    {msg.isEdited && !msg.isDeleted && <span>изменено</span>}
                    <span>{fmtTime(msg.dt)}</span>
                    {out && msg.status && STATUS_LABELS[msg.status] && <span>· {STATUS_LABELS[msg.status]}</span>}
                </div>
            </div>
        </div>
    );
}

const SegButton = ({ active, onClick, icon: Icon, children }) => (
    <button onClick={onClick}
            className={`flex items-center gap-1.5 rounded-[9px] px-3.5 py-1.5 text-[12.5px] font-semibold transition-all ${
                active ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                       : 'text-slate-500 hover:text-slate-700'}`}>
        <Icon size={13} /> {children}
    </button>
);

/* ── Аналитика по менеджерам ──────────────────────────────────────────────
 * Диалоги — чаты (не эпизоды) хотя бы с одним его сообщением; время ответа —
 * одно значение на чат, засчитывается ответившему клиенту первым. */

const ANALYTICS_COLS = [
    { key: 'name', label: 'Менеджер', num: false },
    { key: 'dialogs', label: 'Диалоги', num: true, hint: 'Чатов, где есть хотя бы одно его сообщение' },
    { key: 'messages', label: 'Сообщения', num: true, hint: 'Его исходящие сообщения' },
    { key: 'avgResponseSecs', label: 'Ср. ответ', num: true, hint: 'Среднее: первый ответ менеджера − первое сообщение клиента в чате' },
    { key: 'medianResponseSecs', label: 'Медиана', num: true, hint: 'Медиана того же времени — устойчива к единичным «зависшим» чатам' },
    { key: 'answeredChats', label: 'Первых ответов', num: true, hint: 'По скольким чатам он ответил клиенту первым (база для среднего)' },
    { key: 'lastMessageAt', label: 'Активность', num: true, hint: 'Последнее сообщение менеджера' },
];

const PERIOD_PRESETS = [
    { label: '7 дней', days: 7 },
    { label: '30 дней', days: 30 },
    { label: 'Всё', days: null },
];

// Тот же вид, что iosInput, но со своей шириной: у iosInput ширина зашита
// в w-full, и дописанный следом w-[…] проигрывает ему в каскаде.
const dateInput =
    'w-[150px] shrink-0 rounded-xl border-0 bg-slate-100 px-3 py-2 text-[13px] text-slate-900 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

function AnalyticsTab({ apiBaseUrl, headers, showToast }) {
    const [from, setFrom] = useState(daysAgo(30));
    const [to, setTo] = useState(isoDate(new Date()));
    const [data, setData] = useState(null);          // {items, summary} | null = загрузка
    const [error, setError] = useState(null);
    const [filter, setFilter] = useState('');
    const [sort, setSort] = useState({ key: 'dialogs', dir: 'desc' });
    const request = useRef({ id: 0 });

    const load = (range = { from, to }) => {
        const requestId = request.current.id + 1;
        request.current.id = requestId;
        setData(null); setError(null);
        axios.get(`${apiBaseUrl}/api/wazzup/analytics`, {
            headers: headers(),
            params: { from: range.from || undefined, to: range.to || undefined },
        }).then((r) => {
            if (requestId !== request.current.id) return;
            setData({ items: r.data.items || [], summary: r.data.summary || {} });
        }).catch(() => {
            if (requestId !== request.current.id) return;
            setError('Не удалось загрузить аналитику');
        });
    };
    useEffect(() => { load(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const applyPreset = (days) => {
        const next = days === null ? { from: '', to: '' }
                                   : { from: daysAgo(days), to: isoDate(new Date()) };
        setFrom(next.from); setTo(next.to);
        load(next);
    };

    const activePreset = PERIOD_PRESETS.find((p) => (
        p.days === null ? (!from && !to) : (from === daysAgo(p.days) && to === isoDate(new Date()))
    ));

    const rows = useMemo(() => {
        const q = filter.trim().toLowerCase();
        const list = (data?.items || []).filter((r) => !q || String(r.name || '').toLowerCase().includes(q));
        const { key, dir } = sort;
        const mul = dir === 'asc' ? 1 : -1;
        return [...list].sort((a, b) => {
            const av = a[key], bv = b[key];
            // пустые значения (нет ответов / нет привязки) всегда внизу
            if (av === null || av === undefined) return bv === null || bv === undefined ? 0 : 1;
            if (bv === null || bv === undefined) return -1;
            if (key === 'name') return String(av).localeCompare(String(bv), 'ru') * mul;
            if (key === 'lastMessageAt') return (new Date(av) - new Date(bv)) * mul;
            return (av - bv) * mul;
        });
    }, [data, filter, sort]);

    const toggleSort = (key) => setSort((prev) => (
        prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
                         : { key, dir: key === 'name' ? 'asc' : 'desc' }
    ));

    const exportCsv = () => {
        if (!rows.length) return;
        // имя файла — ASCII: не все корпоративные почтовики/диски переживают кириллицу
        downloadCsv(`wazzup_analytics_${from || 'all'}_${to || 'now'}.csv`, [
            ['Менеджер', 'Автор в Wazzup', 'Привязан', 'Диалоги', 'Сообщения',
             'Первых ответов', 'Ср. время ответа', 'Ср. время ответа, сек',
             'Медиана времени ответа', 'Медиана, сек', 'Последняя активность'],
            ...rows.map((r) => [
                r.name, r.authorName || '', r.linked ? 'да' : 'нет',
                r.dialogs, r.messages, r.answeredChats,
                fmtDur(r.avgResponseSecs),
                r.avgResponseSecs === null ? '' : Math.round(r.avgResponseSecs),
                fmtDur(r.medianResponseSecs),
                r.medianResponseSecs === null ? '' : Math.round(r.medianResponseSecs),
                r.lastMessageAt ? new Date(r.lastMessageAt).toLocaleString('ru-RU') : '',
            ]),
        ]);
        showToast?.(`Выгружено ${rows.length} строк`, 'success');
    };

    const summary = data?.summary || {};
    const cell = (col) => `px-3 py-2.5 ${col.num ? 'text-right tabular-nums' : 'text-left'}`;

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <div className="flex rounded-xl bg-slate-100 p-1">
                        {PERIOD_PRESETS.map((p) => (
                            <button key={p.label} onClick={() => applyPreset(p.days)}
                                    className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                        activePreset === p ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                           : 'text-slate-500 hover:text-slate-700'}`}>
                                {p.label}
                            </button>
                        ))}
                    </div>
                    <input type="date" value={from} max={to || undefined}
                           onChange={(e) => { setFrom(e.target.value); load({ from: e.target.value, to }); }}
                           className={dateInput} />
                    <span className="text-[13px] text-slate-400">—</span>
                    <input type="date" value={to} min={from || undefined}
                           onChange={(e) => { setTo(e.target.value); load({ from, to: e.target.value }); }}
                           className={dateInput} />
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={filter} onChange={(e) => setFilter(e.target.value)}
                               placeholder="Поиск менеджера…"
                               className={`${iosInput} w-52 py-2 pl-8 text-[13px]`} />
                    </div>
                    <button onClick={exportCsv} disabled={!rows.length} className={iosBtnGhost}>
                        <Download size={13} /> Выгрузить
                    </button>
                    <button onClick={() => load()} className={iosBtnGhost}>
                        <RefreshCw size={13} /> Обновить
                    </button>
                </div>
            </div>

            {data && (
                <div className="flex flex-wrap items-center gap-2">
                    <IosBadge tone="slate">{data.items.length} менеджеров</IosBadge>
                    <IosBadge tone="blue">{summary.chats ?? 0} чатов</IosBadge>
                    <IosBadge tone="slate">{summary.messages ?? 0} сообщений</IosBadge>
                    <IosBadge tone="green">
                        <Timer size={11} /> ср. ответ {fmtDur(summary.avgResponseSecs)}
                    </IosBadge>
                    <IosBadge tone="slate">медиана {fmtDur(summary.medianResponseSecs)}</IosBadge>
                </div>
            )}

            {data === null && !error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> Загрузка…
                </div>
            )}
            {error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500`}>
                    <AlertCircle size={15} /> {error}
                </div>
            )}

            {data && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="max-h-[calc(100vh-330px)] overflow-auto">
                        <table className="w-full border-collapse text-[13px]">
                            <thead className="sticky top-0 z-10 bg-white/85 backdrop-blur-xl">
                                <tr className="border-b border-slate-200/70">
                                    {ANALYTICS_COLS.map((col) => (
                                        <th key={col.key} title={col.hint}
                                            onClick={() => toggleSort(col.key)}
                                            className={`${cell(col)} cursor-pointer select-none whitespace-nowrap text-[11px] font-semibold uppercase tracking-wider text-slate-500 transition hover:text-slate-800`}>
                                            <span className={`inline-flex items-center gap-1 ${col.num ? 'flex-row-reverse' : ''}`}>
                                                {col.label}
                                                <ArrowUpDown size={10}
                                                             className={sort.key === col.key ? 'text-blue-500' : 'text-slate-300'} />
                                            </span>
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {rows.length === 0 && (
                                    <tr>
                                        <td colSpan={ANALYTICS_COLS.length}
                                            className="px-4 py-10 text-center text-[13px] text-slate-400">
                                            {filter ? 'Никого не найдено' : 'За выбранный период сообщений нет'}
                                        </td>
                                    </tr>
                                )}
                                {rows.map((r) => (
                                    <tr key={r.key} className="transition-colors hover:bg-slate-50/80">
                                        <td className="px-3 py-2">
                                            <div className="flex items-center gap-2.5">
                                                <Avatar name={r.name} size="h-8 w-8" muted={!r.linked} />
                                                <div className="min-w-0">
                                                    <div className="flex items-center gap-1.5">
                                                        <span className="truncate font-semibold text-slate-900">{r.name}</span>
                                                        {r.isVerifier && <IosBadge tone="blue">верификатор</IosBadge>}
                                                    </div>
                                                    {!r.linked && (
                                                        <div className="text-[11px] text-amber-600">не привязан к оператору</div>
                                                    )}
                                                </div>
                                            </div>
                                        </td>
                                        <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-900">{r.dialogs}</td>
                                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{r.messages}</td>
                                        <td className="px-3 py-2 text-right tabular-nums font-medium text-slate-900">{fmtDur(r.avgResponseSecs)}</td>
                                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fmtDur(r.medianResponseSecs)}</td>
                                        <td className="px-3 py-2 text-right tabular-nums text-slate-500">{r.answeredChats}</td>
                                        <td className="px-3 py-2 text-right tabular-nums text-slate-400">{fmtListDate(r.lastMessageAt)}</td>
                                    </tr>
                                ))}
                            </tbody>
                            {rows.length > 0 && (
                                <tfoot className="sticky bottom-0 bg-slate-50/90 backdrop-blur-xl">
                                    <tr className="border-t border-slate-200/70 text-[12.5px] font-semibold text-slate-600">
                                        <td className="px-3 py-2.5">Итого</td>
                                        <td className="px-3 py-2.5 text-right tabular-nums" title="Уникальных чатов — меньше суммы по строкам, если в чате писали несколько менеджеров">
                                            {summary.chats ?? 0}
                                        </td>
                                        <td className="px-3 py-2.5 text-right tabular-nums">{summary.messages ?? 0}</td>
                                        <td className="px-3 py-2.5 text-right tabular-nums">{fmtDur(summary.avgResponseSecs)}</td>
                                        <td className="px-3 py-2.5 text-right tabular-nums">{fmtDur(summary.medianResponseSecs)}</td>
                                        <td className="px-3 py-2.5 text-right tabular-nums">{summary.answeredChats ?? 0}</td>
                                        <td />
                                    </tr>
                                </tfoot>
                            )}
                        </table>
                    </div>
                </div>
            )}

            <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                Время ответа считается по чату целиком: первое сообщение менеджера после первого
                сообщения клиента минус время этого сообщения клиента. Засчитывается тому, кто
                ответил клиенту первым, поэтому «первых ответов» у менеджера обычно меньше, чем
                диалогов. Диалог — чат (не эпизод), где есть хотя бы одно его сообщение. Авторы,
                отмеченные ботами на вкладке «Привязка», в расчёт не входят.
            </p>
        </div>
    );
}

/* Вкладка «Привязка»: привязка авторов Wazzup к нашим операторам.
 * Привязка нужна атрибуции ИИ-оценки; «Бот» исключает авторассылки. */
function AuthorRow({ author, operators, saving, onSave }) {
    const verifiers = operators.filter((o) => o.isVerifier);
    const others = operators.filter((o) => !o.isVerifier);
    return (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 ${author.isBot ? 'opacity-55' : ''}`}>
            <div className="flex min-w-0 flex-1 items-center gap-3" style={{ minWidth: 220 }}>
                <Avatar name={author.authorName} muted={author.isBot} icon={author.isBot ? Bot : null} />
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">
                        {author.authorName || '—'}
                    </div>
                    <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
                        <span>{author.messagesCount} сообщ. · {author.chatsCount} чат.</span>
                        {author.lastMessageAt && <span>· {fmtListDate(author.lastMessageAt)}</span>}
                    </div>
                </div>
            </div>
            <div className="flex items-center gap-2">
                {!author.isBot && !author.userId && author.suggestedUserId && !saving && (
                    <button onClick={() => onSave(author, { userId: author.suggestedUserId })}
                            title={`Похоже, это ${author.suggestedUserName}`}
                            className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1.5 text-[12px] font-semibold text-amber-700 ring-1 ring-amber-200 transition hover:bg-amber-100 active:scale-[0.97]">
                        <Wand2 size={12} /> {author.suggestedUserName}
                    </button>
                )}
                <div className="relative">
                    <select value={author.userId ?? ''} disabled={author.isBot || saving}
                            onChange={(e) => onSave(author, { userId: e.target.value ? Number(e.target.value) : null })}
                            className={`w-56 appearance-none rounded-xl border-0 px-3.5 py-2 pr-8 text-[13px] font-medium focus:outline-none focus:ring-2 focus:ring-blue-500/70 disabled:cursor-not-allowed ${
                                author.userId ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
                                              : 'bg-slate-100 text-slate-600'}`}>
                        <option value="">— не привязан —</option>
                        <optgroup label="Верификаторы">
                            {verifiers.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                        </optgroup>
                        {others.length > 0 && (
                            <optgroup label="Остальные (отдел продаж)">
                                {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                            </optgroup>
                        )}
                    </select>
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                        {saving ? <Loader2 size={13} className="animate-spin" />
                                : author.userId ? <Link2 size={13} className="text-emerald-500" />
                                : <svg width="10" height="6" viewBox="0 0 10 6" fill="none"><path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>}
                    </span>
                </div>
                <label className="flex items-center gap-2 text-[12px] font-medium text-slate-500">
                    <IosToggle checked={author.isBot} disabled={saving}
                               onChange={(v) => onSave(author, { isBot: v })} />
                    Бот
                </label>
            </div>
        </div>
    );
}

function AuthorsTab({ apiBaseUrl, headers, showToast }) {
    const [data, setData] = useState(null);       // {items, operators} | null = загрузка
    const [error, setError] = useState(null);
    const [savingId, setSavingId] = useState(null);
    const [filter, setFilter] = useState('');

    const load = () => {
        setData(null); setError(null);
        axios.get(`${apiBaseUrl}/api/wazzup/authors`, { headers: headers() })
            .then((r) => setData({ items: r.data.items || [], operators: r.data.operators || [] }))
            .catch(() => setError('Не удалось загрузить авторов'));
    };
    useEffect(() => { load(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const save = (author, patch) => {
        const next = { userId: author.userId, isBot: author.isBot, ...patch };
        if (patch.isBot) next.userId = null;
        setSavingId(author.authorId);
        axios.post(`${apiBaseUrl}/api/wazzup/authors/map`, {
            authorId: author.authorId, authorName: author.authorName,
            userId: next.isBot ? null : next.userId, isBot: next.isBot,
        }, { headers: headers() }).then(() => {
            setSavingId(null);
            const opName = (data.operators.find((o) => o.id === next.userId) || {}).name || null;
            setData((prev) => ({
                ...prev,
                items: prev.items.map((it) => it.authorId === author.authorId
                    ? { ...it, userId: next.isBot ? null : next.userId,
                        userName: next.isBot ? null : opName, isBot: next.isBot }
                    : it),
            }));
        }).catch(() => {
            setSavingId(null);
            showToast?.('Не удалось сохранить привязку', 'error');
        });
    };

    const filtered = (data?.items || []).filter((a) =>
        !filter.trim() || String(a.authorName || '').toLowerCase().includes(filter.trim().toLowerCase()));
    const pending = filtered.filter((a) => !a.userId && !a.isBot);
    const done = filtered.filter((a) => a.userId || a.isBot);

    const renderGroup = (title, list, emptyHint) => (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between">
                <div className={iosGroupLabel}>{title}</div>
                <span className="pr-1 text-[11px] text-slate-400">{list.length}</span>
            </div>
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {list.length === 0 && (
                    <div className="px-4 py-5 text-center text-[13px] text-slate-400">{emptyHint}</div>
                )}
                {list.map((a) => (
                    <AuthorRow key={a.authorId} author={a} operators={data.operators}
                               saving={savingId === a.authorId} onSave={save} />
                ))}
            </div>
        </section>
    );

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    {data && (
                        <>
                            <IosBadge tone="slate">{data.items.length} авторов</IosBadge>
                            {pending.length > 0 && <IosBadge tone="amber">без привязки: {pending.length}</IosBadge>}
                            {data.items.some((a) => a.isBot) && (
                                <IosBadge tone="slate"><Bot size={11} /> ботов: {data.items.filter((a) => a.isBot).length}</IosBadge>
                            )}
                        </>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={filter} onChange={(e) => setFilter(e.target.value)}
                               placeholder="Поиск автора…"
                               className={`${iosInput} w-52 py-2 pl-8 text-[13px]`} />
                    </div>
                    <button onClick={load} className={iosBtnGhost}><RefreshCw size={13} /> Обновить</button>
                </div>
            </div>

            {data === null && !error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> Загрузка…
                </div>
            )}
            {error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500`}>
                    <AlertCircle size={15} /> {error}
                </div>
            )}
            {data && (
                <>
                    {renderGroup('Требуют привязки', pending,
                        filter ? 'Никого не найдено' : 'Все авторы привязаны — отлично')}
                    {renderGroup('Привязанные и боты', done,
                        'Пока пусто: привяжите авторов к операторам или отметьте ботов')}
                    <p className="px-1 text-[11px] text-slate-500">
                        Привязка соединяет автора исходящих сообщений Wazzup с оператором в системе —
                        по ней ИИ-оценка будет относить диалоги к верификаторам. «Бот» исключает
                        авторассылки и интеграции из оценки.
                    </p>
                </>
            )}
        </div>
    );
}

/* Подраздел «Операторы»: аналитика открывается первой — привязка нужна реже,
 * это разовая настройка. */
function OperatorsTab({ apiBaseUrl, headers, showToast }) {
    const [subTab, setSubTab] = useState('analytics');
    return (
        <div className="space-y-3">
            <div className="flex w-fit rounded-xl bg-slate-100 p-1">
                <SegButton active={subTab === 'analytics'} onClick={() => setSubTab('analytics')}
                           icon={BarChart3}>Аналитика</SegButton>
                <SegButton active={subTab === 'mapping'} onClick={() => setSubTab('mapping')}
                           icon={Link2}>Привязка</SegButton>
            </div>
            {subTab === 'analytics'
                ? <AnalyticsTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
                : <AuthorsTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />}
        </div>
    );
}

export default function WazzupChatsView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [mainTab, setMainTab] = useState('chats');

    const [channels, setChannels] = useState(null);       // null = загрузка
    const [channelId, setChannelId] = useState('');       // '' = все каналы
    const [chats, setChats] = useState(null);
    const [chatsTotal, setChatsTotal] = useState(0);
    const [chatsError, setChatsError] = useState(null);
    const [search, setSearch] = useState('');
    const [appliedSearch, setAppliedSearch] = useState('');
    const [selected, setSelected] = useState(null);       // {channelId, chatId, ...}
    const [thread, setThread] = useState(null);
    const [threadHasMore, setThreadHasMore] = useState(false);
    const [threadLoadingMore, setThreadLoadingMore] = useState(false);
    const chatsRequest = useRef({ id: 0, controller: null });
    const threadRequest = useRef({ id: 0, controller: null });
    const threadBox = useRef(null);
    const searchDebounce = useRef(null);

    const channelName = useMemo(() => {
        const map = {};
        (channels || []).forEach((c) => { map[c.channelId] = c.name || c.plainId || c.channelId; });
        return map;
    }, [channels]);

    const loadChannels = () => {
        axios.get(`${apiBaseUrl}/api/wazzup/channels`, { headers: headers() })
            .then((r) => setChannels(r.data.items || []))
            .catch(() => { setChannels([]); showToast?.('Не удалось загрузить каналы', 'error'); });
    };

    const loadChats = ({ reset = true, channel = channelId, q = appliedSearch } = {}) => {
        chatsRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = chatsRequest.current.id + 1;
        chatsRequest.current = { id: requestId, controller };
        const offset = reset ? 0 : (chats?.length || 0);
        if (reset) { setChats(null); setChatsError(null); }
        axios.get(`${apiBaseUrl}/api/wazzup/chats`, {
            headers: headers(), signal: controller.signal,
            params: { channel_id: channel || undefined, q: q || undefined, limit: PAGE_SIZE, offset },
        }).then((r) => {
            if (requestId !== chatsRequest.current.id) return;
            setChatsTotal(r.data.total || 0);
            setChats((prev) => reset ? (r.data.items || []) : [...(prev || []), ...(r.data.items || [])]);
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== chatsRequest.current.id) return;
            setChats((prev) => prev || []);
            setChatsError('Не удалось загрузить чаты');
        });
    };

    const loadThread = (chat, { before = null } = {}) => {
        threadRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = threadRequest.current.id + 1;
        threadRequest.current = { id: requestId, controller };
        if (!before) setThread(null); else setThreadLoadingMore(true);
        axios.get(`${apiBaseUrl}/api/wazzup/chat-messages`, {
            headers: headers(), signal: controller.signal,
            params: { channel_id: chat.channelId, chat_id: chat.chatId,
                      before: before || undefined, limit: THREAD_PAGE },
        }).then((r) => {
            if (requestId !== threadRequest.current.id) return;
            setThreadHasMore(Boolean(r.data.hasMore));
            setThreadLoadingMore(false);
            if (before) {
                const box = threadBox.current;
                const prevHeight = box ? box.scrollHeight : 0;
                setThread((prev) => [...(r.data.items || []), ...(prev || [])]);
                // сохранить позицию скролла при подгрузке вверх
                requestAnimationFrame(() => {
                    if (box) box.scrollTop = box.scrollHeight - prevHeight;
                });
            } else {
                setThread(r.data.items || []);
                requestAnimationFrame(() => {
                    if (threadBox.current) threadBox.current.scrollTop = threadBox.current.scrollHeight;
                });
            }
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== threadRequest.current.id) return;
            setThreadLoadingMore(false);
            if (!before) setThread([]);
            showToast?.('Не удалось загрузить переписку', 'error');
        });
    };

    useEffect(() => { loadChannels(); loadChats(); /* eslint-disable-next-line */ }, [apiBaseUrl]);

    const pickChannel = (cid) => {
        setChannelId(cid);
        setSelected(null); setThread(null);
        loadChats({ channel: cid });
    };

    const onSearchInput = (value) => {
        setSearch(value);
        clearTimeout(searchDebounce.current);
        searchDebounce.current = setTimeout(() => {
            setAppliedSearch(value.trim());
            loadChats({ q: value.trim() });
        }, 350);
    };

    const openChat = (chat) => {
        setSelected(chat);
        setThreadHasMore(false);
        loadThread(chat);
    };

    const refreshAll = () => {
        loadChannels();
        loadChats();
        if (selected) loadThread(selected);
    };

    // Группировка ленты по дням для разделителей
    const threadWithDays = useMemo(() => {
        if (!thread) return [];
        const out = [];
        let lastDay = null;
        thread.forEach((m) => {
            const day = (m.dt || '').slice(0, 10);
            if (day && day !== lastDay) { out.push({ _day: fmtDay(m.dt), messageId: `day-${day}` }); lastDay = day; }
            out.push(m);
        });
        return out;
    }, [thread]);

    const activeChannels = (channels || []).filter((c) => (c.chatsCount || 0) > 0 || c.state === 'active');

    const segBtn = (key, Icon, label) => (
        <SegButton active={mainTab === key} onClick={() => setMainTab(key)} icon={Icon}>
            {label}
        </SegButton>
    );

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Чаты Верификаторов</h2>
                    <p className="text-xs text-slate-500">
                        Переписка Wazzup; история хранится 45 дней, более ранняя — в самом Wazzup
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <div className="flex rounded-xl bg-slate-100 p-1">
                        {segBtn('chats', MessageSquare, 'Чаты')}
                        {segBtn('authors', Users, 'Операторы')}
                    </div>
                    <a href={WAZZUP_APP_BASE} target="_blank" rel="noopener noreferrer"
                       className={iosBtnGhost}>
                        <ExternalLink size={13} /> Открыть в Wazzup
                    </a>
                    {mainTab === 'chats' && (
                        <button onClick={refreshAll} className={iosBtnGhost}>
                            <RefreshCw size={13} /> Обновить
                        </button>
                    )}
                </div>
            </div>

            {mainTab === 'authors' && (
                <OperatorsTab apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
            )}

            <div className={`${iosCard} flex overflow-hidden`}
                 style={{ height: 'calc(100vh - 170px)', minHeight: 420,
                          display: mainTab === 'chats' ? undefined : 'none' }}>
                {/* Каналы */}
                <div className="hidden w-56 shrink-0 flex-col overflow-y-auto border-r border-slate-100 bg-slate-50/70 py-1.5 md:flex">
                    <button onClick={() => pickChannel('')}
                            className={`mx-1.5 rounded-lg px-3 py-2.5 text-left text-[13.5px] transition ${
                                !channelId ? 'bg-white font-semibold text-slate-900 shadow-sm ring-1 ring-slate-200/60'
                                           : 'text-slate-600 hover:bg-slate-100'}`}>
                        Все каналы
                    </button>
                    {channels === null && (
                        <div className="flex items-center gap-2 px-4 py-3 text-xs text-slate-400">
                            <Loader2 size={13} className="animate-spin" /> Загрузка…
                        </div>
                    )}
                    {activeChannels.map((c) => (
                        <button key={c.channelId} onClick={() => pickChannel(c.channelId)}
                                className={`mx-1.5 mt-0.5 rounded-lg px-3 py-2 text-left transition ${
                                    channelId === c.channelId ? 'bg-white shadow-sm ring-1 ring-slate-200/60'
                                                              : 'hover:bg-slate-100'}`}>
                            <div className={`truncate text-[13px] text-slate-800 ${channelId === c.channelId ? 'font-semibold' : ''}`}>
                                {c.name || c.plainId || c.channelId}
                            </div>
                            <div className="mt-0.5 flex items-center justify-between gap-1.5 text-[11px] text-slate-400">
                                <span className="truncate">{c.plainId || ''}</span>
                                {(c.chatsCount || 0) > 0 && (
                                    <span className="rounded-full bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
                                        {c.chatsCount}
                                    </span>
                                )}
                            </div>
                        </button>
                    ))}
                </div>

                {/* Список чатов */}
                <div className="flex w-80 shrink-0 flex-col border-r border-slate-100">
                    <div className="border-b border-slate-100 p-2.5">
                        <div className="relative">
                            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={search} onChange={(e) => onSearchInput(e.target.value)}
                                   placeholder="Имя или телефон…"
                                   className={`${iosInput} py-2 pl-9 text-[13px]`} />
                        </div>
                    </div>
                    <div className="flex-1 overflow-y-auto py-1">
                        {chats === null && (
                            <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-400">
                                <Loader2 size={15} className="animate-spin" /> Загрузка чатов…
                            </div>
                        )}
                        {chatsError && (
                            <div className="flex items-center justify-center gap-2 py-6 text-sm text-rose-500">
                                <AlertCircle size={15} /> {chatsError}
                            </div>
                        )}
                        {chats !== null && chats.length === 0 && !chatsError && (
                            <div className="px-4 py-8 text-center text-sm text-slate-400">
                                Чатов пока нет — сбор идёт с 17.07.2026
                            </div>
                        )}
                        {(chats || []).map((chat) => {
                            const isSel = selected && selected.chatId === chat.chatId && selected.channelId === chat.channelId;
                            return (
                                <button key={`${chat.channelId}:${chat.chatId}`} onClick={() => openChat(chat)}
                                        className={`mx-1.5 block w-[calc(100%-12px)] rounded-xl px-2.5 py-2 text-left transition ${
                                            isSel ? 'bg-blue-500/10 ring-1 ring-blue-200/60' : 'hover:bg-slate-50'}`}>
                                    <div className="flex items-center gap-2.5">
                                        <Avatar name={chat.contactName || chat.contactPhone || chat.chatId} />
                                        <div className="min-w-0 flex-1">
                                            <div className="flex items-baseline justify-between gap-2">
                                                <span className="truncate text-[13.5px] font-semibold text-slate-900">
                                                    {chat.contactName || chat.contactPhone || chat.chatId}
                                                </span>
                                                <span className="shrink-0 text-[11px] text-slate-400">{fmtListDate(chat.lastMessageAt)}</span>
                                            </div>
                                            <div className="flex items-center gap-1 text-[12px] text-slate-500">
                                                {chat.lastMessageIsEcho && <Headset size={11} className="shrink-0 text-blue-500" />}
                                                <span className="truncate">{previewText(chat.lastMessageText)}</span>
                                            </div>
                                            {!channelId && (
                                                <div className="truncate text-[10px] text-slate-400">
                                                    {channelName[chat.channelId] || chat.channelId}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </button>
                            );
                        })}
                        {chats !== null && chats.length < chatsTotal && (
                            <button onClick={() => loadChats({ reset: false })}
                                    className="block w-full py-2.5 text-center text-[12px] font-semibold text-blue-600 hover:bg-slate-50">
                                Показать ещё ({chats.length} из {chatsTotal})
                            </button>
                        )}
                    </div>
                </div>

                {/* Лента переписки */}
                <div className="flex min-w-0 flex-1 flex-col bg-[#f2f2f7]">
                    {!selected && (
                        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-slate-400">
                            <MessageSquare size={32} strokeWidth={1.5} />
                            <span className="text-sm">Выберите чат слева</span>
                        </div>
                    )}
                    {selected && (
                        <>
                            <div className="flex items-center justify-between gap-2 border-b border-slate-200/70 bg-white/85 px-4 py-2.5 backdrop-blur-xl">
                                <div className="flex min-w-0 items-center gap-2.5">
                                    <Avatar name={selected.contactName || selected.contactPhone || selected.chatId} />
                                    <div className="min-w-0">
                                        <div className="truncate text-[14px] font-semibold text-slate-900">
                                            {selected.contactName || selected.contactPhone || selected.chatId}
                                        </div>
                                        <div className="truncate text-[11px] text-slate-400">
                                            {selected.contactPhone && `${selected.contactPhone} · `}
                                            {channelName[selected.channelId] || selected.channelId}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-2">
                                    <IosBadge tone="slate">
                                        {selected.inboundCount ?? 0} вх. · {selected.outboundCount ?? 0} исх.
                                    </IosBadge>
                                    <a href={wazzupChatUrl(selected)} target="_blank" rel="noopener noreferrer"
                                       title="Открыть этот чат в Wazzup (там доступна и история старше 45 дней)"
                                       className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.97]">
                                        <ExternalLink size={12} /> В Wazzup
                                    </a>
                                </div>
                            </div>
                            <div ref={threadBox} className="flex-1 space-y-1.5 overflow-y-auto py-3">
                                {thread === null && (
                                    <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-400">
                                        <Loader2 size={15} className="animate-spin" /> Загрузка переписки…
                                    </div>
                                )}
                                {thread !== null && threadHasMore && (
                                    <div className="flex justify-center pb-1">
                                        <button disabled={threadLoadingMore}
                                                onClick={() => loadThread(selected, { before: thread[0]?.dt })}
                                                className="flex items-center gap-1 rounded-full bg-white px-3.5 py-1.5 text-[12px] font-medium text-slate-500 shadow-sm ring-1 ring-slate-200/70 transition hover:bg-slate-50 active:scale-[0.97] disabled:opacity-50">
                                            {threadLoadingMore ? <Loader2 size={12} className="animate-spin" /> : <ChevronUp size={12} />}
                                            Более ранние
                                        </button>
                                    </div>
                                )}
                                {threadWithDays.map((m) => m._day ? (
                                    <div key={m.messageId} className="flex justify-center py-1.5">
                                        <span className="rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">
                                            {m._day}
                                        </span>
                                    </div>
                                ) : (
                                    <MessageBubble key={m.messageId} msg={m} />
                                ))}
                                {thread !== null && thread.length === 0 && (
                                    <div className="py-8 text-center text-sm text-slate-400">Сообщений нет</div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
