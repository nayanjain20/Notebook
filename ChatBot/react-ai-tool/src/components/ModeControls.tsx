import React from "react";
import { ShieldCheck, Globe, Lock, Cpu, Cloud, ChevronDown, Check } from "lucide-react";

/**
 * Confidential-mode + model-selection controls, shown while drafting a new chat.
 *
 * A session's mode and model are chosen here, once, and then locked:
 *  - Confidential (default) → runs on a local model; the user picks which local
 *    model from those installed on this system.
 *  - Standard → runs on the cloud model (Azure).
 *
 * `ModeBadge` is the read-only indicator shown once the session exists.
 */

export interface IModel {
  id: string;
  label: string;
  provider: string;
}

export interface IModelCatalog {
  local: IModel[];
  cloud: IModel[];
  recommended_local: string | null;
  recommended_cloud: string | null;
}

// ─── Setup panel (draft state) ────────────────────────────────────────────────

export const ModeSetup: React.FC<{
  confidential: boolean;
  onConfidentialChange: (v: boolean) => void;
  models: IModel[];
  selectedModel: string | null;
  onModelChange: (id: string) => void;
}> = ({ confidential, onConfidentialChange, models, selectedModel, onModelChange }) => (
  <div className="flex flex-col items-center gap-3">
    <button
      type="button"
      onClick={() => onConfidentialChange(!confidential)}
      className={`inline-flex items-center gap-2.5 rounded-xl border px-3.5 py-2 text-sm transition-colors
        ${confidential
          ? "border-primary/50 bg-primary/10 text-foreground"
          : "border-border bg-card text-muted-foreground hover:border-ring"}`}
      title="Confidential mode runs entirely on local models — this can't be changed later"
    >
      <span
        className={`flex h-4 w-4 items-center justify-center rounded border transition-colors
          ${confidential ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/50"}`}
      >
        {confidential && <ShieldCheck className="h-3 w-3" />}
      </span>
      <span className="flex flex-col items-start leading-tight">
        <span className="font-medium text-foreground">Confidential mode</span>
        <span className="text-[11px] text-muted-foreground">Runs on local models · nothing leaves your machine</span>
      </span>
    </button>

    <ModelDropdown
      models={models}
      value={selectedModel}
      onChange={onModelChange}
      confidential={confidential}
    />
  </div>
);

// ─── Model dropdown ───────────────────────────────────────────────────────────

const ModelDropdown: React.FC<{
  models: IModel[];
  value: string | null;
  onChange: (id: string) => void;
  confidential: boolean;
}> = ({ models, value, onChange, confidential }) => {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const Icon = confidential ? Cpu : Cloud;

  // No models for the chosen mode → clear guidance, no dropdown.
  if (models.length === 0) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-1.5 text-xs text-destructive">
        {confidential
          ? "No local models found. Install one with Ollama, or uncheck confidential."
          : "No cloud model configured."}
      </span>
    );
  }

  // Standard mode with a single cloud model → static label, nothing to pick.
  if (!confidential && models.length === 1) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground">
        <Cloud className="h-3.5 w-3.5 text-muted-foreground" />
        {models[0].label}
      </span>
    );
  }

  const active = models.find((m) => m.id === value);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground shadow-sm transition-colors hover:border-ring"
        title="Choose the model that will answer (fixed for this session)"
      >
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="max-w-[12rem] truncate">{active?.label ?? "Choose a model"}</span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute bottom-9 left-1/2 z-30 w-64 -translate-x-1/2 rounded-xl border border-border bg-popover p-1 shadow-lg">
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {confidential ? "Local models" : "Cloud model"}
          </div>
          {models.map((m) => (
            <button
              key={m.id}
              onClick={() => { onChange(m.id); setOpen(false); }}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-popover-foreground transition-colors hover:bg-accent"
            >
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate text-left">{m.label}</span>
              {m.id === value && <Check className="h-4 w-4 shrink-0 text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

// ─── Read-only badge (active session) ─────────────────────────────────────────

export const ModeBadge: React.FC<{ confidential: boolean; modelLabel?: string | null }> = ({
  confidential,
  modelLabel,
}) =>
  confidential ? (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 text-xs font-medium text-foreground"
      title="This session runs entirely on local models. Mode and model are fixed."
    >
      <ShieldCheck className="h-3.5 w-3.5 text-primary" />
      Confidential
      {modelLabel && <span className="text-muted-foreground">· {modelLabel}</span>}
      <Lock className="h-3 w-3 text-muted-foreground" />
    </span>
  ) : (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground"
      title="This session runs on public cloud models. Mode and model are fixed."
    >
      <Globe className="h-3.5 w-3.5" />
      Public{modelLabel ? ` · ${modelLabel}` : ""}
    </span>
  );
