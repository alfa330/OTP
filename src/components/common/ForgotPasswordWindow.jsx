import React, { useCallback, useEffect, useRef, useState } from 'react';
import { IosModal, iosCard } from '../ui/ios';
import {
    PASSWORD_HELP_CONTACTS,
    passwordHelpDraft,
    telegramChatUrl,
    telegramUserpicUrl,
} from './passwordHelp';

/*
 * Окно «Забыли пароль?» на экране входа: кому написать в Telegram, чтобы
 * восстановили доступ (просьба владельца 05.11.2026 — Руслан или Шерзад).
 *
 * Окно — общий IosModal: на компьютере проявляется по кривой macOS и закрывается
 * крестиком, щелчком мимо и Esc; на телефоне въезжает экраном справа со
 * стрелкой «назад». Телефонные стили окон включает класс body.mobile-shell, а
 * его App ставит и до входа (useMobileShell вызывается без условия).
 *
 * Хозяин держит окно смонтированным и закрывает его через open: снятое
 * условием окно не успело бы уйти анимацией.
 *
 * Ссылка сразу подставляет в поле сообщения текст с логином из формы входа:
 * человеку остаётся нажать «Отправить», а тому, кто помогает, не нужно
 * переспрашивать, чья это учётка.
 */

/* Знак Telegram (Simple Icons, CC0). Кружок со «самолётиком» узнают сразу —
   по нему понятно, что строка откроет Telegram, без лишней подписи. */
function TelegramGlyph({ className }) {
    return (
        <svg viewBox="0 0 24 24" className={className} aria-hidden="true" focusable="false">
            <path
                fill="currentColor"
                d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"
            />
        </svg>
    );
}

/* Фото профиля Telegram, а под ним — буква имени на сером круге, как в
   «Контактах» iOS. Фото проявляется, только когда загрузилось: пока его нет
   или Telegram недоступен, человек видит букву, а не пустое место и не значок
   битой картинки. Размер кружка задан заранее — строка не прыгает, когда
   приходит фото. */
function ContactAvatar({ name, username }) {
    const [photo, setPhoto] = useState('loading');
    /* Фото из кэша браузера бывает готово раньше, чем React повесит onLoad. */
    const checkReady = useCallback((img) => {
        if (img && img.complete && img.naturalWidth > 0) setPhoto('ready');
    }, []);
    return (
        <span
            aria-hidden="true"
            className="relative grid h-11 w-11 shrink-0 select-none place-items-center overflow-hidden rounded-full bg-gradient-to-b from-slate-300 to-slate-400 text-[17px] font-semibold text-white"
        >
            {name.charAt(0)}
            {photo !== 'failed' && (
                <img
                    ref={checkReady}
                    src={telegramUserpicUrl(username)}
                    alt=""
                    width={44}
                    height={44}
                    draggable={false}
                    decoding="async"
                    /* Адрес портала Telegram знать незачем. */
                    referrerPolicy="no-referrer"
                    onLoad={() => setPhoto('ready')}
                    onError={() => setPhoto('failed')}
                    className={`absolute inset-0 h-11 w-11 object-cover transition-opacity duration-200 ${photo === 'ready' ? 'opacity-100' : 'opacity-0'}`}
                />
            )}
        </span>
    );
}

export default function ForgotPasswordWindow({ open, onClose, login = '' }) {
    /* Esc — как у остальных окон macOS. У IosModal своего Esc нет, а
       FullscreenSheet закрывается им же. Обработчик через ref: хозяин передаёт
       onClose новой стрелкой на каждой отрисовке, переподписываться незачем. */
    const closeRef = useRef(onClose);
    closeRef.current = onClose;
    useEffect(() => {
        if (!open) return undefined;
        const onKeyDown = (event) => {
            if (event.key === 'Escape') closeRef.current?.();
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [open]);

    const draft = passwordHelpDraft(login);

    return (
        <IosModal open={open} onClose={onClose} title="Забыли пароль?" maxWidth="max-w-sm">
            <p className="px-1 text-[14px] leading-relaxed text-slate-600">
                Напишите в Telegram Руслану или Шерзаду — они помогут восстановить доступ.
            </p>
            <ul className={`mt-3 overflow-hidden ${iosCard}`}>
                {PASSWORD_HELP_CONTACTS.map((contact, index) => (
                    <li key={contact.username} className="relative">
                        {/* Разделитель с отступом — от начала текста, как в
                            списках iOS: под кружком линия не нужна. */}
                        {index > 0 && (
                            <span aria-hidden="true" className="pointer-events-none absolute left-[4.375rem] right-0 top-0 h-px bg-slate-200/70" />
                        )}
                        <a
                            href={telegramChatUrl(contact.username, draft)}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Написать ${contact.dative} в Telegram`}
                            className="flex items-center gap-3 px-3.5 py-3 outline-none transition-colors hover:bg-slate-50 focus-visible:bg-slate-100 active:bg-slate-100"
                        >
                            <ContactAvatar name={contact.name} username={contact.username} />
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[15px] font-semibold leading-tight text-slate-900">
                                    {contact.name}
                                </span>
                                <span className="mt-0.5 block truncate text-[13px] text-slate-500">
                                    @{contact.username}
                                </span>
                            </span>
                            <TelegramGlyph className="h-7 w-7 shrink-0 text-[#2AABEE]" />
                        </a>
                    </li>
                ))}
            </ul>
        </IosModal>
    );
}
