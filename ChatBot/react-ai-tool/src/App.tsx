import React from "react";
import "./App.css";
import ChatPannel from "./components/ChatPannel";
import ChatHeader from "./components/ChatHeader";
import FileUpload from "./components/FileUpload";
import AddSourceMenu from "./components/AddSourceMenu";
import SessionList, { type ISession, type SessionListRef } from "./components/SessionList";
import { ArrowUp } from "lucide-react";
import { useChat } from "./hooks/useChat";

export type ChatPart =
  | { text: string }
  | { base64Image: string }
  | { base64Video: string }
  | { imageUrl: string };

export interface IChatMessage {
  role: IRoleTypes;
  parts: ChatPart[];
  confidence?: number;
  sources?: { filename: string; page: string; chunk_id: number }[];
  follow_ups?: string[];
  suggested_links?: { url: string; title: string }[];
  diagram?: { mermaid: string; caption?: string } | null;
  steps?: string[];
}

export const RoleEnum = {
  User: "user",
  Model: "model",
} as const;
export type IRoleTypes = (typeof RoleEnum)[keyof typeof RoleEnum];

export const ChatTypeEnum = {
  Text: "Text",
  Image: "Image",
  ImageUrl: "ImageUrl",
  Video: "Video",
} as const;
export type IChatTypes = keyof typeof ChatTypeEnum;

function App() {
  const [inputText, setInputText] = React.useState<string>("");
  const [visuals, setVisuals] = React.useState(true);
  const [hasDocs, setHasDocs] = React.useState(false);
  const [docsRefresh, setDocsRefresh] = React.useState(0);
  const [sidebarOpen, setSidebarOpen] = React.useState(false);
  const [ingesting, setIngesting] = React.useState(false);
  // Draft mode: an empty new-chat state before a source (and session) exist.
  const [isDrafting, setIsDrafting] = React.useState(true);
  const [activeSession, setActiveSession] = React.useState<ISession | null>(null);
  const scrollToAns = React.useRef<any>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const sessionListRef = React.useRef<SessionListRef>(null);

  const handleSessionCreated = React.useCallback((session: ISession) => {
    setIsDrafting(false);
    setActiveSession(session);
    sessionListRef.current?.addSession(session);
  }, []);

  const { messages, isLoading, sendMessage, latestTitle, ensureSession, refreshMessages, liveSteps } = useChat(
    visuals,
    activeSession?.id ?? null,
    handleSessionCreated,
    () => setDocsRefresh((v) => v + 1)
  );

  React.useEffect(() => {
    if (!isLoading) inputRef.current?.focus();
  }, [isLoading]);

  React.useEffect(() => {
    if (scrollToAns.current) {
      scrollToAns.current.scrollTop = scrollToAns.current.scrollHeight;
    }
  }, [messages, isLoading]);

  React.useEffect(() => { setHasDocs(false); }, [activeSession?.id]);

  // Update sidebar title when the LLM generates one (from the first source summary)
  React.useEffect(() => {
    if (latestTitle && activeSession) {
      setActiveSession((s) => s ? { ...s, title: latestTitle } : s);
      sessionListRef.current?.updateTitle(activeSession.id, latestTitle);
    }
  }, [latestTitle]);

  const onPrompt = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setInputText("");
    await sendMessage(trimmed);
  };

  const onKeyDownQuestion = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onPrompt(inputText);
    }
  };

  const handleSelectSession = (session: ISession) => {
    setIsDrafting(false);
    setActiveSession(session);
  };

  const handleNewChat = () => {
    setActiveSession(null);
    setIsDrafting(true);
    setInputText("");
  };

  const handleSessionDelete = (deletedId: string) => {
    if (activeSession?.id === deletedId) {
      setActiveSession(null);
      setIsDrafting(true);
    }
  };

  // Called after any source is added — refresh the sidebar list and reload
  // messages so the freshly generated summary appears in the chat.
  const handleSourceAdded = React.useCallback((sid: string) => {
    setIngesting(false);
    setDocsRefresh((v) => v + 1);
    if (sid) refreshMessages(sid);
  }, [refreshMessages]);

  const noSessionSelected = !activeSession;
  const canChat = !!activeSession && hasDocs;   // chat only after a source exists

  return (
    <div className="scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent h-screen flex bg-background text-foreground">
      {/* Sidebar */}
      <div
        className={`shrink-0 flex flex-col border-r border-sidebar-border bg-sidebar overflow-hidden transition-all duration-200
          ${sidebarOpen ? "w-64" : "w-0 border-r-0"}`}
      >
        {/* Sessions — takes remaining space */}
        <SessionList
          ref={sessionListRef}
          activeSessionId={activeSession?.id ?? null}
          onSelect={handleSelectSession}
          onDelete={handleSessionDelete}
        />

        {/* Sources — pinned at bottom (read-only list; add via + near input) */}
        <div className="border-t border-sidebar-border">
          <div className="px-4 py-2.5">
            <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Sources</span>
          </div>
          <FileUpload onDocsChange={setHasDocs} sessionId={activeSession?.id ?? null} refreshKey={docsRefresh} />
        </div>
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <ChatHeader sidebarOpen={sidebarOpen} onToggleSidebar={() => setSidebarOpen((v) => !v)} onNewChat={handleNewChat} />

        {noSessionSelected ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 px-6 text-center">
            <span className="font-serif text-3xl text-foreground/80">Notebook</span>
            {ingesting ? (
              <div className="inline-flex items-center gap-2.5 text-sm">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary/50" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
                </span>
                <span className="font-medium text-foreground/90 animate-pulse">Reading your source…</span>
              </div>
            ) : isDrafting ? (
              <>
                <p className="text-sm text-muted-foreground max-w-sm">
                  Add a document or link to start. Notebook will read it, then help you learn it step by step.
                </p>
                <AddSourceMenu
                  ensureSession={ensureSession}
                  onSourceAdded={handleSourceAdded}
                  onSourceAddStart={() => setIngesting(true)}
                  visuals={visuals}
                  variant="cta"
                />
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Select a chat or start a new one.</p>
            )}
          </div>
        ) : (
          <ChatPannel
            chatMessages={messages}
            scrollToAns={scrollToAns}
            onFollowUp={(t) => onPrompt(t)}
            isLoading={isLoading || ingesting}
            ingesting={ingesting}
            liveSteps={liveSteps}
          />
        )}

        <div className="shrink-0 flex flex-col items-center gap-2.5 py-4">
          {/* Visual representation toggle */}
          <label className="flex items-center gap-2 text-xs select-none cursor-pointer text-foreground">
            <div
              onClick={() => setVisuals((v) => !v)}
              className={`relative w-8 h-4 rounded-full transition-colors ${visuals ? "bg-primary" : "bg-border"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-card shadow-sm transition-transform ${visuals ? "translate-x-4" : ""}`} />
            </div>
            Visual explanations
          </label>

          {/* Input */}
          <div
            className={`bg-card w-[min(42rem,90%)] text-foreground rounded-2xl border flex items-center gap-1 px-2 py-2 h-14 transition-colors shadow-sm
              ${canChat ? "border-border focus-within:border-ring" : "border-border opacity-50"}`}
          >
            <AddSourceMenu
              ensureSession={ensureSession}
              onSourceAdded={handleSourceAdded}
              onSourceAddStart={() => setIngesting(true)}
              visuals={visuals}
              disabled={!activeSession}
            />
            <input
              ref={inputRef}
              autoFocus
              type="text"
              placeholder={canChat ? "Ask about your sources…" : "Add a source to start chatting"}
              className="w-full h-full outline-none bg-transparent placeholder:text-muted-foreground disabled:cursor-not-allowed"
              value={inputText}
              onKeyDown={onKeyDownQuestion}
              onChange={(e) => setInputText(e.target.value)}
              disabled={isLoading || !canChat}
            />
            <button
              onClick={() => onPrompt(inputText)}
              disabled={isLoading || !canChat || !inputText.trim()}
              className="p-2 rounded-full bg-primary text-primary-foreground hover:opacity-90 shrink-0 disabled:opacity-40 transition-opacity"
            >
              <ArrowUp className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
