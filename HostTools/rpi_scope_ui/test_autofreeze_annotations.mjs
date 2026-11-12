import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';
import uniformTests from './test_scope_uniform.mjs';

const work=process.argv[2],fixture=JSON.parse(await fs.readFile(work+'/fixture.json','utf8'));
function script(page){
  const scripts=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]);
  for(const s of scripts)new vm.Script(s);
  const main=scripts.find(s=>s.includes('const MODE_LABELS'));
  const code=main.slice(main.indexOf('const MODE_LABELS')).split('\n').filter(line=>
    !line.trimStart().startsWith('initTheme();')&&!line.startsWith("document.addEventListener('visibilitychange'")&&
    !line.startsWith("window.addEventListener('pagehide'")).join('\n');
  return {code,main,scripts:scripts.length};
}
function harness(page){
  const window=new Window({url:'http://bmi30.test/portal-oscilloscope',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
  window.document.write(page.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  const texts=[];
  window.HTMLCanvasElement.prototype.getBoundingClientRect=()=>({width:1200,height:340,left:0,top:0,right:1200,bottom:340});
  window.HTMLCanvasElement.prototype.getContext=function(){
    const canvas=this,values={canvas,measureText:text=>({width:String(text).length*7}),fillText:text=>texts.push({canvas:canvas.id,text:String(text)})};
    return new Proxy(values,{get:(target,key)=>key in target?target[key]:(()=>{}),set:(target,key,value)=>(target[key]=value,true)});
  };
  const context=vm.createContext({window,document:window.document,fixture:structuredClone(fixture),
    performance:{now:()=>1000},getComputedStyle:window.getComputedStyle.bind(window),
    TextDecoder,URL,AbortController,setTimeout,clearTimeout,setInterval,clearInterval});
  const run=code=>vm.runInContext(code,context);
  run(script(page).code);
  const apply=()=>run(`
    lastStatus=fixture.status;currentWorkingWindow=lastStatus.working_window;freezeMode='auto';
    autoFreezeRequest={kind:'detection',ch:fixture.channel===0?'upper':'lower',seq:fixture.seq,
      baseline:{upper:fixture.seq,lower:fixture.seq},reviewBaseline:0};
    fixture.frame._reviewQuery='&fire_seq'+fixture.channel+'='+fixture.seq;
    applyFrame(fixture.frame,true);
  `);
  return {window,document:window.document,texts,apply,run};
}
const original=await fs.readFile(work+'/scope.html','utf8');
const old=harness(original);
assert.throws(()=>old.apply(),/reviewReason is not defined/,'Reproduce the failure with the actual common-mode event');
const page=await fs.readFile(work+'/candidate/scope.html','utf8'),h=harness(page);
h.apply();
for(const name of ['AMP:','S/Ninst:','POS:','BASE:','CONF:','CM:','FIRE'])assert(h.document.querySelector('#upperNorm').textContent.includes(name),name);
assert(!h.document.querySelector('#upperNorm').textContent.includes('⏱'));
assert(!h.document.querySelector('#lowerNorm').textContent.includes('⏱'));
const expected=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Prague',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(fixture.frame.event_snapshot.timestamp*1000));
assert.equal(h.document.querySelector('#upperCard h2').getAttribute('data-autofreeze-time'),expected);
assert.equal(h.document.querySelector('#lowerCard h2').getAttribute('data-autofreeze-time'),'');
assert(h.document.querySelector('#upperCard').classList.contains('autofreeze-hit'));
assert(!h.document.querySelector('#lowerCard').classList.contains('autofreeze-hit'));
assert(h.texts.some(x=>x.canvas==='upperPlot'&&x.text.startsWith('A:')));
assert(h.texts.some(x=>x.canvas==='lowerPlot'&&x.text.startsWith('A:')));
assert.equal(h.texts.filter(x=>x.text.startsWith('NOISE ')).length,2);
assert(h.texts.some(x=>x.text.startsWith('DETECT ')));
// Exercise every common-mode reason, including vetoes, with the real readout.
for(const reason of ['weak_neighbor','disabled','unpaired','pair_timeout','common_mode','residual_pass','same_lag_common_mode']){
 h.run(`fixture.frame.event_snapshot.common_mode=${JSON.stringify({enabled:true,reason,blocked:reason==='common_mode',residual:123,required:100})}`);
 assert.doesNotThrow(()=>h.run('detectorDecisionParts(fixture.frame.event_snapshot)'));
}
// Mirror the saved event to cover a lower-channel trigger without changing hardware.
h.run(`
 let originalSnap=fixture.frame.event_snapshot,originalRef=fixture.frame.reference_snapshot1;
 fixture.channel=1;fixture.seq++;fixture.frame={...fixture.frame,_noiseSnapshot:undefined,last_frame_t:fixture.frame.last_frame_t+1,
  event_snapshot:{...originalSnap,seq:fixture.seq},reference_snapshot0:originalRef,reference_snapshot1:null};
`);
h.apply();
assert.equal(h.document.querySelector('#lowerCard h2').getAttribute('data-autofreeze-time'),expected);
assert.equal(h.document.querySelector('#upperCard h2').getAttribute('data-autofreeze-time'),'');
assert(h.document.querySelector('#lowerNorm').textContent.includes('AMP:'));
h.run("setFreezeMode('freeze')");
assert.equal(h.document.querySelector('#lowerCard h2').getAttribute('data-autofreeze-time'),'');
const parsed=script(page);await fs.writeFile(work+'/candidate/scope-script.js',parsed.main);
const result={reproducedOldFailure:true,fullReadoutRestored:true,bothCanvasesPainted:true,noiseLines:2,
 headingTime:expected,timeOnlyOnTriggeredAntenna:true,commonModeReasons:7,syntaxScripts:parsed.scripts,
 uniform:await uniformTests(new URL('./scope_uniform.js',import.meta.url),work+'/candidate/scope-script.js')};
await fs.writeFile(work+'/javascript-tests.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
