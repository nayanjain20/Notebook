import React from "react";
import { Upload, FileText, Loader, Trash2 } from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;
const MAX_SIZE_MB = 5;

interface FileUploadProps {
  onDocsChange: (hasDocs: boolean) => void;
  sessionId: string | null;
}

const FileUpload: React.FC<FileUploadProps> = ({ onDocsChange, sessionId }) => {
  const [documents, setDocuments] = React.useState<string[]>([]);
  const [uploading, setUploading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    setDocuments([]);
    onDocsChange(false);
    if (!sessionId) return;

    fetch(`${BASE_URL}/api/docs?session_id=${encodeURIComponent(sessionId)}`)
      .then((r) => r.json())
      .then((data) => {
        const docs = data.documents ?? [];
        setDocuments(docs);
        onDocsChange(docs.length > 0);
      })
      .catch(() => {});
  }, [sessionId]);

  const handleFile = async (file: File) => {
    setError(null);
    if (!sessionId) {
      setError("Select a chat before uploading.");
      return;
    }
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
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", sessionId);

    try {
      const res = await fetch(`${BASE_URL}/api/upload`, { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Upload failed.");
        return;
      }
      const updated = [...new Set([...documents, data.filename])];
      setDocuments(updated);
      onDocsChange(true);
    } catch {
      setError("Could not reach the backend.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const handleDelete = async (filename: string) => {
    if (!sessionId) return;
    try {
      await fetch(
        `${BASE_URL}/api/docs/${encodeURIComponent(filename)}?session_id=${encodeURIComponent(sessionId)}`,
        { method: "DELETE" }
      );
      const updated = documents.filter((d) => d !== filename);
      setDocuments(updated);
      onDocsChange(updated.length > 0);
    } catch {
      setError("Could not delete document.");
    }
  };

  const disabled = !sessionId;

  return (
    <div className="p-3">
      <div
        onDrop={disabled ? undefined : onDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => !disabled && inputRef.current?.click()}
        className={`border border-dashed rounded-xl p-3 flex flex-col items-center gap-1 transition-colors
          ${disabled
            ? "border-zinc-700 opacity-40 cursor-not-allowed"
            : "border-zinc-600 cursor-pointer hover:border-zinc-400"}`}
      >
        {uploading ? (
          <Loader size={18} className="animate-spin text-zinc-400" />
        ) : (
          <Upload size={18} className="text-zinc-400" />
        )}
        <span className="text-xs text-zinc-400 text-center">
          {disabled
            ? "Select a chat to upload"
            : uploading
            ? "Processing…"
            : "Drop PDF or TXT (max 5 MB)"}
        </span>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.txt"
          className="hidden"
          onChange={onFileChange}
        />
      </div>

      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

      {documents.length > 0 && (
        <ul className="mt-2 space-y-1">
          {documents.map((doc) => (
            <li key={doc} className="flex items-center gap-1.5 text-xs text-zinc-300 group">
              <FileText size={12} className="text-zinc-500 shrink-0" />
              <span className="flex-1 truncate">{doc}</span>
              <button
                onClick={() => handleDelete(doc)}
                title="Delete document"
                className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 transition-opacity shrink-0"
              >
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default FileUpload;
