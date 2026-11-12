// Exercise the controls rendered by the installed M02 portal and its real i18n.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {Window} from '../../.tmp/language-20261016/node_modules/happy-dom/lib/index.js';

const folder = new URL('../../.tmp/confirmation-growth-20261021/M02-deploy/', import.meta.url);
const html = JSON.parse(await fs.readFile(process.argv[2] || new URL('portal_html.json', folder), 'utf8'));
const script = html.match(/<script\b[^>]*>([\s\S]*?)<\/script>/i)[1];
const window = new Window({url:'http://bmi30.test/portal', settings:{disableJavaScriptFileLoading:true,disableCSSFileLoading:true}});
window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
window.eval(script);
window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
const document = window.document;
const checkbox = document.querySelector('input[type="checkbox"][name="confirm_growth_enabled"]');
const hidden = document.querySelector('input[type="hidden"][name="confirm_growth_enabled"]');
const ratio = document.querySelector('[name="confirm_growth_ratio"]');
assert.ok(checkbox);
assert.equal(checkbox.closest('tr'),ratio.closest('tr'));
assert.equal(checkbox.checked, false);
assert.equal(hidden.value, '0');
assert.equal(ratio.value, '2.0');
assert.equal(ratio.min, '1');
assert.equal(ratio.max, '100');
assert.equal(ratio.step, '0.1');
assert.ok(document.body.textContent.includes('Confirmation peak growth'));
window.BMI30I18n.setLanguage('ru');
assert.ok(document.body.textContent.includes('Рост амплитуды подтверждений'));
assert.equal(ratio.getAttribute('aria-label'),'Минимальное отношение последнего пика к первому');
const tip = document.querySelector('[data-tip]').getAttribute('data-tip');
assert.ok(tip.includes('Последний максимум должен быть не меньше первого'));
assert.ok(tip.includes('неокруглённые'));
assert.equal(ratio.value, '2.0');
assert.equal(checkbox.checked, false);
const form = document.createElement('form');
form.innerHTML = document.querySelector('table').outerHTML;
document.body.appendChild(form);
assert.deepEqual(new window.FormData(form).getAll('confirm_growth_enabled'), ['0']);
form.querySelector('input[type="checkbox"]').checked = true;
assert.deepEqual(new window.FormData(form).getAll('confirm_growth_enabled'), ['0','1']);
window.BMI30I18n.setLanguage('en');
assert.ok(document.body.textContent.includes('Confirmation peak growth'));
await window.happyDOM.abort();
await fs.writeFile(process.argv[3] || new URL('portal-dom-tests.json',folder), JSON.stringify({passed:true, controls:true,
    defaults:true, russianLabels:true, russianTooltip:true, reversibleTranslation:true,
    sameRow:true, uncheckedSubmission:'0', checkedSubmission:['0','1']}, null,2));
console.log('PASS: installed portal controls on one row, defaults 2.0/OFF, EN/RU labels and tooltip, checkbox submission.');
