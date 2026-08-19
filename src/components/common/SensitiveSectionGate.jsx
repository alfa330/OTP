import React from 'react';
import FaIcon from './FaIcon';
import { iosCard, iosBtnPrimary } from '../ui/ios';

/*
 * Экран закрытого раздела: «Обращения» и «Вики» оператор открывает только
 * после того, как админ или супервайзер подтвердил его QR-код.
 *
 * Ключ тот же, что открывает записи и переписки в «Моих оценках»
 * (/api/sensitive-access), поэтому и экран один на оба раздела: два разных
 * замка на одну и ту же механику человек читал бы как две разные проблемы.
 *
 * Замок — это удобство, а не защита: доступом является ответ сервера, оба
 * раздела закрыты гейтом на каждом своём роуте. Здесь мы только объясняем, что
 * делать, и даём кнопку — иначе человек упирался бы в «403» без выхода.
 */

const STEPS = [
    'Нажмите «Сгенерировать QR» — код появится на экране.',
    'Покажите его супервайзеру или администратору.',
    'Он подтвердит доступ у себя в разделе «QR доступ» — раздел откроется сам.',
];

const SensitiveSectionGate = ({ sectionTitle, description, checking = false, onRequestQr }) => {
    if (checking) {
        // Пока статус сессии не приехал, замок не показываем: у того, кто уже
        // подтвердил доступ, он мигнул бы на долю секунды — и это выглядело бы
        // как отказ.
        return (
            <div className="mx-auto w-full max-w-xl px-4 py-12">
                <div className={`${iosCard} p-6 flex items-center justify-center gap-3 text-[13.5px] text-slate-500`}>
                    <FaIcon className="fas fa-circle-notch fa-spin text-slate-400" aria-hidden="true" />
                    <span>Проверяем доступ…</span>
                </div>
            </div>
        );
    }

    return (
        <div className="mx-auto w-full max-w-xl px-4 py-10 sm:py-14">
            <div className={`${iosCard} p-6 sm:p-8`}>
                <div className="flex flex-col items-center text-center">
                    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 ring-1 ring-blue-100">
                        <FaIcon className="fas fa-lock text-[20px] text-blue-600" aria-hidden="true" />
                    </div>
                    <h2 className="mt-4 text-[19px] font-semibold text-slate-900">
                        Раздел «{sectionTitle}» открывается по QR
                    </h2>
                    <p className="mt-2 text-[13.5px] leading-relaxed text-slate-600">
                        {description}
                    </p>
                </div>

                <ol className="mt-6 space-y-2.5">
                    {STEPS.map((step, index) => (
                        <li key={index} className="flex items-start gap-3 text-[13.5px] text-slate-700">
                            <span className="mt-[1px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">
                                {index + 1}
                            </span>
                            <span>{step}</span>
                        </li>
                    ))}
                </ol>

                <button type="button" onClick={onRequestQr} className={`${iosBtnPrimary} mt-6 w-full`}>
                    <FaIcon className="fas fa-qrcode" aria-hidden="true" />
                    Сгенерировать QR
                </button>

                <p className="mt-3 text-center text-[11.5px] leading-relaxed text-slate-500">
                    Подтверждение действует до конца этой сессии и только на этом устройстве.
                    После нового входа в портал раздел снова попросит код.
                </p>
            </div>
        </div>
    );
};

export default SensitiveSectionGate;
