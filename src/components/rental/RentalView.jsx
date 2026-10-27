import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Car } from 'lucide-react';
import { APPLE_FONT, IosHint, IosSegmented, iosCard } from '../ui/ios';
import ConditionsTab from './ConditionsTab';
import FleetTab from './FleetTab';
import NightReport, { NIGHT_HOW_IT_WORKS } from './NightReport';
import RegistryTab from './RegistryTab';
import { errorOf } from './rentalMeta';
import { Loading } from './rentalUi';

/*
 * Раздел «Аренда авто». Вкладки — по правам (rental/access.py):
 *
 *   Наличие авто, Реестр направленных, Условия работы — #391: отдел продаж (направляет клиента
 *       в офис), отдел аренды и руководители (ведут наличие, итог визита и условия);
 *   Ночной отчёт — #398: ООЗ.
 *
 * Шапка общая: сплошная плитка без градиента (владелец 27.10.2026) и вкладки сегментами.
 * Последняя открытая вкладка помнится в браузере — без неё раздел открывается на первой.
 */

const TAB_STORAGE_KEY = 'rental.tab';

const FLEET_HOW_IT_WORKS =
    'Свободные машины указывают сотрудники аренды — у каждой карточки своё число, путевой лист — отдельная карточка. '
    + 'Из Yume приходит только, сколько машин модели в аренде и в брони. Нет свободных — направить нельзя. '
    + 'Направление машину не бронирует: клиент получает запись «Ожидаем» в реестре, итог визита ставит аренда.';

// short — подпись вкладки на телефоне: полные названия в одну строку там не помещаются.
const TAB_META = {
    fleet: { label: 'Наличие авто', short: 'Наличие', subtitle: 'Свободные машины и направление клиента в офис', hint: FLEET_HOW_IT_WORKS },
    registry: { label: 'Реестр направленных', short: 'Реестр', subtitle: 'Направленные клиенты и итог визита' },
    conditions: { label: 'Условия работы', short: 'Условия', subtitle: 'Единая памятка для консультации' },
    night: { label: 'Ночной отчёт', short: 'Ночной отчёт', subtitle: 'Ночной отчёт по арендным машинам', hint: NIGHT_HOW_IT_WORKS },
};

const tabLabel = ({ label, short }) => (short === label ? label : (
    <>
        <span className="sm:hidden">{short}</span>
        <span className="hidden sm:inline">{label}</span>
    </>
));

const readStoredTab = () => {
    try {
        return window.localStorage.getItem(TAB_STORAGE_KEY) || '';
    } catch (storageError) {
        return '';
    }
};

const RentalView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);

    const api = useMemo(() => {
        const url = (path) => `${apiBaseUrl}/api/rental${path}`;
        const config = (extra) => ({ headers: headersRef.current ? headersRef.current() : {}, ...extra });
        return {
            get: (path, params) => axios.get(url(path), config({ params })),
            post: (path, body) => axios.post(url(path), body || {}, config()),
            put: (path, body) => axios.put(url(path), body || {}, config()),
            patch: (path, body) => axios.patch(url(path), body || {}, config()),
            delete: (path) => axios.delete(url(path), config()),
            blob: (path) => axios.get(url(path), config({ responseType: 'blob' })),
        };
    }, [apiBaseUrl]);

    // Первый ответ раздела: права, а у ночной смены — ещё и последний отчёт (его берёт вкладка).
    const [boot, setBoot] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState(readStoredTab);

    useEffect(() => {
        let alive = true;
        api.get('')
            .then(({ data }) => { if (alive) setBoot(data); })
            .catch((requestError) => { if (alive) setError(errorOf(requestError, 'Раздел не открылся — попробуйте обновить страницу')); });
        return () => { alive = false; };
    }, [api]);

    const caps = boot?.capabilities || {};
    const tabs = useMemo(() => [
        caps.fleet && 'fleet', caps.fleet && 'registry', caps.fleet && 'conditions', caps.night_report && 'night',
    ].filter(Boolean), [caps.fleet, caps.night_report]);
    const active = tabs.includes(tab) ? tab : tabs[0];
    const chooseTab = (value) => {
        setTab(value);
        try {
            window.localStorage.setItem(TAB_STORAGE_KEY, value);
        } catch (storageError) {
            // Приватное окно: вкладка просто не запомнится.
        }
    };

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1400px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !boot) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!boot) return shell(<Loading text="Открываем раздел…" />);

    const meta = TAB_META[active] || TAB_META.night;
    return shell(
        <>
            <header className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
                <div className="flex min-w-0 items-center gap-3">
                    {/* Плитка без градиента — владелец 27.10.2026: «не нравится градиент, его надо убрать». */}
                    <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-blue-600 text-white shadow-sm">
                        <Car size={24} strokeWidth={2} />
                    </span>
                    <div className="min-w-0 grow basis-0">
                        <h1 className="flex items-center gap-1.5 text-[22px] font-bold leading-tight tracking-tight text-slate-900">
                            Аренда авто
                            {meta.hint && <IosHint text={meta.hint} label="Как это работает" />}
                        </h1>
                        <p className="mt-0.5 text-[13px] text-slate-500">{meta.subtitle}</p>
                    </div>
                </div>
                {tabs.length > 1 && (
                    <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
                        <IosSegmented value={active} onChange={chooseTab} ariaLabel="Вкладка раздела"
                                      options={tabs.map((value) => ({ value, label: tabLabel(TAB_META[value]) }))} />
                    </div>
                )}
            </header>
            {active === 'fleet' && <FleetTab api={api} toast={toast} />}
            {active === 'registry' && <RegistryTab api={api} toast={toast} caps={caps} />}
            {active === 'conditions' && <ConditionsTab api={api} toast={toast} />}
            {active === 'night' && <NightReport api={api} toast={toast} initial={boot.report !== undefined ? boot : null} />}
        </>,
    );
};

export default RentalView;
