import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { IosToggle, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import { leaveApiError } from './leaveRequests';

/*
 * Настройки заявок на отпуск и Б/С по отделам (задача #358, «общее и
 * настраиваемое»). Второй экран окна «Запросы», как в «Настройках» iOS: строка
 * отдела с переключателем, а у включённого — что именно он принимает.
 *
 * Сохраняется каждое изменение сразу, без кнопки «Сохранить»: переключатель,
 * который нужно ещё и подтвердить, на телефоне читается как сломанный.
 * Меняет только администратор (сервер проверяет сам).
 */

const STEP_HINT = 'Включено — заявку сначала согласует супервайзер группы оператора, затем утверждает руководитель отдела. Выключено — заявка сразу идёт руководителю. У оператора без супервайзера она идёт руководителю в любом случае.';
const LONG_BS_HINT = 'Б/С от этого числа дней при утверждении по умолчанию отмечается «не учитывать в стаже». Руководитель видит переключатель и решает сам. Пусто — подсказки нет, переключатель выключен.';

/* «i» — общий InfoHint: пузырёк рисуется поверх окна и краем карточки
   (overflow-hidden) не обрезается. Значок держится за последнее слово подписи:
   на узком экране она переносится, а одинокий значок на новой строке
   читается как отдельный пункт. */
function Row({ title, hint = null, children }) {
    const words = title.split(' ');
    const last = words.pop();
    return (
        <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <div className="min-w-0 text-[13px] text-slate-700">
                {hint ? (
                    <>
                        {words.length > 0 && `${words.join(' ')} `}
                        <span className="whitespace-nowrap">
                            {last}
                            <InfoHint side="left" text={hint} className="ml-1.5 align-middle" />
                        </span>
                    </>
                ) : title}
            </div>
            {children}
        </div>
    );
}

function DepartmentCard({ department, saving, onSave }) {
    const [days, setDays] = useState(department.longBsDays ?? '');
    useEffect(() => { setDays(department.longBsDays ?? ''); }, [department.longBsDays]);

    const commitDays = () => {
        const value = String(days).trim();
        const current = department.longBsDays ?? '';
        if (value === String(current)) return;
        onSave(department, { long_bs_days: value === '' ? null : Number(value) });
    };

    return (
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
            <div className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">{department.name}</div>
                    <div className="truncate text-[12px] text-slate-500">
                        {department.headName ? `Руководитель — ${department.headName}` : 'Руководитель отдела не назначен'}
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {saving && <Loader2 size={13} className="animate-spin text-slate-400" aria-hidden="true" />}
                    <IosToggle
                        checked={department.enabled}
                        disabled={saving}
                        onChange={(value) => onSave(department, { enabled: value })}
                    />
                </div>
            </div>
            {department.enabled && (
                <>
                    <Row title="Ежегодный отпуск">
                        <IosToggle checked={department.allowAnnualLeave} disabled={saving}
                                   onChange={(value) => onSave(department, { allow_annual_leave: value })} />
                    </Row>
                    <Row title="Б/С">
                        <IosToggle checked={department.allowBs} disabled={saving}
                                   onChange={(value) => onSave(department, { allow_bs: value })} />
                    </Row>
                    <Row title="Сначала согласует супервайзер" hint={STEP_HINT}>
                        <IosToggle checked={department.supervisorStep} disabled={saving}
                                   onChange={(value) => onSave(department, { supervisor_step: value })} />
                    </Row>
                    <Row title="Длительный Б/С не в стаж — от" hint={LONG_BS_HINT}>
                        <div className="flex items-center gap-1.5">
                            <input
                                type="number"
                                inputMode="numeric"
                                min={1}
                                max={366}
                                value={days}
                                disabled={saving}
                                onChange={(event) => setDays(event.target.value)}
                                onBlur={commitDays}
                                onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                                placeholder="—"
                                aria-label="С какого числа дней Б/С не идёт в стаж"
                                className={`${iosInput} !w-[72px] !py-1.5 text-right tabular-nums`}
                            />
                            <span className="text-[13px] text-slate-500">дней</span>
                        </div>
                    </Row>
                </>
            )}
        </div>
    );
}

export default function LeaveSettingsPanel({ api }) {
    const [departments, setDepartments] = useState(null);
    const [error, setError] = useState('');
    const [savingId, setSavingId] = useState(null);

    useEffect(() => {
        let alive = true;
        api.settings()
            .then((data) => { if (alive) setDepartments(Array.isArray(data?.departments) ? data.departments : []); })
            .catch((err) => { if (alive) setError(leaveApiError(err, 'Не удалось загрузить настройки')); });
        return () => { alive = false; };
    }, [api]);

    const save = async (department, patch) => {
        setSavingId(department.id);
        setError('');
        // Сразу показываем новое положение — сервер вернёт то же самое или откажет.
        setDepartments((prev) => prev.map((d) => (d.id === department.id ? {
            ...d,
            ...('enabled' in patch ? { enabled: patch.enabled } : {}),
            ...('allow_annual_leave' in patch ? { allowAnnualLeave: patch.allow_annual_leave } : {}),
            ...('allow_bs' in patch ? { allowBs: patch.allow_bs } : {}),
            ...('supervisor_step' in patch ? { supervisorStep: patch.supervisor_step } : {}),
            ...('long_bs_days' in patch ? { longBsDays: patch.long_bs_days } : {}),
        } : d)));
        try {
            const data = await api.saveSettings(department.id, patch);
            if (data?.department) {
                setDepartments((prev) => prev.map((d) => (d.id === department.id ? data.department : d)));
            }
        } catch (err) {
            setError(leaveApiError(err, 'Не удалось сохранить'));
            setDepartments((prev) => prev.map((d) => (d.id === department.id ? department : d)));
        } finally {
            setSavingId(null);
        }
    };

    if (departments === null && !error) {
        // Заготовка того же роста, что и список: окно не прыгает, когда он придёт.
        return (
            <div className="space-y-3" aria-busy="true">
                {[0, 1, 2].map((i) => (
                    <div key={i} className={`${iosCard} h-[60px] animate-pulse bg-slate-50`} />
                ))}
            </div>
        );
    }

    // Порядок — как пришёл с сервера (включённые сверху, дальше по имени) и
    // при переключении не меняется: карточка, уехавшая из-под пальца в другую
    // группу, — тот самый рывок.
    return (
        <div className="space-y-3">
            <p className="px-1 text-[12.5px] leading-snug text-slate-500">
                Отдел с включённым переключателем принимает заявки: операторы подают их в «Моих сменах» → «Запросы».
            </p>
            {error && (
                <div className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700 ring-1 ring-rose-200/70">{error}</div>
            )}
            <div className={iosGroupLabel}>Отделы</div>
            {(departments || []).map((department) => (
                <DepartmentCard key={department.id} department={department} saving={savingId === department.id} onSave={save} />
            ))}
        </div>
    );
}
