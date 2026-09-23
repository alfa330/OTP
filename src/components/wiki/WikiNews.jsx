import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Highlight from '@tiptap/extension-highlight';
import {
    AlertTriangle, Bold, Check, ChevronDown, ChevronLeft, ChevronRight, Download, Image as ImageIcon,
    Italic, Link2, List, ListChecks, ListOrdered, Loader2, Megaphone, PlayCircle, Plus,
    Search, Sparkles, Underline as UnderlineIcon, Users, X,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosHint, IosMenu, IosModal, IosSegmented, IosToggle,
} from '../ui/ios';
import { publishedLabel, roleTitle } from '../news/newsShared';
/* Результаты новости собраны из тех же кирпичей, что результаты опросов
   (просьба владельца 23.09.2026): плитки, распределение ответов и разбор
   попытки — один код на оба раздела. */
import {
    AttemptReview, Badge, OptionStatRow, StatTiles, scoreToneClass,
} from '../surveys/resultsKit';
import useIsMobileShell from '../common/useIsMobileShell';
import useScreenBackGesture from '../common/useScreenBackGesture';
import NewsFeed from '../news/NewsFeed';
import NewsGallery from '../news/NewsGallery';
import NewsQuizEditor from '../news/NewsQuizEditor';
import { quizForForm, quizProblem } from './questionQuiz';
/* Реестр тренажёров — без экранов и без React (см. шапку registry.js): форме
   нужны только названия, проигрыватель сюда не едет. */
import { TRAINER_CARDS } from './trainers/registry';
/* Клиентский конвейер берём готовым у «Посылок»: модуль ничего не импортирует
   и уже решает три вещи, которые пришлось бы решать заново и хуже — поворот из
   EXIF (иначе половина снимков с телефона ляжет боком), сторож зависшего
   toBlob (в части Android WebView колбэк не приходит никогда, и форма замерла
   бы навсегда) и правило «пережали, а стало тяжелее — значит испортили».
   Не берём оттуда только countIssue: там сказано «к одной посылке». */
import {
    PHOTO_ACCEPT, PHOTO_MAX_COUNT, pluralPhotos, preparePhoto,
} from '../parcels/parcelPhoto';
import '../news/news-modal.css';

/* Вкладка «Новости» — там, где новость ПИШУТ и где её перечитывают.
 *
 * Открыта ВСЕМ (решение владельца 17.09.2026, задача #342): «для людей с
 * правами чтения показывать только сами новости, которые ему были
 * предназначены; если у человека есть право на редактирование — они могут
 * редактировать и публиковать новости». Право редактирования — прежний потолок
 * публикации (супервайзер и выше, администратор вики): второй, новый признак
 * рядом с ним разошёлся бы с сервером молча. Читатель видит ленту NewsFeed,
 * редактор — управление и ту же ленту сегментом «Для меня».
 *
 * ПОЧЕМУ ДАННЫЕ НЕ ИЗ /api/wiki. Роуты вики стоят за тумблером отдела и
 * QR-подтверждением сессии, а новость обязана доехать и до тех, кто через эти
 * двери не проходит. Поэтому у раздела свой /api/news — вкладка лишь его
 * витрина, а не его граница.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Куда отправляют объявление. Подписи короткие: это названия двух окон, а не
   объяснение их устройства — объяснение живёт под «i» рядом с переключателем. */
const NEWS_CHANNELS = [
    { value: 'icore', label: 'iCORE' },
    { value: 'oktell', label: 'Oktell' },
];

const BUCKETS = [
    { value: 'published', label: 'Опубликованные' },
    /* Запланированные — своя корзина, а не подмножество черновиков: объявление,
       ждущее своего часа, и недописанный черновик — разные вещи. */
    { value: 'scheduled', label: 'Запланированные' },
    { value: 'draft', label: 'Черновики' },
    { value: 'archived', label: 'Архив' },
    /* Новости, адресованные самому редактору: сверху они приходят и ему. */
    { value: 'mine', label: 'Для меня' },
];

/* ТИП НОВОСТИ (ТЗ #300, п.5). Один вопрос автору — «насколько это важно» —
   вместо двух несвязанных тумблеров, которыми можно было собрать и
   бессмысленное («необязательная новость с обязательным тестом»), и опасное
   («критичное изменение, которое закрывают крестиком»).

   ПРАВИЛО ТО ЖЕ, ЧТО НА СЕРВЕРЕ (news/access.py: KIND_RULES), и их совпадение
   сверяет тест: разойдись они — форма показывала бы новость не такой, какой её
   записывает сервер. passRequired: null — «решает автор», это и есть
   «настраиваемая» строка таблицы ТЗ у важной новости. */
const NEWS_KINDS = [
    { value: 'info', label: 'Информационная',
      note: 'Окно можно закрыть крестиком, тест — по желанию' },
    { value: 'important', label: 'Важная',
      note: 'Окно закрывается только кнопкой «Прочитал»' },
    { value: 'critical', label: 'Критичная',
      note: 'Тест обязателен: пока он не сдан, окно не закрыть' },
];

const KIND_RULES = {
    info: { mandatory: false, passRequired: false, quizRequired: false },
    important: { mandatory: true, passRequired: null, quizRequired: false },
    critical: { mandatory: true, passRequired: true, quizRequired: true },
};

/* РЕЖИМ ЗАПУСКА (ТЗ #300, п.8). «Растянуть» существует ради одного: выпуск на
   весь отдел разом — это отдел, одновременно вышедший из очереди. */
const PUBLISH_MODES = [
    { value: 'now', label: 'Сразу' },
    { value: 'later', label: 'Отложить' },
    { value: 'spread', label: 'Растянуть' },
];

/* Периоды и интервалы — примеры из ТЗ. Пределы приходят от сервера
   (/api/news/access), и кнопка вне пределов просто не рисуется: предлагать
   значение, которое сервер отвергнет, нельзя. */
const SPREAD_PRESETS = [120, 240, 480, 1440];
const WAVE_INTERVAL_PRESETS = [5, 10, 15, 30];

const DEFAULT_SPREAD_MINUTES = 120;
const DEFAULT_WAVE_INTERVAL_MINUTES = 10;

/* Проходной балл: сто — «все ответы верны», как тест вёл себя до ТЗ #300. */
const PASS_SCORE_PRESETS = [100, 80, 60];

const kindRule = (kind) => KIND_RULES[kind] || KIND_RULES.important;

/* СОСТОЯНИЕ СОТРУДНИКА В ЖУРНАЛЕ (ТЗ #300, п.11.2). Считает его сервер
   (news/access.py: person_status) — одним правилом на экран, сводку и выгрузку
   в Excel. Здесь только подписи, и их совпадение с серверными сверяет тест:
   файл и экран обязаны называть одно состояние одним словом. */
const STATUS_LABELS = {
    passed: 'Успешно пройден',
    done: 'Ознакомление подтверждено',
    retrying: 'Проходит повторно',
    failed: 'Тест не пройден',
    pending: 'Ожидает ознакомления',
    absent: 'Нет смен после публикации',
    not_seen: 'Объявление не открыто',
};

/* Часы и минуты человеческими словами — для расчёта рассылки и подписи строки:
   «120 минут» автор пересчитывает в уме, «2 часа» он прочитал в своём же
   выборе. */
/* «09:00» у сегодняшнего момента и «22.09 09:00» у любого другого: расчёт
   читают прямо перед нажатием «Опубликовать», и дата в нём нужна ровно тогда,
   когда она не сегодняшняя. */
/* «снята сегодня, 09:40 · Зарина Алиева» — кто и когда остановил объявление
   (ТЗ #300, п.16). Имя — только если снял НЕ автор: автор уже стоит первым в
   той же строке, и «Руслан · … · снята · Руслан» говорило бы одно дважды.
   Без глагола в роде («снял/сняла») — по имени пол не угадывают. */
const takedownLabel = (post) => {
    const when = publishedLabel(post?.archived_at);
    if (!when) return '';
    const who = post.archived_by && post.archived_by !== post.author_id
        ? post.archived_by_name : '';
    return who ? `снята ${when} · ${who}` : `снята ${when}`;
};

const whenLabel = (iso) => {
    if (!iso) return '';
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    const time = at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    const today = new Date();
    const sameDay = at.getFullYear() === today.getFullYear()
        && at.getMonth() === today.getMonth()
        && at.getDate() === today.getDate();
    return sameDay ? time
        : `${at.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })} ${time}`;
};

const minutesLabel = (minutes) => {
    const value = Number(minutes) || 0;
    if (value < 60) return `${value} мин`;
    const hours = Math.floor(value / 60);
    const rest = value % 60;
    return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
};

/* Виды адресата в том порядке, в каком их выбирают: сначала «кому вообще»
   (отдел, направление, группа), потом поимённо. Должность стоит последней и
   доступна только тому, у кого нет границы отдела: правило на роль адресует
   людей по всей компании (см. wiki/access.py: COMPANY_WIDE_SUBJECTS). */
const SUBJECT_TABS = [
    { key: 'department', label: 'Отделы' },
    { key: 'direction', label: 'Направления' },
    { key: 'group', label: 'Группы' },
    { key: 'user', label: 'Люди' },
    { key: 'otp_role', label: 'Должности' },
];

const SUBJECT_TITLES = {
    department: 'отдел',
    direction: 'направление',
    group: 'группа',
    user: 'сотрудник',
    otp_role: 'должность',
};

const questionsWord = (count) => (
    count % 10 === 1 && count % 100 !== 11 ? 'вопрос'
        : ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100) ? 'вопроса' : 'вопросов')
);

const ruleKey = (rule) => `${rule.subject_type}:${rule.subject_id ?? rule.subject_role}`;

/* Подпись правила адресата. Живёт на уровне модуля, а не внутри формы: её
   одинаково спрашивают и строка «Кому» на главном экране, и сам экран выбора,
   а две копии подписали бы одно правило по-разному. */
const audienceRuleLabel = (rule, access) => {
    if (rule.subject_type === 'otp_role') {
        return roleTitle(rule.subject_role) || rule.subject_role;
    }
    if (rule.subject_name) return rule.subject_name;
    const pool = rule.subject_type === 'user'
        ? (access?.people || [])
        : (access?.subjects?.[rule.subject_type] || []);
    return pool.find((item) => item.id === rule.subject_id)?.name || '—';
};

/* Журнал нужен обязательной новости и новости, которую можно ПРОЙТИ: у
   необязательной с тестом или тренажёром подтверждений не ждут, а «кто прошёл»
   автору знать надо (задача #342). */
const hasJournal = (post) => !!post?.is_mandatory || (post?.quiz_count || 0) > 0 || !!post?.trainer_key;

/* Заготовки задержки. Числом в поле её тоже задают, но человек, ставящий
   объявление на смену, думает не в секундах, а «быстро / нормально / вдумчиво». */
const DELAY_PRESETS = [0, 10, 30, 60];

// ─────────────────────────────────────────────────────────────────────────────
// Выбор адресатов
// ─────────────────────────────────────────────────────────────────────────────

function AudienceScreen({ access, value, onChange, spaceName = '' }) {
    const [tab, setTab] = useState('department');
    const [query, setQuery] = useState('');

    const tabs = useMemo(() => SUBJECT_TABS.filter((item) => {
        if (item.key === 'user') return (access?.people || []).length > 0;
        if (item.key === 'otp_role') return (access?.roles || []).length > 0;
        return (access?.subjects?.[item.key] || []).length > 0;
    }), [access]);

    useEffect(() => {
        if (tabs.length && !tabs.some((t) => t.key === tab)) setTab(tabs[0].key);
    }, [tabs, tab]);

    const options = useMemo(() => {
        const text = query.trim().toLowerCase();
        const match = (name) => !text || String(name || '').toLowerCase().includes(text);
        if (tab === 'user') {
            return (access?.people || [])
                .filter((person) => match(person.name))
                .map((person) => ({
                    key: `user:${person.id}`,
                    rule: { subject_type: 'user', subject_id: person.id, subject_role: null },
                    title: person.name,
                    hint: [roleTitle(person.role), person.department_name].filter(Boolean).join(' · '),
                }));
        }
        if (tab === 'otp_role') {
            return (access?.roles || [])
                .filter((role) => match(roleTitle(role.code) || role.code))
                .map((role) => ({
                    key: `otp_role:${role.code}`,
                    rule: { subject_type: 'otp_role', subject_id: null, subject_role: role.code },
                    title: roleTitle(role.code) || role.code,
                    /* Подписи у строки нет намеренно: «все сотрудники этой
                       должности» повторялось бы в КАЖДОЙ строке вкладки и
                       давало стену серого, ничего не различая. Смысл сказан
                       один раз — в подсказке за «i» рядом с переключателем
                       вкладок. У вкладки «Люди» подпись, наоборот, осталась:
                       там она различает однофамильцев. */
                }));
        }
        return (access?.subjects?.[tab] || [])
            .filter((item) => match(item.name))
            .map((item) => ({
                key: `${tab}:${item.id}`,
                rule: { subject_type: tab, subject_id: item.id, subject_role: null },
                title: item.name,
                hint: SUBJECT_TITLES[tab],
            }));
    }, [access, query, tab]);

    const selected = useMemo(() => new Set((value || []).map(ruleKey)), [value]);

    const toggle = (option) => {
        if (selected.has(option.key)) {
            onChange((value || []).filter((rule) => ruleKey(rule) !== option.key));
        } else {
            onChange([...(value || []), option.rule]);
        }
    };

    return (
        <div className="space-y-3">
            {/* Отмеченные — первым делом и здесь же снимаются: экран отвечает
                на один вопрос «кому», и ответ на него должен быть виден сразу,
                а не за прокруткой списка. */}
            {value.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                    {value.map((rule) => (
                        <span key={ruleKey(rule)}
                              className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12px] text-slate-700">
                            {audienceRuleLabel(rule, access)}
                            <button
                                type="button"
                                onClick={() => onChange(value.filter(
                                    (item) => ruleKey(item) !== ruleKey(rule)))}
                                className="grid h-4 w-4 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                aria-label={`Убрать ${audienceRuleLabel(rule, access)}`}
                            >
                                <X className="h-3 w-3" aria-hidden="true" />
                            </button>
                        </span>
                    ))}
                </div>
            )}
            {/* ЕДИНСТВЕННОЕ пояснение раздела, оставшееся на виду, — и это не
                недоделка. Остальные объясняют то, что человек и так видит на
                экране; это — границу, которой на экране НЕТ: потолок
                audience_max_role_level режет уже выбранный отдел на выдаче.
                Автор публикует «отделу», журнал показывает знаменатель меньше
                состава отдела, и это читается как дефект данных. Прочитать надо
                ДО «Опубликовать», а подсказку за «i» читают после — то есть
                никогда. slate-500, а не slate-400: на белом slate-400 даёт
                около 2.8:1 при норме 4.5. */}
            <p className="text-[12px] text-slate-500">
                {value.length
                    ? 'Из выбранного новость увидят только те, кто ниже вас по должности'
                    : 'Новость увидят только те, кто ниже вас по должности'}
            </p>
            <div className="flex items-center gap-2">
                {tabs.length > 1 && (
                    <div className="min-w-0 flex-1">
                        <IosSegmented
                            value={tab}
                            options={tabs.map((item) => ({ value: item.key, label: item.label }))}
                            onChange={setTab}
                            stretch
                        />
                    </div>
                )}
                <span className="ml-auto shrink-0">
                    <IosHint
                        align="right"
                        label="Почему список сужен"
                        /* Пространство названо ПЕРВЫМ и по имени: отдел
                           соседней компании пропал из списка не потому, что
                           прав не хватило, а потому что вика другая, и без
                           этой строки человек ищет причину в своих правах. */
                        text={`В списке только те, кого вам можно адресовать: ${
                            spaceName ? `отделы пространства «${spaceName}»` : 'отделы вашего пространства'
                        }${
                            access?.bounded
                                ? ', ваш отдел и должности ниже вашей'
                                : ' и должности ниже вашей'
                        }. Выбрать должность целиком может лишь тот, у кого границы отдела нет: правило на должность адресует людей по всей компании — в этом пространстве.`}
                    />
                </span>
            </div>
            <input
                className={`${iosInput} mt-3`}
                placeholder="Поиск"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
            />
            <div className="mt-3 space-y-1">
                {options.length === 0 && (
                    <p className="px-1 py-6 text-center text-[13px] text-slate-400">Ничего не нашлось</p>
                )}
                {options.map((option) => {
                    const active = selected.has(option.key);
                    return (
                        <button
                            key={option.key}
                            type="button"
                            onClick={() => toggle(option)}
                            className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition active:scale-[0.99] ${
                                active ? 'bg-indigo-50' : 'hover:bg-slate-50'}`}
                        >
                            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1 ${
                                active ? 'bg-indigo-600 text-white ring-indigo-600' : 'ring-slate-300'}`}>
                                {active && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[14px] text-slate-900">{option.title}</span>
                                {option.hint && (
                                    <span className="block truncate text-[12px] text-slate-400">{option.hint}</span>
                                )}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Выбор тренажёра (задача #342)
// ─────────────────────────────────────────────────────────────────────────────

/* Список тех же тренажёров, что во вкладке «Тренажёры» вики, — из реестра, а не
   своим перечнем: сценарии живут в коде, и второй список отстал бы от первого
   на первом же новом тренажёре. Нажатие выбирает и возвращает назад — как выбор
   значения в «Настройках» iOS. */
function TrainerScreen({ value, onChange, onDone }) {
    return (
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {TRAINER_CARDS.map((trainer) => {
                    const active = trainer.key === value;
                    return (
                        <button
                            key={trainer.key}
                            type="button"
                            onClick={() => { onChange(trainer.key); onDone(); }}
                            className="flex w-full items-center gap-3 px-3.5 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100"
                        >
                            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                                <PlayCircle className="h-[18px] w-[18px]" aria-hidden="true" />
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[14px] font-medium text-slate-900">{trainer.title}</span>
                                {trainer.subtitle && (
                                    <span className="block truncate text-[12px] text-slate-500">{trainer.subtitle}</span>
                                )}
                            </span>
                            {active && <Check className="h-4 w-4 shrink-0 text-blue-600" strokeWidth={2.5} aria-hidden="true" />}
                        </button>
                    );
                })}
        </div>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Форма новости
// ─────────────────────────────────────────────────────────────────────────────

/* Строка настройки — пункт «Настроек» iOS: слева имя, справа текущее значение и
   шеврон. Значение справа ОБЯЗАТЕЛЬНО, и это не украшение: строка без него
   заставляет открыть экран только затем, чтобы вспомнить, что там выбрано, —
   то есть ровно тот лишний шаг, ради снятия которого настройки и свёрнуты. */
/* ЛИСТАЛКА ЗНАЧЕНИЙ прямо в строке — «как галерея фоток» (решение владельца
   21.09.2026): стрелки по краям, значение посередине, пролистал и выбрал.
   Для двух-трёх вариантов это короче отдельного экрана: ответ виден сразу и
   меняется одним нажатием, а не «открыть → выбрать → вернуться».
   Как в галерее, на краях листалка останавливается, а не заворачивается по
   кругу: «Информационная» после «Критичной» означала бы, что у списка нет ни
   начала, ни конца, и найти нужное можно только наугад.
   Пальцем листается тем же движением, что кадры, — стрелки на телефоне мелкие. */
function PickerRow({ label, value, options, onChange, disabled = false,
                    badge = null, ariaLabel, children = null }) {
    const index = Math.max(0, options.findIndex((item) => item.value === value));
    /* Откуда приезжает новое значение: вперёд — справа, назад — слева. Без
       этого движение читается как мигание, а не как перелистывание. */
    const [forward, setForward] = useState(true);
    const touchX = useRef(null);

    const step = (delta) => {
        const next = index + delta;
        if (disabled || next < 0 || next >= options.length) return;
        setForward(delta > 0);
        onChange(options[next].value);
    };

    const arrow = (delta, Icon, title) => (
        <button
            type="button"
            onClick={() => step(delta)}
            disabled={index + delta < 0 || index + delta >= options.length}
            aria-label={title}
            /* Стрелки на сером кружке, а не плоские: рядом в той же карточке
               стоит шеврон «открыть экран», и два одинаковых значка означали бы
               два разных действия. Кружок читается как кнопка. */
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-90 disabled:pointer-events-none disabled:opacity-30"
        >
            <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
    );

    return (
        <div className="px-3.5 py-3">
            <div className="flex items-center gap-3">
                <span className="shrink-0 text-[14px] text-slate-900">{label}</span>
                <div
                    className="ml-auto flex shrink-0 items-center gap-0.5"
                    role="group"
                    aria-label={ariaLabel || label}
                    onTouchStart={(e) => { touchX.current = e.touches[0]?.clientX ?? null; }}
                    onTouchEnd={(e) => {
                        if (touchX.current === null) return;
                        const shift = (e.changedTouches[0]?.clientX ?? 0) - touchX.current;
                        touchX.current = null;
                        if (Math.abs(shift) > 36) step(shift < 0 ? 1 : -1);
                    }}
                >
                    {!disabled && arrow(-1, ChevronLeft, 'Предыдущее значение')}
                    {/* key по значению — чтобы анимация проигрывалась на КАЖДОМ
                        переключении, а не один раз за жизнь строки. */}
                    <span
                        key={options[index]?.value}
                        aria-live="polite"
                        className={`min-w-[7.5rem] truncate text-center text-[13px] font-medium text-slate-900 ${
                            disabled ? '' : (forward ? 'animate-push-in' : 'animate-pop-in')}`}
                    >
                        {options[index]?.label}
                    </span>
                    {!disabled && arrow(1, ChevronRight, 'Следующее значение')}
                </div>
                {badge}
            </div>
            {children}
        </div>
    );
}

function SettingRow({ label, value, muted = false, badge = null, onOpen }) {
    return (
        <button
            type="button"
            onClick={onOpen}
            className="flex w-full items-center gap-3 px-3.5 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100"
        >
            <span className="shrink-0 text-[14px] text-slate-900">{label}</span>
            <span className={`ml-auto min-w-0 truncate text-[13px] ${
                muted ? 'text-slate-400' : 'text-slate-500'}`}>
                {value}
            </span>
            {badge}
            <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" aria-hidden="true" />
        </button>
    );
}

function NewsForm({ open, post, access, onClose, onSave, saving, apiBaseUrl, headers,
                   spaceId = null, spaceName = '' }) {
    const [title, setTitle] = useState('');
    /* Тип решает и за обязательность, и за обязательность прохождения (ТЗ #300,
       п.5): отдельного тумблера «обязательно к прочтению» в форме больше нет —
       он спрашивал бы то же самое во второй раз. */
    const [kind, setKind] = useState('important');
    const [delay, setDelay] = useState(access?.default_confirm_delay_seconds ?? 10);
    const [expires, setExpires] = useState('');
    const [audience, setAudience] = useState([]);
    const [error, setError] = useState('');
    /* Какой экран формы открыт. Второй уровень живёт ВНУТРИ того же окна:
       окно поверх окна — это два окна об одной новости, и «закрыть» у них
       означает разное. Стек не нужен: у каждого экрана ровно один родитель. */
    const [screen, setScreen] = useState('main');
    const [anim, setAnim] = useState('');
    /* Кадры: [{ key, id, url, localUrl, busy, failed }].
       id и url появляются, когда файл доехал до сервера; localUrl живёт до
       этого момента и показывает плитку сразу после выбора — иначе человек
       десять секунд смотрит на пустое место и жмёт «добавить» второй раз. */
    const [photos, setPhotos] = useState([]);
    const [photoError, setPhotoError] = useState('');
    const [preview, setPreview] = useState(false);
    /* Тест в окне новости: пустой список — теста нет. Составляют руками или
       кнопкой «Составить ИИ» по заголовку и тексту; правила те же, что у сервера
       (questionQuiz.js ↔ news/access.py: normalize_quiz). У опубликованной
       новости тест не меняется: часть отдела уже ответила на эти вопросы, и
       подменённый тест сделал бы журнал «Кто прочитал» журналом другой новости. */
    const [quiz, setQuiz] = useState([]);
    const [quizBusy, setQuizBusy] = useState(false);
    const [quizNotes, setQuizNotes] = useState([]);
    /* Тренажёр и обязательность прохождения (задача #342). Один тумблер на тест
       и тренажёр — постановка говорит об одной настройке обязательности. */
    const [trainerKey, setTrainerKey] = useState(null);
    const [passRequired, setPassRequired] = useState(true);
    /* Проходной результат теста (ТЗ #300, п.4). Сто — прежнее поведение: любая
       ошибка не засчитывает попытку. */
    const [passScore, setPassScore] = useState(100);
    /* Режим запуска и растяжка (ТЗ #300, п.8). */
    const [publishMode, setPublishMode] = useState('now');
    const [scheduledAt, setScheduledAt] = useState('');
    const [spreadMinutes, setSpreadMinutes] = useState(DEFAULT_SPREAD_MINUTES);
    const [waveInterval, setWaveInterval] = useState(DEFAULT_WAVE_INTERVAL_MINUTES);
    /* Предварительный расчёт рассылки (ТЗ п.8.4). Считает СЕРВЕР теми же
       функциями, что и настоящий выпуск: вторая формула в форме обещала бы
       одно, а происходило бы другое. */
    const [spreadPreview, setSpreadPreview] = useState(null);
    /* Куда отправить объявление (решение владельца 18.09.2026): 'icore' — окно
       портала, 'oktell' — окно поверх клиента АТС, которое рисует «Ограничитель
       Перезвона». Какие каналы вообще есть в этом пространстве, говорит сервер
       (news/routes.py: _channels_for_space) — в «Тез» программы нет, и выбора
       там не показываем: единственный вариант это не выбор, а лишний вопрос. */
    const [channel, setChannel] = useState(NEWS_CHANNELS[0].value);
    const channels = access?.channels || [NEWS_CHANNELS[0].value];
    const channelOptions = NEWS_CHANNELS.filter((item) => channels.includes(item.value));
    /* Кто из отмеченных не увидит объявление в Oktell: в АТС без SIP-номера не
       работают, и окно поверх клиента такому человеку показать некому. Считает
       сервер теми же правилами адресата, что и журнал, — вторая формула тут
       предупреждала бы про одних, а объявление уходило бы другим. */
    const [sipCheck, setSipCheck] = useState(null);
    const [sipOpen, setSipOpen] = useState(false);
    const sipMissing = Number(sipCheck?.missing_count) || 0;
    // Единственный, у кого нет номера: его имя уходит в заголовок панели.
    const sipOnly = sipMissing === 1 ? (sipCheck?.missing || [])[0] : null;
    // По published_at, как на сервере: снятая с показа новость ответы уже собрала.
    // Тем же замком запираются тренажёр и обязательность (NEWS_PASS_LOCKED).
    const quizLocked = !!post?.published_at;
    /* Канал — под тем же замком и по той же причине (NEWS_CHANNEL_LOCKED):
       объявление уже показано людям там, куда его отправили. */
    const channelLocked = quizLocked;
    const trainerCard = TRAINER_CARDS.find((item) => item.key === trainerKey) || null;
    const push = (next) => { setAnim('animate-push-in'); setScreen(next); };
    /* Назад — всегда к родителю: у тренажёра это «Тест и тренажёр», у
       остальных главный экран. */
    const back = () => {
        setAnim('animate-pop-in');
        setScreen(screen === 'trainer' ? 'passes' : 'main');
    };
    /* Что навязывает тип — тем же правилом, что на сервере (kind_flags). */
    const rule = kindRule(kind);
    const mandatory = rule.mandatory;
    const passEffective = rule.passRequired === null ? passRequired : rule.passRequired;
    /* Держит ли прохождение подтверждение — то же правило, что на сервере
       (news/access.py: must_pass). Держит — новость обязательна всегда. */
    const mustPass = passEffective && (quiz.length > 0 || !!trainerKey);
    /* Планировщик рисуется, только когда он развёрнут: предлагать отложенный
       запуск, который сервер отвергнет, — обещание, которое он не выполнит. */
    const planReady = access?.plan_ready !== false;
    /* Значения в строках настроек. Каждая отвечает на свой вопрос одним
       коротким словом — иначе строка заставляет открыть экран только затем,
       чтобы вспомнить выбранное. */
    const kindLabel = (NEWS_KINDS.find((item) => item.value === kind) || NEWS_KINDS[1]).label;
    const channelLabel = (NEWS_CHANNELS.find((item) => item.value === channel)
                          || NEWS_CHANNELS[0]).label;
    const audienceSummary = audience.length === 0
        ? 'Не выбрано'
        : (audience.length === 1
            ? audienceRuleLabel(audience[0], access)
            : `${audienceRuleLabel(audience[0], access)} и ещё ${audience.length - 1}`);
    const passesSummary = [
        // «Тест · 2» человек читает как «тест номер два»: число тут — вопросы,
        // и сказать это надо словом.
        quiz.length ? `${quiz.length} ${questionsWord(quiz.length)}` : '',
        trainerKey ? 'тренажёр' : '',
    ].filter(Boolean).join(' · ') || 'Нет';
    const scheduleSummary = [
        (PUBLISH_MODES.find((item) => item.value === publishMode) || PUBLISH_MODES[0]).label,
        mandatory ? `кнопка через ${delay} с` : '',
    ].filter(Boolean).join(' · ');
    const spreadChoices = SPREAD_PRESETS.filter(
        (value) => value >= (access?.min_spread_minutes ?? 5)
            && value <= (access?.max_spread_minutes ?? 1440));
    const waveChoices = WAVE_INTERVAL_PRESETS.filter(
        (value) => value >= (access?.min_wave_interval_minutes ?? 5)
            && value <= spreadMinutes);
    // Что отдали в URL.createObjectURL — освобождаем при закрытии формы, иначе
    // байты кадров висят в памяти вкладки до перезагрузки страницы.
    const localUrls = useRef([]);

    const editor = useEditor({
        extensions: [
            // Заголовки до третьего уровня: объявление на экран — не статья,
            // и пятый уровень вложенности в нём означал бы, что это не новость.
            StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
            Underline,
            Link.configure({ openOnClick: false, autolink: true }),
            Highlight,
        ],
        content: '',
        editorProps: {
            attributes: {
                class: 'news-body min-h-[120px] focus:outline-none',
            },
        },
    }, []);

    /* Форма заполняется при ОТКРЫТИИ, а не на каждый рендер: правка в поле не
       должна затираться тем же объектом post, приехавшим из списка заново. */
    useEffect(() => {
        if (!open) return;
        setTitle(post?.title || '');
        setKind(post?.kind || 'important');
        setDelay(post ? post.confirm_delay_seconds : (access?.default_confirm_delay_seconds ?? 10));
        setExpires(post?.expires_at ? String(post.expires_at).slice(0, 16) : '');
        setAudience((post?.audience || []).map((rule) => ({
            subject_type: rule.subject_type,
            subject_id: rule.subject_id,
            subject_role: rule.subject_role,
            subject_name: rule.subject_name,
        })));
        setError('');
        setPhotoError('');
        setPreview(false);
        setQuiz(post?.quiz?.length ? quizForForm(post.quiz) : []);
        setQuizNotes([]);
        setQuizBusy(false);
        setTrainerKey(post?.trainer_key || null);
        setPassRequired(post ? post.pass_required !== false : true);
        setPassScore(post?.pass_score_percent || 100);
        setPublishMode(post?.publish_mode || 'now');
        setScheduledAt(post?.scheduled_at ? String(post.scheduled_at).slice(0, 16) : '');
        setSpreadMinutes(post?.spread_minutes || DEFAULT_SPREAD_MINUTES);
        setWaveInterval(post?.wave_interval_minutes || DEFAULT_WAVE_INTERVAL_MINUTES);
        setSpreadPreview(null);
        setChannel(post?.channel || NEWS_CHANNELS[0].value);
        setSipCheck(null);
        setSipOpen(false);
        // Открываем всегда с главного: форма, начатая на третьем экране, —
        // это форма, у которой человек не видел ни заголовка, ни текста.
        setScreen('main');
        setAnim('');
        // Уже прикреплённые кадры приезжают с карточкой готовыми адресами.
        setPhotos((post?.photos || []).map((photo) => ({
            key: `id:${photo.id}`, id: photo.id, url: photo.url,
        })));
        editor?.commands.setContent(post?.body || '');
    }, [open, post, access, editor]);

    /* Проверка SIP-номеров — ТОЛЬКО когда выбран Oktell и есть кому адресовать.
       В портале номер ни при чём, и запрос «на всякий случай» на каждый щелчок
       по справочнику адресата был бы обращением к базе ради ответа, который
       никто не спросит.

       Пауза в 400 мс: адресатов набирают подряд, по одному, и без неё каждый
       выбранный отдел стоил бы своего запроса. Отменяем прошлый ответ флагом —
       иначе медленный первый придёт после быстрого второго и покажет список от
       уже снятого адресата. */
    const audienceKey = audience.map((rule) => `${rule.subject_type}:${rule.subject_id
        || rule.subject_role || ''}`).join(',');
    useEffect(() => {
        if (!open || channel !== 'oktell' || !audienceKey) { setSipCheck(null); return undefined; }
        let alive = true;
        setSipCheck((prev) => ({ ...(prev || {}), loading: true }));
        const timer = setTimeout(() => {
            axios.post(`${apiBaseUrl}/api/news/audience/oktell`, {
                space_id: spaceId,
                audience: audience.map((rule) => ({
                    subject_type: rule.subject_type,
                    subject_id: rule.subject_id,
                    subject_role: rule.subject_role,
                })),
            }, { headers })
                .then((r) => { if (alive) setSipCheck({ ...r.data, loading: false }); })
                /* Молча: проверка — подсказка, а не условие публикации, и
                   красная строка про неудавшийся запрос отвлекала бы от
                   собственно новости. */
                .catch(() => { if (alive) setSipCheck(null); });
        }, 400);
        return () => { alive = false; clearTimeout(timer); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, channel, audienceKey, apiBaseUrl, headers, spaceId]);

    // Предупреждение исчезло — исчезает и раскрытая панель под ним.
    useEffect(() => {
        if (!sipCheck?.missing_count) setSipOpen(false);
    }, [sipCheck?.missing_count]);

    /* Расчёт рассылки — ТОЛЬКО у растяжки и только когда есть кому адресовать
       (ТЗ п.8.4). Пауза и отмена прошлого ответа — те же, что у проверки
       SIP-номеров: адресатов набирают подряд, а медленный первый ответ,
       пришедший после быстрого второго, показал бы расчёт для уже снятого
       отдела. */
    useEffect(() => {
        if (!open || publishMode !== 'spread' || !audienceKey) {
            setSpreadPreview(null);
            return undefined;
        }
        let alive = true;
        const timer = setTimeout(() => {
            axios.post(`${apiBaseUrl}/api/news/audience/preview`, {
                space_id: spaceId,
                scheduled_at: scheduledAt || null,
                spread_minutes: spreadMinutes,
                wave_interval_minutes: waveInterval,
                audience: audience.map((item) => ({
                    subject_type: item.subject_type,
                    subject_id: item.subject_id,
                    subject_role: item.subject_role,
                })),
            }, { headers })
                .then((r) => { if (alive) setSpreadPreview(r.data); })
                /* Молча: расчёт — подсказка перед запуском, а не условие
                   публикации. */
                .catch(() => { if (alive) setSpreadPreview(null); });
        }, 400);
        return () => { alive = false; clearTimeout(timer); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, publishMode, audienceKey, scheduledAt, spreadMinutes, waveInterval,
        apiBaseUrl, headers, spaceId]);

    /* «Составить ИИ» — по тому, что уже написано в форме. Ответ модели ложится
       в тот же редактор: проверить и поправить его человек обязан сам, выпускает
       тест кнопка «Опубликовать», а не модель. */
    const draftQuiz = async () => {
        if (quizBusy) return;
        if (!editor?.getText().trim()) {
            setError('Сначала напишите текст новости — тест составляется по нему');
            return;
        }
        setQuizBusy(true);
        setError('');
        try {
            const { data } = await axios.post(`${apiBaseUrl}/api/news/quiz/draft`,
                                              { title, body: editor.getHTML() }, { headers });
            setQuiz(quizForForm(data?.quiz));
            setQuizNotes(data?.warnings || []);
        } catch (e) {
            setError(errText(e, 'ИИ не составил тест — добавьте вопросы вручную'));
        } finally {
            setQuizBusy(false);
        }
    };

    // Закрыли форму — отпускаем локальные адреса кадров.
    useEffect(() => {
        if (open) return undefined;
        localUrls.current.forEach((url) => URL.revokeObjectURL(url));
        localUrls.current = [];
        return undefined;
    }, [open]);

    /* Кадр уезжает на сервер СРАЗУ при выборе, а не на «Опубликовать».
       Причина не в удобстве: черновика, к которому можно было бы прикрепить
       файл, ещё не существует — сервер отвергает новость без адресатов, — а
       десять снимков с телефона на кнопке «Опубликовать» означали бы полминуты
       заблокированной формы. Поэтому кадр грузится «ничьим», а привязывается
       вместе с сохранением. */
    const addFiles = useCallback(async (files) => {
        const incoming = [...(files || [])];
        if (!incoming.length) return;
        setPhotoError('');
        /* Счёт места — ОДИН раз на всю пачку и ДО цикла: счётчик из замыкания
           рендера не растёт по ходу пачки, и двадцать брошенных файлов прошли
           бы все двадцать. */
        const already = photos.length;
        const free = Math.max(0, PHOTO_MAX_COUNT - already);
        if (incoming.length > free) {
            setPhotoError(free
                ? `Поместится ещё ${free} ${pluralPhotos(free)} — остальные не добавлены`
                : `К одной новости можно прикрепить не больше ${PHOTO_MAX_COUNT} фотографий`);
        }
        if (!free) return;

        for (const file of incoming.slice(0, free)) {
            // eslint-disable-next-line no-await-in-loop
            const ready = await preparePhoto(file);
            if (!ready.ok) {
                setPhotoError(`${file.name || 'Файл'}: ${ready.issue}`);
                continue;
            }
            const localUrl = URL.createObjectURL(ready.blob);
            localUrls.current.push(localUrl);
            const key = `new:${file.name || 'photo'}:${file.size}:${file.lastModified || ''}`;
            setPhotos((prev) => [...prev, { key, localUrl, busy: true }]);

            const form = new FormData();
            form.append('file', ready.blob, ready.name || 'photo.webp');
            try {
                // eslint-disable-next-line no-await-in-loop
                const answer = await axios.post(`${apiBaseUrl}/api/news/photos`, form, { headers });
                const saved = answer.data?.photo || {};
                setPhotos((prev) => prev.map((item) => (item.key === key
                    ? { ...item, id: saved.id, url: saved.url, busy: false } : item)));
            } catch (e) {
                setPhotoError(errText(e, 'Фотография не загрузилась'));
                setPhotos((prev) => prev.map((item) => (item.key === key
                    ? { ...item, busy: false, failed: true } : item)));
            }
        }
    }, [apiBaseUrl, headers, photos.length]);

    const dropPhoto = useCallback((photo) => {
        setPhotos((prev) => prev.filter((item) => item.key !== photo.key));
        setPhotoError('');
        if (photo.localUrl) {
            URL.revokeObjectURL(photo.localUrl);
            localUrls.current = localUrls.current.filter((url) => url !== photo.localUrl);
        }
        // Уехавший кадр снимаем и с сервера: иначе он останется «ничьим» и
        // будет занимать место в потолке до самой уборки.
        if (photo.id) {
            axios.delete(`${apiBaseUrl}/api/news/photos/${photo.id}`, { headers })
                .catch(() => { /* не доехало — уберёт уборка брошенных */ });
        }
    }, [apiBaseUrl, headers]);

    // Порядок здесь и есть порядок показа, поэтому переставлять надо уметь.
    const movePhoto = useCallback((index, step) => {
        setPhotos((prev) => {
            const next = [...prev];
            const to = index + step;
            if (to < 0 || to >= next.length) return prev;
            [next[index], next[to]] = [next[to], next[index]];
            return next;
        });
    }, []);

    const submit = (publish) => {
        const text = title.trim();
        if (!text) { setError('Укажите заголовок'); return; }
        // ДО остальных проверок: сохранив сейчас, автор выпустил бы объявление
        // без тех кадров, которые ещё едут, — и второй раз окно не всплывёт.
        if (photos.some((photo) => photo.busy)) {
            setError('Дождитесь загрузки фотографий'); return;
        }
        if (!audience.length) { setError('Укажите, кому адресована новость'); return; }
        const body = editor?.getHTML() || '';
        // Пустой абзац TipTap отдаёт как <p></p> — для проверки «текст есть»
        // это ничем не отличается от пустого поля.
        if (!editor?.getText().trim()) { setError('Напишите текст новости'); return; }
        if (quizBusy) { setError('Дождитесь, пока ИИ составит тест'); return; }
        const quizIssue = quiz.length && !quizLocked ? quizProblem(quiz) : null;
        if (quizIssue) { setError(quizIssue); return; }
        /* Те же отказы, что у сервера, — до запроса: критичная новость без
           теста блокировать работу «до успешного прохождения» не может, а
           отложенный запуск без времени не запуск. */
        if (rule.quizRequired && !quiz.length && !quizLocked) {
            setError('Критичная новость выпускается с тестом: добавьте вопросы '
                     + 'или выберите тип «Важная»');
            return;
        }
        if (publish && publishMode === 'later' && !scheduledAt) {
            setError('Укажите дату и время запуска'); return;
        }
        if (publish && publishMode !== 'now' && scheduledAt
            && new Date(scheduledAt).getTime() <= Date.now()) {
            setError('Время запуска уже прошло — укажите будущее время'); return;
        }
        setError('');
        onSave({
            title: text,
            body,
            kind,
            publish_mode: publishMode,
            scheduled_at: publishMode === 'now' ? null : (scheduledAt || null),
            spread_minutes: publishMode === 'spread' ? spreadMinutes : null,
            wave_interval_minutes: publishMode === 'spread' ? waveInterval : null,
            is_mandatory: mandatory || mustPass,
            // Тест опубликованной новости не отправляется вовсе: сервер его не
            // меняет (NEWS_QUIZ_LOCKED), а пустой список читался бы как «убрать».
            // Тренажёр и обязательность — под тем же замком (NEWS_PASS_LOCKED).
            ...(quizLocked ? {} : {
                quiz: quiz.map(({ prompt, options, correct }) => ({ prompt, options, correct })),
                trainer_key: trainerKey,
                pass_required: passEffective,
                pass_score_percent: passScore,
            }),
            confirm_delay_seconds: Number(delay) || 0,
            expires_at: expires || null,
            /* Канал опубликованной новости сервер не меняет (NEWS_CHANNEL_LOCKED),
               но прислать его надо и тогда: он у неё тот же, и отказ будет
               только у настоящей попытки перенести объявление. */
            channel,
            audience: audience.map((rule) => ({
                subject_type: rule.subject_type,
                subject_id: rule.subject_id,
                subject_role: rule.subject_role,
            })),
            // Порядок массива и есть порядок показа в карусели. Кадры, которые
            // не доехали, не отправляем: сервер их всё равно не знает.
            photos: photos.filter((photo) => photo.id).map((photo) => photo.id),
            publish,
        });
    };

    const toolbarButton = (active, onClick, Icon, label) => (
        <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={onClick}
            aria-label={label}
            title={label}
            className={`grid h-8 w-8 place-items-center rounded-lg transition active:scale-95 ${
                active ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}
        >
            <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
    );

    /* Заголовок шапки — имя текущего экрана: на втором уровне «Новая новость»
       не отвечает на вопрос «что я сейчас настраиваю». */
    const SCREEN_TITLES = {
        main: post?.id ? 'Новость' : 'Новая новость',
        audience: 'Кому',
        passes: 'Тест и тренажёр',
        trainer: 'Тренажёр к новости',
        schedule: 'Публикация и показ',
    };

    return (
            <IosModal
                open={open}
                /* Крестик и клик мимо окна на втором уровне возвращают на шаг
                   назад, а не закрывают форму: промах мышью по затемнению не
                   должен стоить набранного текста. */
                onClose={screen === 'main' ? onClose : back}
                onBack={screen === 'main' ? null : back}
                title={SCREEN_TITLES[screen]}
                /* Подзаголовка нет: у опубликованной там стояло «правка не
                   сбрасывает подтверждения», и IosModal резал эту строку
                   посередине (subtitle рисуется с truncate). Сказать её надо
                   один раз и в том месте, где решение принимают, — у кнопки
                   «Сохранить», за «i». */
                maxWidth="max-w-2xl"
                /* Кнопки — только на главном экране. На втором их нет намеренно:
                   значение применяется сразу, как в «Настройках», и «Готово»
                   рядом с шевроном «назад» было бы второй кнопкой того же
                   действия. */
                footer={screen !== 'main' ? null : (
                    <>
                        {error && <span className="mr-auto text-[12px] text-rose-600">{error}</span>}
                        {post?.status === 'published' && (
                            <IosHint
                                align="right"
                                label="Что будет с подтверждениями"
                                text="Те, кто уже подтвердил эту новость, второй раз её не увидят: правка текста подтверждения не сбрасывает. Нужно спросить заново — опубликуйте новую новость."
                            />
                        )}
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                        {post?.status !== 'published' && (
                            <button type="button" className={iosBtnSecondary}
                                    disabled={saving} onClick={() => submit(false)}>
                                В черновики
                            </button>
                        )}
                        <button type="button" className={iosBtnPrimary}
                                disabled={saving} onClick={() => submit(true)}>
                            {saving && <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />}
                            {post?.status === 'published' ? 'Сохранить' : 'Опубликовать'}
                        </button>
                    </>
                )}
            >
                {/* ЭКРАНЫ ВНУТРИ ОДНОГО ОКНА, а не окно поверх окна.
                    Форма отвечает на восемь вопросов сразу, и вываленные в один
                    свиток они давали полтора экрана прокрутки и семь значков «i»
                    на первом же взгляде. Открытым остаётся только то, ради чего
                    новость и пишут, — заголовок, текст и кадры; остальное свёрнуто
                    в строки со значением справа, как в «Настройках».
                    Второй уровень открывается ТУТ ЖЕ, сдвигом, и возвращается
                    шевроном в шапке: окно поверх окна — это два окна об одной
                    новости, и закрытие одного из них попадает не туда. */}
                <div key={screen} className={anim}>
                    {screen === 'main' && (
                        <>
                            <label className={iosGroupLabel}>Заголовок</label>
                            <input
                                className={iosInput}
                                value={title}
                                onChange={(e) => setTitle(e.target.value)}
                                placeholder="Например: Акция «Приведи друга» — во всех таксопарках"
                                maxLength={255}
                            />

                            <label className={`${iosGroupLabel} mt-4`}>Текст</label>
                            <div className={`${iosCard} overflow-hidden`}>
                                <div className="flex flex-wrap items-center gap-0.5 border-b border-slate-100 px-2 py-1.5">
                                    {toolbarButton(editor?.isActive('bold'),
                                        () => editor?.chain().focus().toggleBold().run(), Bold, 'Полужирный')}
                                    {toolbarButton(editor?.isActive('italic'),
                                        () => editor?.chain().focus().toggleItalic().run(), Italic, 'Курсив')}
                                    {toolbarButton(editor?.isActive('underline'),
                                        () => editor?.chain().focus().toggleUnderline().run(), UnderlineIcon, 'Подчёркнутый')}
                                    <span className="mx-1 h-5 w-px bg-slate-200" />
                                    {toolbarButton(editor?.isActive('bulletList'),
                                        () => editor?.chain().focus().toggleBulletList().run(), List, 'Список')}
                                    {toolbarButton(editor?.isActive('orderedList'),
                                        () => editor?.chain().focus().toggleOrderedList().run(), ListOrdered, 'Нумерованный список')}
                                    <span className="mx-1 h-5 w-px bg-slate-200" />
                                    {toolbarButton(editor?.isActive('link'), () => {
                                        const previous = editor?.getAttributes('link')?.href || '';
                                        const href = window.prompt('Адрес ссылки', previous);
                                        if (href === null) return;
                                        if (!href) { editor?.chain().focus().unsetLink().run(); return; }
                                        editor?.chain().focus().extendMarkRange('link')
                                            .setLink({ href, target: '_blank' }).run();
                                    }, Link2, 'Ссылка')}
                                </div>
                                <div className="px-3.5 py-3">
                                    <EditorContent editor={editor} />
                                </div>
                            </div>

                            <div className="mt-4 flex items-center justify-between px-1">
                                <span className={`${iosGroupLabel} flex items-center gap-2 px-0`}>
                                    Фотографии
                                    <IosHint
                                        label="Что будет с фотографиями"
                                        text="Снимки уменьшаются и переводятся в WebP прямо в браузере — уходит десятая часть исходного веса. Сотрудник видит их каруселью над текстом новости и листает пальцем, стрелками или трекпадом. Порядок здесь и есть порядок показа: первый кадр он увидит первым."
                                    />
                                </span>
                                <span className="text-[12px] tabular-nums text-slate-400">
                                    {photos.length} / {PHOTO_MAX_COUNT}
                                </span>
                            </div>
                            <div className={`${iosCard} p-2`}>
                                {/* Плитки мельче, чем были: на пустой форме ряд из
                                    трёх крупных квадратов занимал треть экрана ради
                                    одной кнопки «+». Десять кадров в два ряда по
                                    шесть читаются так же, а места просят вдвое
                                    меньше. */}
                                {/* Ширина колонки задана СТИЛЕМ, а не классом
                                    grid-cols-*: мобильная оболочка схлопывает
                                    такие сетки в две колонки (это правило про
                                    плитки показателей), и «фотоплёнка» на
                                    телефоне превращалась в два квадрата по
                                    167 px — треть экрана ради одной кнопки «+». */}
                                <div
                                    className="grid gap-2"
                                    style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(88px, 1fr))' }}
                                >
                                    {photos.map((photo, index) => (
                                        <div
                                            key={photo.key}
                                            className={`relative aspect-square overflow-hidden rounded-xl bg-slate-100 ring-1 ${
                                                photo.failed ? 'ring-rose-300' : 'ring-slate-200/70'}`}
                                        >
                                            <img
                                                src={photo.localUrl || photo.url}
                                                alt=""
                                                loading="lazy"
                                                decoding="async"
                                                className="h-full w-full object-cover"
                                            />
                                            {photo.busy && (
                                                <span className="absolute inset-0 grid place-items-center bg-white/60">
                                                    <Loader2 className="h-4 w-4 animate-spin text-slate-500" aria-hidden="true" />
                                                </span>
                                            )}
                                            {/* Номер = место в карусели: он же отвечает на
                                                вопрос «что сотрудник увидит первым». */}
                                            <span className="absolute left-1 top-1 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-slate-900/55 px-1 text-[10.5px] tabular-nums text-white backdrop-blur">
                                                {index + 1}
                                            </span>
                                            {/* Крестик виден ВСЕГДА, а не по наведению: на
                                                телефоне наведения нет, и спрятанная там
                                                кнопка просто не существует. */}
                                            <button
                                                type="button"
                                                onClick={() => dropPhoto(photo)}
                                                aria-label={`Убрать фотографию ${index + 1}`}
                                                className="absolute right-1 top-1 grid h-[22px] w-[22px] place-items-center rounded-full bg-slate-900/55 text-white backdrop-blur transition active:scale-95"
                                            >
                                                <X className="h-3 w-3" aria-hidden="true" />
                                            </button>
                                            <div className="absolute inset-x-1 bottom-1 flex justify-between">
                                                <button
                                                    type="button"
                                                    disabled={index === 0}
                                                    onClick={() => movePhoto(index, -1)}
                                                    aria-label={`Переставить фотографию ${index + 1} левее`}
                                                    className="grid h-[22px] w-[22px] place-items-center rounded-full bg-slate-900/55 text-[13px] leading-none text-white backdrop-blur transition active:scale-95 disabled:opacity-0"
                                                >
                                                    ‹
                                                </button>
                                                <button
                                                    type="button"
                                                    disabled={index === photos.length - 1}
                                                    onClick={() => movePhoto(index, 1)}
                                                    aria-label={`Переставить фотографию ${index + 1} правее`}
                                                    className="grid h-[22px] w-[22px] place-items-center rounded-full bg-slate-900/55 text-[13px] leading-none text-white backdrop-blur transition active:scale-95 disabled:opacity-0"
                                                >
                                                    ›
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                    {photos.length < PHOTO_MAX_COUNT && (
                                        <label className="grid aspect-square cursor-pointer place-items-center rounded-xl border border-dashed border-slate-300 text-slate-400 transition hover:border-slate-400 hover:bg-slate-50">
                                            <Plus className="h-4 w-4" aria-hidden="true" />
                                            <input
                                                type="file"
                                                accept={PHOTO_ACCEPT}
                                                multiple
                                                className="hidden"
                                                onChange={(e) => {
                                                    addFiles(Array.from(e.target.files || []));
                                                    // Сбрасываем, иначе повторный выбор того
                                                    // же файла не поднимет событие.
                                                    e.target.value = '';
                                                }}
                                            />
                                        </label>
                                    )}
                                </div>
                                {photoError && <p className="mt-2 px-1 text-[12px] text-rose-600">{photoError}</p>}
                                {photos.filter((photo) => photo.url).length > 1 && (
                                    <button
                                        type="button"
                                        onClick={() => setPreview((value) => !value)}
                                        className="mt-2 px-1 text-[12px] text-indigo-600 transition hover:underline"
                                    >
                                        {preview ? 'Скрыть предпросмотр' : 'Как увидит сотрудник'}
                                    </button>
                                )}
                                {/* Плитка КРОПАЕТ (квадрат, object-cover), а карусель нет:
                                    вертикальный плакат в сетке выглядит нормально, а в окне
                                    встанет узкой полосой. Поэтому предпросмотр рисует ТОТ ЖЕ
                                    компонент, что и окно сотрудника, из того же CSS — один
                                    компонент, одна правда. */}
                                {preview && (
                                    <div className="mt-2">
                                        <NewsGallery photos={photos.filter((photo) => photo.url)} />
                                    </div>
                                )}
                            </div>

                            <div className={`${iosCard} mt-4 divide-y divide-slate-100`}>
                                {/* Тип листается прямо здесь: вариантов три, и
                                    отдельный экран ради них был бы шагом туда и
                                    обратно. У выпущенной новости стрелок нет —
                                    тип заперт сервером (NEWS_KIND_LOCKED), и
                                    показывать переключатель, который только
                                    откажет, хуже, чем не показывать его. */}
                                <PickerRow
                                    label="Тип"
                                    value={kind}
                                    options={NEWS_KINDS.map(({ value, label }) => ({ value, label }))}
                                    onChange={setKind}
                                    disabled={quizLocked}
                                    ariaLabel="Тип новости"
                                >
                                    {/* Одна строка под выбором — что он меняет. Это не
                                        пересказ кнопки: сам по себе «Важная» не говорит,
                                        чем она важнее. Остальное про тип — за «i». */}
                                    <p className="mt-2 flex items-start gap-1.5 text-[12px] text-slate-500">
                                        <span className="min-w-0">
                                            {(NEWS_KINDS.find((item) => item.value === kind) || NEWS_KINDS[1]).note}
                                        </span>
                                        <IosHint
                                            label="Чем различаются типы"
                                            text="Информационная — окно закрывается крестиком, подтверждение и тест по желанию. Важная — окно закрывается только кнопкой «Прочитал», тест прикрепляется по желанию. Критичная — тест обязателен, и пока он не сдан, окно не закрыть и работу продолжить нельзя. Тип опубликованной новости не меняется."
                                        />
                                    </p>
                                </PickerRow>
                                <SettingRow label="Кому" value={audienceSummary}
                                            muted={!audience.length}
                                            onOpen={() => push('audience')} />
                                {/* Строки нет вовсе, когда канал один: в «Тез»
                                    программы Oktell нет, и листалка с единственным
                                    значением была бы вопросом без выбора. */}
                                {channelOptions.length > 1 && (
                                    <PickerRow
                                        label="Куда отправить"
                                        value={channel}
                                        options={channelOptions}
                                        onChange={setChannel}
                                        disabled={channelLocked}
                                        ariaLabel="Куда отправить объявление"
                                        /* Предупреждение — РЯДОМ с листалкой, а не
                                           строкой под карточкой: оно относится к
                                           одному значению «Oktell», и отдельная
                                           красная строка внизу читалась бы как
                                           ошибка всей формы. Само число уже ответ:
                                           «троим не дойдёт». */
                                        badge={sipMissing > 0 ? (
                                            <button
                                                type="button"
                                                onClick={() => setSipOpen((value) => !value)}
                                                aria-expanded={sipOpen}
                                                aria-label="Кто не увидит объявление в Oktell"
                                                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-amber-50 pl-2.5 pr-2 text-[12.5px] font-semibold text-amber-700 ring-1 ring-amber-200 transition hover:bg-amber-100 active:scale-[0.97]"
                                            >
                                                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                                                <span className="tabular-nums">{sipMissing}</span>
                                                <ChevronDown
                                                    className={`h-3.5 w-3.5 transition-transform duration-300 ${
                                                        sipOpen ? 'rotate-180' : ''}`}
                                                    aria-hidden="true"
                                                />
                                            </button>
                                        ) : null}
                                    >
                                        <p className="mt-2 flex items-start gap-1.5 text-[12px] text-slate-500">
                                            <span className="min-w-0">
                                                {channel === 'oktell'
                                                    ? 'Окно поверх клиента АТС — только тем, у кого он установлен'
                                                    : 'Окно «Новость дня» в портале — всем, кому адресована'}
                                            </span>
                                            <IosHint
                                                label="Чем iCORE отличается от Oktell"
                                                text="iCORE — окно «Новость дня» в портале: его видит каждый, кому новость адресована. Oktell — окно поверх клиента АТС, его рисует программа «Ограничитель Перезвона» и на время чтения снимает оператора с линии; увидят только те, у кого программа установлена. Необязательное объявление в клиент АТС не уходит вовсе. У опубликованной новости канал не меняется."
                                            />
                                        </p>
                                    {/* Раскрытие СЕТКОЙ 0fr → 1fr, а не max-height с числом:
                                        высота считается по содержимому, поэтому список из трёх
                                        имён и из тридцати раскрываются одинаково плавно и без
                                        рывка в конце, каким заканчивается всякий подобранный
                                        на глаз максимум. */}
                                    <div
                                        className={`grid transition-all duration-300 ease-out ${
                                            sipOpen ? 'mt-2.5 grid-rows-[1fr] opacity-100'
                                                    : 'grid-rows-[0fr] opacity-0'}`}
                                    >
                                        <div className="overflow-hidden">
                                            <div className="rounded-2xl bg-amber-50/70 px-3.5 py-3 ring-1 ring-amber-200/70">
                                                {/* У ОДНОГО человека имя стоит прямо в заголовке, а
                                                    списка нет вовсе: перечень из одной строки под
                                                    фразой «без номера — 1 из 24» повторял бы сам себя.
                                                    Дальше сразу инструкция — за ней сюда и пришли. */}
                                                <p className="text-[13px] font-semibold text-amber-900">
                                                    {sipOnly
                                                        ? `Без SIP-номера — ${sipOnly.name}`
                                                        : `Без SIP-номера — ${sipMissing} из ${sipCheck?.checked || 0}`}
                                                    {sipOnly && (
                                                        <span className="font-normal text-amber-900/60">
                                                            {' · '}
                                                            {[roleTitle(sipOnly.role), sipOnly.department_name]
                                                                .filter(Boolean).join(', ')}
                                                        </span>
                                                    )}
                                                </p>
                                                <p className="mt-0.5 text-[12px] leading-snug text-amber-900/70">
                                                    Объявление в Oktell показывает программа поверх клиента
                                                    АТС — тот, кто в АТС не заведён, его не увидит.
                                                </p>
                                                {/* Имена — списком с прокруткой: отдел из сорока
                                                    человек иначе вытолкнул бы кнопки формы за экран. */}
                                                {sipMissing > 1 && (
                                                    <ul className="mt-2 max-h-44 space-y-1 overflow-y-auto pr-1">
                                                        {(sipCheck?.missing || []).map((person) => (
                                                            <li key={person.user_id}
                                                                className="flex items-baseline justify-between gap-3 text-[12.5px]">
                                                                <span className="min-w-0 truncate text-amber-900">
                                                                    {person.name}
                                                                </span>
                                                                <span className="shrink-0 text-amber-900/60">
                                                                    {[roleTitle(person.role), person.department_name]
                                                                        .filter(Boolean).join(' · ')}
                                                                </span>
                                                            </li>
                                                        ))}
                                                    </ul>
                                                )}
                                                {sipMissing > (sipCheck?.missing || []).length && (
                                                    <p className="mt-1 text-[12px] text-amber-900/60">
                                                        …и ещё {sipMissing - (sipCheck?.missing || []).length}
                                                    </p>
                                                )}
                                                {/* Инструкция — шагами в том порядке, в каком их делают,
                                                    и названиями, которые человек увидит на экране. */}
                                                <div className="mt-2.5 border-t border-amber-200/70 pt-2 text-[12px] leading-relaxed text-amber-900/80">
                                                    <p className="font-medium text-amber-900">Как добавить номер</p>
                                                    <ol className="mt-1 list-decimal space-y-0.5 pl-4">
                                                        <li>Меню слева → «Настройки SIP».</li>
                                                        <li>Выберите отдел сотрудника и найдите его в списке.</li>
                                                        <li>Впишите номер в поле «SIP-номер» и нажмите «Сохранить».</li>
                                                    </ol>
                                                    <p className="mt-1.5">
                                                        Номер появится у человека сразу — новость можно
                                                        отправлять в Oktell после этого.
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    </PickerRow>
                                )}
                                <SettingRow label="Тест и тренажёр" value={passesSummary}
                                            muted={!quiz.length && !trainerKey}
                                            onOpen={() => push('passes')} />
                                <SettingRow label="Публикация и показ" value={scheduleSummary}
                                            onOpen={() => push('schedule')} />
                            </div>
                        </>
                    )}

                    {screen === 'audience' && (
                        <AudienceScreen
                            access={access}
                            value={audience}
                            onChange={setAudience}
                            spaceName={spaceName}
                        />
                    )}

                    {screen === 'passes' && (
                        <>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className={`${iosGroupLabel} flex items-center gap-1.5 px-0`}>
                                    Что прикрепить
                                    <IosHint
                                        label="Что дают тест и тренажёр"
                                        text="Тест — 2–3 вопроса под текстом новости: ошибка подсвечивается у вопроса, правильный ответ не подсказывается. Тренажёр — урок из вкладки «Тренажёры», сотрудник открывает его прямо из новости. Кто что прошёл, видно в журнале «Кто прочитал»."
                                    />
                                </span>
                                {quiz.length > 0 && !quizLocked && (
                                    <span className="flex flex-wrap items-center gap-1">
                                        <button type="button" onClick={draftQuiz} disabled={quizBusy}
                                                className={`${iosBtnGhost} !py-1 text-[12.5px]`}>
                                            {quizBusy
                                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                                                : <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
                                            {quizBusy ? 'Составляем…' : 'Составить заново'}
                                        </button>
                                        <button type="button" disabled={quizBusy}
                                                onClick={() => { setQuiz([]); setQuizNotes([]); }}
                                                className={`${iosBtnGhost} !py-1 text-[12.5px]`}>
                                            Убрать тест
                                        </button>
                                    </span>
                                )}
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                <div className="p-3">
                                    {quiz.length === 0 ? (
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <p className="flex items-center gap-2.5 text-[14px] text-slate-900">
                                                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                                                    <ListChecks className="h-[18px] w-[18px]" aria-hidden="true" />
                                                </span>
                                                {quizLocked ? 'Теста нет' : 'Тест'}
                                            </p>
                                            {!quizLocked && (
                                                <span className="flex flex-wrap gap-2">
                                                    <button type="button" className={iosBtnSecondary} disabled={quizBusy}
                                                            onClick={() => setQuiz(quizForForm([]))}>
                                                        <Plus className="mr-1 inline h-4 w-4" aria-hidden="true" />
                                                        Добавить вопросы
                                                    </button>
                                                    <button type="button" className={iosBtnSecondary} disabled={quizBusy}
                                                            onClick={draftQuiz}>
                                                        {quizBusy
                                                            ? <Loader2 className="mr-1 inline h-4 w-4 animate-spin" aria-hidden="true" />
                                                            : <Sparkles className="mr-1 inline h-4 w-4" aria-hidden="true" />}
                                                        {quizBusy ? 'Составляем…' : 'Составить ИИ'}
                                                    </button>
                                                </span>
                                            )}
                                        </div>
                                    ) : (
                                        <>
                                            {quizLocked && (
                                                <p className="mb-2 text-[12px] text-slate-500">
                                                    Тест опубликованной новости не меняется: часть отдела уже ответила
                                                </p>
                                            )}
                                            <NewsQuizEditor quiz={quiz} onChange={setQuiz} disabled={quizBusy || quizLocked} />
                                            {quizNotes.length > 0 && (
                                                <ul className="mt-2 space-y-1 rounded-xl bg-amber-50 px-3 py-2 text-[12px] leading-relaxed text-amber-900 ring-1 ring-amber-200/70">
                                                    {quizNotes.map((note) => <li key={note}>{note}</li>)}
                                                </ul>
                                            )}
                                                    </>
                                                )}
                                            </div>

                                            {/* Тренажёр — строкой, как пункт «Настроек»: иконка, название,
                                                действие справа. Описание урока здесь не нужно — автор
                                                выбирает из знакомого списка вкладки «Тренажёры». */}
                                            <div className="flex items-center gap-2.5 px-3 py-3">
                                                <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${
                                                    trainerKey ? 'bg-indigo-50 text-indigo-600' : 'bg-slate-100 text-slate-500'}`}>
                                                    <PlayCircle className="h-[18px] w-[18px]" aria-hidden="true" />
                                                </span>
                                                <span className="min-w-0 flex-1">
                                                    <span className="block truncate text-[14px] text-slate-900">
                                                        {trainerKey ? (trainerCard?.title || 'Тренажёр недоступен') : (quizLocked ? 'Тренажёра нет' : 'Тренажёр')}
                                                    </span>
                                                    {trainerCard?.subtitle && (
                                                        <span className="block truncate text-[12px] text-slate-500">{trainerCard.subtitle}</span>
                                                    )}
                                                </span>
                                                {!quizLocked && (trainerKey ? (
                                                    <span className="flex shrink-0 items-center gap-1">
                                                        <button type="button" onClick={() => push('trainer')}
                                                                className={`${iosBtnGhost} !py-1 text-[12.5px]`}>
                                                            Сменить
                                                        </button>
                                                        <button type="button" onClick={() => setTrainerKey(null)}
                                                                aria-label="Убрать тренажёр" title="Убрать тренажёр"
                                                                className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-rose-500 active:scale-95">
                                                            <X className="h-4 w-4" aria-hidden="true" />
                                                        </button>
                                                    </span>
                                                ) : (
                                                    <button type="button" className={iosBtnSecondary}
                                                            onClick={() => push('trainer')}>
                                                        <Plus className="mr-1 inline h-4 w-4" aria-hidden="true" />
                                                        Выбрать
                                                    </button>
                                                ))}
                                            </div>

                                            {/* Проходной балл — только у теста: у тренажёра баллов нет.
                                                Процент, как в ТЗ, но подпись считает его в вопросах —
                                                «нужно 4 из 5» автор проверяет глазами, а 80% ему
                                                пришлось бы делить в уме (ТЗ #300, п.4). */}
                                            {quiz.length > 0 && (
                                                <div className="px-3.5 py-3">
                                                    <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                                        Проходной результат
                                                        <IosHint
                                                            label="Зачем проходной результат"
                                                            text="Сколько ответов надо взять верно, чтобы тест был засчитан. Сто процентов — прежнее правило: ошибка не засчитывает попытку целиком. Для критичных изменений так и оставляют. Число попыток не ограничено, но каждая видна в журнале."
                                                        />
                                                    </p>
                                                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                        {PASS_SCORE_PRESETS.map((preset) => (
                                                            <button
                                                                key={preset}
                                                                type="button"
                                                                disabled={quizLocked}
                                                                onClick={() => setPassScore(preset)}
                                                                className={`rounded-full px-3 py-1 text-[12px] tabular-nums transition active:scale-[0.98] disabled:opacity-50 ${
                                                                    Number(passScore) === preset
                                                                        ? 'bg-slate-900 text-white'
                                                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                                            >
                                                                {preset} %
                                                            </button>
                                                        ))}
                                                        <span className="text-[12px] tabular-nums text-slate-500">
                                                            нужно верных: {Math.max(1, Math.ceil(quiz.length * passScore / 100))}
                                                            {' из '}{quiz.length}
                                                        </span>
                                                    </div>
                                                </div>
                                            )}

                                            {/* Тумблер — только когда есть что проходить И когда выбор
                                                вообще за автором: у информационной и критичной новости
                                                обязательность прохождения решает тип, и тумблер рядом
                                                спрашивал бы о том, что уже решено. */}
                                            {(quiz.length > 0 || trainerKey) && rule.passRequired === null && (
                                                <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                                                    <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                                        Пройти обязательно
                                                        <IosHint
                                                            label="Что значит «пройти обязательно»"
                                                            text="Включено — сотрудник не подтвердит новость и не закроет окно, пока не пройдёт тест и тренажёр. Выключено — пройти можно по желанию: в окне новости или позже во вкладке «Новости», а прочитать и закрыть новость — и без этого."
                                                        />
                                                    </p>
                                                    <IosToggle checked={passRequired} onChange={setPassRequired} disabled={quizLocked} />
                                                </div>
                                            )}
                                        </div>
                        </>
                    )}

                    {screen === 'trainer' && (
                        <TrainerScreen
                            value={trainerKey}
                            onChange={setTrainerKey}
                            onDone={() => back()}
                        />
                    )}

                    {screen === 'schedule' && (
                        <>
                            {/* Сначала КОГДА выпустить, потом КАК показывать: второе
                                имеет смысл только после первого, и обратный порядок
                                заставлял бы возвращаться вверх. */}
                            {/* ── КОГДА ОПУБЛИКОВАТЬ (ТЗ #300, п.8) ──────────────────────
                                Опубликованной новости этой части нет вовсе — планировать
                                в ней уже нечего. */}
                            {planReady && post?.status !== 'published' && (
                                <>
                                    <label className={iosGroupLabel}>Когда опубликовать</label>
                                    <div className={`${iosCard} divide-y divide-slate-100`}>
                                        <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-3">
                                            <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                                Запуск
                                                <IosHint
                                                    label="Зачем откладывать и растягивать"
                                                    text="Обязательное объявление снимает человека с линии, поэтому выпуск на весь отдел разом — это отдел, одновременно вышедший из очереди. «Отложить» запускает новость в указанное время, «Растянуть» делит адресатов на волны и включает их порциями. До своей волны сотрудник работает как обычно."
                                                />
                                            </p>
                                            <IosSegmented
                                                value={publishMode}
                                                options={PUBLISH_MODES}
                                                onChange={setPublishMode}
                                                ariaLabel="Режим запуска"
                                            />
                                        </div>

                                        {publishMode !== 'now' && (
                                            <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-3">
                                                <div className="min-w-0">
                                                    <p className="text-[14px] text-slate-900">Начать</p>
                                                    {/* У растяжки поле можно оставить пустым — это
                                                        «сразу», и так прямо написано: пустое поле
                                                        без подписи читается как незаполненное. */}
                                                    <p className="text-[12px] text-slate-500">
                                                        {publishMode === 'spread'
                                                            ? 'Пусто — начать сразу после публикации'
                                                            : 'До этого времени новость никого не потревожит'}
                                                    </p>
                                                </div>
                                                <input
                                                    type="datetime-local"
                                                    value={scheduledAt}
                                                    onChange={(e) => setScheduledAt(e.target.value)}
                                                    aria-label="Дата и время запуска"
                                                    className="rounded-xl bg-slate-100 px-3 py-1.5 text-[13px] text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                                />
                                            </div>
                                        )}

                                        {publishMode === 'spread' && (
                                            <div className="px-3.5 py-3">
                                                <p className="text-[14px] text-slate-900">Распределить за</p>
                                                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                    {spreadChoices.map((value) => (
                                                        <button
                                                            key={value}
                                                            type="button"
                                                            onClick={() => setSpreadMinutes(value)}
                                                            className={`rounded-full px-3 py-1 text-[12px] tabular-nums transition active:scale-[0.98] ${
                                                                Number(spreadMinutes) === value
                                                                    ? 'bg-slate-900 text-white'
                                                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                                        >
                                                            {minutesLabel(value)}
                                                        </button>
                                                    ))}
                                                </div>
                                                <p className="mt-3 text-[14px] text-slate-900">Интервал между волнами</p>
                                                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                    {waveChoices.map((value) => (
                                                        <button
                                                            key={value}
                                                            type="button"
                                                            onClick={() => setWaveInterval(value)}
                                                            className={`rounded-full px-3 py-1 text-[12px] tabular-nums transition active:scale-[0.98] ${
                                                                Number(waveInterval) === value
                                                                    ? 'bg-slate-900 text-white'
                                                                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                                        >
                                                            {minutesLabel(value)}
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                        )}

                                        {/* РАСЧЁТ ПЕРЕД ЗАПУСКОМ (ТЗ п.8.4): «позволит оценить
                                            влияние обязательной новости на доступность
                                            операторов до её запуска». Считает сервер теми же
                                            функциями, что и сам выпуск. */}
                                        {publishMode === 'spread' && spreadPreview && (
                                            <div className="px-3.5 py-3">
                                                {/* Существительное перед числом — чтобы не
                                                    склонять его под каждое значение: «волн 12»
                                                    и «волн 1» одинаково верны, а «12 волн» и
                                                    «1 волна» требовали бы разбора числа ради
                                                    одной строки. */}
                                                <p className="text-[13px] text-slate-900">
                                                    Получателей{' '}
                                                    <span className="font-semibold tabular-nums">
                                                        {spreadPreview.recipients}
                                                    </span>
                                                    {' · волн '}
                                                    <span className="tabular-nums">{spreadPreview.waves}</span>
                                                    {' · в волне ≈'}
                                                    <span className="tabular-nums">{spreadPreview.per_wave}</span>
                                                </p>
                                                <p className="mt-0.5 text-[12px] tabular-nums text-slate-500">
                                                    {whenLabel(spreadPreview.starts_at)}
                                                    {' → '}
                                                    {whenLabel(spreadPreview.ends_at)}
                                                    {', шаг '}{minutesLabel(spreadPreview.interval_minutes)}
                                                </p>
                                            </div>
                                        )}
                                    </div>
                                </>
                            )}

                            <label className={`${iosGroupLabel} mt-4`}>Как показывать</label>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {/* Тумблера «обязательно к прочтению» здесь больше нет: на
                                    этот вопрос отвечает ТИП новости (ТЗ #300, п.5), и два
                                    места для одного ответа разошлись бы в первый же день.
                                    Задержка нужна только обязательной: у необязательной
                                    кнопки «Прочитал» нет вовсе, и поле рядом с ней было бы
                                    настройкой того, чего не существует. */}
                                {mandatory && (
                                    <div className="px-3.5 py-3">
                                        <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                            Задержка кнопки «Прочитал»
                                            <IosHint
                                                label="Зачем задержка"
                                                text="Пока идёт задержка, кнопка «Прочитал» неактивна — чтобы объявление не закрыли, не читая. Отсчёт ведёт сервер с того момента, как окно открылось у сотрудника. Больше десяти минут поставить нельзя."
                                            />
                                        </p>
                                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                            {DELAY_PRESETS.map((preset) => (
                                                <button
                                                    key={preset}
                                                    type="button"
                                                    onClick={() => setDelay(preset)}
                                                    className={`rounded-full px-3 py-1 text-[12px] tabular-nums transition active:scale-[0.98] ${
                                                        Number(delay) === preset
                                                            ? 'bg-slate-900 text-white'
                                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                                >
                                                    {preset === 0 ? 'сразу' : `${preset} с`}
                                                </button>
                                            ))}
                                            <input
                                                type="number"
                                                min={0}
                                                max={600}
                                                value={delay}
                                                /* Режем здесь же: сервер всё равно приведёт
                                                   значение к потолку, и «1000» в поле рядом
                                                   с подписью «загорится через 1000 с» было
                                                   бы обещанием, которого он не выполнит. */
                                                onChange={(e) => setDelay(Math.max(0, Math.min(600,
                                                    Number(e.target.value) || 0)))}
                                                className="w-20 rounded-xl bg-slate-100 px-3 py-1 text-[12px] tabular-nums text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                                aria-label="Задержка в секундах"
                                            />
                                        </div>
                                        {/* Строки «загорится через N секунд» здесь больше
                                            нет: она пересказывала нажатый чип и число в
                                            соседнем поле — то есть третий раз повторяла
                                            одно и то же значение. */}
                                    </div>
                                )}
                                <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                                    <div className="min-w-0">
                                        <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                            Показывать до
                                            <IosHint
                                                label="Что значит срок показа"
                                                text="Дата — момент, когда окно перестанет всплывать, даже если человек его не подтвердил. Оставите пусто — окно показывается до подтверждения, но не дольше 14 дней с публикации: иначе вышедший из отпуска получил бы подряд все объявления за год. Журнал «Кто прочитал» не обрезается ни в том, ни в другом случае."
                                            />
                                        </p>
                                        {/* Строку не спрятали, а ИСПРАВИЛИ: «пусто — пока
                                            не подтвердят» было неправдой. Показ снимает
                                            ещё и горизонт SHOW_HORIZON_DAYS = 14 дней
                                            (news/queries.py), про который форма молчала.
                                            Прятать неправду под «i» нельзя — её надо
                                            убрать. */}
                                        <p className="text-[12px] text-slate-500">
                                            Пусто — до подтверждения, максимум 14 дней
                                        </p>
                                    </div>
                                    <input
                                        type="datetime-local"
                                        value={expires}
                                        onChange={(e) => setExpires(e.target.value)}
                                        className="rounded-xl bg-slate-100 px-3 py-1.5 text-[13px] text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                    />
                                </div>
                            </div>
                        </>
                    )}
                </div>
            </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Журнал прочтений
// ─────────────────────────────────────────────────────────────────────────────

/* ─── РЕЗУЛЬТАТЫ НОВОСТИ ────────────────────────────────────────────────────
 *
 * Просьба владельца (23.09.2026): «о результатах при наличии теста и прочтении
 * сделать примерно как в разделе "Опросы", переиспользовать». Поэтому экран
 * повторяет результаты опроса и собран из ТЕХ ЖЕ кирпичей
 * (surveys/resultsKit.jsx): плитки итогов, карточки сотрудников, разбор
 * попытки со всеми вариантами и распределение ответов по вопросу. Человек,
 * который смотрит результаты опросов, читает этот экран без привыкания.
 *
 * Отличия от опросов — только там, где их требует сама новость:
 *   * у новости без теста вкладок нет — только люди; вторая вкладка с пустой
 *     статистикой была бы вопросом без ответа;
 *   * у теста новости бывают ПОВТОРНЫЕ попытки (неверный ответ снимает весь
 *     выбор), поэтому в разборе можно переключать попытки, а статистика по
 *     вопросам считается по первой — см. news/queries.py: question_stats.
 */

// Цвет плашки состояния: янтарный — застрял на тесте, это требует действия;
// остальное нейтрально. Успешных плашкой не метим: это норма, а не событие.
const STATUS_BADGE_COLOR = {
    retrying: 'amber', failed: 'amber', pending: 'gray', absent: 'gray', not_seen: 'gray',
};

// «2 попытки», «5 попыток» — считается у каждой карточки, склонять обязательно.
const attemptsLabel = (count) => {
    const n = Math.abs(Number(count) || 0);
    const last = n % 10;
    const lastTwo = n % 100;
    if (last === 1 && lastTwo !== 11) return `${n} попытка`;
    if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return `${n} попытки`;
    return `${n} попыток`;
};

const shortTime = (iso) => {
    if (!iso) return '';
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    return at.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit',
                                        hour: '2-digit', minute: '2-digit' });
};

/* Какие номера попыток показать: все, пока их не больше семи, дальше —
   «1 … 6 7 8 … 22». Первая и последняя видны всегда: первая говорит, понятен
   ли текст, последняя — чем кончилось. Ячеек всегда не больше семи, поэтому
   ширина ряда от числа попыток не зависит. null — многоточие. */
const ATTEMPT_SLOTS = 7;
const attemptSlots = (count, current) => {
    const all = Array.from({ length: count }, (_, i) => i);
    if (count <= ATTEMPT_SLOTS) return all;
    if (current <= 3) return [0, 1, 2, 3, 4, null, count - 1];
    if (current >= count - 4) return [0, null, ...all.slice(count - 5)];
    return [0, null, current - 1, current, current + 1, null, count - 1];
};

/* Переключатель попыток в разборе сотрудника. Вид — IosSegmented, но свой:
   сегментному контролу нечем нарисовать многоточие. Двадцать две попытки
   сегментами в ряд уезжали за край окна, и выбранная — последняя — оказывалась
   как раз за краем (#300, 23.09.2026). */
function AttemptSwitcher({ attempts, value, onChange }) {
    const current = Math.max(0, attempts.findIndex((item) => item.attempt_no === value));
    /* Фокус идёт за выбранной попыткой. Иначе после клика по «1» и листания
       стрелками рамка фокуса оставалась на «1», а выбрана была «10» — два
       выделенных номера в одном ряду. Ловим, только если фокус уже в ряду:
       отбирать его у остального окна незачем. */
    const listRef = useRef(null);
    useEffect(() => {
        const list = listRef.current;
        if (!list || !list.contains(document.activeElement)) return;
        list.querySelector('[aria-selected="true"]')?.focus();
    }, [value]);
    return (
        <div className="flex items-center gap-2.5">
            <span className="shrink-0 text-[12.5px] text-slate-500">Попытка</span>
            <div ref={listRef} role="tablist" aria-label="Попытка"
                 className="inline-flex min-w-0 items-center rounded-[10px] bg-slate-100 p-[3px]">
                {attemptSlots(attempts.length, current).map((slot, position) => {
                    if (slot === null) {
                        return (
                            <span key={`gap-${position}`} aria-hidden="true"
                                  className="px-1 text-[12.5px] text-slate-400">…</span>
                        );
                    }
                    const item = attempts[slot];
                    const active = slot === current;
                    return (
                        <button
                            key={item.attempt_no}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            aria-label={`Попытка ${item.attempt_no} из ${attempts.length}`}
                            onClick={() => onChange(item.attempt_no)}
                            className={`min-w-[2.25rem] rounded-[8px] px-2.5 py-[5px] text-[12.5px] font-medium tabular-nums transition-all active:scale-[0.98] ${
                                active
                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]'
                                    : 'text-slate-500 hover:text-slate-800'
                            }`}
                        >
                            {item.attempt_no}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

function NewsReport({ open, post, apiBaseUrl, headers, onClose }) {
    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(false);
    const [tab, setTab] = useState('people');
    const [query, setQuery] = useState('');
    const [onlyAttention, setOnlyAttention] = useState(false);
    const [exporting, setExporting] = useState(false);
    /* Второй уровень окна — разбор одного сотрудника, как «Ответы» в опросах.
       Не второе окно: IosModal умеет шеврон «назад» внутри себя. */
    const [person, setPerson] = useState(null);
    const [personData, setPersonData] = useState(null);
    const [attemptNo, setAttemptNo] = useState(null);

    useEffect(() => {
        if (!open || !post?.id) { setState(null); return; }
        setLoading(true);
        setTab('people');
        setQuery('');
        setOnlyAttention(false);
        setPerson(null);
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}/report`, { headers })
            .then((r) => setState(r.data))
            .catch(() => setState(null))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, open, post?.id]);

    /* Что у новости есть проходить (задача #342): от этого зависят плитки,
       вкладки и то, открывается ли карточка сотрудника. */
    const hasQuiz = (post?.quiz_count || 0) > 0;
    const hasTrainer = !!post?.trainer_key;

    const people = useMemo(() => {
        const text = query.trim().toLowerCase();
        return (state?.items || []).filter((row) => (
            (!text || String(row.name || '').toLowerCase().includes(text))
            /* «Требуют внимания» — от кого ещё чего-то ждут: не подтвердил ИЛИ
               не сдал тест. Выбывшего из адресатов не показываем: дожимать его
               незачем. */
            && (!onlyAttention || (row.in_audience && !['passed', 'done'].includes(row.status)))
        ));
    }, [state, query, onlyAttention]);

    const openPerson = (row) => {
        if (!hasQuiz || !row.attempts) return;
        setPerson(row);
        setPersonData(null);
        setAttemptNo(null);
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}/attempts/${row.user_id}`, { headers })
            .then((r) => {
                setPersonData(r.data);
                const attempts = r.data?.attempts || [];
                setAttemptNo(attempts.length ? attempts[attempts.length - 1].attempt_no : null);
            })
            .catch(() => setPersonData({ attempts: [], quiz: [] }));
    };
    const closePerson = useCallback(() => setPerson(null), []);

    /* Esc и системное «назад» из разбора — на шаг, к списку, а не закрывают
       окно целиком: так же ведут себя «Ответы» в опросах. Запись «назад»
       кладётся ПОЗЖЕ записи окна и снимается первой. */
    const isMobileShell = useIsMobileShell();
    useScreenBackGesture(isMobileShell && !!person, closePerson);
    /* ←/→ листают попытки: при двадцати попытках до соседней мышью далеко, а
       ряд номеров показывает не все. */
    const attemptNumbers = useMemo(
        () => (personData?.attempts || []).map((item) => item.attempt_no), [personData]);
    useEffect(() => {
        if (!person) return undefined;
        const onKey = (event) => {
            if (event.key === 'Escape') {
                event.stopPropagation();
                closePerson();
                return;
            }
            const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
            if (!step || attemptNumbers.length < 2) return;
            const target = event.target;
            if (target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)) return;
            event.preventDefault();
            setAttemptNo((currentNo) => {
                const next = attemptNumbers.indexOf(currentNo) + step;
                return next >= 0 && next < attemptNumbers.length ? attemptNumbers[next] : currentNo;
            });
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [person, closePerson, attemptNumbers]);

    const exportReport = () => {
        if (!post?.id || exporting) return;
        setExporting(true);
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}/report.xlsx`,
                  { headers, responseType: 'blob' })
            .then((r) => {
                const url = URL.createObjectURL(new Blob([r.data]));
                const link = document.createElement('a');
                link.href = url;
                link.download = `Ознакомление — ${(post.title || 'новость').slice(0, 60)}.xlsx`;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .finally(() => setExporting(false));
    };

    /* Тест новости в виде вопросов опроса — чтобы разбор попытки был ТЕМ ЖЕ
       компонентом, что в «Опросах». У новости всегда один верный вариант. */
    const reviewQuestions = useMemo(() => (personData?.quiz || []).map((item) => ({
        id: item.id,
        text: item.prompt,
        type: 'single',
        options: item.options,
        correct_options: [item.options[item.correct]],
        __correctIndex: item.correct,
    })), [personData]);
    const attempts = personData?.attempts || [];
    const attempt = attempts.find((item) => item.attempt_no === attemptNo) || null;

    const tiles = state ? (hasQuiz ? [
        { key: 'assigned', label: 'Назначено', value: state.assigned ?? state.total ?? 0 },
        { key: 'confirmed', label: 'Ознакомились', value: state.confirmed ?? 0 },
        { key: 'passed', label: 'Прошли тест', value: state.quiz_passed ?? 0,
          hint: `${state.percent ?? 0}%` },
        { key: 'attempts', label: 'Попыток',
          value: state.avg_attempts ? String(state.avg_attempts).replace('.', ',') : '—',
          hint: state.avg_attempts ? 'в среднем' : null },
    ] : [
        { key: 'assigned', label: 'Назначено', value: state.assigned ?? state.total ?? 0 },
        { key: 'confirmed', label: 'Ознакомились', value: state.confirmed ?? 0 },
        { key: 'rate', label: 'Доля ознакомления', value: `${state.percent ?? 0}%` },
    ]) : [];

    return (
        <IosModal
            open={open}
            onClose={onClose}
            onBack={person ? closePerson : null}
            title={person ? person.name : 'Результаты'}
            subtitle={post?.title}
            maxWidth="max-w-2xl"
            footer={person ? null : (
                <>
                    {/* Выгрузка — там же, где закрывают окно: её берут, когда
                        результаты уже посмотрели (ТЗ #300, п.14). */}
                    <button type="button" className={iosBtnSecondary}
                            disabled={exporting || !state} onClick={exportReport}>
                        {exporting
                            ? <Loader2 className="mr-1 inline h-4 w-4 animate-spin" aria-hidden="true" />
                            : <Download className="mr-1 inline h-4 w-4" aria-hidden="true" />}
                        Excel
                    </button>
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button>
                </>
            )}
        >
            {loading && (
                <p className="py-8 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Считаем
                </p>
            )}

            {/* ── РАЗБОР СОТРУДНИКА ─────────────────────────────────────── */}
            {!loading && state && person && (
                <div className="animate-card-open space-y-3">
                    {!personData && (
                        <p className="py-8 text-center text-[13px] text-slate-400">
                            <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Загружаем попытки
                        </p>
                    )}
                    {personData && attempt && (
                        <>
                            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/70 pb-3">
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge color={attempt.passed ? 'green' : 'amber'}>
                                            {attempt.passed ? 'Засчитана' : 'Не засчитана'}
                                        </Badge>
                                        {STATUS_LABELS[person.status] && !['passed', 'done'].includes(person.status) && (
                                            <Badge color={STATUS_BADGE_COLOR[person.status] || 'gray'}>
                                                {STATUS_LABELS[person.status]}
                                            </Badge>
                                        )}
                                    </div>
                                    <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
                                        <span>
                                            Верно:{' '}
                                            <strong className="tabular-nums text-slate-800">
                                                {attempt.correct} из {attempt.total}
                                            </strong>
                                        </span>
                                        <span>
                                            Нужно:{' '}
                                            <strong className="tabular-nums text-slate-800">{attempt.needed}</strong>
                                        </span>
                                        <span className="tabular-nums text-slate-400">{shortTime(attempt.created_at)}</span>
                                    </div>
                                </div>
                                <div className={`shrink-0 text-[26px] font-bold leading-none tabular-nums ${
                                    scoreToneClass(attempt.total ? attempt.correct * 100 / attempt.total : 0)}`}>
                                    {attempt.total ? Math.round(attempt.correct * 100 / attempt.total) : 0}%
                                </div>
                            </div>

                            {/* Переключатель попыток — только когда их несколько: у
                                сдавшего с первого раза выбирать не из чего. Последняя
                                открыта сразу — она и есть итог. */}
                            {attempts.length > 1 && (
                                <AttemptSwitcher attempts={attempts} value={attemptNo}
                                                 onChange={setAttemptNo} />
                            )}

                            <AttemptReview
                                questions={reviewQuestions}
                                isTest
                                getAnswer={(question) => {
                                    const chosen = attempt.answers?.[String(question.id)];
                                    if (chosen === undefined || chosen === null) return null;
                                    return {
                                        selected_options: [question.options[Number(chosen)]],
                                        is_correct: Number(chosen) === question.__correctIndex,
                                    };
                                }}
                            />
                        </>
                    )}
                    {personData && !attempt && (
                        <p className="py-8 text-center text-[13px] text-slate-400">Попыток нет</p>
                    )}
                </div>
            )}

            {/* ── СВОДКА И СПИСКИ ───────────────────────────────────────── */}
            {!loading && state && !person && (
                <div className="space-y-3">
                    <StatTiles tiles={tiles} />

                    {/* Обстоятельства выпуска — мелкой строкой под плитками и только
                        когда они есть: растяжка, запуск по плану, снятие. */}
                    {(state.confirmed_outside > 0 || hasTrainer || state.plan?.publish_mode === 'spread'
                      || state.plan?.scheduled_at || post?.archived_at) && (
                        <div className="space-y-0.5 px-1 text-[12px] tabular-nums text-slate-500">
                            {state.confirmed_outside > 0 && (
                                <p>и ещё {state.confirmed_outside} подтвердили из тех, кто больше не в адресатах</p>
                            )}
                            {hasTrainer && <p>тренажёр прошли {state.trainer_passed || 0}</p>}
                            {state.plan?.publish_mode === 'spread' && (
                                <p>
                                    волнами: {state.plan.waves} по {minutesLabel(state.plan.wave_interval_minutes)}
                                    {', всего '}{minutesLabel(state.plan.spread_minutes)}
                                </p>
                            )}
                            {state.plan?.scheduled_at && (
                                <p>
                                    запуск по плану {publishedLabel(state.plan.scheduled_at)}
                                    {state.plan.published_at
                                        ? `, вышла ${publishedLabel(state.plan.published_at)}` : ''}
                                </p>
                            )}
                            {/* КТО И КОГДА СНЯЛ (ТЗ #300, п.16). «Снималась» — у
                                выпущенной заново: перерыв в показе обязан
                                объясняться и потом. */}
                            {post?.archived_at && (
                                <p>
                                    {post.status === 'archived' ? 'снята с показа' : 'снималась с показа'}
                                    {' '}{publishedLabel(post.archived_at)}
                                    {post.archived_by_name ? ` · ${post.archived_by_name}` : ''}
                                </p>
                            )}
                        </div>
                    )}

                    {/* Вкладки — только у новости с тестом: без него «Вопросов»
                        нет, и переключатель из одной кнопки был бы шумом. */}
                    {hasQuiz && (
                        <IosSegmented
                            value={tab}
                            onChange={setTab}
                            stretch
                            ariaLabel="Результаты новости"
                            options={[
                                { value: 'people', label: 'Сотрудники', count: (state.items || []).length },
                                { value: 'questions', label: 'Вопросы', count: (state.questions || []).length },
                            ]}
                        />
                    )}

                    {tab === 'people' && (
                        <div className="animate-card-open space-y-2.5">
                            <div className="flex flex-wrap items-center gap-2">
                                <div className="relative min-w-0 flex-1">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                                    <input
                                        value={query}
                                        onChange={(e) => setQuery(e.target.value)}
                                        placeholder="Поиск по сотруднику"
                                        className={`${iosInput} py-2 pl-8 text-[13px]`}
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={() => setOnlyAttention((value) => !value)}
                                    aria-pressed={onlyAttention}
                                    className={`shrink-0 rounded-full px-3 py-1.5 text-[12px] tabular-nums transition active:scale-[0.98] ${
                                        onlyAttention ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                >
                                    Требуют внимания{state.needs_attention ? ` · ${state.needs_attention}` : ''}
                                </button>
                            </div>

                            {people.length === 0 && (
                                <p className="py-8 text-center text-[13px] text-slate-400">
                                    {query ? 'Сотрудники не найдены'
                                        : (onlyAttention ? 'Все ознакомились' : 'Адресатов нет')}
                                </p>
                            )}

                            {/* Карточки — как «Ответы» в опросах: по две в ряд,
                                открывается та, у которой есть что разбирать. */}
                            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                                {people.map((row) => {
                                    const clickable = hasQuiz && row.attempts > 0;
                                    const score = row.last_total
                                        ? Math.round((row.last_correct || 0) * 100 / row.last_total) : null;
                                    const settled = ['passed', 'done'].includes(row.status);
                                    return (
                                        <button
                                            key={row.user_id}
                                            type="button"
                                            disabled={!clickable}
                                            onClick={() => openPerson(row)}
                                            className={`flex min-h-[88px] flex-col justify-between rounded-2xl px-4 py-3.5 text-left ring-1 transition-all duration-200 ${
                                                clickable
                                                    ? 'bg-white ring-slate-200/70 hover:-translate-y-0.5 hover:shadow-[0_6px_20px_-12px_rgba(15,23,42,0.4)] hover:ring-blue-300 active:scale-[0.99]'
                                                    : `cursor-default ring-slate-200/60 ${settled ? 'bg-white' : 'bg-slate-50'}`
                                            }`}
                                        >
                                            <div className="min-w-0">
                                                <div className={`truncate text-[13.5px] font-semibold leading-snug ${
                                                    settled || clickable ? 'text-slate-900' : 'text-slate-500'}`}>
                                                    {row.name}
                                                </div>
                                                <div className="mt-0.5 truncate text-[11.5px] text-slate-400">
                                                    {[roleTitle(row.role), row.department_name,
                                                      row.wave_no ? `волна ${row.wave_no}` : null]
                                                        .filter(Boolean).join(' · ')}
                                                </div>
                                            </div>
                                            <div className="mt-2 flex items-end justify-between gap-2">
                                                <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                                                    {!settled && (
                                                        <Badge color={STATUS_BADGE_COLOR[row.status] || 'gray'}>
                                                            {STATUS_LABELS[row.status] || 'Объявление не открыто'}
                                                        </Badge>
                                                    )}
                                                    {settled && row.confirmed_at && (
                                                        <span className="text-[11.5px] tabular-nums text-slate-400">
                                                            {/* «прочитано», а не «ознакомился»: по
                                                                имени пол не угадывают. */}
                                                            прочитано {publishedLabel(row.confirmed_at)}
                                                        </span>
                                                    )}
                                                    {!row.in_audience && <Badge color="gray">уже не в адресатах</Badge>}
                                                    {/* Число попыток — только когда их больше одной:
                                                        «с первого раза» это норма. */}
                                                    {hasQuiz && row.attempts > 1 && (
                                                        <Badge color="blue">{attemptsLabel(row.attempts)}</Badge>
                                                    )}
                                                    {hasTrainer && row.trainer_passed_at && (
                                                        <Badge color="green">тренажёр</Badge>
                                                    )}
                                                </div>
                                                {hasQuiz && score !== null && (
                                                    <span className={`shrink-0 text-[19px] font-bold leading-none tabular-nums ${scoreToneClass(score)}`}>
                                                        {score}%
                                                    </span>
                                                )}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {tab === 'questions' && hasQuiz && (
                        <div className="animate-card-open space-y-2.5">
                            {/* По какой попытке — сказано один раз, за «i», а не у
                                каждого вопроса. */}
                            <p className="flex items-center gap-1.5 px-1 text-[12px] text-slate-500">
                                Ответы по первой попытке
                                <IosHint
                                    label="Почему по первой попытке"
                                    text="Первая попытка показывает, понятен ли текст новости. Повторные — уже нет: после неверной попытки выбор сбрасывается, и человек отвечает, помня, какой вариант не прошёл. К десятой попытке верно ответят все, и непонятный вопрос выглядел бы понятным."
                                />
                            </p>
                            {(state.questions || []).length === 0 && (
                                <p className="py-8 text-center text-[13px] text-slate-400">Ответов пока нет</p>
                            )}
                            {(state.questions || []).map((question) => {
                                const leader = Math.max(0, ...question.options.map((option) => option.count));
                                return (
                                    <div key={question.id} className="rounded-2xl bg-white p-4 ring-1 ring-slate-200/70">
                                        <div className="flex items-start justify-between gap-3">
                                            <div className="min-w-0">
                                                <div className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                                                    Вопрос {question.number}
                                                </div>
                                                <div className="mt-0.5 text-[13.5px] font-medium text-slate-900">
                                                    {question.prompt}
                                                </div>
                                            </div>
                                            <div className="shrink-0 text-right">
                                                <div className="text-[15px] font-semibold tabular-nums text-slate-900">
                                                    {question.answered}
                                                </div>
                                                <div className="text-[10.5px] text-slate-400">ответили</div>
                                            </div>
                                        </div>
                                        {/* Доля ошибившихся — янтарём, и только когда
                                            ошибались: «ошиблись 0%» у каждого вопроса
                                            было бы шумом (ТЗ #300, п.12). */}
                                        {question.wrong_people > 0 && (
                                            <div className="mt-1.5 text-[11.5px] tabular-nums text-amber-700">
                                                ошиблись {question.percent}% ({question.wrong_people} из {question.answered})
                                            </div>
                                        )}
                                        <div className="mt-3 space-y-1.5">
                                            {question.options.map((option) => (
                                                <OptionStatRow
                                                    key={option.index}
                                                    label={option.label}
                                                    count={option.count}
                                                    percent={option.percent}
                                                    isCorrect={option.is_correct}
                                                    isLeader={leader > 0 && option.count === leader}
                                                />
                                            ))}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}
        </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Витрина
// ─────────────────────────────────────────────────────────────────────────────

/* spaceId — пространство, в котором открыта вкладка (решение владельца
   18.09.2026: «чтобы по пространствам новости Таксопарков и Тез не
   смешивались»). Он и есть граница раздела здесь: список показывает
   объявления ЭТОЙ вики, справочник адресата — её отделы, а новая новость
   этой вике и принадлежит. Сервер держит ту же границу сам — параметр не
   «уточнение выборки», но и единственной дверью он не остаётся. */
export default function WikiNews({ apiBaseUrl, headers, showToast, compose = null,
                                   onComposeFinished, spaceId = null, spaceName = '' }) {
    const [bucket, setBucket] = useState('published');
    const [items, setItems] = useState([]);
    const [access, setAccess] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [formPost, setFormPost] = useState(undefined);   // undefined — закрыта
    const [reportPost, setReportPost] = useState(null);
    /* Какую новость сейчас снимают: подтверждение стоит прямо в её строке.
       Не отдельное окно — на телефоне любое окно здесь целый экран, а вопрос в
       одну строку целого экрана не стоит. */
    const [takingDown, setTakingDown] = useState(null);
    const [takeDownBusy, setTakeDownBusy] = useState(false);

    /* «Опубликовать как новость» из вкладки «Вопросы» (задача #321): форма
       открывается сама — с заголовком, текстом и отделом оператора. Один раз на
       просьбу (nonce) и только когда права уже приехали: форме нужна задержка
       кнопки по умолчанию. Чем закончилась форма, узнаёт тот, кто просил. */
    const composeActive = useRef(null);
    const composeOpened = useRef(null);
    const finishCompose = useRef(onComposeFinished);
    useEffect(() => { finishCompose.current = onComposeFinished; }, [onComposeFinished]);
    useEffect(() => {
        if (!compose?.nonce || !access?.can_publish) return;
        if (composeOpened.current === compose.nonce) return;
        composeOpened.current = compose.nonce;
        composeActive.current = compose;
        setFormPost({
            title: compose.draft?.title || '',
            body: compose.draft?.body || '',
            audience: compose.draft?.audience || [],
            is_mandatory: true,
            confirm_delay_seconds: access.default_confirm_delay_seconds ?? 10,
            expires_at: null,
            photos: [],
        });
    }, [compose, access]);
    const closeCompose = useCallback((newsId) => {
        const request = composeActive.current;
        composeActive.current = null;
        if (request) finishCompose.current?.(request, newsId || null);
    }, []);

    /* showToast приходит из App новой функцией на каждом её рендере. В
       зависимостях загрузчика это означало бы перезапрос списка на каждый чужой
       рендер — та же ловушка, что уже ловили в разделе «Опросы». */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const canPublish = !!access?.can_publish;

    /* Список редактора грузим только редактору и только в «его» сегментах:
       читателю сервер ответил бы 403 красной строкой в консоли на каждом
       заходе, а сегмент «Для меня» — это лента, у неё свой запрос. */
    const load = useCallback(() => {
        if (!canPublish || bucket === 'mine') { setLoading(false); return Promise.resolve(); }
        setLoading(true);
        return axios.get(`${apiBaseUrl}/api/news/posts`,
                         { headers, params: { status: bucket, space_id: spaceId } })
            .then((r) => { setItems(r.data?.items || []); setError(''); })
            .catch((e) => { setItems([]); setError(errText(e, 'Не удалось загрузить новости')); })
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, bucket, canPublish, spaceId]);

    useEffect(() => { load(); }, [load]);

    /* null — ещё не знаем, кто перед нами; false — ответ не пришёл. Различать
       нужно: до ответа вкладка не решает, ленту показывать или управление. */
    useEffect(() => {
        /* Пространство спрашивается ВМЕСТЕ с правами: справочник адресата
           сужен его отделами, и переключение вики обязано его перечитать —
           иначе форма предлагала бы отделы соседней компании. */
        axios.get(`${apiBaseUrl}/api/news/access`, { headers, params: { space_id: spaceId } })
            .then((r) => setAccess(r.data))
            .catch(() => setAccess(false));
    }, [apiBaseUrl, headers, spaceId]);

    /* Карточку для правки берём с сервера целиком: в списке нет ни текста, ни
       адресатов, и открытая по нему форма показала бы пустую новость. */
    const openForm = useCallback((post) => {
        if (!post) { setFormPost(null); return; }
        if (post.can_edit === false) {
            toastRef.current?.('Править новость может её автор', 'error');
            return;
        }
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}`, { headers })
            .then((r) => setFormPost(r.data))
            .catch((e) => toastRef.current?.(errText(e, 'Не удалось открыть новость'), 'error'));
    }, [apiBaseUrl, headers]);

    const save = useCallback((payload) => {
        setSaving(true);
        const post = formPost;
        const request = post?.id
            ? axios.patch(`${apiBaseUrl}/api/news/posts/${post.id}`, payload, { headers })
                .then((r) => (payload.publish && r.data?.status !== 'published'
                    ? axios.post(`${apiBaseUrl}/api/news/posts/${post.id}/publish`, {}, { headers })
                    : r))
            /* Новая новость принадлежит той вике, из которой её пишут.
               У правки пространство не меняется вовсе: переезд объявления в
               соседнюю вику — это другой круг адресатов, а он у выпущенной
               новости уже показан людям и посчитан в журнале. */
            : axios.post(`${apiBaseUrl}/api/news/posts`, { ...payload, space_id: spaceId },
                         { headers });
        request
            .then((response) => {
                setFormPost(undefined);
                closeCompose(payload.publish ? response?.data?.id : null);
                /* Что сказать, решает ОТВЕТ СЕРВЕРА, а не нажатая кнопка:
                   «Опубликовать» с отложенным запуском новость не публикует, и
                   тост «Новость опубликована» соврал бы ровно в тот момент,
                   когда автор проверяет, что всё сделал верно. */
                const state = response?.data?.state;
                toastRef.current?.(
                    !payload.publish ? 'Черновик сохранён'
                        : state === 'scheduled' ? 'Запуск запланирован'
                            : 'Новость опубликована',
                    'success');
                /* Уводим в ту корзину, где сохранённое теперь лежит: иначе
                   черновик, сохранённый со вкладки «Опубликованные», исчезает
                   без следа — список перечитывается, а его в нём нет. */
                const target = !payload.publish ? 'draft'
                    : state === 'scheduled' ? 'scheduled' : 'published';
                if (bucket !== target) setBucket(target);
                else load();
            })
            .catch((e) => toastRef.current?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving(false));
    }, [apiBaseUrl, headers, formPost, bucket, load, closeCompose, spaceId]);

    const act = useCallback((post, action) => {
        /* Удаление выпускавшейся новости спрашивают вслух: вместе с ней
           пропадает журнал «Кто прочитал» (решение владельца 21.09.2026), а он
           и есть ответ на вопрос, был ли сотрудник проинформирован. У
           черновика спрашивать нечего — его никто не видел. */
        if (action === 'delete' && post.published_at
            && !window.confirm(`Удалить новость «${post.title}» навсегда?\n\n`
                               + 'Вместе с ней пропадёт журнал «Кто прочитал» — кто из '
                               + 'сотрудников подтвердил объявление и прошёл тест. '
                               + 'Вернуть его будет нельзя.')) return;
        const url = `${apiBaseUrl}/api/news/posts/${post.id}${
            ['delete', 'launch_now', 'unschedule'].includes(action) ? '' : `/${action}`}`;
        /* Отмена запуска и выпуск «сейчас» — это одна и та же правка: режим
           «Сразу» снимает взвод (сервер: scheduled_armed). Разница только в
           том, идёт ли следом публикация. Своих роутов им не заводим: новое
           правило хранилось бы в двух местах. */
        const reset = { publish_mode: 'now', scheduled_at: null,
                        spread_minutes: null, wave_interval_minutes: null };
        if (action === 'archive') setTakeDownBusy(true);
        const request = action === 'delete'
            ? axios.delete(url, { headers })
            : action === 'unschedule'
                ? axios.patch(url, reset, { headers })
                : action === 'launch_now'
                    ? axios.patch(url, reset, { headers }).then(() => axios.post(
                        `${url}/publish`, {}, { headers }))
                    : axios.post(url, {}, { headers });
        request
            .then((response) => {
                toastRef.current?.({
                    /* Та же оговорка, что у формы: у новости с отложенным
                       запуском «Опубликовать» взводит запуск, а не выпускает. */
                    publish: (response?.data?.state === 'scheduled'
                        ? 'Запуск запланирован' : 'Новость опубликована'),
                    launch_now: 'Новость опубликована',
                    unschedule: 'Запуск отменён — объявление вернулось в черновики',
                    archive: 'Новость снята с показа',
                    delete: post.published_at ? 'Новость удалена' : 'Черновик удалён',
                }[action], 'success');
                if (action === 'archive') setTakingDown(null);
                load();
            })
            .catch((e) => {
                /* Сняли раньше нас (сервер: NEWS_NOT_ON_AIR) — вопрос в строке
                   больше не о чем задавать, а список показывает устаревшее. */
                if (action === 'archive' && e?.response?.status === 409) {
                    setTakingDown(null);
                    load();
                }
                toastRef.current?.(errText(e, 'Не получилось'), 'error');
            })
            .finally(() => { if (action === 'archive') setTakeDownBusy(false); });
    }, [apiBaseUrl, headers, load]);

    /* «Раздел разворачивается» и «нет прав» — разные ответы, и путать их
       нельзя: первый пройдёт сам, а второй человек понесёт в поддержку. */
    if (access && access.schema_ready === false) {
        return (
            <div className={`${iosCard} p-6 text-center`}>
                <p className="text-[14px] text-slate-900">Раздел «Новости» разворачивается</p>
                <p className="mt-1 text-[13px] text-slate-400">Загляните чуть позже</p>
            </div>
        );
    }
    if (access === null) {
        return (
            <p className="py-10 text-center text-[13px] text-slate-400">
                <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Загружаем
            </p>
        );
    }
    /* Право только на чтение — «только сами новости, которые ему были
       предназначены» (решение владельца 17.09.2026). Без сегментов и кнопок:
       управлять читателю нечем. */
    if (!canPublish) {
        return <NewsFeed apiBaseUrl={apiBaseUrl} headers={headers} spaceId={spaceId} />;
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                {/* Четыре сегмента на телефоне шире экрана — переключатель
                    прокручивается пальцем, а не вылезает за край. */}
                <div className="max-w-full overflow-x-auto scrollbar-hide">
                    <IosSegmented
                        value={bucket}
                        options={BUCKETS}
                        onChange={setBucket}
                    />
                </div>
                <button type="button" className={iosBtnPrimary} onClick={() => openForm(null)}>
                    <Megaphone className="mr-1.5 inline h-4 w-4" aria-hidden="true" />
                    Новая новость
                </button>
            </div>

            {bucket === 'mine' && (
                <NewsFeed apiBaseUrl={apiBaseUrl} headers={headers} spaceId={spaceId} />
            )}

            {bucket !== 'mine' && error && <p className="text-[13px] text-rose-600">{error}</p>}

            {bucket !== 'mine' && loading && (
                <p className="py-10 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Загружаем
                </p>
            )}

            {bucket !== 'mine' && !loading && items.length === 0 && (
                <div className={`${iosCard} px-6 py-10 text-center`}>
                    <p className="text-[14px] text-slate-900">
                        {bucket === 'published' ? 'Опубликованных новостей нет'
                            : bucket === 'draft' ? 'Черновиков нет' : 'Архив пуст'}
                    </p>
                    <p className="mt-1 text-[13px] text-slate-400">
                        Новость показывается окном поверх портала — сотрудник увидит её
                        при входе, а если он уже в системе, окно всплывёт сразу после публикации.
                    </p>
                </div>
            )}

            {bucket !== 'mine' && !loading && items.map((post) => (
                <div key={post.id} className={`${iosCard} flex items-start gap-3 px-4 py-3.5`}>
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <p className="text-[15px] font-medium text-slate-900">{post.title}</p>
                            {/* Плашка только у того, что не опубликовано:
                                «опубликована» и так видно по вкладке, а метка
                                на каждой строке была бы шумом. */}
                            {post.state === 'draft' && <IosBadge tone="slate">черновик</IosBadge>}
                            {/* Запланированная — не черновик: её никто не
                                допишет, она ждёт своего часа. */}
                            {post.state === 'scheduled' && <IosBadge tone="blue">запланирована</IosBadge>}
                            {post.status === 'archived' && <IosBadge tone="slate">снята</IosBadge>}
                            {!post.is_mandatory && <IosBadge tone="slate">необязательная</IosBadge>}
                            {/* Растяжку показываем меткой: объявление уходит не
                                всем сразу, и это первое, что спросят, увидев в
                                журнале «подтвердили 10 из 120». */}
                            {post.publish_mode === 'spread' && <IosBadge tone="slate">волнами</IosBadge>}
                            {/* Куда ушло объявление — только у Oktell: портал
                                это умолчание, и метка «в iCORE» стояла бы
                                почти у каждой строки, ничего не различая. */}
                            {post.channel === 'oktell' && <IosBadge tone="slate">в Oktell</IosBadge>}
                            {/* Числом, а не фразой: строка списка отвечает на
                                «что это за новость», а не рассказывает про её
                                устройство. */}
                            {post.photo_count > 0 && (
                                <span className="inline-flex items-center gap-1 text-[12px] tabular-nums text-slate-400">
                                    <ImageIcon className="h-3.5 w-3.5" aria-hidden="true" />
                                    {post.photo_count}
                                </span>
                            )}
                            {/* Метка, а не число: тест меняет у сотрудника сам
                                способ закрыть окно. */}
                            {post.quiz_count > 0 && <IosBadge tone="slate">с тестом</IosBadge>}
                            {post.trainer_key && <IosBadge tone="slate">с тренажёром</IosBadge>}
                        </div>
                        <p className="mt-1 truncate text-[12px] text-slate-400">
                            {[post.author_name, post.author_department,
                              /* У запланированной в строке стоит ВРЕМЯ ЗАПУСКА, а
                                 не дата создания: «когда это выйдет» — весь смысл
                                 такой строки, а созданием её никто не меряет. */
                              post.state === 'scheduled'
                                  ? `запуск ${publishedLabel(post.scheduled_at)}`
                                  : (post.status === 'archived' && post.archived_at
                                      ? takedownLabel(post)
                                      : publishedLabel(post.published_at || post.created_at))]
                                .filter(Boolean).join(' · ')}
                        </p>
                        {/* Журнал — и у СНЯТОЙ новости (ТЗ #300, п.16: «публикация
                            сохраняется в истории»). Раньше кнопка пропадала вместе
                            с показом, и у ошибочного объявления, которое как раз
                            разбирают, журнал становился недоступен. */}
                        {post.published_at && hasJournal(post) && (
                            <button
                                type="button"
                                onClick={() => setReportPost(post)}
                                className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-slate-500 underline-offset-2 transition hover:text-slate-900 hover:underline"
                            >
                                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                                <span className="tabular-nums">
                                    Прочитали: {post.confirmed_count} из {post.audience_count}
                                </span>
                            </button>
                        )}
                        {/* ПОДТВЕРЖДЕНИЕ СНЯТИЯ — как системный алерт: нейтральная
                            плашка, красная только сама кнопка действия. Текст —
                            последствие, а не вопрос «вы уверены?»: человеку
                            надо знать, ЧТО произойдёт, а не подтверждать, что он
                            нажимал. Журнал упомянут, потому что именно его боятся
                            потерять, снимая ошибочное объявление. */}
                        {takingDown === post.id && (
                            <div className="mt-3 rounded-xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
                                <p className="text-[13.5px] font-semibold text-slate-900">
                                    Снять с показа?
                                </p>
                                <p className="mt-0.5 text-[12.5px] leading-snug text-slate-500">
                                    Объявление перестанет показываться. Журнал «Кто прочитал»
                                    останется, и в нём будет видно, кто снял и когда.
                                </p>
                                <div className="mt-2.5 flex justify-end gap-2">
                                    <button
                                        type="button"
                                        disabled={takeDownBusy}
                                        onClick={() => setTakingDown(null)}
                                        className={`${iosBtnSecondary} !px-3.5 !py-1.5 text-[13px]`}
                                    >
                                        Отмена
                                    </button>
                                    <button
                                        type="button"
                                        disabled={takeDownBusy}
                                        onClick={() => act(post, 'archive')}
                                        className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-rose-600 px-3.5 py-1.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-rose-700 active:scale-[0.98] disabled:opacity-60"
                                    >
                                        {takeDownBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                                        Снять с показа
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                    {/* Что можно с этой новостью, решает СЕРВЕР и присылает
                        признаком can_edit: коллега того же уровня видит чужое
                        объявление своего отдела, но правит его только автор.
                        Вторая формула здесь дала бы пункт меню, на который
                        сервер отвечает 403.
                        Пустое меню не рисуем вовсе: «три точки», за которыми
                        ничего нет, — обещание действия, которого не будет. */}
                    {(() => {
                        const actions = [
                            ...(post.can_edit
                                ? [{ key: 'edit', label: 'Изменить',
                                     onSelect: () => openForm(post) }]
                                : []),
                            /* У запланированной «Опубликовать» не значит
                               ничего: сервер прочтёт её же расписание и
                               взведёт запуск заново. Поэтому ей — два прямых
                               действия, а обычному черновику остаётся прежнее. */
                            ...(post.can_edit && post.state === 'scheduled'
                                ? [{ key: 'launch_now', label: 'Выпустить сейчас',
                                     onSelect: () => act(post, 'launch_now') },
                                   { key: 'unschedule', label: 'Отменить запуск',
                                     onSelect: () => act(post, 'unschedule') }]
                                : []),
                            ...(post.can_edit && post.state === 'draft'
                                ? [{ key: 'publish', label: 'Опубликовать',
                                     onSelect: () => act(post, 'publish') }]
                                : []),
                            /* Снятую по ошибке — выпустить снова. Кто её снимал,
                               после этого не забывается: сервер хранит последнее
                               снятие и после повторного выпуска. */
                            ...(post.can_edit && post.status === 'archived'
                                ? [{ key: 'publish', label: 'Опубликовать снова',
                                     onSelect: () => act(post, 'publish') }]
                                : []),
                            /* Снятие — через подтверждение прямо в строке: оно
                               убирает окно у всего круга адресатов разом, и
                               промах мимо соседнего пункта меню стоил бы отделу
                               пропавшего объявления. */
                            ...(post.can_take_down && post.status === 'published'
                                ? [{ key: 'archive', label: 'Снять с показа', danger: true,
                                     onSelect: () => setTakingDown(post.id) }]
                                : []),
                            ...(post.published_at && hasJournal(post)
                                ? [{ key: 'report', label: 'Кто прочитал',
                                     onSelect: () => setReportPost(post) }]
                                : []),
                            ...(post.can_edit && post.status !== 'published'
                                ? [{ key: 'delete', label: 'Удалить', danger: true,
                                     separatorBefore: true,
                                     onSelect: () => act(post, 'delete') }]
                                : []),
                        ];
                        return actions.length ? <IosMenu items={actions} /> : null;
                    })()}
                </div>
            ))}

            <NewsForm
                open={formPost !== undefined}
                post={formPost}
                access={access}
                saving={saving}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                spaceId={spaceId}
                spaceName={spaceName}
                onClose={() => {
                    setFormPost(undefined);
                    closeCompose(null);
                }}
                onSave={save}
            />
            <NewsReport
                open={!!reportPost}
                post={reportPost}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onClose={() => setReportPost(null)}
            />
        </div>
    );
}
