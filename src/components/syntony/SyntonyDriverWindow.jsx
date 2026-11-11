import React from 'react';
import { IosModal } from '../ui/ios';
import SyntonyWindow from './SyntonyWindow';
import { SCOPE_PHONE } from './syntonyRules';

/*
 * Переписка водителя в Syntony — окно по его номеру (ТЗ Syntony, задача 3: «карточка
 * водителя», высота 600–800 px). Решение владельца 11.11.2026: кнопкой в карточке
 * водителя «Чатов ОП», окно запрашивается только по нажатию.
 *
 * Показывает переписку с этим водителем, в каком бы парке сети она ни шла. Номеров
 * может быть несколько (номер чата и номер из CRM) — сервер сам выберет «phone» или
 * «phones». Закрыли — окна нет; открыли снова — новая ссылка. Поэтому своей кнопки
 * «Открыть заново» в шапке нет: при ошибке её несёт само сообщение, а открытое окно
 * переоткрывают, закрыв и нажав кнопку карточки ещё раз.
 */

const SyntonyDriverWindow = ({ open, onClose, phones, apiBaseUrl, withAccessTokenHeader }) => (
    <IosModal
        open={open}
        onClose={onClose}
        title="Переписка в Syntony"
        subtitle={(phones || []).join(' · ') || null}
        maxWidth="max-w-3xl"
        /* Своё тело окна: фрейм во всю площадь, без полей. На компьютере — высота из ТЗ
           (но не выше экрана), на телефоне — весь экран. Класс thin-scroll оставлен, как
           у всех окон портала (страж test_thin_scroll), хотя прокручивается здесь само окно Syntony. */
        bodyClassName="thin-scroll min-h-0 flex-1 overflow-hidden bg-white sm:h-[min(760px,78vh)] sm:flex-none"
    >
        {open && (
            <SyntonyWindow
                apiBaseUrl={apiBaseUrl}
                withAccessTokenHeader={withAccessTokenHeader}
                scope={SCOPE_PHONE}
                phones={phones}
                title="Переписка водителя в Syntony"
            />
        )}
    </IosModal>
);

export default SyntonyDriverWindow;
