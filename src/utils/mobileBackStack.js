/* Системное «назад» на телефоне: свайп от края в iOS и кнопка «назад» в Android.
 *
 * ЧЕГО НЕ ХВАТАЛО. Портал — одна страница: разделы и окна живут состоянием, а
 * не адресом, и записей в истории браузера не заводит НИ ОДНА из них (во всём
 * App.jsx только history.replaceState, чистящий адрес). Поэтому жест «назад»
 * на телефоне не делал ничего — владелец так и написал: «просто делаю назад, а
 * оно не идёт назад». Здесь на каждый открытый экран и на каждый переход между
 * разделами кладётся запись истории, а на popstate снимается верхняя.
 *
 * ПОЧЕМУ ОДИН СТЕК НА ВСЁ, А НЕ ПО ЭКРАНУ. Экраны вкладываются друг в друга
 * (окно поверх окна), и «назад» обязано снимать РОВНО верхний. Разложенные по
 * компонентам слушатели popstate сработали бы все разом и закрыли бы весь
 * стопкой.
 *
 * ЗАЧЕМ history.back() ПРИ ОБЫЧНОМ ЗАКРЫТИИ. Если экран закрыли крестиком, его
 * запись в истории останется, и следующий жест «назад» уйдёт впустую — человек
 * свайпнет, а экран уже закрыт и ничего не произойдёт. Поэтому свою запись
 * снимаем сами; вызванный ею popstate гасится счётчиком pendingBacks, иначе он
 * закрыл бы ещё и соседний экран.
 */

const stack = [];
let listening = false;
/* Сколько popstate мы вызвали сами (history.back при обычном закрытии) — их
   надо пропустить. Счётчик, а не флаг: два экрана можно закрыть подряд быстрее,
   чем браузер успеет доставить первое событие. */
let pendingBacks = 0;

const handlePop = () => {
    if (pendingBacks > 0) {
        pendingBacks -= 1;
        return;
    }
    const top = stack.pop();
    /* Стек пуст — запись не наша: это либо адрес, открытый до входа в портал,
       либо переход, который мы не заводили. Не мешаем браузеру. */
    if (top) top.close();
};

const listen = () => {
    if (listening || typeof window === 'undefined') return;
    window.addEventListener('popstate', handlePop);
    listening = true;
};

/**
 * Положить в историю запись, которую системное «назад» снимет вызовом close.
 * Возвращает функцию отмены — её зовут, когда экран закрыли своими средствами.
 */
export const pushBackEntry = (close) => {
    if (typeof window === 'undefined' || typeof window.history?.pushState !== 'function') {
        return () => {};
    }
    listen();
    const entry = { close };
    stack.push(entry);
    /* Прежнее состояние сохраняем: в нём лежит то, что положил роутер адреса
       (urlHygiene зовёт replaceState с window.history.state). */
    window.history.pushState({ ...(window.history.state || {}), otpBack: stack.length }, '');
    return () => {
        const at = stack.indexOf(entry);
        /* Уже снят системным «назад» — истории ничего не должны. */
        if (at === -1) return;
        stack.splice(at, 1);
        pendingBacks += 1;
        window.history.back();
    };
};

/** Только для тестов: состояние стека между прогонами не должно течь. */
export const resetBackStack = () => {
    stack.length = 0;
    pendingBacks = 0;
};

export const backStackDepth = () => stack.length;
