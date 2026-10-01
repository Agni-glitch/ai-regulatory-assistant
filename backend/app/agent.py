"""
Agentic RAG agent.

A tool-calling loop on Azure GPT-4.1. The model decides which retrieval tools
to invoke (possibly several times / multi-hop), then synthesizes a grounded,
cited answer. Every passage returned by a tool is collected into a citation
pool that is returned alongside the answer so the UI can render sources.
"""

from __future__ import annotations

import json
import logging

from .azure_client import chat
from .config import get_settings
from .vectorstore import list_jurisdictions, search

logger = logging.getLogger("agent")

SYSTEM_PROMPT = """You are ComplyNexus, an expert assistant on AI regulation and \
governance across jurisdictions. You answer using ONLY the regulatory passages \
returned by your tools — never from prior knowledge or assumptions.

Workflow:
1. Use `list_jurisdictions` if you are unsure which jurisdictions exist.
2. Use `retrieve_regulations` to find relevant passages. Filter by jurisdiction \
when the user names a country/region; otherwise search across all.
3. Use `compare_jurisdictions` when the user asks to compare two or more.
4. You may call tools multiple times to gather enough evidence.

Answering rules:
- Ground every claim in retrieved passages. Cite sources inline as \
[file_name p.PAGE].
- If the passages do not contain the answer, say so plainly and suggest what \
to look for — do NOT fabricate.
- Be precise about which jurisdiction a rule applies to.
- Keep answers well-structured (short paragraphs or bullets).

JURISDICTION SCOPE RULES (highest priority — always enforced):
- The active jurisdiction scope is provided in a system message below.
- If scope is ALL_JURISDICTIONS:
    * Check whether the user's question mentions a specific country, region, or \
jurisdiction. If it does NOT, respond ONLY with: "Please specify a jurisdiction \
or country in your question (e.g. 'What does India require for AI systems?') \
so I can find the relevant regulations. You can also narrow the scope by \
selecting a jurisdiction from the sidebar."
    * Do NOT call any retrieval tools before asking.
- If scope is a specific jurisdiction (e.g. US, EUROPE, INDIA):
    * ONLY retrieve and answer about that jurisdiction.
    * If the user's question is about a DIFFERENT country or jurisdiction that \
does not match the active scope, do NOT call any tools. Respond ONLY with: \
"⚠️ Your question appears to be about [detected jurisdiction], but your current \
scope is set to **[active scope]**. Please select the correct jurisdiction from \
the sidebar and try again."
    * Never cross-search other jurisdictions.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_jurisdictions",
            "description": "List all jurisdictions/standards available in the "
                           "knowledge base with document counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_regulations",
            "description": "Semantic search over regulatory documents. Returns "
                           "the most relevant passages with citations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Focused search query.",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "description": "Optional. Restrict to one jurisdiction "
                                       "(e.g. 'US', 'EUROPE', 'INTERNATIONAL'). "
                                       "Omit or 'ALL' to search everywhere.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of passages (default 6).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_jurisdictions",
            "description": "Retrieve passages on a topic for several "
                           "jurisdictions at once for side-by-side comparison.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "jurisdictions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Two or more jurisdictions to compare.",
                    },
                },
                "required": ["query", "jurisdictions"],
            },
        },
    },
]


def _passage_to_citation(p: dict) -> dict:
    return {
        "file_name": p.get("file_name"),
        "jurisdiction": p.get("jurisdiction"),
        "page_number": p.get("page_number"),
        "source_path": p.get("source_path"),
        "score": p.get("score"),
        "snippet": (p.get("text") or "")[:280],
    }


def _run_tool(name: str, args: dict, citations: list[dict]) -> str:
    """Execute a tool call, accumulate citations, return a JSON string result."""
    if name == "list_jurisdictions":
        return json.dumps({"jurisdictions": list_jurisdictions()})

    if name == "retrieve_regulations":
        passages = search(
            query=args["query"],
            jurisdiction=args.get("jurisdiction"),
            k=args.get("k"),
        )
        citations.extend(_passage_to_citation(p) for p in passages)
        return json.dumps({"passages": passages})

    if name == "compare_jurisdictions":
        grouped: dict[str, list[dict]] = {}
        for j in args.get("jurisdictions", []):
            passages = search(query=args["query"], jurisdiction=j)
            citations.extend(_passage_to_citation(p) for p in passages)
            grouped[j] = passages
        return json.dumps({"comparison": grouped})

    return json.dumps({"error": f"Unknown tool: {name}"})


def _dedupe_citations(citations: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for c in citations:
        key = (c.get("file_name"), c.get("page_number"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def run_agent(question: str, history: list[dict] | None = None,
              jurisdiction: str | None = None) -> dict:
    """
    Run the agentic RAG loop.

    Returns {"answer": str, "citations": [...], "steps": int}.
    `history` is a list of prior {role, content} messages.
    `jurisdiction` is an optional UI-selected default scope hint.
    """
    s = get_settings()

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if jurisdiction and jurisdiction.upper() not in ("ALL", "ANY", ""):
        messages.append({
            "role": "system",
            "content": (
                f"ACTIVE SCOPE: {jurisdiction}. "
                f"You MUST only answer about '{jurisdiction}'. "
                f"If the user asks about any other country or jurisdiction, "
                f"do NOT retrieve anything — immediately tell them their question "
                f"is outside the current scope and ask them to change it in the sidebar."
            ),
        })
    else:
        messages.append({
            "role": "system",
            "content": (
                "ACTIVE SCOPE: ALL_JURISDICTIONS. "
                "If the user's question does not mention any specific country, "
                "region, or jurisdiction, do NOT call any retrieval tools. "
                "Instead, ask them to specify a jurisdiction in their question."
            ),
        })
    for m in history or []:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})

    citations: list[dict] = []

    for step in range(1, s.agent_max_steps + 1):
        response = chat(messages, tools=TOOLS)
        msg = response.choices[0].message

        if not msg.tool_calls:
            return {
                "answer": msg.content or "",
                "citations": _dedupe_citations(citations),
                "steps": step,
            }

        # Append the assistant turn that requested tools.
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        # Execute each requested tool and append results.
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = _run_tool(tc.function.name, args, citations)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Tool %s failed", tc.function.name)
                result = json.dumps({"error": str(exc)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # Step budget exhausted — ask for a final synthesis without more tools.
    messages.append({
        "role": "system",
        "content": "Tool budget exhausted. Answer now using gathered passages "
                   "and cite sources. If insufficient, say what is missing.",
    })
    final = chat(messages)
    return {
        "answer": final.choices[0].message.content or "",
        "citations": _dedupe_citations(citations),
        "steps": s.agent_max_steps,
    }
