import { useEffect } from 'react';

/* Закрытие модалки по Escape.
 *
 * Зачем локальный хук, а не правка IosModal. Общий примитив Escape не
 * обрабатывает вовсе — ни в одном из разделов, которые его используют. Добавить
 * обработчик прямо в него было бы улучшением для всего портала, но и риском:
 * там, где модалки стоят одна над другой, Escape закрыл бы разом обе, и это
 * пришлось бы проверять во всех двадцати разделах-потребителях. Здесь же
 * поведение нужно ровно этому разделу — прошлая версия «Тренингов» по Escape
 * закрывалась, и потерять это при переоформлении было бы регрессом.
 *
 * FullscreenSheet свой Escape уже умеет, поэтому раскатку хук не трогает.
 */
export default function useEscapeClose(open, onClose) {
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            if (event.key === 'Escape') {
                event.stopPropagation();
                onClose?.();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose]);
}
