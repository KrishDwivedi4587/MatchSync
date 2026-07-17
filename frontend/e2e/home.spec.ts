import { expect, test } from "@playwright/test";

// End-to-end smoke test: the app boots and serves the landing page.
test("landing page loads", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "MatchSync" })).toBeVisible();
});
