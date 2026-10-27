import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Trash2 } from 'lucide-react';
import { IosModal, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import { AccountTiles } from './TwofaPeopleSheet';
import { accountsCount, errorOf } from './twofaMeta';
import { FIELD_ERROR, FIELD_LABEL, INVALID_RING } from './twofaUi';

/*
 * Раздел аккаунтов «Кодов 2FA»: название и состав (владелец 27.10.2026: «руководитель
 * маркетинга и суперадмины могут редактировать составы этих кодов, менять названия
 * разделов»). Состав — галочки по всем аккаунтам под заголовками их нынешних разделов:
 * отмеченный из другого раздела переедет сюда, снятый отсюда останется без раздела. Доступы
 * и ключи раздел не трогает.
 *
 * group = { id: null } — новый раздел.
 */

export const GroupSheet = ({ group, accounts, groups, onClose, api, onSaved, onRemoved }) => {
    const isNew = group?.id == null;
    const initial = useMemo(() => new Set(isNew ? [] : (accounts || [])
        .filter((account) => account.group_id === group?.id).map((account) => account.id)), [group, accounts, isNew]);
    const [title, setTitle] = useState('');
    const [picked, setPicked] = useState(() => new Set());
    const [titleError, setTitleError] = useState('');
    const [problem, setProblem] = useState('');
    const [busy, setBusy] = useState('');
    const [confirmRemove, setConfirmRemove] = useState(false);
    const titleRef = useRef(null);
    const initialRef = useRef(initial);
    initialRef.current = initial;

    // Окно открыли — начать с того, что есть сейчас. Перечитанный за спиной экран (колокол)
    // отмеченное не сбрасывает: эффект смотрит только на сам раздел.
    useEffect(() => {
        setTitle(group?.title || '');
        setPicked(new Set(initialRef.current));
        setTitleError('');
        setProblem('');
        setBusy('');
        setConfirmRemove(false);
    }, [group]);

    if (!group) return <IosModal open={false} onClose={onClose} title="" />;

    const cleanTitle = title.trim();
    const titleChanged = cleanTitle !== (group.title || '');
    const incoming = [...picked].filter((id) => !initial.has(id)).length;
    const leaving = [...initial].filter((id) => !picked.has(id)).length;
    const changed = isNew || titleChanged || incoming > 0 || leaving > 0;
    // Свой раздел — первым: его аккаунты и есть то, что правят.
    const ordered = isNew ? groups : [group, ...(groups || []).filter((item) => item.id !== group.id)];

    const toggle = (id) => setPicked((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const save = async () => {
        if (!cleanTitle) {
            setTitleError('Укажите название раздела');
            titleRef.current?.focus();
            return;
        }
        setBusy('save');
        try {
            if (isNew) {
                await api.post('/groups', { title: cleanTitle, account_ids: [...picked] });
            } else {
                await api.patch(`/groups/${group.id}`, {
                    ...(titleChanged ? { title: cleanTitle } : {}),
                    ...(incoming || leaving ? { account_ids: [...picked] } : {}),
                });
            }
            onSaved?.(isNew);
            onClose();
        } catch (requestError) {
            const message = errorOf(requestError, 'Не сохранилось');
            if (requestError?.response?.data?.field === 'title') {
                setTitleError(message);
                titleRef.current?.focus();
            } else {
                setProblem(message);
            }
            setBusy('');
        }
    };

    const remove = async () => {
        if (!confirmRemove) { setConfirmRemove(true); return; }
        setBusy('remove');
        try {
            const { data } = await api.delete(`/groups/${group.id}`);
            onRemoved?.(group, data?.accounts || 0);
            onClose();
        } catch (requestError) {
            setProblem(errorOf(requestError, 'Не удалилось'));
            setBusy('');
        }
    };

    const moves = [
        incoming ? `сюда перейдут: ${incoming}` : '',
        leaving ? `без раздела останутся: ${leaving}` : '',
    ].filter(Boolean).join(' · ');

    return (
        <IosModal
            open={Boolean(group)}
            onClose={onClose}
            title={isNew ? 'Новый раздел' : group.title}
            subtitle={isNew ? null : accountsCount(initial.size)}
            maxWidth="max-w-lg"
            footer={(
                <div className="flex w-full items-center justify-between gap-2">
                    {!isNew ? (
                        <button type="button" onClick={remove} disabled={Boolean(busy)}
                                className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                    confirmRemove ? 'bg-rose-600 text-white hover:bg-rose-700' : 'text-rose-600 hover:bg-rose-50'
                                }`}>
                            {busy === 'remove' ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                            {confirmRemove ? 'Удалить раздел' : 'Удалить'}
                        </button>
                    ) : <span />}
                    <div className="flex items-center gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={Boolean(busy) || !changed}>
                            {busy === 'save' && <Loader2 size={15} className="animate-spin" />}
                            {isNew ? 'Создать' : 'Сохранить'}
                        </button>
                    </div>
                </div>
            )}
        >
            <div className="space-y-4">
                <div>
                    <label className={FIELD_LABEL} htmlFor="twofa-group-title">Название раздела</label>
                    <input id="twofa-group-title" ref={titleRef} className={`${iosInput} ${titleError ? INVALID_RING : ''}`}
                           value={title} maxLength={120} placeholder="Например, SMM и таргет"
                           onChange={(event) => { setTitle(event.target.value); setTitleError(''); }} />
                    {titleError && <p className={FIELD_ERROR}>{titleError}</p>}
                </div>
                <div>
                    <div className={FIELD_LABEL}>Аккаунты раздела</div>
                    {(accounts || []).length === 0 ? (
                        <p className="px-1 text-[13px] text-slate-500">Аккаунтов пока нет — раздел можно наполнить потом.</p>
                    ) : (
                        <AccountTiles accounts={accounts} groups={ordered} labelled
                                      isChecked={(id) => picked.has(id)} onToggle={toggle} />
                    )}
                </div>
                {moves && <p className="px-1 text-[12.5px] text-slate-600">После сохранения {moves}.</p>}
                {confirmRemove && (
                    <p className="px-1 text-[12.5px] text-rose-600">
                        {initial.size
                            ? `Раздел удалится, его аккаунты останутся — без раздела (${accountsCount(initial.size)}). Доступы и ключи не меняются.`
                            : 'Раздел пуст — удалится только он.'}
                    </p>
                )}
                {problem && <p className={FIELD_ERROR}>{problem}</p>}
            </div>
        </IosModal>
    );
};
