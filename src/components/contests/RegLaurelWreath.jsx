import React from 'react';

// Тона лаврового венка по призовым местам: золото, серебро, бронза.
const REG_WREATH_TONES = {
    1: { leaf: '#E8B10E', leafAlt: '#C98F06', berry: '#F6CE53' },
    2: { leaf: '#A8B2BD', leafAlt: '#8B96A3', berry: '#CBD3DB' },
    3: { leaf: '#C4854E', leafAlt: '#A76B39', berry: '#DFAC7C' },
};

// Лавровый венок призёра: две зеркальные ветви обнимают аватарку снизу
// и по бокам, сверху разрыв — лицо не перекрывается. Рисуется слоем ПОД
// аватаркой, наружу выглядывают только кончики листьев.
// Общий для подиума рейтинга (App.jsx) и церемонии итогов.
const RegLaurelWreath = ({ place, className = '' }) => {
    const tone = REG_WREATH_TONES[place];
    if (!tone) return null;
    const leaves = [78, 100, 122, 143, 162, 176];
    return (
        <svg viewBox="0 0 100 100" className={className} aria-hidden="true">
            {[false, true].map((mirrored) => (
                <g key={mirrored ? 'left' : 'right'} transform={mirrored ? 'scale(-1 1) translate(-100 0)' : undefined}>
                    {leaves.map((angle, i) => (
                        <path key={angle}
                              d="M50 3 C57 9 57 21 50 27 C43 21 43 9 50 3"
                              fill={i % 2 ? tone.leafAlt : tone.leaf}
                              transform={`rotate(${angle} 50 50) rotate(-22 50 15)`} />
                    ))}
                    <circle cx="50" cy="7" r="3" fill={tone.berry} transform="rotate(168 50 50)" />
                </g>
            ))}
        </svg>
    );
};

export default RegLaurelWreath;
