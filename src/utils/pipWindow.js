/*
 * Окно поверх других окон (Document Picture-in-Picture) — общая механика портала.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. К приходу помощника такое окно в портале открывали уже
 * двое — виджет закреплённой задачи («Задачи») и табло СЗоВ, — и перенос стилей
 * у них успел разъехаться: табло подставляет клонированной <link> разрешённый
 * href, «Задачи» копируют атрибут как есть. Разница не косметическая: у PiP-окна
 * СВОЙ базовый адрес, и относительный путь до бандла в нём ищется от другого
 * корня. Третья копия у помощника закрепила бы расхождение навсегда, поэтому
 * механика живёт здесь одна.
 *
 * ОКНО В ДОКУМЕНТЕ РОВНО ОДНО. Chrome не даёт открыть второе, и запрос при
 * занятом окне не падает ошибкой, а ОТБИРАЕТ его у прежнего владельца молча.
 * Поэтому просящий обязан сначала спросить, свободно ли оно, и объяснить отказ
 * словами: иначе открытый помощник схлопывал бы табло линии, за которым следит
 * вся смена, и человек увидел бы это как пропажу табло, а не как свой выбор.
 */

/** Умеет ли браузер вообще. Chrome и Edge — да, Firefox и Safari — нет. */
export const canOpenPipWindow = () => (
    typeof window !== 'undefined' && Boolean(window.documentPictureInPicture?.requestWindow)
);

/** Занято ли единственное окно документа кем-то другим. */
export const pipWindowTaken = () => (
    typeof window !== 'undefined' && Boolean(window.documentPictureInPicture?.window)
);

/*
 * Стили в окно PiP. Это отдельный документ: без переноса таблиц стилей там
 * голый HTML. href клонированной ссылки подставляем уже разрешённым — см.
 * причину в шапке файла.
 */
const documentStyleNodes = () => Array.from(document.querySelectorAll('link[rel="stylesheet"], style'));

const cloneStyleNode = (node) => {
    const clone = node.cloneNode(true);
    if (clone.tagName === 'LINK' && node.href) clone.href = node.href;
    return clone;
};

export const cloneDocumentStyles = (targetWindow) => {
    documentStyleNodes().forEach((node) => {
        targetWindow.document.head.appendChild(cloneStyleNode(node));
    });
};

/*
 * То же, но окно следит за стилями основного документа, пока открыто.
 *
 * Разового снимка хватает окну, чьи стили к открытию уже загружены. Просмотр
 * вложений «Чатов ОП» догружает их ПОСЛЕ открытия окна: таблица текстового слоя
 * PDF приходит вместе с первым PDF, а на dev-сервере Vite переписывает <style>
 * на лету. Со снимком такой PDF открылся бы в окне без своих стилей.
 *
 * Возвращает функцию остановки: она снимает наблюдение и убирает из окна ровно
 * то, что сюда положила, — чужие узлы <head> окна остаются на месте.
 */
export const mirrorDocumentStyles = (targetWindow) => {
    const head = targetWindow.document.head;
    const clones = new Map();
    documentStyleNodes().forEach((source) => {
        const clone = cloneStyleNode(source);
        head.appendChild(clone);
        clones.set(source, clone);
    });
    // Свои клоны держим перед меткой: по ней видно, куда вставлять новые.
    const boundary = targetWindow.document.createComment('pip-document-styles');
    head.appendChild(boundary);
    let stopped = false;

    const sync = () => {
        if (stopped || targetWindow.closed) return;
        const sources = documentStyleNodes();
        const alive = new Set(sources);
        clones.forEach((clone, source) => {
            if (alive.has(source)) return;
            clone.remove();
            clones.delete(source);
        });
        sources.forEach((source) => {
            const clone = cloneStyleNode(source);
            const previous = clones.get(source);
            if (previous?.isEqualNode(clone)) return;
            previous?.remove();
            clones.set(source, clone);
        });
        // Порядок — как в основном документе. Неизменённую ссылку с места не
        // снимаем: переставленный <link> браузер загружает заново.
        let next = boundary;
        sources.slice().reverse().forEach((source) => {
            const clone = clones.get(source);
            if (clone.nextSibling !== next) head.insertBefore(clone, next);
            next = clone;
        });
    };
    const observer = typeof MutationObserver !== 'undefined' ? new MutationObserver(sync) : null;
    observer?.observe(document.head, {
        childList: true, subtree: true, characterData: true, attributes: true,
    });
    return () => {
        stopped = true;
        observer?.disconnect();
        clones.forEach((clone) => clone.remove());
        clones.clear();
        boundary.remove();
    };
};

/*
 * Тёмный режим и базовые классы <body> — вслед за стилями.
 *
 * Тема портала включается атрибутом data-otp-theme на <html> (src/utils/darkTheme.js),
 * а не медиазапросом. В новом документе атрибута нет, и слой тёмной темы —
 * который в стилях уже лежит, его принёс cloneDocumentStyles, — просто не
 * находит, к чему прицепиться: окно открывалось бы белым у того единственного
 * аккаунта, ради которого тёмный режим и делался.
 *
 * Классы <body> (bg-gray-50, font-sans, antialiased, text-gray-900) заданы в
 * index.html, а не в CSS: без них в окне другой шрифт и другой цвет текста.
 */
export const mirrorDocumentChrome = (targetWindow) => {
    const theme = document.documentElement.getAttribute('data-otp-theme');
    if (theme) targetWindow.document.documentElement.setAttribute('data-otp-theme', theme);
    if (document.body.className) targetWindow.document.body.className = document.body.className;
};
