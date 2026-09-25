import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import CustomSelect from '../ui/CustomSelect';
import useIsMobileShell from '../common/useIsMobileShell';
import { ProfileEmpty, ProfileGroup, ProfileRow, ProfileSkeletonValue, ProfileValue, textSize } from './profileUi';
import {
    CARD_INPUT_MAX_DIGITS,
    COURSE_OPTIONS,
    buildChanges,
    canonicalCourse,
    courseLabel,
    draftFromData,
    formatCardInput,
    formatPhone,
} from './myData';

/*
 * «Мои данные» в «Профиле» оператора (задача #357).
 *
 * Шесть полей и ничего сверх: остальные данные сотрудника здесь не
 * показываются, а чьи данные — решает сессия на сервере, номера сотрудника
 * блок не знает и не шлёт. От карты приходят только последние 4 цифры —
 * полного номера в интерфейсе нет вовсе, поэтому поле карты при правке всегда
 * пустое и означает «новый номер».
 *
 * Вид — та же группа «Настроек», что и «Работа» над ней (profileUi.jsx):
 * колонка подписей и сразу за ней значения. «Изменить» превращает значения в
 * поля с того же места, текст не прыгает.
 *
 * Истории правок здесь нет намеренно — её видят СВ и выше в «Учете
 * сотрудников», оператору она не показывается (решение владельца 24.09.2026).
 */

const ROWS = [
    { field: 'phone', label: 'Телефон' },
    { field: 'telegram_nick', label: 'Telegram' },
    { field: 'card_number', label: 'Номер карты' },
    { field: 'study_place', label: 'Университет' },
    { field: 'study_specialty', label: 'Специальность' },
    { field: 'study_course', label: 'Курс' },
];

const displayValue = (data, field) => {
    switch (field) {
        case 'phone':
            return formatPhone(data?.phone);
        case 'card_number':
            if (!data?.has_card) return '';
            return data.card_last4 ? `•••• ${data.card_last4}` : '••••';
        case 'study_course':
            return courseLabel(data?.study_course);
        default:
            return String(data?.[field] ?? '').trim();
    }
};

// Поле правки — того же вида, что кнопка списка курсов (CustomSelect ios):
// белое, с тонкой обводкой и мягкой тенью, синее кольцо в фокусе. Кегль — как у
// строк группы, чтобы правка не мельчила текст. Одинаковые поля и список — одна
// форма, а не набор разнородных деталей.
const inputClass = (hasError, isMobileShell) => (
    `w-full min-w-0 rounded-xl bg-white px-3 py-2 ${textSize(isMobileShell)} text-slate-900 `
    + 'placeholder-slate-400 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 transition '
    + 'focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:opacity-70 '
    + (hasError ? 'ring-rose-300 focus:ring-rose-400' : 'ring-slate-200/70 focus:ring-blue-500/60')
);

export default function MyDataCard({ apiBaseUrl, userId, withAccessTokenHeader, showToast }) {
    const isMobileShell = useIsMobileShell();
    const [state, setState] = useState({ status: 'loading', data: null, courses: COURSE_OPTIONS });
    const [draft, setDraft] = useState(null);
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState(false);

    // Колбэки родителя пересоздаются на каждый рендер App; в зависимости
    // эффектов их не ставим, иначе блок перезапрашивал бы данные бесконечно.
    const depsRef = useRef({ withAccessTokenHeader, showToast });
    depsRef.current = { withAccessTokenHeader, showToast };
    const aliveRef = useRef(true);
    useEffect(() => () => { aliveRef.current = false; }, []);

    const requestHeaders = () => {
        const base = { 'X-User-Id': userId };
        const wrap = depsRef.current.withAccessTokenHeader;
        return typeof wrap === 'function' ? wrap(base) : base;
    };
    const toast = (message, tone) => {
        const fn = depsRef.current.showToast;
        if (typeof fn === 'function') fn(message, tone);
    };

    const load = async () => {
        setState((prev) => ({ ...prev, status: 'loading' }));
        try {
            const { data } = await axios.get(`${apiBaseUrl}/api/my_data`, { headers: requestHeaders() });
            if (!aliveRef.current) return;
            setState({
                status: 'ready',
                data: data?.data || {},
                courses: Array.isArray(data?.course_options) ? data.course_options : COURSE_OPTIONS,
            });
        } catch (error) {
            if (!aliveRef.current) return;
            // 403 — блок этому человеку не положен: молча не рисуем ничего.
            setState((prev) => ({ ...prev, status: error?.response?.status === 403 ? 'closed' : 'error' }));
        }
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, userId]);

    const courseOptions = useMemo(() => {
        const list = [
            { value: '', label: 'Не указан' },
            ...state.courses.map((value) => ({ value, label: courseLabel(value) })),
        ];
        // Старая запись вне списка («курс 4») остаётся выбранной, пока её не
        // сменят: иначе поле выглядело бы пустым, хотя курс в базе есть.
        // «3 курс» сюда не попадает — canonicalCourse узнаёт в нём пункт «3».
        const stored = canonicalCourse(state.data?.study_course, state.courses);
        if (stored && !state.courses.includes(stored)) list.splice(1, 0, { value: stored, label: stored });
        return list;
    }, [state.courses, state.data]);

    if (state.status === 'closed') return null;

    const editing = draft !== null;
    const data = state.data;

    const startEditing = () => {
        setErrors({});
        setDraft(draftFromData(data));
    };
    const cancelEditing = () => {
        setErrors({});
        setDraft(null);
    };
    const setField = (field, value) => {
        setDraft((prev) => ({ ...prev, [field]: value }));
        if (errors[field]) setErrors((prev) => ({ ...prev, [field]: undefined }));
    };

    const save = async (event) => {
        event?.preventDefault();
        if (saving || !editing) return;
        const { payload, errors: found } = buildChanges(data, draft);
        setErrors(found);
        if (Object.keys(found).length) return;
        if (!Object.keys(payload).length) {
            setDraft(null);
            return;
        }
        setSaving(true);
        try {
            const { data: body } = await axios.post(`${apiBaseUrl}/api/my_data`, payload, { headers: requestHeaders() });
            if (!aliveRef.current) return;
            setState((prev) => ({
                ...prev,
                data: body?.data || prev.data,
                courses: Array.isArray(body?.course_options) ? body.course_options : prev.courses,
            }));
            setDraft(null);
            setErrors({});
            toast('Данные сохранены', 'success');
        } catch (error) {
            const body = error?.response?.data || {};
            if (body.errors && typeof body.errors === 'object') setErrors(body.errors);
            toast(body.error || 'Не удалось сохранить данные', 'error');
        } finally {
            if (aliveRef.current) setSaving(false);
        }
    };

    const renderEditor = (field) => {
        const common = {
            'aria-label': ROWS.find((row) => row.field === field)?.label,
            'aria-invalid': errors[field] ? true : undefined,
            className: inputClass(Boolean(errors[field]), isMobileShell),
            disabled: saving,
        };
        switch (field) {
            case 'phone':
                return (
                    <input
                        {...common}
                        className={`${common.className} tabular-nums`}
                        type="tel"
                        inputMode="tel"
                        autoComplete="tel"
                        placeholder="+7 701 234 56 78"
                        value={draft.phone}
                        onChange={(e) => setField('phone', e.target.value)}
                    />
                );
            case 'telegram_nick':
                return (
                    <input
                        {...common}
                        type="text"
                        autoComplete="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        placeholder="@username"
                        value={draft.telegram_nick}
                        onChange={(e) => setField('telegram_nick', e.target.value)}
                    />
                );
            case 'card_number':
                return (
                    <input
                        {...common}
                        className={`${common.className} tabular-nums`}
                        type="text"
                        inputMode="numeric"
                        autoComplete="cc-number"
                        maxLength={CARD_INPUT_MAX_DIGITS + Math.floor((CARD_INPUT_MAX_DIGITS - 1) / 4)}
                        placeholder={data?.has_card && data?.card_last4 ? `•••• ${data.card_last4}` : '0000 0000 0000 0000'}
                        value={draft.card_number}
                        onChange={(e) => setField('card_number', formatCardInput(e.target.value))}
                    />
                );
            case 'study_place':
                return (
                    <input
                        {...common}
                        type="text"
                        maxLength={255}
                        placeholder="Название вуза"
                        value={draft.study_place}
                        onChange={(e) => setField('study_place', e.target.value)}
                    />
                );
            case 'study_specialty': {
                const noPlace = !draft.study_place.trim();
                return (
                    <input
                        {...common}
                        type="text"
                        maxLength={255}
                        disabled={saving || noPlace}
                        placeholder={noPlace ? 'Сначала укажите вуз' : 'Специальность'}
                        value={noPlace ? '' : draft.study_specialty}
                        onChange={(e) => setField('study_specialty', e.target.value)}
                    />
                );
            }
            case 'study_course':
                return (
                    <CustomSelect
                        variant="ios"
                        className="w-full"
                        ariaLabel="Курс"
                        textClassName={`${textSize(isMobileShell)} text-slate-900`}
                        placeholder="Не указан"
                        disabled={saving}
                        options={courseOptions}
                        value={draft.study_course}
                        onChange={(value) => setField('study_course', value)}
                    />
                );
            default:
                return null;
        }
    };

    const renderValue = (field) => {
        if (state.status === 'loading') return <ProfileSkeletonValue />;
        const value = displayValue(data, field);
        // Перенос, а не многоточие: длинное название вуза — свои же данные,
        // и человек должен видеть их целиком (ProfileValue переносит слова).
        return value ? <ProfileValue>{value}</ProfileValue> : <ProfileEmpty />;
    };

    const rowNote = (field) => {
        if (!editing) return [null];
        if (errors[field]) return [errors[field], 'error'];
        // Подсказка одна и только при правке: пустое поле карты легко принять
        // за «номер стёрт», хотя оно значит «оставить как есть».
        if (field === 'card_number' && data?.has_card && !draft.card_number) {
            return ['Пусто — не меняется', 'muted'];
        }
        return [null];
    };

    const accessory = state.status !== 'ready' ? null : editing ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button
                type="button"
                onClick={cancelEditing}
                disabled={saving}
                className="rounded-xl px-3 py-1.5 text-[14px] font-medium text-slate-500 transition hover:bg-slate-100 active:scale-[0.98] disabled:opacity-50"
            >
                Отмена
            </button>
            <button
                type="submit"
                disabled={saving}
                className="rounded-xl bg-blue-600 px-3.5 py-1.5 text-[14px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:opacity-60"
            >
                {saving ? 'Сохраняю…' : 'Сохранить'}
            </button>
        </div>
    ) : (
        <button
            type="button"
            onClick={startEditing}
            className="rounded-xl px-3 py-1.5 text-[14px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.98]"
        >
            Изменить
        </button>
    );

    // Вся группа — форма: Enter в любом поле сохраняет, а «Сохранить» в
    // шапке группы — её же кнопка отправки. Отмены по Esc нет намеренно: Esc
    // закрывает список курсов, а событие из него всплывает до формы, и одно
    // нажатие заодно стирало бы всю несохранённую правку.
    return (
        <ProfileGroup as="form" id="my-data-title" title="Мои данные" accessory={accessory} onSubmit={save} noValidate>
            {state.status === 'error' ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }} className="px-4 py-3">
                    <span className="text-[14px] text-slate-500">Не удалось загрузить данные</span>
                    <button
                        type="button"
                        onClick={load}
                        className="rounded-xl px-3 py-1.5 text-[14px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.98]"
                    >
                        Повторить
                    </button>
                </div>
            ) : ROWS.map(({ field, label }) => {
                const [note, tone] = rowNote(field);
                return (
                    <ProfileRow key={field} label={label} note={note} noteTone={tone} stacked={editing && isMobileShell}>
                        {editing ? renderEditor(field) : renderValue(field)}
                    </ProfileRow>
                );
            })}
        </ProfileGroup>
    );
}
