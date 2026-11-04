// Screenshots for the README (docs/img). Not a test: run it against a server whose state directory has the
// "telescope" payload selected, e.g.
//   backend/.venv/bin/python -c "from openskymount.config import AppConfig; from pathlib import Path; \
//     AppConfig(payload='telescope').save(Path('/tmp/osm-shots/config.json'))"
//   backend/.venv/bin/python -m openskymount.server --port 8770 --state-dir /tmp/osm-shots &
//   node frontend/e2e/readme-shots.cjs http://127.0.0.1:8770 docs/img
const { chromium, devices } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8000";
const out = process.argv[3] || ".";

async function waitText(page, selector, re, timeout = 60000) {
  await page.waitForFunction(
    ([sel, src]) => {
      const el = document.querySelector(sel);
      return el && new RegExp(src).test(el.textContent || "");
    },
    [selector, re.source],
    { timeout },
  );
}

(async () => {
  const browser = await chromium.launch();
  const desk = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })).newPage();
  const phone = await (await browser.newContext({ ...devices["iPhone 13"] })).newPage();
  desk.setDefaultTimeout(120000);
  phone.setDefaultTimeout(120000);
  await desk.goto(base);
  await phone.goto(base);
  await waitText(desk, ".conn", /Simulator connected/);
  await waitText(phone, ".conn", /Simulator connected/);

  // A clear night in Berlin with Jupiter well up; GoTo and track it with the telescope payload.
  await desk.getByRole("button", { name: "Take control" }).click();
  await waitText(desk, ".authority", /You have control/);
  await desk.fill('.clock input[type="datetime-local"]', "2026-11-01T02:00");
  await desk.getByRole("button", { name: "×10" }).click();
  await desk.fill('input[type="search"]', "Jupiter");
  await desk.locator(".hits button").first().click({ noWaitAfter: true });
  await waitText(desk, ".target h2", /Jupiter/);
  await desk.getByRole("button", { name: "GoTo & track" }).click();
  await waitText(desk, ".telemetry .mode", /Tracking/);
  await desk.getByRole("button", { name: "Sync" }).click();
  await waitText(desk, ".alignment", /1 point/);
  await desk.fill('input[type="search"]', "");
  await desk.evaluate(() => document.activeElement && document.activeElement.blur());
  await desk.waitForTimeout(2500);
  await desk.screenshot({ path: `${out}/desktop.png` });

  await desk.getByRole("button", { name: "Red light" }).click();
  await desk.waitForTimeout(800);
  await desk.screenshot({ path: `${out}/red-light.png` });
  await desk.getByRole("button", { name: "Red light" }).click();

  // The phone watches the same mount.
  await waitText(phone, ".telemetry .mode", /Tracking/);
  await phone.getByRole("tab", { name: "Sky" }).click();
  await phone.waitForTimeout(1000);
  await phone.screenshot({ path: `${out}/mobile-sky.png` });
  await phone.getByRole("tab", { name: "Mount" }).click();
  await phone.waitForTimeout(1500);
  await phone.screenshot({ path: `${out}/mobile-mount.png` });
  await browser.close();
  console.log("Screenshots written to", out);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
