"""The diagram skill.

A dedicated, model-agnostic capability for turning an explanation into a *good*
diagram — not just "boxes and arrows". It is driven by a diagramming persona
(``diagram_soul.md``) and adapts its steps to the model in play:

- **Cloud (strong) models** do it in **one step**: decide, classify, and render
  the Mermaid in a single structured call.
- **Local (weaker) models** split it into **two steps**: first *plan* (decide +
  classify the best visual), then *render* the Mermaid as free text — which is
  far more reliable than asking a small model to embed multi-line Mermaid inside
  a constrained JSON field.

Either way it goes through the shared ``llm`` facade, so the same skill serves
both Ollama and Azure.
"""

import logging
import os
import re

import helpers
import llm
import providers

logger = logging.getLogger(__name__)


def _load_persona() -> str:
    path = os.path.join(os.path.dirname(__file__), "diagram_soul.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        logger.warning("[Diagram] Could not load diagram_soul.md (%s); using fallback", exc)
        return (
            "You are a diagramming expert. Draw a clear Mermaid diagram only when the answer is about how things "
            "relate, connect, or flow. Choose the diagram type and direction that fit; capture real flow order and "
            "label arrows meaningfully. Output only Mermaid source."
        )


DIAGRAM_PERSONA = _load_persona()


# Visual kinds → the Mermaid diagram type and a default direction, plus a short
# rendering hint. The planner picks the kind that best FITS the explanation, and
# each kind renders as the most appropriate Mermaid diagram type.
_VISUAL_KINDS = {
    "flow":         {"mermaid": "flowchart", "direction": "TD",
                     "hint": "a process or data flow — order the nodes by what happens first, next, last, and label "
                             "arrows with the action (writes, reads, routes)."},
    "architecture": {"mermaid": "flowchart", "direction": "TB",
                     "hint": "a system architecture — group by layer/role and show interactions with labelled "
                             "arrows, not mere containment."},
    "hierarchy":    {"mermaid": "flowchart", "direction": "TD",
                     "hint": "a hierarchy — parent at the top, children beneath, one level per rank."},
    "sequence":     {"mermaid": "sequenceDiagram", "direction": "",
                     "hint": "an interaction over time — participants across the top, ordered messages down the page."},
    "class":        {"mermaid": "classDiagram", "direction": "",
                     "hint": "classes/objects and their structure — show classes with key attributes and methods, and "
                             "the relationships between them (inheritance, composition, association)."},
    "state":        {"mermaid": "stateDiagram-v2", "direction": "",
                     "hint": "a lifecycle or state machine — the states something moves through and the transitions "
                             "(with the event that triggers each) between them."},
    "entity":       {"mermaid": "erDiagram", "direction": "",
                     "hint": "entities and their relationships, with cardinality."},
    "mindmap":      {"mermaid": "mindmap", "direction": "",
                     "hint": "a concept broken into sub-concepts — a central idea with branches for its parts."},
}

# Kinds that local models render reliably. Local models frequently produce
# invalid syntax for the stricter types (classDiagram/stateDiagram/erDiagram), so
# for local we remap those to a flowchart — which still conveys the structure and
# renders correctly. Cloud models handle the full set well.
_LOCAL_SAFE_KINDS = {"flow", "architecture", "hierarchy", "sequence"}


def _coerce_kind(kind: str, is_local: bool) -> str:
    """Pick a renderable visual kind for the active model."""
    if kind not in _VISUAL_KINDS:
        return "flow"
    if is_local and kind not in _LOCAL_SAFE_KINDS:
        return "architecture"  # a flowchart of the entities still shows structure
    return kind


_PLAN_PROPERTIES = {
    "needs_diagram": {
        "type": "boolean",
        "description": (
            "Whether a Mermaid diagram would help. Use a MEDIUM bias tied to complexity: choose TRUE when the answer "
            "explains something genuinely complex or hard to follow in words alone — a multi-step flow or pipeline, a "
            "system architecture with several interacting parts, a class/object model, a state machine, or an entity "
            "relationship. Choose FALSE for a simple or short answer: a definition, a single fact, a brief "
            "explanation, a clarification, an opinion, or a plain list. In short: complex/structural → draw; "
            "simple → don't."
        ),
    },
    "visual_kind": {
        "type": "string",
        "enum": list(_VISUAL_KINDS.keys()),
        "description": (
            "Pick the diagram type that best FITS what's being explained: 'flow' for an ordered process/data flow; "
            "'architecture' for how a system's components interact; 'hierarchy' for parent/child structure; "
            "'sequence' for ordered interactions over time; 'class' for classes/objects, their attributes and "
            "relationships; 'state' for a lifecycle or state machine; 'entity' for entities and relationships; "
            "'mindmap' for breaking a concept into sub-concepts."
        ),
    },
    "direction": {
        "type": "string",
        "enum": ["TD", "TB", "LR", ""],
        "description": (
            "Layout direction for flowchart kinds: 'TD'/'TB' (top-down, good for steps/hierarchies) or 'LR' "
            "(left-to-right, good for wide pipelines). Empty for non-flowchart kinds."
        ),
    },
    "caption": {"type": "string", "description": "A short caption for the diagram (empty if none)."},
}

# Planning only (used by the local, split path).
PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_diagram",
        "description": "Decide whether a diagram helps and, if so, how best to visualise the explanation.",
        "parameters": {"type": "object", "properties": _PLAN_PROPERTIES, "required": ["needs_diagram"]},
    },
}

# Plan + render in one call (used by the cloud, single-step path).
DIAGRAM_TOOL = {
    "type": "function",
    "function": {
        "name": "make_diagram",
        "description": "Decide whether a diagram helps and, if so, produce the best Mermaid diagram for it.",
        "parameters": {
            "type": "object",
            "properties": {
                **_PLAN_PROPERTIES,
                "mermaid": {
                    "type": "string",
                    "description": (
                        "Valid Mermaid source starting with the diagram type (e.g. 'flowchart TD'). Reflect the real "
                        "flow order; label arrows meaningfully; concise node labels; no parentheses/special chars in "
                        "node text. Empty when needs_diagram is false."
                    ),
                },
            },
            "required": ["needs_diagram"],
        },
    },
}


def _example_hint(example_domain: str | None) -> str:
    if not example_domain:
        return ""
    return (
        f" A running example is in play this session: \"{example_domain}\". You may label nodes using the example's "
        "concrete terms so the picture matches the explanation."
    )


def _render_guidance(kind: str, direction: str, example_domain: str | None) -> str:
    """Kind-specific instruction for the free-text Mermaid rendering step (local path)."""
    spec = _VISUAL_KINDS.get(kind, _VISUAL_KINDS["flow"])
    header = spec["mermaid"]
    if header == "flowchart":
        header = f"flowchart {direction or spec['direction']}"
    return (
        DIAGRAM_PERSONA
        + f"\n\nFor THIS answer, draw {spec['hint']}\nBegin the output with `{header}`."
        + _example_hint(example_domain)
    )


def _user_payload(question: str, answer: str) -> str:
    return f"Question:\n{question}\n\nAnswer:\n{answer[:2500]}"


# ─── Semantic colour palette ──────────────────────────────────────────────────
# Designer-chosen, muted palette that harmonises with the app's warm paper theme.
# The model tags each flowchart node with a ROLE (:::process, :::decision, …) and
# we own the actual colours here — so the same role is always the same colour and
# different roles are visually distinct, consistently across every diagram.
#
#   process     soft blue     — an action / processing step (the default "doing")
#   decision    warm amber    — a condition / branch point ("pause and choose")
#   terminator  soft green    — a start or end point
#   store       soft lavender — data at rest (a store, topic, table, cache)
#   external    warm clay     — an external actor/system (producer, consumer, user)
#   highlight   soft rose     — the key thing to notice
_ROLE_NAMES = ("process", "decision", "terminator", "store", "external", "highlight")
_FLOWCHART_CLASSDEFS = "\n".join([
    "classDef process fill:#DCE6F0,stroke:#6E93B0,color:#22303C;",
    "classDef decision fill:#F5E6C5,stroke:#C7A35A,color:#4A3B1A;",
    "classDef terminator fill:#D9EBD6,stroke:#7FA877,color:#26361F;",
    "classDef store fill:#E4DCEF,stroke:#9B85C0,color:#2F2440;",
    "classDef external fill:#F1DAD0,stroke:#C58B72,color:#4A2E22;",
    "classDef highlight fill:#F3D6D6,stroke:#C77E7E,color:#4A2323;",
])


def _apply_palette(mermaid: str) -> str:
    """Append our semantic class definitions to flowcharts so any role the model
    tagged (``:::process`` etc.) renders in the app's consistent palette."""
    if not mermaid:
        return mermaid
    first = mermaid.lstrip().split(maxsplit=1)[0].lower()
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return mermaid  # other diagram types are themed on the frontend
    return mermaid.rstrip() + "\n" + _FLOWCHART_CLASSDEFS


# Line prefixes that are only valid in a Mermaid sequenceDiagram — if a model
# leaks them into a flowchart, Mermaid.js fails to render the whole diagram.
_SEQUENCE_ONLY_PREFIXES = ("note ", "activate ", "deactivate ", "participant ", "loop ", "alt ", "opt ", "par ")


def _sanitize(mermaid: str) -> str:
    """Make model-generated Mermaid safe to render, keeping semantic roles.

    - Local models sometimes mix sequenceDiagram directives (e.g. ``note right of``)
      into a flowchart, which makes Mermaid.js reject the entire diagram — for
      flowchart/graph diagrams we drop those stray lines.
    - Models emit malformed ``style NODE:::role`` lines (mixing ``style`` with class
      syntax). We normalise these to a valid ``class NODE role`` statement so the
      role's palette colour still applies.
    - Models also emit ad-hoc colour styling (``style X fill:#hex``, ``classDef ...``);
      we drop it so the app's curated palette governs colour instead.
    """
    if not mermaid:
        return mermaid
    first = mermaid.lstrip().split(maxsplit=1)[0].lower()
    is_flowchart = first.startswith("flowchart") or first.startswith("graph")
    role_alt = "|".join(_ROLE_NAMES)
    # Matches a malformed "style NODE:::role" (optionally with trailing color junk).
    style_role_re = re.compile(rf"^\s*style\s+(\w+):::({role_alt})\b", re.IGNORECASE)

    kept = []
    for line in mermaid.splitlines():
        stripped = line.strip()
        low = stripped.lower()

        # Normalise malformed "style NODE:::role" → "class NODE role".
        m = style_role_re.match(stripped)
        if m:
            kept.append(f"    class {m.group(1)} {m.group(2).lower()}")
            continue

        # Drop explicit colour styling — the palette is owned by the app.
        if low.startswith(("style ", "classdef ")) and (
            "fill:" in low or "stroke:" in low or "color:" in low
        ):
            continue

        # In flowcharts, drop sequenceDiagram-only directives that break rendering.
        if is_flowchart and low.startswith(_SEQUENCE_ONLY_PREFIXES):
            continue

        kept.append(line)
    return "\n".join(kept).strip()


# Patterns that indicate malformed Mermaid a model sometimes emits and that would
# make the whole diagram fail to render. If any is present we drop the diagram
# rather than show a broken one.
_INVALID_PATTERNS = (
    re.compile(r"-->\+"),          # e.g. "Producer -->+ Event"
    re.compile(r"\bo--\s+\w+\s+\w"),  # e.g. "Consumer o-- X Event"
    re.compile(r"\+\+\w"),          # e.g. "++void configure(...)"
    re.compile(r":::\w+:::"),       # two role tags on one node (e.g. "A:::x:::y")
)


def _looks_renderable(mermaid: str) -> bool:
    """Cheap structural check to reject Mermaid that clearly won't parse.

    We can't run the browser Mermaid engine server-side, but we can catch the
    specific malformed constructs local models tend to produce for the stricter
    diagram types, and drop those instead of rendering a broken diagram.
    """
    for pat in _INVALID_PATTERNS:
        if pat.search(mermaid):
            logger.info("[Diagram] Rejecting malformed Mermaid (%s)", pat.pattern)
            return False
    return True


def _finalize(raw: str, caption: str, kind: str) -> dict | None:
    """Clean, sanitize, apply the palette, validate, and package a Mermaid string."""
    mermaid = _apply_palette(_sanitize(helpers.clean_mermaid(raw)))
    if not mermaid or len(mermaid.splitlines()) < 2:
        logger.info("[Diagram] Rendered Mermaid was invalid/empty; skipping")
        return None
    if not _looks_renderable(mermaid):
        return None
    logger.info("[Diagram] Generated a '%s' diagram (%s…)", kind, mermaid.splitlines()[0][:40])
    return {"mermaid": mermaid, "caption": (caption or "").strip()}


def _generate_cloud(question: str, answer: str, example_domain: str | None) -> dict | None:
    """Single-step path for strong models: decide + render in one structured call."""
    system = DIAGRAM_PERSONA + _example_hint(example_domain)
    args = llm.call_tool(
        [{"role": "system", "content": system}, {"role": "user", "content": _user_payload(question, answer)}],
        DIAGRAM_TOOL, max_tokens=700, temperature=0.2,
    )
    if not args.get("needs_diagram"):
        return None
    kind = _coerce_kind(args.get("visual_kind") or "flow", is_local=False)
    return _finalize(args.get("mermaid", ""), args.get("caption") or "", kind)


def _generate_local(question: str, answer: str, example_domain: str | None) -> dict | None:
    """Two-step path for local models: plan, then render as free text."""
    plan = llm.call_tool(
        [
            {"role": "system", "content": (
                DIAGRAM_PERSONA + "\n\nFirst, decide whether a diagram helps and classify the BEST way to "
                "visualise this answer (kind and direction). Do not draw yet.\n"
                "Be selective: if the question is simple or the answer is short (a definition, a single fact, a "
                "one- or two-sentence explanation), set needs_diagram=false. Only choose true when the answer "
                "explains something genuinely complex — a multi-step flow, an architecture, or how several parts "
                "interact."
            )},
            {"role": "user", "content": _user_payload(question, answer)},
        ],
        PLAN_TOOL, max_tokens=160, temperature=0.1,
    )
    if not plan.get("needs_diagram"):
        return None

    kind = _coerce_kind(plan.get("visual_kind") or "flow", is_local=True)
    raw = llm.call_text(
        [
            {"role": "system", "content": _render_guidance(kind, plan.get("direction") or "", example_domain)},
            {"role": "user", "content": _user_payload(question, answer)},
        ],
        max_tokens=600, temperature=0.2,
    )
    return _finalize(raw, plan.get("caption") or "", kind)


def generate(question: str, answer: str, example_domain: str = None) -> dict | None:
    """Return ``{mermaid, caption}`` when a diagram aids understanding, else ``None``.

    Chooses the single-step (cloud) or split (local) path based on the active model.
    """
    try:
        if providers.active_chat_is_local():
            return _generate_local(question, answer, example_domain)
        return _generate_cloud(question, answer, example_domain)
    except Exception as exc:
        logger.warning("[Diagram] Generation failed: %s", exc)
        return None
