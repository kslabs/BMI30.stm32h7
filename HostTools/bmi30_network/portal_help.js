// Shared help for all portal parameter panels. Text stays in the portal's
// language; the same popup works with a mouse, keyboard and touch.
var portalFaultHelp = 'Acquisition fault: USB to STM32 is disconnected, or a stream fault has started automatic recovery. No detection, an intentional stream stop and a temporary wait for frames do not activate Fault.';
var portalLabelHelp = {
  'Device': 'Device role and bus ID, followed by its unique Raspberry identity and IP address suffix.',
  'State optic': 'Optical input and the selected reaction gate. The color distinguishes local input, local reaction, neighbor reaction and inactive input.',
  'Group channel': 'Channel used to display group telemetry. BMI30 is the network channel; RS485 remains the continuous wired synchronization and sensor channel.',
  'Uplink / signal': 'Current network uplink, Wi-Fi band and signal strength. A relayed connection names the intermediate device.',
  'Internet': 'Result and round-trip time of the latest internet reachability probe. Local group synchronization does not require internet.',
  'Role assignment': 'Master sends wired synchronization; Slave follows it. Changes apply to the device in this column.',
  'RS485 ID': 'Device address on the wired bus, from 0 to 31. Each device on the same bus needs a unique address.',
  'STM32 UID': 'Unique STM32 hardware identity, independent of its editable RS485 address.',
  'IP address': 'Current network address. The link opens that device\'s portal.',
  'Optic sensor': 'Current local optical input. ON indicates an active input, independently of whether a reaction is enabled.',
  'DetADC1 (modulation)': 'Modulation detection flag for ADC channel 1 reported by STM32.',
  'DetADC2 (modulation)': 'Modulation detection flag for ADC channel 2 reported by STM32.',
  'TX': 'Whether STM32 transmission is enabled.',
  'Device online': 'Device presence reported by the synchronization and sensor telemetry.',
  'Relay': 'Current relay output and its independent permission. Disabled permission prevents automatic relay activation.',
  'LED pattern': 'Current selected addressable pattern: 16 patterns numbered 0–15. Pattern 0 keeps the LEDs dark. The number remains visible in idle; Offline or Stale marks the last received selection after communication stops.',
  'LED output': 'Addressable LED output requested by the controller and the common LED permission. Pattern selection remains visible when the output is off.',
  'Sound active': 'Whether the controller is currently driving the sound indication.',
  'Fault': portalFaultHelp,
  'Own optic → sound/LED/relay': 'Use this device\'s optical input as the gate for its sound, LED and relay reactions. Each output also follows its own permission.',
  'RS485 neighbor optics → sound / LED / relay': 'Off ignores neighbor optics; Any accepts any current neighbor; a device name selects only that bus address. This selection applies to the device in the column.',
  'Optic trigger hold (sec)': 'How long the optical trigger remains effective after the input becomes inactive, from 0 to 10 seconds.',
  'Situation': 'Event whose addressable LED pattern is configured in this row.',
  'Pattern': 'Choose one of 16 addressable patterns, numbered 0–15. Pattern 0 has no illumination.',
  'Test': 'Temporarily test the selected indication. Use Stop to end the test.',
  'Upper antenna detection': 'Pattern used for a local upper-antenna detection.',
  'Lower antenna detection': 'Pattern used for a local lower-antenna detection.',
  'Both antennas detection': 'Pattern used when both local antennas detect.',
  'Neighbor upper antenna detection': 'Stored pattern for an upper-antenna neighbor event. Automatic neighbor LED indication is currently disabled.',
  'Neighbor lower antenna detection': 'Stored pattern for a lower-antenna neighbor event. Automatic neighbor LED indication is currently disabled.',
  'Neighbor both antennas detection': 'Stored pattern for a both-antenna neighbor event. Automatic neighbor LED indication is currently disabled.',
  'Maximum base width': 'Maximum accepted pulse width in samples, including the limit itself. Wider responses are rejected and restart detection confirmation.',
  'Default averaging': 'Number of frames averaged by the acquisition core. Saved for startup; Save and Apply also updates the running core.',
  'TX1': 'Phase of transmitter 1 relative to the shared synchronization: in phase (0°) or antiphase (180°). Changes apply and save immediately.',
  'TX2': 'Phase of transmitter 2 relative to the shared synchronization: in phase (0°) or antiphase (180°). Changes apply and save immediately.',
  'Minimum ON (ms)': 'Minimum automatic relay ON time in milliseconds. The relay test holds the output until Stop.',
  'Test sound volume': 'Playback volume for the Bluetooth test and connection sounds.',
  'New login': 'New username for the portal account. The current portal password is required to save it.',
  'Current password': 'Current portal password used to authorize the account change.',
  'New password': 'New portal password, at least 8 characters. Confirm it in the next field.',
  'Repeat new password': 'Repeat the new portal password exactly.',
  'Remote login': 'Username for the RDP/VNC desktop account; separate from portal access.',
  'New RDP/VNC password': 'New password for the remote desktop account.',
  'Repeat RDP/VNC password': 'Repeat the new remote desktop password exactly.',
  'HotSpot network name': 'Name (SSID) of this device\'s wireless access point.',
  'New HotSpot password': 'New password for connecting to this device\'s HotSpot.',
  'Repeat HotSpot password': 'Repeat the new HotSpot password exactly.',
  'Visible networks': 'Select an available Wi-Fi network for the uplink. Signal and security are shown alongside its name.',
  'Manual network name': 'Wi-Fi network name (SSID), including a hidden network not shown in the scan.',
  'Wi-Fi password': 'Password for the selected uplink Wi-Fi network.',
  'Login': 'Account username for this access channel.',
  'SSID': 'Wireless network name.',
  'Wired': 'Wired Ethernet network interface.',
  'Level': 'Current antenna signal level reported by the detector.',
  'Noise': 'Current noise estimate used by the detector.',
  'CPU': 'Processor temperature.',
  'RP1': 'Raspberry RP1 I/O controller temperature.',
  'GPU': 'Graphics processor temperature.',
  'STM32': 'STM32 device telemetry, including its reported temperature when available.',
  'Last device update': 'Source and age of the latest device telemetry.',
  'Last status update': 'Source and age of the latest group telemetry.',
  'Local optic': 'Local optical input reported by STM32.',
  'Stream': 'Current acquisition stream state.',
  'DC control': 'DC compensation state used to maintain the receive baseline.',
  'Uptime': 'Time since this device started.',
  'Frames': 'Acquisition frame counter reported by the statistics service.',
  'Events': 'Event counter reported by the statistics service.',
  'Last event': 'Most recent event reported by the statistics service.',
  'Errors': 'Error counter reported by the statistics service.',
  'Restarts': 'Restart counter reported by the statistics service.',
  'Hostname': 'Network hostname of this Raspberry.',
  'Serial': 'Raspberry hardware serial number.',
  'Release': 'Installed software release identifier.',
  'Created': 'Build creation date of the installed release.',
  'Portal': 'Management portal software information.',
  'Version': 'Version of the software component shown in this section.',
  'Source': 'Source used by the software component shown in this section.',
  'Software': 'Installed Raspberry software information.',
  'Role': 'Current synchronization role.',
  'Core': 'Running acquisition and detection core.',
  'Selected': 'Software version selected for activation.'
};
var portalFieldHelp = {
  common_mode_enabled: 'Subtract a synchronized reference from the other channel to suppress common interference.',
  smooth_mode: 'Input smoothing applied before detection. Select the mode used to reduce sample noise.',
  sound_enabled: 'Permit automatic sound indication. The selected optical gate and detector decision still apply.',
  sound_test_upper_enabled: 'Enable a manual test of the upper-antenna sound output.',
  sound_test_lower_enabled: 'Enable a manual test of the lower-antenna sound output.',
  sound_volume_percent: 'Sound output volume, from 0 to 100 percent.',
  sound_upper_frequency_hz: 'Fixed sound frequency for upper-antenna indication, in hertz.',
  sound_lower_frequency_hz: 'Fixed sound frequency for lower-antenna indication, in hertz.',
  sound_phase_upper_min_hz: 'Lower frequency limit for phase-dependent upper-antenna sound.',
  sound_phase_upper_max_hz: 'Upper frequency limit for phase-dependent upper-antenna sound.',
  sound_phase_lower_min_hz: 'Lower frequency limit for phase-dependent lower-antenna sound.',
  sound_phase_lower_max_hz: 'Upper frequency limit for phase-dependent lower-antenna sound.',
  lcd_role_overlay_enabled: 'Periodically show a large Mxx, Sxx or O on the device LCD for Master, Slave or synchronization off.',
  lcd_role_overlay_period_s: 'Repeat interval of the large LCD role display, in seconds.',
  lcd_role_overlay_duration_s: 'Duration of each large LCD role display, in seconds.',
  burst_blank_s0: 'Upper channel suppression interval after a burst, in seconds.',
  burst_blank_s1: 'Lower channel suppression interval after a burst, in seconds.',
  burst_max_ratio0: 'Upper channel burst threshold as a ratio to the reference noise level.',
  burst_max_ratio1: 'Lower channel burst threshold as a ratio to the reference noise level.',
  noise_up0: 'Maximum upward step of the upper-channel noise baseline, in the selected units.',
  noise_up1: 'Maximum upward step of the lower-channel noise baseline, in the selected units.',
  noise_down0: 'Maximum downward step of the upper-channel noise baseline, in the selected units.',
  noise_down1: 'Maximum downward step of the lower-channel noise baseline, in the selected units.'
};
function portalHelpLabel(el) {
  var copy=el.cloneNode(true);
  copy.querySelectorAll('input,select,button,output,small,.help,.sound-volume-output').forEach(function(n) {n.remove();});
  return copy.textContent.replace(/\s+/g,' ').trim().replace(/\s*\?$/, '');
}
function portalAddHelp(el, text) {
  if(!text) {return;}
  var icon=el.querySelector('.help');
  if(!icon) {icon=document.createElement('b'); icon.textContent='?'; el.appendChild(document.createTextNode(' ')); el.appendChild(icon);}
  icon.classList.add('help','tag-help'); icon.tabIndex=0;
  icon.setAttribute('role','button'); icon.setAttribute('aria-label',text); icon.setAttribute('data-tip',text);
  // The shared popup replaces native titles so there is only one description.
  icon.removeAttribute('title');
}
document.querySelectorAll('.portal-panel .tag-param,.portal-panel .group-param,.portal-panel .group-led-table th,.portal-panel .metric > span').forEach(function(el) {
  if(!el.querySelector('[data-tip]')) {portalAddHelp(el,portalLabelHelp[portalHelpLabel(el)]);}
});
document.querySelectorAll('.portal-panel label').forEach(function(label) {
  if(label.querySelector('[data-tip]')) {return;}
  var ctl=label.querySelector('input:not([type=hidden]),select'), text=label.getAttribute('title');
  text=text || (ctl && portalFieldHelp[ctl.name]) || portalLabelHelp[portalHelpLabel(label)];
  if(!text && ctl && ctl.hasAttribute('data-nonaddr-led-enable')) {text='Common permission for simple and addressable LED indication. Disabling it keeps their outputs off.';}
  if(!text && ctl && ctl.hasAttribute('data-relay-enable')) {text=portalLabelHelp.Relay;}
  // Table rows already have one help icon shared by both channel controls.
  var row=label.closest('tr');
  if(!text && row && row.querySelector('[data-tip]')) {return;}
  var caption=label.matches('#panel-privacy .privacy-fields .field,#panel-operation label') ? label.querySelector(':scope > span') : null;
  if(text) {portalAddHelp(caption || label,text); label.removeAttribute('title');}
});
document.querySelectorAll('.portal-panel input:not([type=hidden]),.portal-panel select').forEach(function(ctl) {
  var owner=ctl.closest('label'), row=ctl.closest('tr');
  var help=(owner && owner.querySelector('[data-tip]')) || (row && row.querySelector('[data-tip]'));
  var text=portalFieldHelp[ctl.name] || (help && help.getAttribute('data-tip'));
  if(text) {ctl.setAttribute('title',text);}
});
document.querySelectorAll('[data-tip]').forEach(function(icon) {icon.setAttribute('role','button'); icon.tabIndex=0;});
var portalHelpPopup=document.createElement('div');
portalHelpPopup.id='portal-parameter-help'; portalHelpPopup.className='portal-parameter-help';
portalHelpPopup.setAttribute('role','tooltip'); portalHelpPopup.hidden=true;
document.body.appendChild(portalHelpPopup);
var portalHelpAnchor=null;
function portalHideHelp() {
  if(portalHelpAnchor) {portalHelpAnchor.removeAttribute('aria-describedby');}
  portalHelpAnchor=null; portalHelpPopup.hidden=true;
}
function portalShowHelp(icon) {
  if(portalHelpAnchor && portalHelpAnchor !== icon) {portalHelpAnchor.removeAttribute('aria-describedby');}
  portalHelpAnchor=icon; portalHelpPopup.textContent=icon.getAttribute('data-tip');
  portalHelpPopup.hidden=false; icon.setAttribute('aria-describedby',portalHelpPopup.id);
  var r=icon.getBoundingClientRect(), p=portalHelpPopup.getBoundingClientRect();
  portalHelpPopup.style.left=Math.max(8,Math.min(r.left,window.innerWidth-p.width-8))+'px';
  portalHelpPopup.style.top=Math.max(8,r.top-p.height-8 > 8 ? r.top-p.height-8 : Math.min(r.bottom+8,window.innerHeight-p.height-8))+'px';
}
document.addEventListener('pointerover',function(e) {var icon=e.target.closest('[data-tip]'); if(icon) {portalShowHelp(icon);}});
document.addEventListener('pointerout',function(e) {var icon=e.target.closest('[data-tip]'); if(icon && !icon.contains(e.relatedTarget) && document.activeElement !== icon) {portalHideHelp();}});
document.addEventListener('focusin',function(e) {var icon=e.target.closest('[data-tip]'); if(icon) {portalShowHelp(icon);}});
document.addEventListener('focusout',function(e) {if(e.target === portalHelpAnchor) {portalHideHelp();}});
document.addEventListener('click',function(e) {
  var icon=e.target.closest('[data-tip]');
  if(icon) {e.preventDefault(); e.stopPropagation(); portalShowHelp(icon);}
  else {portalHideHelp();}
});
document.addEventListener('keydown',function(e) {
  if(e.key === 'Escape') {portalHideHelp();}
  var icon=e.target.closest('[data-tip]');
  if(icon && (e.key === 'Enter' || e.key === ' ')) {e.preventDefault(); portalShowHelp(icon);}
});
window.addEventListener('resize',portalHideHelp);
document.addEventListener('scroll',portalHideHelp,true);
