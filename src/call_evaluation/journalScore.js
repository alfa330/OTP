/* Итоговый балл «Журнала оценок» по мониторинговой шкале направления.
 *
 * Одна формула на три окна журнала — оценку звонка, оценку чата и оценку
 * звонка калибровки (раньше она была переписана в каждом). Зеркала на сервере:
 * bot_schedule2._compute_total_score_from_criteria (калибровка) и
 * call_qa/human_review.score_of («Моя оценка» в карточке ИИ-оценки, там же
 * call_qa/api._ai_score для балла ИИ).
 *
 * Правила:
 *  • «Критич. ошибка» (Error) по критическому критерию обнуляет итог;
 *  • взвешенный критерий: «Корректно» и N/A — полный вес, «Недочёт» — вес
 *    недочёта из шкалы, «Ошибка» — ноль;
 *  • «Недочёт» по критическому критерию снимает с итога установленное в шкале
 *    число баллов, итог при этом не уходит ниже нуля.
 *
 * Поле недочёта в шкале одно — deficiency.weight, а смысл задаёт критичность:
 * у взвешенного критерия это частичный зачёт, у критического — вычет.
 */

export const criticalDeficiencyPenalty = (criterion) => (
    criterion?.isCritical && criterion?.deficiency
        ? Math.max(0, Number(criterion.deficiency.weight) || 0)
        : 0
);

export const journalTotalScore = (criteria = [], scores = []) => {
    const list = Array.isArray(criteria) ? criteria : [];
    if (list.some((c, i) => c?.isCritical && scores[i] === 'Error')) return 0;
    let penalty = 0;
    const total = list.reduce((sum, c, i) => {
        if (c?.isCritical) {
            if (scores[i] === 'Deficiency') penalty += criticalDeficiencyPenalty(c);
            return sum;
        }
        if (scores[i] === 'Correct' || scores[i] === 'N/A') return sum + (Number(c?.weight) || 0);
        if (scores[i] === 'Deficiency' && c?.deficiency) return sum + (Number(c.deficiency.weight) || 0);
        return sum;
    }, 0);
    // Без вычета итог прежний, байт в байт. С вычетом — до сотых, как считает
    // сервер калибровки: 33,3 + 33,3 − 10 иначе показалось бы 56.599999999999994.
    return penalty ? Math.max(0, Math.round((total - penalty) * 100) / 100) : total;
};
