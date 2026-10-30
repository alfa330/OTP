import './staleBundleRecovery';
import { embedLaunchDone, reloadPending } from './embedLaunchBoot';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App, { ErrorBoundary } from './App';
import { cleanTechnicalQueryParamsFromAddressBar } from './utils/urlHygiene';
import { startPwaRuntime } from './utils/pwa';
import { isHalloweenTime } from './components/seasonal/halloween/halloweenWindow';
import { applyDarkTheme, readStoredDarkTheme } from './utils/darkTheme';
import './styles.css';

/* Хэллоуин (до конца 1 ноября 2026). Праздничная метка и тёмный слой — сразу, до
   отрисовки: иначе каждая загрузка начиналась бы со светлого кадра и синих кнопок.
   Сбой праздничного чанка (сеть, выкладка) не должен перезагружать портал через
   staleBundleRecovery: украшения просто не появятся. Слушатель на фазе перехвата
   срабатывает раньше восстановления и гасит только свои чанки. */
const HALLOWEEN_CHUNK_RE = /mountHalloween|PumpkinMascot|halloween-theme/;
if (isHalloweenTime()) {
  document.documentElement.setAttribute('data-otp-halloween', '');
  window.addEventListener('vite:preloadError', (event) => {
    if (!HALLOWEEN_CHUNK_RE.test(String(event?.payload?.message || event?.payload || ''))) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  import('./components/seasonal/halloween/halloween-theme.css').catch(() => {});
  if (readStoredDarkTheme()) applyDarkTheme(true);
}

try {
  /* Метки перезагрузки (?v=, ?auth_reload=) свою работу уже сделали — документ
     загружен. Убираем их до старта роутера, чтобы они не жили в адресной строке
     и не уезжали в ссылки, которыми делятся. */
  cleanTechnicalQueryParamsFromAddressBar();
  /* БЕЗ ЗАВЕРШАЮЩЕГО СЛЭША. От этого зависит, покажется ли портал вообще.
   *
   * На GitHub Pages портал живёт в /OTP/, и `BASE_URL` приходит сюда именно так
   * — со слэшем. Роутер сверяет адрес с этой строкой в `stripBasename`
   * (react-router 6.30): если `location.pathname` не начинается с basename, он
   * НЕ РИСУЕТ НИЧЕГО — `<Router>` возвращает null, и вместе с ним исчезает всё
   * приложение. Предупреждение об этом в сборке для людей вырезано, поэтому в
   * консоли пусто: просто белый экран.
   *
   * А адрес раздела портал пишет как `/OTP?view=…` — без слэша перед `?`
   * (resolveAppPathname в App.jsx). Строка «/OTP» с «/OTP/» не начинается.
   * Пока адрес меняется нашим `history.replaceState`, роутер о подмене не знает
   * и рисует прежнее место; но первый же ШАГ НАЗАД — кнопкой, свайпом от края
   * или нашей же сторожевой записью — приносит `popstate`, роутер перечитывает
   * адрес, не узнаёт его и гасит портал. Ровно это владелец и видел: «после
   * кнопки назад или свайпа ловлю белый экран».
   *
   * Basename без слэша принимает ОБА вида адреса: и «/OTP» (следующего знака
   * нет), и «/OTP/lms/...» (следующий знак — слэш). Это важнее косметики:
   * записи вида «/OTP?view=…» уже лежат в историях у людей на телефонах, и
   * починка одного лишь адреса их не спасла бы. */
  const routerBase = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '') || '/';
  /* Портал как приложение на телефоне: значок на домашнем экране, запуск во
     весь экран, живучесть на плохой сети. Ставится ДО отрисовки: событие
     `beforeinstallprompt` браузер присылает когда захочет, и подписчик должен
     ждать его с первой секунды жизни страницы, а не с появления дерева.
     Сервис-воркер — только в собранной версии: на разработке он отдавал бы из
     кэша файлы, которые Vite только что пересобрал. */
  startPwaRuntime({ baseUrl: routerBase, withServiceWorker: import.meta.env.PROD });
  const root = ReactDOM.createRoot(document.getElementById('root'));
  /* Окно «Чатов ОП» в CRM сначала меняет ссылку входа на сессию (embedLaunchBoot.js);
     вне окна обещание уже выполнено. После обмена окно перезагружается — не рисуем. */
  embedLaunchDone.then((launch) => {
    if (reloadPending(launch)) return;
    root.render(
      <BrowserRouter basename={routerBase}>
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </BrowserRouter>
    );
    /* Хэллоуин (до конца 1 ноября 2026): украшения поверх портала — своим
       чанком и своим корнем, когда портал уже встал и браузер свободен.
       После срока чанк не грузится вовсе. */
    if (isHalloweenTime()) {
      const startHalloween = () => import('./components/seasonal/halloween/mountHalloween')
        .then((module) => module?.mountHalloween?.())
        .catch(() => {});
      if ('requestIdleCallback' in window) window.requestIdleCallback(startHalloween, { timeout: 2500 });
      else window.setTimeout(startHalloween, 800);
    }
  }).catch((err) => console.error('Root rendering error:', err));
} catch (err) {
  console.error('Root rendering error:', err);
  document.getElementById('root').innerHTML = `
    <div class="min-h-screen flex items-center justify-center bg-gray-100">
      <div class="bg-white p-8 rounded-lg shadow-lg w-full max-w-md">
        <h1 class="text-2xl font-bold mb-4 text-center text-red-500">Application Failed to Load</h1>
        <p class="text-center">An error occurred while loading the application. Please check the console for details.</p>
      </div>
    </div>
  `;
}

