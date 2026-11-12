(function () {
  'use strict';
  const root = document.getElementById('panel-privacy');
  if (!root || !root.querySelector('#privacy-zone-wifi') || document.getElementById('bmi30-access')) return;
  function section(zone, id, content) {
    const box = document.createElement('section');
    box.className = 'privacy-form'; box.id = id; box.innerHTML = content;
    root.querySelector('#privacy-zone-' + zone).append(box);
    return box;
  }
  const router = section('wifi', 'bmi30-access-router',
    '<h3>Сохранённые сети и автономный тест</h3>' +
    '<p id="bmi30-access-router-status" role="status" aria-live="polite">Проверяем связь…</p>' +
    '<label class="field"><span>Сохранённая сеть роутера</span><select id="bmi30-access-saved"></select></label>' +
    '<div class="privacy-access-actions">' +
    '<button type="button" class="link" data-access="forget">Забыть сеть роутера</button>' +
    '<button type="button" class="link" data-access="offline">Тест без роутера</button>' +
    '<button type="button" class="link" data-access="resume">Завершить тест</button></div>');
  const box = section('hotspot', 'bmi30-access',
    '<h3>Подключение и восстановление связи</h3>' +
    '<p>Портал работает без интернета. Короткое нажатие кнопки Raspberry восстанавливает связь. Детектор продолжает работать.</p>' +
    '<p id="bmi30-access-status" role="status" aria-live="polite">Проверяем связь…</p>' +
    '<div class="privacy-access-actions">' +
    '<button type="button" class="link" data-access="recover">Восстановить связь</button>' +
    '<a class="link" href="api/access/qr?kind=wifi" download="BMI30-WiFi.svg">QR подключения</a></div>' +
    '<label><input id="bmi30-access-power" type="checkbox"> Автоподбор мощности для одного пользователя</label>' +
    '<p>Мощность проверяется только в автономном режиме, при очень сильном сигнале и ошибках передачи. При ухудшении возвращается исходное значение.</p>');
  section('portal', 'bmi30-access-portal', '<div class="privacy-access-actions">' +
    '<a class="link" href="bmi30-connect.cmd" download="Open-BMI30.cmd">Открыть BMI30 — помощник Windows</a>' +
    '<a class="link" href="api/access/qr?kind=portal" download="BMI30-portal.svg">QR портала</a></div>');
  const statuses = [...root.querySelectorAll('#bmi30-access-status,#bmi30-access-router-status')];
  const saved = router.querySelector('select'), power = box.querySelector('input');
  const showStatus = message => statuses.forEach(status => {status.textContent = message;});
  async function request(path, body) {
    const r = await fetch(path, {method: body ? 'POST' : 'GET', credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json', 'X-BMI30-Access': '1'} : {},
      body: body ? JSON.stringify(body) : undefined});
    const data = await r.json();
    if (!r.ok || data.ok === false) throw new Error(data.error || 'Нет ответа. Подключитесь к Wi-Fi BMI30 повторно.');
    return data;
  }
  async function refresh() {
    try {
      const s = await request('api/access/status');
      let message = s.offline_mode ? 'Автономный тест: подключение к роутеру приостановлено.' :
        s.status === 'ready' ? 'Портал доступен. Интернет для управления не требуется.' : 'Связь проверяется и восстанавливается.';
      if (s.last_action && !s.last_action.ok) message += ' Последнее действие не завершено; повторите восстановление.';
      showStatus(message);
      const selected = saved.value; saved.replaceChildren();
      (s.saved_networks || []).forEach(name => {const o = document.createElement('option'); o.value = name; o.textContent = name; saved.append(o);});
      if ([...saved.options].some(o => o.value === selected)) saved.value = selected;
      power.checked = s.adaptive_power === true;
      router.querySelector('[data-access="forget"]').disabled = !saved.options.length;
    } catch (e) {showStatus(e.message);}
  }
  async function action(name, value) {
    const buttons = [...root.querySelectorAll('[data-access]')]; buttons.forEach(b => b.disabled = true);
    try {const r = await request('api/access/action', {action: name, value}); showStatus(r.message);}
    catch (e) {showStatus(e.message);}
    finally {setTimeout(() => {buttons.forEach(b => b.disabled = false); refresh();}, 12000);}
  }
  root.addEventListener('click', e => {
    const b = e.target.closest('[data-access]'); if (!b) return;
    const name = b.dataset.access;
    if (name === 'forget' && (!saved.value || !confirm('Забыть сеть «' + saved.value + '» на этом приборе и перейти в автономный тест? Пароль роутера потребуется ввести заново. Сеть BMI30 и настройки детектора сохранятся.'))) return;
    if (name === 'offline' && !confirm('Отключить прибор от роутера для проверки? Интернет и удалённый доступ к этому прибору прервутся. Подключитесь к его сети BMI30.')) return;
    action(name, name === 'forget' ? saved.value : null);
  });
  power.addEventListener('change', () => action('power', power.checked));
  refresh(); setInterval(() => {if (!document.hidden) refresh();}, 15000);
}());
