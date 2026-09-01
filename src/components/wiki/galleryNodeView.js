/* Галерея в редакторе — узел схемы с собственным видом.
 *
 * ЗАЧЕМ. Автор правит статью в том же виде, в каком её увидит читатель, — это
 * правило раздела, и галерея была единственным блоком, который его нарушал.
 * Замер на стенде: три вертикальных скриншота телефона (499x1080) при высоте
 * ленты в 26rem дают по 194 px ширины; втроём с промежутками — 606 px в
 * колонке 820. Лента не переполнялась ВООБЩЕ (scrollWidth равнялся
 * clientWidth), листать было нечего, стрелок и точек не было, а третий кадр
 * упирался в правый край и обрезался. Собрав галерею, автор не мог её
 * проверить, не сохранив статью и не открыв её заново.
 *
 * ПОЧЕМУ ЭТО ВИД УЗЛА, А НЕ ТОТ ЖЕ mountGalleries, ЧТО НА ВИТРИНЕ. Тело
 * статьи в редакторе — это DOM, которым владеет ProseMirror: он сверяет его со
 * своим документом и всё лишнее оттуда выкидывает. Обёртка и кнопки,
 * вставленные снаружи, прожили бы до первой правки, а потом либо исчезли, либо
 * (хуже) уехали бы в getHTML и оттуда в базу. Вид узла — единственное место,
 * где ProseMirror согласен видеть чужой DOM: contentDOM он ведёт сам, всё
 * остальное обязуется не трогать.
 *
 * ПОЧЕМУ ВИД ОДИН НА ВСЕ БЛОКИ, А ГАЛЕРЕЯ ВНУТРИ НЕГО — ОСОБЫЙ СЛУЧАЙ. Узел
 * wikiBlock один на вводку, плашку, сетку, карточку и галерею (см. шапку
 * WikiBlockNode.js), и вид регистрируется на весь узел разом. Для всех прочих
 * видов эта функция возвращает undefined — и это не заглушка, а описанное
 * поведение ProseMirror: при пустом ответе он рисует узел так же, как рисовал
 * бы без всякого вида, через toDOM схемы. То есть плашки и карточки этот файл
 * не касается ВООБЩЕ, и разойтись с renderHTML им негде.
 *
 * ЧТО УЕЗЖАЕТ В БАЗУ. Ничего отсюда. В документе остаётся тот же
 * <div data-wiki-block="gallery"> с картинками внутри; обёртка, кнопки и точки
 * живут только в DOM редактора. Сериализацию делает renderHTML узла, а её этот
 * файл не трогает.
 */

import { attachGallery } from './gallery.js';

/** Вид узла wikiBlock. Для всех видов, кроме галереи, возвращает undefined —
 *  ProseMirror нарисует их сам, как и раньше. */
export const galleryNodeView = ({ node, view, HTMLAttributes }) => {
    if (node.attrs.kind !== 'gallery') return undefined;

    /* Документ берём у ПЕРЕДАННОГО вида, а не у editor.view: вид узла строится
       в тот момент, когда сам редактор ещё не считает себя смонтированным, и
       обращение к editor.view роняет TipTap 3 («The editor view is not
       available»). Проверено на стенде: страница падала целиком. */
    const doc = view?.dom?.ownerDocument || document;

    const box = doc.createElement('div');
    box.className = 'wiki-gallery';

    /* Атрибуты ленте ставим ТЕ ЖЕ, что отдал бы renderHTML: TipTap их уже
       посчитал и передал сюда. Собирать их здесь заново значило бы завести
       пятое место, где имя data-wiki-block должно совпасть с четырьмя
       остальными, — а паритет тех четырёх и так сторожит отдельный тест. */
    const strip = doc.createElement('div');
    Object.entries(HTMLAttributes || {}).forEach(([name, value]) => {
        if (value !== null && value !== undefined) strip.setAttribute(name, String(value));
    });
    strip.setAttribute('data-wiki-block', 'gallery');
    box.appendChild(strip);

    /* Клавиши и тяга мышью остаются редактору: стрелка здесь двигает каретку
       по тексту, а тяга — это выделение. Трекпад, кнопки, точки и палец
       работают так же, как при чтении. */
    const gallery = attachGallery(strip, {
        box,
        doc,
        keys: false,
        drag: false,
        slidesOf: () => Array.from(strip.children),
    });

    /* Пересчёт ПОСЛЕ того, как ProseMirror доложит кадры. update() вызывается
       до правки детей, и посчитанное в нём относилось бы к прошлому составу
       ленты: добавив второй кадр, автор увидел бы одну точку. */
    let pending = 0;
    const later = () => {
        if (pending) return;
        pending = requestAnimationFrame(() => { pending = 0; gallery.sync(); });
    };

    return {
        dom: box,
        contentDOM: strip,

        update(updated) {
            /* Сменился вид блока (галерею разобрали в плашку) — вид узла
               обязан пересоздаться, иначе кнопки останутся висеть на чужом
               блоке. */
            if (updated.type !== node.type) return false;
            if (updated.attrs.kind !== 'gallery') return false;
            later();
            return true;
        },

        /* Обвязка — не текст статьи. Без этого каждая перерисовка точек и
           каждая смена подписи читались бы редактором как правка документа: он
           бы перечитывал узел с экрана, не находил там кнопок в своём дереве и
           выбрасывал их. Мутации ВНУТРИ ленты — другое дело, там правда лежит
           содержимое, и разбираться с ними обязан ProseMirror. */
        ignoreMutation(mutation) {
            if (mutation.type === 'selection') return false;
            /* Атрибуты ленты (aria-label, class, style со снятым снапом) ставим
               мы, к содержимому они отношения не имеют. */
            if (mutation.type === 'attributes') return true;
            return !strip.contains(mutation.target);
        },

        /* Щелчки по стрелке и точке до редактора не доходят: иначе он поставил
           бы каретку в кнопку и посчитал её текстом. Всё, что происходит
           внутри ленты, наоборот, отдаём ему — там ставят курсор и правят. */
        stopEvent(event) {
            const target = event.target;
            return !!(target && !strip.contains(target));
        },

        destroy() {
            if (pending) cancelAnimationFrame(pending);
            gallery.undo();
        },
    };
};

export default galleryNodeView;
