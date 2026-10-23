import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, ChevronRight, Loader2 } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import { IosModal, IosSegmented, iosCard } from '../ui/ios';
import { fmtDateTime } from '../parcels/parcelMeta';
import {
    accessChanges, accessDraftOf, accessPeople, accessSummary, officePlace, officeShortLabel, toggleAccess,
} from './waterMeta';
import { ModalSubtitle, ModalTitle, T, wBtnPrimary, wBtnSecondary, wIcon, wLabel, wSegments } from './waterUi';

/*
 * «Доступ по офисам» — где сотрудник фронт-офиса выдаёт воду.
 *
 * Владелец 23.10.2026: «в настройках раздела учёт воды нужно сделать кнопку
 * выдачи доступов по офисам… выбираем офис, далее из фронт-офисников выбираем
 * людей, которым открыт данный раздел, и выдаём доступ, чтобы они могли
 * выдавать воду только по доступным им офисам».
 *
 * В «Настройках» — одна строка-кнопка, как в настройках iOS: что открыто
 * сейчас («Алматы: 6 · Астана: 2») и шеврон. Окно — офис сверху (два-три офиса —
 * сегментами, больше — списком), под ним сотрудники фронт-офисов, которым
 * открыт раздел; отмеченные выдают воду в этом офисе. Черновик один на все
 * офисы: отметил людей в Алматы, переключился на Астану, отметил там — одно
 * «Сохранить» уносит всё. Сервер получает только переключённые пары (PATCH
 * /api/water/access) и проверяет их сам.
 *
 * Руководителя фронт-офисов и супер-админов в списке нет: они выдают воду в
 * любом офисе (water/access.py).
 */

const PEOPLE_HINT = 'В списке — сотрудники фронт-офисов, которым открыт раздел. Отмеченные выдают воду '
    + 'в выбранном офисе, в остальных офисах — только если отмечены и там. Руководитель фронт-офисов '
    + 'и супер-админы выдают воду в любом офисе, поэтому их здесь нет';

const GROUP_HINT = 'Сотрудники фронт-офисов выдают воду только в офисах, к которым им выдан доступ. '
    + 'Руководитель фронт-офисов и супер-админы — в любом офисе';

/* Оболочка телефона переводит ряды с gap в перенос (как в «Настройках»):
   галочка падала бы под имя. Перебивается только инлайном. */
const NOWRAP = { flexWrap: 'nowrap' };

/* Кто и когда менял доступ офиса — последняя строка журнала. Записи перехода
   сделал не человек: доступ тогда выдан по городу сотрудника. */
const stampOf = (changed) => {
    if (!changed?.at) return '';
    if (changed.by_seed) return `Выдано по городу сотрудника · ${fmtDateTime(changed.at)}`;
    return ['Изменено', changed.by_name, fmtDateTime(changed.at)].filter(Boolean).join(' · ');
};

/* Галочка выбора — как у плиток тарифов в «Настройках»: мягко-голубая, а не
   чёрная (владелец 16.10.2026). Цвета строк — из тех, что уже есть в тёмном
   слое владельца (src/theme-dark.css): новый модификатор вроде `/70` остался
   бы там светлым до пересборки слоя. */
const CheckDot = ({ on }) => (on ? (
    <span className="grid size-5 shrink-0 place-items-center rounded-full bg-blue-600 text-white 2xl:size-6">
        <Check size={13} strokeWidth={3} className="2xl:size-[15px]" />
    </span>
) : (
    <span className="size-5 shrink-0 rounded-full ring-1 ring-inset ring-slate-300 2xl:size-6" />
));

/* ── Окно ───────────────────────────────────────────────────────────────── */

export const WaterAccessSheet = ({ open, onClose, access, onSaved, apiBaseUrl, headers, showToast }) => {
    const offices = useMemo(() => access?.offices || [], [access]);
    const [saving, setSaving] = useState(false);
    // Офис, на котором окно закрыли, — с него и открывается в следующий раз.
    const lastOffice = useRef(null);

    /* Черновик живёт одно открытие окна и заполняется в той же отрисовке, в
       которой окно открылось, а не эффектом после неё: иначе первый кадр стоял
       бы с прошлым выбором. Закрыли — черновик ушёл, и «Отмена» ничего не
       откатывает руками. */
    const [session, setSession] = useState(null);
    if (open && !session && access) {
        const officeId = offices.some((office) => office.id === lastOffice.current)
            ? lastOffice.current : offices[0]?.id ?? null;
        setSession({ draft: accessDraftOf(access), officeId });
    }
    if (!open && session) setSession(null);

    const draft = useMemo(() => session?.draft || {}, [session]);
    const office = offices.find((item) => item.id === session?.officeId) || offices[0] || null;
    const changes = useMemo(() => accessChanges(draft, access), [draft, access]);
    const dirty = changes.length > 0;
    const people = useMemo(() => accessPeople(access?.people || [], office), [access, office]);
    const chosen = new Set(office ? draft[office.id] || [] : []);

    const choose = (officeId) => {
        lastOffice.current = officeId;
        setSession((current) => (current ? { ...current, officeId } : current));
    };
    const toggle = (userId) => {
        if (saving || !office) return;
        setSession((current) => (current ? { ...current, draft: toggleAccess(current.draft, office.id, userId) } : current));
    };
    const reset = () => setSession((current) => (current ? { ...current, draft: accessDraftOf(access) } : current));

    /* Сохранение отвечает свежим доступом — кладём его в строку «Настроек» и
       закрываем окно. Отказ (офис вывели из учёта, сотрудника перевели) тоже
       несёт свежий доступ: окно перерисовывается по нему и остаётся открытым. */
    const save = async () => {
        if (!dirty || saving) return;
        lastOffice.current = office?.id ?? null;
        setSaving(true);
        try {
            const response = await axios.patch(`${apiBaseUrl}/api/water/access`, { changes }, { headers: headers() });
            if (response.data?.access) onSaved?.(response.data.access);
            showToast?.('Доступ к выдаче сохранён', 'success');
            onClose?.();
        } catch (requestError) {
            const data = requestError?.response?.data || {};
            showToast?.(data.error || 'Не удалось сохранить доступ', 'error');
            if (data.access) {
                onSaved?.(data.access);
                setSession((current) => (current ? { ...current, draft: accessDraftOf(data.access) } : current));
            }
        } finally {
            setSaving(false);
        }
    };

    const close = () => {
        lastOffice.current = office?.id ?? null;
        onClose?.();
    };

    const stamp = stampOf(office?.changed);

    return (
        <IosModal
            open={open}
            onClose={close}
            title={<ModalTitle>Доступ к выдаче</ModalTitle>}
            subtitle={<ModalSubtitle>{office ? officePlace(office) : 'Учёт воды'}</ModalSubtitle>}
            maxWidth="max-w-lg 2xl:max-w-[640px]"
            footer={(
                <>
                    {dirty && (
                        <button type="button" className={wBtnSecondary} disabled={saving} onClick={reset}>
                            Отменить
                        </button>
                    )}
                    <button type="button" className={`${wBtnPrimary} 2xl:!px-8`} disabled={!dirty || saving} onClick={save}>
                        {saving && <Loader2 size={15} className={`animate-spin ${wIcon}`} />}
                        Сохранить
                    </button>
                </>
            )}
        >
            {offices.length === 0 ? (
                <p className={`py-6 text-center text-[13px] text-slate-500 ${T.body}`}>
                    Офисов в учёте пока нет — добавьте их во вкладке «Остатки».
                </p>
            ) : (
                <div className="space-y-4 2xl:space-y-5">
                    <section className="space-y-1.5 2xl:space-y-2">
                        <span className={wLabel}>Офис</span>
                        {/* Два-три офиса — сегментами (выбор из двух-трёх, владелец
                            06.10.2026), больше — списком. Число в сегменте — сколько
                            человек выдаёт в офисе по черновику. */}
                        {offices.length <= 3 ? (
                            <IosSegmented
                                value={office?.id}
                                onChange={choose}
                                stretch
                                className={wSegments}
                                ariaLabel="Офис"
                                options={offices.map((item) => ({
                                    value: item.id,
                                    label: officeShortLabel(item, offices),
                                    count: (draft[item.id] || []).length,
                                }))}
                            />
                        ) : (
                            <CustomSelect
                                value={office?.id}
                                onChange={choose}
                                variant="ios"
                                roomy
                                ariaLabel="Офис"
                                options={offices.map((item) => ({
                                    value: item.id,
                                    label: `${item.city} · ${item.name}`,
                                    meta: String((draft[item.id] || []).length),
                                }))}
                            />
                        )}
                    </section>

                    <section className="space-y-1.5 2xl:space-y-2">
                        <div className="flex items-center gap-1.5">
                            <span className={wLabel}>Сотрудники фронт-офисов</span>
                            <InfoHint side="left" text={PEOPLE_HINT} />
                        </div>
                        {people.length ? (
                            <div role="group" aria-label="Кто выдаёт воду в этом офисе"
                                 className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {people.map((person) => {
                                    const on = chosen.has(Number(person.id));
                                    return (
                                        <button
                                            key={person.id}
                                            type="button"
                                            role="checkbox"
                                            aria-checked={on}
                                            onClick={() => toggle(person.id)}
                                            style={NOWRAP}
                                            className={`flex min-h-[48px] w-full items-center gap-3 px-4 py-2.5 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/60 xl:min-h-[52px] 2xl:min-h-[60px] 2xl:px-5 ${on
                                                ? 'bg-blue-50 hover:bg-blue-100'
                                                : 'bg-white hover:bg-slate-50 active:bg-slate-100'}`}
                                        >
                                            <span className={`min-w-0 flex-1 truncate text-[14px] font-medium ${T.body} ${on ? 'text-blue-700' : 'text-slate-900'}`}>
                                                {person.name}
                                            </span>
                                            {person.city && (
                                                <span className={`shrink-0 text-[12.5px] ${T.sub} ${on ? 'text-blue-600/80' : 'text-slate-400'}`}>
                                                    {person.city}
                                                </span>
                                            )}
                                            <CheckDot on={on} />
                                        </button>
                                    );
                                })}
                            </div>
                        ) : (
                            <div className={`${iosCard} px-4 py-6 text-center text-[13px] text-slate-500 ${T.body}`}>
                                Сотрудников фронт-офисов, которым открыт раздел, пока нет
                            </div>
                        )}
                        {stamp && <p className={`px-1 text-[11.5px] text-slate-400 ${T.note}`}>{stamp}</p>}
                    </section>
                </div>
            )}
        </IosModal>
    );
};

/* ── Строка в «Настройках» ──────────────────────────────────────────────── */

const errorOf = (requestError) => requestError?.response?.data?.error || 'Не удалось загрузить доступ';

/* Строка — ростом и полями как строки соседних карточек «Настроек»; второй
   строкой — что открыто сейчас. До ответа на её месте неразрывный пробел того
   же роста: строка не перестраивается, когда доступ приходит. */
const WaterAccess = ({ apiBaseUrl, headers, showToast }) => {
    const [access, setAccess] = useState(null);
    const [error, setError] = useState('');
    const [open, setOpen] = useState(false);

    const load = useCallback(() => {
        setError('');
        axios.get(`${apiBaseUrl}/api/water/access`, { headers: headers() })
            .then((response) => setAccess(response.data?.access || { offices: [], people: [] }))
            .catch((requestError) => setError(errorOf(requestError)));
    }, [apiBaseUrl, headers]);

    useEffect(() => { load(); }, [load]);

    const summary = error || (access ? accessSummary(access) : ' ');

    return (
        /* Столбик на gap, а не на space-y: окно лежит его ребёнком, и отступ
           space-y сдвинул бы затемнение вниз от края экрана. */
        <section className="flex flex-col gap-1.5 2xl:gap-2">
            <div className="flex items-center gap-1.5 px-3 xl:px-4 2xl:px-5">
                <span className={wLabel}>Выдача воды</span>
                <InfoHint side="left" text={GROUP_HINT} />
            </div>
            <button
                type="button"
                onClick={() => (error ? load() : setOpen(true))}
                disabled={!access && !error}
                style={NOWRAP}
                className={`${iosCard} flex min-h-[56px] w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50 active:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:cursor-default disabled:hover:bg-white xl:min-h-[64px] xl:px-5 2xl:min-h-[76px] 2xl:px-6`}
            >
                <span className="min-w-0 flex-1">
                    <span className={`block text-[14px] text-slate-800 ${T.body}`}>Доступ по офисам</span>
                    <span className={`block truncate text-[12px] ${T.note} ${error ? 'text-rose-600' : 'text-slate-500'}`}>
                        {summary}
                    </span>
                </span>
                <ChevronRight size={16} className={`shrink-0 text-slate-300 ${wIcon}`} />
            </button>
            <WaterAccessSheet
                open={open}
                onClose={() => setOpen(false)}
                access={access}
                onSaved={setAccess}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                showToast={showToast}
            />
        </section>
    );
};

export default WaterAccess;
