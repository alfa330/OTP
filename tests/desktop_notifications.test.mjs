import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildDesktopNotice,
    desktopPermission,
    desktopPrefKey,
    desktopSupported,
    readDesktopPref,
    requestDesktopPermission,
    shouldNotifyDesktop,
    showDesktopNotice,
    writeDesktopPref,
} from '../src/components/notifications/desktopNotifications.js';

const memoryStorage = () => {
    const map = new Map();
    return {
        getItem: (k) => (map.has(k) ? map.get(k) : null),
        setItem: (k, v) => map.set(k, String(v)),
        removeItem: (k) => map.delete(k),
        size: () => map.size,
    };
};

/* Заглушка окна: Notification обязан быть функцией-конструктором, потому что
   desktopSupported проверяет именно typeof === 'function'. */
const stubWindow = (permission = 'granted') => {
    const shown = [];
    let focused = 0;
    function Notification(title, options) {
        this.title = title;
        this.options = options;
        this.onclick = null;
        this.close = () => { this.closed = true; };
        shown.push(this);
    }
    Notification.permission = permission;
    Notification.requestPermission = async () => permission;
    return { Notification, focus: () => { focused += 1; }, shown, focusCount: () => focused };
};

const base = { enabled: true, permission: 'granted', hidden: false, focused: false, panelOpen: false };

test('молчим, когда человек смотрит в портал', () => {
    // Главное правило: колокол и выехавшая карточка уже всё сказали, плашка
    // поверх них была бы дублем.
    assert.equal(shouldNotifyDesktop({ ...base, hidden: false, focused: true }), false);
});

test('свёрнутое окно и фоновая вкладка — показываем', () => {
    assert.equal(shouldNotifyDesktop({ ...base, hidden: true, focused: false }), true);
    // hidden побеждает даже при странном сочетании флагов
    assert.equal(shouldNotifyDesktop({ ...base, hidden: true, focused: true }), true);
});

test('вкладка видима, но фокус в другой программе — показываем', () => {
    // Открытый поверх браузера Excel оставляет visibilityState === 'visible',
    // и без проверки фокуса оператор не увидел бы ничего за весь день.
    assert.equal(shouldNotifyDesktop({ ...base, hidden: false, focused: false }), true);
});

test('выключатель, разрешение и открытая панель гасят каждый по отдельности', () => {
    assert.equal(shouldNotifyDesktop({ ...base, enabled: false }), false);
    assert.equal(shouldNotifyDesktop({ ...base, permission: 'default' }), false);
    assert.equal(shouldNotifyDesktop({ ...base, permission: 'denied' }), false);
    assert.equal(shouldNotifyDesktop({ ...base, permission: 'unsupported' }), false);
    assert.equal(shouldNotifyDesktop({ ...base, panelOpen: true }), false);
});

test('карточка одного уведомления несёт раздел и подробность', () => {
    const notice = buildDesktopNotice({
        item: { source: 'tasks', id: 42, title: 'Проверить отчёт', body: 'срок сегодня' },
        extra: 0,
        added: 1,
        sourceLabel: 'Задачи',
    });
    assert.equal(notice.title, 'Проверить отчёт');
    assert.equal(notice.body, 'Задачи · срок сегодня');
    assert.equal(notice.tag, 'otp-bell:tasks:42');
});

test('пришло несколько — показываем важнейшее и число остальных', () => {
    // Одна плашка на приход, а не семь подряд.
    const notice = buildDesktopNotice({
        item: { source: 'wiki_ack', id: 7, title: 'Регламент склада' },
        extra: 2,
        added: 3,
        sourceLabel: 'Ознакомление',
    });
    assert.equal(notice.title, 'Регламент склада');
    assert.equal(notice.body, 'Ознакомление\nи ещё 2 уведомления');
});

test('русское склонение «уведомление»', () => {
    const tail = (extra) => buildDesktopNotice({
        item: { source: 's', id: 1, title: 't' }, extra, sourceLabel: '',
    }).body;
    assert.equal(tail(1), 'и ещё 1 уведомление');
    assert.equal(tail(2), 'и ещё 2 уведомления');
    assert.equal(tail(5), 'и ещё 5 уведомлений');
    assert.equal(tail(11), 'и ещё 11 уведомлений');
    assert.equal(tail(21), 'и ещё 21 уведомление');
    assert.equal(tail(112), 'и ещё 112 уведомлений');
});

test('состав неизвестен — говорим хотя бы о факте', () => {
    // Так бывает у «4 You»: дюжина фотографий свёрнута сервером в одну строку.
    const notice = buildDesktopNotice({ item: null, extra: 0, added: 4 });
    assert.equal(notice.title, 'Новые уведомления');
    assert.equal(notice.body, '4 уведомления');
    // Сводка обязана вытеснять предыдущую сводку, а не ложиться стопкой.
    assert.equal(notice.tag, 'otp-bell-summary');
});

test('по умолчанию ВКЛЮЧЕНО, пока человек не высказался', () => {
    // Прежний opt-in означал два шага вместо одного: разрешение выдано, а
    // переключатель серый — и функция «не работает».
    const storage = memoryStorage();
    assert.equal(readDesktopPref(11, storage), true);
});

test('явное «выключить» переживает перезаход', () => {
    // Ключ НЕ стирается: стёртый читался бы как «по умолчанию», то есть как
    // «включено», и снятый флажок воскресал бы сам собой.
    const storage = memoryStorage();
    writeDesktopPref(11, false, storage);
    assert.equal(storage.getItem(desktopPrefKey(11)), '0');
    assert.equal(readDesktopPref(11, storage), false);

    writeDesktopPref(11, true, storage);
    assert.equal(readDesktopPref(11, storage), true);
});

test('выключатель помнится по пользователю, а не по компьютеру', () => {
    // За одним компьютером сидят посменно: чужой снятый флажок не должен
    // доставаться следующему оператору.
    const storage = memoryStorage();
    writeDesktopPref(11, false, storage);
    assert.equal(readDesktopPref(11, storage), false);
    assert.equal(readDesktopPref(12, storage), true);
    assert.notEqual(desktopPrefKey(11), desktopPrefKey(12));
});

test('недоступное хранилище не роняет колокол', () => {
    // Приватный режим и запрет кук роняют доступ к localStorage.
    const broken = {
        getItem() { throw new Error('SecurityError'); },
        setItem() { throw new Error('SecurityError'); },
        removeItem() { throw new Error('SecurityError'); },
    };
    assert.equal(readDesktopPref(1, broken), true);
    assert.doesNotThrow(() => writeDesktopPref(1, true, broken));
    assert.equal(readDesktopPref(1, undefined), true);
});

test('браузер без Notification опознаётся, а не притворяется рабочим', () => {
    assert.equal(desktopSupported({}), false);
    assert.equal(desktopPermission({}), 'unsupported');
    assert.equal(desktopSupported(null), false);
    const win = stubWindow('default');
    assert.equal(desktopSupported(win), true);
    assert.equal(desktopPermission(win), 'default');
});

test('без разрешения ничего не показываем', () => {
    const win = stubWindow('default');
    assert.equal(showDesktopNotice({ title: 'x' }, { win }), null);
    assert.equal(win.shown.length, 0);
});

test('клик по плашке поднимает окно и ведёт к уведомлению', () => {
    const win = stubWindow('granted');
    let activated = 0;
    const notification = showDesktopNotice(
        { title: 'Проверить отчёт', body: 'Задачи', tag: 'otp-bell:tasks:42' },
        { win, icon: '/OTP-1/favicon.ico', onActivate: () => { activated += 1; } },
    );
    assert.equal(win.shown.length, 1);
    assert.equal(notification.title, 'Проверить отчёт');
    assert.equal(notification.options.tag, 'otp-bell:tasks:42');
    assert.equal(notification.options.icon, '/OTP-1/favicon.ico');

    notification.onclick();
    // Порядок важен: сначала поднять окно, иначе переход произойдёт в
    // невидимой вкладке и человек решит, что клик не сработал.
    assert.equal(win.focusCount(), 1);
    assert.equal(activated, 1);
    assert.equal(notification.closed, true);
});

test('падение Notification не ломает колокол', () => {
    // Системные уведомления, выключенные на уровне ОС, умеют бросать.
    function Boom() { throw new Error('notifications disabled by OS'); }
    Boom.permission = 'granted';
    assert.equal(showDesktopNotice({ title: 'x' }, { win: { Notification: Boom } }), null);
});

test('запрос разрешения переживает отказ браузера отвечать', async () => {
    const win = stubWindow('default');
    win.Notification.requestPermission = async () => { throw new Error('nope'); };
    assert.equal(await requestDesktopPermission(win), 'default');
    assert.equal(await requestDesktopPermission({}), 'unsupported');
});
