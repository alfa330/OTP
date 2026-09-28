import React from 'react';
import { Check } from 'lucide-react';
import { IosHint } from '../ui/ios';

/* «Кому засчитать» в разборе низкой оценки (задача #286).
 *
 * Chat2Desk отдаёт низкую оценку тому, кто закрыл чат, а вести чат могли
 * несколько менеджеров: первый начал, потом передали — конец смены, обед,
 * эскалация. Здесь проверяющий видит, кто вёл чат и на каком отрезке, и сам
 * отмечает, кому оценка засчитывается. Отмеченных может быть несколько —
 * тогда оценка идёт каждому целиком, а не делится.
 *
 * Отметки сохраняются вместе с вердиктом «Обоснованно»: необоснованная не
 * засчитывается никому, поэтому при ней выбирать некого. Если в чате один
 * менеджер, выбирать тоже нечего — показываем просто его имя. */

const plural = (n, one, few, many) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
};

// Время из снапшота — наивная местная строка «YYYY-MM-DDTHH:MM:SS»: берём как
// есть, без Date, чтобы пояс браузера его не сдвинул.
const clock = (iso) => (iso ? String(iso).slice(11, 16) : '');

export const participantMeta = (participant) => {
    const from = clock(participant?.first_at);
    const to = clock(participant?.last_at);
    const span = from && to && from !== to ? `${from}–${to}` : from;
    const replies = Number(participant?.replies || 0);
    const repliesText = replies > 0
        ? `${replies} ${plural(replies, 'ответ', 'ответа', 'ответов')}`
        : (span ? 'без ответов' : '');
    return [span, repliesText].filter(Boolean).join(' · ');
};

/* Кого показывать: менеджеры из переписки плюс уже засчитанные — последних
 * в переписке может не оказаться (старый снапшот), но снять отметку должно
 * быть можно. */
export const mergeParticipants = (participants, attributed) => {
    const list = Array.isArray(participants) ? participants.filter(Boolean) : [];
    const seen = new Set(list.map((p) => p.id).filter((id) => id != null).map(String));
    (Array.isArray(attributed) ? attributed : []).forEach((op) => {
        if (op?.id == null || seen.has(String(op.id))) return;
        seen.add(String(op.id));
        list.push({ id: op.id, name: op.name || '', first_at: null, last_at: null, replies: 0 });
    });
    return list;
};

const HINT = 'Chat2Desk отдаёт оценку менеджеру, который закрыл чат, — он отмечен по умолчанию. '
    + 'Если чат вели несколько человек, отметьте тех, из-за кого оценка возникла: '
    + 'каждому она засчитается целиком. Отметки сохраняются вместе с вердиктом «Обоснованно».';

export default function LowRatingAttribution({
    participants = [],
    attributed = [],
    value = [],
    onChange,
    verdict = '',
    disabled = false,
}) {
    const list = mergeParticipants(participants, attributed);
    const selected = new Set((Array.isArray(value) ? value : []).map(String));
    // selectable приходит с сервера: учётка без сотрудника или менеджер чужого
    // отдела (проверяющему с ограничением по отделу) видны, но не выбираются.
    const canPick = (p) => p.id != null && p.selectable !== false;
    const selectable = list.filter(canPick);

    if (verdict === 'invalid') {
        return (
            <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Кому засчитать</span>
                <span className="text-[12.5px] text-slate-500">Необоснованная оценка не засчитывается никому</span>
            </div>
        );
    }

    if (selectable.length <= 1) {
        const only = selectable[0] || list[0];
        if (!only) return null;
        return (
            <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Засчитывается</span>
                <span className="text-[12.5px] font-semibold text-slate-800">{only.name || 'Менеджер'}</span>
                {participantMeta(only) && (
                    <span className="text-[11.5px] tabular-nums text-slate-400">{participantMeta(only)}</span>
                )}
            </div>
        );
    }

    // onChange получает функцию от текущего значения (как setState): два быстрых
    // нажатия до перерисовки не должны терять первое.
    const toggle = (id) => {
        const key = String(id);
        onChange?.((current) => {
            const list = Array.isArray(current) ? current : [];
            return list.some((v) => String(v) === key)
                ? list.filter((v) => String(v) !== key)
                : [...list, id];
        });
    };

    return (
        <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
            <span className="flex items-center gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Кому засчитать</span>
                <IosHint text={HINT} label="Как засчитывается оценка" />
            </span>
            {list.map((participant, index) => {
                const known = canPick(participant);
                const checked = participant.id != null && selected.has(String(participant.id));
                const meta = participantMeta(participant);
                let hint;
                if (participant.id == null) hint = 'Учётки Chat2Desk нет среди сотрудников — засчитать ей оценку нельзя';
                else if (!known) hint = 'Менеджер другого отдела — засчитать ему оценку вы не можете';
                else if (participant.is_default) hint = 'Этому менеджеру оценку отдал Chat2Desk';
                return (
                    <button
                        key={participant.id != null
                            ? `u-${participant.id}`
                            : `c-${participant.c2d_id ?? index}`}
                        type="button"
                        role="checkbox"
                        aria-checked={checked}
                        disabled={disabled || !known}
                        onClick={() => toggle(participant.id)}
                        title={hint}
                        className={`inline-flex min-h-9 max-w-full items-center gap-2 rounded-full py-1 pl-1.5 pr-3.5 text-left transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 ${
                            checked
                                ? 'bg-slate-900 text-white shadow-sm'
                                : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50'
                        }`}
                    >
                        <span
                            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full ${
                                checked ? 'bg-white text-slate-900' : 'bg-white ring-1 ring-slate-300'
                            }`}
                            aria-hidden="true"
                        >
                            {checked && <Check size={13} strokeWidth={3} />}
                        </span>
                        <span className="min-w-0">
                            <span className="block truncate text-[12.5px] font-semibold leading-tight">
                                {participant.name || 'Без имени'}
                            </span>
                            {meta && (
                                <span className={`block text-[10.5px] font-medium leading-tight tabular-nums ${
                                    checked ? 'text-white/70' : 'text-slate-400'
                                }`}>
                                    {meta}
                                </span>
                            )}
                        </span>
                    </button>
                );
            })}
        </div>
    );
}
