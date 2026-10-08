import React, { memo } from 'react';
import { appleEmojiUrl, handleEmojiImageError } from './chatEmojiImages';
import { tokenizeMessageEmoji } from './chatMessageEmoji';

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
export default memo(function ChatMessageText({ text, className = '' }) {
    return <span className={className}>
        {tokenizeMessageEmoji(text).map((token, index) => token.unified
            ? <span key={index} style={emojiBox}><span style={{ opacity: 0 }}>{token.text}</span><img
                src={appleEmojiUrl(token.unified)} alt="" aria-hidden="true" width={20} height={20}
                loading="lazy" decoding="async" draggable={false} style={emojiImage}
                onError={handleEmojiImageError} /></span>
            : token.text)}
    </span>;
});
