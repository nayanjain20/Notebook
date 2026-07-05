import React from "react";
import { Trash2, MessageSquare } from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export interface ISession {
  id: string;
  title: string;
  updated_at: string;
}

interface SessionListProps {
  activeSessionId: string | null;
  onSelect: (session: ISession) => void;
  onDelete: (sessionId: string) => void;
}

export interface SessionListRef {
  updateTitle: (sessionId: string, title: string) => void;
  addSession: (session: ISession) => void;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const SessionList = React.forwardRef<SessionListRef, SessionListProps>(
  ({ activeSessionId, onSelect, onDelete }, ref) => {
    const [sessions, setSessions] = React.useState<ISession[]>([]);

    React.useEffect(() => {
      fetch(`${BASE_URL}/api/sessions`)
        .then((r) => r.json())
        .then((data) => setSessions(data.sessions ?? []))
        .catch(() => {});
    }, []);

    React.useImperativeHandle(ref, () => ({
      updateTitle: (sessionId: string, title: string) => {
        setSessions((prev) =>
          prev.map((s) => (s.id === sessionId ? { ...s, title } : s))
        );
      },
      addSession: (session: ISession) => {
        setSessions((prev) =>
          prev.some((s) => s.id === session.id) ? prev : [session, ...prev]
        );
      },
    }));

    const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      try {
        await fetch(`${BASE_URL}/api/sessions/${sessionId}`, { method: "DELETE" });
        setSessions((prev) => prev.filter((s) => s.id !== sessionId));
        onDelete(sessionId);
      } catch {}
    };

    return (
      <div className="flex flex-col flex-1 overflow-hidden">
        {/* Header */}
        <div className="flex items-center px-4 py-3 border-b border-sidebar-border">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Chats</span>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto py-1 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent">
          {sessions.length === 0 ? (
            <p className="text-xs text-muted-foreground/70 text-center mt-6 px-3">No chats yet.</p>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => onSelect(s)}
                className={`w-full text-left flex items-start gap-2 px-3 py-2 mx-0 group transition-colors
                  ${activeSessionId === s.id ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"}`}
              >
                <MessageSquare size={13} className="mt-0.5 shrink-0 opacity-60" />
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] truncate font-medium" title={s.title}>{s.title}</p>
                  <p className="text-[10px] text-muted-foreground/70">{relativeTime(s.updated_at)}</p>
                </div>
                <button
                  onClick={(e) => handleDelete(e, s.id)}
                  title="Delete chat"
                  className="opacity-0 group-hover:opacity-100 p-0.5 text-muted-foreground/70 hover:text-destructive transition-opacity shrink-0 mt-0.5"
                >
                  <Trash2 size={12} />
                </button>
              </button>
            ))
          )}
        </div>
      </div>
    );
  }
);

export default SessionList;
