import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Loader2, Plus, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { iosBtnGhost, iosBtnPrimary, iosInput } from '../ui/ios';
import { cancelsForm } from './ChatDriverCard';
import { defaultNewChatChannel, newChatChannelOptions, normalizeNewChatPhone } from './newChat';

const closeButton = 'grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95';
const label = 'mb-1 block px-1 text-[12px] font-medium text-slate-500';

/* Кнопка «+» рядом с поиском: новый чат по номеру (newChat.js).
 *
 * Окно — поповер под кнопкой, как «Карточка водителя»: набранное не теряется от
 * случайного щелчка мимо, закрывают его «Отмена», крестик или Escape. Канал
 * спрашивается, только если начинать можно больше чем в одном.
 *
 * onStart({ channelId, phone, name }) открывает чат — существующий или
 * заготовку; пока он ищет существующий, кнопка «Начать» крутится. */
export default function NewChatButton({ channels, allowedIds, preferredChannelId, onStart }) {
    const [open, setOpen] = useState(false);
    const [channelId, setChannelId] = useState('');
    const [phone, setPhone] = useState('');
    const [name, setName] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const root = useRef(null);
    const phoneRef = useRef(null);
    /* Список каналов (CustomSelect) рисуется в портале, вне окна: щелчок по нему
       для document — «мимо», и окно закрывалось вместе с выбором канала. Событие
       React из портала всплывает до окна раньше, чем до document, — по нему окно
       и узнаёт свой щелчок. */
    const pointerInside = useRef(false);
    // Варианты — постоянным массивом: CustomSelect пересчитывает подсветку на каждый новый.
    const options = useMemo(() => newChatChannelOptions(channels, allowedIds), [channels, allowedIds]);

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (event) => {
            const inside = pointerInside.current || root.current?.contains(event.target);
            pointerInside.current = false;
            if (inside || phone.trim() || name.trim()) return;
            setOpen(false);
        };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open, phone, name]);

    if (!options.length) return null;

    const toggle = () => {
        if (open) { setOpen(false); return; }
        setChannelId(defaultNewChatChannel(options, preferredChannelId));
        setPhone(''); setName(''); setError(''); setBusy(false);
        setOpen(true);
        requestAnimationFrame(() => phoneRef.current?.focus());
    };

    const submit = async (event) => {
        event.preventDefault();
        if (busy) return;
        const digits = normalizeNewChatPhone(phone);
        if (!digits) {
            setError('Введите номер полностью, например +7 700 123 45 67');
            phoneRef.current?.focus();
            return;
        }
        if (!options.some((option) => option.value === channelId)) {
            setError('Выберите канал');
            return;
        }
        setBusy(true);
        setError('');
        try {
            await onStart({ channelId, phone: digits, name: name.trim() });
            setOpen(false);
        } catch (failure) {
            setError(failure?.message || 'Не удалось открыть чат. Попробуйте ещё раз.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div ref={root} className="relative shrink-0" onMouseDown={() => { pointerInside.current = true; }}>
            <button type="button" onClick={toggle} aria-expanded={open} aria-haspopup="dialog"
                aria-label="Новый чат по номеру" title="Новый чат по номеру" data-testid="wazzup-new-chat-button"
                className={`grid h-9 w-9 place-items-center rounded-xl transition active:scale-[0.97] ${
                    open ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                <Plus size={18} aria-hidden="true" />
            </button>
            {open && (
                <div role="dialog" aria-label="Новый чат"
                    className="absolute right-0 top-full z-40 mt-2 w-[300px] max-w-[calc(100vw-24px)] rounded-2xl bg-white p-3.5 text-left shadow-[0_14px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-200/80 motion-safe:animate-popover-in">
                    <form onSubmit={submit} onKeyDown={(event) => { if (cancelsForm(event)) setOpen(false); }}
                        className="space-y-2.5" data-testid="wazzup-new-chat-form">
                        <div className="flex items-center justify-between gap-2">
                            <span className="text-[14px] font-semibold text-slate-900">Новый чат</span>
                            <button type="button" onClick={() => setOpen(false)} aria-label="Закрыть" className={closeButton}>
                                <X size={14} aria-hidden="true" />
                            </button>
                        </div>
                        {options.length > 1 && (
                            <div>
                                <span className={label}>Канал</span>
                                <CustomSelect variant="ios" searchable={options.length > 6} value={channelId}
                                    onChange={(value) => { setChannelId(value); setError(''); }}
                                    options={options} ariaLabel="Канал" />
                            </div>
                        )}
                        <label className="block">
                            <span className={label}>Номер</span>
                            <input ref={phoneRef} value={phone} inputMode="tel" autoComplete="off"
                                onChange={(event) => { setPhone(event.target.value); setError(''); }}
                                placeholder="+7 700 123 45 67" className={`${iosInput} py-2 text-[13.5px]`} />
                        </label>
                        <label className="block">
                            <span className={label}>Имя · необязательно</span>
                            <input value={name} maxLength={100} autoComplete="off"
                                onChange={(event) => setName(event.target.value)}
                                placeholder="Как подписать чат" className={`${iosInput} py-2 text-[13.5px]`} />
                        </label>
                        {error && (
                            <div role="alert" className="flex items-start gap-1.5 text-[12px] text-rose-600">
                                <AlertCircle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                                <span>{error}</span>
                            </div>
                        )}
                        <div className="flex justify-end gap-1.5 pt-0.5">
                            <button type="button" onClick={() => setOpen(false)} className={iosBtnGhost}>Отмена</button>
                            <button type="submit" disabled={busy} className={`${iosBtnPrimary} py-2`}>
                                {busy && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
                                Начать
                            </button>
                        </div>
                    </form>
                </div>
            )}
        </div>
    );
}
