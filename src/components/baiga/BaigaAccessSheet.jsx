import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Loader2, Plus, RotateCw, Trash2 } from 'lucide-react';
import {
    IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    LEVELS, LEVEL_HINT, MAX_SUBJECTS, buildRecipients, circleRows, circleSummary, grantBody, grantMeta,
    grantTitle, grantToast, grantedLevels, levelBody, levelOf, replacingCount, replacingNote,
} from './baigaAccess';
import { fmtStamp } from './baigaMeta';

/*
 * Лист «Доступ» — кому открыт раздел «Списки Байги» и кому его выдать.
 *
 * Просьба владельца 07.10.2026: «в самом разделе можно было раздавать доступы…
 * группам, людям и отделам, примерно как в вики структуре». Отсюда форма — та
 * же, что у «Доступа к разделу» во вкладке «Структура» вики
 * (WikiSectionAccess.jsx): одно окно и два уровня, как в «Настройках» iOS.
 * Первый — список «кому открыт», одной строкой на адресата; второй — либо
 * выдача (адресатов отмечают галочками, сколько нужно, уровень один на всех),
 * либо одна готовая выдача: сменить уровень или снять.
 *
 * Кому раздел открыт и без выдач (супер-админы, «Маркетинг», ОП, СЗоВ —
 * baiga/access.py), в списке стоит ОДНОЙ строкой «Открыт по умолчанию» и
 * раскрывается своим экраном: это задано правилом и отсюда не меняется, а пять
 * запертых строк над выдачами читались бы как пять вещей, которые надо
 * настроить. Выдача только добавляет к этому кругу.
 *
 * Каждое сохранение — один запрос, и ответ несёт свежий список выдач: второго
 * запроса «перечитать» нет.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

/* Навигационная строка в духе «Настроек»: слева адресат, справа одно слово о
   выданном и шеврон. */
const AccessRow = ({ title, meta, value = '', muted = false, onOpen }) => (
    <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100"
    >
        <div className="min-w-0 grow basis-0">
            <div className={`truncate text-[14px] font-medium ${muted ? 'text-slate-400' : 'text-slate-900'}`}>
                {title}
            </div>
            {meta && <div className="mt-0.5 truncate text-[11.5px] text-slate-400">{meta}</div>}
        </div>
        {value && (
            <span className={`shrink-0 text-[13px] ${muted ? 'text-slate-400' : 'text-slate-700'}`}>{value}</span>
        )}
        <ChevronRight size={16} className="shrink-0 text-slate-300" />
    </button>
);

/* «Что разрешено» — сегменты. Чем уровни отличаются друг от друга — под «i»,
   а не строкой под полем: это нужно один раз. «i» — общий InfoHint: он
   рисуется порталом, и короткое окно выдачи его не обрезает (пузырь IosHint
   внутри тела окна терял нижние строки). */
const LevelPicker = ({ value, onChange }) => (
    <section className="space-y-2">
        <div className="flex items-center gap-1.5">
            <span className={iosGroupLabel}>Что разрешено</span>
            <InfoHint side="left">
                <div className="space-y-1.5">
                    <div>{LEVEL_HINT.intro}</div>
                    <div className="space-y-1">
                        {LEVEL_HINT.options.map(([name, meaning]) => (
                            <div key={name}>
                                <span className="font-semibold text-slate-800">{name}</span> — {meaning}
                            </div>
                        ))}
                    </div>
                    <div className="text-slate-500">{LEVEL_HINT.outro}</div>
                </div>
            </InfoHint>
        </div>
        <IosSegmented
            value={value}
            options={LEVELS.map((level) => ({ value: level.key, label: level.label }))}
            size="lg"
            ariaLabel="Что разрешено"
            onChange={onChange}
        />
    </section>
);

const BaigaAccessSheet = ({ open, onClose, apiBaseUrl, headers, toast }) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    /* Отказ загрузки — отдельное состояние, а не пустой список: сорвавшийся
       запрос иначе показывал бы уверенное «никому не выдано». */
    const [failed, setFailed] = useState('');
    const [busy, setBusy] = useState(false);
    /* Второй уровень окна. Одновременно открыт ровно один: circleOpen — кому
       раздел открыт по умолчанию, draft — выдача (новая или готовая). Правка
       идёт в копии: «Отмена» ничего не откатывает руками. */
    const [circleOpen, setCircleOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    // Направление перехода: вперёд экран приезжает справа, назад — слева.
    const [anim, setAnim] = useState('');
    const narrow = useIsMobileShell();

    const load = useCallback(() => {
        setLoading(true);
        setFailed('');
        axios.get(`${apiBaseUrl}/api/baiga/access`, { headers: headers() })
            .then((response) => setData(response.data || {}))
            .catch((requestError) => setFailed(errorOf(requestError, 'Не удалось загрузить доступ')))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers]);

    // Каждое открытие — свежие данные: выдачи правят несколько человек.
    useEffect(() => {
        if (!open) return;
        setCircleOpen(false);
        setDraft(null);
        setAnim('');
        load();
    }, [open, load]);

    const grants = useMemo(() => data?.grants || [], [data]);
    const circle = useMemo(() => circleRows(data?.circle || []), [data]);
    const recipients = useMemo(() => buildRecipients(data?.catalog || {}), [data]);
    const recipientByKey = useMemo(() => new Map(recipients.map((item) => [item.key, item])), [recipients]);
    const granted = useMemo(() => grantedLevels(grants), [grants]);
    /* Кому раздел уже выдан — видно прямо в списке: уровень приглушённым
       словом справа от строки, как сочетание клавиш в меню macOS. */
    const recipientOptions = useMemo(() => recipients.map((item) => ({
        value: item.key, label: item.label, groupLabel: item.groupLabel,
        meta: levelOf(granted.get(item.key))?.label,
    })), [recipients, granted]);
    const maxSubjects = data?.max_subjects || MAX_SUBJECTS;

    /* Справочник перечитан (адресата убрали, пока лист был открыт) — из
       отмеченных уходят те, кого в нём больше нет: иначе в перечне выбранного
       остался бы сырой ключ, а снять его галочкой было бы негде. */
    useEffect(() => {
        setDraft((current) => {
            if (!current || current.grant) return current;
            const kept = current.subjects.filter((key) => recipientByKey.has(key));
            return kept.length === current.subjects.length ? current : { ...current, subjects: kept };
        });
    }, [recipientByKey]);

    const leave = () => {
        setAnim('animate-pop-in');
        setCircleOpen(false);
        setDraft(null);
    };

    /* Пока идёт сохранение, с экрана не уходят: ответ вернул бы человека в
       список уже с ДРУГОГО экрана, вместе с несохранённым там выбором. */
    const goBack = () => { if (!busy) leave(); };

    const openCircle = () => { setAnim('animate-push-in'); setCircleOpen(true); };

    const openGrant = (grant) => {
        setAnim('animate-push-in');
        setDraft(grant
            ? { grant, level: grant.level }
            // У новой выдачи уровень назван сразу: «Чтение» — наименьшее из
            // возможного, а не молчаливое «ничего».
            : { grant: null, subjects: [], level: LEVELS[0].key });
    };

    const changed = draft && (draft.grant
        ? draft.level !== draft.grant.level
        : draft.subjects.length > 0);
    const replacing = draft && !draft.grant ? replacingCount(draft.subjects, granted, draft.level) : 0;

    /* Сохранение и снятие отвечают свежим списком выдач — кладём его на место
       и возвращаемся в список. На отказе экран остаётся открытым: иначе
       несохранённый выбор пропал бы вместе с ним. */
    const run = (request, message) => {
        if (busy) return;
        setBusy(true);
        request
            .then((response) => {
                setData((prev) => ({ ...(prev || {}), grants: response.data?.grants || [] }));
                toast(typeof message === 'function' ? message(response.data || {}) : message, 'success');
                leave();
            })
            .catch((requestError) => {
                toast(errorOf(requestError, 'Не удалось сохранить доступ'), 'error');
                const status = requestError?.response?.status;
                // Выдачу успели снять: её экран больше ни к чему — в список.
                if (status === 404) leave();
                // И список, и справочник на экране устарели — перечитываем.
                if (status === 404 || status === 422) load();
            })
            .finally(() => setBusy(false));
    };

    const save = () => {
        if (draft.grant) {
            run(axios.patch(`${apiBaseUrl}/api/baiga/access/grants/${draft.grant.id}`,
                levelBody(draft.level), { headers: headers() }), 'Доступ изменён');
            return;
        }
        run(axios.post(`${apiBaseUrl}/api/baiga/access/grants`,
            grantBody(draft.subjects, recipientByKey, draft.level), { headers: headers() }), grantToast);
    };

    const revoke = () => run(
        axios.delete(`${apiBaseUrl}/api/baiga/access/grants/${draft.grant.id}`, { headers: headers() }),
        'Доступ снят');

    /* Крестик и клик мимо окна закрывают его целиком — на экране выдачи это
       уносило бы отмеченных адресатов молча. Пока выбор не сохранён, первый
       выход возвращает в список; ничего не изменено — закрываем сразу.

       На телефоне сюда приходит системный жест «назад» (IosModal): он ведёт на
       уровень назад, как шеврон в шапке, а не закрывает лист со второго экрана.
       Ответ false — «остаюсь»: запись жеста возвращается на место
       (useScreenBackGesture), иначе следующий жест прошёл бы мимо листа. */
    const handleClose = () => {
        if (busy) return false;
        if ((draft && changed) || (narrow && (circleOpen || draft))) { leave(); return false; }
        onClose?.();
        return undefined;
    };

    let title = 'Доступ к разделу';
    let subtitle = 'Списки Байги';
    let footer = <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>;

    if (circleOpen) {
        title = 'Открыт по умолчанию';
        subtitle = 'Задано правилом раздела — здесь не меняется';
        footer = <button type="button" className={iosBtnSecondary} onClick={goBack}>Назад</button>;
    } else if (draft) {
        title = draft.grant ? grantTitle(draft.grant) : 'Выдать доступ';
        subtitle = draft.grant ? grantMeta(draft.grant) : 'Списки Байги';
        footer = (
            <>
                {draft.grant && (
                    <button
                        type="button"
                        disabled={busy}
                        onClick={revoke}
                        className="mr-auto inline-flex items-center gap-1.5 rounded-xl px-3 py-2.5 text-[13.5px] font-semibold text-rose-600 transition hover:bg-rose-50 active:scale-[0.98] disabled:opacity-50"
                    >
                        <Trash2 size={14} /> Снять доступ
                    </button>
                )}
                <button type="button" className={iosBtnSecondary} disabled={busy} onClick={goBack}>
                    {changed ? 'Отмена' : 'Назад'}
                </button>
                <button type="button" className={iosBtnPrimary} disabled={busy || !changed} onClick={save}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                </button>
            </>
        );
    }

    const screen = circleOpen ? 'circle' : draft ? `grant:${draft.grant?.id || 'new'}` : 'list';

    return (
        <IosModal
            open={open}
            onClose={handleClose}
            onBack={circleOpen || draft ? goBack : null}
            title={title}
            subtitle={subtitle}
            maxWidth="max-w-xl"
            footer={footer}
        >
            {/* key переклеивает содержимое на каждом переходе — иначе анимация
                проигралась бы один раз за всю жизнь окна. */}
            <div key={screen} className={anim}>

                {/* ── Открыт по умолчанию ─────────────────────────────────── */}
                {circleOpen && (
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {circle.map((row) => (
                            <div key={row.key} className="flex items-center gap-3 px-4 py-3">
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[14px] font-medium text-slate-900">{row.title}</div>
                                    {row.meta && (
                                        <div className="mt-0.5 truncate text-[11.5px] text-slate-400">{row.meta}</div>
                                    )}
                                </div>
                                <span className="shrink-0 text-[13px] text-slate-700">{row.value}</span>
                            </div>
                        ))}
                    </div>
                )}

                {/* ── Выдача: новая или готовая ───────────────────────────── */}
                {draft && (
                    <div className="space-y-4">
                        {!draft.grant && (
                            <section className="space-y-1.5">
                                <div className={iosGroupLabel}>Кому</div>
                                {/* Селект без обёртки-карточки: у варианта ios своя
                                    белая плашка с кантом, внутри iosCard вышли бы две
                                    рамки в трёх пикселях друг от друга. */}
                                <CustomSelect
                                    variant="ios"
                                    multiple
                                    searchable
                                    value={draft.subjects}
                                    onChange={(next) => setDraft({ ...draft, subjects: next })}
                                    options={recipientOptions}
                                    maxSelected={maxSubjects}
                                    placeholder="Группы, отделы, люди…"
                                    searchPlaceholder="Поиск по названию или ФИО…"
                                    ariaLabel="Кому открыть раздел"
                                    renderValue={(keys) => `Выбрано: ${keys.length}`}
                                />
                                {/* Кого именно отметили: «Выбрано: 4» отвечает
                                    сколько, но не кому, а открывать закрытый список
                                    второй раз ради проверки никто не станет. */}
                                {draft.subjects.length > 0 && (
                                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                                        {draft.subjects.map((key) => recipientByKey.get(key)?.label || key).join(' · ')}
                                    </p>
                                )}
                                {/* Выдача поверх готовой меняет её уровень. Молча
                                    сменить чужую настройку — человек думает, что
                                    добавил, а он заменил. */}
                                {replacing > 0 && (
                                    <p className="px-1 text-[11.5px] leading-relaxed text-amber-700">
                                        {replacingNote(replacing)}
                                    </p>
                                )}
                            </section>
                        )}

                        <LevelPicker value={draft.level} onChange={(level) => setDraft({ ...draft, level })} />

                        {draft.grant && (draft.grant.granted_by_name || draft.grant.granted_at) && (
                            <p className="px-1 text-[11.5px] text-slate-400">
                                {['Выдано', draft.grant.granted_by_name, fmtStamp(draft.grant.granted_at)]
                                    .filter(Boolean).join(' · ')}
                            </p>
                        )}
                    </div>
                )}

                {/* ── Список ──────────────────────────────────────────────── */}
                {!circleOpen && !draft && (
                    <section className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Кому открыт</span>
                            <InfoHint
                                side="left"
                                text="Первая строка — кому раздел открыт по умолчанию: это задано правилом и здесь не меняется. Ниже — кому он выдан дополнительно: группе, отделу или человеку. Выдача только добавляет доступ."
                            />
                        </div>
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {loading && !data ? (
                                <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                                    <Loader2 size={16} className="animate-spin" />
                                    <span className="text-[13px]">Загружаем…</span>
                                </div>
                            ) : failed ? (
                                <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                                    <p className="text-[12.5px] leading-relaxed text-slate-500">{failed}</p>
                                    <button type="button" className={iosBtnSecondary} onClick={load}>
                                        <RotateCw size={14} /> Повторить
                                    </button>
                                </div>
                            ) : (
                                <>
                                    <AccessRow
                                        title="Открыт по умолчанию"
                                        meta={circleSummary(data?.circle || [])}
                                        onOpen={openCircle}
                                    />
                                    {grants.map((grant) => (
                                        <AccessRow
                                            key={grant.id}
                                            title={grantTitle(grant)}
                                            meta={grantMeta(grant)}
                                            value={levelOf(grant.level)?.summary || grant.level}
                                            muted={grant.active === false}
                                            onOpen={() => openGrant(grant)}
                                        />
                                    ))}
                                    {/* Кнопка — последней строкой списка: она
                                        продолжает перечень, а не висит под ним. */}
                                    <button
                                        type="button"
                                        onClick={() => openGrant(null)}
                                        className="flex w-full items-center gap-2 px-4 py-3 text-left text-[13.5px] font-medium text-blue-600 transition hover:bg-slate-50 active:bg-slate-100"
                                    >
                                        <Plus size={16} className="shrink-0" />
                                        Выдать доступ
                                    </button>
                                </>
                            )}
                        </div>
                    </section>
                )}
            </div>
        </IosModal>
    );
};

export default BaigaAccessSheet;
