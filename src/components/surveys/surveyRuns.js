/* ─── Запуски опроса на вкладке «Ответы» ───

   Повтор в разделе — это ОТДЕЛЬНЫЙ прогон того же опроса: своя карточка, свои
   вопросы, свои назначения. Ответы всех прогонов приходят одним списком, и на
   вкладке они лежали единой сеткой: у «Обратной связи» это девять запусков и
   шесть десятков карточек вперемешку, а отличить их можно было только по
   значку «#7» в углу. Здесь список сворачивается в запуски: карточка на прогон
   со своими «прошли N из M» и средним результатом.

   Логика вынесена из компонента, чтобы её можно было прогнать без браузера. */

// Нумерация повторов идёт от первого запуска: «Повторение #1» читалось бы
// как второй прогон.
export const surveyRunLabel = (iteration) => {
    const number = Number(iteration) || 1;
    return number > 1 ? `Повторение #${number}` : 'Первый запуск';
};

/**
 * Собирает запуски опроса из карточки, списка повторений и карточек ответов.
 *
 * @param {object}   survey      открытый опрос (его прогон тоже в списке)
 * @param {Array}    repetitions соседние прогоны из ответа сервера
 * @param {Array}    cards       карточки сотрудников по всем прогонам семьи
 * @returns {Array}  запуски, свежий сверху
 */
export const buildAnswerRuns = ({ survey = null, repetitions = [], cards = [] } = {}) => {
    const runsById = new Map();

    const putRun = (id, source) => {
        const runId = Number(id);
        if (!Number.isFinite(runId) || runId <= 0 || runsById.has(runId)) return;
        runsById.set(runId, {
            id: runId,
            title: String(source?.title || ''),
            iteration: Number(source?.iteration) || 1,
            createdAt: source?.created_at || null,
            cards: []
        });
    };

    putRun(survey?.id, {
        title: survey?.title,
        iteration: survey?.repeat?.iteration,
        created_at: survey?.created_at
    });
    (Array.isArray(repetitions) ? repetitions : []).forEach((repetition) => putRun(repetition?.id, repetition));

    (Array.isArray(cards) ? cards : []).forEach((card) => {
        // Ответ ссылается на прогон, которого нет в списке повторений, —
        // заводим запуск по самой строке: иначе её ответы исчезли бы со
        // вкладки вместе с прогоном.
        putRun(card?.repeatSurveyId, {
            title: card?.row?.repeat_survey_title,
            iteration: card?.repeatIteration
        });
        const run = runsById.get(Number(card?.repeatSurveyId));
        if (run) run.cards.push(card);
    });

    return Array.from(runsById.values())
        .map((run) => {
            const completed = run.cards.filter((card) => card.isCompleted);
            const scored = completed.filter((card) => card.hasScore);
            return {
                ...run,
                assignedCount: run.cards.length,
                completedCount: completed.length,
                // Средний — по прошедшим с баллом: не начавшие тест утянули бы
                // его вниз, хотя ноль они не получали.
                averageScore: scored.length
                    ? scored.reduce((sum, card) => sum + Number(card.scoreValue), 0) / scored.length
                    : null
            };
        })
        // Свежий запуск сверху: спрашивают почти всегда про него.
        .sort((a, b) => (b.iteration - a.iteration) || (b.id - a.id));
};
