// Run the real portal i18n and serialize all four checkbox combinations.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';

const folder = new URL('../../.tmp/confirmation-change-20261023/', import.meta.url);
const html = JSON.parse(await fs.readFile(process.argv[2] || new URL('portal_html.json',folder),'utf8'));
const script = html.match(/<script\b[^>]*>([\s\S]*?)<\/script>/i)[1];
const window = new Window({url:'http://bmi30.test/portal',settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
window.eval(script);
window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
const doc=window.document, up=doc.querySelector('input[type="checkbox"][name="confirm_change_up"]');
const down=doc.querySelector('input[type="checkbox"][name="confirm_change_down"]');
const ratio=doc.querySelector('[name="confirm_growth_ratio"]');
assert.ok(up && down && ratio);
assert.equal(up.closest('tr'),down.closest('tr'));
assert.equal(up.closest('tr'),ratio.closest('tr'));
assert.equal(doc.querySelectorAll('[name="confirm_growth_ratio"]').length,1);
assert.equal(doc.querySelectorAll('[name="confirm_growth_enabled"]').length,0);
const initial={up:up.checked,down:down.checked,ratio:ratio.value};
assert.equal(ratio.min,'1');assert.equal(ratio.max,'100');assert.equal(ratio.step,'0.1');
window.BMI30I18n.setLanguage('ru');
assert.ok(doc.body.textContent.includes('Изменение амплитуды подтверждений'));
assert.equal(up.getAttribute('aria-label'),'Разрешить рост амплитуды');
assert.equal(down.getAttribute('aria-label'),'Разрешить снижение амплитуды');
assert.equal(ratio.getAttribute('aria-label'),'Минимальный коэффициент изменения амплитуды');
const tip=doc.querySelector('[data-tip]').getAttribute('data-tip');
assert.ok(tip.includes('Обе галочки'));assert.ok(tip.includes('Ни одной'));
assert.ok(tip.includes('неокруглённые'));
assert.deepEqual({up:up.checked,down:down.checked,ratio:ratio.value},initial);
const combinations=[];
for(const a of [false,true]) for(const b of [false,true]) {
 up.checked=a;down.checked=b;
 const form = new window.FormData(doc.querySelector('form'));
 assert.deepEqual(form.getAll('confirm_change_up'),a?['0','1']:['0']);
 assert.deepEqual(form.getAll('confirm_change_down'),b?['0','1']:['0']);
 assert.equal(form.getAll('confirm_growth_ratio').length,1);
 combinations.push({up:a,down:b,mode:a?(b?'either':'up'):(b?'down':'off')});
}
window.BMI30I18n.setLanguage('en');
assert.ok(doc.body.textContent.includes('Confirmation amplitude change'));
await window.happyDOM.abort();
const result={passed:true,twoIndependentCheckboxes:true,oneFactor:true,sameRow:true,russianLabels:true,russianTooltip:true,
 reversibleTranslation:true,initial,combinations};
await fs.writeFile(process.argv[3]||new URL('portal-dom-tests.json',folder),JSON.stringify(result,null,2));
console.log('PASS: real portal, two checkboxes and one coefficient on the same row, four submissions, EN/RU labels and tooltip.');
