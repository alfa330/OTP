import React, { useEffect, useState } from 'react';
import { IosModal, iosBtnSecondary } from '../ui/ios';
import { STAGE_LABELS, SUBSTAGE_LABELS, SUBSTAGE_TONES, errorOf } from './hiringMeta';
import { Empty, Loading, Pill } from './hiringUi';
import { cardStatus } from './dayReportMeta';

const toneOf = (card) => {
    if (card.stage === 'hired') return 'green';
    if (['refused', 'dropped'].includes(card.stage)) return 'red';
    return (card.stage === 'in_work' && SUBSTAGE_TONES[card.substage]) || 'slate';
};

/*
 * Список карточек за цифрой отчёта («клик по любой цифре открывает список», ТЗ 8.1): дашборд спрашивает
 * /reports/cards, отчёт за день — /reports/day/cards. Строка открывает карточку кандидата.
 */
const ReportCardsModal = ({ api, toast, request, onClose, onOpenCard, path = '/reports/cards', params }) => {
    const [rows, setRows] = useState(null);
    useEffect(() => {
        if (!request) return undefined;
        let alive = true;
        setRows(null);
        api.get(path, { ...params, key: request.key })
            .then(({ data }) => { if (alive) setRows(data.items); })
            .catch((error) => toast(errorOf(error, 'Список не открылся'), 'error'));
        return () => { alive = false; };
    }, [api, request, path, params, toast]);
    return (
        <IosModal open={Boolean(request)} onClose={onClose} title={request?.label || ''} subtitle={rows ? `${rows.length} карточек` : ''}
                  footer={<div className="flex justify-end"><button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button></div>}>
            {!rows ? <Loading text="Собираем список…" /> : rows.length === 0 ? <Empty>Карточек нет</Empty> : (
                <ul className="divide-y divide-slate-100">
                    {rows.map((card) => (
                        <li key={card.id}>
                            <button type="button" onClick={() => onOpenCard(card.id)} className="flex w-full items-center gap-3 py-2 text-left hover:bg-slate-50">
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] font-medium text-slate-900">{card.full_name || 'Без имени'}{card.candidate_no ? <span className="ml-1.5 text-[12px] font-normal text-slate-400">№{card.candidate_no}</span> : null}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[card.recruiter_name, card.source_name, card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ')}</div>
                                </div>
                                <Pill tone={toneOf(card)}>{cardStatus(card, STAGE_LABELS, SUBSTAGE_LABELS)}</Pill>
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </IosModal>
    );
};

export default ReportCardsModal;
