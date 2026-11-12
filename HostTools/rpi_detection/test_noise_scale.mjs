import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

export async function checkNativeReadouts(coreFile, beforeFile, options={}) {
  const core = await readFile(coreFile, 'utf8');
  const before = JSON.parse(await readFile(beforeFile, 'utf8'));
  const context = vm.createContext({
    lastStatus: before.status, freezeMode:'live', playerState:{playback:false},
    autoFreezeDiagnostics:{}, normalizationEnabled:()=>false,
    reviewReason:()=>'',
  });
  const names = ['clampDetectorThreshold','clampDetectorDisplayLevel','detectorThresholdText',
    'detectorAutoTriggerDisplay','detectorDisplayScale','detectorDisplayValue',
    'detectorPlotSnapshot','detectorPlotThreshold','detectorPlotThresholdLabel',
    'detectorLevelText','detectorSnrText','detectorNoiseHtml','detectorRawValue',
    'detectorLevelNumber','detectorRatioText','detectorDecisionParts'];
  for (const name of names) {
    const start = core.indexOf('function '+name+'(');
    const end = core.indexOf('\nfunction ', start + 1);
    assert(start > 0 && end > start, name);
    vm.runInContext(core.slice(start, end), context, {filename:name+'.js'});
  }
  const det = before.status.detector;
  const snap = det.fire_snapshot0;
  const results = [];
  const integer = !!options.integer;
  for (const [value, expected] of [[0,'0'],[1,'1'],[7140,'7140'],[36952,'36952'],
    [1073741824,'1073741824'],[3744.95,integer?'3745':'3744.95'],[.01,integer?'<1':'0.01'],[1e-8,integer?'<1':'1.000000e-8']]) {
    assert.equal(context.detectorLevelText(value, det),expected);
    results.push({value, text:expected});
  }
  const parts = context.detectorDecisionParts({...snap,fired:true});
  const amplitude = parts.find(p=>p.text.startsWith('AMP:'));
  assert(amplitude.text.includes(String(snap.level)));
  assert(amplitude.text.includes(String(snap.trigger)));
  const instant = parts.find(p=>p.text.startsWith('S/Ninst:'));
  assert(instant.tip.includes(String(snap.noise_ref)));
  assert(!instant.tip.includes(' / 0 ='));
  assert(parts.find(p=>p.text.startsWith('N:')).text.includes(String(integer?Math.round(snap.noise_avg):snap.noise_avg)));
  const zeroParts = context.detectorDecisionParts({...snap,noise_ref:0,instant_snr:Infinity,instant_noise_ok:false});
  assert(zeroParts.some(p=>p.text==='S/Ninst:invalid noise ✗'&&p.className==='fail'));
  assert(!zeroParts.some(p=>p.text.includes('∞')));
  const text = context.detectorNoiseHtml(snap.level,snap.noise_avg,snap.trigger,snap.level/snap.noise_avg,det);
  assert(text.includes('p/n/t/SN')&&text.includes(String(snap.level))&&text.includes(String(integer?Math.round(snap.noise_avg):snap.noise_avg)));
  const rawTrigger = det.trigger0;
  assert(context.detectorPlotThresholdLabel('upper').includes(String(rawTrigger)));
  assert.equal(context.detectorPlotThreshold('upper'),Math.round(rawTrigger/det.display_scale));
  const autoValue = context.detectorAutoTriggerDisplay('upper',{trigger:1073741824},det);
  assert.equal(autoValue,1073741824);
  assert.equal(context.detectorThresholdText(autoValue,true),'1073741824');
  assert.equal(context.detectorThresholdText(3,false),integer?'3':'3.0');
  assert.equal(context.detectorThresholdText(3.5,false),'3.5');
  if(integer) assert.equal(context.detectorSnrText(3.6),'4x');
  return {all_passed:true,format_cases:results.length,saved_event:{amplitude:amplitude.text,instant:instant.text},
    zero_noise_rejected:true,plot_scaling_preserved:true,auto_readout_native:true};
}

export async function checkReadoutHelp(coreFile) {
  const core=await readFile(coreFile,'utf8');
  let now=0, pending=null, shown=false;
  const context=vm.createContext({
    tooltipEl:{textContent:'',classList:{add(){shown=true},remove(){shown=false}}},
    tooltipTimer:null,tooltipHover:null,tooltipAnchor:null,TOOLTIP_HOVER_DELAY_MS:2000,
    setTimeout(fn,delay){pending={fn,at:now+delay};return 1},clearTimeout(){pending=null},
    placeTooltip(){},
  });
  for(const name of ['normNumber','detectorNoiseDescription','setTooltip','tooltipText','clearTooltipTimer',
    'scheduleTooltipForPointer','hideTooltip']) {
    const start=core.indexOf('function '+name+'('),end=core.indexOf('\nfunction ',start+1);
    assert(start>0&&end>start,name);
    vm.runInContext(core.slice(start,end),context);
  }
  const text=context.detectorNoiseDescription('upper',5);
  for(const fragment of ['p (peak)','n (noise)','t (trigger)','SN —','P95','округляются','полная точность'])assert(text.includes(fragment),fragment);
  const zone={getAttribute(name){return name==='data-tooltip'?text:null},hasAttribute(){return false}};
  context.scheduleTooltipForPointer(zone,{clientX:100,clientY:50});
  assert.equal(pending.at,2000);
  now=1999;
  assert.equal(shown,false);
  now=2000;
  pending.fn();
  assert.equal(shown,true);
  assert.equal(context.tooltipEl.textContent,text);
  context.hideTooltip();
  assert.equal(shown,false);
  context.scheduleTooltipForPointer(zone,{clientX:100,clientY:50});
  context.hideTooltip();
  assert.equal(pending,null);
  assert(core.includes('#detUpperNoise>*,#detLowerNoise>*{pointer-events:none}'));
  return {description_fields:4,delay_ms:2000,not_visible_at_1999:true,visible_at_2000:true,
    cancels_on_exit:true,live_value_children_do_not_capture_pointer:true};
}
