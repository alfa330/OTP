import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, Loader2 } from 'lucide-react';
import MonthPicker from '../trainings/MonthPicker';
import { iosBtnPrimary, iosCard } from '../ui/ios';
import FrontOfficeTimesheetFilters from './FrontOfficeTimesheetFilters';
import { emptyTimesheetFilters } from './frontOfficeTimesheet';
import { downloadTimesheet } from './frontOfficeTimesheetApi';

/*
 * «Учет часов» главы фронт-офисов — одна страница табеля.
 *
 * Решение владельца 06.11.2026: табель фронт-офисов выгружают глава этого
 * отдела и суперадмины. Весь «Учет часов» (часы, бонусы и штрафы с правкой)
 * главе фронт-офисов не открывали — у неё в разделе только табель. Суперадмин
 * берёт табель плиткой окна «Сформировать отчёт» на обычной странице раздела.
 *
 * На телефоне — та же страница: карточка в ширину экрана, отборы столбиком.
 */
const currentMonth = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
};

export default function FrontOfficeTimesheetPage({ user, apiBaseUrl, withAccessTokenHeader, showToast }) {
    const [month, setMonth] = useState(currentMonth);
    const [filters, setFilters] = useState(emptyTimesheetFilters);
    const [isDownloading, setIsDownloading] = useState(false);
    const abortRef = useRef(null);

    const authHeaders = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader({ 'X-User-Id': String(user?.id ?? '') }) : {}),
        [withAccessTokenHeader, user?.id],
    );

    useEffect(() => () => abortRef.current?.abort(), []);

    const handleDownload = async () => {
        const controller = new AbortController();
        abortRef.current = controller;
        setIsDownloading(true);
        try {
            await downloadTimesheet({
                apiBaseUrl,
                headers: authHeaders(),
                month,
                filters,
                signal: controller.signal,
            });
            showToast?.('Табель загружен', 'success');
        } catch (error) {
            if (error?.name !== 'AbortError') showToast?.(error?.message || 'Не удалось скачать табель', 'error');
        } finally {
            if (abortRef.current === controller) abortRef.current = null;
            setIsDownloading(false);
        }
    };

    return (
        <div className="mx-auto w-full max-w-2xl">
            {/* На телефоне карточка во всю ширину, как в других разделах, а
                заголовку нужен свой отступ от края экрана. */}
            <div className="mb-4 flex flex-wrap items-center gap-3 px-4 md:px-0">
                <h2 className="mr-auto text-[22px] font-semibold tracking-tight text-slate-900">Учёт часов</h2>
                <MonthPicker value={month} onChange={setMonth} allowFuture />
            </div>
            <section className={`${iosCard} p-5`}>
                <h3 className="text-[15px] font-semibold text-slate-900">Табель учёта рабочего времени</h3>
                <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">
                    Фронт-офисы, Excel. Без отбора — все города и сотрудники.
                </p>
                <div className="mt-4">
                    <FrontOfficeTimesheetFilters
                        month={month}
                        apiBaseUrl={apiBaseUrl}
                        authHeaders={authHeaders}
                        value={filters}
                        onChange={setFilters}
                    />
                </div>
                <div className="mt-5 flex justify-end">
                    <button
                        type="button"
                        onClick={handleDownload}
                        disabled={isDownloading}
                        className={iosBtnPrimary}
                    >
                        {isDownloading
                            ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                            : <FileSpreadsheet size={15} aria-hidden="true" />}
                        {isDownloading ? 'Формируется…' : 'Скачать табель'}
                    </button>
                </div>
            </section>
        </div>
    );
}
