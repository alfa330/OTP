import React, { useState } from 'react';

import { Tap } from './screenKit';
import { BackspaceIcon, EgovLogo, FingerprintIcon, SignedBadge } from './appIcons';
import { TRAINEE } from './scenarioTaxiPro';

/* Экраны eGov Mobile — общие для обоих тренажёров.
 *
 * Так и в жизни: и приложение Такси.Про, и сайт Сапар приводят в одно и то же
 * приложение подписи. Два набора экранов расползлись бы по деталям, и человек
 * учился бы двум разным eGov вместо одного.
 *
 * Нарисовано по настоящим кадрам подписания: чёрно-сине-жёлтый знак eGov,
 * крупная надпись «Подписание услуги», круглые клавиши кода с отпечатком
 * пальца, лист «Подписать документ» с полями «Срок действия QR-кода» и
 * «От кого», синяя кнопка «Подписать» (#2b6bcc) и красная надпись «Отказать»
 * без подложки. Раньше здесь стоял выдуманный экран с точками-квадратиками и
 * двумя одинаковыми кнопками — по нему настоящий eGov не узнавался.
 *
 * purpose ('auth' | 'docs') меняет ТОЛЬКО подписи: чей это документ и что
 * подписывается. Логика шага живёт в сценарии — экран о ней не знает.
 */

/* ── Код быстрого доступа ─────────────────────────────────────────────────── */

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'bio', '0', 'del'];

/** Код быстрого доступа. Своё состояние — вводимые цифры не касаются мира:
 *  промах по цифре не должен становиться частью учебной попытки.
 *
 *  Кнопки «Далее» здесь НЕТ, и это не упрощение: eGov отправляет код сам, как
 *  только набрана четвёртая цифра. Пока «Далее» стояла, человек искал её и в
 *  настоящем приложении. */
export const EgCode = ({ tap, target, purpose }) => {
    const [code, setCode] = useState('');
    const press = (key) => {
        if (key === 'del') { setCode((v) => v.slice(0, -1)); return; }
        if (key === 'bio') return;
        const next = (code + key).slice(0, 4);
        setCode(next);
        if (next.length !== 4) return;
        // Четвёртая цифра — и код уходит на проверку сам, как в приложении.
        tap('submit_code', { code: next });
        // Неверный код приложение стирает и ждёт новый; на верном экран всё
        // равно сменится, и очистки не видно.
        setCode('');
    };
    return (
        <div className="wt-screen wt-eg wt-eg--code">
            <Tap id="egov_close" target={target} tap={tap} className="wt-eg__close"
                aria-label="Закрыть eGov Mobile">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1"
                    strokeLinecap="round" aria-hidden="true">
                    <path d="M6 6l12 12M18 6 6 18" />
                </svg>
            </Tap>
            <EgovLogo />
            <h2 className="wt-eg__big">
                {purpose === 'auth' ? 'Подписание услуги' : 'Подписание документов'}
            </h2>
            <p className="wt-eg__lead">Введите код для быстрого доступа к приложению</p>
            <div className="wt-eg__dots" aria-label={`Введено цифр: ${code.length} из 4`}>
                {[0, 1, 2, 3].map((i) => (
                    <i key={i} className={i < code.length ? 'is-filled' : ''} />
                ))}
            </div>
            <div className="wt-eg__pad">
                {KEYS.map((key) => (
                    <button
                        key={key}
                        type="button"
                        onClick={() => press(key)}
                        className={key === 'bio' || key === 'del' ? 'is-aux' : ''}
                        aria-label={
                            key === 'del' ? 'Удалить цифру'
                                : key === 'bio' ? 'Вход по отпечатку пальца'
                                    : `Цифра ${key}`
                        }
                    >
                        {key === 'del' ? <BackspaceIcon />
                            : key === 'bio' ? <FingerprintIcon />
                                : key}
                    </button>
                ))}
            </div>
        </div>
    );
};

/* ── Лист подписания ──────────────────────────────────────────────────────── */

/** Период в сценарии хранится с предлогом («за Июль 2026») — реплики барса
 *  читаются «акты ЗА июль». В подписях экрана предлог свой, поэтому лишний
 *  срезаем: иначе на экране стоит «Документы за за Июль 2026». */
const bare = (label) => String(label || '').replace(/^за\s+/i, '');

/** Строка «поле — значение» листа eGov: подпись сверху серым, значение снизу
 *  чёрным. Это НЕ та же строка, что в кабинете (там значение справа), и
 *  различие видно с первого взгляда — по нему и отличают приложение от сайта. */
const EgField = ({ label, children }) => (
    <div className="wt-eg__field">
        <span>{label}</span>
        <b>{children}</b>
    </div>
);

export const EgSign = ({ world, tap, toggle, target, purpose, period }) => {
    const open = !!world.egovExpanded;
    return (
        <div className="wt-screen wt-eg wt-eg--sign">
            <header className="wt-eg__head">
                <Tap id="egov_close" target={target} tap={tap} className="wt-eg__close"
                    aria-label="Закрыть eGov Mobile">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1"
                        strokeLinecap="round" aria-hidden="true">
                        <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                </Tap>
                <b>Подписание</b>
            </header>
            <h2 className="wt-eg__title">Подписать документ</h2>
            <div className="wt-eg__fields">
                <span className="wt-eg__group">Подписание действия</span>
                <EgField label="Срок действия QR-кода">
                    {world.qrValid || 'сегодня, 15:25'}
                </EgField>
                <EgField label="От кого">
                    {TRAINEE.requester}: {purpose === 'auth'
                        ? 'Вход в систему Sapar'
                        : 'Подписание документов'}
                </EgField>
                {purpose === 'docs' && (
                    <p className="wt-eg__warn">
                        * После подписания необходимо самостоятельно вернуться на
                        веб-страницу портала, для которого запрошено разрешение и нажать
                        на кнопку &laquo;Далее&raquo;
                    </p>
                )}
            </div>
            <div className="wt-eg__docs">
                <span className="wt-eg__group">Документы на подписание</span>
                <button
                    type="button"
                    className={`wt-eg__doc${open ? ' is-open' : ''}`}
                    onClick={() => toggle('egovExpanded')}
                    aria-expanded={open}
                >
                    {purpose === 'auth' ? 'Авторизация' : `Документы за ${bare(period)}`}
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
                        stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                        strokeLinejoin="round" aria-hidden="true">
                        <path d="m6 9 6 6 6-6" />
                    </svg>
                </button>
                {open && (
                    <div className="wt-eg__docbody">
                        {purpose === 'auth' ? (
                            <div>Вход в личный кабинет водителя Sapar</div>
                        ) : (
                            <>
                                <div>АВР №66909180/26: {bare(period)}</div>
                                <div>АВР №1: {bare(period)}</div>
                            </>
                        )}
                    </div>
                )}
            </div>
            {/* Кнопки прижаты к низу экрана, «Отказать» — красной надписью без
                подложки. Пока обе были одинаковыми плашками, отказ нажимали
                случайно, а в приложении промахнуться по нему заметно труднее. */}
            <div className="wt-eg__actions">
                <Tap id="approve" target={target} tap={tap} className="wt-eg-primary">
                    Подписать
                </Tap>
                <Tap id="decline" target={target} tap={tap} className="wt-eg-decline">
                    Отказать
                </Tap>
            </div>
        </div>
    );
};

/* ── Подписано ───────────────────────────────────────────────────────────── */

export const EgSuccess = ({ tap, target, purpose }) => (
    <div className="wt-screen wt-eg wt-eg--done">
        <div className="wt-eg__done-body">
            <SignedBadge />
            <b>Подписание выполнено успешно!</b>
            <p>
                {purpose === 'auth'
                    ? 'Вход подтверждён. Портал ждёт возврата.'
                    : 'Подпись создана. Осталось вернуться на портал.'}
            </p>
        </div>
        <Tap id="continue" target={target} tap={tap} className="wt-eg-primary">Продолжить</Tap>
        {/* Ловушка — не кнопка, а полоска «домой» внизу экрана: свернуть
            приложение жестом первое, что делает человек, и никакой надписи
            «Свернуть приложение» в eGov для этого нет. */}
        <Tap id="minimize" target={target} tap={tap} className="wt-eg__home"
            aria-label="Свернуть приложение" />
    </div>
);
