import { expect, test } from "@playwright/test";

test.describe("login flow", () => {
  test("keeps the success state visible until the destination page takes over", async ({ page }) => {
    await page.addInitScript(() => {
      window.addEventListener("beforeunload", () => {
        const button = document.querySelector<HTMLButtonElement>("button[type='submit']");
        window.sessionStorage.setItem(
          "login-button-before-unload",
          JSON.stringify({
            text: button?.textContent?.trim() ?? "",
            disabled: button?.disabled ?? false,
          }),
        );
      });
    });

    await page.route("**/api/auth/login", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true }),
      });
    });
    await page.route("**/cases", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 750));
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><head><meta charset='utf-8'></head><body><h1>服务单</h1></body></html>",
      });
    });

    await page.goto("/login");
    await page.fill("#username", "admin");
    await page.fill("#password", "correct-password");
    await page.getByRole("button", { name: "登 录" }).click({ noWaitAfter: true });
    await expect(page.getByRole("heading", { name: "服务单" })).toBeVisible();

    const buttonStateBeforeUnload = await page.evaluate(() => {
      const stored = window.sessionStorage.getItem("login-button-before-unload");
      return stored ? JSON.parse(stored) : null;
    });
    expect(buttonStateBeforeUnload).toEqual({
        text: "登录成功，正在进入系统…",
        disabled: true,
    });
  });

  test("restores the form and shows the server error after a rejected login", async ({ page }) => {
    await page.route("**/api/auth/login", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "用户名或密码错误" }),
      });
    });

    await page.goto("/login");
    await page.fill("#username", "admin");
    await page.fill("#password", "wrong-password");
    await page.getByRole("button", { name: "登 录" }).click();

    await expect(page.locator("form [role='alert']")).toHaveText("用户名或密码错误");
    await expect(page.getByRole("button", { name: "登 录" })).toBeEnabled();
    await expect(page.locator("#password")).toBeEnabled();
  });
});

test.describe("login form before hydration", () => {
  test.use({ javaScriptEnabled: false });

  test("never places credentials in the URL", async ({ page }) => {
    await page.goto("/login");
    await page.fill("#username", "admin");
    await page.fill("#password", "private-password");
    const formRequest = page.waitForRequest(
      (request) =>
        new URL(request.url()).pathname === "/login" &&
        request.method() === "POST",
    );

    await page.getByRole("button", { name: "登 录" }).click();
    const request = await formRequest;

    expect(request.method()).toBe("POST");
    expect(new URL(request.url()).search).toBe("");
  });
});
