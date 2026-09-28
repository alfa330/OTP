import { lazy } from 'react';

/* React.lazy с одной перезагрузкой страницы при устаревшем чанке.
 *
 * После выкладки имена файлов бандла меняются, и вкладка, открытая с утра,
 * просит чанк, которого на сервере уже нет. Без перезагрузки ошибка доходит до
 * корневого ErrorBoundary и заменяет весь портал экраном «Ошибка приложения».
 * Перезагружаем один раз за сессию вкладки (ключ в sessionStorage), чтобы
 * настоящая поломка не превратилась в бесконечный цикл перезагрузок.
 *
 * Вынесено из App.jsx 28.09.2026: ленивые части есть и внутри разделов
 * («Библиотека» грузит ридер только при открытии книги), и там
 * нужен тот же повтор, а обратный импорт из App.jsx был бы циклом.
 */
const CHUNK_RELOAD_STORAGE_KEY = 'otp_chunk_reload_attempted';

const lazyWithRetry = (importer) =>
    lazy(async () => {
        try {
            const module = await importer();
            if (typeof window !== 'undefined') {
                window.sessionStorage.removeItem(CHUNK_RELOAD_STORAGE_KEY);
            }
            return module;
        } catch (error) {
            if (typeof window !== 'undefined') {
                const message = String(error?.message || '');
                const isChunkLoadError =
                    message.includes('Failed to fetch dynamically imported module') ||
                    message.includes('Importing a module script failed') ||
                    message.includes('ChunkLoadError');

                if (isChunkLoadError) {
                    const hasReloaded = window.sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY);
                    if (!hasReloaded) {
                        window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, '1');
                        const url = new URL(window.location.href);
                        url.searchParams.set('v', Date.now().toString());
                        window.location.replace(url.toString());
                        return new Promise(() => {});
                    }
                }
            }
            throw error;
        }
    });

export default lazyWithRetry;
