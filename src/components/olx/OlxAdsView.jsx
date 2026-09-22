import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, Check, ChevronDown, ChevronRight, ExternalLink, History, Loader2,
    RefreshCw, RotateCcw, Search, Send, Sparkles, Trash2, X,
} from 'lucide-react';
import {
    APPLE_FONT, IosBadge, IosModal, IosPager, IosSegmented, IosToggle, iosBtnGhost,
    iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    LIMITS, ORIGIN_LABEL, RESULT_LABEL, RESULT_TONE, SOURCE_LABEL, directionLabel,
    excerpt, fmtAgo, fmtDate, fmtDateTime, plural, statusLabel, statusTone, toPlain,
} from './olxAdsMeta';

/*
 * Раздел «Объявления OLX» — тексты объявлений девяти кабинетов (задача #299).
 *
 * Как устроена работа, ради которой раздел открывают. Раз в месяц у маркетинга
 * новый оффер, и его надо разнести по ~280 объявлениям. Руками это неделя:
 * зайти в кабинет, открыть объявление, заменить два поля, сохранить — и так
 * сотни раз. Здесь то же самое укладывается в три шага на ОДНОМ экране:
 *
 *   1. БРИФ     — один раз вписать, что предлагаем в этом месяце;
 *   2. ИИ       — отметить объявления и нажать «Сочинить»: модель пишет СВОЙ
 *                 текст каждому (город, направление, парк), а не один на всех;
 *   3. ПРОВЕРКА — пролистать черновики, поправить руками, что не понравилось,
 *                 и нажать «Опубликовать».
 *
 * История правок обязательна (решение владельца 14.09.2026), поэтому у каждой
 * записи в OLX есть строка «кто, когда, что было и что стало», и из неё же
 * делается откат.
 *
 * Про права. Сочинять и править черновики может весь раздел, а публиковать в
 * OLX — только админ и главы отделов. Кнопки, которых человеку нельзя, не
 * рисуются вовсе, а не показываются серыми: серая кнопка без объяснения — шум.
 *
 * Про цвет. Красится только отклонение: отказ площадки, истёкшее объявление,
 * текст, не проходящий правила. Всё штатное остаётся нейтральным.
 */

const PAGE_SIZE = 50;
const CHECK_DEBOUNCE_MS = 350;

const TABS = [
    { value: 'adverts', label: 'Объявления' },
    { value: 'brief', label: 'Бриф месяца' },
    { value: 'history', label: 'История' },
];

/* ── Описание: HTML ⇄ текст для редактирования ─────────────────────────────
   В OLX описание — HTML из пяти тегов, но заставлять маркетолога править
   <p> и <li> руками нельзя. В поле он видит абзацы, а пункты списка — строками
   с «•». Обратно каждый абзац становится <p>, а подряд идущие «•» — одним <ul>.
   Этого хватает: живьём по 284 объявлениям встречаются только p, ul и li. */
const htmlToEditable = (html) => {
    if (!html) return '';
    return String(html)
        .replace(/<\s*li[^>]*>/gi, '\n• ')
        .replace(/<\s*\/\s*(p|li|ul)\s*>/gi, '\n')
        .replace(/<\s*br\s*\/?\s*>/gi, '\n')
        .replace(/<[^>]+>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .join('\n');
};

const escapeHtml = (text) => String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

const editableToHtml = (text) => {
    const lines = String(text || '').split('\n').map((line) => line.trim()).filter(Boolean);
    const out = [];
    let list = [];
    const flush = () => {
        if (list.length) {
            out.push(`<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`);
            list = [];
        }
    };
    lines.forEach((line) => {
        const bullet = line.match(/^[•\-–—*]\s+(.*)$/);
        if (bullet) {
            list.push(bullet[1]);
        } else {
            flush();
            out.push(`<p>${escapeHtml(line)}</p>`);
        }
    });
    flush();
    return out.join('');
};

const keyOf = (item) => `${item.cabinet_code}:${item.advert_id}`;

/* ── Корень раздела ─────────────────────────────────────────────────────── */

const OlxAdsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* showToast приходит новой функцией на каждый рендер родителя, поэтому в
       зависимости эффектов он не идёт — иначе данные перезапрашивались бы на
       каждый чужой рендер. Держим его в ref. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((text, kind) => {
        if (toastRef.current) toastRef.current(text, kind);
    }, []);

    const [ping, setPing] = useState(null);
    const [pingFailed, setPingFailed] = useState(false);
    const [tab, setTab] = useState('adverts');
    const [syncing, setSyncing] = useState(false);
    const [listVersion, setListVersion] = useState(0);

    const loadPing = useCallback(() => (
        axios.get(`${apiBaseUrl}/api/olx_ads/ping`, { headers: headers() })
            .then((response) => { setPing(response.data); setPingFailed(false); return response.data; })
            .catch(() => { setPingFailed(true); return null; })
    ), [apiBaseUrl, headers]);

    const sync = useCallback((silent = false) => {
        setSyncing(true);
        return axios.post(`${apiBaseUrl}/api/olx_ads/sync`, {}, { headers: headers() })
            .then((response) => {
                const data = response.data || {};
                if (!silent) {
                    const errors = data.errors || [];
                    if (errors.length) {
                        toast(`Обновлено, но ${errors.length} ${plural(errors.length, 'кабинет', 'кабинета', 'кабинетов')} не ответили`, 'warning');
                    } else {
                        toast(`Обновлено: ${data.total || 0} ${plural(data.total || 0, 'объявление', 'объявления', 'объявлений')}`, 'success');
                    }
                }
                setListVersion((v) => v + 1);
                return loadPing();
            })
            .catch((error) => {
                toast(error.response?.data?.error || 'Не удалось обновить объявления из OLX', 'error');
            })
            .finally(() => setSyncing(false));
    }, [apiBaseUrl, headers, loadPing, toast]);

    /* Первый вход: если снимок пустой или старше 15 минут — перечитываем сами.
       Человек пришёл править объявления, и показывать ему вчерашний список,
       чтобы он первым делом нажал «Обновить», — лишний шаг. */
    const bootstrapped = useRef(false);
    useEffect(() => {
        loadPing().then((data) => {
            if (bootstrapped.current || !data) return;
            bootstrapped.current = true;
            if (data.schema_ready && data.snapshot_stale) sync(true);
        });
    }, [loadPing, sync]);

    const refreshAll = useCallback(() => {
        setListVersion((v) => v + 1);
        loadPing();
    }, [loadPing]);

    if (pingFailed && !ping) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-6">
                <div className={`${iosCard} p-6 text-[13.5px] text-slate-600`}>
                    Раздел сейчас недоступен. Обновите страницу через минуту.
                </div>
            </div>
        );
    }
    if (ping && ping.schema_ready === false) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-6">
                <div className={`${iosCard} p-6 text-[13.5px] text-slate-600`}>
                    Раздел ещё разворачивается. Обновите страницу через минуту.
                </div>
            </div>
        );
    }

    const caps = ping?.capabilities || {};
    const facets = ping?.facets || {};
    /* У каждого кабинета может быть свой бриф: карта «кабинет → действующий
       бриф». Кабинета без брифа в ней просто нет. */
    const briefsByCabinet = ping?.briefs_by_cabinet || {};
    const limits = { ...LIMITS, ...(ping?.limits || {}) };
    const activeTotal = (facets.cabinets || []).reduce((sum, c) => sum + (Number(c.active) || 0), 0);

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="mx-auto w-full max-w-[1400px] space-y-4 p-4 sm:p-6">
            <header className="flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Объявления OLX</h1>
                    <p className="mt-0.5 text-[13px] text-slate-500 tabular-nums">
                        {activeTotal} {plural(activeTotal, 'активное', 'активных', 'активных')}
                        {' · '}обновлено {fmtAgo(facets.synced_at)}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        onClick={() => sync(false)}
                        disabled={syncing}
                    >
                        <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
                        {syncing ? 'Обновляю…' : 'Обновить из OLX'}
                    </button>
                </div>
            </header>

            <IosSegmented value={tab} options={TABS} onChange={setTab} ariaLabel="Разделы" />

            {tab === 'adverts' && (
                <AdvertsPanel
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    toast={toast}
                    caps={caps}
                    facets={facets}
                    cabinets={ping?.cabinets || []}
                    briefsByCabinet={briefsByCabinet}
                    limits={limits}
                    version={listVersion}
                    onChanged={refreshAll}
                    onOpenBrief={() => setTab('brief')}
                    syncing={syncing}
                />
            )}
            {tab === 'brief' && (
                <BriefPanel
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    toast={toast}
                    caps={caps}
                    cabinets={ping?.cabinets || []}
                    briefsByCabinet={briefsByCabinet}
                    onChanged={loadPing}
                />
            )}
            {tab === 'history' && (
                <HistoryPanel
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    toast={toast}
                    caps={caps}
                    cabinets={ping?.cabinets || []}
                    onChanged={refreshAll}
                />
            )}
        </div>
    );
};

/* ── Список объявлений ──────────────────────────────────────────────────── */

const AdvertsPanel = ({
    apiBaseUrl, headers, toast, caps, facets, cabinets, briefsByCabinet, limits,
    version, onChanged, onOpenBrief, syncing,
}) => {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [page, setPage] = useState(1);

    const [cabinet, setCabinet] = useState('');
    const [cityId, setCityId] = useState('');
    const [categoryId, setCategoryId] = useState('');
    const [status, setStatus] = useState('active');
    const [onlyDrafts, setOnlyDrafts] = useState(false);
    const [searchDraft, setSearchDraft] = useState('');
    const [search, setSearch] = useState('');

    const [selected, setSelected] = useState(() => new Map());
    const [editing, setEditing] = useState(null);
    /* Пачку пишет фоновый прогон на сервере: экран лишь ставит его и опрашивает
       ход. Так обновление страницы не запускает вторую пачку (22.09.2026 две
       пачки по 32 шли параллельно), а прогресс виден любому, кто открыл раздел. */
    const [job, setJob] = useState(null);
    const [starting, setStarting] = useState(false);
    const generating = starting || Boolean(job && job.status === 'running');
    const [confirmApply, setConfirmApply] = useState(false);
    const [applying, setApplying] = useState(false);
    const [instruction, setInstruction] = useState('');
    const [showInstruction, setShowInstruction] = useState(false);

    /* Поиск с задержкой: запрос уходит, когда человек перестал печатать.
       Таймер держим в ref, а колбэк не пересоздаём — нестабильная функция в
       зависимостях сбрасывала бы задержку на каждый рендер. */
    const searchTimer = useRef(null);
    const onSearchInput = (value) => {
        setSearchDraft(value);
        if (searchTimer.current) clearTimeout(searchTimer.current);
        searchTimer.current = setTimeout(() => { setSearch(value.trim()); setPage(1); }, 300);
    };
    useEffect(() => () => { if (searchTimer.current) clearTimeout(searchTimer.current); }, []);

    const load = useCallback(() => {
        setLoading(true);
        const params = {
            limit: PAGE_SIZE,
            offset: (page - 1) * PAGE_SIZE,
            cabinet: cabinet || undefined,
            city_id: cityId || undefined,
            category_id: categoryId || undefined,
            status: status || undefined,
            search: search || undefined,
            has_draft: onlyDrafts ? '1' : undefined,
        };
        return axios.get(`${apiBaseUrl}/api/olx_ads/adverts`, { headers: headers(), params })
            .then((response) => {
                setItems(response.data?.items || []);
                setTotal(response.data?.total || 0);
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось загрузить объявления', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, toast, page, cabinet, cityId, categoryId, status, search, onlyDrafts]);

    useEffect(() => { load(); }, [load, version]);

    /* При открытии раздела подключаемся к идущему прогону, если он есть. */
    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/olx_ads/generate/active`, { headers: headers() })
            .then((response) => {
                const active = response.data?.job;
                if (active && active.status === 'running') setJob(active);
            })
            .catch(() => {});
    }, [apiBaseUrl, headers]);

    /* Итог прогона: приклеить черновики к выбору (отмеченные на ДРУГИХ страницах
       списка перечитывание не обновит), сказать словами, перечитать список. */
    const finishJob = (snapshot) => {
        if (snapshot.status === 'lost') {
            /* Сервер перезапустился и прогон потерял: сказано тостом выше,
               остаётся перечитать список — написанное до перезапуска в базе. */
            onChanged();
            return;
        }
        const result = snapshot.result || {};
        const madeItems = result.made || [];
        const failedItems = result.failed || [];
        setSelected((prev) => {
            const next = new Map(prev);
            madeItems.forEach((draft) => {
                const key = `${draft.cabinet}:${draft.advert_id}`;
                const current = next.get(key);
                if (current) {
                    next.set(key, {
                        ...current,
                        draft_id: draft.draft_id,
                        draft_title: draft.title,
                        draft_description: draft.description,
                    });
                }
            });
            return next;
        });
        const made = madeItems.length;
        const failed = failedItems.length;
        const skipped = (result.skipped || []).length;
        /* Причину показываем, а не только счёт: «не получилось 32» без слов —
           это то, с чем человек 22.09.2026 пришёл в IT, хотя сервер причину
           каждый раз отдавал. */
        const reason = failedItems.find((item) => item.error)?.error;
        if (snapshot.status === 'error') {
            toast(snapshot.error || 'ИИ сейчас недоступен', 'error');
        } else if (snapshot.status === 'stopped') {
            toast(`Остановлено: написано ${made}, не дошло до ${skipped}`, 'warning');
        } else if (failed && !made) {
            toast(`ИИ не справился ни с одним из ${failed}${reason ? `: ${reason}` : ''}`, 'error');
        } else if (failed) {
            toast(`Готово ${made}, не получилось ${failed}${reason ? ` (${reason})` : ''}`, 'warning');
        } else {
            toast(`ИИ написал ${made} ${plural(made, 'текст', 'текста', 'текстов')} — проверьте и опубликуйте`, 'success');
        }
        setShowInstruction(false);
        setInstruction('');
        onChanged();
    };
    const finishRef = useRef(finishJob);
    finishRef.current = finishJob;
    const finishedIdRef = useRef(null);

    /* Опрос хода раз в полторы секунды, пока прогон идёт. toast стабилен,
       onChanged читается через finishRef — в зависимости их не кладём, иначе
       опрос перезапускался бы на каждый рендер родителя. */
    const jobId = job?.id;
    const jobRunning = Boolean(job && job.status === 'running');
    useEffect(() => {
        if (!jobId || !jobRunning) return undefined;
        let cancelled = false;
        const tick = () => {
            axios.get(`${apiBaseUrl}/api/olx_ads/generate/jobs/${jobId}`, { headers: headers() })
                .then((response) => {
                    if (cancelled) return;
                    const next = response.data?.job;
                    if (!next) return;
                    if (next.status !== 'running') {
                        if (finishedIdRef.current === next.id) return;
                        finishedIdRef.current = next.id;
                        setJob(null);
                        finishRef.current(next);
                        return;
                    }
                    setJob(next);
                })
                .catch((error) => {
                    /* Сеть моргнула — следующий тик повторит. 404 — сервер
                       перезапустился и прогон потерял; написанное в базе цело. */
                    if (cancelled || !error.response) return;
                    if (error.response.status === 404) {
                        setJob(null);
                        toast(error.response.data?.error || 'Прогон прерван — обновите список', 'error');
                        finishRef.current({ status: 'lost', result: {} });
                    }
                });
        };
        tick();
        const timer = setInterval(tick, 1500);
        return () => { cancelled = true; clearInterval(timer); };
    }, [apiBaseUrl, headers, jobId, jobRunning]);

    const resetPage = (setter) => (value) => { setter(value); setPage(1); };

    const cabinetTitle = useMemo(() => {
        const map = {};
        cabinets.forEach((c) => { map[c.code] = c.title || c.code; });
        return map;
    }, [cabinets]);

    const cabinetOptions = useMemo(() => ([
        { value: '', label: 'Все кабинеты' },
        ...(facets.cabinets || []).map((c) => ({
            value: c.cabinet_code,
            label: `${cabinetTitle[c.cabinet_code] || c.cabinet_code} · ${c.active}`,
        })),
    ]), [facets.cabinets, cabinetTitle]);

    const cityOptions = useMemo(() => ([
        { value: '', label: 'Все города' },
        ...(facets.cities || []).map((c) => ({
            value: String(c.city_id), label: `${c.city_name || c.city_id} · ${c.total}`,
        })),
    ]), [facets.cities]);

    const categoryOptions = useMemo(() => ([
        { value: '', label: 'Все направления' },
        ...(facets.categories || []).map((c) => ({
            value: String(c.category_id),
            label: `${directionLabel(c.category_id, c.category_name)} · ${c.total}`,
        })),
    ]), [facets.categories]);

    const statusOptions = [
        { value: 'active', label: 'Активные' },
        { value: '', label: 'Все статусы' },
        ...(facets.statuses || [])
            .filter((s) => s.status && s.status !== 'active')
            .map((s) => ({ value: s.status, label: `${statusLabel(s.status)} · ${s.total}` })),
    ];

    /* Выбор живёт по ключу «кабинет:id», а не по индексу строки: страница
       меняется, фильтр меняется, а отмеченное должно оставаться отмеченным. */
    const toggle = (item) => {
        setSelected((prev) => {
            const next = new Map(prev);
            const key = keyOf(item);
            if (next.has(key)) next.delete(key); else next.set(key, item);
            return next;
        });
    };
    const pageKeys = items.map(keyOf);
    const allOnPage = items.length > 0 && pageKeys.every((key) => selected.has(key));
    const togglePage = () => {
        setSelected((prev) => {
            const next = new Map(prev);
            if (allOnPage) {
                pageKeys.forEach((key) => next.delete(key));
            } else {
                items.forEach((item) => next.set(keyOf(item), item));
            }
            return next;
        });
    };
    const clearSelection = () => setSelected(new Map());

    const selectedList = useMemo(() => Array.from(selected.values()), [selected]);
    const selectedWithDraft = selectedList.filter((item) => item.draft_id);
    /* Бриф у каждого кабинета свой: отмеченные объявления кабинетов без брифа
       ИИ пропустит, и человек должен знать это ДО нажатия, а не после. */
    const selectedWithoutBrief = selectedList.filter((item) => !briefsByCabinet[item.cabinet_code]);
    const targets = (list) => list.map((item) => ({
        cabinet: item.cabinet_code, advert_id: item.advert_id,
    }));

    /* Выбор хранит объект строки на момент отметки. После генерации, публикации
       или сброса черновиков список перечитывается — и в выбранном должен
       оказаться СВЕЖИЙ объект, иначе «Опубликовать N» смотрит на старые строки
       без черновика и не появляется вовсе (поймано прогоном в браузере
       14.09.2026). Обновляем только уже отмеченные: снятый выбор не возвращаем. */
    useEffect(() => {
        setSelected((prev) => {
            if (!prev.size) return prev;
            let changed = false;
            const next = new Map(prev);
            items.forEach((item) => {
                const key = keyOf(item);
                if (next.has(key) && next.get(key) !== item) {
                    next.set(key, item);
                    changed = true;
                }
            });
            return changed ? next : prev;
        });
    }, [items]);

    const generate = () => {
        /* Бриф у каждого кабинета свой. Если ни у одного кабинета выбранных
           объявлений брифа нет — ИИ не из чего писать, ведём к брифам. Если нет
           только у части — сервер пропустит их и скажет об этом сам. */
        if (!selectedList.some((item) => briefsByCabinet[item.cabinet_code])) {
            toast('У кабинетов выбранных объявлений нет брифа — задайте его во вкладке «Бриф месяца»', 'warning');
            onOpenBrief();
            return;
        }
        setStarting(true);
        axios.post(`${apiBaseUrl}/api/olx_ads/generate/start`, {
            targets: targets(selectedList),
            instruction: instruction.trim() || undefined,
        }, { headers: headers() })
            .then((response) => { setJob(response.data?.job || null); })
            .catch((error) => {
                const data = error.response?.data;
                if (error.response?.status === 409 && data?.job) {
                    /* Пачку уже пишут (или это мы сами до обновления страницы):
                       не вторая пачка, а подключение к идущей. */
                    setJob(data.job);
                    toast('ИИ уже пишет пачку — показываю её ход', 'warning');
                    return;
                }
                toast(data?.error || (error.response ? 'ИИ сейчас недоступен' : 'Сервер не ответил — попробуйте ещё раз'), 'error');
            })
            .finally(() => setStarting(false));
    };

    const stopJob = () => {
        if (!job) return;
        axios.post(`${apiBaseUrl}/api/olx_ads/generate/jobs/${job.id}/stop`, {}, { headers: headers() })
            .then((response) => {
                if (response.data?.job) setJob(response.data.job);
                toast('Остановлю после текущего объявления — написанное останется', 'warning');
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось остановить', 'error'));
    };

    const discard = () => {
        axios.post(`${apiBaseUrl}/api/olx_ads/drafts/discard`, {
            targets: targets(selectedWithDraft),
        }, { headers: headers() })
            .then((response) => {
                const n = response.data?.discarded || 0;
                toast(`Убрано ${n} ${plural(n, 'черновик', 'черновика', 'черновиков')}`, 'success');
                onChanged();
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось убрать черновики', 'error'));
    };

    const apply = () => {
        setApplying(true);
        axios.post(`${apiBaseUrl}/api/olx_ads/apply`, {
            targets: targets(selectedWithDraft),
            origin: 'ai',
        }, { headers: headers(), timeout: 0 })
            .then((response) => {
                const data = response.data || {};
                if (data.stopped) {
                    toast(data.stopped, 'error');
                } else if (data.failed) {
                    toast(`Опубликовано ${data.applied}, не приняты ${data.failed} — причины в «Истории»`, 'warning');
                } else {
                    toast(`Опубликовано ${data.applied} ${plural(data.applied, 'объявление', 'объявления', 'объявлений')}`, 'success');
                }
                setConfirmApply(false);
                clearSelection();
                onChanged();
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось опубликовать', 'error'))
            .finally(() => setApplying(false));
    };

    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const draftsTotal = facets.drafts || 0;
    const selectedCabinets = new Set(selectedWithDraft.map((item) => item.cabinet_code));

    return (
        <div className="space-y-3">
            {Object.keys(briefsByCabinet).length === 0 && caps.can_write_content && (
                <button
                    type="button"
                    onClick={onOpenBrief}
                    className={`${iosCard} flex w-full items-center gap-3 p-4 text-left transition hover:bg-slate-50`}
                >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                        <Sparkles className="h-[18px] w-[18px]" />
                    </span>
                    <span className="min-w-0 flex-1">
                        <span className="block text-[14px] font-semibold text-slate-900">Начните с брифа месяца</span>
                        <span className="block text-[12.5px] text-slate-500">
                            Впишите оффер, бонус и акции — и ИИ напишет свой текст каждому объявлению
                        </span>
                    </span>
                    <ChevronRight className="h-4 w-4 text-slate-400" />
                </button>
            )}

            <div className={`${iosCard} space-y-3 p-3 sm:p-4`}>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} pl-9`}
                        placeholder="Поиск по тексту или номеру объявления"
                        value={searchDraft}
                        onChange={(e) => onSearchInput(e.target.value)}
                    />
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <CustomSelect variant="ios" value={cabinet} onChange={resetPage(setCabinet)} options={cabinetOptions} ariaLabel="Кабинет" />
                    <CustomSelect variant="ios" value={categoryId} onChange={resetPage(setCategoryId)} options={categoryOptions} ariaLabel="Направление" />
                    <CustomSelect variant="ios" value={cityId} onChange={resetPage(setCityId)} options={cityOptions} ariaLabel="Город" searchable />
                    <CustomSelect variant="ios" value={status} onChange={resetPage(setStatus)} options={statusOptions} ariaLabel="Статус" />
                </div>
                <label className="flex cursor-pointer items-center justify-between gap-3 px-1">
                    <span className="text-[13px] text-slate-600 tabular-nums">
                        Только с готовым текстом
                        {draftsTotal > 0 && <span className="text-slate-400"> · {draftsTotal}</span>}
                    </span>
                    <IosToggle checked={onlyDrafts} onChange={resetPage(setOnlyDrafts)} />
                </label>
            </div>

            {job && job.status === 'running' && (
                <div data-ai-progress className={`${iosCard} space-y-2.5 p-3 sm:p-4`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2 text-[13.5px] text-slate-700 tabular-nums">
                            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-600" />
                            <span className="shrink-0">ИИ пишет <b>{job.done}</b> из <b>{job.total}</b></span>
                            {job.current && (
                                <span className="truncate text-slate-500">
                                    · {job.current.city_name ? `${job.current.city_name} · ` : ''}{job.current.title}
                                </span>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            {job.actor_name && <span className="text-[12.5px] text-slate-400">{job.actor_name}</span>}
                            {caps.can_write_content && (
                                <button type="button" className={iosBtnGhost} onClick={stopJob} disabled={job.stop_requested}>
                                    <X className="h-4 w-4" /> {job.stop_requested ? 'Останавливаю…' : 'Остановить'}
                                </button>
                            )}
                        </div>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div
                            className="h-full rounded-full bg-blue-500 transition-[width] duration-500"
                            style={{ width: `${job.total ? Math.round((job.done / job.total) * 100) : 0}%` }}
                        />
                    </div>
                </div>
            )}

            {selected.size > 0 && (
                <div className={`${iosCard} mobile-sticky-top sticky top-2 z-20 space-y-3 p-3 sm:p-4`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-[13.5px] text-slate-700 tabular-nums">
                            Выбрано <b>{selected.size}</b>
                            {selectedWithDraft.length > 0 && (
                                <span className="text-slate-500"> · с текстом {selectedWithDraft.length}</span>
                            )}
                        </div>
                        <button type="button" className={iosBtnGhost} onClick={clearSelection}>
                            <X className="h-4 w-4" /> Снять выбор
                        </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {caps.can_write_content && (
                            <button type="button" className={iosBtnPrimary} onClick={generate} disabled={generating}>
                                {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                                {generating ? 'ИИ пишет…' : 'Сочинить тексты ИИ'}
                            </button>
                        )}
                        {caps.can_write_content && (
                            <button
                                type="button"
                                className={iosBtnSecondary}
                                onClick={() => {
                                    /* «Без указания» должно значить без указания: прячем
                                       поле — стираем текст, иначе он молча уйдёт со
                                       следующей пачкой. */
                                    if (showInstruction) setInstruction('');
                                    setShowInstruction(!showInstruction);
                                }}
                            >
                                {showInstruction ? 'Без указания' : 'С указанием для ИИ'}
                            </button>
                        )}
                        {caps.can_apply && selectedWithDraft.length > 0 && (
                            <button type="button" className={iosBtnSecondary} onClick={() => setConfirmApply(true)}>
                                <Send className="h-4 w-4" /> Опубликовать {selectedWithDraft.length}
                            </button>
                        )}
                        {caps.can_write_content && selectedWithDraft.length > 0 && (
                            <button type="button" className={iosBtnGhost} onClick={discard}>
                                <Trash2 className="h-4 w-4" /> Убрать черновики
                            </button>
                        )}
                    </div>
                    {selectedWithoutBrief.length > 0 && caps.can_write_content && (
                        <p data-no-brief-note className="px-1 text-[12.5px] text-amber-700">
                            {selectedWithoutBrief.length === 1
                                ? 'У одного из выбранных объявлений нет брифа для его кабинета — ИИ его пропустит'
                                : `У ${selectedWithoutBrief.length} из выбранных объявлений нет брифа для их кабинетов — ИИ их пропустит`}
                        </p>
                    )}
                    {showInstruction && (
                        <textarea
                            className={`${iosInput} min-h-[72px]`}
                            placeholder="Например: короче и бодрее, упомяни выплаты каждый день, без капса в подзаголовках"
                            value={instruction}
                            onChange={(e) => setInstruction(e.target.value)}
                        />
                    )}
                </div>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-2.5">
                    <input
                        type="checkbox"
                        className="h-4 w-4 accent-blue-600"
                        checked={allOnPage}
                        onChange={togglePage}
                        aria-label="Выбрать все на странице"
                    />
                    <span className={iosGroupLabel}>
                        {/* «Загрузка» только пока показывать нечего: при фоновом
                            перечитывании счётчик не должен мигать над видимым списком. */}
                        {(loading || syncing) && !items.length
                            ? 'Загрузка…'
                            : `${total} ${plural(total, 'объявление', 'объявления', 'объявлений')}`}
                    </span>
                </div>

                {!loading && items.length === 0 && (
                    <div className="px-4 py-12 text-center text-[13.5px] text-slate-500">
                        {syncing ? 'Читаю объявления из OLX…' : 'Под этот фильтр объявлений нет'}
                    </div>
                )}

                <ul className="divide-y divide-slate-100">
                    {items.map((item) => (
                        <AdvertRow
                            key={keyOf(item)}
                            item={item}
                            cabinetTitle={cabinetTitle[item.cabinet_code] || item.cabinet_code}
                            checked={selected.has(keyOf(item))}
                            onToggle={() => toggle(item)}
                            onOpen={() => setEditing(item)}
                        />
                    ))}
                </ul>

                <div className="px-3 py-2">
                    <IosPager
                        page={page}
                        pageCount={pageCount}
                        total={total}
                        from={(page - 1) * PAGE_SIZE + 1}
                        to={Math.min(page * PAGE_SIZE, total)}
                        onPage={setPage}
                        unit="объявления"
                    />
                </div>
            </div>

            {editing && (
                <AdvertEditor
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    toast={toast}
                    caps={caps}
                    limits={limits}
                    brief={briefsByCabinet[editing.cabinet_code] || null}
                    target={editing}
                    cabinetTitle={cabinetTitle[editing.cabinet_code] || editing.cabinet_code}
                    onClose={() => setEditing(null)}
                    onChanged={onChanged}
                />
            )}

            <IosModal
                open={confirmApply}
                onClose={() => (applying ? null : setConfirmApply(false))}
                title="Опубликовать в OLX?"
                subtitle={`${selectedWithDraft.length} ${plural(selectedWithDraft.length, 'объявление', 'объявления', 'объявлений')} в ${selectedCabinets.size} ${plural(selectedCabinets.size, 'кабинете', 'кабинетах', 'кабинетах')}`}
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={() => setConfirmApply(false)} disabled={applying}>
                            Отмена
                        </button>
                        <button type="button" className={iosBtnPrimary} onClick={apply} disabled={applying}>
                            {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                            {applying ? 'Публикую…' : 'Опубликовать'}
                        </button>
                    </div>
                )}
            >
                <div className="space-y-2 text-[13.5px] leading-relaxed text-slate-600">
                    <p>
                        Текст заменится сразу в живых объявлениях. Город, телефон, категория
                        и автопродление не меняются.
                    </p>
                    <p>
                        Каждая правка попадёт в «Историю» — оттуда любую можно откатить к
                        прежнему тексту.
                    </p>
                    {selected.size > selectedWithDraft.length && (
                        <p className="text-slate-500">
                            {selected.size - selectedWithDraft.length} из выбранных без готового текста — их не трогаем.
                        </p>
                    )}
                </div>
            </IosModal>
        </div>
    );
};

const AdvertRow = ({ item, cabinetTitle, checked, onToggle, onOpen }) => {
    const hasDraft = Boolean(item.draft_id);
    const failed = item.last_change_result === 'failed';
    return (
        <li className={`flex items-start gap-3 px-4 py-3 transition ${checked ? 'bg-blue-50/50' : 'hover:bg-slate-50'}`}>
            <input
                type="checkbox"
                className="mt-1 h-4 w-4 shrink-0 accent-blue-600"
                checked={checked}
                onChange={onToggle}
                aria-label="Выбрать объявление"
            />
            <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500">
                    <span className="font-medium text-slate-600">{cabinetTitle}</span>
                    <span>·</span>
                    <span>{item.city_name || '—'}</span>
                    <span>·</span>
                    <span>{directionLabel(item.category_id, item.category_name)}</span>
                    {item.status !== 'active' && (
                        <IosBadge tone={statusTone(item.status)}>{statusLabel(item.status)}</IosBadge>
                    )}
                    {hasDraft && (
                        <IosBadge tone="blue">
                            <Sparkles className="mr-1 inline h-3 w-3" />
                            Новый текст готов
                        </IosBadge>
                    )}
                    {failed && <IosBadge tone="red">Не принято OLX</IosBadge>}
                </div>
                <div className="mt-1 truncate text-[14px] font-medium text-slate-900">
                    {hasDraft ? item.draft_title : item.title}
                </div>
                <div className="mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-slate-500">
                    {excerpt(hasDraft ? item.draft_description : item.description, 220)}
                </div>
                {hasDraft && (
                    <div className="mt-1 truncate text-[12px] text-slate-400 line-through decoration-slate-300">
                        {item.title}
                    </div>
                )}
            </button>
        </li>
    );
};

/* ── Карточка объявления: правка, ИИ, публикация, история ──────────────── */

const Counter = ({ value, min, max }) => {
    const bad = value > max || (value > 0 && value < min);
    return (
        <span className={`tabular-nums text-[12px] ${bad ? 'font-semibold text-rose-600' : 'text-slate-400'}`}>
            {value} / {max}
        </span>
    );
};

const AdvertEditor = ({
    apiBaseUrl, headers, toast, caps, limits, brief: activeBrief, target, cabinetTitle,
    onClose, onChanged,
}) => {
    const [card, setCard] = useState(null);
    const [title, setTitle] = useState('');
    const [body, setBody] = useState('');
    const [dirty, setDirty] = useState(false);
    const [check, setCheck] = useState(null);
    const [busy, setBusy] = useState('');
    const [instruction, setInstruction] = useState('');
    const [view, setView] = useState('edit');

    const code = target.cabinet_code;
    const advertId = target.advert_id;

    const loadCard = useCallback(() => (
        axios.get(`${apiBaseUrl}/api/olx_ads/adverts/${code}/${advertId}`, { headers: headers() })
            .then((response) => {
                const data = response.data || {};
                setCard(data);
                /* В поле сразу стоит то, что человек, скорее всего, хочет
                   править: черновик, если он есть, иначе текущий текст. */
                const source = data.draft || data.advert || {};
                setTitle(source.title || '');
                setBody(htmlToEditable(source.description || ''));
                setDirty(false);
                return data;
            })
            .catch((error) => {
                toast(error.response?.data?.error || 'Не удалось открыть объявление', 'error');
                onClose();
            })
    ), [apiBaseUrl, headers, code, advertId, toast, onClose]);

    useEffect(() => { loadCard(); }, [loadCard]);

    const advert = card?.advert || null;
    const draft = card?.draft || null;
    const history = card?.history || [];
    const descriptionHtml = useMemo(() => editableToHtml(body), [body]);

    /* Проверка правилами OLX на лету — с задержкой, чтобы не слать запрос на
       каждую букву. Таймер в ref: пересоздание функции сбрасывало бы задержку. */
    const checkTimer = useRef(null);
    useEffect(() => {
        if (!advert) return undefined;
        if (checkTimer.current) clearTimeout(checkTimer.current);
        checkTimer.current = setTimeout(() => {
            axios.post(`${apiBaseUrl}/api/olx_ads/check`, {
                title, description: descriptionHtml, category_id: advert.category_id,
            }, { headers: headers() })
                .then((response) => setCheck(response.data))
                .catch(() => setCheck(null));
        }, CHECK_DEBOUNCE_MS);
        return () => { if (checkTimer.current) clearTimeout(checkTimer.current); };
    }, [apiBaseUrl, headers, title, descriptionHtml, advert]);

    const blocking = check?.blocking || [];
    const warnings = (check?.problems || []).filter((p) => p.level !== 'error');
    const titleLength = title.trim().length;
    const descriptionLength = check?.description_length ?? toPlain(descriptionHtml).length;
    const unchanged = advert
        && title.trim() === (advert.title || '').trim()
        && descriptionHtml === editableToHtml(htmlToEditable(advert.description || ''));

    const rewrite = () => {
        if (!activeBrief) {
            toast('У кабинета этого объявления нет брифа — задайте его во вкладке «Бриф месяца»', 'warning');
            return;
        }
        setBusy('ai');
        axios.post(`${apiBaseUrl}/api/olx_ads/generate`, {
            targets: [{ cabinet: code, advert_id: advertId }],
            instruction: instruction.trim() || undefined,
        }, { headers: headers(), timeout: 0 })
            .then((response) => {
                const made = response.data?.made?.[0];
                if (!made) {
                    const reason = response.data?.failed?.[0]?.error;
                    toast(reason ? `ИИ не справился: ${reason}` : 'ИИ не вернул текст', 'error');
                    return;
                }
                setTitle(made.title || '');
                setBody(htmlToEditable(made.description || ''));
                setDirty(false);
                toast('Готово — посмотрите и при необходимости поправьте', 'success');
                onChanged();
                loadCard();
            })
            .catch((error) => toast(
                error.response
                    ? (error.response.data?.error || 'ИИ сейчас недоступен')
                    : 'Сервер не ответил — попробуйте ещё раз',
                'error',
            ))
            .finally(() => setBusy(''));
    };

    const saveDraft = () => {
        setBusy('save');
        return axios.post(`${apiBaseUrl}/api/olx_ads/drafts/${code}/${advertId}`, {
            title: title.trim(), description: descriptionHtml,
        }, { headers: headers() })
            .then(() => {
                toast('Черновик сохранён', 'success');
                setDirty(false);
                onChanged();
                return loadCard();
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось сохранить', 'error'))
            .finally(() => setBusy(''));
    };

    const publish = () => {
        setBusy('apply');
        axios.post(`${apiBaseUrl}/api/olx_ads/apply`, {
            targets: [{
                cabinet: code, advert_id: advertId,
                title: title.trim(), description: descriptionHtml,
            }],
            origin: draft && !dirty && draft.origin === 'ai' ? 'ai' : 'human',
        }, { headers: headers(), timeout: 0 })
            .then((response) => {
                const result = response.data?.results?.[0];
                if (result?.ok) {
                    toast('Опубликовано в OLX', 'success');
                    onChanged();
                    loadCard();
                } else {
                    toast(result?.error ? `OLX не принял: ${result.error}` : 'OLX не принял текст', 'error');
                    loadCard();
                }
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось опубликовать', 'error'))
            .finally(() => setBusy(''));
    };

    const onTitle = (value) => { setTitle(value); setDirty(true); };
    const onBody = (value) => { setBody(value); setDirty(true); };

    const footer = advert ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
            <a
                href={advert.url}
                target="_blank"
                rel="noreferrer"
                className={iosBtnGhost}
            >
                <ExternalLink className="h-4 w-4" /> На OLX
            </a>
            <div className="flex flex-wrap gap-2">
                {caps.can_write_content && (
                    <button type="button" className={iosBtnSecondary} onClick={saveDraft} disabled={Boolean(busy) || unchanged}>
                        {busy === 'save' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        Сохранить черновик
                    </button>
                )}
                {caps.can_apply && (
                    <button
                        type="button"
                        className={iosBtnPrimary}
                        onClick={publish}
                        disabled={Boolean(busy) || unchanged || blocking.length > 0}
                    >
                        {busy === 'apply' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                        Опубликовать
                    </button>
                )}
            </div>
        </div>
    ) : null;

    return (
        <IosModal
            open
            onClose={() => (busy ? null : onClose())}
            title={advert ? advert.city_name || 'Объявление' : 'Объявление'}
            subtitle={advert ? `${cabinetTitle} · ${directionLabel(advert.category_id, advert.category_name)} · №${advert.advert_id}` : ''}
            maxWidth="max-w-3xl"
            footer={footer}
        >
            {!advert ? (
                <div className="flex items-center justify-center py-16 text-slate-400">
                    <Loader2 className="h-5 w-5 animate-spin" />
                </div>
            ) : (
                <div className="space-y-4">
                    <IosSegmented
                        value={view}
                        onChange={setView}
                        options={[
                            { value: 'edit', label: 'Текст' },
                            { value: 'history', label: `История${history.length ? ` · ${history.length}` : ''}` },
                        ]}
                        size="sm"
                    />

                    {view === 'edit' && (
                        <>
                            {draft && !dirty && (
                                <div className="flex items-start gap-2 rounded-xl bg-blue-50 px-3 py-2.5 text-[12.5px] text-blue-800">
                                    <Sparkles className="mt-0.5 h-4 w-4 shrink-0" />
                                    <span>
                                        В поле — {draft.origin === 'ai' ? 'текст от ИИ' : 'сохранённый черновик'}
                                        {draft.created_by_name ? `, ${draft.created_by_name}` : ''}, {fmtDateTime(draft.updated_at || draft.created_at)}.
                                        В OLX пока висит прежний.
                                    </span>
                                </div>
                            )}

                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between px-1">
                                    <label className={iosGroupLabel} htmlFor="olx-ad-title">Заголовок</label>
                                    <Counter value={titleLength} min={limits.title_min} max={limits.title_max} />
                                </div>
                                <input
                                    id="olx-ad-title"
                                    className={iosInput}
                                    value={title}
                                    onChange={(e) => onTitle(e.target.value)}
                                    disabled={!caps.can_write_content}
                                />
                                {titleLength > limits.title_max && check?.suggested_title && (
                                    <button
                                        type="button"
                                        className="px-1 text-left text-[12.5px] text-blue-600 hover:underline"
                                        onClick={() => onTitle(check.suggested_title)}
                                    >
                                        Сократить по границе слова: «{check.suggested_title}»
                                    </button>
                                )}
                            </div>

                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between px-1">
                                    <label className={iosGroupLabel} htmlFor="olx-ad-body">Описание</label>
                                    <Counter value={descriptionLength} min={limits.description_min} max={limits.description_max} />
                                </div>
                                <textarea
                                    id="olx-ad-body"
                                    className={`${iosInput} min-h-[260px] leading-relaxed`}
                                    value={body}
                                    onChange={(e) => onBody(e.target.value)}
                                    disabled={!caps.can_write_content}
                                />
                                <p className="px-1 text-[12px] text-slate-400">
                                    Каждая строка — абзац. Строка с «•» в начале — пункт списка.
                                </p>
                            </div>

                            {(blocking.length > 0 || warnings.length > 0) && (
                                <ul className="space-y-1.5">
                                    {blocking.map((p) => (
                                        <li key={`${p.field}-${p.code}`} className="flex items-start gap-2 text-[12.5px] text-rose-600">
                                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {p.message}
                                        </li>
                                    ))}
                                    {warnings.map((p) => (
                                        <li key={`${p.field}-${p.code}`} className="flex items-start gap-2 text-[12.5px] text-amber-600">
                                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {p.message}
                                        </li>
                                    ))}
                                </ul>
                            )}

                            {caps.can_write_content && (
                                <div className="space-y-2 rounded-2xl bg-slate-50 p-3">
                                    <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-700">
                                        <Sparkles className="h-4 w-4 text-blue-600" /> Переписать с ИИ
                                    </div>
                                    <textarea
                                        className={`${iosInput} min-h-[56px] bg-white`}
                                        placeholder="Необязательно: что поменять — «короче», «добавь розыгрыш», «без капса»"
                                        value={instruction}
                                        onChange={(e) => setInstruction(e.target.value)}
                                    />
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-[12px] text-slate-400">
                                            {activeBrief ? `По брифу «${activeBrief.title}»` : 'У этого кабинета нет брифа'}
                                        </span>
                                        <button
                                            type="button"
                                            className={iosBtnSecondary}
                                            onClick={rewrite}
                                            disabled={Boolean(busy) || !activeBrief}
                                        >
                                            {busy === 'ai' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                                            {busy === 'ai' ? 'Пишу…' : 'Сочинить'}
                                        </button>
                                    </div>
                                </div>
                            )}

                            <details className="rounded-2xl bg-slate-50 px-3 py-2.5 text-[12.5px] text-slate-500">
                                <summary className="cursor-pointer select-none text-slate-600">Что сейчас в OLX</summary>
                                <div className="mt-2 space-y-1">
                                    <div className="font-medium text-slate-800">{advert.title}</div>
                                    <div className="whitespace-pre-line">{toPlain(advert.description)}</div>
                                    <div className="pt-1 text-slate-400 tabular-nums">
                                        {statusLabel(advert.status)} · до {fmtDate(advert.valid_to)}
                                        {advert.auto_extend ? ' · автопродление' : ''}
                                    </div>
                                </div>
                            </details>
                        </>
                    )}

                    {view === 'history' && (
                        <HistoryList
                            items={history}
                            apiBaseUrl={apiBaseUrl}
                            headers={headers}
                            toast={toast}
                            caps={caps}
                            compact
                            onChanged={() => { onChanged(); loadCard(); }}
                        />
                    )}
                </div>
            )}
        </IosModal>
    );
};

/* ── Бриф месяца ────────────────────────────────────────────────────────── */

const BRIEF_FIELDS = [
    { key: 'offer', label: 'Оффер месяца', hint: 'Главное предложение, ради которого стоит позвонить' },
    { key: 'bonus', label: 'Бонус за подключение', hint: 'Например: 15 000 ₸ за первые 300 поездок' },
    { key: 'promo', label: 'Действующая акция', hint: 'Что действует именно сейчас' },
    { key: 'raffle', label: 'Розыгрыш', hint: 'Название, призовой фонд и сроки' },
    { key: 'commission', label: 'Комиссия парка', hint: 'Например: 2%' },
    { key: 'income', label: 'Доход', hint: 'Например: до 500 000 ₸ в месяц' },
    { key: 'extra', label: 'Что ещё сказать', hint: 'Требования, выплаты, график — всё, что ИИ должен знать' },
];

const BRIEF_TEXT_KEYS = ['title', ...BRIEF_FIELDS.map((field) => field.key)];

const emptyBrief = () => ({
    ...Object.fromEntries(BRIEF_TEXT_KEYS.map((key) => [key, ''])),
    cabinets: [],
});

const briefToForm = (brief) => ({
    ...Object.fromEntries(BRIEF_TEXT_KEYS.map((key) => [key, brief[key] || ''])),
    cabinets: [...(brief.cabinets || [])],
});

/* «Все кабинеты», «tenge_olx, jana_olx» или «8 кабинетов» — коротко, для списка. */
const cabinetsSummary = (codes, cabinets) => {
    const list = codes || [];
    if (!list.length) return 'кабинеты не выбраны';
    if (cabinets.length && list.length === cabinets.length) return 'все кабинеты';
    if (list.length <= 2) {
        const titleOf = Object.fromEntries(cabinets.map((cab) => [cab.code, cab.title || cab.code]));
        return list.map((code) => titleOf[code] || code).join(', ');
    }
    return `${list.length} ${plural(list.length, 'кабинет', 'кабинета', 'кабинетов')}`;
};

/* «tenge_olx перешёл с брифа «Сентябрь»» — смена брифа у кабинета не должна
   пройти молча: человек включал один бриф, а поменялся и другой. */
const movedMessage = (moved, cabinets) => {
    if (!moved || !moved.length) return '';
    const titleOf = Object.fromEntries(cabinets.map((cab) => [cab.code, cab.title || cab.code]));
    const groups = {};
    moved.forEach((item) => {
        const key = item.from_title || '—';
        groups[key] = groups[key] || [];
        groups[key].push(titleOf[item.cabinet] || item.cabinet);
    });
    return Object.entries(groups)
        .map(([from, list]) => `${list.join(', ')} ${list.length === 1 ? 'перешёл' : 'перешли'} с брифа «${from}»`)
        .join('; ');
};

/*
 * Бриф месяца — вводные для ИИ, у КАЖДОГО кабинета свой.
 *
 * Решение владельца 14.09.2026: «разный бриф на разные кабинеты», с мультивыбором
 * кабинетов при написании. Правило, которое держит сервер: у кабинета один
 * действующий бриф, и кабинет забирает бриф, включённый последним. Экран
 * предупреждает об этом ДО сохранения, а после — говорит, что куда перешло.
 *
 * Слева — сам бриф с выбором кабинетов; справа — карта «где какой бриф
 * действует»: ради неё раздел брифов и открывают, когда брифов несколько.
 */
const BriefPanel = ({ apiBaseUrl, headers, toast, caps, cabinets, briefsByCabinet, onChanged }) => {
    const [briefs, setBriefs] = useState(null);
    const [form, setForm] = useState(emptyBrief());
    const [editingId, setEditingId] = useState(null);
    const [saving, setSaving] = useState(false);

    const load = useCallback(() => (
        axios.get(`${apiBaseUrl}/api/olx_ads/briefs`, { headers: headers() })
            .then((response) => {
                const items = response.data?.items || [];
                setBriefs(items);
                return items;
            })
            .catch(() => {
                setBriefs([]);
                return [];
            })
    ), [apiBaseUrl, headers]);

    const pick = useCallback((brief) => {
        if (!brief) {
            setEditingId(null);
            setForm(emptyBrief());
            return;
        }
        setEditingId(brief.id);
        setForm(briefToForm(brief));
    }, []);

    /* Открываем сразу действующий бриф: чаще всего его и правят. */
    useEffect(() => {
        load().then((items) => {
            const active = (items || []).find((brief) => brief.is_active);
            if (active) pick(active);
        });
    }, [load, pick]);

    /* Где какой бриф действует. Считаем из списка брифов этой вкладки — он
       перечитывается сразу после сохранения, — а до его загрузки берём карту
       из /ping, чтобы справа не мигала пустота. */
    const coverage = useMemo(() => {
        if (!briefs) return briefsByCabinet || {};
        const map = {};
        briefs.filter((brief) => brief.is_active).forEach((brief) => {
            (brief.cabinets || []).forEach((code) => { map[code] = { id: brief.id, title: brief.title }; });
        });
        return map;
    }, [briefs, briefsByCabinet]);

    const current = (briefs || []).find((brief) => brief.id === editingId) || null;
    const readOnly = !caps.can_write_content;
    const allCodes = cabinets.map((cab) => cab.code);
    const selected = new Set(form.cabinets);
    const allSelected = allCodes.length > 0 && allCodes.every((code) => selected.has(code));
    const titleOf = Object.fromEntries(cabinets.map((cab) => [cab.code, cab.title || cab.code]));

    const toggleCabinet = (code) => setForm((prev) => {
        const next = new Set(prev.cabinets);
        if (next.has(code)) next.delete(code); else next.add(code);
        return { ...prev, cabinets: allCodes.filter((item) => next.has(item)) };
    });
    const toggleAll = () => setForm((prev) => ({ ...prev, cabinets: allSelected ? [] : [...allCodes] }));

    /* Кабинеты выбора, которые сейчас на ДРУГОМ действующем брифе. */
    const conflicts = form.cabinets
        .map((code) => ({ code, owner: coverage[code] }))
        .filter((item) => item.owner && item.owner.id !== editingId);
    const conflictText = (() => {
        if (!conflicts.length) return '';
        const groups = {};
        conflicts.forEach((item) => {
            groups[item.owner.title] = groups[item.owner.title] || [];
            groups[item.owner.title].push(titleOf[item.code] || item.code);
        });
        const when = current?.is_active ? 'при сохранении' : 'после включения';
        return Object.entries(groups).map(([from, list]) => (
            `${list.join(', ')} сейчас на брифе «${from}» — ${when} ${list.length === 1 ? 'перейдёт' : 'перейдут'} сюда`
        )).join('; ');
    })();

    const save = (activate) => {
        if (!form.title.trim()) {
            toast('Назовите бриф — например, «Сентябрь 2026»', 'warning');
            return;
        }
        if (!form.cabinets.length) {
            toast('Выберите хотя бы один кабинет, для которого этот бриф', 'warning');
            return;
        }
        setSaving(true);
        const payload = { ...form, title: form.title.trim() };
        const request = editingId
            ? axios.patch(`${apiBaseUrl}/api/olx_ads/briefs/${editingId}`, payload, { headers: headers() })
                .then((response) => {
                    if (!activate || response.data?.brief?.is_active) return response;
                    return axios.post(`${apiBaseUrl}/api/olx_ads/briefs/${editingId}/activate`, {}, { headers: headers() })
                        .then((activated) => ({
                            data: {
                                ...activated.data,
                                moved: [...(response.data?.moved || []), ...(activated.data?.moved || [])],
                            },
                        }));
                })
            : axios.post(`${apiBaseUrl}/api/olx_ads/briefs`, { ...payload, activate }, { headers: headers() });
        request
            .then((response) => {
                const brief = response.data?.brief;
                if (brief?.id) {
                    setEditingId(brief.id);
                    setForm(briefToForm(brief));
                }
                const moved = movedMessage(response.data?.moved, cabinets);
                const head = brief?.is_active ? 'Бриф сохранён и действует' : 'Бриф сохранён';
                toast([head, moved].filter(Boolean).join('. '), 'success');
                load();
                onChanged();
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось сохранить бриф', 'error'))
            .finally(() => setSaving(false));
    };

    const deactivate = () => {
        if (!editingId) return;
        setSaving(true);
        axios.post(`${apiBaseUrl}/api/olx_ads/briefs/${editingId}/deactivate`, {}, { headers: headers() })
            .then((response) => {
                const brief = response.data?.brief;
                if (brief) setForm(briefToForm(brief));
                toast('Бриф выключен — у его кабинетов нет брифа, пока не включите другой', 'success');
                load();
                onChanged();
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось выключить бриф', 'error'))
            .finally(() => setSaving(false));
    };

    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
            <div className={`${iosCard} space-y-4 p-4 sm:p-5`}>
                <div>
                    <h2 className="text-[16px] font-semibold text-slate-900">
                        {editingId ? 'Бриф' : 'Новый бриф'}
                        {current?.is_active && <IosBadge tone="blue" className="ml-2 align-middle">Действует</IosBadge>}
                    </h2>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        ИИ возьмёт отсюда только факты — и только для объявлений выбранных кабинетов.
                    </p>
                </div>

                <div className="space-y-1.5">
                    <label className={iosGroupLabel} htmlFor="brief-title">Название</label>
                    <input
                        id="brief-title"
                        className={iosInput}
                        placeholder="Сентябрь 2026"
                        value={form.title}
                        onChange={(e) => setForm({ ...form, title: e.target.value })}
                        disabled={readOnly}
                    />
                </div>

                <div className="space-y-2">
                    <div className="flex items-center justify-between px-1">
                        <span className={iosGroupLabel}>Для каких кабинетов</span>
                        {!readOnly && cabinets.length > 0 && (
                            <button
                                type="button"
                                data-cabinet-all
                                onClick={toggleAll}
                                className="text-[12.5px] font-medium text-blue-600 hover:underline"
                            >
                                {allSelected ? 'Снять все' : 'Все'}
                            </button>
                        )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {cabinets.map((cab) => {
                            const on = selected.has(cab.code);
                            return (
                                <button
                                    key={cab.code}
                                    type="button"
                                    data-cabinet={cab.code}
                                    aria-pressed={on}
                                    disabled={readOnly}
                                    onClick={() => toggleCabinet(cab.code)}
                                    className={`rounded-full px-3 py-1.5 text-[13px] font-medium ring-1 transition active:scale-[0.98] disabled:cursor-default ${
                                        on ? 'bg-blue-600 text-white ring-blue-600' : 'bg-white text-slate-600 ring-slate-200 hover:bg-slate-50'
                                    }`}
                                >
                                    {cab.title || cab.code}
                                </button>
                            );
                        })}
                    </div>
                    {conflictText && (
                        <p data-brief-conflict className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] leading-snug text-amber-800">
                            {conflictText}
                        </p>
                    )}
                </div>

                {BRIEF_FIELDS.map((field) => (
                    <div key={field.key} className="space-y-1.5">
                        <label className={iosGroupLabel} htmlFor={`brief-${field.key}`}>{field.label}</label>
                        <textarea
                            id={`brief-${field.key}`}
                            className={`${iosInput} ${field.key === 'extra' ? 'min-h-[96px]' : 'min-h-[44px]'}`}
                            placeholder={field.hint}
                            value={form[field.key]}
                            onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
                            disabled={readOnly}
                        />
                    </div>
                ))}

                {!readOnly && (
                    <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
                        {current?.is_active && (
                            <button type="button" className={iosBtnGhost} onClick={deactivate} disabled={saving}>
                                Выключить
                            </button>
                        )}
                        <button type="button" className={iosBtnSecondary} onClick={() => save(false)} disabled={saving}>
                            Сохранить
                        </button>
                        {!current?.is_active && (
                            <button type="button" className={iosBtnPrimary} onClick={() => save(true)} disabled={saving}>
                                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                                Сохранить и включить
                            </button>
                        )}
                    </div>
                )}
            </div>

            <aside className="space-y-4">
                <div className="space-y-2">
                    <span className={`${iosGroupLabel} block`}>Где какой бриф</span>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {cabinets.map((cab) => {
                            const owner = coverage[cab.code];
                            const ownerBrief = owner ? (briefs || []).find((brief) => brief.id === owner.id) : null;
                            return (
                                <div key={cab.code} data-coverage={cab.code}>
                                    <button
                                        type="button"
                                        disabled={!ownerBrief}
                                        onClick={() => ownerBrief && pick(ownerBrief)}
                                        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left transition ${
                                            ownerBrief ? 'hover:bg-slate-50' : 'cursor-default'
                                        } ${owner && owner.id === editingId ? 'bg-blue-50/60' : ''}`}
                                    >
                                        <span className="truncate text-[13px] text-slate-700">{cab.title || cab.code}</span>
                                        <span className={`truncate text-right text-[12px] ${owner ? 'text-slate-500' : 'text-slate-400'}`}>
                                            {owner ? owner.title : 'нет брифа'}
                                        </span>
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                </div>

                <div className="space-y-2">
                    <div className="flex items-center justify-between px-1">
                        <span className={iosGroupLabel}>Все брифы</span>
                        {!readOnly && (
                            <button type="button" className="text-[12.5px] font-medium text-blue-600 hover:underline" onClick={() => pick(null)}>
                                Новый
                            </button>
                        )}
                    </div>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {briefs === null && (
                            <div className="flex justify-center py-6 text-slate-400"><Loader2 className="h-4 w-4 animate-spin" /></div>
                        )}
                        {briefs && briefs.length === 0 && (
                            <div className="px-3 py-6 text-center text-[12.5px] text-slate-500">Брифов пока нет</div>
                        )}
                        {(briefs || []).map((brief) => (
                            <button
                                key={brief.id}
                                type="button"
                                onClick={() => pick(brief)}
                                className={`block w-full px-3 py-2.5 text-left transition ${brief.id === editingId ? 'bg-blue-50/60' : 'hover:bg-slate-50'}`}
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <span className="truncate text-[13.5px] font-medium text-slate-800">{brief.title}</span>
                                    {brief.is_active && <IosBadge tone="blue">Действует</IosBadge>}
                                </div>
                                <div className="text-[11.5px] text-slate-400 tabular-nums">
                                    {cabinetsSummary(brief.cabinets, cabinets)} · {fmtDateTime(brief.updated_at)}
                                </div>
                            </button>
                        ))}
                    </div>
                </div>
            </aside>
        </div>
    );
};

/* ── История правок ─────────────────────────────────────────────────────── */

const HistoryPanel = ({ apiBaseUrl, headers, toast, caps, cabinets, onChanged }) => {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [cabinet, setCabinet] = useState('');
    const [result, setResult] = useState('');
    const [loading, setLoading] = useState(false);

    const load = useCallback(() => {
        setLoading(true);
        return axios.get(`${apiBaseUrl}/api/olx_ads/history`, {
            headers: headers(),
            params: {
                limit: PAGE_SIZE,
                offset: (page - 1) * PAGE_SIZE,
                cabinet: cabinet || undefined,
                result: result || undefined,
            },
        })
            .then((response) => {
                setItems(response.data?.items || []);
                setTotal(response.data?.total || 0);
            })
            .catch((error) => toast(error.response?.data?.error || 'Не удалось загрузить историю', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, toast, page, cabinet, result]);

    useEffect(() => { load(); }, [load]);

    const cabinetOptions = [
        { value: '', label: 'Все кабинеты' },
        ...cabinets.map((c) => ({ value: c.code, label: c.title || c.code })),
    ];
    const resultOptions = [
        { value: '', label: 'Все правки' },
        { value: 'applied', label: RESULT_LABEL.applied },
        { value: 'failed', label: RESULT_LABEL.failed },
        { value: 'rolled_back', label: RESULT_LABEL.rolled_back },
    ];
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <div className="space-y-3">
            <div className={`${iosCard} grid grid-cols-1 gap-2 p-3 sm:grid-cols-2 sm:p-4`}>
                <CustomSelect variant="ios" value={cabinet} onChange={(v) => { setCabinet(v); setPage(1); }} options={cabinetOptions} ariaLabel="Кабинет" />
                <CustomSelect variant="ios" value={result} onChange={(v) => { setResult(v); setPage(1); }} options={resultOptions} ariaLabel="Результат" />
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                <div className="border-b border-slate-100 px-4 py-2.5">
                    <span className={iosGroupLabel}>
                        {loading && !items.length
                            ? 'Загрузка…'
                            : `${total} ${plural(total, 'правка', 'правки', 'правок')}`}
                    </span>
                </div>
                {!loading && items.length === 0 && (
                    <div className="px-4 py-12 text-center text-[13.5px] text-slate-500">
                        <History className="mx-auto mb-2 h-6 w-6 text-slate-300" />
                        Правок пока не было
                    </div>
                )}
                <HistoryList
                    items={items}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    toast={toast}
                    caps={caps}
                    cabinets={cabinets}
                    onChanged={() => { load(); onChanged(); }}
                />
                <div className="px-3 py-2">
                    <IosPager
                        page={page}
                        pageCount={pageCount}
                        total={total}
                        from={(page - 1) * PAGE_SIZE + 1}
                        to={Math.min(page * PAGE_SIZE, total)}
                        onPage={setPage}
                        unit="правки"
                    />
                </div>
            </div>
        </div>
    );
};

const HistoryList = ({ items, apiBaseUrl, headers, toast, caps, cabinets = [], compact = false, onChanged }) => {
    const [open, setOpen] = useState(null);
    const [confirm, setConfirm] = useState(null);
    const [rolling, setRolling] = useState(false);

    const cabinetTitle = useMemo(() => {
        const map = {};
        cabinets.forEach((c) => { map[c.code] = c.title || c.code; });
        return map;
    }, [cabinets]);

    if (!items.length) {
        return compact ? (
            <div className="py-8 text-center text-[13px] text-slate-500">Это объявление через раздел ещё не правили</div>
        ) : null;
    }

    const rollback = () => {
        setRolling(true);
        axios.post(`${apiBaseUrl}/api/olx_ads/history/${confirm.id}/rollback`, {}, { headers: headers(), timeout: 0 })
            .then((response) => {
                if (response.data?.ok) {
                    toast('Прежний текст возвращён', 'success');
                } else {
                    toast(response.data?.error ? `OLX не принял: ${response.data.error}` : 'Откат не прошёл', 'error');
                }
                setConfirm(null);
                onChanged();
            })
            .catch((error) => toast(error.response?.data?.error || 'Откат не прошёл', 'error'))
            .finally(() => setRolling(false));
    };

    return (
        <>
            <ul className={compact ? 'space-y-2' : 'divide-y divide-slate-100'}>
                {items.map((entry) => {
                    const expanded = open === entry.id;
                    const titleChanged = (entry.old_title || '') !== (entry.new_title || '');
                    return (
                        <li key={entry.id} className={compact ? 'rounded-2xl bg-slate-50' : ''}>
                            <button
                                type="button"
                                onClick={() => setOpen(expanded ? null : entry.id)}
                                className="flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-50/80"
                            >
                                {expanded
                                    ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                                    : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />}
                                <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500 tabular-nums">
                                        <span>{fmtDateTime(entry.created_at)}</span>
                                        <span>·</span>
                                        <span className="font-medium text-slate-600">{entry.actor_name || '—'}</span>
                                        {!compact && (
                                            <>
                                                <span>·</span>
                                                <span>{cabinetTitle[entry.cabinet_code] || entry.cabinet_code}</span>
                                            </>
                                        )}
                                        <span>·</span>
                                        <span>{ORIGIN_LABEL[entry.origin] || entry.origin}, {SOURCE_LABEL[entry.source] || entry.source}</span>
                                        {entry.result !== 'applied' && (
                                            <IosBadge tone={RESULT_TONE[entry.result] || 'slate'}>{RESULT_LABEL[entry.result] || entry.result}</IosBadge>
                                        )}
                                    </div>
                                    <div className="mt-1 truncate text-[13.5px] text-slate-900">
                                        {titleChanged ? entry.new_title : (entry.new_title || '—')}
                                    </div>
                                    {entry.result === 'failed' && entry.error_text && (
                                        <div className="mt-0.5 text-[12.5px] text-rose-600">{entry.error_text}</div>
                                    )}
                                </div>
                            </button>

                            {expanded && (
                                <div className="space-y-3 px-4 pb-4 pl-11">
                                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                        <div className="space-y-1">
                                            <div className={iosGroupLabel}>Было</div>
                                            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/70">
                                                <div className="text-[13px] font-medium text-slate-800">{entry.old_title || '—'}</div>
                                                <div className="mt-1 whitespace-pre-line text-[12.5px] text-slate-500">{toPlain(entry.old_description) || '—'}</div>
                                            </div>
                                        </div>
                                        <div className="space-y-1">
                                            <div className={iosGroupLabel}>Стало</div>
                                            <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200/70">
                                                <div className="text-[13px] font-medium text-slate-800">{entry.new_title || '—'}</div>
                                                <div className="mt-1 whitespace-pre-line text-[12.5px] text-slate-500">{toPlain(entry.new_description) || '—'}</div>
                                            </div>
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        {entry.advert_url ? (
                                            <a href={entry.advert_url} target="_blank" rel="noreferrer" className={iosBtnGhost}>
                                                <ExternalLink className="h-4 w-4" /> №{entry.advert_id} на OLX
                                            </a>
                                        ) : <span className="text-[12px] text-slate-400">№{entry.advert_id}</span>}
                                        {caps.can_apply && entry.result === 'applied' && entry.old_title && (
                                            <button type="button" className={iosBtnSecondary} onClick={() => setConfirm(entry)}>
                                                <RotateCcw className="h-4 w-4" /> Вернуть как было
                                            </button>
                                        )}
                                    </div>
                                </div>
                            )}
                        </li>
                    );
                })}
            </ul>

            <IosModal
                open={Boolean(confirm)}
                onClose={() => (rolling ? null : setConfirm(null))}
                title="Вернуть прежний текст?"
                subtitle={confirm ? `Объявление №${confirm.advert_id}` : ''}
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={() => setConfirm(null)} disabled={rolling}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={rollback} disabled={rolling}>
                            {rolling ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                            Вернуть
                        </button>
                    </div>
                )}
            >
                {confirm && (
                    <div className="space-y-2 text-[13.5px] text-slate-600">
                        <p>В объявлении снова будет стоять:</p>
                        <p className="font-medium text-slate-900">«{confirm.old_title}»</p>
                        <p className="text-slate-500">Откат тоже попадёт в историю.</p>
                    </div>
                )}
            </IosModal>
        </>
    );
};

export default OlxAdsView;
