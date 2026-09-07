import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Building2, ChevronDown, ChevronRight, Globe, Loader2, Lock, Plus, RotateCw,
    Trash2, TriangleAlert,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosBtnPrimary, iosBtnSecondary,
    IosHint, IosModal, IosSegmented, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { sectionAncestors } from './sectionPicker';
import { grantableCheck, presetIsGrantable } from './sectionGrants';
import useStableCallback from './useStableCallback';

/* Доступ к разделу — прямо из строки раздела во вкладке «Структура».
 *
 * Отдельной вкладки «Доступы» больше нет, и это не перестановка ради вида.
 * Там раздел выбирался селектом из плоского списка, где у СЗоВ и у ОП свои
 * одноимённые «Руководитель», «Супервайзер», «Оператор»: правило регулярно
 * уезжало в чужую ветку, а замечали это, только когда раздел переставали
 * видеть нужные люди. Здесь раздел выбран тем, что человек нажал на его строку.
 *
 * ── Одно окно и два уровня ────────────────────────────────────────────────
 * Первый уровень — СПИСОК: кому раздел уже открыт, одной строкой на адресата.
 * Второй — ПОДРОБНОСТЬ одного адресата: что ему разрешено. Переход внутри
 * того же окна, шевроном «назад» в шапке (`onBack` у IosModal), как в
 * «Настройках» iOS.
 *
 * До 07.09.2026 второй уровень был ВТОРОЙ МОДАЛКОЙ поверх первой. Это давало
 * два затемнения и два размытия подряд (фон уходил почти в чёрный), две шапки
 * с двумя крестиками, а вложенное окно было ýже нижнего — из-под него торчала
 * рамка, как ошибка вёрстки. На телефоне второй лист закрывал первый целиком,
 * и было непонятно, куда вернёт «Отмена».
 *
 * Заодно исчезла причина двух разных моделей сохранения. Раньше матрица
 * должностей копила правки до кнопки в подвале, а точечные правила писались
 * сразу — и любое действие с точечным правилом звало loadRules(), молча
 * стирая несохранённые тумблеры наверху. Теперь правило сохраняется там, где
 * его правят: на своём экране подробности, своей кнопкой.
 *
 * ── Из чего состоит список ────────────────────────────────────────────────
 * Наверху — должности ЭТОЙ ветки, как они заведены в дереве «Структуры»: у
 * СЗоВ это «Оператор», «Супервайзер», «Руководитель группы», у «Маркетинга» —
 * «Видеограф», «Таргетолог», «Контекстолог», «SMM-менеджер», «Руководитель».
 * Это ровно то, что настраивают каждый день, и оно не требует знать слово
 * «субъект». Ниже — точечные правила (человек, группа, направление, роль
 * вики): нужны редко, но без них модель прав беднее.
 *
 * До 04.09.2026 строк было четыре и они были одинаковы во всех ветках. Для
 * линии это правда, для отделов без линии — нет: форма предлагала выдать
 * доступ «супервайзеру Маркетинга», которого не существует, и не предлагала
 * ни одной настоящей должности отдела. Список считает сервер
 * (structure.branch_positions) — см. FALLBACK_ROWS ниже.
 *
 * ── Почему у должности два разных смысла ──────────────────────────────────
 * Если раздел лежит внутри ветки отдела (у неё заполнен «Отдел ветки»), правило
 * пишется на ОТДЕЛ, суженный либо порогом должности («СЗоВ, не ниже
 * супервайзера»), либо самой должностью («Маркетинг, видеограф»). Голая роль
 * 'sv' пробила бы границу отдела — супервайзер продаж увидел бы ОТП.
 * Если ветки отдела над разделом нет, писать не на что, кроме самой роли, —
 * и тогда правило действует по всей компании, о чём форма прямо предупреждает.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Должности, на которые выдают доступ, КОГДА РАЗДЕЛ ВНЕ ВЕТКИ ОТДЕЛА.
   Внутри ветки строки приезжают с сервера (positions в GET /access/section-rules)
   и повторяют должности этой ветки: у «СЗоВ» — руководитель группы, супервайзер
   и оператор, у «Маркетинга» — видеограф, таргетолог, контекстолог, SMM-менеджер
   и руководитель. Считать их здесь нельзя: список выводится из дерева, из отдела
   ветки и из кадров отдела, а у формы на руках лишь плоский список разделов —
   расчёт разошёлся бы с проверкой на записи, всегда в сторону «строку показали,
   а сервер отказал».

   Здесь остаётся только запасной вариант: над разделом ветки отдела нет вовсе,
   писать не на что, кроме самой роли, и правило действует по всей компании — о
   чём форма прямо предупреждает.

   Порог «не ниже»: правило действует, если уровень человека не меньше
   указанного (шкала ROLE_LEVELS, wiki/access.py). Отсюда бесплатно выходит
   «видит своё и всё, что ниже себя».

   У самой нижней строки порог NULL, а не 10. Разница не косметическая:
   role_level_of отдаёт 0 для роли, которой нет в шкале, и порог 10 отрезал бы
   таких людей от раздела, открытого «всему отделу».

   Порог здесь ПУСТОЙ у всех четырёх: правило адресовано самой роли, и второй
   раз ограничивать уровень нечем. Поэтому «вес» строки (то, с чем сравнивается
   потолок раздающего) вынесен отдельным полем — иначе строку руководителя
   раздавал бы кто угодно. */
const FALLBACK_ROWS = [
    { key: 'operator', label: 'Оператор', hint: 'и все, кто выше — весь отдел',
      min_role_level: null, weight: 10, subject_type: 'otp_role', subject_role: 'operator' },
    { key: 'trainer', label: 'Тренер', hint: 'и выше',
      min_role_level: null, weight: 20, subject_type: 'otp_role', subject_role: 'trainer' },
    { key: 'sv', label: 'Супервайзер', hint: 'и выше',
      min_role_level: null, weight: 30, subject_type: 'otp_role', subject_role: 'sv' },
    { key: 'head', label: 'Руководитель группы', hint: 'и выше',
      min_role_level: null, weight: 40, subject_type: 'otp_role', subject_role: 'admin' },
];

/* Правило без порога открывает раздел всем от оператора и выше — и «весит»
   столько же. Ноль вместо уровня оператора прошёл бы любую проверку. */
const OPERATOR_LEVEL = 10;
const rowWeight = (row) => (
    row.weight ?? (row.min_role_level == null ? OPERATOR_LEVEL : row.min_role_level));

/* Совпадает ли правило с этой строкой — по ПОЛНОМУ адресату, а не по одному
   порогу. Внутри «Маркетинга» у всех четырёх должностей порог одинаковый (10):
   сравнение по нему одно правило приписало бы сразу нескольким строкам, а
   остальные показали бы «доступа нет» при фактически выданном доступе. */
const ruleMatchesRow = (rule, row) => (
    rule.subject_type === row.subject_type
    && (row.subject_type === 'otp_role'
        ? rule.subject_role === row.subject_role
        : Number(rule.subject_id) === Number(row.subject_id))
    && (rule.min_role_level ?? null) === (row.min_role_level ?? null)
    && (rule.job_title || null) === (row.job_title || null));

const PERMISSIONS = [
    { key: 'can_read', label: 'Читать', note: 'видит раздел и его статьи' },
    { key: 'can_create', label: 'Создавать', note: 'заводит новые статьи' },
    { key: 'can_edit', label: 'Править', note: 'меняет текст существующих' },
    { key: 'can_publish', label: 'Публиковать', note: 'выпускает черновик' },
    { key: 'can_approve', label: 'Согласовывать', note: 'подтверждает чужую правку' },
    { key: 'can_delete', label: 'Удалять', note: 'убирает статьи', danger: true },
];

/* Готовые наборы прав — то, что выбирают в 9 случаях из 10. Тонкая настройка
   остаётся рядом, но НИЖЕ и свёрнутая: пока шесть тумблеров лежали прямо под
   сегментами, пресеты не экономили ничего — человек всё равно видел десять
   органов управления одним и тем же.

   `summary` — как набор называется одним словом в строке списка, `note` — чем
   он отличается от соседнего. Без них строка перечисляла все выданные права
   пилюлями: у полного доступа их семь, они переносились в три ряда, и список
   должностей превращался в рваную лестницу. */
const PRESETS = [
    { key: 'none', label: 'Нет', summary: 'Нет доступа',
      note: 'Раздела не видно в дереве, статьи не открываются.',
      permissions: {} },
    { key: 'read', label: 'Чтение', summary: 'Чтение',
      note: 'Видит раздел и читает его статьи. Менять ничего не может.',
      permissions: { can_read: true } },
    { key: 'write', label: 'Правка', summary: 'Правка',
      note: 'Читает, заводит новые статьи и меняет текст существующих.',
      permissions: { can_read: true, can_create: true, can_edit: true } },
    { key: 'full', label: 'Полный', summary: 'Полный доступ',
      note: 'Все шесть прав: вместе с публикацией, согласованием и удалением.',
      permissions: { can_read: true, can_create: true, can_edit: true,
                     can_publish: true, can_approve: true, can_delete: true } },
];

/* Пятый сегмент — не выбор, а ЧЕСТНАЯ подпись набора, собранного руками.
   Пока его не было, ручная правка тумблера гасила подсветку у всех четырёх
   сегментов сразу, и контрол выглядел сломанным. */
const CUSTOM_KEY = 'custom';

const NO_PERMISSIONS = Object.fromEntries(PERMISSIONS.map((p) => [p.key, false]));

const permissionsOf = (rule) => Object.fromEntries(
    PERMISSIONS.map((p) => [p.key, !!rule?.[p.key]]));

const anyPermission = (permissions) => PERMISSIONS.some((p) => permissions[p.key]);

/** Какой пресет описывает набор прав целиком. null — набор собран вручную. */
const presetOf = (permissions) => PRESETS.find(
    (preset) => PERMISSIONS.every(
        (p) => !!preset.permissions[p.key] === !!permissions[p.key]),
)?.key || null;

/** Право на запись без чтения бессмысленно — сервер всё равно включит чтение. */
const withRead = (permissions) => (
    anyPermission(permissions) ? { ...permissions, can_read: true } : permissions);

/** Одно слово для строки списка: «Чтение», «Полный доступ», «Свои права». */
const accessSummary = (permissions) => {
    const key = presetOf(permissions);
    if (key) return PRESETS.find((p) => p.key === key).summary;
    return 'Свои права';
};

/** Чем этот набор отличается от соседнего — строкой под сегментами. */
const accessNote = (permissions) => {
    const key = presetOf(permissions);
    if (key) return PRESETS.find((p) => p.key === key).note;
    return PERMISSIONS.filter((p) => permissions[p.key]).map((p) => p.label).join(' · ');
};

/* «174 человека», «1 человек», «22 человека». Счётчик под строкой должности
   отвечает на главный вопрос выдачи — кому именно я сейчас открываю раздел, —
   и склонение тут не украшение: «174 человек» читается как опечатка и роняет
   доверие ко всей строке. Число приезжает готовым (positions[].people). */
const peopleLabel = (count) => {
    const ten = count % 10;
    const hundred = count % 100;
    if (ten === 1 && hundred !== 11) return `${count} человек`;
    if (ten >= 2 && ten <= 4 && (hundred < 12 || hundred > 14)) return `${count} человека`;
    return `${count} человек`;
};

const SUBJECT_KINDS = [
    { value: 'user', label: 'Конкретный человек' },
    { value: 'group', label: 'Группа' },
    { value: 'direction', label: 'Направление' },
    { value: 'department', label: 'Отдел' },
    // Адресуется НАЗНАЧЕНИЮ, а не человеку: правило переезжает вместе со сменой
    // главы, и переставлять его руками не нужно.
    { value: 'department_head', label: 'Глава отдела' },
    { value: 'wiki_role', label: 'Роль в вики' },
    { value: 'otp_role', label: 'Роль в системе' },
];

const SUBJECT_KIND_LABEL = Object.fromEntries(SUBJECT_KINDS.map((k) => [k.value, k.label]));

/* Субъекты БЕЗ отдела: роль в системе носят сотрудники всех отделов сразу,
   роль вики — все, кому её назначили. Раздающему, у которого есть граница
   отдела, они закрыты (та же пара в wiki/access.py: COMPANY_WIDE_SUBJECTS). */
const COMPANY_WIDE_KINDS = ['otp_role', 'wiki_role'];

/* Словарь портала, а не свой. Раньше здесь стояли «руководитель» и «директор»,
   которых больше нигде в системе нет: поиск по слову «админ» не находил никого,
   и выглядело это как «админов в списке нет» — хотя они были. Ровно эти же
   подписи отдаёт справочник ролей в /access/subjects. */
const ROLE_TITLE = {
    operator: 'оператор', trainee: 'стажёр', trainer: 'тренер',
    sv: 'супервайзер', supervisor: 'супервайзер',
    admin: 'админ', super_admin: 'супер-админ',
};

/* Развёрнуто, а не «от СВ»: в строке правила это единственное объяснение,
   кого правило захватывает, и аббревиатура должности там читается как код. */
const ROLE_LEVEL_LABEL = {
    10: 'от оператора и выше', 20: 'от тренера и выше', 30: 'от супервайзера и выше',
    40: 'от руководителя и выше', 50: 'супер-админ',
};

/** Ветка отдела над разделом: он сам или ближайший предок с отделом. */
export function branchDepartment(sections, sectionId) {
    const path = sectionAncestors(sections, sectionId);
    for (let i = path.length - 1; i >= 0; i -= 1) {
        if (path[i].department_id) {
            return {
                id: path[i].department_id,
                name: path[i].department_name || `отдел #${path[i].department_id}`,
                sectionName: path[i].name,
                own: path[i].id === Number(sectionId),
            };
        }
    }
    return null;
}

/** Строка матрицы, которой принадлежит правило. null — оно точечное. */
const rowOfRule = (rule, rows) => rows.find((row) => ruleMatchesRow(rule, row)) || null;

/* Что дописано к правилу сверх шести прав. Серым текстом, а не цветной
   пилюлей: это не право, а область его действия. Цвет тут ничего не значил
   бы, а на строке с выданным доступом их бывает сразу два.

   Первой идёт расшифровка ручного набора: одно слово «Свои права» в правой
   колонке — ровно тот чёрный ящик, ради которого строку и открывают. Здесь
   набор назван поимённо, и открывать её незачем. */
const scopeNotes = (permissions, state) => [
    presetOf(permissions) ? null : accessNote(permissions),
    state.grant_subsections ? 'вместе с подразделами' : null,
    state.manage_subsections ? 'строит подразделы' : null,
].filter(Boolean);

// ── Строка списка ───────────────────────────────────────────────────────────
/* Навигационная строка в духе «Настроек»: слева адресат, справа одно слово о
   выданном и шеврон. Всё, что нужно для «кому открыт раздел», читается
   вертикальным взглядом по правому краю — не разбирая пилюли в каждой строке. */
const AccessRow = ({ title, meta, notes = [], value, muted, locked, onOpen }) => {
    const body = (
        <>
            <div className="min-w-0 flex-1">
                <div className={`truncate text-[14px] font-medium ${
                    locked ? 'text-slate-400' : 'text-slate-900'}`}>
                    {title}
                </div>
                {meta && (
                    <div className="mt-0.5 truncate text-[11.5px] text-slate-400">{meta}</div>
                )}
                {notes.length > 0 && (
                    <div className="mt-1 truncate text-[11.5px] text-slate-400">
                        {notes.join(' · ')}
                    </div>
                )}
            </div>
            <span className={`shrink-0 text-[13px] ${muted ? 'text-slate-400' : 'text-slate-700'}`}>
                {value}
            </span>
            {/* Строка выше потолка ПОКАЗАНА, но заперта, а не спрятана.
                Спрятанная строка выглядит как «такой должности не бывает»;
                запертая объясняет, что выдача есть, но не отсюда. */}
            {locked
                ? <Lock size={13} className="shrink-0 text-slate-300" />
                : <ChevronRight size={16} className="shrink-0 text-slate-300" />}
        </>
    );

    if (locked) {
        return <div className="flex items-center gap-3 px-4 py-3 text-left">{body}</div>;
    }
    return (
        <button
            type="button"
            onClick={onOpen}
            className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100"
        >
            {body}
        </button>
    );
};

// ── Что разрешено ───────────────────────────────────────────────────────────
/* Один и тот же блок на обоих экранах подробности — должности и точечного
   правила. Пока это были два разных куска разметки, они разъезжались: в
   матрице у каждого права была подпись, в точечном правиле — нет. */
const PermissionPicker = ({ permissions, onChange, mayGrant, detailed, onDetailed }) => {
    const preset = presetOf(permissions);
    /* Показываем пресеты по силам раздающему — И тот, который стоит сейчас,
       даже если сам он его выдать не вправе. Иначе супервайзер, открывший
       строку с полным доступом от директора, видел бы контрол без единого
       подсвеченного сегмента: набор есть, а как он называется — не сказано. */
    const options = PRESETS
        .filter((p) => presetIsGrantable(p, mayGrant) || p.key === preset)
        .map((p) => ({ value: p.key, label: p.label }));
    if (!preset) options.push({ value: CUSTOM_KEY, label: 'Своё' });

    return (
        <section className="space-y-2">
            <div className={iosGroupLabel}>Что разрешено</div>
            <IosSegmented
                value={preset || CUSTOM_KEY}
                options={options}
                size="lg"
                ariaLabel="Что разрешено"
                onChange={(key) => {
                    const chosen = PRESETS.find((p) => p.key === key);
                    if (!chosen) return;   // «Своё» — признак ручного набора, а не выбор
                    onChange({ ...NO_PERMISSIONS, ...chosen.permissions });
                }}
            />
            <p className="px-1 text-[12px] leading-relaxed text-slate-500">
                {accessNote(permissions)}
            </p>

            <button
                type="button"
                onClick={() => onDetailed(!detailed)}
                aria-expanded={detailed}
                className="flex w-full items-center gap-1.5 rounded-lg px-1 py-1 text-left transition hover:bg-slate-100"
            >
                <span className={iosGroupLabel}>Права по одному</span>
                <ChevronDown
                    size={14}
                    className={`text-slate-400 transition-transform ${detailed ? 'rotate-180' : ''}`}
                />
            </button>

            {detailed && (
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {PERMISSIONS.map((p) => (
                        <div key={p.key} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                            <div className="min-w-0">
                                <div className={`text-[13.5px] ${
                                    !mayGrant(p.key) ? 'text-slate-400'
                                        : p.danger ? 'text-amber-700' : 'text-slate-800'}`}>
                                    {p.label}
                                </div>
                                <div className="text-[11.5px] text-slate-400">
                                    {mayGrant(p.key) ? p.note
                                        : 'это право выдаёт вышестоящий руководитель'}
                                </div>
                            </div>
                            <IosToggle
                                checked={!!permissions[p.key]}
                                // Снять чтение, оставив правку, нельзя: сервер всё
                                // равно вернёт его обратно, и тумблер соврал бы.
                                // Право выше собственного — тоже: сервер откажет.
                                disabled={!mayGrant(p.key)
                                    || (p.key === 'can_read' && PERMISSIONS.some(
                                        (x) => x.key !== 'can_read' && permissions[x.key]))}
                                onChange={(v) => onChange(withRead({ ...permissions, [p.key]: v }))}
                            />
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
};

// ── Область действия правила ────────────────────────────────────────────────
/* Два тумблера про ДЕРЕВО, а не про статьи, поэтому они отдельной карточкой
   под правами, а не седьмым и восьмым в общем списке.

   Показываются только когда права вообще выданы: «вместе с подразделами» без
   единого права — это уточнение к тому, чего нет. */
const ScopeCard = ({ state, onChange, canManage }) => {
    if (!anyPermission(state.permissions)) return null;
    return (
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
            <div className="flex items-start justify-between gap-3 p-3.5">
                <div className="min-w-0">
                    <div className="text-[13.5px] font-medium text-slate-900">
                        Вместе с подразделами
                    </div>
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                        Те же права во всех вложенных разделах, включая созданные позже.
                    </p>
                </div>
                <IosToggle
                    checked={!!state.grant_subsections}
                    onChange={(v) => onChange({ ...state, grant_subsections: v })}
                />
            </div>

            {/* Передача управления ВЕТКОЙ. Тумблера нет у того, кто сам
                структуру не ведёт: сервер такое правило отвергнет, и галочка
                соврала бы. Шести прав выше он не касается — те про статьи,
                этот про дерево. */}
            {canManage && (
                <div className="flex items-start justify-between gap-3 p-3.5">
                    <div className="min-w-0">
                        <div className="text-[13.5px] font-medium text-slate-900">
                            Может заводить подразделы
                        </div>
                        <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                            Заводит подразделы внутри этого раздела и переименовывает их.
                            Архив, публичность и перенос в другую ветку остаются у вас.
                        </p>
                    </div>
                    <IosToggle
                        checked={!!state.manage_subsections}
                        onChange={(v) => onChange({ ...state, manage_subsections: v })}
                    />
                </div>
            )}

            {/* Право строить дерево уже выдано, а этот раздающий им не
                распоряжается. Строка показана заперто и с прямым
                предупреждением: сервер такое правило принимает только от того,
                кто управляет структурой, поэтому сохранение отсюда снимет флаг.
                Раньше он снимался молча — правило переставало пускать человека
                в дерево, и связать это с чужой правкой прав было невозможно. */}
            {!canManage && state.manage_subsections && (
                <div className="flex items-start justify-between gap-3 p-3.5 opacity-70">
                    <div className="min-w-0">
                        <div className="text-[13.5px] font-medium text-slate-500">
                            Может заводить подразделы · включено
                        </div>
                        <p className="mt-0.5 text-[11.5px] leading-relaxed text-amber-700">
                            Этим правом распоряжается тот, кто управляет структурой вики.
                            Если сохранить права отсюда, оно снимется.
                        </p>
                    </div>
                    <Lock size={14} className="mt-1 shrink-0 text-slate-400" />
                </div>
            )}
        </div>
    );
};

export default function WikiSectionAccess({ base, headers, showToast, section, sections,
                                            spaceId = null, onClose, reload }) {
    const toast = useStableCallback(showToast);

    const [rules, setRules] = useState([]);
    /* Должности ветки — строки матрицы. Считает сервер (structure.branch_positions):
       список выводится из дерева разделов, отдела ветки и кадров отдела, а форме
       из этого доступен только плоский список разделов. */
    const [positions, setPositions] = useState([]);
    const [ceiling, setCeiling] = useState(null);
    /* Отделы, которым этот человек вправе адресовать правило; null — без
       границы (директор, администратор вики). Считает сервер: граница зависит
       от должности и от возглавляемых отделов, и второй расчёт на клиенте
       однажды разошёлся бы с первым — всегда в сторону «показали, а сервер
       ответил 403». */
    const [grantDepartments, setGrantDepartments] = useState(null);
    // Какие права этот раздающий вправе поставить. Приезжает вместе с потолком
    // и границей отдела — тремя измерениями выдачи (routes_structure).
    const [grantable, setGrantable] = useState(null);
    /* Вправе ли ЭТОТ раздающий передать управление деревом. Считает сервер по
       способностям ДОЛЖНОСТИ: право, полученное из правила, дальше не
       передаётся, иначе лестница выдачи расползлась бы вниз сама собой. */
    const [grantableStructure, setGrantableStructure] = useState(false);
    const [people, setPeople] = useState([]);
    const [catalog, setCatalog] = useState({});
    const [loading, setLoading] = useState(true);
    /* Отказ загрузки — ОТДЕЛЬНОЕ состояние, а не пустой список. Пока его не
       было, сорвавшийся запрос показывал уверенное «В этой ветке ещё нет
       разделов-должностей» и отправлял человека заводить то, что уже заведено. */
    const [failed, setFailed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [matrix, setMatrix] = useState({});

    /* Второй уровень окна. Одновременно открыт ровно один: rowDraft — экран
       должности, draft — экран точечного правила. Правка идёт в КОПИИ, а не в
       matrix: иначе «Отмена» пришлось бы откатывать вручную, а перезагрузка
       правил посреди правки затирала бы тумблеры. */
    const [rowDraft, setRowDraft] = useState(null);
    const [draft, setDraft] = useState(null);
    const [detailed, setDetailed] = useState(false);
    // Направление перехода: вперёд экран приезжает справа, назад — слева.
    const [anim, setAnim] = useState('');

    const sectionId = section?.id;
    const department = useMemo(
        () => (sectionId ? branchDepartment(sections, sectionId) : null),
        [sections, sectionId]);

    const path = useMemo(
        () => sectionAncestors(sections, sectionId).map((s) => s.name).join(' › '),
        [sections, sectionId]);

    const loadRules = useCallback(() => {
        if (!sectionId) return;
        setLoading(true);
        axios.get(`${base}/access/section-rules`, { headers, params: { section_id: sectionId } })
            .then((r) => {
                setRules(r.data?.items || []);
                // Должности ветки — тем же ответом. Пустой список означает
                // «раздел вне ветки отдела»: там правило пишется на роль по
                // всей компании, и строки берутся из FALLBACK_ROWS.
                setPositions(r.data?.positions || []);
                // Потолок приезжает вместе со списком: он зависит и от должности,
                // и от отдела раздела, поэтому считать его на клиенте нельзя.
                setCeiling(r.data?.grant_ceiling ?? null);
                setGrantDepartments(r.data?.grant_departments ?? null);
                setGrantable(r.data?.grantable ?? null);
                setGrantableStructure(!!r.data?.grantable_structure);
                setFailed(false);
            })
            .catch((e) => {
                setFailed(true);
                toast(errText(e, 'Не удалось загрузить правила'), 'error');
            })
            .finally(() => setLoading(false));
    }, [base, headers, sectionId, toast]);

    useEffect(() => { loadRules(); }, [loadRules]);

    useEffect(() => {
        if (!spaceId) return;   // пространство ещё не приехало из ping
        /* space_id — не уточнение выборки, а граница: без него справочник
           предлагал отделы и людей ЧУЖОГО пространства (в «Таксопарках» —
           «Тез КЦ» и его сотрудников). Правило на них ничего не открыло бы, но
           чужая оргструктура показывалась целиком. */
        axios.get(`${base}/access/subjects`, { headers, params: { space_id: spaceId } })
            .then((r) => setCatalog(r.data || {}))
            .catch(() => setCatalog({}));
        // Сотрудники приходят уже отфильтрованными по потолку, отделу и
        // пространству: форма не должна предлагать того, кого сервер отвергнет.
        axios.get(`${base}/access/people`, { headers, params: { space_id: spaceId } })
            .then((r) => setPeople(r.data?.items || []))
            .catch(() => setPeople([]));
    }, [base, headers, spaceId]);

    /* Строки матрицы: должности ветки с сервера, а вне ветки — запасной набор
       ролей. Не «или пусто»: ветка без единого раздела-должности честно даёт
       пустую матрицу, и подменять её ролями значило бы вернуть ту же выдумку,
       из-за которой «Маркетингу» предлагали супервайзера. */
    const rows = useMemo(
        () => (department ? positions : FALLBACK_ROWS), [department, positions]);

    /* Матрица должностей — производная от загруженных правил, но состояние
       собственное: с ним сверяется экран подробности, отвечая на вопрос
       «что здесь изменили». */
    useEffect(() => {
        const next = {};
        rows.forEach((row) => {
            const rule = rules.find((r) => ruleMatchesRow(r, row));
            next[row.key] = {
                permissions: rule ? permissionsOf(rule) : { ...NO_PERMISSIONS },
                // Новое правило по умолчанию НЕ уходит вглубь: глубокое правило
                // на родителе сливает соседние ветки отделов в одну.
                grant_subsections: rule ? !!rule.grant_subsections : false,
                /* Флаг про ДЕРЕВО обязан ездить с правилом туда и обратно.
                   POST перезаписывает правило целиком, и пока матрица его не
                   возила, сохранение строки молча гасило «строит подразделы» у
                   правила, попавшего в эту строку. */
                manage_subsections: rule ? !!rule.manage_subsections : false,
                ruleId: rule?.id || null,
            };
        });
        setMatrix(next);
    }, [rules, rows]);

    const extraRules = useMemo(
        () => rules.filter((r) => !rowOfRule(r, rows)), [rules, rows]);

    /* Заперта ли строка. У должностей ветки ответ считает СЕРВЕР той же
       функцией, что и отказ на записи (may_grant_with_ceiling): у строки без
       порога вес равен уровню оператора, и она не заперлась бы никогда, а
       сервер на неё всё равно ответил бы WIKI_GRANT_CEILING. Вес остаётся
       запасным расчётом — для строк вне ветки, которых сервер не считает. */
    const isLocked = (row) => (row.locked != null
        ? !!row.locked
        : ceiling == null || rowWeight(row) > ceiling);

    /* Адресат правила берётся из САМОЙ строки, а не выводится заново из отдела:
       должность (job_title) порогом не выражается, и вывести её здесь неоткуда. */
    const ruleBody = (row) => (row.subject_type === 'otp_role'
        ? { subject_type: 'otp_role', subject_role: row.subject_role,
            min_role_level: row.min_role_level ?? null }
        : { subject_type: row.subject_type, subject_id: row.subject_id,
            min_role_level: row.min_role_level ?? null,
            job_title: row.job_title || null });

    const mayGrant = useMemo(() => grantableCheck(grantable), [grantable]);

    const openRow = (row) => {
        const state = matrix[row.key];
        if (!state) return;
        setAnim('animate-push-in');
        // Ручной набор прав раскрываем сразу: свёрнутый список скрыл бы
        // единственное объяснение, почему сегмент показывает «Своё».
        setDetailed(!presetOf(state.permissions));
        setRowDraft({ key: row.key, ...state });
    };

    const openRule = (rule) => {
        setAnim('animate-push-in');
        setDetailed(rule ? !presetOf(permissionsOf(rule)) : false);
        setDraft(rule ? {
            editing: true,
            ruleId: rule.id,
            subject_label: rule.subject_label || rule.subject_role || `#${rule.subject_id}`,
            subject_type: rule.subject_type,
            subject_id: rule.subject_id ?? '',
            subject_role: rule.subject_role || 'operator',
            min_role_level: rule.min_role_level ?? '',
            // Должность — часть КЛЮЧА правила. Не передав её обратно, правка
            // прав завела бы второе правило, а исходное осталось бы висеть с
            // прежними правами.
            job_title: rule.job_title || null,
            grant_subsections: !!rule.grant_subsections,
            manage_subsections: !!rule.manage_subsections,
            permissions: permissionsOf(rule),
        } : {
            editing: false,
            ruleId: null,
            subject_type: 'user', subject_id: '', subject_role: 'operator',
            min_role_level: '', job_title: null, grant_subsections: false,
            manage_subsections: false,
            permissions: { ...NO_PERMISSIONS, can_read: true },
        });
    };

    const goBack = () => {
        setAnim('animate-pop-in');
        setRowDraft(null);
        setDraft(null);
    };

    /* Что изменилось на экране подробности. Сравниваем с matrix, а не с
       правилом: matrix уже свёл правило и «правила нет» к одному виду. */
    const rowChanged = rowDraft && (() => {
        const before = matrix[rowDraft.key];
        if (!before) return false;
        return PERMISSIONS.some((p) => !!before.permissions[p.key] !== !!rowDraft.permissions[p.key])
            || (anyPermission(rowDraft.permissions)
                && (!!before.grant_subsections !== !!rowDraft.grant_subsections
                    || !!before.manage_subsections !== !!rowDraft.manage_subsections));
    })();

    /* То же на экране точечного правила. У нового правила «изменилось» — это
       «адресат выбран и права не пусты»: без обоих сервер либо откажет, либо
       заведёт правило, которое ничего не откроет. */
    const ruleChanged = draft && (() => {
        if (!draft.editing) {
            return !!(draft.subject_type === 'otp_role' || draft.subject_id)
                && anyPermission(draft.permissions);
        }
        const before = rules.find((r) => r.id === draft.ruleId);
        if (!before) return true;
        return PERMISSIONS.some((p) => !!before[p.key] !== !!draft.permissions[p.key])
            || !!before.grant_subsections !== !!draft.grant_subsections
            || !!before.manage_subsections !== !!draft.manage_subsections;
    })();

    const saveRow = () => {
        const row = rows.find((r) => r.key === rowDraft.key);
        if (!row) return;
        const state = rowDraft;
        setBusy(true);
        const request = anyPermission(state.permissions)
            ? axios.post(`${base}/access/section-rules`, {
                section_id: sectionId,
                ...ruleBody(row),
                ...state.permissions,
                grant_subsections: state.grant_subsections,
                /* Флаг едет обратно только от того, кто им распоряжается.
                   Сервер отвергает manage_subsections=true у всех остальных
                   (WIKI_GRANT_BEYOND_SELF), и отправлять его «как было» значило
                   бы ловить отказ на правке обычного права. */
                manage_subsections: grantableStructure && state.manage_subsections,
            }, { headers })
            // Права сняты все до одного — правила больше нет, а не «есть, но
            // пустое»: пустое правило всё равно открывало бы раздел.
            : (state.ruleId
                ? axios.delete(`${base}/access/section-rules/${state.ruleId}`, { headers })
                : Promise.resolve());
        request
            // Возвращаемся в список только на успехе: после отказа экран обязан
            // остаться открытым с несохранёнными переключателями, иначе правка
            // молча пропадёт вместе с ним.
            .then(() => { toast('Доступ сохранён', 'success'); goBack(); loadRules(); reload?.(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить доступ'), 'error'))
            .finally(() => setBusy(false));
    };

    const saveExtra = () => {
        setBusy(true);
        /* Правило без единого права — это удаление, ровно как в строке
           должности. Пока экраны расходились, через точечное правило можно было
           СОХРАНИТЬ пустое правило: в таблице оно оставалось и раздел открывало. */
        const request = (!anyPermission(draft.permissions) && draft.ruleId)
            ? axios.delete(`${base}/access/section-rules/${draft.ruleId}`, { headers })
            : axios.post(`${base}/access/section-rules`, {
                section_id: sectionId,
                subject_type: draft.subject_type,
                subject_id: draft.subject_type === 'otp_role' ? null : Number(draft.subject_id) || null,
                subject_role: draft.subject_type === 'otp_role' ? draft.subject_role : null,
                min_role_level: draft.min_role_level === '' ? null : Number(draft.min_role_level),
                job_title: draft.job_title || null,
                ...draft.permissions,
                grant_subsections: draft.grant_subsections,
                manage_subsections: !!draft.manage_subsections && grantableStructure,
            }, { headers });
        request
            .then(() => { toast('Правило сохранено', 'success'); goBack(); loadRules(); reload?.(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить правило'), 'error'))
            .finally(() => setBusy(false));
    };

    const removeRule = () => {
        setBusy(true);
        axios.delete(`${base}/access/section-rules/${draft.ruleId}`, { headers })
            .then(() => { toast('Правило удалено', 'success'); goBack(); loadRules(); reload?.(); })
            .catch((e) => toast(errText(e, 'Не удалось удалить'), 'error'))
            .finally(() => setBusy(false));
    };

    /* Должность в подписи не для красоты: тёзки в списке из 174 человек
       неразличимы, а ошибка тут выдаёт доступ не тому. */
    const peopleOptions = useMemo(() => people.map((person) => ({
        value: String(person.id),
        label: [person.name, ROLE_TITLE[person.role] || person.role,
                person.department_name].filter(Boolean).join(' · '),
    })), [people]);

    /* Субъекты, которые этот человек вправе адресовать.
       Роль в системе и роль вики действуют ПО ВСЕЙ КОМПАНИИ, мимо отдела:
       правило otp_role='operator' без порога открывает раздел каждому
       сотруднику. Раздающему с границей отдела сервер их отвергает
       (access.may_grant_to_subject), поэтому и в форме их нет — предложенная
       строка, на которую приходит отказ, читается как поломка, а не как
       правило. Отделы, группы и направления сервер присылает уже сужёнными. */
    const subjectKinds = useMemo(
        () => (grantDepartments
            ? SUBJECT_KINDS.filter((k) => !COMPANY_WIDE_KINDS.includes(k.value))
            : SUBJECT_KINDS),
        [grantDepartments]);

    const subjectOptions = useMemo(() => {
        const kind = draft?.subject_type;
        if (!kind || kind === 'otp_role' || kind === 'user') return [];
        const source = kind === 'department_head' ? 'department' : kind;
        return (catalog[source] || []).map((item) => ({
            value: String(item.id), label: item.name,
        }));
    }, [catalog, draft?.subject_type]);

    const isPublic = section?.visibility_scope === 'public';
    const openedRow = rowDraft ? rows.find((r) => r.key === rowDraft.key) : null;

    // ── Шапка и подвал зависят от того, какой экран открыт ───────────────────
    let title = 'Доступ к разделу';
    let subtitle = path || section?.name;
    let footer = (
        <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
    );

    if (rowDraft && openedRow) {
        title = openedRow.label;
        subtitle = department
            ? `Должность отдела «${department.name}»`
            : 'Роль в системе — во всей компании';
        footer = (
            <>
                <button type="button" className={iosBtnSecondary} onClick={goBack}>
                    {rowChanged ? 'Отмена' : 'Назад'}
                </button>
                <button type="button" className={iosBtnPrimary} disabled={busy || !rowChanged}
                        onClick={saveRow}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                </button>
            </>
        );
    } else if (draft) {
        title = draft.editing ? draft.subject_label : 'Новое правило';
        subtitle = draft.editing
            ? SUBJECT_KIND_LABEL[draft.subject_type]
            : `Раздел «${section?.name}»`;
        footer = (
            <>
                {draft.editing && (
                    <button
                        type="button"
                        disabled={busy}
                        onClick={removeRule}
                        className="mr-auto inline-flex items-center gap-1.5 rounded-xl px-3 py-2.5 text-[13.5px] font-semibold text-rose-600 transition hover:bg-rose-50 active:scale-[0.98] disabled:opacity-50"
                    >
                        <Trash2 size={14} /> Удалить
                    </button>
                )}
                <button type="button" className={iosBtnSecondary} onClick={goBack}>
                    {ruleChanged ? 'Отмена' : 'Назад'}
                </button>
                <button
                    type="button"
                    className={iosBtnPrimary}
                    disabled={busy || !ruleChanged}
                    onClick={saveExtra}
                >
                    {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                </button>
            </>
        );
    }

    const screen = rowDraft ? `row:${rowDraft.key}` : (draft ? 'rule' : 'list');

    /* Крестик и клик мимо окна закрывают его целиком, и на экране подробности
       это уносило бы несохранённые тумблеры молча — промах мышью по затемнению
       стоил бы всей настройки. Пока правки не сохранены, первый выход
       возвращает в список: работа остаётся на виду, а второй клик закрывает.
       Ничего не изменено — закрываем сразу, лишнего шага нет. */
    const handleClose = () => {
        if ((rowDraft && rowChanged) || (draft && ruleChanged)) { goBack(); return; }
        onClose?.();
    };

    return (
        <IosModal
            open={!!section}
            onClose={handleClose}
            onBack={rowDraft || draft ? goBack : null}
            title={title}
            subtitle={subtitle}
            maxWidth="max-w-xl"
            footer={footer}
        >
            {/* key переклеивает содержимое на каждом переходе — иначе анимация
                проигралась бы один раз за всю жизнь окна.

                Высоту окна экраны задают собой, без нижней границы: у строки
                со снятым доступом настраивать нечего, и подпёртое до «как у
                списка» окно давало полполотна пустого поля — тот самый воздух,
                который на этом экране и убирали. */}
            <div key={screen} className={anim}>

                {/* ── Экран должности ─────────────────────────────────────── */}
                {rowDraft && openedRow && (
                    <div className="space-y-4">
                        <div className="px-1 text-[12.5px] leading-relaxed text-slate-500">
                            {typeof openedRow.people === 'number' && (
                                openedRow.people === 0
                                    // Ноль носителей — почти всегда опечатка в названии
                                    // раздела-должности: правило сохранится и не откроет
                                    // НИЧЕГО. Без этой подписи такая выдача выглядит рабочей.
                                    ? <span className="text-amber-600">нет таких сотрудников</span>
                                    : <span>{peopleLabel(openedRow.people)}</span>
                            )}
                            {openedRow.hint && (
                                <span>
                                    {typeof openedRow.people === 'number' ? ' · ' : ''}
                                    {openedRow.hint}
                                </span>
                            )}
                        </div>

                        <PermissionPicker
                            permissions={rowDraft.permissions}
                            onChange={(permissions) => setRowDraft({ ...rowDraft, permissions })}
                            mayGrant={mayGrant}
                            detailed={detailed}
                            onDetailed={setDetailed}
                        />

                        <ScopeCard
                            state={rowDraft}
                            onChange={setRowDraft}
                            canManage={grantableStructure}
                        />
                    </div>
                )}

                {/* ── Экран точечного правила ─────────────────────────────── */}
                {draft && (
                    <div className="space-y-4">
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Кому</div>
                            {/* Селекты БЕЗ обёртки-карточки: у CustomSelect в
                                варианте ios своя белая плашка с кантом, и внутри
                                iosCard получались две рамки в трёх пикселях
                                друг от друга — ровно тот «ящик в ящике», из-за
                                которого экран и выглядел неаккуратно. */}
                            <div className="space-y-2">
                                <CustomSelect
                                    variant="ios"
                                    value={draft.subject_type}
                                    onChange={(v) => setDraft({ ...draft, subject_type: v, subject_id: '' })}
                                    options={subjectKinds}
                                    ariaLabel="Тип субъекта"
                                    // Адресат — часть ключа правила (раздел + субъект +
                                    // порог). Сменить его на месте нельзя: получилось бы
                                    // второе правило, а первое осталось бы висеть.
                                    disabled={!!draft.editing}
                                />

                                {/* Сотрудник выбирается поиском по имени, а не вводом id.
                                    Числовой id можно было узнать только заглянув в базу, а
                                    опечатка выдавала доступ постороннему молча — сервер
                                    несуществующий id даже не проверял. Список приходит уже
                                    обрезанным по потолку и отделу. */}
                                {draft.subject_type === 'user' && (
                                    <CustomSelect
                                        variant="ios"
                                        value={draft.subject_id}
                                        onChange={(v) => setDraft({ ...draft, subject_id: v })}
                                        options={peopleOptions}
                                        searchable
                                        placeholder="Выберите сотрудника…"
                                        searchPlaceholder="Поиск по имени…"
                                        ariaLabel="Сотрудник"
                                        disabled={!!draft.editing}
                                    />
                                )}

                                {draft.subject_type === 'otp_role' && (
                                    <CustomSelect
                                        variant="ios"
                                        value={draft.subject_role}
                                        onChange={(v) => setDraft({ ...draft, subject_role: v })}
                                        options={(catalog.otp_role || []).map((r) => ({
                                            value: String(r.id), label: r.name,
                                        }))}
                                        ariaLabel="Роль в системе"
                                        disabled={!!draft.editing}
                                    />
                                )}

                                {!['otp_role', 'user'].includes(draft.subject_type) && (
                                    <CustomSelect
                                        variant="ios"
                                        value={draft.subject_id}
                                        onChange={(v) => setDraft({ ...draft, subject_id: v })}
                                        options={subjectOptions}
                                        searchable
                                        placeholder="Выберите…"
                                        ariaLabel="Субъект правила"
                                        // Тот же ключ правила, что у человека и у роли.
                                        // Здесь запрета не было, и «переставленный»
                                        // адресат заводил ВТОРОЕ правило, а первое
                                        // оставалось с прежними правами.
                                        disabled={!!draft.editing}
                                    />
                                )}
                            </div>

                            {draft.editing ? (
                                <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                                    Адресата у готового правила не меняют — заведите отдельное.
                                </p>
                            ) : draft.subject_type === 'otp_role' ? (
                                <p className="px-1 text-[11.5px] leading-relaxed text-amber-700">
                                    Роль не знает границ отдела: правило подействует
                                    во всей компании.
                                </p>
                            ) : draft.subject_type === 'user' && !peopleOptions.length ? (
                                <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                                    Открывать раздел отдельным людям вам пока некому.
                                </p>
                            ) : null}
                        </section>

                        <PermissionPicker
                            permissions={draft.permissions}
                            onChange={(permissions) => setDraft({ ...draft, permissions })}
                            mayGrant={mayGrant}
                            detailed={detailed}
                            onDetailed={setDetailed}
                        />

                        <ScopeCard
                            state={draft}
                            onChange={setDraft}
                            canManage={grantableStructure}
                        />
                    </div>
                )}

                {/* ── Список ──────────────────────────────────────────────── */}
                {!rowDraft && !draft && (
                    <div className="space-y-5">
                        {/* Чей это раздел: ветка отдела задаётся в форме раздела, здесь
                            она только показана — иначе непонятно, почему строки должностей
                            означают «в СЗоВ», а не «во всей компании».

                            Отдел на месте — это норма, и норме карточка не нужна: строка
                            под шапкой. Отдела нет — это риск открыть раздел всей компании,
                            и вот он предупреждением. Цвет только со смыслом. */}
                        {department ? (
                            <div className="flex items-center gap-2 px-1 text-[12px] text-slate-500">
                                <Building2 size={14} className="shrink-0 text-slate-400" />
                                <span className="truncate">
                                    Отдел ветки: {department.name}
                                    {' · '}
                                    {department.own
                                        ? 'задан у этого раздела'
                                        : `унаследован от «${department.sectionName}»`}
                                </span>
                            </div>
                        ) : (
                            <div className="flex items-start gap-2 rounded-2xl bg-amber-50 px-4 py-3 text-[12.5px] leading-relaxed text-amber-800">
                                <TriangleAlert size={15} className="mt-0.5 shrink-0" />
                                <span>
                                    <b>Отдел ветки не задан.</b> Права ниже получат сотрудники
                                    всей компании с такой должностью. Чтобы удержать границу
                                    отдела, укажите отдел у этого раздела или у ветки над ним —
                                    в форме «Изменить».
                                </span>
                            </div>
                        )}

                        {isPublic && (
                            <div className="flex items-start gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-[12.5px] leading-relaxed text-emerald-800">
                                <Globe size={15} className="mt-0.5 shrink-0" />
                                <span>
                                    Раздел публичный: читают его все сотрудники независимо от правил.
                                    Настройки ниже нужны только для прав на запись.
                                </span>
                            </div>
                        )}

                        <section className="space-y-1.5">
                            <div className="flex items-center gap-1.5 px-1">
                                <span className={iosGroupLabel}>
                                    {department ? 'Должности отдела' : 'Роли в системе'}
                                </span>
                                <IosHint
                                    label="Как работают должности"
                                    text={department
                                        ? 'Строки повторяют должности этой ветки. Доступ, выданный должности, автоматически есть и у всех, кто выше неё в отделе: руководитель видит всё, что видит подчинённый.'
                                        : 'Над разделом нет ветки отдела, поэтому писать не на что, кроме самой роли. Такое правило действует во всей компании.'}
                                />
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {loading ? (
                                    <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                                        <Loader2 size={16} className="animate-spin" />
                                        <span className="text-[13px]">Загружаем…</span>
                                    </div>
                                ) : failed ? (
                                    <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                                        <p className="text-[12.5px] leading-relaxed text-slate-500">
                                            Не удалось загрузить правила доступа.
                                        </p>
                                        <button type="button" className={iosBtnSecondary} onClick={loadRules}>
                                            <RotateCw size={14} /> Повторить
                                        </button>
                                    </div>
                                ) : rows.length === 0 ? (
                                    /* Ветка отдела есть, а разделов-должностей внутри
                                       неё нет: выдавать нечему. Подменять это ролями
                                       значило бы предложить «супервайзера маркетинга»,
                                       которого не существует. */
                                    <div className="px-4 py-8 text-center text-[12.5px] leading-relaxed text-slate-400">
                                        В этой ветке ещё нет разделов-должностей.
                                        <br />
                                        Заведите их во вкладке «Структура» — строки ниже
                                        повторяют дерево отдела.
                                    </div>
                                ) : rows.map((row) => {
                                    const state = matrix[row.key];
                                    if (!state) return null;
                                    const locked = isLocked(row);
                                    const granted = anyPermission(state.permissions);
                                    const meta = [
                                        typeof row.people === 'number'
                                            ? (row.people === 0 ? 'нет таких сотрудников' : peopleLabel(row.people))
                                            : null,
                                        row.hint,
                                        locked ? 'выдаёт вышестоящий' : null,
                                    ].filter(Boolean).join(' · ');
                                    return (
                                        <AccessRow
                                            key={row.key}
                                            title={row.label}
                                            meta={meta}
                                            notes={granted ? scopeNotes(state.permissions, state) : []}
                                            value={accessSummary(state.permissions)}
                                            muted={!granted}
                                            locked={locked}
                                            onOpen={() => openRow(row)}
                                        />
                                    );
                                })}
                            </div>
                        </section>

                        {/* Точечные правила: человек, группа, направление, роль вики.
                            Нужны редко — и раньше блок был свёрнут, из-за чего владелец
                            21.08.2026 искал выписанное им же правило и не находил.
                            Теперь строки те же, что у должностей, и места занимают
                            столько же: прятать стало нечего. */}
                        <section className="space-y-1.5">
                            <div className="flex items-center gap-1.5 px-1">
                                <span className={iosGroupLabel}>Точечные правила</span>
                                <IosHint
                                    label="Что это"
                                    text={grantDepartments
                                        ? 'Доступ мимо должностей: конкретному человеку, группе, направлению или главе отдела. Адресовать можно только своему отделу.'
                                        : 'Доступ мимо должностей: конкретному человеку, группе, направлению, главе отдела или роли.'}
                                />
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {extraRules.map((rule) => {
                                    const state = {
                                        grant_subsections: rule.grant_subsections,
                                        manage_subsections: rule.manage_subsections,
                                    };
                                    return (
                                        <AccessRow
                                            key={rule.id}
                                            title={rule.subject_label || rule.subject_role || `#${rule.subject_id}`}
                                            meta={[
                                                SUBJECT_KIND_LABEL[rule.subject_type] || rule.subject_type,
                                                // Должность сужает правило и заменяет порог: без
                                                // неё правило видеографа и правило таргетолога
                                                // подписаны здесь одинаково.
                                                rule.job_title
                                                    || (rule.min_role_level
                                                        ? ROLE_LEVEL_LABEL[rule.min_role_level] || rule.min_role_level
                                                        : null),
                                            ].filter(Boolean).join(' · ')}
                                            notes={scopeNotes(permissionsOf(rule), state)}
                                            value={accessSummary(permissionsOf(rule))}
                                            onOpen={() => openRule(rule)}
                                        />
                                    );
                                })}
                                {/* Кнопка — последней строкой списка, а не отдельным
                                    призраком под карточкой: так она читается как
                                    продолжение перечня, а не как украшение. */}
                                <button
                                    type="button"
                                    onClick={() => openRule(null)}
                                    className="flex w-full items-center gap-2 px-4 py-3 text-left text-[13.5px] font-medium text-blue-600 transition hover:bg-slate-50 active:bg-slate-100"
                                >
                                    <Plus size={16} className="shrink-0" />
                                    Добавить правило
                                </button>
                            </div>
                        </section>
                    </div>
                )}
            </div>
        </IosModal>
    );
}
