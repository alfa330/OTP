import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Languages, Loader2, PenLine, Sparkles } from 'lucide-react';
import FaIcon from '../common/FaIcon';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosHint, IosMenu, IosModal, IosSegmented,
} from '../ui/ios';
import { prepareInformerTrack } from './informerAudio';
import {
    RESULT_LABEL, STATE_LABEL, STATUS, VOICE_SPEEDS, campaignActions, partsLabel, percent, seconds,
    shortDateTime, speechEstimate, stageLabel, stageShares,
} from './informerView';

/*
 * «Автоинформатор» во вкладках «Удаленного КЦ» (владелец, 15.10.2026): робот на шлюзе звонит водителям
 * с номера удалённого КЦ и проигрывает запись — сначала казахская часть, потом русская.
 *
 * Запись — двумя путями: написать текст и озвучить голосом Soniox (ИИ пишет текст по описанию, правит
 * и переводит) или загрузить готовые записи. Дальше одинаково: браузер склеивает части в телефонный
 * WAV (informerAudio.js), сервер проверяет формат, робот играет.
 *
 * Аналитика рассылки — из итогов самого робота: дозвонились ли, сколько секунд слушал, на какой
 * части положил трубку. Номер водителя — маской, как в журнале раздела.
 */

const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 30000;
const SOURCE_OPTIONS = [
    { value: 'tts', label: 'Озвучить текст', icon: <FaIcon className="fas fa-wand-magic-sparkles" /> },
    { value: 'upload', label: 'Загрузить записи', icon: <FaIcon className="fas fa-file-arrow-up" /> },
];
const ATTEMPT_OPTIONS = [1, 2, 3].map((n) => ({ value: String(n), label: String(n) }));
const RETRY_OPTIONS = [
    { value: '30', label: '30 мин' },
    { value: '60', label: '1 час' },
    { value: '120', label: '2 часа' },
];
const PART_META = {
    kz: { title: 'Казахская часть', order: 'звучит первой', from: 'ru', translate: 'Перевести с русского',
          placeholder: 'Сәлеметсіз бе! …' },
    ru: { title: 'Русская часть', order: 'звучит второй', from: 'kz', translate: 'Перевести с казахского',
          placeholder: 'Здравствуйте! …' },
};

/* Ответ сервера → текст для человека, без «Internal server error». */
const errorText = async (resp, fallback) => {
    const data = await resp.json().catch(() => ({}));
    return (resp.status < 500 && data?.error) ? data.error : (data?.error || fallback);
};

/* Кнопка «прослушать»: берёт ссылку на звук (getUrl) и играет её; второе нажатие — стоп. */
const PlayButton = ({ getUrl, label = 'Прослушать', disabled = false, className = iosBtnSecondary, onError }) => {
    const [state, setState] = useState('idle'); // idle | loading | playing
    const audioRef = useRef(null);
    useEffect(() => () => { audioRef.current?.pause(); }, []);
    const toggle = async () => {
        if (state === 'playing') {
            audioRef.current?.pause();
            setState('idle');
            return;
        }
        setState('loading');
        try {
            const url = await getUrl();
            if (!url) { setState('idle'); return; }
            audioRef.current?.pause();
            const audio = new Audio(url);
            audioRef.current = audio;
            audio.onended = () => setState('idle');
            await audio.play();
            setState('playing');
        } catch (err) {
            setState('idle');
            onError?.(err?.message || 'Не удалось воспроизвести');
        }
    };
    const icon = state === 'loading' ? 'fa-spinner fa-spin' : state === 'playing' ? 'fa-stop' : 'fa-play';
    return (
        <button type="button" className={`${className} py-2`} disabled={disabled || state === 'loading'} onClick={toggle}>
            <FaIcon className={`fas ${icon}`} />
            {state === 'playing' ? 'Стоп' : label}
        </button>
    );
};

/* ── робот ────────────────────────────────────────────────────────────────── */
const RobotCard = ({ robot }) => {
    const online = !!robot?.online;
    const lines = Object.entries(robot?.lines || {});
    return (
        <div className={`${iosCard} flex items-center gap-3 px-4 py-3`}>
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${online ? 'bg-emerald-500' : 'bg-slate-300'}`} />
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-[13.5px] font-semibold text-slate-800">
                    {online ? 'Робот на связи' : 'Робот не на связи'}
                    <IosHint text={'Робот — телефон на шлюзе в офисе: звонит с номера удалённого КЦ и проигрывает '
                        + 'запись. Рассылка звонит, только пока он на связи; без него запущенная рассылка просто ждёт.'} />
                </div>
                <div className="truncate text-[12px] text-slate-500">
                    {online && lines.length
                        ? lines.map(([line, info]) => `линия ${line}: ${info.active} из ${info.capacity}`).join(' · ')
                        : online ? 'Линий нет' : 'Звонки начнутся, когда он подключится'}
                </div>
            </div>
        </div>
    );
};

/* ── список рассылок ──────────────────────────────────────────────────────── */
const CampaignRow = ({ campaign, onOpen }) => {
    const status = STATUS[campaign.status] || STATUS.draft;
    const processed = campaign.answered + campaign.given_up;
    const progress = campaign.targets_total ? Math.min(100, Math.round((processed / campaign.targets_total) * 100)) : 0;
    const rate = percent(campaign.answered, processed);
    return (
        <button type="button" onClick={() => onOpen(campaign.id)}
            className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50">
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                    <div className="truncate text-[13.5px] font-medium text-slate-800">{campaign.name}</div>
                    <IosBadge tone={status.tone}>{status.label}</IosBadge>
                </div>
                <div className="mt-1 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="w-28 shrink-0 text-right text-[11.5px] tabular-nums text-slate-500">
                        {processed} из {campaign.targets_total}
                    </div>
                </div>
                <div className="mt-1 text-[12px] text-slate-500">
                    Дозвонились {campaign.answered}{rate != null ? ` · ${rate}%` : ''}
                    {campaign.avg_listened_ms != null ? ` · слушали в среднем ${seconds(campaign.avg_listened_ms)}` : ''}
                </div>
            </div>
            <FaIcon className="fas fa-chevron-right text-slate-300" />
        </button>
    );
};

/* ── новая рассылка ───────────────────────────────────────────────────────── */
const emptyForm = () => ({
    name: '', source: 'tts', brief: '', texts: { kz: '', ru: '' }, files: { kz: null, ru: null },
    recipients: null, windowStart: '10:00', windowEnd: '20:00', perMinute: '10', attempts: '2', retry: '60',
});

const CreateCampaignModal = ({ open, onClose, onCreated, api, showToast }) => {
    const [form, setForm] = useState(emptyForm);
    const [voices, setVoices] = useState({ voices: [], default: '', default_speed: 'normal', text_max: 1500 });
    const [voice, setVoice] = useState('');
    const [speed, setSpeed] = useState('normal');
    const [busy, setBusy] = useState(''); // '' | 'generate' | 'polish-kz' | … | 'create'
    const [error, setError] = useState('');
    // Озвученные части: {kz: {key, url, blob}} — key = текст+голос+темп; поменяли — озвучим заново.
    const speechRef = useRef({});
    const trackRef = useRef(null); // {key, url, parts, durationMs}
    const [trackInfo, setTrackInfo] = useState(null);

    useEffect(() => {
        if (!open) return;
        setForm(emptyForm());
        setError('');
        setTrackInfo(null);
        speechRef.current = {};
        trackRef.current = null;
        api('/voices').then(async (resp) => {
            if (!resp.ok) return;
            const data = await resp.json();
            setVoices(data);
            setVoice(data.default || data.voices?.[0]?.id || '');
            setSpeed(data.default_speed || 'normal');
        }).catch(() => {});
    }, [open, api]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const setText = (lang, value) => setForm((f) => ({ ...f, texts: { ...f.texts, [lang]: value } }));
    const setFile = (lang, file) => setForm((f) => ({ ...f, files: { ...f.files, [lang]: file } }));

    const fail = (text) => { setError(text); };

    const callAi = async (payload, tag) => {
        setBusy(tag);
        setError('');
        try {
            const resp = await api('/ai', { method: 'POST', json: payload });
            if (!resp.ok) { fail(await errorText(resp, 'ИИ сейчас недоступен — попробуйте ещё раз')); return null; }
            return (await resp.json()).result;
        } catch {
            fail('Нет связи с сервером — попробуйте ещё раз');
            return null;
        } finally {
            setBusy('');
        }
    };

    const generate = async () => {
        const result = await callAi({ mode: 'generate', brief: form.brief }, 'generate');
        if (result) setForm((f) => ({ ...f, texts: { kz: result.kz, ru: result.ru } }));
    };
    const polish = async (lang) => {
        const result = await callAi({ mode: 'polish', lang, text: form.texts[lang] }, `polish-${lang}`);
        if (result) setText(lang, result.text);
    };
    const translate = async (lang) => {
        const from = PART_META[lang].from;
        const result = await callAi({ mode: 'translate', source: from, target: lang, text: form.texts[from] },
            `translate-${lang}`);
        if (result) setText(lang, result.text);
    };

    /* Озвучка части: та же, что потом сыграет робот. Кэш по тексту, голосу и темпу. */
    const speech = async (lang) => {
        const text = form.texts[lang].trim();
        if (!text) throw new Error(`Напишите ${lang === 'kz' ? 'казахский' : 'русский'} текст`);
        const key = `${voice}|${speed}|${text}`;
        const cached = speechRef.current[lang];
        if (cached?.key === key) return cached;
        const resp = await api('/tts', { method: 'POST', json: { lang, text, voice, speed } });
        if (!resp.ok) throw new Error(await errorText(resp, 'Soniox не озвучил текст — попробуйте ещё раз'));
        const blob = await resp.blob();
        if (cached?.url) URL.revokeObjectURL(cached.url);
        const entry = { key, blob, url: URL.createObjectURL(blob) };
        speechRef.current[lang] = entry;
        return entry;
    };

    /* Вся запись целиком (казахская, потом русская) — именно её услышит водитель. */
    const track = async () => {
        let sources;
        let key;
        if (form.source === 'tts') {
            // По очереди, не разом: Soniox отвечает «занято» на одновременные озвучки одного ключа.
            const kz = await speech('kz');
            const ru = await speech('ru');
            sources = { kz: kz.blob, ru: ru.blob };
            key = `tts|${kz.key}|${ru.key}`;
        } else {
            if (!form.files.kz || !form.files.ru) throw new Error('Добавьте обе записи: казахскую и русскую');
            sources = form.files;
            key = `upload|${form.files.kz.name}|${form.files.kz.size}|${form.files.ru.name}|${form.files.ru.size}`;
        }
        if (trackRef.current?.key === key) return trackRef.current;
        const prepared = await prepareInformerTrack(sources);
        if (trackRef.current?.url) URL.revokeObjectURL(trackRef.current.url);
        trackRef.current = { key, ...prepared, url: URL.createObjectURL(prepared.blob) };
        setTrackInfo({ durationMs: prepared.durationMs, parts: prepared.parts });
        return trackRef.current;
    };

    const ready = form.name.trim() && form.recipients
        && (form.source === 'tts' ? (form.texts.kz.trim() && form.texts.ru.trim()) : (form.files.kz && form.files.ru));

    const create = async () => {
        setBusy('create');
        setError('');
        try {
            const built = await track();
            const body = new FormData();
            body.append('name', form.name.trim());
            body.append('audio', built.blob, 'informer.wav');
            body.append('parts', JSON.stringify(built.parts));
            body.append('recipients', form.recipients, form.recipients.name);
            body.append('window_start', form.windowStart);
            body.append('window_end', form.windowEnd);
            body.append('max_per_minute', form.perMinute);
            body.append('max_attempts', form.attempts);
            body.append('retry_after_min', form.retry);
            body.append('script', JSON.stringify(form.source === 'tts'
                ? { source: 'tts', voice, speed, texts: form.texts } : { source: 'upload' }));
            const resp = await api('/campaigns', { method: 'POST', body });
            if (!resp.ok) { fail(await errorText(resp, 'Сервер не создал рассылку — попробуйте ещё раз')); return; }
            const data = await resp.json();
            const skipped = (data.recipients?.invalid || 0) + (data.recipients?.duplicates || 0);
            showToast?.(`Рассылка создана: ${data.recipients?.rows || 0} водителей`
                + (skipped ? `, пропущено ${skipped} строк (повторы и неверные номера)` : ''), 'success');
            onCreated(data.campaign);
        } catch (err) {
            fail(err?.message || 'Не удалось подготовить запись');
        } finally {
            setBusy('');
        }
    };

    const voiceOptions = (voices.voices || []).map((v) => ({ value: v.id, label: v.label }));
    const textMax = voices.text_max || 1500;

    const footer = (
        <div className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1 text-[12px] text-rose-500" role="status">{error}</div>
            <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy === 'create'}>Отмена</button>
            <button type="button" className={iosBtnPrimary} onClick={create} disabled={!ready || !!busy}>
                <FaIcon className={`fas ${busy === 'create' ? 'fa-spinner fa-spin' : 'fa-check'}`} />
                {busy === 'create' ? 'Готовим запись…' : 'Создать рассылку'}
            </button>
        </div>
    );

    return (
        <IosModal open={open} onClose={onClose} title="Новая рассылка" maxWidth="max-w-2xl" footer={footer}>
            <div className="space-y-5">
                <input className={iosInput} placeholder="Название, например «Бонус 5000 за 7 заказов»" value={form.name}
                    maxLength={200} onChange={(e) => set({ name: e.target.value })} />

                <section className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <div className={iosGroupLabel}>Запись</div>
                        <IosSegmented value={form.source} onChange={(v) => { set({ source: v }); setTrackInfo(null); }}
                            options={SOURCE_OPTIONS} size="xs" ariaLabel="Откуда запись" />
                    </div>

                    {form.source === 'tts' && (
                        <div className={`${iosCard} space-y-4 p-4`}>
                            <div className="flex gap-2">
                                <input className={`${iosInput} flex-1`} value={form.brief} maxLength={2000}
                                    placeholder="О чём сообщить водителям? ИИ напишет текст на двух языках"
                                    onChange={(e) => set({ brief: e.target.value })} />
                                <button type="button" className={iosBtnSecondary} onClick={generate}
                                    disabled={form.brief.trim().length < 10 || !!busy}>
                                    <FaIcon className={`fas ${busy === 'generate' ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles'}`} />
                                    Написать
                                </button>
                            </div>

                            {['kz', 'ru'].map((lang) => (
                                <div key={lang} className="space-y-1.5">
                                    <div className="flex items-center justify-between gap-2 px-0.5">
                                        <div className="text-[12.5px] font-semibold text-slate-700">
                                            {PART_META[lang].title}
                                            <span className="ml-1.5 font-normal text-slate-400">· {PART_META[lang].order}</span>
                                        </div>
                                        <div className="text-[11.5px] tabular-nums text-slate-400">
                                            {speechEstimate(form.texts[lang])}
                                        </div>
                                    </div>
                                    <textarea className={`${iosInput} min-h-[84px] resize-y leading-relaxed`} rows={3}
                                        maxLength={textMax} value={form.texts[lang]} placeholder={PART_META[lang].placeholder}
                                        onChange={(e) => setText(lang, e.target.value)} />
                                    <div className="flex items-center gap-2">
                                        <PlayButton label="Прослушать" disabled={!form.texts[lang].trim() || !!busy}
                                            getUrl={async () => (await speech(lang)).url} onError={fail} />
                                        {/* IosMenu ждёт в icon пункта компонент иконки, а не строку и не элемент:
                                            иначе страница падает целиком в момент открытия меню (React #130). */}
                                        <IosMenu label="ИИ" align="left"
                                            trigger={{ icon: busy.endsWith(`-${lang}`) ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />,
                                                       text: 'ИИ', className: iosBtnGhost }}
                                            disabled={!!busy}
                                            items={[
                                                { key: 'polish', label: 'Подправить текст', icon: PenLine,
                                                  onSelect: () => polish(lang) },
                                                { key: 'translate', label: PART_META[lang].translate, icon: Languages,
                                                  onSelect: () => translate(lang) },
                                            ]} />
                                    </div>
                                </div>
                            ))}

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                    <div className="px-0.5 text-[12px] font-medium text-slate-500">Голос</div>
                                    <IosSegmented value={voice} onChange={setVoice} options={voiceOptions} stretch size="xs"
                                        ariaLabel="Голос озвучки" />
                                    <div className="px-0.5 text-[11.5px] text-slate-400">
                                        {(voices.voices || []).find((v) => v.id === voice)?.about || ''}
                                    </div>
                                </div>
                                <div className="space-y-1.5">
                                    <div className="px-0.5 text-[12px] font-medium text-slate-500">Темп</div>
                                    <IosSegmented value={speed} onChange={setSpeed} options={VOICE_SPEEDS} stretch size="xs"
                                        ariaLabel="Темп речи" />
                                </div>
                            </div>
                        </div>
                    )}

                    {form.source === 'upload' && (
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {['kz', 'ru'].map((lang) => (
                                <label key={lang} className="flex cursor-pointer items-center gap-3 px-4 py-3">
                                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                                        <FaIcon className="fas fa-headphones" />
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block text-[13px] font-medium text-slate-800">
                                            {PART_META[lang].title} <span className="font-normal text-slate-400">· {PART_META[lang].order}</span>
                                        </span>
                                        <span className="block truncate text-[12px] text-slate-500">
                                            {form.files[lang]?.name || 'MP3, WAV или M4A'}
                                        </span>
                                    </span>
                                    <span className={`${iosBtnSecondary} py-1.5`}>{form.files[lang] ? 'Заменить' : 'Выбрать'}</span>
                                    <input type="file" accept="audio/*" className="hidden"
                                        onChange={(e) => { setFile(lang, e.target.files?.[0] || null); setTrackInfo(null); }} />
                                </label>
                            ))}
                        </div>
                    )}

                    <div className="flex flex-wrap items-center gap-2 px-0.5">
                        <PlayButton label="Прослушать всю запись" className={iosBtnGhost}
                            disabled={form.source === 'tts' ? !(form.texts.kz.trim() && form.texts.ru.trim())
                                : !(form.files.kz && form.files.ru)}
                            getUrl={async () => (await track()).url} onError={fail} />
                        {trackInfo && (
                            <span className="text-[11.5px] text-slate-400">
                                {seconds(trackInfo.durationMs)}: {partsLabel(trackInfo.parts)}
                            </span>
                        )}
                    </div>
                </section>

                <section className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <div className={iosGroupLabel}>Водители</div>
                        <IosHint text="Excel или CSV с колонкой телефонов, ФИО — по желанию. Повторы номеров и неверные номера пропускаются." />
                    </div>
                    <label className={`${iosCard} flex cursor-pointer items-center gap-3 px-4 py-3`}>
                        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                            <FaIcon className="fas fa-address-book" />
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                            {form.recipients?.name || 'Список номеров: .xlsx или .csv'}
                        </span>
                        <span className={`${iosBtnSecondary} py-1.5`}>{form.recipients ? 'Заменить' : 'Выбрать'}</span>
                        <input type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                            onChange={(e) => set({ recipients: e.target.files?.[0] || null })} />
                    </label>
                </section>

                <section className="space-y-2">
                    <div className={iosGroupLabel}>Когда звонить</div>
                    <div className={`${iosCard} grid gap-4 p-4 sm:grid-cols-2`}>
                        <div className="space-y-1.5">
                            <div className="px-0.5 text-[12px] font-medium text-slate-500">Время звонков, по Алматы</div>
                            <div className="flex items-center gap-2">
                                <input type="time" className={iosInput} value={form.windowStart}
                                    onChange={(e) => set({ windowStart: e.target.value })} />
                                <span className="text-slate-400">—</span>
                                <input type="time" className={iosInput} value={form.windowEnd}
                                    onChange={(e) => set({ windowEnd: e.target.value })} />
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <div className="flex items-center gap-1.5 px-0.5 text-[12px] font-medium text-slate-500">
                                Звонков в минуту
                                <IosHint text="Не больше 10: закон РК «О связи» (с 20.08.2026) запрещает операторам пропускать автозвонки с записью чаще. Одна линия робота держит 2 звонка одновременно." />
                            </div>
                            <input type="number" min={1} max={10} className={iosInput} value={form.perMinute}
                                onChange={(e) => set({ perMinute: e.target.value })} />
                        </div>
                        <div className="space-y-1.5">
                            <div className="flex items-center gap-1.5 px-0.5 text-[12px] font-medium text-slate-500">
                                Попыток на водителя
                                <IosHint text="Сколько раз звонить, если не ответил или занято. «Линия занята» на стороне Binotel попыткой не считается." />
                            </div>
                            <IosSegmented value={form.attempts} onChange={(v) => set({ attempts: v })} options={ATTEMPT_OPTIONS}
                                stretch size="xs" ariaLabel="Попыток на водителя" />
                        </div>
                        <div className="space-y-1.5">
                            <div className="px-0.5 text-[12px] font-medium text-slate-500">Повтор недозвона через</div>
                            <IosSegmented value={form.retry} onChange={(v) => set({ retry: v })} options={RETRY_OPTIONS}
                                stretch size="xs" ariaLabel="Повтор через" />
                        </div>
                    </div>
                </section>
            </div>
        </IosModal>
    );
};

/* ── карточка рассылки ────────────────────────────────────────────────────── */
const Tile = ({ label, value, sub }) => (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
        <div className="mt-0.5 text-[18px] font-semibold tabular-nums text-slate-800">{value}</div>
        {sub && <div className="text-[11.5px] text-slate-500">{sub}</div>}
    </div>
);

const CampaignModal = ({ campaignId, onClose, api, robot, showToast, onChanged, canEdit }) => {
    const [campaign, setCampaign] = useState(null);
    const [busy, setBusy] = useState('');
    const load = useCallback(async () => {
        if (!campaignId) return;
        try {
            const resp = await api(`/campaigns/${campaignId}`);
            if (resp.ok) setCampaign((await resp.json()).campaign);
        } catch { /* следующий опрос */ }
    }, [api, campaignId]);

    useEffect(() => { setCampaign(null); load(); }, [load]);
    useEffect(() => {
        if (!campaignId || campaign?.status !== 'active') return undefined;
        const timer = setInterval(load, POLL_ACTIVE_MS);
        return () => clearInterval(timer);
    }, [campaignId, campaign?.status, load]);

    const act = async (action) => {
        if (action === 'finish' && !window.confirm('Завершить рассылку? Кому ещё не звонили — уже не позвоним.')) return;
        setBusy(action);
        try {
            const resp = await api(`/campaigns/${campaignId}/${action}`, { method: 'POST', json: {} });
            if (!resp.ok) { showToast?.(await errorText(resp, 'Не получилось — попробуйте ещё раз'), 'error'); return; }
            setCampaign((await resp.json()).campaign);
            load();
            onChanged();
        } finally {
            setBusy('');
        }
    };

    const download = async () => {
        setBusy('export');
        try {
            const resp = await api(`/campaigns/${campaignId}/export`);
            if (!resp.ok) { showToast?.(await errorText(resp, 'Сервер не собрал файл'), 'error'); return; }
            const url = URL.createObjectURL(await resp.blob());
            const link = document.createElement('a');
            link.href = url;
            link.download = `Автоинформатор — ${campaign?.name || campaignId}.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } finally {
            setBusy('');
        }
    };

    const audioUrl = useRef(null);
    useEffect(() => () => { if (audioUrl.current) URL.revokeObjectURL(audioUrl.current); }, []);
    const getAudio = async () => {
        if (audioUrl.current) return audioUrl.current;
        const resp = await api(`/campaigns/${campaignId}/audio`);
        if (!resp.ok) throw new Error(await errorText(resp, 'Запись не загрузилась'));
        audioUrl.current = URL.createObjectURL(await resp.blob());
        return audioUrl.current;
    };

    const a = campaign?.analytics;
    // Без права правки — только смотреть: ни запуска, ни паузы, ни завершения.
    const actions = canEdit ? campaignActions(campaign?.status) : { start: false, pause: false, finish: false };
    const status = STATUS[campaign?.status] || STATUS.draft;
    const shares = stageShares(a?.stages);
    const states = a?.targets || {};
    const script = campaign?.script || {};

    return (
        <IosModal open={!!campaignId} onClose={onClose} title={campaign?.name || 'Рассылка'} maxWidth="max-w-3xl"
            subtitle={campaign ? `${status.label} · создана ${shortDateTime(campaign.created_at)}` : ''}>
            {!campaign ? (
                <div className="py-10 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
            ) : (
                <div className="space-y-5">
                    <div className="flex flex-wrap items-center gap-2">
                        {actions.start && (
                            <button type="button" className={iosBtnPrimary} disabled={!!busy} onClick={() => act('start')}>
                                <FaIcon className={`fas ${busy === 'start' ? 'fa-spinner fa-spin' : 'fa-play'}`} />
                                {campaign.status === 'paused' ? 'Продолжить' : 'Запустить'}
                            </button>
                        )}
                        {actions.pause && (
                            <button type="button" className={iosBtnSecondary} disabled={!!busy} onClick={() => act('pause')}>
                                <FaIcon className={`fas ${busy === 'pause' ? 'fa-spinner fa-spin' : 'fa-pause'}`} /> Пауза
                            </button>
                        )}
                        <PlayButton label="Запись" getUrl={getAudio} onError={(t) => showToast?.(t, 'error')} />
                        <button type="button" className={iosBtnGhost} disabled={!!busy} onClick={download}>
                            <FaIcon className={`fas ${busy === 'export' ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} /> Excel
                        </button>
                        {actions.finish && (
                            <button type="button" className={`${iosBtnGhost} text-rose-500`} disabled={!!busy} onClick={() => act('finish')}>
                                Завершить
                            </button>
                        )}
                    </div>

                    {campaign.status === 'active' && !robot?.online && (
                        <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] text-amber-700">
                            Робот не на связи — рассылка ждёт и начнёт звонить, как только он подключится.
                        </div>
                    )}

                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <Tile label="Водителей" value={campaign.targets_total}
                            sub={`в очереди ${(states.pending || 0) + (states.calling || 0)}`} />
                        <Tile label="Дозвонились" value={a?.answered ?? 0}
                            sub={a?.answer_rate != null ? `${Math.round(a.answer_rate * 100)}% от обработанных` : '—'} />
                        <Tile label="Слушали в среднем" value={seconds(a?.avg_listened_ms)}
                            sub={a?.median_listened_ms != null ? `медиана ${seconds(a.median_listened_ms)}` : ''} />
                        <Tile label="Дослушали" value={shares.parts[2].count}
                            sub={shares.total ? `${percent(shares.parts[2].count, shares.total)}% дозвонившихся` : '—'} />
                    </div>

                    {shares.total > 0 && (
                        <div className="space-y-2">
                            <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
                                {shares.parts.map((p) => p.count > 0 && (
                                    <div key={p.key} className={p.tone} style={{ width: `${p.share * 100}%` }} title={`${p.label}: ${p.count}`} />
                                ))}
                            </div>
                            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500">
                                {shares.parts.map((p) => (
                                    <span key={p.key} className="inline-flex items-center gap-1.5">
                                        <span className={`h-2 w-2 rounded-full ${p.tone}`} />
                                        {p.label} <span className="tabular-nums text-slate-700">{p.count}</span>
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}

                    <div className="text-[12px] text-slate-500">
                        Не дозвонились {states.given_up || 0}
                        {a?.calls?.line_busy ? ` · повторов из-за занятой линии ${a.calls.line_busy}` : ''}
                        {states.stopped ? ` · не звонили ${states.stopped}` : ''}
                        {' · '}{campaign.window_start}–{campaign.window_end}, до {campaign.max_per_minute} в минуту
                        {' · '}{partsLabel(campaign.parts)}
                    </div>

                    {script.source === 'tts' && (
                        <div className="grid gap-2 sm:grid-cols-2">
                            {['kz', 'ru'].map((lang) => (
                                <div key={lang} className="rounded-xl bg-slate-50 px-3 py-2.5">
                                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                                        {PART_META[lang].title}
                                    </div>
                                    <div className="mt-1 whitespace-pre-wrap text-[12.5px] leading-relaxed text-slate-700">
                                        {script.texts?.[lang] || '—'}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* На телефоне четыре колонки не помещаются (имя и итог резались до «Ахметов Е…»,
                        «дослу…»): там две — водитель с номером и итогом под именем, и секунды. */}
                    <div className={`${iosCard} overflow-hidden`}>
                        <div className="hidden grid-cols-[minmax(0,1.6fr)_7.5rem_minmax(0,1fr)_4.5rem] gap-2 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400 sm:grid">
                            <div>Водитель</div><div>Номер</div><div>Итог</div><div className="text-right">Слушал</div>
                        </div>
                        <div className="max-h-[22rem] divide-y divide-slate-100 overflow-y-auto">
                            {campaign.targets.length === 0 ? (
                                <div className="px-4 py-6 text-center text-[12.5px] text-slate-500">Звонков ещё не было</div>
                            ) : campaign.targets.map((t) => {
                                const outcome = t.state === 'done' ? stageLabel(t.stage) : (STATE_LABEL[t.state] || t.state);
                                const reason = t.state !== 'done' && t.last_result && t.state !== 'stopped'
                                    ? (RESULT_LABEL[t.last_result] || t.last_result) : '';
                                return (
                                    <div key={t.id} className="grid grid-cols-[minmax(0,1fr)_4.5rem] items-center gap-2 px-4 py-2 text-[12.5px] sm:grid-cols-[minmax(0,1.6fr)_7.5rem_minmax(0,1fr)_4.5rem]">
                                        <div className="min-w-0">
                                            <div className="truncate text-slate-800">{t.full_name || 'Без имени'}</div>
                                            <div className="truncate text-[11px] text-slate-400">
                                                {shortDateTime(t.last_call_at)}{t.attempts > 1 ? ` · попыток ${t.attempts}` : ''}
                                                <span className="sm:hidden"> · {t.phone}</span>
                                            </div>
                                            <div className="truncate text-[11.5px] text-slate-600 sm:hidden">
                                                {outcome}{reason ? <span className="text-slate-400"> · {reason}</span> : null}
                                            </div>
                                        </div>
                                        <div className="hidden tabular-nums text-slate-500 sm:block">{t.phone}</div>
                                        <div className="hidden min-w-0 truncate text-slate-600 sm:block">
                                            {outcome}{reason ? <span className="text-slate-400"> · {reason}</span> : null}
                                        </div>
                                        <div className="text-right tabular-nums text-slate-700">{t.state === 'done' ? seconds(t.listened_ms) : '—'}</div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            )}
        </IosModal>
    );
};

/* ── вкладка ──────────────────────────────────────────────────────────────── */
const DialListInformerPanel = ({ apiBaseUrl, authHeaders, departmentId, showToast, canEdit = true }) => {
    const [overview, setOverview] = useState(null);
    const [error, setError] = useState('');
    const [creating, setCreating] = useState(false);
    const [openId, setOpenId] = useState(null);

    const api = useCallback((path, { method = 'GET', json, body } = {}) => fetch(
        `${apiBaseUrl}/api/dial_list/departments/${departmentId}/informer${path}`,
        {
            method,
            credentials: 'include',
            headers: authHeaders(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
            body: json !== undefined ? JSON.stringify(json) : body,
        },
    ), [apiBaseUrl, authHeaders, departmentId]);

    const load = useCallback(async () => {
        try {
            const resp = await api('');
            if (!resp.ok) { setError(await errorText(resp, 'Не удалось загрузить рассылки')); return; }
            setOverview(await resp.json());
            setError('');
        } catch {
            setError('Нет связи с сервером');
        }
    }, [api]);

    useEffect(() => { setOverview(null); load(); }, [load]);
    const anyActive = useMemo(() => (overview?.campaigns || []).some((c) => c.status === 'active'), [overview]);
    useEffect(() => {
        const timer = setInterval(load, anyActive ? POLL_ACTIVE_MS : POLL_IDLE_MS);
        return () => clearInterval(timer);
    }, [load, anyActive]);

    const campaigns = overview?.campaigns || [];
    return (
        <section className="space-y-3">
            <RobotCard robot={overview?.robot} />

            <div className="flex items-center justify-between gap-3 px-1">
                <div className={iosGroupLabel}>Рассылки</div>
                {canEdit && (
                    <button type="button" className={`${iosBtnPrimary} py-2`} onClick={() => setCreating(true)}>
                        <FaIcon className="fas fa-plus" /> Новая рассылка
                    </button>
                )}
            </div>

            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {error ? (
                    <div className="px-4 py-6 text-center text-[13px] text-rose-500">{error}</div>
                ) : !overview ? (
                    <div className="px-4 py-6 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : campaigns.length === 0 ? (
                    <div className="px-4 py-8 text-center">
                        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-phone-volume" style={{ width: 18, height: 18 }} />
                        </div>
                        <div className="mt-3 text-[14px] font-semibold text-slate-800">Рассылок ещё не было</div>
                        <div className="mx-auto mt-1 max-w-md text-[12.5px] text-slate-500">
                            Робот позвонит водителям с номера удалённого КЦ и проиграет запись: сначала на казахском, потом на русском.
                        </div>
                    </div>
                ) : campaigns.map((c) => <CampaignRow key={c.id} campaign={c} onOpen={setOpenId} />)}
            </div>

            <CreateCampaignModal open={creating} onClose={() => setCreating(false)} api={api} showToast={showToast}
                onCreated={(campaign) => { setCreating(false); load(); setOpenId(campaign.id); }} />
            <CampaignModal campaignId={openId} onClose={() => setOpenId(null)} api={api} robot={overview?.robot}
                showToast={showToast} onChanged={load} canEdit={canEdit} />
        </section>
    );
};

export default DialListInformerPanel;
