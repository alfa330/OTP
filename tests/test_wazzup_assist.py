import pytest
from flask import Blueprint, Flask, jsonify
from wazzup import assist


def fixture(generate=lambda action, text: text, denied=False):
    assist._requests.clear()
    app = Flask(__name__)
    bp = Blueprint('draft_assist', __name__)
    def actor():
        return (None, (jsonify(error='denied'), 403)) if denied else ([42], None)
    assist.register_assist_routes(bp, actor, lambda fn: fn, lambda: ('', 204), generate=generate)
    app.register_blueprint(bp)
    return app.test_client()


def body(**updates):
    return dict(account='op', action='rewrite', text='Пришлите документ', **updates)


def test_auth_and_validation_before_provider():
    def never(*args):
        raise AssertionError('provider must not run')
    assert fixture(never, denied=True).post('/assist', json=body()).status_code == 403
    client=fixture(never)
    for data in (None, [], dict(account='potok'), dict(account='op', action='bad', text='text'),
                 dict(account='op', action='rewrite', text='@template: hidden'),
                 dict(account='op', action=[], text='text'), dict(account='op',action={},text='text'),
                 dict(account='op', action='rewrite', text='a'*4097)):
        assert client.post('/assist', json=data).status_code in (400,403)


def test_supported_actions_return_drafts_only_and_limit_frequency():
    calls=[]
    client=fixture(lambda action,text: calls.append(action) or text)
    for action in ('ru','kk','rewrite')*4:
        result=client.post('/assist', json={'account':'op','action':action,'text':'Пример 2500'})
        assert result.status_code == 200 and result.json['text'] == 'Пример 2500'
    assert client.post('/assist', json=body()).status_code == 429
    assert len(calls)==12


def test_preserves_sensitive_facts_and_does_not_expose_provider_errors():
    client=fixture(lambda *args:'Заплатите 5000')
    assert client.post('/assist', json=dict(account='op',action='rewrite',text='Заплатите 2500')).status_code==422
    def fail(*args):
        raise RuntimeError('private-provider-payload')
    result=fixture(fail).post('/assist', json=body())
    assert result.status_code==503
    assert 'private' not in result.text
    assert fixture().post('/assist',json=body()).status_code==200  # semaphore released


@pytest.mark.parametrize('response', ['Заплатите 25000', 'Заплатите 2500, потом 2500', 'Заплатите 500'])
def test_exact_numbers_are_preserved(response):
    client=fixture(lambda *args: response)
    assert client.post('/assist',json=dict(account='op',action='rewrite',text='Заплатите 2500')).status_code==422


def test_concurrent_capacity_rejects_without_waiting():
    assist._slots.acquire(); assist._slots.acquire()
    try:
        assert fixture().post('/assist', json=body()).status_code==429
    finally:
        assist._slots.release(); assist._slots.release()


KAZAKH_LETTERS = set('әғқңөұүһі')
KAZAKH_REPLY = 'Құжаттарды жіберуіңізді сұраймыз. Тексеріп, жауап береміз.'


def fake_vertex(monkeypatch, language='kk', reply=KAZAKH_REPLY):
    from wiki.ai import providers
    calls = []

    def generate(model, system, text, **limits):
        calls.append((model, system, text, limits))
        return {'text': language if system == assist._DETECT else reply, 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    monkeypatch.setenv('WAZZUP_DRAFT_AI_MODEL', 'fixture-model')
    return calls


def assert_kazakh_service_style(prompt):
    # Verify the instructions actually sent through the production provider
    # boundary. A model stub does not establish translation quality.
    assert 'умеренно официальный, спокойный и уважительный деловой тон' in prompt
    assert 'Всегда обращайся к клиенту на «Сіз»' in prompt
    assert 'Құжаттарды жіберуіңізді сұраймыз' in prompt
    assert 'Слово «өтініш» в значении заявления' in prompt
    assert 'редкие, архаичные' in prompt
    assert 'Имена, названия сервисов, номера, суммы, даты, телефоны, ссылки' in prompt
    assert 'Не добавляй приветствия, извинения, обещания' in prompt


def test_kazakh_translation_sends_the_service_style_with_translation_examples(monkeypatch):
    calls = fake_vertex(monkeypatch)
    draft = 'Пришлите, пожалуйста, документы. Мы проверим их и сообщим результат.'
    assert assist.transform('kk', draft) == KAZAKH_REPLY
    [(model, prompt, actual_draft, limits)] = calls
    assert model == 'fixture-model' and actual_draft == draft
    assert limits == {'max_tokens': 2400, 'timeout': 12}
    assert_kazakh_service_style(prompt)
    assert '«Пришлите, пожалуйста, документы» → «Құжаттарды жіберуіңізді сұраймыз»' in prompt


def test_kazakh_rewrite_detects_the_language_first_and_gets_no_translation_examples(monkeypatch):
    calls = fake_vertex(monkeypatch, 'kk')
    draft = 'кужаттарды жиберши, тексерип жауап беремиз'
    assert assist.transform('rewrite', draft) == KAZAKH_REPLY
    detect, rewrite = calls
    assert detect == ('fixture-model', assist._DETECT, draft, {'max_tokens': 32, 'timeout': 6})
    model, prompt, actual_draft, limits = rewrite
    assert model == 'fixture-model' and actual_draft == draft
    assert limits == {'max_tokens': 2400, 'timeout': 12}
    assert_kazakh_service_style(prompt)
    assert 'восстанови правильную казахскую кириллицу' in prompt
    # Russian -> Kazakh example pairs are what pulled Russian drafts into Kazakh.
    assert 'Пришлите' not in prompt and 'Подождите' not in prompt


@pytest.mark.parametrize(('language', 'draft'), [
    ('ru', 'Подождите пожалуйста'),
    ('mixed', 'Құжаттар дайын. Документы готовы'),
    ('other', 'Ok, thanks'),
])
def test_only_a_kazakh_draft_gets_the_kazakh_rules(monkeypatch, language, draft):
    calls = fake_vertex(monkeypatch, language, draft)
    assert assist.transform('rewrite', draft) == draft
    prompt = calls[1][1]
    assert prompt == assist._SYSTEM + '\n' + assist._REWRITE[language]
    assert 'Всегда обращайся к клиенту на «Сіз»' not in prompt
    assert 'не переводи' in prompt.lower()
    if language == 'ru':
        assert not KAZAKH_LETTERS & set(prompt.lower())


@pytest.mark.parametrize('language', ['ru', 'kk', 'mixed', 'other'])
def test_rewrite_keeps_digits_so_the_number_check_passes(language):
    # «5 минут» → «пять минут» and «один день» → «1 день» failed the route's number check.
    assert ('Числа пиши так же, как в исходнике: «5 минут», «1 заказ», «2 грузчика» — цифрами, '
            '«один день» — словами.') in assist._REWRITE[language]


def test_language_request_settles_greetings():
    # «Сәлеметсіз бе! Пришлите фото» went to Kazakh as "mixed", and
    # «Сәлеметсіз бе! 5 минут» went to Russian, before these two rules.
    assert 'приветствие или благодарность на другом языке не делают текст смешанным' in assist._DETECT
    assert 'язык черновика — язык приветствия или благодарности' in assist._DETECT


@pytest.mark.parametrize(('answer', 'language'), [
    ('kk', 'kk'), ('ru', 'ru'), ('`ru`.', 'ru'), ('MIXED\n', 'mixed'), ('other', 'other'),
    ('Казахский', 'other'), ('', 'other'), ('uz', 'other'), ('Сіз қай қаладасыз?', 'other'),
])
def test_detected_language_is_read_strictly(monkeypatch, answer, language):
    fake_vertex(monkeypatch, answer)
    assert assist.draft_language('черновик') == language


@pytest.mark.parametrize(('language', 'draft', 'reply'), [
    ('ru', 'Подождите пожалуйста', 'Күте тұруыңызды сұраймыз.'),
    ('ru', 'Рахмет за ожидание, ваши документы приняты', 'Күткеніңіз үшін рахмет, құжаттарыңыз қабылданды.'),
    ('kk', 'Құжаттарды жіберіңізші, тексеріп жауап береміз', 'Пришлите документы, мы проверим и ответим.'),
    ('kk', 'кужаттарды жиберши, тексерип жауап беремиз', 'Пришлите документы, мы проверим и ответим.'),
    ('ru', 'Сәлеметсіз бе, құрметті серіктес! Документы получили', 'Здравствуйте, уважаемый партнёр! Документы получили.'),
    ('other', 'Сіздің құжаттарыңыз дайын', 'Ваши документы готовы.'),
])
def test_rewrite_in_another_language_is_rejected_and_the_draft_kept(monkeypatch, language, draft, reply):
    fake_vertex(monkeypatch, language, reply)
    response = fixture(assist.transform).post('/assist', json=dict(account='op', action='rewrite', text=draft))
    assert response.status_code == 422
    assert response.json == {'error': 'ИИ изменил язык сообщения. Исходный текст сохранён, попробуйте ещё раз.'}


@pytest.mark.parametrize(('language', 'draft', 'reply'), [
    ('kk', 'кужаттарды жиберши', 'Құжаттарды жіберуіңізді сұраймыз.'),
    ('kk', 'жарайды', 'Жарайды.'),
    ('ru', 'Сәлеметсіз бе! Документы получили', 'Сәлеметсіз бе! Документы получены.'),
    ('ru', 'Здравствуйте, Әлия! Пришлите фото', 'Здравствуйте, Әлия! Пришлите, пожалуйста, фото.'),
    ('mixed', 'Құжаттар дайын. Документы готовы', 'Құжаттарыңыз дайын. Ваши документы готовы.'),
    ('other', 'ок', 'Хорошо.'),
])
def test_rewrite_in_the_same_language_passes(monkeypatch, language, draft, reply):
    fake_vertex(monkeypatch, language, reply)
    response = fixture(assist.transform).post('/assist', json=dict(account='op', action='rewrite', text=draft))
    assert response.status_code == 200 and response.json == {'text': reply}


@pytest.mark.parametrize(('draft', 'language'), [
    ('Құжаттарыңызды жіберіңіз', 'kk'), ('Сәлеметсіз бе! Документы получили', 'other'),
    ('кужаттарды жиберши', 'other'), ('2500', 'other'),
])
def test_failed_language_request_does_not_fail_the_rewrite(monkeypatch, draft, language):
    from wiki.ai import providers
    calls = []

    def generate(model, system, text, **limits):
        calls.append(system)
        if system == assist._DETECT:
            raise providers.ProviderError('HTTP 429', status=429)
        return {'text': draft, 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    assert assist.transform('rewrite', draft) == draft
    assert calls == [assist._DETECT, assist._SYSTEM + '\n' + assist._REWRITE[language]]


def test_vertex_overload_is_retried_once(monkeypatch):
    from wiki.ai import providers
    calls, sleeps = [], []
    failures = [providers.ProviderError('HTTP 429', status=429)]

    def generate(model, system, text, **limits):
        calls.append(system)
        if failures:
            raise failures.pop()
        return {'text': 'Пришлите документы', 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    monkeypatch.setattr(assist.time, 'sleep', sleeps.append)
    assert assist.transform('ru', 'Құжаттарды жіберіңіз') == 'Пришлите документы'
    assert len(calls) == 2 and sleeps == [1]
    for repeated in ([providers.ProviderError('HTTP 429', status=429)] * 2,
                     [providers.ProviderError('HTTP 500', status=500)]):
        calls.clear()
        failures[:] = repeated
        with pytest.raises(providers.ProviderError):
            assist.transform('ru', 'Құжаттарды жіберіңіз')
        assert len(calls) == len(repeated)


def test_russian_translation_keeps_its_existing_instructions(monkeypatch):
    from wiki.ai import providers
    calls = []

    def generate(model, system, text, **limits):
        calls.append((system, text))
        return {'text': 'Пришлите документы', 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    assert assist.transform('ru', 'Құжаттарды жіберуіңізді сұраймыз') == 'Пришлите документы'
    assert calls == [(assist._SYSTEM + '\nПереведи сообщение на русский язык.',
                      'Құжаттарды жіберуіңізді сұраймыз')]
    assert 'Всегда обращайся к клиенту на «Сіз»' not in calls[0][0]


@pytest.mark.parametrize('action', ['kk', 'rewrite'])
def test_kazakh_drafts_keep_data_protection_for_dates_amounts_contacts_and_links(action):
    draft = ('Айдос, 12.10.2026 күні 2500 теңге төлеуіңізді сұраймыз. '
             'https://example.invalid/pay/25 support@example.invalid +7 777 123 45 67')
    unchanged = fixture(lambda *_: draft).post('/assist', json={
        'account': 'op', 'action': action, 'text': draft})
    assert unchanged.status_code == 200
    assert unchanged.json['text'] == draft
    for old, new in [('12.10.2026', '13.10.2026'), ('2500', '5000'),
                     ('/pay/25', '/pay/26'), ('support@', 'another@'), ('123 45 67', '123 45 68')]:
        response = fixture(lambda *_, changed=draft.replace(old, new): changed).post('/assist', json={
            'account': 'op', 'action': action, 'text': draft})
        assert response.status_code == 422
        assert 'text' not in response.json
