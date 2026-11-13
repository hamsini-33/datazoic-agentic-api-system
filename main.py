import argparse
import json
import sys
import uuid
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from src.config import settings
from src.registry.db import ToolRegistryDB
from src.orchestrator.graph import ScalableAgentOrchestrator
from src.orchestrator.trace import redact_text, redact_trace_value

console = Console()


def ask_for_sensitive_approval() -> bool:
    """Return True only after the user explicitly types yes."""
    decision = Prompt.ask(
        "[bold yellow]Do you authorize execution?[/bold yellow]",
        choices=["yes", "no"],
    )
    return decision.lower() == "yes"


def submit_approval_decision(orchestrator, user_request, state, decision):
    """Resume graph execution with a decision for its stored pending action."""
    return orchestrator.run(
        user_request,
        state=state,
        approval_decision=decision,
    )


def submit_clarification_response(orchestrator, user_request, state, response):
    """Resume only the saved incomplete action with one user clarification."""
    return orchestrator.run(
        user_request,
        state=state,
        clarification_response=response,
    )

PRESET_PROMPTS = {
    "1": "Send an invoice for $50 to bob@example.com for Web Design services",
    "2": "What was my total sales volume last month?",
    "3": "Is there a dispute open from user_123?",
    "4": "What is the dispute response timeframe according to PayPal policy?",
    "5": "What tools are available for managing invoices?",
    "6": "What was the status of my last request?"
}

def display_welcome(db: ToolRegistryDB):
    total_tools = db.count_tools()
    console.print(Panel.fit(
        f"[bold cyan]Scalable Agentic System[/bold cyan] - [yellow]Intelligent API Tool Discovery & Execution[/yellow]\n"
        f"[dim]LangGraph Orchestrator · ChromaDB & SQLite Registry · Deterministic Safe Execution[/dim]\n\n"
        f"• [green]Active API Tools Registered:[/green] {total_tools} PayPal REST APIs\n"
        f"• [green]Dual ChromaDB Collections:[/green] 'api_tools' & 'knowledge_docs'\n"
        f"• [green]Safe Approval Gates:[/green] {'Enabled' if settings.ENABLE_APPROVAL_GATES else 'Disabled'}",
        title="🤖 Antigravity Agent CLI",
        border_style="cyan"
    ))

    console.print("\n[bold]Quick Demo Shortcuts (or type any freeform question):[/bold]")
    console.print("  [cyan][1][/cyan] Scenario 1: Send an invoice for $50 to bob@example.com")
    console.print("  [cyan][2][/cyan] Scenario 2: What was my total sales volume last month?")
    console.print("  [cyan][3][/cyan] Scenario 3: Is there a dispute open from user_123?")
    console.print("  [cyan][4][/cyan] Scenario 4: Dispute response timeframe (Knowledge RAG)")
    console.print("  [cyan][5][/cyan] Scenario 5: Available invoice tools (System Search: Capabilities)")
    console.print("  [cyan][6][/cyan] Scenario 6: Status of my last request (System Search: Audit Logs)")
    console.print("  [dim]Type 'history' to inspect the audit log table, or 'exit' to quit.[/dim]\n")

def display_audit_history(db: ToolRegistryDB):
    logs = db.get_recent_logs(limit=8)
    if not logs:
        console.print("[yellow]No execution logs found in audit store.[/yellow]")
        return

    table = Table(title="Recent System Execution Audit Traces", border_style="dim")
    table.add_column("Step ID", style="dim", no_wrap=True)
    table.add_column("Trace ID", style="dim", no_wrap=True)
    table.add_column("Timestamp", style="dim")
    table.add_column("User Request", style="cyan")
    table.add_column("Selected Tool", style="green")
    table.add_column("Approval", style="yellow")
    table.add_column("HTTP", justify="center")
    table.add_column("Latency", justify="right")

    for log in logs:
        status_color = "green" if log["http_status"] in [200, 201] else "yellow" if log["http_status"] == 202 else "red"
        table.add_row(
            (log.get("execution_id") or "legacy")[:12],
            log["trace_id"][:12],
            log["timestamp"].split("T")[1][:8],
            log["user_request"][:30] + "..." if len(log["user_request"]) > 30 else log["user_request"],
            log["selected_tool"] or "N/A",
            log["approval_status"],
            f"[{status_color}]{log['http_status']}[/{status_color}]",
            f"{log['latency_ms']:.1f}ms"
        )
    console.print(table)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Datazoic agent in the terminal.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Display an in-memory, privacy-filtered per-step retrieval and execution trace.",
    )
    return parser


def display_verbose_trace(user_request, state, console_instance=console):
    """Display a privacy-filtered view of the current request's API steps."""
    console_instance.print(Panel(
        redact_text(user_request),
        title="Original Request (sensitive values redacted)",
        border_style="dim",
    ))
    steps = state.get("planning_trace", [])
    if not steps:
        console_instance.print("[dim]No API retrieval steps were recorded for this request.[/dim]")
        return

    table = Table(title="Per-Step API Retrieval Trace", border_style="cyan")
    for heading in ("Step", "Subgoal", "Retrieval Query", "Fresh Candidates (score)", "Passed to Planner", "Selected API", "Execution / HTTP"):
        table.add_column(heading, overflow="fold")

    for step in steps:
        retrieved = ", ".join(
            f"{item['name']} ({item['score']:.3f})" if isinstance(item.get("score"), (int, float))
            else str(item["name"])
            for item in step.get("retrieved_candidates", [])
        ) or "none"
        status = str(step.get("execution_status", "unknown"))
        if step.get("http_status") is not None:
            status += f" / HTTP {step['http_status']}"
        table.add_row(
            str(step.get("step_number", "?")),
            redact_text(str(step.get("subgoal", ""))),
            redact_text(str(step.get("retrieval_query", ""))),
            f"Fresh retrieval: {redact_text(retrieved)}",
            ", ".join(redact_text(str(name)) for name in step.get("planner_candidates", [])) or "none",
            redact_text(str(step.get("selected_api") or "none")),
            redact_text(status),
        )
    console_instance.print(table)

    for step in steps:
        detail_lines = []
        for label, key in (
            ("Parameters", "parameters"),
            ("Prior outputs available to this step", "prior_results"),
            ("Step output", "output"),
        ):
            value = redact_trace_value(step.get(key))
            if value not in (None, {}, []):
                detail_lines.append(f"{label}: {json.dumps(value, ensure_ascii=False, default=str)}")
        if step.get("error"):
            detail_lines.append(f"Error: {redact_text(str(step['error']))}")
        if detail_lines:
            console_instance.print(Panel("\n".join(detail_lines), title=f"Step {step.get('step_number')} data", border_style="dim"))

def main():
    args = build_argument_parser().parse_args()
    db = ToolRegistryDB(settings.DATABASE_PATH)
    orchestrator = ScalableAgentOrchestrator(db=db)
    display_welcome(db)

    while True:
        try:
            user_input = Prompt.ask("[bold green]User[/bold green]").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit", "q"]:
                console.print("[dim]Exiting Agent Session. Goodbye![/dim]")
                break

            if user_input.lower() == "history":
                display_audit_history(db)
                continue

            # Check if user selected a shortcut
            if user_input in PRESET_PROMPTS:
                user_input = PRESET_PROMPTS[user_input]
                console.print(f"[dim]Executing preset: '{user_input}'[/dim]")

            # Run Orchestrator Turn 1
            with console.status("[bold cyan]Agent reasoning, discovering tools & planning...[/bold cyan]"):
                state = orchestrator.run(user_input)

            # Check if Sensitive Operation is awaiting Human Approval
            while state.get("pending_approval") or state.get("pending_clarification"):
                if state.get("pending_clarification"):
                    clarification = state["pending_clarification"]
                    console.print(Panel(
                        clarification["prompt"],
                        title="Additional information needed",
                        border_style="yellow",
                    ))
                    response = Prompt.ask("[bold green]Clarification[/bold green]").strip()
                    with console.status("[bold cyan]Resuming the incomplete action...[/bold cyan]"):
                        state = submit_clarification_response(
                            orchestrator,
                            state.get("user_request", user_input),
                            state,
                            response,
                        )
                    continue

                approval = state["pending_approval"]
                console.print(Panel(
                    f"[bold yellow]⚠️ SENSITIVE FINANCIAL OPERATION[/bold yellow]\n\n"
                    f"Tool: [bold]{approval['tool_name']}[/bold]\n"
                    f"Workflow step: [bold]{approval['workflow_step_id']}[/bold]\n"
                    f"Parameters: {approval['arguments']}\n\n"
                    f"[yellow]{approval['prompt']}[/yellow]",
                    title="🛡️ Human-in-the-Loop Approval Gate",
                    border_style="yellow"
                ))

                decision = ask_for_sensitive_approval()
                with console.status("[bold green]Resolving approval decision...[/bold green]"):
                    state = submit_approval_decision(
                        orchestrator,
                        state.get("user_request", user_input),
                        state,
                        decision,
                    )

            if args.verbose:
                display_verbose_trace(user_input, state)

            # Display Final Formatted Response
            console.print(Panel(
                state.get("final_response", "Operation completed."),
                title="🤖 Agent Response",
                border_style="green"
            ))
            console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]Session terminated.[/dim]")
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {str(e)}")

if __name__ == "__main__":
    main()
