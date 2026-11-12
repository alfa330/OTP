/* Запрос в другой отдел прямо из карточки звонка (задача #335, п. 6–7).
 *
 * Оператор выбирает отдел, пишет, что нужно, и при необходимости прикладывает фото, видео
 * или документ; ссылку вставляет в текст. Кто звонил, по какому аккаунту и о чём — в
 * сообщение группы портал подставляет сам из карточки: оператору не нужно это повторять,
 * и об этом сказано одной строкой под заголовком, а не полями формы.
 *
 * Отдел без привязанной Telegram-группы показан, но выбрать его нельзя — с подписью почему.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Loader2, Paperclip, Send } from 'lucide-react';

import { IosModal, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, iosInput } from '../ui/ios';
import { accountTitle, errorText, topicLabel, uploadsProblem } from './cardRules';
import { PickedFiles } from './ui';

export default function RequestComposer({
    open, onClose, api, card, account, destinations, limits, toast, onCreated,
}) {
    const ready = (destinations || []).filter((item) => item.is_ready);
    const [destinationId, setDestinationId] = useState(null);
    const [text, setText] = useState('');
    const [files, setFiles] = useState([]);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState('');
    const fileRef = useRef(null);

    useEffect(() => {
        if (!open) return;
        setDestinationId(ready.length === 1 ? ready[0].id : null);
        setText('');
        setFiles([]);
        setProblem('');
        // Только на открытие: список адресатов за время работы окна не меняется.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    const pickFiles = (event) => {
        const next = [...files, ...Array.from(event.target.files || [])];
        event.target.value = '';
        const issue = uploadsProblem(next, limits);
        if (issue) { setProblem(issue); return; }
        setProblem('');
        setFiles(next);
    };

    const send = async () => {
        if (busy) return;
        if (!destinationId) { setProblem('Выберите отдел'); return; }
        if (text.trim().length < 3) { setProblem('Напишите, что нужно от отдела'); return; }
        setBusy(true);
        setProblem('');
        try {
            const { data } = await api.createRequest(
                { card_id: card.id, destination_id: destinationId, body: text.trim() }, files);
            if (data.delivery_error) {
                toast(`Запрос сохранён, но не дошёл до группы: ${data.delivery_error}. Отправьте его ещё раз во вкладке «Запросы».`, 'error');
            } else {
                toast(`Запрос №${data.request.id} отправлен: ${data.request.destination_title}`, 'success');
            }
            onCreated(data);
            onClose();
        } catch (error) {
            setProblem(errorText(error, 'Запрос не отправился'));
        } finally {
            setBusy(false);
        }
    };

    const topic = card?.topics?.[0];
    const summary = [
        account ? `${account.kind_label}: ${accountTitle(account)}` : null,
        card?.phone_text,
        topic ? topicLabel(topic) : null,
    ].filter(Boolean).join(' · ');

    return (
        <IosModal
            open={open}
            onClose={busy ? () => {} : onClose}
            title="Запрос в другой отдел"
            subtitle={summary ? `В сообщение попадут: ${summary}` : 'Аккаунт и причина попадут в сообщение из карточки'}
            maxWidth="max-w-md"
            footer={(
                <div className="flex justify-end gap-2">
                    <button type="button" onClick={onClose} disabled={busy} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" onClick={send} disabled={busy} className={iosBtnPrimary}>
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Отправить
                    </button>
                </div>
            )}
        >
            <div className="space-y-4">
                <div className="space-y-1.5">
                    <div className={iosGroupLabel}>Куда</div>
                    {destinations?.length ? (
                        <div className="flex flex-wrap gap-1.5">
                            {destinations.map((item) => {
                                const active = item.id === destinationId;
                                return (
                                    <button key={item.id} type="button" disabled={!item.is_ready}
                                            onClick={() => { setDestinationId(item.id); setProblem(''); }}
                                            title={item.is_ready ? (item.chat_title || '') : 'К отделу не привязана Telegram-группа'}
                                            aria-pressed={active}
                                            className={`rounded-xl px-3 py-2 text-[13px] font-medium transition active:scale-[0.98] ${
                                                active ? 'bg-slate-900 text-white'
                                                    : item.is_ready ? 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                                        : 'cursor-not-allowed bg-slate-50 text-slate-300'}`}>
                                        {item.title}
                                    </button>
                                );
                            })}
                        </div>
                    ) : (
                        <p className="text-[12.5px] text-slate-500">
                            Отделы для запросов ещё не заведены — их добавляет руководитель в разделе «Карточка клиента».
                        </p>
                    )}
                </div>
                <div className="space-y-1.5">
                    <div className={iosGroupLabel}>Что нужно</div>
                    <textarea value={text} onChange={(event) => { setText(event.target.value); setProblem(''); }}
                              rows={5} maxLength={limits?.request_body || 3500} autoFocus
                              placeholder="Опишите вопрос; ссылку можно вставить прямо в текст"
                              className={`${iosInput} resize-y`} />
                </div>
                <div className="space-y-2">
                    <input ref={fileRef} type="file" multiple className="hidden" onChange={pickFiles}
                           accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.zip" />
                    <button type="button" onClick={() => fileRef.current?.click()} className={iosBtnSecondary}>
                        <Paperclip size={14} /> Фото, видео или файл
                    </button>
                    <PickedFiles files={files} onRemove={(index) => setFiles(files.filter((_, i) => i !== index))} />
                </div>
                {problem && <div className="rounded-xl bg-rose-50 px-3 py-2 text-[12.5px] text-rose-700 ring-1 ring-rose-100">{problem}</div>}
            </div>
        </IosModal>
    );
}
