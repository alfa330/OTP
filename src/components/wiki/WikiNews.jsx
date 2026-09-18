import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Highlight from '@tiptap/extension-highlight';
import {
    AlertTriangle, Bold, Check, ChevronDown, Image as ImageIcon, Italic, Link2, List,
    ListChecks, ListOrdered, Loader2, Megaphone, PlayCircle, Plus, Sparkles,
    Underline as UnderlineIcon, Users, X,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosHint, IosMenu, IosModal, IosSegmented, IosToggle,
} from '../ui/ios';
import { publishedLabel, roleTitle } from '../news/newsShared';
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
    { value: 'draft', label: 'Черновики' },
    { value: 'archived', label: 'Архив' },
    /* Новости, адресованные самому редактору: сверху они приходят и ему. */
    { value: 'mine', label: 'Для меня' },
];

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

const ruleKey = (rule) => `${rule.subject_type}:${rule.subject_id ?? rule.subject_role}`;

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

function AudiencePicker({ open, onClose, access, value, onChange, spaceName = '' }) {
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
        <IosModal
            open={open}
            onClose={onClose}
            title="Кому показать новость"
            /* Почему список сужен — под «i», а не подзаголовком: IosModal
               рисует subtitle одной строкой с truncate, и на телефоне фраза
               обрывалась на середине. Объяснение читают один раз, а место в
               шапке оно занимало всегда. */
            maxWidth="max-w-xl"
            footer={(
                <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
            )}
        >
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
        </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Выбор тренажёра (задача #342)
// ─────────────────────────────────────────────────────────────────────────────

/* Список тех же тренажёров, что во вкладке «Тренажёры» вики, — из реестра, а не
   своим перечнем: сценарии живут в коде, и второй список отстал бы от первого
   на первом же новом тренажёре. Нажатие выбирает и закрывает окно — как выбор
   значения в «Настройках» iOS. */
function TrainerPicker({ open, value, onClose, onChange }) {
    return (
        <IosModal open={open} onClose={onClose} title="Тренажёр к новости" maxWidth="max-w-lg">
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {TRAINER_CARDS.map((trainer) => {
                    const active = trainer.key === value;
                    return (
                        <button
                            key={trainer.key}
                            type="button"
                            onClick={() => { onChange(trainer.key); onClose(); }}
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
        </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Форма новости
// ─────────────────────────────────────────────────────────────────────────────

function NewsForm({ open, post, access, onClose, onSave, saving, apiBaseUrl, headers,
                   spaceId = null, spaceName = '' }) {
    const [title, setTitle] = useState('');
    const [mandatory, setMandatory] = useState(true);
    const [delay, setDelay] = useState(access?.default_confirm_delay_seconds ?? 10);
    const [expires, setExpires] = useState('');
    const [audience, setAudience] = useState([]);
    const [pickerOpen, setPickerOpen] = useState(false);
    const [error, setError] = useState('');
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
    const [trainerPickerOpen, setTrainerPickerOpen] = useState(false);
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
    /* Держит ли прохождение подтверждение — то же правило, что на сервере
       (news/access.py: must_pass). Держит — новость обязательна всегда. */
    const mustPass = passRequired && (quiz.length > 0 || !!trainerKey);
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
                class: 'news-body min-h-[180px] focus:outline-none',
            },
        },
    }, []);

    /* Форма заполняется при ОТКРЫТИИ, а не на каждый рендер: правка в поле не
       должна затираться тем же объектом post, приехавшим из списка заново. */
    useEffect(() => {
        if (!open) return;
        setTitle(post?.title || '');
        setMandatory(post ? !!post.is_mandatory : true);
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
        setChannel(post?.channel || NEWS_CHANNELS[0].value);
        setSipCheck(null);
        setSipOpen(false);
        setTrainerPickerOpen(false);
        // Уже прикреплённые кадры приезжают с карточкой готовыми адресами.
        setPhotos((post?.photos || []).map((photo) => ({
            key: `id:${photo.id}`, id: photo.id, url: photo.url,
        })));
        editor?.commands.setContent(post?.body || '');
    }, [open, post, access, editor]);

    // Новость с ОБЯЗАТЕЛЬНЫМ тестом или тренажёром всегда обязательна: у
    // необязательной крестик подтверждал бы прочтение без единого ответа
    // (сервер: NEWS_QUIZ_MANDATORY).
    useEffect(() => {
        if (mustPass) setMandatory(true);
    }, [mustPass]);

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

    const audienceLabel = useCallback((rule) => {
        if (rule.subject_type === 'otp_role') {
            return roleTitle(rule.subject_role) || rule.subject_role;
        }
        if (rule.subject_name) return rule.subject_name;
        const pool = rule.subject_type === 'user'
            ? (access?.people || [])
            : (access?.subjects?.[rule.subject_type] || []);
        return pool.find((item) => item.id === rule.subject_id)?.name || '—';
    }, [access]);

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
        setError('');
        onSave({
            title: text,
            body,
            is_mandatory: mandatory || mustPass,
            // Тест опубликованной новости не отправляется вовсе: сервер его не
            // меняет (NEWS_QUIZ_LOCKED), а пустой список читался бы как «убрать».
            // Тренажёр и обязательность — под тем же замком (NEWS_PASS_LOCKED).
            ...(quizLocked ? {} : {
                quiz: quiz.map(({ prompt, options, correct }) => ({ prompt, options, correct })),
                trainer_key: trainerKey,
                pass_required: passRequired,
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

    return (
        <>
            <IosModal
                open={open}
                onClose={onClose}
                title={post?.id ? 'Новость' : 'Новая новость'}
                /* Подзаголовка нет: у опубликованной там стояло «правка не
                   сбрасывает подтверждения», и IosModal резал эту строку
                   посередине (subtitle рисуется с truncate). Сказать её надо
                   один раз и в том месте, где решение принимают, — у кнопки
                   «Сохранить», за «i». */
                maxWidth="max-w-2xl"
                footer={(
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
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
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

                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                    <span className={`${iosGroupLabel} flex items-center gap-1.5`}>
                        Тест и тренажёр
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
                                <button type="button" onClick={() => setTrainerPickerOpen(true)}
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
                                    onClick={() => setTrainerPickerOpen(true)}>
                                <Plus className="mr-1 inline h-4 w-4" aria-hidden="true" />
                                Выбрать
                            </button>
                        ))}
                    </div>

                    {/* Тумблер — только когда есть что проходить: без теста и
                        тренажёра он настраивал бы то, чего нет. */}
                    {(quiz.length > 0 || trainerKey) && (
                        <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                            <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                Пройти обязательно
                                <IosHint
                                    label="Что значит «пройти обязательно»"
                                    text="Включено — сотрудник не подтвердит новость и не закроет окно, пока не пройдёт тест и тренажёр; такая новость всегда обязательна к прочтению. Выключено — пройти можно по желанию: в окне новости или позже во вкладке «Новости», а прочитать и закрыть новость — и без этого."
                                />
                            </p>
                            <IosToggle checked={passRequired} onChange={setPassRequired} disabled={quizLocked} />
                        </div>
                    )}
                </div>

                <label className={`${iosGroupLabel} mt-4`}>Кому</label>
                <div className={`${iosCard} p-3`}>
                    <div className="flex flex-wrap items-center gap-1.5">
                        {audience.map((rule) => (
                            <span key={ruleKey(rule)}
                                  className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12px] text-slate-700">
                                {audienceLabel(rule)}
                                <button
                                    type="button"
                                    onClick={() => setAudience(audience.filter(
                                        (item) => ruleKey(item) !== ruleKey(rule)))}
                                    className="grid h-4 w-4 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                    aria-label={`Убрать ${audienceLabel(rule)}`}
                                >
                                    <X className="h-3 w-3" aria-hidden="true" />
                                </button>
                            </span>
                        ))}
                        <button
                            type="button"
                            onClick={() => setPickerOpen(true)}
                            className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-[12px] font-medium text-indigo-700 transition hover:bg-indigo-100 active:scale-[0.98]"
                        >
                            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                            Добавить
                        </button>
                    </div>
                    {/* ЕДИНСТВЕННОЕ пояснение формы, оставшееся на виду, — и
                        это не недоделка. Остальные объясняют то, что человек
                        и так видит на экране; это — границу, которой на экране
                        НЕТ: потолок audience_max_role_level режет уже выбранный
                        отдел на выдаче, а форма при сохранении об этом молчит.
                        Автор публикует «отделу», журнал показывает знаменатель
                        меньше состава отдела, и это читается как дефект данных.
                        Прочитать надо ДО нажатия «Опубликовать», а подсказку за
                        «i» читают после — то есть никогда.
                        slate-500, а не slate-400: на белом slate-400 даёт около
                        2.8:1 при норме 4.5. */}
                    <p className="mt-2 text-[12px] text-slate-500">
                        {audience.length
                            ? 'Из выбранного новость увидят только те, кто ниже вас по должности'
                            : 'Новость увидят только те, кто ниже вас по должности'}
                    </p>
                </div>

                <div className={`${iosCard} mt-4 divide-y divide-slate-100`}>
                    {/* Куда отправить — ПЕРВОЙ строкой: это решение про то, где
                        человек вообще увидит объявление, и принимают его до
                        настроек самого показа. Строки нет вовсе, когда канал
                        один: в «Тез» программы Oktell нет, и переключатель с
                        единственной кнопкой был бы вопросом без выбора. */}
                    {channelOptions.length > 1 && (
                        <div className="px-3.5 py-3">
                            <div className="flex items-center justify-between gap-3">
                                <p className="flex min-w-0 items-center gap-2 text-[14px] text-slate-900">
                                    Куда отправить
                                    <IosHint
                                        label="Чем iCORE отличается от Oktell"
                                        text="iCORE — окно «Новость дня» в портале: его видит каждый, кому новость адресована. Oktell — окно поверх клиента АТС, его рисует программа «Ограничитель Перезвона» и на время чтения снимает оператора с линии; увидят только те, у кого программа установлена. У опубликованной новости канал не меняется."
                                    />
                                </p>
                                <div className="flex shrink-0 items-center gap-2">
                                    {/* Предупреждение — РЯДОМ с переключателем, а не строкой
                                        под ним: оно относится к одной кнопке «Oktell», и
                                        отдельная красная строка внизу карточки читалась бы
                                        как ошибка всей формы. Само число на кнопке — уже
                                        ответ: «троим не дойдёт». */}
                                    {sipMissing > 0 && (
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
                                    )}
                                    {channelLocked ? (
                                        <span className="text-[13px] text-slate-500">
                                            {(NEWS_CHANNELS.find((item) => item.value === channel)
                                              || NEWS_CHANNELS[0]).label}
                                        </span>
                                    ) : (
                                        <IosSegmented
                                            value={channel}
                                            options={channelOptions}
                                            onChange={setChannel}
                                            ariaLabel="Куда отправить объявление"
                                        />
                                    )}
                                </div>
                            </div>
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
                        </div>
                    )}
                    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                        <div className="min-w-0">
                            {/* Оба состояния — в ОДНОМ пузырьке, а не подписью,
                                которая переписывается на каждый щелчок тумблера:
                                мигающая строка под переключателем читается как
                                ошибка, а не как пояснение. */}
                            <p className="flex items-center gap-2 text-[14px] text-slate-900">
                                Обязательно к прочтению
                                <IosHint
                                    label="Что меняет обязательность"
                                    text="Обязательную новость нельзя закрыть крестиком — только кнопкой «Прочитал», и отметка попадёт в журнал «Кто прочитал». Необязательная закрывается крестиком, и журнал по ней не ведётся."
                                />
                            </p>
                        </div>
                        {/* У новости с обязательным тестом или тренажёром
                            обязательность не снимается: у необязательной крестик
                            подтверждал бы прочтение без единого ответа. Сервер
                            держит то же правило (NEWS_QUIZ_MANDATORY). */}
                        <IosToggle checked={mandatory} onChange={setMandatory} disabled={mustPass} />
                    </div>
                    {/* Задержка нужна только обязательной: у необязательной
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
            </IosModal>

            <AudiencePicker
                open={pickerOpen}
                onClose={() => setPickerOpen(false)}
                access={access}
                value={audience}
                onChange={setAudience}
                spaceName={spaceName}
            />
            <TrainerPicker
                open={trainerPickerOpen}
                value={trainerKey}
                onClose={() => setTrainerPickerOpen(false)}
                onChange={setTrainerKey}
            />
        </>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Журнал прочтений
// ─────────────────────────────────────────────────────────────────────────────

function NewsReport({ open, post, apiBaseUrl, headers, onClose }) {
    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(false);
    const [onlyPending, setOnlyPending] = useState(false);

    useEffect(() => {
        if (!open || !post?.id) { setState(null); return; }
        setLoading(true);
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}/report`, { headers })
            .then((r) => setState(r.data))
            .catch(() => setState(null))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, open, post?.id]);

    /* Что у новости есть проходить (задача #342): от этого зависят счётчики в
       шапке и отметки в строках. Без теста и тренажёра журнал выглядит как
       прежде — лишних пустых колонок у простого объявления нет. */
    const hasQuiz = (post?.quiz_count || 0) > 0;
    const hasTrainer = !!post?.trainer_key;

    const rows = useMemo(() => {
        const items = state?.items || [];
        // «Только непрочитавшие» — про тех, от кого ещё ждут: человек, который
        // из адресатов выбыл, в этот список не попадает, дожимать его незачем.
        return onlyPending
            ? items.filter((row) => !row.confirmed_at && row.in_audience)
            : items;
    }, [state, onlyPending]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Кто прочитал"
            subtitle={post?.title}
            maxWidth="max-w-xl"
            footer={<button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button>}
        >
            {loading && (
                <p className="py-8 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Считаем
                </p>
            )}
            {!loading && state && (
                <>
                    <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                            <p className="text-[14px] text-slate-900 tabular-nums">
                                Подтвердили {state.confirmed} из {state.total}
                            </p>
                            {/* Подтвердившие, которых в адресатах уже нет,
                                в знаменатель не идут, но и не исчезают: журнал
                                читают при разборе, задним числом. */}
                            {state.confirmed_outside > 0 && (
                                <p className="text-[12px] text-slate-400 tabular-nums">
                                    и ещё {state.confirmed_outside} — из тех, кто больше не в адресатах
                                </p>
                            )}
                            {(hasQuiz || hasTrainer) && (
                                <p className="text-[12px] text-slate-500 tabular-nums">
                                    {[hasTrainer ? `тренажёр прошли ${state.trainer_passed || 0}` : null,
                                      hasQuiz ? `тест прошли ${state.quiz_passed || 0}` : null]
                                        .filter(Boolean).join(' · ')}
                                </p>
                            )}
                        </div>
                        <button
                            type="button"
                            onClick={() => setOnlyPending((value) => !value)}
                            className={`rounded-full px-3 py-1 text-[12px] transition active:scale-[0.98] ${
                                onlyPending ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'}`}
                        >
                            Только непрочитавшие
                        </button>
                    </div>
                    <div className="mt-3 space-y-1">
                        {rows.length === 0 && (
                            <p className="py-6 text-center text-[13px] text-slate-400">
                                {onlyPending ? 'Прочитали все' : 'Адресатов нет'}
                            </p>
                        )}
                        {rows.map((row) => (
                            <div key={row.user_id}
                                 className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 hover:bg-slate-50">
                                <div className="min-w-0">
                                    <p className="truncate text-[14px] text-slate-900">{row.name}</p>
                                    <p className="truncate text-[12px] text-slate-400">
                                        {[roleTitle(row.role), row.department_name,
                                          row.in_audience ? null : 'уже не в адресатах']
                                            .filter(Boolean).join(' · ')}
                                    </p>
                                </div>
                                {/* Обе стороны — серой подписью.
                                    Плашка на непрочитавшем выглядела разумно на
                                    одной строке и превращалась в стену цвета на
                                    восемнадцати: цвет, который стоит у половины
                                    списка, не значит ничего. Кто не прочитал,
                                    видно и так — они наверху (сортировка) и
                                    посчитаны в шапке. */}
                                <span className="flex shrink-0 flex-col items-end gap-0.5">
                                    <span className="text-[12px] tabular-nums text-slate-400">
                                        {row.confirmed_at
                                            ? publishedLabel(row.confirmed_at)
                                            : (row.shown_at ? 'открыл, не подтвердил' : 'не видел')}
                                    </span>
                                    {/* Пройденное — галочкой, непройденное не пишем
                                        вовсе: «не прошёл» у половины строк стало бы
                                        той же стеной серого, от которой ушли выше. */}
                                    {((hasTrainer && row.trainer_passed_at) || (hasQuiz && row.quiz_passed_at)) && (
                                        <span className="flex items-center gap-2 text-[11.5px] text-slate-500">
                                            {hasTrainer && row.trainer_passed_at && (
                                                <span className="inline-flex items-center gap-0.5">
                                                    <Check className="h-3 w-3 text-emerald-600" strokeWidth={3} aria-hidden="true" />
                                                    тренажёр
                                                </span>
                                            )}
                                            {hasQuiz && row.quiz_passed_at && (
                                                <span className="inline-flex items-center gap-0.5">
                                                    <Check className="h-3 w-3 text-emerald-600" strokeWidth={3} aria-hidden="true" />
                                                    тест
                                                </span>
                                            )}
                                        </span>
                                    )}
                                </span>
                            </div>
                        ))}
                    </div>
                </>
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
                toastRef.current?.(payload.publish ? 'Новость опубликована' : 'Черновик сохранён',
                                   'success');
                /* Уводим в ту корзину, где сохранённое теперь лежит: иначе
                   черновик, сохранённый со вкладки «Опубликованные», исчезает
                   без следа — список перечитывается, а его в нём нет. */
                const target = payload.publish ? 'published' : 'draft';
                if (bucket !== target) setBucket(target);
                else load();
            })
            .catch((e) => toastRef.current?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving(false));
    }, [apiBaseUrl, headers, formPost, bucket, load, closeCompose, spaceId]);

    const act = useCallback((post, action) => {
        const url = `${apiBaseUrl}/api/news/posts/${post.id}${action === 'delete' ? '' : `/${action}`}`;
        const request = action === 'delete'
            ? axios.delete(url, { headers })
            : axios.post(url, {}, { headers });
        request
            .then(() => {
                toastRef.current?.({
                    publish: 'Новость опубликована',
                    archive: 'Новость снята с показа',
                    delete: 'Черновик удалён',
                }[action], 'success');
                load();
            })
            .catch((e) => toastRef.current?.(errText(e, 'Не получилось'), 'error'));
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
                            {post.status === 'draft' && <IosBadge tone="slate">черновик</IosBadge>}
                            {post.status === 'archived' && <IosBadge tone="slate">снята</IosBadge>}
                            {!post.is_mandatory && <IosBadge tone="slate">необязательная</IosBadge>}
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
                              publishedLabel(post.published_at || post.created_at)]
                                .filter(Boolean).join(' · ')}
                        </p>
                        {post.status === 'published' && hasJournal(post) && (
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
                            ...(post.can_edit && post.status !== 'published'
                                ? [{ key: 'publish', label: 'Опубликовать',
                                     onSelect: () => act(post, 'publish') }]
                                : []),
                            ...(post.can_take_down && post.status === 'published'
                                ? [{ key: 'archive', label: 'Снять с показа',
                                     onSelect: () => act(post, 'archive') }]
                                : []),
                            ...(post.status === 'published' && hasJournal(post)
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
