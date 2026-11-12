// Push every group change over one authenticated same-origin WebSocket.
// HTTP snapshots remain for baseline details and automatic fallback.
var groupLiveSocket=null, groupLiveLast=0, groupLiveRevision=0;
var groupLiveSnapshot=null, groupLiveDevice=null, groupLiveHTTPAt=0;
var groupLiveRetryAt=0,groupLiveFailures=0;
var groupLiveStatus=document.querySelector('[data-group-live-status]');
function groupLiveHealthy() {
  return groupLiveSocket && groupLiveSocket.readyState===1 && performance.now()-groupLiveLast<2500;
}
function groupLiveFallback() {
  return typeof _groupLatest === 'object' && Object.keys(_groupLatest).some(function(key) {
    return _groupLatest[key]._channel==='RS485';
  });
}
function groupLiveLabel() {
  if(groupLiveStatus) {groupLiveStatus.textContent=groupLiveHealthy() ? 'Live · WebSocket' : 'HTTP · reconnecting';}
}
function groupLiveApply(message) {
  if(!message || message.type !== 'group' || !message.group || !Array.isArray(message.group.nodes) ||
     !Number.isInteger(message.revision) || message.revision<=groupLiveRevision) {return;}
  groupLiveRevision=message.revision; groupLiveLast=performance.now(); groupLiveSnapshot=message.group;groupLiveFailures=0;
  if(groupLiveDevice) {
    groupLiveDevice.bmi30_group=groupLiveSnapshot;
    _updateGroupMatrix(groupLiveDevice);
    if(groupLiveFallback() && performance.now()-groupLiveHTTPAt>=500) {_pollSensors();}
    var own=groupLiveSnapshot.nodes.find(function(n) {return n.id===groupLiveSnapshot.local_id;});
    if(own && own.available && own.age_s<4) {
      _updateSensorTextEl('local-optic',_formatBool((own.sensors || {}).optic_active));
      _updateSensorTextEl('local-detadc1',_formatBool((own.sensors || {}).detadc1));
      _updateSensorTextEl('local-detadc2',_formatBool((own.sensors || {}).detadc2));
      if(own.relay && typeof updateRelay === 'function') {
        var relay=Object.assign({},own.relay);
        updateRelay(relay,false);
      }
    }
  }
  groupLiveLabel();
}
function groupLiveConnect() {
  if(document.hidden || performance.now()<groupLiveRetryAt || typeof WebSocket !== 'function' || (groupLiveSocket && groupLiveSocket.readyState<2)) {return;}
  var endpoint=new URL('api/group/live',window.location.href);
  endpoint.protocol=endpoint.protocol==='https:' ? 'wss:' : 'ws:';
  var ws;
  try {ws=new WebSocket(endpoint.href);} catch(error) {groupLiveRetryAt=performance.now()+5000;return;}
  groupLiveSocket=ws;groupLiveRevision=0;
  ws.onmessage=function(event) {
    if(groupLiveSocket!==ws) {return;}
    try {groupLiveApply(JSON.parse(event.data));} catch(error) {}
  };
  ws.onclose=function() {if(groupLiveSocket===ws) {groupLiveLast=0;groupLiveFailures++;
    groupLiveRetryAt=performance.now()+Math.min(5000,1000*groupLiveFailures);groupLiveLabel();}};
  ws.onerror=function() {ws.close();};
}
document.addEventListener('visibilitychange',function() {
  if(document.hidden && groupLiveSocket) {groupLiveSocket.close();}
  else {groupLiveConnect(); _pollSensors();}
});
window.addEventListener('pagehide',function() {if(groupLiveSocket) {groupLiveSocket.close();}});
window.setInterval(function() {
  if(groupLiveSocket && groupLiveSocket.readyState===1 && groupLiveLast && performance.now()-groupLiveLast>3000) {groupLiveSocket.close();}
  groupLiveLabel();groupLiveConnect();
},1000);
groupLiveConnect();
