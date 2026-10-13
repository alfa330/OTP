/*
 * Страница «Адреса офисов» для iCORE Phone и то, что она делит с тренажёрами:
 * настоящие компоненты отрисовываются через react-dom/server (браузерного
 * окружения в проекте нет). Серверный рендер закрывает главное — что экран вообще
 * открывается: обращение к значению, объявленному ниже по телу компонента, даёт
 * ReferenceError уже на первом рендере, сборка такое пропускает, а у оператора во
 * вкладке телефона вместо страницы — белый экран.
 *
 * Эффекты в серверном рендере не идут, поэтому здесь — первые кадры: «подключаемся»,
 * экран QR до выдачи кода, раздел офисов до загрузки. Логика состояний и ответов
 * сервера — в tests/offices_embed.test.mjs.
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

async function load(name, file) {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${name}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(file, import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    // Пакеты оставляем внешними: их резолвит сам node из node_modules проекта.
    external: ['react', 'react-dom', 'axios', 'lucide-react', 'framer-motion', 'three', 'qrcode', '@tiptap/*'],
    // Стили (trainer.css проигрывателя) серверному рендеру не нужны.
    loader: { '.jsx': 'jsx', '.css': 'empty' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const { default: OfficesEmbed } = await load('OfficesEmbed', '../src/offices_embed/OfficesEmbed.jsx');
const { default: QrUnlockPanel } = await load('QrUnlockPanel', '../src/embed/QrUnlockPanel.jsx');
const { default: WikiOffices, loadProblem, officesScreen } = await load('WikiOfficesReadOnly', '../src/components/wiki/WikiOffices.jsx');
const { default: OfficeInfoModal } = await load('OfficeInfoModal', '../src/components/wiki/OfficeInfoModal.jsx');
const { filterPanelBox } = await load('OfficeFilters', '../src/components/wiki/OfficeFilters.jsx');
const {
  default: TrainersEmbed, recordingHint, recordingStatusOf,
} = await load('TrainersEmbedHonest', '../src/trainers_embed/TrainersEmbed.jsx');

const render = (Component, props = {}) => renderToStaticMarkup(React.createElement(Component, props));

/* Отрисовать «как в узком окне». «Мы на телефоне» компоненты узнают у
   window.matchMedia (useIsMobileShell); в node окна нет, и без подмены серверный
   рендер всегда настольный. Подмена — только на время одной отрисовки. */
const renderNarrow = (Component, props = {}) => {
  const had = Object.prototype.hasOwnProperty.call(globalThis, 'window');
  const previous = globalThis.window;
  globalThis.window = {
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  };
  try {
    return render(Component, props);
  } finally {
    if (had) globalThis.window = previous;
    else delete globalThis.window;
  }
};

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
// Исходник без комментариев: стражи по тексту не должны срабатывать на пояснения.
const code = (path) => source(path)
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

test('страница офисов открывается: первый кадр — проверка входа', () => {
  const html = render(OfficesEmbed);
  // Вне телефона (в node моста нет) — вход портала.
  assert.match(html, /Проверяем вход…/);
  // Корень — в теме вики: без .wiki-scope раздел выглядел бы не как в портале.
  assert.match(html, /class="wiki-scope /);
});

test('экран QR: что делать оператору и где вводит код супервайзер', () => {
  const html = render(QrUnlockPanel, {
    apiBase: 'https://otp-2-fos4.onrender.com',
    onGranted: () => {},
    title: 'Офисы откроются после подтверждения',
    inPhone: true,
  });
  assert.match(html, /Офисы откроются после подтверждения/);
  assert.match(html, /супервайзеру своего отдела/);
  assert.match(html, /«QR доступ»/);
  assert.match(html, /Ждём подтверждения…/);
  assert.match(html, /пока вы не выйдете из iCORE Phone/);
  // Код ещё не выдан — кнопки копирования нет, есть заглушка картинки.
  assert.doesNotMatch(html, /Скопировать код/);
});

test('экран QR в браузере говорит про портал, а не про телефон', () => {
  const html = render(QrUnlockPanel, { apiBase: '', onGranted: () => {}, title: 'Т', inPhone: false });
  assert.match(html, /до выхода из портала/);
  assert.doesNotMatch(html, /iCORE Phone/);
});

test('раздел офисов в режиме только чтения открывается без кнопок правки', () => {
  const html = render(WikiOffices, {
    base: 'https://otp-2-fos4.onrender.com/api/wiki',
    headers: {},
    showToast: () => {},
    spaceId: 3,
    readOnly: true,
  });
  assert.match(html, /Статус офисов по городам/);
  assert.match(html, /Город, адрес или телефон/);
  assert.doesNotMatch(html, /lucide-plus/, 'кнопки «+ Офис» нет');
});

test('readOnly перекрывает can_manage сервера, а не наоборот', () => {
  /* Право правки приходит с сервера ПОСЛЕ загрузки, и серверный рендер его не
     увидит (эффекты не идут). Поэтому правило проверяем по исходнику: право —
     производное «не только чтение И сервер разрешил», и других путей задать его
     нет. Разъедись они — кнопки правки вернулись бы во вкладку телефона. */
  const source = readFileSync(new URL('../src/components/wiki/WikiOffices.jsx', import.meta.url), 'utf8');
  assert.match(source, /readOnly = false/);
  assert.match(source, /const canManage = !readOnly && serverCanManage;/);
  assert.doesNotMatch(source, /setCanManage\(/);
  const setters = source.match(/setServerCanManage\(/g) || [];
  assert.equal(setters.length, 1, 'право с сервера записывается в одном месте — в загрузке');
});

test('тренажёры после выноса хука токена открываются как раньше', () => {
  const html = render(TrainersEmbed);
  assert.match(html, /Тренажёры/);
  assert.match(html, /Пройти тренажёр/);
  // Пока токен не спросили — «подключаемся», а не «с записью».
  assert.match(html, /title="Подключаемся…"/);
  assert.doesNotMatch(html, /Включить запись/);
});

test('значок записи говорит правду', () => {
  assert.equal(recordingHint({ asked: false, status: 'off' }), 'Подключаемся…');
  assert.equal(recordingHint({ asked: true, status: 'checking' }), 'Проверяем запись…');
  assert.equal(recordingHint({ asked: true, status: 'on' }), 'Попытки записываются');
  // Токен есть, а сессия без QR — сервер запись отклонит: «без записи».
  assert.equal(recordingHint({ asked: true, status: 'qr' }), 'Без записи — нужно подтверждение по QR');
  assert.equal(recordingHint({ asked: true, status: 'blocked', message: 'Раздел «Вики» не выдан вашему отделу' }),
    'Раздел «Вики» не выдан вашему отделу');
  assert.match(recordingHint({ asked: true, status: 'expired', inPhone: true }), /iCORE Phone/);
  assert.equal(recordingHint({ asked: true, status: 'off', error: 'Телефон не ответил' }), 'Телефон не ответил');
  assert.equal(recordingHint({ asked: true, status: 'off', inPhone: false }), 'Войдите в портал, чтобы попытки записывались');
});

test('проверка записи не дошла — «не проверено», а не «с записью»', () => {
  /* Сбой сети или 5xx раньше считался «записывается», и проверка больше не
     повторялась: одна неудача на старте — и значок обещал запись всю смену, хотя
     сессии нужен QR и сервер попытки отклонял. */
  const httpError = (status, data) => ({ response: { status, data } });
  const misses = [
    { message: 'Network Error' },
    { code: 'ECONNABORTED', message: 'timeout of 30000ms exceeded' },
    httpError(502, '<html>Bad gateway</html>'),
    httpError(500, { error: 'Внутренняя ошибка раздела «Вики»' }),
    httpError(429, {}),
    undefined,
  ];
  for (const err of misses) {
    assert.equal(recordingStatusOf(err).status, 'unknown', JSON.stringify(err));
  }
  // Отказы по существу читаются как раньше.
  assert.equal(recordingStatusOf(httpError(403, { code: 'SENSITIVE_ACCESS_REQUIRED' })).status, 'qr');
  assert.deepEqual(
    recordingStatusOf(httpError(403, { code: 'WIKI_DEPARTMENT_DISABLED', error: 'Раздел «Вики» не выдан вашему отделу' })),
    { status: 'blocked', message: 'Раздел «Вики» не выдан вашему отделу' },
  );
  assert.equal(recordingStatusOf(httpError(401, { error: 'JWT authentication failed' })).status, 'expired');

  assert.equal(recordingHint({ asked: true, status: 'unknown' }), 'Запись не проверена — нет связи с порталом');

  const jsx = code('../src/trainers_embed/TrainersEmbed.jsx');
  assert.doesNotMatch(jsx, /\|\| 'on'/, 'сбой проверки больше не подменяется на «с записью»');
  // «Не проверено» перепроверяется и по возврату во вкладку: слушателя видимости нет
  // только у состояний, где спрашивать незачем.
  assert.match(jsx, /if \(status === 'on' \|\| status === 'checking' \|\| status === 'off'\) return undefined;/);
  // «Сессии нет» перепроверяется, когда приходит новый токен той же сессии.
  assert.match(jsx, /token !== previous && status === 'expired'\) recheck\(\);/);
});

// ── Карточка офиса ──────────────────────────────────────────────────────────

const HOURS = { from: '09:00', to: '18:00' };
const OFFICE = {
  id: 7,
  name: 'Караганда',
  city: 'Караганда',
  address: 'пр. Бухар-Жырау, 66',
  phone: '',
  schedule: { mon: HOURS, tue: HOURS, wed: HOURS, thu: HOURS, fri: HOURS, sat: null, sun: null },
  status: 'active',
  kind: 'park',
  parks: [],
};
const modalProps = (office, extra = {}) => ({
  office,
  base: 'https://otp-2-fos4.onrender.com/api/wiki',
  dayISO: '2026-10-12',
  isToday: false,
  tick: 0,
  canManage: false,
  onClose() {},
  onCopyAddress() {},
  onEdit() {},
  onArchive() {},
  onRestore() {},
  onMarkDay() {},
  ...extra,
});
const telLinks = (html) => [...html.matchAll(/href="(tel:[^"]*)"/g)].map((m) => m[1]);

test('карточка офиса: каждый номер — своя ссылка, склейки в href нет', () => {
  const two = render(OfficeInfoModal, modalProps({ ...OFFICE, phone: '+7 707 705 08 80, +7 727 333 22 11' }));
  assert.deepEqual(telLinks(two), ['tel:+77077050880', 'tel:+77273332211']);
  // На экране — как в справочнике, вместе с запятой между номерами.
  assert.match(two, />\+7 707 705 08 80<\/a>, <a [^>]*>\+7 727 333 22 11<\/a>/);

  const ext = render(OfficeInfoModal, modalProps({ ...OFFICE, phone: '8 (7212) 50-60-70 доб. 12' }));
  assert.deepEqual(telLinks(ext), ['tel:87212506070'], 'добавочный в набираемый номер не идёт');
  assert.match(ext, />8 \(7212\) 50-60-70 доб\. 12<\/a>/, 'но на экране он остаётся рядом с номером');

  const one = render(OfficeInfoModal, modalProps({ ...OFFICE, phone: '+7 (7212) 41-00-00' }));
  assert.deepEqual(telLinks(one), ['tel:+77212410000']);

  // Номером не читается — остаётся текстом, а не ссылкой на выдуманный номер.
  const none = render(OfficeInfoModal, modalProps({ ...OFFICE, phone: 'уточняйте у супервайзера' }));
  assert.deepEqual(telLinks(none), []);
  assert.match(none, /уточняйте у супервайзера/);

  // Телефона нет — нет и блока «Телефоны».
  assert.doesNotMatch(render(OfficeInfoModal, modalProps(OFFICE)), /Телефоны/);
});

/* Подвал IosModal узнаём по его собственной примете: отступ под «домашнюю» полосу
   телефона в окне есть только у него. */
const FOOTER_MARK = 'env(safe-area-inset-bottom)';

test('карточка офиса: подвал только когда в нём что-то есть', () => {
  // Компьютер: «Закрыть» в подвале, как и было.
  const desktop = render(OfficeInfoModal, modalProps(OFFICE));
  assert.ok(desktop.includes(FOOTER_MARK));
  assert.match(desktop, /Закрыть/);

  /* Узкое окно без права правки — вкладка iCORE Phone (справочник только для
     чтения) и оператор, открывший портал с телефона: ни действий, ни «Закрыть».
     Раньше от подвала оставалась пустая полоса с рамкой — 25 px из 285. */
  const narrow = renderNarrow(OfficeInfoModal, modalProps(OFFICE));
  assert.ok(!narrow.includes(FOOTER_MARK), 'пустого подвала нет');
  assert.doesNotMatch(narrow, /Закрыть/);
  assert.match(narrow, /aria-label="Назад"/, 'с экрана уходят шевроном в шапке');

  // Узкое окно с правом правки: подвал с действиями на месте, «Закрыть» в нём нет.
  const manager = renderNarrow(OfficeInfoModal, modalProps(OFFICE, { canManage: true }));
  assert.ok(manager.includes(FOOTER_MARK));
  assert.match(manager, /wiki-m-office-actions/);
  assert.match(manager, /Изменить/);
  assert.doesNotMatch(manager, /Закрыть/);
});

// ── Раздел офисов: загрузка, обновление, окна ───────────────────────────────

test('сбой загрузки — карточка ошибки, а не «Справочник ещё не заполнен»', () => {
  // Первая загрузка упала, показать нечего — карточка ошибки с повтором.
  assert.equal(officesScreen({ loading: false, failed: true, total: 0, shown: 0 }), 'failed');
  // Идёт повтор — карточка остаётся на месте, «Загружаем…» не мигает.
  assert.equal(officesScreen({ loading: true, failed: true, total: 0, shown: 0 }), 'failed');
  // Загрузилось, а офисов правда нет — честное «пусто».
  assert.equal(officesScreen({ loading: false, failed: false, total: 0, shown: 0 }), 'empty');
  assert.equal(officesScreen({ loading: true, failed: false, total: 0, shown: 0 }), 'loading');
  assert.equal(officesScreen({ loading: false, failed: false, total: 6, shown: 6 }), 'list');
  // Обновить не вышло, но прежний список есть — он остаётся (плашка «не удалось
  // обновить» стоит над ним).
  assert.equal(officesScreen({ loading: false, failed: true, total: 6, shown: 6 }), 'list');
  // Список есть, а условие легенды скрыло все строки — «ничего не найдено», не ошибка.
  assert.equal(officesScreen({ loading: false, failed: true, total: 6, shown: 0 }), 'empty');
  assert.equal(officesScreen({ loading: true, failed: true, total: 6, shown: 6 }), 'loading');

  // Текст карточки — русский: сообщение axios человеку не показываем.
  assert.match(loadProblem({ message: 'Network Error' }), /^Нет связи с порталом/);
  assert.match(
    loadProblem({ message: 'Request failed with status code 502', response: { status: 502, data: '<html>Bad gateway</html>' } }),
    /^Портал не ответил/,
  );
  assert.equal(
    loadProblem({ response: { status: 500, data: { error: 'Внутренняя ошибка раздела «Вики»' } } }),
    'Внутренняя ошибка раздела «Вики»',
  );
  for (const err of [{ message: 'Network Error' }, { message: 'timeout of 1ms exceeded' }, { response: { status: 502, data: {} } }]) {
    assert.doesNotMatch(loadProblem(err), /[A-Za-z]{4,}/);
  }

  const jsx = code('../src/components/wiki/WikiOffices.jsx');
  assert.match(jsx, /screen === 'failed' && \(/);
  assert.match(jsx, /Не удалось загрузить офисы/);
  assert.match(jsx, /screen === 'empty' && \(/, '«Офисов пока нет» — только когда список загрузился');

  /* Ошибку загрузки раздел показывает на месте и помечает свой тост (inline):
     страница телефона его не дублирует — внизу вкладки высотой 285 px тост лёг бы
     на саму карточку и закрыл кнопку «Повторить». В портале тост остаётся. */
  assert.match(jsx, /toast\(errText\(e, 'Не удалось загрузить офисы'\), 'error', \{ inline: true \}\)/);
  assert.match(code('../src/offices_embed/OfficesEmbed.jsx'), /phaseRef\.current !== 'ready' \|\| meta\?\.inline\)\) return;/);
  // Карточка ошибки во вкладке собрана плотнее — по метке на ней.
  assert.match(jsx, /office-load-failed/);
  assert.match(source('../src/offices_embed/offices-embed.css'), /\.offices-embed \.office-load-failed \{/);
});

test('раздел офисов обновляется по просьбе страницы — тихо и с переходом на новые сутки', () => {
  const jsx = code('../src/components/wiki/WikiOffices.jsx');
  // Портал этих свойств не передаёт: по умолчанию раздел грузится как раньше.
  assert.match(jsx, /reloadKey = 0, onLoadResult = null, errorHint = ''/);
  // Просьба (смена reloadKey) доходит до загрузки…
  assert.match(jsx, /\}, \[load, query, focusTick, reloadTick\]\);/);
  // …и идёт тихо: список остаётся на экране, тост не всплывает.
  assert.match(jsx, /if \(!quiet\) setLoading\(true\);/);
  assert.match(jsx, /if \(!quiet\) toast\(/);
  // Новые сутки: дата переходит на «сегодня», если человек не выбирал её сам.
  assert.match(jsx, /if \(!dayPinned\.current && dayISO !== current\) setDayISO\(current\);/);
  // Дату руками ставит только pickDay — он и помнит, что её выбрали.
  assert.equal((jsx.match(/setDayISO\(/g) || []).length, 2, 'setDayISO — в pickDay и в переходе на новые сутки');

  // Страница телефона этим пользуется, портал — нет.
  const page = code('../src/offices_embed/OfficesEmbed.jsx');
  assert.match(page, /reloadKey=\{listRefresh\.reloadKey\}/);
  assert.match(page, /onLoadResult=\{listRefresh\.onLoadResult\}/);
  assert.doesNotMatch(code('../src/components/wiki/WikiView.jsx'), /reloadKey|onLoadResult/);
});

test('окна раздела офисов лежат вне потока отступов', () => {
  /* space-y-4 вешает margin-top на каждого прямого ребёнка, кроме первого. Окно
     (IosModal) — fixed inset-0, и с таким отступом его корень начинался на 16 px ниже
     верха окна: незатемнённая полоса страницы и минус 16 px высоты. Окна обязаны
     стоять в обёртке без собственного блока, а не прямыми детьми раздела. */
  const jsx = code('../src/components/wiki/WikiOffices.jsx');
  const wrapper = jsx.indexOf('<div className="contents">');
  assert.ok(wrapper > 0, 'обёртка окон на месте');
  for (const tag of ['<IosModal', '<OfficeInfoModal', '<OfficeDayModal']) {
    assert.ok(jsx.indexOf(tag) > wrapper, `${tag} — внутри обёртки`);
    assert.equal(jsx.indexOf(tag), jsx.lastIndexOf(tag), `${tag} отрисован в одном месте`);
  }
});

// ── Вёрстка под вкладку 393 × 285 ───────────────────────────────────────────

test('страница офисов: свои стили заперты на её корень', () => {
  const html = render(OfficesEmbed);
  assert.match(html, /class="wiki-scope offices-embed /);
  assert.match(source('../src/offices_embed/main.jsx'), /^import '\.\/offices-embed\.css';$/m);

  const css = source('../src/offices_embed/offices-embed.css').replace(/\/\*[\s\S]*?\*\//g, '');
  const selectors = [...css.matchAll(/([^{}]+)\{[^{}]*\}/g)].map((m) => m[1].trim());
  assert.ok(selectors.length >= 4);
  for (const selector of selectors) {
    assert.ok(selector.startsWith('.offices-embed '), `правило «${selector}» действует и вне страницы офисов`);
  }
  // Карточка офиса — сетка: текст во всю ширину, статус второй строкой.
  assert.match(css, /\.offices-embed \.wiki-m-office-card > button \{[^}]*display: grid;[^}]*grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(css, /\.offices-embed \.wiki-m-office-card > button > span \{[^}]*grid-area: 2 \/ 1;/);
  // Оболочку портала страница не включает: её слой поднимает кегли до 17 px.
  assert.doesNotMatch(code('../src/offices_embed/OfficesEmbed.jsx'), /mobile-shell/);
});

test('экран QR: в узкой вкладке код и кнопка стоят перед QR', () => {
  const jsx = code('../src/embed/QrUnlockPanel.jsx');
  // «QR слева, код справа» — только от 460 px: вкладка телефона — около 393.
  assert.match(jsx, /flex flex-col items-center gap-3 min-\[460px\]:flex-row/);
  assert.doesNotMatch(jsx, /min-\[400px\]/, 'порог 400 px выше ширины вкладки: ряд там не включался, а QR стоял первым');
  // Столбиком колонка кода — первой, а кнопка в ней — над самим кодом.
  assert.match(jsx, /className="order-first [^"]*min-\[460px\]:order-none"/);
  assert.match(jsx, /\$\{iosBtnSecondary\} order-1 [^`]*min-\[460px\]:order-none/);
  assert.match(jsx, /className="order-2 [^"]*select-all [^"]*min-\[460px\]:order-none/);
});

test('панель фильтра не выходит за окно', () => {
  const inside = (box, { viewHeight, floor = viewHeight }) => {
    const top = box.top ?? (viewHeight - box.bottom - box.maxHeight);
    const bottom = box.top != null ? box.top + box.maxHeight : viewHeight - box.bottom;
    assert.ok(top >= 0, `верх панели на ${top}`);
    assert.ok(bottom <= floor, `низ панели на ${bottom} при границе ${floor}`);
    assert.ok(box.maxHeight > 0);
  };

  // Вкладка iCORE Phone 393 × 285: у кнопки 107 px снизу и 106 сверху — лист.
  const tab = { viewWidth: 393, viewHeight: 285, pageWidth: 378, floor: 285, isPhone: true };
  const sheet = filterPanelBox({ ...tab, rect: { top: 122, bottom: 162, right: 366 } });
  assert.deepEqual(sheet, { sheet: true, width: 346, left: 16, top: 8, bottom: undefined, maxHeight: 269 });
  inside(sheet, tab);
  assert.ok(sheet.left + sheet.width <= tab.pageWidth, 'правый край — внутри страницы');

  // 243 × 285: раньше ширина была max(240, 211) = 240 при левом крае 16 — за окно.
  const tiny = { viewWidth: 243, viewHeight: 285, pageWidth: 228, floor: 285, isPhone: true };
  const tinyBox = filterPanelBox({ ...tiny, rect: { top: 196, bottom: 236, right: 110 } });
  assert.equal(tinyBox.sheet, true);
  assert.equal(tinyBox.width, 196);
  assert.ok(tinyBox.left + tinyBox.width <= tiny.pageWidth);
  inside(tinyBox, tiny);

  // Портал на телефоне (390 × 844, бар разделов с 746): панель у кнопки, как и была.
  const phone = { viewWidth: 390, viewHeight: 844, pageWidth: 390, floor: 746, isPhone: true };
  const below = filterPanelBox({ ...phone, rect: { top: 200, bottom: 240, right: 374 } });
  assert.deepEqual(below, { sheet: false, width: 358, left: 16, top: 246, bottom: undefined, maxHeight: 490 });
  inside(below, phone);

  // Портал на компьютере: 268 px у правого края кнопки, как и была.
  const desktop = { viewWidth: 1366, viewHeight: 768, pageWidth: 1351, floor: 768, isPhone: false };
  const popover = filterPanelBox({ ...desktop, rect: { top: 250, bottom: 290, right: 1200 } });
  assert.deepEqual(popover, { sheet: false, width: 268, left: 932, top: 296, bottom: undefined, maxHeight: 462 });
  // Мало места снизу — вверх, и ровно в доступную высоту (раньше — не меньше 200).
  const up = filterPanelBox({ ...desktop, viewHeight: 500, floor: 500, rect: { top: 300, bottom: 340, right: 1200 } });
  assert.deepEqual([up.sheet, up.top, up.bottom, up.maxHeight], [false, undefined, 206, 284]);
  inside(up, { viewHeight: 500 });

  // Где бы ни стояла кнопка и каким бы ни было окно — панель целиком в окне.
  for (const viewHeight of [240, 285, 390, 540, 768, 1080]) {
    for (let top = 0; top + 40 <= viewHeight; top += 13) {
      for (const isPhone of [true, false]) {
        const view = { viewWidth: 900, viewHeight, pageWidth: 885, floor: viewHeight, isPhone };
        inside(filterPanelBox({ ...view, rect: { top, bottom: top + 40, right: 700 } }), view);
      }
    }
  }
});
