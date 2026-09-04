import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { BookOpen, Users2, Plus, BellRing } from 'lucide-react';
import { APPLE_FONT, iosBtnSecondary, IosModal, IosBadge } from '../ui/ios';
import useStableCallback from '../wiki/useStableCallback';
import MonthPicker from './MonthPicker';
import TopicsTab from './TopicsTab';
import GroupsTab from './GroupsTab';
import SessionModal from './SessionModal';
import TopicModal from './TopicModal';
import RolloutSheet from './RolloutSheet';
import SessionList from './SessionList';
import ReportSubscriptionModal from './ReportSubscriptionModal';
import { ErrorBlock } from './pieces';
import {
    TAB_TOPICS, TAB_GROUPS, FAMILY_CORPORATE, TOPIC_KIND_LABELS,
    buildTopicSummaries, sortTopicSummaries,
    readPrefs, writePrefs, readMonth, writeMonth,
    formatMonth, formatDuration, durationMinutes,
    pluralPeople, pluralSessions, errText,
} from './constants';

/* Раздел «Тренинги».
 *
 * Раздел жил внутри App.jsx с момента создания и не менялся с 06.03.2026: своя
 * вёрстка на gray-палитре, свой список тем из 9 значений (сервер знал 11), свои
 * модалки. Здесь он переписан на общих примитивах ui/ios.jsx и разбит на две
 * вкладки по двум разным вопросам:
 *
 *   «По темам»   — по каким темам в этом месяце проводили и как идёт охват
 *                  корпоративных тем;
 *   «По группам» — кто в какой группе что прошёл, вплоть до одного сотрудника.
 *
 * ОДИН запрос данных на весь раздел. Все выборки, сводки и группировки
 * считаются из него мемоизацией: за месяц в базе меньше двухсот занятий, за всё
 * время 1648, и второй круг запросов ради арифметики был бы платой ни за что.
 * Справочник тем — второй запрос, но он не зависит от месяца и не перезапрашивается
 * при его смене.
 */

export default function TrainingsView({
    user,
    apiBaseUrl,
    showToast,
    withAccessTokenHeader,
    departments: departmentsProp = null,
}) {
    const toast = useStableCallback(showToast);

    const initialPrefs = useMemo(readPrefs, []);
    const [tab, setTab] = useState(initialPrefs.tab);
    const [topicView, setTopicView] = useState(initialPrefs.topicView);
    const [groupView, setGroupView] = useState(initialPrefs.groupView);
    const [month, setMonth] = useState(readMonth);

    const [trainings, setTrainings] = useState([]);
    const [catalog, setCatalog] = useState(null);
    const [departments, setDepartments] = useState(departmentsProp || []);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');

    const [sessionModal, setSessionModal] = useState(null);      // {initial, defaultPeopleIds, lockedTopicId}
    const [topicModal, setTopicModal] = useState(null);          // {topic} | {}
    const [rolloutTopic, setRolloutTopic] = useState(null);
    const [rollout, setRollout] = useState({ loading: false, data: null, error: '' });
    /* Раскрытая тема — КЛЮЧ, а не снимок сводки. Со снимком удаление занятия из
     * модалки оставляло на экране удалённую строку и старые счётчики: список
     * тренингов обновлялся, сводки пересчитывались, а в state лежал прежний
     * объект. */
    const [openTopicKey, setOpenTopicKey] = useState(null);
    /* Подписка на Telegram-сводки. Своих данных раздел под неё не грузит:
     * окно запрашивает настройку само при открытии, а показывать кнопку или
     * нет — говорит флаг в справочнике тем. */
    const [reportsOpen, setReportsOpen] = useState(false);

    const headers = useMemo(
        () => ({ headers: withAccessTokenHeader({ 'X-User-Id': user?.id }) }),
        // withAccessTokenHeader приходит пропом и на каждый рендер App новая —
        // в deps её брать нельзя, иначе раздел перезапрашивался бы от любого
        // чужого рендера (свёрнутый сайдбар уже ломал так другой раздел).
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [user?.id],
    );

    useEffect(() => { writePrefs({ tab, topicView, groupView }); }, [tab, topicView, groupView]);
    useEffect(() => { writeMonth(month); }, [month]);

    /* ── Загрузка ───────────────────────────────────────────────────────── */

    /* Гонка месяцев. Два быстрых клика по стрелке — два запроса в полёте, и
     * ответ ПРЕДЫДУЩЕГО месяца, придя позже, перетирал данные текущего. Помним,
     * какой месяц запрошен последним, и принимаем только его ответ. */
    const wantedMonthRef = useRef(month);
    const loadTrainings = useCallback(async (targetMonth) => {
        wantedMonthRef.current = targetMonth;
        const response = await axios.get(
            `${apiBaseUrl}/api/trainings?month=${encodeURIComponent(targetMonth)}`, headers,
        );
        if (wantedMonthRef.current !== targetMonth) return;
        const rows = Array.isArray(response?.data?.trainings) ? response.data.trainings : [];
        setTrainings(rows);
    }, [apiBaseUrl, headers]);

    /* include_archived=1 — обязательно. Архив это единственный способ убрать
     * тему с историей (удаление сервер запрещает), и без архивных тем в ответе
     * пункт «Вернуть из архива» был бы недостижим: заархивированная тема
     * исчезала из раздела навсегда. В список карточек они при этом не
     * подмешиваются — для них отдельный срез «Архив». */
    const loadCatalog = useCallback(async () => {
        const response = await axios.get(
            `${apiBaseUrl}/api/training_topics?include_archived=1`, headers,
        );
        setCatalog(response?.data || null);
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        if (!user?.id) return;
        let alive = true;
        setLoading(true);
        setLoadError('');
        Promise.all([loadTrainings(month), catalog ? Promise.resolve() : loadCatalog()])
            .then(() => { if (alive) setLoading(false); })
            .catch((error) => {
                if (!alive) return;
                setLoading(false);
                setLoadError(errText(error, 'Не удалось загрузить тренинги'));
            });
        return () => { alive = false; };
    // catalog в deps не берём СОЗНАТЕЛЬНО: он грузится один раз, и его
    // появление не должно перезапускать загрузку месяца.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user?.id, month, loadTrainings, loadCatalog]);

    /* Отделы. У СВ /api/admin/departments отдаёт 403, у главы отдела —
     * единственный его отдел; поэтому запрос не обязателен, а его провал не
     * ошибка раздела: просто селектора отделов не будет. */
    useEffect(() => {
        if (departmentsProp?.length) { setDepartments(departmentsProp); return; }
        if (!user?.id) return;
        let alive = true;
        axios.get(`${apiBaseUrl}/api/admin/departments`, headers)
            .then((response) => {
                if (!alive) return;
                const rows = Array.isArray(response?.data?.departments) ? response.data.departments : [];
                setDepartments(rows.filter((item) => item?.is_active !== false));
            })
            .catch(() => { /* СВ его и не должен видеть */ });
        return () => { alive = false; };
    }, [apiBaseUrl, headers, user?.id, departmentsProp]);

    const reload = useCallback(async () => {
        try {
            await Promise.all([loadTrainings(month), loadCatalog()]);
        } catch (error) {
            toast?.(errText(error, 'Не удалось обновить данные'), 'error');
        }
    }, [loadTrainings, loadCatalog, month, toast]);

    /* ── Производные данные ─────────────────────────────────────────────── */

    const topics = useMemo(() => (Array.isArray(catalog?.topics) ? catalog.topics : []), [catalog]);
    const defaultReasons = useMemo(
        () => (Array.isArray(catalog?.default_reasons) ? catalog.default_reasons : []), [catalog]);
    const archivedReasons = useMemo(
        () => (Array.isArray(catalog?.archived_reasons) ? catalog.archived_reasons : []), [catalog]);
    const canManage = Boolean(catalog?.can_manage);
    const canChooseDepartment = Boolean(catalog?.unscoped);
    const canSubscribeReports = Boolean(catalog?.can_subscribe_reports);

    /* Сводки по темам считаются ЗДЕСЬ, а не во вкладке: их нужно и списку
     * карточек, и раскрытой теме. Два независимых расчёта одного и того же
     * однажды разошлись бы, и это увидели бы как «в карточке одно число, а
     * внутри другое». */
    const summaries = useMemo(() => sortTopicSummaries(
        buildTopicSummaries({ trainings, topics, archivedReasons }),
    ), [trainings, topics, archivedReasons]);

    const openTopic = useMemo(
        () => (openTopicKey ? summaries.find((item) => item.key === openTopicKey) || null : null),
        [openTopicKey, summaries],
    );

    const scopeDepartmentName = useMemo(() => {
        const id = catalog?.scope_department_id;
        if (id == null) return '';
        return departments.find((item) => Number(item.id) === Number(id))?.name || '';
    }, [catalog?.scope_department_id, departments]);

    /* Сотрудники для формы занятия — из самих занятий месяца плюс аудитории
     * корпоративных тем. Отдельного справочника людей раздел не запрашивает:
     * /api/admin/users отдаёт админу только роль operator и только текущую
     * группу, из-за чего 11 занятий супервайзеров были не видны вовсе. */
    const [peopleIndex, setPeopleIndex] = useState([]);
    useEffect(() => {
        const map = new Map();
        trainings.forEach((item) => {
            const id = Number(item?.operator_id);
            if (!Number.isFinite(id) || map.has(id)) return;
            map.set(id, { id, name: item.operator_name || `#${id}`, status: item.operator_status });
        });
        (rollout.data?.audience || []).forEach((person) => {
            if (!map.has(person.id)) map.set(person.id, person);
        });
        setPeopleIndex(Array.from(map.values())
            .sort((a, b) => String(a.name).localeCompare(String(b.name), 'ru', { sensitivity: 'base' })));
    }, [trainings, rollout.data]);

    const existingByOperator = useMemo(() => {
        const map = {};
        trainings.forEach((item) => {
            const id = Number(item?.operator_id);
            if (!Number.isFinite(id)) return;
            if (!map[id]) map[id] = [];
            map[id].push(item);
        });
        return map;
    }, [trainings]);

    /* ── Действия ───────────────────────────────────────────────────────── */

    const saveSession = useCallback(async (payload) => {
        if (sessionModal?.initial?.id) {
            await axios.put(`${apiBaseUrl}/api/trainings/${sessionModal.initial.id}`, payload, headers);
            await reload();
            toast?.('Занятие обновлено', 'success');
            return;
        }
        const response = await axios.post(`${apiBaseUrl}/api/trainings`, payload, headers);
        await reload();
        const data = response?.data || {};
        const created = Number(data.created_count || 0);
        const failed = Array.isArray(data.errors) ? data.errors.length : 0;
        if (failed > 0) {
            // Частичный успех — говорим и сколько прошло, и сколько нет:
            // «создано 8 из 10» без второй половины прочиталось бы как успех.
            const overlap = data.errors.some((item) => item?.overlap);
            toast?.(
                `Записано ${created} из ${created + failed}. ${overlap
                    ? 'У остальных занятие пересекается по времени.'
                    : String(data.errors[0]?.error || 'Часть записать не удалось.')}`,
                'error',
            );
            return;
        }
        toast?.(created > 1
            ? `Занятие записано ${created} ${pluralPeople(created)}`
            : 'Занятие записано', 'success');
    }, [sessionModal, apiBaseUrl, headers, reload, toast]);

    const deleteSession = useCallback(async (session) => {
        // eslint-disable-next-line no-alert
        if (!window.confirm(`Удалить занятие ${session?.date} у ${session?.operator_name || 'сотрудника'}?`)) return;
        try {
            await axios.delete(`${apiBaseUrl}/api/trainings/${session.id}`, headers);
            await reload();
            toast?.('Занятие удалено', 'success');
        } catch (error) {
            toast?.(errText(error, 'Не удалось удалить занятие'), 'error');
        }
    }, [apiBaseUrl, headers, reload, toast]);

    const saveTopic = useCallback(async (payload) => {
        const editing = topicModal?.topic;
        if (editing?.id) {
            await axios.put(`${apiBaseUrl}/api/training_topics/${editing.id}`, payload, headers);
        } else {
            await axios.post(`${apiBaseUrl}/api/training_topics`, payload, headers);
        }
        await loadCatalog();
        toast?.(editing?.id ? 'Тема обновлена' : 'Тема создана', 'success');
    }, [topicModal, apiBaseUrl, headers, loadCatalog, toast]);

    const archiveTopic = useCallback(async (topic, archive) => {
        try {
            await axios.put(`${apiBaseUrl}/api/training_topics/${topic.id}`,
                { is_archived: archive }, headers);
            await loadCatalog();
            toast?.(archive ? 'Тема отправлена в архив' : 'Тема возвращена из архива', 'success');
        } catch (error) {
            toast?.(errText(error, 'Не удалось изменить тему'), 'error');
        }
    }, [apiBaseUrl, headers, loadCatalog, toast]);

    const deleteTopic = useCallback(async (topic) => {
        // eslint-disable-next-line no-alert
        if (!window.confirm(`Удалить тему «${topic.title}»?`)) return;
        try {
            await axios.delete(`${apiBaseUrl}/api/training_topics/${topic.id}`, headers);
            await loadCatalog();
            toast?.('Тема удалена', 'success');
        } catch (error) {
            toast?.(errText(error, 'Не удалось удалить тему'), 'error');
        }
    }, [apiBaseUrl, headers, loadCatalog, toast]);

    const openRollout = useCallback(async (topic) => {
        setRolloutTopic(topic);
        setRollout({ loading: true, data: null, error: '' });
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/training_topics/${topic.id}/audience`, headers);
            setRollout({ loading: false, data: response?.data || null, error: '' });
        } catch (error) {
            setRollout({ loading: false, data: null, error: errText(error, 'Не удалось загрузить аудиторию темы') });
        }
    }, [apiBaseUrl, headers]);

    const submitRollout = useCallback(async (payload) => {
        const response = await axios.post(`${apiBaseUrl}/api/trainings`, payload, headers);
        await reload();
        const data = response?.data || {};
        const created = Number(data.created_count || 0);
        const failed = Array.isArray(data.errors) ? data.errors.length : 0;
        if (failed > 0) {
            toast?.(`Записано ${created} из ${created + failed}. Часть не прошла — проверьте пересечения по времени.`, 'error');
        } else {
            toast?.(`Тема проведена ${created} ${pluralPeople(created)}`, 'success');
        }
    }, [apiBaseUrl, headers, reload, toast]);

    /* ── Разметка ───────────────────────────────────────────────────────── */

    const tabs = [
        { key: TAB_TOPICS, label: 'По темам', icon: BookOpen },
        { key: TAB_GROUPS, label: 'По группам', icon: Users2 },
    ];

    const monthTotals = useMemo(() => {
        const people = new Set(trainings.map((item) => item.operator_id));
        const minutes = trainings.reduce((acc, item) => (
            item.count_in_hours === false ? acc : acc + durationMinutes(item)
        ), 0);
        return { sessions: trainings.length, people: people.size, minutes };
    }, [trainings]);

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div className="min-w-0">
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">Тренинги</h2>
                    <p className="text-xs text-slate-500">
                        {loading || monthTotals.sessions === 0
                            ? 'Кто, когда и по какой теме проводил занятия'
                            : `${formatMonth(month)}: ${monthTotals.sessions} ${pluralSessions(monthTotals.sessions)} · `
                              + `${monthTotals.people} ${pluralPeople(monthTotals.people)}`
                              + (monthTotals.minutes > 0 ? ` · ${formatDuration(monthTotals.minutes)} в часах` : '')}
                    </p>
                </div>

                {/* flex-wrap и whitespace-nowrap: на телефоне месяц и две вкладки
                    в одну строку не влезают, и без переноса подпись «По группам»
                    ломалась на два слова и уезжала за край. */}
                <div className="flex flex-wrap items-center gap-2">
                    {/* Отчёты в Telegram — иконкой, а не строкой-настройкой в
                        шапке: настройку открывают раз в жизни, а видеть её
                        каждый день над списком занятий незачем. */}
                    {canSubscribeReports && (
                        <button
                            type="button"
                            onClick={() => setReportsOpen(true)}
                            title="Отчёты по тренингам в Telegram"
                            aria-label="Отчёты по тренингам в Telegram"
                            className="grid h-[34px] w-[34px] place-items-center rounded-xl bg-slate-100 text-slate-500 transition-all hover:bg-slate-200 hover:text-slate-700 active:scale-[0.98]"
                        >
                            <BellRing size={15} />
                        </button>
                    )}
                    <MonthPicker value={month} onChange={setMonth} />
                    <div className="flex rounded-xl bg-slate-100 p-1">
                        {tabs.map((item) => (
                            <button
                                key={item.key}
                                type="button"
                                onClick={() => setTab(item.key)}
                                className={`flex items-center gap-1.5 whitespace-nowrap rounded-[9px] px-3.5 py-1.5 text-[12.5px] font-semibold transition-all ${
                                    tab === item.key
                                        ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                            >
                                <item.icon size={13} className="shrink-0" /> {item.label}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {loadError && !loading && (
                <ErrorBlock text={loadError} onRetry={() => { setLoadError(''); reload(); }} />
            )}

            {!loadError && tab === TAB_TOPICS && (
                <TopicsTab
                    month={month}
                    summaries={summaries}
                    loading={loading}
                    canManage={canManage}
                    view={topicView}
                    onViewChange={setTopicView}
                    onCreateTopic={() => setTopicModal({})}
                    onEditTopic={(topic) => setTopicModal({ topic })}
                    onArchiveTopic={archiveTopic}
                    onDeleteTopic={deleteTopic}
                    onRollout={openRollout}
                    onOpenTopic={(summary) => setOpenTopicKey(summary.key)}
                    onAddSession={() => setSessionModal({})}
                />
            )}

            {!loadError && tab === TAB_GROUPS && (
                <GroupsTab
                    month={month}
                    trainings={trainings}
                    loading={loading}
                    departments={departments}
                    showDepartmentPicker={canChooseDepartment && departments.length > 1}
                    view={groupView}
                    onViewChange={setGroupView}
                    canManage={canManage}
                    onAddSession={() => setSessionModal({})}
                    onEditSession={(session) => setSessionModal({ initial: session })}
                    onDeleteSession={deleteSession}
                />
            )}

            <SessionModal
                open={Boolean(sessionModal)}
                onClose={() => setSessionModal(null)}
                onSave={saveSession}
                initial={sessionModal?.initial || null}
                defaultPeopleIds={sessionModal?.defaultPeopleIds || []}
                lockedTopicId={sessionModal?.lockedTopicId || null}
                people={peopleIndex}
                topics={topics}
                defaultReasons={defaultReasons}
                archivedReasons={archivedReasons}
                existingByOperator={existingByOperator}
            />

            <TopicModal
                open={Boolean(topicModal)}
                onClose={() => setTopicModal(null)}
                onSave={saveTopic}
                initial={topicModal?.topic || null}
                departments={departments}
                canChooseDepartment={canChooseDepartment}
                scopeDepartmentName={scopeDepartmentName}
            />

            <RolloutSheet
                open={Boolean(rolloutTopic)}
                onClose={() => { setRolloutTopic(null); setRollout({ loading: false, data: null, error: '' }); }}
                topic={rolloutTopic}
                audience={rollout.data}
                loading={rollout.loading}
                loadError={rollout.error}
                onReload={() => rolloutTopic && openRollout(rolloutTopic)}
                onSubmit={submitRollout}
            />

            <ReportSubscriptionModal
                open={reportsOpen}
                onClose={() => setReportsOpen(false)}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onToast={toast}
            />

            <IosModal
                open={Boolean(openTopic)}
                onClose={() => setOpenTopicKey(null)}
                title={openTopic?.title || ''}
                subtitle={openTopic
                    ? `${formatMonth(month)} · ${openTopic.monthSessions} ${pluralSessions(openTopic.monthSessions)}`
                    : undefined}
                maxWidth="max-w-xl"
                footer={(
                    <>
                        {openTopic?.family === FAMILY_CORPORATE && openTopic?.topic && canManage
                            && !openTopic.isArchivedTopic && (
                            <button
                                type="button"
                                onClick={() => { const topic = openTopic.topic; setOpenTopicKey(null); openRollout(topic); }}
                                className={iosBtnSecondary}
                            >
                                <Plus size={14} /> Провести пачке
                            </button>
                        )}
                        <button type="button" onClick={() => setOpenTopicKey(null)} className={iosBtnSecondary}>
                            Закрыть
                        </button>
                    </>
                )}
            >
                {openTopic && (
                    <div className="space-y-3">
                        {openTopic.family === FAMILY_CORPORATE && (
                            <div className="flex flex-wrap items-center gap-1.5">
                                <IosBadge tone="blue">Корпоративная</IosBadge>
                                <IosBadge>{TOPIC_KIND_LABELS[openTopic.kind] || 'Информационный'}</IosBadge>
                                {openTopic.audienceCount > 0 && (
                                    <span className="text-[12px] tabular-nums text-slate-500">
                                        охват {openTopic.coveredCount} из {openTopic.audienceCount}
                                    </span>
                                )}
                            </div>
                        )}
                        {openTopic.topic?.description && (
                            <p className="text-[12.5px] leading-relaxed text-slate-500">
                                {openTopic.topic.description}
                            </p>
                        )}
                        <SessionList
                            sessions={openTopic.sessions}
                            showPerson
                            showTopic={false}
                            canManage={canManage}
                            onEdit={(session) => { setOpenTopicKey(null); setSessionModal({ initial: session }); }}
                            onDelete={deleteSession}
                            emptyText="В этом месяце по теме не проводили"
                        />
                    </div>
                )}
            </IosModal>
        </div>
    );
}
