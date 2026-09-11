/* Экран, который умеет уехать: уход для окон, которые о нём не знают.
 *
 * ЗАЧЕМ. На телефоне окна портала — это экраны: въезжают справа, уезжают
 * вправо (решение владельца 10.09.2026). Въезд достаётся всем даром — это
 * CSS-анимация появления, и она играет на любом узле с меткой
 * `otp-modal-root`. А уход даром не достаётся никому: чтобы что-то уехало,
 * разметка обязана дожить до конца движения, а React снимает её в тот же такт,
 * когда состояние стало «закрыто».
 *
 * Своё ожидание завели ровно два примитива — IosModal и SimpleModal (там это
 * состояние `leaving`). Остальные экраны — окна «Задач», полноэкранный лист,
 * смена логина, пароля и фотографии, карточка сотрудника, история правок —
 * ИСЧЕЗАЛИ МГНОВЕННО. Въехал плавно, пропал щелчком; на жесте «назад» это
 * заметнее всего, потому что палец ещё ведёт, а экрана уже нет. Ровно это
 * владелец 11.09.2026 назвал «резко при свайпе назад».
 *
 * ПОЧЕМУ НЕ ПЕРЕПИСАТЬ КАЖДОЕ ОКНО. Ожидание внутри окна не работает: окно
 * рисуется условием в родителе (`{open && <Окно/>}`), и держать разметку лишние
 * 0.3 с должен РОДИТЕЛЬ — то есть правка нужна в каждом месте, где окно
 * открывают, а их десятки, и каждое новое окно снова про это забудет. Здесь же
 * правило одно и действует на всё, что помечено `otp-modal-root`, — включая то,
 * что напишут завтра.
 *
 * КАК. Наблюдатель видит, что узел с меткой сняли с дерева, и возвращает ЕГО ЖЕ
 * на прежнее место с классом `is-leaving` — под тот самый уход, который уже
 * описан в стилях. Через SCREEN_EXIT_MS узел снимается насовсем.
 *
 * Возвращаем ИМЕННО ТОТ ЖЕ УЗЕЛ, а не его копию: у экрана кадрирования
 * фотографии внутри <canvas> с нарисованным снимком, а копия <canvas> приходит
 * пустой — вместо уезжающего фото уехал бы белый прямоугольник. Плюс у копии
 * заново грузятся картинки, то есть на кадр они пропадают.
 *
 * Узел на это время глухой: `inert` и `pointer-events: none` (в стилях).
 * React про него уже забыл, и нажатие ушло бы обработчику, которого нет.
 */

/* Сколько длится уход. Одно число на три места: здесь, SCREEN_LEAVE_MS в
   src/components/ui/ios.jsx и анимация otp-screen-out в стилях моторики.
   Разойдясь, они дадут либо обрубленное движение, либо застывший кадр. */
export const SCREEN_EXIT_MS = 300;

const ROOT_CLASS = 'otp-modal-root';
const LEAVING_CLASS = 'is-leaving';
/* Запас поверх длительности: анимация стартует со следующего кадра, и снимать
   узел ровно в SCREEN_EXIT_MS значило бы иногда обрывать последние кадры. */
const CLEANUP_SLACK_MS = 80;
/* Больше трёх уезжающих экранов разом не бывает — это признак того, что
   что-то пошло не так (перерисовка дерева, спор двух состояний). Лишние не
   провожаем: пусть лучше исчезнет мгновенно, чем экран забьётся призраками. */
const MAX_LEAVING = 3;

const isScreenRoot = (node) => (
    node?.nodeType === 1 && node.classList?.contains(ROOT_CLASS)
);

/**
 * Включает проводы экранов. Возвращает функцию, снимающую наблюдателя.
 *
 * Зовётся только на телефоне (см. useMobileShell в MobileTabBar.jsx): на
 * компьютере окна не ездят, и держать наблюдателя на всё дерево незачем.
 */
export function startMobileScreenExit(doc) {
    const target = doc?.body;
    if (!target || typeof MutationObserver !== 'function') return () => {};

    /* Системная настройка «меньше движения» отменяет поездки целиком: провожать
       экран, который не поедет, значит просто задержать его на 0.3 с. */
    const calm = typeof doc.defaultView?.matchMedia === 'function'
        ? doc.defaultView.matchMedia('(prefers-reduced-motion: reduce)')
        : null;

    /* Узел → его таймер. Именно Map, а не Set: при выключении оболочки
       (поворот в настольную ширину, выход из портала) незавершённые таймеры
       надо снять, иначе они снимут уже чужие узлы. */
    const leaving = new Map();

    const sendOff = (node, parent) => {
        if (!parent.isConnected || leaving.size >= MAX_LEAVING) return;
        node.classList.add(LEAVING_CLASS);
        node.setAttribute('aria-hidden', 'true');
        /* inert закрывает узел и для читалки, и для клавиатуры: уезжающий
           экран не должен ловить фокус — он уже не существует для портала. */
        try { node.setAttribute('inert', ''); } catch (error) { /* старый браузер */ }
        /* Ставим последним ребёнком, а не на прежнее место: у экрана свой слой
           (z-index 120), порядок среди соседей на вид не влияет, зато вставка
           в конец гарантированно не мешает React'у, который свои узлы
           расставляет относительно СВОИХ же соседей. */
        parent.appendChild(node);
        leaving.set(node, setTimeout(() => {
            leaving.delete(node);
            node.remove();
        }, SCREEN_EXIT_MS + CLEANUP_SLACK_MS));
    };

    const observer = new MutationObserver((records) => {
        if (!target.classList.contains('mobile-shell')) return;
        if (calm?.matches) return;

        /* Родители, куда в этой же партии ЛЁГ новый экран. Это признак не
           закрытия, а пересоздания: React снял узел и тут же поставил на его
           место другой (смена ключа, перестройка поддерева). Провожать старый
           тогда нельзя — он уехал бы поверх только что приехавшего. */
        let replaced = null;
        for (const record of records) {
            for (const node of record.addedNodes) {
                if (!isScreenRoot(node)) continue;
                (replaced || (replaced = new Set())).add(record.target);
            }
        }

        for (const record of records) {
            if (!record.removedNodes.length) continue;
            for (const node of record.removedNodes) {
                /* Только сам корень экрана. Если React снял целый кусок
                   раздела, внутри которого был экран, вернуть этот кусок
                   значило бы на треть секунды показать двойник половины
                   страницы — а это хуже щелчка, который мы чиним. */
                if (!isScreenRoot(node)) continue;
                /* Окно, у которого уход есть свой (IosModal, SimpleModal),
                   уходит с этой меткой уже поставленной. */
                if (node.classList.contains(LEAVING_CLASS)) continue;
                if (replaced?.has(record.target)) continue;
                sendOff(node, record.target);
            }
        }
    });

    observer.observe(target, { childList: true, subtree: true });

    return () => {
        observer.disconnect();
        leaving.forEach((timer, node) => {
            clearTimeout(timer);
            node.remove();
        });
        leaving.clear();
    };
}

export default startMobileScreenExit;
