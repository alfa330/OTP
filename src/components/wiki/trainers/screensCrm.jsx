import React, { useEffect, useState } from 'react';

import BrowserChrome from './BrowserChrome';
import CallPanel from './CallPanel';
import IcorePhone, { IncomingCall, useDrag } from './IcorePhone';
import WinDesktop from './WinDesktop';
import FleetApp, { fleetUrl } from './screensFleet';
import { applyEdit } from './fleetCase.js';
import { DIRECTORY } from './callDirectory.js';
import CrmFrame from './crmFrame';
import CrmShell from './crmShell';
import { CRM_STUBS, CRM_VIEWS, crmUrl, emptyForm, groupOfItem, menuTitle } from './crmModel.js';
import { emptyYaForm } from './crmYandex.js';
import { noNav } from './crmKit';
import { RequestCreate, RequestList, RequestShow } from './crmRequests';
import { AccountCard, AccountSearch } from './crmDrivers';
import { YaCreate, YaList, YaShow } from './crmYandexAppeals';

/* Учебная CRM — та, в которой оператор работает на смене (снята 20.11.2026).
 *
 * ОДИН В ОДИН. CRM рисуется настоящими стилями рабочей CRM и её разметкой
 * (crmFrame.jsx — свой документ во фрейме, чтобы стили CRM и портала не
 * смешивались). Цвета, отступы, шрифты иконок — её собственные, а не
 * «похожие»: по ним CRM и узнают, выйдя на смену.
 *
 * Учебных разделов три — ровно по ТЗ обучения: «Обращения» (создать, найти,
 * открыть), «Учетные записи водителей» (поиск и карточка) и «Обращения в
 * Яндекс» (список, новое обращение, переписка). Остальные пункты меню и
 * кнопки, которые меняют данные водителя, открывают честную заглушку.
 *
 * Связь с уроком — объект ui (см. makeUi): страницы зовут ui.act / ui.touch /
 * ui.pick, а что из этого ход урока, а что просто работа в CRM, решает
 * движок (runner.uiAction). В режиме «Звонок» и на шагах-практике урок
 * не мешает: чужое нажатие там не промах.
 */

/* Заголовок вкладки браузера — как у рабочей CRM: «Страница - ITaxi». */
const PAGE_TITLES = {
    request_new: 'Создать обращение',
    requests: 'Обращения',
    request_show: 'Обращение',
    accounts: 'Учетные записи водителей',
    account: 'Карточка водителя',
    ya_list: 'Обращения в Яндекс',
    ya_new: 'Новое обращение в Яндекс',
    ya_show: 'Обращение в Яндекс',
};

export const crmTitle = (world) => `${world.crmView === 'stub'
    ? menuTitle(world.crmStub) : PAGE_TITLES[world.crmView] || 'Создать обращение'} - ITaxi`;

/** Страница, которой нет в учебной среде: пункт меню или кнопка, меняющая данные. */
const StubPage = ({ ui }) => {
    const key = ui.world.crmStub;
    const title = menuTitle(key) || 'Раздел';
    const own = CRM_STUBS[key];
    return (
        <div className="row">
            <div className="col">
                <div className="card">
                    <nav aria-label="breadcrumb">
                        <ol className="breadcrumb">
                            <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                            <li className="breadcrumb-item active" aria-current="page">{title}</li>
                        </ol>
                    </nav>
                    <div className="card-header"><i className="fa fa-align-justify" /> {title} </div>
                    <div className="card-body">
                        <div className="alert alert-secondary py-3 mb-0">
                            {own ? own[2] : `Раздел «${title}» в учебную среду не входит: здесь учатся обращениям, учётным записям водителей и обращениям в Яндекс.`}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

const PAGES = {
    request_new: RequestCreate,
    requests: RequestList,
    request_show: RequestShow,
    accounts: AccountSearch,
    account: AccountCard,
    ya_list: YaList,
    ya_new: YaCreate,
    ya_show: YaShow,
    stub: StubPage,
};

/**
 * Переход на страницу CRM — что меняется в мире. Как у настоящей страницы:
 * «Создать обращение» и «Новое обращение в Яндекс» открываются чистыми,
 * поиск учёток — без запроса (история поиска остаётся), а группа меню, в
 * которой лежит раздел, раскрыта.
 */
export const viewPatch = (world, view, extra = {}) => {
    const patch = { crmView: view, crmMenuQuery: '', drvModal: null };
    if (view === 'request_new') patch.form = emptyForm(world.case?.crm?.prefill);
    if (view === 'ya_new') patch.yaForm = emptyYaForm();
    if (view === 'accounts') { patch.drvQuery = ''; patch.drvSearched = null; }
    if (view === 'account') {
        patch.drvTab = 'general';
        patch.drvOpenedAt = world.drvOpenedAt || world.now.slice(0, 16);
    }
    const item = view === 'stub' ? extra.crmStub : CRM_VIEWS[view];
    const group = item ? groupOfItem(item) : null;
    if (group && !(world.crmGroups || []).includes(group)) patch.crmGroups = [...(world.crmGroups || []), group];
    return { ...patch, ...extra };
};

/**
 * Связь страниц CRM с уроком.
 *   set    — состояние самой CRM (поиск раздела, гаснущее уведомление): не действие;
 *   act    — кнопка или ссылка; touch — поле, вкладка, окно; pick — выбор из списка;
 *   go     — переход на страницу CRM; menu — пункт меню; crumb — «Главная»;
 *   save   — «Сохранить» обращения (заодно — событие для статистики);
 *   t      — класс подсветки цели урока; misses — будет ли нажатие промахом.
 */
export const makeUi = ({ world, onUi, browse, emit, target, misses }) => {
    const targets = Array.isArray(target) ? target : (target ? [target] : []);
    const send = (kind, id, patch = null, payload = {}) => {
        if (onUi) onUi({ kind, id, patch, payload });
        else if (patch && browse) browse(patch);
    };
    const ui = {
        world,
        emit,
        set: (patch) => (browse ? browse(patch) : send('touch', '_set', patch)),
        act: (id, patch, payload) => send('act', id, patch, payload),
        touch: (id, patch, payload) => send('touch', id, patch, payload),
        pick: (id, patch, payload) => send('pick', id, patch, payload),
        go: (view, extra = {}, id = `go_${view}`, payload = {}) => {
            send('act', id, viewPatch(world, view, extra), { view, ...payload });
            if (emit) {
                emit('crm.section', { view });
                if (view === 'account') emit('crm.driver_open', {});
            }
        },
        menu: (key, view) => {
            send('act', `nav_${key}`, view ? viewPatch(world, view) : viewPatch(world, 'stub', { crmStub: key }), { view: view || key });
            if (emit) emit('crm.section', { view: view || key });
        },
        crumb: (key) => send('act', `crumb_${key}`, viewPatch(world, 'stub', { crmStub: key })),
        save: (id, patch) => {
            if (emit) emit('crm.save', { form: world.form });
            send('act', id, { ...patch, saved: true }, { form: world.form });
        },
        t: (...ids) => (ids.some((id) => targets.includes(id)) ? ' is-target' : ''),
        misses: (id) => (misses ? misses(id) : false),
    };
    return ui;
};

/** Учебная CRM целиком: оболочка и текущая страница. */
const CrmApp = ({ ui }) => {
    const Page = PAGES[ui.world.crmView] || RequestCreate;
    return (
        <CrmShell ui={ui}>
            <Page ui={ui} />
        </CrmShell>
    );
};

/** Экран рабочего места: окно браузера с учебной CRM и плашка звонка.
 *
 * В окне одна вкладка — CRM: на смене оператор почти всё делает в ней
 * (владелец, 23.11.2026), и всё, что нужно для ответа водителю, есть в
 * карточке водителя CRM.
 *
 * Плашка звонка стоит ПОВЕРХ окна: во время разговора оператор листает CRM,
 * и кнопки «Удержание» и «Перевод» обязаны оставаться под рукой.
 */
/* ── Вкладки браузера в среде Windows ───────────────────────────────────────
   Владелец, 25.11.2026: «должны быть три вкладки — одна для обращений, другая
   для обращений в Яндекс и ещё одна для Диспетчерской». Две первые — одна и та
   же CRM, но у каждой вкладки своя страница: «Обращения» помнит форму и
   список, «Обращения в Яндекс» — свой список и переписку. Поля «где мы» в мире
   общие (CRM одна), поэтому при смене вкладки текущие уходят в tabPages, а у
   новой — восстанавливаются (впервые — стартовая страница вкладки). */
const PAGE_FIELDS = ['crmView', 'crmStub', 'crmShown', 'crmGroups', 'crmMenuQuery', 'drvQuery', 'drvSearched',
    'drvOpenId', 'drvOpenNumber', 'drvTab', 'drvModal', 'drvOrder', 'drvOpenedAt', 'yaOpen', 'yaFilter'];
const PAGE_BLANK = {
    crmStub: null, crmShown: null, crmMenuQuery: '', drvQuery: '', drvSearched: null, drvOpenId: null,
    drvOpenNumber: null, drvTab: 'general', drvModal: null, drvOrder: null, drvOpenedAt: null, yaOpen: null, yaFilter: null,
};
const TAB_START = {
    crm: { ...PAGE_BLANK, crmView: 'request_new', crmGroups: ['tickets'] },
    crm_ya: { ...PAGE_BLANK, crmView: 'ya_list', crmGroups: ['support'] },
};
const pageOf = (world) => Object.fromEntries(PAGE_FIELDS.map((key) => [key, world[key]]));

/** Переход на вкладку next: что записать в мир (null — уже там). */
export const switchTabPatch = (world, next) => {
    const current = world.tab || 'crm';
    if (next === current) return null;
    const pages = { ...(world.tabPages || {}) };
    if (current !== 'fleet') pages[current] = pageOf(world);
    const restore = next === 'fleet' ? {} : (pages[next] || TAB_START[next] || {});
    return { tab: next, tabPages: pages, ...restore };
};

/** Вкладки окна браузера среды Windows: заголовок каждой — по её странице. */
export const windowsTabs = (world) => {
    const pageFor = (id) => (id === (world.tab || 'crm') ? world
        : { ...world, ...((world.tabPages || {})[id] || TAB_START[id]) });
    const tabs = [
        { id: 'crm', title: crmTitle(pageFor('crm')), icon: 'crm' },
        { id: 'crm_ya', title: crmTitle(pageFor('crm_ya')), icon: 'crm' },
    ];
    if (world.fleetCase) tabs.push({ id: 'fleet', title: 'Диспетчерская', icon: 'fleet' });
    return tabs;
};

/* Уведомление при входе в смену — первое, что видно в среде Windows. */
const SHIFT_TOAST = {
    app: 'Тренажёр',
    title: 'Вы на линии водителей',
    text: 'Входящий придёт в iCORE Phone через несколько секунд. Ответьте, выслушайте водителя '
        + 'и найдите его в CRM по номеру — правда о его деле в карточке.',
};

/**
 * Среда Windows (владелец, 25.11.2026): браузер с CRM на весь экран, поверх —
 * iCORE Phone, внизу панель задач. Окно телефона перетаскивается за заголовок
 * и сворачивается на панель задач; на входящий оно открывается само — звонок
 * пропустить нельзя. Окно «Входящий вызов» — слева от телефона.
 */
const WinDesk = ({
    world, crm, onCall, voice, aiSpeaking, micLevel, micError, onFinishAttempt, desktopMenu, buddy,
}) => {
    const call = world.call || {};
    const [phoneOpen, setPhoneOpen] = useState(true);
    const [toast, setToast] = useState(SHIFT_TOAST);
    const { pos, handlers } = useDrag(null);
    const ringing = call.state === 'ringing';

    // Входящий: телефон открывается сам, а уведомление уступает место окну
    // «Входящий вызов» — оба живут справа внизу.
    useEffect(() => {
        if (!ringing) return;
        setPhoneOpen(true);
        setToast(null);
    }, [ringing]);
    useEffect(() => {
        if (!toast) return undefined;
        const id = setTimeout(() => setToast(null), 11000);
        return () => clearTimeout(id);
    }, [toast]);

    const phoneStyle = pos ? { left: pos.left, top: pos.top, right: 'auto', bottom: 'auto' } : null;
    return (
        <WinDesktop
            browser={crm}
            phone={phoneOpen ? (
                <IcorePhone
                    call={call}
                    onCall={onCall}
                    directory={DIRECTORY}
                    voice={voice}
                    aiSpeaking={aiSpeaking}
                    micLevel={micLevel}
                    micError={micError}
                    onFinishAttempt={onFinishAttempt}
                    onMinimize={() => setPhoneOpen(false)}
                    style={phoneStyle}
                    dragHandlers={handlers}
                />
            ) : null}
            ring={ringing ? <IncomingCall call={call} onCall={onCall} /> : null}
            phoneOpen={phoneOpen}
            onPhoneToggle={() => setPhoneOpen((open) => !open)}
            ringing={ringing}
            menu={desktopMenu || []}
            toast={toast}
            onToastClose={() => setToast(null)}
            buddy={buddy}
        />
    );
};

export const DeskScreen = ({
    world, tap, target, browse, emit, onUi, misses, onCall, voice, aiSpeaking,
    micLevel, micError, onRing, devMode, environment = null, desktopMenu = null, onFinishAttempt = null,
    buddy = null,
}) => {
    const windows = environment === 'windows';
    const tab = windows ? (world.tab || 'crm') : 'crm';
    const tabs = windows ? windowsTabs(world) : [{ id: 'crm', title: crmTitle(world), icon: 'crm' }];
    const switchTab = (id) => {
        const patch = switchTabPatch(world, id);
        if (!patch || !browse) return;
        if (emit) emit('ui.tab', { tab: id });
        browse(patch);
    };
    /* Правки в учебной Диспетчерской — только над копией дела в попытке. */
    const fleetAct = (what, args) => {
        if (browse && world.fleetCase) browse({ fleetCase: applyEdit(world.fleetCase, what, args) });
    };
    const ui = makeUi({ world, onUi, browse, emit, target, misses });
    /* Кнопки самого браузера («Назад», «Закрыть вкладку») — тем же путём, что
       кнопки CRM: на практике и в смене чужое нажатие не промах, а ловушка
       сценария объясняется везде. */
    const chromeTap = onUi ? (id) => onUi({ kind: 'act', id }) : tap;
    const browser = (
        <BrowserChrome
            tap={chromeTap}
            target={target}
            tabs={tabs}
            active={tab}
            onSwitch={switchTab}
            url={tab === 'fleet' ? fleetUrl(world) : crmUrl(world)}
            variant={windows ? 'windows' : 'mac'}
        >
            {/* CRM не размонтируется при уходе на Диспетчерскую: фрейм со
                стилями CRM грузился бы заново и мигал. */}
            <CrmFrame title="Учебная CRM" hidden={tab === 'fleet'}
                focusKey={target ? `${world.crmView}:${[].concat(target).join(',')}` : ''}>
                <CrmApp ui={ui} />
            </CrmFrame>
            {tab === 'fleet' && world.fleetCase
                ? <FleetApp world={world} go={browse} emit={emit || (() => {})} act={fleetAct} /> : null}
        </BrowserChrome>
    );
    if (windows) {
        return (
            <WinDesk world={world} crm={browser} onCall={onCall} voice={voice} aiSpeaking={aiSpeaking}
                micLevel={micLevel} micError={micError} onFinishAttempt={onFinishAttempt}
                desktopMenu={desktopMenu} buddy={buddy} />
        );
    }
    return (
        <div className="wt-desk__inner">
            {browser}

            {onCall ? (
                <CallPanel
                    call={world.call}
                    onCall={onCall}
                    directory={DIRECTORY}
                    voice={voice}
                    aiSpeaking={aiSpeaking}
                    micLevel={micLevel}
                    micError={micError}
                    onRing={onRing}
                    devMode={devMode}
                />
            ) : null}
        </div>
    );
};
