import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";

/**
 * The organisation's libraries (FR-ADM-02): the canonical object types, and how each
 * consultant draws them. Everything here is versioned: an edit is a new version, and every
 * version, with who made it and why, can be opened from its row. Deprecating a type keeps
 * its history and its existing mappings; it is just no longer offered.
 */

type ObjectType = components["schemas"]["ObjectTypeOut"];
type Consultant = components["schemas"]["ConsultantOut"];
type Mapping = components["schemas"]["MappingOut"];

// Only the roles the API lets change the object library.
const EDITORS = ["senior_estimator", "system_admin"];

export function LibraryPage() {
  const [tab, setTab] = useState<"types" | "mappings">("types");
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Library</h1>
        <p className="text-sm text-muted-foreground">
          The objects the platform can take off, and how each consultant draws
          them.
        </p>
      </div>
      <div role="tablist" className="flex gap-2 border-b">
        {(
          [
            ["types", "Object types"],
            ["mappings", "Consultant symbols"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === key
                ? "border-primary font-medium"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "types" ? <ObjectTypes /> : <ConsultantMappings />}
    </section>
  );
}

function ObjectTypes() {
  const { session } = useAuth();
  const canEdit =
    session?.roles.some((role) => EDITORS.includes(role)) ?? false;
  const types = useQuery({
    queryKey: ["library", "types"],
    queryFn: async (): Promise<ObjectType[]> => {
      const { data, error } = await api.GET("/library/object-types");
      if (error || !data) throw new Error("Could not read the object library");
      return data;
    },
  });
  const [open, setOpen] = useState<string | null>(null);

  if (types.isLoading) return <p className="text-sm">Loading…</p>;
  if (types.error)
    return <p className="text-sm text-red-700">{String(types.error)}</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-2 pr-3">Type</th>
          <th className="py-2 pr-3">Category</th>
          <th className="py-2 pr-3">Taken off by</th>
          <th className="py-2 pr-3">Attributes</th>
          <th className="py-2 pr-3">Version</th>
          <th className="py-2" />
        </tr>
      </thead>
      <tbody>
        {(types.data ?? []).map((item) => (
          <TypeRow
            key={item.key}
            item={item}
            canEdit={canEdit}
            open={open === item.key}
            onToggle={() => setOpen(open === item.key ? null : item.key)}
          />
        ))}
      </tbody>
    </table>
  );
}

function TypeRow({
  item,
  canEdit,
  open,
  onToggle,
}: {
  item: ObjectType;
  canEdit: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState(item.label);
  const [editing, setEditing] = useState(false);
  const change = useMutation({
    mutationFn: async (body: {
      label?: string;
      deprecate?: boolean;
      restore?: boolean;
    }) => {
      const { error } = await api.POST("/library/object-types/{key}", {
        params: { path: { key: item.key } },
        body: { deprecate: false, restore: false, ...body },
      });
      if (error)
        throw new Error(apiErrorMessage(error, "The change was not saved"));
    },
    onSuccess: async () => {
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });

  return (
    <>
      <tr
        className={`border-t align-top ${item.deprecated ? "text-muted-foreground" : ""}`}
      >
        <td className="py-2 pr-3">
          {editing ? (
            <input
              aria-label={`Label for ${item.key}`}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              className="w-full rounded-md border bg-background px-2 py-1"
            />
          ) : (
            <>
              <div className={item.deprecated ? "line-through" : ""}>
                {item.label}
              </div>
              <div className="text-xs text-muted-foreground">{item.key}</div>
            </>
          )}
        </td>
        <td className="py-2 pr-3">{item.category}</td>
        <td className="py-2 pr-3">
          {item.measure === "none" ? "not taken off" : item.measure}
        </td>
        <td className="py-2 pr-3 text-xs">
          {Object.keys(item.attribute_schema).join(", ") || "none"}
        </td>
        <td className="py-2 pr-3">
          <button className="underline" onClick={onToggle} aria-expanded={open}>
            v{item.version}
          </button>
          {item.deprecated && <span className="ml-2 text-xs">deprecated</span>}
        </td>
        <td className="space-x-2 py-2 text-right">
          {canEdit && editing && (
            <Button
              size="sm"
              disabled={change.isPending}
              onClick={() => change.mutate({ label })}
            >
              Save
            </Button>
          )}
          {canEdit && !editing && !item.deprecated && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setEditing(true)}
              >
                Rename
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={change.isPending}
                onClick={() => change.mutate({ deprecate: true })}
              >
                Deprecate
              </Button>
            </>
          )}
          {canEdit && item.deprecated && (
            <Button
              size="sm"
              variant="outline"
              disabled={change.isPending}
              onClick={() => change.mutate({ restore: true })}
            >
              Restore
            </Button>
          )}
          {change.error && (
            <p className="text-xs text-red-700">{change.error.message}</p>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="pb-3">
            <TypeHistory typeKey={item.key} />
          </td>
        </tr>
      )}
    </>
  );
}

function TypeHistory({ typeKey }: { typeKey: string }) {
  const history = useQuery({
    queryKey: ["library", "types", typeKey, "history"],
    queryFn: async (): Promise<ObjectType[]> => {
      const { data, error } = await api.GET(
        "/library/object-types/{key}/history",
        {
          params: { path: { key: typeKey } },
        },
      );
      if (error || !data) throw new Error("Could not read the history");
      return data;
    },
  });
  return (
    <ol
      className="space-y-1 rounded-md bg-muted/40 p-3 text-xs"
      aria-label="Version history"
    >
      {(history.data ?? []).map((version) => (
        <li key={version.version}>
          <span className="font-medium">v{version.version}</span> ·{" "}
          {version.label}
          {version.deprecated ? " (deprecated)" : ""} ·{" "}
          {version.changed_by ?? "platform"} ·{" "}
          {new Date(version.created_at).toLocaleString()}
          {version.change_note ? ` · ${version.change_note}` : ""}
        </li>
      ))}
    </ol>
  );
}

function ConsultantMappings() {
  const consultants = useQuery({
    queryKey: ["library", "consultants"],
    queryFn: async (): Promise<Consultant[]> => {
      const { data, error } = await api.GET("/library/consultants");
      if (error || !data) throw new Error("Could not read the consultants");
      return data;
    },
  });
  const [chosen, setChosen] = useState<string>("");
  const key = chosen || consultants.data?.[0]?.consultant_key || "";

  if (consultants.isLoading) return <p className="text-sm">Loading…</p>;
  if (!consultants.data?.length)
    return (
      <p className="text-sm text-muted-foreground">
        No consultant symbols have been mapped yet. They are mapped from a bid's
        Symbols page.
      </p>
    );
  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-sm">
        Consultant
        <select
          value={key}
          onChange={(event) => setChosen(event.target.value)}
          className="rounded-md border bg-background px-2 py-1"
        >
          {consultants.data.map((item) => (
            <option key={item.consultant_key} value={item.consultant_key}>
              {item.consultant} ({item.confirmed} confirmed, {item.proposed} to
              confirm)
            </option>
          ))}
        </select>
      </label>
      {key && <MappingTable consultantKey={key} />}
    </div>
  );
}

function MappingTable({ consultantKey }: { consultantKey: string }) {
  const mappings = useQuery({
    queryKey: ["library", "mappings", consultantKey],
    queryFn: async (): Promise<Mapping[]> => {
      const { data, error } = await api.GET("/library/mappings", {
        params: { query: { consultant_key: consultantKey } },
      });
      if (error || !data) throw new Error("Could not read the mappings");
      return data;
    },
  });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-2 pr-3">Legend says</th>
          <th className="py-2 pr-3">Maps to</th>
          <th className="py-2 pr-3">State</th>
          <th className="py-2 pr-3">Version</th>
        </tr>
      </thead>
      <tbody>
        {(mappings.data ?? []).map((mapping) => (
          <MappingRow
            key={mapping.lineage_id}
            mapping={mapping}
            open={open === mapping.lineage_id}
            onToggle={() =>
              setOpen(open === mapping.lineage_id ? null : mapping.lineage_id)
            }
          />
        ))}
      </tbody>
    </table>
  );
}

function MappingRow({
  mapping,
  open,
  onToggle,
}: {
  mapping: Mapping;
  open: boolean;
  onToggle: () => void;
}) {
  const history = useQuery({
    enabled: open,
    queryKey: ["library", "mapping", mapping.lineage_id],
    queryFn: async (): Promise<Mapping[]> => {
      const { data, error } = await api.GET(
        "/library/mappings/{lineage_id}/history",
        {
          params: { path: { lineage_id: mapping.lineage_id } },
        },
      );
      if (error || !data) throw new Error("Could not read the history");
      return data;
    },
  });
  return (
    <>
      <tr className="border-t">
        <td className="py-2 pr-3">
          {mapping.description}
          {mapping.project_only && (
            <span className="ml-2 text-xs text-muted-foreground">
              (one project only)
            </span>
          )}
        </td>
        <td className="py-2 pr-3">{mapping.object_type ?? "not decided"}</td>
        <td className="py-2 pr-3">{mapping.state}</td>
        <td className="py-2 pr-3">
          <button className="underline" onClick={onToggle} aria-expanded={open}>
            v{mapping.version}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} className="pb-3">
            <ol
              className="space-y-1 rounded-md bg-muted/40 p-3 text-xs"
              aria-label="Mapping history"
            >
              {(history.data ?? []).map((version) => (
                <li key={version.version}>
                  <span className="font-medium">v{version.version}</span> ·{" "}
                  {version.state} · {version.object_type ?? "no type"} · by{" "}
                  {version.confirmed_by ?? version.source} ·{" "}
                  {new Date(version.created_at).toLocaleString()}
                  {version.change_note ? ` · ${version.change_note}` : ""}
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}
