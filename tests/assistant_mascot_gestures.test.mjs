// Жесты шарика → настроение талисмана (тыква, котик): listenToOrb в порядке
// событий НАСТОЯЩЕГО шарика (стенд и разбор облика «Котик», 02.11.2026).
//
// Мышью AssistantOrb переключает помощника на pointerup, поэтому click приходит
// уже после «закрыли»; с клавиатуры — наоборот: click раньше переключения.
// Стережём, что закрытие обоими путями даёт грусть и не сдвигает круг эмоций,
// конец перетаскивания не считается щелчком, щелчок и колесо будят спящего,
// котик в откреплённом окне слушает своё окно, а отписка снимает всё.
//
// Запуск: node --test tests/assistant_mascot_gestures.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import { listenToOrb } from '../src/components/assistant/mascot/orbGestures.js';
import catMood, { CLICK_CYCLE } from '../src/components/assistant/cat/catMood.js';
import { SLEEP_AFTER_MS } from '../src/components/assistant/mascot/mood.js';

/* Лёгкий DOM: кнопка шарика, корень талисмана, документ и окно — EventTarget'ы,
   наблюдатель атрибутов вызываем руками, как его вызвал бы браузер. */
function stand({ expanded = false, ownWindow = null } = {}) {
    const win = ownWindow || new EventTarget();
    const doc = new EventTarget();
    doc.defaultView = win;
    const observers = [];
    win.MutationObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this); }
        observe() {}
        disconnect() { this.callback = null; }
    };
    win.requestAnimationFrame = (fn) => { fn(); return 1; };
    win.cancelAnimationFrame = () => {};
    const button = new EventTarget();
    const classes = new Set();
    const attrs = { 'aria-expanded': String(expanded) };
    button.classList = { contains: (name) => classes.has(name) };
    button.getAttribute = (name) => attrs[name] ?? null;
    const root = new EventTarget();
    root.ownerDocument = doc;
    root.closest = (selector) => (selector === '.aorb-button' ? button : null);
    root.getBoundingClientRect = () => ({ left: 1000, top: 600, width: 56, height: 56 });
    const clock = { t: 0 };
    const events = [];
    const looks = [];
    let sleeping = false;
    const stop = listenToOrb(root, {
        dispatch: (event) => events.push(event),
        isSleeping: () => sleeping,
        onLook: (x, y) => looks.push([x, y]),
        now: () => clock.t,
    });
    const notify = () => observers.forEach((o) => o.callback?.());
    return {
        win, doc, button, root, clock, events, looks, stop,
        setSleeping: (value) => { sleeping = value; },
        setExpanded: (value) => { attrs['aria-expanded'] = String(value); notify(); },
        setDragging: (value) => { if (value) classes.add('aorb-dragging'); else classes.delete('aorb-dragging'); notify(); },
        fire: (node, type, props = {}) => {
            const event = new Event(type);
            for (const [key, value] of Object.entries(props)) Object.defineProperty(event, key, { value });
            node.dispatchEvent(event);
        },
    };
}

const replay = (events) => events.reduce(catMood.moodReducer, catMood.initialMood());

test('закрыли мышью: нажатие → «закрыли» → click — грусть, круг не сдвинулся', () => {
    const s = stand({ expanded: true });
    s.clock.t = 1000;
    s.fire(s.button, 'pointerdown');
    s.clock.t = 1080;
    s.setExpanded(false);                       // pointerup переключил помощника
    s.clock.t = 1085;
    s.fire(s.button, 'click', { detail: 1 });
    assert.deepEqual(s.events.map((e) => e.type), ['press', 'close', 'click']);
    const state = replay(s.events);
    assert.equal(catMood.displayEmotion(state, 1200), 'sad');
    assert.equal(state.cycle, 0);
    s.stop();
});

test('закрыли с клавиатуры: click раньше «закрыли» — помечен closing, тоже грусть', () => {
    const s = stand({ expanded: true });
    s.clock.t = 500;
    s.fire(s.button, 'click', { detail: 0 });
    s.setExpanded(false);
    assert.deepEqual(s.events.map((e) => [e.type, e.closing ?? null]), [['click', true], ['close', null]]);
    const state = replay(s.events);
    assert.equal(catMood.displayEmotion(state, 600), 'sad');
    assert.equal(state.cycle, 0, 'клавиатурное закрытие не крадёт эмоцию из круга');
    s.stop();
});

test('открыли — с клавиатуры и мышью — первая эмоция круга', () => {
    const keyboard = stand({ expanded: false });
    keyboard.clock.t = 100;
    keyboard.fire(keyboard.button, 'click', { detail: 0 });
    keyboard.setExpanded(true);
    assert.equal(catMood.displayEmotion(replay(keyboard.events), 150), CLICK_CYCLE[0]);
    keyboard.stop();
    const mouse = stand({ expanded: false });
    mouse.clock.t = 100;
    mouse.fire(mouse.button, 'pointerdown');
    mouse.setExpanded(true);
    mouse.fire(mouse.button, 'click', { detail: 1 });
    assert.equal(catMood.displayEmotion(replay(mouse.events), 150), CLICK_CYCLE[0]);
    mouse.stop();
});

test('перетаскивание: страх до отпускания, click сразу после — не щелчок', () => {
    const s = stand();
    s.clock.t = 0;
    s.fire(s.button, 'pointerdown');
    s.setDragging(true);
    s.clock.t = 900;
    s.setDragging(false);
    s.clock.t = 920;
    s.fire(s.button, 'click', { detail: 1 });
    assert.deepEqual(s.events.map((e) => e.type), ['press', 'dragStart', 'dragEnd']);
    s.clock.t = 2000;
    s.fire(s.button, 'click', { detail: 1 });
    assert.equal(s.events.at(-1).type, 'click', 'обычный щелчок позже — снова щелчок');
    s.stop();
});

test('спящего будят нажатие, колесо и клавиши; бодрого — не трогают', () => {
    const s = stand();
    s.setSleeping(true);
    s.fire(s.button, 'pointerdown');
    assert.deepEqual(s.events.map((e) => e.type), ['wake', 'press'], 'проснулся раньше, чем удивился нажатию');
    s.events.length = 0;
    s.fire(s.win, 'wheel');
    s.fire(s.win, 'keydown');
    assert.deepEqual(s.events.map((e) => e.type), ['wake', 'wake']);
    s.setSleeping(false);
    s.events.length = 0;
    s.fire(s.win, 'pointermove', { clientX: 10, clientY: 10 });
    s.fire(s.win, 'wheel');
    assert.deepEqual(s.events, [], 'движение мыши бодрого талисмана не перерисовывает');
    s.stop();
});

test('простой SLEEP_AFTER_MS — сон; активность откладывает его', (t) => {
    t.mock.timers.enable({ apis: ['setInterval'] });
    const s = stand();
    s.clock.t = SLEEP_AFTER_MS - 2000;
    t.mock.timers.tick(1000);
    assert.equal(s.events.length, 0);
    s.fire(s.win, 'keydown');                   // работает с клавиатуры
    s.clock.t += SLEEP_AFTER_MS - 1000;
    t.mock.timers.tick(1000);
    assert.equal(s.events.length, 0, 'набор текста — не простой');
    s.clock.t += 2000;
    t.mock.timers.tick(1000);
    assert.deepEqual(s.events.map((e) => e.type), ['sleep']);
    s.setSleeping(true);
    t.mock.timers.tick(5000);
    assert.equal(s.events.length, 1, 'спящему «сон» раз в секунду не шлём');
    s.stop();
});

test('котик в откреплённом окне слушает своё окно, а не портал', () => {
    // Портал — глобальный window: слушай хук его, клавиши портала будили бы котика в окне.
    const portal = new EventTarget();
    const saved = globalThis.window;
    globalThis.window = portal;
    try {
        const pip = new EventTarget();
        const s = stand({ ownWindow: pip });
        s.setSleeping(true);
        s.fire(portal, 'keydown');
        s.fire(portal, 'pointermove', { clientX: 5, clientY: 5 });
        assert.equal(s.events.length, 0, 'работа в портале котика в окне не трогает');
        s.fire(pip, 'keydown');
        assert.deepEqual(s.events.map((e) => e.type), ['wake']);
        s.stop();
    } finally {
        globalThis.window = saved;
    }
});

test('взгляд: к курсору, за окном — прямо; отписка снимает всё', () => {
    const s = stand();
    s.fire(s.win, 'pointermove', { clientX: 200, clientY: 620 });
    assert.ok(s.looks.at(-1)[0] < -0.9, 'курсор далеко слева — взгляд влево');
    s.fire(s.doc, 'pointerout', { relatedTarget: null });
    assert.deepEqual(s.looks.at(-1), [0, 0]);
    s.stop();
    const before = s.events.length;
    s.setSleeping(true);
    s.fire(s.button, 'pointerdown');
    s.fire(s.win, 'keydown');
    s.fire(s.button, 'click', { detail: 1 });
    assert.equal(s.events.length, before, 'после отписки слушателей нет');
});
