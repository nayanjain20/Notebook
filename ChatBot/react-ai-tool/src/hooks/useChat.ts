import React from "react";
import { RoleEnum, type IChatMessage } from "../App";
import type { ISession } from "../components/SessionList";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export const useChat = (
  useDocs: boolean,
  sessionId: string | null,
  onSessionCreated?: (session: ISession) => void
) => {
  const [messages, setMessages] = React.useState<IChatMessage[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);
  const [latestTitle, setLatestTitle] = React.useState<string | null>(null);
  // When we create a session programmatically we must NOT re-fetch its (empty)
  // messages, or an optimistic first message would be clobbered.
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

  // Creates a backend session lazily and notifies the app.
  const createSession = async (): Promise<string | null> => {
    try {
      const res = await fetch(`${BASE_URL}/api/sessions`, { method: "POST" });
      const session: ISession = await res.json();
      skipLoadRef.current = true;
      setMessages([]);
      onSessionCreated?.(session);
      return session.id;
    } catch {
      return null;
    }
  };

  // Returns the current session id, creating one if none exists yet.
  const ensureSession = async (): Promise<string | null> =>
    sessionId ?? (await createSession());

  const sendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    let sid = sessionId;
    if (!sid) {
      sid = await createSession();
      if (!sid) {
        setMessages((prev) => [
          ...prev,
          { role: RoleEnum.Model, parts: [{ text: "Error: could not start a new chat." }] },
        ]);
        setIsLoading(false);
        return;
      }
    }

    const priorMessages = messages;
    const userMessage: IChatMessage = {
      role: RoleEnum.User,
      parts: [{ text: trimmed }],
    };
    setMessages((prev) => [...prev, userMessage]);

    try {
      const res = await fetch(`${BASE_URL}/api/get_response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          history: priorMessages,
          use_docs: useDocs,
          session_id: sid,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error ?? `Server error ${res.status}`);
      }
      const data = await res.json();
      const botMessage: IChatMessage = {
        role: RoleEnum.Model,
        parts: [{ text: data.answer }],
        confidence: data.confidence,
        sources: data.sources,
        follow_ups: data.follow_ups,
        suggested_links: data.suggested_links,
      };
      setMessages((prev) => [...prev, botMessage]);
      if (data.session_title) setLatestTitle(data.session_title);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: RoleEnum.Model, parts: [{ text: "Error: could not reach the backend." }] },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  return { messages, isLoading, sendMessage, latestTitle, ensureSession };
};
