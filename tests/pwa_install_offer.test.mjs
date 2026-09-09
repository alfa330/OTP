import test from 'node:test';
import assert from 'node:assert/strict';

import {
    INSTALL_SNOOZE_KEY,
    INSTALL_SNOOZE_MS,
    detectInstallPlatform,
    isStandaloneDisplay,
    readInstallSnoozeUntil,
    shouldOfferInstall,
    snoozeInstallOffer,
} from '../src/utils/pwa.js';

/* Установка портала на телефон предлагается САМА, поверх работы человека, — и
   ровно поэтому решение «предлагать или молчать» вынесено в чистые функции и
   проверяется здесь. Ошибка в любую сторону стоит дорого: лишнее предложение
   на компьютере или во встроенном браузере Telegram — это шум поверх работы,
   а пропущенное на iPhone означает, что раздел просто не доехал до людей. */

const memoryStorage = () => {
    const map = new Map();
    return {
        getItem: (key) => (map.has(key) ? map.get(key) : null),
        setItem: (key, value) => map.set(key, String(value)),
        removeItem: (key) => map.delete(key),
    };
};

const stubWindow = ({ modes = [], standalone = undefined } = {}) => ({
    navigator: standalone === undefined ? {} : { standalone },
    matchMedia: (query) => ({ matches: modes.some((mode) => query.includes(mode)) }),
});

test('запуск с иконки распознаётся и по display-mode, и по navigator.standalone', () => {
    assert.equal(isStandaloneDisplay(stubWindow({ modes: ['standalone'] })), true);
    // iOS: display-mode там появился поздно, живые телефоны отвечают вот так.
    assert.equal(isStandaloneDisplay(stubWindow({ standalone: true })), true);
    assert.equal(isStandaloneDisplay(stubWindow({ standalone: false })), false);
    assert.equal(isStandaloneDisplay(stubWindow()), false);
});

test('iPad с iPadOS 13+ представляется маком — отличаем по касаниям', () => {
    // Без поправки на maxTouchPoints владельцы планшетов не увидели бы
    // предложения никогда: iPad честно пишет в UA «Macintosh».
    const ipad = {
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15',
        platform: 'MacIntel',
        maxTouchPoints: 5,
    };
    const mac = { ...ipad, maxTouchPoints: 0 };
    assert.equal(detectInstallPlatform(ipad), 'ios');
    assert.equal(detectInstallPlatform(mac), 'desktop');
});

test('встроенные браузеры мессенджеров — отдельная платформа', () => {
    // В них «Добавить на экран Домой» нет ни в каком виде, и подсказка про
    // «Поделиться» отправила бы человека искать несуществующий пункт.
    const telegram = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) TelegramBot Safari' };
    const instagram = { userAgent: 'Mozilla/5.0 (Linux; Android 13) Instagram 300.0 Android' };
    assert.equal(detectInstallPlatform(telegram), 'webview');
    assert.equal(detectInstallPlatform(instagram), 'webview');
});

test('iPhone предлагаем всегда, Android — только когда браузер отдал событие', () => {
    // На iOS системного события установки не существует: там ведём человека
    // руками по меню «Поделиться». На Android без beforeinstallprompt наша
    // кнопка «Установить» не открыла бы ничего.
    const base = { standalone: false, snoozedUntil: 0, now: 1_000 };
    assert.equal(shouldOfferInstall({ ...base, platform: 'ios', canPrompt: false }), true);
    assert.equal(shouldOfferInstall({ ...base, platform: 'android', canPrompt: false }), false);
    assert.equal(shouldOfferInstall({ ...base, platform: 'android', canPrompt: true }), true);
});

test('на компьютере и во встроенном браузере панель не всплывает', () => {
    // На компьютере установка ставится значком в адресной строке; всплывшая
    // посреди работы панель была бы чистым шумом.
    const base = { standalone: false, snoozedUntil: 0, now: 1_000, canPrompt: true };
    assert.equal(shouldOfferInstall({ ...base, platform: 'desktop' }), false);
    assert.equal(shouldOfferInstall({ ...base, platform: 'webview' }), false);
});

test('уже установленному порталу предлагать нечего', () => {
    assert.equal(
        shouldOfferInstall({ standalone: true, platform: 'ios', canPrompt: true, snoozedUntil: 0, now: 1_000 }),
        false,
    );
});

test('установленный портал молчит трое суток и снова спрашивает после них', () => {
    const storage = memoryStorage();
    const now = 1_700_000_000_000;
    snoozeInstallOffer(now, storage);

    const snoozedUntil = readInstallSnoozeUntil(storage);
    assert.equal(snoozedUntil, now + INSTALL_SNOOZE_MS);
    assert.equal(shouldOfferInstall({ platform: 'ios', snoozedUntil, now: now + 1000 }), false);
    assert.equal(
        shouldOfferInstall({ platform: 'ios', snoozedUntil, now: snoozedUntil + 1 }),
        true,
        '«Позже» — это отсрочка, а не отказ навсегда',
    );
});

test('испорченное и пустое хранилище не отменяют предложение', () => {
    // Приватный режим, чужая запись, очищенные данные сайта: отсрочки нет —
    // значит предложение живо. Отдельно проверяем запись прежнего формата
    // (голое число): она тоже не должна читаться как отсрочка.
    const storage = memoryStorage();
    assert.equal(readInstallSnoozeUntil(storage), 0);
    storage.setItem(INSTALL_SNOOZE_KEY, 'позже');
    assert.equal(readInstallSnoozeUntil(storage), 0);
    storage.setItem(INSTALL_SNOOZE_KEY, '1789197478947');
    assert.equal(readInstallSnoozeUntil(storage), 0);
    assert.equal(readInstallSnoozeUntil(null), 0);
});

test('запись отсрочки не падает на запрещённом хранилище', () => {
    // localStorage в приватном режиме бросает на самой записи. Портал обязан
    // пережить это: не записалось — спросим в следующий раз.
    const hostile = {
        getItem: () => { throw new Error('denied'); },
        setItem: () => { throw new Error('denied'); },
    };
    assert.doesNotThrow(() => snoozeInstallOffer(Date.now(), hostile));
    assert.equal(readInstallSnoozeUntil(hostile), 0);
});
