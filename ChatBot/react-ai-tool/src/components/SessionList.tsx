import React from "react";
import { MessageSquarePlus, Trash2, MessageSquare } from "lucide-react";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export interface ISession {
  id: string;
  title: string;
  updated_at: string;
}

interface SessionListProps {
  activeSessionId: string | null;
  onSelect: (session: ISession) => void;
  onCreate: (session: ISession) => void;
  onDelete: (sessionId: string) => void;
}

export interface SessionListRef {
  updateTitle: (sessionId: string, title: string) => void;
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
  ({ activeSessionId, onSelect, onCreate, onDelete }, ref) => {
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
    }));

    const handleCreate = async () => {
      try {
        const res = await fetch(`${BASE_URL}/api/sessions`, { method: "POST" });
        const session: ISession = await res.json();
        setSessions((prev) => [session, ...prev]);
        onCreate(session);
      } catch {}
    };

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
        <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700">
          <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">Chats</span>
          <button
            onClick={handleCreate}
            title="New chat"
            className="p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <MessageSquarePlus size={15} />
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto py-1">
          {sessions.length === 0 ? (
            <p className="text-xs text-zinc-600 text-center mt-6 px-3">No chats yet.<br />Click + to start one.</p>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => onSelect(s)}
                className={`w-full text-left flex items-start gap-2 px-3 py-2 group transition-colors
                  ${activeSessionId === s.id ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"}`}
              >
                <MessageSquare size={13} className="mt-0.5 shrink-0 opacity-60" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs truncate font-medium">{s.title}</p>
                  <p className="text-[10px] text-zinc-600">{relativeTime(s.updated_at)}</p>
                </div>
                <button
                  onClick={(e) => handleDelete(e, s.id)}
                  title="Delete chat"
                  className="opacity-0 group-hover:opacity-100 p-0.5 text-zinc-600 hover:text-red-400 transition-opacity shrink-0 mt-0.5"
                >
                  <Trash2 size={11} />
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
