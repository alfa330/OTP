import React, { useEffect, useMemo, useState } from 'react';
import CustomSelect from '../ui/CustomSelect';
import { IosHint } from '../ui/ios';
import {
    INTEGRATION_STATE_LABELS, errorOf, hhChoice, hhChoiceOptions, hhSettingsBody, shortTime,
} from './hiringMeta';
import { Pill, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Отклики hh.kz (владелец 23.11.2026: «по данному сайту нужно автоматизировать передачу откликов в icore в воронку
 * найма»). Мост на шлюзе под входом в кабинет работодателя через случайную паузу (днём 2–5 минут, ночью 45 минут –
 * 2 часа) смотрит счётчики откликов и приносит новые отклики в «Отклики» пула с источником HeadHunter. Строка
 * «Настроек → Интеграции»: состояние моста, чей вход, с какого момента отклики переносятся, и вакансии hh — переносить
 * ли и с какой вакансией iCORE (новые вакансии hh идут сами).
 */

const HOW_IT_WORKS = 'Мост на шлюзе смотрит отклики вакансий в кабинете работодателя hh.kz под входом администратора, '
    + 'как рекрутер на сайте: днём — раз в 2–5 минут, ночью — раз в 45 минут – 2 часа, каждый раз через случайный '
    + 'промежуток. Новые он приносит в «Отклики»: ФИО, телефон, город, вакансию и ссылку на резюме. Телефон, по '
    + 'которому уже есть открытая карточка, — повторный отклик в ней. В hh мост ничего не меняет: '
    + 'отклики там остаются как были. Новые вакансии hh переносятся сами — выключить или поставить вакансию iCORE можно ниже.';

export const HhIntegration = ({ row, api, toast, vacancies, onItems }) => {
    const view = row.hh || {};
    const saved = useMemo(() => Object.fromEntries((view.vacancies || []).map((v) => [v.id, hhChoice(v)])), [view.vacancies]);
    const [choices, setChoices] = useState(saved);
    const [saving, setSaving] = useState(false);
    useEffect(() => { setChoices(saved); }, [saved]);
    const changed = Object.keys(choices).some((id) => choices[id] !== saved[id]);
    const options = useMemo(() => hhChoiceOptions(vacancies), [vacancies]);

    const save = async () => {
        setSaving(true);
        try {
            const { data } = await api.put('/integrations/hh/settings', hhSettingsBody(row.settings?.vacancies, choices));
            onItems(data.items);
            toast('Вакансии hh сохранены', 'success');
        } catch (error) {
            toast(errorOf(error, 'Не удалось сохранить'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const account = view.account || {};
    const who = [account.manager, account.employer].filter(Boolean).join(', ');
    const status = [
        who ? `вход: ${who}` : null,
        view.seen_at ? `на связи ${shortTime(view.seen_at)}` : 'мост ещё не выходил на связь',
        view.since ? `переносит отклики с ${shortTime(view.since)}` : null,
        `получено всего ${row.received_total}`,
    ].filter(Boolean).join(' · ');

    return (
        <li className="flex items-start gap-3 px-4 py-2.5">
            <div className="min-w-0 grow basis-0">
                <div className="flex items-center gap-2 text-[13.5px] font-medium text-slate-900">
                    HeadHunter
                    <Pill tone={row.state === 'ok' ? 'green' : row.state === 'error' ? 'red' : 'slate'}>
                        {INTEGRATION_STATE_LABELS[row.state] || row.state}
                    </Pill>
                    <IosHint text={HOW_IT_WORKS} label="Как работает" />
                </div>
                <div className="mt-0.5 text-[12px] tabular-nums text-slate-500">{status}</div>
                {row.last_error && <div className="mt-0.5 text-[12px] text-rose-600">{row.last_error}</div>}
                {(view.vacancies || []).length > 0 && (
                    <ul className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200/80">
                        {view.vacancies.map((v) => (
                            <li key={v.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5">
                                <div className="min-w-0 grow basis-48">
                                    <div className="truncate text-[13px] text-slate-800" title={v.name}>{v.name}</div>
                                    <div className="text-[11.5px] tabular-nums text-slate-500">
                                        {[v.area, v.responses != null ? `откликов ${v.responses}` : null].filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                                <div className="w-56 shrink-0">
                                    <CustomSelect variant="ios" value={choices[v.id] || 'on'} options={options}
                                                  ariaLabel={`Вакансия hh «${v.name}»`}
                                                  onChange={(value) => setChoices((current) => ({ ...current, [v.id]: value }))} />
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
                {changed && (
                    <div className="mt-2">
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={save} disabled={saving}>Сохранить</button>
                    </div>
                )}
            </div>
        </li>
    );
};

export default HhIntegration;
