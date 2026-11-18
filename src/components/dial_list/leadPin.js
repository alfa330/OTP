/* Закреплённые водители в журнале (владелец, 18.11.2026; сервер — dial_list/pins.py): водителя выдают только
   оператору, за которым он закреплён. Журнал показывает это в том же месте, где «Ответственный», — без новых
   колонок: в списке у оператора сейчас — он; иначе тот, за кем водитель закреплён; иначе — кто звонил последним.
   lead.pinned_operator сервер присылает, только пока закрепление в силе (оператор работает в отделе). */

export const PIN_SUB = 'закреплён';
const CURRENT_SUB = 'в списке сейчас';

/** Кто ответственный за водителя и почему: kind — 'current' (в списке сейчас) | 'pinned' (закреплён, сейчас ни у
    кого в списке) | 'last' (звонил последним) | 'none'; pinned — водитель закреплён за этим же человеком. */
export function responsibleOf(lead) {
    const pinned = lead?.pinned_operator || null;
    const responsible = lead?.responsible || null;
    if (lead?.responsible_is_current && responsible) {
        return { person: responsible, kind: 'current', pinned: Boolean(pinned) && String(pinned.id) === String(responsible.id) };
    }
    if (pinned) return { person: pinned, kind: 'pinned', pinned: true };
    if (responsible) return { person: responsible, kind: 'last', pinned: false };
    return { person: null, kind: 'none', pinned: false };
}

/** Подпись под ответственным. lastText — своя у строки списка (время звонка) и у карточки («по последнему
    звонку»). */
export function responsibleSub(resp, lastText = '') {
    if (!resp) return '';
    if (resp.kind === 'current') return resp.pinned ? `${CURRENT_SUB} · ${PIN_SUB}` : CURRENT_SUB;
    if (resp.kind === 'pinned') return PIN_SUB;
    if (resp.kind === 'last') return lastText || '';
    return '';
}
