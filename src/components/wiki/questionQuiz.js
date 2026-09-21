/* Тест в окне новости — правила формы: «Записать в базу знаний» во вкладке
 * «Вопросы» (задача #321) и форма новости во вкладке «Новости» (кнопки
 * «Добавить вопросы» и «Составить ИИ»). Редактор у обеих один —
 * news/NewsQuizEditor.jsx.
 *
 * Отдельным модулем без React, чтобы правила проверялись node-тестом
 * (tests/question_quiz.test.mjs). Границы — те же, что у сервера
 * (news/schema.py: QUIZ_*), и их совпадение сверяет
 * tests/test_wiki_operator_questions.py: разойдись они, форма пускала бы к
 * публикации тест, который сервер отвергнет, — или не пускала бы годный.
 */

export const QUIZ_MIN_QUESTIONS = 1;
export const QUIZ_MAX_QUESTIONS = 10;
export const QUIZ_MIN_OPTIONS = 2;
export const QUIZ_MAX_OPTIONS = 4;

export const emptyQuestion = () => ({ prompt: '', options: ['', '', ''], correct: null });

/* Черновик теста от ИИ → форма. Недостающие вопросы добиваются пустыми до
   минимума: модель могла не справиться с форматом, и дописать руками быстрее,
   чем гонять её заново. */
export const quizForForm = (quiz) => {
    const items = (Array.isArray(quiz) ? quiz : [])
        .slice(0, QUIZ_MAX_QUESTIONS)
        .map((item) => {
            const options = (Array.isArray(item?.options) ? item.options : [])
                .map((option) => String(option ?? ''))
                .slice(0, QUIZ_MAX_OPTIONS);
            while (options.length < QUIZ_MIN_OPTIONS) options.push('');
            const correct = Number.isInteger(item?.correct)
                && item.correct >= 0 && item.correct < options.length
                ? item.correct : null;
            return { prompt: String(item?.prompt ?? ''), options, correct };
        });
    while (items.length < QUIZ_MIN_QUESTIONS) items.push(emptyQuestion());
    return items;
};

/* Почему тест ещё нельзя публиковать — первой найденной причиной и словами
   сервера (news/access.py: normalize_quiz). Серая кнопка без объяснения
   читается как сломанная. */
export const quizProblem = (quiz) => {
    const items = Array.isArray(quiz) ? quiz : [];
    if (!items.length) return 'Тест не заполнен';
    for (let index = 0; index < items.length; index += 1) {
        const number = index + 1;
        const item = items[index] || {};
        const options = Array.isArray(item.options) ? item.options : [];
        if (!String(item.prompt || '').trim()) return `В вопросе ${number} нет текста`;
        if (options.some((option) => !String(option || '').trim())) {
            return `В вопросе ${number} есть пустой вариант ответа`;
        }
        if (options.length < QUIZ_MIN_OPTIONS || options.length > QUIZ_MAX_OPTIONS) {
            return `В вопросе ${number} должно быть от ${QUIZ_MIN_OPTIONS} до ${QUIZ_MAX_OPTIONS} вариантов`;
        }
        const unique = new Set(options.map((option) => String(option).trim().toLowerCase()));
        if (unique.size !== options.length) return `В вопросе ${number} варианты повторяются`;
        if (!Number.isInteger(item.correct) || item.correct < 0 || item.correct >= options.length) {
            return `В вопросе ${number} не отмечен верный вариант`;
        }
    }
    if (items.length < QUIZ_MIN_QUESTIONS || items.length > QUIZ_MAX_QUESTIONS) {
        return `В тесте должно быть от ${QUIZ_MIN_QUESTIONS} до ${QUIZ_MAX_QUESTIONS} вопросов`;
    }
    return null;
};

/* Убрать вариант и не потерять отметку верного: индексы после удалённого
   сдвигаются, а отметка на самом удалённом снимается — выбирать за человека
   другой верный ответ нельзя. */
export const dropOption = (item, optionIndex) => {
    const correct = item.correct === optionIndex
        ? null
        : (Number.isInteger(item.correct) && item.correct > optionIndex ? item.correct - 1 : item.correct);
    return { ...item, options: item.options.filter((_, index) => index !== optionIndex), correct };
};
