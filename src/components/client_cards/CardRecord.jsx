/* Запись разговора карточки (владелец, 13.11.2026: карточка, которую оператор не успел
 * заполнить, остаётся в черновиках «вместе с аудио» — он слушает разговор и дозаполняет
 * причину).
 *
 * Запись лежит у АТС и запрашивается по нажатию: ссылка на неё подписанная и живёт недолго,
 * а поиск звонка у АТС — не бесплатный запрос. Ссылка устарела или запись не открылась —
 * кнопка возвращается, следующее нажатие берёт новую ссылку.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Headphones, Loader2 } from 'lucide-react';

import { errorText } from './cardRules';

const EMPTY = { url: '', loading: false, note: '' };

export default function CardRecord({ api, cardId }) {
    const [record, setRecord] = useState(EMPTY);
    // Ответ по прежней карточке, пришедший после смены карточки, на экран не идёт.
    const current = useRef(cardId);
    useEffect(() => {
        current.current = cardId;
        setRecord(EMPTY);
    }, [cardId]);

    const load = async () => {
        if (record.loading) return;
        setRecord({ url: '', loading: true, note: '' });
        try {
            const { data } = await api.cardRecord(cardId);
            if (current.current === cardId) setRecord({ url: data.url, loading: false, note: '' });
        } catch (error) {
            if (current.current === cardId) {
                setRecord({ url: '', loading: false, note: errorText(error, 'Запись не открылась') });
            }
        }
    };

    if (record.url) {
        return (
            // autoPlay: запись запросили нажатием — второе нажатие «▶» было бы лишним.
            <audio controls autoPlay preload="auto" src={record.url} className="h-9 w-full" aria-label="Запись разговора"
                   onError={() => setRecord({ url: '', loading: false, note: 'Запись не открылась — нажмите ещё раз' })} />
        );
    }
    return (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <button type="button" onClick={load} disabled={record.loading}
                    className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12.5px] font-medium text-slate-700 transition hover:bg-slate-200 active:scale-[0.98] disabled:opacity-60">
                {record.loading ? <Loader2 size={14} className="animate-spin" /> : <Headphones size={14} />}
                {record.loading ? 'Ищем запись…' : 'Прослушать разговор'}
            </button>
            {record.note && <span className="text-[12px] text-amber-700" role="status">{record.note}</span>}
        </div>
    );
}
