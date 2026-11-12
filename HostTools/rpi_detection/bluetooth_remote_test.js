/* T33 card feedback comes only from authenticated, sequenced device events. */
(function (global) {
  'use strict';
  if (global.BMI30RemoteTest) return;
  var root = document.querySelector('[data-bluetooth-root]');
  var panel = document.getElementById('panel-bluetooth');
  var states = Object.create(null), buttons = ['up', 'left', 'center', 'right', 'down'];
  var expectedMac = 'FF:24:01:26:A0:4A';
  var words = {
    title:['Remote buttons','Кнопки пульта'], start:['Test','Тест'], stop:['Stop','Стоп'],
    stopped:['',''], starting:['Starting…','Запуск…'], stopping:['Stopping…','Остановка…'],
    connected:['Connected','Подключён'], disconnected:['Not connected','Не подключён'],
    waiting:['Connected · input not ready','Подключён · ввод ещё не готов'],
    ready:['Press buttons on the remote','Нажимайте кнопки на пульте'],
    busy:['Input is busy with another test','Ввод занят другим тестом'],
    failed:['Test connection lost. Start again.','Связь с тестом потеряна. Начните снова.'],
    lost:['Input events were lost. Restart the test.','События ввода потеряны. Запустите тест заново.'],
    diagnostic:['Received an unassigned command','Получена команда без назначения'],
    other:['Other commands','Прочие команды'], last:['Last signal','Последний сигнал'],
    controller:['Remote controller','Пульт управления'], hidProfile:['Bluetooth HID','Bluetooth HID'],
    up:['Up','Вверх'], left:['Left','Влево'], center:['OK','OK'], right:['Right','Вправо'], down:['Down','Вниз']
  };
  function label(key) {
    var pair = words[key] || [key,key];
    return pair[/^ru\b/i.test(document.documentElement.lang || '') ? 1 : 0];
  }
  function escapeText(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function isHid(device) {
    if (!device) return false;
    var codes = Array.isArray(device.uuid_codes) ? device.uuid_codes : [];
    return device.hid_capable === true || codes.some(function(code) {
      return ['1812','1124'].indexOf(String(code).toLowerCase().replace(/^0x/,'')) >= 0;
    }) || /^input-(keyboard|mouse|gaming|tablet)/i.test(String(device.icon || ''));
  }
  function eligible(device) {
    return isHid(device) && (String(device.address || '').toUpperCase() === expectedMac
      || device.remote_test_eligible === true);
  }
  function state(address) {
    if (!states[address]) states[address] = {address:address, connected:false, running:false, busy:false,
      id:null, cursor:0, generation:0, counts:{up:0,left:0,center:0,right:0,down:0}, flashes:{},
      flashTimers:{}, diagnostic:0, status:'stopped', timer:0, tick:0, request:null,
      lastAge:null, lastReceipt:0, wasReady:false};
    return states[address];
  }
  function visible() {
    return !document.hidden && !!panel && panel.classList.contains('is-active') && document.body.contains(panel);
  }
  function card(device) {
    if (!eligible(device)) return '';
    var address = String(device.address).toUpperCase(), s = state(address);
    s.connected = device.connected === true;
    var icons = {up:'↑',left:'←',center:'OK',right:'→',down:'↓'};
    return '<aside class="bt-remote-test" data-bt-remote-root="'+escapeText(address)+'" translate="no">' +
      '<strong data-bt-remote-label="title">'+label('title')+'</strong>' +
      '<div class="bt-remote-cross" role="group" aria-label="'+label('title')+'">' + buttons.map(function(key) {
        return '<button type="button" disabled tabindex="-1" class="bt-remote-key bt-remote-key--'+key+'" '+
          'data-bt-remote-key="'+key+'" aria-label="'+label(key)+'" aria-pressed="false">'+icons[key]+
          '<small data-bt-remote-count="'+key+'">'+s.counts[key]+'</small></button>';
      }).join('') + '</div><div class="bt-remote-controls">' +
      '<button type="button" class="link" data-bt-remote-action="start">'+label('start')+'</button>' +
      '<button type="button" class="link link-secondary" data-bt-remote-action="stop">'+label('stop')+'</button></div>' +
      '<span class="bt-remote-status" data-bt-remote-status role="status" aria-live="polite"></span>' +
      '<span class="bt-remote-age" data-bt-remote-age></span><span class="bt-remote-other" data-bt-remote-other hidden></span></aside>';
  }
  function setText(element, text) { if (element && element.textContent !== text) element.textContent = text; }
  function widgets(s) {
    return root ? Array.prototype.slice.call(root.querySelectorAll('[data-bt-remote-root]')).filter(function(el) {
      return el.getAttribute('data-bt-remote-root') === s.address;
    }) : [];
  }
  function paint(s) {
    widgets(s).forEach(function(el) {
      el.querySelectorAll('[data-bt-remote-label]').forEach(function(node) {setText(node,label(node.dataset.btRemoteLabel));});
      var start = el.querySelector('[data-bt-remote-action="start"]'), stop = el.querySelector('[data-bt-remote-action="stop"]');
      setText(start,label('start')); setText(stop,label('stop'));
      start.disabled = !s.connected || s.running || s.busy || !visible();
      stop.disabled = !s.id && !s.busy;
      var testing = s.running || (s.busy && s.status === 'starting');
      start.classList.toggle('is-selected', testing);
      stop.classList.toggle('is-selected', !testing);
      start.setAttribute('aria-pressed', testing ? 'true' : 'false');
      stop.setAttribute('aria-pressed', testing ? 'false' : 'true');
      buttons.forEach(function(key) {
        var node = el.querySelector('[data-bt-remote-key="'+key+'"]');
        node.classList.toggle('is-received', !!s.flashes[key]);
        node.setAttribute('aria-pressed', s.flashes[key] ? 'true' : 'false');
        node.setAttribute('aria-label',label(key));
        setText(el.querySelector('[data-bt-remote-count="'+key+'"]'),String(s.counts[key]));
      });
      setText(el.querySelector('[data-bt-remote-status]'),label(!s.connected ? 'disconnected' : s.status));
      var age = s.lastAge == null ? '—' : Math.max(0,s.lastAge+(performance.now()-s.lastReceipt)/1000).toFixed(1)+' s';
      setText(el.querySelector('[data-bt-remote-age]'),label('last')+': '+age);
      var other = el.querySelector('[data-bt-remote-other]'); other.hidden = !s.diagnostic;
      setText(other,label('other')+': '+s.diagnostic);
    });
  }
  function paintAll() {
    Object.keys(states).forEach(function(key) {
      var s = states[key];
      if ((s.running || s.busy) && (!visible() || !widgets(s).length)) stop(s,'stopped');
      paint(s);
    });
    document.querySelectorAll('[data-bt-hid-label]').forEach(function(el) {setText(el,label(el.dataset.btHidLabel));});
  }
  function clearTimers(s) {
    clearTimeout(s.timer); clearTimeout(s.tick); s.timer=s.tick=0;
    buttons.forEach(function(key) {clearTimeout(s.flashTimers[key]); delete s.flashes[key];});
  }
  function postStop(id) {
    if (!id) return Promise.resolve();
    return fetch('api/bluetooth/remote-test/stop', {method:'POST', cache:'no-store', credentials:'same-origin',
      keepalive:true, headers:{'Content-Type':'application/json','X-BMI30-Bluetooth-Test':'1'},
      body:JSON.stringify({test_id:id})}).catch(function() {});
  }
  function stop(s, status) {
    var id = s.id; s.id=null; s.running=false; s.generation++;
    if (s.request) {s.request.abort();s.request=null;}
    clearTimers(s); s.status=status || 'stopped';
    if (!id) {s.busy=false;paint(s);return;}
    s.busy=true;paint(s);
    postStop(id).finally(function() {s.busy=false;paint(s);});
  }
  function fail(s, message) { stop(s,message || 'failed'); }
  function flash(s, key) {
    s.flashes[key]=true; clearTimeout(s.flashTimers[key]);
    s.flashTimers[key]=setTimeout(function() {delete s.flashes[key];paint(s);},320);
  }
  function consume(s, data) {
    if (!s.id || data.test_id !== s.id) {fail(s,'failed');return false;}
    var serverStatus=String(data.status||'').toLowerCase();
    if(['disconnected','error','busy','expired','stopped','unknown'].indexOf(serverStatus)>=0) {
      if(serverStatus==='disconnected')s.connected=false;
      fail(s,serverStatus==='busy'?'busy':serverStatus==='disconnected'?'disconnected':'failed');return false;
    }
    if(s.wasReady && data.connected===false){s.connected=false;stop(s,'disconnected');return false;}
    var next=Number(data.cursor);
    if (!Number.isSafeInteger(next) || next < 0) {fail(s,'lost');return false;}
    if (next < s.cursor) return true; // A delayed old snapshot cannot replay buttons.
    if (Number(data.lost_events || 0)>0 || Number(data.history_start_seq || 0)>s.cursor+1) {fail(s,'lost');return false;}
    var rows=Array.isArray(data.events)?data.events.slice():[];
    rows.sort(function(a,b) {return Number(a.seq)-Number(b.seq);});
    var previous=s.cursor;
    for (var i=0;i<rows.length;i++) {
      var row=rows[i], seq=Number(row.seq);
      if (!Number.isSafeInteger(seq) || seq<1 || seq>next) {fail(s,'lost');return false;}
      if (seq<=previous) continue;
      if (seq!==previous+1) {fail(s,'lost');return false;}
      previous=seq;
    }
    if (previous!==next) {fail(s,'lost');return false;}
    if(data.connected===true)s.connected=true;
    if(data.connected===true&&data.ready===true)s.wasReady=true;
    s.status=serverStatus==='starting'?'starting':data.ready===true?'ready':'waiting';
    rows.forEach(function(row) {
      var seq=Number(row.seq); if(seq<=s.cursor) return;
      var now=Number(data.server_at), at=Number(row.at);
      var age=Number.isFinite(now)&&Number.isFinite(at)?Math.max(0,now-at):null;
      s.lastAge=age;s.lastReceipt=performance.now();
      if (s.connected && data.ready===true && row.complete===true && row.phase==='complete' && buttons.indexOf(row.button)>=0) {
        s.counts[row.button]++;
        if (age!=null && age<=1.5 && now>=at-.05) flash(s,row.button);
      } else if (row.complete!==true || row.button==null) {
        s.diagnostic++;s.status='diagnostic';
      }
      s.cursor=seq;
    });
    s.cursor=next;
    paint(s);return true;
  }
  function tick(s,generation) {
    if (!s.running || s.generation!==generation) return;
    paint(s);s.tick=setTimeout(function(){tick(s,generation);},250);
  }
  function poll(s,generation) {
    if (!s.running || s.generation!==generation || !visible()) {if(s.running)stop(s,'stopped');return;}
    var controller=new AbortController();s.request=controller;
    fetch('api/bluetooth/remote-test/events?test_id='+encodeURIComponent(s.id)+'&cursor='+s.cursor,
      {cache:'no-store',credentials:'same-origin',signal:controller.signal,headers:{'X-BMI30-Bluetooth-Test':'1'}})
      .then(function(response) {return response.json().then(function(data) {
        if(!response.ok||!data.ok) throw new Error('poll failed');return data;
      });})
      .then(function(data) {if(s.generation===generation&&s.running)consume(s,data);})
      .catch(function(error) {if(s.generation===generation&&s.running)fail(s,'failed');})
      .finally(function() {
        if(s.request===controller)s.request=null;
        if(s.generation===generation&&s.running)s.timer=setTimeout(function(){poll(s,generation);},100);
      });
  }
  function start(s) {
    if (!visible() || !s.connected || s.running || s.busy) return;
    Object.keys(states).forEach(function(key){if(states[key]!==s&&(states[key].running||states[key].busy))stop(states[key],'stopped');});
    s.busy=true;s.status='starting';s.generation++;var generation=s.generation;
    s.cursor=0;s.diagnostic=0;s.lastAge=null;s.lastReceipt=0;s.wasReady=false;
    buttons.forEach(function(key){s.counts[key]=0;});paint(s);
    fetch('api/bluetooth/remote-test/start',{method:'POST',cache:'no-store',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-BMI30-Bluetooth-Test':'1'},body:JSON.stringify({mac:s.address})})
      .then(function(response) {return response.json().then(function(data) {
        if(!response.ok||!data.ok)throw new Error(String(data.status||data.error||'failed'));
        return data;
      });})
      .then(function(data) {
        if(s.generation!==generation||!visible()) {postStop(data.test_id);return;}
        if(typeof data.test_id!=='string'||!data.test_id||!Number.isSafeInteger(Number(data.cursor)))throw new Error('failed');
        s.id=data.test_id;s.cursor=0;s.running=true;s.busy=false;
        if(!consume(s,data))return;
        tick(s,generation);poll(s,generation);
      })
      .catch(function(error) {if(s.generation===generation){s.busy=false;s.status=/busy|occupied/i.test(String(error))?'busy':'failed';paint(s);}});
  }
  global.BMI30RemoteTest={card:card,isHid:isHid,label:label,refresh:paintAll};
  if (!root || !panel) return;
  root.addEventListener('click',function(event) {
    var button=event.target.closest&&event.target.closest('[data-bt-remote-action]');
    if(!button||button.disabled)return;
    var holder=button.closest('[data-bt-remote-root]');if(!holder)return;
    var s=states[holder.getAttribute('data-bt-remote-root')];if(!s)return;
    if(button.dataset.btRemoteAction==='start')start(s);else stop(s,'stopped');
  });
  function leave(){Object.keys(states).forEach(function(key){stop(states[key],'stopped');});}
  document.addEventListener('visibilitychange',function(){if(document.hidden)leave();else paintAll();});
  global.addEventListener('pagehide',leave);global.addEventListener('beforeunload',leave);
  global.addEventListener('bmi30-language-change',paintAll);
  new MutationObserver(paintAll).observe(panel,{attributes:true,attributeFilter:['class']});
})(window);
