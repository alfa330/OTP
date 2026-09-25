/* Точка входа страницы тренажёров (trainers.html) — для iCORE Phone.
 *
 * Отдельная точка сборки, а не режим App.jsx: телефону нужны только тренажёры, без
 * меню, новостей, задач и остального портала; общий вход весил бы мегабайты и тянул
 * бы за собой вход в портал, которого у страницы нет — токен ей отдаёт телефон
 * (см. phoneBridge.js). Стили общие с порталом: карточки и кнопки в стиле iOS
 * берутся из тех же классов.
 */
import '../staleBundleRecovery';
import React from 'react';
import ReactDOM from 'react-dom/client';
import '../styles.css';
import TrainersEmbed from './TrainersEmbed';

try {
    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(<TrainersEmbed />);
} catch (err) {
    console.error('Trainers page failed to start:', err);
    document.getElementById('root').innerHTML = `
      <div class="min-h-screen flex items-center justify-center bg-slate-50 p-6">
        <div class="bg-white p-6 rounded-2xl shadow w-full max-w-sm text-center">
          <div class="text-[15px] font-semibold text-slate-900">Тренажёры не открылись</div>
          <p class="mt-2 text-[13px] text-slate-500">Нажмите «обновить» в iCORE Phone. Если не помогает — сообщите в техподдержку.</p>
        </div>
      </div>
    `;
}
