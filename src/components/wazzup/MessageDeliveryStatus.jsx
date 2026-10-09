import React from 'react';
import { AlertCircle, Check, CheckCheck } from 'lucide-react';

const STATUSES = {
    // Своё сообщение ещё у iCORE (очередь, sendQueue.js) или уже принято Wazzup —
    // одна серая галочка, как «отправлено» (решение владельца 09.10.2026: часы
    // вместо неё только отвлекали). Различает их подсказка при наведении.
    queued: { Icon: Check, label: 'Отправляется', color: 'text-slate-500' },
    pending: { Icon: Check, label: 'Принято Wazzup', color: 'text-slate-500' },
    sent: { Icon: Check, label: 'Отправлено', color: 'text-slate-500' },
    delivered: { Icon: CheckCheck, label: 'Доставлено', color: 'text-slate-500' },
    read: { Icon: CheckCheck, label: 'Прочитано', color: 'text-sky-500' },
    error: { Icon: AlertCircle, label: 'Ошибка отправки', color: 'text-rose-600' },
    failed: { Icon: AlertCircle, label: 'Не отправлено', color: 'text-rose-600' },
    unknown: { Icon: AlertCircle, label: 'Отправка не подтверждена', color: 'text-amber-600' },
};

export default function MessageDeliveryStatus({ status }) {
    const item = STATUSES[status];
    if (!item) return null;
    const { Icon, label, color } = item;
    // data-status — тема чатов красит «прочитано» под цвет своего пузыря (chatThemes.css).
    return <span role="img" aria-label={label} title={label} data-status={status} className={`inline-flex items-center ${color}`}>
        <Icon size={16} strokeWidth={2} aria-hidden="true" />
    </span>;
}
