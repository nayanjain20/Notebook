import React from "react";
import { PanelLeftClose, Notebook, BookOpen, PenSquare } from "lucide-react";

interface ChatHeaderProps {
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onNewChat: () => void;
}

const ChatHeader: React.FC<ChatHeaderProps> = ({ sidebarOpen, onToggleSidebar, onNewChat }) => {
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-border h-[52px] shrink-0">
      <button
        onClick={onToggleSidebar}
        title={sidebarOpen ? "Collapse sidebar" : "Open notebook"}
        className="group relative p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
      >
        {sidebarOpen ? (
          <PanelLeftClose size={17} />
        ) : (
          <>
            {/* Collapsed: closed Notebook icon, reveal "open notebook" on hover */}
            <Notebook size={17} className="text-foreground group-hover:opacity-0 transition-opacity" />
            <BookOpen
              size={17}
              className="absolute inset-0 m-auto text-foreground opacity-0 group-hover:opacity-100 transition-opacity"
            />
          </>
        )}
      </button>
      <h1 className="font-serif text-xl font-semibold tracking-tight text-foreground">
        Notebook
      </h1>
      <button
        onClick={onNewChat}
        title="New chat"
        className="ml-auto p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
      >
        <PenSquare size={17} />
      </button>
    </div>
  );
};

export default ChatHeader;
