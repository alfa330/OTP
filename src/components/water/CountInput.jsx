import React, { useState } from 'react';
import { COUNT_MAX_DIGITS, isCountDraft } from './waterMeta';
import { T } from './waterUi';

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
    /* Число не из клавиатуры (шаг степпера): оно заведомо годное, и прежнее
       «Только цифры» гаснет вместе с ним. */
    const setValue = (value) => {
        setRejected(false);
        onChange(String(value));
    };
    // Ушёл из поля — «Только цифры» гаснет: значение в поле прежнее и годное.
    const clearRejected = () => setRejected(false);
    return {
        rejected,
        setValue,
        clearRejected,
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
    <span className={`block text-[11.5px] text-rose-600 ${T.note}`}>{COUNT_HINT}</span>
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
