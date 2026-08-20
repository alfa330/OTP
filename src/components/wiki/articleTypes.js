/* Тип статьи — один список на весь раздел.
 *
 * Значения совпадают с ARTICLE_TYPES в wiki/schema.py: их принимает правка и по
 * ним же фильтрует витрина. До этого список жил копией в редакторе, а сам тип
 * НИ НА ЧТО не влиял — человек выбирал «Должностная инструкция», и статья
 * ничем не отличалась от обычной: ни в списке, ни при чтении, ни в поиске.
 *
 * tone есть не у всех: «Обычная статья» подписи не получает. Подписать типом
 * каждую вторую статью — значит превратить подпись в фон, который перестают
 * читать; наличие tone и означает «тип стоит показать».
 */

export const ARTICLE_TYPES = [
    { value: 'general', label: 'Обычная статья', plural: 'Статьи' },
    { value: 'regulation', label: 'Регламент', plural: 'Регламенты', tone: 'blue' },
    { value: 'instruction', label: 'Инструкция', plural: 'Инструкции', tone: 'slate' },
    { value: 'job_description', label: 'Должностная инструкция',
      plural: 'Должностные инструкции', tone: 'blue' },
    { value: 'tool_description', label: 'Описание инструмента',
      plural: 'Описания инструментов', tone: 'slate' },
    /* «Тренажёр» — единственный тип, который что-то ВКЛЮЧАЕТ, а не подписывает:
       выбрав его, автор получает в панели редактора выбор тренажёра и вставляет
       в текст кнопку запуска. Поэтому тип нужен и здесь, и на сервере
       (wiki/schema.py: ARTICLE_TYPES) — по нему же собирается подборка. */
    { value: 'trainer', label: 'Тренажёр', plural: 'Тренажёры', tone: 'green' },
];

const BY_VALUE = new Map(ARTICLE_TYPES.map((type) => [type.value, type]));

/** Тип, при котором в редакторе появляется выбор тренажёра. Одна константа на
 *  редактор и на проверки: строкой 'trainer' в двух местах разойтись легко. */
export const TRAINER_TYPE = 'trainer';

/** Подпись типа — или null для обычной статьи и незнакомого значения. */
export const typeBadge = (value) => {
    const meta = BY_VALUE.get(value);
    return meta?.tone ? meta : null;
};

/** Типы, по которым имеет смысл фильтровать витрину. */
export const FILTERABLE_TYPES = ARTICLE_TYPES.filter((type) => type.tone);

/** Заголовок подборки: «Должностные инструкции», а не «Должностная инструкция · 2». */
export const typePlural = (value) => BY_VALUE.get(value)?.plural || 'Документы';

/** Незнакомый тип считаем обычной статьёй: статья с ним не должна пропасть. */
export const normalizeType = (value) => (BY_VALUE.has(value) ? value : 'general');

/* Порядок групп в оглавлении: сначала то, чему подчиняются (должностная
   инструкция, регламент), потом справочное, и только потом обычные статьи.
   Отдельная константа, а не порядок ARTICLE_TYPES: там порядок задан
   выпадающим списком редактора, где первым стоит значение по умолчанию. */
export const GROUP_ORDER = [
    'job_description', 'regulation', 'instruction', 'trainer', 'tool_description', 'general',
];

/* Статьи, разложенные по типу: [{ key, label, items }] — или null, если тип
   всего один. Полоска над списком, где все статьи одного вида, повторяла бы
   название раздела и не разделяла бы ничего. */
export const groupByType = (items) => {
    const buckets = new Map();
    (items || []).forEach((article) => {
        const key = normalizeType(article.article_type);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(article);
    });
    if (buckets.size < 2) return null;
    return GROUP_ORDER
        .filter((key) => buckets.has(key))
        .map((key) => ({ key, label: typePlural(key), items: buckets.get(key) }));
};

/* Скелет должностной инструкции — только заголовки, без подсказок внутри.
 *
 * Подсказка в шаблоне переживает автора: её забывают стереть, и она уезжает в
 * опубликованный документ. Заголовки сами по себе говорят, что писать, и
 * удалять их не нужно — лишний из них просто останется пустым.
 */
export const JOB_DESCRIPTION_TEMPLATE = [
    '<h2>Общие положения</h2><p></p>',
    '<h2>Квалификационные требования</h2><p></p>',
    '<h2>Должностные обязанности</h2><p></p>',
    '<h2>Права</h2><p></p>',
    '<h2>Ответственность</h2><p></p>',
    '<h2>Показатели эффективности</h2><p></p>',
].join('');
