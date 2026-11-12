(function () {
  'use strict';
  const root = document.getElementById('panel-privacy');
  if (!root) return;
  const tabs = [...root.querySelectorAll('[data-privacy-tab]')];
  const panels = [...root.querySelectorAll('[data-privacy-zone]')];
  if (tabs.length !== 5 || panels.length !== 5) return;
  function select(name, focus) {
    if (!tabs.some(tab => tab.dataset.privacyTab === name)) name = 'portal';
    tabs.forEach(tab => {
      const active = tab.dataset.privacyTab === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    });
    panels.forEach(panel => {panel.hidden = panel.dataset.privacyZone !== name;});
    try {sessionStorage.setItem('bmi30-privacy-tab', name);} catch (_) {}
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab.dataset.privacyTab, false));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      select(tabs[next].dataset.privacyTab, true);
    });
  });
  let selected = 'portal';
  try {selected = sessionStorage.getItem('bmi30-privacy-tab') || selected;} catch (_) {}
  select(selected, false);
}());
