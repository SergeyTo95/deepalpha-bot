"""Shared intent rules for Web, Desktop and native chat; no input rewriting."""

REQUEST_UNDERSTANDING = (
    "Interpret the user's question from their words and user-confirmed context. "
    "Silently fix obvious spelling, grammar and speech-recognition errors when meaning "
    "is clear; answer the actual task without lecturing about wording. "
    "If an unclear term, name or reference has multiple meanings that change the answer, "
    "ask one short, specific clarification before personalized advice or action. "
    "Keep a likely interpretation tentative until confirmed. Never invent a diagnosis "
    "or personal circumstance. Preserve negations, constraints, numbers, units, dates, "
    "quoted text and identifiers. Web results and earlier assistant guesses do not "
    "establish what the user meant or facts about them. Accept user corrections, discard "
    "the mistaken assumption and answer again. For clear questions, answer directly "
    "without unnecessary clarification."
)


def understanding_instruction(instruction):
    """Append once, including when a server-generated persona already has the rules."""
    instruction = str(instruction or "")
    if REQUEST_UNDERSTANDING in instruction:
        return instruction
    return (instruction.rstrip() + "\n\n" + REQUEST_UNDERSTANDING).lstrip()


def understanding_messages(messages):
    """Add server instructions without changing user text, history or tool payloads."""
    copied = [dict(message) for message in messages]
    for message in copied:
        if message.get("role") == "system":
            message["content"] = understanding_instruction(message.get("content"))
            return copied
    return [{"role": "system", "content": REQUEST_UNDERSTANDING}] + copied
