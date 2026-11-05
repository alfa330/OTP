import React, { useState } from 'react';
import { Award, BadgeCheck, Car, Crown, HeartHandshake, MessageCircleHeart, TrendingUp, Zap } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { initialsOf } from './honorBoardMeta';

/*
 * Общие кусочки «Доски почёта»: фото победителя и бейдж номинации. Ими рисуют
 * и доска, и окно тренера — карточка в окне выглядит так же, как её увидят.
 */

const ICONS = { Award, BadgeCheck, Car, Crown, HeartHandshake, MessageCircleHeart, TrendingUp, Zap };

export const NominationIcon = ({ name, ...props }) => {
    const Icon = ICONS[name] || Award;
    return <Icon aria-hidden="true" {...props} />;
};

/* Кружок с фото из профиля. Кружок — сетка, а у картинки свой размер в
   пикселях: высокий снимок в `h-full` сетки вставал бы вытянутым и держался
   одной обрезкой. Не загрузилось — инициалы; запоминаем саму негодную ссылку:
   придёт свежая (подпись живёт сутки) — фото вернётся. */
export const HonorPhoto = ({ name, url, size = 88 }) => {
    const [brokenUrl, setBrokenUrl] = useState('');
    const photo = Boolean(url) && url !== brokenUrl;
    return (
        <span
            data-honor-photo={photo ? 'photo' : 'initials'}
            className="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-100 font-semibold text-slate-500"
            style={{ width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.34)) }}
        >
            {photo ? (
                <img
                    src={url}
                    alt=""
                    className="block object-cover"
                    style={{ width: size, height: size }}
                    loading="lazy"
                    decoding="async"
                    draggable={false}
                    onError={() => setBrokenUrl(url)}
                />
            ) : initialsOf(name)}
        </span>
    );
};

/* Бейдж номинации — «название с тематической иконкой» (ТЗ, п. 3). Цвет со
   смыслом: золото только у ключевой номинации, остальные — синим сайта.
   Название своей номинации Фронт-офиса бывает длинным (до 60 знаков) — оно
   переносится, а не обрезается многоточием: прочитать его больше негде.
   За что дают номинацию — под «i» рядом, общим InfoHint: подсказка браузера
   по наведению на телефоне не открывается вовсе. */
export const NominationBadge = ({ meta, className = '' }) => (
    <span className={`inline-flex max-w-full items-center justify-center gap-1.5 ${className}`}>
        <span
            className={`inline-flex min-w-0 items-start gap-1.5 rounded-[14px] px-2.5 py-1 text-[12.5px] font-semibold leading-snug ${
                meta.main ? 'bg-amber-50 text-amber-700 ring-1 ring-amber-200' : 'bg-blue-50 text-blue-700'
            }`}
        >
            <NominationIcon name={meta.icon} size={14} strokeWidth={2.2} className="mt-[2px] shrink-0" />
            <span className="min-w-0 break-words">{meta.title}</span>
        </span>
        {meta.reason && <InfoHint text={`За что: ${meta.reason}`} side="left" />}
    </span>
);
