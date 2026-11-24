import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRightLeft, PackagePlus, PackageX, Plus, ScanBarcode, ShoppingCart, UserMinus } from 'lucide-react';
import { APPLE_FONT, IosMenu, IosSegmented } from '../ui/ios';
import { Loading, useSheetState } from '../hr_office/hrUi';
import { KIND_MENU, TABS, createApi, errorText } from './fixedAssetsMeta';
import { CAPSULE, CAPSULE_PRIMARY, EmptyState } from './fixedAssetsUi';
import FaMyAssets from './FaMyAssets';
import FaRegistry from './FaRegistry';
import FaRequestBoard from './FaRequestBoard';
import FaDismissals from './FaDismissals';
import FaHistory from './FaHistory';
import FaSettings from './FaSettings';
import FaAssetCard from './FaAssetCard';
import FaRequestCard from './FaRequestCard';
import FaRequestForm from './FaRequestForm';
import FaIntakeForm from './FaIntakeForm';
import { FaScanFind } from './FaScanner';

/*
 * «Основные средства» (ACC-1, ТЗ бухгалтерии): приём в учёт, перемещение, списание и передача
 * при увольнении — только запросом с фотографиями и подписанным актом; вся история остаётся в
 * системе.
 *
 * Какие вкладки и кнопки у человека, решает сервер (capabilities). Сотрудник, за которым
 * числится ОС, только смотрит: «Мои ОС» и своя история — кнопки «Новый запрос» у него нет.
 * Запросы ведут бухгалтерия, IT, HR и руководители отделов. Открытые вкладки остаются в дереве
 * (hidden): поиск, отбор и страница переживают переключение. Вкладка и открытая карточка — в
 * адресе, обновление страницы их не теряет. Карточка ОС, карточка запроса и формы — одни на
 * раздел: их открывает любая вкладка.
 */
const TAB_PARAM = 'fa_tab';
const REQUEST_PARAM = 'fa_request';
const ASSET_PARAM = 'fa_asset';
const KIND_ICONS = { transfer: ArrowRightLeft, writeoff: PackageX, dismissal: UserMinus, need: ShoppingCart };
const TAB_WIDTH = 132;
const SCAN_ICON = 'grid h-10 w-10 shrink-0 place-items-center rounded-full bg-white text-slate-700 shadow-[0_0_0_1px_rgba(15,23,42,0.08),0_1px_2px_rgba(15,23,42,0.06)] transition hover:bg-slate-50 active:scale-[0.96]';

const readParam = (name) => {
    try {
        return new URLSearchParams(window.location.search).get(name) || '';
    } catch {
        return '';
    }
};

const writeParam = (name, value) => {
    try {
        const url = new URL(window.location.href);
        if (value) url.searchParams.set(name, String(value));
        else url.searchParams.delete(name);
        window.history.replaceState(window.history.state, '', url.toString());
    } catch {
        // без адресной строки (встроенное окно) — просто не запоминаем
    }
};

export default function FixedAssetsView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const api = useMemo(() => createApi(apiBaseUrl, headersRef), [apiBaseUrl]);

    const [context, setContext] = useState(null);
    const [error, setError] = useState('');
    const [active, setActive] = useState(() => readParam(TAB_PARAM));
    const [visited, setVisited] = useState(() => new Set());
    // Любое изменение ОС или запроса — списки перечитываются.
    const [version, setVersion] = useState(0);

    const reloadContext = useCallback(() => api.get('/context')
        .then(({ data }) => { setContext(data); setError(''); return data; })
        .catch((requestError) => setError(errorText(requestError, 'Раздел не открылся — обновите страницу'))), [api]);
    useEffect(() => { reloadContext(); }, [reloadContext]);
    const bump = useCallback(() => { setVersion((value) => value + 1); reloadContext(); }, [reloadContext]);

    /* Сотрудники для выбора МОЛ и получателя — один раз, когда открыли первую форму. */
    const [people, setPeople] = useState(null);
    const peopleAsked = useRef(false);
    const needPeople = useCallback(() => {
        if (peopleAsked.current) return;
        peopleAsked.current = true;
        api.get('/people').then(({ data }) => setPeople(data.items || []))
            .catch(() => { peopleAsked.current = false; });
    }, [api]);

    const [assetId, keptAssetId, openAssetSheet, closeAssetSheet, swapAsset, assetToken] = useSheetState(null);
    const [requestId, keptRequestId, openRequestSheet, closeRequestSheet, swapRequest, requestToken] = useSheetState(null);
    const [form, keptForm, , closeForm, swapForm, formToken] = useSheetState(null);
    const [intake, keptIntake, openIntakeSheet, closeIntake, swapIntake, intakeToken] = useSheetState(null);
    /* Сканер наклейки: найденное ОС открывается своей карточкой, а с ней — и действия над ним. */
    const [scanning, setScanning] = useState(false);

    const tabs = useMemo(() => (context?.capabilities?.tabs || []).filter((id) => TABS[id]), [context]);
    /* С какой вкладки раздел открывается, если её нет в адресе: есть запросы, которые ждут
       человека, — с них; у бухгалтерии, HR и IT — с реестра; у сотрудника — с «Моих ОС».
       Решается один раз при входе: вкладка не прыгает, когда «Ждут меня» опустело. */
    const firstTab = useRef(null);
    if (!firstTab.current && tabs.length) {
        firstTab.current = (Number(context?.waiting) > 0 && tabs.includes('requests')) ? 'requests'
            : (context?.capabilities?.sees_registry && tabs.includes('registry')) ? 'registry'
                : tabs[0];
    }
    const current = tabs.includes(active) ? active : (tabs.includes(firstTab.current) ? firstTab.current : tabs[0] || null);
    useEffect(() => {
        if (current) setVisited((prev) => (prev.has(current) ? prev : new Set(prev).add(current)));
    }, [current]);
    const choose = useCallback((id) => { setActive(id); writeParam(TAB_PARAM, id); }, []);

    const openAsset = useCallback((id) => { openAssetSheet(id); writeParam(ASSET_PARAM, id); }, [openAssetSheet]);
    const closeScan = useCallback(() => setScanning(false), []);
    const scannedAsset = useCallback((id) => { setScanning(false); openAsset(id); }, [openAsset]);
    const closeAsset = useCallback(() => { closeAssetSheet(); writeParam(ASSET_PARAM, ''); }, [closeAssetSheet]);
    /* Шаг, ради которого карточку запроса открыли с доски (её бросили в колонку): окно встаёт сразу
       на него. Обычное открытие шаг снимает. */
    const [requestStep, setRequestStep] = useState(null);
    const openRequest = useCallback((id) => { setRequestStep(null); openRequestSheet(id); writeParam(REQUEST_PARAM, id); }, [openRequestSheet]);
    const openRequestStep = useCallback((id, step) => {
        setRequestStep({ id, step });
        openRequestSheet(id);
        writeParam(REQUEST_PARAM, id);
    }, [openRequestSheet]);
    const closeRequest = useCallback(() => { closeRequestSheet(); writeParam(REQUEST_PARAM, ''); }, [closeRequestSheet]);

    // Ссылка на запрос или ОС (из адреса) открывает карточку сразу после входа в раздел.
    const linked = useRef(false);
    useEffect(() => {
        if (!context || linked.current) return;
        linked.current = true;
        const request = Number(readParam(REQUEST_PARAM));
        const asset = Number(readParam(ASSET_PARAM));
        if (request) openRequestSheet(request);
        else if (asset) openAssetSheet(asset);
    }, [context, openRequestSheet, openAssetSheet]);

    /* Окна сменяют друг друга, а не встают одно на другое: два затемнения разом читаются как
       сбой. Прежнее уходит — открывается следующее (swap в useSheetState). */
    const startRequest = useCallback((kind, preset = {}) => {
        needPeople();
        closeAsset();
        closeRequest();
        swapForm({ kind, preset });
    }, [needPeople, closeAsset, closeRequest, swapForm]);
    const editRequest = useCallback((card) => {
        needPeople();
        closeRequest();
        swapForm({ kind: card.request.kind, card });
    }, [needPeople, closeRequest, swapForm]);
    const startIntake = useCallback((source = null) => {
        needPeople();
        closeRequest();
        if (source) swapIntake({ source });
        else openIntakeSheet({ source: null });
    }, [needPeople, closeRequest, swapIntake, openIntakeSheet]);
    const requestFromAsset = useCallback((id) => {
        closeAsset();
        writeParam(REQUEST_PARAM, id);
        swapRequest(id);
    }, [closeAsset, swapRequest]);
    const assetFromRequest = useCallback((id) => {
        closeRequest();
        writeParam(ASSET_PARAM, id);
        swapAsset(id);
    }, [closeRequest, swapAsset]);
    const formSaved = useCallback((card) => {
        bump();
        closeForm();
        if (card?.request?.id) {
            writeParam(REQUEST_PARAM, card.request.id);
            swapRequest(card.request.id);
        }
    }, [bump, closeForm, swapRequest]);
    const intakeSaved = useCallback((asset) => {
        bump();
        closeIntake();
        if (asset?.id) {
            writeParam(ASSET_PARAM, asset.id);
            swapAsset(asset.id);
        }
    }, [bump, closeIntake, swapAsset]);

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1800px] bg-gray-50 px-3 py-4 sm:px-6 sm:py-7 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !context) return shell(<EmptyState title="Раздел не открылся">{error}</EmptyState>);
    if (!context) return shell(<Loading text="Открываем раздел…" />);
    if (!current) return shell(<EmptyState title="Раздел вам не открыт" />);

    const capabilities = context.capabilities || {};
    const kinds = (capabilities.create_kinds || []).filter((kind) => KIND_MENU[kind]);
    const canIntake = Boolean(capabilities.can_intake);
    const requestButton = canIntake ? CAPSULE : CAPSULE_PRIMARY;
    return shell(
        <>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 sm:mb-5">
                {/* Строка заголовка — сеткой через style: оболочка телефона переписывает flex-ряды. */}
                <div className="grid w-full items-center gap-3 sm:w-auto" style={{ gridTemplateColumns: 'minmax(0, 1fr) auto' }}>
                    <h1 className="text-[28px] font-bold leading-[1.05] tracking-tight text-slate-900 sm:text-[34px]">Основные средства</h1>
                    {/* На телефоне «Сканировать» — значком в строке заголовка, как кнопка в шапке iOS: три
                        кнопки с подписями в ряд не помещаются. */}
                    <button type="button" className={`${SCAN_ICON} sm:hidden`} aria-label="Сканировать" onClick={() => setScanning(true)}>
                        <ScanBarcode size={19} />
                    </button>
                </div>
                <div className={`flex flex-wrap items-center gap-2.5 ${kinds.length > 0 || canIntake ? '' : 'hidden sm:flex'}`}>
                    {/* Найти ОС по наклейке — всем в разделе: МОЛ откроет своё, бухгалтерия — любое. */}
                    <button type="button" className={`${CAPSULE} hidden sm:inline-flex`} onClick={() => setScanning(true)}>
                        <ScanBarcode size={17} /> Сканировать
                    </button>
                    {kinds.length === 1 && (
                        <button type="button" className={requestButton} onClick={() => startRequest(kinds[0])}>
                            <Plus size={17} strokeWidth={2.4} /> Новый запрос
                        </button>
                    )}
                    {kinds.length > 1 && (
                        /* Кнопка меню — светлая: в раскрытом виде IosMenu красит её серым, и синяя
                           кнопка на это время теряла бы цвет. */
                        <IosMenu
                            label="Новый запрос"
                            trigger={{ icon: <Plus size={17} strokeWidth={2.4} />, text: 'Новый запрос', className: CAPSULE }}
                            items={kinds.map((kind) => ({
                                key: kind, label: KIND_MENU[kind].label, icon: KIND_ICONS[kind],
                                onSelect: () => startRequest(kind),
                            }))}
                        />
                    )}
                    {canIntake && (
                        <button type="button" className={CAPSULE_PRIMARY} onClick={() => startIntake(null)}>
                            <PackagePlus size={17} /> Принять в учёт
                        </button>
                    )}
                </div>
            </div>

            {tabs.length > 1 && (
                <div className="thin-scroll mb-5 overflow-x-auto sm:mb-6">
                    <div style={{ width: tabs.length * TAB_WIDTH, maxWidth: '100%', minWidth: 'min-content' }}>
                        <IosSegmented size="lg" slide value={current} onChange={choose} ariaLabel="Вкладка раздела «Основные средства»"
                                      options={tabs.map((id) => ({
                                          value: id, label: TABS[id].title,
                                          count: id === 'requests' ? Number(context.waiting) || 0 : undefined,
                                      }))} />
                    </div>
                </div>
            )}

            {tabs.filter((id) => id === current || visited.has(id)).map((id) => {
                const shown = id === current;
                const common = { api, context, version, toast };
                let body = null;
                if (id === 'my') {
                    body = <FaMyAssets {...common} onOpenAsset={openAsset} onOpenRequest={openRequest} />;
                } else if (id === 'registry') {
                    body = <FaRegistry {...common} onOpenAsset={openAsset} />;
                } else if (id === 'requests') {
                    body = <FaRequestBoard {...common} onOpenRequest={openRequest} onStep={openRequestStep} />;
                } else if (id === 'dismissal') {
                    body = <FaDismissals {...common} people={people} needPeople={needPeople} onOpenRequest={openRequest}
                                         onStart={(userId) => startRequest('dismissal', { from_user_id: userId })} />;
                } else if (id === 'history') {
                    body = <FaHistory {...common} onOpenAsset={openAsset} onOpenRequest={openRequest} />;
                } else if (id === 'settings') {
                    body = <FaSettings {...common} people={people} needPeople={needPeople} onSaved={reloadContext} />;
                }
                return (
                    <div key={id} hidden={!shown} className={shown ? 'motion-safe:animate-fade-in-soft' : ''}>
                        {body}
                    </div>
                );
            })}

            {keptAssetId && (
                <FaAssetCard key={`asset-${assetToken}`} open={Boolean(assetId)} assetId={keptAssetId} api={api}
                             context={context} toast={toast} onClose={closeAsset} onChanged={bump}
                             onOpenRequest={requestFromAsset} onRequest={startRequest} />
            )}
            {keptRequestId && (
                <FaRequestCard key={`request-${requestToken}`} open={Boolean(requestId)} requestId={keptRequestId} api={api}
                               context={context} people={people} needPeople={needPeople} toast={toast}
                               onClose={closeRequest} onChanged={bump} onEdit={editRequest}
                               onOpenAsset={assetFromRequest} onAccept={startIntake}
                               step={requestStep && requestStep.id === keptRequestId ? requestStep.step : null} />
            )}
            {keptForm && (
                <FaRequestForm key={`form-${formToken}`} open={Boolean(form)} kind={keptForm.kind} card={keptForm.card || null}
                               preset={keptForm.preset || null} api={api} context={context} people={people}
                               toast={toast} onClose={closeForm} onSaved={formSaved} />
            )}
            {scanning && <FaScanFind api={api} canBind={canIntake} onClose={closeScan} onOpenAsset={scannedAsset} />}
            {keptIntake && (
                <FaIntakeForm key={`intake-${intakeToken}`} open={Boolean(intake)} source={keptIntake.source || null}
                              api={api} context={context} people={people} toast={toast}
                              onClose={closeIntake} onSaved={intakeSaved} />
            )}
        </>,
    );
}
