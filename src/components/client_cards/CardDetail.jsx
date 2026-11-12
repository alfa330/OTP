/* Обращение целиком — для чтения: из вкладки «Обращения» карточки и из журнала раздела.
 *
 * onContinue — «Продолжить это обращение» (только в карточке звонка): человек перезвонил
 * по тому же вопросу, и новая карточка связывается с прежней.
 */
import React, { useEffect, useState } from 'react';
import { ExternalLink, PhoneIncoming, PhoneOutgoing } from 'lucide-react';

import { IosBadge, IosModal, iosBtnPrimary, iosGroupLabel } from '../ui/ios';
import { REQUEST_TONES, accountTitle, errorText, fmtSeconds, fmtWhen, topicLabel } from './cardRules';
import { Spinner, StateBadge } from './ui';

const Row = ({ label, children }) => (
    <div className="flex gap-3 py-1.5 text-[13px]">
        <span className="w-28 shrink-0 text-slate-500">{label}</span>
        <span className="min-w-0 flex-1 break-words text-slate-900">{children}</span>
    </div>
);

export default function CardDetail({ api, cardId, onClose, onContinue = null, onOpenRequest = null }) {
    const [detail, setDetail] = useState(null);
    const [failed, setFailed] = useState('');

    useEffect(() => {
        if (!cardId) return undefined;
        let alive = true;
        setDetail(null);
        setFailed('');
        api.card(cardId)
            .then(({ data }) => { if (alive) setDetail(data); })
            .catch((error) => { if (alive) setFailed(errorText(error, 'Обращение не открылось')); });
        return () => { alive = false; };
    }, [api, cardId]);

    const card = detail?.card;
    const account = detail?.account;
    const Direction = card?.direction === 'out' ? PhoneOutgoing : PhoneIncoming;
    const spent = card?.completed_at && card?.ended_at
        ? Math.max(0, (new Date(card.completed_at) - new Date(card.ended_at)) / 1000) : null;

    return (
        <IosModal
            open={Boolean(cardId)}
            onClose={onClose}
            title={card ? `Обращение №${card.id}` : 'Обращение'}
            subtitle={card ? `${fmtWhen(card.created_at)} · ${card.operator_name || ''}` : ''}
            maxWidth="max-w-md"
            // Закрывает окно его собственный крестик; внизу — только действие, когда оно есть.
            footer={card && onContinue ? (
                <div className="flex justify-end">
                    <button type="button" onClick={() => onContinue(card)} className={iosBtnPrimary}>
                        Продолжить это обращение
                    </button>
                </div>
            ) : null}
        >
            {failed && <p className="py-6 text-center text-[13px] text-slate-500">{failed}</p>}
            {!failed && !card && <Spinner />}
            {card && (
                <div className="space-y-4">
                    <div className="flex flex-wrap items-center gap-2 text-[13px] text-slate-700">
                        <Direction size={15} className="text-slate-400" aria-hidden="true" />
                        <span>{card.direction_label}</span>
                        <span className="tabular-nums">{card.phone_text}</span>
                        <StateBadge card={card} />
                        {card.late && <IosBadge tone="amber">Сохранена позже срока</IosBadge>}
                    </div>
                    <div className="divide-y divide-slate-100">
                        <Row label="Аккаунт">
                            {account ? (
                                <a href={account.url} target="_blank" rel="noopener noreferrer"
                                   className="inline-flex items-center gap-1 text-blue-600 hover:underline">
                                    {account.kind_label} · {accountTitle(account)} <ExternalLink size={12} aria-hidden="true" />
                                </a>
                            ) : '—'}
                        </Row>
                        {card.city && <Row label="Город">{card.city}</Row>}
                        <Row label="Причина">
                            {card.topics.length
                                ? card.topics.map((topic) => <div key={`${topic.category_id}:${topic.subcategory_id ?? 0}`}>{topicLabel(topic)}</div>)
                                : '—'}
                        </Row>
                        {card.comment && <Row label="Комментарий"><span className="whitespace-pre-wrap">{card.comment}</span></Row>}
                        {detail.parent && (
                            <Row label="Продолжение">обращения №{detail.parent.id} от {fmtWhen(detail.parent.created_at)}</Row>
                        )}
                        {spent !== null && <Row label="На карточку">{fmtSeconds(spent)} после звонка</Row>}
                    </div>
                    {detail.requests.length > 0 && (
                        <div className="space-y-1.5">
                            <div className={iosGroupLabel}>Запросы в отделы</div>
                            <ul className="space-y-1">
                                {detail.requests.map((request) => (
                                    <li key={request.id}>
                                        <button type="button" disabled={!onOpenRequest} onClick={() => onOpenRequest?.(request.id)}
                                                className="flex w-full items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2 text-left text-[12.5px] enabled:hover:bg-slate-100">
                                            <span className="min-w-0 truncate text-slate-700">№{request.id} · {request.destination_title}</span>
                                            <IosBadge tone={REQUEST_TONES[request.status] || 'slate'}>{request.status_label}</IosBadge>
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}
        </IosModal>
    );
}
