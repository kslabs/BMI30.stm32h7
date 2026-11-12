import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const core = await readFile(process.argv[2], 'utf8');
const start = core.indexOf('function noiseAreaMask(');
const end = core.indexOf('\nfunction ', core.indexOf('function drawRelativeNoiseSectors(', start) + 1);
assert(start > 0 && end > start);
const context = vm.createContext({lastStatus: {mode: {base_buf_len: 200}, detector: {settings: {noise_area_percent: 50}}}});
vm.runInContext(core.slice(start, end), context);
let cases = 0;
for (const n of [4, 5, 199, 200, 201, 600]) {
  for (let percent = 5; percent <= 95; percent++) {
    const mask = context.noiseAreaMask(n, n - 1, percent);
    assert.equal(mask.filter(Boolean).length, Math.max(1, Math.min(n - 1, Math.floor((n * percent + 50) / 100))));
    assert.equal(mask[n - 1], false);
    cases++;
  }
}
const labels = [], rectangles = [];
const canvas = {save() {}, restore() {}, strokeRect() {}, fillRect(...args) {rectangles.push(args);}, fillText(text) {labels.push(text);}};
context.drawRelativeNoiseSectors(canvas, 80, [0, 199], 200, 500, 250, 1, {sample_count: 200, noise_area_percent: 80});
assert.equal(labels[0], 'NOISE 80% (160/200)');
assert.equal(rectangles.length, 2); // Circular area wraps at the buffer boundary.
assert(rectangles.every(([x, , width]) => x >= 0 && width > 0));
console.log(`${cases} geometry cases; captured-frame label and wrapped shading passed`);
