/* Real-browser dropdown regression test. Pass a synthetic exported HTML path. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const { chromium } = require('playwright');
(async () => {
  const html = fs.readFileSync(process.argv[2]);
  const server = http.createServer((req, res) => { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(html); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({channel: process.env.ATLAS_BROWSER_CHANNEL || undefined});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
    page.setDefaultTimeout(10000);
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (!req.url().startsWith('http://127.0.0.1:')) external.push(req.url()); });
    await page.goto('http://127.0.0.1:' + server.address().port);
    const trigger = page.locator('#relationTrigger');
    await trigger.click();
    await page.getByRole('option', {name: '关于', exact: true}).click();
    assert.equal(await page.locator('#relation').inputValue(), 'about');
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    await trigger.press('ArrowDown');
    await page.keyboard.press('End');
    assert.equal(await page.getByRole('option').last().evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Home');
    assert.equal(await page.getByRole('option').first().evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('listbox').count(), 0);
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    await trigger.click();
    await page.locator('#search').click();
    assert.equal(await page.getByRole('listbox').count(), 0);
    await page.locator('#languageToggle').click();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await trigger.innerText(), 'About');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    for (const width of [270, 269]) {
      await page.evaluate(width => panelWidth('left', width), width);
      const columns = await page.locator('.filter-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
      assert.equal(columns, width === 270 ? 2 : 1);
    }
    await page.evaluate(() => panelWidth('left', 270));
    await page.locator('#languageToggle').click();
    await trigger.click();
    if (process.env.ATLAS_SCREENSHOT) await page.screenshot({path: process.env.ATLAS_SCREENSHOT});
    await page.keyboard.press('Escape');
    await page.locator('#themeDark').click();
    await page.locator('#languageToggle').click();
    await trigger.click();
    if (process.env.ATLAS_SCREENSHOT) await page.screenshot({path: process.env.ATLAS_SCREENSHOT.replace('.png', '-dark.png')});
    await page.keyboard.press('Escape');
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => !document.getElementById('filtersPanel').open);
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('Browser: dropdown selection, focus, keyboard, outside click, language persistence, 270/269px, themes and mobile passed.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
