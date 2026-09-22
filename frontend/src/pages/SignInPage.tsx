import { Navigate } from "react-router";

import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";

export function SignInPage() {
  const { status, signIn } = useAuth();

  if (status === "signed-in") return <Navigate to="/" replace />;

  return (
    <section className="max-w-sm space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="text-sm text-muted-foreground">
        FireBid SG uses your organisation's single sign-on. You will be sent to your identity
        provider and back.
      </p>
      <Button onClick={() => void signIn()} disabled={status === "loading"}>
        {status === "loading" ? "Checking…" : "Sign in"}
      </Button>
    </section>
  );
}
