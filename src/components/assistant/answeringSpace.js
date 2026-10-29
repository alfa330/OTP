/* Из какой вики отвечает помощник — подпись мини-чата (AssistantPanel: spaceName).
 *
 * Имя нужно, когда без него ответ читался бы ответом из открытой вики: вик с
 * помощником у человека несколько, или сервер подставил другую вместо
 * запрошенной (в открытой «Помощник» выключен — wiki/routes_ai.py:
 * effective_space). Шарик висит и над такой вики, и ответ из «Таксопарков» над
 * статьями «Тез КЦ» без имени выглядел бы ответом из них. У кого вика одна,
 * имя — лишнее слово в каждой подписи.
 *
 * status — ответ /ai/status: space_id — вика, по которой отвечают на самом
 * деле, spaces — вики, где помощник включён. askSpace — о какой спрашивали.
 */
export const answeringSpaceName = (status, askSpace) => {
    const spaces = status?.spaces || [];
    const answering = spaces.find((sp) => sp.id === status.space_id);
    if (!answering) return null;
    const substituted = !!askSpace && askSpace !== status.space_id;
    return spaces.length > 1 || substituted ? answering.name : null;
};
