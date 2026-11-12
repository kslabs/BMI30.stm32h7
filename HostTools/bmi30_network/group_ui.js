// The RS485 builder remains the fallback. Fast rows join by hardware identity.
var _groupNetwork = {};
var _groupFast = {};
var _groupLastFast = {};
var _groupSettings = {};
var _groupNeighborLabels = {};
var _groupBusy = {};
var _groupLatest = {};
var _groupLastPoll = 0;
var _groupTransportBusy = false;
var _groupTransportState = null;
function _groupTransportText(en, ru) {
  // Keep a canonical source so changing languages remains reversible.
  return en;
}
function _groupLocalChannel() {
  var s = _groupFast.service_transports;
  if (!_groupFast.available || !s) { return 'Local · Service unavailable'; }
  if (s.mode === 'off') { return 'Local · Service off'; }
  var active = (s.active || []).map(function(m) {return m === 'bluetooth' ? 'Bluetooth' : 'Wi-Fi';});
  if (active.length) { return 'Local · ' + active.join(' + '); }
  var enabled = (s.allowed || []).map(function(m) {return m === 'bluetooth' ? 'Bluetooth' : 'Wi-Fi';});
  return 'Local · ' + enabled.join(' + ') + ' · No peer link';
}
function _groupRefreshTransport() {
  if (typeof document === 'undefined' || typeof document.querySelector !== 'function') {return;}
  var select = document.querySelector('[data-group-transport]');
  if (!select) {return;}
  var s = _groupFast.available && _groupFast.service_transports;
  if (!_groupTransportBusy) {
    _groupTransportState = s || null;
    select.disabled = !s;
    if (s) {select.value = s.mode;}
  }
  if (!select._transportBound) {
    select._transportBound = true;
    select.addEventListener('change', function() {_groupSetTransport(select.value);});
  }
  var label = document.querySelector('[data-group-transport-label]');
  if (label) {label.textContent = _groupTransportText('Service channel on this device', 'Служебная связь этого прибора');}
  Array.prototype.forEach.call(select.options, function(option) {
    if (option.value === 'both') {option.textContent = 'Wi-Fi + Bluetooth';}
    if (option.value === 'off') {option.textContent = _groupTransportText('Off', 'Выключено');}
  });
  var hint = document.querySelector('[data-group-transport-hint]');
  if (hint) {hint.textContent = _groupTransportText('Select on each device. Internet and page access stay connected.',
    'Выберите режим на каждом приборе. Интернет и доступ к странице сохраняются.');}
}
function _groupSetTransport(mode) {
  if (_groupTransportBusy || !_groupTransportState) {return Promise.resolve(false);}
  var select = document.querySelector('[data-group-transport]');
  var status = document.querySelector('[data-group-transport-result]');
  var revision = _groupTransportState.revision;
  _groupTransportBusy = true;
  if (select) {select.disabled = true;}
  if (status) {status.textContent = _groupTransportText('Saving…', 'Сохранение…');}
  var controller = new AbortController(), timer = setTimeout(function() {controller.abort();}, 5000);
  return fetch('/api/group-service-transports', {method:'POST', credentials:'same-origin', signal:controller.signal,
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:mode, revision:revision})})
    .then(function(response) {if (!response.ok) {throw new Error('HTTP');} return response.json();})
    .then(function(result) {
      if (result.service_transports) {_groupFast.service_transports = result.service_transports;}
      if (status) {status.textContent = result.ok ? _groupTransportText('Saved', 'Сохранено') :
        _groupTransportText('Not saved. Refresh and retry.', 'Не сохранено. Обновите страницу и повторите.');}
      return !!result.ok;
    }).catch(function() {
      if (status) {status.textContent = _groupTransportText('Result unknown. Refresh the page.', 'Результат неизвестен. Обновите страницу.');}
      return false;
    }).finally(function() {
      clearTimeout(timer); _groupTransportBusy = false; _groupRefreshTransport();
      if (typeof _pollSensors === 'function') {_pollSensors();}
    });
}
function _groupNow() { return performance.now(); }
// Identity is independent of telemetry freshness and RS485/IP addresses.
var _groupRpiIdentities = Object.create(null);
function _groupRpiId(value) {
  var text = String(value || '').trim().toUpperCase();
  var match = text.match(/^(?:BMI30-)?([0-9A-F]{9})$/);
  return match ? match[1] : '';
}
function _groupRpiHeaderLabel(source) {
  source = source || {};
  var rid = _groupRpiId(source.rpi_id || source.rpi_identifier);
  if (!rid) { return 'RPI —'; }
  var ip = String(source.ip_last || source.ip_octet || source.ip || source.address || '');
  var last = /^[0-9]+$/.test(ip) ? ip : (/^(?:\d+\.){3}\d+$/.test(ip) ? ip.split('.').pop() : '');
  return last ? rid + '/' + last : rid;
}
function _groupRememberRpiIdentity(d) {
  var rid = _groupRpiId(d.rpi_id);
  var uid = String(d.stm32_uid96 || '').toUpperCase();
  var fullKey = /^[0-9A-F]{24}$/.test(uid) && !/^0+$/.test(uid) ? 'uid96:' + uid : '';
  var shortId = String(d.stm32_short_id || (!fullKey && d.stm32_id) || '').toUpperCase();
  var shortKey = /^[0-9A-F]{9}$/.test(shortId) ? 'short:' + shortId : '';
  if (rid) {
    if (fullKey) { _groupRpiIdentities[fullKey] = rid; }
    if (shortKey) {
      var previous = _groupRpiIdentities[shortKey];
      // A colliding legacy short UID must never select another Raspberry Pi.
      _groupRpiIdentities[shortKey] = previous === undefined || previous === rid ? rid : '';
    }
  } else {
    rid = (fullKey ? _groupRpiIdentities[fullKey] : (shortKey && _groupRpiIdentities[shortKey])) || '';
  }
  d.rpi_id = rid;
  d.header_label = _groupRpiHeaderLabel(d);
}
function _groupKey(d) { return d._networkId || d.uid_key || d.rpi_id || d.stm32_id || ('node:' + d.node_id); }
function _groupAugment(devices) {
  _groupRefreshTransport();
  var now = _groupNow() / 1000;
  _groupLastPoll = _groupNow();
  var fast = _groupFast.available ? (_groupFast.nodes || []) : [];
  (_groupNetwork.nodes || []).forEach(function(n) {
    _groupRememberRpiIdentity({rpi_id:_groupRpiId(n.id), stm32_uid96:n.stm32_uid96,
      stm32_short_id:n.stm32_short_id});
  });
  devices.forEach(function(d) { d._channel = d.is_local ? _groupLocalChannel() : 'RS485'; d._canControl = !!d.is_local; d._validUntil = _groupNow()+2500; });
  fast.forEach(function(n) { if (n.age_s < 4) { _groupLastFast[n.id] = {node:n, seen:now}; } });
  Object.keys(_groupLastFast).forEach(function(id) {
    var remembered = _groupLastFast[id];
    if (now - remembered.seen > 60) { delete _groupLastFast[id]; delete _groupSettings[id]; delete _groupNeighborLabels[id]; return; }
    var n = fast.find(function(item) { return item.id === id && item.age_s < 4; });
    var last = n || remembered.node;
    var uid = String(last.stm32_uid96 || '').toUpperCase();
    var rid = _groupRpiId(id);
    if (!rid) { return; }
    var matching = devices.filter(function(item) {
      return (item.is_local && id === _groupFast.local_id) ||
        (uid && String(item.stm32_uid96 || '').toUpperCase() === uid) ||
        (rid && String(item.rpi_id || '').toUpperCase() === rid) ||
        (!item.stm32_uid96 && !item.rpi_id && last.stm32_short_id && item.stm32_id === last.stm32_short_id);
    });
    var d = matching.length === 1 ? matching[0] : null;
    if (!d) {
      var s = last.sync || {};
      d = {uid_key:'rpi:' + rid, rpi_id:rid, stm32_uid96:uid, stm32_id:last.stm32_short_id || uid.slice(-9),
        is_local:id === _groupFast.local_id, node_id:s.device_id, role:s.role, code:s.code,
        header_label:rid, ip:(last.via || []).length === 1 && last.transport !== 'bluetooth' ? last.address : '', _channel:'Offline', _canControl:false,
        optic_active:null, detadc1:null, detadc2:null, tx_enabled:null, online:null};
      devices.push(d);
    }
    // A matched STM32-only row must acquire the Raspberry Pi identity too.
    d.rpi_id = rid;
    _groupRememberRpiIdentity(d);
    d._networkId = id;
    d._net = (_groupNetwork.nodes || []).find(function(item) { return item.id === id; });
    var selectedPattern = (last.optic || {}).led_pattern_selected;
    d._ledPattern = Number.isInteger(selectedPattern) && selectedPattern >= 0 && selectedPattern <= 15 ? selectedPattern : null;
    if (!d.is_local) { d._opticSettingsKnown=false; d._reaction=null; d._neighborReaction=null; d._neighborDeviceId=undefined; }
    if (!n || !n.available) { d._fast = n || null; return; }
    d._fast = n;
    d._channel = d.is_local ? _groupLocalChannel() : (n.transport === 'bluetooth' ? 'Bluetooth' : 'Wi-Fi');
    d._canControl = true;
    d._validUntil = _groupNow() + Math.max(0, 4-n.age_s)*1000;
    var sync = n.sync || {}, optic = n.optic || {};
    d.role = sync.role || d.role;
    if (Number.isInteger(sync.device_id) && sync.device_id >= 0 && sync.device_id <= 31) { d.node_id = sync.device_id; }
    d.code = sync.code || ((d.role === 'master' ? 'M' : 'S') + _padNode2(d.node_id));
    var assigned = ['master','slave'].indexOf(sync.assigned_role) >= 0 ? sync.assigned_role : sync.role;
    if (['master','slave'].indexOf(assigned) >= 0) { d._syncRole = d._syncAssign = assigned; }
    d._rs485Id = d.node_id;
    if (typeof sync.device_id_assigned === 'boolean') { d._idAssigned = sync.device_id_assigned; }
    Object.keys(n.sensors || {}).forEach(function(k) { d[k] = n.sensors[k]; });
    var binding = optic.neighbor_device_id;
    if (typeof optic.reaction_enabled === 'boolean' && typeof optic.neighbor_reaction_enabled === 'boolean' &&
        Object.prototype.hasOwnProperty.call(optic,'neighbor_device_id') &&
        (binding === null || (Number.isInteger(binding) && binding >= 0 && binding <= 31))) {
      _groupSettings[id] = {reaction:optic.reaction_enabled, neighbor:optic.neighbor_reaction_enabled,
        device:binding, issued:now-n.age_s};
    }
    var settings = _groupSettings[id];
    d._opticSettingsKnown = !!settings && now-settings.issued < 4;
    d._reaction = d._opticSettingsKnown ? settings.reaction : null;
    d._neighborReaction = d._opticSettingsKnown ? settings.neighbor : null;
    d._neighborDeviceId = d._opticSettingsKnown ? settings.device : undefined;
    d._neighborOpticActive = !!optic.neighbor_reaction_enabled && !!optic.reaction_neighbor_active;
    d._holdSec = n.hold_ds == null ? null : Math.round(n.hold_ds / 10);
    var labels = _groupNeighborLabels[id] || (_groupNeighborLabels[id] = {});
    d._neighborOptions = (n.neighbors || []).map(function(option) {
      var known = typeof option.master === 'boolean' || (!Object.prototype.hasOwnProperty.call(option,'master') && /^[MS]\d\d$/.test(option.label || ''));
      if (known) { labels[option.node_id] = {label:option.label, issued:now-n.age_s}; }
      var previous = labels[option.node_id];
      return {node_id:option.node_id,label:known ? option.label :
        (previous && now-previous.issued < 4 ? previous.label : ('RS485 ' + _padNode2(option.node_id)))};
    });
  });
  // Apply authoritative roles before sorting; transient RS485 metadata must
  // not move hardware columns between polls.
  devices.sort(function(a,b) {
    var ar=a.role === 'master' ? 0 : 1, br=b.role === 'master' ? 0 : 1;
    if(ar !== br) {return ar-br;}
    if(a.is_local !== b.is_local) {return a.is_local ? -1 : 1;}
    return (a.node_id == null ? 99 : a.node_id)-(b.node_id == null ? 99 : b.node_id) || String(_groupKey(a)).localeCompare(String(_groupKey(b)));
  });
  devices.forEach(function(d) {
    // Internet probes do not depend on the detector's fast telemetry lane.
    var network = _groupNetwork.available ? (_groupNetwork.nodes || []) : [];
    var local = d.is_local && _groupNetwork.local;
    var matches = local ? [local] : network.filter(function(n) {
      return (d._networkId && n.id === d._networkId) ||
        (d.rpi_id && _groupRpiId(n.id) === _groupRpiId(d.rpi_id)) ||
        (d.stm32_uid96 && String(n.stm32_uid96 || '').toUpperCase() === String(d.stm32_uid96).toUpperCase()) ||
        (d.is_local && n.id === _groupNetwork.local_id);
    });
    if (matches.length === 1) { d._net = matches[0]; d._networkId = matches[0].id; }
    _groupRememberRpiIdentity(d);
    _groupLatest[_groupKey(d)] = d;
  });
  return devices;
}
function _groupNetworkText(d, field) {
  var n = d._net;
  var fresh = n && _groupNetwork.available && n.age_s < 60;
  if (field === 'network') {
    var text = d._channel;
    if (d._fast && d._fast.available) {
      if (!d.is_local) {
        var via = d._fast.via || [];
        var relay = via.length > 1 ? via[0] : (fresh && n.parent);
        text += relay ? ' · via ' + _groupCode(relay) : ' · Direct';
      }
      if (d._networkId === _groupNetwork.coordinator) { text += ' · Coordinator'; }
    } else if (d._networkId && !d.is_local && d._channel === 'RS485') { text += ' · Service unavailable'; }
    return text || 'Offline';
  }
  if (field === 'link') {
    if (!fresh) { return '—'; }
    var label = n.parent ? 'Via ' + _groupCode(n.parent) : (n.uplink === 'ethernet' ? 'Ethernet' : 'Router');
    if (n.uplink === 'wifi' && n.freq) { label += ' · ' + (n.freq >= 4900 ? '5 GHz' : '2.4 GHz') + ' · ' + n.signal + '%'; }
    return label;
  }
  if (field === 'internet') {
    if (!fresh || !n.internet || typeof n.internet.ok !== 'boolean') { return 'Unknown'; }
    if (!n.internet.ok) { return 'No internet'; }
    return Number.isFinite(n.internet.rtt_ms) ? 'Online · ' + Math.round(n.internet.rtt_ms) + ' ms' : 'Online';
  }
  var state = d._fast;
  if (field === 'led') {
    return d._ledPattern == null ? '—' : String(d._ledPattern);
  }
  if (!state || !state.available) { return '—'; }
  if (field === 'relay') { return !state.relay || !state.relay.available || state.relay.age_s > 5 ? 'Unknown' : (state.relay.active ? 'On' : 'Off') + (state.relay.enabled ? ' · Enabled' : ' · Disabled'); }
  if (field === 'led-output') { var optic=state.optic || {}; return _formatBool(optic.led_output_active) +
    (typeof optic.led_indication_enabled === 'boolean' ? (optic.led_indication_enabled ? ' · Enabled' : ' · Disabled') : ''); }
  if (field === 'sound') { return _formatBool((state.sound || {}).active); }
  if (field === 'fault') { return _formatBool(state.fault); }
  return '—';
}
function _groupCode(id) {
  var node = (_groupFast.nodes || []).find(function(n) { return n.id === id; });
  return node && node.sync && node.sync.code ? node.sync.code : id;
}
function _groupSend(key, action, params, td) {
  var d = _groupLatest[key];
  if (!d || !d._canControl || _groupNow() > d._validUntil || _groupBusy[key] || (action === 'optic_reaction' && d._opticSettingsKnown === false)) { return; }
  _groupBusy[key] = true;
  td._status.textContent = 'Applying…';
  td._status.classList.remove('is-error');
  _groupRefreshControls();
  var controller = new AbortController();
  var timer = setTimeout(function() { controller.abort(); }, 10500);
  fetch('/api/group-command', {method:'POST', credentials:'same-origin', signal:controller.signal,
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({target:d.is_local ? 'local' : d._networkId, action:action, params:params})})
    .then(function(response) { return response.json(); })
    .then(function(result) {
      if (!result.ok) { throw new Error(result.error || 'Command failed'); }
      td._status.textContent = 'Applied';
      setTimeout(function() { if (td._status.textContent === 'Applied') { td._status.textContent = ''; } }, 2000);
    })
    .catch(function(error) {
      td._status.textContent = error.name === 'AbortError' ? 'Not confirmed · refresh state' : error.message;
      td._status.classList.add('is-error');
    }).finally(function() { clearTimeout(timer); delete _groupBusy[key]; _groupRefreshControls(); });
}
function _groupChoice(d, kind, options, value, checkbox) {
  var td = document.createElement('td');
  td.className = 'group-dev-cell group-dev-ctl' + (d.is_local ? ' is-local' : '');
  td._key = _groupKey(d); td._kind = kind;
  var wrap = document.createElement('div'); wrap.className = 'group-role-control';
  var ctl = document.createElement(checkbox ? 'input' : 'select');
  if (checkbox) { ctl.type = 'checkbox'; ctl.checked = value === true; ctl.indeterminate = typeof value !== 'boolean'; }
  else {
    ctl.className = 'group-ctl-select';
    options.forEach(function(pair) { var opt = document.createElement('option'); opt.value=String(pair[0]); opt.textContent=pair[1]; ctl.appendChild(opt); });
    ctl.value = value == null ? '' : String(value);
  }
  ctl.setAttribute('aria-label', (d.code || d.rpi_id) + ' ' + kind);
  ctl.disabled = !d._canControl;
  var help={role:'Master sends RS485 synchronization; Slave follows it.',
    id:'Unique address on this RS485 bus, from 0 to 31.', hold:'Optical trigger hold after the input becomes inactive, from 0 to 10 seconds.',
    reaction:'Use this device\'s own optical input as its reaction gate.',
    neighbor:'Off ignores neighbor optics; Any accepts any current RS485 neighbor; a device name selects only that bus address.'};
  ctl.title = (help[kind] || '') + (d._canControl ? '' : ' Remote control requires a live BMI30 connection.');
  ctl.addEventListener('change', function() {
    var current = _groupLatest[td._key];
    if (!current) { return; }
    if (kind === 'role') { _groupSend(td._key, 'sync_mode', {role:ctl.value}, td); }
    if (kind === 'id') { _groupSend(td._key, 'rs485_id', {device_id:Number(ctl.value)}, td); }
    if (kind === 'hold') { _groupSend(td._key, 'optic_hold', {hold_ds:Number(ctl.value)*10}, td); }
    if (kind === 'reaction' || kind === 'neighbor') {
      var enabled = kind === 'reaction' ? ctl.checked : !!current._reaction;
      var neighbor = kind === 'neighbor' ? ctl.value : (!current._neighborReaction ? 'off' : (current._neighborDeviceId == null ? 'any' : String(current._neighborDeviceId)));
      _groupSend(td._key, 'optic_reaction', {enabled:enabled, neighbor_enabled:neighbor !== 'off', neighbor_device_id:neighbor === 'off' || neighbor === 'any' ? null : Number(neighbor)}, td);
    }
  });
  wrap.appendChild(ctl); td._ctl = ctl;
  var status = document.createElement('span'); status.className='group-role-feedback'; status.setAttribute('aria-live','polite');
  td._status=status; wrap.appendChild(status); td.appendChild(wrap); return td;
}
function _groupMakeSyncRoleCell(d) {
  return _groupChoice(d, 'role', [['master','Master'],['slave','Slave']], d._syncAssign || d.role);
}
function _groupMakeRs485IdCell(d) {
  return _groupChoice(d, 'id', Array.from({length:32},function(_,i) { return [i,_padNode2(i)]; }), d._idAssigned === false ? null : (d._rs485Id == null ? d.node_id : d._rs485Id));
}
function _groupNeighborValue(d) {
  return d._opticSettingsKnown === false ? 'unknown' : (!d._neighborReaction ? 'off' : (d._neighborDeviceId == null ? 'any' : String(d._neighborDeviceId)));
}
function _groupNeighborChoices(d) {
  var options = [['off','Off · ignore neighbor optics'],['any','Any current RS485 neighbor']];
  if(d._opticSettingsKnown === false) {options.unshift(['unknown','Unknown · settings unavailable']);}
  (d._neighborOptions || []).forEach(function(n) { options.push([n.node_id,n.label || ('RS485 ' + _padNode2(n.node_id))]); });
  if (d._neighborDeviceId != null && !options.some(function(p) { return p[0] === d._neighborDeviceId; })) {
    options.push([d._neighborDeviceId,'RS485 ' + _padNode2(d._neighborDeviceId) + ' · absent']);
  }
  return options;
}
function _groupMakeReactionCell(d, neighbor) {
  if (!neighbor) { return _groupChoice(d,'reaction',[],d._reaction,true); }
  return _groupChoice(d,'neighbor',_groupNeighborChoices(d),_groupNeighborValue(d));
}
function _groupUpdateNeighborChoices(ctl,d) {
  var choices=_groupNeighborChoices(d), sig=JSON.stringify(choices);
  if(ctl._optionsSig === sig) {return;}
  choices.forEach(function(pair,idx) {
    var value=String(pair[0]);
    var option=Array.from(ctl.options).find(function(o) {return o.value === value;});
    if(!option) {option=document.createElement('option'); option.value=value;}
    if(option.textContent !== pair[1]) {option.textContent=pair[1];}
    if(ctl.options[idx] !== option) {ctl.insertBefore(option,ctl.options[idx] || null);}
  });
  while(ctl.options.length > choices.length) {ctl.removeChild(ctl.options[choices.length]);}
  ctl._optionsSig=sig;
}
function _groupMakeHoldCell(d) {
  return _groupChoice(d,'hold',Array.from({length:11},function(_,i) { return [i,String(i)]; }),d._holdSec);
}
function _groupRefreshControls() {
  _groupCols.forEach(function(col) {
    Object.keys(col.cells).forEach(function(k) {
      var cell=col.cells[k]; if (!cell._ctl) { return; }
      var d=_groupLatest[cell._key];
      var unknown=d && (cell._kind === 'reaction' || cell._kind === 'neighbor') && d._opticSettingsKnown === false;
      cell._ctl.disabled = !d || !d._canControl || _groupNow() > d._validUntil || !!_groupBusy[cell._key] || unknown;
    });
  });
}
function _groupUpdateColumns(devices) {
  devices.forEach(function(d,idx) {
    var col=_groupCols[idx]; if (!col) { return; }
    if (col.head) {
      var code=col.head.querySelector('.group-dev-code'); if(code) {code.textContent=d.code || '??';}
      var badge=col.head.querySelector('.group-dev-badge');
      if (!badge) { badge=document.createElement('span'); badge.className='group-dev-badge'; col.head.appendChild(badge); }
      badge.textContent=_groupRpiHeaderLabel(d);
    }
    if(col.cells.indicator) {_groupSetState(col.cells.indicator,d);}
    if(col.cells.led) {col.cells.led._key=_groupKey(d);}
    ['network','link','internet','relay','led','led-output','sound','fault'].forEach(function(k) { if(col.cells[k]) {_groupSetText(col.cells[k],_groupNetworkText(d,k));} });
    if(col.cells.stm32) {_groupSetText(col.cells.stm32,d.stm32_id);}
    if(col.cells.ip) {_groupSetIpCell(col.cells.ip,d);}
    ['optic','detadc1','detadc2','tx','online'].forEach(function(k) {
      if(col.cells[k]) {_groupSetBool(col.cells[k],d[{optic:'optic_active',tx:'tx_enabled'}[k] || k]);}
    });
    Object.keys(col.cells).forEach(function(k) {
      var cell=col.cells[k],ctl=cell._ctl; if(!ctl || _groupBusy[cell._key] || document.activeElement === ctl) {return;}
      ctl.setAttribute('aria-label',(d.code || d.rpi_id) + ' ' + cell._kind);
      if(cell._kind === 'neighbor') {_groupUpdateNeighborChoices(ctl,d);}
      var values={role:d._syncAssign || d.role,id:d._rs485Id == null ? d.node_id : d._rs485Id,hold:d._holdSec,
        neighbor:_groupNeighborValue(d)};
      if(cell._kind === 'reaction') {ctl.checked=d._reaction === true; ctl.indeterminate=typeof d._reaction !== 'boolean';}
      else {ctl.value=values[cell._kind] == null ? '' : String(values[cell._kind]);}
    });
  });
  _groupRefreshControls();
}
function _groupCompositionSig(devices) {
  return devices.map(function(d) {
    return [_groupKey(d),d.is_local?'L':'R'].join(':');
  }).join('|');
}
setInterval(function() {
  if (_groupLastPoll && _groupNow()-_groupLastPoll > 2500) {
    _groupCols.forEach(function(col) {
      if(col.cells.network) {_groupSetText(col.cells.network,'Connection lost');}
      ['optic','detadc1','detadc2','tx','online'].forEach(function(k) {if(col.cells[k]) {_groupSetBool(col.cells[k],null);}});
      if(col.cells.led) {var d=_groupLatest[col.cells.led._key];
        _groupSetText(col.cells.led,d && d._ledPattern != null ? String(d._ledPattern) : '—');}
      ['link','internet','relay','led-output','sound','fault'].forEach(function(k) {if(col.cells[k]) {_groupSetText(col.cells[k],'Unknown');}});
    });
  }
  _groupRefreshControls();
},500);
