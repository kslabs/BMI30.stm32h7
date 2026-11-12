(()=>{'use strict';
const button=document.getElementById('firmware-check-btn'),status=document.getElementById('firmware-check-status');
if(!button||!status)return;
const write=(element,source)=>window.BMI30I18n?window.BMI30I18n.text(element,source):(element.textContent=source);
let inFlight=false,timer=null,lastState=null;
function render(data){
 lastState=data;
 const cloud=data.cloud||{},operation=data.operation||{};
 write(button,'↻ Check updates');
 button.disabled=inFlight||!!cloud.checking||['starting','running'].includes(operation.status);
 if(cloud.checking){write(status,'Checking server…');return;}
 const stamp=cloud.checked_at?new Date(cloud.checked_at*1000).toLocaleTimeString(window.BMI30I18n?window.BMI30I18n.language:'en'):'';
 const error=/gaierror|name resolution|getaddrinfo/i.test(cloud.error||'')?'Server name lookup failed.':cloud.error;
 const result=error?'Check failed: '+error:
  cloud.available?'Version available: '+(cloud.version||''):
  cloud.checked_at?'No newer version found.':'Not checked yet.';
 write(status,(stamp?stamp+' · ':'')+result);
}
async function state(manual){
 const response=await fetch('api/firmware-control',{cache:'no-store',credentials:'same-origin'});
 if(!response.ok)throw Error('Could not read check status.');
 const data=await response.json();render(data);
 if(manual)window.dispatchEvent(new CustomEvent('bmi30-firmware-manual-state',{detail:data}));
 return data;
}
async function poll(){
 try{const data=await state(true);if(data.cloud&&data.cloud.checking)timer=setTimeout(poll,750);}
 catch(error){write(status,error.message);button.disabled=false;}
}
button.addEventListener('click',async()=>{
 if(inFlight||button.disabled)return;
 inFlight=true;button.disabled=true;write(status,'Requesting a fresh check…');
 try{
  const response=await fetch('api/firmware-check',{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json','X-BMI30-Firmware-Check':'1'},body:'{}'});
  const result=await response.json();if(!response.ok||!result.ok)throw Error(result.message||'Update check unavailable.');
  clearTimeout(timer);timer=setTimeout(poll,100);
 }catch(error){write(status,error.message);button.disabled=false;}
 finally{inFlight=false;}
});
window.addEventListener('bmi30-firmware-state',event=>render(event.detail));
window.addEventListener('bmi30-language-change',()=>{if(lastState)render(lastState);});
state(false).catch(()=>{});
})();
