# react-ai-tool (Notebook Frontend)

React + TypeScript single-page application for the Notebook chatbot. Built with Vite, Tailwind CSS, and native `fetch` for API communication.

---

## Running Locally

```bash
npm install
npm run dev       # dev server at http://localhost:5173
npm run build     # TypeScript check + production bundle → dist/
npm run lint      # ESLint
npm run preview   # Preview production build locally
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | Backend API base URL |

`.env.development` is pre-configured:
```
VITE_API_BASE_URL=http://127.0.0.1:5000
```

Update `.env.production` before deploying.

---

## Component Tree

```
App
├── ChatHeader
├── SessionList (sidebar, via ref)
├── FileUpload  (sidebar, session-scoped docs)
├── ChatPannel  (message display)
├── LoaderSpinner (full-screen overlay)
└── [input bar + submit button]
```

---

## Components

### `App.tsx`

Root component. Owns session state and wires everything together.

**State:**
| Name | Type | Description |
|------|------|-------------|
| `activeSession` | `ISession \| null` | Currently selected chat session |
| `inputText` | `string` | Current value of the message input |
| `useDocs` | `boolean` | Whether to use RAG on the next message |
| `hasDocs` | `boolean` | Whether the active session has any uploaded documents |

**Refs:**
- `scrollToAns` — scrolls the message panel to the bottom after each new message
- `inputRef` — focuses the input when not loading
- `sessionListRef` — imperative handle to call `updateTitle` on the sidebar after the LLM generates a session title

**Key behaviors:**
- Passes `sessionId` and `useDocs` into `useChat`; receives `messages`, `isLoading`, `latestTitle`
- Calls `sessionListRef.current.updateTitle()` whenever `latestTitle` changes
- Resets `hasDocs` to `false` when `activeSession` changes
- Disables the input and send button when no session is active or when loading

---

### `SessionList.tsx`

Sidebar showing all chat sessions. Exposed via `React.forwardRef` so `App` can imperatively call `updateTitle`.

**Props:**
| Prop | Type | Description |
|------|------|-------------|
| `activeSessionId` | `string \| null` | Highlights the active session |
| `onSelect` | `(session) => void` | Called when a session is clicked |
| `onCreate` | `(session) => void` | Called after a new session is created |
| `onDelete` | `(sessionId) => void` | Called after a session is deleted |

**Ref handle (`SessionListRef`):**
- `updateTitle(sessionId, title)` — updates the displayed title in the list without re-fetching

**API calls:**
- `GET /api/sessions` — on mount
- `POST /api/sessions` — on `+` button click
- `DELETE /api/sessions/{id}` — on trash icon click

---

### `FileUpload.tsx`

Sidebar panel for managing per-session documents. Supports drag-and-drop and file input.

**Props:**
| Prop | Type | Description |
|------|------|-------------|
| `sessionId` | `string \| null` | Active session; upload/list/delete scoped to this |
| `onDocsChange` | `(hasDocs: boolean) => void` | Notifies `App` when docs are added or removed |

**Validation:**
- Accepted extensions: `.pdf`, `.txt`
- Max file size: 5 MB
- Session must be selected before upload

**API calls:**
- `GET /api/docs?session_id=` — on sessionId change
- `POST /api/upload` — on file selection (FormData: `file`, `session_id`)
- `DELETE /api/docs/{filename}?session_id=` — on delete button

---

### `ChatPannel.tsx`

Displays the full message history. Purely presentational.

**Props:**
| Prop | Type | Description |
|------|------|-------------|
| `chatMessages` | `IChatMessage[]` | Full message list from `useChat` |
| `scrollToAns` | `React.RefObject<HTMLDivElement>` | Container ref for auto-scroll |

Flattens multi-part `IChatMessage` objects into flat `IMessageBody[]` via `useMemo`.

**Message rendering:**
- User messages: right-aligned, dark gray bubble
- Bot messages: left-aligned, rendered as Markdown (GFM via `react-markdown` + `remark-gfm`)
- Bot messages show: confidence badge (green ≥80%, yellow 50–79%, red <50%) and source pills

---

### `ChatHeader.tsx`

Static header bar displaying the "Notebook" title in a pink-to-violet gradient.

---

### `LoaderSpinner.tsx`

Full-screen semi-transparent overlay with a spinning SVG. Shown while an API request is in flight.

**Props:** `{ isLoading: boolean }`

---

## `useChat` Hook

```ts
const { messages, isLoading, sendMessage, latestTitle } =
  useChat(useDocs: boolean, sessionId: string | null)
```

**Returns:**
| Name | Type | Description |
|------|------|-------------|
| `messages` | `IChatMessage[]` | Full message history for the active session |
| `isLoading` | `boolean` | True while a request is in flight |
| `sendMessage` | `(text: string) => Promise<void>` | Send a message; updates `messages` optimistically |
| `latestTitle` | `string \| null` | Set when the backend returns a generated session title |

**On `sessionId` change:** fetches `GET /api/sessions/{id}` and replaces `messages`.

**`sendMessage` flow:**
1. Append user message to `messages`
2. POST to `/api/get_response` with `{ message, history, use_docs, session_id }`
3. Append model response (with `confidence` and `sources`)
4. Set `latestTitle` if `response.session_title` is returned
5. On error: append an error message to `messages`

---

## TypeScript Types

```ts
type ChatPart =
  | { text: string }
  | { base64Image: string }
  | { imageUrl: string }
  | { base64Video: string };

interface IChatMessage {
  role: "user" | "model";
  parts: ChatPart[];
  confidence?: number;
  sources?: { filename: string; page: string; chunk_id: number }[];
}

interface ISession {
  id: string;
  title: string;
  updated_at: string;
}
```

---

## State Management

No Redux or Context API. All state is `useState` inside components, with `useChat` centralizing message logic. `App` is the single source of truth for `activeSession` and passes it down via props.

---

## Styling

- **Tailwind CSS 4.x** via `@tailwindcss/vite` plugin
- **Dark theme** applied globally via `.dark` class on `<html>`
- **Color palette:** zinc grays (`bg-zinc-900/800/700`), violet accents (`bg-violet-600`), green/yellow/red for confidence scores
- **Scrollbars:** customized via `tailwind-scrollbar` plugin

---

## Key Design Decisions

- `fetch` is used for all HTTP calls — `axios` is installed but unused
- `useImperativeHandle` + `forwardRef` on `SessionList` lets `App` update the sidebar title without re-fetching all sessions
- History sent to the backend is the full current `messages` array; the backend trims to the last 6 messages internally
- The `useDocs` toggle is only active when `hasDocs === true`
