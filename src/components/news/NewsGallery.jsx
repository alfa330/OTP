import React, { useEffect, useRef, useState } from 'react';
import { IosLightbox } from '../ui/ios';

/* Карусель кадров новости.
 *
 * ЛИСТАНИЕ БЕРЁТСЯ ГОТОВЫМ — src/components/wiki/gallery.js. Там уже решены все
 * четыре жеста (палец браузером, трекпад, мышь и клавиши — нами), захват
 * указателя, гашение клика после тяги и пересчёт размеров после загрузки кадра.
 * Модуль ничего не импортирует и про раздел «Вики» не знает — привязки к нему
 * тут нет, только переиспользование выверенного кода.
 *
 * ИМПОРТ ДИНАМИЧЕСКИЙ. Окно новости статически смонтировано в App.jsx, и обычный
 * import утащил бы gallery.js в главный чанк КАЖДОМУ вошедшему в портал, включая
 * тех, кому новость сегодня не пришла. Чанк едет тогда же, когда первый кадр, —
 * то есть когда картинку всё равно ждут.
 *
 * УЗЛЫ СТРОИМ РУКАМИ, а не детьми JSX, и это главное здесь. mountGallery
 * перекладывает DOM: оборачивает каждый кадр в .wiki-gallery__slide, подменяет
 * ленту сценой и боксом. React, владеющий этими детьми, стал бы диффать дерево,
 * которого не строил, и упал бы NotFoundError на размонтировании — причём не
 * сразу, а на ВТОРОЙ новости в очереди, то есть у человека с двумя объявлениями
 * подряд, а не у разработчика с одним. А уронив обязательное окно, он закрыл бы
 * человеку вход в портал. Здесь React владеет ровно одним пустым div.
 *
 * ЗОВЁМ mountGallery, А НЕ attachGallery. Второй не оборачивает кадры в слайды —
 * это делает только первый, — и правило `flex: 0 0 100%` не применилось бы:
 * три вертикальных снимка встали бы в ряд, scrollWidth сравнялся бы с clientWidth,
 * и листать было бы нечего при живых стрелках.
 *
 * СТИЛИ СВОИ, в news-modal.css. Все 46 правил галереи в wiki-blocks.css
 * начинаются с предка .wiki-prose и берут цвета из переменных .wiki-scope,
 * который вдобавок означает теперь и zoom. Тащить чужую тему в главный чанк
 * ради ленты кадров — ровно то, от чего раздел отвязан.
 */
export default function NewsGallery({ photos = [], onBroken }) {
    const hostRef = useRef(null);
    const [zoomed, setZoomed] = useState(null);

    /* Кадр без адреса не показываем вовсе: подпись могла не собраться (нет
       ключа, опечатка в имени бакета), и окно обязано открыться хотя бы с
       текстом объявления. */
    const shown = (photos || []).filter((photo) => photo && photo.url);
    /* Ключ по адресам, а не по массиву: ответ /pending приезжает на каждый тычок
       канала колокола, и новый объект пересобирал бы карусель, отматывая её на
       первый кадр ровно тогда, когда человек смотрит пятый. Адреса при этом
       стабильны байт в байт — их держит кэш подписей на сервере. */
    const key = shown.map((photo) => photo.url).join('|');

    useEffect(() => {
        const host = hostRef.current;
        if (!host || !key) return undefined;
        let undo = null;
        let alive = true;

        const strip = document.createElement('div');
        strip.className = 'news-gallery__strip';
        shown.forEach((photo, at) => {
            const frame = document.createElement('img');
            frame.src = photo.url;
            /* Подписи у кадра нет (колонки под неё в базе тоже нет), а читалке
               с экрана нужно сказать хоть что-то осмысленное. */
            frame.alt = `Фото ${at + 1} из ${shown.length}`;
            /* Размеры до загрузки: без них offsetWidth равен нулю, и вся
               арифметика карусели считается по нулям, а окно прыгает на высоту
               кадра в момент, когда человек уже читает. */
            if (photo.width) frame.width = photo.width;
            if (photo.height) frame.height = photo.height;
            /* Первый — сразу, остальные по мере листания: десять кадров это
               мегабайты, и тянуть их разом значит держать окно пустым, пока
               человек читает первый абзац. */
            if (at) frame.loading = 'lazy';
            frame.decoding = 'async';
            /* Подпись конечна. Протухла — просим свежую выдачу: она принесёт
               новые адреса, а слияние очереди в окне их подхватит. */
            frame.addEventListener('error', () => { if (alive) onBroken?.(); });
            frame.addEventListener('click', () => { if (alive) setZoomed(photo); });
            strip.appendChild(frame);
        });
        host.appendChild(strip);

        import('../wiki/gallery').then(({ mountGallery }) => {
            if (!alive) return;
            undo = mountGallery(strip, document);
        }).catch(() => {
            /* Обвязка не доехала — лента остаётся прокручиваемой пальцем:
               кадры видно, листать можно, просто без стрелок и точек. */
        });

        return () => {
            alive = false;
            if (undo) undo();
            host.textContent = '';
        };
    }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps

    if (!shown.length) return null;
    return (
        <>
            <div className="news-gallery" ref={hostRef} />
            {/* Лайтбокс живёт ЗДЕСЬ, а не в окне новости, и рисуется порталом:
                в NewsOfDayModal.jsx строка `fixed inset-0` обязана остаться
                единственной — её первое вхождение сторожит тест как подложку
                модального окна. */}
            <IosLightbox url={zoomed?.url} alt="" onClose={() => setZoomed(null)} />
        </>
    );
}
