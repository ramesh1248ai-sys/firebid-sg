import { expect, type Page, test } from "@playwright/test";

/**
 * End to end through the real stack: Keycloak issues the token, the API enforces who may see
 * what, and the browser only ever talks to its own origin.
 */
const PASSWORD = "firebid-dev";

async function signIn(page: Page, username: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "Your bids" })).toBeVisible();
}

function uniqueReference() {
  return `MC/E2E/${Date.now()}`;
}

test("someone signed out is asked to sign in", async ({ page }) => {
  await page.goto("/bids/new");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a bid manager registers a bid and sees it with its deadlines", async ({ page }) => {
  await signIn(page, "bid.manager@firebid.test");
  const reference = uniqueReference();

  await page.getByRole("link", { name: "New bid" }).click();
  await page.getByLabel("Project").fill("Marina Bay Commercial Tower");
  await page.getByLabel("Client").fill("Main Contractor Pte Ltd");
  await page.getByLabel("Tender reference").fill(reference);
  await page.getByLabel("Submission deadline").fill("2027-03-01T17:00");
  await page.getByLabel("Clarifications close").fill("2027-02-15T17:00");
  await page.getByRole("button", { name: "Register bid" }).click();

  // The detail page opens on the new bid, with its human-readable identifier.
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toContainText(/^BID-\d{4}-\d+$/);
  const humanId = (await heading.textContent()) ?? "";

  await page.getByRole("link", { name: "Bids" }).click();
  const row = page.getByRole("row", { name: new RegExp(humanId) });
  await expect(row).toContainText("01 Mar 2027");
  await expect(row).toContainText("15 Feb 2027");
  await expect(row).toContainText(reference);

  // Registering the bid is in the history, with who did it.
  await page.getByRole("link", { name: "History" }).click();
  await expect(page.getByRole("cell", { name: "bid: created" }).first()).toBeVisible();
});

test("an estimator on no bids sees none of them", async ({ page }) => {
  await signIn(page, "estimator@firebid.test");
  await expect(page.getByText("You are not on any bids yet")).toBeVisible();
});

test("a bid you are not on is not found, even by its address", async ({ browser }) => {
  const manager = await browser.newContext();
  const managerPage = await manager.newPage();
  await signIn(managerPage, "bid.manager@firebid.test");

  await managerPage.getByRole("link", { name: "New bid" }).click();
  await managerPage.getByLabel("Project").fill("Jurong Logistics Hub");
  await managerPage.getByLabel("Client").fill("Another Contractor Pte Ltd");
  await managerPage.getByLabel("Tender reference").fill(uniqueReference());
  await managerPage.getByLabel("Submission deadline").fill("2027-04-01T17:00");
  await managerPage.getByRole("button", { name: "Register bid" }).click();
  await expect(managerPage.getByRole("heading", { level: 1 })).toContainText(/^BID-/);
  const address = new URL(managerPage.url()).pathname;
  await manager.close();

  const outsider = await browser.newContext();
  const outsiderPage = await outsider.newPage();
  await signIn(outsiderPage, "estimator@firebid.test");
  await outsiderPage.goto(address);
  await expect(outsiderPage.getByRole("heading", { name: "Bid not found" })).toBeVisible();
  await outsider.close();
});
