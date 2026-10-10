const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../templates/_download_names.html'), 'utf8');
for (const coach of ['000 999', null]) {
    const values = [coach, '../A "B"\r\n', 'coach_bom'];
    let index = 0;
    const script = source.replace(/<\/?script>/g, '')
        .replace(/\{\{[\s\S]*?\}\}/g, () => JSON.stringify(values[index++]));
    const handlers = {};
    const document = {title: 'Original title'};
    class Clock extends Date { static now() { return Date.parse('2026-10-10T23:04:05Z'); } }
    vm.runInNewContext(script, {document, Date: Clock,
        window: {addEventListener(event, fn) { handlers[event] = fn; }}});
    handlers.beforeprint();
    assert.equal(document.title,
        `coach_bom_${coach ? '000_999' : 'all-coaches'}_20261011_010405_SAST_A_B`);
    handlers.beforeprint();
    handlers.afterprint();
    assert.equal(document.title, 'Original title');
    handlers.beforeprint();
    handlers.afterprint();
    assert.equal(document.title, 'Original title');
}
console.log('PASS: PDF filename coach, user, SAST rollover, sanitization and title restoration');
