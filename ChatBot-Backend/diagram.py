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
# rendering hint. Shared by both the single-call and split-call paths.
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
    "entity":       {"mermaid": "erDiagram", "direction": "",
                     "hint": "entities and their relationships, with cardinality."},
}

_PLAN_PROPERTIES = {
    "needs_diagram": {
        "type": "boolean",
        "description": (
            "Whether a diagram should accompany this answer. Default to TRUE whenever the answer explains a FLOW, a "
            "PROCESS, a DESIGN, an ARCHITECTURE, how COMPONENTS INTERACT, a hierarchy, an ordered sequence, or an "
            "entity relationship — for any of these a picture helps, so draw one. Only choose FALSE for a single "
            "definition, one fact, a short clarification, an opinion, or a plain list whose items don't relate to "
            "each other."
        ),
    },
    "visual_kind": {
        "type": "string",
        "enum": list(_VISUAL_KINDS.keys()),
        "description": (
            "How best to visualise it: 'flow' for an ordered process/data flow; 'architecture' for how a system's "
            "components interact; 'hierarchy' for parent/child structure; 'sequence' for ordered interactions over "
            "time; 'entity' for entities and their relationships."
        ),
    },
    "direction": {
        "type": "string",
        "enum": ["TD", "TB", "LR", ""],
        "description": (
            "Layout direction for flowchart kinds: 'TD'/'TB' (top-down, good for steps/hierarchies) or 'LR' "
            "(left-to-right, good for wide pipelines). Empty for sequence/entity diagrams."
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


# Line prefixes that are only valid in a Mermaid sequenceDiagram — if a model
# leaks them into a flowchart, Mermaid.js fails to render the whole diagram.
_SEQUENCE_ONLY_PREFIXES = ("note ", "activate ", "deactivate ", "participant ", "loop ", "alt ", "opt ", "par ")


def _sanitize(mermaid: str) -> str:
    """Drop lines that are invalid for the diagram's declared type.

    Local models sometimes mix sequenceDiagram directives (e.g. ``note right of``)
    into a flowchart, which makes Mermaid.js reject the entire diagram. For
    flowchart/graph diagrams we strip those stray lines so the rest still renders.
    """
    if not mermaid:
        return mermaid
    first = mermaid.lstrip().split(maxsplit=1)[0].lower()
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return mermaid  # only flowcharts suffer the sequence-syntax leak
    kept = []
    for line in mermaid.splitlines():
        if line.strip().lower().startswith(_SEQUENCE_ONLY_PREFIXES):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _finalize(raw: str, caption: str, kind: str) -> dict | None:
    """Clean, sanitize, and package a rendered Mermaid string."""
    mermaid = _sanitize(helpers.clean_mermaid(raw))
    if not mermaid or len(mermaid.splitlines()) < 2:
        logger.info("[Diagram] Rendered Mermaid was invalid/empty; skipping")
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
    return _finalize(args.get("mermaid", ""), args.get("caption") or "", args.get("visual_kind") or "?")


def _generate_local(question: str, answer: str, example_domain: str | None) -> dict | None:
    """Two-step path for local models: plan, then render as free text."""
    plan = llm.call_tool(
        [
            {"role": "system", "content": (
                DIAGRAM_PERSONA + "\n\nFirst, decide whether a diagram helps and classify the BEST way to "
                "visualise this answer (kind and direction). Do not draw yet."
            )},
            {"role": "user", "content": _user_payload(question, answer)},
        ],
        PLAN_TOOL, max_tokens=160, temperature=0.1,
    )
    if not plan.get("needs_diagram"):
        return None

    kind = plan.get("visual_kind") if plan.get("visual_kind") in _VISUAL_KINDS else "flow"
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
