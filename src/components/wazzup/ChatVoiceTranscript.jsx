import React, { useRef } from 'react';
import { Loader2 } from 'lucide-react';

/* Текст голосового под плеером в пузыре. Готовый текст — цветом самого пузыря
 * (его перекрашивают темы); что он машинный, сказано в подсказке, а не надписью.
 * Служебные состояния — одной тихой строкой; «не будет текста» — ничем.
 * Строка состояний — один и тот же элемент: после «Повторить» фокус переходит на
 * неё и не теряется вместе с исчезнувшей кнопкой, а смену текста объявляет role=status. */
export default function ChatVoiceTranscript({ state, onRetry }) {
    const line = useRef(null);
    if (!state) return null;
    if (state.status === 'ready' && state.text) {
        return <p className="wazzup-voice-transcript mt-1.5 whitespace-pre-wrap break-words text-[15px] leading-[1.4]"
            title="Расшифровка голосового (ИИ) — возможны неточности" data-testid="wazzup-voice-transcript">
            {state.text}
        </p>;
    }
    let content = null;
    if (state.status === 'pending') {
        content = <><Loader2 size={12} className="animate-spin" aria-hidden="true" /> Расшифровываем…</>;
    } else if (state.status === 'no_speech') {
        // И тишина, и два несовпавших прослушивания (wazzup/voice_asr.py): слушать самому.
        content = 'Речь не разобрать';
    } else if (state.status === 'skipped') {
        content = 'Запись слишком длинная для расшифровки';
    } else if (state.status === 'failed') {
        content = <>
            Не удалось расшифровать
            {onRetry && <button type="button" onClick={() => { onRetry(); line.current?.focus(); }}
                className="font-semibold text-blue-600 hover:underline">Повторить</button>}
        </>;
    }
    if (!content) return null;
    return <div ref={line} tabIndex={-1} role="status"
        className="mt-1 flex items-center gap-1.5 text-[12px] leading-4 text-slate-500 focus:outline-none">
        {content}
    </div>;
}
