/* Вкладка «Обращения» карточки звонка (задача #335, п. 2): прошлые обращения человека.
 *
 * По умолчанию — обращения выбранного аккаунта с любых номеров и обращения с этого номера,
 * где аккаунт так и не указали. Поиск идёт по ним же; «Все обращения» — по всему отделу
 * (найти обращение, когда человек звонит с третьего номера, а аккаунт ещё не выбран).
 * Нажатие открывает обращение целиком; оттуда — «Продолжить это обращение».
 */
import React, { useCallback, useEffect, useState } from 'react';
import { History, Search } from 'lucide-react';

import { IosBadge, IosSegmented, iosBtnSecondary, iosCard, iosInput } from '../components/ui/ios';
import CardDetail from '../components/client_cards/CardDetail';
import { NO_ACCOUNT_LABEL, REQUEST_TONES, accountTitle, errorText, fmtWhen, topicLabel } from '../components/client_cards/cardRules';
import { EmptyState, Spinner, StateBadge } from '../components/client_cards/ui';

export default function HistoryTab({ api, card, toast, onContinue, onOpenRequest, onCount }) {
    const [scope, setScope] = useState('person');
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [items, setItems] = useState(null);
    const [hasMore, setHasMore] = useState(false);
    const [failed, setFailed] = useState('');
    const [openId, setOpenId] = useState(null);
    const [loadingMore, setLoadingMore] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setSearch(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);

    const cardId = card?.id || null;
    const accountId = card?.account_id || null;
    const load = useCallback(async () => {
        try {
            const { data } = await api.history({
                card_id: cardId || undefined, scope: scope === 'all' ? 'all' : undefined, q: search || undefined,
            });
            setItems(data.items);
            setHasMore(Boolean(data.has_more));
            setFailed('');
            if (scope === 'person' && !search) onCount?.(data.items.length, Boolean(data.has_more));
        } catch (error) {
            setFailed(errorText(error, 'Обращения не загрузились'));
        }
        // accountId: выбрали другой аккаунт — история у карточки другая.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api, cardId, accountId, scope, search]);

    useEffect(() => { setItems(null); load(); }, [load]);

    const more = async () => {
        const last = items?.[items.length - 1];
        if (!last || loadingMore) return;
        setLoadingMore(true);
        try {
            const { data } = await api.history({
                card_id: cardId || undefined, scope: scope === 'all' ? 'all' : undefined,
                q: search || undefined, before: last.id,
            });
            setItems([...items, ...data.items]);
            setHasMore(Boolean(data.has_more));
        } catch (error) {
            toast(errorText(error), 'error');
        } finally {
            setLoadingMore(false);
        }
    };

    return (
        <div className="space-y-3">
            <div className="space-y-2">
                <IosSegmented value={cardId ? scope : 'all'} onChange={setScope} stretch
                              options={[
                                  cardId && { value: 'person', label: 'Этот человек' },
                                  { value: 'all', label: 'Все обращения' },
                              ]} ariaLabel="Чьи обращения показать" />
                <label className="relative block">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)}
                           placeholder="Телефон, ID аккаунта, причина, текст" className={`${iosInput} bg-white py-2 pl-8 ring-1 ring-slate-200/70`} />
                </label>
            </div>

            {failed && (
                <EmptyState title={failed}>
                    <button type="button" onClick={load} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                </EmptyState>
            )}
            {!failed && !items && <Spinner />}
            {items && !items.length && (
                <EmptyState icon={History}
                            title={search ? 'Ничего не нашлось' : 'Обращений ещё не было'}
                            text={!search && scope === 'person' && cardId && !accountId
                                ? 'С этого номера раньше не обращались. Выберите аккаунт — покажем обращения по нему с других номеров.'
                                : ''} />
            )}
            <ul className="space-y-1.5">
                {items?.map((item) => (
                    <li key={item.id}>
                        <button type="button" onClick={() => setOpenId(item.id)}
                                className={`${iosCard} block w-full px-3 py-2.5 text-left transition hover:bg-slate-50 active:scale-[0.995]`}>
                            <div className="flex items-center justify-between gap-2">
                                <span className="min-w-0 truncate text-[13.5px] font-medium text-slate-900">
                                    {item.topics.length ? item.topics.map(topicLabel).join(' · ') : 'Причина не указана'}
                                </span>
                                <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">{fmtWhen(item.created_at)}</span>
                            </div>
                            {item.comment && <div className="mt-0.5 line-clamp-2 text-[12.5px] text-slate-600">{item.comment}</div>}
                            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-400">
                                <span>{item.operator_name}</span>
                                {scope === 'all' && item.account && <span>· {item.account.kind_label} {accountTitle(item.account)}</span>}
                                {scope === 'all' && !item.account && item.no_account && <span>· {NO_ACCOUNT_LABEL}</span>}
                                {scope === 'all' && <span className="tabular-nums">· {item.phone_text}</span>}
                                <StateBadge card={item} />
                                {item.requests.map((request) => (
                                    <IosBadge key={request.id} tone={REQUEST_TONES[request.status] || 'slate'} className="!py-0.5">
                                        {request.destination_title}: {request.status_label.toLowerCase()}
                                    </IosBadge>
                                ))}
                            </div>
                        </button>
                    </li>
                ))}
            </ul>
            {hasMore && (
                <button type="button" onClick={more} disabled={loadingMore} className={`${iosBtnSecondary} mx-auto flex`}>
                    {loadingMore ? 'Загружаем…' : 'Показать ещё'}
                </button>
            )}

            <CardDetail api={api} cardId={openId} onClose={() => setOpenId(null)}
                        onContinue={cardId && card.status !== 'void'
                            ? (picked) => { setOpenId(null); onContinue(picked); } : null}
                        onOpenRequest={(requestId) => { setOpenId(null); onOpenRequest(requestId); }} />
        </div>
    );
}
