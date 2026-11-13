/* HTML-вложение как макет: страница рисуется в окне просмотра, а не кодом.

   Просьба владельца 13.11.2026: «сделай так, чтобы html файлы открывались как
   макеты». Макеты часто держатся на скриптах (Tailwind с CDN, React, Babel),
   поэтому скрипты страницы работают — но в клетке, из которой не достать
   портал. ГРАНИЦА — первые два пункта; остальное — меры вежливости и запас.

   1. iframe с srcdoc и sandbox="allow-scripts" БЕЗ allow-same-origin: у
      страницы непрозрачное происхождение — ни куки, ни localStorage, ни DOM
      портала; без allow-forms, allow-popups, allow-top-navigation,
      allow-modals, allow-downloads — ни форм, ни окон, ни перехода вкладки, ни
      скачиваний. Именно srcdoc, а не blob:-адрес: blob: принадлежит
      происхождению портала, и «Открыть фрейм в новой вкладке» запустило бы файл
      с правами портала (нашёл разбор 13.11, H1). Документ about:srcdoc
      отдельной вкладкой не открыть.
   2. credentialless (Chrome): запросы макета уходят без куки вообще. Веб-портал
      на проде и так держит вход токеном в заголовке, а не кукой (API на другом
      домене), так что учётных данных, которые браузер приложил бы сам, у макета
      нет; изменяющие запросы с кукой и чужим Origin сервер отклоняет.
   3. CSP первым элементом документа: connect-src 'none' (fetch, XHR,
      WebSocket, sendBeacon), form-action 'none', frame-src/object-src/worker-src
      'none' — вложенная страница или воркер своей политики не наследуют.
      От вывоза данных CSP НЕ защищает: картинки, шрифты, стили и скрипты с CDN
      разрешены (без них макет не похож на себя), а значит, и запрос-«пиксель».
      Защищать нечего: у макета нет ничего, кроме самого файла.
   4. Скрипт-сторож (идёт сразу за CSP): в верхнем окне или с настоящим
      происхождением не даёт файлу исполниться вовсе; ссылки не уводят со
      страницы, формы не отправляются, Esc внутри макета закрывает окно
      просмотра. Якоря «#раздел» сторож прокручивает сам. Сторож слушает window
      во всплытии и не трогает то, что страница обработала сама
      (defaultPrevented) — роутер прототипа по-прежнему переключает экраны.
      <meta http-equiv="refresh"> вырезается. Что всё же ушло со страницы
      скриптом, окно возвращает назад (createLeaveWatch) — это вежливость к
      макету, а не граница: страница, у которой не наступает load, может стоять
      в рамке сколько угодно (тоже лишь своим же содержимым — пункт 1 держит).
   5. Бесконечный цикл в макете может подвесить вкладку там, где браузер не
      выносит песочницу в отдельный процесс (телефоны, Safari), — цена того, что
      скрипты макета включены.

   Модуль чистый — проверяется в node (tests/file_preview.test.mjs). */

export const MOCKUP_SANDBOX = 'allow-scripts';

export const MOCKUP_CSP = [
    "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'",
    "connect-src 'none'",
    "form-action 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "worker-src 'none'",
].join('; ');

/** Сообщение сторожа родителю: в макете нажали Esc. Больше макет ничего не может попросить. */
export const MOCKUP_ESCAPE_MESSAGE = 'otp-mockup-escape';

const GUARD = `(function () {
  var w = window, d = document;
  if (w.origin !== 'null' || w.top === w) {
    try { w.stop(); } catch (error) {}
    d.documentElement.innerHTML = '';
    return;
  }
  function ownTarget(href) {
    var id = href.slice(1);
    try { id = decodeURIComponent(id); } catch (error) {}
    return id ? d.getElementById(id) || d.getElementsByName(id)[0] || null : null;
  }
  w.addEventListener('click', function (event) {
    if (event.defaultPrevented) return;
    var node = event.target, link = node && node.closest ? node.closest('a[href], area[href]') : null;
    if (!link) return;
    var href = link.getAttribute('href') || '';
    event.preventDefault();
    if (href.charAt(0) !== '#') return;
    var target = ownTarget(href);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else if (href === '#' || href === '#top') w.scrollTo({ top: 0, behavior: 'smooth' });
  });
  w.addEventListener('submit', function (event) { if (!event.defaultPrevented) event.preventDefault(); });
  w.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !event.defaultPrevented) {
      try { parent.postMessage({ type: '${MOCKUP_ESCAPE_MESSAGE}' }, '*'); } catch (error) {}
    }
  });
})();`;

const HEAD = `<meta http-equiv="Content-Security-Policy" content="${MOCKUP_CSP}">`
    + '<meta name="referrer" content="no-referrer">'
    + `<script>${GUARD}</script>`;

/* Куда вставить защиту. Только после doctype, перед которым ОДНИ пробелы в
   смысле HTML (таб, перевод строки, перевод страницы, возврат каретки,
   пробел): так страница остаётся в своём режиме вёрстки. Всё прочее перед
   doctype — комментарии, <?…?>, неразрывный пробел, текст — разборщик понимает
   иначе, чем любое регулярное выражение («<!-->» закрывает комментарий сразу,
   «<?x>» — на первом «>»), и под ним можно спрятать скрипт, который выполнится
   раньше политики (разбор 13.11, H2). Тогда защита встаёт в самое начало файла,
   а страница уходит в режим совместимости — цена редкого файла, не защиты. */
const DOCTYPE_FIRST = /^[\t\n\f\r ]*<!doctype[^>]*>/i;
// Длина тега ограничена: без предела файл из тысяч незакрытых «<meta » разбирался
// бы за квадратичное время и подвешивал вкладку ещё до показа (разбор 13.11).
// Файл без слова «refresh» не просматривается вовсе — это почти все файлы.
const REFRESH = /<meta\b[^>]{0,1024}?\bhttp-equiv\s*=\s*["']?\s*refresh\b[^>]{0,1024}>/gi;

export function prepareMockupHtml(source) {
    const raw = String(source ?? '').replace(/^\uFEFF/, '');
    const html = /refresh/i.test(raw) ? raw.replace(REFRESH, '') : raw;
    const doctype = DOCTYPE_FIRST.exec(html);
    const at = doctype ? doctype[0].length : 0;
    return html.slice(0, at) + HEAD + html.slice(at);
}

/** Превью HTML для окна: исходник — для «Кода» и «Скопировать код», документ клетки — для iframe. */
export function buildHtmlPreview(text) {
    return { kind: 'html', text, srcDoc: prepareMockupHtml(text) };
}

/* Сторож ухода. Первая загрузка каждого iframe — показ; следующая у того же
   iframe — страница ушла (её возвращают); со второго ухода — без скриптов.
   Считается для каждого iframe отдельно: новый iframe (после возврата) свой
   счёт начинает с нуля. */
export function createLeaveWatch() {
    const loads = new WeakMap();
    let returns = 0;
    return {
        load(frame) {
            const count = (loads.get(frame) || 0) + 1;
            loads.set(frame, count);
            if (count === 1) return 'shown';
            returns += 1;
            return returns >= 2 ? 'scripts-off' : 'returned';
        },
    };
}

/** Esc из макета принимается только от своего iframe и только когда фокус в нём:
 *  сообщение без настоящего нажатия (по таймеру) окно не закроет. */
export function acceptMockupEscape(event, frame, activeElement) {
    return Boolean(frame) && Boolean(event?.source) && event.source === frame.contentWindow
        && event.data?.type === MOCKUP_ESCAPE_MESSAGE && activeElement === frame;
}

/** Виды HTML-файла: на телефоне макет и так в ширину телефона. */
export function htmlViewModes(isPhoneShell) {
    return isPhoneShell ? ['desktop', 'code'] : ['desktop', 'phone', 'code'];
}

export function shownHtmlMode(mode, isPhoneShell) {
    return htmlViewModes(isPhoneShell).includes(mode) ? mode : 'desktop';
}
