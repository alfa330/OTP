/* Точка входа страницы «Карточка клиента» (client_card.html) — для iCORE Phone.
 *
 * Отдельная точка сборки, а не режим App.jsx — по той же причине, что у тренажёров и
 * офисов (src/trainers_embed/main.jsx): телефону нужна одна карточка, без меню и входа в
 * портал, и грузиться она должна за мгновение — её открывает каждый звонок.
 *
 * ErrorBoundary свой и маленький: общий живёт внутри App.jsx и тянет весь портал. Без него
 * ошибка отрисовки давала бы в окне телефона пустой белый экран посреди звонка.
 */
// Первым импортом, как у тренажёров: восстановление после устаревшей сборки должно
// встать на ошибки загрузки раньше, чем начнут грузиться остальные куски.
import { recoverFromStaleBundle } from '../staleBundleRecovery';
import React from 'react';
import ReactDOM from 'react-dom/client';
import '../styles.css';
import ClientCardEmbed from './ClientCardEmbed';

class ClientCardErrorBoundary extends React.Component {
    state = { error: null };

    static getDerivedStateFromError(error) {
        return { error };
    }

    componentDidCatch(error, info) {
        console.error('Client card page crashed:', error, info);
        recoverFromStaleBundle(error);
    }

    render() {
        const { error } = this.state;
        if (!error) return this.props.children;
        return (
            <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
                <div className="w-full max-w-sm rounded-2xl bg-white p-6 text-center shadow">
                    <div className="text-[15px] font-semibold text-slate-900">Карточка клиента не открылась</div>
                    <p className="mt-2 text-[13px] text-slate-500">
                        Обновите страницу. Если не помогает — сообщите в техподдержку.
                    </p>
                    {/* Текст ошибки — мелко, для фото экрана в поддержку. */}
                    <p className="mt-3 break-words text-[11px] text-slate-400">{String(error?.message || error)}</p>
                    <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="mt-4 rounded-xl bg-slate-100 px-4 py-2 text-[13px] font-semibold text-slate-600 hover:bg-slate-200"
                    >
                        Обновить страницу
                    </button>
                </div>
            </div>
        );
    }
}

try {
    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(
        <ClientCardErrorBoundary>
            <ClientCardEmbed />
        </ClientCardErrorBoundary>,
    );
} catch (err) {
    console.error('Client card page failed to start:', err);
    document.getElementById('root').innerHTML = `
      <div class="min-h-screen flex items-center justify-center bg-slate-50 p-6">
        <div class="bg-white p-6 rounded-2xl shadow w-full max-w-sm text-center">
          <div class="text-[15px] font-semibold text-slate-900">Карточка клиента не открылась</div>
          <p class="mt-2 text-[13px] text-slate-500">Обновите страницу. Если не помогает — сообщите в техподдержку.</p>
        </div>
      </div>
    `;
}
