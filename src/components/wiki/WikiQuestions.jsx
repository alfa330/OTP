import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    ArrowLeft, Check, ChevronDown, FilePlus2, FileText, Loader2, Megaphone, Plus, Sparkles, X,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosSegmented,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { publishedLabel, subscribeNewsPoke } from '../news/newsShared';
import { createCoalescedReload } from '../notifications/coalescedReload.js';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import useStableCallback from './useStableCallback';
import {
    QUIZ_MAX_OPTIONS, QUIZ_MAX_QUESTIONS, QUIZ_MIN_OPTIONS, QUIZ_MIN_QUESTIONS,
    dropOption, emptyQuestion, quizForForm, quizProblem,
} from './questionQuiz';
import { newsDraftFromQuestion } from './questionNews';

/* Вкладка «Вопросы» — вопросы операторов, на которые не ответил помощник.
 *
 * Задача #321. Цепочка из постановки: вопрос оператора → помощник не знает
 * ответа → вопрос уходит супервайзеру отдела → супервайзер отвечает → ответ
 * записывается в статью (существующую или новую) → новость об изменении с
 * тестом уходит отделу. Сервер — wiki/routes_questions.py.
 *
 * ТРИ КОРЗИНЫ, А НЕ СТАТУСЫ. «Новые» ждут ответа; «Ждут статьи» — оператор ответ
 * получил, но в базу знаний он ещё не записан; «Разобранные» — всё остальное.
 * Вторая корзина и есть половина задачи: без неё ответ остался бы в одном чате,
 * и следующий оператор спросил бы о том же снова.
 *
 * ОТВЕТ УХОДИТ СРАЗУ, СТАТЬЯ — СЛЕДОМ: оператор на линии, и ждать публикации ему
 * незачем.
 *
 * ИИ ГОТОВИТ, ЧЕЛОВЕК ПУБЛИКУЕТ. Правка статьи, новость и тест приходят
 * черновиком, всё правится здесь же, и публикует одна кнопка: новость с тестом
 * уходит всему отделу обязательным окном, и отдел выучит ровно то, что написано
 * на этом экране.
 *
 * Новый вопрос, пока вкладка открыта, приезжает тычком колокола — своего канала
 * у вкладки нет, как и у окна новости.
 */

const MAX_ANSWER_LENGTH = 4000;

const BUCKETS = [
    { value: 'open', label: 'Новые' },
    { value: 'answered', label: 'Ждут статьи' },
    { value: 'done', label: 'Разобранные' },
];

const EMPTY_TEXT = {
    open: ['Новых вопросов нет',
           'Сюда приходят вопросы операторов вашего отдела, на которые не ответил помощник.'],
    answered: ['Все ответы записаны в базу знаний', null],
    done: ['Разобранных вопросов пока нет', null],
};

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

export default function WikiQuestions({ base, headers, showToast, onOpenArticle,
                                        spaceId = null, spaces = [], onSpaceChange,
                                        canComposeNews = false, onComposeNews,
                                        focusRequest = null, onFocusConsumed }) {
    const toast = useStableCallback(showToast);
    const openArticle = useStableCallback(onOpenArticle);
    const consumeFocus = useStableCallback(onFocusConsumed);
    const changeSpace = useStableCallback(onSpaceChange);
    const composeNews = useStableCallback(onComposeNews);
    const isMobile = useIsMobileShell();

    const [bucket, setBucket] = useState('open');
    const [list, setList] = useState({ items: [], counts: {}, manyDepartments: false });
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState(null);

    // Гонка корзин: ответ по прежней корзине не должен лечь поверх новой.
    const listRequest = useRef(0);

    /* Вика в шапке и открытые человеку вики — ещё и ссылками: их читает ответ
       сервера, а в зависимостях эффектов список вик перезапускал бы их на
       каждом ping — он приходит оттуда новым массивом. */
    const spaceRef = useRef(spaceId);
    const reachableRef = useRef(new Set());
    useEffect(() => { spaceRef.current = spaceId; }, [spaceId]);
    useEffect(() => { reachableRef.current = new Set(spaces.map((sp) => sp.id)); }, [spaces]);

    const load = useCallback(() => {
        const request = ++listRequest.current;
        // Вопросы — той вики, что открыта в шапке (wiki/questions.py: _space_scope).
        return axios.get(`${base}/questions`, { headers, params: { bucket, space_id: spaceId } })
            .then((r) => {
                if (request !== listRequest.current) return;
                setList({
                    items: r.data?.items || [],
                    counts: r.data?.counts || {},
                    manyDepartments: !!r.data?.many_departments,
                });
            })
            .catch((e) => {
                if (request === listRequest.current) toast(errText(e, 'Не удалось загрузить вопросы'), 'error');
            })
            .finally(() => {
                if (request === listRequest.current) setLoading(false);
            });
    }, [base, headers, bucket, spaceId, toast]);

    useEffect(() => { load(); }, [load]);

    /* Сменили вику в шапке — строки прежней не стоят на экране, пока едет новый
       список, а открытая из неё карточка закрывается. Вопрос из вики, которую
       человек открыть не может, остаётся: он и виден в любой. */
    useEffect(() => {
        setLoading(true);
        setList((prev) => ({ ...prev, items: [] }));
        setSelected((prev) => (prev?.space_id && prev.space_id !== spaceId
            && reachableRef.current.has(prev.space_id) ? null : prev));
    }, [spaceId]);

    /* Новый вопрос отделу, пока вкладка открыта, — тычок канала колокола.
       Склейка, а не таймер: тычок приходит на любое событие колокола, и на пачку
       событий перечиток должно быть не больше двух. */
    useEffect(() => subscribeNewsPoke(createCoalescedReload(() => load())), [load]);

    /* Пришли из колокола — открываем карточку. Строку берём у сервера, а не из
       списка: вопрос мог уже переехать в другую корзину. */
    useEffect(() => {
        if (!focusRequest?.id) return;
        axios.get(`${base}/questions/${focusRequest.id}`, { headers })
            .then((r) => {
                const item = r.data?.item;
                if (!item) return;
                /* Вопрос из другой вики — переключаем шапку на неё, а не кладём
                   карточку поверх чужого списка. */
                if (item.space_id && item.space_id !== spaceRef.current
                    && reachableRef.current.has(item.space_id)) {
                    changeSpace(item.space_id);
                }
                setSelected(item);
            })
            .catch((e) => toast(errText(e, 'Вопрос не открылся'), 'error'))
            .finally(() => consumeFocus());
    }, [focusRequest, base, headers, toast, consumeFocus]);

    // «Назад» на телефоне возвращает из карточки к списку, а не из раздела.
    useScreenBackGesture(isMobile && !!selected, () => {
        setSelected(null);
        return true;
    });

    const switchBucket = (value) => {
        if (value === bucket) return;
        setLoading(true);
        setList((prev) => ({ ...prev, items: [] }));
        setBucket(value);
    };

    const applyChange = useCallback((item) => {
        if (item) setSelected(item);
        load();
    }, [load]);

    const showList = !isMobile || !selected;
    const showCard = !isMobile || !!selected;

    return (
        <div className="grid items-start gap-3 md:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
            {showList && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="border-b border-slate-100 p-2.5">
                        <IosSegmented
                            value={bucket}
                            onChange={switchBucket}
                            stretch
                            ariaLabel="Вопросы операторов"
                            /* Число — только у очередей: у «Разобранных» оно
                               растёт вечно и ни к какому действию не зовёт. */
                            options={BUCKETS.map((option) => ({
                                ...option,
                                count: option.value === 'done' ? undefined : list.counts[option.value],
                            }))}
                        />
                    </div>
                    <div className="thin-scroll max-h-[calc(100vh-260px)] min-h-[320px] overflow-y-auto">
                        {loading && (
                            <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400">
                                <Loader2 size={15} className="animate-spin" /> Загружаем…
                            </div>
                        )}
                        {!loading && !list.items.length && <EmptyBucket bucket={bucket} />}
                        {!loading && list.items.map((item) => (
                            <QuestionRow
                                key={item.id}
                                item={item}
                                bucket={bucket}
                                active={selected?.id === item.id}
                                showDepartment={list.manyDepartments}
                                spaceId={spaceId}
                                onOpen={() => setSelected(item)}
                            />
                        ))}
                    </div>
                </div>
            )}

            {showCard && (selected ? (
                <QuestionCard
                    key={selected.id}
                    item={selected}
                    base={base}
                    headers={headers}
                    toast={toast}
                    showDepartment={list.manyDepartments}
                    canComposeNews={canComposeNews}
                    onComposeNews={composeNews}
                    onBack={isMobile ? () => setSelected(null) : null}
                    onChanged={applyChange}
                    onOpenArticle={openArticle}
                />
            ) : (
                <div className={`${iosCard} hidden min-h-[320px] items-center justify-center px-6 text-center text-[13px] text-slate-400 md:flex`}>
                    Выберите вопрос в списке
                </div>
            ))}
        </div>
    );
}

function EmptyBucket({ bucket }) {
    const [title, hint] = EMPTY_TEXT[bucket] || EMPTY_TEXT.open;
    return (
        <div className="px-6 py-12 text-center">
            <p className="text-[14px] text-slate-900">{title}</p>
            {hint && <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">{hint}</p>}
        </div>
    );
}

function QuestionRow({ item, bucket, active, showDepartment, spaceId, onOpen }) {
    /* Метка — только у исключений. В «Разобранных» «записано в статью» — норма и
       не подписывается, а «без ответа», «не для базы» и «новостью» — другая судьба
       вопроса. В «Новых» — вопрос, который оператор передал сам: помощник ему
       ответил, и без метки строка читалась бы ошибкой передачи. */
    const mark = bucket === 'open' ? (item.requested_by_asker ? 'ответ не устроил' : null)
        : bucket !== 'done' ? null
            : item.status === 'dismissed' ? 'без ответа'
                : item.kb_status === 'skipped' ? 'не для базы'
                    : item.kb_status === 'news' ? 'новостью' : null;
    const department = showDepartment ? item.department_name : null;
    /* Чужая вика в списке бывает только у вопроса, который иначе потерялся бы
       (wiki/questions.py: _space_scope), — и по строке это должно быть видно. */
    const space = item.space_id && item.space_id !== spaceId ? item.space_name : null;
    return (
        <button
            type="button"
            onClick={onOpen}
            aria-current={active ? 'true' : undefined}
            className={`block w-full border-b border-slate-100 px-3.5 py-3 text-left transition last:border-b-0 ${
                active ? 'bg-blue-50/70' : 'hover:bg-slate-50'
            }`}
        >
            <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-[13px] font-semibold text-slate-900">
                    {item.asker_name || 'Оператор'}
                </span>
                <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">
                    {publishedLabel(item.created_at)}
                </span>
            </div>
            <p className="mt-0.5 line-clamp-2 break-words text-[13px] leading-snug text-slate-600">
                {item.question}
            </p>
            {(mark || department || space) && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {mark && <IosBadge tone="slate">{mark}</IosBadge>}
                    {department && <span className="truncate text-[11.5px] text-slate-400">{department}</span>}
                    {space && <span className="truncate text-[11.5px] text-slate-400">пространство «{space}»</span>}
                </div>
            )}
        </button>
    );
}

function QuestionCard({ item, base, headers, toast, showDepartment, canComposeNews, onComposeNews,
                        onBack, onChanged, onOpenArticle }) {
    const meta = [item.asker_name, showDepartment ? item.department_name : null,
                  publishedLabel(item.created_at)].filter(Boolean).join(' · ');
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <div className="flex items-start gap-2 border-b border-slate-100 px-4 py-3.5">
                {onBack && (
                    <button
                        type="button"
                        onClick={onBack}
                        aria-label="Назад к списку"
                        className="-ml-1.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 active:scale-95"
                    >
                        <ArrowLeft size={17} />
                    </button>
                )}
                <div className="min-w-0 flex-1">
                    <p className="truncate text-[12px] text-slate-400">{meta}</p>
                    <h3 className="mt-1 whitespace-pre-wrap break-words text-[16px] font-semibold leading-snug text-slate-900">
                        {item.question}
                    </h3>
                </div>
            </div>

            <div className="space-y-4 px-4 py-4">
                <AssistantReply text={item.assistant_text} requested={item.requested_by_asker} />

                {item.status === 'open' && (
                    <AnswerForm item={item} base={base} headers={headers} toast={toast} onChanged={onChanged} />
                )}

                {item.status === 'dismissed' && (
                    <p className="text-[13px] text-slate-500">
                        Закрыт без ответа{item.resolved_by_name ? ` · ${item.resolved_by_name}` : ''}
                    </p>
                )}

                {item.status === 'answered' && (
                    <>
                        <div className="rounded-xl bg-slate-50 px-3.5 py-3">
                            <p className="text-[11.5px] text-slate-400">
                                {['Ответ', item.resolved_by_name, publishedLabel(item.resolved_at)]
                                    .filter(Boolean).join(' · ')}
                            </p>
                            <p className="mt-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-slate-800">
                                {item.answer}
                            </p>
                        </div>

                        {!item.kb_status && (
                            <KnowledgeFlow key={item.id} item={item} base={base} headers={headers}
                                           toast={toast} onChanged={onChanged}
                                           canComposeNews={canComposeNews}
                                           onComposeNews={onComposeNews} />
                        )}

                        {item.kb_status === 'published' && (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-emerald-50 px-3.5 py-2.5 ring-1 ring-emerald-100">
                                <p className="text-[13px] text-emerald-900">
                                    Записано в «{item.kb_article_title || 'статью'}» · отдел получил новость с тестом
                                </p>
                                {item.kb_article_slug && (
                                    <button
                                        type="button"
                                        onClick={() => onOpenArticle(item.kb_article_slug)}
                                        className={`${iosBtnGhost} !text-emerald-700 hover:!bg-emerald-100`}
                                    >
                                        <FileText size={14} /> Открыть статью
                                    </button>
                                )}
                            </div>
                        )}

                        {item.kb_status === 'skipped' && (
                            <p className="text-[13px] text-slate-500">В базу знаний не записывался</p>
                        )}

                        {item.kb_status === 'news' && (
                            <p className="rounded-xl bg-emerald-50 px-3.5 py-2.5 text-[13px] text-emerald-900 ring-1 ring-emerald-100">
                                Опубликовано новостью · отдел увидит её при входе
                            </p>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

/* Что ответил помощник. Оператор, передавший вопрос сам, спорит именно с этим
   ответом — поэтому у такого вопроса он раскрыт сразу. У отказа там «в статьях
   этого нет», и раскрытым он был бы шумом над полем ответа. */
function AssistantReply({ text, requested }) {
    const [open, setOpen] = useState(!!requested);
    if (!text) return null;
    return (
        <div className="rounded-xl bg-slate-50 px-3.5 py-2.5">
            <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                aria-expanded={open}
                className="flex w-full items-center justify-between gap-2 text-left text-[11.5px] text-slate-400"
            >
                <span>{requested ? 'Ответ помощника · оператора он не устроил' : 'Ответ помощника'}</span>
                <ChevronDown size={14} className={`shrink-0 transition ${open ? 'rotate-180' : ''}`} />
            </button>
            {open && (
                <p className="mt-1.5 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-600">
                    {text}
                </p>
            )}
        </div>
    );
}

function AnswerForm({ item, base, headers, toast, onChanged }) {
    const [text, setText] = useState('');
    const [busy, setBusy] = useState(null);                 // 'answer' | 'dismiss'
    /* «Закрыть без ответа» — в два нажатия. Модалка ради одной кнопки была бы
       шумом, а случайный щелчок оставил бы оператора без ответа насовсем. */
    const [confirmDismiss, setConfirmDismiss] = useState(false);

    useEffect(() => {
        if (!confirmDismiss) return undefined;
        const timer = setTimeout(() => setConfirmDismiss(false), 4000);
        return () => clearTimeout(timer);
    }, [confirmDismiss]);

    const send = (kind) => {
        if (busy) return;
        const answer = text.trim();
        if (kind === 'answer' && !answer) return;
        if (kind === 'dismiss' && !confirmDismiss) {
            setConfirmDismiss(true);
            return;
        }
        setBusy(kind);
        axios.post(`${base}/questions/${item.id}/${kind}`, kind === 'answer' ? { answer } : {}, { headers })
            .then((r) => {
                toast(kind === 'answer' ? 'Ответ отправлен оператору' : 'Вопрос закрыт без ответа', 'success');
                onChanged(r.data?.item);
            })
            .catch((e) => {
                // Коллега успел раньше — сервер присылает, как вопрос разобран.
                if (e?.response?.data?.item) onChanged(e.response.data.item);
                toast(errText(e, 'Не получилось'), 'error');
            })
            .finally(() => {
                setBusy(null);
                setConfirmDismiss(false);
            });
    };

    return (
        <div className="space-y-2.5">
            <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        send('answer');
                    }
                }}
                rows={5}
                maxLength={MAX_ANSWER_LENGTH}
                placeholder="Ответ оператору"
                aria-label="Ответ оператору"
                className={`${iosInput} resize-y leading-relaxed`}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
                <button
                    type="button"
                    onClick={() => send('dismiss')}
                    disabled={!!busy}
                    className={`${iosBtnGhost} ${confirmDismiss ? '!text-rose-600' : ''}`}
                >
                    {busy === 'dismiss' ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
                    {confirmDismiss ? 'Нажмите ещё раз, чтобы закрыть' : 'Закрыть без ответа'}
                </button>
                <button
                    type="button"
                    onClick={() => send('answer')}
                    disabled={!!busy || !text.trim()}
                    className={iosBtnPrimary}
                >
                    {busy === 'answer' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                    Ответить
                </button>
            </div>
        </div>
    );
}

/* Запись ответа в базу знаний: куда → черновик → проверка → публикация. */
function KnowledgeFlow({ item, base, headers, toast, onChanged, canComposeNews, onComposeNews }) {
    const url = `${base}/questions/${item.id}/knowledge`;
    const [targets, setTargets] = useState(null);
    const [targetsFailed, setTargetsFailed] = useState(false);
    const [choice, setChoice] = useState(null);             // {action, article_id | section_id}
    const [stage, setStage] = useState('targets');          // targets | choose | article | news | review
    const [draft, setDraft] = useState(null);
    const [news, setNews] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const loadTargets = useCallback(() => {
        setStage('targets');
        setError('');
        setTargetsFailed(false);
        return axios.post(`${url}/targets`, {}, { headers })
            .then((r) => {
                setTargets(r.data || {});
                setChoice(r.data?.suggested || null);
            })
            .catch((e) => {
                setTargets({ candidates: [], sections: [] });
                setTargetsFailed(true);
                setError(errText(e, 'Не удалось подобрать статьи'));
            })
            .finally(() => setStage('choose'));
    }, [url, headers]);

    // Подбор — сразу при открытии: он не вызывает модель, а без него карточке
    // нечего предложить, кроме лишнего нажатия.
    useEffect(() => { loadTargets(); }, [loadTargets]);

    const prepare = async () => {
        if (!choice || busy) return;
        setBusy(true);
        setError('');
        setDraft(null);
        setNews(null);
        setStage('article');
        let article = null;
        try {
            article = (await axios.post(`${url}/draft`, choice, { headers })).data;
            setDraft(article);
            setStage('news');
            const drafted = (await axios.post(`${url}/news`, {
                article_title: article.title, changes: article.changes || [],
            }, { headers })).data;
            setNews({
                title: drafted.title || '',
                body: drafted.body || '',
                quiz: quizForForm(drafted.quiz),
                warnings: drafted.warnings || [],
            });
            setStage('review');
        } catch (e) {
            if (article) {
                /* Статья готова, а новость нет — готовое не выбрасываем:
                   новость и тест дописываются руками, а статью второй раз
                   гонять через модель незачем. */
                setNews({ title: '', body: '', quiz: quizForForm([]), warnings: [] });
                setError(errText(e, 'ИИ не подготовил новость — заполните её вручную'));
                setStage('review');
            } else {
                setError(errText(e, 'ИИ не подготовил правку статьи'));
                setStage('choose');
            }
        } finally {
            setBusy(false);
        }
    };

    const publish = () => {
        if (busy || !draft || !news) return;
        setBusy(true);
        setError('');
        axios.post(`${url}/publish`, {
            action: draft.action,
            article_id: draft.article?.id,
            section_id: draft.section_id,
            title: draft.title,
            summary: draft.summary,
            content: draft.content,
            news_title: news.title,
            news_body: news.body,
            quiz: news.quiz.map(({ prompt, options, correct }) => ({ prompt, options, correct })),
        }, { headers })
            .then((r) => {
                // Коротко: что именно записано и кому ушло, говорит плашка
                // карточки сразу под тостом — второй раз то же самое было бы шумом.
                toast('Опубликовано', 'success');
                onChanged(r.data?.item);
            })
            .catch((e) => setError(errText(e, 'Не удалось опубликовать')))
            .finally(() => setBusy(false));
    };

    const skip = () => {
        if (busy) return;
        setBusy(true);
        axios.post(`${url}/skip`, {}, { headers })
            .then((r) => {
                toast('Отмечено: не для базы знаний', 'success');
                onChanged(r.data?.item);
            })
            .catch((e) => toast(errText(e, 'Не получилось'), 'error'))
            .finally(() => setBusy(false));
    };

    if (stage === 'targets') {
        return (
            <div className="flex items-center gap-2 text-[13px] text-slate-400">
                <Loader2 size={14} className="animate-spin" /> Подбираем статью…
            </div>
        );
    }

    if (stage === 'article' || stage === 'news') {
        return (
            <section className="space-y-2 rounded-xl bg-slate-50 px-3.5 py-3">
                <ProgressLine done={stage === 'news'} active={stage === 'article'}
                              label={choice?.action === 'create' ? 'Собираем новую статью' : 'Готовим правку статьи'} />
                <ProgressLine done={false} active={stage === 'news'} label="Пишем новость и тест для отдела" />
            </section>
        );
    }

    if (stage === 'review' && draft && news) {
        const notes = [...(draft.questions || []), ...(draft.warnings || [])];
        const blocker = (draft.action === 'create' && !String(draft.title || '').trim()
            ? 'Укажите название статьи' : null)
            || (!news.title.trim() || !news.body.trim() ? 'Заполните заголовок и текст новости' : null)
            || quizProblem(news.quiz);

        return (
            <div className="space-y-5">
                <section className="space-y-2">
                    <p className={iosGroupLabel}>{draft.action === 'create' ? 'Новая статья' : 'Статья'}</p>
                    {draft.action === 'create' ? (
                        <input
                            value={draft.title || ''}
                            onChange={(e) => setDraft((prev) => ({ ...prev, title: e.target.value }))}
                            disabled={busy}
                            maxLength={255}
                            placeholder="Название статьи"
                            aria-label="Название статьи"
                            className={iosInput}
                        />
                    ) : (
                        <p className="text-[14px] font-medium text-slate-900">«{draft.title}»</p>
                    )}
                    {!!draft.changes?.length && (
                        <ul className="list-disc space-y-0.5 pl-5 text-[13px] leading-relaxed text-slate-700">
                            {draft.changes.map((line, index) => <li key={index}>{line}</li>)}
                        </ul>
                    )}
                    {!!notes.length && <Notes items={notes} />}
                    {/* Текст целиком — под раскрытием: читать всю статью, чтобы
                        проверить одну вписанную строку, незачем, а список
                        изменений выше для этого и нужен. */}
                    <details className="rounded-xl ring-1 ring-slate-200/70">
                        <summary className="cursor-pointer select-none px-3 py-2 text-[12.5px] text-slate-500 hover:text-slate-800">
                            Текст статьи целиком
                        </summary>
                        <div
                            className="wiki-prose max-h-[420px] overflow-y-auto border-t border-slate-100 px-3 py-2"
                            dangerouslySetInnerHTML={{ __html: draft.content || '' }}
                        />
                    </details>
                </section>

                <section className="space-y-2">
                    <p className={iosGroupLabel}>Новость для отдела</p>
                    <input
                        value={news.title}
                        onChange={(e) => setNews((prev) => ({ ...prev, title: e.target.value }))}
                        disabled={busy}
                        maxLength={255}
                        placeholder="Заголовок"
                        aria-label="Заголовок новости"
                        className={iosInput}
                    />
                    <textarea
                        value={news.body}
                        onChange={(e) => setNews((prev) => ({ ...prev, body: e.target.value }))}
                        disabled={busy}
                        rows={4}
                        placeholder="Текст новости"
                        aria-label="Текст новости"
                        className={`${iosInput} resize-y leading-relaxed`}
                    />
                    {!!news.warnings?.length && <Notes items={news.warnings} />}
                </section>

                <QuizEditor
                    quiz={news.quiz}
                    disabled={busy}
                    onChange={(quiz) => setNews((prev) => ({ ...prev, quiz }))}
                />

                <div className="space-y-2 border-t border-slate-100 pt-4">
                    {error && <p className="text-[12.5px] text-rose-600">{error}</p>}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <button
                            type="button"
                            onClick={() => { setStage('choose'); setError(''); }}
                            disabled={busy}
                            className={iosBtnSecondary}
                        >
                            Назад
                        </button>
                        <button
                            type="button"
                            onClick={publish}
                            disabled={busy || !!blocker}
                            className={iosBtnPrimary}
                        >
                            {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                            Опубликовать
                        </button>
                    </div>
                    <p className="text-right text-[12px] text-slate-400">
                        {blocker || 'Статья выйдет сразу, отдел увидит новость с тестом при входе'}
                    </p>
                </div>
            </div>
        );
    }

    // stage === 'choose'
    const candidates = targets?.candidates || [];
    const sections = targets?.sections || [];
    const defaultSection = targets?.suggested?.section_id ?? sections[0]?.id ?? null;
    return (
        <section className="space-y-2.5">
            <p className={iosGroupLabel}>Записать в базу знаний</p>
            <div className="overflow-hidden rounded-xl ring-1 ring-slate-200/70" role="radiogroup">
                {candidates.map((candidate) => (
                    <TargetOption
                        key={candidate.article_id}
                        icon={FileText}
                        selected={choice?.action === 'update' && choice.article_id === candidate.article_id}
                        disabled={!candidate.can_edit || busy}
                        onSelect={() => setChoice({ action: 'update', article_id: candidate.article_id })}
                        title={`Дополнить «${candidate.title}»`}
                        hint={candidate.can_edit ? candidate.heading_path : 'нет права править или публиковать'}
                    />
                ))}
                <TargetOption
                    icon={FilePlus2}
                    selected={choice?.action === 'create'}
                    disabled={!sections.length || busy}
                    onSelect={() => setChoice({ action: 'create', section_id: choice?.section_id ?? defaultSection })}
                    title="Новая статья"
                    hint={sections.length ? null : 'нет раздела, где вы вправе выпускать статьи'}
                />
            </div>
            {choice?.action === 'create' && sections.length > 0 && (
                <CustomSelect
                    value={choice.section_id ?? null}
                    onChange={(value) => setChoice({ action: 'create', section_id: value })}
                    options={sections.map((section) => ({
                        value: section.id,
                        label: section.parent_name ? `${section.parent_name} · ${section.name}` : section.name,
                    }))}
                    placeholder="Раздел для новой статьи"
                    variant="ios"
                    ariaLabel="Раздел для новой статьи"
                />
            )}
            {error && (
                <p className="text-[12.5px] text-rose-600">
                    {error}
                    {targetsFailed && (
                        <button type="button" onClick={loadTargets} className="ml-2 underline underline-offset-2">
                            Повторить
                        </button>
                    )}
                </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-0.5">
                <button type="button" onClick={skip} disabled={busy} className={iosBtnGhost}>
                    Не для базы знаний
                </button>
                <div className="flex flex-wrap items-center justify-end gap-2">
                    {/* Короткий путь без статьи: форма «Новостей» с заполненными
                        полями — поправить, приложить фото и выпустить. */}
                    <button
                        type="button"
                        onClick={() => onComposeNews?.({
                            questionId: item.id, draft: newsDraftFromQuestion(item), nonce: Date.now(),
                        })}
                        disabled={busy || !canComposeNews}
                        className={iosBtnSecondary}
                    >
                        <Megaphone size={15} /> Опубликовать как новость
                    </button>
                    <button
                        type="button"
                        onClick={prepare}
                        disabled={busy || !choice || (choice.action === 'create' && !choice.section_id)}
                        className={iosBtnPrimary}
                    >
                        <Sparkles size={15} /> Подготовить изменения
                    </button>
                </div>
            </div>
            {/* Вкладка «Новости» выключена настройками пространства — выпустить
                новость руками здесь негде, и молча серая кнопка это не объяснит. */}
            {!canComposeNews && (
                <p className="text-right text-[12px] text-slate-400">
                    Новостью не опубликовать: вкладка «Новости» выключена в настройках пространства
                </p>
            )}
        </section>
    );
}

function TargetOption({ icon: Icon, selected, disabled, onSelect, title, hint }) {
    return (
        <button
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={onSelect}
            className={`flex w-full items-start gap-2.5 border-b border-slate-100 px-3 py-2.5 text-left transition last:border-b-0 disabled:cursor-not-allowed disabled:opacity-50 ${
                selected ? 'bg-blue-50/70' : 'bg-white hover:bg-slate-50'
            }`}
        >
            <Icon size={15} className={`mt-[2px] shrink-0 ${selected ? 'text-blue-600' : 'text-slate-400'}`} />
            <span className="min-w-0 flex-1">
                <span className="block break-words text-[13.5px] text-slate-900">{title}</span>
                {hint && <span className="mt-0.5 block truncate text-[11.5px] text-slate-400">{hint}</span>}
            </span>
            {selected && <Check size={15} className="mt-[2px] shrink-0 text-blue-600" />}
        </button>
    );
}

function ProgressLine({ done, active, label }) {
    return (
        <div className={`flex items-center gap-2 text-[13px] ${done || active ? 'text-slate-700' : 'text-slate-400'}`}>
            {done
                ? <Check size={14} className="text-emerald-600" />
                : active ? <Loader2 size={14} className="animate-spin text-slate-400" /> : <span className="h-3.5 w-3.5" />}
            {label}
        </div>
    );
}

function Notes({ items }) {
    return (
        <ul className="space-y-1 rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] leading-relaxed text-amber-900 ring-1 ring-amber-200/70">
            {items.map((line, index) => <li key={index}>{line}</li>)}
        </ul>
    );
}

function IconButton({ label, onClick, disabled }) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            title={label}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-white hover:text-rose-500 active:scale-95 disabled:opacity-50"
        >
            <X size={14} />
        </button>
    );
}

/* Тест для окна новости. Верный вариант отмечается кружком слева — зелёным:
   здесь цвет несёт ровно один смысл, «это правильный ответ». */
function QuizEditor({ quiz, onChange, disabled }) {
    const update = (index, next) => onChange(quiz.map((item, i) => (i === index ? next : item)));
    return (
        <section className="space-y-2.5">
            <p className={iosGroupLabel}>Тест в окне новости</p>
            {quiz.map((item, index) => (
                <div key={index} className="space-y-2 rounded-xl bg-slate-50 p-3">
                    <div className="flex items-center gap-2">
                        <span className="w-4 shrink-0 text-right text-[12px] font-semibold tabular-nums text-slate-400">
                            {index + 1}
                        </span>
                        <input
                            value={item.prompt}
                            onChange={(e) => update(index, { ...item, prompt: e.target.value })}
                            disabled={disabled}
                            maxLength={300}
                            placeholder="Вопрос"
                            aria-label={`Вопрос ${index + 1}`}
                            className={`${iosInput} !bg-white`}
                        />
                        {quiz.length > QUIZ_MIN_QUESTIONS && (
                            <IconButton
                                label="Убрать вопрос"
                                disabled={disabled}
                                onClick={() => onChange(quiz.filter((_, i) => i !== index))}
                            />
                        )}
                    </div>
                    <div className="space-y-1.5 pl-6" role="radiogroup" aria-label={`Верный ответ на вопрос ${index + 1}`}>
                        {item.options.map((option, optionIndex) => {
                            const correct = item.correct === optionIndex;
                            return (
                                <div key={optionIndex} className="flex items-center gap-2">
                                    <button
                                        type="button"
                                        role="radio"
                                        aria-checked={correct}
                                        aria-label={`Вариант ${optionIndex + 1} — верный`}
                                        title="Верный вариант"
                                        disabled={disabled}
                                        onClick={() => update(index, { ...item, correct: optionIndex })}
                                        className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ring-1 transition ${
                                            correct
                                                ? 'bg-emerald-500 text-white ring-emerald-500'
                                                : 'bg-white ring-slate-300 hover:ring-slate-400'
                                        }`}
                                    >
                                        {correct && <Check size={12} strokeWidth={3} />}
                                    </button>
                                    <input
                                        value={option}
                                        onChange={(e) => update(index, {
                                            ...item,
                                            options: item.options.map((value, i) => (i === optionIndex ? e.target.value : value)),
                                        })}
                                        disabled={disabled}
                                        maxLength={200}
                                        placeholder={`Вариант ${optionIndex + 1}`}
                                        aria-label={`Вариант ${optionIndex + 1}`}
                                        className={`${iosInput} !bg-white !py-2`}
                                    />
                                    {item.options.length > QUIZ_MIN_OPTIONS && (
                                        <IconButton
                                            label="Убрать вариант"
                                            disabled={disabled}
                                            onClick={() => update(index, dropOption(item, optionIndex))}
                                        />
                                    )}
                                </div>
                            );
                        })}
                        {item.options.length < QUIZ_MAX_OPTIONS && (
                            <button
                                type="button"
                                disabled={disabled}
                                onClick={() => update(index, { ...item, options: [...item.options, ''] })}
                                className={`${iosBtnGhost} -ml-2 !px-2 !py-1 text-[12.5px]`}
                            >
                                <Plus size={13} /> Вариант
                            </button>
                        )}
                    </div>
                </div>
            ))}
            {quiz.length < QUIZ_MAX_QUESTIONS && (
                <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange([...quiz, emptyQuestion()])}
                    className={iosBtnGhost}
                >
                    <Plus size={14} /> Вопрос
                </button>
            )}
        </section>
    );
}
