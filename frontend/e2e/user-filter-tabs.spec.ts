import { expect, test } from "@playwright/test";

test.describe("user filter tabs", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    if (await page.locator("#username").isVisible()) {
      await page.fill("#username", process.env.ADMIN_USERNAME || "admin");
      await page.fill("#password", process.env.ADMIN_PASSWORD || "change-me");
      await page.click('button[type="submit"]');
      await page.waitForURL((url) => !url.pathname.endsWith("/login"), {
        waitUntil: "commit",
      });
    }
  });

  test("show immediate pending feedback for all three transitions", async ({ page }) => {
    await page.goto("/users", { waitUntil: "domcontentloaded" });
    const tabs = page.getByRole("navigation", { name: "用户范围筛选" });
    await expect(tabs).toBeVisible();

    let delayedRequests = 0;
    await page.route("**/users?*", async (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "GET" && url.searchParams.has("_rsc")) {
        delayedRequests += 1;
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
      await route.continue();
    });

    const transitions = [
      {
        href: "/users?active=1",
        settledLabel: "活跃用户",
        heading: "活跃用户",
        currentLabel: "正常用户",
      },
      {
        href: "/users?deleted=1",
        settledLabel: "已删除",
        heading: "已删除用户",
        currentLabel: "活跃用户",
      },
      {
        href: "/users",
        settledLabel: "正常用户",
        heading: "正常用户",
        currentLabel: "已删除",
      },
    ];

    for (const transition of transitions) {
      const target = tabs.locator(`a[href="${transition.href}"]`);
      const current = tabs.getByRole("link", {
        name: transition.currentLabel,
        exact: true,
      });

      await target.click({ noWaitAfter: true });

      await expect(target).toContainText("加载中…");
      await expect(target).toHaveAttribute("aria-busy", "true");
      await expect(tabs).toHaveAttribute("aria-busy", "true");
      await expect(current).toHaveAttribute("aria-current", "page");
      const links = await tabs.locator("a").all();
      expect(links).toHaveLength(3);
      for (const link of links) {
        await expect(link).toHaveAttribute("aria-disabled", "true");
      }

      const targetUrl = new URL(transition.href, "http://internal");
      await page.waitForURL(
        (url) => url.pathname === targetUrl.pathname && url.search === targetUrl.search,
        { waitUntil: "commit" },
      );
      await expect(
        page.getByRole("heading", { name: transition.heading, exact: true }),
      ).toBeVisible();
      await expect(target).toHaveText(transition.settledLabel);
      await expect(target).toHaveAttribute("aria-current", "page");
      await expect(target).not.toHaveAttribute("aria-busy", "true");
      await expect(tabs).toHaveAttribute("aria-busy", "false");
    }

    expect(delayedRequests).toBe(3);
  });
});
