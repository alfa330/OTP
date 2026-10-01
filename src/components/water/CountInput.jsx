import React, { useState } from 'react';
import { COUNT_MAX_DIGITS, isCountDraft } from './waterMeta';

/*
 * Поле количества — блоки, пороги, поездки, дни. Принимает только цифры
 * (просьба владельца 01.10.2026: «там нужно вводить только числа, а не
 * буквы»).
 *
 * Буква, пробел, минус или запятая в поле не попадают вовсе — ни с клавиатуры,
 * ни вставкой, — а под полем загорается «Только цифры»: иначе нажатие
 * выглядело бы так, будто клавиатура не работает. Ввод ОТБРАСЫВАЕТСЯ, а не
 * вычищается: из «12,5» молча получилось бы 125, из «-3» — 3.
 */

export const COUNT_HINT = 'Только цифры';

export const useCountInput = (onChange) => {
    const [rejected, setRejected] = useState(false);
    const handleChange = (event) => {
        const next = event.target.value;
        if (!isCountDraft(next)) {
            setRejected(true);
            return;
        }
        setRejected(false);
        onChange(next);
    };
    return {
        rejected,
        inputProps: {
            inputMode: 'numeric',
            pattern: '[0-9]*',
            autoComplete: 'off',
            maxLength: COUNT_MAX_DIGITS,
            'aria-invalid': rejected || undefined,
            onChange: handleChange,
        },
    };
};

export const CountHint = () => (
    <span className="block text-[11.5px] text-rose-600">{COUNT_HINT}</span>
);

const CountInput = ({ value, onChange, className, ...rest }) => {
    const { rejected, inputProps } = useCountInput(onChange);
    return (
        <>
            <input {...rest} {...inputProps} className={className} value={value} />
            {rejected && <CountHint />}
        </>
    );
};

export default CountInput;
