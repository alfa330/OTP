import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Check, CircleAlert, FolderPlus, KeyRound, Loader2, Pencil, Plus, ScrollText, Send, ShieldCheck, UserPlus, Users, X,
} from 'lucide-react';
import { APPLE_FONT, IosHint, IosSegmented, IosToggle, iosCard } from '../ui/ios';
import { subscribeNewsPoke } from '../news/newsShared';
import { createCoalescedReload } from '../notifications/coalescedReload';
import { AccountSheet, AddAccountSheet } from './TwofaAccountSheet';
import { GroupSheet } from './TwofaGroupSheet';
import { PeoplePicker, PersonSheet } from './TwofaPeopleSheet';
import { EmptyState, GroupLabel, Notice, Pill, SectionHeader, StateLine, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './twofaUi';
import {
    accountStateLine, accountsCount, clockOf, errorOf, groupAccounts, journalGroupNote, latestByAccount, peopleCount,
    tabsFor, toneOfStatus, whenOf,
} from './twofaMeta';

/*
 * Раздел «Коды 2FA» (#373) — коды Google Authenticator маркетингу с подтверждением главы.
 *
 * Постановка владельца 27.10.2026: «когда человек запрашивает код, уведомление приходит
 * главе маркетинга, и он просто подтверждает, и код отправляется, так же время, после
 * которого код будет недействителен». Код и время приходят сотруднику в Telegram (выбор
 * владельца: «Только в Telegram») — здесь его нет ни на экране, ни в ответах сервера.
 *
 *   сотрудник  — «Коды»: свои аккаунты, кнопка «Запросить код», что с запросом сейчас;
 *                то же — кнопкой «Запросить код 🔐» в меню бота с выбором аккаунта;
 *   глава      — «Запросы» (подтвердить здесь или в Telegram), «Аккаунты», «Доступы»,
 *                «Журнал»; супер-админ видит и подтверждает то же самое.
 *
 * Аккаунты лежат по разделам (владелец 27.10.2026): во вкладке «Аккаунты» — раздел за
 * разделом с кнопкой «Изменить» (название и состав), у сотрудника — заголовки разделов, если
 * его аккаунты в нескольких.
 *
 * Экран перечитывается по тычку колокола (новый запрос, решение, сгорание — триггер на
 * twofa_requests) и при возврате на вкладку; своего таймера нет.
 */

const HOW_IT_WORKS = 'Сотрудник нажимает «Запросить код» здесь или «Запросить код 🔐» в меню бота портала и выбирает '
    + 'аккаунт. Подтверждают глава маркетинга и супер-админы — здесь '
    + 'или кнопкой в Telegram; уведомление о запросе (колокол портала и Telegram) получает тот, кто включил его '
    + 'тумблером во вкладке «Запросы». Код и время, до которого он действует, приходят сотруднику в Telegram от бота '
    + 'портала; цифры стираются через минуту. Не подошёл — кнопка «Код не подошёл» в сообщении выдаёт новый код без '
    + 'повторного подтверждения в течение 3 минут. Запрос без ответа сгорает через 10 минут.';

const viaLabel = (via) => (via === 'telegram' ? 'в Telegram' : via === 'portal' ? 'в портале' : '');

/* basis-[260px], а не basis-0: с нулевой основой текст сжимался до «Вера …», лишь бы
   кнопки остались в той же строке; с основой на телефоне кнопки уходят строкой ниже. */
const RequestCard = ({ request, canApprove, onDecide, busy, serverTime }) => (
    <div className={`${iosCard} flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3.5`}>
        <div className="min-w-0 grow basis-[260px]">
            <div className="truncate text-[14.5px] font-semibold text-slate-900">{request.requester_name}</div>
            <div className="mt-0.5 truncate text-[13px] text-slate-600">
                «{request.account_title}»{request.service ? ` · ${request.service}` : ''}
            </div>
            <div className="mt-1.5">
                <StateLine tone="amber">
                    Запрошено в {whenOf(request.created_at, serverTime)} · сгорит в {clockOf(request.expires_at)}
                </StateLine>
            </div>
        </div>
        {canApprove ? (
            <div className="flex w-full shrink-0 items-center gap-2 sm:w-auto">
                <button type="button" className={`${TOOLBAR_BTN} flex-1 sm:flex-none`} disabled={Boolean(busy)} onClick={() => onDecide(request, 'reject')}>
                    {busy === 'reject' ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
                    Отклонить
                </button>
                <button type="button" className={`${TOOLBAR_BTN_PRIMARY} flex-1 sm:flex-none`} disabled={Boolean(busy)} onClick={() => onDecide(request, 'approve')}>
                    {busy === 'approve' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                    Подтвердить
                </button>
            </div>
        ) : (
            <span className="shrink-0 text-[12.5px] text-slate-500">Подтверждают глава маркетинга и супер-админы</span>
        )}
    </div>
);

const HistoryRow = ({ request, serverTime, showRequester }) => {
    const decided = request.decided_at ? whenOf(request.decided_at, serverTime) : '';
    return (
        <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
            <span className="min-w-0 grow basis-[220px] truncate text-[13.5px] text-slate-800">
                {showRequester && <span className="font-semibold">{request.requester_name} · </span>}
                «{request.account_title}»
            </span>
            <span className="flex shrink-0 items-center gap-2 text-[12.5px] text-slate-500">
                {request.delivery_error && request.status === 'approved'
                    ? <Pill tone="red">Код не дошёл</Pill>
                    : <Pill tone={toneOfStatus(request.status)}>{request.status_label}</Pill>}
                <span className="tabular-nums">{decided || whenOf(request.created_at, serverTime)}</span>
                {request.decided_via && request.status !== 'expired' && request.status !== 'cancelled' && (
                    <span className="hidden sm:inline">{viaLabel(request.decided_via)}</span>
                )}
            </span>
        </li>
    );
};

const History = ({ title, requests, serverTime, showRequester }) => (
    requests.length > 0 && (
        <section className="mt-6">
            <GroupLabel>{title}</GroupLabel>
            <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {requests.map((request) => (
                    <HistoryRow key={request.id} request={request} serverTime={serverTime} showRequester={showRequester} />
                ))}
            </ul>
        </section>
    )
);

/* grid-cols-1 — колонка minmax(0, 1fr): без неё на телефоне колонка растягивалась под самое
   длинное название (почта вида «marketing.team…@…») и карточка уезжала за край экрана. */
const AccountGrid = ({ children }) => <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">{children}</div>;

/* Заголовок раздела над его карточками; count — сколько в нём аккаунтов (у главы). Название
   переносится, а не режется: на телефоне рядом с «Изменить» от него оставалась половина. */
const SectionTitle = ({ title, count = null, action = null }) => (
    <div className="mb-2.5 flex items-end justify-between gap-3 px-1">
        <div className="min-w-0">
            <h2 className="break-words text-[15px] font-semibold leading-snug text-slate-900">{title}</h2>
            {count !== null && <p className="text-[12.5px] text-slate-500">{accountsCount(count)}</p>}
        </div>
        {action}
    </div>
);

const TwofaView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);

    const api = useMemo(() => {
        const url = (path) => `${apiBaseUrl}/api/twofa${path}`;
        const config = (params) => ({ headers: headersRef.current ? headersRef.current() : {}, params });
        return {
            get: (path, params) => axios.get(url(path), config(params)),
            post: (path, body) => axios.post(url(path), body || {}, config()),
            patch: (path, body) => axios.patch(url(path), body || {}, config()),
            put: (path, body) => axios.put(url(path), body || {}, config()),
            delete: (path) => axios.delete(url(path), config()),
        };
    }, [apiBaseUrl]);

    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState('');
    const [busy, setBusy] = useState({});
    const [adding, setAdding] = useState(false);
    const [openedAccount, setOpenedAccount] = useState(null);
    const [openedGroup, setOpenedGroup] = useState(null);
    const [picking, setPicking] = useState(false);
    const [openedPerson, setOpenedPerson] = useState(null);
    const [journal, setJournal] = useState(null);
    const [journalBusy, setJournalBusy] = useState(false);

    const load = useCallback(async () => {
        try {
            const { data: screen } = await api.get('');
            setData(screen);
            setError('');
        } catch (requestError) {
            // Доступ сняли, пока раздел был открыт: старый экран («ждёт подтверждения», кнопки)
            // врал бы, что всё по-прежнему. Показываем причину вместо него.
            if (requestError?.response?.data?.code === 'TWOFA_SECTION_CLOSED') {
                setData(null);
                setError('Раздел «Коды 2FA» вам больше не открыт. Если доступ нужен, обратитесь к главе маркетинга.');
                return;
            }
            setError(errorOf(requestError, 'Раздел не обновился — проверьте связь'));
        }
    }, [api]);

    /* Один перезапуск на все входы: тычок колокола и возврат на вкладку приходят почти разом,
       и два параллельных GET могли прийти не по порядку — старый ответ затёр бы новый. */
    const reload = useMemo(() => createCoalescedReload(() => load()), [load]);
    useEffect(() => { reload(); }, [reload]);
    useEffect(() => subscribeNewsPoke(reload), [reload]);
    useEffect(() => {
        const onVisible = () => { if (document.visibilityState === 'visible') reload(); };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [reload]);

    const caps = data?.capabilities || {};
    const tabs = useMemo(() => tabsFor(caps), [caps.can_manage, caps.can_request]); // eslint-disable-line react-hooks/exhaustive-deps
    const activeTab = tabs.some((item) => item.value === tab) ? tab : (tabs[0]?.value || '');

    const loadJournal = useCallback(async (before) => {
        setJournalBusy(true);
        try {
            const { data: page } = await api.get('/journal', before ? { before } : undefined);
            setJournal((prev) => ({
                items: before ? [...(prev?.items || []), ...(page.items || [])] : (page.items || []),
                has_more: Boolean(page.has_more),
            }));
        } catch (requestError) {
            toast(errorOf(requestError, 'Журнал не открылся'), 'error');
        } finally {
            setJournalBusy(false);
        }
    }, [api, toast]);

    useEffect(() => {
        if (activeTab === 'journal') loadJournal();
    }, [activeTab, loadJournal]);

    const serverTime = data?.server_time;
    const botLink = data?.bot_username ? `https://t.me/${data.bot_username}` : null;
    const accountsById = useMemo(() => {
        const map = {};
        (data?.accounts || []).forEach((account) => { map[account.id] = account; });
        return map;
    }, [data]);
    const peopleNames = useMemo(() => {
        const map = {};
        (data?.people || []).forEach((person) => { map[person.id] = person.name; });
        return map;
    }, [data]);

    const requestCode = async (account) => {
        setBusy((prev) => ({ ...prev, [`req:${account.id}`]: true }));
        try {
            await api.post('/requests', { account_id: account.id });
            toast('Запрос отправлен на подтверждение. Код придёт в Telegram', 'success');
        } catch (requestError) {
            toast(errorOf(requestError, 'Запрос не отправился'), 'error');
        } finally {
            setBusy((prev) => ({ ...prev, [`req:${account.id}`]: false }));
            reload();
        }
    };

    const decide = async (request, decision) => {
        setBusy((prev) => ({ ...prev, [`dec:${request.id}`]: decision }));
        try {
            const { data: result } = await api.post(`/requests/${request.id}/${decision}`);
            if (decision === 'reject') {
                toast('Запрос отклонён', 'success');
            } else if (result.delivered === false) {
                toast(`Подтверждено, но код не дошёл: ${result.delivery_reason || 'Telegram не принял сообщение'}`, 'error');
            } else {
                toast('Подтверждено — код отправлен в Telegram', 'success');
            }
        } catch (requestError) {
            toast(errorOf(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy((prev) => ({ ...prev, [`dec:${request.id}`]: '' }));
            reload();
        }
    };

    const toggleNotify = async () => {
        const next = !data?.notify?.enabled;
        setBusy((prev) => ({ ...prev, notify: true }));
        setData((prev) => (prev ? { ...prev, notify: { ...(prev.notify || {}), enabled: next } } : prev));
        try {
            const { data: result } = await api.put('/notify', { enabled: next });
            setData((prev) => (prev ? { ...prev, notify: { ...(prev.notify || {}), enabled: result.enabled } } : prev));
        } catch (requestError) {
            setData((prev) => (prev ? { ...prev, notify: { ...(prev.notify || {}), enabled: !next } } : prev));
            toast(errorOf(requestError, 'Не удалось переключить уведомления'), 'error');
        } finally {
            setBusy((prev) => ({ ...prev, notify: false }));
        }
    };

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1400px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !data) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    }
    // Перечитка не удалась, а прежние данные есть: показываем их, но честно говорим, что они
    // могли устареть.
    const staleNotice = error ? (
        <Notice key="stale" tone="amber" icon={CircleAlert} title="Данные могли устареть">{error}</Notice>
    ) : null;
    if (!data) {
        return shell(
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем коды 2FA…
            </div>,
        );
    }

    const notices = staleNotice ? [staleNotice] : [];
    if (data.key_status !== 'ok') {
        notices.push(
            <Notice key="key" tone="red" icon={KeyRound} title="Не задан ключ шифрования">
                Пока в настройках сервера нет TWOFA_SECRET_KEY, раздел не принимает аккаунты и не выдаёт коды, а сотрудникам он не виден.
            </Notice>,
        );
    }
    // Подтверждающих нет вовсе (ни главы, ни работающего супер-админа) — сказать сотруднику
    // сразу, а не дать ему ждать 10 минут запроса, который некому подтвердить.
    if (caps.can_request && !data.approvers_available) {
        notices.push(
            <Notice key="nobody" tone="amber" icon={CircleAlert} title="Подтвердить запрос сейчас некому">
                Обратитесь к руководству отдела маркетинга.
            </Notice>,
        );
    }
    if (caps.can_request && !data.me.telegram_linked) {
        notices.push(
            <Notice key="me-tg" tone="red" icon={Send} title="Код приходит в Telegram, а он у вас не привязан"
                    action={botLink && (
                        <a href={botLink} target="_blank" rel="noopener noreferrer" className={TOOLBAR_BTN}>Открыть бота</a>
                    )}>
                Войдите в бота портала своим логином и паролем — после этого запросите код.
            </Notice>,
        );
    }

    const header = (
        <SectionHeader
            subtitle={caps.can_manage
                ? 'Запросы кодов, аккаунты и доступы сотрудников'
                : 'Код приходит в Telegram после подтверждения'}
            hint={HOW_IT_WORKS}
            actions={tabs.length > 1 && (
                <IosSegmented value={activeTab} onChange={setTab} options={tabs} ariaLabel="Вкладка раздела" />
            )}
        />
    );

    /* ── Мои коды ─────────────────────────────────────────────────────── */
    const myLatest = latestByAccount(data.my_requests);
    const myAccounts = caps.can_manage
        ? (data.accounts || []).filter((a) => (a.user_ids || []).includes(data.me.id))
        : (data.accounts || []);
    const myCardIds = new Set(myAccounts.map((account) => account.id));
    // Заголовки разделов — только когда их у человека несколько: над двумя карточками одного
    // раздела его название ничего бы не сказало.
    const mySections = groupAccounts(myAccounts, data.groups);
    const codeCard = (account) => {
        const latest = myLatest[account.id];
        const line = accountStateLine(latest, serverTime);
        const pending = latest?.status === 'pending';
        const blocked = !data.me.telegram_linked || !data.approvers_available || data.key_status !== 'ok';
        return (
            <div key={account.id} className={`${iosCard} flex flex-col gap-3 px-4 py-3.5`}>
                <div className="flex items-start gap-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                        <ShieldCheck size={18} />
                    </span>
                    <div className="min-w-0 grow basis-0">
                        <div className="truncate text-[14.5px] font-semibold text-slate-900">{account.title}</div>
                        {account.service && <div className="truncate text-[12.5px] text-slate-500">{account.service}</div>}
                    </div>
                </div>
                {/* На телефоне строка состояния над кнопкой: рядом с кнопкой
                    «Код отправлен в Telegram · 11:54» обрезалось на «· 1…». */}
                <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                    <span className="min-w-0">{line && <StateLine tone={line.tone}>{line.text}</StateLine>}</span>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} w-full sm:w-auto`}
                            disabled={pending || blocked || Boolean(busy[`req:${account.id}`])}
                            onClick={() => requestCode(account)}>
                        {busy[`req:${account.id}`] ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                        {pending ? 'Запрос отправлен' : 'Запросить код'}
                    </button>
                </div>
            </div>
        );
    };
    const codesTab = (
        <>
            {myAccounts.length === 0 ? (
                <div className={iosCard}>
                    <EmptyState icon={KeyRound} title="Вам пока не выдан ни один аккаунт">
                        Доступ к аккаунтам выдаёт глава маркетинга в этом разделе.
                    </EmptyState>
                </div>
            ) : mySections.length > 1 ? (
                <div className="space-y-6">
                    {mySections.map((section) => (
                        <section key={section.id ?? 'none'}>
                            <SectionTitle title={section.title} />
                            <AccountGrid>{section.accounts.map(codeCard)}</AccountGrid>
                        </section>
                    ))}
                </div>
            ) : (
                <AccountGrid>{myAccounts.map(codeCard)}</AccountGrid>
            )}
            {/* Последний запрос по аккаунту уже сказан на его карточке — в истории его нет. Но только
                если карточка есть: по снятому или удалённому аккаунту карточки нет, и его последний
                запрос («Отменён») иначе пропал бы бесследно. */}
            <History title="История запросов"
                     requests={(data.my_requests || []).filter((request) => !(
                         myCardIds.has(request.account_id) && myLatest[request.account_id]?.id === request.id))}
                     serverTime={serverTime} />
        </>
    );

    /* ── Запросы (глава, супер-админ) ─────────────────────────────────── */
    const notifyRow = data.notify && (
        <div className={`${iosCard} mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3`}>
            <div className="min-w-0 grow basis-[240px]">
                <div className="flex items-center gap-1.5 text-[14px] font-semibold text-slate-900">
                    Уведомления о запросах
                    <IosHint
                        label="Что за уведомления"
                        text="Когда сотрудник запрашивает код, приходит строка в колокол портала и сообщение в Telegram с кнопками «Подтвердить / Отклонить». Только вам и только пока тумблер включён; по умолчанию он выключен. Подтверждать можно и без уведомлений — здесь, во вкладке «Запросы»."
                    />
                </div>
                {data.notify.enabled && !data.me.telegram_linked && (
                    <div className="mt-0.5 text-[12.5px] text-amber-700">
                        Telegram не привязан — уведомления придут только в колокол.
                    </div>
                )}
            </div>
            <IosToggle checked={Boolean(data.notify.enabled)} disabled={Boolean(busy.notify)} onChange={toggleNotify} />
        </div>
    );

    const requestsTab = (
        <>
            {notifyRow}
            {(data.pending || []).length === 0 ? (
                <div className={iosCard}>
                    <EmptyState icon={Check} title="Ждущих запросов нет">
                        Новый запрос появится здесь, а с включёнными уведомлениями — ещё в колоколе и в Telegram.
                    </EmptyState>
                </div>
            ) : (
                <section>
                    <GroupLabel>Ждут подтверждения</GroupLabel>
                    <div className="space-y-2.5">
                        {data.pending.map((request) => (
                            <RequestCard key={request.id} request={request} canApprove={caps.can_approve}
                                         busy={busy[`dec:${request.id}`]} onDecide={decide} serverTime={serverTime} />
                        ))}
                    </div>
                </section>
            )}
            <History title="Последние запросы" requests={(data.recent || []).filter((r) => r.status !== 'pending')}
                     serverTime={serverTime} showRequester />
        </>
    );

    /* ── Аккаунты ─────────────────────────────────────────────────────── */
    // Разделы — в порядке создания, пустые тоже (их наполняют кнопкой «Изменить»); «Без
    // раздела» — в конце, если там что-то есть.
    const accountSections = groupAccounts(data.accounts, data.groups, { withEmpty: true });
    const accountCard = (account) => (
        <button key={account.id} type="button" onClick={() => setOpenedAccount(account)}
                className={`${iosCard} flex items-start gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50`}>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                <ShieldCheck size={18} />
            </span>
            <span className="min-w-0 grow basis-0">
                <span className="block truncate text-[14.5px] font-semibold text-slate-900">{account.title}</span>
                {account.service && <span className="block truncate text-[12.5px] text-slate-500">{account.service}</span>}
                <span className="mt-1.5 block text-[12.5px] text-slate-600">
                    {(account.user_ids || []).length ? `Доступ: ${peopleCount(account.user_ids.length)}` : 'Доступ не выдан'}
                </span>
            </span>
        </button>
    );
    const accountsTab = (
        <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <GroupLabel>{accountsCount((data.accounts || []).length)}</GroupLabel>
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => setOpenedGroup({ id: null })}>
                        <FolderPlus size={15} /> Новый раздел
                    </button>
                    <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={data.key_status !== 'ok'} onClick={() => setAdding(true)}>
                        <Plus size={15} /> Добавить аккаунт
                    </button>
                </div>
            </div>
            {accountSections.length === 0 ? (
                <div className={iosCard}>
                    <EmptyState icon={KeyRound} title="Аккаунтов пока нет">
                        Добавьте аккаунт по QR из настроек 2FA сервиса или экспортом из Google Authenticator на телефоне владельца.
                    </EmptyState>
                </div>
            ) : (
                <div className="space-y-6">
                    {accountSections.map((section) => (
                        <section key={section.id ?? 'none'}>
                            <SectionTitle
                                title={section.title}
                                count={section.accounts.length}
                                action={section.id !== null && (
                                    <button type="button" className={TOOLBAR_BTN}
                                            onClick={() => setOpenedGroup({ id: section.id, title: section.title })}>
                                        <Pencil size={14} /> Изменить
                                    </button>
                                )}
                            />
                            {section.accounts.length ? (
                                <AccountGrid>{section.accounts.map(accountCard)}</AccountGrid>
                            ) : (
                                <div className={`${iosCard} px-4 py-3.5 text-[13px] text-slate-500`}>
                                    В разделе пока нет аккаунтов — отметьте их кнопкой «Изменить» или добавьте новый.
                                </div>
                            )}
                        </section>
                    ))}
                </div>
            )}
        </>
    );

    /* ── Доступы ──────────────────────────────────────────────────────── */
    const peopleTab = (
        <>
            <div className="mb-3 flex items-center justify-between gap-3">
                <GroupLabel>{peopleCount((data.people || []).length)} с доступом</GroupLabel>
                <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={!(data.accounts || []).length} onClick={() => setPicking(true)}>
                    <UserPlus size={15} /> Выдать доступ
                </button>
            </div>
            {(data.people || []).length === 0 ? (
                <div className={iosCard}>
                    <EmptyState icon={Users} title="Доступ пока никому не выдан">
                        {(data.accounts || []).length
                            ? 'Выдайте сотруднику аккаунты, к которым ему можно запрашивать коды.'
                            : 'Сначала добавьте аккаунт во вкладке «Аккаунты».'}
                    </EmptyState>
                </div>
            ) : (
                <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {data.people.map((person) => (
                        <li key={person.id}>
                            <button type="button" onClick={() => setOpenedPerson(person)}
                                    className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-left transition hover:bg-slate-50">
                                <span className="min-w-0 grow basis-[200px]">
                                    <span className="block truncate text-[14px] font-semibold text-slate-900">{person.name}</span>
                                    <span className="block truncate text-[12.5px] text-slate-500">
                                        {person.department || 'Без отдела'}
                                        {!person.working && ' · не работает — доступ не действует'}
                                    </span>
                                </span>
                                <span className="flex min-w-0 max-w-full flex-wrap items-center gap-1.5">
                                    {!person.telegram_linked && <Pill tone="red">Telegram не привязан</Pill>}
                                    {(person.account_ids || []).map((id) => accountsById[id]).filter(Boolean).map((account) => (
                                        <Pill key={account.id} tone="blue">{account.title}</Pill>
                                    ))}
                                </span>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );

    /* ── Журнал ───────────────────────────────────────────────────────── */
    const journalTab = (
        <>
            {!journal ? (
                <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" /> Открываем журнал…
                </div>
            ) : journal.items.length === 0 ? (
                <div className={iosCard}>
                    <EmptyState icon={ScrollText} title="Журнал пуст">
                        Каждый запрос, решение и выдача кода попадут сюда. Журнал не правится.
                    </EmptyState>
                </div>
            ) : (
                <>
                    <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {journal.items.map((item) => {
                            const groupNote = journalGroupNote(item);
                            return (
                                <li key={item.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5">
                                    <span className="w-[92px] shrink-0 text-[12.5px] tabular-nums text-slate-500">
                                        {whenOf(item.at, serverTime)}
                                    </span>
                                    <span className="min-w-0 grow basis-0 text-[13.5px] text-slate-800">
                                        {/* «Система», а не «Раздел»: разделы теперь — группы аккаунтов. */}
                                        <span className="font-semibold">{item.system ? 'Система' : (item.actor_name || '—')}</span>
                                        {' — '}{item.label}
                                        {item.account_title && <> · «{item.account_title}»</>}
                                        {groupNote && <span className="text-slate-500"> · {groupNote}</span>}
                                        {item.subject_name && item.subject_name !== item.actor_name && <> · {item.subject_name}</>}
                                        {item.details?.valid_until && <span className="text-slate-500"> · действовал до {item.details.valid_until}</span>}
                                        {item.details?.via && <span className="text-slate-500"> · {viaLabel(item.details.via)}</span>}
                                        {item.details?.error && <span className="text-rose-600"> · {item.details.error}</span>}
                                    </span>
                                </li>
                            );
                        })}
                    </ul>
                    {journal.has_more && (
                        <div className="mt-3 flex justify-center">
                            <button type="button" className={TOOLBAR_BTN} disabled={journalBusy}
                                    onClick={() => loadJournal(journal.items[journal.items.length - 1]?.id)}>
                                {journalBusy && <Loader2 size={14} className="animate-spin" />}
                                Показать ещё
                            </button>
                        </div>
                    )}
                </>
            )}
        </>
    );

    const body = {
        codes: codesTab,
        requests: requestsTab,
        accounts: accountsTab,
        people: peopleTab,
        journal: journalTab,
    }[activeTab] || null;

    return shell(
        <>
            {header}
            {notices}
            {body}

            <AddAccountSheet
                open={adding}
                onClose={() => setAdding(false)}
                api={api}
                groups={data.groups || []}
                onSaved={({ count, batch }) => {
                    const more = batch && batch.size > batch.index + 1;
                    toast(more
                        ? `Добавлено: ${accountsCount(count)}. Это QR ${batch.index + 1} из ${batch.size} — отсканируйте следующий`
                        : `Добавлено: ${accountsCount(count)}`, 'success');
                    reload();
                }}
            />
            <AccountSheet
                account={openedAccount}
                onClose={() => setOpenedAccount(null)}
                api={api}
                peopleNames={peopleNames}
                groups={data.groups || []}
                onSaved={() => { toast('Сохранено', 'success'); reload(); }}
                onRemoved={(account) => { toast(`Аккаунт «${account.title}» удалён вместе с ключом`, 'success'); reload(); }}
            />
            <GroupSheet
                group={openedGroup}
                accounts={data.accounts || []}
                groups={data.groups || []}
                onClose={() => setOpenedGroup(null)}
                api={api}
                onSaved={(created) => { toast(created ? 'Раздел создан' : 'Сохранено', 'success'); reload(); }}
                onRemoved={(group, count) => {
                    toast(count ? `Раздел «${group.title}» удалён — его аккаунты теперь без раздела`
                        : `Раздел «${group.title}» удалён`, 'success');
                    reload();
                }}
            />
            <PeoplePicker
                open={picking}
                onClose={() => setPicking(false)}
                api={api}
                onPick={(person) => {
                    setPicking(false);
                    const known = (data.people || []).find((item) => item.id === person.id);
                    setOpenedPerson(known || { ...person, account_ids: [] });
                }}
            />
            <PersonSheet
                person={openedPerson}
                accounts={data.accounts || []}
                groups={data.groups || []}
                onClose={() => setOpenedPerson(null)}
                api={api}
                botLink={botLink}
                onSaved={() => { toast('Доступ сохранён', 'success'); reload(); }}
            />
        </>,
    );
};

export default TwofaView;
