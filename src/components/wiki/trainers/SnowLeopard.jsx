import React from 'react';

/* Барс — помощник тренажёра.
 *
 * Зачем персонаж вообще. Реплика «сейчас нужно другое действие» без лица
 * читается как ошибка приложения; та же реплика от помощника читается как
 * подсказка. Разница не в вежливости текста, а в том, кому его приписывают.
 *
 * Почему рисунок в SVG, а не картинка. Барс меняет выражение на каждом шаге
 * (объясняет / подсказывает / поправляет / радуется финалу) — это четыре
 * состояния, и в растре они превратились бы в четыре файла по 40 КБ, которые
 * ещё и не покрасить под тему. Здесь это несколько путей и один класс.
 *
 * МИНИ намеренно: барс стоит РЯДОМ с телефоном и не должен с ним спорить за
 * внимание — учебный экран главный, помощник комментирует.
 */

const STATES = new Set(['idle', 'speak', 'hint', 'error', 'success']);

/* Глаза — единственное, что по-настоящему меняет выражение. Остальное
   (наклон головы, хвост) делает CSS: анимация не должна множить разметку. */
const Eyes = ({ state }) => {
    if (state === 'success') {
        // Довольные глаза-дуги: закрытые «улыбкой» вверх.
        return (
            <g className="wt-leo__eyes" fill="none" stroke="#1e293b" strokeWidth="3.4"
                strokeLinecap="round">
                <path d="M40 62c3-4 9-4 12 0" />
                <path d="M68 62c3-4 9-4 12 0" />
            </g>
        );
    }
    if (state === 'error') {
        // Брови сведены — «ой, не туда». Глаза при этом широкие, а не злые:
        // тренажёр поправляет, а не отчитывает.
        return (
            <g className="wt-leo__eyes">
                <circle cx="46" cy="63" r="6.4" fill="#1e293b" />
                <circle cx="74" cy="63" r="6.4" fill="#1e293b" />
                <circle cx="48.4" cy="60.6" r="2" fill="#fff" />
                <circle cx="76.4" cy="60.6" r="2" fill="#fff" />
                <g stroke="#475569" strokeWidth="3" strokeLinecap="round">
                    <path d="M39 52.5 51 49" />
                    <path d="M81 52.5 69 49" />
                </g>
            </g>
        );
    }
    return (
        <g className="wt-leo__eyes">
            <circle cx="46" cy="63" r="6" fill="#1e293b" />
            <circle cx="74" cy="63" r="6" fill="#1e293b" />
            <circle cx="48.2" cy="60.8" r="1.9" fill="#fff" />
            <circle cx="76.2" cy="60.8" r="1.9" fill="#fff" />
        </g>
    );
};

const Mouth = ({ state }) => {
    if (state === 'speak' || state === 'hint') {
        // Открытый рот — единственный признак «говорю сейчас», который читается
        // на 120 пикселях. Анимацию рта даёт CSS (wt-leo--speak).
        return (
            <g className="wt-leo__mouth">
                {/* Открытый рот держится ВНУТРИ светлой маски морды (она
                    заканчивается на y≈85): раньше он спускался до 87 и наезжал
                    на шарф — на кадре это читалось как «рот съехал с морды». */}
                <path d="M53.5 75.6c3 2.4 10 2.4 13 0" fill="none" stroke="#475569"
                    strokeWidth="2.3" strokeLinecap="round" />
                <ellipse cx="60" cy="79.2" rx="4.6" ry="3.6" fill="#f19b9b" />
            </g>
        );
    }
    return (
        <path className="wt-leo__mouth" d="M60 76v3.4M60 79.4c-2.6 3-6.6 3-8.6.4M60 79.4c2.6 3 6.6 3 8.6.4"
            fill="none" stroke="#475569" strokeWidth="2.4" strokeLinecap="round" />
    );
};

export default function SnowLeopard({ state = 'idle', className = '' }) {
    const mood = STATES.has(state) ? state : 'idle';
    return (
        <svg
            className={`wt-leo wt-leo--${mood} ${className}`}
            viewBox="0 0 120 132"
            role="img"
            aria-label="Барс — помощник тренажёра"
        >
            {/* Тень под барсом: без неё фигура висит в воздухе. */}
            <ellipse cx="60" cy="126" rx="30" ry="4.6" fill="rgba(15,23,42,.10)" />

            {/* Хвост — длинный и пушистый, как у снежного барса. Он же и
                показывает настроение: CSS покачивает его в такт реплике. */}
            <path className="wt-leo__tail"
                d="M86 112c14 2 22-8 20-18-2-9-11-11-15-5"
                fill="none" stroke="#e6ecf3" strokeWidth="11" strokeLinecap="round" />
            <path className="wt-leo__tail"
                d="M86 112c14 2 22-8 20-18-2-9-11-11-15-5"
                fill="none" stroke="#cbd5e1" strokeWidth="11" strokeLinecap="round"
                strokeDasharray="3 13" />

            <g className="wt-leo__body">
                <path d="M60 84c16 0 26 12 26 26 0 8-11 12-26 12s-26-4-26-12c0-14 10-26 26-26Z"
                    fill="#f2f6fa" />
                {/* Грудка светлее корпуса — снежный барс белый снизу. */}
                <ellipse cx="60" cy="112" rx="13" ry="10" fill="#fff" />
                <ellipse cx="45" cy="120" rx="7.5" ry="5.2" fill="#fff" />
                <ellipse cx="75" cy="120" rx="7.5" ry="5.2" fill="#fff" />
                {/* Розетки на корпусе. Их мало и они бледные: пятна должны
                    читаться как барс, а не как рябь. */}
                <g fill="#d7dee8">
                    <ellipse cx="41" cy="102" rx="3.4" ry="2.6" />
                    <ellipse cx="79" cy="102" rx="3.4" ry="2.6" />
                    <ellipse cx="36" cy="112" rx="3" ry="2.2" />
                    <ellipse cx="84" cy="112" rx="3" ry="2.2" />
                </g>
            </g>

            {/* Шарф цветом раздела — единственная «фирменная» деталь. Он же
                прячет стык головы и корпуса. */}
            <g className="wt-leo__scarf">
                <path d="M42 86c6 4 30 4 36 0v7c-6 4-30 4-36 0Z" fill="var(--wiki-accent, #4f46e5)" />
                <path d="M76 92c5 1 7 5 6 9l-7-2Z" fill="var(--wiki-accent, #4f46e5)" opacity=".85" />
            </g>

            <g className="wt-leo__head">
                {/* Уши: наружная часть — шерсть, внутренняя тёплая. */}
                <path d="M33 40c-2-11 0-17 3-18 4-1 9 5 12 11Z" fill="#e6ecf3" />
                <path d="M87 40c2-11 0-17-3-18-4-1-9 5-12 11Z" fill="#e6ecf3" />
                <path d="M37 38c-1-7 0-11 2-11 2 0 5 3 7 7Z" fill="#f0a9a9" />
                <path d="M83 38c1-7 0-11-2-11-2 0-5 3-7 7Z" fill="#f0a9a9" />

                {/* Голова: круг с пушистыми щеками — два выступа по бокам. */}
                <path d="M60 30c19 0 32 13 32 30 0 5-1 9-3 13 4 2 5 5 3 7-3 2-8 1-11-1-6 4-13 6-21 6
                         s-15-2-21-6c-3 2-8 3-11 1-2-2-1-5 3-7-2-4-3-8-3-13 0-17 13-30 32-30Z"
                    fill="#f7fafc" />
                {/* Розетки на лбу и щеках — узнаваемый рисунок барса. */}
                <g fill="#dbe3ec">
                    <ellipse cx="47" cy="43" rx="4.2" ry="3.1" transform="rotate(-14 47 43)" />
                    <ellipse cx="60" cy="38.5" rx="4.6" ry="3.2" />
                    <ellipse cx="73" cy="43" rx="4.2" ry="3.1" transform="rotate(14 73 43)" />
                    <ellipse cx="37" cy="57" rx="3.4" ry="2.6" />
                    <ellipse cx="83" cy="57" rx="3.4" ry="2.6" />
                </g>
                {/* Светлая маска вокруг носа и глаз. */}
                <ellipse cx="60" cy="72" rx="17" ry="13" fill="#fff" />

                <Eyes state={mood} />

                {/* Нос. */}
                <path d="M56.4 71.6c0-2 1.6-3.2 3.6-3.2s3.6 1.2 3.6 3.2c0 2.2-1.8 3.6-3.6 3.6
                         s-3.6-1.4-3.6-3.6Z" fill="#e58b8b" />
                <Mouth state={mood} />

                {/* Усы — по три с каждой стороны, тонкие. */}
                <g stroke="#c3cddb" strokeWidth="1.6" strokeLinecap="round">
                    <path d="M42 70h-11M43 75l-10 3M44 79l-9 5" />
                    <path d="M78 70h11M77 75l10 3M76 79l9 5" />
                </g>
            </g>

            {/* Поднятая лапка — только в подсказке: жест «смотри сюда» делает
                подсказку заметнее, чем ещё одна строка текста. */}
            {mood === 'hint' && (
                <g className="wt-leo__paw">
                    <path d="M92 96c6-3 11 1 11 6" fill="none" stroke="#f2f6fa" strokeWidth="9"
                        strokeLinecap="round" />
                    <circle cx="103" cy="100" r="6.4" fill="#fff" />
                    <circle cx="103" cy="100" r="2.4" fill="#f0a9a9" />
                </g>
            )}

            {/* Искры финала. Три штуки: больше — уже фейерверк. */}
            {mood === 'success' && (
                <g className="wt-leo__spark" fill="var(--wiki-accent, #4f46e5)">
                    <path d="M22 44l1.8 4.6L28 50l-4.2 1.4L22 56l-1.8-4.6L16 50l4.2-1.4Z" />
                    <path d="M98 34l1.5 3.8 3.5 1.2-3.5 1.2L98 44l-1.5-3.8L93 39l3.5-1.2Z" />
                    <path d="M104 66l1.2 3 2.8 1-2.8 1-1.2 3-1.2-3-2.8-1 2.8-1Z" />
                </g>
            )}
        </svg>
    );
}
