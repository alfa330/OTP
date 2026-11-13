import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus } from 'lucide-react';
import { APPLE_FONT, IosSegmented, iosCard } from '../ui/ios';
import { Loading, TOOLBAR_BTN_PRIMARY, useSheetState } from '../hr_office/hrUi';
import { TABS, createMailApi, errorText } from './mailMeta';
import MailOverview from './MailOverview';
import MailRegistry from './MailRegistry';
import MailShipmentCard from './MailShipmentCard';
import MailShipmentForm from './MailShipmentForm';
import MailParticipants from './MailParticipants';
import MailSettings from './MailSettings';

/*
 * «Почта» (#417): курьерские отправления между Алматы и региональными офисами — ТЗ
 * «Раздел «Почта» в ICORE» и макет «Макет_ЛК_ICORE_Почта_v3». Вкладки — разделы ТЗ п. 2,
 * названия — как в макете; «Участники» — решение владельца 13.11.2026 вместо «офис
 * определяется автоматически»: администратор раздела привязывает человека к офису и роли.
 *
 * Какие вкладки у человека, решает сервер (capabilities.tabs). Открытые вкладки остаются
 * в дереве (hidden): поиск, фильтр и страница переживают переключение. Вкладка — в адресе
 * (?tab=), обновление страницы её не теряет. Карточка отправления и форма — одни на раздел:
 * их открывает любая вкладка.
 */
/* Своя метка, а не `tab`: `tab` — метка вики, и портал снимает её при входе в любой другой раздел
   (App.jsx: buildAppViewUrl / syncAppViewWithUrl) — вкладка терялась бы при обновлении страницы. */
const TAB_PARAM = 'mail_tab';

function tabFromUrl() {
    try {
        return new URLSearchParams(window.location.search).get(TAB_PARAM) || '';
    } catch {
        return '';
    }
}

function rememberTab(id) {
    try {
        const url = new URL(window.location.href);
        url.searchParams.set(TAB_PARAM, id);
        window.history.replaceState(window.history.state, '', url.toString());
    } catch {
        // без адресной строки (встроенное окно) — просто не запоминаем
    }
}

export default function MailView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const api = useMemo(() => createMailApi(apiBaseUrl, headersRef), [apiBaseUrl]);

    const [context, setContext] = useState(null);
    const [error, setError] = useState('');
    const [active, setActive] = useState(tabFromUrl);
    const [visited, setVisited] = useState(() => new Set());
    // Быстрый фильтр, с которым плитка «Обзора» открывает реестр.
    const [registryQuick, setRegistryQuick] = useState({ value: null, token: 0 });
    // Любое изменение отправления — списки перечитываются.
    const [version, setVersion] = useState(0);
    const bump = useCallback(() => setVersion((value) => value + 1), []);

    const [cardId, keptCardId, openCardSheet, closeCard, , cardToken] = useSheetState(null);
    const [formCard, keptFormCard, openFormSheet, closeForm, , formToken] = useSheetState(null);

    const reloadContext = useCallback(() => api.get('/context')
        .then(({ data }) => { setContext(data); setError(''); return data; })
        .catch((requestError) => setError(errorText(requestError, 'Раздел не открылся — обновите страницу'))), [api]);
    useEffect(() => { reloadContext(); }, [reloadContext]);

    const tabs = useMemo(() => (context?.capabilities?.tabs || []).filter((id) => TABS[id]), [context]);
    const current = tabs.includes(active) ? active : tabs[0] || null;
    useEffect(() => {
        if (current) setVisited((prev) => (prev.has(current) ? prev : new Set(prev).add(current)));
    }, [current]);
    const choose = useCallback((id) => {
        setActive(id);
        rememberTab(id);
    }, []);

    const openCard = useCallback((id) => openCardSheet(id), [openCardSheet]);
    // Форма: null — новое отправление, карточка — правка.
    const openForm = useCallback((card = null) => openFormSheet(card || 'new'), [openFormSheet]);
    const showQuick = useCallback((quick) => {
        setRegistryQuick((prev) => ({ value: quick, token: prev.token + 1 }));
        choose('registry');
    }, [choose]);

    /* Окна сменяют друг друга, а не встают одно на другое: два затемнения разом читаются как
       сбой. Карточка уходит — открывается форма; форма сохранена — снова карточка. */
    const SWAP_MS = 380;
    const editFromCard = useCallback((card) => {
        closeCard();
        window.setTimeout(() => openFormSheet(card), SWAP_MS);
    }, [closeCard, openFormSheet]);
    const formSaved = useCallback((card) => {
        bump();
        closeForm();
        if (card?.shipment?.id) window.setTimeout(() => openCardSheet(card.shipment.id), SWAP_MS);
    }, [bump, closeForm, openCardSheet]);

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1800px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !context) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!context) return shell(<Loading text="Открываем раздел…" />);
    if (!current) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>Раздел вам не открыт</div>);
    }

    const canCreate = Boolean(context.capabilities?.can_create);
    return shell(
        <>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Почта</h1>
                    <div className="text-[13px] text-slate-500" data-mail-tab-subtitle>{TABS[current].subtitle}</div>
                </div>
                <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
                    {/* До семи вкладок: на узком экране полоса листается вбок, а не раздвигает страницу. */}
                    {tabs.length > 1 && (
                        <div className="thin-scroll min-w-0 max-w-full overflow-x-auto">
                            <IosSegmented slide value={current} onChange={choose} ariaLabel="Вкладка раздела «Почта»"
                                          className="min-w-max"
                                          options={tabs.map((id) => ({ value: id, label: TABS[id].title }))} />
                        </div>
                    )}
                    {canCreate && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={() => openForm(null)}>
                            <Plus size={15} /> Новое отправление
                        </button>
                    )}
                </div>
            </div>
            {tabs.filter((id) => id === current || visited.has(id)).map((id) => {
                const shown = id === current;
                let body = null;
                if (id === 'overview') {
                    body = <MailOverview api={api} context={context} version={version} onOpen={openCard} onQuick={showQuick} />;
                } else if (TABS[id].scope) {
                    body = (
                        <MailRegistry api={api} context={context} scope={TABS[id].scope} version={version} onOpen={openCard}
                                      toast={toast} quickRequest={id === 'registry' ? registryQuick : null} />
                    );
                } else if (id === 'participants') {
                    body = <MailParticipants api={api} context={context} toast={toast} onChanged={reloadContext} />;
                } else if (id === 'settings') {
                    body = <MailSettings api={api} toast={toast} onSaved={reloadContext} />;
                }
                return (
                    <div key={id} hidden={!shown} data-mail-tab={id} className={shown ? 'motion-safe:animate-fade-in-soft' : ''}>
                        {body}
                    </div>
                );
            })}
            {keptCardId && (
                <MailShipmentCard key={cardToken} open={Boolean(cardId)} shipmentId={keptCardId} api={api} context={context}
                                  toast={toast} onClose={closeCard} onChanged={bump} onEdit={editFromCard} />
            )}
            {keptFormCard && (
                <MailShipmentForm key={formToken} open={Boolean(formCard)} card={keptFormCard === 'new' ? null : keptFormCard}
                                  api={api} context={context} toast={toast} onClose={closeForm} onSaved={formSaved} />
            )}
        </>,
    );
}
