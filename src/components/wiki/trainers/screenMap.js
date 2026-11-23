/* Карта экранов тренажёров: какой компонент рисует step.screen каждого сценария.
 *
 * Живёт отдельным модулем, а не внутри проигрывателя, ради теста: он проходит
 * урок правильными нажатиями, рисует экран каждого шага ЭТОЙ ЖЕ картой и
 * проверяет, что нужная кнопка на нём есть и подсвечена. Пока карта была
 * внутри TrainerPlayer.jsx, переименованный экран или потерянная кнопка
 * обнаруживались только у человека — пустым телефоном посреди урока.
 */

import { IntroScreen, ResultScreen } from './screenKit';
import { DeskScreen } from './screensCrm';
import { EgCode, EgSign, EgSuccess } from './screensEgov';
import {
    TpCheck, TpDocuments, TpEcpAdd, TpEcpHistory, TpEcpSigned, TpHome, TpSheetAd,
    TpSheetCabinet, TpSheetSignAll, TpSheetSigned,
} from './screensTaxiPro';
import {
    ChromeAddress, ChromeBlank, PhoneHome, SaparDocuments, SaparGuest, SaparProfile,
    SaparSave, SaparSignSheet, SaparStatus,
} from './screensSapar';
import {
    YpConsent, YpEdo, YpLegal, YpNews, YpProfile, YpProviders, YpSheet,
} from './screensYandexPro';

/* Экраны по сценарию. Ключи совпадают со step.screen — расхождение здесь
   означало бы пустой телефон посреди урока, поэтому карта одна и её сверяет
   tests/wiki_trainer_screens.test.mjs. */
export const SCREENS = {
    /* Единственный тренажёр не на телефоне, а за компьютером: оператор заводит
       обращение в браузере. Экран формы один на все шаги — меняется не он, а
       состав полей, и приходят они из мира (см. scenarioCrmTicket). */
    'crm-ticket-create': {
        intro: IntroScreen,
        desk: DeskScreen,
        result: ResultScreen,
    },
    /* Аттестация — та же CRM, без подсказок. */
    'crm-attestation': {
        intro: IntroScreen,
        desk: DeskScreen,
        result: ResultScreen,
    },
    /* Та же среда, но со звонком: экраны общие, разный только режим. */
    'operator-call': {
        intro: IntroScreen,
        desk: DeskScreen,
        result: ResultScreen,
    },
    'yandex-pro-edo-provider': {
        intro: IntroScreen,
        yp_news: YpNews,
        yp_profile: YpProfile,
        yp_legal: YpLegal,
        // Экран ЭДО один и тот же до смены и после: разница — строка активного
        // провайдера, по ней и проверяют результат.
        yp_edo: YpEdo,
        yp_providers: YpProviders,
        yp_sheet: YpSheet,
        yp_consent: YpConsent,
        result: ResultScreen,
    },
    'taxi-pro-avr': {
        intro: IntroScreen,
        tp_home: TpHome,
        // Вкладка («Через ЭЦП» или «Через eGov») берётся из мира: экран один.
        tp_documents: TpDocuments,
        // Окно «Подписание документов» с кабинетом Сапар внутри. Префикс tp_sheet
        // важен и для стилей: над этим окном строка состояния белая на чёрном.
        tp_sheet_ad: TpSheetAd,
        tp_sheet_cabinet: TpSheetCabinet,
        tp_sheet_sign_all: TpSheetSignAll,
        eg_sign: EgSign,
        eg_success: EgSuccess,
        tp_sheet_signed: TpSheetSigned,
        tp_check: TpCheck,
        // Способ «Через ЭЦП»: окно «Добавьте ЭЦП на этот телефон» над главной,
        // после «Подписать» — «Документы подписаны», в конце — история подписаний.
        tp_ecp_add: TpEcpAdd,
        tp_ecp_signed: TpEcpSigned,
        tp_ecp_history: TpEcpHistory,
        result: ResultScreen,
    },
    'photo-control-car': {
        intro: IntroScreen,
        result: ResultScreen,
        // Экраны pc_list и pc_camera — не экраны телефона, а телефон В РУКАХ
        // человека, стоящего у трёхмерной машины, поэтому они живут не здесь,
        // а в ветке world ниже.
    },
    'sapar-site-avr': {
        intro: IntroScreen,
        phone_home: PhoneHome,
        chrome_blank: ChromeBlank,
        chrome_address: ChromeAddress,
        sapar_guest: SaparGuest,
        eg_code: EgCode,
        eg_sign: EgSign,
        eg_success: EgSuccess,
        sapar_profile: SaparProfile,
        sapar_documents: SaparDocuments,
        sapar_sign_sheet: SaparSignSheet,
        sapar_save: SaparSave,
        sapar_status: SaparStatus,
        result: ResultScreen,
    },
};
