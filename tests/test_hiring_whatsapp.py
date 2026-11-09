# -*- coding: utf-8 -*-
"""Номер WhatsApp найма: чистые правила (hiring/whatsapp.py) — разбор события сервиса шлюза, ключ
собеседника, комментарий и кампания карточки, состояние для «Интеграций», команды и тревоги.

Процесс на настоящем Postgres — tests/test_hiring_postgres.py (раздел «Номер WhatsApp найма»).
"""

from datetime import datetime, timedelta

from hiring import whatsapp

NOW = datetime(2026, 11, 6, 15, 0)


def event(**extra):
    base = {'type': 'message', 'id': '3EB0AA', 'chat': '77055552233@s.whatsapp.net', 'phone': '77055552233',
            'from_me': False, 'ts': 1794000000, 'push_name': 'Айгерим', 'kind': 'text', 'text': 'Здравствуйте'}
    base.update(extra)
    return base


def test_phone_rules_only_international_kz():
    assert whatsapp.kz_phone('77055552233') == '+77055552233'
    # У WhatsApp номер всегда с кодом страны: 10 цифр — это чужой номер, а не «+7» без кода.
    assert whatsapp.kz_phone('6791234567') is None
    assert whatsapp.kz_phone('998901234567') is None
    assert whatsapp.chat_key('77055552233', '123') == '+77055552233'
    assert whatsapp.chat_key('998901234567', None) == 'wa:998901234567'
    assert whatsapp.chat_key(None, '205981234567890') == 'lid:205981234567890'
    assert whatsapp.chat_key(None, None) is None


def test_parse_message_shapes():
    message = whatsapp.parse_message(event())
    assert message['key'] == '+77055552233' and message['phone'] == '+77055552233'
    assert message['at'] == datetime(2026, 11, 7, 2, 20)       # секунды Unix → наивное время Алматы (UTC+5)
    assert not message['from_me'] and message['kind'] == 'text' and message['push_name'] == 'Айгерим'
    hidden = whatsapp.parse_message(event(chat='205981234567890@lid', phone='', lid='205981234567890'))
    assert hidden['key'] == 'lid:205981234567890' and hidden['phone'] is None
    foreign = whatsapp.parse_message(event(phone='998901234567', chat='998901234567@s.whatsapp.net'))
    assert foreign['key'] == 'wa:998901234567' and foreign['phone'] is None
    # Не сообщение, без id или без собеседника — мимо.
    assert whatsapp.parse_message({'type': 'receipt'}) is None
    assert whatsapp.parse_message(event(id='')) is None
    assert whatsapp.parse_message(event(phone='', lid='')) is None
    # Незнакомый вид — «другое»; длинный текст и поля рекламы обрезаются, лишние поля рекламы отброшены.
    odd = whatsapp.parse_message(event(kind='poll', text='я' * 9000,
                                       ad={'title': 'Оператор КЦ', 'ctwa_clid': 'x' * 900, 'evil': 'drop'},
                                       media={'mime': 'image/jpeg', 'size': '2048', 'junk': 1}))
    assert odd['kind'] == 'other' and len(odd['text']) == 8000
    assert odd['ad'] == {'title': 'Оператор КЦ', 'ctwa_clid': 'x' * 500}
    assert odd['media'] == {'mime': 'image/jpeg', 'size': 2048}


def test_card_texts_from_message():
    plain = whatsapp.parse_message(event(text='Хочу работать оператором'))
    assert whatsapp.first_message_comment(plain) == 'WhatsApp: «Хочу работать оператором»'
    photo = whatsapp.parse_message(event(kind='image', text='резюме'))
    assert whatsapp.first_message_comment(photo) == 'WhatsApp: [фото] «резюме»'
    voice = whatsapp.parse_message(event(kind='voice', text=''))
    assert whatsapp.first_message_comment(voice) == 'WhatsApp: [голосовое]'
    long = whatsapp.parse_message(event(text='а' * 700))
    assert whatsapp.first_message_comment(long).endswith('…»') and len(whatsapp.first_message_comment(long)) < 520
    assert whatsapp.phone_reply_text(plain) == 'Ответ с телефона найма: «Хочу работать оператором»'
    assert whatsapp.phone_reply_text(voice) == 'Ответ с телефона найма: голосовое'
    assert whatsapp.phone_reply_text(long).endswith('…»') and len(whatsapp.phone_reply_text(long)) < 330


def test_campaign_tells_ads_apart():
    """Заголовок Click-to-WhatsApp — надпись кнопки, общая для всех объявлений («Напишите нам!» у всех
    39 карточек, сверка 09.11.2026): различает их только номер объявления, текст — для людей."""
    assert whatsapp.campaign_of(None) is None
    instagram = {'title': 'Напишите нам!', 'body': 'Требуются  операторы\nв отделы продаж', 'source_id': '120250535993810050',
                 'source_app': 'instagram', 'conversion_source': 'FB_Ads', 'entry_point': 'ctwa_ad', 'ctwa_clid': 'x'}
    assert whatsapp.campaign_of(instagram) == 'Instagram · объявление 120250535993810050 · Требуются операторы в отделы продаж'
    assert whatsapp.campaign_of({'title': 'Напишите нам!', 'source_id': '120251238264490050', 'source_app': 'facebook'}) \
        == 'Facebook · объявление 120251238264490050'
    assert whatsapp.campaign_of({'source_id': '120208'}) == 'Реклама · объявление 120208'
    assert len(whatsapp.campaign_of({'source_id': '1', 'body': 'я' * 400})) == 200
    # Без номера объявления — откуда пришёл человек.
    assert whatsapp.campaign_of({'entry_point': 'phone_number_hyperlink'}) == 'Ссылка на номер'
    assert whatsapp.campaign_of({'entry_point': 'message_short_link'}) == 'Короткая ссылка WhatsApp'
    assert whatsapp.campaign_of({'conversion_source': 'FB_Post'}) == 'Пост в Facebook или Instagram'
    assert whatsapp.campaign_of({'conversion_source': 'FB_Ads'}) == 'Реклама без номера объявления'
    assert whatsapp.campaign_of({'conversion_source': 'NEW_KIND'}) == 'NEW_KIND'
    assert whatsapp.campaign_of({'title': 'Оператор — Алматы'}) == 'Оператор — Алматы'


def test_clean_detail_keeps_only_known_fields():
    detail = whatsapp.clean_detail({'paired': 1, 'number': '+7 705 555 22 33', 'qr': 'q' * 1000, 'evil': 'x',
                                    'park': {'reason': 'temp_ban', 'code': '101', 'until': '2026-11-07T10:00:00', 'x': 1},
                                    'timelock': {'active': 1}})
    assert detail['paired'] is True and detail['number'] == '77055552233' and len(detail['qr']) == 600
    assert 'evil' not in detail and detail['park'] == {'reason': 'temp_ban', 'code': 101, 'since': None,
                                                         'until': '2026-11-07T10:00:00'}
    assert detail['timelock'] == {'active': True, 'until': None}
    assert whatsapp.clean_detail('junk') == {'paired': False}


def test_commands_by_state():
    paired = {'paired': True}
    assert whatsapp.can_command('pair', 'unpaired', {}) == (True, None)
    assert whatsapp.can_command('pair', 'connected', paired)[0] is False
    assert whatsapp.can_command('pair', 'pairing', {})[0] is False
    # Отвязан с телефона — новая привязка можно; остановлен по другой причине — сначала «Возобновить».
    assert whatsapp.can_command('pair', 'parked', {'park': {'reason': 'logged_out'}}) == (True, None)
    assert whatsapp.can_command('pair', 'parked', {'park': {'reason': 'pair_failed'}})[0] is False
    assert whatsapp.can_command('resume', 'parked', {'park': {'reason': 'temp_ban'}}) == (True, None)
    assert whatsapp.can_command('resume', 'parked', {'park': {'reason': 'duplicate'}})[0] is False
    assert whatsapp.can_command('resume', 'connected', paired)[0] is False
    assert whatsapp.can_command('logout', 'connected', paired) == (True, None)
    assert whatsapp.can_command('logout', 'unpaired', {})[0] is False
    assert whatsapp.can_command('pair', 'offline', {})[0] is False
    assert whatsapp.can_command('pair', 'not_configured', {}) == (False, 'Сервис на шлюзе ещё не запущен')
    assert whatsapp.can_command('send', 'connected', paired)[0] is False


def test_view_offline_qr_and_warnings():
    row = {'state': 'connected', 'last_run_at': NOW - timedelta(minutes=5),
           'cursor': {'paired': True, 'number': '77055552233'}, 'settings': {}}
    assert whatsapp.view(row, NOW)['state'] == 'offline'
    row['last_run_at'] = NOW - timedelta(seconds=20)
    row['cursor']['phone_seen_at'] = (NOW - timedelta(days=8)).isoformat()
    shown = whatsapp.view(row, NOW)
    assert shown['state'] == 'connected' and shown['tone'] == 'green' and shown['phone_seen_days'] == 8
    assert shown['warnings'] and 'Телефон' in shown['warnings'][0]
    assert shown['actions'] == {'pair': False, 'resume': False, 'logout': True}
    pairing = {'state': 'pairing', 'last_run_at': NOW, 'settings': {'command': {'action': 'pair'}},
               'cursor': {'qr': '2@abc', 'qr_at': (NOW - timedelta(seconds=10)).isoformat()}}
    assert whatsapp.view(pairing, NOW)['qr'] == '2@abc' and whatsapp.view(pairing, NOW)['pending'] == 'pair'
    pairing['cursor']['qr_at'] = (NOW - timedelta(minutes=3)).isoformat()
    assert whatsapp.view(pairing, NOW)['qr'] is None
    parked = {'state': 'parked', 'last_run_at': NOW,
              'cursor': {'paired': True, 'park': {'reason': 'temp_ban', 'code': 101, 'until': '2026-11-07T10:00:00'}}}
    text = whatsapp.view(parked, NOW)['reason']
    assert 'временно ограничил' in text and 'не из контактов' not in text and 'нет в контактах' in text and '2026-11-07 10:00' in text
    assert whatsapp.view(None, NOW)['state'] == 'not_configured'


def test_alerts_once_per_transition():
    detail = {'park': {'reason': 'replaced', 'since': '2026-11-06T15:00:00'}}
    key, title, body = whatsapp.alert_for('connected', 'parked', detail)
    assert key == 'wa:park:replaced:2026-11-06T15:00:00' and 'другом месте' in body
    assert whatsapp.alert_for('parked', 'parked', detail) is None
    assert whatsapp.alert_for('parked', 'connected', {'connected_since': '2026-11-06T16:00:00'})[0] == 'wa:back:2026-11-06T16:00:00'
    assert whatsapp.alert_for('connecting', 'connected', {}) is None
