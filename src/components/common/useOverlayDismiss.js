import { useRef } from 'react';

/* Закрытие окна кликом по подложке — так, чтобы выделение текста его не гасило.
 *
 * Подложка, которая ОБОРАЧИВАЕТ панель и висит на голом onClick, закрывает
 * окно не только от клика по себе. Нажми ЛКМ в поле ввода, протяни выделение
 * за край окна и отпусти там — браузер шлёт click не тому, над чем отпустили,
 * а ближайшему общему предку нажатия и отпускания. Общий предок здесь и есть
 * подложка: окно схлопывается вместе с набранным текстом.
 *
 * Два приёма, которые выглядят защитой, но ею не являются:
 *   • onClick={e => e.stopPropagation()} на панели — всплывать неоткуда,
 *     click прилетает прямо на подложку;
 *   • onClick={e => { if (e.target === e.currentTarget) onClose(); }} — при
 *     отпускании за краем окна target подложки и есть, условие истинно.
 *
 * Поэтому смотрим на нажатие и отпускание по отдельности и закрываем, только
 * если оба пришлись ровно на подложку. Строже, чем у IosModal
 * (src/components/ui/ios.jsx), где окно уходит уже по mousedown: там нажатие
 * по подложке с возвратом мыши в окно всё равно закрывает.
 *
 * Бага НЕТ, если подложка — элемент-СОСЕД панели, а не обёртка (SimpleModal в
 * src/App.jsx, карточка задачи .tv-overlay + .tv-drawer): общий предок тогда
 * выше подложки. Такую разметку нельзя «причёсывать» в обёртку.
 *
 * Отдаёт готовые props — разворачивать спредом в подложку:
 *     const dismiss = useOverlayDismiss(onClose);
 *     <div className="…" {...dismiss}>…</div>
 */
export default function useOverlayDismiss(onClose) {
    const pressRef = useRef({ down: false, up: false });
    return {
        onPointerDown: (event) => {
            pressRef.current = { down: event.target === event.currentTarget, up: false };
        },
        onPointerUp: (event) => {
            pressRef.current.up = event.target === event.currentTarget;
        },
        onClick: (event) => {
            const { down, up } = pressRef.current;
            pressRef.current = { down: false, up: false };
            // Третья проверка — на случай, если внутри окна кто-то гасит
            // всплытие pointerdown: тогда подложка нажатия не увидит вовсе.
            if (down && up && event.target === event.currentTarget) onClose();
        },
    };
}
