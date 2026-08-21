import React, { useMemo } from 'react';
import { ChevronDown } from 'lucide-react';
import { iosInput } from '../ui/ios';
import { OPERATING_CITIES } from '../../utils/kazakhstanCities';

/* Поле формы раздела: подпись, контрол, необязательная подсказка.
 *
 * Общий примитив на редакторы вики: подписи полей задавались в каждой форме
 * своим набором классов, и они разъезжались по размеру и цвету. Здесь одна
 * формулировка на всех — как iosInput и iosCard в ui/ios.jsx.
 */
export const Field = ({ label, hint, children, className = '' }) => (
    <div className={className}>
        {label && (
            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">{label}</label>
        )}
        {children}
        {hint && <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">{hint}</p>}
    </div>
);

/* Выбор города для справочника: офис и таксопарк заводят в одних и тех же
 * городах, и перечень у них обязан быть один — разные списки в двух формах
 * дают парк в «Астане» и его офис в «Нур-Султане».
 *
 * Список закрытый (см. OPERATING_CITIES), но чужое значение не теряется:
 * город, которого в перечне нет, подмешивается отдельной строкой. Колонка в
 * базе текстовая, и молча стереть город правкой телефона нельзя.
 */
export const CitySelect = ({ value, onChange, placeholder = 'Город не выбран' }) => {
    const current = String(value ?? '').trim();
    const cities = useMemo(() => (
        current && !OPERATING_CITIES.includes(current)
            ? [current, ...OPERATING_CITIES]
            : OPERATING_CITIES
    ), [current]);

    return (
        <div className="relative min-w-0">
            <select
                className={`${iosInput} appearance-none pr-9`}
                value={current}
                onChange={(e) => onChange(e.target.value)}
            >
                <option value="">{placeholder}</option>
                {cities.map((city) => (
                    <option key={city} value={city}>{city}</option>
                ))}
            </select>
            <ChevronDown
                size={15}
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
        </div>
    );
};

export default Field;
