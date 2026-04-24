import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatTypeEnum, RoleEnum, type IChatMessage, type IChatTypes, type IRoleTypes } from "../App";
import { Copy, CopyCheck } from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────────────────

interface IChatPannel {
  chatMessages: IChatMessage[];
  scrollToAns: React.RefObject<HTMLDivElement>;
}

interface IMessageBody {
  role: IRoleTypes;
  type: IChatTypes;
  text?: string;
  base64Image?: string;
  imageUrl?: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Flattens IChatMessage[] parts into a flat renderable list. */
function flattenMessages(chatMessages: IChatMessage[]): IMessageBody[] {
  return chatMessages
    .flatMap((chat) =>
      chat.parts.map((part): IMessageBody | null => {
        if ("text" in part)
          return { role: chat.role, type: ChatTypeEnum.Text, text: part.text };
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

/** Renders a single chat bubble — user (right-aligned) or model (left-aligned). */
const MessageBubble: React.FC<{ msg: IMessageBody }> = ({ msg }) => {
  const isUser = msg.role === RoleEnum.User;

  const content =
    msg.type === ChatTypeEnum.Text
      ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
      : <ImageMessage msg={msg} />;

  if (isUser) {
    return (
      <div className="flex justify-end px-4 py-1">
        <div className="bg-zinc-700 text-white text-base px-4 py-2 rounded-tl-3xl rounded-br-3xl rounded-bl-3xl max-w-[70%]">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-1">
      <div className="text-white text-base max-w-[70%]">
        {content}
      </div>
    </div>
  );
};

// ─── Main Component ───────────────────────────────────────────────────────────

const ChatPannel: React.FC<IChatPannel> = ({ chatMessages, scrollToAns }) => {
  const messageList = useMemo(() => flattenMessages(chatMessages), [chatMessages]);

  return (
    <div
      ref={scrollToAns}
      className="flex-1 overflow-y-auto py-4 scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-zinc-900"
    >
      {messageList.map((msg, index) => (
        <MessageBubble key={index} msg={msg} />
      ))}
    </div>
  );
};

export default ChatPannel;
