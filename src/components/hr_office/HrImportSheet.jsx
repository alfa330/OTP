import React, { useMemo, useRef, useState } from 'react';
import { CircleCheck, FileSpreadsheet, Info, Loader2, Search, TriangleAlert, UploadCloud } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosModal, IosSegmented, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import { dateText, errorText, plural, timeOf } from './hrOffice';
import { Avatar, ChangeList, RowNotes } from './HrChanges';

/*
 * Загрузка таблиц HR: «Список по компаниям» (колонка «Отдел» — проекты) и табель (колонка
 * «Отдел» — отделы табеля). Просьба владельца 10.11.2026: «если уже такое есть — зачем
 * загружать во второй раз; сверяет, говорит, что где поменяется, и подтвердить».
 *
 * Поэтому три шага в одном окне: файл → сверка → итог. Сверку делает сервер: по каждому
 * найденному «было → станет», совпавшие целиком — «без изменений» и не пишутся; тот же файл,
 * загруженный раньше, узнаётся по отпечатку. Записывается только подтверждённое — и только
 * если «было» к моменту записи осталось тем, что видно здесь (иначе — в «не записано»).
 *
 * Окно не прыгает: шаг выбора файла и шаг сверки — одного роста с заготовкой, рамка
 * доезжает до нового шага (sizeKey), список — постоянной высоты, фильтр переезжает плашкой.
 */
const STEP_PICK = 'pick';
const STEP_READING = 'reading';
const STEP_REVIEW = 'review';
const STEP_DONE = 'done';
/* Все шаги окна — одного роста на компьютере (как страницы журнала): рамке не нужно ехать
   ни на шаге «файл → сверка», ни на «сверка → итог». Замер 10.11.2026: с ростом по содержимому
   рамка прыгала с 463 до 836 px за один кадр — первый показ сверки занимал поток на 267 мс. */
const STEP_HEIGHT = 'sm:h-[min(66vh,640px)]';

const PEOPLE = ['сотрудник', 'сотрудника', 'сотрудников'];
const PEOPLE_GEN = ['сотрудника', 'сотрудников', 'сотрудников'];
const FIELDS = ['поле', 'поля', 'полей'];

function rowView(row, picked, createCompanies) {
    const userId = row.match?.user_id ?? picked[row.line] ?? null;
    const candidate = row.state === 'ambiguous' && userId
        ? (row.candidates || []).find((item) => item.user_id === userId) : null;
    const source = row.state === 'found' ? row : candidate;
    // Компания, которой нет в справочнике, без «завести в справочнике» не запишется — и не показывается.
    const base = (source?.changes || []).filter((change) => !change.derived && (createCompanies || !change.new_company));
    /* «Станет пустым» (новый договор) — следствие смены компании или вида оформления: остаётся,
       только пока такая смена есть среди показанных. */
    const replaces = base.some((change) => change.field === 'company' || change.field === 'employment_type');
    const changes = base.concat(replaces ? (source?.changes || []).filter((change) => change.derived) : []);
    let kind = 'same';
    if (row.state === 'missing') kind = 'missing';
    else if (row.state === 'ambiguous' && !userId) kind = 'unpicked';
    else if (base.length) kind = 'changed';
    return { ...row, user_id: userId, person: row.match?.name || candidate?.name || row.name, changes, base,
             notes: source?.notes || [], now: source?.now || '', kind };
}

/* Две строки таблицы на одного человека не пишутся (вторая откатила бы первую): помечаем их
   «повтором» — так же решает и сервер. Строки тёзок, где выбрали уже занятого, — туда же. */
function markDuplicates(rows) {
    const usage = new Map();
    rows.forEach((row) => { if (row.user_id) usage.set(row.user_id, [...(usage.get(row.user_id) || []), row.line]); });
    return rows.map((row) => {
        const lines = row.user_id ? usage.get(row.user_id) : null;
        return lines && lines.length > 1 ? { ...row, kind: 'duplicate', duplicate_lines: lines } : row;
    });
}

function matches(row, needle) {
    if (!needle.length) return true;
    const hay = `${row.name} ${row.person}`.toLowerCase();
    return needle.every((part) => hay.includes(part));
}

function Checkbox({ checked, onChange, label }) {
    return (
        <input type="checkbox" checked={checked} aria-label={label} onChange={(event) => onChange(event.target.checked)}
               className="mt-2.5 h-4 w-4 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
    );
}

function PersonTitle({ row }) {
    return (
        <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[14px] font-medium text-slate-900">{row.person}</span>
            {row.person !== row.name && <span className="text-[12px] text-slate-400">в таблице: {row.name}</span>}
        </div>
    );
}

function ChangedRow({ row, skipped, onSkip }) {
    return (
        <div className={`flex items-start gap-3 px-4 py-3.5 transition-opacity duration-200 ${skipped ? 'opacity-50' : ''}`}>
            <Checkbox checked={!skipped} onChange={(value) => onSkip(row.line, !value)} label={`Записать: ${row.person}`} />
            <Avatar name={row.person} />
            <div className="min-w-0 flex-1">
                <PersonTitle row={row} />
                <ChangeList changes={row.changes} className="mt-1.5" />
                <RowNotes notes={row.notes} />
            </div>
        </div>
    );
}

function NamesakeRow({ row, picked, onPick, skipped, onSkip }) {
    const chosen = picked[row.line] ?? null;
    return (
        <div className={`flex items-start gap-3 px-4 py-3.5 transition-opacity duration-200 ${chosen && skipped ? 'opacity-50' : ''}`}>
            {chosen && row.kind === 'changed'
                ? <Checkbox checked={!skipped} onChange={(value) => onSkip(row.line, !value)} label={`Записать: ${row.person}`} />
                : <span className="w-4 shrink-0" />}
            <Avatar name={row.name} />
            <div className="min-w-0 flex-1">
                <div className="text-[14px] font-medium text-slate-900">{row.name}</div>
                <div className="text-[12.5px] text-amber-700">В iCORE несколько человек с таким ФИО — выберите, кто это</div>
                <CustomSelect variant="ios" value={chosen} onChange={(value) => onPick(row.line, value)}
                              placeholder="Выбрать сотрудника" className="mt-2 max-w-sm" ariaLabel={`Кто это: ${row.name}`}
                              options={(row.candidates || []).map((item) => ({ value: item.user_id, label: item.name, meta: item.department }))} />
                {chosen && row.kind === 'duplicate' && (
                    <div className="mt-2 text-[12.5px] text-amber-700">Этот сотрудник уже есть в другой строке таблицы — выберите другого</div>
                )}
                {chosen && row.kind !== 'duplicate' && (row.changes.length
                    ? <ChangeList changes={row.changes} className="mt-2" />
                    : <div className="mt-2 text-[12.5px] text-slate-500">Всё уже совпадает — записывать нечего</div>)}
                {chosen && <RowNotes notes={row.notes} />}
            </div>
        </div>
    );
}

function SameRow({ row }) {
    return (
        <div className="flex items-center gap-3 px-4 py-3">
            <Avatar name={row.person} />
            <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] font-medium text-slate-800">{row.person}</div>
                <div className="truncate text-[12.5px] text-slate-500">{row.now || 'В таблице для него нет данных'}</div>
                <RowNotes notes={row.notes} />
            </div>
            <CircleCheck size={17} className="shrink-0 text-emerald-500" aria-label="совпадает" />
        </div>
    );
}

function DuplicateRow({ row }) {
    return (
        <div className="flex items-start gap-3 px-4 py-3">
            <Avatar name={row.person} />
            <div className="min-w-0 flex-1 pt-0.5">
                <div className="text-[13.5px] font-medium text-slate-800">{row.person}</div>
                <div className="text-[12.5px] leading-snug text-amber-700">
                    В таблице несколько строк на этого сотрудника (строки {row.duplicate_lines.join(', ')}) — не записываются.
                    Оставьте в таблице одну строку или поправьте карточку вручную.
                </div>
            </div>
        </div>
    );
}

function MissingRow({ row }) {
    return (
        <div className="flex items-center gap-3 px-4 py-3">
            <Avatar name={row.name} className="!bg-slate-50 !text-slate-400" />
            <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] text-slate-700">{row.name}</div>
                <div className="truncate text-[12.5px] text-slate-400">{[row.group, row.job_title].filter(Boolean).join(' · ') || '—'}</div>
            </div>
            <span className="shrink-0 text-[12px] text-slate-400">нет в iCORE</span>
        </div>
    );
}

function Notice({ tone = 'blue', icon: Icon = Info, children }) {
    const tones = {
        blue: 'bg-blue-50/80 text-blue-900 ring-blue-100',
        amber: 'bg-amber-50 text-amber-900 ring-amber-200/70',
    };
    return (
        <div className={`flex items-start gap-2.5 rounded-2xl px-4 py-3 text-[13px] leading-relaxed ring-1 ${tones[tone]}`}>
            <Icon size={16} className="mt-0.5 shrink-0 opacity-70" />
            <div className="min-w-0">{children}</div>
        </div>
    );
}

function EmptyState({ icon: Icon = CircleCheck, tone = 'text-emerald-500', title, text }) {
    return (
        <div className="flex h-full min-h-[220px] flex-col items-center justify-center px-6 py-10 text-center">
            <Icon size={34} className={tone} />
            <div className="mt-3 text-[15px] font-semibold text-slate-900">{title}</div>
            {text && <div className="mt-1 max-w-md text-[13px] leading-relaxed text-slate-500">{text}</div>}
        </div>
    );
}

export default function HrImportSheet({ open = true, api, boot, toast, onClose, onApplied, onOpenJournal }) {
    const fileRef = useRef(null);
    const [step, setStep] = useState(STEP_PICK);
    const [file, setFile] = useState(null);
    const [preview, setPreview] = useState(null);
    const [refreshing, setRefreshing] = useState(false);
    const [picked, setPicked] = useState({});
    const [skipped, setSkipped] = useState({});
    const [createCompanies, setCreateCompanies] = useState(true);
    const [filter, setFilter] = useState('changed');
    const [query, setQuery] = useState('');
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);
    const [dragging, setDragging] = useState(false);

    // Номер сверки: ответ на прежнюю (сменили проект дважды подряд) не перетирает свежую.
    const ticket = useRef(0);
    const read = async (nextFile, project) => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        const fresh = nextFile !== file;
        const previousFile = file;
        if (fresh) {
            setStep(STEP_READING);
            setFile(nextFile);
        } else {
            setRefreshing(true);
        }
        try {
            const { data } = await api.upload('/import/preview', nextFile, { project });
            if (mine !== ticket.current) return;
            setPreview(data);
            if (fresh) {
                setPicked({});
                setSkipped({});
                setQuery('');
                setFilter('changed');
            }
            setStep(STEP_REVIEW);
        } catch (requestError) {
            if (mine !== ticket.current) return;
            toast?.(errorText(requestError, 'Таблица не прочиталась'), 'error');
            // Новый файл не прочитался — остаётся прежняя сверка (если была) со своим файлом.
            if (fresh) {
                setFile(previousFile);
                setStep(preview && previousFile ? STEP_REVIEW : STEP_PICK);
            }
        } finally {
            if (mine === ticket.current) setRefreshing(false);
        }
    };

    const choose = (next) => {
        if (!next) return;
        if (!/\.xlsx$/i.test(next.name || '')) {
            toast?.('Нужен файл Excel (.xlsx)', 'error');
            return;
        }
        read(next, undefined);
    };

    const rows = useMemo(() => markDuplicates((preview?.rows || []).map((row) => rowView(row, picked, createCompanies))),
        [preview, picked, createCompanies]);
    const counts = useMemo(() => {
        const out = { changed: 0, namesakes: 0, unpicked: 0, same: 0, missing: 0, duplicates: 0, fields: 0 };
        rows.forEach((row) => {
            if (row.kind === 'changed') { out.changed += 1; out.fields += row.base.length; }
            if (row.kind === 'same') out.same += 1;
            if (row.kind === 'duplicate') out.duplicates += 1;
            if (row.kind === 'missing') out.missing += 1;
            if (row.kind === 'unpicked') out.unpicked += 1;
            if (row.state === 'ambiguous') out.namesakes += 1;
        });
        return out;
    }, [rows]);
    const chosen = rows.filter((row) => row.kind === 'changed' && !skipped[row.line]);
    const chosenFields = chosen.reduce((sum, row) => sum + row.base.length, 0);
    const usedNewCompanies = useMemo(() => {
        const names = new Set();
        (preview?.rows || []).map((row) => rowView(row, picked, true)).forEach((row) => row.base.forEach((change) => {
            if (change.new_company) names.add(change.new);
        }));
        return [...names];
    }, [preview, picked]);

    const needle = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const listed = rows.filter((row) => {
        if (filter === 'changed' && row.kind !== 'changed') return false;
        if (filter === 'namesakes' && row.state !== 'ambiguous') return false;
        if (filter === 'same' && row.kind !== 'same') return false;
        if (filter === 'missing' && row.kind !== 'missing') return false;
        if (filter === 'duplicates' && row.kind !== 'duplicate') return false;
        return matches(row, needle);
    });

    const apply = async () => {
        setBusy(true);
        try {
            const { data } = await api.post('/import/apply', {
                mode: preview.mode, project: preview.project, create_companies: createCompanies,
                file_name: file?.name || '', sheet: preview.sheet, file_sha256: preview.file_sha256,
                rows: rows.map((row) => ({
                    line: row.line, name: row.name, state: row.state, user_id: row.user_id,
                    // Снятая галочка — только у строки с изменениями: у совпавшей она ничего не значит.
                    skip: row.kind === 'changed' && Boolean(skipped[row.line]), group: row.group, job_title: row.job_title,
                    employment_type: row.employment_type, company: row.company, schedule: row.schedule,
                    day_hours: row.day_hours,
                    // Подтверждаются только поля HR; «станет пустым» — следствие, сервер считает его сам.
                    confirm: row.kind === 'changed' ? Object.fromEntries(row.base.map((change) => [change.field, change.old])) : {},
                })),
            });
            setResult(data);
            setStep(STEP_DONE);
            if (data.changed || data.companies_created) onApplied?.();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не записалось'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const projectOptions = (boot.projects || []).map((item) => ({ value: item, label: item }));
    const nothingToDo = preview && counts.changed === 0 && counts.unpicked === 0;
    const filterOptions = [
        { value: 'changed', label: `Изменится · ${counts.changed}` },
        counts.namesakes > 0 && { value: 'namesakes', label: counts.unpicked ? `Выбрать · ${counts.unpicked}` : `Тёзки · ${counts.namesakes}` },
        { value: 'same', label: `Без изменений · ${counts.same}` },
        counts.missing > 0 && { value: 'missing', label: `Нет в iCORE · ${counts.missing}` },
        counts.duplicates > 0 && { value: 'duplicates', label: `Повторы · ${counts.duplicates}` },
    ].filter(Boolean);

    // Подвал есть у каждого шага — иначе окно подрастало бы на его высоту при переходе к сверке.
    let footer = (
        <div className="flex w-full items-center justify-between gap-2">
            <span className="text-[12.5px] text-slate-500">Файл Excel (.xlsx) до 5 МБ</span>
            <button type="button" className={iosBtnPrimary} onClick={() => fileRef.current?.click()} disabled={step === STEP_READING}>
                {step === STEP_READING ? <Loader2 size={15} className="animate-spin" /> : <FileSpreadsheet size={15} />} Выбрать файл
            </button>
        </div>
    );
    if (step === STEP_REVIEW) {
        footer = (
            <div className="flex w-full flex-wrap items-center justify-between gap-2">
                <button type="button" className={iosBtnSecondary} onClick={() => fileRef.current?.click()} disabled={busy}>
                    Другой файл
                </button>
                {nothingToDo ? (
                    <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
                ) : (
                    <div className="flex items-center gap-3">
                        {chosen.length > 0 && (
                            <span className="hidden text-[12.5px] text-slate-500 sm:inline">
                                {chosenFields} {plural(chosenFields, FIELDS)} у {chosen.length} {plural(chosen.length, PEOPLE_GEN)}
                            </span>
                        )}
                        <button type="button" className={iosBtnPrimary} onClick={apply} disabled={busy || refreshing || chosen.length === 0}>
                            {busy && <Loader2 size={15} className="animate-spin" />} Подтвердить изменения
                        </button>
                    </div>
                )}
            </div>
        );
    } else if (step === STEP_DONE) {
        footer = (
            <div className="flex w-full flex-wrap items-center justify-between gap-2">
                {onOpenJournal && result?.changed > 0
                    ? <button type="button" className={iosBtnSecondary} onClick={onOpenJournal}>Открыть журнал</button>
                    : <span />}
                <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
            </div>
        );
    }

    const subtitle = step === STEP_PICK || !file
        ? '«Список по компаниям» или табель'
        : `${file.name}${preview?.sheet?.trim() ? ` · лист «${preview.sheet.trim()}»` : ''}`;

    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-4xl" title="Загрузить таблицу HR" subtitle={subtitle}
                  footer={footer} sizeKey={step}>
            <input ref={fileRef} type="file" accept=".xlsx" className="hidden"
                   onChange={(event) => { const next = event.target.files?.[0]; event.target.value = ''; choose(next); }} />

            {(step === STEP_PICK || step === STEP_READING) && (
                <button type="button" onClick={() => fileRef.current?.click()} disabled={step === STEP_READING}
                        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                        onDragLeave={() => setDragging(false)}
                        onDrop={(event) => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files?.[0]); }}
                        className={`flex min-h-[320px] w-full flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed px-6 py-12 text-center transition-colors duration-200 sm:min-h-0 ${STEP_HEIGHT} ${
                            dragging ? 'border-blue-400 bg-blue-50/70' : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/70'}`}>
                    {step === STEP_READING ? (
                        <div key="reading" className="flex flex-col items-center gap-3 motion-safe:animate-fade-in-soft">
                            <Loader2 size={26} className="animate-spin text-blue-500" />
                            <span className="text-[14px] font-medium text-slate-800">Сверяем с iCORE…</span>
                            <span className="text-[12.5px] text-slate-500">{file?.name}</span>
                        </div>
                    ) : (
                        <div key="pick" className="flex flex-col items-center gap-3">
                            <span className={`grid h-14 w-14 place-items-center rounded-2xl transition-colors duration-200 ${dragging ? 'bg-blue-100 text-blue-600' : 'bg-slate-100 text-slate-500'}`}>
                                <UploadCloud size={26} />
                            </span>
                            <span className="text-[15px] font-semibold text-slate-900">Перетащите файл Excel сюда</span>
                            <span className="text-[13px] text-slate-500">или нажмите, чтобы выбрать · таблица с колонкой «Ф.И.О должностного лица»</span>
                            <span className="mt-1 max-w-md text-[12.5px] leading-relaxed text-slate-400">
                                Перед записью покажем, у кого и что поменяется. Что уже совпадает — второй раз не запишется.
                            </span>
                        </div>
                    )}
                </button>
            )}

            {step === STEP_REVIEW && preview && (
                <div key="review" className={`flex flex-col gap-3 motion-safe:animate-fade-in-soft ${STEP_HEIGHT}`}>
                    <div className={`${iosCard} flex shrink-0 flex-wrap items-center gap-3 p-4`}>
                        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                            <FileSpreadsheet size={20} />
                        </span>
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-[14px] font-medium text-slate-900">{file?.name}</div>
                            <div className="text-[12.5px] text-slate-500">
                                {rows.length} {plural(rows.length, ['строка', 'строки', 'строк'])} · колонка «Отдел» — {preview.mode === 'project' ? 'проекты' : 'отделы табеля'}
                            </div>
                        </div>
                        {preview.mode === 'group' && (
                            <div className="flex items-center gap-2">
                                <span className="text-[13px] text-slate-600">Проект этих людей</span>
                                <CustomSelect variant="ios" value={preview.project} options={projectOptions} className="w-40"
                                              ariaLabel="Проект" onChange={(value) => value !== preview.project && read(file, value)} />
                            </div>
                        )}
                    </div>

                    {preview.previous && (
                        <Notice>
                            Этот файл уже загружали {dateText(preview.previous.at)} в {timeOf(preview.previous.at)}
                            {preview.previous.actor_name ? ` — ${preview.previous.actor_name}` : ''}.
                            {/* Не «данные поменялись»: расхождение бывает и потому, что прошлый раз записали
                                не всё (сняли галочки), — говорим, что есть сейчас. */}
                            {counts.changed === 0
                                ? ' Сейчас всё в iCORE совпадает с файлом — записывать нечего.'
                                : ` Сейчас с файлом расходятся данные у ${counts.changed} ${plural(counts.changed, PEOPLE_GEN)} — ниже, что поменяется.`}
                        </Notice>
                    )}
                    {usedNewCompanies.length > 0 && (
                        <div className={`${iosCard} flex items-center justify-between gap-3 px-4 py-3`}>
                            <span className="min-w-0 text-[13px] text-slate-700">
                                Завести в справочнике компаний: <span className="font-medium">{usedNewCompanies.join(', ')}</span>
                            </span>
                            <IosToggle checked={createCompanies} onChange={setCreateCompanies} />
                        </div>
                    )}

                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                        <IosSegmented slide value={filter} onChange={setFilter} options={filterOptions} ariaLabel="Какие строки показать" />
                        <div className="relative ml-auto min-w-[200px] flex-1 sm:max-w-[260px]">
                            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                                   className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                        </div>
                    </div>

                    <div className={`${iosCard} thin-scroll overflow-y-auto transition-opacity duration-200 sm:min-h-0 sm:flex-1 ${refreshing ? 'opacity-50' : ''}`}>
                        <div key={filter} className="divide-y divide-slate-100 motion-safe:animate-fade-in-soft">
                            {listed.length === 0 && (
                                filter === 'changed' && !needle.length
                                    ? <EmptyState title="Изменений нет"
                                                  text={counts.same
                                                      ? `У всех ${counts.same} найденных данные в iCORE уже такие же, как в файле. Записывать нечего.`
                                                      : 'В файле нет строк, которые что-то поменяют.'} />
                                    : <EmptyState icon={Search} tone="text-slate-300" title="Никого не нашли"
                                                  text={needle.length ? 'Попробуйте другую часть ФИО' : ''} />
                            )}
                            {listed.map((row) => {
                                if (row.state === 'ambiguous') {
                                    return <NamesakeRow key={row.line} row={row} picked={picked} skipped={!!skipped[row.line]}
                                                        onPick={(line, value) => {
                                                            setPicked((prev) => ({ ...prev, [line]: value }));
                                                            // Другой человек — снятая у прежнего галочка к нему не переходит.
                                                            setSkipped((prev) => ({ ...prev, [line]: false }));
                                                        }}
                                                        onSkip={(line, value) => setSkipped((prev) => ({ ...prev, [line]: value }))} />;
                                }
                                if (row.kind === 'changed') {
                                    return <ChangedRow key={row.line} row={row} skipped={!!skipped[row.line]}
                                                       onSkip={(line, value) => setSkipped((prev) => ({ ...prev, [line]: value }))} />;
                                }
                                if (row.kind === 'missing') return <MissingRow key={row.line} row={row} />;
                                if (row.kind === 'duplicate') return <DuplicateRow key={row.line} row={row} />;
                                return <SameRow key={row.line} row={row} />;
                            })}
                        </div>
                    </div>
                </div>
            )}

            {step === STEP_DONE && result && (
                <div key="done" className={`thin-scroll flex flex-col gap-3 motion-safe:animate-fade-in-soft sm:justify-center sm:overflow-y-auto ${STEP_HEIGHT}`}>
                    <div className="flex flex-col items-center px-6 pb-2 pt-6 text-center">
                        {result.changed > 0
                            ? <span className="grid h-14 w-14 place-items-center rounded-full bg-emerald-50 text-emerald-600"><CircleCheck size={30} /></span>
                            : <span className="grid h-14 w-14 place-items-center rounded-full bg-amber-50 text-amber-600"><TriangleAlert size={28} /></span>}
                        <div className="mt-3 text-[17px] font-semibold text-slate-900">
                            {result.changed > 0 ? 'Изменения записаны' : 'Ничего не записано'}
                        </div>
                        {result.changed > 0 && (
                            <div className="mt-1 text-[13.5px] text-slate-500">
                                {result.fields} {plural(result.fields, FIELDS)} у {result.changed} {plural(result.changed, PEOPLE_GEN)}
                            </div>
                        )}
                    </div>
                    <div className={`${iosCard} divide-y divide-slate-100`}>
                        {[
                            ['Без изменений', result.same ? `${result.same} ${plural(result.same, PEOPLE)}` : ''],
                            ['Пропущены вами', result.skipped ? `${result.skipped} ${plural(result.skipped, PEOPLE)}` : ''],
                            ['Нет в iCORE', result.missing ? `${result.missing} ${plural(result.missing, PEOPLE)}` : ''],
                            ['Тёзки без выбора', result.unresolved ? `${result.unresolved} ${plural(result.unresolved, PEOPLE)}` : ''],
                            ['Новые компании', result.companies_created ? String(result.companies_created) : ''],
                        ].filter(([, value]) => value).map(([label, value]) => (
                            <div key={label} className="flex items-center justify-between gap-3 px-4 py-3 text-[13.5px]">
                                <span className="text-slate-600">{label}</span>
                                <span className="font-medium text-slate-900">{value}</span>
                            </div>
                        ))}
                    </div>
                    {result.cards_failed > 0 && (
                        <Notice tone="amber" icon={TriangleAlert}>
                            <div className="font-medium">Не обновилась карточка «Учета сотрудников»</div>
                            <div>{(result.cards_failed_names || []).join(', ')}</div>
                            <div className="mt-1 text-amber-800/80">
                                Договор записан, а вид оформления, компания или должность в «Учете сотрудников» остались прежними —
                                поправьте их там вручную: повторная загрузка этих людей уже покажет «без изменений».
                            </div>
                        </Notice>
                    )}
                    {result.duplicates?.length > 0 && (
                        <Notice tone="amber" icon={TriangleAlert}>
                            Не записаны — в таблице несколько строк на одного человека: {[...new Set(result.duplicates.map((item) => item.name))].join(', ')}
                        </Notice>
                    )}
                    {result.conflicts?.length > 0 && (
                        <Notice tone="amber" icon={TriangleAlert}>
                            <div className="font-medium">Не записано: пока шла сверка, эти данные в iCORE поменялись</div>
                            <ul className="mt-1 space-y-0.5">
                                {result.conflicts.map((item) => (
                                    <li key={item.user_id}>{item.name} — {item.fields.join(', ').toLowerCase()}</li>
                                ))}
                            </ul>
                            <div className="mt-1 text-amber-800/80">Загрузите файл ещё раз — сверка покажет, что теперь поменяется.</div>
                        </Notice>
                    )}
                </div>
            )}
        </IosModal>
    );
}
