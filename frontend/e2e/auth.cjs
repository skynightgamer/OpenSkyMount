// E2E for password-protected LAN mode. Usage: node e2e/auth.cjs <baseUrl> <password>
// The server must be started with OPENSKYMOUNT_PASSWORD=<password>.
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8767";
const password = process.argv[3];

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(base);
  await page.getByRole("button", { name: "Log in" }).waitFor();
  if (await page.locator(".telemetry").count()) throw new Error("controls visible before login");

  // E-stop is accepted without the password.
  await page.getByRole("button", { name: "E-STOP" }).click();
  await page.getByText("Emergency stop sent").waitFor();

  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("alert").filter({ hasText: "wrong password" }).waitFor();

  await page.getByLabel("Password").fill(password);
  // The sky catalog must load after logging in (a 401 from before the login must not be cached).
  const catalog = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/catalog" && r.status() === 200, { timeout: 20000 });
  await page.getByRole("button", { name: "Log in" }).click();
  await catalog;
  await page.waitForFunction(() => /E-STOP/.test(document.querySelector(".telemetry .mode")?.textContent || ""), null, { timeout: 20000 });
  console.log("Logged in; mount mode after login-screen E-STOP:", await page.locator(".telemetry .mode").innerText());

  // Session survives a reload (cookie), and log out returns to the login screen.
  await page.reload();
  await page.locator(".telemetry .mode").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Log out" }).click();
  await page.getByRole("button", { name: "Log in" }).waitFor();
  const status = await page.evaluate(() => fetch("/api/config").then((r) => r.status));
  if (status !== 401) throw new Error(`API still reachable after logout: ${status}`);

  await browser.close();
  if (errors.length) {
    console.error("Browser errors:", errors);
    process.exit(1);
  }
  console.log("Auth E2E OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
