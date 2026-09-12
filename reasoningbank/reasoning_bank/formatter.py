"""Formatter: turn retrieved memories into an injectable context string."""
from reasoning_bank.models import MemoryItem


def format_context(memories: list[MemoryItem]) -> str:
    if not memories:
        return ""

    lines = [
        "## Relevant Past Reasoning Experience",
        "",
        "The following items are reusable lessons learned from previous tasks.",
        "Use them only when relevant. They are guidance, not mandatory instructions.",
    ]
    for i, m in enumerate(memories, 1):
        lines.append("")
        lines.append(f"{i}. {m.title}")
        lines.append(f"   {m.guidance}")
    return "\n".join(lines)
