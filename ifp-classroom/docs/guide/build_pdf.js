// Renders guide.html to ../IFP-Classroom-Guide.pdf with page numbers.
//   node build_pdf.js           (needs Playwright with Chromium)
const path = require('path');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + path.join(__dirname, 'guide.html'), { waitUntil: 'networkidle' });
  await page.pdf({
    path: path.join(__dirname, '..', 'IFP-Classroom-Guide.pdf'),
    format: 'A4', printBackground: true, displayHeaderFooter: true,
    margin: { top: '16mm', bottom: '16mm', left: '14mm', right: '14mm' },
    headerTemplate: '<div style="font-size:7px;color:#8a92a3;width:100%;padding:0 14mm;display:flex;justify-content:space-between"><span>IFP Classroom Attendance</span><span>Setup guide &amp; user manual</span></div>',
    footerTemplate: '<div style="font-size:7px;color:#8a92a3;width:100%;text-align:center">Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
  });
  await browser.close();
})();
