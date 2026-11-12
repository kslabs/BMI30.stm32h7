/* Offline UI localization shared by portal and oscilloscope. */
(function () {
  'use strict';
  if (window.BMI30I18n) return;
  const catalogs = __BMI30_CATALOGS__;
  const aliases=catalogs._aliases||{};
  const languages = [['en','English','EN'],['uk','Українська','UK'],['es','Español','ES'],['de','Deutsch','DE'],['cs','Čeština','CS'],['ja','日本語','JA'],['fr','Français','FR'],['pt','Português','PT'],['zh-Hans','中文','中文'],['it','Italiano','IT'],['ru','Русский','RU']];
  const storageKey = 'bmi30.ui.language', cookieKey = 'bmi30_language';
  const attrs = ['title','aria-label','placeholder','data-tip','data-tooltip','data-channel-label','alt'];
  const records = new WeakMap(), attrRecords = new WeakMap(), cache = new Map();
  const keys = Object.keys(catalogs.ru).filter(k => k.length > 1 && /[A-Za-z]/.test(k));
  const escapeRE = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const aliasKeys=Object.keys(aliases).sort((a,b)=>b.length-a.length);
  const aliasFragments=aliasKeys.length?new RegExp(aliasKeys.map(escapeRE).join('|'),'g'):null;
  function canonical(value){const source=String(value==null?'':value);return aliases[source]||
    (aliasFragments&&/[А-Яа-яЁё]/.test(source)?source.replace(aliasFragments,key=>aliases[key]):source);}
  const fragments = new RegExp('(?<![A-Za-z0-9_])(?:' + keys.sort((a,b) => b.length-a.length).map(escapeRE).join('|') + ')(?![A-Za-z0-9_])','g');
  let language = 'en', button, menu, timer, observer, heldOpen=false;
  function valid(code) { return languages.some(x => x[0] === code); }
  function preference() {
    // A host cookie also shares the explicit choice with the direct core port.
    try { const m=document.cookie.match(/(?:^|;\s*)bmi30_language=([^;]*)/); if(m && valid(decodeURIComponent(m[1]))) return decodeURIComponent(m[1]); } catch(e) {}
    try { const code=localStorage.getItem(storageKey); if(valid(code)) return code; } catch(e) {}
    return 'en';
  }
  function translate(text, params) {
    const original=canonical(text);
    let result=original;
    if(language !== 'en' && /[A-Za-z]/.test(original)) {
      if(cache.has(original)) result=cache.get(original);
      else {
        const dictionary=catalogs[language], normalized=original.replace(/\s+/g,' ').trim();
        if(dictionary[normalized]) result=original.replace(/\S[\s\S]*\S|\S/,dictionary[normalized]);
        else if(original.includes('\n')) result=original.split('\n').map(line=>translate(line)).join('\n');
        else {
          const seconds=original.match(/^n \(noise\) — noise estimate: max\(mean, P95\) over ([0-9.,]+) s\. P95 is the 95th percentile of measurement history\.$/);
          const template='n (noise) — noise estimate: max(mean, P95) over {seconds} s. P95 is the 95th percentile of measurement history.';
          result=seconds && dictionary[template] ? dictionary[template].replace('{seconds}',seconds[1]) : original.replace(fragments,k => dictionary[k] || k);
        }
        if(cache.size>=2048) cache.clear();
        cache.set(original,result);
      }
    }
    if(params) Object.keys(params).forEach(k => {result=result.split('{'+k+'}').join(String(params[k]));});
    return result;
  }
  function skipped(element) {
    return !element || !!element.closest('script,style,pre,code,textarea,[translate="no"],.identity-value,.player-transcript,select[name="visible_ssid"],[data-ssid],[data-i18n-skip]');
  }
  function textNode(node) {
    if(skipped(node.parentElement)) return;
    if(node.parentElement.tagName==='OPTION' && /\.(json|npz|wav|mp3|pdf)$/i.test(node.parentElement.value)) return;
    if(/^\s*(?:\/[^\s]+|[^\s]+\.(?:json|npz|wav|mp3|pdf))\s*$/i.test(node.nodeValue)) return;
    let record=records.get(node), value=node.nodeValue;
    if(!record || value!==record.last) record={source:value,last:value};
    const output=translate(record.source);
    if(value!==output) node.nodeValue=output;
    record.last=output;records.set(node,record);
  }
  function attributes(element) {
    if(skipped(element)) return;
    let saved=attrRecords.get(element); if(!saved) {saved={};attrRecords.set(element,saved);}
    attrs.forEach(name => {
      if(!element.hasAttribute(name)) {delete saved[name];return;}
      const value=element.getAttribute(name);
      let record=saved[name];if(!record || value!==record.last) record={source:value,last:value};
      const output=translate(record.source);
      if(value!==output) element.setAttribute(name,output);
      record.last=output;saved[name]=record;
    });
  }
  function refresh(root) {
    if(!root) root=document.body;
    if(!root) return;
    if(root.nodeType===3) {textNode(root);return;}
    if(root.nodeType!==1 || skipped(root)) return;
    attributes(root);
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
    let node;while((node=walker.nextNode())) {if(node.nodeType===3) textNode(node);else attributes(node);}
  }
  function closeMenu() {clearTimeout(timer);timer=null;if(menu) menu.hidden=true;if(button) button.setAttribute('aria-expanded','false');}
  function openMenu(focus) {
    clearTimeout(timer);timer=null;if(!menu) return;
    menu.hidden=false;button.setAttribute('aria-expanded','true');
    if(focus) (menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
  }
  function scheduleMenu(held) {clearTimeout(timer);timer=setTimeout(() => {heldOpen=!!held;openMenu(false);},2000);}
  function updateChooser() {
    if(!button) return;
    const selected=languages.find(x => x[0]===language);
    button.textContent='🌐 '+selected[2];
    button.setAttribute('aria-label',translate('Interface language')+': '+selected[1]);
    button.title=translate('Hover for 2 seconds or click to choose the interface language.');
    menu.querySelectorAll('button').forEach(item => item.setAttribute('aria-checked',String(item.dataset.language===language)));
  }
  function setLanguage(code,persist=true) {
    if(!valid(code)) code='en';
    language=code;cache.clear();document.documentElement.lang=code;
    if(persist) {
      try {localStorage.setItem(storageKey,code);} catch(e) {}
      try {document.cookie=cookieKey+'='+encodeURIComponent(code)+'; Path=/; Max-Age=31536000; SameSite=Lax';} catch(e) {}
    }
    refresh(document.body);refresh(document.querySelector('title'));updateChooser();closeMenu();
    window.dispatchEvent(new CustomEvent('bmi30-language-change',{detail:{language:code}}));
  }
  function createChooser() {
    const host=document.querySelector('.device-meta') || document.querySelector('.title') || document.querySelector('main') || document.body;
    const wrap=document.createElement('span');wrap.className='bmi30-language';wrap.setAttribute('translate','no');
    wrap.innerHTML='<button id="bmi30-language-button" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="bmi30-language-menu"></button><span id="bmi30-language-menu" role="menu" hidden></span>';
    button=wrap.firstElementChild;menu=wrap.lastElementChild;
    languages.forEach(([code,name]) => {
      const item=document.createElement('button');item.type='button';item.textContent=name;item.lang=code;
      item.dataset.language=code;item.setAttribute('role','menuitemradio');
      item.addEventListener('click',() => {setLanguage(code);button.focus();});menu.appendChild(item);
    });
    // The portal insertion point is immediately before RPI / STM32 identity.
    host.insertBefore(wrap,host.firstChild);
    wrap.addEventListener('pointerenter',() => scheduleMenu(false));
    wrap.addEventListener('pointerleave',() => {clearTimeout(timer);timer=null;});
    button.addEventListener('pointerdown',() => {heldOpen=false;scheduleMenu(true);});
    button.addEventListener('pointercancel',closeMenu);
    button.addEventListener('click',() => {if(heldOpen){heldOpen=false;return;}if(menu.hidden) openMenu(false);else closeMenu();});
    wrap.addEventListener('keydown',event => {
      if(event.key==='Escape') {closeMenu();button.focus();event.preventDefault();}
      else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
        const items=Array.from(menu.querySelectorAll('button'));const index=items.indexOf(document.activeElement);
        openMenu(false);let next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
        items[next].focus();event.preventDefault();
      }
    });
    document.addEventListener('pointerdown',event => {if(!wrap.contains(event.target)) closeMenu();});
    document.addEventListener('focusin',event => {if(!wrap.contains(event.target)) closeMenu();});
    updateChooser();
  }
  const style=document.createElement('style');style.textContent=`
    .bmi30-language{position:relative;display:inline-flex;align-items:center;vertical-align:middle;margin-right:8px;flex-shrink:0;white-space:normal;text-align:left}
    .device-meta:has(>.bmi30-language){grid-template-columns:max-content max-content minmax(0,max-content)!important}
    .device-meta>.bmi30-language{grid-column:1!important;grid-row:1/span 2!important;align-self:center}
    .device-meta:has(>.bmi30-language)>.identity-label{grid-column:2!important}
    .device-meta:has(>.bmi30-language)>.identity-value{grid-column:3!important}
    #bmi30-language-button{font:700 12px/1.3 system-ui,sans-serif;min-height:28px;padding:4px 8px;border-radius:8px;border:1px solid var(--line,#c9d3df);background:var(--panel,#fff);color:var(--text,#172a3d);cursor:pointer}
    #bmi30-language-menu{position:absolute;top:calc(100% + 5px);right:0;display:grid;grid-template-columns:1fr;gap:2px;z-index:10050;min-width:172px;max-height:min(72vh,450px);overflow:auto;padding:6px;border:1px solid var(--line,#c9d3df);border-radius:10px;background:var(--panel-fallback,var(--panel2,var(--panel,#fff)));color:var(--text,#172a3d);box-shadow:0 8px 30px #0004}
    #bmi30-language-menu[hidden]{display:none!important}
    main>.bmi30-language #bmi30-language-menu{left:0;right:auto}
    #bmi30-language-menu button{display:block;width:100%;font:14px/1.35 system-ui,sans-serif;text-align:left;white-space:nowrap;min-height:29px;padding:4px 9px;border:1px solid transparent;border-radius:6px;color:inherit;background:transparent;cursor:pointer}
    #bmi30-language-menu button:hover,#bmi30-language-menu button:focus-visible{background:var(--accent-soft,var(--panel2,var(--panel,#e1eeff)));outline:1px solid var(--accent,#1672be)}
    #bmi30-language-menu button[aria-checked=true]{font-weight:800;border-color:var(--accent,#1672be)}
    .title .bmi30-language{order:9;margin-left:auto;margin-right:0}
    @media(max-width:680px){.device-meta .bmi30-language{margin-right:4px}#bmi30-language-menu{min-width:154px}}
  `;document.head.appendChild(style);
  language=preference();document.documentElement.lang=language;
  function writeText(element,source) {
    if(!element) return;source=String(source);const output=translate(source);
    if(element.textContent!==output) element.textContent=output;
    if(element.childNodes.length===1 && element.firstChild.nodeType===3) records.set(element.firstChild,{source,last:output});
  }
  function writeAttribute(element,name,source) {
    if(!element) return;source=String(source);const output=translate(source);
    if(element.getAttribute(name)!==output) element.setAttribute(name,output);
    let saved=attrRecords.get(element);if(!saved){saved={};attrRecords.set(element,saved);}saved[name]={source,last:output};
  }
  window.BMI30I18n={t:translate,text:writeText,attribute:writeAttribute,setLanguage,refresh,get language(){return language;},languages:languages.map(x=>x[0])};
  function init() {
    createChooser();refresh(document.body);refresh(document.querySelector('title'));
    observer=new MutationObserver(changes => {
      const pending=new Set();
      changes.forEach(change => {
        if(change.type==='attributes') attributes(change.target);
        else if(change.type==='characterData') textNode(change.target);
        else change.addedNodes.forEach(node=>pending.add(node));
      });
      pending.forEach(refresh);
    });
    observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attrs});
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true});else init();
  window.addEventListener('storage',event => {if(event.key===storageKey && valid(event.newValue)) setLanguage(event.newValue,false);});
  window.addEventListener('pageshow',() => {const code=preference();if(code!==language)setLanguage(code,false);});
  window.addEventListener('focus',() => {const code=preference();if(code!==language)setLanguage(code,false);});
}());
