import React from "react";
import { RoleEnum, type IChatMessage } from "../App";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export const useChat = () => {
  const [messages, setMessages] = React.useState<IChatMessage[]>([]);
  const [isLoading, setIsLoading] = React.useState(false);

  const sendMessage = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isLoading) return;

    const userMessage: IChatMessage = {
      role: RoleEnum.User,
      parts: [{ text: trimmed }],
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const res = await fetch(`${BASE_URL}/api/get_response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed, history: messages }),
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
      };
      setMessages((prev) => [...prev, botMessage]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: RoleEnum.Model, parts: [{ text: "Error: could not reach the backend." }] },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  return { messages, isLoading, sendMessage };
};
