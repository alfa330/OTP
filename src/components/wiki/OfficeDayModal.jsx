import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosGroupLabel, iosInput, IosModal } from '../ui/ios';
import { formatDay, formatDayShort, officeDayStatus } from './officeDayStatus';

/* Отметка «офис открыт / закрыт» и закрытие на срок.
 *
 * Живёт отдельно от формы офиса намеренно: график правят раз в полгода, а
 * «сегодня закрыто, прорвало трубу» отмечают на ходу. Спрятать это в форму
 * значило бы заставить дежурного открывать шесть полей ради одного.
 *
 * Срок появился по задаче #236. До него закрытие держалось ровно один день, и
 * дежурные писали период словами в причину («с 17.08 по 03.09 по тех.причинам»)
 * — а назавтра офис всё равно «открывался» сам по графику, и оператор видел
 * открытым офис, который стоял на ремонте. Поэтому срок здесь не украшение
 * надписи, а то, что удерживает состояние.
 *
 * Причина не обязательна, но именно она отвечает оператору на вопрос водителя
 * «а почему закрыто», поэтому поле на виду, а не под чипом.
 */

const STATES = [
    { key: 'open', label: 'Открыт' },
    { key: 'closed', label: 'Закрыт' },
];

/* Три ответа на «насколько закрыт». «Только этот день» стоит первым, потому что
 * это прежнее поведение и самый частый случай — «сегодня не работаем». */
const TERMS = [
    { key: 'day', label: 'Только этот день' },
    { key: 'until', label: 'До даты' },
    { key: 'open', label: 'Срок не известен' },
];

const Segmented = ({ value, options, onChange }) => (
    <div className="mt-1.5 flex rounded-xl bg-slate-100 p-1">
        {options.map((item) => (
            <button
                key={item.key}
                type="button"
                onClick={() => onChange(item.key)}
                className={`flex-1 rounded-[9px] px-3 py-1.5 text-[13px] font-semibold transition-all ${
                    value === item.key
                        ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                        : 'text-slate-500 hover:text-slate-700'
                }`}
            >
                {item.label}
            </button>
        ))}
    </div>
);

/** Следующий календарный день — минимум для даты открытия: закрытие, которое
 *  кончается в день своего начала, это не закрытие. */
const nextDay = (dayISO) => {
    const [y, m, d] = String(dayISO).split('-').map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    date.setUTCDate(date.getUTCDate() + 1);
    return date.toISOString().slice(0, 10);
};

export default function OfficeDayModal({ office, dayISO, busy, onSubmit, onClear, onClose }) {
    const current = officeDayStatus(office, dayISO);
    const [state, setState] = useState(current.state === 'closed' ? 'closed' : 'open');

    const closure = office?.closed_from ? office : null;
    const [term, setTerm] = useState(
        // eslint-disable-next-line no-nested-ternary
        !closure ? 'day' : (office.closed_until ? 'until' : 'open'),
    );
    const [until, setUntil] = useState(office?.closed_until || nextDay(dayISO));
    const [note, setNote] = useState(current.note || '');

    // Отметка человека уже есть — значит её можно снять, вернув день графику.
    // Закрытие на срок снимается той же кнопкой: для дежурного это одно
    // действие «считать по графику», а не два разных.
    const hasRecord = office?.day?.source === 'manual' || !!closure;

    const closedTerm = state === 'closed' ? term : 'day';
    const invalid = closedTerm === 'until' && (!until || until <= dayISO);

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
                        disabled={busy || invalid}
                        onClick={() => onSubmit(state, note.trim(), {
                            kind: closedTerm,
                            until: closedTerm === 'until' ? until : null,
                        })}
                    >
                        {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                    </button>
                </>
            )}
        >
            <div className="space-y-3">
                <div>
                    <div className={iosGroupLabel}>Офис в этот день</div>
                    <Segmented value={state} options={STATES} onChange={setState} />
                </div>

                {state === 'closed' && (
                    <div>
                        <div className={iosGroupLabel}>Насколько закрыт</div>
                        <Segmented value={term} options={TERMS} onChange={setTerm} />
                        {term === 'until' && (
                            <>
                                {/* Спрашиваем ДЕНЬ ОТКРЫТИЯ, а не последний
                                    закрытый: надпись оператору — «закрыт до
                                    29.08», и если бы поле означало «по 29.08»,
                                    интерфейс и таблица расходились бы на сутки.
                                    Подпись снизу проговаривает обе границы, чтобы
                                    угадывать не пришлось. */}
                                <input
                                    type="date"
                                    className={`${iosInput} mt-2`}
                                    value={until}
                                    min={nextDay(dayISO)}
                                    onChange={(e) => setUntil(e.target.value)}
                                    aria-label="День, когда офис откроется"
                                />
                                <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-500">
                                    {until && until > dayISO
                                        ? `Закрыт по ${formatDayShort(prevDay(until), dayISO)} включительно, откроется ${formatDayShort(until, dayISO)}.`
                                        : 'Выберите день, когда офис откроется.'}
                                </p>
                            </>
                        )}
                    </div>
                )}

                <div>
                    <div className={iosGroupLabel}>Причина — её увидит оператор</div>
                    <input
                        className={`${iosInput} mt-1.5`}
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        placeholder="Например: ремонт помещения"
                        maxLength={500}
                    />
                </div>

                <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                    {closedTerm === 'day'
                        ? `Отметка относится только к ${formatDay(dayISO)} и перебивает график.
                           Остальные дни считаются как раньше.`
                        : 'Все дни закрытия офис показывается закрытым, график на них не действует. '
                          + 'Отметка на отдельный день сильнее срока — ей можно открыть офис на один день.'}
                </p>
            </div>
        </IosModal>
    );
}

/** Предыдущий календарный день — только для подписи «закрыт по …». */
function prevDay(dayISO) {
    const [y, m, d] = String(dayISO).split('-').map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    date.setUTCDate(date.getUTCDate() - 1);
    return date.toISOString().slice(0, 10);
}
