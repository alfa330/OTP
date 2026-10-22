import React, { useEffect, useState } from 'react';
import { SearchField, Spinner, T } from './disciplineUi';

/*
 * Выбор сотрудника поиском — в форме нарушения и в «Поставить на особый
 * контроль»: поле с лупой и список под ним.
 *
 * Высота списка постоянная. Окно стоит посреди экрана, и каждая смена его
 * роста сдвигает его вверх-вниз на половину разницы: список приходил через
 * 0,2 с после открытия (окно 316 → 547 px за один кадр), а пока набирали ФИО,
 * 40 строк сменялись одной — и поле ввода уезжало из-под пальцев на ~120 px
 * (замер на стенде, жалоба владельца 22.10.2026 на «пролаги» формы). Теперь
 * меняется только содержимое списка, окно стоит.
 *
 * До первого ответа — заготовка строк, а не «Начните вводить ФИО»: список
 * сразу придёт. Пока ищется новая строка, прежние результаты стоят на месте.
 */

/* Поиск: запрос через 200 мс после последней буквы, опоздавший ответ
   отбрасывается. loaded — пришёл ли ответ с открытия окна (закрытие его
   сбрасывает, и следующее открытие начинается с заготовки). */
export const useOperatorSearch = (api, open, active, query) => {
    const [state, setState] = useState({ results: [], loaded: false, searching: false, failed: false });
    useEffect(() => {
        if (!open) setState({ results: [], loaded: false, searching: false, failed: false });
    }, [open]);
    useEffect(() => {
        if (!open || !active) return undefined;
        let alive = true;
        const timer = setTimeout(() => {
            setState((prev) => ({ ...prev, searching: true }));
            api.searchOperators(query)
                .then((data) => { if (alive) setState({ results: data.items || [], loaded: true, searching: false, failed: false }); })
                .catch(() => { if (alive) setState({ results: [], loaded: true, searching: false, failed: true }); });
        }, 200);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, open, active, query]);
    return state;
};

// Ширины полос заготовки (имя, строка группы), % — чтобы строки не были одинаковыми.
const BONES = [[46, 72], [38, 64], [52, 58], [41, 69], [49, 61]];

/* Строки заготовки — в рост настоящих: высота строки в em от того же кегля. */
const PersonBones = () => (
    <ul className="divide-y divide-slate-100" aria-busy="true" aria-label="Загрузка">
        {BONES.map(([name, line]) => (
            <li key={`${name}-${line}`} className="px-3.5 py-2.5">
                <span className={`flex h-[1.5em] items-center text-[13.5px] ${T.body}`}>
                    <span className="h-[0.8em] animate-pulse rounded-full bg-slate-200/70" style={{ width: `${name}%` }} />
                </span>
                <span className={`flex h-[1.5em] items-center text-[12px] ${T.note}`}>
                    <span className="h-[0.75em] animate-pulse rounded-full bg-slate-200/70" style={{ width: `${line}%` }} />
                </span>
            </li>
        ))}
    </ul>
);

const ListNote = ({ children }) => (
    <div className={`grid h-full place-items-center px-4 text-center text-[12.5px] text-slate-500 ${T.sub}`}>{children}</div>
);

export const OperatorPicker = ({ query, onQuery, results, loaded, searching, failed, onPick, invalid = false,
    autoFocus = false, listClassName = 'h-[300px]' }) => (
    <div className="space-y-2">
        <SearchField autoFocus={autoFocus} invalid={invalid} placeholder="ФИО или логин" value={query}
                     onChange={(e) => onQuery(e.target.value)} />
        <div className={`thin-scroll overflow-y-auto overscroll-contain rounded-xl ring-1 ring-slate-200/70 ${listClassName}`}>
            {!loaded ? <PersonBones /> : results.length ? (
                <ul className="divide-y divide-slate-100">
                    {results.map((person) => (
                        <li key={person.id}>
                            <button type="button" onClick={() => onPick(person)}
                                    className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left hover:bg-slate-50 active:bg-slate-100">
                                <span className="min-w-0 flex-1">
                                    <span className={`block truncate text-[13.5px] font-medium text-slate-900 ${T.body}`}>{person.name}</span>
                                    <span className={`block truncate text-[12px] text-slate-500 ${T.note}`}>
                                        {[person.group_name, person.department_name].filter(Boolean).join(' · ')}
                                        {person.role === 'trainee' ? ' · стажёр' : ''}
                                    </span>
                                </span>
                            </button>
                        </li>
                    ))}
                </ul>
            ) : searching ? (
                <div className="grid h-full place-items-center"><Spinner /></div>
            ) : (
                <ListNote>
                    {failed ? 'Список не загрузился — попробуйте набрать ФИО ещё раз'
                        : query ? 'Никого не нашли среди ваших сотрудников' : 'Среди ваших сотрудников пока никого нет'}
                </ListNote>
            )}
        </div>
    </div>
);
