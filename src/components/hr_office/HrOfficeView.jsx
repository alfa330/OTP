import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Settings } from 'lucide-react';
import { APPLE_FONT, IosSegmented, iosCard } from '../ui/ios';
import { createHrApi, errorText } from './hrOffice';
import { Loading } from './hrUi';

/*
 * «Кадры» (ТЗ HR «iCore 4») — один раздел, решение владельца 10.11.2026: «три отдельных раздела
 * делать не нужно, а сделать один единый, который уже будет раздельно отображаться у каждого».
 * Вкладки названы как разделы ТЗ:
 *   «Прием» (п. 1–4) — оформление и документы, отделу кадров;
 *   «Табель учета рабочего времени» (п. 6–10) — отделу кадров;
 *   «АВР» (п. 11–13) — отделу кадров на всех, главам отделов и супервайзерам на своих.
 * «Настройки» (правила оформления по умолчанию, ТЗ п. 3: кому какой вид, компания, должность) — не вкладка, а
 * шестерёнка в углу шапки, отделу кадров: показывает настройки на месте содержимого вкладки, шапка с вкладками и
 * сайдбар портала остаются (владелец 19.11.2026: «настройки не должны идти за ними … отдельной кнопкой в углу»,
 * «не модальным окном», «не на весь экран — в рамках раздела, сайдбар и прием/табель/авр на месте»).
 * Статусы сотрудников (п. 5) — в карточке «Приема», в карточке «Учета сотрудников» и в «Отметках».
 *
 * Каждый видит только свои вкладки (сервер: hr_office/service.py, capabilities); у кого вкладка одна,
 * переключателя нет. Открытые вкладки остаются в дереве (hidden), а не собираются заново: выбранный
 * месяц табеля, поиск и прокрутка переживают переключение. Ширина у всех вкладок одна — шапка и
 * переключатель не прыгают. Вкладка запоминается в адресе (?hr_tab=), обновление страницы её не теряет.
 */
const TABS = [
    {
        id: 'intake',
        title: 'Прием',
        subtitle: 'Оформление сотрудников и документы приема',
        capability: 'hiring',
        Component: lazy(() => import('./HrHiringTab')),
    },
    {
        id: 'timesheet',
        title: 'Табель учета рабочего времени',
        subtitle: 'Явка, часы, опоздания и переработки за месяц',
        capability: 'timesheet',
        Component: lazy(() => import('./HrTimesheetTab')),
    },
    {
        id: 'acts',
        title: 'АВР',
        subtitle: 'Акты выполненных работ',
        capability: 'acts',
        Component: lazy(() => import('./HrActsTab')),
    },
];

// «Настройки» — вид раздела за шестерёнкой в углу шапки; в адресе — ?hr_tab=settings, обновление страницы его не теряет.
const HrSettingsView = lazy(() => import('./HrSettingsView'));
const SETTINGS_ID = 'settings';
// Круглая кнопка-шестерёнка, как кнопки в шапке окон: служебное, а не ещё одна вкладка. Нажата — мягко-голубая.
const SETTINGS_BUTTON = 'h-9 w-9 shrink-0 place-items-center rounded-full transition active:scale-95 focus:outline-none '
    + 'focus-visible:ring-2 focus-visible:ring-blue-500/60';

/* Своя метка вкладки: «tab» занят вики — портал снимает его в любом разделе, кроме вики, и
   вкладка «Кадров» терялась при каждом обновлении страницы. Снимает «hr_tab» при уходе из раздела
   App.jsx (syncAppViewWithUrl). */
const TAB_PARAM = 'hr_tab';

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

export default function HrOfficeView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const api = useMemo(() => createHrApi(apiBaseUrl, headersRef), [apiBaseUrl]);

    const [boot, setBoot] = useState(null);
    const [error, setError] = useState('');
    const [active, setActive] = useState(tabFromUrl);
    const [visited, setVisited] = useState(() => new Set());

    const reloadBoot = useCallback(() => api.get('/context')
        .then(({ data }) => { setBoot(data); return data; })
        .catch((requestError) => { setError(errorText(requestError, 'Раздел не открылся — обновите страницу')); }), [api]);

    useEffect(() => { reloadBoot(); }, [reloadBoot]);

    const available = useMemo(() => TABS.filter((tab) => boot?.capabilities?.[tab.capability]), [boot]);
    const canSettings = Boolean(boot?.capabilities?.settings);
    // «Настройки» открыты — ни одна вкладка не выбрана, их содержимое на месте вкладки.
    const settingsShown = canSettings && active === SETTINGS_ID;
    const current = settingsShown ? null : (available.find((tab) => tab.id === active) || available[0] || null);
    useEffect(() => {
        if (current) setVisited((prev) => (prev.has(current.id) ? prev : new Set(prev).add(current.id)));
    }, [current]);
    // Куда вернуть шестерёнкой из «Настроек» — последняя открытая вкладка.
    const lastTab = useRef('');
    useEffect(() => { if (current) lastTab.current = current.id; }, [current]);

    // Правила поменялись — вкладки перечитывают строки (умолчания «Приема», должность в табеле и АВР).
    const [rulesVersion, setRulesVersion] = useState(0);
    const rulesChanged = useCallback(() => setRulesVersion((version) => version + 1), []);
    // «Настройки» — отдельный кусок сборки: подтягиваем заранее, чтобы по нажатию они открылись сразу.
    useEffect(() => { if (canSettings) import('./HrSettingsView').catch(() => {}); }, [canSettings]);
    // Несохранённая правка правила: «Настройки» сначала спрашивают «Сохранить / Не сохранять», потом вкладка меняется.
    const settingsGuard = useRef(null);

    const switchTo = (id) => {
        setActive(id);
        rememberTab(id);
    };
    const choose = (id) => {
        if (settingsShown && settingsGuard.current) settingsGuard.current(() => switchTo(id));
        else switchTo(id);
    };
    const toggleSettings = () => {
        if (settingsShown) choose(lastTab.current || available[0]?.id || '');
        else switchTo(SETTINGS_ID);
    };

    /* «Настройки» — в углу, отдельно от вкладок (владелец 19.11.2026: «не должны идти за ними»): круглая шестерёнка;
       нажата — настройки на месте содержимого вкладки, шапка и вкладки остаются; ещё нажатие — назад к вкладке. На
       компьютере — в правом углу за чертой после вкладок, на телефоне — в строке «Кадры»: в одном ряду со вкладками
       она ушла бы на свою строку (mobile-shell.css переносит ряды с gap) или спрятала бы «АВР» за край полосы. */
    const settingsButton = (display) => (
        <button type="button" onClick={toggleSettings} title="Настройки" aria-label="Настройки" aria-pressed={settingsShown}
                data-hr-settings-button className={`${display} ${SETTINGS_BUTTON} ${settingsShown
                    ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-200' : 'bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-800'}`}>
            <Settings size={17} />
        </button>
    );

    // На телефоне три вкладки — впритык, на узком экране не помещаются: полоса прокручивается вбок, открытая — на виду.
    const tabsRef = useRef(null);
    useEffect(() => {
        const list = tabsRef.current?.querySelector('[role="tablist"]');
        const tab = list?.querySelector('[aria-selected="true"]');
        if (!tab || list.scrollWidth <= list.clientWidth) return;
        const right = tab.offsetLeft + tab.offsetWidth - list.clientWidth;
        if (list.scrollLeft > tab.offsetLeft) list.scrollLeft = tab.offsetLeft;
        else if (list.scrollLeft < right) list.scrollLeft = right;
    }, [current?.id, available.length]);

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1800px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !boot) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!boot) return shell(<Loading text="Открываем раздел…" />);
    if (!current && !settingsShown) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>Раздел вам не открыт</div>);
    }
    let subtitle = 'Настройки — правила оформления по умолчанию';
    if (current) subtitle = available.length > 1 ? current.subtitle : `${current.title} — ${current.subtitle.toLowerCase()}`;

    return shell(
        <>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div className="w-full min-w-0 sm:w-auto">
                    <div className="flex items-center justify-between gap-2">
                        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Кадры</h1>
                        {canSettings && settingsButton('grid sm:hidden')}
                    </div>
                    <div className="text-[13px] text-slate-500" data-hr-tab-subtitle>{subtitle}</div>
                </div>
                <div className="flex min-w-0 max-w-full items-center gap-2">
                    {available.length > 1 && (
                        <div ref={tabsRef} className="min-w-0">
                            {/* Кнопки не сжимаются (иначе длинное «Табель учета рабочего времени» режется) — полоса
                                прокручивается. */}
                            <IosSegmented slide value={current?.id || ''} onChange={choose} ariaLabel="Вкладка раздела «Кадры»"
                                          className="max-w-full overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden [&>button]:shrink-0"
                                          options={available.map((tab) => ({ value: tab.id, label: tab.title }))} />
                        </div>
                    )}
                    {canSettings && <span aria-hidden="true" className="mx-1 hidden h-6 w-px bg-slate-200 sm:block" />}
                    {canSettings && settingsButton('hidden sm:grid')}
                </div>
            </div>
            {available.filter((tab) => tab.id === current?.id || visited.has(tab.id)).map((tab) => {
                const { Component } = tab;
                const shown = tab.id === current?.id;
                return (
                    <div key={tab.id} hidden={!shown} data-hr-tab={tab.id}
                         className={shown ? 'motion-safe:animate-fade-in-soft' : ''}>
                        <Suspense fallback={<Loading />}>
                            <Component api={api} boot={boot} toast={toast} reloadBoot={reloadBoot} rulesVersion={rulesVersion} />
                        </Suspense>
                    </div>
                );
            })}
            {settingsShown && (
                <div className="motion-safe:animate-fade-in-soft" data-hr-tab="settings">
                    <Suspense fallback={<Loading />}>
                        <HrSettingsView api={api} boot={boot} toast={toast} onChanged={rulesChanged} guardRef={settingsGuard} />
                    </Suspense>
                </div>
            )}
        </>,
    );
}
