import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, ExternalLink, Link2, Loader2, Plus, RefreshCw, Search, Settings2, Sparkles, X } from 'lucide-react';
import {
    IosHint, IosModal, IosPager, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import { copyText } from '../call_qa/evalLink';
import { HhResume } from './HhResume';
import { Empty, Loading, Notice } from './hiringUi';
import {
    errorOf, hhAddKeywords, hhBaseExperience, hhBaseLine, hhBaseNeedsHh, hhBaseSearchLine, hhBaseState, hhBaseTone,
    hhResumeLink, hhSearchBody, longTime, plural,
} from './hiringMeta';

/*
 * «База hh» — поиск кандидатов в базе резюме hh.kz (владелец 25.11.2026: «поиск кандидатов по конкретной вакансии (для
 * которой включен такой поиск) с помощью ключевых слов с ИИ… чтобы ИИ могла по портрету сотрудника определять и давать
 * скорбалл кандидату… кнопка взять в работу… с источником База HeadHunter… обновление резюмешки в момент просмотра»).
 *
 * Вакансии с включённым поиском — плитками; кандидаты вакансии — карточками по баллу ИИ. Окно резюме при открытии
 * просит мост перечитать резюме на hh. «Взять в работу» открывает резюме на hh в новой вкладке: «Открыть контакты» там
 * жмёт сам рекрутер (hh подписывает эту кнопку настоящим щелчком — решение владельца 25.11.2026), а ФИО и телефон мост
 * переносит в карточку «В работе» сам; вернулся во вкладку iCORE — проверка сразу. Портрет, ключевые слова и тумблер
 * поиска — у руководителя рекрутинга (кнопка «Настроить»).
 *
 * У каждого резюме своя ссылка (владелец 26.11.2026): открытое окно — в адресе ?view=hiring&hh_resume=<номер>
 * (метку ведёт HiringView), кнопка «Ссылка» в шапке окна копирует её для коллеги.
 */

const VACANCY_KEY = 'hiring.base.vacancy';
const LIST_POLL_MS = 15000;
const PAGE_SIZE = 30;

const VIEWS = [
    { value: 'new', label: 'Новые' },
    { value: 'taken', label: 'Взятые' },
    { value: 'rejected', label: 'Не подошли' },
];
const ORDERS = [
    { value: 'score', label: 'По баллу' },
    { value: 'fresh', label: 'Свежие' },
];
const DOTS = { green: 'bg-emerald-500', blue: 'bg-blue-500', amber: 'bg-amber-500', red: 'bg-rose-500', slate: 'bg-slate-400' };
// «Кружок + текст» рядом и на телефоне: сетка с колонками в style (mobile-shell.css не трогает её, в отличие от flex).
const ROW = { gridTemplateColumns: 'auto minmax(0, 1fr)' };
const RINGS = {
    green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    amber: 'bg-amber-50 text-amber-700 ring-amber-200',
    red: 'bg-rose-50 text-rose-700 ring-rose-200',
    slate: 'bg-slate-100 text-slate-500 ring-slate-200',
};

const readStored = () => {
    try {
        return window.localStorage.getItem(VACANCY_KEY) || '';
    } catch (error) {
        return '';
    }
};

const storeVacancy = (value) => {
    try {
        window.localStorage.setItem(VACANCY_KEY, value);
    } catch (error) {
        // Приватное окно: выбор просто не запомнится.
    }
};

/** Балл ИИ кружком: цвет — по порогам; ещё не оценён — ИИ думает. */
const ScoreRing = ({ item, size = 'md' }) => {
    const big = size === 'lg';
    const box = big ? 'h-14 w-14 text-[20px]' : 'h-11 w-11 text-[16px]';
    if (item.score == null) {
        return (
            <span className={`grid shrink-0 place-items-center rounded-full ring-1 ${RINGS.slate} ${box}`}
                  title={item.scoring ? 'ИИ ставит балл' : (item.score_error || 'Балла нет')}>
                {item.scoring ? <Loader2 size={big ? 20 : 16} className="animate-spin" /> : '—'}
            </span>
        );
    }
    return (
        <span className={`grid shrink-0 place-items-center rounded-full font-semibold tabular-nums ring-1 ${RINGS[hhBaseTone(item.score)]} ${box}`}
              title={item.score_basis === 'full' ? 'Балл ИИ по полному резюме' : 'Балл ИИ по краткой карточке из поиска'}>
            {item.score}
        </span>
    );
};

const StateDot = ({ state }) => (state ? (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-600">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOTS[state.tone] || DOTS.slate}`} />
        {state.text}
    </span>
) : null);

const VacancyTile = ({ vacancy, active, onClick }) => {
    const counts = vacancy.counts || {};
    const fresh = (counts.new || 0) + (counts.taking || 0);
    return (
        <button type="button" onClick={onClick}
                className={`min-w-0 rounded-2xl px-4 py-3 text-left ring-1 transition ${active
                    ? 'bg-blue-50 ring-blue-200' : 'bg-white ring-slate-200/70 hover:ring-slate-300'}`}>
            <div className="grid items-center gap-2.5" style={ROW}>
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${vacancy.enabled ? 'bg-blue-600 text-white' : 'bg-slate-200 text-slate-500'}`}>
                    <Search size={15} strokeWidth={2.2} />
                </span>
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">{vacancy.name}</div>
                    <div className="truncate text-[12px] text-slate-500">
                        {vacancy.running ? 'Ищем на hh…' : `${fresh} ${plural(fresh, 'новый', 'новых', 'новых')}${counts.strong ? ` · ${counts.strong} от 70` : ''}`}
                        {!vacancy.enabled && ' · поиск выключен'}
                    </div>
                </div>
            </div>
        </button>
    );
};

const ResumeCard = ({ item, meId, onOpen }) => {
    const experience = hhBaseExperience(item);
    return (
        <button type="button" onClick={() => onOpen(item.id)}
                className={`${iosCard} flex min-w-0 flex-col gap-2 p-4 text-left transition hover:ring-blue-200`}>
            {/* Кружок балла и название — сеткой: на телефоне оболочка ставит «flex items-start» с flex-1 столбиком. */}
            <div className="grid items-start gap-3" style={ROW}>
                <ScoreRing item={item} />
                <div className="min-w-0">
                    <div className="truncate text-[15px] font-semibold text-slate-900">{item.title || 'Без должности'}</div>
                    <div className="truncate text-[12.5px] text-slate-500">{hhBaseLine(item)}</div>
                </div>
            </div>
            {experience && <div className="line-clamp-1 text-[12.5px] text-slate-600">{experience}</div>}
            {item.verdict && <div className="line-clamp-2 text-[13px] leading-snug text-slate-800">{item.verdict}</div>}
            <StateDot state={hhBaseState(item, meId)} />
        </button>
    );
};

/* ── Окно резюме ─────────────────────────────────────────────────────────── */

const Points = ({ items, tone }) => (items?.length ? (
    <ul className="space-y-1">
        {items.map((text) => (
            <li key={text} className="flex items-start gap-2 text-[13px] text-slate-700">
                {tone === 'pro'
                    ? <Check size={14} className="mt-0.5 shrink-0 text-emerald-600" />
                    : <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />}
                <span>{text}</span>
            </li>
        ))}
    </ul>
) : null);

const ResumeSheet = ({ api, toast, id, meId, caps, onClose, onChanged, onOpenCard, onLoaded }) => {
    const [item, setItem] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [busy, setBusy] = useState(false);
    const polls = useRef(0);
    const loadedRef = useRef(onLoaded);
    useEffect(() => { loadedRef.current = onLoaded; }, [onLoaded]);

    useEffect(() => {
        if (!id) {
            setItem(null);
            return undefined;
        }
        let alive = true;
        polls.current = 0;
        setItem(null);
        setLoadError('');
        // Окно открыл рекрутер — мост перечитывает резюме на hh (если прошлое чтение не свежее).
        api.get(`/hh/base/resume/${id}`, { refresh: '1' })
            .then(({ data }) => {
                if (!alive) return;
                setItem(data);
                loadedRef.current?.(data);
            })
            .catch((error) => { if (alive) setLoadError(errorOf(error, 'Резюме не открылось')); });
        return () => { alive = false; };
    }, [api, id]);

    const copyLink = async () => {
        const link = hhResumeLink(window.location.href, id);
        const copied = link ? await copyText(link) : false;
        toast(copied ? 'Ссылка на резюме скопирована' : 'Не удалось скопировать — ссылка есть в адресной строке',
              copied ? 'success' : 'error');
    };

    // Мост читает резюме или ждём контакты — окно перечитывается: первые две минуты часто, потом реже.
    const waiting = Boolean(item && (item.refreshing || item.status === 'taking'));
    useEffect(() => {
        if (!waiting) return undefined;
        polls.current += 1;
        const delay = polls.current > 40 ? 15000 : (item.refreshing ? 3000 : 5000);
        const timer = setTimeout(() => {
            api.get(`/hh/base/resume/${item.id}`)
                .then(({ data }) => {
                    setItem(data);
                    if (data.status !== item.status) onChanged();
                })
                .catch(() => {});
        }, delay);
        return () => clearTimeout(timer);
    }, [api, item, waiting, onChanged]);

    const mineTaking = Boolean(item && item.status === 'taking' && Number(item.status_by) === Number(meId));
    const check = useCallback((silent) => {
        if (!item) return;
        api.post(`/hh/base/resume/${item.id}/check`)
            .then(({ data }) => { setItem(data); if (!silent) toast('Проверяем контакты на hh…', 'success'); })
            .catch((error) => { if (!silent) toast(errorOf(error, 'Не получилось проверить контакты'), 'error'); });
    }, [api, item, toast]);
    // Рекрутер открыл контакты на hh и вернулся во вкладку iCORE — проверить сразу, не ждать следующей проверки.
    useEffect(() => {
        if (!mineTaking) return undefined;
        const onBack = () => { if (document.visibilityState === 'visible') check(true); };
        document.addEventListener('visibilitychange', onBack);
        window.addEventListener('focus', onBack);
        return () => {
            document.removeEventListener('visibilitychange', onBack);
            window.removeEventListener('focus', onBack);
        };
    }, [mineTaking, check]);

    const take = () => {
        if (!item || busy) return;
        // Вкладку hh — сразу в обработчике щелчка (иначе браузер заблокирует всплывающее окно): контакты открывает человек.
        if (hhBaseNeedsHh(item)) window.open(item.url, '_blank', 'noopener');
        setBusy(true);
        api.post(`/hh/base/resume/${item.id}/take`)
            .then(({ data }) => {
                setItem(data);
                onChanged();
                toast(hhBaseNeedsHh(item) ? 'Откройте контакты на hh — карточка появится сама' : 'Берём контакты с hh…', 'success');
            })
            .catch((error) => toast(errorOf(error, 'Не получилось взять в работу'), 'error'))
            .finally(() => setBusy(false));
    };
    const setStatus = (status) => {
        if (!item || busy) return;
        setBusy(true);
        api.post(`/hh/base/resume/${item.id}/status`, { status })
            .then(({ data }) => { setItem(data); onChanged(); })
            .catch((error) => toast(errorOf(error, 'Не получилось'), 'error'))
            .finally(() => setBusy(false));
    };

    const cardId = item?.card_id || (item?.known && !item.known.is_closed ? item.known.card_id : null);
    const cardNo = item?.known?.candidate_no || item?.card_id;
    let footer = null;
    if (item) {
        const buttons = [];
        if (item.status === 'new' && caps.take && !(item.known && !item.known.is_closed)) {
            buttons.push(<button key="take" type="button" className={iosBtnPrimary} disabled={busy} onClick={take}>Взять в работу</button>);
        }
        if (item.status === 'taking' && (mineTaking || caps.manage)) {
            buttons.push(
                <a key="hh" href={item.url} target="_blank" rel="noopener noreferrer" className={iosBtnPrimary}>
                    Открыть контакты на hh <ExternalLink size={14} />
                </a>,
                <button key="check" type="button" className={iosBtnSecondary} onClick={() => check(false)}>Проверить</button>,
            );
        }
        if (cardId && onOpenCard) {
            buttons.push(<button key="card" type="button" className={iosBtnPrimary} onClick={() => { onClose(); onOpenCard(cardId); }}>
                Открыть карточку{cardNo ? ` №${cardNo}` : ''}
            </button>);
        }
        if (item.status === 'new') {
            buttons.push(<button key="reject" type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setStatus('rejected')}>Не подходит</button>);
        }
        if (item.status === 'rejected' || (item.status === 'taking' && (mineTaking || caps.manage))) {
            buttons.push(<button key="back" type="button" className={iosBtnGhost} disabled={busy} onClick={() => setStatus('new')}>
                {item.status === 'rejected' ? 'Вернуть в список' : 'Отменить'}
            </button>);
        }
        footer = buttons.length ? <div className="flex flex-wrap justify-end gap-2">{buttons}</div> : null;
    }

    return (
        <IosModal open={Boolean(id)} onClose={onClose} maxWidth="max-w-2xl" sizeKey={item ? 'ready' : 'loading'}
                  title={item?.title || 'Резюме'} subtitle={item ? hhBaseLine(item) : ''} footer={footer}
                  headerActions={item ? (
                      <button type="button" onClick={copyLink} aria-label="Скопировать ссылку на резюме"
                              title="Скопировать ссылку на это резюме — её можно отправить коллеге" className={iosBtnGhost}>
                          <Link2 size={14} aria-hidden="true" />
                          <span className="hidden sm:inline">Ссылка</span>
                      </button>
                  ) : null}>
            {loadError && <Notice tone="red">{loadError}</Notice>}
            {!item && !loadError && <Loading text="Открываем резюме…" />}
            {item && (
                <div className="space-y-4">
                    <div className="grid items-start gap-4 rounded-2xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/60" style={ROW}>
                        <ScoreRing item={item} size="lg" />
                        <div className="min-w-0 space-y-2">
                            <div className="text-[14px] font-medium leading-snug text-slate-900">
                                {item.verdict || (item.scoring ? 'ИИ ставит балл по портрету…' : (item.score_error || 'Балла пока нет'))}
                            </div>
                            <Points items={item.pros} tone="pro" />
                            <Points items={item.cons} tone="con" />
                            {item.score != null && item.score_basis !== 'full' && (
                                <div className="text-[11.5px] text-slate-500">Балл по краткой карточке — по полному резюме он уточнится</div>
                            )}
                        </div>
                    </div>
                    {mineTaking && (
                        <Notice tone="slate" title={item.contacts_note === 'Контакты на hh ещё не открыты' ? 'Контакты на hh ещё не открыты' : 'Ждём контакты'}>
                            Нажмите на hh «Открыть контакты» — спишется 1 контакт. ФИО и телефон перейдут в карточку сами.
                        </Notice>
                    )}
                    {item.contacts_note && !(mineTaking && item.contacts_note === 'Контакты на hh ещё не открыты') && (
                        <Notice tone={item.status === 'taken' ? 'blue' : 'amber'}>{item.contacts_note}</Notice>
                    )}
                    {/* Ждём контакты — всё сказано выше: ни точки состояния, ни строки «обновляем», ни «контакты закрыты». */}
                    {!mineTaking && (
                        <>
                            <StateDot state={hhBaseState(item, meId)} />
                            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[12px] text-slate-500">
                                <span className="inline-flex items-center gap-1.5">
                                    {item.refreshing
                                        ? <><Loader2 size={13} className="animate-spin" /> Обновляем резюме с hh…</>
                                        : (item.resume_at ? `Обновлено с hh ${longTime(item.resume_at)}` : 'Краткая карточка из поиска')}
                                </span>
                                {item.access_label && <span>{item.access_label}</span>}
                            </div>
                        </>
                    )}
                    <HhResume responses={[{ topic_id: 'base', resume: item.resume, resume_url: item.url, source: 'base' }]} />
                </div>
            )}
        </IosModal>
    );
};

/* ── Настройка поиска вакансии (руководитель) ────────────────────────────── */

const SEARCH_HINT = 'Включённый поиск сам ищет новые резюме на hh в рабочее время раз в 3 часа (первый раз — сразу, за '
    + '30 дней) в регионе вакансии. ИИ ставит каждому кандидату балл по портрету.';

const SearchSettings = ({ api, toast, target, vacancies, onClose, onSaved }) => {
    const [picked, setPicked] = useState(null);
    const [enabled, setEnabled] = useState('off');
    const [portrait, setPortrait] = useState('');
    const [keywords, setKeywords] = useState([]);
    const [draft, setDraft] = useState('');
    const [saving, setSaving] = useState(false);
    const [suggesting, setSuggesting] = useState(false);
    const vacancy = target === 'pick' ? picked : target;

    useEffect(() => { setPicked(null); }, [target]);
    useEffect(() => {
        if (!vacancy) return;
        setEnabled(vacancy.enabled ? 'on' : 'off');
        setPortrait(vacancy.portrait || '');
        setKeywords(vacancy.keywords || []);
        setDraft('');
    }, [vacancy]);

    const addDraft = () => {
        if (!draft.trim()) return;
        setKeywords((list) => hhAddKeywords(list, draft));
        setDraft('');
    };
    const suggest = () => {
        if (!vacancy || suggesting) return;
        setSuggesting(true);
        api.post(`/hh/search/${vacancy.hh_vacancy_id}/keywords`, { portrait })
            .then(({ data }) => { setKeywords(hhAddKeywords([], (data.keywords || []).join('\n'))); toast('ИИ подобрал слова — проверьте и сохраните', 'success'); })
            .catch((error) => toast(errorOf(error, 'ИИ не подобрал слова'), 'error'))
            .finally(() => setSuggesting(false));
    };
    const save = () => {
        if (!vacancy || saving) return;
        const words = draft.trim() ? hhAddKeywords(keywords, draft) : keywords;
        setSaving(true);
        api.put(`/hh/search/${vacancy.hh_vacancy_id}`, hhSearchBody({ enabled: enabled === 'on', portrait, keywords: words }))
            .then(({ data }) => { toast(enabled === 'on' ? 'Поиск по базе включён' : 'Сохранено', 'success'); onSaved(data, vacancy.hh_vacancy_id); })
            .catch((error) => toast(errorOf(error, 'Не сохранилось'), 'error'))
            .finally(() => setSaving(false));
    };

    const choices = (vacancies || []).filter((item) => item.in_cabinet);
    return (
        <IosModal open={Boolean(target)} onClose={onClose} maxWidth="max-w-xl"
                  onBack={target === 'pick' && picked ? () => setPicked(null) : null}
                  title={vacancy ? vacancy.name : 'Поиск по базе hh'} subtitle={vacancy?.area || ''}
                  sizeKey={vacancy ? 'edit' : 'pick'}
                  footer={vacancy ? (
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} disabled={saving} onClick={save}>Сохранить</button>
                      </div>
                  ) : null}>
            {!vacancy ? (
                <div className="space-y-2">
                    {choices.length ? choices.map((item) => (
                        <button key={item.hh_vacancy_id} type="button" onClick={() => setPicked(item)}
                                className="flex w-full items-center justify-between gap-3 rounded-xl px-3.5 py-3 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-50">
                            <span className="min-w-0">
                                <span className="block truncate text-[14px] font-medium text-slate-900">{item.name}</span>
                                <span className="block truncate text-[12px] text-slate-500">{item.area || ''}</span>
                            </span>
                            <span className={`shrink-0 text-[12px] ${item.enabled ? 'text-blue-700' : 'text-slate-400'}`}>
                                {item.enabled ? 'Ищем' : 'Выключен'}
                            </span>
                        </button>
                    )) : <Empty>Мост hh ещё не прислал вакансии кабинета</Empty>}
                </div>
            ) : (
                <div className="space-y-5">
                    <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-1.5 text-[14px] font-medium text-slate-900">
                            Поиск в базе резюме <IosHint text={SEARCH_HINT} />
                        </div>
                        <IosSegmented value={enabled} onChange={setEnabled} size="sm" ariaLabel="Поиск в базе резюме"
                                      options={[{ value: 'off', label: 'Выключен' }, { value: 'on', label: 'Включён' }]} />
                    </div>
                    <div className="space-y-1.5">
                        <div className={iosGroupLabel}>Портрет сотрудника</div>
                        <textarea className={`${iosInput} min-h-[132px] resize-y leading-relaxed`} value={portrait} maxLength={3000}
                                  onChange={(event) => setPortrait(event.target.value)}
                                  placeholder="Кого ищем: опыт, навыки, языки, график, район, ожидания по зарплате" />
                    </div>
                    <div className="space-y-1.5">
                        <div className="flex items-end justify-between gap-2">
                            <div className={iosGroupLabel}>Ключевые слова поиска</div>
                            <button type="button" className={iosBtnGhost} disabled={suggesting || portrait.trim().length < 10} onClick={suggest}
                                    title={portrait.trim().length < 10 ? 'Сначала опишите портрет' : 'ИИ подберёт слова по портрету'}>
                                {suggesting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Подобрать с ИИ
                            </button>
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                            {keywords.map((word) => (
                                <span key={word} className="inline-flex items-center gap-1 rounded-lg bg-slate-100 py-1 pl-2.5 pr-1 text-[13px] text-slate-800">
                                    {word}
                                    <button type="button" aria-label={`Убрать «${word}»`} className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                                            onClick={() => setKeywords((list) => list.filter((item) => item !== word))}>
                                        <X size={13} />
                                    </button>
                                </span>
                            ))}
                        </div>
                        <div className="flex gap-2">
                            <input className={iosInput} value={draft} placeholder="Например: оператор колл-центра"
                                   onChange={(event) => setDraft(event.target.value)}
                                   onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); addDraft(); } }} />
                            <button type="button" className={iosBtnSecondary} onClick={addDraft} aria-label="Добавить слово"><Plus size={16} /></button>
                        </div>
                    </div>
                </div>
            )}
        </IosModal>
    );
};

/* ── Вкладка ─────────────────────────────────────────────────────────────── */

const BaseTab = ({ api, toast, boot, caps, onOpenCard, version, onChanged, openId = null, onOpenId }) => {
    const meId = boot?.me?.id;
    const [overview, setOverview] = useState(null);
    const [error, setError] = useState('');
    const [vacancyId, setVacancyId] = useState(readStored);
    const [view, setView] = useState('new');
    const [order, setOrder] = useState('score');
    const [page, setPage] = useState(1);
    const [list, setList] = useState(null);
    const [settings, setSettings] = useState(null);
    const listSeq = useRef(0);

    const loadOverview = useCallback(() => api.get('/hh/base')
        .then(({ data }) => { setOverview(data); setError(''); return data; })
        .catch((requestError) => setError(errorOf(requestError, 'База hh не открылась'))), [api]);
    useEffect(() => { loadOverview(); }, [loadOverview, version]);

    const vacancies = overview?.vacancies || [];
    const shown = useMemo(() => vacancies.filter((item) => item.enabled
        || Object.values(item.counts || {}).some((count) => count > 0)), [vacancies]);
    const current = shown.find((item) => item.hh_vacancy_id === vacancyId) || shown[0] || null;
    const currentId = current?.hh_vacancy_id || null;

    const loadList = useCallback(() => {
        if (!currentId) return Promise.resolve();
        const seq = ++listSeq.current;
        return api.get(`/hh/base/${currentId}/resumes`, { view, order, page })
            .then(({ data }) => { if (seq === listSeq.current) setList(data); })
            .catch((requestError) => { if (seq === listSeq.current) toast(errorOf(requestError, 'Список не загрузился'), 'error'); });
    }, [api, currentId, view, order, page, toast]);
    useEffect(() => { setList(null); }, [currentId, view, order]);
    useEffect(() => { loadList(); }, [loadList, version]);

    // Идёт поиск, ИИ ставит баллы или ждём чьи-то контакты — список и плитки перечитываются сами.
    const busy = Boolean(current?.running || (list?.items || []).some((item) => item.scoring || item.status === 'taking'));
    useEffect(() => {
        if (!busy) return undefined;
        const timer = setInterval(() => {
            if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
            loadOverview();
            loadList();
        }, LIST_POLL_MS);
        return () => clearInterval(timer);
    }, [busy, loadOverview, loadList]);

    const choose = (value) => {
        setVacancyId(value);
        storeVacancy(value);
        setPage(1);
    };
    const runNow = () => {
        if (!current) return;
        api.post(`/hh/search/${current.hh_vacancy_id}/run`)
            .then(({ data }) => { setOverview(data); toast('Ищем на hh — новые резюме появятся через минуту-две', 'success'); })
            .catch((requestError) => toast(errorOf(requestError, 'Поиск не запустился'), 'error'));
    };
    const changed = useCallback(() => { loadOverview(); loadList(); onChanged(); }, [loadOverview, loadList, onChanged]);
    // Резюме открыто по ссылке и оно другой вакансии — список под окном переключается на неё (когда плитки загрузятся:
    // окно по ссылке может открыться раньше них).
    const [linkedVacancy, setLinkedVacancy] = useState(null);
    const resumeLoaded = useCallback((item) => setLinkedVacancy(item?.hh_vacancy_id || null), []);
    useEffect(() => {
        if (!linkedVacancy || !shown.some((item) => item.hh_vacancy_id === linkedVacancy)) return;
        if (linkedVacancy !== currentId) {
            setVacancyId(linkedVacancy);
            storeVacancy(linkedVacancy);
            setPage(1);
        }
        setLinkedVacancy(null);
    }, [linkedVacancy, shown, currentId]);

    if (error && !overview) return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    if (!overview) return <Loading text="Открываем базу hh…" />;

    const counts = current?.counts || {};
    const viewOptions = VIEWS.map((option) => {
        const count = option.value === 'new' ? (counts.new || 0) + (counts.taking || 0) : (counts[option.value] || 0);
        return { value: option.value, label: count ? `${option.label} ${count}` : option.label };
    });
    const items = list?.items || [];
    return (
        <div className="space-y-4">
            {!overview.bridge?.ready && (
                <Notice tone="amber" title="Мост hh на шлюзе ещё не обновлён">Поиск по базе начнёт работать после его обновления</Notice>
            )}
            {overview.bridge?.ready && overview.bridge?.session && overview.bridge.session !== 'ok' && (
                <Notice tone="red" title="Мост hh потерял вход">Поиск и резюме не обновляются, пока владелец не войдёт в hh заново</Notice>
            )}
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {shown.map((item) => (
                    <VacancyTile key={item.hh_vacancy_id} vacancy={item} active={item.hh_vacancy_id === currentId}
                                 onClick={() => choose(item.hh_vacancy_id)} />
                ))}
                {caps.manage && (
                    <button type="button" onClick={() => setSettings('pick')}
                            className="flex items-center gap-2.5 rounded-2xl border border-dashed border-slate-300 px-4 py-3 text-left text-[13.5px] font-medium text-slate-600 transition hover:border-blue-300 hover:text-blue-700">
                        <Plus size={16} /> Вакансии для поиска
                    </button>
                )}
            </div>

            {!current ? (
                <Empty>{caps.manage ? 'Выберите вакансию, опишите портрет сотрудника — и поиск по базе hh начнётся'
                    : 'Поиск по базе hh ещё не включён — его включает руководитель рекрутинга'}</Empty>
            ) : (
                <>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                            <IosSegmented value={view} onChange={(value) => { setView(value); setPage(1); }} options={viewOptions} ariaLabel="Кандидаты" />
                            <IosSegmented value={order} onChange={(value) => { setOrder(value); setPage(1); }} options={ORDERS} size="sm" ariaLabel="Порядок" />
                        </div>
                        <div className="flex flex-wrap items-center justify-end gap-2">
                            <span className="text-[12.5px] text-slate-500">{hhBaseSearchLine(current)}</span>
                            {current.enabled && (
                                <button type="button" className={iosBtnSecondary} disabled={!current.can_run_now} onClick={runNow}
                                        title={current.can_run_now ? 'Найти новые резюме сейчас' : 'Поиск идёт или был только что'}>
                                    <RefreshCw size={15} className={current.running ? 'animate-spin' : ''} /> Искать сейчас
                                </button>
                            )}
                            {caps.manage && (
                                <button type="button" className={iosBtnGhost} onClick={() => setSettings(current)}>
                                    <Settings2 size={15} /> Настроить
                                </button>
                            )}
                        </div>
                    </div>
                    {list === null ? <Loading text="Загружаем кандидатов…" /> : items.length === 0 ? (
                        <Empty>{view === 'new'
                            ? (current.running ? 'Ищем на hh — первые резюме появятся через минуту-две' : 'Новых кандидатов нет')
                            : view === 'taken' ? 'Из базы ещё никого не взяли' : 'Пусто'}</Empty>
                    ) : (
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {items.map((item) => <ResumeCard key={item.id} item={item} meId={meId} onOpen={onOpenId} />)}
                        </div>
                    )}
                    {list && list.pages > 1 && (
                        <IosPager page={list.page} pageCount={list.pages} total={list.total}
                                  from={(list.page - 1) * PAGE_SIZE + 1} to={Math.min(list.total, list.page * PAGE_SIZE)}
                                  onPage={setPage} unit="резюме" />
                    )}
                </>
            )}

            <ResumeSheet api={api} toast={toast} id={openId} meId={meId} caps={caps} onClose={() => onOpenId(null)}
                         onChanged={changed} onOpenCard={onOpenCard} onLoaded={resumeLoaded} />
            <SearchSettings api={api} toast={toast} target={settings} vacancies={vacancies} onClose={() => setSettings(null)}
                            onSaved={(data, savedId) => { setOverview(data); setSettings(null); choose(savedId); loadList(); }} />
        </div>
    );
};

export default BaseTab;
