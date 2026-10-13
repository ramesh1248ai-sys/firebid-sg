import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";

/**
 * The organisation's people (FR-ADM-01, ADR-013).
 *
 * Who someone is (their name, their sign-in name) comes from the identity provider and is
 * shown, not edited. What they may do here is the roles they hold, which a system
 * administrator sets on this page, and whether their access is on. A role on a bid is set
 * on that bid's team.
 */

type Person = components["schemas"]["UserOut"];
type Role = components["schemas"]["Role"];

const words = (role: string) => role.replaceAll("_", " ");

export function UsersPage() {
  const { session } = useAuth();
  const isAdmin = session?.roles.includes("system_admin") ?? false;
  const people = useQuery({
    queryKey: ["people"],
    enabled: isAdmin,
    queryFn: async (): Promise<Person[]> => {
      const { data, error } = await api.GET("/admin/users");
      if (!data) throw new Error(apiErrorMessage(error, "Could not read the people"));
      return data;
    },
  });
  const roles = useQuery({
    queryKey: ["people", "roles"],
    enabled: isAdmin,
    queryFn: async (): Promise<Role[]> => {
      const { data, error } = await api.GET("/admin/users/roles");
      if (!data) throw new Error(apiErrorMessage(error, "Could not read the roles"));
      return data as Role[];
    },
  });
  const [editing, setEditing] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">People</h1>
        <p className="text-sm text-muted-foreground">
          Everyone who has signed in. A name and a sign-in name come from the sign-in service
          and are changed there. The roles someone holds, and whether their access is on, are
          set here and hold from their next click. Their role on a bid is set on that
          bid&apos;s team.
        </p>
      </div>
      {!isAdmin && (
        <p role="alert" className="text-sm text-destructive">
          Only a system administrator sees this page.
        </p>
      )}
      {people.isPending && isAdmin && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the people…
        </p>
      )}
      {(people.isError || roles.isError) && (
        <p role="alert" className="text-sm text-destructive">
          Could not read the people.
        </p>
      )}
      {people.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="People">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Name</th>
                <th className="py-2 pr-3">Sign-in name</th>
                <th className="py-2 pr-3">Roles</th>
                <th className="py-2 pr-3 text-right">Bids</th>
                <th className="py-2 pr-3">Access</th>
                <th className="py-2 pr-3">First signed in</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {people.data.map((person) => (
                <PersonRows
                  key={person.user_id}
                  person={person}
                  roles={roles.data ?? []}
                  own={person.user_id === session?.userId}
                  editing={editing === person.user_id}
                  onEdit={(open) => setEditing(open ? person.user_id : null)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Someone who has never signed in is not listed: ask them to sign in once. They start
        with the roles the sign-in service gives them.
      </p>
    </div>
  );
}

function PersonRows({
  person,
  roles,
  own,
  editing,
  onEdit,
}: {
  person: Person;
  roles: Role[];
  own: boolean;
  editing: boolean;
  onEdit: (open: boolean) => void;
}) {
  return (
    <>
      <tr className="border-t align-top">
        <td className="py-2 pr-3 font-medium">{person.display_name}</td>
        <td className="py-2 pr-3">{person.username}</td>
        <td className="py-2 pr-3">
          {person.roles.length > 0 ? (
            person.roles.map(words).join(", ")
          ) : (
            <span className="text-muted-foreground">No role</span>
          )}
        </td>
        <td className="py-2 pr-3 text-right">{person.bids}</td>
        <td className="py-2 pr-3">
          {person.is_active ? "On" : <span className="text-destructive">Switched off</span>}
        </td>
        <td className="py-2 pr-3">{person.first_signed_in_at.slice(0, 10)}</td>
        <td className="py-2 text-right">
          {own ? (
            <span className="text-xs text-muted-foreground">
              You: another administrator changes your access
            </span>
          ) : (
            !editing && (
              <Button
                size="sm"
                variant="outline"
                aria-label={`Edit ${person.display_name}`}
                onClick={() => onEdit(true)}
              >
                Edit
              </Button>
            )
          )}
        </td>
      </tr>
      {editing && (
        <tr className="bg-muted/40">
          <td colSpan={7} className="px-3 py-3">
            <EditPerson person={person} roles={roles} onClose={() => onEdit(false)} />
          </td>
        </tr>
      )}
    </>
  );
}

function EditPerson({
  person,
  roles,
  onClose,
}: {
  person: Person;
  roles: Role[];
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [held, setHeld] = useState<string[]>(person.roles);
  const [active, setActive] = useState(person.is_active);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const { data, error: refused } = await api.PATCH("/admin/users/{user_id}", {
        params: { path: { user_id: person.user_id } },
        body: { roles: held as Role[], is_active: active },
      });
      if (!data) throw new Error(apiErrorMessage(refused, "Could not save the change"));
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "Could not save the change"),
  });
  const same =
    active === person.is_active &&
    held.length === person.roles.length &&
    held.every((role) => person.roles.includes(role));

  return (
    <form
      aria-label={`Access of ${person.display_name}`}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <fieldset className="space-y-1">
        <legend className="text-xs text-muted-foreground">
          Roles {person.display_name} holds
        </legend>
        <div className="flex flex-wrap gap-x-5 gap-y-1">
          {roles.map((role) => (
            <label key={role} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={held.includes(role)}
                onChange={(event) =>
                  setHeld(
                    event.target.checked ? [...held, role] : held.filter((one) => one !== role),
                  )
                }
              />
              {words(role)}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="flex items-center gap-1.5 text-sm">
        <input
          type="checkbox"
          checked={active}
          onChange={(event) => setActive(event.target.checked)}
        />
        Access on
        <span className="text-xs text-muted-foreground">
          (off: they cannot use FireBid; their roles and their work are kept)
        </span>
      </label>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={same || save.isPending}>
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </form>
  );
}
