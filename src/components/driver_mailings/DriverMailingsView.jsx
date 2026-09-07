import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, ChevronDown, Loader2, Pencil, RefreshCw, Send, Trash2,
} from 'lucide-react';

import {
    APPLE_FONT, IosBadge, IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary,
    iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import MailingComposer from './MailingComposer';
import MailingJournal from './MailingJournal';
import MailingPreview from './MailingPreview';
import { composeBilingual, formatCount, splitBilingual } from './mailingText';
import { describeAudience, parksLabel, recipientsLabel } from './mailingMeta';

/* Раздел «Рассылки» (задача #166).
 *
 * Сообщение водителям в приложение Pro прямо из портала — без входа в кабинет
 * диспетчерской руками. Кабинет остаётся источником правды: он считает охват, он
 * отправляет, он же считает прочтения. Портал добавляет то, чего в кабинете нет:
 * одна форма на несколько диспетчерских сразу, предпросмотр «как увидит
 * водитель», журнал с настоящим автором и свои шаблоны.
 *
 * Цена ошибки высокая: рассылка уходит тысяче человек и отзывается только пять
 * минут. Поэтому отправка идёт через окно подтверждения, где ещё раз показаны
 * текст, охват и список диспетчерских, а кнопка защищена токеном идемпотентности
 * — двойной клик не отправит второй раз. */

const COUNT_DEBOUNCE_MS = 450;
const JOURNAL_PAGE = 20;

const EMPTY_DRAFT = {
    title: '', message: '', message_kk: '', message_ru: '', bilingual: false, park_ids: [],
};

/* Токен идемпотентности. crypto.randomUUID есть во всех браузерах, где живёт
   портал, но запасной путь оставлен: без токена повторный клик по «Отправить»
   создал бы вторую рассылку, а это уже сообщение людям. */
const newToken = () => {
    try {
        if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
            return globalThis.crypto.randomUUID();
        }
    } catch { /* дальше запасной путь */ }
    return `dm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
};

const formatDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
};

export default function DriverMailingsView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/driver_mailings`;

    /* showToast приходит из App новой функцией на КАЖДЫЙ его рендер. Если
       положить её в зависимости, следом пересоздаётся loadJournal, следом
       срабатывает эффект вкладки — и журнал перезапрашивается по любому чиху в
       приложении, подменяя список заглушкой «Загружаем журнал…». Этой граблёй
       портал уже наступал; держим ссылку в ref, а наружу отдаём стабильную
       функцию. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((text, kind) => {
        if (toastRef.current) toastRef.current(text, kind);
    }, []);

    const [tab, setTab] = useState('compose');
    const [overview, setOverview] = useState(null);
    const [loadError, setLoadError] = useState(null);
    const [refreshingParks, setRefreshingParks] = useState(false);
    const [linkOpen, setLinkOpen] = useState(false);

    const [draft, setDraft] = useState(EMPTY_DRAFT);
    const [filters, setFilters] = useState({});
    const [refs, setRefs] = useState(null);
    const [refsLoading, setRefsLoading] = useState(false);

    const [count, setCount] = useState(null);
    const [counting, setCounting] = useState(false);
    const [countError, setCountError] = useState(null);

    const [confirmOpen, setConfirmOpen] = useState(false);
    const [sending, setSending] = useState(false);
    const sendToken = useRef(null);

    const [journal, setJournal] = useState({ items: [], total: 0 });
    const [journalLoading, setJournalLoading] = useState(false);
    const [journalMore, setJournalMore] = useState(false);
    const [journalError, setJournalError] = useState(null);
    const [revoking, setRevoking] = useState(false);

    const [templatesOpen, setTemplatesOpen] = useState(false);
    const [templateName, setTemplateName] = useState('');
    // Обе кнопки открывают одно окно, но «Сохранить как шаблон» ставит курсор в
    // поле названия: иначе подпись обещает действие, а окно молча ждёт, и
    // выглядит это как «кнопка не работает».
    const templateNameRef = useRef(null);
    const [savingTemplate, setSavingTemplate] = useState(false);

    const parks = overview?.parks || [];
    const limits = overview?.limits || {};
    const session = overview?.session || {};
    const capabilities = overview?.capabilities || {};
    const templates = overview?.templates || [];
    const linked = Boolean(session.configured) && !session.last_error;

    const message = draft.bilingual
        ? composeBilingual(draft.message_kk, draft.message_ru)
        : String(draft.message || '');

    // ── загрузка раздела ─────────────────────────────────────────────────────

    const load = useCallback(() => {
        setLoadError(null);
        return axios.get(`${base}/overview`, { headers: headers() })
            .then((response) => setOverview(response.data))
            .catch((error) => setLoadError(
                error?.response?.data?.error || 'Не удалось загрузить раздел',
            ));
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    // Связь оборвалась — служебная строка раскрывается сама: человек пришёл
    // отправлять рассылку, и молчащая свёрнутая строка его не спасёт.
    useEffect(() => { if (overview && !linked) setLinkOpen(true); }, [overview, linked]);

    // ── справочники отбора ───────────────────────────────────────────────────

    /* Справочники зависят и от набора диспетчерских, и от сегмента (города
       кабинет отдаёт свои на каждый сегмент). Запрос идёт по номеру: медленный
       ответ на прежний набор не должен перетереть свежий. */
    const refsSeq = useRef(0);
    const parkKey = (draft.park_ids || []).join(',');
    useEffect(() => {
        if (!parkKey) { setRefs(null); return; }
        const seq = refsSeq.current + 1;
        refsSeq.current = seq;
        setRefsLoading(true);
        axios.get(`${base}/filters`, {
            headers: headers(),
            params: { park_ids: parkKey, segment: filters.segment || '' },
        })
            .then((response) => { if (refsSeq.current === seq) setRefs(response.data); })
            .catch(() => { if (refsSeq.current === seq) setRefs(null); })
            .finally(() => { if (refsSeq.current === seq) setRefsLoading(false); });
    }, [base, headers, parkKey, filters.segment]);

    // ── пересчёт получателей ─────────────────────────────────────────────────

    const countSeq = useRef(0);
    const filtersKey = JSON.stringify(filters);
    useEffect(() => {
        if (!parkKey) { setCount(null); setCountError(null); return undefined; }
        const seq = countSeq.current + 1;
        countSeq.current = seq;
        setCounting(true);
        const timer = setTimeout(() => {
            axios.post(`${base}/recipients/count`, {
                park_ids: parkKey.split(','), filters: JSON.parse(filtersKey),
            }, { headers: headers() })
                .then((response) => {
                    if (countSeq.current !== seq) return;
                    setCount(response.data);
                    setCountError(null);
                })
                .catch((error) => {
                    if (countSeq.current !== seq) return;
                    setCount(null);
                    setCountError(error?.response?.data?.error || 'Не удалось посчитать получателей');
                })
                .finally(() => { if (countSeq.current === seq) setCounting(false); });
        }, COUNT_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [base, headers, parkKey, filtersKey]);

    // ── журнал ───────────────────────────────────────────────────────────────

    const loadJournal = useCallback((offset = 0) => {
        const first = offset === 0;
        if (first) { setJournalLoading(true); setJournalError(null); } else setJournalMore(true);
        return axios.get(`${base}/journal`, {
            headers: headers(), params: { limit: JOURNAL_PAGE, offset },
        })
            .then((response) => {
                const data = response.data || {};
                setJournal((prev) => ({
                    items: first ? (data.items || []) : [...prev.items, ...(data.items || [])],
                    total: data.total || 0,
                }));
            })
            .catch((error) => {
                const text = error?.response?.data?.error || 'Не удалось загрузить журнал';
                if (first) setJournalError(text); else toast(text, 'error');
            })
            .finally(() => { if (first) setJournalLoading(false); else setJournalMore(false); });
    }, [base, headers, toast]);

    useEffect(() => { if (tab === 'journal') loadJournal(0); }, [tab, loadJournal]);

    /* Журнал открывают без выбранных диспетчерских, и справочников в состоянии
       нет — а описание отбора без них печатается машинными кодами («econom»,
       «taxi/driver»). Поэтому на вкладке журнала догружаем справочники по ВСЕМ
       разрешённым диспетчерским: запрос кэшируется на сервере, и людям видны
       нормальные слова. */
    useEffect(() => {
        if (tab !== 'journal' || refs || !parks.length) return;
        axios.get(`${base}/filters`, {
            headers: headers(),
            params: { park_ids: parks.map((park) => park.id).join(','), segment: '' },
        })
            .then((response) => setRefs(response.data))
            .catch(() => { /* журнал важнее подписей: покажем как есть */ });
    }, [tab, refs, parks, base, headers]);

    // ── действия ─────────────────────────────────────────────────────────────

    const openConfirm = () => {
        /* Токен живёт до УСПЕШНОЙ отправки, а не до закрытия окна. Раньше он
           рождался на каждое открытие: человек нажимал «Отправить», ответ
           терялся по дороге, окно закрывалось, он открывал заново — и получал
           новый токен, то есть вторую настоящую рассылку тем же людям. Теперь
           повтор придёт с прежним токеном, сервер узнает его и в кабинет не
           пойдёт. Обнуляется токен там же, где чистится форма. */
        if (!sendToken.current) sendToken.current = newToken();
        setConfirmOpen(true);
    };

    const doSend = () => {
        setSending(true);
        axios.post(`${base}/send`, {
            park_ids: draft.park_ids,
            title: draft.title,
            message: draft.bilingual ? '' : draft.message,
            message_kk: draft.bilingual ? draft.message_kk : '',
            message_ru: draft.bilingual ? draft.message_ru : '',
            filters,
            idempotency_key: sendToken.current,
        }, { headers: headers() })
            .then((response) => {
                const data = response.data || {};
                setConfirmOpen(false);
                if (data.status === 'failed') {
                    // Форму НЕ чистим: рассылка не ушла никуда, а набранный
                    // текст — единственное, что у человека есть. Стереть его
                    // значит заставить писать заново.
                    toast('Рассылка не ушла ни в одну диспетчерскую — смотрите журнал', 'error');
                    setTab('journal');
                    loadJournal(0);
                    return;
                }
                if (data.status === 'partial') {
                    toast('Рассылка ушла не во все диспетчерские — смотрите журнал', 'error');
                } else if (data.repeated) {
                    toast('Эта рассылка уже была отправлена', 'error');
                } else {
                    toast(`Рассылка ушла: ${recipientsLabel(data.sent_total || 0)}`, 'success');
                }
                sendToken.current = null;
                setDraft(EMPTY_DRAFT);
                setFilters({});
                setCount(null);
                setTab('journal');
                loadJournal(0);
            })
            .catch((error) => {
                toast(error?.response?.data?.error || 'Не удалось отправить рассылку', 'error');
            })
            .finally(() => setSending(false));
    };

    const revoke = (item) => {
        setRevoking(true);
        axios.post(`${base}/journal/${item.id}/revoke`, {}, { headers: headers() })
            .then((response) => {
                const revoked = response.data?.revoked || 0;
                toast(revoked
                    ? `Рассылка отозвана в ${parksLabel(revoked)}`
                    : 'Отзывать уже нечего — окно закрылось', revoked ? 'success' : 'error');
                loadJournal(0);
            })
            .catch((error) => toast(error?.response?.data?.error || 'Не удалось отозвать', 'error'))
            .finally(() => setRevoking(false));
    };

    /* Диспетчерские из старой рассылки или из шаблона могли исчезнуть: у аккаунта
       отобрали парк, и опрос выкинул его из списка. Молча оставить такой id в
       форме нельзя — выпадающий список показал бы пустоту или соврал числом
       («2 диспетчерские», из которых существует одна), а сервер всё равно
       отбросил бы его при отправке. Отсеиваем и говорим об этом вслух. */
    const keepKnownParks = (ids) => {
        const known = new Set(parks.map((park) => park.id));
        const kept = (ids || []).filter((id) => known.has(id));
        const dropped = (ids || []).length - kept.length;
        if (dropped) {
            toast(dropped === 1
                ? 'Одна диспетчерская из списка больше недоступна и не выбрана'
                : `Недоступных диспетчерских в списке: ${dropped} — они не выбраны`, 'error');
        }
        return kept;
    };

    const repeat = (item) => {
        const parts = splitBilingual(item.message);
        setDraft({
            title: item.title || '',
            bilingual: parts.bilingual,
            message: parts.bilingual ? '' : item.message || '',
            message_kk: parts.bilingual ? parts.kk : '',
            message_ru: parts.bilingual ? parts.ru : '',
            park_ids: keepKnownParks((item.targets || []).map((target) => target.park_id)),
        });
        setFilters(item.filters || {});
        setTab('compose');
        toast('Текст перенесён в форму — проверьте и отправьте', 'success');
    };

    const refreshParks = () => {
        setRefreshingParks(true);
        axios.post(`${base}/parks/refresh`, {}, { headers: headers() })
            .then(() => load().then(() => toast('Список диспетчерских обновлён', 'success')))
            .catch((error) => toast(error?.response?.data?.error || 'Не удалось обновить список', 'error'))
            .finally(() => setRefreshingParks(false));
    };

    const applyTemplate = (template) => {
        const bilingual = Boolean(template.message_kk || template.message_ru);
        setDraft({
            title: template.title || '',
            bilingual,
            message: bilingual ? '' : template.message || '',
            message_kk: template.message_kk || '',
            message_ru: template.message_ru || '',
            park_ids: keepKnownParks(template.park_ids || []),
        });
        setFilters(template.filters || {});
        setTemplatesOpen(false);
        setTab('compose');
    };

    const saveTemplate = () => {
        const name = templateName.trim();
        if (!name) return;
        setSavingTemplate(true);
        axios.post(`${base}/templates`, {
            name,
            title: draft.title,
            message: draft.bilingual ? '' : draft.message,
            message_kk: draft.bilingual ? draft.message_kk : '',
            message_ru: draft.bilingual ? draft.message_ru : '',
            filters,
            park_ids: draft.park_ids,
        }, { headers: headers() })
            .then(() => { setTemplateName(''); toast('Шаблон сохранён', 'success'); return load(); })
            .catch((error) => toast(error?.response?.data?.error || 'Не удалось сохранить шаблон', 'error'))
            .finally(() => setSavingTemplate(false));
    };

    const removeTemplate = (template) => {
        axios.delete(`${base}/templates/${template.id}`, { headers: headers() })
            .then(() => { toast('Шаблон удалён', 'success'); return load(); })
            .catch((error) => toast(error?.response?.data?.error || 'Не удалось удалить шаблон', 'error'));
    };

    const audience = useMemo(() => describeAudience(filters, refs), [filters, refs]);
    const draftEmpty = !draft.title && !message && !(draft.park_ids || []).length;

    // ── разметка ─────────────────────────────────────────────────────────────

    if (loadError) {
        return (
            <div className="mx-auto max-w-[1080px] px-4 py-6" style={{ fontFamily: APPLE_FONT }}>
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <AlertTriangle size={20} className="text-rose-400" />
                    <div className="text-[13px] text-rose-600">{loadError}</div>
                    <button type="button" className={iosBtnSecondary} onClick={load}>Попробовать снова</button>
                </div>
            </div>
        );
    }

    if (!overview) {
        return (
            <div className="mx-auto max-w-[1080px] px-4 py-6" style={{ fontFamily: APPLE_FONT }}>
                <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500`}>
                    <Loader2 size={15} className="animate-spin" /> Загружаем раздел…
                </div>
            </div>
        );
    }

    return (
        <div className="mx-auto max-w-[1080px] px-4 py-6" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h1 className="text-[20px] font-semibold tracking-tight text-slate-900">Рассылки</h1>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Сообщение водителям в приложение Pro — сразу по нескольким диспетчерским.
                    </p>
                </div>
                {tab === 'compose' && !draftEmpty && (
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        onClick={() => { setDraft(EMPTY_DRAFT); setFilters({}); setCount(null); }}
                    >
                        <Trash2 size={14} /> Очистить
                    </button>
                )}
            </div>

            <IosSegmented
                size="lg"
                stretch
                ariaLabel="Разделы рассылок"
                value={tab}
                onChange={setTab}
                options={[
                    { value: 'compose', label: 'Новая рассылка' },
                    { value: 'journal', label: 'Журнал', count: journal.total },
                ]}
                className="mb-4"
            />

            {tab === 'compose' ? (
                <MailingComposer
                    parks={parks}
                    limits={limits}
                    refs={refs}
                    refsLoading={refsLoading}
                    filters={filters}
                    onFilters={setFilters}
                    draft={draft}
                    onDraft={setDraft}
                    counting={counting}
                    count={count}
                    countError={countError}
                    sending={sending}
                    onSend={openConfirm}
                    onOpenTemplates={() => setTemplatesOpen(true)}
                    onSaveTemplate={() => {
                        setTemplatesOpen(true);
                        requestAnimationFrame(() => templateNameRef.current?.focus());
                    }}
                />
            ) : (
                <MailingJournal
                    items={journal.items}
                    total={journal.total}
                    loading={journalLoading}
                    loadingMore={journalMore}
                    error={journalError}
                    refs={refs}
                    revokeWindow={limits.revoke_seconds || 300}
                    revoking={revoking}
                    onMore={() => loadJournal(journal.items.length)}
                    onRevoke={revoke}
                    onRepeat={repeat}
                />
            )}

            {/* Служебная строка: связь с кабинетом. Внизу и свёрнутой — тому, кто
                пришёл отправить рассылку, читать про сессии незачем. */}
            <div className={`${iosCard} mt-5 overflow-hidden`}>
                <button
                    type="button"
                    onClick={() => setLinkOpen((prev) => !prev)}
                    className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition hover:bg-slate-50"
                >
                    <span className={`h-2 w-2 shrink-0 rounded-full ${linked ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                    <span className="text-[12.5px] font-medium text-slate-700">Связь с диспетчерской</span>
                    <span className="text-[12px] text-slate-500">{linked ? 'активна' : 'нужно восстановить'}</span>
                    <ChevronDown
                        size={15}
                        className={`ml-auto shrink-0 text-slate-400 transition ${linkOpen ? 'rotate-180' : ''}`}
                    />
                </button>
                {linkOpen && (
                    <div className="space-y-2 border-t border-slate-100 px-4 py-3 text-[12.5px] text-slate-600">
                        <div className="flex flex-wrap gap-x-6 gap-y-1">
                            <span>Аккаунт: <span className="text-slate-900">{session.account || '—'}</span></span>
                            <span>Диспетчерских с рассылкой: <span className="tabular-nums text-slate-900">{parks.length}</span></span>
                            <span>Обновлена: <span className="tabular-nums text-slate-900">{formatDateTime(session.updated_at)}</span></span>
                        </div>
                        {session.last_error && (
                            <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12px] text-amber-800 ring-1 ring-amber-100">
                                {session.last_error}
                            </div>
                        )}
                        <p className="text-[11.5px] text-slate-500">
                            Связь общая с разделом «Провайдер ЭДО» и настраивается там же — её обновляет
                            разработчик со своего компьютера. Здесь она только используется.
                        </p>
                        <button type="button" className={iosBtnGhost} onClick={refreshParks} disabled={refreshingParks}>
                            {refreshingParks
                                ? <Loader2 size={14} className="animate-spin" />
                                : <RefreshCw size={14} />}
                            Обновить список диспетчерских
                        </button>
                    </div>
                )}
            </div>

            {/* Подтверждение отправки */}
            <IosModal
                open={confirmOpen}
                onClose={() => { if (!sending) setConfirmOpen(false); }}
                title="Отправить рассылку?"
                subtitle={`${recipientsLabel(count?.total || 0)} · ${parksLabel((draft.park_ids || []).length)}`}
                maxWidth="max-w-lg"
                footer={(
                    <>
                        <button
                            type="button"
                            className={iosBtnSecondary}
                            disabled={sending}
                            onClick={() => setConfirmOpen(false)}
                        >
                            Отмена
                        </button>
                        <button type="button" className={iosBtnPrimary} disabled={sending} onClick={doSend}>
                            {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                            {sending ? 'Отправляем…' : `Отправить (${formatCount(count?.total || 0)})`}
                        </button>
                    </>
                )}
            >
                <div className="space-y-3.5">
                    <MailingPreview title={draft.title} message={message} />

                    <div>
                        <div className={iosGroupLabel}>Диспетчерские</div>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                            {(draft.park_ids || []).map((id) => {
                                const park = parks.find((item) => item.id === id);
                                const row = (count?.by_park || []).find((item) => item.park_id === id);
                                return (
                                    <IosBadge key={id} tone="slate" className="tabular-nums">
                                        {park ? park.name : id}
                                        {row && !row.error && <span className="text-slate-400"> · {formatCount(row.count)}</span>}
                                    </IosBadge>
                                );
                            })}
                        </div>
                    </div>

                    <div>
                        <div className={iosGroupLabel}>Кому</div>
                        <ul className="mt-1.5 space-y-1 text-[12.5px] text-slate-600">
                            {audience.map((line) => <li key={line}>{line}</li>)}
                        </ul>
                    </div>

                    <p className="rounded-xl bg-slate-100 px-3 py-2.5 text-[12px] leading-snug text-slate-600">
                        Сообщение придёт водителям в приложение Pro сразу. Отозвать его можно будет
                        в течение пяти минут — те, кто успел открыть уведомление, его уже увидят.
                    </p>
                </div>
            </IosModal>

            {/* Шаблоны */}
            <IosModal
                open={templatesOpen}
                onClose={() => setTemplatesOpen(false)}
                title="Шаблоны рассылок"
                subtitle="Готовые тексты вместе с отбором получателей"
                maxWidth="max-w-lg"
                footer={<button type="button" className={iosBtnGhost} onClick={() => setTemplatesOpen(false)}>Закрыть</button>}
            >
                <div className="space-y-4">
                    {capabilities.can_manage_templates && (
                        <div className={`${iosCard} p-3`}>
                            <div className="mb-1.5 text-[12.5px] font-medium text-slate-600">
                                Сохранить то, что сейчас в форме
                            </div>
                            <div className="flex gap-2">
                                <input
                                    ref={templateNameRef}
                                    value={templateName}
                                    onChange={(event) => setTemplateName(event.target.value.slice(0, 80))}
                                    placeholder="Название шаблона"
                                    className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                                />
                                <button
                                    type="button"
                                    className={iosBtnPrimary}
                                    disabled={!templateName.trim() || savingTemplate}
                                    onClick={saveTemplate}
                                >
                                    {savingTemplate ? <Loader2 size={15} className="animate-spin" /> : 'Сохранить'}
                                </button>
                            </div>
                        </div>
                    )}

                    {templates.length === 0 ? (
                        <div className="px-2 py-8 text-center text-[13px] text-slate-500">
                            Шаблонов пока нет
                        </div>
                    ) : (
                        <ul className="space-y-1.5">
                            {templates.map((template) => (
                                <li key={template.id} className={`${iosCard} flex items-center gap-2 px-3 py-2.5`}>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13px] font-medium text-slate-900">
                                            {template.name}
                                        </span>
                                        <span className="block truncate text-[11.5px] text-slate-500">
                                            {template.title || 'Без заголовка'}
                                        </span>
                                    </span>
                                    <button type="button" className={iosBtnGhost} onClick={() => applyTemplate(template)}>
                                        <Pencil size={13} /> Взять
                                    </button>
                                    {capabilities.can_manage_templates && (
                                        <button
                                            type="button"
                                            aria-label={`Удалить шаблон ${template.name}`}
                                            onClick={() => removeTemplate(template)}
                                            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </IosModal>
        </div>
    );
}
