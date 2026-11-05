/* No external dependencies; forms also work without JavaScript. */
(function () {
    'use strict';
    var busy = false;
    var selectedAt = 0;
    var noticeTimer;
    var wakeLock = null;
    var keepAwake = false;
    var eventForm = document.getElementById('event-form');
    var notice = document.getElementById('notice');
    var lastAction = null;

    function showNotice(message, persistent) {
        clearTimeout(noticeTimer);
        notice.textContent = message;
        notice.hidden = false;
        if (!persistent) noticeTimer = setTimeout(function () { notice.hidden = true; }, 7000);
    }
    function storageSet(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
    function updateClock() {
        var now = new Date();
        var clock = document.getElementById('clock');
        clock.textContent = now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
        var date = document.getElementById('today-date');
        if (date) date.textContent = now.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
        var times = document.querySelectorAll('time[datetime]');
        for (var i = 0; i < times.length; i++) {
            var seconds = Math.max(0, Math.floor((now - new Date(times[i].getAttribute('datetime'))) / 1000));
            if (seconds < 60) times[i].textContent = 'только что';
            else if (seconds < 3600) times[i].textContent = Math.floor(seconds / 60) + ' мин назад';
            else if (seconds < 86400) times[i].textContent = Math.floor(seconds / 3600) + ' ч назад';
            else if (seconds < 604800) times[i].textContent = Math.floor(seconds / 86400) + ' дн назад';
        }
    }
    updateClock();
    setInterval(updateClock, 30000);

    document.getElementById('theme-toggle').addEventListener('click', function () {
        var dark = document.documentElement.getAttribute('data-theme') !== 'dark';
        if (dark) document.documentElement.setAttribute('data-theme', 'dark');
        else document.documentElement.removeAttribute('data-theme');
        storageSet('dishwasher-theme', dark ? 'dark' : 'light');
    });

    function requestWakeLock() {
        if (keepAwake && navigator.wakeLock && document.visibilityState === 'visible') {
            navigator.wakeLock.request('screen').then(function (lock) {
                if (keepAwake) wakeLock = lock;
                else lock.release();
            }).catch(function () {});
        }
    }
    document.getElementById('fullscreen-toggle').addEventListener('click', function () {
        var root = document.documentElement;
        var active = document.fullscreenElement || document.webkitFullscreenElement;
        if (active) {
            keepAwake = false;
            var exit = document.exitFullscreen || document.webkitExitFullscreen;
            if (exit) exit.call(document);
            if (wakeLock) { wakeLock.release(); wakeLock = null; }
            return;
        }
        var enter = root.requestFullscreen || root.webkitRequestFullscreen;
        if (!enter) {
            showNotice('Добавьте сайт на главный экран через меню браузера, чтобы открыть его без вкладок.');
            return;
        }
        keepAwake = true;
        requestWakeLock();
        try {
            var result = enter.call(root);
            if (result && result.catch) result.catch(function () {
                keepAwake = false;
                if (wakeLock) { wakeLock.release(); wakeLock = null; }
                showNotice('Браузер не разрешил полноэкранный режим. Попробуйте добавить сайт на главный экран.');
            });
        } catch (e) { showNotice('Полноэкранный режим недоступен в этом браузере.'); }
        if (!navigator.wakeLock) showNotice('На весь экран. Чтобы планшет не засыпал, измените время блокировки в его настройках.');
    });
    function fullscreenChanged() {
        var active = document.fullscreenElement || document.webkitFullscreenElement;
        document.getElementById('fullscreen-toggle').setAttribute('aria-label', active ? 'Выйти из полноэкранного режима' : 'Открыть на весь экран');
        if (!active) {
            keepAwake = false;
            if (wakeLock) { wakeLock.release(); wakeLock = null; }
        }
    }
    document.addEventListener('fullscreenchange', fullscreenChanged);
    document.addEventListener('webkitfullscreenchange', fullscreenChanged);

    function resetSelection(message) {
        if (!eventForm || busy) return;
        var radios = eventForm.querySelectorAll('input[name="user"]');
        for (var i = 0; i < radios.length; i++) radios[i].checked = false;
        selectedAt = 0;
        if (message) document.getElementById('form-hint').textContent = message;
    }
    if (eventForm) {
        eventForm.addEventListener('change', function (event) {
            if (event.target.name === 'user') {
                selectedAt = Date.now();
                document.getElementById('form-hint').textContent = event.target.value + ', выберите выполненное действие.';
            }
        });
        var actionButtons = eventForm.querySelectorAll('button[name="action"]');
        for (var b = 0; b < actionButtons.length; b++) {
            actionButtons[b].addEventListener('click', function () { lastAction = this.value; });
        }
        eventForm.addEventListener('submit', function (event) {
            // Background tabs can suspend timers. Check the timeout again on submit.
            if (selectedAt && Date.now() - selectedAt > 45000) {
                event.preventDefault();
                resetSelection('Выбор сброшен после паузы. Выберите себя заново.');
                showNotice('После паузы нужно выбрать себя заново.');
            }
        });
        setInterval(function () {
            if (selectedAt && Date.now() - selectedAt > 45000) resetSelection('Выбор сброшен после паузы. Выберите себя заново.');
        }, 5000);
        // Browsers can restore radio state from the previous shared-device session.
        window.addEventListener('pageshow', function (event) {
            if (event.persisted) { window.location.reload(); return; }
            resetSelection('Сначала человек, потом действие. Всё просто.');
        });
    }

    if (window.fetch && window.URLSearchParams) {
        var forms = document.querySelectorAll('.async-form');
        for (var f = 0; f < forms.length; f++) {
            forms[f].addEventListener('submit', function (event) {
                if (event.defaultPrevented) return;
                event.preventDefault();
                if (busy) return;
                var form = this;
                var data = new URLSearchParams();
                var fields = form.querySelectorAll('input');
                for (var n = 0; n < fields.length; n++) {
                    if (fields[n].name && (fields[n].type !== 'radio' || fields[n].checked)) data.append(fields[n].name, fields[n].value);
                }
                if (form === eventForm) {
                    var action = event.submitter ? event.submitter.value : lastAction;
                    if (!action) { showNotice('Выберите действие.'); return; }
                    data.append('action', action);
                }
                busy = true;
                var buttons = document.querySelectorAll('.async-form button, #event-form input[type="radio"]');
                for (var k = 0; k < buttons.length; k++) buttons[k].disabled = true;
                showNotice('Сохраняем…', true);
                var controller = window.AbortController ? new AbortController() : null;
                var settled = false;
                function failed(message) {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timeout);
                    busy = false;
                    for (var k = 0; k < buttons.length; k++) buttons[k].disabled = false;
                    showNotice(message, true);
                }
                var timeout = setTimeout(function () {
                    if (controller) controller.abort();
                    failed('Не удалось подтвердить запись. Проверьте связь и повторите: дубля не будет.');
                }, 15000);
                var options = { method: 'POST', headers: { 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' }, body: data.toString() };
                if (controller) options.signal = controller.signal;
                fetch(form.getAttribute('action'), options).then(function (response) {
                    if (!response.ok) return response.json().then(function (error) { throw new Error(typeof error.detail === 'string' ? error.detail : 'Не удалось сохранить отметку.'); });
                    return response.json();
                }).then(function (result) {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timeout);
                    window.location.replace(result.redirect);
                }).catch(function (error) {
                    failed(error instanceof TypeError || error.name === 'AbortError' ? 'Не удалось подтвердить запись. Проверьте связь и повторите: дубля не будет.' : error.message);
                });
            });
        }
    }

    function setConnection(online) {
        document.getElementById('connection-dot').className = 'connection-dot' + (online ? '' : ' offline');
        document.getElementById('connection-label').textContent = online ? 'Связь с кухонной панелью есть' : 'Нет связи с сервером';
    }
    function sync() {
        if (!window.fetch || document.visibilityState === 'hidden' || busy) return;
        var controller = window.AbortController ? new AbortController() : null;
        var timeout = controller ? setTimeout(function () { controller.abort(); }, 8000) : null;
        fetch('/api/status', controller ? { cache: 'no-store', signal: controller.signal } : { cache: 'no-store' }).then(function (response) {
            if (!response.ok) throw new Error('unavailable');
            return response.json();
        }).then(function (state) {
            clearTimeout(timeout);
            setConnection(true);
            if (document.body.getAttribute('data-page') === 'home' && state.revision !== document.body.getAttribute('data-revision') && !selectedAt && !busy) window.location.reload();
        }).catch(function () { clearTimeout(timeout); setConnection(false); });
    }
    setInterval(sync, 30000);
    sync();
    window.addEventListener('online', sync);
    window.addEventListener('offline', function () { setConnection(false); });
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') resetSelection();
        else { requestWakeLock(); updateClock(); sync(); }
    });
    var heatmap = document.getElementById('heatmap-container');
    if (heatmap) {
        heatmap.scrollLeft = heatmap.scrollWidth;
        heatmap.addEventListener('click', function (event) {
            var label = event.target.getAttribute('data-day-label');
            if (label) document.getElementById('day-detail').textContent = label;
        });
    }
})();
