# -*- coding: utf-8 -*-
"""Номер WhatsApp найма: чистые правила (hiring/whatsapp.py) — разбор события сервиса шлюза, ключ
собеседника, комментарий и кампания карточки, состояние для «Интеграций», команды и тревоги.

Процесс на настоящем Postgres — tests/test_hiring_postgres.py (раздел «Номер WhatsApp найма»).
"""

from datetime import datetime, timedelta

from hiring import rules, wa_media, whatsapp

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


def test_from_ads_marks_only_ad_clicks():
    """Владелец 09.11.2026: «Те что пришли с таргета нужно автоматом отмечать как Таргет». Таргет — клик по
    объявлению Click-to-WhatsApp; пост без рекламы и ссылки на номер (с hh, сайта) — нет."""
    instagram = {'title': 'Напишите нам!', 'body': 'Требуются операторы', 'source_id': '120250535993810050',
                 'source_app': 'instagram', 'conversion_source': 'FB_Ads', 'entry_point': 'ctwa_ad', 'ctwa_clid': 'x'}
    assert whatsapp.from_ads(instagram)
    for ad in ({'ctwa_clid': 'x'}, {'source_type': 'ad'}, {'source_type': 'AD', 'title': 'Оператор'},
               {'conversion_source': 'FB_Ads'}, {'entry_point': 'ctwa_ad'}, {'source_id': '120208'},
               {'source_id': '120251238264490050', 'source_app': 'facebook'},
               {'source_id': '987', 'source_type': 'post', 'ctwa_clid': 'y'}):
        assert whatsapp.from_ads(ad), ad
    for ad in (None, {}, {'entry_point': 'phone_number_hyperlink'}, {'entry_point': 'message_short_link'},
               {'conversion_source': 'FB_Post'}, {'source_id': '987', 'source_type': 'post'},
               {'source_id': '987', 'conversion_source': 'FB_Post'}, {'title': 'Оператор — Алматы'}):
        assert not whatsapp.from_ads(ad), ad


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


def test_media_events_from_bridge():
    """Файлы (09.11.2026): шлюз помечает сообщение, чей файл поставил в очередь, и сообщает, если файл не
    сохранится; id сообщения приходит в пути запроса — только безопасные символы."""
    voice = whatsapp.parse_message(event(kind='voice', text='', media={'mime': 'audio/ogg; codecs=opus', 'size': 4096,
                                                                       'seconds': 7, 'fetch': True}))
    assert voice['media'] == {'mime': 'audio/ogg; codecs=opus', 'size': 4096, 'seconds': 7, 'fetch': True}
    assert 'fetch' not in whatsapp.parse_message(event(kind='image', media={'size': 10, 'fetch': 'yes'}))['media']
    assert whatsapp.parse_media_failure({'type': 'media_failed', 'id': '3EB0AA', 'reason': 'gone'}) == {'id': '3EB0AA', 'reason': 'gone'}
    assert whatsapp.parse_media_failure({'type': 'media_failed', 'id': '../x'}) is None
    assert whatsapp.parse_media_failure(event()) is None
    assert whatsapp.clean_message_id('AC853968D50DDD8CD1CFECC8713D35F8') == 'AC853968D50DDD8CD1CFECC8713D35F8'
    assert whatsapp.clean_message_id('a/b') is None and whatsapp.clean_message_id('x' * 129) is None


def test_media_storage_rules():
    assert wa_media.content_type('audio/ogg; codecs=opus') == 'audio/ogg'
    assert wa_media.content_type('image/svg+xml') == 'application/octet-stream'
    assert wa_media.content_type('text/html') == 'application/octet-stream'
    assert wa_media.content_type('') == 'application/octet-stream'
    assert wa_media.content_type('application/pdf') == 'application/pdf'
    path = wa_media.object_path(42, 'audio/ogg', None, NOW)
    assert path.startswith('hiring/whatsapp/2026/11/42-') and path.endswith('.ogg')
    # Имя файла кандидата в путь не идёт — только расширение, если тип незнакомый.
    odd = wa_media.object_path(7, 'application/x-odd', 'Резюме Иванова.RTF', NOW)
    assert odd.endswith('.rtf') and 'Иванов' not in odd
    assert wa_media.disposition('voice', 'audio/ogg', None) == 'inline'
    assert wa_media.disposition('image', 'image/jpeg', None) == 'inline'
    document = wa_media.disposition('document', 'application/pdf', 'Резюме "Иванова".pdf')
    assert document.startswith('attachment; filename="') and "filename*=UTF-8''" in document and '"Иванова"' not in document
    assert wa_media.disposition('image', 'image/tiff', 'scan.tiff').startswith('attachment')
    assert 'Правила:' in wa_media.system_prompt(type('Asr', (), {'SYSTEM': 'Вступление такси.\n\nПравила:\n1. Дословно.'}))
    assert 'такси' not in wa_media.system_prompt(type('Asr', (), {'SYSTEM': 'Вступление такси.\n\nПравила:\n1. Дословно.'}))


def test_media_view_states():
    signed = []

    def sign(path, ctype, disposition):
        signed.append((path, ctype, disposition))
        return 'https://storage.test/' + path

    stored = wa_media.public_view('voice', {'mime': 'audio/ogg; codecs=opus', 'seconds': 7, 'object': 'hiring/whatsapp/1.ogg',
                                            'stored_type': 'audio/ogg', 'stored_size': 5000, 'bucket': 'secret',
                                            'transcript': {'status': 'ready', 'text': 'Здравствуйте', 'cost': 0.001}}, sign)
    assert stored == {'mime': 'audio/ogg; codecs=opus', 'seconds': 7, 'size': 5000, 'state': 'stored', 'inline': True,
                      'url': 'https://storage.test/hiring/whatsapp/1.ogg',
                      'transcript': {'status': 'ready', 'text': 'Здравствуйте'}}
    assert signed == [('hiring/whatsapp/1.ogg', 'audio/ogg', 'inline')]
    assert wa_media.public_view('image', {'size': 10, 'fetch': True})['state'] == 'loading'
    assert wa_media.public_view('video', {'size': wa_media.MAX_BYTES + 1})['state'] == 'too_large'
    failed = wa_media.public_view('document', {'failed': 'gone', 'fetch': True})
    assert failed['state'] == 'failed' and failed['reason'] == 'файла у WhatsApp уже нет'
    # До 09.11.2026 шлюз файлы не качал — такие шлюз просит у телефона заново: тоже «загружается…»;
    # телефон так и не прислал — «не сохранился».
    assert wa_media.public_view('image', {'size': 10})['state'] == 'loading'
    assert wa_media.public_view('voice', None)['state'] == 'loading'
    unanswered = wa_media.public_view('image', {'size': 10, 'resend': {'at': '2026-11-10T11:00:00', 'attempts': 3},
                                                'failed': 'unanswered'})
    assert unanswered == {'size': 10, 'state': 'failed', 'reason': 'телефон не прислал файл'}
    assert wa_media.public_view('sticker', None) == {'state': 'none'}
    assert wa_media.public_view('image', {'object': 'x.jpg', 'stored_type': 'image/jpeg'}, lambda *a: None)['state'] == 'failed'


def test_resend_chat_and_anchor():
    """Просьба к телефону: третья — другим видом чата (номер ↔ LID), если он известен, остальные — видом
    чата в портале. Отсчёт истории — следующее сообщение, время — секунды Unix из времени Алматы."""
    lid_chat = ('205981234567890@lid', '+77055552233', '205981234567890')
    assert [wa_media.resend_chat(*lid_chat, attempt) for attempt in range(4)] == [
        '205981234567890@lid', '205981234567890@lid', '77055552233@s.whatsapp.net', '205981234567890@lid']
    phone_chat = ('77055552233@s.whatsapp.net', '+77055552233', '205981234567890')
    assert wa_media.resend_chat(*phone_chat, 2) == '205981234567890@lid'
    # Другого вида нет — всегда вид чата.
    assert wa_media.resend_chat('205981234567890@lid', None, '205981234567890', 2) == '205981234567890@lid'
    assert wa_media.resend_chat('77055552233@s.whatsapp.net', '+77055552233', None, 2) == '77055552233@s.whatsapp.net'
    at = whatsapp.moment_of(1792047600)
    assert wa_media.resend_anchor('3EB0A2', 1, at, rules.ALMATY) == {'id': '3EB0A2', 'from_me': True, 'ts': 1792047600}
    assert wa_media.resend_anchor(None, None, None, rules.ALMATY) is None
