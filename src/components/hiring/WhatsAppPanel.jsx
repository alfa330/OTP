import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { FileText, Image as ImageIcon, MapPin, Mic, Music, Smile, UserRound, Video } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosHint } from '../ui/ios';
import { errorOf, plural, prettyPhone, shortTime } from './hiringMeta';
import { ConfirmDialog, Empty, Loading, Pill, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Номер WhatsApp найма (владелец 06.11.2026). Сервис на шлюзе подключён к номеру связанным устройством,
 * как WhatsApp Web, и приносит каждое сообщение; первое входящее чата становится откликом в пуле.
 *
 *   WhatsAppIntegration  строка «Настроек → Интеграции»: состояние, QR привязки, кнопки, предупреждения;
 *   WhatsAppThread       вкладка карточки: переписка с номером найма, старые сообщения сверху.
 *
 * Сервис сам ничего не отправляет (этап 1): рекрутеры отвечают с телефона, ответы приходят эхом; первый
 * ответ берёт карточку в работу к тому, кто выбран «Отвечает с телефона».
 */

const HOW_IT_WORKS = 'Сервис на шлюзе подключён к номеру найма как связанное устройство (как WhatsApp Web). '
    + 'Каждый новый собеседник становится откликом в пуле с источником «WhatsApp», переписка — во вкладке карточки. '
    + 'Сервис сам ничего не отправляет — отвечайте с телефона. Чтобы номер не заблокировали: не писать с него первыми, '
    + 'без рассылок и одинаковых текстов, телефон держать в сети и открывать на нём WhatsApp Business каждый день.';

const PHONE_RECRUITER_HINT = 'Кто отвечает кандидатам с телефона найма. Его первый ответ в чате берёт карточку из пула '
    + 'в работу к нему, дальше каждый день переписки — касание WhatsApp в карточке. Ответ в первые 15 секунд после '
    + 'сообщения кандидата — автоответ WhatsApp Business, он не считается. При выборе уже отвеченные карточки тоже '
    + 'уходят в работу — со временем первого ответа.';

const PAIR_STEPS = 'На телефоне с номером найма: WhatsApp Business → Настройки → Связанные устройства → '
    + 'Привязка устройства — и наведите камеру на код.';

const ACTION_LABELS = { pair: 'Подключить', resume: 'Возобновить', logout: 'Отвязать' };

export const WhatsAppIntegration = ({ api, toast }) => {
    const [status, setStatus] = useState(null);
    const [busy, setBusy] = useState('');
    const [confirmLogout, setConfirmLogout] = useState(false);
    const [qrImage, setQrImage] = useState('');

    const load = useCallback(() => api.get('/whatsapp')
        .then(({ data }) => setStatus(data))
        .catch(() => {}), [api]);

    // Пока идёт привязка или ждёт команда — чаще (код QR живёт 20–60 секунд), иначе раз в 15 секунд.
    const fast = Boolean(status && (status.pending || status.state === 'pairing' || status.state === 'connecting'));
    useEffect(() => {
        let alive = true;
        let timer = null;
        const tick = () => {
            load().finally(() => { if (alive) timer = setTimeout(tick, fast ? 2000 : 15000); });
        };
        tick();
        return () => { alive = false; clearTimeout(timer); };
    }, [load, fast]);

    useEffect(() => {
        if (!status?.qr) { setQrImage(''); return undefined; }
        let cancelled = false;
        import('qrcode')
            .then((mod) => (mod.default || mod).toDataURL(status.qr, { errorCorrectionLevel: 'L', margin: 1, width: 440 }))
            .then((url) => { if (!cancelled) setQrImage(url); })
            .catch(() => { if (!cancelled) setQrImage(''); });
        return () => { cancelled = true; };
    }, [status?.qr]);

    const run = async (action) => {
        setBusy(action);
        try {
            const { data } = await api.post('/whatsapp/command', { action });
            setStatus(data);
            setConfirmLogout(false);
        } catch (error) {
            toast(errorOf(error, 'Команда не отправлена'), 'error');
        } finally {
            setBusy('');
        }
    };

    const savePhoneRecruiter = async (value) => {
        setBusy('recruiter');
        try {
            const { data } = await api.put('/whatsapp/settings', { phone_recruiter_id: value ? Number(value) : null });
            setStatus(data);
            toast(data.applied
                ? `Сохранено · в работу ${plural(data.applied, 'ушла', 'ушли', 'ушло')} ${data.applied} ${plural(data.applied, 'карточка', 'карточки', 'карточек')}`
                : 'Сохранено', 'success');
        } catch (error) {
            toast(errorOf(error, 'Не сохранилось'), 'error');
        } finally {
            setBusy('');
        }
    };

    if (!status) {
        return (
            <li className="flex items-center gap-3 px-4 py-2.5 text-[13.5px] font-medium text-slate-900">
                WhatsApp найма <span className="text-[12px] font-normal text-slate-400">загрузка…</span>
            </li>
        );
    }
    const { actions = {}, unmatched = {}, recruiter_options: recruiterOptions = [] } = status;
    const facts = [
        status.number ? prettyPhone(`+${status.number}`) : null,
        status.state === 'connected' && status.connected_since ? `подключён с ${shortTime(status.connected_since)}` : null,
        status.last_seen_at && status.state !== 'connected' ? `на связи ${shortTime(status.last_seen_at)}` : null,
        `откликов из WhatsApp ${status.received_total}`,
    ].filter(Boolean);
    const lost = [
        unmatched.foreign ? `${unmatched.foreign} ${plural(unmatched.foreign, 'чат', 'чата', 'чатов')} с иностранным номером` : null,
        unmatched.hidden ? `${unmatched.hidden} ${plural(unmatched.hidden, 'чат', 'чата', 'чатов')} со скрытым номером` : null,
    ].filter(Boolean);

    return (
        <li className="px-4 py-2.5">
            <div className="flex items-center gap-3">
                <div className="min-w-0 grow basis-0">
                    <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium text-slate-900">
                        WhatsApp найма
                        <Pill tone={status.tone}>{status.label}</Pill>
                        <IosHint text={HOW_IT_WORKS} label="Как работает" />
                    </div>
                    <div className="mt-0.5 text-[12px] tabular-nums text-slate-500">{facts.join(' · ')}</div>
                    {status.reason && <div className="mt-0.5 text-[12px] text-rose-600">{status.reason}</div>}
                    {status.warnings.map((text) => <div key={text} className="mt-0.5 text-[12px] text-amber-700">{text}</div>)}
                    {lost.length > 0 && (
                        <div className="mt-0.5 flex items-center gap-1 text-[12px] text-slate-500">
                            Без карточки: {lost.join(', ')}
                            <IosHint text="Карточку заводим только по казахстанскому номеру. Такие чаты остаются на телефоне — их видно там, в портал они не попадают." label="Почему без карточки" />
                        </div>
                    )}
                    {status.pending && <div className="mt-0.5 text-[12px] text-slate-500">Команда «{ACTION_LABELS[status.pending]}» отправлена сервису…</div>}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="text-[12.5px] text-slate-600">Отвечает с телефона</span>
                        <div className="w-64">
                            <CustomSelect variant="ios" value={status.phone_recruiter_id ? String(status.phone_recruiter_id) : ''}
                                          onChange={savePhoneRecruiter} disabled={busy === 'recruiter'} placeholder="Не выбран"
                                          searchable searchPlaceholder="Фамилия" ariaLabel="Кто отвечает с телефона найма"
                                          options={[{ value: '', label: 'Не выбран' },
                                              ...recruiterOptions.map((person) => ({ value: String(person.id), label: person.name }))]} />
                        </div>
                        <IosHint text={PHONE_RECRUITER_HINT} label="Что это" />
                    </div>
                </div>
                <div className="flex shrink-0 flex-wrap justify-end gap-2">
                    {actions.pair && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={Boolean(busy)} onClick={() => run('pair')}>Подключить</button>
                    )}
                    {actions.resume && (
                        <button type="button" className={TOOLBAR_BTN} disabled={Boolean(busy)} onClick={() => run('resume')}>Возобновить</button>
                    )}
                    {actions.logout && (
                        <button type="button" className={TOOLBAR_BTN} disabled={Boolean(busy)} onClick={() => setConfirmLogout(true)}>Отвязать</button>
                    )}
                </div>
            </div>
            {status.state === 'pairing' && (
                <div className="mt-3 flex flex-wrap items-center gap-4 rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200/70">
                    <div className="grid h-[220px] w-[220px] shrink-0 place-items-center rounded-xl bg-white ring-1 ring-slate-200/70">
                        {qrImage
                            ? <img src={qrImage} alt="QR-код привязки WhatsApp" className="h-[208px] w-[208px] [image-rendering:pixelated]" />
                            : <span className="px-4 text-center text-[12px] text-slate-400">Код появится через несколько секунд</span>}
                    </div>
                    <div className="min-w-[200px] grow basis-0 text-[13px] leading-snug text-slate-700">
                        <div className="font-semibold text-slate-900">Отсканируйте код телефоном с номером найма</div>
                        <div className="mt-1 text-slate-600">{PAIR_STEPS}</div>
                        <div className="mt-1 text-[12px] text-slate-500">Код обновляется сам. Привязываем один раз — устройство «iCore Найм» потом не удаляйте.</div>
                    </div>
                </div>
            )}
            <ConfirmDialog open={confirmLogout} onClose={() => setConfirmLogout(false)} danger busy={busy === 'logout'}
                           title="Отвязать номер?"
                           text="Новые сообщения перестанут попадать в воронку, пока номер не привяжут заново по QR. Частые привязки опасны для номера — отвязывайте, только если номер меняется."
                           confirmLabel="Отвязать" onConfirm={() => run('logout')} />
        </li>
    );
};

const KIND_ICONS = { image: ImageIcon, video: Video, audio: Music, voice: Mic, document: FileText, sticker: Smile,
    location: MapPin, contact: UserRound };
const KIND_LABELS = { image: 'Фото', video: 'Видео', audio: 'Аудио', voice: 'Голосовое', document: 'Документ',
    sticker: 'Стикер', location: 'Геопозиция', contact: 'Контакт', other: 'Сообщение' };

const duration = (seconds) => (seconds ? ` ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : '');

const dayOf = (iso) => String(iso || '').slice(0, 10);

const MediaChip = ({ message }) => {
    const Icon = KIND_ICONS[message.kind] || FileText;
    const media = message.media || {};
    const name = message.kind === 'document' && media.file_name ? ` · ${media.file_name}` : '';
    return (
        <span className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-slate-900/[0.04] px-2 py-1 text-[12.5px] text-slate-600">
            <Icon size={13} className="shrink-0" />
            <span className="truncate">{KIND_LABELS[message.kind] || 'Файл'}{duration(media.seconds)}{name}</span>
        </span>
    );
};

export const WhatsAppThread = ({ api, cardId }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const listRef = useRef(null);

    useEffect(() => {
        let alive = true;
        let timer = null;
        const tick = () => {
            api.get(`/cards/${cardId}/whatsapp`)
                .then(({ data: payload }) => { if (alive) { setData(payload); setError(''); } })
                .catch((requestError) => { if (alive) setError(errorOf(requestError, 'Переписка не загрузилась')); })
                .finally(() => { if (alive) timer = setTimeout(tick, 15000); });
        };
        tick();
        return () => { alive = false; clearTimeout(timer); };
    }, [api, cardId]);

    // Лента — с последних сообщений, как в телефоне. После отрисовки React, а не в rAF.
    const count = data?.messages?.length || 0;
    useLayoutEffect(() => {
        const node = listRef.current;
        if (node) node.scrollTop = node.scrollHeight;
    }, [count]);

    if (error) return <Empty>{error}</Empty>;
    if (!data) return <Loading text="Загружаем переписку…" />;
    if (!count) return <Empty>Переписки с номером найма нет</Empty>;
    let lastDay = '';
    return (
        <div className="space-y-2">
            <div ref={listRef} className="max-h-[60vh] space-y-1.5 overflow-y-auto rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200/70">
                {data.messages.map((message) => {
                    const day = dayOf(message.sent_at);
                    const separator = day !== lastDay ? day : '';
                    lastDay = day;
                    const mine = message.from_me;
                    return (
                        <React.Fragment key={message.id}>
                            {separator && (
                                <div className="py-1 text-center text-[11.5px] font-medium tabular-nums text-slate-400">{shortTime(separator)}</div>
                            )}
                            <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                                <div className={`max-w-[80%] rounded-2xl px-3 py-1.5 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.04)] ring-1 ${
                                    mine ? 'rounded-br-md bg-blue-50 ring-blue-100' : 'rounded-bl-md bg-white ring-slate-200/70'}`}>
                                    {message.ad_label && !mine && (
                                        <div className="mb-1 text-[11.5px] font-medium text-blue-700">{message.ad_label}</div>
                                    )}
                                    {message.deleted_at ? (
                                        <span className="italic text-slate-400">Сообщение удалено</span>
                                    ) : (
                                        <>
                                            {message.kind !== 'text' && <div className={message.body ? 'mb-1' : ''}><MediaChip message={message} /></div>}
                                            {message.body && <div className="whitespace-pre-wrap break-words text-slate-900">{message.body}</div>}
                                        </>
                                    )}
                                    <div className="mt-0.5 text-right text-[11px] tabular-nums text-slate-400">
                                        {message.edited_at && !message.deleted_at ? 'изменено · ' : ''}{String(message.sent_at || '').slice(11, 16)}
                                    </div>
                                </div>
                            </div>
                        </React.Fragment>
                    );
                })}
            </div>
            <div className="text-[12px] text-slate-400">Отвечайте с телефона — ответы появятся здесь. Файлы и голосовые — на телефоне.</div>
        </div>
    );
};
