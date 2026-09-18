import React, { useCallback, useEffect, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard, iosGroupLabel, iosBtnPrimary, iosBtnGhost, IosBadge } from '../ui/ios';

/*
 * Раздел «Тест iCORE/Oktell» — пилот единой программы.
 *
 * Сегодня оператор СЗоВ держит отдельно веб-клиент Oktell в браузере и отдельно
 * портал iCORE с объявлениями. Здесь и то и другое — одна программа: она
 * открывает АТС, входит в неё по учётке iCORE (пароль кабинета оператор не
 * вводит и не знает) и показывает обязательные объявления поверх рабочего окна.
 *
 * Раздел намеренно один и маленький: пока это пилот, и отдельной кнопки
 * скачивания в общем меню нет — программа берётся отсюда.
 */

const formatSize = (bytes) => {
    const value = Number(bytes || 0);
    if (!value) return '';
    return value >= 1024 * 1024
        ? `${(value / 1024 / 1024).toFixed(1)} МБ`
        : `${Math.max(1, Math.round(value / 1024))} КБ`;
};

const formatDate = (iso) => {
    if (!iso) return '';
    // Метки в базе наивные и записаны алматинским временем — показываем как есть.
    const [date, time] = String(iso).split('T');
    const [year, month, day] = (date || '').split('-');
    if (!year) return '';
    return `${day}.${month}.${year}${time ? ` ${time.slice(0, 5)}` : ''}`;
};

const OktellClientTestView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const [release, setRelease] = useState(null);
    const [loading, setLoading] = useState(true);
    const [downloading, setDownloading] = useState(false);

    // Манифест публичный и без токена — тот же, по которому обновляется сам
    // клиент. Берём его, а не историю релизов: история открыта админам, а
    // раздел смотрят и операторы.
    const fetchRelease = useCallback(async () => {
        setLoading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/oktell_client/version`);
            const data = await resp.json().catch(() => ({}));
            setRelease(data?.release || null);
        } catch (error) {
            setRelease(null);
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl]);

    useEffect(() => { fetchRelease(); }, [fetchRelease]);

    // Ссылку берём свежей по нажатию: подпись живёт час, и держать её в
    // разметке нельзя.
    const download = async () => {
        setDownloading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/oktell_client/download`, {
                credentials: 'include',
                headers: withAccessTokenHeader(),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            if (!data?.url) throw new Error('Сервер не вернул ссылку');
            window.open(data.url, '_blank', 'noopener');
        } catch (error) {
            showToast?.(`Не удалось скачать: ${error.message}`, 'error');
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div className="p-4 sm:p-6" style={{ fontFamily: APPLE_FONT }}>
            <div className="mx-auto max-w-3xl space-y-4">
                <div>
                    <h1 className="text-[22px] font-semibold tracking-[-0.3px] text-slate-900">
                        Тест iCORE/Oktell
                    </h1>
                    <p className="mt-1 text-[13px] leading-relaxed text-slate-500">
                        Oktell и вход в него — одна программа. Оператор вводит только свою
                        учётную запись iCORE: логин и пароль АТС подставляются за него, а
                        обязательные объявления показываются поверх рабочего окна.
                    </p>
                </div>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Программа</div>
                    <div className={`${iosCard} p-4`}>
                        {loading ? (
                            <p className="text-[13px] text-slate-400">
                                <FaIcon className="fas fa-spinner fa-spin mr-2" />Загрузка…
                            </p>
                        ) : release ? (
                            <>
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="font-mono text-[15px] font-medium text-slate-900">
                                        {release.version}
                                    </span>
                                    {release.mandatory && <IosBadge tone="amber">Обязательное обновление</IosBadge>}
                                    {formatSize(release.size) && (
                                        <span className="text-[12px] text-slate-400">{formatSize(release.size)}</span>
                                    )}
                                    {formatDate(release.published_at) && (
                                        <span className="text-[12px] text-slate-400">
                                            от {formatDate(release.published_at)}
                                        </span>
                                    )}
                                </div>
                                {release.notes && (
                                    <p className="mt-2 text-[12.5px] leading-relaxed text-slate-600">{release.notes}</p>
                                )}
                                <div className="mt-3 flex items-center gap-2">
                                    <button type="button" onClick={download} disabled={downloading}
                                            className={iosBtnPrimary}>
                                        <FaIcon className={downloading ? 'fas fa-spinner fa-spin' : 'fas fa-download'} />
                                        Скачать
                                    </button>
                                    <button type="button" onClick={fetchRelease} className={iosBtnGhost} title="Обновить">
                                        <FaIcon className="fas fa-sync-alt" />
                                    </button>
                                </div>
                                <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
                                    Дальше программа обновляется сама и только в паузе — не во время
                                    разговора и не пока открыто объявление.
                                </p>
                            </>
                        ) : (
                            <p className="text-[13px] text-slate-500">
                                Сборка ещё не опубликована. Как только её выложат, она появится здесь.
                            </p>
                        )}
                    </div>
                </section>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Как пользоваться</div>
                    <div className={`${iosCard} space-y-2 p-4 text-[13px] leading-relaxed text-slate-700`}>
                        <p>1. Скачайте и запустите файл — программа поставится сама и создаст ярлык.</p>
                        <p>2. Введите логин и пароль от iCORE. Пароль от Oktell вводить не нужно.</p>
                        <p>3. Откроется привычный клиент АТС — работайте как обычно.</p>
                        <p>
                            4. Когда выйдет обязательное объявление, программа дождётся конца разговора,
                            переведёт вас в «Тренинг» и покажет его. После подтверждения статус вернётся сам.
                        </p>
                    </div>
                </section>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Что проверяем на пилоте</div>
                    <div className={`${iosCard} space-y-2 p-4 text-[12.5px] leading-relaxed text-slate-600`}>
                        <p>· Подставляется ли учётная запись в форму входа АТС.</p>
                        <p>· Верно ли определяется разговор — объявление не должно всплывать посреди консультации.</p>
                        <p>· Уходит ли статус «Тренинг» и возвращается ли он после подтверждения.</p>
                        <p>· Не мешает ли окно объявления работе с другими программами.</p>
                        <p className="text-slate-400">
                            Что заметили — напишите в задачу по пилоту, это и есть смысл раздела.
                        </p>
                    </div>
                </section>
            </div>
        </div>
    );
};

export default OktellClientTestView;
