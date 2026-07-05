# react-ai-tool (Notebook frontend)

React + TypeScript single-page app for Notebook. Built with Vite and Tailwind
CSS, talking to the Flask backend over native `fetch` (and SSE for streaming).

See the root [README.md](../../README.md) and [DESIGN.md](../../DESIGN.md) for
the whole picture.

---

## Running locally

```bash
npm install
npm run dev       # dev server at http://localhost:5173
npm run build     # TypeScript check + production bundle → dist/
npm run lint      # ESLint
npm run preview   # preview the production build
```

## Environment

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | backend API base URL |

`.env.development` is pre-set to `http://127.0.0.1:5000`; update
`.env.production` before deploying.

---

## Component tree

```
App
├── ChatHeader
├── SessionList     sidebar — list/select/delete sessions
├── FileUpload      sidebar — session-scoped sources
├── AddSourceMenu   "+" menu to add a file/URL (also in the input bar)
├── ChatPanel       message stream + live thought trace
│   └── MermaidDiagram   renders a message's diagram when present
└── [input bar + submit]
```

## Key pieces

| File | Responsibility |
|------|----------------|
| `App.tsx` | root component; owns the active session and wires the panels |
| `hooks/useChat.ts` | message state + the SSE consumer for `/api/chat_stream` |
| `components/ChatPanel.tsx` | renders messages, the collapsible thought trace, and follow-up chips |
| `components/MermaidDiagram.tsx` | renders Mermaid diagrams with zoom |
| `components/SessionList.tsx` | session sidebar |
| `components/FileUpload.tsx` / `AddSourceMenu.tsx` | adding sources (file or URL) |

## Talking to the backend

`useChat` opens `POST /api/chat_stream` and reads Server-Sent Events:

- `step` — appended to the live thought trace,
- `sources_added` — refreshes the source list,
- `final` — the finished message (answer, sources, follow-ups, diagram),
- `error` — surfaces a failure.

Sessions are created lazily and messages reload from `GET /api/sessions/<id>`,
so a conversation survives a refresh.
