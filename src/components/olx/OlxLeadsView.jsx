import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, Download, ExternalLink, Loader2, RefreshCw, Send } from 'lucide-react';
import {
    APPLE_FONT, IosBadge, IosModal, IosPager, IosSegmented, iosBtnGhost, iosBtnPrimary,
    iosBtnSecondary, iosCard, iosInput, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/*
 * Раздел «Лиды OLX» — что сделал робот переноса откликов в amoCRM (задача #223).
 *
 * Раздел смотрят двое, и оба с одним вопросом — «всё ли доезжает»:
 *   маркетолог          раньше переносил лиды руками, теперь следит за роботом
 *   руководитель ОП     видит поток заявок и то, что не уложилось в минуту
 *
 * Отсюда раскладка. Первое, что видно, — ПОЛОСА КАБИНЕТОВ: девять плиток с
 * состоянием. Она отвечает на главный вопрос до всякой прокрутки, и она же
 * закрывает страх ТЗ про «тихий» простой: кабинет, который давно не
 * опрашивался, виден сразу, а не обнаруживается постфактум.
 *
 * Про цвет. Красится ТОЛЬКО отклонение — кабинет без доступа, ошибка, простой,
 * промах по SLA. Работающий кабинет и обычная строка журнала остаются
 * нейтральными: если раскрасить всё, «плохо» перестаёт бросаться в глаза.
 *
 * Сводка и журнал разведены переключателем, а не показаны рядом: это два
 * разных вопроса («сколько всего» и «что именно»), и вместе они дали бы два
 * экрана прокрутки там, где обычно нужен один.
 */

const PAGE_SIZE = 50;

// Подписи исходов обработки. Коды приходят с сервера, человеку они не
// показываются. Порядок = порядок в фильтре.
const RESULTS = [
    { value: '', label: 'Все исходы' },
    { value: 'lead_created', label: 'Сделка создана' },
    { value: 'canned_reply', label: 'Отправлен ответ' },
    { value: 'duplicate', label: 'Повтор за день' },
    { value: 'manual_review', label: 'Нужна проверка' },
    { value: 'error', label: 'Ошибка' },
];

const RESULT_LABEL = RESULTS.reduce((acc, item) => {
    if (item.value) acc[item.value] = item.label;
    return acc;
}, {});

// Тон исхода. Нейтральное состояние не красим вовсе — см. про цвет выше.
const RESULT_TONE = {
    error: 'red',
    manual_review: 'amber',
};

// Подписи состояний кабинета. `needs_auth` — самое частое на старте: доступ
// выдан на приложение, но владелец кабинета ещё не подтвердил согласие.
const STATE_LABEL = {
    ok: 'Работает',
    needs_auth: 'Нужен вход владельца',
    not_configured: 'Нет доступов',
    disabled: 'Выключен',
    error: 'Ошибка',
};

const STATE_TONE = {
    ok: 'slate',
    needs_auth: 'amber',
    not_configured: 'slate',
    disabled: 'slate',
    error: 'red',
};

const SLA_MS = 60 * 1000;

const fmtTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
};

const fmtAgo = (value) => {
    if (!value) return 'ни разу';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'ни разу';
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds} с назад`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} мин назад`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} ч назад`;
    return `${Math.round(seconds / 86400)} дн назад`;
};

const fmtLatency = (ms) => {
    if (ms === null || ms === undefined) return '—';
    if (ms < 1000) return `${ms} мс`;
    return `${(ms / 1000).toFixed(1)} с`;
};

const fmtPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return value || '—';
    return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

const isoToday = () => new Date().toISOString().slice(0, 10);

const OlxLeadsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    // showToast приходит новой функцией на каждый рендер родителя, поэтому в
    // зависимости эффектов он не идёт — иначе данные перезапрашивались бы на
    // каждый чужой рендер. Держим его в ref и зовём оттуда.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((text, kind) => {
        if (toastRef.current) toastRef.current(text, kind);
    }, []);

    const [schemaReady, setSchemaReady] = useState(true);
    const [capabilities, setCapabilities] = useState(null);
    const [chats, setChats] = useState(null);
    const [connecting, setConnecting] = useState(null);
    const [health, setHealth] = useState(null);
    const [tab, setTab] = useState('journal');

    const [journal, setJournal] = useState({ items: [], total: 0 });
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(false);
    const [page, setPage] = useState(0);

    const [cabinet, setCabinet] = useState('');
    const [result, setResult] = useState('');
    const [dateFrom, setDateFrom] = useState(isoToday());
    const [dateTo, setDateTo] = useState(isoToday());

    // ── загрузка ─────────────────────────────────────────────────────────

    const loadHealth = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/health`, { headers: headers() })
            .then((response) => setHealth(response.data))
            .catch(() => setHealth(null));
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/ping`, { headers: headers() })
            .then((response) => {
                setSchemaReady(response.data?.schema_ready !== false);
                setCapabilities(response.data?.capabilities || null);
            })
            .catch(() => setSchemaReady(false));
    }, [apiBaseUrl, headers]);

    const loadChats = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/chats`, { headers: headers() })
            .then((response) => setChats(response.data))
            .catch(() => setChats(null));
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        // Список чатов открыт только тому, кто вправе его менять, — остальным
        // запрос вернул бы отказ и зря шумел бы в консоли.
        if (capabilities?.can_manage_cabinets) loadChats();
    }, [capabilities, loadChats]);

    useEffect(() => {
        loadHealth();
        // Полоса кабинетов обновляется сама раз в минуту: раздел открывают и
        // оставляют открытым, а «тихий» простой должен становиться виден без
        // того, чтобы человек вспомнил нажать «Обновить».
        const timer = setInterval(loadHealth, 60000);
        return () => clearInterval(timer);
    }, [loadHealth]);

    const loadJournal = useCallback(() => {
        setLoading(true);
        const params = new URLSearchParams();
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(page * PAGE_SIZE));
        if (cabinet) params.set('cabinet', cabinet);
        if (result) params.set('result', result);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        axios.get(`${apiBaseUrl}/api/olx_amo/journal?${params.toString()}`,
            { headers: headers() })
            .then((response) => setJournal(response.data || { items: [], total: 0 }))
            .catch(() => toast('Не удалось загрузить журнал', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, cabinet, dateFrom, dateTo, headers, page, result, toast]);

    const loadSummary = useCallback(() => {
        setLoading(true);
        axios.get(`${apiBaseUrl}/api/olx_amo/summary?day=${dateTo || isoToday()}`,
            { headers: headers() })
            .then((response) => setSummary(response.data))
            .catch(() => toast('Не удалось загрузить сводку', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, dateTo, headers, toast]);

    useEffect(() => {
        if (tab === 'journal') loadJournal();
        else loadSummary();
    }, [tab, loadJournal, loadSummary]);

    const [exporting, setExporting] = useState(false);

    const exportJournal = useCallback(() => {
        setExporting(true);
        const params = new URLSearchParams();
        if (cabinet) params.set('cabinet', cabinet);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        // Файл забираем запросом, а не ссылкой: ссылка не несёт заголовок
        // авторизации, и сервер ответил бы на неё отказом.
        axios.get(`${apiBaseUrl}/api/olx_amo/journal/export?${params.toString()}`,
            { headers: headers(), responseType: 'blob' })
            .then((response) => {
                const href = window.URL.createObjectURL(new Blob([response.data]));
                const link = document.createElement('a');
                link.href = href;
                link.download = `Лиды OLX ${dateFrom || ''}${dateTo && dateTo !== dateFrom ? ` — ${dateTo}` : ''}.xlsx`.trim();
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.URL.revokeObjectURL(href);
            })
            .catch(() => toast('Не удалось выгрузить журнал', 'error'))
            .finally(() => setExporting(false));
    }, [apiBaseUrl, cabinet, dateFrom, dateTo, headers, toast]);

    // Смена фильтра возвращает на первую страницу: иначе после сужения выборки
    // человек оказывается на пустой десятой странице и решает, что данных нет.
    useEffect(() => { setPage(0); }, [cabinet, result, dateFrom, dateTo]);

    const cabinetOptions = useMemo(() => ([
        { value: '', label: 'Все кабинеты' },
        ...((health?.cabinets || []).map((c) => ({ value: c.code, label: c.title }))),
    ]), [health]);

    const pageCount = Math.max(1, Math.ceil((journal.total || 0) / PAGE_SIZE));

    // ── разметка ─────────────────────────────────────────────────────────

    if (!schemaReady) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-6">
                <div className={`${iosCard} p-6 text-slate-600`}>
                    Раздел ещё разворачивается. Обновите страницу через минуту.
                </div>
            </div>
        );
    }

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="p-4 sm:p-6 space-y-5">
            <header className="flex items-center justify-between gap-3">
                <div>
                    <h1 className="text-[19px] font-semibold text-slate-900">Лиды OLX</h1>
                    <p className="text-[13px] text-slate-500">
                        Отклики из чатов кабинетов OLX, перенесённые в amoCRM
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {/* Выгрузка — требование раздела 7 ТЗ: журнал за произвольный
                        период. Кнопка живёт рядом с фильтрами по смыслу: выгружается
                        ровно то, что сейчас на экране. */}
                    {tab === 'journal' && (
                        <button
                            type="button"
                            onClick={exportJournal}
                            disabled={exporting}
                            className={`${iosBtnGhost} disabled:opacity-40 active:scale-[0.98]`}
                        >
                            <Download size={15} />
                            {exporting ? 'Готовим…' : 'Выгрузить'}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={() => { loadHealth(); if (tab === 'journal') loadJournal(); else loadSummary(); }}
                        className={`${iosBtnGhost} active:scale-[0.98]`}
                    >
                        <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                        Обновить
                    </button>
                </div>
            </header>

            <CabinetStrip
                health={health}
                canManage={!!capabilities?.can_manage_cabinets}
                onConnect={setConnecting}
            />

            {capabilities?.can_manage_cabinets && (
                <AlertChatsPicker
                    chats={chats}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onSaved={loadChats}
                    toast={toast}
                />
            )}

            <ConnectCabinetModal
                cabinet={connecting}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onClose={() => setConnecting(null)}
                onDone={() => { setConnecting(null); loadHealth(); }}
                toast={toast}
            />

            <div className="flex flex-wrap items-end gap-3">
                <IosSegmented
                    value={tab}
                    onChange={setTab}
                    options={[
                        { value: 'journal', label: 'Журнал' },
                        { value: 'summary', label: 'Сводка за день' },
                    ]}
                />
                <div className="flex flex-wrap items-end gap-3 ml-auto">
                    {tab === 'journal' && (
                        <>
                            <label className="block">
                                <span className={iosGroupLabel}>С</span>
                                <input type="date" value={dateFrom} className={iosInput}
                                    onChange={(e) => setDateFrom(e.target.value)} />
                            </label>
                            <div className="w-44">
                                <span className={iosGroupLabel}>Кабинет</span>
                                <CustomSelect value={cabinet} onChange={setCabinet}
                                    options={cabinetOptions} />
                            </div>
                            <div className="w-44">
                                <span className={iosGroupLabel}>Исход</span>
                                <CustomSelect value={result} onChange={setResult}
                                    options={RESULTS} />
                            </div>
                        </>
                    )}
                    <label className="block">
                        <span className={iosGroupLabel}>{tab === 'journal' ? 'По' : 'День'}</span>
                        <input type="date" value={dateTo} className={iosInput}
                            onChange={(e) => setDateTo(e.target.value)} />
                    </label>
                </div>
            </div>

            {tab === 'journal'
                ? (
                    <JournalTable
                        items={journal.items}
                        loading={loading}
                        page={page}
                        pageCount={pageCount}
                        total={journal.total}
                        onPage={setPage}
                    />
                )
                : <SummaryTable summary={summary} loading={loading} />}
        </div>
    );
};

/* Полоса кабинетов. Отвечает на главный вопрос раздела до всякой прокрутки. */
const CabinetStrip = ({ health, canManage, onConnect }) => {
    if (!health) return null;
    const stale = health.stale || [];
    return (
        <section className="space-y-2">
            {stale.length > 0 && (
                <div className="flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-[13px] text-amber-800 ring-1 ring-amber-200">
                    <AlertTriangle size={15} />
                    {/* Порог — 15 минут из ТЗ: дольше робот молчать не должен. */}
                    Не опрашивались дольше {health.idle_minutes} мин: {stale.join(', ')}
                </div>
            )}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {(health.cabinets || []).map((cab) => (
                    <div key={cab.code}
                        className={`${iosCard} px-3 py-2.5 ${cab.is_stale && cab.is_enabled ? 'ring-amber-300' : ''}`}>
                        <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-[13.5px] font-medium text-slate-900">
                                {cab.title}
                            </span>
                            <IosBadge tone={STATE_TONE[cab.state] || 'slate'}>
                                {STATE_LABEL[cab.state] || cab.state}
                            </IosBadge>
                        </div>
                        <div className="mt-1 text-[12px] tabular-nums text-slate-500">
                            опрос {fmtAgo(cab.last_poll_at)}
                        </div>
                        {cab.last_error && (
                            <div className="mt-1 line-clamp-2 text-[11.5px] text-rose-600">
                                {cab.last_error}
                            </div>
                        )}
                        {/* Кнопка показывается только там, где она что-то меняет:
                            кабинету без согласия владельца и только админу. */}
                        {canManage && cab.is_configured && cab.state === 'needs_auth' && (
                            <button
                                type="button"
                                onClick={() => onConnect(cab)}
                                className="mt-2 text-[12.5px] font-medium text-blue-600 underline-offset-2 hover:underline active:scale-[0.98]"
                            >
                                Подключить кабинет
                            </button>
                        )}
                    </div>
                ))}
            </div>
        </section>
    );
};

/* Куда слать отбивку робота.
 *
 * Свой реестр групп раздел не заводит: те, куда добавлен бот, уже копятся в
 * общей таблице портала, и из неё же берут списки «Обращения» и «Бот
 * опозданий». Здесь только выбор.
 *
 * Блок свёрнут в одну строку и раскрывается по нажатию: настраивают его один
 * раз, а место на экране он занимал бы каждый день. По той же причине он виден
 * только тому, кто вправе его менять.
 */
const AlertChatsPicker = ({ chats, apiBaseUrl, headers, onSaved, toast }) => {
    const [open, setOpen] = useState(false);
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState(null);

    const chosen = useMemo(
        () => (chats?.chosen_ids || []).map(Number),
        [chats],
    );
    const value = draft === null ? chosen : draft;

    const options = useMemo(() => (chats?.available || []).map((chat) => ({
        value: Number(chat.chat_id),
        label: chat.title || String(chat.chat_id),
    })), [chats]);

    const lost = (chats?.selected || []).filter((row) => row.is_available === false);

    const save = () => {
        setSaving(true);
        axios.put(`${apiBaseUrl}/api/olx_amo/chats`, { chat_ids: value },
            { headers: headers() })
            .then(() => {
                toast('Адресаты отбивки сохранены', 'success');
                setDraft(null);
                onSaved();
            })
            .catch((err) => toast(
                err?.response?.data?.error || 'Не удалось сохранить', 'error'))
            .finally(() => setSaving(false));
    };

    if (!chats) return null;

    return (
        <section className={`${iosCard} px-4 py-3`}>
            <button
                type="button"
                onClick={() => setOpen((was) => !was)}
                className="flex w-full items-center gap-2 text-left"
            >
                <Send size={15} className="text-slate-400" />
                <span className="text-[13.5px] font-medium text-slate-900">
                    Уведомления о сбоях
                </span>
                <span className="text-[12.5px] text-slate-500">
                    {chosen.length
                        ? `${chosen.length} ${pluralChats(chosen.length)}`
                        : 'не настроены'}
                </span>
                <span className="ml-auto text-[12.5px] text-blue-600">
                    {open ? 'Свернуть' : 'Настроить'}
                </span>
            </button>

            {open && (
                <div className="mt-3 space-y-3">
                    <p className="text-[12.5px] text-slate-500">
                        Выберите группы, куда робот сообщит о простое, потере доступа к
                        кабинету и об ошибках передачи в amoCRM. В списке — те, куда уже
                        добавлен бот. Сообщение уходит только когда что-то изменилось.
                    </p>
                    {options.length ? (
                        <CustomSelect
                            multiple
                            value={value}
                            onChange={setDraft}
                            options={options}
                            placeholder="Выберите группы"
                            searchable
                        />
                    ) : (
                        <p className="text-[12.5px] text-amber-700">
                            Бот пока не добавлен ни в одну группу. Добавьте его туда,
                            где должна приходить отбивка, — группа появится в списке сама.
                        </p>
                    )}
                    {lost.length > 0 && (
                        <p className="text-[12.5px] text-amber-700">
                            Бота больше нет в группах: {lost.map((r) => r.title || r.chat_id).join(', ')}.
                            Отбивка туда не дойдёт.
                        </p>
                    )}
                    <div className="flex justify-end">
                        <button
                            type="button"
                            onClick={save}
                            disabled={saving || draft === null}
                            className={`${iosBtnPrimary} disabled:opacity-40 active:scale-[0.98]`}
                        >
                            {saving ? 'Сохраняем…' : 'Сохранить'}
                        </button>
                    </div>
                </div>
            )}
        </section>
    );
};

const pluralChats = (count) => {
    const tail = count % 100;
    if (tail >= 11 && tail <= 14) return 'групп';
    switch (count % 10) {
        case 1: return 'группа';
        case 2:
        case 3:
        case 4: return 'группы';
        default: return 'групп';
    }
};

/* Подключение кабинета: одноразовый обряд на каждый из девяти.
 *
 * Двумя шагами, а не одной кнопкой, потому что согласие выдаёт ЧЕЛОВЕК в своём
 * браузере: OLX возвращает код на адрес из заявки, и портал этот код увидеть не
 * может. Админ открывает ссылку, подтверждает доступ и приносит код обратно.
 *
 * Главное предупреждение вынесено в текст шага, а не в подсказку под вопросом:
 * согласие выдаёт тот, кто ВОШЁЛ в браузере, а не тот, чей кабинет выбран в
 * списке. Перепутать вход — самая частая ошибка подключения, и заметна она не
 * сразу: токен приедет, робот начнёт читать чужие чаты.
 */
const ConnectCabinetModal = ({ cabinet, apiBaseUrl, headers, onClose, onDone, toast }) => {
    const [url, setUrl] = useState('');
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        setUrl('');
        setCode('');
        setError('');
        if (!cabinet) return;
        axios.get(`${apiBaseUrl}/api/olx_amo/cabinets/${cabinet.code}/authorize`,
            { headers: headers() })
            .then((response) => setUrl(response.data?.url || ''))
            .catch((err) => setError(err?.response?.data?.error
                || 'Не удалось собрать ссылку согласия'));
    }, [cabinet, apiBaseUrl, headers]);

    const save = () => {
        if (!code.trim()) return;
        setBusy(true);
        setError('');
        axios.post(`${apiBaseUrl}/api/olx_amo/cabinets/${cabinet.code}/callback`,
            { code: code.trim() }, { headers: headers() })
            .then(() => {
                toast(`Кабинет «${cabinet.title}» подключён`, 'success');
                onDone();
            })
            .catch((err) => setError(err?.response?.data?.error
                || 'OLX не принял код. Он живёт секунды — получите новый по ссылке выше.'))
            .finally(() => setBusy(false));
    };

    return (
        <IosModal
            open={!!cabinet}
            onClose={onClose}
            title={cabinet ? `Подключение кабинета «${cabinet.title}»` : ''}
            subtitle="Владелец кабинета подтверждает доступ один раз"
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>
                        Отмена
                    </button>
                    <button
                        type="button"
                        onClick={save}
                        disabled={busy || !code.trim()}
                        className={`${iosBtnPrimary} disabled:opacity-40 active:scale-[0.98]`}
                    >
                        {busy ? 'Сохраняем…' : 'Сохранить доступ'}
                    </button>
                </div>
            )}
        >
            <ol className="space-y-4 text-[13.5px] text-slate-700">
                <li>
                    <div className="font-medium text-slate-900">1. Войдите в этот кабинет OLX</div>
                    <p className="mt-0.5 text-slate-500">
                        Согласие выдаёт тот аккаунт, под которым выполнен вход в браузере,
                        а не тот, что выбран здесь. Если войти под другим кабинетом,
                        робот начнёт читать его чаты.
                    </p>
                </li>
                <li>
                    <div className="font-medium text-slate-900">2. Откройте экран согласия</div>
                    {url ? (
                        <a
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            className={`${iosBtnGhost} mt-1.5 inline-flex active:scale-[0.98]`}
                        >
                            <ExternalLink size={15} />
                            Подтвердить доступ в OLX
                        </a>
                    ) : (
                        <p className="mt-0.5 text-slate-400">Собираем ссылку…</p>
                    )}
                </li>
                <li>
                    <div className="font-medium text-slate-900">3. Вставьте код</div>
                    <p className="mt-0.5 text-slate-500">
                        Можно вставить сам код, а можно скопировать адрес из строки
                        браузера целиком — нужное достанем сами. Код живёт считанные
                        секунды, поэтому вставляйте сразу.
                    </p>
                    <input
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                        placeholder="код или адрес из строки браузера"
                        className={`${iosInput} mt-1.5`}
                    />
                </li>
            </ol>
            {error && (
                <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-700 ring-1 ring-rose-200">
                    {error}
                </div>
            )}
        </IosModal>
    );
};

const JournalTable = ({ items, loading, page, pageCount, total, onPage }) => (
    <section className={`${iosCard} overflow-hidden`}>
        <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
                <thead className="bg-slate-50/80 text-left text-[12px] uppercase tracking-wide text-slate-500">
                    <tr>
                        <th className="px-3 py-2 font-medium">Время отклика</th>
                        <th className="px-3 py-2 font-medium">Кабинет</th>
                        <th className="px-3 py-2 font-medium">Телефон</th>
                        <th className="px-3 py-2 font-medium">Исход</th>
                        <th className="px-3 py-2 font-medium">Доставка</th>
                        <th className="px-3 py-2 font-medium">Сделка</th>
                    </tr>
                </thead>
                <tbody>
                    {loading && !items.length && (
                        <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-400">
                            <Loader2 size={18} className="mx-auto animate-spin" />
                        </td></tr>
                    )}
                    {!loading && !items.length && (
                        <tr><td colSpan={6} className="px-3 py-8 text-center text-slate-400">
                            За выбранный период обращений нет
                        </td></tr>
                    )}
                    {items.map((row) => (
                        <tr key={row.id} className="border-t border-slate-100">
                            <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">
                                {fmtTime(row.message_at || row.created_at)}
                            </td>
                            <td className="px-3 py-2 text-slate-700">{row.cabinet_title}</td>
                            <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-900">
                                {row.phone ? fmtPhone(row.phone) : (row.phone_raw || '—')}
                            </td>
                            <td className="px-3 py-2">
                                <IosBadge tone={RESULT_TONE[row.result] || 'slate'}>
                                    {RESULT_LABEL[row.result] || row.result}
                                </IosBadge>
                                {row.error_text && (
                                    <div className="mt-1 max-w-md text-[11.5px] text-rose-600">
                                        {row.error_text}
                                    </div>
                                )}
                            </td>
                            {/* Промах по SLA — единственное, что здесь красится:
                                ТЗ задаёт минуту как целевой показатель. */}
                            <td className={`whitespace-nowrap px-3 py-2 tabular-nums ${
                                row.latency_ms > SLA_MS ? 'text-amber-700' : 'text-slate-600'}`}>
                                {fmtLatency(row.latency_ms)}
                            </td>
                            <td className="px-3 py-2 tabular-nums text-slate-500">
                                {row.amo_lead_id || '—'}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
        {total > PAGE_SIZE && (
            <div className="border-t border-slate-100 px-3 py-2">
                <IosPager
                    page={page + 1}
                    pageCount={pageCount}
                    total={total}
                    from={page * PAGE_SIZE + 1}
                    to={Math.min((page + 1) * PAGE_SIZE, total)}
                    onPage={(number) => onPage(number - 1)}
                    unit="обращения"
                />
            </div>
        )}
    </section>
);

const SummaryTable = ({ summary, loading }) => {
    if (loading && !summary) {
        return (
            <div className={`${iosCard} p-8 text-center text-slate-400`}>
                <Loader2 size={18} className="mx-auto animate-spin" />
            </div>
        );
    }
    const rows = summary?.cabinets || [];
    const totals = summary?.totals || {};
    return (
        <section className={`${iosCard} overflow-hidden`}>
            <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                    <thead className="bg-slate-50/80 text-left text-[12px] uppercase tracking-wide text-slate-500">
                        <tr>
                            <th className="px-3 py-2 font-medium">Кабинет</th>
                            <th className="px-3 py-2 text-right font-medium">Обращений</th>
                            <th className="px-3 py-2 text-right font-medium">Сделок</th>
                            <th className="px-3 py-2 text-right font-medium">Повторов</th>
                            <th className="px-3 py-2 text-right font-medium">На проверку</th>
                            <th className="px-3 py-2 text-right font-medium">Автоответов</th>
                            <th className="px-3 py-2 text-right font-medium">Ошибок</th>
                            <th className="px-3 py-2 text-right font-medium">Вне минуты</th>
                            <th className="px-3 py-2 text-right font-medium">Среднее</th>
                            <th className="px-3 py-2 text-right font-medium">Максимум</th>
                        </tr>
                    </thead>
                    <tbody>
                        {!rows.length && (
                            <tr><td colSpan={10} className="px-3 py-8 text-center text-slate-400">
                                За этот день обращений не было
                            </td></tr>
                        )}
                        {rows.map((row) => (
                            <tr key={row.code} className="border-t border-slate-100">
                                <td className="px-3 py-2 text-slate-700">{row.title}</td>
                                <td className="px-3 py-2 text-right tabular-nums">{row.total}</td>
                                <td className="px-3 py-2 text-right tabular-nums font-medium">{row.leads}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{row.duplicates}</td>
                                <td className={`px-3 py-2 text-right tabular-nums ${row.manual ? 'text-amber-700' : 'text-slate-500'}`}>{row.manual}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{row.replies}</td>
                                <td className={`px-3 py-2 text-right tabular-nums ${row.errors ? 'text-rose-600' : 'text-slate-500'}`}>{row.errors}</td>
                                <td className={`px-3 py-2 text-right tabular-nums ${row.sla_missed ? 'text-amber-700' : 'text-slate-500'}`}>{row.sla_missed}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fmtLatency(row.avg_latency_ms)}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fmtLatency(row.max_latency_ms)}</td>
                            </tr>
                        ))}
                    </tbody>
                    {rows.length > 1 && (
                        <tfoot className="border-t border-slate-200 bg-slate-50/60">
                            <tr>
                                <td className="px-3 py-2 font-medium text-slate-700">Итого</td>
                                <td className="px-3 py-2 text-right tabular-nums">{totals.total || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums font-medium">{totals.leads || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.duplicates || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.manual || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.replies || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.errors || 0}</td>
                                <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.sla_missed || 0}</td>
                                <td className="px-3 py-2" />
                                <td className="px-3 py-2" />
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>
        </section>
    );
};

export default OlxLeadsView;
