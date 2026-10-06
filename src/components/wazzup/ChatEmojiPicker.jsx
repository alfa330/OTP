import React from 'react';
import EmojiPicker from 'emoji-picker-react';
import ru from 'emoji-picker-react/dist/data/emojis-ru';

export default function ChatEmojiPicker({ onSelect }) {
    return <EmojiPicker emojiStyle="native" emojiData={ru} width="100%" height={340}
        searchPlaceHolder="Найти эмодзи" previewConfig={{ showPreview: false }}
        onEmojiClick={(data) => onSelect(data.emoji)} />;
}
