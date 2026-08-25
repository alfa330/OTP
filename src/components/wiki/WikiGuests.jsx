import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, CalendarClock, Clock, FileText, FolderTree, KeyRound, Loader2,
    RotateCw, Search, ShieldOff, UserPlus, Users,
} from 'lucide-react';

import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary,
    IosBadge, IosModal, IosSegmented, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import IosTimePicker from '../ui/TimePicker';
import SectionTreeSelect from './SectionTreeSelect';
import useStableCallback from './useStableCallback';
import {
    STATUS_FILTERS, STATUS_META, clampDate, daysLeftLabel, fmtDeadline,
    plural, presetLabel, presetsWithin, targetLabel, urgency,
} from './guestAccess';

/* Четвёртая половина вкладки «Статьи»: гостевой доступ.
 *
 * Механика в вики жила с самого начала и работала — на чтение. Раздел и статья,
 * выданные гостю, попадали в периметр, истёкшая и отозванная выдачи отсекались.
 * Не было ВЫДАЮЩЕЙ стороны: выдать доступ было физически нечем, и таблица
 * лежала пустой. Этот экран — та самая дверь.
 *
 * Почему половина, а не вкладка. Гостевой доступ — это ответ на вопрос «кому
 * ещё показать то, что у меня лежит», то есть продолжение работы «что лежит»
 * и «как разложено». Отдельным пунктом меню он стал бы пятой вкладкой с одной
 * таблицей внутри, и открывали бы его так же редко, как и любой другой пункт,
 * который надо вспомнить.
 *
 * ТРИ ГРАНИЦЫ ВЫДАЧИ СЧИТАЕТ СЕРВЕР, а не эта форма (wiki/guests.py):
 *   право   — должность: директор всем, руководитель супервайзерам и
 *             операторам, супервайзер операторам;
 *   объект  — раздел или статья из ветки своего отдела;
 *   человек — СВОЙ подчинённый: и по чину, и по отделу.
 * Форма только показывает то, что сервер уже отфильтровал: списки «кому» и
 * «что» приезжают готовыми. Считать границы во второй раз здесь значило бы
 * однажды предложить то, что сервер отвергнет, — молчаливый отказ с обратной
 * стороны стола, от которого этот раздел лечили дважды.
 *
 * КАЛЕНДАРЬ ТОЖЕ СЧИТАЕТ СЕРВЕР. «Сегодня» у браузера и «сегодня» в Алматы —
 * разные дни западнее нас, и пикер, построенный от new Date(), предложил бы
 * дату, которую сервер тут же отвергнет со словами «уже прошла». Рамки
 * приезжают полями today и max_until (см. guestAccess.js).
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const ROLE_TITLE = {
    operator: 'оператор', trainee: 'стажёр', trainer: 'тренер',
    sv: 'супервайзер', supervisor: 'супервайзер',
    admin: 'руководитель', super_admin: 'директор',
};

/* Тон срока. Три состояния, а не градиент: «ещё нескоро», «вот-вот» и «уже
   нет». Больше оттенков на дате — это шум, из которого не следует действия. */
const URGENCY_TONE = {
    calm: 'text-slate-500',
    soon: 'text-amber-600',
    gone: 'text-slate-400',
};

const Empty = ({ icon: Icon, title, hint }) => (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
        <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
            <Icon size={19} />
        </div>
        <div className="text-[14px] font-semibold text-slate-900">{title}</div>
        <p className="max-w-md text-[12.5px] leading-relaxed text-slate-500">{hint}</p>
    </div>
);

/* Выбор срока — общий для выдачи и для продления.
 *
 * Пресеты и дата стоят рядом и исключают друг друга: выбрал кнопку — дата
 * гаснет, выбрал дату — гаснет кнопка. Иначе в форме два ответа на один вопрос,
 * и какой из них уедет на сервер, видно только из кода (сервер такое и не
 * принимает: resolve_expiry отвергает оба поля сразу).
 */
const TermPicker = ({ meta, days, until, atTime, onDays, onUntil, onTime }) => {
    /* Час ограничиваем снизу только на СЕГОДНЯШНЕМ дне: прошедшие часы сервер
       всё равно отвергнет («это время уже прошло»), и предлагать их — обещать
       отказ. «Сегодня» — это либо пресет «сегодня» (0 дней), либо дата, равная
       сегодняшней; на любом другом дне доступен весь сутки. */
    const today = meta?.today;
    const isToday = until ? until === today : days === 0;
    return (
    <div className="space-y-2.5">
        <div className={iosGroupLabel}>На сколько</div>
        <div className="flex flex-wrap items-center gap-1.5">
            {presetsWithin(meta?.max_days).map((value) => {
                const on = days === value && !until;
                return (
                    <button
                        key={value}
                        type="button"
                        aria-pressed={on}
                        onClick={() => { onDays(value); onUntil(''); }}
                        className={`rounded-xl px-3 py-1.5 text-[12.5px] font-medium transition ${
                            on ? 'bg-indigo-600 text-white shadow-sm'
                               : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                    >
                        {presetLabel(value)}
                    </button>
                );
            })}
            <div className="ml-auto w-full sm:ml-2 sm:w-auto">
                <IosDatePicker
                    value={until}
                    min={meta?.today}
                    max={meta?.max_until}
                    allowEmpty
                    placeholder="до даты"
                    ariaLabel="Дата, до которой действует гостевой доступ"
                    /* Панель умеет отдать день мимо min/max своими пресетами
                       (ловушка из OfficeDayModal) — подтягиваем сами. */
                    onChange={(iso) => onUntil(clampDate(iso, meta?.today, meta?.max_until))}
                />
            </div>
        </div>

        {/* Час — УТОЧНЕНИЕ выбранного дня, а не отдельный вид срока, поэтому он
            стоит строкой ниже и по умолчанию пуст. Пустой = до конца дня: это
            и есть обычная выдача, и заставлять называть 23:59 незачем. */}
        <div className={`${iosCard} flex flex-wrap items-center gap-3 p-3`}>
            <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-slate-900">До какого часа</div>
                <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                    Не указан — доступ живёт до конца дня. «Сегодня до 18:00» — это
                    пресет «сегодня» и час здесь.
                </p>
            </div>
            <IosTimePicker
                value={atTime}
                onChange={onTime}
                min={isToday ? meta?.now_time : undefined}
                allowEmpty
                step={30}
                placeholder="до конца дня"
                ariaLabel="Час, до которого действует гостевой доступ"
                className="w-full sm:w-32"
            />
        </div>

        <p className="text-[11.5px] leading-relaxed text-slate-500">
            Дольше {meta?.max_days || 14}
            {' '}{plural(meta?.max_days || 14, 'дня', 'дней', 'дней')} выдать нельзя:
            бессрочный «гостевой» доступ перестаёт быть гостевым и подменяет собой
            правило раздела.
        </p>
    </div>
    );
};

/* Одна строка списка. Три вопроса подряд, в том же порядке, в каком их задают:
   кому открыто, что открыто, до какого срока — и только потом кто выдал. */
const GrantRow = ({ item, onExtend, onRevoke, busy }) => {
    const status = STATUS_META[item.status] || STATUS_META.expired;
    const tone = URGENCY_TONE[urgency(item.days_left)] || URGENCY_TONE.calm;
    const active = item.status === 'active';
    return (
        <div className="flex flex-wrap items-start gap-3 px-4 py-3.5">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                {item.kind === 'article' ? <FileText size={16} /> : <FolderTree size={16} />}
            </div>

            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[13.5px] font-semibold text-slate-900">
                        {item.user_name}
                    </span>
                    <IosBadge tone={status.tone}>{status.label}</IosBadge>
                </div>
                <div className="mt-0.5 truncate text-[11.5px] text-slate-500">
                    {[ROLE_TITLE[item.user_role] || item.user_role, item.user_department]
                        .filter(Boolean).join(' · ')}
                </div>
                <div className="mt-1.5 truncate text-[12.5px] text-slate-700">
                    {targetLabel(item)}
                </div>
                {item.reason && (
                    <div className="mt-1 truncate text-[11.5px] italic text-slate-500">
                        {item.reason}
                    </div>
                )}
            </div>

            <div className="min-w-[9.5rem] shrink-0 text-right">
                <div className="text-[12.5px] font-medium tabular-nums text-slate-900">
                    до {fmtDeadline(item.expires_at)}
                </div>
                <div className={`mt-0.5 text-[11px] ${tone}`}>
                    {item.status === 'revoked'
                        ? `отозвал ${item.revoked_by_name || '—'}`
                        : daysLeftLabel(item.days_left, item.expires_at)}
                </div>
                <div className="mt-0.5 text-[10.5px] text-slate-400">
                    выдал {item.granted_by_name || '—'}
                </div>
            </div>

            {/* Кнопки только у действующей выдачи: продлевать истёкшую или
                отзывать отозванную нечего, а серая кнопка «ничего не делает»
                читается как поломка. */}
            {active && (
                <div className="flex w-full shrink-0 gap-2 sm:w-auto">
                    <button type="button" disabled={busy} onClick={() => onExtend(item)}
                            className={iosBtnSecondary}>
                        <CalendarClock size={14} /> Продлить
                    </button>
                    <button type="button" disabled={busy} onClick={() => onRevoke(item)}
                            className={`${iosBtnSecondary} !text-rose-600`}>
                        <ShieldOff size={14} /> Отозвать
                    </button>
                </div>
            )}
        </div>
    );
};

export default function WikiGuests({ base, headers, space = null, showToast = null }) {
    const toast = useStableCallback(showToast);
    const spaceId = space?.id || null;

    const [items, setItems] = useState([]);
    const [meta, setMeta] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [status, setStatus] = useState('active');
    const [query, setQuery] = useState('');
    const [busy, setBusy] = useState(false);

    const [granting, setGranting] = useState(false);
    const [extending, setExtending] = useState(null);
    const [revoking, setRevoking] = useState(null);

    const load = useCallback(() => {
        setLoading(true);
        /* Поиск уезжает на сервер только от двух символов — как в журнале: по
           одной букве ILIKE перебирает таблицу и всё равно возвращает почти всё.
           Короткий запрос отдаём как пустой, а не глотаем: список тогда просто
           не сужается, вместо того чтобы замереть без объяснения. */
        const q = query.trim().length >= 2 ? query.trim() : '';
        return axios.get(`${base}/guests`, { headers, params: { space_id: spaceId, q } })
            .then((r) => {
                setItems(r.data?.items || []);
                setMeta(r.data || null);
                setError('');
            })
            .catch((e) => {
                setItems([]);
                setError(errText(e, 'Не удалось загрузить выдачи'));
            })
            .finally(() => setLoading(false));
    }, [base, headers, spaceId, query]);

    /* Поиск с задержкой. Зависимость — только load, и она стабильна по
       useCallback выше; showToast сюда не попадает намеренно (новая функция на
       каждый рендер App перезапрашивала бы список на любой чужой рендер). */
    useEffect(() => {
        const timer = setTimeout(load, query.trim() ? 300 : 0);
        return () => clearTimeout(timer);
    }, [load, query]);

    const visible = useMemo(
        () => items.filter((item) => item.status === status), [items, status]);

    const counts = useMemo(() => {
        const result = { active: 0, expired: 0, revoked: 0 };
        items.forEach((item) => { result[item.status] = (result[item.status] || 0) + 1; });
        return result;
    }, [items]);

    const revoke = (item) => {
        setBusy(true);
        axios.delete(`${base}/guests/${item.id}`, { headers })
            .then(() => { toast?.('Гостевой доступ отозван', 'success'); setRevoking(null); load(); })
            .catch((e) => toast?.(errText(e, 'Не удалось отозвать доступ'), 'error'))
            .finally(() => setBusy(false));
    };

    return (
        <div className="space-y-4">
            <div className={`${iosCard} flex flex-wrap items-center gap-3 px-4 py-3.5`}>
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                    <KeyRound size={17} />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-[14px] font-semibold text-slate-900">Гостевой доступ</div>
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                        Временный доступ к разделу или статье вашего отдела — своим
                        подчинённым: на срок до
                        {' '}{meta?.max_days || 14} {plural(meta?.max_days || 14, 'дня', 'дней', 'дней')}
                        {' '}или до названного часа, хоть до 18:00 сегодня.
                    </p>
                </div>
                {meta?.can_grant && (
                    <button type="button" className={iosBtnPrimary}
                            onClick={() => setGranting(true)}>
                        <UserPlus size={15} /> Выдать доступ
                    </button>
                )}
            </div>

            {/* Выдавать нельзя — объясняем ПОЧЕМУ, а не прячем экран: человек
                пришёл сюда осознанно, и пустой список без объяснения он
                прочитает как «выдач нет». Причины две, и чинятся они разным:
                нет права по должности — или права хватает, а в своей ветке
                отдела открывать нечего. */}
            {meta && !meta.can_grant && (
                <div className="flex items-start gap-3 rounded-2xl bg-amber-50/70 px-4 py-3.5 ring-1 ring-amber-200">
                    <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-600" />
                    <div className="min-w-0 text-[12.5px] leading-relaxed text-amber-900">
                        {meta.may_grant_by_role
                            ? 'В вашей ветке отдела нет разделов, которые можно открыть гостю. Ниже видны выдачи, которые вы делали раньше.'
                            : 'Гостевой доступ выдают супервайзер и выше. Ниже видны выдачи, которые вы делали раньше.'}
                    </div>
                </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
                <IosSegmented
                    value={status}
                    onChange={setStatus}
                    ariaLabel="Какие выдачи показывать"
                    options={STATUS_FILTERS.map((f) => ({
                        value: f.key,
                        label: f.label,
                        // count — родное поле IosSegmented: оно само рисует
                        // число приглушённым и прячет ноль. Склеенное в подпись,
                        // оно читалось бы как часть названия фильтра.
                        count: counts[f.key],
                    }))}
                />
                <div className="relative ml-auto w-full sm:w-64">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Сотрудник или объект"
                        aria-label="Поиск по выдачам"
                        className={`${iosInput} pl-9`}
                    />
                </div>
                <button type="button" onClick={load} className={iosBtnSecondary}>
                    <RotateCw size={14} /> Обновить
                </button>
            </div>

            <div className={iosCard}>
                {loading ? (
                    <div className="flex items-center justify-center gap-2 px-4 py-14 text-[12.5px] text-slate-500">
                        <Loader2 size={15} className="animate-spin" /> Загружаем выдачи…
                    </div>
                ) : error ? (
                    <Empty icon={AlertCircle} title="Не удалось загрузить" hint={error} />
                ) : !visible.length ? (
                    <Empty
                        icon={status === 'active' ? Users : Clock}
                        title={status === 'active' ? 'Действующих выдач нет'
                                                   : `${STATUS_FILTERS.find((f) => f.key === status)?.label} — пусто`}
                        hint={status === 'active'
                            ? (meta?.can_grant
                                ? 'Нажмите «Выдать доступ», выберите сотрудника и раздел — он увидит его у себя в вики до конца срока.'
                                : 'Здесь появятся выдачи по разделам вашей ветки отдела — и ваши собственные.')
                            : 'История выдач не удаляется: отозванная и истёкшая строка остаются здесь.'}
                    />
                ) : (
                    <div className="divide-y divide-slate-100">
                        {visible.map((item) => (
                            <GrantRow
                                key={item.id}
                                item={item}
                                busy={busy}
                                onExtend={setExtending}
                                onRevoke={setRevoking}
                            />
                        ))}
                    </div>
                )}
            </div>

            {granting && (
                <GrantModal
                    base={base}
                    headers={headers}
                    meta={meta}
                    space={space}
                    onClose={() => setGranting(false)}
                    onDone={() => { setGranting(false); load(); }}
                    toast={toast}
                />
            )}

            {extending && (
                <ExtendModal
                    base={base}
                    headers={headers}
                    meta={meta}
                    item={extending}
                    onClose={() => setExtending(null)}
                    onDone={() => { setExtending(null); load(); }}
                    toast={toast}
                />
            )}

            <IosModal
                open={!!revoking}
                onClose={() => setRevoking(null)}
                title="Отозвать гостевой доступ"
                subtitle={revoking ? `${revoking.user_name} — ${targetLabel(revoking)}` : ''}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary}
                                onClick={() => setRevoking(null)}>Отмена</button>
                        <button type="button" disabled={busy}
                                className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`}
                                onClick={() => revoke(revoking)}>
                            {busy ? <Loader2 size={15} className="animate-spin" /> : <ShieldOff size={15} />}
                            {' '}Отозвать
                        </button>
                    </>
                )}
            >
                <p className="text-[12.5px] leading-relaxed text-slate-600">
                    Доступ пропадёт сразу — дожидаться конца срока не нужно. Строка
                    останется в списке во вкладке «Отозванные»: история выдач не
                    удаляется.
                </p>
            </IosModal>
        </div>
    );
}

/* ─────────────────────────────────────────────────────────────────────────
   Форма выдачи
   ───────────────────────────────────────────────────────────────────────── */

const GrantModal = ({ base, headers, meta, space, onClose, onDone, toast }) => {
    const [people, setPeople] = useState([]);
    const [targets, setTargets] = useState(null);
    const [targetsError, setTargetsError] = useState('');

    const [userId, setUserId] = useState('');
    const [kind, setKind] = useState('section');
    const [sectionId, setSectionId] = useState(null);
    const [articleId, setArticleId] = useState('');
    const [deep, setDeep] = useState(true);
    const [days, setDays] = useState(7);
    const [until, setUntil] = useState('');
    const [atTime, setAtTime] = useState('');
    const [reason, setReason] = useState('');
    const [busy, setBusy] = useState(false);

    const spaceId = space?.id || null;

    /* Оба справочника тянем при ОТКРЫТИИ формы, а не при монтировании экрана:
       список людей — это вся компания, и грузить его на каждый заход в раздел
       ради кнопки, которую нажимают раз в неделю, незачем. */
    useEffect(() => {
        axios.get(`${base}/guests/people`, { headers })
            .then((r) => setPeople(r.data?.items || []))
            .catch(() => setPeople([]));
    }, [base, headers]);

    useEffect(() => {
        axios.get(`${base}/guests/targets`, { headers, params: { space_id: spaceId } })
            .then((r) => { setTargets(r.data || null); setTargetsError(''); })
            .catch((e) => {
                setTargets({ sections: [], articles: [] });
                setTargetsError(errText(e, 'Не удалось загрузить разделы'));
            });
    }, [base, headers, spaceId]);

    const peopleOptions = useMemo(() => people.map((person) => ({
        value: String(person.id),
        label: [person.name, ROLE_TITLE[person.role] || person.role, person.department_name]
            .filter(Boolean).join(' · '),
    })), [people]);

    const articleOptions = useMemo(() => (targets?.articles || []).map((article) => ({
        value: String(article.id),
        label: article.title,
    })), [targets]);

    const chosenTarget = kind === 'section' ? sectionId : articleId;
    const ready = !!userId && !!chosenTarget && (!!days || !!until);

    const submit = () => {
        setBusy(true);
        axios.post(`${base}/guests`, {
            user_id: Number(userId),
            section_id: kind === 'section' ? sectionId : null,
            article_id: kind === 'article' ? Number(articleId) : null,
            include_subsections: kind === 'section' ? deep : false,
            // Ровно одно поле ДНЯ: сервер отвергает оба сразу, и это правильно —
            // умолчание пришлось бы выбрать за человека. Час к дню не относится
            // как альтернатива, он его уточняет, и едет отдельным полем.
            ...(until ? { until } : { days }),
            ...(atTime ? { at_time: atTime } : {}),
            reason: reason.trim() || null,
        }, { headers })
            .then((r) => {
                const label = r.data?.created ? 'Гостевой доступ выдан' : 'Срок доступа продлён';
                toast?.(`${label} — до ${fmtDeadline(r.data?.expires_at)}`, 'success');
                /* Оператору вики открывается только после QR-подтверждения
                   сессии, и подтверждать его нужно каждый раз заново. Выдача
                   этого не отменяет, и узнать про это выдающий обязан здесь, а
                   не от получателя через неделю. */
                if (r.data?.needs_qr) {
                    toast?.('Сотруднику с должностью «оператор» вики открывается '
                            + 'после QR-подтверждения доступа — предупредите его',
                            'error');
                }
                onDone();
            })
            .catch((e) => toast?.(errText(e, 'Не удалось выдать доступ'), 'error'))
            .finally(() => setBusy(false));
    };

    return (
        <IosModal
            open
            onClose={onClose}
            title="Выдать гостевой доступ"
            subtitle="Сотрудник увидит выбранное у себя в вики до конца срока"
            maxWidth="max-w-xl"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Отмена
                    </button>
                    <button type="button" className={iosBtnPrimary}
                            disabled={!ready || busy} onClick={submit}>
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <UserPlus size={15} />}
                        {' '}Выдать
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                <div className="space-y-2">
                    <div className={iosGroupLabel}>Кому</div>
                    <CustomSelect
                        variant="ios"
                        value={userId}
                        onChange={setUserId}
                        options={peopleOptions}
                        searchable
                        placeholder="Выберите сотрудника…"
                        searchPlaceholder="Поиск по имени, отделу, должности…"
                        ariaLabel="Кому выдать гостевой доступ"
                    />
                    <p className="text-[11.5px] leading-relaxed text-slate-500">
                        В списке только ваши подчинённые: сотрудники вашего отдела,
                        которым вы вправе открыть раздел по должности.
                    </p>
                </div>

                <div className="space-y-2">
                    <div className={iosGroupLabel}>Что открыть</div>
                    <IosSegmented
                        value={kind}
                        onChange={setKind}
                        ariaLabel="Раздел или статья"
                        options={[
                            { value: 'section', label: 'Раздел' },
                            { value: 'article', label: 'Статья' },
                        ]}
                    />

                    {targetsError ? (
                        <div className="rounded-xl bg-amber-50 px-3 py-2 text-[11.5px] leading-relaxed text-amber-900">
                            {targetsError}
                        </div>
                    ) : !targets ? (
                        <div className="flex items-center gap-2 px-1 py-2 text-[12px] text-slate-500">
                            <Loader2 size={14} className="animate-spin" /> Загружаем…
                        </div>
                    ) : kind === 'section' ? (
                        <SectionTreeSelect
                            sections={targets.sections || []}
                            spaces={space ? [space] : []}
                            value={sectionId}
                            onChange={setSectionId}
                        />
                    ) : (
                        <CustomSelect
                            variant="ios"
                            value={articleId}
                            onChange={setArticleId}
                            options={articleOptions}
                            searchable
                            placeholder={articleOptions.length ? 'Выберите статью…'
                                                              : 'Статей для выдачи нет'}
                            searchPlaceholder="Поиск по названию…"
                            ariaLabel="Какую статью открыть"
                            disabled={!articleOptions.length}
                        />
                    )}

                    <p className="text-[11.5px] leading-relaxed text-slate-500">
                        {kind === 'section'
                            ? 'В списке только разделы вашей ветки отдела — те, что видны вам самим.'
                            : 'Только опубликованные статьи. Черновик и статью в строгом режиме гостевой доступ не открывает — сотрудник их всё равно не увидит.'}
                    </p>
                </div>

                {kind === 'section' && (
                    <div className={`${iosCard} flex items-start justify-between gap-3 p-3.5`}>
                        <div className="min-w-0">
                            <div className="text-[13.5px] font-medium text-slate-900">
                                Вместе с подразделами
                            </div>
                            <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                Всё, что лежит внутри раздела, включая созданное позже.
                            </p>
                        </div>
                        <IosToggle checked={deep} onChange={setDeep} />
                    </div>
                )}

                <TermPicker meta={meta} days={days} until={until} atTime={atTime}
                            onDays={setDays} onUntil={setUntil} onTime={setAtTime} />

                <div className="space-y-2">
                    <div className={iosGroupLabel}>Зачем (необязательно)</div>
                    <input
                        type="text"
                        value={reason}
                        maxLength={500}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder="Например: сверяет регламент по задаче #214"
                        aria-label="Причина выдачи"
                        className={iosInput}
                    />
                    <p className="text-[11.5px] leading-relaxed text-slate-500">
                        Причина видна в списке выдач и в журнале. Через две недели
                        по ней понятно, зачем доступ давали, — а память об этом
                        уходит раньше, чем истекает срок.
                    </p>
                </div>
            </div>
        </IosModal>
    );
};

/* ─────────────────────────────────────────────────────────────────────────
   Продление
   ───────────────────────────────────────────────────────────────────────── */

const ExtendModal = ({ base, headers, meta, item, onClose, onDone, toast }) => {
    const [days, setDays] = useState(7);
    const [until, setUntil] = useState('');
    const [atTime, setAtTime] = useState('');
    const [busy, setBusy] = useState(false);

    const submit = () => {
        setBusy(true);
        axios.patch(`${base}/guests/${item.id}`, {
            ...(until ? { until } : { days }),
            ...(atTime ? { at_time: atTime } : {}),
        }, { headers })
            .then((r) => {
                toast?.(`Срок продлён до ${fmtDeadline(r.data?.expires_at)}`, 'success');
                onDone();
            })
            .catch((e) => toast?.(errText(e, 'Не удалось продлить доступ'), 'error'))
            .finally(() => setBusy(false));
    };

    return (
        <IosModal
            open
            onClose={onClose}
            title="Продлить гостевой доступ"
            subtitle={`${item.user_name} — ${targetLabel(item)}`}
            maxWidth="max-w-md"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Отмена
                    </button>
                    <button type="button" className={iosBtnPrimary}
                            disabled={busy || (!days && !until)} onClick={submit}>
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <CalendarClock size={15} />}
                        {' '}Продлить
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                <div className="rounded-xl bg-slate-50 px-3 py-2.5 text-[12px] leading-relaxed text-slate-600">
                    Сейчас доступ действует до {fmtDeadline(item.expires_at)}
                    {item.days_left !== null && item.days_left !== undefined
                        ? ` — ${daysLeftLabel(item.days_left, item.expires_at)}` : ''}.
                    {' '}Новый срок считается от сегодняшнего дня, а не прибавляется
                    к прежнему.
                </div>
                <TermPicker meta={meta} days={days} until={until} atTime={atTime}
                            onDays={setDays} onUntil={setUntil} onTime={setAtTime} />
            </div>
        </IosModal>
    );
};
