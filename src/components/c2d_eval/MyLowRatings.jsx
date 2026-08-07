import React, { useEffect, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import { iosCard } from '../ui/ios';

/* «Проверки низких оценок» в разделе «Мои оценки» чат-менеджера.
 *
 * Оценку клиента ниже 4 проверяет ОКК (см. «Учёт часов → Низкие оценки»):
 * необоснованную снимают, и она перестаёт влиять на показатель оператора.
 * Здесь оператор видит решения ТОЛЬКО по своим оценкам и только после
 * QR-подтверждения доступа — тем же ключом, что открывает телефоны и записи.
 *
 * Цвета согласованы с проверяющей стороной и намеренно «перевёрнуты»:
 * обоснованно (оценка остаётся) — красный, необоснованно (снята) — зелёный. */

const VERDICTS = {
    invalid: { label: 'Необоснованно · снята', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
    valid: { label: 'Обоснованно', pill: 'bg-rose-50 text-rose-700 ring-rose-200' },
    pending: { label: 'На проверке', pill: 'bg-slate-100 text-slate-500 ring-slate-200' },
};

const verdictOf = (row) => VERDICTS[row?.final_status] || VERDICTS.pending;

// За месяц у чат-менеджера набирается под сотню низких оценок — весь список
// сразу превратил бы страницу оценок в простыню. Показываем свежие, остальные — по кнопке.
const VISIBLE_LIMIT = 8;

const formatDateTime = (value) => {
    const text = String(value || '').trim();
    if (!text) return '—';
    const [datePart, timePart = ''] = text.replace('T', ' ').split(' ');
    const [, mm, dd] = datePart.split('-');
    if (!mm || !dd) return text.slice(0, 16);
    return `${dd}.${mm}${timePart ? ` ${timePart.slice(0, 5)}` : ''}`;
};

export default function MyLowRatings({ apiBaseUrl, withAccessTokenHeader, userId, month, granted }) {
    const [data, setData] = useState(null);
    const [expandedId, setExpandedId] = useState('');
    const [showAll, setShowAll] = useState(false);

    useEffect(() => {
        if (!userId || !month) return undefined;
        let cancelled = false;
        const headers = withAccessTokenHeader ? withAccessTokenHeader({ 'X-User-Id': userId }) : {};
        axios.get(`${apiBaseUrl}/api/my/low_rating_reviews`, { params: { month }, headers })
            .then((response) => {
                if (cancelled) return;
                setData(response.data || null);
                setExpandedId('');
                setShowAll(false);
            })
            .catch((e) => {
                if (cancelled) return;
                // Блок дополнительный: по ошибке молча исчезаем, а не пугаем
                // плашкой всех операторов — у большинства блока и так нет.
                console.error('my low ratings:', e?.response?.data?.error || e?.message);
                setData(null);
            });
        return () => { cancelled = true; };
        // granted в зависимостях: после скана QR данные нужно перезапросить.
    }, [apiBaseUrl, withAccessTokenHeader, userId, month, granted]);

    // Оператору без чат-менеджерской модели блок не показываем совсем: низких
    // оценок клиента у него не бывает, пустая карточка была бы шумом.
    if (!data || !data.is_chat_manager) return null;

    if (!data.sensitive_access?.granted) {
        return (
            <div className={`${iosCard} mb-6 flex items-center gap-2 px-4 py-3 text-[13px] text-slate-500`}>
                <FaIcon className="fas fa-shield-alt text-slate-400" />
                Проверки низких оценок откроются после QR-подтверждения доступа
            </div>
        );
    }

    const rows = Array.isArray(data.rows) ? data.rows : [];
    const summary = data.summary || {};
    const visibleRows = showAll ? rows : rows.slice(0, VISIBLE_LIMIT);

    return (
        <section className={`${iosCard} mb-6 overflow-hidden`}>
            <div className="border-b border-slate-100 px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                        <FaIcon className="fas fa-star text-amber-500" />
                        Проверки низких оценок
                    </h3>
                    {rows.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 text-[11.5px] font-medium">
                            {Number(summary.invalid) > 0 && (
                                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700 ring-1 ring-emerald-200">Снято {summary.invalid}</span>
                            )}
                            {Number(summary.valid) > 0 && (
                                <span className="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700 ring-1 ring-rose-200">Обоснованно {summary.valid}</span>
                            )}
                            {Number(summary.pending) > 0 && (
                                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-500 ring-1 ring-slate-200">На проверке {summary.pending}</span>
                            )}
                        </div>
                    )}
                </div>
                <p className="mt-1 text-[12px] text-slate-500">
                    Оценку клиента ниже 4 проверяет ОКК. Признанную необоснованной снимают — она не влияет на ваш показатель.
                </p>
            </div>

            {rows.length === 0 ? (
                <div className="px-4 py-4 text-[13px] text-slate-500">За этот месяц низких оценок нет.</div>
            ) : (
                <ul className="divide-y divide-slate-100">
                    {visibleRows.map((row) => {
                        const verdict = verdictOf(row);
                        const isOpen = String(expandedId) === String(row.id);
                        const decisions = row.decisions || [];
                        const hasDetails = Boolean(row.client_comment) || decisions.length > 0 || Boolean(row.final_comment);
                        return (
                            <li key={row.id}>
                                <button
                                    type="button"
                                    onClick={() => setExpandedId(isOpen ? '' : String(row.id))}
                                    disabled={!hasDetails}
                                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition ${hasDetails ? 'hover:bg-slate-50' : 'cursor-default'}`}
                                >
                                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-amber-50 text-[13px] font-semibold text-amber-700 ring-1 ring-amber-100">
                                        {row.score}
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13.5px] text-slate-900">
                                            {`${formatDateTime(row.rated_at || row.day)}${row.taxi_park ? ` · ${row.taxi_park}` : ''}`}
                                        </span>
                                        {row.client_comment && !isOpen && (
                                            <span className="block truncate text-[12px] text-slate-500">{row.client_comment}</span>
                                        )}
                                    </span>
                                    <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11.5px] font-medium ring-1 ${verdict.pill}`}>
                                        {verdict.label}
                                    </span>
                                    {hasDetails && (
                                        <FaIcon className={`fas ${isOpen ? 'fa-chevron-down' : 'fa-chevron-right'} text-slate-300`} />
                                    )}
                                </button>

                                {isOpen && (
                                    <div className="space-y-2.5 bg-slate-50/60 px-4 pb-4 pt-1">
                                        {row.client_comment && (
                                            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/70">
                                                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Комментарий клиента</div>
                                                <div className="mt-1 whitespace-pre-wrap break-words text-[13px] text-slate-700">{row.client_comment}</div>
                                            </div>
                                        )}
                                        {decisions.map((decision, index) => (
                                            <div key={`${row.id}-decision-${index}`} className="rounded-xl bg-white p-3 ring-1 ring-slate-200/70">
                                                <div className="flex flex-wrap items-center justify-between gap-2">
                                                    <span className="text-[12.5px] font-semibold text-slate-700">{decision.reviewer_name}</span>
                                                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${(VERDICTS[decision.status] || VERDICTS.pending).pill}`}>
                                                        {decision.status === 'invalid' ? 'Необоснованно' : 'Обоснованно'}
                                                    </span>
                                                </div>
                                                {decision.comment && (
                                                    <div className="mt-1 whitespace-pre-wrap break-words text-[13px] text-slate-600">{decision.comment}</div>
                                                )}
                                            </div>
                                        ))}
                                        {row.final_comment && (
                                            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/70">
                                                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Решение руководителя</div>
                                                <div className="mt-1 whitespace-pre-wrap break-words text-[13px] text-slate-700">{row.final_comment}</div>
                                            </div>
                                        )}
                                        {row.phone_number && (
                                            <div className="px-1 text-[11.5px] text-slate-400">Клиент: {row.phone_number}</div>
                                        )}
                                    </div>
                                )}
                            </li>
                        );
                    })}
                    {rows.length > VISIBLE_LIMIT && (
                        <li>
                            <button
                                type="button"
                                onClick={() => setShowAll((prev) => !prev)}
                                className="w-full px-4 py-2.5 text-[13px] font-medium text-blue-600 transition hover:bg-slate-50"
                            >
                                {showAll ? 'Свернуть' : `Показать все (${rows.length})`}
                            </button>
                        </li>
                    )}
                </ul>
            )}
        </section>
    );
}
