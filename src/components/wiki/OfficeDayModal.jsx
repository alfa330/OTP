import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosGroupLabel, iosInput, IosModal } from '../ui/ios';
import { formatDay, officeDayStatus } from './officeDayStatus';

/* Отметка «в этот день офис был открыт / закрыт».
 *
 * Живёт отдельно от формы офиса намеренно: график правят раз в полгода, а
 * «сегодня закрыто, прорвало трубу» отмечают на ходу. Спрятать это в форму
 * значило бы заставить дежурного открывать шесть полей ради одного.
 *
 * Причина не обязательна, но именно она отвечает оператору на вопрос водителя
 * «а почему закрыто», поэтому поле на виду, а не под чипом.
 */

const STATES = [
    { key: 'open', label: 'Открыт' },
    { key: 'closed', label: 'Закрыт' },
];

export default function OfficeDayModal({ office, dayISO, busy, onSubmit, onClear, onClose }) {
    const current = officeDayStatus(office, dayISO);
    const [state, setState] = useState(current.state === 'closed' ? 'closed' : 'open');
    const [note, setNote] = useState(current.note || '');

    // Отметка человека уже есть — значит её можно снять, вернув день графику.
    const hasRecord = office?.day?.source === 'manual';

    return (
        <IosModal
            open
            onClose={onClose}
            title="Статус на дату"
            subtitle={`${office?.name || ''} · ${formatDay(dayISO)}`}
            footer={(
                <>
                    {hasRecord && (
                        <button
                            type="button"
                            className={`${iosBtnSecondary} mr-auto`}
                            disabled={busy}
                            onClick={onClear}
                        >
                            Считать по графику
                        </button>
                    )}
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Отмена
                    </button>
                    <button
                        type="button"
                        className={iosBtnPrimary}
                        disabled={busy}
                        onClick={() => onSubmit(state, note.trim())}
                    >
                        {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                    </button>
                </>
            )}
        >
            <div className="space-y-3">
                <div>
                    <div className={iosGroupLabel}>Офис в этот день</div>
                    <div className="mt-1.5 flex rounded-xl bg-slate-100 p-1">
                        {STATES.map((item) => (
                            <button
                                key={item.key}
                                type="button"
                                onClick={() => setState(item.key)}
                                className={`flex-1 rounded-[9px] px-3 py-1.5 text-[13px] font-semibold transition-all ${
                                    state === item.key
                                        ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                            >
                                {item.label}
                            </button>
                        ))}
                    </div>
                </div>

                <div>
                    <div className={iosGroupLabel}>Причина — её увидит оператор</div>
                    <input
                        className={`${iosInput} mt-1.5`}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        placeholder="Например: ремонт, до 22 августа"
                        maxLength={500}
                    />
                </div>

                <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                    Отметка относится только к {formatDay(dayISO)} и перебивает график.
                    Остальные дни считаются как раньше.
                </p>
            </div>
        </IosModal>
    );
}
