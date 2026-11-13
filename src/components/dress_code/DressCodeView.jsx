import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Camera, CheckCircle2, Loader2, Shirt } from 'lucide-react';
import {
    APPLE_FONT, IosBadge, IosHint, IosLightbox, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import DressCodeCamera from './DressCodeCamera';
import { employeeStatus, fmtDay, fmtTime } from './dressCode';

/*
 * «Дресс-код» сотрудника (ТЗ #418): фото после отметки прихода в Workpace.
 *
 * Сегодня — одно фото; повторное — только когда руководитель попросил переснять
 * или подтвердил нарушение (исправил — сфотографировался ещё раз). До решения
 * руководителя спорный вывод ИИ сотруднику не показывается (ТЗ п. 2): «фото
 * принято, его проверит руководитель». Подтверждённое нарушение — с причиной,
 * комментарием и кнопкой «Ознакомлен».
 */

const errText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const StatusCard = ({ item, onOpenPhoto, onAck, acking }) => {
    const status = employeeStatus(item.status);
    return (
        <div className="flex gap-3 px-4 py-3">
            {item.photo_url ? (
                <button type="button" onClick={() => onOpenPhoto(item.photo_url)}
                        className="shrink-0 overflow-hidden rounded-xl ring-1 ring-slate-200/70 transition active:scale-[0.97]"
                        aria-label="Открыть фото">
                    <img src={item.photo_url} alt="" className="h-16 w-12 object-cover" />
                </button>
            ) : (
                <span className="grid h-16 w-12 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-400">
                    <Shirt size={18} />
                </span>
            )}
            <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] tabular-nums text-slate-500">
                        {fmtDay(item.day)}{fmtTime(item.taken_at) ? `, ${fmtTime(item.taken_at)}` : ''}
                        {item.retake_of ? ' · повторное фото' : ''}
                    </span>
                    <IosBadge tone={status.tone}>{status.label}</IosBadge>
                    {item.is_repeat && <IosBadge tone="red">Повторное нарушение</IosBadge>}
                </div>
                {item.reasons?.length > 0 && (
                    <div className="text-[13.5px] text-slate-800">Причина: {item.reasons.join('; ')}</div>
                )}
                {item.comment && (
                    <div className="text-[13px] text-slate-600">Комментарий руководителя: {item.comment}</div>
                )}
                {item.status === 'confirmed' && (
                    item.acknowledged_at ? (
                        <div className="inline-flex items-center gap-1 text-[12.5px] text-slate-500">
                            <CheckCircle2 size={13} /> Ознакомлен
                        </div>
                    ) : (
                        <button type="button" onClick={() => onAck(item.id)} disabled={acking}
                                className={`${iosBtnSecondary} !px-3 !py-1.5 !text-[12.5px]`}>
                            {acking && <Loader2 size={13} className="animate-spin" />} Ознакомлен
                        </button>
                    )
                )}
            </div>
        </div>
    );
};

export default function DressCodeView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const isMobileShell = useIsMobileShell();
    const base = `${apiBaseUrl}/api/dress_code`;
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);
    const [state, setState] = useState(null);
    const [error, setError] = useState('');
    const [camera, setCamera] = useState(null);       // { attempt, hint, retakeOf }
    const [sending, setSending] = useState(false);
    const [acking, setAcking] = useState(false);
    const [photo, setPhoto] = useState(null);

    const load = useCallback(() => {
        setError('');
        axios.get(`${base}/me`, { headers: headers() })
            .then((r) => setState(r.data))
            .catch((e) => { setState(null); setError(errText(e, 'Не удалось загрузить раздел')); });
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    const openCamera = () => setCamera({ attempt: 1, hint: '', retakeOf: state?.retake_for || null });

    const upload = async (blob, source) => {
        if (!camera) return;
        setSending(true);
        try {
            const form = new FormData();
            form.append('photo', blob, 'dress_code.jpg');
            form.append('attempt', String(camera.attempt));
            form.append('source', source);
            if (camera.retakeOf) form.append('retake_of', String(camera.retakeOf));
            const { data } = await axios.post(`${base}/photo`, form, { headers: headers() });
            if (data.status === 'retake') {
                // Плохой кадр: ИИ назвал, что исправить; сохранено ничего не было.
                setCamera((prev) => ({ ...prev, attempt: (data.attempt || prev.attempt) + 1, hint: data.hint || 'Переснимите фото' }));
                return;
            }
            setCamera(null);
            setState(data);
            showToast?.('Фото принято', 'success');
        } catch (e) {
            showToast?.(errText(e, 'Не удалось отправить фото'), 'error');
        } finally {
            setSending(false);
        }
    };

    const acknowledge = async (checkId) => {
        setAcking(true);
        try {
            const { data } = await axios.post(`${base}/checks/${checkId}/ack`, {}, { headers: headers() });
            setState(data);
        } catch (e) {
            showToast?.(errText(e, 'Не удалось отметить'), 'error');
        } finally {
            setAcking(false);
        }
    };

    const today = state?.today || [];
    const history = state?.history || [];
    const retake = Boolean(state?.retake_for);

    return (
        <div className={`mx-auto w-full max-w-2xl ${isMobileShell ? 'px-3.5' : ''}`} style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-4 flex items-center gap-3 px-1">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-blue-600 text-white shadow-sm">
                    <Shirt size={19} />
                </span>
                <div className="flex min-w-0 items-center gap-2">
                    <h2 className="truncate text-[19px] font-bold leading-tight tracking-tight text-slate-900">Дресс-код</h2>
                    <IosHint
                        label="О разделе"
                        text="Каждое утро после отметки прихода в Workpace сделайте здесь фото: голова и одежда до пояса — внутри рамки. ИИ проверит кадр сразу, а спорные фото посмотрит руководитель."
                    />
                </div>
            </div>

            {error && (
                <div className={`${iosCard} px-4 py-3.5 text-[13.5px] text-rose-600`}>{error}</div>
            )}
            {!state && !error && (
                <div className="flex justify-center py-16 text-slate-400"><Loader2 size={22} className="animate-spin" /></div>
            )}

            {state && (
                <div className="space-y-5">
                    {!state.required && (
                        <div className={`${iosCard} px-4 py-3.5 text-[13.5px] text-slate-600`}>
                            Фото для дресс-кода от вас сейчас не требуется.
                        </div>
                    )}

                    {state.required && state.can_capture && (
                        <div className={`${iosCard} flex flex-col items-center gap-3 px-5 py-6 text-center`}>
                            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                                <Camera size={22} />
                            </span>
                            <div className="space-y-1">
                                <div className="text-[15.5px] font-semibold text-slate-900">
                                    {retake ? 'Нужно повторное фото' : 'Фото на сегодня'}
                                </div>
                                <div className="text-[13px] text-slate-500">
                                    {retake ? 'Сделайте фото ещё раз — так, как просил руководитель.'
                                        : 'Сфотографируйтесь после отметки прихода: голова и одежда до пояса — внутри рамки.'}
                                </div>
                            </div>
                            <button type="button" onClick={openCamera} className={iosBtnPrimary}>
                                <Camera size={16} /> {retake ? 'Переснять' : 'Сделать фото'}
                            </button>
                        </div>
                    )}

                    {today.length > 0 && (
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Сегодня</div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {today.map((item) => (
                                    <StatusCard key={item.id} item={item} onOpenPhoto={setPhoto}
                                                onAck={acknowledge} acking={acking} />
                                ))}
                            </div>
                        </div>
                    )}

                    {history.length > 0 && (
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Прошлые дни</div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {history.map((item) => (
                                    <StatusCard key={item.id} item={item} onOpenPhoto={setPhoto}
                                                onAck={acknowledge} acking={acking} />
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            <DressCodeCamera
                open={Boolean(camera)}
                onClose={() => { if (!sending) setCamera(null); }}
                onCapture={upload}
                busy={sending}
                hint={camera?.hint || ''}
            />
            <IosLightbox url={photo} alt="Фото для дресс-кода" onClose={() => setPhoto(null)} />
        </div>
    );
}
