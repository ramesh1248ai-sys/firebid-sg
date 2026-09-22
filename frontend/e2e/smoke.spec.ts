import { expect, test } from "@playwright/test";

test("the shell loads and reaches the API through the same origin", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("FireBid SG");
  await expect(page.getByRole("heading", { name: "FireBid SG" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("API connected");
});

test("the sign-in placeholder is reachable from the navigation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeDisabled();
});
