import React from "react";
import { Plus, Upload, Link as LinkIcon, X, Loader, FileText } from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;
const MAX_SIZE_MB = 5;

interface AddSourceMenuProps {
  // Resolves the current session id, creating one lazily if needed.
  ensureSession: () => Promise<string | null>;
  onSourceAdded: (sessionId: string) => void;
  visuals: boolean;
  disabled?: boolean;
  variant?: "icon" | "cta";
}

type ModalKind = null | "doc" | "link";

// ─── Modal shell ──────────────────────────────────────────────────────────────

const ModalShell: React.FC<{
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}> = ({ title, onClose, children }) => {
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
          <h2 className="font-serif text-lg font-semibold text-foreground">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
};

// ─── Document upload modal ────────────────────────────────────────────────────

const DocumentModal: React.FC<{
  ensureSession: () => Promise<string | null>;
  onClose: () => void;
  onSourceAdded: (sessionId: string) => void;
  visuals: boolean;
}> = ({ ensureSession, onClose, onSourceAdded, visuals }) => {
  const [uploading, setUploading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [dragOver, setDragOver] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    setError(null);
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`File exceeds ${MAX_SIZE_MB} MB limit.`);
      return;
    }
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (ext !== "pdf" && ext !== "txt") {
      setError("Only PDF and TXT files are supported.");
      return;
    }

    setUploading(true);
    const sid = await ensureSession();
    if (!sid) {
      setError("Could not start a chat.");
      setUploading(false);
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", sid);
    formData.append("visuals", String(visuals));
    try {
      const res = await fetch(`${BASE_URL}/api/upload`, { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Upload failed.");
        return;
      }
      onSourceAdded(sid);
      onClose();
    } catch {
      setError("Could not reach the backend.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <ModalShell title="Upload document" onClose={onClose}>
      <div
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) handleFile(file);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => inputRef.current?.click()}
        className={`border border-dashed rounded-xl px-4 py-8 flex flex-col items-center gap-2 cursor-pointer transition-colors
          ${dragOver ? "border-ring bg-accent/40" : "border-border hover:border-ring hover:bg-muted/50"}`}
      >
        {uploading ? (
          <Loader size={22} className="animate-spin text-muted-foreground" />
        ) : (
          <Upload size={22} className="text-muted-foreground" />
        )}
        <span className="text-sm text-foreground">
          {uploading ? "Processing…" : "Drop a file or click to browse"}
        </span>
        <span className="text-xs text-muted-foreground">PDF or TXT · max {MAX_SIZE_MB} MB</span>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.txt"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFile(file);
          }}
        />
      </div>
      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
    </ModalShell>
  );
};

// ─── Link modal ───────────────────────────────────────────────────────────────

const LinkModal: React.FC<{
  ensureSession: () => Promise<string | null>;
  onClose: () => void;
  onSourceAdded: (sessionId: string) => void;
  visuals: boolean;
}> = ({ ensureSession, onClose, onSourceAdded, visuals }) => {
  const [url, setUrl] = React.useState("");
  const [adding, setAdding] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const trimmed = url.trim();
    if (!/^https?:\/\/.+/i.test(trimmed)) {
      setError("Enter a valid http(s) URL.");
      return;
    }

    setAdding(true);
    const sid = await ensureSession();
    if (!sid) {
      setError("Could not start a chat.");
      setAdding(false);
      return;
    }
    try {
      const res = await fetch(`${BASE_URL}/api/upload_url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: trimmed, session_id: sid, visuals }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Failed to add link.");
        return;
      }
      onSourceAdded(sid);
      onClose();
    } catch {
      setError("Could not reach the backend.");
    } finally {
      setAdding(false);
    }
  };

  return (
    <ModalShell title="Add link" onClose={onClose}>
      <label className="text-xs text-muted-foreground">Public web page URL</label>
      <div className="mt-1.5 flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 focus-within:border-ring">
        <LinkIcon size={14} className="text-muted-foreground shrink-0" />
        <input
          autoFocus
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (!adding) submit();
            }
          }}
          placeholder="https://example.com/docs"
          className="flex-1 bg-transparent py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground"
        />
      </div>
      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button
          onClick={onClose}
          className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={submit}
          disabled={adding || !url.trim()}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40 transition-opacity"
        >
          {adding ? <Loader size={14} className="animate-spin" /> : <FileText size={14} />}
          {adding ? "Fetching…" : "Add source"}
        </button>
      </div>
    </ModalShell>
  );
};

// ─── Menu ─────────────────────────────────────────────────────────────────────

const AddSourceMenu: React.FC<AddSourceMenuProps> = ({ ensureSession, onSourceAdded, visuals, disabled, variant = "icon" }) => {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [modal, setModal] = React.useState<ModalKind>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  const open = (k: ModalKind) => {
    setModal(k);
    setMenuOpen(false);
  };

  const isCta = variant === "cta";
  const menuPosition = isCta ? "top-full mt-2" : "bottom-12";

  return (
    <>
      <div className={`relative ${isCta ? "" : "shrink-0"}`} ref={menuRef}>
        {isCta ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (!disabled) setMenuOpen((v) => !v);
            }}
            disabled={disabled}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:opacity-40 transition-opacity"
          >
            <Plus className="w-4 h-4" /> Add a source
          </button>
        ) : (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (!disabled) setMenuOpen((v) => !v);
            }}
            disabled={disabled}
            title="Add a source"
            className={`p-2 rounded-full transition-colors ${
              menuOpen ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground hover:bg-muted"
            } disabled:opacity-40`}
          >
            <Plus className="w-5 h-5" />
          </button>
        )}
        {menuOpen && (
          <div className={`absolute ${menuPosition} left-1/2 -translate-x-1/2 z-30 w-48 rounded-xl border border-border bg-popover shadow-lg p-1`}>
            <button
              onClick={() => open("doc")}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-popover-foreground hover:bg-accent transition-colors"
            >
              <Upload size={15} /> Upload document
            </button>
            <button
              onClick={() => open("link")}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-popover-foreground hover:bg-accent transition-colors"
            >
              <LinkIcon size={15} /> Add link
            </button>
          </div>
        )}
      </div>

      {modal === "doc" && (
        <DocumentModal ensureSession={ensureSession} onClose={() => setModal(null)} onSourceAdded={onSourceAdded} visuals={visuals} />
      )}
      {modal === "link" && (
        <LinkModal ensureSession={ensureSession} onClose={() => setModal(null)} onSourceAdded={onSourceAdded} visuals={visuals} />
      )}
    </>
  );
};

export default AddSourceMenu;
