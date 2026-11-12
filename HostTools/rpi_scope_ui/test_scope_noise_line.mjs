import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile,writeFile} from 'node:fs/promises';
import uniformTests from './test_scope_uniform.mjs';

const folder=process.argv[2],page=await readFile(folder+'/candidate/scope.html','utf8');
const scripts=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]);
for(const script of scripts)new vm.Script(script);
const main=scripts.find(s=>s.includes('const MODE_LABELS'));
await writeFile(folder+'/candidate/scope-script.js',main);
const code=main.slice(main.indexOf('const MODE_LABELS')).split('\n').filter(line=>
  !line.trimStart().startsWith('initTheme();')&&!line.startsWith("document.addEventListener('visibilitychange'")&&
  !line.startsWith("window.addEventListener('pagehide'")).join('\n');
const context=vm.createContext({window:{addEventListener(){}},document:{getElementById:id=>({id})},
  performance:{now:()=>0},draws:[],levelLines:[],labels:[]});
vm.runInContext(code,context);
vm.runInContext(`actualDrawPlot=drawPlot;drawPlot=(...args)=>draws.push(args);
 updateNormReadouts=()=>{};applyModeVisibility=()=>{};setScopeLegend=()=>{};paintAutoFreezeSources=()=>{};
 lastStatus={mode:{selected:6},detector:{display_scale:10,noise_avg0:100,noise_avg1:200,trigger0:300,trigger1:600},normalization:{enabled:false}};
 f={available:true,last_frame_t:1,data0_even:[1,2,3],data0_odd:[3,2,1],prod0:[0,1000,0],
 data1_even:[4,5,6],data1_odd:[6,5,4],prod1:[0,2000,0]};renderFrame(f,true);`,context);
assert.equal(context.draws.length,2);
assert.equal(context.draws[0][18].level,10);assert.equal(context.draws[1][18].level,20);
assert.equal(context.draws[0][18].label,'NOISE 100');
vm.runInContext(`freezeMode='freeze';lastStatus.detector.noise_avg0=999;lastStatus.detector.trigger0=2997;renderFrame(f,true)`,context);
assert.equal(context.draws.at(-2)[18].label,'NOISE 100','Freeze keeps the measured raw level');
assert.equal(context.draws.at(-2)[12],30,'Freeze also keeps the corresponding threshold');
vm.runInContext(`lastStatus.normalization.enabled=true;f.prod_norm0={normalization_peak:1000};
 f.prod_norm1={normalization_peak:2000};renderFrame(f,true);`,context);
assert.equal(context.draws.at(-2)[18].level,6553.5);assert.equal(context.draws.at(-1)[18].level,6553.5);
const stamp=Date.UTC(2026,9,16,11,12,13)/1000;
for(const ch of ['upper','lower']){
  vm.runInContext(`freezeMode='auto';autoFreezeRequest={kind:'detection',ch:'${ch}',seq:7,
   baseline:{upper:7,lower:7},reviewBaseline:0};
   applyFrame({...f,_noiseSnapshot:undefined,last_frame_t:2,exact_event_frame:true,
    _reviewQuery:'&fire_seq${ch==='upper'?0:1}=7',event_snapshot:{seq:7,noise_avg:300,display_scale:10,timestamp:${stamp}},
    reference_snapshot${ch==='upper'?1:0}:{reference_only:true,noise_avg:400,display_scale:10,event_timestamp:${stamp},synchronized:false,context_frames:5,context_required:5}},true);`,context);
  const lines=context.draws.slice(-2).map(x=>x[18].label);
  assert.deepEqual(lines,ch==='upper'?['NOISE 300','NOISE 400']:['NOISE 400','NOISE 300']);
  assert.equal(vm.runInContext('autoFreezeRequest',context),null);
}
vm.runInContext(`snap={fired:true,noise_avg:300,display_scale:10,timestamp:${stamp}};parts=detectorDecisionParts(snap)`,context);
assert.equal(context.parts[0].text,'⏱ 13:12:13');assert.match(context.parts[0].tip,/16\/10\/2026/);
assert(!context.parts.some(x=>x.text==='FIRE'));
assert.equal(vm.runInContext(`detectorEventTimeParts({event_timestamp:${stamp}})[0].text`,context),'⏱ 13:12:13');
assert.equal(vm.runInContext('detectorEventTimeParts({}) .length',context),0);
for(const raw of [null,undefined,NaN,-1,Infinity]){
  context.testRaw=raw;
  assert.equal(vm.runInContext("detectorPlotNoise('upper',null,{_noiseSnapshot:{upper:{noise_avg:testRaw}}})",context),null);
}
assert.equal(vm.runInContext("detectorPlotNoise('upper',null,{_noiseSnapshot:{upper:{noise_avg:0}}}).level",context),0);
vm.runInContext(`resizeCanvas=()=>({w:600,h:300,d:1});
 for(const name of ['drawGrid','drawWorkingWindow','drawMarkerRanges','drawDetectionGate','drawXAxis','drawTrace',
  'drawConfirmationProducts','drawConfirmationPeakRows','drawBaseWidthGate'])eval(name+'=()=>{}');
 noiseSectorsHovered=()=>false;
 fakeCtx={save(){},restore(){},setLineDash(){},beginPath(){},moveTo(x,y){levelLines.push(y)},lineTo(){},stroke(){},fillRect(){}};
 scopeMeasureText=()=>({width:40});scopeFillText=(ctx,text)=>labels.push(text);
 actualDrawPlot({getContext:()=>fakeCtx},{style:{}},[0,1,2],[1,2,3],[3,2,1],[0,1000,0],
 '#1','#2','#3',null,[],'',10000,'THR 1000',null,[],null,null,{level:5000,label:'NOISE 500'});`,context);
assert.deepEqual([...context.labels],['THR 1000','NOISE 500']);
assert.equal(context.levelLines.length,2);assert(context.levelLines[1]>context.levelLines[0]);
assert.equal(vm.runInContext('fakeCtx.textAlign',context),'left');
assert(!page.includes('content:" AUTO FIRE";'));
assert(!page.includes("'DETECT '+Math.round(minimum)"));
const result={syntaxScripts:scripts.length,noiseSources:'raw reference noise; frozen event and both antennas',
  scalingAndFreeze:true,eventTime:'13:12:13 Europe/Prague',noDetectionCaption:true,
  realCanvasLines:true,uniform:await uniformTests(new URL('./scope_uniform.js',import.meta.url),folder+'/candidate/scope-script.js')};
await writeFile(folder+'/javascript-tests.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
