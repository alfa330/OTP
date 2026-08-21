import React, { useEffect, useMemo, useState } from 'react';
import { Clock, Users, AlertTriangle } from 'lucide-react';
import {
    IosModal, IosBadge, IosToggle, IosHint, iosInput, iosBtnPrimary, iosBtnSecondary, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { Field } from '../wiki/formField';
import {
    durationMinutes, formatDuration, timeToMinutes, pluralPeople, plural, errText,
} from './constants';
import useEscapeClose from './useEscapeClose';

/* Заведение и правка одного занятия.
 *
 * Что здесь принципиально иначе, чем было:
 *
 * 1. Тема выбирается из ОДНОГО справочника — базовые и корпоративные в одном
 *    списке, разделённые заголовками. Раньше модалка держала свою копию
 *    списка из 9 значений, сервер разрешал 11, и 243 записи «Тех. сбой» /
 *    «Мониторинг» при открытии на редактирование теряли причину, а сохранение
 *    молча подменяло её другой.
 * 2. Архивная базовая тема («Тех. сбой») показывается, если запись УЖЕ под ней
 *    заведена, но для новой не предлагается — с явной подписью, куда сбои
 *    заводят теперь.
 * 3. У корпоративной темы галочки «учитывать в часах» нет вовсе: такая тема —
 *    факт прохождения, а не оплачиваемая работа, и решает это тема, а не тот,
 *    кто заполняет форму.
 */

export default function SessionModal({
    open,
    onClose,
    onSave,
    initial = null,
    people = [],
    defaultPeopleIds = [],
    topics = [],
    defaultReasons = [],
    archivedReasons = [],
    lockedTopicId = null,
    existingByOperator = {},
}) {
    const isEdit = Boolean(initial?.id);

    const [date, setDate] = useState('');
    const [startTime, setStartTime] = useState('');
    const [endTime, setEndTime] = useState('');
    const [choice, setChoice] = useState('');          // 'reason:<текст>' | 'topic:<id>'
    const [comment, setComment] = useState('');
    const [countInHours, setCountInHours] = useState(true);
    const [peopleIds, setPeopleIds] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEscapeClose(open, onClose);

    const archived = useMemo(() => new Set(archivedReasons), [archivedReasons]);

    useEffect(() => {
        if (!open) return;
        setDate(initial?.date || new Date().toISOString().slice(0, 10));
        setStartTime(initial?.start_time || '');
        setEndTime(initial?.end_time || '');
        setComment(initial?.comment || '');
        setCountInHours(initial?.count_in_hours ?? true);
        setError('');
        setPeopleIds(isEdit ? [] : (defaultPeopleIds || []).map(Number).filter(Number.isFinite));
        if (lockedTopicId) setChoice(`topic:${lockedTopicId}`);
        else if (initial?.topic_id) setChoice(`topic:${initial.topic_id}`);
        else if (initial?.reason) setChoice(`reason:${initial.reason}`);
        else setChoice('');
    // initial приходит новым объектом на каждое открытие — этого достаточно.
    }, [open, initial, isEdit, lockedTopicId, defaultPeopleIds]);

    const selectedTopic = useMemo(() => {
        if (!choice.startsWith('topic:')) return null;
        const id = Number(choice.slice(6));
        return topics.find((item) => Number(item.id) === id) || null;
    }, [choice, topics]);

    const isCorporate = Boolean(selectedTopic) || choice.startsWith('topic:');

    /* Единый список тем. Архивная базовая попадает в список ТОЛЬКО если правим
     * запись, которая под ней и заведена: иначе её было бы видно всем и всегда,
     * и привычка заводить сбои тренингом никуда бы не ушла. */
    const options = useMemo(() => {
        const current = initial?.reason;
        const base = (defaultReasons || [])
            .filter((reason) => !archived.has(reason) || (isEdit && reason === current))
            .map((reason, index) => ({
                value: `reason:${reason}`,
                label: archived.has(reason) ? `${reason} · архивная` : reason,
                ...(index === 0 ? { groupLabel: 'Базовые' } : {}),
            }));

        const corporate = (topics || [])
            .filter((topic) => !topic.is_archived || Number(topic.id) === Number(initial?.topic_id))
            .map((topic, index) => ({
                value: `topic:${topic.id}`,
                label: topic.is_archived ? `${topic.title} · архивная` : topic.title,
                ...(index === 0 ? { groupLabel: 'Корпоративные' } : {}),
            }));

        return [...base, ...corporate];
    }, [defaultReasons, topics, archived, isEdit, initial?.reason, initial?.topic_id]);

    const minutes = durationMinutes({ start_time: startTime, end_time: endTime });

    /* Пересечение по времени — считаем на клиенте до отправки. Сервер проверит
     * ещё раз (он источник правды), но узнать об этом до нажатия «Сохранить»
     * дешевле, чем поймать 409 на половине пачки. */
    const overlapping = useMemo(() => {
        if (!date || !startTime || !endTime) return [];
        const from = timeToMinutes(startTime);
        const to = timeToMinutes(endTime);
        if (from == null || to == null || to <= from) return [];
        const targets = isEdit ? [Number(initial?.operator_id)] : peopleIds;
        const clash = [];
        targets.filter(Number.isFinite).forEach((operatorId) => {
            const own = existingByOperator[operatorId] || [];
            const hit = own.some((item) => {
                if (String(item?.date) !== String(date)) return false;
                if (initial?.id && Number(item?.id) === Number(initial.id)) return false;
                const otherFrom = timeToMinutes(item?.start_time);
                const otherTo = timeToMinutes(item?.end_time);
                if (otherFrom == null || otherTo == null) return false;
                return Math.max(from, otherFrom) < Math.min(to, otherTo <= otherFrom ? otherTo + 1440 : otherTo);
            });
            if (hit) {
                const person = people.find((item) => Number(item.id) === operatorId);
                clash.push(person?.name || `#${operatorId}`);
            }
        });
        return clash;
    }, [date, startTime, endTime, peopleIds, isEdit, initial, existingByOperator, people]);

    const peopleOptions = useMemo(() => (people || []).map((person) => ({
        value: String(person.id),
        label: person.name,
    })), [people]);

    const validate = () => {
        if (!isEdit && peopleIds.length === 0) return 'Выберите хотя бы одного сотрудника.';
        if (!date) return 'Укажите дату занятия.';
        if (!startTime) return 'Укажите время начала.';
        if (!endTime) return 'Укажите время окончания.';
        if (!choice) return 'Выберите тему занятия.';
        if (minutes <= 0) return 'Время окончания должно быть позже начала.';
        if (overlapping.length > 0) {
            return `У ${overlapping.slice(0, 3).join(', ')} уже есть занятие, пересекающееся по времени.`;
        }
        return '';
    };

    const submit = async () => {
        const problem = validate();
        if (problem) { setError(problem); return; }
        setError('');
        setSaving(true);
        try {
            const payload = {
                date,
                start_time: startTime,
                end_time: endTime,
                comment: comment.trim() || null,
            };
            if (isCorporate) {
                // Причину и зачёт в часы сервер возьмёт у темы — не отправляем
                // их вовсе, чтобы у клиента не было способа их переопределить.
                payload.topic_id = Number(choice.slice(6));
            } else {
                payload.reason = choice.slice(7);
                payload.count_in_hours = countInHours;
            }
            if (!isEdit) payload.operator_ids = peopleIds.map(Number);
            await onSave(payload);
            setSaving(false);
            onClose();
        } catch (saveError) {
            const overlap = saveError?.response?.data?.overlap
                || (saveError?.response?.data?.errors || []).some((item) => item?.overlap);
            setError(overlap
                ? 'У выбранного сотрудника уже есть занятие, пересекающееся по времени.'
                : errText(saveError, 'Не удалось сохранить. Попробуйте ещё раз.'));
            setSaving(false);
        }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={isEdit ? 'Занятие' : 'Новое занятие'}
            subtitle={isEdit ? 'Правка проведённого тренинга' : 'Кому, когда и по какой теме'}
            footer={(
                <>
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" onClick={submit} disabled={saving} className={iosBtnPrimary}>
                        {saving ? 'Сохраняем…' : (isEdit ? 'Сохранить' : 'Провести')}
                    </button>
                </>
            )}
        >
            <div className="space-y-3.5">
                {!isEdit && (
                    <Field
                        label="Сотрудники"
                        hint={peopleIds.length > 1
                            ? `Одно занятие будет записано каждому из ${peopleIds.length}: время у всех общее.`
                            : undefined}
                    >
                        <CustomSelect
                            variant="ios"
                            multiple
                            searchable
                            value={peopleIds.map(String)}
                            onChange={(next) => setPeopleIds((next || []).map(Number))}
                            options={peopleOptions}
                            placeholder="Выберите сотрудников"
                            searchPlaceholder="Имя сотрудника"
                            renderValue={(selected) => (
                                selected.length === 0
                                    ? 'Выберите сотрудников'
                                    : `${selected.length} ${pluralPeople(selected.length)}`
                            )}
                            ariaLabel="Сотрудники занятия"
                        />
                    </Field>
                )}

                {isEdit && initial?.operator_name && (
                    <div className="flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2.5">
                        <Users size={14} className="shrink-0 text-slate-400" />
                        <span className="truncate text-[13px] font-medium text-slate-700">{initial.operator_name}</span>
                    </div>
                )}

                <Field
                    label="Тема"
                    hint={lockedTopicId
                        ? 'Тема задана раскаткой и здесь не меняется.'
                        : (isCorporate ? 'Корпоративная тема в оплачиваемые часы не идёт — записывается только факт прохождения.' : undefined)}
                >
                    <CustomSelect
                        variant="ios"
                        searchable
                        disabled={Boolean(lockedTopicId)}
                        value={choice}
                        onChange={setChoice}
                        options={options}
                        placeholder="Выберите тему"
                        searchPlaceholder="Название темы"
                        ariaLabel="Тема занятия"
                    />
                </Field>

                {/* Подсказка про «Тех. сбой» видна только когда она к делу —
                    то есть когда эта тема и выбрана. Постоянная плашка была бы
                    шумом на каждом открытии формы. */}
                {choice === 'reason:Тех. сбой' && (
                    <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 ring-1 ring-amber-100">
                        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />
                        <p className="text-[12px] leading-snug text-amber-800">
                            Тема архивная: технические сбои заводятся в разделе «Тех. сбои».
                            Здесь она осталась, чтобы можно было править прошлые записи.
                        </p>
                    </div>
                )}

                <div className="grid grid-cols-2 gap-3">
                    <Field label="Дата">
                        <input
                            type="date"
                            value={date}
                            max={new Date().toISOString().slice(0, 10)}
                            onChange={(event) => setDate(event.target.value)}
                            className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                        />
                    </Field>
                    <div className="flex items-end">
                        <div className="flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3 py-2.5">
                            <Clock size={14} className="shrink-0 text-slate-400" />
                            <span className="text-[13px] font-semibold tabular-nums text-slate-700">
                                {minutes > 0 ? formatDuration(minutes) : '—'}
                            </span>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                    <Field label="Начало">
                        <input
                            type="time"
                            value={startTime}
                            onChange={(event) => setStartTime(event.target.value)}
                            className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                        />
                    </Field>
                    <Field label="Окончание">
                        <input
                            type="time"
                            value={endTime}
                            onChange={(event) => setEndTime(event.target.value)}
                            className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                        />
                    </Field>
                </div>

                <Field label="Комментарий">
                    <textarea
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        rows={2}
                        placeholder="Что разобрали — по желанию"
                        className={`${iosInput} resize-none bg-white ring-1 ring-slate-200/70`}
                    />
                </Field>

                {/* Галочка часов есть только у базовой темы: у корпоративной
                    ответ один и задан темой. */}
                {!isCorporate && (
                    <div className="flex items-center justify-between rounded-xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
                        <span className="flex items-center gap-2 text-[13px] text-slate-700">
                            Учитывать в оплачиваемых часах
                            <IosHint text="Снимите, если занятие не должно попасть в часы сотрудника: часы считаются по этому флагу, а не по факту записи." />
                        </span>
                        <IosToggle checked={countInHours} onChange={setCountInHours} />
                    </div>
                )}

                {isCorporate && (
                    <div className="flex items-center gap-2 px-1">
                        <IosBadge tone="blue">Корпоративная</IosBadge>
                        <span className="text-[11.5px] text-slate-500">в часы не идёт</span>
                    </div>
                )}

                {overlapping.length > 0 && (
                    <div className="rounded-xl bg-rose-50 px-3 py-2.5 text-[12px] leading-snug text-rose-700 ring-1 ring-rose-100">
                        Пересечение по времени у {overlapping.length} {pluralPeople(overlapping.length)}:{' '}
                        {overlapping.slice(0, 4).join(', ')}
                        {overlapping.length > 4 && ` и ещё ${overlapping.length - 4}`}
                    </div>
                )}

                {error && (
                    <div className="rounded-xl bg-rose-50 px-3 py-2.5 text-[12px] leading-snug text-rose-700 ring-1 ring-rose-100">
                        {error}
                    </div>
                )}

                {!isEdit && peopleIds.length > 1 && (
                    <div className={`${iosGroupLabel} pt-1`}>
                        Будет записано {peopleIds.length}{' '}
                        {plural(peopleIds.length, 'занятие', 'занятия', 'занятий')}
                    </div>
                )}
            </div>
        </IosModal>
    );
}
