// Capture the raw reference level before freezing; scaling only affects drawing.
function capturePlotNoise(f){
  if(!f||playerState.playback)return;
  if(f.exact_event_frame){
    if(!f._noiseSnapshot)f._noiseSnapshot={upper:autoFreezeDiagnostics.upper||null,lower:autoFreezeDiagnostics.lower||null};
  }else if(freezeMode==='live'||!f._noiseSnapshot){
    let det=(lastStatus&&lastStatus.detector)||{},scale=detectorDisplayScale(det);
    let channels=det.channels||{};
    f._noiseSnapshot={upper:{noise_avg:det.noise_avg0,display_scale:scale,trigger:det.trigger0||det.thr0,auto_threshold:!!(channels.upper||{}).auto_threshold},
      lower:{noise_avg:det.noise_avg1,display_scale:scale,trigger:det.trigger1||det.thr1,auto_threshold:!!(channels.lower||{}).auto_threshold}};
  }
}
function detectorPlotLevelSnapshot(channel){
  return detectorPlotSnapshot(channel)||(freezeMode==='freeze'&&lastFrame&&lastFrame._noiseSnapshot?lastFrame._noiseSnapshot[channel]:null);
}
function detectorPlotNoise(channel,normStats,f){
  let snap=f&&f._noiseSnapshot&&f._noiseSnapshot[channel],raw=snap&&snap.noise_avg;
  if(raw===null||raw===undefined||raw==='')return null;
  raw=Number(raw);if(!Number.isFinite(raw)||raw<0)return null;
  let scale=detectorDisplayScale(snap),level=raw/scale;
  if(normalizationEnabled()&&normStats){
    let peak=Number(normStats.normalization_peak);
    if(Number.isFinite(peak)&&peak>0)level=raw*65535/peak;
    else{let mx=Number(normStats.normalization_display_max||normStats.display_max);if(Number.isFinite(mx)&&mx>0)level=level*65535/mx}
  }
  if(!Number.isFinite(level))return null;
  return {level:Math.max(0,Math.min(65535,level)),label:'NOISE '+detectorLevelNumber(raw)};
}
function detectorEventTimeParts(snap){
  let raw=snap&&(snap.event_timestamp===undefined?snap.timestamp:snap.event_timestamp);
  if(raw===null||raw===undefined||raw==='')return[];
  let seconds=Number(raw),date=new Date(seconds*1000);
  if(!Number.isFinite(seconds)||seconds<=0||!Number.isFinite(date.getTime()))return[];
  let time=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Prague',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date);
  let day=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Prague',day:'2-digit',month:'2-digit',year:'numeric'}).format(date);
  return[{text:'⏱ '+time,tip:day+' '+time+' Europe/Prague'}];
}
