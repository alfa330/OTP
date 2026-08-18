import React from 'react';

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

export default Field;
