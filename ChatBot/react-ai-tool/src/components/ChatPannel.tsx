import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatTypeEnum, RoleEnum, type IChatMessage, type IChatTypes, type IRoleTypes } from "../App";
import { Copy, CopyCheck, BookText, ChevronDown, FileText, Link as LinkIcon, Plus, Check, Loader, Sparkles } from "lucide-react";
import MermaidDiagram from "./MermaidDiagram";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

// Playful, Claude-Code-style status verbs shown while the agent responds.
const THINKING_VERBS = [
  "Cooking", "Brewing", "Roasting", "Pondering", "Simmering", "Percolating",
  "Marinating", "Crunching", "Noodling", "Untangling", "Distilling", "Mulling",
];

const ThinkingIndicator: React.FC = () => {
  const [i, setI] = React.useState(() => Math.floor(Math.random() * THINKING_VERBS.length));
  React.useEffect(() => {
    const id = setInterval(() => setI((v) => (v + 1) % THINKING_VERBS.length), 1600);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="py-3">
      <div className="inline-flex items-center gap-2.5 text-sm">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary/50" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
        </span>
        <span className="font-medium text-foreground/90 animate-pulse">{THINKING_VERBS[i]}…</span>
        <span className="text-muted-foreground">reading your sources</span>
      </div>
    </div>
  );
};


// ─── Types ───────────────────────────────────────────────────────────────────

interface IChatPannel {
  chatMessages: IChatMessage[];
  scrollToAns: React.RefObject<HTMLDivElement>;
  sessionId: string | null;
  onSourceAdded: (sessionId: string) => void;
  onFollowUp: (text: string) => void;
  isLoading: boolean;
}

interface IMessageBody {
  role: IRoleTypes;
  type: IChatTypes;
  text?: string;
  base64Image?: string;
  imageUrl?: string;
  confidence?: number;
  sources?: { filename: string; page: string; chunk_id: number }[];
  followUps?: string[];
  suggestedLinks?: { url: string; title: string }[];
  diagram?: { mermaid: string; caption?: string } | null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Flattens IChatMessage[] parts into a flat renderable list. */
function flattenMessages(chatMessages: IChatMessage[]): IMessageBody[] {
  return chatMessages
    .flatMap((chat) =>
      chat.parts.map((part): IMessageBody | null => {
        if ("text" in part)
          return {
            role: chat.role,
            type: ChatTypeEnum.Text,
            text: part.text,
            confidence: chat.confidence,
            sources: chat.sources,
            followUps: chat.follow_ups,
            suggestedLinks: chat.suggested_links,
            diagram: chat.diagram,
          };
        if ("base64Image" in part)
          return { role: chat.role, type: ChatTypeEnum.Image, base64Image: part.base64Image };
        if ("imageUrl" in part)
          return { role: chat.role, type: ChatTypeEnum.ImageUrl, imageUrl: part.imageUrl };
        return null;
      })
    )
    .filter((m): m is IMessageBody => m !== null);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

/** Renders an image with a copy-to-clipboard button that appears on hover. */
const ImageMessage: React.FC<{ msg: IMessageBody }> = ({ msg }) => {
  const [copied, setCopied] = React.useState(false);

  const imageSrc =
    msg.type === ChatTypeEnum.ImageUrl
      ? msg.imageUrl!
      : `data:image/png;base64,${msg.base64Image}`;

  const handleCopy = async () => {
    try {
      const res = await fetch(imageSrc);
      const blob = await res.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable or blocked
    }
  };

  return (
    <div className="relative group max-w-xs rounded-lg overflow-hidden shadow-md border border-zinc-600">
      <img src={imageSrc} alt="Generated" className="rounded-lg w-full" />
      <button
        onClick={handleCopy}
        title="Copy image to clipboard"
        aria-label="Copy image"
        className="absolute top-2 right-2 p-2 bg-white/90 text-zinc-800 rounded-full shadow
                   opacity-0 group-hover:opacity-100 focus:opacity-100
                   hover:bg-white focus:outline-none transition-opacity"
      >
        {copied
          ? <CopyCheck size={18} className="scale-110 transition-transform duration-300" />
          : <Copy size={18} className="transition-transform duration-200" />
        }
      </button>
    </div>
  );
};

const confidenceColor = (c: number) =>
  c >= 0.8 ? "text-emerald-600 dark:text-emerald-400" : c >= 0.5 ? "text-amber-600 dark:text-amber-400" : "text-destructive";

/** Follow-up option chips — click to ask that question next. */
const FollowUps: React.FC<{ items: string[]; onFollowUp: (t: string) => void }> = ({ items, onFollowUp }) => (
  <div className="mt-3">
    <span className="text-xs font-medium text-muted-foreground">You might want to</span>
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {items.map((q, i) => (
        <button
          key={i}
          onClick={() => onFollowUp(q)}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground hover:bg-accent hover:border-ring transition-colors"
        >
          <Sparkles size={12} className="text-muted-foreground" />
          {q}
        </button>
      ))}
    </div>
  </div>
);

/** Curated links the assistant recommends adding as sources (add each / add all). */
const SuggestedLinks: React.FC<{
  items: { url: string; title: string }[];
  sessionId: string | null;
  onSourceAdded: (sessionId: string) => void;
}> = ({ items, sessionId, onSourceAdded }) => {
  const [status, setStatus] = React.useState<Record<string, "idle" | "adding" | "done" | "error">>({});

  const addOne = async (url: string) => {
    if (!sessionId || status[url] === "adding" || status[url] === "done") return;
    setStatus((s) => ({ ...s, [url]: "adding" }));
    try {
      const res = await fetch(`${BASE_URL}/api/upload_url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, session_id: sessionId, visuals: true }),
      });
      if (!res.ok) throw new Error();
      setStatus((s) => ({ ...s, [url]: "done" }));
      onSourceAdded(sessionId);
    } catch {
      setStatus((s) => ({ ...s, [url]: "error" }));
    }
  };

  const addAll = () => items.forEach((it) => addOne(it.url));
  const remaining = items.filter((it) => status[it.url] !== "done" && status[it.url] !== "adding").length;

  return (
    <div className="mt-3 rounded-lg border border-border bg-muted/40 p-2.5">
      <div className="flex items-center justify-between mb-1.5">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <LinkIcon size={12} /> Suggested sources to add
        </span>
        {items.length > 1 && remaining > 0 && (
          <button
            onClick={addAll}
            disabled={!sessionId}
            className="text-xs font-medium text-foreground hover:underline disabled:opacity-40"
          >
            Add all
          </button>
        )}
      </div>
      <ul className="space-y-1">
        {items.map(({ url, title }) => {
          const st = status[url] ?? "idle";
          return (
            <li key={url} className="flex items-center gap-2 text-xs">
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="flex-1 min-w-0 truncate text-foreground/90 hover:text-foreground underline underline-offset-2 decoration-muted-foreground"
                title={url}
              >
                {title}
              </a>
              <button
                onClick={() => addOne(url)}
                disabled={!sessionId || st === "adding" || st === "done"}
                title={st === "done" ? "Added" : st === "error" ? "Failed — retry" : "Add as source"}
                className={`inline-flex items-center gap-1 shrink-0 rounded-md px-2 py-1 border transition-colors
                  ${st === "done"
                    ? "border-emerald-600/40 text-emerald-600 dark:text-emerald-400"
                    : st === "error"
                      ? "border-destructive/50 text-destructive hover:bg-destructive/10"
                      : "border-border text-foreground hover:bg-accent"}`}
              >
                {st === "adding" ? <Loader size={11} className="animate-spin" />
                  : st === "done" ? <Check size={11} />
                  : <Plus size={11} />}
                {st === "done" ? "Added" : st === "adding" ? "Adding" : st === "error" ? "Retry" : "Add"}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

/** Renders a single chat bubble — user (right-aligned) or model (left-aligned). */
const MessageBubble: React.FC<{
  msg: IMessageBody;
  sessionId: string | null;
  onSourceAdded: (sessionId: string) => void;
  onFollowUp: (t: string) => void;
}> = ({ msg, sessionId, onSourceAdded, onFollowUp }) => {
  const isUser = msg.role === RoleEnum.User;
  const [showSources, setShowSources] = React.useState(false);

  if (msg.type !== ChatTypeEnum.Text) {
    return (
      <div className={`py-1.5 ${isUser ? "flex justify-end" : ""}`}>
        <ImageMessage msg={msg} />
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end py-1.5">
        <div className="bg-secondary text-secondary-foreground border border-border text-[15px] leading-relaxed px-4 py-2.5 rounded-2xl rounded-br-sm max-w-[85%] whitespace-pre-wrap">
          {msg.text}
        </div>
      </div>
    );
  }

  // Grounded answers carry citations; only then do we surface confidence/sources.
  const hasSources = !!(msg.sources && msg.sources.length > 0);
  const followUps = msg.followUps ?? [];
  const suggestedLinks = msg.suggestedLinks ?? [];

  return (
    <div className="py-2">
      <div className="w-full">
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
        </div>

        {msg.diagram?.mermaid && (
          <MermaidDiagram chart={msg.diagram.mermaid} caption={msg.diagram.caption} />
        )}

        {hasSources && (
          <div className="mt-3">
            <button
              onClick={() => setShowSources((v) => !v)}
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors rounded-md px-1.5 py-1 -ml-1.5 hover:bg-muted"
              title="View sources and confidence"
            >
              <BookText size={13} />
              <span>{msg.sources!.length} source{msg.sources!.length > 1 ? "s" : ""}</span>
              {msg.confidence !== undefined && (
                <>
                  <span className="text-border">·</span>
                  <span className={confidenceColor(msg.confidence)}>
                    {Math.round(msg.confidence * 100)}%
                  </span>
                </>
              )}
              <ChevronDown size={12} className={`transition-transform ${showSources ? "rotate-180" : ""}`} />
            </button>

            {showSources && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {msg.sources!.map((s, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted border border-border text-muted-foreground text-xs"
                  >
                    <FileText size={11} className="opacity-70" />
                    <span className="font-medium text-foreground/80">{s.filename}</span>
                    <span className="opacity-50">·</span>
                    <span>{s.page !== "N/A" ? `p. ${s.page}` : `part ${s.chunk_id + 1}`}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {suggestedLinks.length > 0 && (
          <SuggestedLinks items={suggestedLinks} sessionId={sessionId} onSourceAdded={onSourceAdded} />
        )}

        {followUps.length > 0 && (
          <FollowUps items={followUps} onFollowUp={onFollowUp} />
        )}
      </div>
    </div>
  );
};

// ─── Main Component ───────────────────────────────────────────────────────────

const ChatPannel: React.FC<IChatPannel> = ({ chatMessages, scrollToAns, sessionId, onSourceAdded, onFollowUp, isLoading }) => {
  const messageList = useMemo(() => flattenMessages(chatMessages), [chatMessages]);

  return (
    <div
      ref={scrollToAns}
      className="flex-1 overflow-y-auto py-6 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent"
    >
      <div className="mx-auto w-full max-w-3xl px-4">
        {messageList.map((msg, index) => (
          <MessageBubble
            key={index}
            msg={msg}
            sessionId={sessionId}
            onSourceAdded={onSourceAdded}
            onFollowUp={onFollowUp}
          />
        ))}
        {isLoading && <ThinkingIndicator />}
      </div>
    </div>
  );
};

export default ChatPannel;
