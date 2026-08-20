import React, { useState } from 'react';

import { Tap, Row } from './screenKit';
import { TRAINEE } from './scenarioTaxiPro';

/* Экраны eGov Mobile — общие для обоих тренажёров.
 *
 * Так и в жизни: и приложение Такси.Про, и сайт Сапар приводят в одно и то же
 * приложение подписи. Два набора экранов расползлись бы по деталям, и человек
 * учился бы двум разным eGov вместо одного.
 *
 * purpose ('auth' | 'docs') меняет ТОЛЬКО подписи: чей это документ и что
 * подписывается. Логика шага живёт в сценарии — экран о ней не знает.
 */

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];

/** Код быстрого доступа. Своё состояние — вводимые цифры не касаются мира:
 *  промах по цифре не должен становиться частью учебной попытки. */
export const EgCode = ({ tap, target, purpose }) => {
    const [code, setCode] = useState('');
    const press = (key) => {
        if (key === 'del') { setCode((v) => v.slice(0, -1)); return; }
        setCode((v) => (v + key).slice(0, 4));
    };
    return (
        <div className="wt-screen wt-eg">
            <div className="wt-eg__head">
                <Tap id="egov_close" target={target} tap={tap} className="wt-eg__close"
                    aria-label="Закрыть eGov Mobile">×</Tap>
                <b>eGov Mobile</b>
            </div>
            <h2 className="wt-eg__title">Код быстрого доступа</h2>
            <p className="wt-eg__lead">
                {purpose === 'auth'
                    ? 'Подтвердите вход на портал.'
                    : 'Подтвердите подписание документов.'}
            </p>
            <div className="wt-eg__dots" aria-label={`Введено цифр: ${code.length} из 4`}>
                {[0, 1, 2, 3].map((i) => (
                    <i key={i} className={i < code.length ? 'is-filled' : ''} />
                ))}
            </div>
            <div className="wt-eg__pad">
                {KEYS.map((key, index) => (key === '' ? <span key={`gap-${index}`} /> : (
                    <button
                        key={key}
                        type="button"
                        onClick={() => press(key)}
                        aria-label={key === 'del' ? 'Удалить цифру' : `Цифра ${key}`}
                    >
                        {key === 'del' ? '⌫' : key}
                    </button>
                )))}
            </div>
            <Tap
                id="submit_code"
                /* Подсветку даём только собранному коду: обводка вокруг
                   недоступной кнопки показывает цель, нажать которую нельзя, —
                   человек тыкает в неё и решает, что тренажёр сломан. */
                target={code.length === 4 ? target : null}
                tap={tap}
                payload={{ code }}
                className="wt-primary"
                disabled={code.length !== 4}
            >
                Далее
            </Tap>
        </div>
    );
};

export const EgSign = ({ world, tap, toggle, target, purpose, period }) => {
    const open = !!world.egovExpanded;
    return (
        <div className="wt-screen wt-eg">
            <div className="wt-eg__head">
                <Tap id="egov_close" target={target} tap={tap} className="wt-eg__close"
                    aria-label="Закрыть eGov Mobile">×</Tap>
                <b>Подписание</b>
            </div>
            <h2 className="wt-eg__title">
                {purpose === 'auth' ? 'Подписать вход на портал' : 'Подписать документы'}
            </h2>
            {/* Предупреждение — не украшение экрана: именно его не читают, и
                именно поэтому следующий шаг («вернуться на портал») теряется. */}
            <div className="wt-eg__warn">
                После подписания необходимо самостоятельно вернуться на страницу портала
                и нажать «Продолжить».
            </div>
            <div className="wt-eg__fields">
                <Row label="Срок действия QR-кода">{world.qrValid || 'сегодня, 15:25'}</Row>
                <Row label="От кого">{TRAINEE.requester}</Row>
                <Row label="Подписант">{TRAINEE.short}</Row>
            </div>
            <button
                type="button"
                className={`wt-eg__doc${open ? ' is-open' : ''}`}
                onClick={() => toggle('egovExpanded')}
                aria-expanded={open}
            >
                {purpose === 'auth' ? 'Запрос на авторизацию' : 'Документы на подписание (2)'}
                <span aria-hidden="true">{open ? '⌃' : '⌄'}</span>
            </button>
            {open && (
                <div className="wt-eg__docbody">
                    {purpose === 'auth' ? (
                        <div><span>Разрешение</span>Вход в личный кабинет провайдера</div>
                    ) : (
                        <>
                            <div><span>АВР от Яндекса</span>{period} · учебный документ</div>
                            <div><span>АВР от таксопарка</span>{period} · учебный документ</div>
                        </>
                    )}
                </div>
            )}
            <div className="wt-eg__actions">
                <Tap id="approve" target={target} tap={tap} className="wt-primary">Подписать</Tap>
                <Tap id="decline" target={target} tap={tap} className="wt-danger">Отказать</Tap>
            </div>
        </div>
    );
};

export const EgSuccess = ({ tap, target, purpose }) => (
    <div className="wt-screen wt-eg wt-eg--done">
        <div className="wt-eg__mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor"
                strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="m4 12.5 5 5L20 6.5" />
            </svg>
        </div>
        <b>Успешно подписано</b>
        <p>
            {purpose === 'auth'
                ? 'Вход подтверждён. Портал ждёт возврата.'
                : 'Подпись создана. Осталось вернуться на портал.'}
        </p>
        {/* Ловушка стоит РЯДОМ с правильной кнопкой и выглядит так же безобидно,
            как в жизни: свернуть приложение — первое, что делает человек. */}
        <Tap id="minimize" target={target} tap={tap} className="wt-ghost">Свернуть приложение</Tap>
        <Tap id="continue" target={target} tap={tap} className="wt-primary">Продолжить</Tap>
    </div>
);
