// Channel names may include a support/team suffix. Match whole brand words,
// never substrings (Taxi must not turn into iTaxi), and reject ambiguity.
const GENERIC_PARK_WORDS = new Set(['taxi', 'такси', 'таксопарк', 'таксопарки']);
const BRAND_ALIASES = new Map([
    ['tenge', 'тенге'], ['chestniy', 'честный'], ['eki dongelek', '2dongelek'],
]);
const words = (value) => String(value || '').normalize('NFKC').toLowerCase()
    .replace(/ё/g, 'е').match(/[\p{L}\p{N}]+/gu) || [];

export const parkNameKey = (value) => {
    let key = words(value).filter((word) => !GENERIC_PARK_WORDS.has(word)).join(' ');
    for (const [alias, canonical] of BRAND_ALIASES) {
        key = ` ${key} `.replaceAll(` ${alias} `, ` ${canonical} `).trim();
    }
    return key;
};

export function matchChannelPark(channel, parks) {
    const key = parkNameKey(channel?.name);
    if (!key) return null;
    const candidates = (parks || []).filter((park) => park.status !== 'archived')
        .map((park) => ({ park, key: parkNameKey(park.name) }))
        .filter((entry) => entry.key);
    const exact = candidates.filter((entry) => entry.key === key);
    if (exact.length) return exact.length === 1 ? exact[0].park : null;
    const contained = candidates.filter((entry) => ` ${key} `.includes(` ${entry.key} `));
    return contained.length === 1 ? contained[0].park : null;
}

export function chooseParkSpace(spaces) {
    const own = (spaces || []).filter((space) => !space.guest_only && Number(space.id) > 0);
    const taxi = own.filter((space) => words(space.name).join(' ') === 'таксопарки');
    if (taxi.length) return taxi.length === 1 ? taxi[0].id : null;
    return own.length === 1 ? own[0].id : null;
}

export function channelInitials(channel) {
    const parts = words(channel?.name).filter((part) => !GENERIC_PARK_WORDS.has(part));
    if (parts.length > 1) return `${[...parts[0]][0]}${[...parts[1]][0]}`.toUpperCase();
    if (parts.length) return [...parts[0]].slice(0, 2).join('').toUpperCase();
    return words(channel?.name).join('').slice(0, 2).toUpperCase() || 'К';
}

// One directory per mount/user/account. No polling, per-channel requests,
// persistent signed URLs, or shared caches surviving a user change.
export async function loadChannelParks({ get, base, headers, signal }) {
    const options = () => ({ headers: typeof headers === 'function' ? headers() : (headers || {}),
        signal, timeout: 12000 });
    const ping = await get(`${base}/ping`, options());
    if (signal.aborted) return [];
    const spaceId = chooseParkSpace(ping.data?.spaces);
    if (!spaceId) return [];
    const response = await get(`${base}/parks`, { ...options(), params: { space_id: spaceId } });
    if (signal.aborted) return [];
    return (Array.isArray(response.data?.items) ? response.data.items : [])
        .filter((park) => park.status !== 'archived')
        // Do not keep office/phone details in the chat UI.
        .map(({ id, name, logo_url, logo_frame }) => ({ id, name, logo_url, logo_frame }));
}
