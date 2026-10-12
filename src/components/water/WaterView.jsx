import React, { useCallback } from 'react';
import { APPLE_FONT } from '../ui/ios';
import WaterPanel from './WaterPanel';
import { T } from './waterUi';

/*
 * Раздел «Учёт воды» — свой пункт меню (решение владельца 30.09.2026: «нужно
 * сделать это в отдельном разделе, а не в самих посылках»).
 *
 * Страница — только рамка и заголовок; всё содержимое живёт в WaterPanel,
 * который сам ходит в /api/water. Контракт с App.jsx тот же, что у остальных
 * разделов-реестров («Посылки», «Ссылка на подписание»): apiBaseUrl,
 * withAccessTokenHeader, showToast.
 *
 * Колонка раздела растёт вместе с экраном (размеры — в waterUi.jsx): на
 * большом мониторе в 1180 px он занимал меньше половины ширины. Потолки —
 * 1180 px как было, 1280 px с xl (окно от 1280 px), 1800 px на большом экране
 * (тот же, что у «Профиля»).
 */
const WaterView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    return (
        <div className="mx-auto w-full max-w-[1180px] px-3 py-4 sm:px-5 sm:py-6 xl:max-w-[1280px] 2xl:max-w-[1800px] 2xl:py-8"
             style={{ fontFamily: APPLE_FONT }}>
            <header className="mb-4 xl:mb-5 2xl:mb-7">
                <h1 className={`text-[19px] font-semibold leading-tight text-slate-900 ${T.title}`}>Учёт воды</h1>
                <p className={`mt-0.5 text-[12.5px] text-slate-500 xl:mt-1 2xl:mt-1.5 ${T.lead}`}>
                    Выдача питьевой воды водителям и остатки по фронт-офисам
                </p>
            </header>
            <WaterPanel apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
        </div>
    );
};

export default WaterView;
