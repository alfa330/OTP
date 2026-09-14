import React, { useState } from 'react';
import axios from 'axios';
import { Loader2, Plus, X } from 'lucide-react';
import { iosBtnPrimary, IosSection } from '../ui/ios';
import { MEMBER_ROLES } from './paymentsMeta';
import { UserSelect, errorText } from './paymentsUi';

/*
 * «Участники»: кто в процессе Учредитель и кто Бухгалтерия.
 *
 * В портале нет ни должности «Учредитель», ни списка бухгалтеров как роли
 * процесса (отдел «Бухгалтерия» есть, но в нём один человек, и он же может не
 * быть тем, кто оплачивает счета). Поэтому участников ролей назначают здесь,
 * руками, и на шагах роли отписывается любой из списка. Без обоих списков
 * заявку завести нельзя — сервер откажет с понятным текстом.
 */

const PaymentsRoles = ({ apiBaseUrl, headers, roles, users, onChanged, showToast, canEdit }) => {
    const [picked, setPicked] = useState({});
    const [busy, setBusy] = useState('');

    const add = async (roleCode) => {
        const userId = picked[roleCode];
        if (!userId) return;
        setBusy(roleCode);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/roles`, { role_code: roleCode, user_id: userId }, { headers: headers() });
            onChanged?.(response.data?.roles || {});
            setPicked((prev) => ({ ...prev, [roleCode]: null }));
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось добавить участника'), 'error');
        } finally {
            setBusy('');
        }
    };

    const remove = async (roleCode, userId) => {
        setBusy(`${roleCode}-${userId}`);
        try {
            const response = await axios.delete(`${apiBaseUrl}/api/payments/roles/${roleCode}/${userId}`, { headers: headers() });
            onChanged?.(response.data?.roles || {});
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось убрать участника'), 'error');
        } finally {
            setBusy('');
        }
    };

    return (
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {MEMBER_ROLES.map((role) => {
                const members = roles?.[role.code] || [];
                return (
                    <IosSection key={role.code} title={role.label} hint={role.hint}>
                        {members.length === 0 && (
                            <div className="text-[13px] text-slate-500">
                                {role.optional ? 'Пока никого.' : 'Пока никого. Без участника этой роли заявки не заводятся.'}
                            </div>
                        )}
                        {members.map((member) => (
                            <div key={member.user_id} className="flex items-center gap-3">
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-[13.5px] text-slate-900">{member.name}</div>
                                    <div className="truncate text-[12px] text-slate-500">
                                        {member.department_name || '—'}{member.has_telegram ? '' : ' · без Telegram: уведомления не дойдут'}
                                    </div>
                                </div>
                                {canEdit && (
                                    <button
                                        type="button"
                                        aria-label={`Убрать ${member.name}`}
                                        className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                                        disabled={busy === `${role.code}-${member.user_id}`}
                                        onClick={() => remove(role.code, member.user_id)}
                                    >
                                        <X size={14} />
                                    </button>
                                )}
                            </div>
                        ))}
                        {canEdit && (
                            <div className="flex flex-col gap-2 pt-1 sm:flex-row">
                                <div className="min-w-0 flex-1">
                                    <UserSelect
                                        users={users}
                                        value={picked[role.code] ?? null}
                                        onChange={(value) => setPicked((prev) => ({ ...prev, [role.code]: value }))}
                                        exclude={members.map((member) => member.user_id)}
                                        placeholder="Добавить сотрудника"
                                    />
                                </div>
                                <button type="button" className={`${iosBtnPrimary} shrink-0`} disabled={!picked[role.code] || busy === role.code} onClick={() => add(role.code)}>
                                    {busy === role.code ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Добавить
                                </button>
                            </div>
                        )}
                    </IosSection>
                );
            })}
        </div>
    );
};

export default PaymentsRoles;
