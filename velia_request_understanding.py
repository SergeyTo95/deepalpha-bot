"""Shared intent rules for Web, Desktop and native chat; no input rewriting."""

REQUEST_UNDERSTANDING = (
    "Понимай намерение по словам пользователя и подтверждённому им контексту. "
    "Явные опечатки, грамматику и ошибки диктовки исправляй по смыслу молча: "
    "исправленный термин должен быть близок по написанию или звучанию и подходить "
    "контексту. На понятный вопрос отвечай прямо, без лишних уточнений. "
    "Если существенное слово, название или ссылка на контекст остаётся непонятным "
    "и меняет ответ, весь ответ должен состоять ТОЛЬКО из одного короткого "
    "уточняющего вопроса с точной цитатой непонятного фрагмента. Дождись ответа. "
    "Не добавляй приветствие, объяснение, советы или перечень возможных расшифровок. "
    "Не предлагай выдуманные диагнозы, названия и обстоятельства даже со словами "
    "«скорее всего». Не пропускай непонятную существенную часть запроса. "
    "Страницы из поиска и прежние догадки ассистента не подтверждают смысл слов "
    "пользователя и факты о нём. Учитывай его исправления и отбрасывай ошибочную "
    "догадку. Сохраняй отрицания, ограничения, числа, единицы, даты, цитаты и "
    "идентификаторы. При редактировании сохраняй автора, род и лицо исходного "
    "текста, если пользователь не просит их изменить: «рада» остаётся «рада», "
    "а «рад» остаётся «рад». Пример явной опечатки: «перезагрузиь роутор» — "
    "дай шаги перезагрузки роутера. Пример неоднозначности: на «Мне нужен ключ» "
    "спроси «Для какой задачи вам нужен ключ?» и остановись."
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
