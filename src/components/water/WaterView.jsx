import React, { useCallback } from 'react';
import { APPLE_FONT } from '../ui/ios';
import WaterPanel from './WaterPanel';

/*
 * Раздел «Учёт воды» — свой пункт меню (решение владельца 30.09.2026: «нужно
 * сделать это в отдельном разделе, а не в самих посылках»).
 *
 * Страница — только рамка и заголовок; всё содержимое живёт в WaterPanel,
 * который сам ходит в /api/water. Контракт с App.jsx тот же, что у остальных
 * разделов-реестров («Посылки», «Ссылка на подписание»): apiBaseUrl,
 * withAccessTokenHeader, showToast.
 */
const WaterView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    return (
        <div className="mx-auto w-full max-w-[1180px] px-3 py-4 sm:px-5 sm:py-6" style={{ fontFamily: APPLE_FONT }}>
            <header className="mb-4">
                <h1 className="text-[19px] font-semibold leading-tight text-slate-900">Учёт воды</h1>
                <p className="mt-0.5 text-[12.5px] text-slate-500">
                    Выдача питьевой воды водителям и остатки по фронт-офисам
                </p>
            </header>
            <WaterPanel apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
        </div>
    );
};

export default WaterView;
