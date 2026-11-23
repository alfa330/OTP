import React from 'react';

import BrowserChrome from './BrowserChrome';
import CallPanel from './CallPanel';
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
export const DeskScreen = ({
    world, tap, target, browse, emit, onUi, misses, onCall, voice, aiSpeaking,
    micLevel, micError, onRing, devMode,
}) => {
    const tabs = [{ id: 'crm', title: crmTitle(world), icon: 'crm' }];
    const ui = makeUi({ world, onUi, browse, emit, target, misses });
    /* Кнопки самого браузера («Назад», «Закрыть вкладку») — тем же путём, что
       кнопки CRM: на практике и в смене чужое нажатие не промах, а ловушка
       сценария объясняется везде. */
    const chromeTap = onUi ? (id) => onUi({ kind: 'act', id }) : tap;
    return (
        <div className="wt-desk__inner">
            <BrowserChrome
                tap={chromeTap}
                target={target}
                tabs={tabs}
                active="crm"
                onSwitch={() => {}}
                url={crmUrl(world)}
            >
                <CrmFrame title="Учебная CRM"
                    focusKey={target ? `${world.crmView}:${[].concat(target).join(',')}` : ''}>
                    <CrmApp ui={ui} />
                </CrmFrame>
            </BrowserChrome>

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
