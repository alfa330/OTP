import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import useIsMobileShell from '../common/useIsMobileShell';
import { ProfileGroup } from './profileUi';
import { DeskGroup, IconSquare } from './profileDesktop';

/*
 * «Моё имущество» в «Профиле» (ТЗ «Закуп и оплата», п. 12): имущество, которое
 * числится за сотрудником, — ноутбук, монитор, телефон.
 *
 * Блок есть у каждого, а не только у тех, кому открыт раздел «Оплата счетов»:
 * имущество закупает один человек, а пользуется им другой. Чьё имущество
 * показывать, решает сессия на сервере. Стоимости и ссылки на заявку здесь
 * нет — это сведения учёта, а не владельца.
 *
 * За кем ничего не числится, тому блок не рисуется вовсе: пустая группа
 * «Моё имущество — нет» в профиле оператора была бы шумом.
 */

const dateLabel = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
};

const placeLine = (asset) => [asset.city, asset.location].filter(Boolean).join(' · ');
const numbersLine = (asset) => [
    asset.inventory_number && `инв. № ${asset.inventory_number}`,
    asset.serial_number && `серийный № ${asset.serial_number}`,
].filter(Boolean).join(' · ');

export default function MyAssetsCard({ apiBaseUrl, userId, withAccessTokenHeader, legacy = false }) {
    const isMobileShell = useIsMobileShell();
    const [items, setItems] = useState([]);

    // Колбэк родителя пересоздаётся на каждый рендер App — в зависимости
    // эффекта его не ставим, иначе блок перезапрашивал бы данные бесконечно.
    const wrapRef = useRef(withAccessTokenHeader);
    wrapRef.current = withAccessTokenHeader;

    useEffect(() => {
        if (!userId) return undefined;
        let cancelled = false;
        const base = { 'X-User-Id': userId };
        const headers = typeof wrapRef.current === 'function' ? wrapRef.current(base) : base;
        axios.get(`${apiBaseUrl}/api/payments/my-assets`, { headers })
            .then((response) => { if (!cancelled) setItems(Array.isArray(response.data?.items) ? response.data.items : []); })
            // Не загрузилось — блока нет: имущество в профиле справочное, и отказ
            // здесь не должен выглядеть как поломка профиля.
            .catch(() => {});
        return () => { cancelled = true; };
    }, [apiBaseUrl, userId]);

    if (!items.length) return null;

    // Статус пишем, только когда он необычный: «в эксплуатации» у каждой строки — шум.
    const statusOf = (asset) => (asset.status && asset.status !== 'in_use' ? asset.status_label : '');

    if (!isMobileShell && !legacy) {
        return (
            <DeskGroup id="my-assets-title" title="Моё имущество" className="xl:col-span-3">
                <div className="divide-y divide-slate-100">
                    {items.map((asset) => (
                        <div key={asset.id} className="flex min-h-[60px] items-center gap-3 px-4 py-3 2xl:gap-4 2xl:px-5">
                            <IconSquare icon="fa-box" tone="slate" />
                            <div className="min-w-0 flex-1">
                                <div className="truncate text-[16px] text-slate-900">{asset.name}</div>
                                <div className="truncate text-[13px] text-slate-500">
                                    {[asset.category_name, dateLabel(asset.received_on) && `получено ${dateLabel(asset.received_on)}`, statusOf(asset)].filter(Boolean).join(' · ')}
                                </div>
                            </div>
                            <div className="min-w-0 max-w-[55%] text-right">
                                <div className="truncate text-[16px] tabular-nums text-slate-500">{numbersLine(asset)}</div>
                                <div className="truncate text-[13px] text-slate-400">{placeLine(asset)}</div>
                            </div>
                        </div>
                    ))}
                </div>
            </DeskGroup>
        );
    }

    const group = (
        <ProfileGroup id="my-assets-title" title="Моё имущество">
            {items.map((asset) => (
                <div key={asset.id} className={`${isMobileShell ? 'sa-m-row ' : ''}px-4 py-2.5`}>
                    <div className={`break-words text-slate-900 ${isMobileShell ? 'text-[16px] leading-[22px]' : 'text-[14px] leading-5'}`}>{asset.name}</div>
                    <div className="break-words text-[13px] leading-[18px] text-slate-500">
                        {[numbersLine(asset), placeLine(asset), statusOf(asset)].filter(Boolean).join(' · ')}
                    </div>
                </div>
            ))}
        </ProfileGroup>
    );
    // В прежнем виде «Профиля» блок стоит вне его карточки; на телефоне у раздела
    // боковых полей нет, и без своего поля карточка упиралась бы в края экрана.
    return legacy ? <div className={isMobileShell ? 'mb-8 px-4' : 'mb-8'}>{group}</div> : group;
}
