/* Общие кирпичи экранов результатов: «Опросы» и результаты новостей.
 *
 * Вынесены из SurveysView.jsx (23.09.2026), когда такой же экран понадобился
 * новостям: плитки итогов, строка распределения ответов по варианту и разбор
 * попытки со всеми вариантами. Копия в разделе новостей разошлась бы с
 * оригиналом на первой же правке — и одно и то же «Верно · выбрано» читалось
 * бы в двух разделах по-разному.
 *
 * Разбор попытки перенесён ДОСЛОВНО: «Опросы» показывают его как раньше.
 */

import React from 'react';
import FaIcon from '../common/FaIcon';

export const toUniqueTrimmedList = (values) => {
    const source = Array.isArray(values) ? values : [];
    const normalized = [];
    source.forEach((value) => {
        const text = String(value || '').trim();
        if (text && !normalized.includes(text)) normalized.push(text);
    });
    return normalized;
};

export const formatPoints = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return String(Math.round(number * 100) / 100);
};

// Цвет результата — один на все места показа (карточка сотрудника, карточка
// запуска, шапка разбора): пороги, разъехавшиеся между экранами, читались бы
// как разные оценки одного и того же процента.
export const scoreToneClass = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'text-slate-400';
    if (number >= 80) return 'text-emerald-600';
    return number >= 60 ? 'text-blue-600' : 'text-amber-600';
};

// Процент с одним знаком после запятой, без «,0»: «62%», «37.5%».
export const formatPercent = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '0%';
    return `${number.toFixed(1).replace(/\.0$/, '')}%`;
};

export const percentToWidth = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return '0%';
    return `${Math.max(0, Math.min(100, number))}%`;
};

export const Badge = ({ children, color = 'gray' }) => {
    const colors = {
        green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
        blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
        amber: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
        gray: 'bg-slate-100 text-slate-600',
    };
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${colors[color]}`}>
            {children}
        </span>
    );
};

/* ─── Разбор ответа: чистые правила, общие для всех трёх мест показа ───
   Ими пользуются вкладка «Ответы» у руководителя, свой результат у оператора
   и статистика. Раньше это были useCallback внутри компонента — переиспользовать
   их из отдельного блока разбора попытки было нечем. */

export const formatQuestionAnswerText = (question, answer) => {
    if (!question || !answer) return '—';
    if (question.type === 'rating') {
        const rating = Number(answer.rating_value);
        return Number.isFinite(rating) ? `${rating}` : '—';
    }

    const selectedOptions = Array.isArray(answer.selected_options)
        ? answer.selected_options.map((item) => String(item || '').trim()).filter(Boolean)
        : [];
    const otherText = String(answer.answer_text || '').trim();

    if (selectedOptions.length > 0 && otherText) {
        return `${selectedOptions.join(', ')}; Другое: ${otherText}`;
    }
    if (selectedOptions.length > 0) {
        return selectedOptions.join(', ');
    }
    if (otherText) {
        return `Другое: ${otherText}`;
    }
    return '—';
};

export const getExpectedOptionsForTest = (question, answer) => {
    const fromAnswer = toUniqueTrimmedList(answer?.expected_options);
    if (fromAnswer.length > 0) return fromAnswer;
    return toUniqueTrimmedList(question?.correct_options);
};

export const isTestAnswerCorrect = (question, answer) => {
    if (!question || !answer) return false;
    if (typeof answer?.is_correct === 'boolean') return answer.is_correct;

    const type = String(question?.type || '');
    const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
    const answerText = String(answer?.answer_text || '').trim();
    const expectedOptions = getExpectedOptionsForTest(question, answer);

    if (type === 'single') {
        return (
            expectedOptions.length === 1
            && selectedOptions.length === 1
            && selectedOptions[0] === expectedOptions[0]
            && !answerText
        );
    }
    if (type === 'multiple') {
        return (
            expectedOptions.length > 0
            && selectedOptions.length === expectedOptions.length
            && expectedOptions.every((option) => selectedOptions.includes(option))
            && !answerText
        );
    }
    return false;
};

// Частичный зачёт: сервер присылает и признак, и начисленный балл.
export const isTestAnswerPartiallyCorrect = (answer) => (
    answer?.is_partially_correct === true
    || (answer?.is_correct !== true && Number(answer?.earned_points) > 0)
);

export const testAnswerStatusMeta = (question, answer, hasAnswer) => {
    if (!hasAnswer) return { label: 'Нет ответа', color: 'gray' };
    if (isTestAnswerCorrect(question, answer)) return { label: 'Верно', color: 'green' };
    if (isTestAnswerPartiallyCorrect(answer)) return { label: 'Частично', color: 'blue' };
    return { label: 'Неверно', color: 'amber' };
};

export const hasSurveyAnswer = (question, answer) => {
    if (!question || !answer) return false;
    if (question.type === 'rating') {
        return Number.isFinite(Number(answer?.rating_value));
    }
    const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
    const answerText = String(answer?.answer_text || '').trim();
    return selectedOptions.length > 0 || answerText.length > 0;
};

/* ─── Разбор попытки ───
 *
 * В тесте показываем ВСЕ варианты, как на любом тестовом сайте: видно и что
 * человек выбрал, и что было правильным, и — главное — чего он НЕ выбрал,
 * хотя следовало. Строкой «правильный ответ: Город, Номер телефона» это не
 * читается: глазами приходится сопоставлять два списка.
 *
 * Правильные варианты рисуем только если они известны: оператору внутри
 * открытого окна теста сервер их не отдаёт, и «пустой» разбор не должен
 * превращаться в подсказку.
 */
export const ReviewOptionRow = ({ option, isMultiple, isChosen, isCorrect, revealCorrect }) => {
    // Красный — только там, где человек ОШИБСЯ: выбрал вариант, который
    // оказался неверным. Прочие неверные варианты красить не надо — их никто
    // не выбирал, и три красные строки из четырёх читались бы как «всё плохо»
    // вместо «вот здесь ошибка». Зелёный остаётся у правильных: и у того,
    // что человек угадал, и у того, что пропустил.
    const rightChoice = revealCorrect && isChosen && isCorrect;
    const wrongChoice = revealCorrect && isChosen && !isCorrect;
    const missedRight = revealCorrect && !isChosen && isCorrect;

    const tone = rightChoice
        ? 'bg-emerald-50 ring-emerald-200'
        : wrongChoice
            ? 'bg-rose-50 ring-rose-300'
            : missedRight
                ? 'bg-emerald-50/50 ring-emerald-200'
                : (isChosen ? 'bg-slate-100 ring-slate-200' : 'bg-white ring-slate-200/70');

    const markTone = rightChoice
        ? 'border-emerald-500 bg-emerald-500 text-white'
        : wrongChoice
            ? 'border-rose-500 bg-rose-500 text-white'
            : missedRight
                ? 'border-emerald-400 text-emerald-600'
                : (isChosen ? 'border-slate-400 bg-slate-400 text-white' : 'border-slate-300 text-transparent');

    const textTone = rightChoice || missedRight
        ? 'text-emerald-900'
        : (wrongChoice ? 'text-rose-900' : 'text-slate-700');

    return (
        <div className={`flex items-center gap-2.5 rounded-xl px-3 py-2 ring-1 transition-colors ${tone}`}>
            <span
                className={`grid h-[18px] w-[18px] shrink-0 place-items-center border-2 text-[9px] ${
                    isMultiple ? 'rounded-[5px]' : 'rounded-full'
                } ${markTone}`}
            >
                <FaIcon className={`fas ${wrongChoice ? 'fa-times' : 'fa-check'}`} />
            </span>
            <span className={`min-w-0 flex-1 break-words text-[13px] ${textTone}`}>
                {option}
            </span>
            <span className="shrink-0 text-[11px] font-medium">
                {rightChoice && <span className="text-emerald-700">Верно · выбрано</span>}
                {wrongChoice && <span className="text-rose-600">Неверно · выбрано</span>}
                {missedRight && <span className="text-emerald-700">Правильный</span>}
                {!revealCorrect && isChosen && <span className="text-slate-500">Выбрано</span>}
            </span>
        </div>
    );
};

export const AttemptReview = ({ questions = [], getAnswer, isTest = false, selfView = false }) => {
    if (!questions.length) {
        return <div className="py-6 text-center text-[13px] text-slate-400">В этом опросе нет вопросов</div>;
    }
    return (
        <div className="space-y-2.5">
            {questions.map((question, index) => {
                const answer = getAnswer(question, index);
                const resolvedQuestion = answer?.__question || question;
                const hasAnswer = hasSurveyAnswer(resolvedQuestion, answer);
                const expectedOptions = isTest ? getExpectedOptionsForTest(resolvedQuestion, answer) : [];
                const revealCorrect = isTest && expectedOptions.length > 0;
                const status = isTest ? testAnswerStatusMeta(resolvedQuestion, answer, hasAnswer) : null;
                const earnedPoints = Number(answer?.earned_points);
                const selectedOptions = toUniqueTrimmedList(answer?.selected_options);
                const otherText = String(answer?.answer_text || '').trim();
                const options = toUniqueTrimmedList(resolvedQuestion?.options);
                const type = String(resolvedQuestion?.type || '');

                return (
                    <div key={`review_${index}_${resolvedQuestion?.id || 'q'}`} className="rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/70">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                                    Вопрос {index + 1}
                                </div>
                                <div className="mt-0.5 text-[13.5px] font-medium text-slate-900">
                                    {resolvedQuestion?.text || `Вопрос ${index + 1}`}
                                </div>
                            </div>
                            {status && (
                                <div className="flex shrink-0 items-center gap-2">
                                    <Badge color={status.color}>{status.label}</Badge>
                                    {Number.isFinite(earnedPoints) && (
                                        <span className="text-[11px] tabular-nums text-slate-400">
                                            {formatPoints(earnedPoints)} / {formatPoints(resolvedQuestion?.points)}
                                        </span>
                                    )}
                                </div>
                            )}
                        </div>

                        <div className="mt-2.5 space-y-1.5">
                            {/* Тест — всегда полный список вариантов. Обычный опрос
                                этого не требует: там нет «правильного», и пять строк
                                вместо одного ответа были бы шумом. */}
                            {isTest && type !== 'rating' && options.length > 0 && options.map((option) => (
                                <ReviewOptionRow
                                    key={`review_${index}_opt_${option}`}
                                    option={option}
                                    isMultiple={type === 'multiple'}
                                    isChosen={selectedOptions.includes(option)}
                                    isCorrect={expectedOptions.includes(option)}
                                    revealCorrect={revealCorrect}
                                />
                            ))}

                            {(!isTest || type === 'rating' || options.length === 0) && (
                                <div className={`rounded-xl px-3 py-2.5 text-[13px] ${
                                    !hasAnswer ? 'bg-slate-50 text-slate-400' : 'bg-slate-50 text-slate-800'
                                }`}>
                                    {hasAnswer
                                        ? (type === 'rating'
                                            ? `${formatQuestionAnswerText(resolvedQuestion, answer)} из 5`
                                            : formatQuestionAnswerText(resolvedQuestion, answer))
                                        : 'Нет ответа'}
                                </div>
                            )}

                            {isTest && type !== 'rating' && options.length > 0 && !hasAnswer && (
                                <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-400">
                                    {selfView ? 'Вы не ответили на этот вопрос' : 'Сотрудник не ответил на этот вопрос'}
                                </div>
                            )}

                            {isTest && otherText && (
                                <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-600">
                                    Свой вариант: {otherText}
                                </div>
                            )}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};

/* ─── Плитки итогов ───
 * Сводка над результатами: до неё, чтобы понять «как прошло», приходилось
 * складывать числа глазами. Четыре плитки, у каждой подпись и число; hint —
 * маленькая строка под числом (доля, «из скольких»), когда без неё число
 * читается неоднозначно. */
export const StatTiles = ({ tiles = [] }) => {
    const shown = tiles.filter(Boolean);
    // Три плитки — три колонки: пустая четвёртая ячейка читалась бы как
    // не догрузившаяся цифра. Классы полные строками — иначе Tailwind их
    // не соберёт.
    const columns = shown.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-4';
    return (
    <div className={`grid grid-cols-2 gap-2 ${columns}`}>
        {shown.map((tile) => (
            <div key={tile.key} className="rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                    {tile.label}
                </div>
                <div className="mt-1 flex items-baseline gap-1.5">
                    <span className={`text-[19px] font-semibold tabular-nums leading-none ${tile.tone || 'text-slate-900'}`}>
                        {tile.value}
                    </span>
                    {tile.hint && (
                        <span className="text-[11.5px] tabular-nums text-slate-400">{tile.hint}</span>
                    )}
                </div>
            </div>
        ))}
    </div>
    );
};

/* ─── Строка распределения ответов по варианту ───
 * Цветом выделен только правильный вариант: он единственный здесь несёт
 * смысл, остальные полосы нейтральные, иначе рябит. Самый частый неверный —
 * чуть темнее: это и есть ответ на вопрос «что людям показалось правильным». */
export const OptionStatRow = ({ label, count, percent, isCorrect = false, isLeader = false }) => (
    <div className={`rounded-xl px-3 py-2 ring-1 ${
        isCorrect ? 'bg-emerald-50/60 ring-emerald-200' : 'bg-white ring-slate-200/70'
    }`}
    >
        <div className="flex items-center justify-between gap-2 text-[12.5px]">
            <span className={`min-w-0 flex-1 truncate ${isCorrect ? 'font-medium text-emerald-900' : 'text-slate-700'}`} title={label}>
                {label}
                {isCorrect && (
                    <FaIcon className="fas fa-check ml-1.5 text-[10px] text-emerald-600" />
                )}
            </span>
            <span className="shrink-0 tabular-nums text-slate-500">
                {count} · {formatPercent(percent)}
            </span>
        </div>
        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
                className={`h-full rounded-full transition-all duration-500 ${
                    isCorrect ? 'bg-emerald-500' : (isLeader ? 'bg-slate-500' : 'bg-slate-300')
                }`}
                style={{ width: percentToWidth(percent) }}
            />
        </div>
    </div>
);
