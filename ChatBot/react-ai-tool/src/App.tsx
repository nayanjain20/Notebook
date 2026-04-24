import React from "react";
import "./App.css";
import ChatPannel from "./components/ChatPannel";
import { LoaderSpinner } from "./components/LoaderSpinner";
import ChatHeader from "./components/ChatHeader";
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
  sources?: string[];
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
  const scrollToAns = React.useRef<any>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const { messages, isLoading, sendMessage } = useChat();

  React.useEffect(() => {
    if (!isLoading) {
      inputRef.current?.focus();
    }
  }, [isLoading]);

  React.useEffect(() => {
    if (scrollToAns.current) {
      scrollToAns.current.scrollTop = scrollToAns.current.scrollHeight;
    }
  }, [messages]);

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

  return (
    <div className="dark scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-zinc-900 h-screen flex flex-col bg-zinc-900">
      <LoaderSpinner isLoading={isLoading} />
      <ChatHeader />
      <ChatPannel chatMessages={messages} scrollToAns={scrollToAns} />
      <div className="h-[80px] shrink-0 flex items-center">
        <div
          className="bg-zinc-800 w-1/2 text-white m-auto rounded-4xl border border-zinc-400 flex items-center p-2 h-16 cursor-text"
          onClick={() => inputRef.current?.focus()}
        >
          <input
            ref={inputRef}
            autoFocus
            type="text"
            placeholder="Ask me anything"
            className="w-full h-full p-3 outline-none bg-transparent"
            value={inputText}
            onKeyDown={onKeyDownQuestion}
            onChange={(e) => setInputText(e.target.value)}
            disabled={isLoading}
          />
          <button
            onClick={() => onPrompt(inputText)}
            disabled={isLoading}
            className="p-2 rounded-full bg-zinc-700 hover:bg-zinc-600 text-white shrink-0 disabled:opacity-40"
          >
            <ArrowUp className="w-5 h-5" />
          </button>
        </div>
      </div>
    </div>
  );
}

export default App;
