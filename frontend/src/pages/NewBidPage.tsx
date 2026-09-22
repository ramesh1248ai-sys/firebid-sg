import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { asInstant } from "@/lib/format";

type BidCreate = components["schemas"]["BidCreate"];

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

interface Form {
  project_name: string;
  client_name: string;
  tender_reference: string;
  submission_deadline: string;
  clarification_cutoff: string;
  tender_validity_days: string;
}

const EMPTY: Form = {
  project_name: "",
  client_name: "",
  tender_reference: "",
  submission_deadline: "",
  clarification_cutoff: "",
  tender_validity_days: "90",
};

function toPayload(form: Form): BidCreate {
  return {
    project_name: form.project_name.trim(),
    client_name: form.client_name.trim(),
    tender_reference: form.tender_reference.trim(),
    submission_deadline: asInstant(form.submission_deadline),
    clarification_cutoff: form.clarification_cutoff ? asInstant(form.clarification_cutoff) : null,
    tender_validity_days: form.tender_validity_days
      ? Number(form.tender_validity_days)
      : null,
  };
}

export function NewBidPage() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [problem, setProblem] = useState<string | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: async (payload: BidCreate) => {
      const { data, error } = await api.POST("/bids", { body: payload });
      if (error || !data) throw new Error(apiErrorMessage(error, "The bid could not be created"));
      return data;
    },
    onSuccess: async (bid) => {
      await queryClient.invalidateQueries({ queryKey: ["bids"] });
      void navigate(`/bids/${bid.id}`);
    },
    onError: (error: Error) => setProblem(error.message),
  });

  const update =
    (field: keyof Form) => (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((current) => ({ ...current, [field]: event.target.value }));

  return (
    <section className="max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Register a bid</h1>
        <p className="text-sm text-muted-foreground">
          The clarification cut-off and tender validity can follow later; qualification waits until
          every detail is in.
        </p>
      </div>

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          setProblem(null);
          create.mutate(toPayload(form));
        }}
      >
        <div className="space-y-1">
          <label htmlFor="project_name" className="text-sm font-medium">
            Project
          </label>
          <input
            id="project_name"
            required
            className={fieldClass}
            value={form.project_name}
            onChange={update("project_name")}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="client_name" className="text-sm font-medium">
            Client
          </label>
          <input
            id="client_name"
            required
            className={fieldClass}
            value={form.client_name}
            onChange={update("client_name")}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="tender_reference" className="text-sm font-medium">
            Tender reference
          </label>
          <input
            id="tender_reference"
            required
            className={fieldClass}
            value={form.tender_reference}
            onChange={update("tender_reference")}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="submission_deadline" className="text-sm font-medium">
            Submission deadline
          </label>
          <input
            id="submission_deadline"
            type="datetime-local"
            required
            className={fieldClass}
            value={form.submission_deadline}
            onChange={update("submission_deadline")}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="clarification_cutoff" className="text-sm font-medium">
            Clarifications close <span className="text-muted-foreground">(optional)</span>
          </label>
          <input
            id="clarification_cutoff"
            type="datetime-local"
            className={fieldClass}
            value={form.clarification_cutoff}
            onChange={update("clarification_cutoff")}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="tender_validity_days" className="text-sm font-medium">
            Tender validity (days) <span className="text-muted-foreground">(optional)</span>
          </label>
          <input
            id="tender_validity_days"
            type="number"
            min={1}
            max={365}
            className={fieldClass}
            value={form.tender_validity_days}
            onChange={update("tender_validity_days")}
          />
        </div>

        {problem && (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        )}

        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Registering…" : "Register bid"}
        </Button>
      </form>
    </section>
  );
}
