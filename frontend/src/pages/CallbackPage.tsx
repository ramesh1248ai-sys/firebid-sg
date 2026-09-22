import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { userManager } from "@/auth/oidc";

/** Where the identity provider sends the browser back after sign-in. */
export function CallbackPage() {
  const navigate = useNavigate();
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    userManager
      .signinCallback()
      .then((user) => {
        const state = user?.state as { returnTo?: string } | undefined;
        void navigate(state?.returnTo ?? "/", { replace: true });
      })
      .catch((error: unknown) => {
        setProblem(error instanceof Error ? error.message : "Sign-in could not be completed");
      });
  }, [navigate]);

  if (problem) {
    return (
      <section className="max-w-prose space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">Sign-in failed</h1>
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      </section>
    );
  }
  return (
    <p role="status" className="text-sm text-muted-foreground">
      Completing sign-in…
    </p>
  );
}
