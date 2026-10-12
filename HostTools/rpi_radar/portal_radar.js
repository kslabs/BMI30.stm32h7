(function () {
  'use strict';
  var root = document.querySelector('[data-radar-root]');
  if (!root) return;
  var port = root.querySelector('[data-radar-port]');
  var status = root.querySelector('[data-radar-status]');
  var busy = false, connected = false, loaded = false, timer = null;
  var fields = Array.from(root.querySelectorAll('[data-radar-setting]'));
  function show(result, reloadFields) {
    connected = !!result.connected;
    if (!port.value && result.port) port.value = result.port;
    var list = root.querySelector('#radar-ports');
    list.textContent = '';
    (result.ports || []).forEach(function (p) {
      var option = document.createElement('option'); option.value = p.port;
      option.label = p.description || p.port; list.appendChild(option);
    });
    var r = result.radar || {};
    var fault = ['OK', 'No echo', 'Sensor error', 'Measurements stopped'][r.fault] || 'Unknown fault';
    status.textContent = result.error || (connected ?
      (r.calibrating ? 'Calibrating: keep passage empty…' :
       !r.armed ? 'Calibration required before use' :
       r.fault ? 'XM125: ' + fault : r.occupied ? 'Obstacle detected' : 'Passage clear') : 'XM125: disconnected');
    status.classList.toggle('is-error', !result.ok || !!r.fault);
    root.querySelector('[data-radar-gap]').textContent = r.gap_mm ? (r.gap_mm / 10).toFixed(1) + ' cm' : '—';
    root.querySelector('[data-radar-nearest]').textContent = r.nearest_mm ? (r.nearest_mm / 10).toFixed(1) + ' cm' : '—';
    root.querySelector('[data-radar-gpio]').textContent = connected ? String(r.gpio0) : '—';
    if (r.config && (reloadFields || !loaded)) {
      fields.forEach(function (input) {
        input.value = r.config[input.dataset.radarSetting] / Number(input.dataset.radarScale || 1);
      }); loaded = true;
    }
    root.querySelector('[data-radar-apply]').disabled = !connected || busy;
    root.querySelector('[data-radar-calibrate]').disabled = !connected || busy;
    root.querySelector('[data-radar-save]').disabled = !connected || busy || !r.armed || !!r.calibrating || !!r.fault;
  }
  async function request(body, reloadFields) {
    if (busy) return;
    busy = true;
    try {
      var options = {cache: 'no-store', credentials: 'same-origin'};
      if (body) { options.method = 'POST'; options.headers = {'Content-Type': 'application/json'}; options.body = JSON.stringify(body); }
      var response = await fetch('api/radar', options);
      var result = await response.json();
      busy = false; show(result, reloadFields); return result;
    } catch (error) {
      busy = false; status.textContent = 'Radar connection: ' + error.message; status.classList.add('is-error');
    }
  }
  root.querySelector('[data-radar-connect]').addEventListener('click', function () {
    loaded = false; request({action: 'connect', port: port.value.trim()}, true);
  });
  root.querySelector('[data-radar-disconnect]').addEventListener('click', function () { request({action: 'disconnect'}); });
  function readSettings() {
    var settings = {};
    for (var input of fields) {
      if (!input.reportValidity()) return null;
      var value = Number(input.value) * Number(input.dataset.radarScale || 1);
      if (!Number.isSafeInteger(value)) { status.textContent = 'Use whole values for radar settings'; return null; }
      settings[input.dataset.radarSetting] = value;
    }
    if (settings.release_delta_mm >= settings.trigger_delta_mm) { status.textContent = 'Release difference must be smaller than trigger difference'; return null; }
    return settings;
  }
  root.querySelector('[data-radar-apply]').addEventListener('click', function () {
    var settings = readSettings(); if (settings) request({action: 'apply', settings: settings}, true);
  });
  root.querySelector('[data-radar-calibrate]').addEventListener('click', function () { request({action: 'calibrate'}); });
  root.querySelector('[data-radar-save]').addEventListener('click', async function () {
    var settings = readSettings(); if (!settings) return;
    var result = await request({action: 'apply', settings: settings}, true);
    if (result && result.ok) await request({action: 'save'});
  });
  function poll() {
    if (!document.hidden && root.closest('.portal-panel').classList.contains('is-active')) request(null, false);
    timer = setTimeout(poll, 500);
  }
  window.addEventListener('pagehide', function () { clearTimeout(timer); });
  request(null, false); timer = setTimeout(poll, 500);
}());
