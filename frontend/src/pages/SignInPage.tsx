import { ShieldCheck } from "lucide-react";
import { Navigate } from "react-router";

import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";

export function SignInPage() {
  const { status, signIn } = useAuth();

  if (status === "signed-in") return <Navigate to="/" replace />;

  return (
    <div className="grid place-items-center py-16">
      <section className="fb-card w-full max-w-md space-y-5 p-8">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
          <p className="text-sm text-muted-foreground">
            FireBid SG uses your organisation's single sign-on. You will be sent to your identity
            provider and back.
          </p>
        </div>
        <Button
          size="lg"
          className="w-full"
          onClick={() => void signIn()}
          disabled={status === "loading"}
        >
          {status === "loading" ? "Checking…" : "Sign in"}
        </Button>
        <p className="flex items-start gap-2 border-t pt-4 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
          The platform never holds your password, and you see only the bids you are on.
        </p>
      </section>
    </div>
  );
}
