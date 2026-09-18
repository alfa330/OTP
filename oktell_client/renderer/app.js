/*
 * Интерфейс клиента: вход, фон после входа и окно обязательной новости.
 *
 * Правила, которые здесь важны:
 *
 * · Выдержку кнопки «Ознакомлен» считает СЕРВЕР. Он присылает remaining_seconds
 *   и он же отвергает раннее подтверждение — наш таймер только рисует секунды.
 *   Считать «сколько прошло» самим нельзя: часы на машине оператора свои.
 *
 * · Крестика у обязательной новости нет вовсе, и окно не закрывается ни Esc, ни
 *   чем-либо ещё. Страница АТС на это время убрана главным процессом, так что
 *   «отложить на потом» нечем — это и есть требование постановки.
 *
 * · Верных ответов теста у нас нет: сервер их не отдаёт. Поэтому «перечитайте
 *   новость» мы не решаем сами, а показываем то, что ответил сервер.
 */
const screens = {
    login: document.getElementById('login'),
    idle: document.getElementById('idle'),
    news: document.getElementById('news'),
};

function show(name) {
    Object.entries(screens).forEach(([key, node]) => {
        node.classList.toggle('is-active', key === name);
    });
}

/* ── Вход ──────────────────────────────────────────────────────────────── */
const form = document.getElementById('login-form');
const loginInput = document.getElementById('login-input');
const passwordInput = document.getElementById('password-input');
const apiField = document.getElementById('api-field');
const apiInput = document.getElementById('api-input');
const loginButton = document.getElementById('login-button');
const loginNote = document.getElementById('login-note');

window.icore.config().then((config) => {
    if (!config.apiBaseUrl) {
        apiField.hidden = false;
        apiInput.value = '';
    }
});

form.addEventListener('submit', async (event) => {
    event.preventDefault();
    loginNote.textContent = '';
    loginNote.className = 'note';
    if (!loginInput.value.trim() || !passwordInput.value) {
        loginNote.textContent = 'Введите логин и пароль';
        loginNote.className = 'note error';
        return;
    }
    loginButton.disabled = true;
    loginButton.textContent = 'Входим…';
    try {
        const result = await window.icore.login({
            login: loginInput.value,
            password: passwordInput.value,
            apiBaseUrl: apiField.hidden ? '' : apiInput.value.trim(),
        });
        if (!result.ok) {
            loginNote.textContent = result.error === 'Invalid credentials'
                ? 'Неверный логин или пароль' : (result.error || 'Не удалось войти');
            loginNote.className = 'note error';
            return;
        }
        // Пароль в поле не держим ни секунды дольше нужного.
        passwordInput.value = '';
        showIdle(result);
    } catch (error) {
        loginNote.textContent = `Нет связи с iCORE: ${error.message}`;
        loginNote.className = 'note error';
    } finally {
        loginButton.disabled = false;
        loginButton.textContent = 'Войти';
    }
});

const idleTitle = document.getElementById('idle-title');
const idleNote = document.getElementById('idle-note');

function showIdle(result) {
    show('idle');
    const name = result?.user?.name || '';
    idleTitle.textContent = result?.oktell_opened
        ? 'Клиент Oktell открывается' : 'Вы вошли в iCORE';
    const lines = [];
    if (name) lines.push(name);
    if (result?.cabinet_login) lines.push(`Учётная запись АТС: ${result.cabinet_login}`);
    if (result?.cabinet_error) lines.push(result.cabinet_error);
    if (!result?.oktell_opened) lines.push('Адрес АТС не задан в настройках клиента.');
    idleNote.textContent = lines.join(' · ');
    idleNote.className = result?.cabinet_error ? 'note warn' : 'note';
}

window.icore.onAuthExpired(() => {
    show('login');
    loginNote.textContent = 'Сессия истекла — войдите заново';
    loginNote.className = 'note warn';
});

window.icore.onOktellFailed((info) => {
    idleTitle.textContent = 'АТС не открылась';
    idleNote.textContent = `${info?.description || 'Страница недоступна'}. `
        + 'Проверьте связь — клиент попробует снова при следующем входе.';
    idleNote.className = 'note error';
    show('idle');
});

window.icore.onAutologin((info) => {
    if (info?.filled) return;
    // Не ошибка, а предупреждение: войти руками оператор всё равно может.
    idleNote.textContent = `Учётную запись подставить не удалось (${info?.reason || 'причина неизвестна'}) `
        + '— введите логин и пароль в окне АТС вручную.';
    idleNote.className = 'note warn';
});

/* ── Обязательная новость ──────────────────────────────────────────────── */
const newsBadge = document.getElementById('news-badge');
const newsTitle = document.getElementById('news-title');
const newsBody = document.getElementById('news-body');
const newsPhotos = document.getElementById('news-photos');
const newsQuiz = document.getElementById('news-quiz');
const newsConfirm = document.getElementById('news-confirm');
const newsNote = document.getElementById('news-note');

let current = null;
let answers = {};
let tick = null;
let remaining = 0;

function renderQuiz(quiz) {
    newsQuiz.innerHTML = '';
    (quiz || []).forEach((item, index) => {
        const box = document.createElement('div');
        box.className = 'quiz-item';
        const prompt = document.createElement('p');
        prompt.textContent = `${index + 1}. ${item.prompt}`;
        box.appendChild(prompt);
        (item.options || []).forEach((option, optionIndex) => {
            const label = document.createElement('label');
            label.className = 'option';
            const input = document.createElement('input');
            input.type = 'radio';
            input.name = `q${item.id}`;
            input.addEventListener('change', () => {
                answers[item.id] = optionIndex;
                box.querySelectorAll('.option').forEach((node) => node.classList.remove('is-picked'));
                label.classList.add('is-picked');
                updateConfirm();
            });
            const text = document.createElement('span');
            text.textContent = option;
            label.append(input, text);
            box.appendChild(label);
        });
        newsQuiz.appendChild(box);
    });
}

function quizAnswered() {
    return (current?.quiz || []).every((item) => Number.isInteger(answers[item.id]));
}

function updateConfirm() {
    const waiting = remaining > 0;
    newsConfirm.disabled = waiting || !quizAnswered();
    newsConfirm.textContent = waiting ? `Ознакомлен · ${remaining}` : 'Ознакомлен';
}

window.icore.onNewsShow((item) => {
    current = item;
    answers = {};
    remaining = Number(item.remaining_seconds || 0);
    newsBadge.hidden = !item.is_mandatory;
    newsTitle.textContent = item.title || '';
    // Текст приходит уже очищенным на стороне портала — тем же, что показывает
    // окно на сайте. Своей чистки не заводим: две разные означали бы два разных
    // объявления из одного текста.
    newsBody.innerHTML = item.body || '';
    newsPhotos.innerHTML = '';
    (item.photos || []).forEach((photo) => {
        if (!photo?.url) return;
        const img = document.createElement('img');
        img.src = photo.url;
        img.alt = '';
        newsPhotos.appendChild(img);
    });
    renderQuiz(item.quiz);
    newsNote.textContent = '';
    newsNote.className = 'note';
    updateConfirm();
    show('news');
    newsBody.scrollTop = 0;

    if (tick) clearInterval(tick);
    if (remaining > 0) {
        tick = setInterval(() => {
            remaining = Math.max(0, remaining - 1);
            updateConfirm();
            if (remaining === 0) { clearInterval(tick); tick = null; }
        }, 1000);
    }
});

window.icore.onNewsHide(() => {
    if (tick) { clearInterval(tick); tick = null; }
    current = null;
    show('idle');
});

newsConfirm.addEventListener('click', async () => {
    if (!current) return;
    newsConfirm.disabled = true;
    const previous = newsConfirm.textContent;
    newsConfirm.textContent = 'Отправляем…';
    try {
        const result = await window.icore.confirmNews({ id: current.id, answers });
        if (result.ok) return;                       // окно закроет главный процесс
        if (result.code === 'NEWS_TOO_EARLY') {
            remaining = Number(result.remaining_seconds || 0);
            newsNote.textContent = 'Кнопка станет активной чуть позже';
            newsNote.className = 'note warn';
            if (tick) clearInterval(tick);
            tick = setInterval(() => {
                remaining = Math.max(0, remaining - 1);
                updateConfirm();
                if (remaining === 0) { clearInterval(tick); tick = null; }
            }, 1000);
        } else if (result.code === 'NEWS_QUIZ_WRONG') {
            // Какие именно вопросы неверны, сервер называет, но мы их не
            // подсвечиваем: подсказка превратила бы тест в перебор.
            answers = {};
            renderQuiz(current.quiz);
            newsNote.textContent = 'Есть неверные ответы — перечитайте новость и ответьте заново';
            newsNote.className = 'note error';
            newsBody.scrollTop = 0;
        } else {
            newsNote.textContent = result.error || 'Не удалось отправить подтверждение';
            newsNote.className = 'note error';
        }
    } catch (error) {
        newsNote.textContent = `Нет связи с iCORE: ${error.message}`;
        newsNote.className = 'note error';
    } finally {
        newsConfirm.textContent = previous.startsWith('Ознакомлен') ? previous : 'Ознакомлен';
        updateConfirm();
    }
});
