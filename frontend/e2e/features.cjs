// End-to-end test of the observing session, satellite tracking, telemetry replay, hand-controller layout,
// gamepad control (emulated Gamepad API) and PWA files, against a running server with a fresh state directory.
// Usage: node e2e/features.cjs [baseUrl] [screenshotDir]
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8000";
const shots = process.argv[3] || ".";

// ISS elements from the Skyfield documentation; the clock is set to a pass over Berlin on 2014-01-21.
const ISS = `ISS (ZARYA)
1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082
2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473`;

async function waitText(page, selector, re, timeout = 30000) {
  await page.waitForFunction(
    ([sel, src]) => [...document.querySelectorAll(sel)].some((el) => new RegExp(src).test(el.textContent || "")),
    [selector, re.source],
    { timeout },
  );
}

// A fake standard-layout gamepad driven by window.__pad (axes, pressed button indices).
const FAKE_PAD = () => {
  window.__pad = { axes: [0, 0, 0, 0], pressed: [] };
  navigator.getGamepads = () => [
    {
      id: "E2E virtual gamepad",
      index: 0,
      connected: true,
      mapping: "standard",
      timestamp: performance.now(),
      axes: window.__pad.axes,
      buttons: Array.from({ length: 17 }, (_, i) => ({ pressed: window.__pad.pressed.includes(i), value: window.__pad.pressed.includes(i) ? 1 : 0 })),
    },
  ];
};

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: "UTC" });
  await ctx.addInitScript(FAKE_PAD);
  const a = await ctx.newPage();
  a.on("pageerror", (e) => errors.push(e.message));
  a.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await a.goto(base);
  await waitText(a, ".conn", /Simulator connected/);
  await a.getByRole("button", { name: "Take control" }).click();
  await waitText(a, ".authority", /You have control/);
  await a.fill('.clock input[type="datetime-local"]', "2014-01-21T03:20");
  await waitText(a, ".clock-read", /03:20/);

  // PWA files are served.
  for (const f of ["/manifest.webmanifest", "/sw.js", "/icon-192.png"]) {
    const r = await a.request.get(base + f);
    if (!r.ok()) throw new Error(`${f}: HTTP ${r.status()}`);
  }

  // Load a TLE, select the ISS, add its next pass to the session.
  await a.locator(".orbits summary").click();
  await a.fill(".orbits textarea", ISS);
  await a.locator(".orbits").getByRole("button", { name: "Load" }).click();
  await waitText(a, ".orbit-list", /ISS \(ZARYA\)/);
  await a.locator(".orbit-list button.link", { hasText: "ISS" }).click();
  await waitText(a, ".target h2", /ISS/);
  await waitText(a, ".target .sub", /Earth satellite/);
  await a.waitForSelector(".passes li", { timeout: 30000 });
  console.log("First pass:", (await a.locator(".passes li").first().innerText()).replace(/\s+/g, " "));
  await a.locator(".passes li").first().getByRole("button", { name: "+ Session" }).click();
  // ... and Capella for 10 minutes after it.
  await a.fill('input[type="search"]', "Capella");
  await a.locator(".hits button").first().click();
  await waitText(a, ".target h2", /Capella/);
  await a.locator(".target").getByRole("button", { name: "+ Session" }).click();
  await waitText(a, ".queue", /Capella/);
  await a.screenshot({ path: `${shots}/session-queue.png` });

  // Run the session at 60x with telemetry recording.
  await a.locator(".session").getByRole("button", { name: "Start session" }).click();
  await waitText(a, ".session .badge", /running/);
  await a.getByRole("button", { name: "×60", exact: true }).click();
  await waitText(a, ".queue li.q-waiting, .queue li.q-slewing, .queue li.q-tracking", /ISS/, 20000);
  await waitText(a, ".queue li.q-tracking", /ISS/, 60000);
  await a.screenshot({ path: `${shots}/iss-tracking.png` });
  const tel = (await a.locator(".telemetry").innerText()).replace(/\s+/g, " ");
  console.log("Tracking the ISS:", tel);
  await waitText(a, ".queue li.q-done", /ISS/, 60000);
  await waitText(a, ".queue li.q-done", /Capella/, 60000);
  await waitText(a, ".session .badge", /stopped/);
  console.log("Session:", (await a.locator(".queue").innerText()).replace(/\s+/g, " "));

  // The recording stopped with the session; replay it.
  await a.locator(".logs summary").click();
  await a.waitForSelector(".log-list li button:has-text('Replay')", { timeout: 15000 });
  await a.locator(".log-list li").first().getByRole("button", { name: "Replay" }).click();
  await waitText(a, "table.stats", /sat:25544/);
  console.log("Replay stats:\n" + (await a.locator("table.stats").innerText()));
  await a.locator(".replay input[type=range]").fill(String(await a.locator(".replay input[type=range]").getAttribute("max")));
  await a.screenshot({ path: `${shots}/replay.png` });
  await a.locator(".replay").getByRole("button", { name: "Close" }).click();

  // Gamepad: nothing moves without the dead-man button; hold RB + stick -> manual slew; release -> stop.
  await a.getByRole("button", { name: "×1", exact: true }).click();
  await waitText(a, ".gamepad-hint", /E2E virtual gamepad/);
  await a.evaluate(() => (window.__pad.axes = [0.9, 0, 0, 0]));
  await a.waitForTimeout(800);
  const before = await a.locator(".telemetry .mode").innerText();
  if (/Manual/.test(before)) throw new Error("gamepad moved the mount without the dead-man button");
  await a.evaluate(() => (window.__pad.pressed = [5]));
  await waitText(a, ".telemetry .mode", /Manual/, 5000);
  await waitText(a, ".gamepad-hint", /moving/);
  await a.evaluate(() => {
    window.__pad.pressed = [];
    window.__pad.axes = [0, 0, 0, 0];
  });
  await waitText(a, ".telemetry .mode", /Idle/, 5000);
  // B = Stop and Back = E-STOP work without the dead-man button.
  await a.evaluate(() => (window.__pad.pressed = [8]));
  await waitText(a, ".telemetry .mode", /E-STOP/, 5000);
  await a.evaluate(() => (window.__pad.pressed = []));
  await a.getByRole("button", { name: /Reset \(/ }).click();
  await waitText(a, ".telemetry .mode", /Idle/, 5000);
  console.log("Gamepad: dead man, manual slew, release, E-STOP OK");

  // Hand-controller layout on a phone.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, timezoneId: "UTC" });
  const p = await phone.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(base + "/?view=hand");
  await waitText(p, ".hand-status", /idle|tracking|manual/i);
  p.on("dialog", (d) => d.accept());
  await p.getByRole("button", { name: /Take (control|over)/ }).click();
  await waitText(p, ".hand", /Track|Reset/);
  const up = p.locator(".manual.big .pad.up");
  const box = await up.boundingBox();
  if (!box || box.height < 60) throw new Error("hand-controller pad buttons are too small");
  await up.dispatchEvent("pointerdown", { pointerId: 1 });
  await waitText(p, ".hand-status", /manual/i, 5000);
  await up.dispatchEvent("pointerup", { pointerId: 1 });
  await waitText(p, ".hand-status", /idle/i, 5000);
  await p.screenshot({ path: `${shots}/hand-controller.png` });
  console.log("Hand controller OK");

  const real = errors.filter((e) => !/favicon|Failed to load resource/.test(e));
  if (real.length) {
    console.error("Browser errors:", real);
    process.exit(1);
  }
  await browser.close();
  console.log("Features E2E OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
