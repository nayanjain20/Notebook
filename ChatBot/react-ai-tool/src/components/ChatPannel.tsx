import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatTypeEnum, RoleEnum, type IChatMessage, type IChatTypes, type IRoleTypes } from "../App";
import { Copy, CopyCheck, BookText, ChevronDown, FileText, Check, Sparkles } from "lucide-react";
import MermaidDiagram from "./MermaidDiagram";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

// Playful, Claude-Code-style status verbs shown while the agent responds.
const THINKING_VERBS = [
  "Cooking", "Brewing", "Roasting", "Pondering", "Simmering", "Percolating",
  "Marinating", "Crunching", "Noodling", "Untangling", "Distilling", "Mulling",
];

const ThinkingIndicator: React.FC<{ steps: string[] }> = ({ steps }) => {
  const [i, setI] = React.useState(() => Math.floor(Math.random() * THINKING_VERBS.length));
  React.useEffect(() => {
    const id = setInterval(() => setI((v) => (v + 1) % THINKING_VERBS.length), 1600);
    return () => clearInterval(id);
  }, []);

  if (steps.length === 0) {
    return (
      <div className="py-3">
        <div className="inline-flex items-center gap-2.5 text-sm">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary/50" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
          </span>
          <span className="font-medium text-foreground/90 animate-pulse">{THINKING_VERBS[i]}…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="py-3 space-y-1">
      {steps.map((s, idx) => {
        const isLast = idx === steps.length - 1;
        return (
          <div key={idx} className={`flex items-center gap-2 text-xs ${isLast ? "text-foreground/90" : "text-muted-foreground/70"}`}>
            {isLast ? (
              <span className="relative flex h-1.5 w-1.5 shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary/50" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary" />
              </span>
            ) : (
              <Check size={11} className="shrink-0 text-muted-foreground/60" />
            )}
            <span className={isLast ? "animate-pulse" : ""}>{s}</span>
          </div>
        );
      })}
    </div>
  );
};

/** Collapsible small-font trace of what the agent did for a past message. */
const ThoughtTrace: React.FC<{ steps: string[] }> = ({ steps }) => {
  const [open, setOpen] = React.useState(false);
  if (!steps.length) return null;
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
      >
        <Sparkles size={11} />
        Thought process
        <ChevronDown size={10} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5 border-l border-border pl-2.5">
          {steps.map((s, i) => (
            <li key={i} className="text-[11px] text-muted-foreground">{s}</li>
          ))}
        </ul>
      )}
    </div>
  );
};


// ─── Types ───────────────────────────────────────────────────────────────────

interface IChatPannel {
  chatMessages: IChatMessage[];
  scrollToAns: React.RefObject<HTMLDivElement>;
  onFollowUp: (text: string) => void;
  isLoading: boolean;
  liveSteps: string[];
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
  steps?: string[];
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
            steps: chat.steps,
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

/** Renders a single chat bubble — user (right-aligned) or model (left-aligned). */
const MessageBubble: React.FC<{
  msg: IMessageBody;
  onFollowUp: (t: string) => void;
}> = ({ msg, onFollowUp }) => {
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

  return (
    <div className="py-2">
      <div className="w-full">
        {msg.steps && msg.steps.length > 0 && <ThoughtTrace steps={msg.steps} />}
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

        {followUps.length > 0 && (
          <FollowUps items={followUps} onFollowUp={onFollowUp} />
        )}
      </div>
    </div>
  );
};

// ─── Main Component ───────────────────────────────────────────────────────────

const ChatPannel: React.FC<IChatPannel> = ({ chatMessages, scrollToAns, onFollowUp, isLoading, liveSteps }) => {
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
            onFollowUp={onFollowUp}
          />
        ))}
        {isLoading && <ThinkingIndicator steps={liveSteps} />}
      </div>
    </div>
  );
};

export default ChatPannel;
