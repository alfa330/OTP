import React, { useMemo, useState } from 'react';
import CustomSelect from '../ui/CustomSelect';
import { IosHint, IosToggle } from '../ui/ios';
import {
    HH_LINK_ICORE, INTEGRATION_STATE_LABELS, errorOf, hhIcoreOptions, hhIcoreValue, hhSettingsBody, hhVacancyMeta,
    shortTime,
} from './hiringMeta';
import { Pill } from './hiringUi';

/*
 * Отклики hh.kz (владелец 23.11.2026: «по данному сайту нужно автоматизировать передачу откликов в icore в воронку
 * найма»). Мост на шлюзе под входом в кабинет работодателя через случайную паузу (днём 2–5 минут, ночью 45 минут –
 * 2 часа) смотрит счётчики откликов и приносит новые отклики в «Отклики» пула с источником HeadHunter. Строка
 * «Настроек → Интеграции»: состояние моста, чей вход, с какого момента отклики переносятся, и какие вакансии hh
 * отслеживать (владелец 23.11.2026: «Добавь в настройки возможность выбрать какие вакансии отслеживать») — тумблер у
 * каждой, у отслеживаемой — с какой вакансией iCORE заводить карточки. Правка сохраняется сразу, как тумблеры соседних
 * справочников; новые вакансии hh отслеживаются сами.
 */

const HOW_IT_WORKS = 'Мост на шлюзе смотрит отклики вакансий в кабинете работодателя hh.kz под входом администратора, '
    + 'как рекрутер на сайте: днём — раз в 2–5 минут, ночью — раз в 45 минут – 2 часа, каждый раз через случайный '
    + 'промежуток. Новые он приносит в «Отклики»: ФИО, телефон, город, вакансию и ссылку на резюме. Телефон, по '
    + 'которому уже есть открытая карточка, — повторный отклик в ней. В hh мост ничего не меняет: '
    + 'отклики там остаются как были. Какие вакансии отслеживать — ниже.';

const VACANCIES_HINT = 'Включена — мост приносит новые отклики этой вакансии в «Отклики». Выключена — мост её не '
    + 'читает вовсе. Включили позже — отклики с минуты включения, прежние в воронку не идут. Новые вакансии hh '
    + 'отслеживаются сами. Включённую можно связать с вакансией iCORE — карточки будут заводиться с ней; не связана — в '
    + 'карточке будет название вакансии с hh.';

export const HhIntegration = ({ row, api, toast, vacancies, onItems }) => {
    const view = row.hh || {};
    const options = useMemo(() => hhIcoreOptions(vacancies), [vacancies]);
    const linkedOptions = useMemo(() => hhIcoreOptions(vacancies, true), [vacancies]);
    // Правка видна сразу, пока сервер её сохраняет; ответ сервера её заменяет, отказ — возвращает как было.
    const [pending, setPending] = useState({});

    const save = async (vacancy, change) => {
        setPending((current) => ({ ...current, [vacancy.id]: change }));
        try {
            const { data } = await api.put('/integrations/hh/settings', hhSettingsBody(row.settings?.vacancies, vacancy.id, change));
            onItems(data.items);
        } catch (error) {
            toast(errorOf(error, 'Не удалось сохранить'), 'error');
        } finally {
            setPending((current) => {
                const next = { ...current };
                delete next[vacancy.id];
                return next;
            });
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
    const list = (view.vacancies || []).map((v) => (pending[v.id] ? { ...v, ...pending[v.id] } : v));

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
                {list.length > 0 && (
                    <div className="mt-2.5">
                        <div className="flex items-center gap-1.5 text-[12px] font-medium text-slate-500">
                            Какие вакансии отслеживать
                            <IosHint text={VACANCIES_HINT} label="Какие вакансии отслеживать" />
                        </div>
                        <ul className="mt-1 divide-y divide-slate-100 rounded-xl border border-slate-200/80">
                            {list.map((v) => {
                                const linked = hhIcoreValue(v.vacancy_id, options);
                                return (
                                    // На телефоне: название и тумблер — строкой, выбор вакансии iCORE — под ними; на компьютере — в ряд.
                                    <li key={v.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2">
                                        <div className={`min-w-0 flex-1 transition-opacity ${v.on ? '' : 'opacity-50'}`}>
                                            <div className="truncate text-[13px] text-slate-800" title={v.name}>{v.name}</div>
                                            <div className="text-[11.5px] tabular-nums text-slate-500">{hhVacancyMeta(v)}</div>
                                        </div>
                                        {v.on && (
                                            <div className="order-last w-full sm:order-none sm:w-56">
                                                <CustomSelect variant="ios" value={linked} options={linked ? linkedOptions : options}
                                                              placeholder={HH_LINK_ICORE} ariaLabel={`Вакансия iCORE для «${v.name}»`}
                                                              disabled={Boolean(pending[v.id])}
                                                              onChange={(value) => save(v, { on: true, vacancy_id: value ? Number(value) : null })} />
                                            </div>
                                        )}
                                        <IosToggle checked={v.on} disabled={Boolean(pending[v.id])} ariaLabel={`Отслеживать «${v.name}»`}
                                                   onChange={(on) => save(v, { on, vacancy_id: v.vacancy_id || null })} />
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                )}
            </div>
        </li>
    );
};

export default HhIntegration;
