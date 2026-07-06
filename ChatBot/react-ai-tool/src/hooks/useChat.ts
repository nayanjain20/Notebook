import React from "react";
import { RoleEnum, type IChatMessage } from "../App";
import type { ISession } from "../components/SessionList";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export const useChat = (
  visuals: boolean,
  sessionId: string | null,
  onSessionCreated?: (session: ISession) => void,
  onSourcesAdded?: () => void,
  confidential?: boolean,
  model?: string | null
) => {
  const [messages, setMessages] = React.useState<IChatMessage[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [latestTitle, setLatestTitle] = React.useState<string | null>(null);
  const [liveSteps, setLiveSteps] = React.useState<string[]>([]);
  const skipLoadRef = React.useRef(false);

  React.useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    if (skipLoadRef.current) {
      skipLoadRef.current = false;
      return;
    }
    fetch(`${BASE_URL}/api/sessions/${sessionId}`)
      .then((r) => r.json())
      .then((data) => setMessages(data.messages ?? []))
      .catch(() => setMessages([]));
  }, [sessionId]);

  // Re-fetch messages for a session (e.g. after a source add appends a summary).
  const refreshMessages = React.useCallback(async (sid: string) => {
    try {
      const res = await fetch(`${BASE_URL}/api/sessions/${sid}`);
      const data = await res.json();
      setMessages(data.messages ?? []);
    } catch {
      /* keep existing */
    }
  }, []);

  // Creates a backend session lazily (used when the first source is added).
  // The confidential mode is fixed here, at creation, and never changes after.
  const createSession = async (): Promise<string | null> => {
    try {
      const res = await fetch(`${BASE_URL}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confidential: !!confidential, model: model ?? undefined }),
      });
      const session: ISession = await res.json();
      skipLoadRef.current = true;
      setMessages([]);
      onSessionCreated?.(session);
      return session.id;
    } catch {
      return null;
    }
  };

  const ensureSession = async (): Promise<string | null> =>
    sessionId ?? (await createSession());

  const sendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isLoading || !sessionId) return; // chat requires an existing session

    setIsLoading(true);
    setLiveSteps([]);
    const priorMessages = messages;
    const userMessage: IChatMessage = { role: RoleEnum.User, parts: [{ text: trimmed }] };
    setMessages((prev) => [...prev, userMessage]);

    try {
      const res = await fetch(`${BASE_URL}/api/chat_stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          history: priorMessages,
          session_id: sessionId,
          visuals,
        }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Server error ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalData: any = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.trim();
          if (!line.startsWith("data:")) continue;
          let ev: any;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === "step") {
            setLiveSteps((prev) => [...prev, ev.text]);
          } else if (ev.type === "sources_added") {
            onSourcesAdded?.();
          } else if (ev.type === "final") {
            finalData = ev;
          } else if (ev.type === "error") {
            throw new Error(ev.text);
          }
        }
      }

      if (finalData) {
        const botMessage: IChatMessage = {
          role: RoleEnum.Model,
          parts: [{ text: finalData.answer }],
          confidence: finalData.confidence,
          sources: finalData.sources,
          follow_ups: finalData.follow_ups,
          suggested_links: finalData.suggested_links,
          diagram: finalData.diagram,
          steps: finalData.steps,
        };
        setMessages((prev) => [...prev, botMessage]);
        if (finalData.session_title) setLatestTitle(finalData.session_title);
      } else {
        throw new Error("No response");
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: RoleEnum.Model, parts: [{ text: "Error: could not reach the backend." }] },
      ]);
    } finally {
      setIsLoading(false);
      setLiveSteps([]);
    }
  };

  return { messages, isLoading, sendMessage, latestTitle, ensureSession, refreshMessages, liveSteps };
};
