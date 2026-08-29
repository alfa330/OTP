/* Размер и выравнивание картинки в статье — счётная часть.
 *
 * Вынесено из WikiImageNode.jsx отдельным модулем не ради красоты: узел тянет
 * за собой React, TipTap и lucide, то есть проверить его `node --test` без
 * сборки нельзя, а ошибаться здесь есть где. Тот же приём в разделе уже
 * применён к articleLink.js, searchText.js и parkLogo.js.
 *
 * ЕДИНИЦА ИЗМЕРЕНИЯ — ПРОЦЕНТ от ширины колонки, и это решение, а не удобство.
 * Статью читают и с телефона, где колонка втрое уже; пиксельная ширина там
 * упирается в max-width, и заданные автором соотношения между соседними
 * картинками рассыпаются. Плюс у раздела есть свой масштаб (zoom на
 * .wiki-scope), от которого любые пиксельные замеры уезжают, а отношение двух
 * экранных величин — нет.
 */

export const MIN_SIZE = 10;
export const MAX_SIZE = 100;
export const STEP = 5;

export const ALIGNS = ['left', 'center', 'right'];

/* null — «размер не задан», и это НЕ то же самое, что 100 %. Пустое значение
   возвращается как есть, а не подменяется краем диапазона: картинка без
   заданной ширины стоит своим размером, и растянуть каждый мелкий значок на всю
   колонку значило бы превратить его в мыло. */
export const clampSize = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return null;
    return Math.min(MAX_SIZE, Math.max(MIN_SIZE, Math.round(number)));
};

export const normalizeAlign = (value) => (ALIGNS.includes(value) ? value : null);

/* Ширина из готовой разметки.
 *
 * Читаются и data-width, и процент из style. Второе — не перестраховка: тело
 * статьи проходит через два санитайзера, и если атрибут когда-нибудь выпадет из
 * белого списка, размер уцелеет в стиле, а не пропадёт молча.
 *
 * Начало объявления в шаблоне обязательно. Без него «max-width: 100%» содержит
 * внутри себя «width: 100%» и читается как заданная автором ширина — то есть
 * картинка, которой ширину НЕ задавали, при открытии редактора растянулась бы
 * на всю колонку. Ровно это свойство стоит у картинок из внешних документов.
 */
const STYLE_WIDTH = /(?:^|;)\s*width\s*:\s*([\d.]+)\s*%/i;

export const sizeFromElement = (element) => {
    const explicit = clampSize(element.getAttribute('data-width'));
    if (explicit) return explicit;
    const styled = STYLE_WIDTH.exec(element.getAttribute('style') || '');
    return styled ? clampSize(styled[1]) : null;
};

/* Инлайновый стиль картинки. Поля, а не text-align: выравнивается сам блок
   картинки, а не текст вокруг. Каждое поле пишется ОТДЕЛЬНЫМ свойством —
   сокращённое margin серверный санитайзер выбрасывает целиком, он сверяет ИМЯ
   свойства с белым списком, а там только margin-left и margin-right
   (wiki/sanitize.py: ALLOWED_CSS). */
export const styleFor = ({ size, align }) => {
    const parts = [];
    const width = clampSize(size);
    if (width) parts.push(`width: ${width}%`);
    if (align === 'left') parts.push('margin-left: 0', 'margin-right: auto');
    if (align === 'center') parts.push('margin-left: auto', 'margin-right: auto');
    if (align === 'right') parts.push('margin-left: auto', 'margin-right: 0');
    return parts.join('; ');
};
