import React from "react";
import mermaid from "mermaid";
import { Maximize2, X, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";

mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  securityLevel: "strict",
  fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
});

interface MermaidDiagramProps {
  chart: string;
  caption?: string;
}

/** Extract intrinsic width/height from a Mermaid SVG's viewBox. */
function parseViewBox(svg: string): { w: number; h: number } | null {
  const m = svg.match(/viewBox="([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)"/);
  if (!m) return null;
  const w = parseFloat(m[3]);
  const h = parseFloat(m[4]);
  return w > 0 && h > 0 ? { w, h } : null;
}

/** Large, zoomable/pannable modal view of a rendered diagram.
 *  100% = fit to ~80% of the screen; user can zoom to 400% with scroll/pan. */
const ZoomModal: React.FC<{ svg: string; caption?: string; onClose: () => void }> = ({ svg, caption, onClose }) => {
  const [userZoom, setUserZoom] = React.useState(1); // 1 = fit (shown as 100%)
  const [fit, setFit] = React.useState(1);
  const [grabbing, setGrabbing] = React.useState(false);
  const dims = React.useMemo(() => parseViewBox(svg), [svg]);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const drag = React.useRef({ active: false, x: 0, y: 0, left: 0, top: 0 });

  const onPanStart = (e: React.MouseEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    drag.current = { active: true, x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
    setGrabbing(true);
  };
  const onPanMove = (e: React.MouseEvent) => {
    if (!drag.current.active) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    el.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };
  const onPanEnd = () => {
    drag.current.active = false;
    setGrabbing(false);
  };

  React.useEffect(() => {
    const compute = () => {
      if (!dims) {
        setFit(1);
        return;
      }
      const availW = window.innerWidth * 0.9 - 48;         // 90vw modal minus body padding
      const availH = window.innerHeight * 0.9 - 53 - 48;   // minus header + body padding
      // Fit within the box preserving aspect, at ~90% so it breathes (~80% of screen).
      const f = Math.min(availW / dims.w, availH / dims.h) * 0.92;
      setFit(Math.max(0.1, Math.min(f, 8)));
    };
    compute();
    window.addEventListener("resize", compute);
    return () => window.removeEventListener("resize", compute);
  }, [dims]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "+" || e.key === "=") setUserZoom((z) => Math.min(z + 0.25, 6));
      if (e.key === "-") setUserZoom((z) => Math.max(z - 0.25, 0.5));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const applied = fit * userZoom;
  const width = dims ? Math.round(dims.w * applied) : undefined;
  const height = dims ? Math.round(dims.h * applied) : undefined;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 backdrop-blur-sm p-6"
      onClick={onClose}
    >
      <div
        className="flex flex-col w-[90vw] h-[90vh] rounded-2xl border border-border bg-card shadow-xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border shrink-0">
          <span className="text-sm font-medium text-foreground truncate">{caption || "Diagram"}</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setUserZoom((z) => Math.max(z - 0.25, 0.5))} title="Zoom out" className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
              <ZoomOut size={16} />
            </button>
            <span className="text-xs text-muted-foreground w-10 text-center tabular-nums">{Math.round(userZoom * 100)}%</span>
            <button onClick={() => setUserZoom((z) => Math.min(z + 0.25, 6))} title="Zoom in" className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
              <ZoomIn size={16} />
            </button>
            <button onClick={() => setUserZoom(1)} title="Reset to fit" className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
              <RotateCcw size={15} />
            </button>
            <button onClick={onClose} title="Close" className="ml-1 p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
              <X size={16} />
            </button>
          </div>
        </div>
        <div
          ref={scrollRef}
          onMouseDown={onPanStart}
          onMouseMove={onPanMove}
          onMouseUp={onPanEnd}
          onMouseLeave={onPanEnd}
          className={`flex-1 overflow-auto p-6 grid place-items-center select-none scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent ${grabbing ? "cursor-grabbing" : "cursor-grab"}`}
        >
          <div
            className="shrink-0 [&_svg]:!w-full [&_svg]:!h-full [&_svg]:!max-w-none [&_svg]:pointer-events-none"
            style={{ width, height }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      </div>
    </div>
  );
};

/** Renders a Mermaid diagram to SVG. Fails gracefully (renders nothing) on bad syntax. */
const MermaidDiagram: React.FC<MermaidDiagramProps> = ({ chart, caption }) => {
  const [svg, setSvg] = React.useState<string>("");
  const [failed, setFailed] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);
  const idRef = React.useRef(`mmd-${Math.random().toString(36).slice(2)}`);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        await mermaid.parse(chart);
        const { svg } = await mermaid.render(idRef.current, chart);
        if (active) {
          setSvg(svg);
          setFailed(false);
        }
      } catch {
        if (active) setFailed(true);
      }
    })();
    return () => {
      active = false;
    };
  }, [chart]);

  if (failed || !svg) return null;

  return (
    <>
      <figure className="group relative mt-3 rounded-xl border border-border bg-card p-3 overflow-x-auto">
        <button
          onClick={() => setExpanded(true)}
          title="Expand diagram"
          className="absolute top-2 right-2 z-10 p-1.5 rounded-md bg-card/80 border border-border text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground transition-opacity"
        >
          <Maximize2 size={14} />
        </button>
        <div
          className="mermaid-svg flex justify-center cursor-zoom-in [&_svg]:max-w-full [&_svg]:h-auto"
          onClick={() => setExpanded(true)}
          dangerouslySetInnerHTML={{ __html: svg }}
        />
        {caption && (
          <figcaption className="mt-2 text-center text-xs text-muted-foreground">{caption}</figcaption>
        )}
      </figure>
      {expanded && <ZoomModal svg={svg} caption={caption} onClose={() => setExpanded(false)} />}
    </>
  );
};

export default MermaidDiagram;
