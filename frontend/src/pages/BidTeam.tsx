import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";

/**
 * The bid's team (FR-BID-01).
 *
 * A bid is seen only by the people on it, so who is on it is shown to all of them. A bid
 * manager adds someone of the organisation, in the role they hold on this bid, changes that
 * role, or takes them off the bid. Work cannot start on a bid with no team, and a bid keeps
 * at least one bid manager.
 */

type Member = components["schemas"]["MemberOut"];
type Candidate = components["schemas"]["TeamCandidateOut"];
type Role = components["schemas"]["MemberIn"]["role"];

const MANAGERS = ["bid_manager", "system_admin"];
// The roles a person may hold on a bid, as the requirements name them.
const ROLES: Role[] = [
  "estimator",
  "senior_estimator",
  "bid_manager",
  "design_manager",
  "commercial_director",
  "procurement",
  "project_manager",
  "executive_sponsor",
];
const words = (role: string) => role.replaceAll("_", " ");

export function BidTeam({ bidId }: { bidId: string }) {
  const { session } = useAuth();
  const canManage = session?.roles.some((role) => MANAGERS.includes(role)) ?? false;
  const path = { params: { path: { bid_id: bidId } } };
  const members = useQuery({
    queryKey: ["bid", bidId, "members"],
    queryFn: async (): Promise<Member[]> => {
      const { data } = await api.GET("/bids/{bid_id}/members", path);
      if (!Array.isArray(data)) throw new Error("Could not read the team");
      return data;
    },
  });

  return (
    <section aria-label="Team" className="fb-card space-y-3 p-5">
      <div>
        <h2 className="text-lg font-medium">Team</h2>
        <p className="text-sm text-muted-foreground">
          Only the people on a bid see it. A bid manager adds them.
        </p>
      </div>
      {members.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not read the team.
        </p>
      )}
      {members.data && members.data.length === 0 && (
        <p className="text-sm text-muted-foreground">Nobody is on this bid yet.</p>
      )}
      {members.data && members.data.length > 0 && (
        <ul aria-label="People on this bid" className="divide-y text-sm">
          {[...members.data]
            .sort((a, b) => a.display_name.localeCompare(b.display_name))
            .map((member) =>
              canManage ? (
                <ManagedMember key={member.user_id} bidId={bidId} member={member} />
              ) : (
                <li key={member.user_id} className="flex gap-3 py-2">
                  <span className="font-medium">{member.display_name}</span>
                  <span className="text-muted-foreground">{words(member.role)}</span>
                </li>
              ),
            )}
        </ul>
      )}
      {canManage && <AddMember bidId={bidId} />}
    </section>
  );
}

/** A member as a bid manager sees them: their role can be changed, and they can be taken off. */
function ManagedMember({ bidId, member }: { bidId: string; member: Member }) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId, user_id: member.user_id } } };
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = () => {
    setError(null);
    setRemoving(false);
    void client.invalidateQueries({ queryKey: ["bid", bidId] });
  };
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Could not change the team");
  const change = useMutation({
    mutationFn: async (role: Role) => {
      const { data, error: refused } = await api.PATCH("/bids/{bid_id}/members/{user_id}", {
        ...path,
        body: { role },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not change the role"));
      return data;
    },
    onSuccess: done,
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: async () => {
      const { error: refused } = await api.DELETE("/bids/{bid_id}/members/{user_id}", path);
      if (refused) throw new Error(apiErrorMessage(refused, "Could not take them off the bid"));
    },
    onSuccess: done,
    onError: fail,
  });
  const busy = change.isPending || remove.isPending;

  return (
    <li className="space-y-1 py-2">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{member.display_name}</span>
        <select
          aria-label={`Role of ${member.display_name}`}
          className="h-8 text-sm"
          value={member.role}
          disabled={busy}
          onChange={(event) => change.mutate(event.target.value as Role)}
        >
          {ROLES.map((one) => (
            <option key={one} value={one}>
              {words(one)}
            </option>
          ))}
        </select>
        {removing ? (
          <>
            <span className="text-muted-foreground">Take them off this bid?</span>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => remove.mutate()}>
              Yes, take {member.display_name} off
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRemoving(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-link"
            aria-label={`Take ${member.display_name} off the bid`}
            onClick={() => setRemoving(true)}
          >
            Take off the bid
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  );
}

function AddMember({ bidId }: { bidId: string }) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const [person, setPerson] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [error, setError] = useState<string | null>(null);
  const candidates = useQuery({
    queryKey: ["bid", bidId, "candidates"],
    queryFn: async (): Promise<Candidate[]> => {
      const { data } = await api.GET("/bids/{bid_id}/members/candidates", path);
      if (!Array.isArray(data)) throw new Error("Could not read who may be added");
      return data;
    },
  });
  const add = useMutation({
    mutationFn: async () => {
      if (!person || !role) throw new Error("Choose a person and a role");
      const { data, error: refused } = await api.POST("/bids/{bid_id}/members", {
        ...path,
        body: { user_id: person, role },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not add them"));
      return data;
    },
    onSuccess: () => {
      setPerson("");
      setRole("");
      setError(null);
      // The bid itself says whether its team is still missing.
      void client.invalidateQueries({ queryKey: ["bid", bidId] });
    },
    onError: (caught) => setError(caught instanceof Error ? caught.message : "Could not add them"),
  });

  if (candidates.data && candidates.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Everyone in the organisation who has signed in is on this bid.
      </p>
    );
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2 border-t pt-3"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <label className="text-sm">
        <span className="block text-xs text-muted-foreground">Person</span>
        <select
          aria-label="Person to add"
          value={person}
          onChange={(event) => {
            const chosen = event.target.value;
            setPerson(chosen);
            // Offer the role they hold in the organisation; it can be changed for this bid.
            const held = candidates.data?.find((one) => one.user_id === chosen)?.roles ?? [];
            const first = ROLES.find((one) => held.includes(one));
            if (first) setRole(first);
          }}
        >
          <option value="">Choose…</option>
          {(candidates.data ?? []).map((one) => (
            <option key={one.user_id} value={one.user_id}>
              {one.display_name} ({one.username})
            </option>
          ))}
        </select>
      </label>
      <label className="text-sm">
        <span className="block text-xs text-muted-foreground">Role on this bid</span>
        <select
          aria-label="Role on this bid"
          value={role}
          onChange={(event) => setRole(event.target.value as Role | "")}
        >
          <option value="">Choose…</option>
          {ROLES.map((one) => (
            <option key={one} value={one}>
              {words(one)}
            </option>
          ))}
        </select>
      </label>
      <Button type="submit" size="sm" disabled={!person || !role || add.isPending}>
        Add to the team
      </Button>
      <p className="basis-full text-xs text-muted-foreground">
        Someone who has never signed in is not listed: ask them to sign in once.
      </p>
      {error && (
        <p role="alert" className="basis-full text-sm text-destructive">
          {error}
        </p>
      )}
    </form>
  );
}
