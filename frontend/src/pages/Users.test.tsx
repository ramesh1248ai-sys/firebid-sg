import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { signedInAs } from "@/test/fake-oidc";
import { renderAt, stubApi } from "@/test/harness";

/**
 * The People page. What matters: a system administrator sees everyone with the roles they
 * hold here and sets them; nobody edits their own access; a refusal is shown as the API
 * words it; and the menus follow the roles the application holds, not the token's.
 */

const ADAM = "aaaaaaaa-0000-4000-8000-000000000001";
const ESTHER = "eeeeeeee-0000-4000-8000-000000000002";

function person(overrides: Record<string, unknown> = {}) {
  return {
    user_id: ESTHER,
    username: "estimator@firebid.test",
    display_name: "Esther Tan",
    is_active: true,
    roles: ["estimator"],
    bids: 2,
    first_signed_in_at: "2026-10-01T03:00:00Z",
    ...overrides,
  };
}

const adam = person({
  user_id: ADAM,
  username: "admin@firebid.test",
  display_name: "Adam Ng",
  roles: ["system_admin"],
  bids: 0,
});
const me = (roles: string[], user_id = ADAM) =>
  Response.json({ user_id, username: "admin@firebid.test", display_name: "Adam Ng", roles });
const ROLES = ["estimator", "senior_estimator", "bid_manager", "system_admin"];

function asAdmin() {
  signedInAs({ name: "Adam Ng", preferred_username: "admin@firebid.test", roles: ["system_admin"] });
}

describe("the people page", () => {
  // req: FR-ADM-01
  it("lists everyone with their roles, their bids and whether their access is on", async () => {
    asAdmin();
    stubApi({
      "/api/me": () => me(["system_admin"]),
      "/admin/users/roles": () => Response.json(ROLES),
      "/admin/users": () =>
        Response.json([adam, person(), person({ user_id: "x", display_name: "Olga Lee", is_active: false, roles: [] })]),
    });
    renderAt("/people");

    const table = await screen.findByRole("table", { name: "People" });
    const esther = within(table).getByText("Esther Tan").closest("tr")!;
    expect(esther).toHaveTextContent("estimator@firebid.test");
    expect(esther).toHaveTextContent("estimator");
    expect(esther).toHaveTextContent("2");
    expect(esther).toHaveTextContent("On");
    const olga = within(table).getByText("Olga Lee").closest("tr")!;
    expect(olga).toHaveTextContent("No role");
    expect(olga).toHaveTextContent("Switched off");
  });

  // req: FR-ADM-01
  it("sets the roles someone holds and switches their access off", async () => {
    asAdmin();
    const calls = stubApi({
      "/api/me": () => me(["system_admin"]),
      "/admin/users/roles": () => Response.json(ROLES),
      [`/admin/users/${ESTHER}`]: () =>
        Response.json(person({ roles: ["estimator", "senior_estimator"], is_active: false })),
      "/admin/users": () => Response.json([adam, person()]),
    });
    renderAt("/people");

    await userEvent.click(await screen.findByRole("button", { name: "Edit Esther Tan" }));
    const form = screen.getByRole("form", { name: "Access of Esther Tan" });
    expect(within(form).getByLabelText("estimator")).toBeChecked();
    expect(within(form).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(within(form).getByLabelText("senior estimator"));
    await userEvent.click(within(form).getByLabelText(/Access on/));
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const sent = calls.find((c) => c.method === "PATCH")!;
    expect(sent.url).toContain(`/admin/users/${ESTHER}`);
    expect(JSON.parse(sent.body!)).toEqual({
      roles: ["estimator", "senior_estimator"],
      is_active: false,
    });
    await waitFor(() => expect(screen.queryByRole("form")).toBeNull());
  });

  // req: FR-ADM-01
  it("does not offer an administrator their own access, and shows a refusal as worded", async () => {
    asAdmin();
    stubApi({
      "/api/me": () => me(["system_admin"]),
      "/admin/users/roles": () => Response.json(ROLES),
      [`/admin/users/${ESTHER}`]: () =>
        Response.json({ detail: "the organisation keeps at least one system administrator" }, { status: 409 }),
      "/admin/users": () => Response.json([adam, person()]),
    });
    renderAt("/people");

    const table = await screen.findByRole("table", { name: "People" });
    const own = within(table).getByText("Adam Ng").closest("tr")!;
    await waitFor(() => expect(own).toHaveTextContent("another administrator changes your access"));
    expect(screen.queryByRole("button", { name: "Edit Adam Ng" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Edit Esther Tan" }));
    await userEvent.click(screen.getByLabelText("bid manager"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the organisation keeps at least one system administrator",
    );
  });

  // req: FR-ADM-01
  it("is in the menu by the roles held here, not the token's", async () => {
    // The token still says estimator; the application has made them an administrator.
    signedInAs({ name: "Adam Ng", preferred_username: "admin@firebid.test", roles: ["estimator"] });
    stubApi({
      "/api/me": () => me(["system_admin"]),
      "/admin/users/roles": () => Response.json(ROLES),
      "/admin/users": () => Response.json([adam]),
    });
    renderAt("/people");

    expect(await screen.findByRole("link", { name: "People" })).toBeVisible();
    expect(await screen.findByRole("table", { name: "People" })).toBeVisible();
  });

  // req: FR-ADM-01
  it("is not shown to someone who is not a system administrator", async () => {
    signedInAs({ name: "Esther Tan", preferred_username: "estimator@firebid.test", roles: ["system_admin"] });
    const calls = stubApi({ "/api/me": () => me(["estimator"], ESTHER) });
    renderAt("/people");

    expect(await screen.findByText("Only a system administrator sees this page.")).toBeVisible();
    expect(screen.queryByRole("link", { name: "People" })).toBeNull();
    expect(calls.some((c) => c.url.includes("/admin/users"))).toBe(false);
  });

  // req: FR-ADM-01
  it("tells someone whose access is switched off, in place of any page", async () => {
    signedInAs({ name: "Olga Lee", preferred_username: "olga@firebid.test", roles: ["estimator"] });
    stubApi({
      "/api/me": () =>
        Response.json(
          { detail: "your access has been switched off: ask a system administrator" },
          { status: 403 },
        ),
    });
    renderAt("/");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "your access has been switched off: ask a system administrator",
    );
  });
});
