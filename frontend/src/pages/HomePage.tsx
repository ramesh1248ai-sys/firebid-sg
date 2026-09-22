import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

function useApiVersion() {
  return useQuery({
    queryKey: ["version"],
    queryFn: async () => {
      const { data, error } = await api.GET("/version");
      if (error || !data) throw new Error("API unreachable");
      return data;
    },
  });
}

export function HomePage() {
  const version = useApiVersion();

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">FireBid SG</h1>
      <p className="max-w-prose text-muted-foreground">
        AI-assisted tendering and pre-construction for Singapore fire protection contractors.
      </p>
      <p role="status" className="text-sm">
        {version.isPending && "Connecting to the API…"}
        {version.isError && <span className="text-destructive">API unreachable</span>}
        {version.isSuccess && (
          <span>
            API connected · version {version.data.version} ({version.data.env})
          </span>
        )}
      </p>
    </section>
  );
}
