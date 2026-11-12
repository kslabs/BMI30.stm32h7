(function () {
  'use strict';
  function init() {
    const words = {
      ru: ['Показать', 'Показать пароль'], uk: ['Показати', 'Показати пароль'],
      cs: ['Zobrazit', 'Zobrazit heslo'], de: ['Anzeigen', 'Passwort anzeigen'],
      pl: ['Pokaż', 'Pokaż hasło'], fr: ['Afficher', 'Afficher le mot de passe'],
      es: ['Mostrar', 'Mostrar contraseña'], en: ['Show', 'Show password']
    };
    const controls = [];
    document.querySelectorAll('input[type="password"]').forEach(input => {
      if (input.dataset.passwordVisibility) return;
      const field = input.closest('label');
      if (!field) return;
      input.dataset.passwordVisibility = '1';
      const row = document.createElement('div'); row.className = 'password-entry';
      field.before(row); row.append(field);
      const label = document.createElement('label'); label.className = 'password-reveal'; label.translate = false;
      const check = document.createElement('input'); check.type = 'checkbox'; check.autocomplete = 'off';
      const caption = document.createElement('span'); label.append(check, caption); row.append(label);
      function hide() {check.checked = false; input.type = 'password';}
      check.addEventListener('change', () => {input.type = check.checked ? 'text' : 'password';});
      if (input.form) input.form.addEventListener('submit', hide);
      controls.push({input, check, caption, label, hide});
    });
    if (!controls.length) return;
    const style = document.createElement('style');
    style.textContent = '.password-entry{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:8px;min-width:0}.password-entry>label:first-child{min-width:0}.password-entry input:not([type=checkbox]){min-width:0;width:100%;box-sizing:border-box}.password-entry>.password-reveal{display:flex;align-items:center;gap:5px;align-self:end;min-height:40px;font-size:12px;font-weight:400;margin:0;white-space:nowrap;cursor:pointer}.password-entry>.password-reveal>input{width:16px!important;height:16px!important;min-height:16px!important;min-width:16px!important;padding:0!important;margin:0;accent-color:var(--accent)}';
    document.head.append(style);
    function translate() {
      const language = document.documentElement.lang.split('-')[0];
      const text = words[language] || words.en;
      controls.forEach(({caption, label, check}) => {
        caption.textContent = text[0]; label.title = text[1]; check.setAttribute('aria-label', text[1]);
      });
    }
    translate();
    window.addEventListener('bmi30-language-change', translate);
    window.addEventListener('pagehide', () => controls.forEach(control => control.hide()));
    document.addEventListener('visibilitychange', () => {if (document.hidden) controls.forEach(control => control.hide());});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
}());
