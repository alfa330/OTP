import './staleBundleRecovery';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App, { ErrorBoundary } from './App';
import { cleanTechnicalQueryParamsFromAddressBar } from './utils/urlHygiene';
import { startPwaRuntime } from './utils/pwa';
import './styles.css';

try {
  /* Метки перезагрузки (?v=, ?auth_reload=) свою работу уже сделали — документ
     загружен. Убираем их до старта роутера, чтобы они не жили в адресной строке
     и не уезжали в ссылки, которыми делятся. */
  cleanTechnicalQueryParamsFromAddressBar();
  const routerBase = import.meta.env.BASE_URL || '/';
  /* Портал как приложение на телефоне: значок на домашнем экране, запуск во
     весь экран, живучесть на плохой сети. Ставится ДО отрисовки: событие
     `beforeinstallprompt` браузер присылает когда захочет, и подписчик должен
     ждать его с первой секунды жизни страницы, а не с появления дерева.
     Сервис-воркер — только в собранной версии: на разработке он отдавал бы из
     кэша файлы, которые Vite только что пересобрал. */
  startPwaRuntime({ baseUrl: routerBase, withServiceWorker: import.meta.env.PROD });
  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(
    <BrowserRouter basename={routerBase}>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  );
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

