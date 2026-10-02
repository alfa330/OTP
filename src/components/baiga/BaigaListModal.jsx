import React, { useEffect, useMemo, useState } from 'react';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { formatInt, plural, splitTokens } from './baigaMeta';

/*
 * «Список ID/ВУ» (постановка #356, п. 5): вставить столбец из Excel или список
 * из мессенджера и найти сразу всех. Принимает ID водителя, ссылку на водителя
 * во Флите и номер ВУ — в том числе набранный русскими буквами. Кто из списка
 * не нашёлся, экран называет поимённо (это считает сервер).
 */
const BaigaListModal = ({ open, value, maxTokens = 2000, onApply, onClose }) => {
    const [draft, setDraft] = useState(value || '');

    useEffect(() => { if (open) setDraft(value || ''); }, [open, value]);

    const count = useMemo(() => splitTokens(draft).length, [draft]);
    const over = count > maxTokens;

    const footer = (
        <>
            {value && (
                <button type="button" className={`${iosBtnGhost} mr-auto`} onClick={() => { onApply(''); onClose(); }}>
                    Убрать список
                </button>
            )}
            <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
            <button type="button" className={iosBtnPrimary} disabled={!count}
                    onClick={() => { onApply(draft); onClose(); }}>
                Найти
            </button>
        </>
    );

    return (
        <IosModal open={open} onClose={onClose} title="Поиск по списку"
                  subtitle="ID водителей, ссылки из Флита или номера ВУ" footer={footer}>
            <textarea
                className="thin-scroll h-56 w-full resize-y rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 font-mono text-[13px] text-slate-900 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={'По одному в строке, через запятую или пробел\nНапример, столбец из Excel'}
                autoFocus
            />
            <div className={`mt-2 text-[12.5px] tabular-nums ${over ? 'text-amber-700' : 'text-slate-500'}`}>
                {count
                    ? `${formatInt(count)} ${plural(count, 'значение', 'значения', 'значений')}`
                    : 'Список пуст'}
                {over && ` — искать будем по первым ${formatInt(maxTokens)}`}
            </div>
        </IosModal>
    );
};

export default BaigaListModal;
