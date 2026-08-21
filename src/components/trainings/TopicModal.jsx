import React, { useEffect, useMemo, useState } from 'react';
import { IosModal, IosHint, iosInput, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { Field } from '../wiki/formField';
import { TOPIC_KIND_LABELS, errText } from './constants';
import useEscapeClose from './useEscapeClose';

/* Корпоративная тема: заведение и правка.
 *
 * Полей намеренно мало. Тип пока один («Информационный»), поэтому он не
 * спрашивается вовсе — выбор из одного варианта это не выбор, а лишний шаг;
 * подпись типа человек всё равно увидит на карточке. Отдел спрашивается только
 * у того, кто вправе завести тему не своему отделу: у СВ и главы отдела ответ
 * предопределён, и показывать им заблокированный селектор незачем.
 */

export default function TopicModal({
    open,
    onClose,
    onSave,
    initial = null,
    departments = [],
    canChooseDepartment = false,
    scopeDepartmentName = '',
}) {
    const isEdit = Boolean(initial?.id);
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [departmentId, setDepartmentId] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEscapeClose(open, onClose);

    useEffect(() => {
        if (!open) return;
        setTitle(initial?.title || '');
        setDescription(initial?.description || '');
        setDepartmentId(initial?.department_id == null ? '' : String(initial.department_id));
        setError('');
    }, [open, initial]);

    const departmentOptions = useMemo(() => ([
        // Пустое значение — легальное: «общая для всей компании». Доступно
        // только тому, кто работает без границы отдела.
        { value: '', label: 'Все отделы (общая тема)' },
        ...(departments || []).map((department) => ({
            value: String(department.id),
            label: department.name,
        })),
    ]), [departments]);

    const audienceHint = useMemo(() => {
        if (canChooseDepartment && !departmentId) {
            return 'Общая тема: охват считается по всем активным сотрудникам портала.';
        }
        const name = canChooseDepartment
            ? (departments.find((item) => String(item.id) === departmentId)?.name || '')
            : scopeDepartmentName;
        return name
            ? `Охват будет считаться по активным сотрудникам отдела «${name}».`
            : 'Охват считается по активным сотрудникам отдела.';
    }, [canChooseDepartment, departmentId, departments, scopeDepartmentName]);

    const submit = async () => {
        const cleanTitle = title.trim();
        if (!cleanTitle) { setError('Укажите название темы.'); return; }
        if (cleanTitle.length > 255) { setError('Название длиннее 255 символов.'); return; }
        setError('');
        setSaving(true);
        try {
            const payload = {
                title: cleanTitle,
                description: description.trim() || null,
            };
            // Тип отправляем явно: сервер его валидирует, и молчаливый дефолт
            // на клиенте однажды разошёлся бы с серверным.
            if (!isEdit) payload.kind = 'info';
            if (!isEdit && canChooseDepartment) {
                payload.department_id = departmentId === '' ? null : Number(departmentId);
            }
            await onSave(payload);
            setSaving(false);
            onClose();
        } catch (saveError) {
            setError(errText(saveError, 'Не удалось сохранить тему.'));
            setSaving(false);
        }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={isEdit ? 'Корпоративная тема' : 'Новая корпоративная тема'}
            subtitle={`${TOPIC_KIND_LABELS.info} · охват считается по активным сотрудникам`}
            footer={(
                <>
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" onClick={submit} disabled={saving} className={iosBtnPrimary}>
                        {saving ? 'Сохраняем…' : 'Сохранить'}
                    </button>
                </>
            )}
        >
            <div className="space-y-3.5">
                <Field
                    label="Название"
                    hint="Его увидит сотрудник в своих часах — пишите так, как назвали бы тренинг вслух."
                >
                    <input
                        value={title}
                        onChange={(event) => setTitle(event.target.value)}
                        placeholder="Например: новые правила отмены заказа"
                        maxLength={255}
                        className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                    />
                </Field>

                <Field label="О чём тема" hint="Для тех, кто будет проводить. Сотруднику не показывается.">
                    <textarea
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                        rows={3}
                        placeholder="Что нужно донести и на что обратить внимание"
                        className={`${iosInput} resize-none bg-white ring-1 ring-slate-200/70`}
                    />
                </Field>

                {canChooseDepartment && !isEdit && (
                    <Field label="Отдел" hint={audienceHint}>
                        <CustomSelect
                            variant="ios"
                            searchable
                            value={departmentId}
                            onChange={setDepartmentId}
                            options={departmentOptions}
                            placeholder="Выберите отдел"
                            searchPlaceholder="Название отдела"
                            ariaLabel="Отдел темы"
                        />
                    </Field>
                )}

                {(!canChooseDepartment || isEdit) && (
                    <div className="flex items-start gap-2 px-1 text-[11.5px] leading-relaxed text-slate-500">
                        <span>{audienceHint}</span>
                        <IosHint
                            align="right"
                            text="В охват идут работающие сотрудники отдела: операторы, супервайзеры и тренеры. Люди в статусе «БС» и уволенные не считаются — провести им тренинг нельзя. Администраторы портала в знаменатель не входят."
                        />
                    </div>
                )}
            </div>
        </IosModal>
    );
}
