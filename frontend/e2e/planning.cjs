// End-to-end test of the planning features against a running server: night-mode brightness, constellation stories and
// clicking a figure, the sky events calendar, the slew preview, the guided star alignment and the mosaic/timelapse planner.
// Usage: node e2e/planning.cjs [baseUrl] [screenshotDir]
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8773";
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
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await ctx.addInitScript(() => localStorage.setItem("osm-welcome", "1"));
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base);
  await waitText(page, ".telemetry .mode", /Idle/);
  await page.getByRole("button", { name: "Take control" }).click();
  await waitText(page, ".authority", /You have control/);
  // A dark night with Cygnus, Capella and the winter stars up (Jupiter is still below the horizon).
  await page.locator(".clock summary").click();
  await page.fill('.clock input[type="datetime-local"]', "2026-11-01T21:00");
  await waitText(page, ".clock-read", /21:00/);

  // ---- constellations: search by English name, the story, click a painted figure
  await page.fill('input[type="search"]', "Swan");
  await page.locator(".hits button").first().click();
  await waitText(page, ".target h2", /Cygnus/);
  await page.waitForSelector(".target .lore");
  const lore = await page.locator(".target .lore").innerText();
  if (!/Swan/.test(lore) || !/Zeus|Orpheus|Deneb/.test(lore)) throw new Error("constellation story missing: " + lore);
  await page.fill('input[type="search"]', "Jupiter");
  await page.locator(".hits button").first().click();
  await waitText(page, ".target h2", /Jupiter/);
  await page.waitForTimeout(2500); // the pictures load
  const box = await page.locator(".sky-canvas").boundingBox();
  let hit = null;
  for (let dy = -240; dy <= 240 && !hit; dy += 30) {
    for (let dx = -240; dx <= 240 && !hit; dx += 30) {
      await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
      await page.waitForTimeout(60);
      if (await page.locator(".target .lore").count()) hit = await page.locator(".target h2").innerText();
    }
  }
  if (!hit) throw new Error("clicking the sky never selected a constellation figure");
  console.log("Clicked figure:", hit);

  // ---- sky events
  await page.locator(".section.sky-events summary").click();
  await page.waitForSelector(".section.sky-events .ev", { timeout: 20000 });
  const rows = await page.locator(".section.sky-events .ev").count();
  if (rows < 3) throw new Error(`only ${rows} sky events`);
  console.log("Sky events:", rows, "|", await page.locator(".section.sky-events .sec-sum").innerText());
  await page.locator(".section.sky-events .ev").first().getByRole("button", { name: "Show" }).first().click().catch(() => {});

  // ---- slew preview (Capella is up in the north-east at 21:00)
  await page.fill('input[type="search"]', "Capella");
  await page.locator(".hits button").first().click();
  await waitText(page, ".target h2", /Capella/);
  await page.waitForSelector(".preview .pv-head", { timeout: 15000 });
  const preview = await page.locator(".preview").innerText();
  if (!/≈ \d+ s|≈ \d+ min/.test(preview) || !/cable|−270/i.test(preview)) throw new Error("slew preview incomplete: " + preview);
  console.log("Preview:", preview.replace(/\n/g, " | ").slice(0, 160));

  // ---- guided star alignment
  await page.locator(".section.alignment summary").click();
  await page.getByRole("button", { name: "Align with stars (guided)" }).click();
  await page.waitForSelector(".wiz-stars li");
  const stars = await page.locator(".wiz-stars li").count();
  if (stars !== 3) throw new Error(`expected 3 alignment stars, got ${stars}`);
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: /^Go to / }).first().click();
    await waitText(page, ".telemetry .mode", /Tracking/);
    await page.waitForTimeout(500);
    await page.getByRole("button", { name: /^Sync:/ }).click();
    await page.waitForTimeout(600);
  }
  await page.waitForSelector("text=Alignment finished");
  console.log("Alignment:", (await page.locator(".wiz").innerText()).replace(/\n/g, " | ").slice(0, 140));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("button", { name: "Clear model" }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();

  // ---- mosaic and timelapse
  await page.fill('input[type="search"]', "Swan");
  await page.locator(".hits button").first().click();
  await waitText(page, ".target h2", /Cygnus/);
  await page.locator(".section.mosaic summary").click();
  await page.waitForSelector(".mz-result");
  await waitText(page, ".mz-result", /6 frames/);
  await page.getByRole("button", { name: /^Add \d+ frames? to the session/ }).click();
  await waitText(page, ".section.session .sec-sum", /\d+ targets?/);
  await page.getByRole("tab", { name: "Timelapse" }).click();
  await page.waitForSelector(".mz-result dl");
  const lapse = await page.locator(".mz-result").innerText();
  if (!/360 frames/.test(lapse) || !/Field rotation/.test(lapse)) throw new Error("timelapse plan incomplete: " + lapse);
  await page.screenshot({ path: `${shots}/planning.png` });

  // ---- night mode brightness and the E-STOP button
  await page.getByRole("button", { name: "Night mode" }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("Night mode brightness").fill("40");
  const filter = await page.evaluate(() => getComputedStyle(document.documentElement).filter);
  if (!/brightness\(0\.4\)/.test(filter)) throw new Error("night brightness not applied: " + filter);
  if (!(await page.evaluate(() => JSON.parse(localStorage.getItem("osm-display") || "{}").nightLevel === 0.4))) throw new Error("night brightness not remembered");
  await page.getByRole("button", { name: "Cancel" }).click();
  const estop = await page.evaluate(() => {
    const el = document.querySelector(".control-head .estop");
    return el ? getComputedStyle(el).backgroundColor : null;
  });
  if (!estop || !/^rgb\((2[0-5]\d), (2[0-5]\d|1[89]\d), (2[0-5]\d|1[89]\d)\)$/.test(estop)) throw new Error("E-STOP is not drawn pale in night mode: " + estop);

  if (errors.length) throw new Error("page errors: " + errors.join("; "));
  await browser.close();
  console.log("Planning E2E OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
