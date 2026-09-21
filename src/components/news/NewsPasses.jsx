import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Check, Loader2, PlayCircle } from 'lucide-react';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';

/* Тест и тренажёр новости — у сотрудника (задача #342).
 *
 * «После публикации новости оператор должен иметь возможность открыть и пройти
 * прикреплённый тест… Если тест обязательный, оператор не должен иметь
 * возможности закрыть новость, не пройдя тест. Если необязательный — может
 * ознакомиться без прохождения».
 *
 * ОДИН блок на два места: окно «Новость дня» и ленту во вкладке «Новости» вики.
 * Две копии разошлись бы в том, что считается пройденным, — а журнал у
 * редактора один.
 *
 * ТРЕНАЖЁР ГРУЗИТСЯ ЛЕНИВО, и реестр тоже. Окно смонтировано в корне портала,
 * статический импорт утащил бы все сценарии с экранами в главный чанк каждому
 * вошедшему — а тренажёр прикреплён к единицам новостей.
 */

const TrainerModal = lazy(() => import('../wiki/trainers/TrainerPlayer'));

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Варианты — кнопками на всю ширину: окно читают и с телефона, между звонками,
 * и в кружок на 16 пикселей пальцем не попасть. Верного ответа здесь нет и не
 * бывает — сервер отдаёт только формулировки и сверяет сам.
 *
 * ЦВЕТА ОШИБКИ ЗДЕСЬ НЕТ. Неверный ответ не помечает вопрос — он снимает весь
 * выбор и показывает одно уведомление над тестом (useQuizAttempt, решение
 * владельца 21.09.2026). Подсветить вопрос значило бы вернуть подбор ответа
 * переключением одного варианта, а тест засчитывается, только когда все ответы
 * выбраны верно сразу.
 */
export function NewsQuiz({ quiz, answers, onAnswer, disabled = false }) {
    return (
        <div className="space-y-4">
            {quiz.map((item, index) => (
                <fieldset key={item.id} className="space-y-2" disabled={disabled}>
                    <legend className="text-[14px] font-medium leading-snug text-slate-900">
                        {index + 1}. {item.prompt}
                    </legend>
                    <div className="space-y-1.5" role="radiogroup">
                        {(item.options || []).map((option, optionIndex) => {
                            const chosen = answers[item.id] === optionIndex;
                            return (
                                <button
                                    key={optionIndex}
                                    type="button"
                                    role="radio"
                                    aria-checked={chosen}
                                    onClick={() => onAnswer(item.id, optionIndex)}
                                    className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-[14px] ring-1 transition active:scale-[0.99] ${
                                        chosen
                                            ? 'bg-indigo-50 text-slate-900 ring-indigo-200'
                                            : 'bg-white text-slate-700 ring-slate-200 hover:bg-slate-50'
                                    }`}
                                >
                                    <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full ring-1 ${
                                        chosen ? 'bg-indigo-600 ring-indigo-600' : 'bg-white ring-slate-300'
                                    }`}>
                                        {chosen && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                                    </span>
                                    <span className="min-w-0 break-words">{option}</span>
                                </button>
                            );
                        })}
                    </div>
                </fieldset>
            ))}
        </div>
    );
}

/* ОДНА ПОПЫТКА ТЕСТА: выбранные варианты и отказ сервера по ним.
 *
 * Живёт СНАРУЖИ NewsPasses, потому что обязательный тест сверяет не кнопка
 * «Проверить», а само подтверждение окна: ответы нужны и ему.
 *
 * ПРАВИЛО ВЛАДЕЛЬЦА (21.09.2026) дословно: «если один вариант не правилен,
 * ответы сбрасываются и выходит уведомление о том что ответы не правильные и
 * попробовать заново, тест будет завершен если он все ответы выберет
 * корректно». Отсюда fail(): снимает ВЕСЬ выбор, а не помечает вопрос.
 *
 * Правило одно на окно и ленту — и держится оно здесь, в общем хуке. Копия в
 * каждом из мест разъехалась бы, а журнал прохождений у редактора один.
 */
export function useQuizAttempt(postId) {
    const [answers, setAnswers] = useState({});
    const [failed, setFailed] = useState(false);
    /* Итог непройденной попытки от сервера: сколько верных и сколько нужно
       (ТЗ #300, п.4). КАКИЕ вопросы неверны, сервер не отдаёт и здесь их нет —
       подсветка вернула бы подбор ответа переключением одного варианта. */
    const [score, setScore] = useState(null);
    // Следующая новость — свой тест с чистого листа.
    useEffect(() => { setAnswers({}); setFailed(false); setScore(null); }, [postId]);
    const answer = useCallback((questionId, index) => {
        setAnswers((prev) => ({ ...prev, [questionId]: index }));
        // Уведомление было про прошлую попытку — человек уже отвечает заново.
        setFailed(false);
    }, []);
    const fail = useCallback((detail) => {
        setAnswers({});
        setFailed(true);
        setScore(detail && Number.isFinite(detail.total) ? detail : null);
    }, []);
    /* Ссылка на объект стабильна: попытку кладут в зависимости обработчиков, и
       новый объект на каждый рендер пересобирал бы их без всякой причины. */
    return useMemo(() => ({ answers, failed, score, answer, fail }),
                   [answers, failed, score, answer, fail]);
}

/* Название тренажёра по ключу. Реестр грузится динамически и один раз на
   вкладку: import() кеширует модуль сам. */
function useTrainerScenario(trainerKey) {
    const [state, setState] = useState({ key: null, scenario: null, loaded: false });
    useEffect(() => {
        if (!trainerKey) return undefined;
        let alive = true;
        import('../wiki/trainers/registry')
            .then((module) => {
                if (alive) setState({ key: trainerKey, scenario: module.findTrainer(trainerKey), loaded: true });
            })
            .catch(() => { if (alive) setState({ key: trainerKey, scenario: null, loaded: true }); });
        return () => { alive = false; };
    }, [trainerKey]);
    return state.key === trainerKey ? state : { key: trainerKey, scenario: null, loaded: false };
}

/* Проигрыватель поверх окна. «Назад» на телефоне закрывает урок, а не новость:
   запись в стеке кладётся ПОЗЖЕ записи окна и снимается первой. */
function TrainerLauncher({ scenario, open, onClose, onFinished, layer }) {
    const isMobileShell = useIsMobileShell();
    useScreenBackGesture(isMobileShell && open, onClose);
    if (!open || !scenario) return null;
    return (
        <Suspense fallback={(
            <div className={`fixed inset-0 ${layer === 'top' ? 'z-[130]' : 'z-[95]'} flex items-center justify-center gap-2 bg-slate-900/40 text-white backdrop-blur-md`}>
                <Loader2 className="h-[18px] w-[18px] animate-spin" aria-hidden="true" />
                <span className="text-[13px]">Готовим тренажёр…</span>
            </div>
        )}>
            {/* record нет намеренно: попытки вики пишутся роутами за тумблером
                отдела и QR-подтверждением, а тренажёр новости проходит и тот, у
                кого вики нет. Прохождение новости уходит своим роутом. */}
            <TrainerModal scenario={scenario} onClose={onClose} onFinished={onFinished} layer={layer} />
        </Suspense>
    );
}

function PassedRow({ children }) {
    return (
        <div className="flex items-center gap-2.5 rounded-2xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-500 text-white">
                <Check className="h-3.5 w-3.5" strokeWidth={3} aria-hidden="true" />
            </span>
            <span className="text-[14px] text-slate-900">{children}</span>
        </div>
    );
}

/**
 * Блок под текстом новости: тренажёр строкой и тест.
 *
 * post        — { id, quiz, trainer_key, pass_required, quiz_passed, trainer_passed }
 * attempt     — попытка теста из useQuizAttempt; ответами из неё окно
 *               подтверждает и обязательный тест
 * checkable   — показывать ли «Проверить». В окне — только у НЕОБЯЗАТЕЛЬНОГО теста:
 *               обязательный сверяется самим подтверждением.
 * layer       — 'top' в окне: тренажёр встаёт над его z-index.
 */
export default function NewsPasses({
    post, apiBaseUrl, headers, attempt,
    quizPassed, onQuizPassed, trainerPassed, onTrainerPassed,
    trainerOpen, onTrainerOpenChange, checkable, layer = null,
}) {
    const quiz = post?.quiz || [];
    const trainerKey = post?.trainer_key || null;
    const required = !!post?.pass_required;
    const { scenario, loaded } = useTrainerScenario(trainerKey);
    const [checking, setChecking] = useState(false);
    const [markFailed, setMarkFailed] = useState(false);
    const [error, setError] = useState('');
    const failRef = useRef(null);

    useEffect(() => { setMarkFailed(false); setError(''); }, [post?.id]);

    /* К уведомлению подводим прокруткой: выбор снят у всех вопросов, и отвечать
       человек начинает сверху, а кнопка, по которой он только что щёлкнул,
       стоит под длинным тестом. */
    useEffect(() => {
        if (attempt.failed) failRef.current?.scrollIntoView({ block: 'center' });
    }, [attempt.failed]);

    const markTrainer = useCallback(() => {
        if (!post?.id) return;
        setMarkFailed(false);
        axios.post(`${apiBaseUrl}/api/news/${post.id}/trainer`, {}, { headers })
            .then(() => onTrainerPassed?.())
            /* Урок пройден, а отметка не доехала: заставлять проходить заново
               нельзя — даём повторить одну отметку. */
            .catch(() => setMarkFailed(true));
    }, [apiBaseUrl, headers, onTrainerPassed, post?.id]);

    const quizAnswered = quiz.every((item) => Number.isInteger(attempt.answers[item.id]));

    const check = () => {
        if (checking || !quizAnswered || !post?.id) return;
        setChecking(true);
        setError('');
        axios.post(`${apiBaseUrl}/api/news/${post.id}/quiz`, { answers: attempt.answers }, { headers })
            .then(() => onQuizPassed?.())
            .catch((e) => {
                // Хоть один неверный — попытка целиком не засчитана: выбор
                // снимается, и тест проходится заново (решение владельца).
                if (e?.response?.data?.code === 'NEWS_QUIZ_WRONG') {
                    attempt.fail(e.response.data);
                    return;
                }
                setError(errText(e, 'Не удалось проверить ответы'));
            })
            .finally(() => setChecking(false));
    };

    if (!quiz.length && !trainerKey) return null;

    /* Подпись под названием тренажёра — что это за урок, а после прохождения —
       «Пройден». Обязательность здесь не повторяется: её говорит одна подпись
       блока («по желанию») или кнопка окна, которая не загорится. */
    const trainerStatus = trainerPassed
        ? 'Пройден'
        : (!loaded ? '' : !scenario ? 'Недоступен в этой версии портала' : (scenario.subtitle || ''));
    const heading = trainerKey && quiz.length ? 'Тренажёр и тест' : (trainerKey ? 'Тренажёр' : 'Тест');

    return (
        <div className="mt-5 space-y-3 border-t border-slate-100 pt-4">
            {/* Подпись «по желанию» — ОДНА на блок. У тренажёра и у теста по
                своей она стояла бы дважды на одном экране. */}
            <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-[15px] font-semibold text-slate-900">{heading}</h3>
                {!required && <span className="text-[12px] text-slate-400">по желанию</span>}
            </div>
            {trainerKey && (
                <div className="flex items-center gap-3 rounded-2xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                        <PlayCircle className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <div className="min-w-0 flex-1">
                        {/* Название переносится, а не режется: на телефоне
                            «Смена провайдера ЭД…» не говорит, о чём урок. */}
                        <p className="line-clamp-2 break-words text-[14px] font-medium leading-snug text-slate-900">
                            {scenario?.title || 'Тренажёр'}
                        </p>
                        <p className="flex min-w-0 items-center gap-1 text-[12px] text-slate-500">
                            {trainerPassed && <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" strokeWidth={3} aria-hidden="true" />}
                            <span className="truncate">{trainerStatus}</span>
                        </p>
                        {markFailed && (
                            <p className="mt-0.5 text-[12px] text-rose-600">
                                Прохождение не сохранилось.{' '}
                                <button type="button" onClick={markTrainer} className="font-medium underline-offset-2 hover:underline">
                                    Повторить
                                </button>
                            </p>
                        )}
                    </div>
                    {scenario && (
                        <button
                            type="button"
                            onClick={() => onTrainerOpenChange(true)}
                            className={`inline-flex h-9 shrink-0 items-center justify-center rounded-xl px-4 text-[13.5px] font-semibold transition active:scale-[0.98] ${
                                trainerPassed
                                    ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                    : 'bg-indigo-600 text-white hover:bg-indigo-700'
                            }`}
                        >
                            {trainerPassed ? 'Ещё раз' : 'Пройти'}
                        </button>
                    )}
                </div>
            )}

            {quiz.length > 0 && (quizPassed ? (
                /* «Тест» уже сказан заголовком блока — повторяем слово только
                   рядом с тренажёром, где строк две. */
                <PassedRow>{trainerKey ? 'Тест пройден' : 'Пройден'}</PassedRow>
            ) : (
                <section className={`space-y-3 ${trainerKey ? 'pt-2' : ''}`}>
                    {/* Уведомление ОДНО на весь тест и стоит над вопросами:
                        какой именно ответ неверен, не говорит ни оно, ни
                        сервер — иначе ответ подбирался бы переключением
                        одного варианта. */}
                    {attempt.failed && (
                        <div
                            ref={failRef}
                            role="alert"
                            className="flex items-start gap-2 rounded-xl bg-rose-50 px-3 py-2.5 text-[13px] leading-snug text-rose-700 ring-1 ring-rose-200"
                        >
                            <AlertCircle className="mt-px h-4 w-4 shrink-0" aria-hidden="true" />
                            {/* Со строгим порогом — прежняя фраза. С мягким
                                («нужно 3 из 5») без чисел было бы непонятно,
                                почему тест не засчитан: часть ответов верна. */}
                            <span>
                                {attempt.score && attempt.score.needed < attempt.score.total
                                    ? `Верных ответов ${attempt.score.correct} из ${attempt.score.total}, `
                                      + `нужно ${attempt.score.needed} — выбор сброшен, пройдите тест заново`
                                    : 'Ответы неверные — выбор сброшен, пройдите тест заново'}
                            </span>
                        </div>
                    )}
                    <NewsQuiz
                        quiz={quiz}
                        answers={attempt.answers}
                        disabled={checking}
                        onAnswer={attempt.answer}
                    />
                    {checkable && (
                        <div className="flex items-center justify-end gap-3">
                            {error && <span className="mr-auto text-[12px] text-rose-600">{error}</span>}
                            <button
                                type="button"
                                onClick={check}
                                disabled={!quizAnswered || checking}
                                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-slate-100 px-4 text-[13.5px] font-semibold text-slate-700 transition hover:bg-slate-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 max-sm:w-full"
                            >
                                {checking && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                                Проверить
                            </button>
                        </div>
                    )}
                </section>
            ))}

            <TrainerLauncher
                scenario={scenario}
                open={!!trainerOpen}
                layer={layer}
                onClose={() => onTrainerOpenChange(false)}
                onFinished={markTrainer}
            />
        </div>
    );
}
