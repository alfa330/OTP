/* Итог попытки тренажёра на экране статистики: метка строки и разбор.
 *
 * Разбор тот же, что в выгрузке (wiki/trainer_report.py: result_verdict,
 * result_misses): то, что видно на экране, и то, что уехало в файл, не должно
 * расходиться.
 *
 * ВЕРДИКТ ЕСТЬ НЕ У ВСЕХ. У аттестации — балл «3 из 5», у старых записей
 * тренажёра CRM — «верно/неверно». У курса и свободной среды правильного
 * ответа нет, судит наставник, — и метки нет. Раньше метку ставили любой
 * попытке с итогом по полю correct, которого у них нет, и каждая такая
 * попытка горела красным «неверно».
 *
 * Модуль без React: его гоняют тесты обычным node.
 */

const count = (value) => (Number.isInteger(value) && value > 0 ? value : 0);

/** { label, tone } для метки строки или null, если вердикта нет. */
export const resultVerdict = (result) => {
    if (!result || typeof result !== 'object') return null;
    const { exam } = result;
    if (exam && typeof exam === 'object') {
        const total = count(exam.total);
        if (total) {
            const correct = Math.min(count(exam.correct), total);
            /* Бросил на середине — балл не окончательный: серым, а не
               красным, иначе «0 из 5» читается как «провалил всё». */
            const unfinished = Array.isArray(exam.items)
                && exam.items.some((item) => item && item.answered === false);
            const tone = unfinished ? 'slate' : correct === total ? 'green' : correct === 0 ? 'red' : 'amber';
            return { label: `${correct} из ${total}`, tone };
        }
    }
    if (typeof result.correct === 'boolean') {
        return result.correct ? { label: 'верно', tone: 'green' } : { label: 'неверно', tone: 'red' };
    }
    return null;
};

/** Ситуации аттестации для разбора: название, решена ли, что было не так. */
export const resultExamItems = (result) => {
    const items = result && result.exam && result.exam.items;
    if (!Array.isArray(items)) return [];
    return items.filter((item) => item && typeof item === 'object').map((item) => ({
        title: String(item.title || item.key || 'Ситуация'),
        ok: Boolean(item.ok),
        // До ситуации не дошли (бросил на середине) — это не ошибка в ней.
        answered: item.answered !== false,
        problems: Array.isArray(item.problems) ? item.problems.filter(Boolean).map(String) : [],
    }));
};
