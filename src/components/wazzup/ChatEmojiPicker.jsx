import React from 'react';
import EmojiPicker from 'emoji-picker-react';
import ru from 'emoji-picker-react/dist/data/emojis-ru';
import { appleEmojiUrl, createEmojiImageWarmup, handleEmojiImageError } from './chatEmojiImages';

export const preloadFirstEmojiImages = createEmojiImageWarmup(ru.emojis.smileys_people.slice(0, 24).map((emoji) => emoji.u));

export default function ChatEmojiPicker({ onSelect }) {
    return <div onErrorCapture={handleEmojiImageError}>
        <EmojiPicker emojiStyle="apple" emojiData={ru} width="100%" height={340}
            lazyLoadEmojis getEmojiUrl={appleEmojiUrl}
            searchPlaceholder="Найти эмодзи" searchClearButtonLabel="Очистить поиск"
            previewConfig={{ showPreview: false }} onEmojiClick={(data) => onSelect(data.emoji)} />
    </div>;
}
