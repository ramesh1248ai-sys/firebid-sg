import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Routing = components["schemas"]["RoutingOut"];
type SpendRow = components["schemas"]["SpendRow"];

type Grouping = "route" | "provider" | "model" | "bid";
const GROUPINGS: Grouping[] = ["route", "provider", "model", "bid"];

function useRouting() {
  return useQuery({
    queryKey: ["admin", "routing"],
    queryFn: async () => {
      const { data, error } = await api.GET("/admin/llm/routing");
      if (error || !data) throw new Error("Could not load the routing table");
      return data;
    },
  });
}

function useSpend(by: Grouping) {
  return useQuery({
    queryKey: ["admin", "cost", by],
    queryFn: async () => {
      const { data, error } = await api.GET("/admin/llm/cost", { params: { query: { by } } });
      if (error || !data) throw new Error("Could not load spend");
      return data;
    },
  });
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}

function Routes({ routing }: { routing: Routing }) {
  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">Routes and the model that would serve each</caption>
      <thead className="border-b text-xs uppercase tracking-wide text-muted-foreground">
        <tr>
          <th scope="col" className="py-2 pr-4 font-medium">Route</th>
          <th scope="col" className="py-2 pr-4 font-medium">Data class</th>
          <th scope="col" className="py-2 pr-4 font-medium">Serves it now</th>
          <th scope="col" className="py-2 font-medium">Chain</th>
        </tr>
      </thead>
      <tbody>
        {routing.routes.map((route) => (
          <tr key={route.name} className="border-b last:border-0 align-top">
            <td className="py-2 pr-4 font-medium">{route.name}</td>
            <td className="py-2 pr-4">{route.data_class}</td>
            <td className="py-2 pr-4">
              {route.effective_model ?? (
                <span className="text-destructive">no enabled model</span>
              )}
            </td>
            <td className="py-2 text-muted-foreground">{route.models.join(" → ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Providers({ routing }: { routing: Routing }) {
  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">Providers and what each may be sent</caption>
      <thead className="border-b text-xs uppercase tracking-wide text-muted-foreground">
        <tr>
          <th scope="col" className="py-2 pr-4 font-medium">Provider</th>
          <th scope="col" className="py-2 pr-4 font-medium">Enabled</th>
          <th scope="col" className="py-2 pr-4 font-medium">Credentials</th>
          <th scope="col" className="py-2 font-medium">Approved for</th>
        </tr>
      </thead>
      <tbody>
        {routing.providers.map((provider) => (
          <tr key={provider.name} className="border-b last:border-0 align-top">
            <td className="py-2 pr-4 font-medium">
              {provider.name}
              <div className="text-xs text-muted-foreground">{provider.platform}</div>
            </td>
            <td className="py-2 pr-4">{provider.enabled ? "yes" : "no"}</td>
            <td
              className={cn(
                "py-2 pr-4",
                provider.enabled && !provider.credentials_configured && "text-destructive",
              )}
            >
              {provider.credentials_configured ? "configured" : "not set"}
            </td>
            <td className="py-2">
              {provider.approved_data_classes.length > 0 ? (
                provider.approved_data_classes.join(", ")
              ) : (
                <span className="text-muted-foreground">nothing</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Spend({ rows }: { rows: SpendRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing has been spent yet.</p>;
  }
  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">AI spend</caption>
      <thead className="border-b text-xs uppercase tracking-wide text-muted-foreground">
        <tr>
          <th scope="col" className="py-2 pr-4 font-medium">Group</th>
          <th scope="col" className="py-2 pr-4 font-medium">Calls</th>
          <th scope="col" className="py-2 pr-4 font-medium">From cache</th>
          <th scope="col" className="py-2 pr-4 font-medium">Tokens in / out</th>
          <th scope="col" className="py-2 font-medium">Cost (SGD)</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.group} className="border-b last:border-0">
            <td className="py-2 pr-4 font-medium">{row.group}</td>
            <td className="py-2 pr-4">{row.calls}</td>
            <td className="py-2 pr-4">{row.cache_hits}</td>
            <td className="py-2 pr-4">
              {row.tokens_in.toLocaleString()} / {row.tokens_out.toLocaleString()}
            </td>
            <td className="py-2">{Number(row.cost_sgd).toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AdminPage() {
  const [by, setBy] = useState<Grouping>("route");
  const routing = useRouting();
  const spend = useSpend(by);

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Platform</h1>
        <p className="text-sm text-muted-foreground">
          Routing comes from <code>backend/config/llm.yaml</code> and is changed by editing that
          file. This page reports it; it does not set it.
        </p>
      </div>

      {routing.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the routing table…
        </p>
      )}
      {routing.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the routing table
        </p>
      )}
      {routing.isSuccess && (
        <>
          <Section title="Routes">
            <Routes routing={routing.data} />
          </Section>
          <Section title="Providers">
            <Providers routing={routing.data} />
          </Section>
          <p className="text-xs text-muted-foreground">
            Configuration version {routing.data.config_version}
          </p>
        </>
      )}

      <Section title="Spend">
        <div className="flex gap-2">
          {GROUPINGS.map((grouping) => (
            <Button
              key={grouping}
              variant={grouping === by ? "default" : "outline"}
              size="sm"
              onClick={() => setBy(grouping)}
            >
              By {grouping}
            </Button>
          ))}
        </div>
        {spend.isPending && (
          <p role="status" className="text-sm text-muted-foreground">
            Loading spend…
          </p>
        )}
        {spend.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not load spend
          </p>
        )}
        {spend.isSuccess && <Spend rows={spend.data} />}
      </Section>
    </div>
  );
}
