// Keep an open page on the installed portal code after a software update.
var portalUIStamp=document.querySelector('meta[name="bmi30-portal-sha256"]');
var portalUILoadedHash=portalUIStamp ? portalUIStamp.content : '';
var portalUIDirtyForms=new Set(),portalUIReloading=false;
function portalUIEditing() {
  var active=document.activeElement;
  return portalUIDirtyForms.size>0 || !!(active && active.matches('input,select,textarea')) ||
    (typeof relayBusy!=='undefined' && relayBusy) ||
    (typeof nonAddressableLedBusy!=='undefined' && nonAddressableLedBusy) ||
    (typeof _groupBusy==='object' && Object.keys(_groupBusy).length>0);
}
function portalUIVersionCheck(hash) {
  if(portalUIReloading) {return true;}
  if(!/^[0-9a-f]{64}$/.test(portalUILoadedHash) || !/^[0-9a-f]{64}$/.test(hash || '') ||
     hash===portalUILoadedHash || portalUIEditing()) {return false;}
  portalUIReloading=true;window.location.reload();return true;
}
function portalUIMarkEdit(event) {
  var el=event.target;
  if(!event.isTrusted || !el || !el.form || !el.matches('input,select,textarea') ||
     el.closest('[data-relay-root],[data-nonaddr-led-root]') || el.hasAttribute('data-lcd-role-live')) {return;}
  portalUIDirtyForms.add(el.form);
}
document.addEventListener('input',portalUIMarkEdit);
document.addEventListener('change',portalUIMarkEdit);
document.addEventListener('submit',function(event) {portalUIDirtyForms.delete(event.target);});
