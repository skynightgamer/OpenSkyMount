// End-to-end smoke test of the real UI against a running server.
// Usage: node e2e/smoke.cjs [baseUrl] [screenshotDir]
// Requires Playwright with Chromium (pre-installed in the Claude Code cloud environment).
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8000";
const shots = process.argv[3] || ".";

async function waitText(page, selector, re, timeout = 30000) {
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
  const errors = [];
  const ctxA = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const ctxB = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const a = await ctxA.newPage();
  const b = await ctxB.newPage();
  for (const p of [a, b]) {
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  }
  await a.goto(base);
  await b.goto(base);
  await waitText(a, ".conn", /Simulator connected/);
  await waitText(b, ".conn", /Simulator connected/);

  // Window A takes control and moves the simulated clock to a night when Jupiter is up.
  await a.getByRole("button", { name: "Take control" }).click();
  await waitText(a, ".authority", /You have control/);
  await a.fill('.clock input[type="datetime-local"]', "2026-11-01T02:00");
  await a.getByRole("button", { name: "×10" }).click();
  await waitText(a, ".clock-read", /simulated ×10/);

  // Tonight's planner is shown while the search box is empty.
  await a.waitForSelector(".tonight-list li", { timeout: 20000 });
  console.log("Tonight top:", (await a.locator(".tonight-list li").first().innerText()).replace(/\s+/g, " "));

  // Search, select and GoTo Jupiter.
  await a.fill('input[type="search"]', "Jupiter");
  await a.locator(".hits button").first().click();
  await waitText(a, ".target h2", /Jupiter/);
  await waitText(a, ".target", /above horizon/);
  const rows = await a.locator(".target > dl").innerText();
  console.log("Jupiter info:\n" + rows);
  // Exposure planner for the selected target.
  await a.locator(".exposure summary").click();
  await waitText(a, ".exp-out", /Max on this mount \(tracking\)\s*\d/);
  console.log("Exposure:", (await a.locator(".exp-out").innerText()).replace(/\s+/g, " "));
  // Display options (per device): decimal angles and an explicit time zone.
  await a.getByRole("button", { name: "Settings" }).click();
  await a.getByLabel("Angle format").selectOption("decimal");
  await a.getByLabel("Time zone").selectOption("Asia/Tokyo");
  await a.getByRole("button", { name: "Cancel" }).click();
  await waitText(a, ".target > dl", /RA \(J2000\)\s*\d{1,3}\.\d{4}°/);
  await waitText(a, ".clock-read", /GMT\+9/);
  console.log("Decimal/Tokyo:", (await a.locator(".clock-read strong").innerText()).trim());
  await a.getByRole("button", { name: "Settings" }).click();
  await a.getByLabel("Angle format").selectOption("dms");
  await a.getByLabel("Time zone").selectOption("device");
  await a.getByRole("button", { name: "Cancel" }).click();
  await waitText(a, ".target > dl", /RA \(J2000\)\s*\d\dh \d\dm/);
  await a.getByRole("button", { name: "GoTo & track" }).click();
  await waitText(a, ".telemetry .mode", /Slewing|Tracking/);

  // Window B (phone layout) observes the same mount state.
  await b.getByRole("tab", { name: "Control" }).click();
  await waitText(b, ".authority", /has control/);
  await waitText(b, ".telemetry .mode", /Tracking/, 60000);
  await waitText(b, ".telemetry", /Jupiter/);
  console.log("B telemetry:", (await b.locator(".telemetry").innerText()).replace(/\s+/g, " "));

  // Alignment: sync on the tracked target adds a model point visible to all clients.
  await a.getByRole("button", { name: "Sync" }).click();
  await waitText(a, ".alignment", /1 point/);
  await waitText(b, ".alignment", /1 point/);

  await a.waitForTimeout(1500);
  await a.screenshot({ path: `${shots}/desktop.png` });
  await b.getByRole("tab", { name: "Sky" }).click();
  await b.waitForTimeout(800);
  await b.screenshot({ path: `${shots}/mobile-sky.png` });
  await b.getByRole("tab", { name: "Mount" }).click();
  await b.waitForTimeout(800);
  await b.screenshot({ path: `${shots}/mobile-mount.png` });

  // Emergency stop from the observer phone takes effect for everyone.
  await b.getByRole("tab", { name: "Control" }).click();
  await b.getByRole("button", { name: "E-STOP" }).click();
  await waitText(a, ".telemetry .mode", /E-STOP/);
  await a.getByRole("button", { name: /Reset/ }).click();
  await waitText(a, ".telemetry .mode", /Idle/);
  await a.getByRole("button", { name: "Live" }).click();

  await browser.close();
  if (errors.length) {
    console.error("Browser errors:", errors);
    process.exit(1);
  }
  console.log("E2E OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
