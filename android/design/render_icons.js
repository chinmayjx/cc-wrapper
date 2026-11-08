// Render design/icon-<variant>.svg to the legacy launcher PNGs (pre-Android 8 launchers; newer ones use
// the adaptive icon). Run after make_icon.py:  NODE_PATH=<playwright> node design/render_icons.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const SIZES = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const variant of ['main', 'debug']) {
    const svg = fs.readFileSync(path.join(root, 'design', `icon-${variant}.svg`), 'utf8');
    for (const [density, size] of Object.entries(SIZES)) {
      const dir = path.join(root, 'app/src', variant, 'res', `mipmap-${density}`);
      fs.mkdirSync(dir, { recursive: true });
      for (const [name, radius] of [['ic_launcher', '23%'], ['ic_launcher_round', '50%']]) {
        await page.setViewportSize({ width: size, height: size });
        await page.setContent(`<body style="margin:0;background:transparent"><div style="width:${size}px;height:${size}px;border-radius:${radius};overflow:hidden">${svg.replace('<svg ', `<svg width="${size}" height="${size}" `)}</div></body>`);
        await page.screenshot({ path: path.join(dir, `${name}.png`), omitBackground: true });
      }
    }
  }
  await browser.close();
  console.log('rendered');
})();
