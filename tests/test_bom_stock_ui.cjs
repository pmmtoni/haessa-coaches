// Run with the page produced by tests/render_bom_stock_fixture.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
(async () => {
    const browser = await chromium.launch({headless: true,
        ...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('http://bom.test/**', route => route.fulfill({
            contentType:'text/html', body:fs.readFileSync(process.argv[2], 'utf8')}));
        await page.goto('http://bom.test/coaches/1/bom');
        const result = await page.evaluate(() => {
            function fields(form) {
                return [...document.querySelectorAll('input,select')].filter(el => el.form === form && el.type !== 'hidden');
            }
            function exercise(form) {
                const controls = fields(form);
                const get = name => controls.find(el => el.name === name);
                const stock = get('stock_status');
                const dates = ['expected_delivery_date', 'supplier_date', 'actual_delivery_date'].map(get);
                const delivered = get('delivered');
                const change = el => el.dispatchEvent(new Event('change', {bubbles:true}));
                stock.value = 'In Stock'; change(stock);
                const disabled = dates.every(el => el.disabled);
                stock.value = 'Out of Stock'; change(stock);
                const enabled = dates.every(el => !el.disabled);
                dates[0].value = '2026-10-01'; change(dates[0]);
                dates[1].value = '2026-10-02'; change(dates[1]);
                dates[2].value = '2026-10-03'; change(dates[2]);
                const automatic = stock.value === 'In Stock' && delivered.checked && dates.every(el => el.disabled);
                const data = new FormData(form);
                const submitted = dates.map(el => data.getAll(el.name));
                delivered.checked = false; change(delivered);
                const protectedCheck = delivered.checked;
                stock.value = 'Out of Stock'; change(stock);
                dates[2].value = ''; change(dates[2]);
                delivered.checked = false; change(delivered);
                const corrected = new FormData(form).get('actual_delivery_date') === '' && !delivered.checked;
                return {disabled, enabled, automatic, submitted, protectedCheck, corrected};
            }
            const selects = [...document.querySelectorAll('select[name="stock_status"]')];
            const initial = selects.map(select => ({stock:select.value,
                dates:fields(select.form).filter(el => el.type === 'date').map(el => el.disabled)}));
            return {initial, results:selects.map(select => exercise(select.form))};
        });
        assert.equal(result.initial.length, 4);
        assert.ok(result.initial.some(row => row.stock === 'In Stock' && row.dates.every(Boolean)));
        for(const row of result.results) {
            for(const name of ['disabled','enabled','automatic','protectedCheck','corrected']) assert.equal(row[name], true, name);
            assert.deepEqual(row.submitted, [['2026-10-01'],['2026-10-02'],['2026-10-03']]);
        }
        assert.deepEqual(errors, []);
        console.log('PASS: rendered add and three edit forms; date toggles, automatic receipt, submitted values, checkbox protection and corrections.');
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
