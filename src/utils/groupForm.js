/*
 * Форма «Новая группа» — правила без React.
 *
 * Модель расчёта у группы необязательна, как и направление: пустое значение —
 * группа без своей модели (сервер хранит NULL), и сотрудники считаются по
 * модели своего направления. Форма показывает ровно то, что сохранится: выбор
 * направления подставляет его модель в поле на виду у человека, и её можно
 * снять обратно на «без модели».
 */

export const EMPTY_GROUP_FORM = { name: '', department_id: '', direction_id: '', calculation_model_code: '' };

export const NO_GROUP_MODEL_LABEL = '— без модели —';

const directionModelOf = (direction) => String(
    direction?.calculationModelCode || direction?.calculation_model_code || 'operator'
);

/** Строки списка моделей: первой идёт «без модели». */
export const groupModelOptions = (calcModels) => [
    { value: '', label: NO_GROUP_MODEL_LABEL },
    ...(Array.isArray(calcModels) ? calcModels : []).map((model) => ({ value: model.code, label: model.name })),
];

/**
 * Форма после выбора направления: его модель встаёт в поле «Модель расчёта»,
 * отдел — если ещё не выбран. Возврат к «без направления» ничего не подставляет
 * и ничего не стирает: выбранная модель остаётся на виду.
 */
export function groupFormAfterDirectionPick(form, directionId, directions) {
    const direction = (Array.isArray(directions) ? directions : [])
        .find((item) => String(item.id) === String(directionId));
    return {
        ...form,
        direction_id: directionId,
        calculation_model_code: direction ? directionModelOf(direction) : form.calculation_model_code,
        department_id: form.department_id
            || (direction ? String(direction.department_id ?? direction.departmentId ?? '') : form.department_id),
    };
}

/** Тело запроса создания: пустая модель уходит как null — «группа без модели». */
export function createGroupBody(form, { force = false } = {}) {
    const body = {
        name: String(form.name || '').trim(),
        calculation_model_code: form.calculation_model_code || null,
        direction_id: form.direction_id ? Number(form.direction_id) : null,
        department_id: form.department_id ? Number(form.department_id) : null,
    };
    if (force) body.force = true;
    return body;
}
