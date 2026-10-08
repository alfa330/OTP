import React from 'react';
import { AlertCircle, Check, CheckCheck, Clock3 } from 'lucide-react';

const STATUSES = {
    pending: { Icon: Clock3, label: 'Принято Wazzup', color: 'text-slate-500' },
    sent: { Icon: Check, label: 'Отправлено', color: 'text-slate-500' },
    delivered: { Icon: CheckCheck, label: 'Доставлено', color: 'text-slate-500' },
    read: { Icon: CheckCheck, label: 'Прочитано', color: 'text-sky-500' },
    error: { Icon: AlertCircle, label: 'Ошибка отправки', color: 'text-rose-600' },
};

export default function MessageDeliveryStatus({ status }) {
    const item = STATUSES[status];
    if (!item) return null;
    const { Icon, label, color } = item;
    return <span role="img" aria-label={label} title={label} className={`inline-flex items-center ${color}`}>
        <Icon size={16} strokeWidth={2} aria-hidden="true" />
    </span>;
}
