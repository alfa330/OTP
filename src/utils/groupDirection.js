/*
 * Направление оператора следует за группой.
 *
 * При переводе в группу сервер сам проставляет оператору её действующее
 * направление (effective_direction_id в /api/groups). Здесь две вещи, которые
 * решает карточка сотрудника: что показать в поле «Направление» после выбора
 * группы и нужно ли потом отдельно отправить направление.
 */

const isBlank = (value) => value === null || value === undefined || value === '';

const departmentOf = (item) => item?.department_id ?? item?.departmentId ?? null;

/**
 * Направление в карточке после выбора группы.
 *
 * Выбранное руками не трогаем. Иначе — действующее направление группы, а при
 * возврате к исходной группе и у группы без направления — исходное направление
 * карточки (в форме создания это пустое поле). Подстановка не копится: выбрать
 * группу, передумать и вернуть прежнюю — значит вернуть и прежнее направление.
 *
 * Исходное направление возвращается только в свой отдел: после смены отдела
 * сотрудника группа нового отдела без направления оставляет поле пустым, и
 * проверка «Направление обязательно» заставляет выбрать его руками — иначе
 * оператор уехал бы в новый отдел с направлением старого.
 */
export function directionForPickedGroup({
    groupId,
    groups,
    directions,
    originalGroupId,
    originalDirectionId,
    currentDirectionId,
    pickedByHand,
}) {
    if (pickedByHand) return currentDirectionId;
    const original = isBlank(originalDirectionId) ? '' : originalDirectionId;
    if (!isBlank(originalGroupId) && String(groupId) === String(originalGroupId)) return original;
    const group = (groups || []).find((item) => String(item?.id) === String(groupId));
    if (!isBlank(group?.effective_direction_id)) return group.effective_direction_id;
    const originalDirection = (directions || []).find((item) => String(item?.id) === String(original));
    const originalDepartment = departmentOf(originalDirection);
    const groupDepartment = departmentOf(group);
    if (!isBlank(originalDepartment) && !isBlank(groupDepartment)
        && Number(originalDepartment) !== Number(groupDepartment)) {
        return '';
    }
    return original;
}

/**
 * Отправлять ли направление из карточки после перевода в группу.
 *
 * directionAfterMove — направление, которое у оператора уже записано: ответ
 * сервера на перевод, а без перевода — исходное. Обычному СВ направление не
 * отправляем никогда: поля у него нет, а сервер ответит 403 (задача #228).
 */
export function shouldSendDirectionUpdate({ nextDirectionId, directionAfterMove, canEditDirection }) {
    if (!canEditDirection || isBlank(nextDirectionId)) return false;
    return String(nextDirectionId) !== String(isBlank(directionAfterMove) ? '' : directionAfterMove);
}
