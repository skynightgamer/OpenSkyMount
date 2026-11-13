// E2E for the welcome screen, the setup guide and accounts. Usage: node e2e/accounts.cjs <baseUrl> [shotDir]
// The server must be fresh (no accounts, no shared password) and reached via 127.0.0.1, because only the computer
// running OpenSkyMount may create the first admin account.
const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8771";
const shots = process.argv[3];
const ADMIN = { user: "root", name: "Night Owl", pw: "orion-nebula-42" };
const OP = { user: "op", pw: "vega-lyra-2026" };
const OBS = { user: "watcher", pw: "watch-only-99" };

async function login(page, u) {
  await page.getByLabel("User name").fill(u.user);
  await page.getByLabel("Password").fill(u.pw);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.locator(".telemetry").waitFor({ timeout: 20000 });
}

async function logoutVia(page) {
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Log out" }).click();
  await page.getByRole("button", { name: "Log in" }).waitFor();
}

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(e.message));

  // 1. First visit: welcome screen, then the setup guide.
  await page.goto(base);
  await page.getByRole("dialog", { name: "OpenSkyMount" }).waitFor();
  await page.getByText("Running on the built-in simulator").waitFor();
  if (shots) await page.screenshot({ path: `${shots}/welcome.png` });
  await page.getByRole("button", { name: "Start the setup guide" }).click();
  const guide = page.getByRole("complementary", { name: "Simulator setup guide" });
  await guide.waitFor();
  await page.getByRole("dialog", { name: "OpenSkyMount" }).waitFor({ state: "hidden" });
  const beforeDrag = await guide.boundingBox();
  const handle = guide.locator(".guide-handle");
  const handleBox = await handle.boundingBox();
  await page.mouse.move(handleBox.x + 30, handleBox.y + 15);
  await page.mouse.down();
  await page.mouse.move(handleBox.x - 70, handleBox.y - 45, { steps: 5 });
  await page.mouse.up();
  const afterDrag = await guide.boundingBox();
  if (afterDrag.x >= beforeDrag.x - 50 || afterDrag.y >= beforeDrag.y - 30) throw new Error("guide did not move when dragged");
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  if ((await guide.boundingBox()).x <= afterDrag.x) throw new Error("guide did not move with keyboard");
  await guide.getByRole("button", { name: "Take control", exact: true }).click();
  await guide.getByText("1/7 done").waitFor(); // detected from the live control state
  await guide.getByRole("button", { name: "Next" }).click();
  await guide.getByRole("button", { name: /^Keep / }).click();
  await guide.getByText("2/7 done").waitFor();
  await guide.getByRole("button", { name: "Next" }).click();
  await guide.getByRole("button", { name: /Compact camera/ }).click();
  await guide.getByText("3/7 done").waitFor();
  // The guide survives a reload, and the welcome screen does not come back.
  await page.reload();
  await guide.getByText("Choose a payload").waitFor();
  if (await page.getByRole("dialog", { name: "OpenSkyMount" }).count()) throw new Error("welcome screen shown twice");
  await guide.getByRole("button", { name: "Close guide" }).click();

  await page.getByRole("button", { name: "Night mode" }).click();
  if (!(await page.getByRole("button", { name: "Night mode" }).getAttribute("aria-pressed") === "true")) throw new Error("night mode did not turn on");
  await page.getByRole("button", { name: "Night mode" }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("tab", { name: "About" }).click();
  await page.getByRole("link", { name: "OpenSkyMount repository" }).waitFor();
  const version = await page.evaluate(() => fetch("/api/health").then((r) => r.json()).then((health) => health.version));
  if (!(await page.getByRole("tabpanel").innerText()).includes(version)) throw new Error("version missing from About");
  await page.getByRole("dialog", { name: "Settings" }).getByRole("button", { name: "Close" }).click();

  // 2. Create the first admin account from the account menu.
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Create admin account" }).click();
  await page.getByLabel("User name").fill(ADMIN.user);
  await page.getByLabel("Display name (optional)").fill(ADMIN.name);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN.pw);
  await page.getByLabel("Repeat password").fill(ADMIN.pw);
  await page.getByRole("button", { name: "Create admin account and turn on sign-in" }).click();
  await page.getByRole("button", { name: "Account" }).filter({ hasText: ADMIN.name }).waitFor({ timeout: 20000 });
  await page.locator(".telemetry").waitFor();
  const authState = await page.evaluate(() => fetch("/api/auth").then((r) => r.json()));
  if (authState.mode !== "accounts" || authState.user.role !== "admin") throw new Error(`unexpected auth ${JSON.stringify(authState)}`);

  // A second browser (no login) now has to log in.
  const other = await (await browser.newContext()).newPage();
  other.on("pageerror", (e) => errors.push(e.message));
  await other.addInitScript(() => localStorage.setItem("osm-welcome", "1"));
  await other.goto(base);
  await other.getByRole("button", { name: "Log in" }).waitFor();

  // 3. Admin adds an operator and an observer.
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Manage users" }).click();
  for (const [u, role] of [[OP, "Operator"], [OBS, "Observer"]]) {
    await page.getByRole("button", { name: "Add user" }).click();
    const form = page.locator(".add-user");
    await form.getByLabel("User name").fill(u.user);
    await form.getByLabel("Role").selectOption({ label: role });
    await form.getByLabel("Password").fill(u.pw);
    await form.getByRole("button", { name: "Create user" }).click();
    await page.locator(".users").getByText(`@${u.user}`).waitFor();
  }
  if (shots) await page.screenshot({ path: `${shots}/users.png` });
  await page.getByRole("button", { name: "Close" }).click();

  // 4. The observer can watch and E-STOP, but not take control.
  await other.getByLabel("User name").fill(OBS.user);
  await other.getByLabel("Password").fill(OBS.pw);
  await other.getByRole("button", { name: "Log in" }).click();
  await other.locator(".authority .badge").filter({ hasText: "observer" }).waitFor({ timeout: 20000 });
  if (await other.locator(".authority").getByRole("button", { name: "Take control" }).count()) throw new Error("observer offered Take control");
  await other.locator(".control-head").getByRole("button", { name: "E-STOP" }).click();
  await page.waitForFunction(() => /E-STOP/.test(document.querySelector(".telemetry .mode")?.textContent || ""), null, { timeout: 10000 });
  // The admin's socket was reconnected when sign-in was turned on, so control has to be taken again before Reset.
  await page.locator(".authority").getByRole("button", { name: /Take control|Take over/ }).click();
  await page.locator(".latched").getByRole("button", { name: /Reset/ }).click();
  await page.locator(".latched").waitFor({ state: "detached" });

  // 5. Deleting the observer logs that browser out at once.
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Manage users" }).click();
  page.once("dialog", (d) => d.accept());
  await page.locator(".users li").filter({ hasText: `@${OBS.user}` }).getByRole("button", { name: "Delete" }).click();
  await other.getByRole("button", { name: "Log in" }).waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "Close" }).click();

  // 6. Operator logs in and changes their password; the old one stops working.
  await login(other, OP);
  await other.getByRole("button", { name: "Account" }).click();
  await other.getByRole("menuitem", { name: "Account settings" }).click();
  await other.getByRole("tab", { name: "Password & logins" }).click();
  await other.getByLabel("Current password").fill(OP.pw);
  await other.getByLabel("New password", { exact: true }).fill("brand-new-pass-1");
  await other.getByLabel("Repeat new password").fill("brand-new-pass-1");
  await other.getByRole("button", { name: "Change password" }).click();
  await other.getByText("Password changed").waitFor();
  await other.getByRole("button", { name: "Close" }).click();
  await logoutVia(other);
  await other.getByLabel("User name").fill(OP.user);
  await other.getByLabel("Password").fill(OP.pw);
  await other.getByRole("button", { name: "Log in" }).click();
  await other.getByRole("alert").filter({ hasText: "wrong user name or password" }).waitFor();
  await login(other, { user: OP.user, pw: "brand-new-pass-1" });
  await other.locator(".authority").getByRole("button", { name: /Take control|Take over/ }).waitFor();

  // 7. Admin logs out.
  await logoutVia(page);
  if (shots) await page.screenshot({ path: `${shots}/login.png` });

  await browser.close();
  if (errors.length) {
    console.error("Browser errors:", errors);
    process.exit(1);
  }
  console.log("Accounts E2E OK");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
