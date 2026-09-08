import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Search, Loader2, AlertCircle, Send, Check, Lock, Eye, X,
    MessageSquare, Download, Phone, Clock, ImageIcon, Building2, RefreshCw,
} from 'lucide-react';

import ChatThread from '../c2d_eval/ChatThread';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangeCalendar, IosDateRangePicker, rangeLabel } from '../ui/DateRangePicker';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosModal, IosSegmented, IosBadge, IosPager,
} from '../ui/ios';
import {
    kindLabel, roleLabel, formatPhone, formatTime, formatDayShort, formatDayFull,
    dayKeyOf, exportFileName, pluralChats, pluralDays, rangeDays, shiftDaysBack,
    todayISO, EXPORT_MAX_DAYS, KIND_TONE,
} from './journalMeta';

/* Раздел «Чаты водителей» (задача #271).
 *
 * Оператор СЗоВ вводит номер телефона водителя, видит его переписку за двое
 * суток, открывает нужный чат, снимает скриншот средствами системы и жмёт
 * «Передан» — в этот же чат уходит внутренний комментарий Chat2Desk, который
 * водитель не видит, а чат-менеджер видит у себя в рабочем окне.
 *
 * ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ:
 *
 * * Кнопки «скачать картинкой». Постановка говорит «оператор может открыть
 *   нужный чат и сделать скриншот» — снимает человек, система показывает. Две
 *   трети чатов содержат медиа с внешнего домена, и клиентский рендер молча
 *   выбросил бы из картинки именно фотографии, ради которых её и снимают.
 * * Поиска «по мере ввода». Каждый поиск может стоить обращения к вендору, чей
 *   месячный лимит общий с ночным синком метрик отдела. Поиск — явное действие
 *   по Enter или кнопке, а не побочный эффект набора текста.
 * * Автообновления ленты. По той же причине: «Обновить» — кнопка, а не таймер.
 *   Она идёт мимо пятиминутного кеша и потому тратит поиск из дневного лимита,
 *   так что жать её должен человек, которому переписка нужна свежей прямо
 *   сейчас, — после «Передан» или пока водитель отвечает на линии.
 *
 * Лента переписки — общий ChatThread из «Журнала оценок»: он уже разбирает
 * внутренние заметки, автоответы и системные строки, рисует фото с лайтбоксом и
 * покрыт тёмной темой. Второй ленты в проекте быть не должно.
 */

const emptyResult = { chats: [], phone: '', clientId: null, clientName: '',
                      notFound: false, fetchedAt: null };

const DriverChatsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* showToast приходит новой функцией на каждый рендер App — известная ловушка
       портала. Держим её в ref, чтобы она не попала в зависимости эффектов и не
       вызывала повторные запросы. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((message, kind) => {
        if (toastRef.current) toastRef.current(message, kind);
    }, []);

    const [context, setContext] = useState(null);
    const [tab, setTab] = useState('search');

    const [query, setQuery] = useState('');
    const [searching, setSearching] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [searchError, setSearchError] = useState('');
    const [result, setResult] = useState(emptyResult);
    const [activeKey, setActiveKey] = useState(null);
    const [handedOff, setHandedOff] = useState({});
    /* Свои заметки «Передан», которых Chat2Desk ещё не показывает в ленте.
       Вендор принимает заметку мгновенно и сразу возвращает её id, но в выборке
       сообщений отдаёт примерно через минуту (замер 07.09.2026: отправлена в
       10:05:23, появилась в 10:06:19, и двадцать нажатий «Обновить» между ними
       возвращали ленту без неё). Ждать вендора незачем — про заметку известно
       всё, и сервер отдаёт её готовым сообщением прямо в ответе на отправку.
       Держим до тех пор, пока та же заметка не приедет от вендора: id у копий
       один, и склейка идёт по нему.

       Рядом с сообщением лежит clientId, и это НЕ перестраховка: адрес заметки —
       парк, а парк у водителей общий (у «Ясной поляны» их тысячи). Без привязки
       к водителю заметка, отправленная одному, показалась бы в чате следующего
       найденного по тому же парку — и уехала бы туда на скриншоте. */
    const [pendingNotes, setPendingNotes] = useState([]);

    const [handoffOpen, setHandoffOpen] = useState(false);
    const [handoffNote, setHandoffNote] = useState('');
    const [handoffSending, setHandoffSending] = useState(false);

    const inputRef = useRef(null);

    // ── Контекст раздела ────────────────────────────────────────────────────
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch(`${apiBaseUrl}/api/driver_chats/context`, {
                    headers: headers(), credentials: 'include',
                });
                const data = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setSearchError(data.error || 'Раздел недоступен');
                    return;
                }
                setContext(data);
            } catch {
                if (!cancelled) setSearchError('Не удалось открыть раздел');
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    useEffect(() => { inputRef.current?.focus(); }, []);

    const canViewJournal = Boolean(context?.capabilities?.can_view_journal);

    // ── Поиск и обновление ──────────────────────────────────────────────────
    //
    // Загрузчик один на обе кнопки: запрос, разбор ответа и коды ошибок у них
    // общие, а расходятся они ровно в двух местах — обновление удерживает
    // открытый чат вместе с отметками «передан» и не стирает ленту, если запрос
    // не удался. Человек жмёт «Обновить» с чатом на экране, и потерять этот чат
    // из-за упавшей сети больнее, чем не увидеть новых сообщений.
    const load = useCallback(async (rawPhone, { refresh = false } = {}) => {
        const phone = String(rawPhone || '').trim();
        if (!phone || searching || refreshing) return;
        const setBusy = refresh ? setRefreshing : setSearching;
        setBusy(true);
        if (!refresh) setSearchError('');
        try {
            const response = await fetch(
                `${apiBaseUrl}/api/driver_chats/search?phone=${encodeURIComponent(phone)}`
                + (refresh ? '&refresh=1' : ''),
                { headers: headers(), credentials: 'include' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (refresh) {
                    toast(data.error || 'Не удалось обновить переписку', 'error');
                    return;
                }
                setResult(emptyResult);
                setSearchError(data.error || 'Не удалось найти чаты');
                return;
            }
            const chats = data.chats || [];
            setSearchError('');
            // Заметка доехала от вендора — своя копия больше не нужна. Ключ
            // склейки тот же, что на сервере: id сообщения.
            const arrived = new Set(
                chats.flatMap((chat) => (chat.messages || []).map((m) => m.id)));
            setPendingNotes((prev) => {
                const left = prev.filter((item) => !arrived.has(item.message.id));
                return left.length === prev.length ? prev : left;
            });
            setResult({
                chats,
                phone: data.phone || phone,
                clientId: data.client_id ?? null,
                clientName: data.client_name || '',
                notFound: Boolean(data.not_found),
                truncated: Boolean(data.truncated),
                fetchedAt: data.fetched_at || null,
            });
            if (refresh) {
                // Открытый чат остаётся открытым. Съехать он может только если
                // парк выпал из двухсуточного окна, — тогда возвращаемся к
                // самому свежему живому, а не в пустоту.
                setActiveKey((prev) => (chats.some((chat) => chatKey(chat) === prev)
                    ? prev : firstLiveKey(chats)));
            } else {
                setHandedOff({});
                // Открываем самый свежий живой чат сразу: в 9 случаях из 10 нужен
                // именно он, и лишний клик здесь — это лишний клик в каждом звонке.
                setActiveKey(firstLiveKey(chats));
            }
            if (typeof data.searches_left === 'number') {
                setContext((prev) => (prev ? {
                    ...prev,
                    limits: { ...(prev.limits || {}), left_today: data.searches_left },
                } : prev));
            }
        } catch {
            if (refresh) {
                toast('Сеть недоступна. Переписка не обновлена', 'error');
                return;
            }
            setResult(emptyResult);
            setSearchError('Сеть недоступна. Попробуйте ещё раз');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, headers, refreshing, searching, toast]);

    const runSearch = useCallback(() => load(query, { refresh: false }), [load, query]);
    const runRefresh = useCallback(() => load(result.phone, { refresh: true }),
                                   [load, result.phone]);

    /* Свои заметки вклеиваем в САМ чат, а не только в ленту: у чата есть ещё
       счётчик сообщений и время последнего — покажи заметку в переписке, но
       оставь «4 сообщ.» под ней, и человек поверит счётчику. Когда вендор
       отдаст свою копию, она придёт с тем же id и вытеснит нашу здесь же. */
    const chats = useMemo(() => {
        const base = result.chats || [];
        if (!pendingNotes.length) return base;
        return base.map((chat) => {
            const known = new Set((chat.messages || []).map((m) => m.id));
            const mine = pendingNotes
                .filter((item) => item.clientId === result.clientId
                    && !known.has(item.message.id)
                    && noteBelongsTo(item.message, chat))
                .map((item) => item.message);
            if (!mine.length) return chat;
            const messages = [...(chat.messages || []), ...mine].sort(
                (a, b) => String(a.created || '').localeCompare(String(b.created || '')));
            return {
                ...chat,
                messages,
                messages_count: (chat.messages_count || 0) + mine.length,
                last_at: messages[messages.length - 1]?.created || chat.last_at,
            };
        });
    }, [result.chats, result.clientId, pendingNotes]);

    const activeChat = useMemo(
        () => chats.find((chat) => chatKey(chat) === activeKey) || chats[0] || null,
        [chats, activeKey]);

    /* Открытие чата пишется в журнал — это и есть ответ на вопрос «кто смотрел
       переписку». Отправляем «в фон»: ответ сервера экрану не нужен, а ждать
       его значило бы тормозить открытие ленты. Ошибку не показываем человеку —
       он не может на неё повлиять, — но и не глотаем: она уходит в консоль. */
    const logOpen = useCallback((chat) => {
        if (!chat || !result.phone) return;
        fetch(`${apiBaseUrl}/api/driver_chats/open`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...headers() },
            credentials: 'include',
            body: JSON.stringify({
                phone: result.phone,
                client_id: result.clientId,
                // Адрес просмотра — парк, а не обращение: после склейки
                // обращений внутри чата несколько, и любое присланное было бы
                // произвольным.
                channel_id: chat.channel_id,
                dialog_id: chat.dialog_id,
                channel_name: chat.channel_name,
                messages_count: chat.messages_count,
            }),
        }).catch(() => { /* журнал не должен мешать работе оператора */ });
    }, [apiBaseUrl, headers, result.phone, result.clientId]);

    /* Запись «открыл переписку» — ОДНА на чат, и делает её эффект ниже.
       Раньше клик по чату логировал напрямую И будил этот же эффект сменой
       activeChat, отчего в журнале появлялись пары строк с разницей в
       миллисекунды (видно в проде 04.09: 10:06:43.525 и 10:06:43.620) и врал
       счётчик «действий» в сводке. */
    const autoLogged = useRef(null);
    useEffect(() => {
        if (!activeChat || !result.phone) return;
        const key = `${result.phone}:${chatKey(activeChat)}`;
        if (autoLogged.current === key) return;
        autoLogged.current = key;
        logOpen(activeChat);
    }, [activeChat, result.phone, logOpen]);

    // ── «Передан» ───────────────────────────────────────────────────────────
    const sendHandoff = useCallback(async () => {
        if (!activeChat || handoffSending) return;
        setHandoffSending(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/driver_chats/handoff`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...headers() },
                credentials: 'include',
                body: JSON.stringify({
                    phone: result.phone,
                    client_id: result.clientId,
                    channel_id: activeChat.channel_id,
                    dialog_id: activeChat.dialog_id,
                    channel_name: activeChat.channel_name,
                    note: handoffNote.trim(),
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                toast(data.error || 'Не удалось отправить комментарий', 'error');
                return;
            }
            setHandedOff((prev) => ({ ...prev, [chatKey(activeChat)]: true }));
            setHandoffOpen(false);
            setHandoffNote('');
            /* Показываем заметку немедленно — сервер вернул её готовым
               сообщением. Дожидаться вендора нельзя: в списке сообщений он
               покажет её примерно через минуту, и всё это время экран выглядел
               бы как «не отправилось», а человек жал бы «Передан» второй раз —
               отозвать заметку через API вендора невозможно. */
            if (data.message && data.message.id != null) {
                setPendingNotes((prev) => (
                    prev.some((item) => item.message.id === data.message.id)
                        ? prev
                        : [...prev, { clientId: result.clientId, message: data.message }]));
            }
            toast('Комментарий отправлен — он уже в ленте', 'success');
        } catch {
            toast('Сеть недоступна. Комментарий не отправлен', 'error');
        } finally {
            setHandoffSending(false);
        }
    }, [activeChat, apiBaseUrl, handoffNote, handoffSending, headers, result, toast]);

    const snapshot = useMemo(() => (activeChat ? {
        messages: activeChat.messages || [],
        operator_name: activeChat.operator_name || null,
    } : null), [activeChat]);

    /* Состояние экрана: пока чатов нет, поиск стоит посреди страницы с
       объяснением; как только они появились — уезжает наверх и уступает место
       переписке. Считаем по чатам, а не по «был ли поиск»: на ненайденном
       номере объяснение должно остаться на месте, человек сейчас же наберёт
       следующий. */
    const hasChats = chats.length > 0;

    const limits = context?.limits || {};
    const leftToday = typeof limits.left_today === 'number'
        ? limits.left_today
        : (typeof limits.searches_per_day === 'number' && typeof limits.used_today === 'number'
            ? Math.max(0, limits.searches_per_day - limits.used_today)
            : null);

    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {canViewJournal && (
                <div className="flex justify-center">
                    <IosSegmented
                        value={tab}
                        onChange={setTab}
                        ariaLabel="Разделы"
                        options={[
                            { value: 'search', label: 'Поиск чатов' },
                            { value: 'journal', label: 'Журнал' },
                        ]}
                    />
                </div>
            )}

            {tab === 'journal' && canViewJournal ? (
                <JournalPanel apiBaseUrl={apiBaseUrl} headers={headers} toast={toast} />
            ) : (
                <>
                    {/* Поиск — ОДИН узел на оба состояния экрана. «Уход наверх»
                        сделан сворачиванием объяснения и поджатием отступа на
                        том же самом поле, а не подменой одного блока другим:
                        подмена размонтировала бы поле вместе с фокусом и
                        кареткой, и следующий номер пришлось бы начинать с
                        щелчка мышью. */}
                    <div className={`transition-[padding] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none ${
                        hasChats ? 'pt-0' : 'pt-[3vh] sm:pt-[6vh]'}`}>
                        <SearchStage
                            compact={hasChats}
                            value={query}
                            onChange={setQuery}
                            onSubmit={runSearch}
                            searching={searching}
                            inputRef={inputRef}
                            leftToday={leftToday}
                            fetchedAt={result.fetchedAt}
                        />

                        {searchError && (
                            <div className={`${iosCard} mx-auto mt-4 flex max-w-[640px] items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                                <span>{searchError}</span>
                            </div>
                        )}

                        {!searchError && result.phone && !chats.length && (
                            <div className={`${iosCard} mx-auto mt-4 max-w-[640px] px-6 py-8 text-center`}>
                                <MessageSquare size={26} className="mx-auto mb-3 text-slate-300" />
                                <div className="text-sm font-medium text-slate-700">
                                    {result.notFound
                                        ? 'Такого номера нет в переписках'
                                        : 'За последние 2 дня этот водитель не писал'}
                                </div>
                                <div className="mt-1 text-[13px] text-slate-500">
                                    {result.notFound
                                        ? 'Проверьте номер: возможно, водитель писал с другого.'
                                        : 'Более ранняя переписка в разделе не показывается.'}
                                </div>
                                {/* Единственная кнопка обновления живёт в шапке
                                    переписки, а её здесь нет. Водитель может
                                    написать прямо сейчас, пока оператор смотрит на
                                    этот экран, — без кнопки пришлось бы искать номер
                                    заново, то есть тратить второй поиск из
                                    дневного лимита на то же самое. */}
                                <button
                                    type="button"
                                    onClick={runRefresh}
                                    disabled={refreshing}
                                    className={`${iosBtnSecondary} mt-4 disabled:opacity-40`}
                                >
                                    <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
                                    {refreshing ? 'Обновляем…' : 'Обновить'}
                                </button>
                            </div>
                        )}
                    </div>

                    {hasChats && (
                        /* Список парков СЛЕВА, переписка справа — расположение
                           владельца от 08.09.2026 (в тот же день он попросил
                           сначала правую сторону, посмотрел и вернул левую).
                           Классы `order` больше не нужны: и на телефоне, и на
                           широком экране список идёт первым — парк выбирают
                           раньше, чем читают. */
                        <div className="grid animate-card-open gap-4 lg:grid-cols-[308px_minmax(0,1fr)]">
                            <ChatList
                                chats={chats}
                                activeKey={chatKey(activeChat)}
                                onPick={(chat) => setActiveKey(chatKey(chat))}
                                handedOff={handedOff}
                                driverName={result.clientName}
                                phone={result.phone}
                                truncated={result.truncated}
                            />
                            <ChatPanel
                                chat={activeChat}
                                snapshot={snapshot}
                                phone={result.phone}
                                driverName={result.clientName}
                                handedOff={activeChat ? handedOff[chatKey(activeChat)] : false}
                                onHandoff={() => setHandoffOpen(true)}
                                onRefresh={runRefresh}
                                refreshing={refreshing}
                                fetchedAt={result.fetchedAt}
                            />
                        </div>
                    )}
                </>
            )}

            <HandoffModal
                open={handoffOpen}
                onClose={() => (handoffSending ? null : setHandoffOpen(false))}
                note={handoffNote}
                onNote={setHandoffNote}
                sending={handoffSending}
                onSend={sendHandoff}
                maxLength={context?.comment_max_length || 500}
                authorName={context?.me?.name || ''}
            />
        </div>
    );
};

/* Ключ чата — таксопарк (канал), а если его нет — диалог. Тот же порядок, что в
   chat2desk.chat_key на бэкенде: на этом ключе держатся выбранный чат, отметка
   «передан» и защита от повторной записи в журнал, и разъехавшись, они пометят
   переданным чужой чат. */
function chatKey(chat) {
    if (chat?.channel_id) return `c${chat.channel_id}`;
    if (chat?.dialog_id) return `d${chat.dialog_id}`;
    return 'x0';
}

/* Та ли это переписка. Заметка адресована парку (каналу), а если канала у неё
   нет — диалогу: тот же порядок, что в chatKey и в chat2desk.chat_key на
   сервере. Без этой проверки заметка, отправленная в один парк, показалась бы
   в чате другого — и уехала бы туда на скриншоте. */
function noteBelongsTo(note, chat) {
    if (note?.channelId && chat?.channel_id) {
        return String(note.channelId) === String(chat.channel_id);
    }
    if (note?.dialogId && chat?.dialog_id) {
        return String(note.dialogId) === String(chat.dialog_id);
    }
    return false;
}

/* Какой чат открывать, когда выбирать приходится за человека. Служебные (одно
   меню парка и опрос «оцените оператора») пропускаем: искали не их. Null, а не
   chatKey(undefined): 'x0' пометил бы выбранным чат, которого нет. */
function firstLiveKey(chats) {
    const first = (chats || []).find((chat) => !chat.is_service) || (chats || [])[0];
    return first ? chatKey(first) : null;
}

// ── Поисковая строка ────────────────────────────────────────────────────────
//
// Экран поиска живёт двумя состояниями ОДНОГО узла (решение владельца
// 08.09.2026):
//
//   пусто        — поле посреди страницы, над ним объяснение «как это работает»;
//   есть чаты    — объяснение свернулось, поле уехало наверх и стало у́же, под
//                  ним переписка и список парков.
//
// Почему один узел, а не два блока по условию: подмена размонтировала бы поле
// вместе с фокусом и кареткой, и следующий номер человек начинал бы с щелчка
// мышью. Свернуть объяснение и поджать отступ CSS умеет сам, переход выходит
// настоящим.
//
// Три шага — это и есть просьба «объяснение, как что работает». Стоят они ПОД
// полем: «поиск по середине сверху» значит, что выше поля не должно быть ничего,
// кроме названия раздела. Формулировки честные: «снимите экран» вместо «нажмите
// скриншот» — снимок делается средствами системы и разделу не виден (та же
// честность, что в подписях журнала).

const SEARCH_STEPS = [
    {
        title: 'Введите номер',
        text: 'Как удобно: 87071234567, 7071234567 или иностранный — +998 90 123 45 67. Enter — и мы найдём переписку за последние двое суток.',
    },
    {
        title: 'Выберите таксопарк',
        text: 'По чату на каждый таксопарк. Вся переписка двух суток с этим парком лежит внутри одного чата.',
    },
    {
        title: 'Передайте чат-менеджеру',
        text: 'Снимите экран средствами системы и нажмите «Передан» — в чат уйдёт внутренний комментарий, водитель его не увидит.',
    },
];

const WINDOW_HINT_SHORT = 'Переписка за последние 2 дня';

/* Сворачивание заголовка и объяснения. Кривая та же, что у раскрытия модалок
   портала (IOS_MODAL_MOTION): быстрый старт, мягкое приземление — характер
   macOS. `motion-reduce` обязателен: раздел открывают по многу раз за смену. */
const COLLAPSE = 'overflow-hidden transition-all duration-500'
    + ' ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none';

const SearchStage = ({ compact, value, onChange, onSubmit, searching, inputRef,
                       leftToday, fetchedAt }) => {
    /* Рост поля при наведении курсора в него — та самая «небольшая анимация».
       Держим её состоянием, а не `focus-within`: у Tailwind нет варианта,
       который дотянулся бы отсюда и до ширины обёртки, и до тени, и до высоты
       строки одновременно. */
    const [focused, setFocused] = useState(false);

    return (
        <div>
            {/* Заголовок над полем и объяснение под ним сворачиваются по
                высоте, а не исчезают: исчезновение рывком читается как «страница
                перескочила», а сворачивание — как «поиск переехал наверх», о чём
                и просил владелец. Само поле между ними остаётся на месте. */}
            <div aria-hidden={compact} className={`${COLLAPSE} ${
                compact ? 'max-h-0 -translate-y-2 opacity-0' : 'max-h-24 translate-y-0 opacity-100'}`}>
                <h2 className="text-center text-[22px] font-semibold tracking-tight text-slate-900">
                    Чаты водителей
                </h2>
            </div>

            {/* Само поле. Ширина и высота меняются и от состояния экрана, и от
                фокуса — отсюда четыре ветки вместо двух. Кнопка «Найти» стоит
                ВНУТРИ поля, как в поиске macOS: снаружи она делала бы из одного
                предмета два, и «строка увеличилась» читалось бы хуже. */}
            <div
                className={`mx-auto w-full transition-[max-width] duration-300 ease-out motion-reduce:transition-none ${
                    compact
                        ? (focused ? 'max-w-[560px]' : 'max-w-[480px]')
                        : (focused ? 'max-w-[680px]' : 'max-w-[600px]')} ${compact ? 'mt-0' : 'mt-3'}`}
            >
                <div
                    className={`relative flex items-center rounded-2xl bg-white transition-all duration-300 ease-out motion-reduce:transition-none ${
                        focused
                            ? 'shadow-[0_12px_34px_-14px_rgba(15,23,42,0.35)] ring-2 ring-blue-500/70'
                            : 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70'} ${
                        compact ? (focused ? 'h-12' : 'h-11') : (focused ? 'h-[58px]' : 'h-[52px]')}`}
                >
                    <Search
                        size={17}
                        className={`pointer-events-none absolute left-4 transition-colors ${
                            focused ? 'text-blue-500' : 'text-slate-400'}`}
                    />
                    <input
                        ref={inputRef}
                        value={value}
                        onChange={(event) => onChange(event.target.value)}
                        onKeyDown={(event) => { if (event.key === 'Enter') onSubmit(); }}
                        onFocus={() => setFocused(true)}
                        onBlur={() => setFocused(false)}
                        inputMode="tel"
                        placeholder="Номер телефона"
                        aria-label="Номер телефона водителя"
                        className={`h-full w-full rounded-2xl bg-transparent pl-11 pr-[112px] tabular-nums text-slate-900 outline-none placeholder:text-slate-400 ${
                            compact ? 'text-[14.5px]' : 'text-[15.5px]'}`}
                    />
                    <button
                        type="button"
                        onClick={onSubmit}
                        disabled={searching || !value.trim()}
                        className={`${iosBtnPrimary} absolute right-1.5 top-1/2 h-9 -translate-y-1/2 px-3.5 py-0 disabled:opacity-40`}
                    >
                        {searching ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                        {searching ? 'Ищем…' : 'Найти'}
                    </button>
                </div>

                <div className="mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[12px] text-slate-500">
                    <span>{WINDOW_HINT_SHORT}</span>
                    {/* Время снятия ленты — с сервера: на кеше оно на несколько минут
                        старше часов браузера, и подпись «обновлено сейчас» под
                        пятиминутным снимком была бы враньём ровно там, где человек ей
                        поверит. */}
                    {Boolean(fetchedAt) && (
                        <span className="tabular-nums">Обновлено в {formatTime(fetchedAt)}</span>
                    )}
                    {typeof leftToday === 'number' && leftToday <= 20 && (
                        <span className="tabular-nums text-amber-600">
                            Осталось поисков сегодня: {leftToday}
                        </span>
                    )}
                </div>
            </div>

            {/* Объяснение — ПОД полем: «поиск по середине сверху» значит, что
                выше него не должно быть ничего, кроме названия раздела. Потолок
                высоты взят с запасом на перенос трёх карточек в столбик. */}
            <div aria-hidden={compact} className={`${COLLAPSE} ${
                compact ? 'max-h-0 -translate-y-2 opacity-0' : 'max-h-[420px] translate-y-0 opacity-100'}`}>
                <ol className="mx-auto mt-6 grid max-w-3xl gap-2 text-left sm:grid-cols-3">
                    {SEARCH_STEPS.map((step, index) => (
                        <li key={step.title} className="rounded-2xl bg-slate-500/[0.045] px-3.5 py-3">
                            <div className="flex items-center gap-2">
                                <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-slate-900/85 text-[10.5px] font-semibold text-white">
                                    {index + 1}
                                </span>
                                <span className="text-[13px] font-semibold text-slate-800">{step.title}</span>
                            </div>
                            <p className="mt-1 text-[12px] leading-snug text-slate-500">{step.text}</p>
                        </li>
                    ))}
                </ol>
            </div>
        </div>
    );
};

// ── Список чатов и панель ───────────────────────────────────────────────────
//
// Чат = таксопарк, поэтому строка списка — это парк, а не обращение. Раньше
// список резал переписку по обращениям, и один разговор с одним парком выглядел
// как несколько разных чатов; теперь строка ровно одна на парк, а вся история
// двух суток лежит внутри.
//
// Стоит список СЛЕВА от переписки. На телефоне он поднимается над перепиской и
// получает свой потолок высоты: во весь экран он оттолкнул бы ленту за нижний
// край, а выбирают парк раньше, чем читают.

const ChatList = ({ chats, activeKey, onPick, handedOff, driverName, phone, truncated }) => (
    <div className={`${iosCard} flex max-h-[44vh] flex-col overflow-hidden lg:max-h-[76vh]`}>
        <div className="border-b border-slate-200/70 px-4 py-3">
            <div className="truncate text-[15px] font-semibold text-slate-900">
                {driverName || formatPhone(phone)}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-[12px] text-slate-500">
                {driverName && <span className="tabular-nums">{formatPhone(phone)}</span>}
                <span>{pluralChats(chats.length)}</span>
            </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {chats.map((chat) => {
                const key = chatKey(chat);
                const active = key === activeKey;
                return (
                    <button
                        key={key}
                        type="button"
                        onClick={() => onPick(chat)}
                        className={`mb-1 w-full rounded-xl px-3 py-2.5 text-left transition-all active:scale-[0.98] ${
                            active ? 'bg-blue-500/10 ring-1 ring-blue-500/25' : 'hover:bg-slate-500/5'
                        }`}
                    >
                        <div className="flex items-baseline gap-2">
                            <span className={`min-w-0 flex-1 truncate text-[13.5px] font-semibold ${
                                active ? 'text-blue-900' : 'text-slate-800'}`}>
                                {chat.channel_name || 'Парк не определён'}
                            </span>
                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">
                                {formatDayShort(chat.last_at)} · {formatTime(chat.last_at)}
                            </span>
                        </div>
                        <div className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-slate-500">
                            {chat.preview || 'Без текста'}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-[11.5px] text-slate-400">
                            <span className="tabular-nums">{chat.messages_count} сообщ. за 2 дня</span>
                            {chat.has_media && <ImageIcon size={12} />}
                            {handedOff[key] && (
                                <span className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-emerald-600">
                                    <Check size={11} /> передан
                                </span>
                            )}
                        </div>
                    </button>
                );
            })}
        </div>

        {truncated && (
            <div className="border-t border-slate-200/70 px-4 py-2.5 text-[11.5px] leading-snug text-amber-600">
                Сообщений за период больше, чем вмещает один запрос — показаны самые свежие.
            </div>
        )}
    </div>
);

/* «Обновить» здесь ОДНА на весь раздел, и она перечитывает всё сразу: и список
   парков со счётчиками, и открытую переписку — запрос к вендору один на всего
   водителя, разделять его не на что. Стояла она раньше и в строке поиска, но
   две одинаковые кнопки читались как разные действия («эта обновляет список, а
   эта — чат»), и владелец попросил свести их в одну (07.09.2026). Живёт она в
   шапке переписки, а не над списком: человек в этот момент смотрит именно в
   переписку — ждёт ответа водителя или только что отправил комментарий. */
const ChatPanel = ({ chat, snapshot, phone, driverName, handedOff, onHandoff,
                     onRefresh, refreshing, fetchedAt }) => {
    if (!chat) return null;
    return (
        <div className={`${iosCard} flex max-h-[76vh] flex-col overflow-hidden`}>
            {/* Шапка переписки. На телефоне сведения и кнопки стоят РАЗНЫМИ
                строками: `flex-wrap` переносит элемент целиком, а `flex-1`
                позволял левому блоку сжаться до 110 px рядом с двумя кнопками —
                имя водителя превращалось в «Ерме…», бейдж парка ломался на две
                строки, а телефон на три (замер на 390 px, 08.09.2026). */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-200/70 px-4 py-3">
                <div className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
                    <div className="flex flex-wrap items-center gap-2">
                        {/* Имени у водителя часто нет — тогда заголовком идёт
                            телефон, а не слово «Водитель»: по нему человека и
                            ищут, и он же нужен на скриншоте. */}
                        <span className="truncate text-[15px] font-semibold text-slate-900">
                            {driverName || formatPhone(phone)}
                        </span>
                        {/* Таксопарк — рядом с именем водителя, а не в подписи
                            мелким: это первое, что спрашивают по чужому чату. */}
                        <span className="inline-flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-0.5 text-[11.5px] font-medium text-blue-700">
                            <Building2 size={11} /> {chat.channel_name || 'Парк не определён'}
                        </span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-[12px] text-slate-500">
                        {driverName && <span className="tabular-nums">{formatPhone(phone)}</span>}
                        {/* Свежесть последнего сообщения, а не время начала:
                            начало после склейки — это граница окна выгрузки, а
                            оператору надо понять, живой ли перед ним разговор. */}
                        <span className="inline-flex items-center gap-1 tabular-nums">
                            <Clock size={11} /> {formatDayShort(chat.last_at)} · {formatTime(chat.last_at)}
                        </span>
                        <span className="tabular-nums">{chat.messages_count} сообщ. за 2 дня</span>
                    </div>
                </div>
                <div className="flex w-full items-center justify-end gap-2 sm:w-auto">
                    {handedOff && (
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1.5 text-[13px] font-medium text-emerald-700">
                            <Check size={14} /> Передан
                        </span>
                    )}
                    <button
                        type="button"
                        onClick={onRefresh}
                        disabled={refreshing}
                        title={fetchedAt
                            ? `Перечитать чаты и переписку · загружены в ${formatTime(fetchedAt)}`
                            : 'Перечитать чаты и переписку'}
                        className={`${iosBtnSecondary} disabled:opacity-40`}
                    >
                        <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />
                        {refreshing ? 'Обновляем…' : 'Обновить'}
                    </button>
                    {/* Кнопку не прячем после передачи: склеенный чат живёт двое
                        суток и покрывает несколько поводов, а запрет вынуждал бы
                        искать номер заново и жёг дневной лимит поисков. */}
                    <button type="button" onClick={onHandoff}
                            className={handedOff ? iosBtnSecondary : iosBtnPrimary}>
                        <Send size={15} /> {handedOff ? 'Ещё раз' : 'Передан'}
                    </button>
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-hidden bg-[#f2f2f7]">
                {/* Переписка показывается ЦЕЛИКОМ. Тумблер «скрыть служебные» и
                    подсказка про снимок экрана убраны по решению владельца
                    07.09.2026: они занимали полосу под лентой на каждом чате, а
                    нужны были один раз. Служебные строки (меню парка, автоопрос
                    «оцените работу оператора») теперь просто часть ленты — ровно
                    так их видит и чат-менеджер у себя. */}
                <ChatThread
                    snapshot={snapshot}
                    initialScroll="end"
                    emptyText="За последние 2 дня живой переписки в этом парке нет"
                    className="h-full"
                />
            </div>
        </div>
    );
};

// ── Окно «Передан» ──────────────────────────────────────────────────────────

const HandoffModal = ({ open, onClose, note, onNote, sending, onSend, maxLength, authorName }) => (
    <IosModal
        open={open}
        onClose={onClose}
        title="Передать чат-менеджеру"
        subtitle="В чат уйдёт внутренний комментарий. Водитель его не увидит"
        footer={(
            <div className="flex justify-end gap-2">
                <button type="button" onClick={onClose} disabled={sending} className={iosBtnSecondary}>
                    Отмена
                </button>
                <button type="button" onClick={onSend} disabled={sending} className={iosBtnPrimary}>
                    {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                    {sending ? 'Отправляем…' : 'Передать'}
                </button>
            </div>
        )}
    >
        <div className="space-y-3">
            <div className="rounded-xl bg-slate-500/5 px-3.5 py-3 text-[13px] leading-relaxed text-slate-600">
                <div className="flex items-start gap-2">
                    <Lock size={14} className="mt-0.5 shrink-0 text-slate-400" />
                    <span>
                        Комментарий подпишется вашим именем{authorName ? ` — ${authorName}` : ''}.
                        Отозвать или отредактировать его после отправки нельзя.
                    </span>
                </div>
            </div>
            <div>
                <label className="mb-1.5 block text-[12.5px] font-medium text-slate-600" htmlFor="dch-note">
                    Что передать (необязательно)
                </label>
                <textarea
                    id="dch-note"
                    value={note}
                    onChange={(event) => onNote(event.target.value.slice(0, maxLength))}
                    rows={3}
                    placeholder="Например: водитель просит уточнить статус заказа"
                    className={`${iosInput} resize-none py-2.5`}
                />
                <div className="mt-1 text-right text-[11.5px] tabular-nums text-slate-400">
                    {note.length}/{maxLength}
                </div>
            </div>
        </div>
    </IosModal>
);

// ── Журнал ──────────────────────────────────────────────────────────────────
//
// Журнал отвечает на один вопрос: КТО открывал переписку водителей и что
// передал чат-менеджеру. Отсюда и устройство экрана (переделан 08.09.2026 по
// просьбе владельца «сделать понятнее и удобнее»):
//
// * Отбор — одна строка чипов и списков, а не сетка из пяти подписанных полей.
//   Подписи «С», «По», «Действие» занимали строку над каждым полем и ничего не
//   добавляли: чип с датами читается сам, а список подписан выбранным значением.
// * Системных `<select>` и `<input type="date">` больше нет: их рисует ОС, и
//   внутри интерфейса в стиле macOS они выглядели деталью из другой программы
//   (эталон пикера — выгрузка табло СЗоВ, `IosDateRangeCalendar`).
// * Дата вынесена в разделитель дня, в строке осталось время. За неделю дата
//   повторялась в пятидесяти строках подряд, а нужна она там, где меняется.
// * Телефон водителя применяется по Enter или уходу из поля, а не по каждой
//   набранной цифре: иначе один номер — это одиннадцать запросов к журналу.

/* Пресеты периода. У экрана свои («7 дней» — рабочая неделя разбора), у
   выгрузки свои: там последний пресет обязан упираться ровно в потолок, и им
   же человек узнаёт, сколько максимум можно взять за раз. Отсчёт от сегодня по
   Алматы (`todayISO`), а не от `new Date()`: у сотрудника в другом поясе иначе
   поехала бы граница на сутки. */
const JOURNAL_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: todayISO(), to: todayISO() }) },
    { label: '7 дней', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    { label: '30 дней', range: () => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }) },
];

const EXPORT_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: todayISO(), to: todayISO() }) },
    { label: 'Неделя', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    {
        label: `${EXPORT_MAX_DAYS} дней`,
        range: () => ({ from: shiftDaysBack(todayISO(), EXPORT_MAX_DAYS - 1), to: todayISO() }),
    },
];

/* Подписи действий берём из одного словаря с выгрузкой (`kindLabel`), а не
   пишем заново: разойдись они — в фильтре и в файле стояли бы разные слова про
   одно и то же. */
const KIND_OPTIONS = [
    { value: 'all', label: 'Все действия' },
    { value: 'handoff', label: kindLabel('handoff') },
    { value: 'open', label: kindLabel('open') },
    { value: 'search', label: kindLabel('search') },
];

const KIND_ICONS = { search: Search, open: Eye, handoff: Send };

const EMPTY_FILTERS = { kind: 'all', userId: 'all', phone: '' };

/* Вид чипа дат и поля телефона — ОДИН В ОДИН с ios-вариантом CustomSelect
   (белое поле, ring-1, px-3 py-2, 12.5px). Иначе в одной строке отбора стоят
   три разных предмета: серый чип, белый список и серое поле, — и строка
   читается как собранная из чужих деталей.
   `[&>span]:flex-1` у чипа обязателен: triggerClassName заменяет класс кнопки
   целиком, и без него подпись не растягивается, а шеврон уезжает к тексту. */
const FILTER_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const FILTER_INPUT = 'h-9 w-full rounded-xl bg-white pl-9 pr-8 text-[12.5px] '
    + 'font-medium tabular-nums text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] outline-none transition-all '
    + 'placeholder:font-normal placeholder:text-slate-400 focus:ring-2 focus:ring-blue-500/60';

/* Период выгрузки не длиннее потолка. Экран периодом не ограничен — на нём
   можно смотреть хоть квартал, — поэтому «выгрузить то, что вижу» приходится
   поджимать: берём последние EXPORT_MAX_DAYS суток окна, а не молча обрезаем
   начало и не отдаём заведомо отказной запрос. */
const clampExportRange = ({ from, to }) => {
    const days = rangeDays(from, to);
    if (!days) return { from: todayISO(), to: todayISO() };
    if (days <= EXPORT_MAX_DAYS) return { from, to };
    return { from: shiftDaysBack(to, EXPORT_MAX_DAYS - 1), to };
};

const JournalPanel = ({ apiBaseUrl, headers, toast }) => {
    const [filters, setFilters] = useState(() => ({
        ...EMPTY_FILTERS,
        from: shiftDaysBack(todayISO(), 6),
        to: todayISO(),
    }));
    /* Черновик телефона живёт отдельно от отбора: набранное на клавиатуре ещё
       не запрос. Применяется он по Enter и по уходу из поля — так «набрал и
       щёлкнул мышью в таблицу» тоже срабатывает, а одиннадцати запросов на
       один номер не случается. */
    const [phoneDraft, setPhoneDraft] = useState('');
    const [data, setData] = useState({ items: [], total: 0, summary: {}, people: [] });
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [downloading, setDownloading] = useState(false);

    /* Пикер периода выгрузки. Период у файла ОБЯЗАТЕЛЕН и не длиннее месяца,
       поэтому он живёт своим состоянием: на экране период мог остаться каким
       угодно, а книга собирается по выбранному в пикере. Открываясь, пикер
       подхватывает период экрана — «выгрузить то, что вижу» самый частый
       случай, — но поджатый до потолка. */
    const [exportOpen, setExportOpen] = useState(false);
    const [exportRange, setExportRange] = useState(
        () => clampExportRange({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }));
    const exportRef = useRef(null);

    /* Клик мимо и Esc закрывают панель выгрузки — как у эталонного пикера табло
       СЗоВ. Слушаем `mousedown`, а не `click`: прокрутка колесом внутри панели
       тогда не считается внешней и не гасит её. */
    useEffect(() => {
        if (!exportOpen) return undefined;
        const onDown = (event) => {
            if (exportRef.current && !exportRef.current.contains(event.target)) {
                setExportOpen(false);
            }
        };
        const onKey = (event) => { if (event.key === 'Escape') setExportOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [exportOpen]);

    const params = useMemo(() => {
        const search = new URLSearchParams();
        if (filters.from) search.set('date_from', filters.from);
        if (filters.to) search.set('date_to', filters.to);
        if (filters.kind !== 'all') search.set('kinds', filters.kind);
        if (filters.userId !== 'all') search.set('user_id', String(filters.userId));
        if (filters.phone.trim()) search.set('phone', filters.phone.trim());
        return search;
    }, [filters]);

    useEffect(() => { setPage(1); }, [params]);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const search = new URLSearchParams(params);
        search.set('page', String(page));
        (async () => {
            try {
                const response = await fetch(
                    `${apiBaseUrl}/api/driver_chats/journal?${search.toString()}`,
                    { headers: headers(), credentials: 'include' });
                const payload = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setError(payload.error || 'Не удалось загрузить журнал');
                    return;
                }
                setError('');
                setData(payload);
            } catch {
                if (!cancelled) setError('Сеть недоступна');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, params, page]);

    const applyPhone = useCallback(() => {
        const next = phoneDraft.trim();
        setFilters((prev) => (prev.phone === next ? prev : { ...prev, phone: next }));
    }, [phoneDraft]);

    const resetFilters = useCallback(() => {
        setPhoneDraft('');
        setFilters((prev) => ({ ...prev, ...EMPTY_FILTERS }));
    }, []);

    const download = useCallback(async (from, to) => {
        setExportOpen(false);
        setDownloading(true);
        try {
            /* Рамку файла задаёт пикер, остальной отбор — экран. Собираем
               строку запроса заново, а не правим `params`: там лежит период
               экрана, и подмена двух ключей в общем объекте разъехалась бы с
               именем файла на первой же правке. */
            const search = new URLSearchParams();
            search.set('date_from', from);
            search.set('date_to', to);
            if (filters.kind !== 'all') search.set('kinds', filters.kind);
            if (filters.userId !== 'all') search.set('user_id', String(filters.userId));
            if (filters.phone.trim()) search.set('phone', filters.phone.trim());

            const response = await fetch(
                `${apiBaseUrl}/api/driver_chats/journal/export?${search.toString()}`,
                { headers: headers(), credentials: 'include' });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                toast(payload.error || 'Не удалось собрать выгрузку', 'error');
                return;
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(from, to);
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch {
            toast('Сеть недоступна. Выгрузка не собрана', 'error');
        } finally {
            setDownloading(false);
        }
    }, [apiBaseUrl, filters, headers, toast]);

    const summary = data.summary || {};
    const pageSize = data.page_size || 50;
    const total = data.total || 0;
    const pageCount = Math.max(1, Math.ceil(total / pageSize));

    const peopleOptions = useMemo(() => ([
        { value: 'all', label: 'Все сотрудники' },
        ...(data.people || []).map((person) => ({
            value: String(person.user_id),
            label: person.name || `№${person.user_id}`,
        })),
    ]), [data.people]);

    /* Строки, разложенные по дням. Группируем последовательно, а не через
       словарь: сервер уже отдал их по убыванию времени, и группа меняется ровно
       там, где меняется день. */
    const groups = useMemo(() => {
        const out = [];
        (data.items || []).forEach((item) => {
            const key = dayKeyOf(item.created_at);
            const last = out[out.length - 1];
            if (last && last.key === key) last.items.push(item);
            else out.push({ key, label: formatDayFull(item.created_at), items: [item] });
        });
        return out;
    }, [data.items]);

    const filtered = filters.kind !== 'all' || filters.userId !== 'all'
        || Boolean(filters.phone.trim());

    /* Длина выбранного периода и подсказка под календарём. Считаем здесь, а не
       в разметке: и «Подтвердить», и строка под ней читают одно число. Потолок
       сторожит сервер — здесь он лишь гасит кнопку заранее, чтобы человек узнал
       о нём до ожидания, а не из ошибки после. */
    const exportDays = rangeDays(exportRange.from, exportRange.to);
    const exportTooLong = exportDays > EXPORT_MAX_DAYS;
    const exportHint = exportTooLong
        ? `Максимум ${EXPORT_MAX_DAYS} суток за раз — выберите период короче`
        : (exportDays
            ? `${rangeLabel(exportRange.from, exportRange.to)} · ${pluralDays(exportDays)}${
                filtered ? ' · с фильтрами экрана' : ''}`
            : 'Выберите период выгрузки');

    return (
        <div className="space-y-3">
            {/* ── Отбор ─────────────────────────────────────────────────── */}
            <div className={`${iosCard} flex flex-wrap items-center gap-2 px-3 py-2.5`}>
                {/* Обёртка нужна: сам пикер рисует `relative`-блок без ширины,
                    и в строке-флексе он сжимается по содержимому — `w-full` на
                    кнопке внутри тогда считается от той же ширины и ничего не
                    меняет. Ширину задаём снаружи, кнопка её наследует. */}
                <div className="w-full sm:w-[188px]">
                    <IosDateRangePicker
                        from={filters.from}
                        to={filters.to}
                        max={todayISO()}
                        presets={JOURNAL_PRESETS}
                        triggerClassName={FILTER_TRIGGER}
                        onChange={(next) => setFilters((prev) => ({
                            ...prev,
                            from: next.from || next.to || prev.from,
                            to: next.to || next.from || prev.to,
                        }))}
                    />
                </div>
                <CustomSelect
                    variant="ios"
                    value={filters.kind}
                    options={KIND_OPTIONS}
                    ariaLabel="Действие"
                    className="w-full sm:w-[196px]"
                    onChange={(value) => setFilters((prev) => ({ ...prev, kind: value }))}
                />
                <CustomSelect
                    variant="ios"
                    value={filters.userId}
                    options={peopleOptions}
                    ariaLabel="Сотрудник"
                    searchable={peopleOptions.length > 8}
                    className="w-full sm:w-[200px]"
                    onChange={(value) => setFilters((prev) => ({ ...prev, userId: value }))}
                />
                <div className="relative w-full sm:w-[186px]">
                    <Phone size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={phoneDraft}
                        onChange={(event) => setPhoneDraft(event.target.value)}
                        onKeyDown={(event) => { if (event.key === 'Enter') applyPhone(); }}
                        onBlur={applyPhone}
                        inputMode="tel"
                        placeholder="Телефон водителя"
                        aria-label="Телефон водителя"
                        className={FILTER_INPUT}
                    />
                    {Boolean(phoneDraft) && (
                        <button
                            type="button"
                            aria-label="Очистить телефон"
                            onClick={() => { setPhoneDraft(''); setFilters((prev) => ({ ...prev, phone: '' })); }}
                            className="absolute right-2 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                        >
                            <X size={12} />
                        </button>
                    )}
                </div>
                {filtered && (
                    <button type="button" onClick={resetFilters} className={iosBtnGhost}>
                        Сбросить
                    </button>
                )}

                {/* Выгрузка прижата к правому краю строки отбора: она итог того,
                    что в этой строке набрали. Нажатие не качает файл сразу, а
                    раскрывает пикер периода — период у файла обязателен и не
                    длиннее месяца. */}
                <div ref={exportRef} className="relative ml-auto shrink-0">
                    <button
                        type="button"
                        onClick={() => {
                            setExportRange(clampExportRange({ from: filters.from, to: filters.to }));
                            setExportOpen((value) => !value);
                        }}
                        disabled={downloading}
                        className={`${iosBtnSecondary} h-9 py-0 ${exportOpen ? 'bg-slate-200 text-slate-900' : ''}`}
                        title={`Выгрузить журнал в Excel — период не длиннее ${EXPORT_MAX_DAYS} суток`}
                    >
                        {downloading
                            ? <Loader2 size={15} className="animate-spin" />
                            : <Download size={15} />}
                        {downloading ? 'Готовим файл…' : 'Выгрузить'}
                    </button>
                    {exportOpen && (
                        /* Панель прижата к правому краю кнопки, а та стоит у
                           правого края карточки на любой ширине (`ml-auto`), —
                           значит и панель никуда не уезжает: замер на 390 px
                           даёт 86…354 при окне 390, на 1024 и 1366 тоже внутри.
                           Раскрытие влево увело бы календарь за край экрана. */
                        <div className="absolute right-0 top-full z-[60] mt-2">
                            <IosDateRangeCalendar
                                from={exportRange.from}
                                to={exportRange.to}
                                max={todayISO()}
                                presets={EXPORT_PRESETS}
                                onChange={(next) => setExportRange({
                                    from: next.from || next.to,
                                    to: next.to || next.from,
                                })}
                                footer={(
                                    <div className="mt-2.5 border-t border-slate-100 pt-2.5">
                                        <button
                                            type="button"
                                            className={`${iosBtnPrimary} w-full`}
                                            disabled={!exportDays || exportTooLong}
                                            onClick={() => download(exportRange.from, exportRange.to)}
                                        >
                                            <Download size={15} />
                                            Подтвердить
                                        </button>
                                        <p className={`mt-1.5 text-center text-[11px] ${
                                            exportTooLong ? 'text-rose-500' : 'text-slate-400'}`}>
                                            {exportHint}
                                        </p>
                                    </div>
                                )}
                            />
                        </div>
                    )}
                </div>
            </div>

            {/* ── Сводка по всей выборке, а не по странице ───────────────── */}
            <div className={`${iosCard} grid grid-cols-2 divide-x divide-y divide-slate-100 sm:grid-cols-4 sm:divide-y-0`}>
                <Stat label="Действий" value={summary.events} />
                <Stat label="Передач чат-менеджеру" value={summary.handoffs} />
                <Stat label="Сотрудников" value={summary.people} />
                <Stat label="Водителей" value={summary.drivers} />
            </div>

            {error && (
                <div className={`${iosCard} flex items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                    <AlertCircle size={16} className="mt-0.5 shrink-0" /><span>{error}</span>
                </div>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                {loading ? (
                    <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-400">
                        <Loader2 size={15} className="animate-spin" /> Загрузка журнала…
                    </div>
                ) : !data.items?.length ? (
                    <div className="px-6 py-12 text-center">
                        <div className="text-sm font-medium text-slate-700">
                            За выбранный период действий не было
                        </div>
                        <div className="mt-1 text-[13px] text-slate-500">
                            {filtered
                                ? 'Попробуйте расширить период или снять фильтры.'
                                : 'Раздел в эти дни не открывали.'}
                        </div>
                        {filtered && (
                            <button type="button" onClick={resetFilters} className={`${iosBtnSecondary} mt-4`}>
                                Снять фильтры
                            </button>
                        )}
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[720px] text-left text-[13px]">
                            <thead>
                                <tr className="border-b border-slate-200/70 text-[11px] uppercase tracking-wide text-slate-400">
                                    <th className="px-4 py-2.5 font-medium">Время</th>
                                    <th className="px-4 py-2.5 font-medium">Сотрудник</th>
                                    <th className="px-4 py-2.5 font-medium">Действие</th>
                                    <th className="px-4 py-2.5 font-medium">Водитель</th>
                                    <th className="px-4 py-2.5 font-medium">Таксопарк</th>
                                    <th className="px-4 py-2.5 font-medium">Комментарий</th>
                                </tr>
                            </thead>
                            <tbody>
                                {groups.map((group) => (
                                    <React.Fragment key={group.key}>
                                        <tr>
                                            <th
                                                colSpan={6}
                                                scope="colgroup"
                                                className="bg-slate-500/[0.04] px-4 py-1.5 text-left text-[11.5px] font-semibold text-slate-500"
                                            >
                                                {group.label}
                                            </th>
                                        </tr>
                                        {group.items.map((item) => (
                                            <JournalRow key={item.id} item={item} />
                                        ))}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {pageCount > 1 && (
                <IosPager
                    page={page}
                    pageCount={pageCount}
                    total={total}
                    from={(page - 1) * pageSize + 1}
                    to={Math.min(total, page * pageSize)}
                    onPage={setPage}
                    unit="записи"
                />
            )}
        </div>
    );
};

/* Строка журнала. Дата вынесена в разделитель дня, поэтому здесь только время.
   Цветом помечено одно действие — «Передал»: оно единственное меняет чужую
   систему и не отзывается. «Искал» и «Открыл» нейтральные, их не красим. */
const JournalRow = ({ item }) => {
    const Icon = KIND_ICONS[item.kind] || Eye;
    return (
        <tr className="border-b border-slate-100 last:border-0">
            <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-500">
                {formatTime(item.created_at)}
            </td>
            <td className="px-4 py-2.5">
                <div className="font-medium text-slate-800">{item.user_name || '—'}</div>
                <div className="text-[11.5px] text-slate-400">{roleLabel(item.user_role)}</div>
            </td>
            <td className="px-4 py-2.5">
                {item.kind === 'handoff' ? (
                    <IosBadge tone={KIND_TONE[item.kind]}>{kindLabel(item.kind)}</IosBadge>
                ) : (
                    <span className="inline-flex items-center gap-1.5 text-slate-600">
                        <Icon size={13} className="text-slate-400" />
                        {kindLabel(item.kind)}
                    </span>
                )}
            </td>
            <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-600">
                {formatPhone(item.phone)}
            </td>
            <td className="px-4 py-2.5 text-[12.5px] text-slate-600">
                {item.channel_name || '—'}
            </td>
            <td className="max-w-[320px] px-4 py-2.5 text-[12.5px] text-slate-600">
                {item.comment_text || ''}
                {/* Номер обращения есть только у передачи: это заявка, куда
                    вендор реально положил заметку. У просмотра его нет — чат
                    склеен по парку, и обращений внутри несколько. Стоит он под
                    текстом, а не своей колонкой: пустая колонка занимала
                    ширину во всех строках ради каждой седьмой. */}
                {item.kind === 'handoff' && item.request_id ? (
                    <div className="mt-0.5 text-[11px] tabular-nums text-slate-400">
                        обращение № {item.request_id}
                    </div>
                ) : null}
            </td>
        </tr>
    );
};

const Stat = ({ label, value }) => (
    <div className="px-4 py-3">
        <div className="text-[19px] font-semibold leading-tight tabular-nums text-slate-900">
            {value ?? 0}
        </div>
        <div className="mt-0.5 text-[11.5px] leading-tight text-slate-500">{label}</div>
    </div>
);

export default DriverChatsView;
