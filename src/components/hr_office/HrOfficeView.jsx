import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
 * Статусы сотрудников (п. 5) — в карточке «Приема», в карточке «Учета сотрудников» и в «Отметках».
 *
 * Каждый видит только свои вкладки (сервер: hr_office/service.py, capabilities); у кого вкладка одна,
 * переключателя нет. Открытые вкладки остаются в дереве (hidden), а не собираются заново: выбранный
 * месяц табеля, поиск и прокрутка переживают переключение. Ширина у всех вкладок одна — шапка и
 * переключатель не прыгают. Вкладка запоминается в адресе (?tab=), обновление страницы её не теряет.
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

const TAB_PARAM = 'tab';

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
    const current = available.find((tab) => tab.id === active) || available[0] || null;
    useEffect(() => {
        if (current) setVisited((prev) => (prev.has(current.id) ? prev : new Set(prev).add(current.id)));
    }, [current]);
    const choose = (id) => {
        setActive(id);
        rememberTab(id);
    };

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1800px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !boot) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!boot) return shell(<Loading text="Открываем раздел…" />);
    if (!current) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>Раздел вам не открыт</div>);
    }

    return shell(
        <>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Кадры</h1>
                    <div className="text-[13px] text-slate-500" data-hr-tab-subtitle>
                        {available.length > 1 ? current.subtitle : `${current.title} — ${current.subtitle.toLowerCase()}`}
                    </div>
                </div>
                {available.length > 1 && (
                    <IosSegmented slide value={current.id} onChange={choose} ariaLabel="Вкладка раздела «Кадры»"
                                  options={available.map((tab) => ({ value: tab.id, label: tab.title }))} />
                )}
            </div>
            {available.filter((tab) => tab.id === current.id || visited.has(tab.id)).map((tab) => {
                const { Component } = tab;
                const shown = tab.id === current.id;
                return (
                    <div key={tab.id} hidden={!shown} data-hr-tab={tab.id}
                         className={shown ? 'motion-safe:animate-fade-in-soft' : ''}>
                        <Suspense fallback={<Loading />}>
                            <Component api={api} boot={boot} toast={toast} reloadBoot={reloadBoot} />
                        </Suspense>
                    </div>
                );
            })}
        </>,
    );
}
