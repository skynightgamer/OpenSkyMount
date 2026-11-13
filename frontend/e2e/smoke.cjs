// End-to-end smoke test of the real UI against a running server.
// Usage: node e2e/smoke.cjs [baseUrl] [screenshotDir]
// Requires Playwright with Chromium (pre-installed in the Claude Code cloud environment).
const { chromium } = require("playwright");

/** New browser context that has already seen the welcome screen (covered by e2e/accounts.cjs). */
async function newCtx(browser, opts) {
  const c = await browser.newContext(opts);
  await c.addInitScript(() => localStorage.setItem("osm-welcome", "1"));
  return c;
}

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

async function checkIdleMountMarker(browser) {
  // At maximum FOV in this wide, shallow sky view, the parked mount is visible 90° from the centre.
  const ctx = await newCtx(browser, { viewport: { width: 1440, height: 430 }, reducedMotion: "reduce" });
  try {
    await ctx.addInitScript(() => {
      const fillText = CanvasRenderingContext2D.prototype.fillText;
      let mountDrawn = false;
      window.idleMarkerFrames = [];
      CanvasRenderingContext2D.prototype.fillText = function (text, ...args) {
        if (this.canvas.matches(".sky-canvas")) {
          if (text === "mount") mountDrawn = true;
          if (text.includes("  ·  FOV ")) {
            if (text.includes("FOV 170°") && text.includes("facing E 90°, alt 25°")) {
              window.idleMarkerFrames.push(mountDrawn);
            }
            mountDrawn = false;
          }
        }
        return fillText.call(this, text, ...args);
      };
    });
    const page = await ctx.newPage();
    await page.goto(base);
    await waitText(page, ".telemetry .mode", /Idle/);
    await page.getByRole("button", { name: "Face E" }).click();
    await page.getByRole("button", { name: "Zoom out" }).click();
    await page.getByRole("button", { name: "Zoom out" }).click();
    await page.waitForFunction(() => window.idleMarkerFrames.length >= 75);
    const stable = await page.evaluate(() => window.idleMarkerFrames.every(Boolean));
    if (!stable) throw new Error("mount marker dropped out while idle");
  } finally {
    await ctx.close();
  }
}

(async () => {
  const browser = await chromium.launch();
  await checkIdleMountMarker(browser);
  const errors = [];
  const ctxA = await newCtx(browser, { viewport: { width: 1440, height: 900 } });
  const ctxB = await newCtx(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const a = await ctxA.newPage();
  const b = await ctxB.newPage();
  for (const p of [a, b]) {
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  }
  let figureRequests = 0;
  a.on("request", (r) => /\/figures\/[A-Za-z]+\.png/.test(r.url()) && figureRequests++);
  await a.goto(base);
  await b.goto(base);
  await waitText(a, ".conn", /Simulator connected/);
  await waitText(b, ".conn", /Simulator connected/);

  // Constellation figures: the pictures are served as static files and the "Figures" layer is on by default,
  // switchable, and remembered per device.
  const pic = await a.request.get(base + "/figures/Ori.png");
  if (!pic.ok() || !(pic.headers()["content-type"] || "").includes("image/png")) throw new Error("figure picture not served");
  const figuresChip = a.getByRole("checkbox", { name: "Figures" });
  if (!(await figuresChip.isChecked())) throw new Error("Figures layer should be on by default");
  await figuresChip.uncheck();
  if ((await a.evaluate(() => JSON.parse(localStorage.getItem("osm-layers") || "{}").figures)) !== false) throw new Error("Figures choice not remembered");
  await figuresChip.check();

  // Window A takes control and moves the simulated clock to a night when Jupiter is up.
  await a.getByRole("button", { name: "Take control" }).click();
  await waitText(a, ".authority", /You have control/);
  await a.locator(".clock summary").click(); // collapsed by default
  await a.fill('.clock input[type="datetime-local"]', "2026-11-01T02:00");
  await a.getByRole("button", { name: "×10" }).click();
  await waitText(a, ".clock-read", /simulated ×10/);

  // Tonight's planner is a popover over the sky map, closed by default.
  if (await a.locator(".tonight-pop").count()) throw new Error("Best tonight should start closed");
  await a.locator(".tonight-toggle").click();
  await a.waitForSelector(".tonight-pop .tonight-list li", { timeout: 20000 });
  // It is night in the simulation, so the sky map has fetched constellation pictures.
  await a.waitForTimeout(500);
  if (!figureRequests) throw new Error("no constellation pictures were requested at night");
  console.log("Figure pictures requested:", figureRequests);
  console.log("Tonight top:", (await a.locator(".tonight-list li").first().innerText()).replace(/\s+/g, " "));
  await a.keyboard.press("Escape");
  if (await a.locator(".tonight-pop").count()) throw new Error("Escape should close Best tonight");

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
  await waitText(a, ".control-head .status", /Jupiter/);
  // The E-STOP/Stop head of the control column stays on screen while the column scrolls.
  await a.locator(".control-col").evaluate((c) => (c.scrollTop = c.scrollHeight));
  const estopBox = await a.locator(".control-head .estop").boundingBox();
  if (!estopBox || estopBox.y < 0 || estopBox.y + estopBox.height > 900) throw new Error("E-STOP scrolled out of view");
  await a.locator(".control-col").evaluate((c) => (c.scrollTop = 0));

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
  // Opened/closed sections are remembered on this device.
  await a.reload();
  await a.waitForSelector("details.clock[open]");
  if (await a.locator("details.alignment[open]").count()) throw new Error("alignment section should stay collapsed");

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
