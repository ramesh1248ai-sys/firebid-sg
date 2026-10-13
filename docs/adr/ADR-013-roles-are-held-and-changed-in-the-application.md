# ADR-013: A person's roles are held and changed in the application

- **Status:** Accepted on 13 Oct 2026 by Ramesh R, who asked for the screen and chose option 2. Built the same day.
- **Date:** 2026-10-13
- **Deciders:** Ramesh R; the DPO and the Commercial Director have not been asked
- **Requirements:** FR-ADM-01; guardrails 2 and 8; NFR-08

## Context

The identity provider (Microsoft Entra ID; Keycloak in the local stack) says who someone is.
`project-context.md` has said from the start that "bid membership and roles live in the app
database", and the `user_role` table is there. But as built, a person's roles were copied
from the token's `roles` claim at every sign-in, so the table was a mirror of the identity
provider: a role could only be changed there, by whoever administers the directory, and a
change made in the database was undone the next time the person signed in.

Two things came of that:

- The people who know who is a senior estimator this month are not the people who administer
  the directory. A role change was an IT request.
- Nothing in the application could take someone's access away. `app_user.is_active` existed
  and only kept a person out of the list of people a bid manager may add.

A role is what lets a person approve a gate (guardrail 2), so who may grant one matters as
much as who holds one.

## Options

1. **Roles stay the identity provider's.** The application shows them and can switch a
   person off. No change to where authority lies; every role change stays a directory
   request.
2. **Roles are the application's; identity stays the provider's.** The token's roles are
   taken once, at a person's first sign-in. After that a system administrator sets them on a
   screen. The name and sign-in name are still refreshed from the token.
3. **Roles and names are the application's.** As 2, and a display name can be edited too. A
   person renamed in the directory keeps their old name here, and approvals and the audit
   trail carry a name the organisation's directory does not.

## Recommendation

**Option 2.**

- **Sign-in.** A person is created at first sign-in with the application roles their token
  carries. At every later sign-in their roles are read from `user_role`; the token's are not
  looked at, but for the one case below. A person whose access is switched off is answered
  403 on every request.
- **Who may change them.** Only a system administrator (`user.manage`). Granting a role
  grants what it may approve, so the commercial director, who sees the platform page, does
  not.
- **Two rules against locking the screen.** Nobody changes their own access. And the
  organisation keeps at least one system administrator whose access is on.
- **Recovery.** Where the organisation has no system administrator who can sign in, a token
  that carries `system_admin` is believed. A new installation gets its first administrator
  that way, and one that has lost them all can be given one from the directory.
- **Every change is on the audit log,** on the organisation's chain: who changed whom, the
  roles before and after, access switched off or on. The person is named by id.
- **The screens follow the application.** They showed and hid things by the token's roles.
  `GET /me` now says who the caller is and what roles they hold here, and the screens use
  that. The API decided every request before and still does.

A role on a bid (`bid_member.role`) is not touched: it is set on the bid's team.

## Consequences

- A role change no longer needs the directory's administrators, and holds from the person's
  next request.
- **The directory's `roles` claim stops mattering after first sign-in.** Removing someone's
  role in Entra ID does not remove it here. Removing the person from the directory still
  ends their access, since they can no longer sign in; a leaver should also be switched off
  here, so that they are not offered for a bid's team.
- The system administrator role is now the most powerful in the application: its holder can
  give any role, to anyone but themselves. Two administrators acting together can still
  give each other anything. The audit log is the control, and the commercial director can
  read it.
- A person switched off keeps their roles, their place on bids and their name on what they
  did.
- Follow-up: no screen removes a person, and none shows a person's history of role changes
  but the audit log.
