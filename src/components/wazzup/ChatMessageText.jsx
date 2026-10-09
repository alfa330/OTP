import React, { memo } from 'react';
import { appleEmojiUrl, handleEmojiImageError } from './chatEmojiImages';
import { tokenizeMessageEmoji } from './chatMessageEmoji';
import { decodeMessageText, tokenizeMessageLinks } from './messageTextFormatting';

const emojiBox = {
    display: 'inline-block', position: 'relative', width: '1.25em', height: '1.25em',
    lineHeight: '1.25em', verticalAlign: '-0.23em', whiteSpace: 'nowrap', overflow: 'hidden',
};
const emojiImage = {
    position: 'absolute', inset: 0, width: '100%', height: '100%',
    objectFit: 'contain', pointerEvents: 'none', userSelect: 'none',
};

// Keep the original Unicode in the selectable text flow. The artwork is decorative,
// so copying text across one or several messages never copies image URLs or alt text.
function renderEmojiText(text, keyPrefix = '') {
    return tokenizeMessageEmoji(text).map((token, index) => token.unified
        ? <span key={`${keyPrefix}${index}`} style={emojiBox}><span style={{ opacity: 0 }}>{token.text}</span><img
            src={appleEmojiUrl(token.unified)} alt="" aria-hidden="true" width={20} height={20}
            loading="lazy" decoding="async" draggable={false} style={emojiImage}
            onError={handleEmojiImageError} /></span>
        : token.text);
}

export default memo(function ChatMessageText({ text, className = '', links = true }) {
    const tokens = links ? tokenizeMessageLinks(text) : [{ text: decodeMessageText(text) }];
    return <span className={className}>
        {tokens.flatMap((token, index) => token.href
            ? [<a key={`link-${index}`} href={token.href} target="_blank" rel="noopener noreferrer"
                className="text-sky-700 underline decoration-sky-500/50 underline-offset-2 hover:text-sky-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500"
                onClick={(event) => event.stopPropagation()}>
                {renderEmojiText(token.text)}
            </a>]
            : renderEmojiText(token.text, `${index}-`))}
    </span>;
});
