import React from 'react';

/** undefined — поле не относится к отделу, null — источник не сообщил парк. */
export default function TaxiPark({ value, className = '' }) {
    if (value === undefined) return null;
    const label = String(value || '').trim() || 'не определён';
    return (
        <p className={`min-w-0 truncate text-[12px] text-slate-500 ${className}`}
           title={`Таксопарк: ${label}`}>
            Таксопарк: <span className="font-medium text-slate-700">{label}</span>
        </p>
    );
}
