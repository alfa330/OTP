/*
 * Модель расчёта, по которой рисуется экран «Учёта часов»: вкладки, подписи
 * колонок, загрузка отчёта за день.
 *
 * У выбранной группы обычно есть своя модель — она и решает. Группу можно
 * завести и без модели: тогда каждый сотрудник считается по модели своего
 * направления, и в одной группе могут оказаться разные модели. Экран при этом
 * один на всех, поэтому модель сотрудников берётся, только когда она у всех
 * одна. При смешанном составе экран остаётся операторским: вид всей группы не
 * должен зависеть от того, чья фамилия первая по алфавиту.
 *
 * Без выбранной группы (режим «по СВ») всё как было: модель первого сотрудника.
 */

const modelCodeOf = (item) => String(item?.calculation_model_code || item?.calculationModelCode || '').trim();

export function hoursScreenModelCode(group, operators) {
    const list = Array.isArray(operators) ? operators : [];
    if (!group) return list.length ? modelCodeOf(list[0]) : '';
    const own = modelCodeOf(group);
    if (own) return own;
    const codes = new Set(list.map(modelCodeOf).filter(Boolean));
    return codes.size === 1 ? [...codes][0] : '';
}
