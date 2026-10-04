"""Shared intent rules for Web, Desktop and native chat; no input rewriting."""

REQUEST_UNDERSTANDING = (
    "Interpret the user's question from their words and user-confirmed context. "
    "Silently fix clear spelling, grammar and speech-recognition errors: a corrected "
    "term must be close in spelling or sound and fit the context. "
    "Answer the actual task without lecturing about wording. "
    "If an unclear term, name or reference has multiple meanings that change the answer, "
    "quote the unclear fragment and return ONLY one short, specific clarification. "
    "Stop there and wait for confirmation; do not continue with advice under a guess. "
    "If you cannot account for a significant part of the message, clarify rather than "
    "omit that part. "
    "Keep a likely interpretation tentative until confirmed. Never invent a diagnosis "
    "or personal circumstance. Preserve negations, constraints, numbers, units, dates, "
    "quoted text and identifiers. Web results and earlier assistant guesses do not "
    "establish what the user meant or facts about them. Accept user corrections, discard "
    "the mistaken assumption and answer again. For clear questions, answer directly "
    "without unnecessary clarification. "
    "Examples: 'Как перезагрузиь роутор?' means reboot a router; give the steps. "
    "'Мне нужен мак' without context needs a question about a computer or a plant. "
    "A garbled disease, device or coin name needs a question quoting that name, not "
    "a new diagnosis, product or exchange rate from search results."
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
