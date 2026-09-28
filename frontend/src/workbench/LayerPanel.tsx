import type { Layers, Mark } from "./marks";
import { STATUS_COLOURS } from "./marks";

/** Show or hide marks by object type, status and confidence band (FR-REV-01). */

const STATUS_LABELS: Record<string, string> = {
  proposed: "Proposed",
  verified: "Verified",
  edited: "Edited",
  rejected: "Rejected",
  manual: "Manual",
  duplicate: "Counted on another sheet",
  not_taken_off: "Not taken off",
};

const BAND_LABELS: Record<string, string> = {
  low: "Low confidence",
  medium: "Medium confidence",
  high: "High confidence",
};

export function LayerPanel({
  marks,
  layers,
  onChange,
}: {
  marks: Mark[];
  layers: Layers;
  onChange: (layers: Layers) => void;
}) {
  const types = [...new Set(marks.map((m) => m.object_type))].sort();
  const statuses = Object.keys(STATUS_LABELS).filter((s) => marks.some((m) => m.status === s));

  function toggle(group: keyof Layers, value: string) {
    const next = new Set(layers[group]);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange({ ...layers, [group]: next });
  }

  return (
    <fieldset className="space-y-3 text-sm" aria-label="Layers">
      <legend className="font-medium">Layers</legend>
      <Group title="Status">
        {statuses.map((status) => (
          <Toggle
            key={status}
            label={STATUS_LABELS[status] ?? status}
            colour={STATUS_COLOURS[status]}
            checked={!layers.statuses.has(status)}
            onChange={() => toggle("statuses", status)}
          />
        ))}
      </Group>
      <Group title="Confidence">
        {Object.entries(BAND_LABELS).map(([band, label]) => (
          <Toggle
            key={band}
            label={label}
            checked={!layers.bands.has(band)}
            onChange={() => toggle("bands", band)}
          />
        ))}
      </Group>
      <Group title="Object type">
        {types.map((type) => (
          <Toggle
            key={type}
            label={type.replaceAll("_", " ")}
            checked={!layers.types.has(type)}
            onChange={() => toggle("types", type)}
          />
        ))}
      </Group>
    </fieldset>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{title}</p>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Toggle({
  label,
  colour,
  checked,
  onChange,
}: {
  label: string;
  colour?: string;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <input type="checkbox" checked={checked} onChange={onChange} />
      {colour && (
        <span
          aria-hidden
          className="inline-block h-3 w-3 rounded-sm"
          style={{ backgroundColor: colour }}
        />
      )}
      <span>{label}</span>
    </label>
  );
}
