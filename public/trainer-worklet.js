// Раздел «Тренажёр»: захват микрофона, Float32 → Int16 LE.
//
// Лежит в public/, а не в src/: AudioWorklet грузится браузером по URL как
// отдельный модуль, и собранный бандл ему не подходит.
//
// Ресемплинга здесь нет намеренно. Открыть AudioContext на 16 кГц удаётся не в
// каждом браузере, а попытка задать частоту насильно даёт либо отказ, либо тихий
// мусор в распознавании. Отдаём звук на родной частоте устройства, а её саму
// сообщаем Soniox — он принимает любую.
class TrainerCapture extends AudioWorkletProcessor {
    process(inputs) {
        const channel = inputs[0] && inputs[0][0];
        if (channel && channel.length) {
            const pcm = new Int16Array(channel.length);
            for (let i = 0; i < channel.length; i += 1) {
                const sample = Math.max(-1, Math.min(1, channel[i]));
                pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            }
            this.port.postMessage(pcm.buffer, [pcm.buffer]);
        }
        return true;
    }
}

registerProcessor('trainer-capture', TrainerCapture);
