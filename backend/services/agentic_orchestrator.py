import json
from typing import Any

from services.llm_client import ollama_chat, ollama_chat_json, ollama_chat_model
from services.mcp_runtime import MCP_TOOLS, invoke_mcp_tool


_AGENT_SYSTEM_PROMPTS: dict[str, str] = {
    "orchestrator": (
        "You are the JARVIS Orchestrator Agent. "
        "Distribute workload across specialist agents, avoid duplication, and optimize execution order. "
        "Return concise plans and aggregation notes."
    ),
    "assessment": (
        "You are the JARVIS Assessment Agent. "
        "Perform migration discovery, dependency/risk analysis, and actionable assessment recommendations."
    ),
    "migration": (
        "You are the JARVIS Migration Agent. "
        "Design cloud target architecture and IaC/CI-CD transformation steps for Azure-to-Google migration."
    ),
    "testing": (
        "You are the JARVIS Testing Agent. "
        "Generate realistic quality gates, test outcomes, and defect-oriented diagnostics."
    ),
    "pmo": (
        "You are the JARVIS PMO Agent. "
        "Provide executive-ready delivery governance, risk, budget and stakeholder actions."
    ),
    "integration": (
        "You are the JARVIS Integration Agent. "
        "Assess enterprise integration readiness and operational connectivity risks."
    ),
}


async def run_specialist_agent(
    *,
    agent: str,
    objective: str,
    context: dict[str, Any] | None = None,
    mcp_calls: list[dict[str, Any]] | None = None,
    max_words: int = 260,
) -> dict[str, Any]:
    chosen = (agent or "assessment").strip().lower()
    if chosen not in _AGENT_SYSTEM_PROMPTS:
        chosen = "assessment"

    mcp_results: list[dict[str, Any]] = []
    for call in (mcp_calls or []):
        tool = str(call.get("tool") or "").strip()
        if not tool:
            continue
        if tool not in MCP_TOOLS:
            mcp_results.append({"tool": tool, "error": "unsupported_tool"})
            continue
        try:
            result = await invoke_mcp_tool(tool, call.get("args") or {})
            mcp_results.append({"tool": tool, "result": result})
        except Exception as e:
            mcp_results.append({"tool": tool, "error": str(e)})

    prompt = {
        "objective": objective,
        "context": context or {},
        "mcp_results": mcp_results,
        "constraints": {
            "max_words": max_words,
            "must_be_actionable": True,
            "must_reference_mcp_evidence": True,
        },
    }

    messages = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPTS[chosen]},
        {
            "role": "user",
            "content": (
                "Generate a professional agent response. "
                "Do not invent unavailable data. "
                "Use MCP evidence provided.\n\n"
                + json.dumps(prompt)
            ),
        },
    ]

    text = ollama_chat(messages)
    return {
        "agent": chosen,
        "model": ollama_chat_model(),
        "reply": text,
        "mcp": mcp_results,
    }


async def run_specialist_agent_json(
    *,
    agent: str,
    objective: str,
    schema_hint: dict[str, Any],
    context: dict[str, Any] | None = None,
    mcp_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    chosen = (agent or "assessment").strip().lower()
    if chosen not in _AGENT_SYSTEM_PROMPTS:
        chosen = "assessment"

    mcp_results: list[dict[str, Any]] = []
    for call in (mcp_calls or []):
        tool = str(call.get("tool") or "").strip()
        if not tool:
            continue
        if tool not in MCP_TOOLS:
            mcp_results.append({"tool": tool, "error": "unsupported_tool"})
            continue
        try:
            result = await invoke_mcp_tool(tool, call.get("args") or {})
            mcp_results.append({"tool": tool, "result": result})
        except Exception as e:
            mcp_results.append({"tool": tool, "error": str(e)})

    payload = {
        "objective": objective,
        "context": context or {},
        "mcp_results": mcp_results,
        "schema_hint": schema_hint,
    }

    messages = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPTS[chosen]},
        {
            "role": "user",
            "content": (
                "Return only valid JSON object for the requested schema. "
                "No markdown, no explanations.\n\n" + json.dumps(payload)
            ),
        },
    ]

    parsed = ollama_chat_json(messages, num_predict=700)
    return {"agent": chosen, "model": ollama_chat_model(), "data": parsed, "mcp": mcp_results}


async def orchestrate_workload(
    *,
    objective: str,
    tasks: list[dict[str, Any]],
    shared_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_tasks = tasks or []

    plan_messages = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPTS["orchestrator"]},
        {
            "role": "user",
            "content": (
                "Return JSON with keys execution_order (array of indexes), rationale (string). "
                "Use all tasks exactly once.\n\n"
                + json.dumps(
                    {
                        "objective": objective,
                        "shared_context": shared_context or {},
                        "tasks": [
                            {
                                "index": i,
                                "agent": (t.get("agent") or "assessment"),
                                "objective": t.get("objective"),
                            }
                            for i, t in enumerate(safe_tasks)
                        ],
                    }
                )
            ),
        },
    ]

    execution_order = list(range(len(safe_tasks)))
    rationale = "default-sequential"
    try:
        plan = ollama_chat_json(plan_messages, num_predict=260)
        order = plan.get("execution_order")
        if isinstance(order, list):
            order_int = [int(x) for x in order if isinstance(x, int) or str(x).isdigit()]
            if sorted(order_int) == list(range(len(safe_tasks))):
                execution_order = order_int
        rationale = str(plan.get("rationale") or rationale)
    except Exception:
        pass

    outputs: list[dict[str, Any]] = []
    for idx in execution_order:
        task = safe_tasks[idx]
        result = await run_specialist_agent(
            agent=str(task.get("agent") or "assessment"),
            objective=str(task.get("objective") or objective),
            context={**(shared_context or {}), **(task.get("context") or {})},
            mcp_calls=task.get("mcp_calls") or [],
            max_words=int(task.get("max_words") or 220),
        )
        outputs.append({"task_index": idx, **result})

    aggregate_messages = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPTS["orchestrator"]},
        {
            "role": "user",
            "content": (
                "Aggregate specialist outputs into one concise response with no more than 260 words. "
                "Highlight decisions and next steps.\n\n"
                + json.dumps(
                    {
                        "objective": objective,
                        "shared_context": shared_context or {},
                        "outputs": outputs,
                    }
                )
            ),
        },
    ]
    summary = ollama_chat(aggregate_messages, num_predict=420)

    return {
        "objective": objective,
        "execution_order": execution_order,
        "rationale": rationale,
        "results": outputs,
        "summary": summary,
    }
