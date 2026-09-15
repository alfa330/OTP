/* «Опубликовать как новость» (задача #321): черновик новости из разобранного вопроса.
 *
 * Форма «Новостей» открывается уже заполненной: заголовок — вопрос оператора,
 * текст — ответ супервайзера, кому — отдел оператора. Без модели: это короткий
 * путь, и полминуты ожидания черновика съели бы весь смысл кнопки. Поправить
 * текст, приложить фото и сменить адресата человек может в самой форме.
 *
 * Отдельным модулем — чтобы правило проверялось node-тестом, а не глазами.
 */

// news/access.py: MAX_TITLE_LENGTH — длиннее сервер молча обрежет.
export const NEWS_TITLE_MAX = 255;

const escapeHtml = (text) => String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

/* Ответ — простой текст из поля ввода. Пустая строка делит абзацы, одиночный
   перенос остаётся переносом: так ответ выглядит в новости тем же, что писали. */
export const answerToHtml = (text) => String(text || '')
    .trim()
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => `<p>${escapeHtml(paragraph).replace(/\n/g, '<br>')}</p>`)
    .join('');

export const newsTitleFromQuestion = (question) => {
    const flat = String(question || '').replace(/\s+/g, ' ').trim();
    return flat.length > NEWS_TITLE_MAX
        ? `${flat.slice(0, NEWS_TITLE_MAX - 1).trimEnd()}…`
        : flat;
};

export function newsDraftFromQuestion(item) {
    return {
        title: newsTitleFromQuestion(item?.question),
        body: answerToHtml(item?.answer),
        // Тот же адресат, что у новости с тестом из «Записать в базу знаний»:
        // отдел оператора (wiki/routes_questions.py: wiki_questions_publish).
        audience: item?.department_id ? [{
            subject_type: 'department',
            subject_id: item.department_id,
            subject_role: null,
            subject_name: item.department_name || '',
        }] : [],
    };
}
