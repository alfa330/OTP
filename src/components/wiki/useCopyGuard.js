import { useEffect } from 'react';

/* Запрет копирования из блока с текстом статьи.
 *
 * ЗАЧЕМ ОТДЕЛЬНЫЙ РУБЕЖ, если выделение уже погашено CSS'ом (.wiki-no-copy).
 * user-select:none решает, можно ли НАЧАТЬ выделение внутри блока. Выделение,
 * начатое выше по странице и протянутое сквозь него, браузер строит как
 * обычно — и Ctrl+C отдаёт текст целиком. Ровно так же обходится запрет и
 * «выделить всё» в тех сборках, где Select All не считается с user-select.
 * Поэтому второй рубеж стоит на самом буфере обмена.
 *
 * ПОЧЕМУ СЛУШАЕМ ДОКУМЕНТ, А НЕ БЛОК. Событие copy приходит на общего предка
 * выделения. Начали выделять за пределами блока — предок окажется выше него, и
 * обработчик, повешенный на сам блок, не сработает вовсе. Слушатель на
 * документе видит любое копирование на странице; чтобы при этом не отнять
 * копирование у остального экрана — у кнопки «Ссылка», у поиска, у соседних
 * панелей — блокируем ТОЛЬКО когда выделение действительно задевает блок.
 *
 * Узел берём из ref в момент СОБЫТИЯ, а не при подписке. Так хук не зависит от
 * того, смонтирован ли блок к моменту вызова: тело статьи появляется в DOM
 * позже первого рендера (см. WikiArticle: bodyReady), и подписка «по готовности»
 * означала бы второй флаг, который надо не забыть прокинуть.
 */

/** Задевает ли выделение узел. Вынесено ради теста: браузера в тестах нет. */
export const selectionTouchesNode = (selection, node) => {
    if (!selection || !node || selection.isCollapsed) return false;
    for (let i = 0; i < (selection.rangeCount || 0); i += 1) {
        const range = selection.getRangeAt(i);
        if (!range) continue;
        // intersectsNode есть во всех живых браузерах; запасной путь — на
        // случай, если выделение приехало объектом без него (jsdom старых
        // версий и мобильный Safari в режиме чтения именно так и делают).
        const touches = typeof range.intersectsNode === 'function'
            ? range.intersectsNode(node)
            : node.contains(range.commonAncestorContainer);
        if (touches) return true;
    }
    return false;
};

export default function useCopyGuard(enabled, nodeRef, onBlocked) {
    useEffect(() => {
        if (!enabled) return undefined;

        const guard = (event) => {
            const node = nodeRef?.current;
            if (!node) return;
            // Копирование ИЗ ПОЛЯ ВВОДА пропускаем всегда, даже если выделение
            // в документе осталось где-то в защищённом блоке. Это не педантизм:
            // кнопка «Ссылка» в запасном пути кладёт адрес во временный
            // <textarea>, выделяет его и зовёт execCommand('copy')
            // (WikiArticle: copyLink). Выделение текстового поля живёт отдельно
            // от выделения документа, и не будь этой проверки — запрет на
            // статью молча ломал бы копирование ссылки на неё же.
            const from = event.target;
            if (from && (from.tagName === 'INPUT' || from.tagName === 'TEXTAREA'
                         || from.isContentEditable)) return;
            if (!selectionTouchesNode(window.getSelection?.(), node)) return;
            // ТОЛЬКО preventDefault. Дописывать сюда setData('text/plain', '')
            // нельзя, хотя соблазн есть: отменённое событие copy кладёт в
            // системный буфер содержимое СВОЕГО DataTransfer, и пустая строка
            // затёрла бы то, что человек скопировал раньше и совсем в другом
            // месте — номер из CRM, кусок переписки, пароль. Он ушёл бы вставлять
            // это в мессенджер и получил бы пустоту, ничего не поняв. Один
            // preventDefault не трогает буфер вовсе: прежнее содержимое цело,
            // текст статьи туда не попал.
            event.preventDefault();
            onBlocked?.();
        };

        /* Ctrl+C, которому нечего копировать, — ГЛАВНЫЙ сценарий, а не крайний.
           Выделения внутри блока нет (его погасил CSS), поэтому событие copy
           браузер может не прислать вовсе: человек тянет мышью по строке, ничего
           не подсвечивается, он жмёт Ctrl+C — и не происходит НИЧЕГО. Молчание
           в ответ на команду читается как поломка портала, а бейдж в шапке к
           этому моменту уже уехал за верхний край длинной статьи.
           Поэтому объяснение даёт и само нажатие. Ничего не отменяем: копировать
           тут и так нечего, а preventDefault отнял бы Ctrl+C у страницы целиком. */
        const explain = (event) => {
            if (event.key !== 'c' && event.key !== 'C'
                && event.key !== 'с' && event.key !== 'С') return;   // латиница и кириллица
            if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
            const node = nodeRef?.current;
            if (!node) return;
            const from = event.target;
            if (from && (from.tagName === 'INPUT' || from.tagName === 'TEXTAREA'
                         || from.isContentEditable)) return;
            /* Объясняем только ПУСТОЕ копирование. Есть выделение по блоку —
               сработает обработчик copy со своим тостом, и второй был бы дублем;
               есть выделение где-то ещё (в тосте, в сайдбаре, в соседней панели)
               — человек копирует не статью, и запрет к нему не относится, а
               тост соврал бы, что копирование не удалось. */
            const selection = window.getSelection?.();
            if (selection && !selection.isCollapsed && selection.toString().trim()) return;
            onBlocked?.();
        };

        // Фаза перехвата: чужой обработчик copy на странице не должен успеть
        // положить текст в буфер раньше запрета.
        document.addEventListener('copy', guard, true);
        document.addEventListener('cut', guard, true);
        document.addEventListener('keydown', explain, true);
        return () => {
            document.removeEventListener('copy', guard, true);
            document.removeEventListener('cut', guard, true);
            document.removeEventListener('keydown', explain, true);
        };
    }, [enabled, nodeRef, onBlocked]);
}
