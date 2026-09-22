/** How a deadline reads in the interface: the date, then how long is left. */
export function formatDeadline(iso: string | null, days: number | null): string {
  if (!iso) return "not set";
  const when = new Date(iso).toLocaleDateString("en-SG", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  if (days === null) return when;
  if (days < 0) return `${when} · overdue`;
  if (days === 0) return `${when} · today`;
  return `${when} · ${days} day${days === 1 ? "" : "s"} left`;
}

/** Under a week to go, or already past: the row needs to stand out. */
export function urgency(days: number | null): string {
  if (days === null) return "";
  if (days < 0) return "text-destructive font-medium";
  if (days <= 7) return "text-amber-700 dark:text-amber-400 font-medium";
  return "";
}

/** A `datetime-local` value is wall-clock; send it as an instant the API can store. */
export function asInstant(local: string): string {
  return new Date(local).toISOString();
}
