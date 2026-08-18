import React, { useMemo, useState } from 'react';
import { Check, MapPin, Search, Wifi } from 'lucide-react';
import { iosInput, iosGroupLabel, IosBadge } from '../ui/ios';
import { Field } from './formField';

/* Форма таксопарка: о парке, контакты, условия и его офисы.
 *
 * Разбита на секции, а не в один столбик из восьми полей: у парка разнородные
 * данные, и «Название» рядом с «Комиссией» читается как один список, хотя
 * заполняют их в разное время и разные люди.
 *
 * Офисы живут здесь по решению владельца: офис отвечает за место (адрес, точка
 * на карте, часы), а кому он принадлежит — вопрос парка. Здесь же задаётся
 * телефон этого парка в этом офисе: в исходном справочнике по одному адресу у
 * разных парков были разные номера.
 */

const ParkOffices = ({ draft, setDraft, offices }) => {
    const [query, setQuery] = useState('');

    const linkFor = (officeId) => draft.offices.find((link) => link.office_id === officeId);

    const toggle = (officeId) => setDraft((prev) => ({
        ...prev,
        offices: prev.offices.find((link) => link.office_id === officeId)
            ? prev.offices.filter((link) => link.office_id !== officeId)
            : [...prev.offices, { office_id: officeId, phone: '' }],
    }));

    const setPhone = (officeId, phone) => setDraft((prev) => ({
        ...prev,
        offices: prev.offices.map((link) => (
            link.office_id === officeId ? { ...link, phone } : link)),
    }));

    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!needle) return offices;
        return offices.filter((office) => (
            `${office.name} ${office.city || ''}`.toLowerCase().includes(needle)));
    }, [offices, query]);

    if (offices.length === 0) {
        return (
            <p className="rounded-xl bg-slate-50 px-3 py-2.5 text-[12.5px] leading-relaxed text-slate-500">
                Справочник офисов пуст — заполните вкладку «Офисы», тогда их можно будет
                привязать к парку.
            </p>
        );
    }

    return (
        <div className="space-y-2">
            {/* Поиск появляется, когда список перестаёт помещаться на экран
                целиком: на пяти офисах он только мешал бы. */}
            {offices.length > 8 && (
                <div className="relative">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} h-9 pl-9`}
                        value={query}
                        placeholder="Найти офис по городу или названию"
                        onChange={(e) => setQuery(e.target.value)}
                    />
                </div>
            )}

            <div className="max-h-[280px] divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200">
                {visible.length === 0 && (
                    <div className="px-3 py-6 text-center text-[12.5px] text-slate-400">
                        Ничего не найдено
                    </div>
                )}
                {visible.map((office) => {
                    const link = linkFor(office.id);
                    return (
                        <div key={office.id} className={link ? 'bg-indigo-50/40' : ''}>
                            <button
                                type="button"
                                onClick={() => toggle(office.id)}
                                className="flex w-full items-center gap-2.5 px-3 py-2 text-left transition hover:bg-slate-50"
                            >
                                <span
                                    className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border transition ${
                                        link
                                            ? 'border-indigo-600 bg-indigo-600 text-white'
                                            : 'border-slate-300 bg-white text-transparent'
                                    }`}
                                    aria-hidden="true"
                                >
                                    <Check size={13} />
                                </span>
                                <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                                    {office.name}
                                </span>
                                {office.is_online && (
                                    <Wifi size={12} className="shrink-0 text-slate-400" aria-label="Только по телефону" />
                                )}
                                {office.city && (
                                    <span className="shrink-0 text-[11.5px] text-slate-400">{office.city}</span>
                                )}
                            </button>
                            {link && (
                                <div className="px-3 pb-2 pl-[42px]">
                                    <input
                                        className={`${iosInput} h-9`}
                                        value={link.phone || ''}
                                        placeholder={office.phone
                                            ? `как у офиса: ${office.phone}`
                                            : 'Телефон этого парка в этом офисе'}
                                        onChange={(e) => setPhone(office.id, e.target.value)}
                                    />
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default function ParkEditor({ draft, setDraft, offices }) {
    const set = (key) => (e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }));
    const selected = draft.offices.length;

    return (
        <div className="space-y-5">
            <section className="space-y-3">
                <div className={iosGroupLabel}>О парке</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field label="Название">
                        <input
                            className={iosInput}
                            autoFocus
                            value={draft.name}
                            placeholder="iTaxi"
                            onChange={set('name')}
                        />
                    </Field>
                    <Field label="Город">
                        <input
                            className={iosInput}
                            value={draft.city}
                            placeholder="Алматы"
                            onChange={set('city')}
                        />
                    </Field>
                </div>
                <Field label="Описание">
                    <textarea
                        className={`${iosInput} min-h-[72px] resize-y`}
                        value={draft.description}
                        placeholder="Чем этот парк отличается — коротко, для оператора"
                        onChange={set('description')}
                    />
                </Field>
            </section>

            <section className="space-y-3">
                <div className={iosGroupLabel}>Контакты</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field label="Телефон">
                        <input
                            className={`${iosInput} tabular-nums`}
                            inputMode="tel"
                            value={draft.phone}
                            placeholder="+7 700 000 00 00"
                            onChange={set('phone')}
                        />
                    </Field>
                    <Field label="Сайт">
                        <input
                            className={iosInput}
                            type="url"
                            value={draft.website}
                            placeholder="https://"
                            onChange={set('website')}
                        />
                    </Field>
                </div>
                <Field label="Адрес" hint="Юридический или головной. Адреса, куда ходят водители, — в офисах ниже.">
                    <input
                        className={iosInput}
                        value={draft.address}
                        placeholder="Алматы, улица Жамбыла, 172"
                        onChange={set('address')}
                    />
                </Field>
            </section>

            <section className="space-y-3">
                <div className={iosGroupLabel}>Условия</div>
                <Field label="Комиссия">
                    <div className="relative w-[140px]">
                        <input
                            className={`${iosInput} pr-8 tabular-nums`}
                            inputMode="decimal"
                            value={draft.commission}
                            placeholder="3.5"
                            onChange={(e) => setDraft((prev) => ({
                                ...prev, commission: e.target.value.replace(/[^\d.]/g, ''),
                            }))}
                        />
                        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[13px] text-slate-400">
                            %
                        </span>
                    </div>
                </Field>
            </section>

            <section className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                    <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
                        <MapPin size={12} /> Офисы парка
                    </div>
                    {offices.length > 0 && (
                        <IosBadge tone={selected ? 'blue' : 'slate'}>
                            выбрано {selected} из {offices.length}
                        </IosBadge>
                    )}
                </div>
                <ParkOffices draft={draft} setDraft={setDraft} offices={offices} />
                <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                    Адрес, карта и график работы живут в самом офисе — на вкладке «Офисы».
                    Здесь выбирается, какие из них принадлежат парку; телефон под галочкой
                    нужен, только если у парка в этом офисе свой номер.
                </p>
            </section>
        </div>
    );
}
