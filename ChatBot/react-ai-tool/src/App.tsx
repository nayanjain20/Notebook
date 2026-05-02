import React from "react";
import "./App.css";
import ChatPannel from "./components/ChatPannel";
import { LoaderSpinner } from "./components/LoaderSpinner";
import ChatHeader from "./components/ChatHeader";
import FileUpload from "./components/FileUpload";
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
  const [useDocs, setUseDocs] = React.useState(false);
  const [hasDocs, setHasDocs] = React.useState(false);
  const [activeSession, setActiveSession] = React.useState<ISession | null>(null);
  const scrollToAns = React.useRef<any>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const sessionListRef = React.useRef<SessionListRef>(null);
  const { messages, isLoading, sendMessage, latestTitle } = useChat(useDocs, activeSession?.id ?? null);

  React.useEffect(() => {
    if (!isLoading) inputRef.current?.focus();
  }, [isLoading]);

  React.useEffect(() => {
    if (scrollToAns.current) {
      scrollToAns.current.scrollTop = scrollToAns.current.scrollHeight;
    }
  }, [messages]);

  React.useEffect(() => { setHasDocs(false); }, [activeSession?.id]);

  // Update sidebar title when LLM generates one after the first exchange
  React.useEffect(() => {
    if (latestTitle && activeSession) {
      setActiveSession((s) => s ? { ...s, title: latestTitle } : s);
      sessionListRef.current?.updateTitle(activeSession.id, latestTitle);
    }
  }, [latestTitle]);

  const onPrompt = async (text: string) => {
    await sendMessage(text);
    setInputText("");
  };

  const onKeyDownQuestion = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onPrompt(inputText);
    }
  };

  const handleSessionDelete = (deletedId: string) => {
    if (activeSession?.id === deletedId) setActiveSession(null);
  };

  const noSessionSelected = !activeSession;

  return (
    <div className="dark scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-zinc-900 h-screen flex bg-zinc-900">
      {/* Sidebar */}
      <div className="w-60 shrink-0 flex flex-col border-r border-zinc-700 overflow-hidden">
        {/* Sessions — takes remaining space */}
        <SessionList
          ref={sessionListRef}
          activeSessionId={activeSession?.id ?? null}
          onSelect={setActiveSession}
          onCreate={setActiveSession}
          onDelete={handleSessionDelete}
        />

        {/* Documents — pinned at bottom */}
        <div className="border-t border-zinc-700">
          <div className="px-3 py-2 border-b border-zinc-700">
            <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">Documents</span>
          </div>
          <FileUpload onDocsChange={setHasDocs} sessionId={activeSession?.id ?? null} />
        </div>
      </div>

      {/* Main chat area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <LoaderSpinner isLoading={isLoading} />
        <ChatHeader />

        {noSessionSelected ? (
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-600 gap-2">
            <span className="text-4xl">💬</span>
            <p className="text-sm">Select a chat or start a new one</p>
          </div>
        ) : (
          <ChatPannel chatMessages={messages} scrollToAns={scrollToAns} />
        )}

        <div className="shrink-0 flex flex-col items-center gap-2 py-3">
          {/* Toggle */}
          <label className={`flex items-center gap-2 text-xs select-none ${hasDocs ? "cursor-pointer text-zinc-300" : "cursor-not-allowed text-zinc-600"}`}>
            <div
              onClick={() => hasDocs && setUseDocs((v) => !v)}
              className={`relative w-8 h-4 rounded-full transition-colors ${useDocs && hasDocs ? "bg-violet-600" : "bg-zinc-600"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white transition-transform ${useDocs && hasDocs ? "translate-x-4" : ""}`} />
            </div>
            Search uploaded docs
          </label>

          {/* Input */}
          <div
            className={`bg-zinc-800 w-1/2 text-white rounded-4xl border flex items-center p-2 h-14 cursor-text transition-colors
              ${noSessionSelected ? "border-zinc-700 opacity-50 pointer-events-none" : "border-zinc-400"}`}
            onClick={() => inputRef.current?.focus()}
          >
            <input
              ref={inputRef}
              autoFocus
              type="text"
              placeholder={noSessionSelected ? "Select a chat to start" : "Ask me anything"}
              className="w-full h-full p-3 outline-none bg-transparent"
              value={inputText}
              onKeyDown={onKeyDownQuestion}
              onChange={(e) => setInputText(e.target.value)}
              disabled={isLoading || noSessionSelected}
            />
            <button
              onClick={() => onPrompt(inputText)}
              disabled={isLoading || noSessionSelected}
              className="p-2 rounded-full bg-zinc-700 hover:bg-zinc-600 text-white shrink-0 disabled:opacity-40"
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
