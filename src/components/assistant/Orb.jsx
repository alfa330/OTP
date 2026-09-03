import React from 'react';
import './assistant-orb.css';

/* Сам пузырь — только картинка, без единого обработчика.
 *
 * Отдельным файлом, потому что шарик встречается в трёх размерах и в трёх
 * разных ролях: кнопка в углу экрана, аватар в шапке панели и крупный знак на
 * пустом экране чата. Роль у них разная, а рисунок обязан быть один — иначе
 * человек перестаёт узнавать в аватаре тот же самый шарик, который он двигал.
 *
 * Слоёв пять, и каждый нужен: ореол, тело, кромка-плёнка, внутренняя дуга и
 * блик. Почему именно так, а не картинкой или canvas — в шапке
 * assistant-orb.css, там же вся палитра.
 */

const VARIANT_CLASS = {
    orb: '',
    mini: 'aorb--mini',
    hero: 'aorb--hero',
};

const Orb = ({ variant = 'orb', animated = true, className = '' }) => (
    <span
        className={`aorb ${VARIANT_CLASS[variant] || ''} ${className}`}
        // Пузырь — оформление: имя и роль несёт кнопка, внутри которой он лежит.
        aria-hidden="true"
    >
        <span className={animated ? 'aorb-bob' : undefined}>
            <span className="aorb-shell">
                <i className="aorb__layer aorb__halo" />
                <i className="aorb__layer aorb__body" />
                <i className="aorb__layer aorb__rim" />
                <i className="aorb__layer aorb__arc" />
                <i className="aorb__layer aorb__spec" />
            </span>
        </span>
    </span>
);

export default Orb;
