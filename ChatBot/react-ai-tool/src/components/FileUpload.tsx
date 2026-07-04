import React from "react";
import { FileText, Link as LinkIcon, Trash2 } from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

interface FileUploadProps {
  onDocsChange: (hasDocs: boolean) => void;
  sessionId: string | null;
  refreshKey?: number;
}

const isLink = (name: string) => !/\.(pdf|txt)$/i.test(name);

const FileUpload: React.FC<FileUploadProps> = ({ onDocsChange, sessionId, refreshKey }) => {
  const [documents, setDocuments] = React.useState<string[]>([]);
  const [error, setError] = React.useState<string | null>(null);

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
  }, [sessionId, refreshKey]);

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
      setError("Could not remove source.");
    }
  };

  return (
    <div className="px-3 pb-3">
      {!sessionId ? (
        <p className="text-xs text-muted-foreground/70 px-1">Select a chat to manage sources.</p>
      ) : documents.length === 0 ? (
        <p className="text-xs text-muted-foreground/70 px-1 leading-relaxed">
          No sources yet. Use the <span className="font-medium text-foreground/80">+</span> near the message box to add a document or link.
        </p>
      ) : (
        <ul className="space-y-1">
          {documents.map((doc) => (
            <li
              key={doc}
              className="flex items-center gap-1.5 text-xs text-foreground/90 group"
            >
              {isLink(doc) ? (
                <LinkIcon size={12} className="text-muted-foreground shrink-0" />
              ) : (
                <FileText size={12} className="text-muted-foreground shrink-0" />
              )}
              <span className="flex-1 truncate" title={doc}>{doc}</span>
              <button
                onClick={() => handleDelete(doc)}
                title="Remove source"
                className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity shrink-0"
              >
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
};

export default FileUpload;
