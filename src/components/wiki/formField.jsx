import React, { useMemo } from 'react';
import CustomSelect from '../ui/CustomSelect';
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

/* Вид кнопки списка. В редакторах офиса и парка «Город» стоит в одной строке
   с «Названием», а то заполнено iosInput — контрол на несколько пикселей ниже
   соседнего сразу заметен, поэтому геометрию поля повторяем один в один.
   Своего класса на триггер CustomSelect не принимает, и классы вешаются на
   дочернюю кнопку: селектор `.класс > button` специфичнее утилит самой кнопки
   и перебивает их без !important. Отдельно переопределён и наведённый фон —
   `hover:` кнопки иначе выигрывает по специфичности и осветляет поле.
   Панель списка остаётся своя, из примитива. */
const cityTrigger = '[&>button]:bg-slate-100 [&>button]:px-3.5 [&>button]:py-2.5 '
    + '[&>button]:text-[14px] [&>button]:font-normal [&>button]:text-slate-900 '
    + '[&>button:hover]:bg-slate-200/70';

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
    /* Пустая строка — не заглушка, а полноценный вариант: «города нет» у
       записи справочника такой же осмысленный ответ, как название города,
       и выбрать его обратно должно быть можно. Поэтому она стоит первой
       строкой списка, а не только подписью на кнопке. */
    const options = useMemo(() => {
        const cities = current && !OPERATING_CITIES.includes(current)
            ? [current, ...OPERATING_CITIES]
            : OPERATING_CITIES;
        return [
            { value: '', label: placeholder },
            ...cities.map((city) => ({ value: city, label: city })),
        ];
    }, [current, placeholder]);

    return (
        <CustomSelect
            variant="ios"
            className={`min-w-0 ${cityTrigger}`}
            value={current}
            onChange={onChange}
            options={options}
            placeholder={placeholder}
            /* Городов два десятка: у системного списка была подсказка по первой
               букве, здесь её заменяет строка поиска — иначе нужный город
               приходится выискивать прокруткой. */
            searchable
            searchPlaceholder="Поиск по городу…"
            ariaLabel="Город"
        />
    );
};

export default Field;
