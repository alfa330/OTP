import React, { useMemo } from 'react';
import { Bell } from 'lucide-react';

import { parseMailingText } from './mailingText';

/*
 * Предпросмотр: как сообщение увидит водитель в приложении Pro.
 *
 * Зачем макет телефона, а не просто отрендеренный текст. Отправляют это тысяче
 * человек и отозвать можно пять минут — значит цена «я думал, ссылка будет
 * кликабельной» очень высокая. Живые рассылки в кабинете идут двумя языками
 * через черту и почти всегда содержат ссылку; увидеть, где именно порвётся
 * строка и что станет ссылкой, можно только на макете нужной ширины.
 *
 * Разметка рисуется React-узлами (см. mailingText.js), без вставки HTML: текст
 * пишет сотрудник в обычное поле, и превращать его в HTML по дороге незачем.
 */

const Inline = ({ nodes }) => (
    <>
        {nodes.map((node, index) => {
            if (typeof node === 'string') return <React.Fragment key={index}>{node}</React.Fragment>;
            if (node.type === 'bold') return <strong key={index} className="font-semibold">{node.text}</strong>;
            if (node.type === 'italic') return <em key={index}>{node.text}</em>;
            return (
                /* Ссылка в предпросмотре не ведёт никуда: это макет чужого
                   приложения, а не наш интерфейс. Показываем ровно то, что
                   станет кликабельным у водителя. */
                <span key={index} className="text-blue-600 underline decoration-blue-300 underline-offset-2">
                    {node.text}
                </span>
            );
        })}
    </>
);

const Blocks = ({ text }) => {
    const blocks = useMemo(() => parseMailingText(text), [text]);
    return (
        <>
            {blocks.map((block, index) => {
                if (block.type === 'gap') return <div key={index} className="h-2" />;
                if (block.type === 'list') {
                    return (
                        <ul key={index} className="my-0.5 space-y-0.5 pl-1">
                            {block.items.map((item, itemIndex) => (
                                <li key={itemIndex} className="flex gap-1.5">
                                    <span className="text-slate-400">•</span>
                                    <span className="min-w-0"><Inline nodes={item} /></span>
                                </li>
                            ))}
                        </ul>
                    );
                }
                return <div key={index}><Inline nodes={block.nodes} /></div>;
            })}
        </>
    );
};

export default function MailingPreview({ title, message }) {
    const empty = !String(title || '').trim() && !String(message || '').trim();
    return (
        <div className="rounded-2xl bg-slate-900 p-2.5 shadow-[0_8px_24px_rgba(15,23,42,0.18)]">
            <div className="rounded-[14px] bg-white px-3.5 py-3">
                <div className="mb-2 flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-wider text-slate-400">
                    <Bell size={11} />
                    <span>Уведомление в Pro</span>
                </div>
                {empty ? (
                    <div className="py-6 text-center text-[12.5px] text-slate-400">
                        Здесь появится то, что увидит водитель
                    </div>
                ) : (
                    <>
                        {String(title || '').trim() && (
                            <div className="mb-1.5 break-words text-[13.5px] font-semibold leading-snug text-slate-900">
                                {title}
                            </div>
                        )}
                        <div className="break-words text-[12.5px] leading-relaxed text-slate-700">
                            <Blocks text={message} />
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
