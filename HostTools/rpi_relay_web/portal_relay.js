    var relayRoot = document.querySelector('[data-relay-root]');
    var relayEnable = document.querySelector('[data-relay-enable]');
    var relayDuration = document.querySelector('[data-relay-duration]');
    var relayTest = document.querySelector('[data-relay-test]');
    var relayLevel = document.querySelector('[data-relay-level]');
    var relayState = {};
    var relayBusy = false;
    var relaySequence = null;
    var relayLastUpdate = 0;
    function updateRelay(cfg, force) {
      if (!relayRoot || (relayBusy && !force)) { return; }
      cfg = cfg || {};
      if (cfg.sequence !== undefined && cfg.sequence !== relaySequence) {
        relaySequence = cfg.sequence;
        relayLastUpdate = performance.now();
      }
      relayState = cfg;
      var available = cfg.available === true && performance.now() - relayLastUpdate < 2500;
      relayEnable.checked = !!(available ? cfg.enabled : cfg.saved_enabled);
      relayEnable.disabled = relayBusy || !available;
      if (force || document.activeElement !== relayDuration) {
        relayDuration.value = String(Number(cfg.duration_ms) || 1000);
      }
      relayDuration.disabled = relayBusy || !available;
      relayTest.disabled = relayBusy || !available || !cfg.enabled;
      relayTest.classList.toggle('is-active', !!cfg.test_enabled);
      relayTest.setAttribute('aria-pressed', cfg.test_enabled ? 'true' : 'false');
      relayTest.setAttribute('aria-label', cfg.test_enabled ? 'Stop relay test' : 'Test relay');
      relayTest.textContent = cfg.test_enabled ? 'Stop' : 'Test';
      relayLevel.textContent = available ? ('Relay: ' + (cfg.pc1 ? 'ON' : 'OFF')) : 'Relay: unavailable';
      relayLevel.title = available ? '' : 'No fresh relay state';
    }
    function sendRelay(change) {
      if (relayBusy) { return; }
      var previous = relayState;
      relayBusy = true;
      [relayEnable, relayDuration, relayTest].forEach(function(el) { el.disabled = true; });
      relayLevel.textContent = 'Applying…';
      fetch('api/relay', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, cache: 'no-store',
        body: JSON.stringify(change)
      }).then(function(response) {
        return response.json().then(function(data) {
          if (!response.ok || !data.ok) { throw new Error(data.message || 'Relay command failed'); }
          relayBusy = false;
          updateRelay(data.relay, true);
        });
      }).catch(function(error) {
        relayBusy = false;
        updateRelay(previous, true);
        relayLevel.textContent = 'Relay: error';
        relayLevel.title = error.message;
      });
    }
    if (relayRoot) {
      setInterval(function() {
        if (!relayBusy && performance.now() - relayLastUpdate >= 2500) { updateRelay(relayState, false); }
      }, 1000);
      relayEnable.addEventListener('change', function() {
        sendRelay({enabled: relayEnable.checked, persist: true});
      });
      relayDuration.addEventListener('change', function() {
        var duration = Number(relayDuration.value);
        if (!Number.isInteger(duration) || duration < 1 || duration > 2147483647) {
          relayDuration.reportValidity(); return;
        }
        sendRelay({duration_ms: duration, persist: true});
      });
      relayDuration.addEventListener('keydown', function(event) {
        if (event.key === 'Enter') { event.preventDefault(); relayDuration.blur(); }
      });
      relayTest.addEventListener('click', function() {
        sendRelay({test_enabled: !relayState.test_enabled, persist: false});
      });
    }
