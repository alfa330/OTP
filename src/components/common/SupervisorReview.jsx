import React, { useEffect, useState } from 'react';
import { Check, CheckCircle2, Loader2, Send } from 'lucide-react';
import {
    iosBtnPrimary, iosBtnSecondary, iosGroupLabel, iosInput, IosModal,
} from '../ui/ios';
import InfoHint from './InfoHint';

/* Проверка супервайзером до группы.
 *
 * Одна и та же проверка у двух видов обращений: у жалобы на Яндекс (раздел
 * «Жалобы», владелец 30.09.2026) и у «Сотрудничества с Яндексом» (раздел
 * «Обращения», возврат задачи #297). В обоих случаях само в группу ничего не
 * уходит: супервайзер решает — «Решено» с итогом или «Отправить в группу».
 *
 * Поэтому панель и окно итога общие: два разных окна про одно и то же решение
 * человек прочитал бы как два разных действия. Различаются только слова
 * («жалоба» / «обращение») — они приходят снаружи. */

/* Окно «Решено». Итог обязателен: это то, что узнает оператор. */
export const ReviewResolveModal = ({ open, onClose, onResolve, busy, subtitle }) => {
    const [note, setNote] = useState('');
    const [touched, setTouched] = useState(false);
    useEffect(() => { if (!open) { setNote(''); setTouched(false); } }, [open]);
    const empty = !note.trim();
    return (
        <IosModal open={open} onClose={onClose} title="Решено" maxWidth="max-w-md"
                  subtitle={subtitle}
                  footer={(
                      <>
                          <button type="button" onClick={onClose} className={iosBtnSecondary} disabled={busy}>
                              Отмена
                          </button>
                          <button type="button" disabled={busy} className={iosBtnPrimary}
                                  onClick={() => { setTouched(true); if (!empty) onResolve(note.trim()); }}>
                              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                              Сохранить
                          </button>
                      </>
                  )}>
            <div>
                <div className={`${iosGroupLabel} mb-1.5`}>Итог</div>
                <textarea value={note} rows={4} onChange={(e) => setNote(e.target.value)}
                          placeholder="Что выяснили и что сделали"
                          className={`${iosInput} resize-y`} />
                {touched && empty && (
                    <div className="text-[11.5px] text-rose-600">Напишите итог</div>
                )}
            </div>
        </IosModal>
    );
};

/* Панель решения — внизу карточки, на месте поля ответа: это главное, что
 * проверяющий здесь может сделать.
 *
 * sendBlocked — почему отправить в группу сейчас нельзя (группа не выбрана);
 * null — можно. Пояснение «что значит каждая кнопка» — под «i»: оно нужно
 * один раз, а панель супервайзер видит на каждом таком обращении. */
export const ReviewPanel = ({
    hint, busy = false, sendBlocked = null, onResolve, onSend,
    className = 'shrink-0 border-t border-slate-100 px-4 py-3',
}) => (
    <div className={className}>
        <div className="mb-2 flex items-center gap-1.5">
            <span className="text-[12.5px] font-medium text-slate-600">Проверка супервайзером</span>
            <InfoHint side="left">{hint}</InfoHint>
        </div>
        <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={onResolve} disabled={busy} className={iosBtnSecondary}>
                <CheckCircle2 size={14} /> Решено
            </button>
            <button type="button" onClick={onSend} disabled={busy || Boolean(sendBlocked)}
                    title={sendBlocked || undefined} className={iosBtnPrimary}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                {/* На телефоне кнопки стоят в две колонки по ~160 px, и полная
                    подпись переносилась на вторую строку — кнопки выходили
                    разной высоты с соседней. Короткая говорит то же самое. */}
                <span className="sm:hidden">В группу</span>
                <span className="hidden sm:inline">Отправить в группу</span>
            </button>
        </div>
    </div>
);
