import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Check, ChevronRight, Image as ImageIcon, ListChecks, Loader2, PlayCircle } from 'lucide-react';
import { iosBtnSecondary, iosCard, IosModal } from '../ui/ios';
import NewsGallery from './NewsGallery';
import NewsPasses from './NewsPasses';
import { publishedLabel } from './newsShared';
import './news-modal.css';

/* Лента «мои новости» во вкладке «Новости» вики (задача #342).
 *
 * Решение владельца 17.09.2026: вкладка открыта всем, и тому, у кого права
 * только на чтение, она показывает «только сами новости, которые ему были
 * предназначены». Редактор видит ту же ленту отдельным сегментом — новости
 * сверху приходят и ему.
 *
 * Зачем она, если новость и так приходит окном. Окно показывается один раз и
 * до подтверждения; после — объявление нигде не перечитать, а постановка
 * требует, чтобы прикреплённый тест можно было «открыть и пройти» после
 * публикации. Необязательный тест, закрытый вместе с окном, проходят здесь.
 *
 * Строка — как письмо в «Почте» iOS: точка у непрочитанного, заголовок, дата
 * справа, две строки текста. Автора нет, как и в окне (решение владельца
 * 01.09.2026: сотрудник читает объявление, а не карточку автора).
 */

const PAGE = 20;

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Короткая дата для строки: время у ленты не нужно, а «сегодня» — нужно. */
const shortDate = (iso) => {
    if (!iso) return '';
    const label = publishedLabel(iso);
    if (label.startsWith('сегодня')) return 'сегодня';
    const at = new Date(iso);
    const sameYear = at.getFullYear() === new Date().getFullYear();
    return at.toLocaleDateString('ru-RU', sameYear
        ? { day: 'numeric', month: 'short' }
        : { day: 'numeric', month: 'short', year: 'numeric' }).replace('.', '');
};

/* Метки строки — серым и только о том, что в новости ЕСТЬ. Зелёная галочка —
   единственный цвет: «пройдено». */
function RowMarks({ item }) {
    const marks = [];
    if (item.photo_count > 0) {
        marks.push(
            <span key="photos" className="inline-flex items-center gap-1 tabular-nums">
                <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />{item.photo_count}
            </span>,
        );
    }
    if (item.trainer_key) {
        marks.push(
            <span key="trainer" className="inline-flex items-center gap-1">
                <PlayCircle className="h-3.5 w-3.5" aria-hidden="true" />Тренажёр
                {item.trainer_passed && <Check className="h-3.5 w-3.5 text-emerald-600" strokeWidth={3} aria-label="пройден" />}
            </span>,
        );
    }
    if (item.quiz_count > 0) {
        marks.push(
            <span key="quiz" className="inline-flex items-center gap-1">
                <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />Тест
                {item.quiz_passed && <Check className="h-3.5 w-3.5 text-emerald-600" strokeWidth={3} aria-label="пройден" />}
            </span>,
        );
    }
    if (!marks.length) return null;
    return <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-400">{marks}</div>;
}

function FeedPost({ open, postId, apiBaseUrl, headers, onClose, onPassed }) {
    const [post, setPost] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [answers, setAnswers] = useState({});
    const [wrong, setWrong] = useState([]);
    const [quizPassed, setQuizPassed] = useState(false);
    const [trainerPassed, setTrainerPassed] = useState(false);
    const [trainerOpen, setTrainerOpen] = useState(false);

    useEffect(() => {
        if (!open || !postId) return undefined;
        let alive = true;
        setLoading(true);
        setError('');
        setPost(null);
        setAnswers({});
        setWrong([]);
        setTrainerOpen(false);
        axios.get(`${apiBaseUrl}/api/news/feed/${postId}`, { headers })
            .then((r) => {
                if (!alive) return;
                setPost(r.data);
                setQuizPassed(!!r.data?.quiz_passed);
                setTrainerPassed(!!r.data?.trainer_passed);
            })
            .catch((e) => { if (alive) setError(errText(e, 'Не удалось открыть новость')); })
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [apiBaseUrl, headers, open, postId]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            /* Заголовок новости — в теле, целиком: шапка окна режет строку, а
               два одинаковых заголовка подряд были бы дублем. */
            title="Новость"
            subtitle={post?.published_at ? publishedLabel(post.published_at) : undefined}
            maxWidth="max-w-xl"
        >
            {loading && (
                <p className="py-10 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Открываем
                </p>
            )}
            {error && <p className="py-6 text-center text-[13px] text-rose-600">{error}</p>}
            {post && (
                <div className={`${iosCard} px-4 py-4 sm:px-5`}>
                    <h2 className="text-[18px] font-semibold leading-snug text-slate-900">{post.title}</h2>
                    <div className="mt-3">
                        <NewsGallery photos={post.photos} />
                        <div className="news-body" dangerouslySetInnerHTML={{ __html: post.body || '' }} />
                    </div>
                    {/* В ленте «Проверить» есть у любого непройденного теста:
                        подтверждения здесь нет, и пройти тест можно только так. */}
                    <NewsPasses
                        post={post}
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        answers={answers}
                        onAnswer={(questionId, index) => setAnswers((prev) => ({ ...prev, [questionId]: index }))}
                        wrong={wrong}
                        onWrong={setWrong}
                        quizPassed={quizPassed}
                        onQuizPassed={() => { setQuizPassed(true); onPassed?.(post.id, 'quiz'); }}
                        trainerPassed={trainerPassed}
                        onTrainerPassed={() => { setTrainerPassed(true); onPassed?.(post.id, 'trainer'); }}
                        trainerOpen={trainerOpen}
                        onTrainerOpenChange={setTrainerOpen}
                        checkable
                    />
                </div>
            )}
        </IosModal>
    );
}

/* spaceId — пространство, из которого открыта вкладка. Лента спрашивает
   новости ЭТОЙ вики: «Новости» в «Тез» — это новости Тез, а не всё, что
   человеку когда-либо адресовали (решение владельца 18.09.2026). */
export default function NewsFeed({ apiBaseUrl, headers, spaceId = null }) {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [more, setMore] = useState(false);
    const [error, setError] = useState('');
    const [openId, setOpenId] = useState(null);
    const loadedRef = useRef(0);

    const load = useCallback((offset = 0) => {
        if (offset) setMore(true); else setLoading(true);
        return axios.get(`${apiBaseUrl}/api/news/feed`,
                         { headers, params: { limit: PAGE, offset, space_id: spaceId } })
            .then((r) => {
                const page = r.data?.items || [];
                setItems((prev) => (offset ? [...prev, ...page] : page));
                setTotal(Number(r.data?.total) || 0);
                loadedRef.current = offset + page.length;
                setError('');
            })
            .catch((e) => setError(errText(e, 'Не удалось загрузить новости')))
            .finally(() => { setLoading(false); setMore(false); });
    }, [apiBaseUrl, headers, spaceId]);

    useEffect(() => { load(0); }, [load]);

    /* Прошёл тест или тренажёр в карточке — галочка в строке сразу, без
       перезапроса всей ленты. */
    const markPassed = useCallback((id, kind) => {
        setItems((prev) => prev.map((item) => (item.id !== id ? item : {
            ...item,
            ...(kind === 'quiz' ? { quiz_passed: true } : { trainer_passed: true }),
        })));
    }, []);

    if (loading) {
        return (
            <p className="py-10 text-center text-[13px] text-slate-400">
                <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Загружаем
            </p>
        );
    }

    return (
        <div className="space-y-3">
            {error && <p className="text-[13px] text-rose-600">{error}</p>}

            {!error && items.length === 0 && (
                <div className={`${iosCard} px-6 py-10 text-center`}>
                    <p className="text-[14px] text-slate-900">Новостей пока нет</p>
                    <p className="mt-1 text-[13px] text-slate-400">Здесь появятся новости, адресованные вам</p>
                </div>
            )}

            {items.length > 0 && (
                <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {items.map((item) => (
                        <li key={item.id}>
                            <button
                                type="button"
                                onClick={() => setOpenId(item.id)}
                                className="flex w-full items-start gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50 active:bg-slate-100"
                            >
                                {/* Точка — у неподтверждённой: тот же смысл, что у
                                    непрочитанного письма. Место под неё держится
                                    всегда, иначе заголовки прыгали бы по строкам. */}
                                <span className="mt-[7px] flex h-2 w-2 shrink-0 items-center justify-center" aria-hidden="true">
                                    {!item.confirmed && <span className="h-2 w-2 rounded-full bg-blue-500" />}
                                </span>
                                <span className="min-w-0 flex-1">
                                    <span className="flex items-baseline justify-between gap-3">
                                        <span className={`truncate text-[15px] text-slate-900 ${item.confirmed ? 'font-medium' : 'font-semibold'}`}>
                                            {item.title}
                                        </span>
                                        <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
                                            {shortDate(item.published_at)}
                                        </span>
                                    </span>
                                    {item.preview && (
                                        <span className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-slate-500">
                                            {item.preview}
                                        </span>
                                    )}
                                    <RowMarks item={item} />
                                </span>
                                <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-300" aria-hidden="true" />
                            </button>
                        </li>
                    ))}
                </ul>
            )}

            {items.length < total && (
                <div className="flex justify-center">
                    <button type="button" className={iosBtnSecondary} disabled={more}
                            onClick={() => load(loadedRef.current)}>
                        {more && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                        Показать ещё
                    </button>
                </div>
            )}

            <FeedPost
                open={openId !== null}
                postId={openId}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onClose={() => setOpenId(null)}
                onPassed={markPassed}
            />
        </div>
    );
}
