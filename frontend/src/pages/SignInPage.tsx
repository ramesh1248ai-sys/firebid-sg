import { Button } from "@/components/ui/button";

export function SignInPage() {
  return (
    <section className="max-w-sm space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="text-sm text-muted-foreground">
        Single sign-on with Microsoft Entra ID arrives in step P0-03.
      </p>
      <Button disabled>Sign in with Microsoft</Button>
    </section>
  );
}
