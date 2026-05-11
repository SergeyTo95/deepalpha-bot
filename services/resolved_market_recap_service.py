from typing import Final

_RU_TEMPLATE: Final[str] = """⏳ Пока ты думаешь — рынки закрываются

Событие завершилось:
{market_title}

✅ Результат: {resolved_outcome}

На таких рынках решение нужно принимать не после результата, а до того, как рынок всё переоценит.

DeepAlpha помогает заранее разобрать:
— вероятность исхода;
— ключевые драйверы;
— риски;
— возможный edge против цены рынка.

После завершения остаётся только смотреть результат.
До завершения — можно было принять решение.

Проверь следующий рынок заранее 👇
{bot_username}"""

_EN_TEMPLATE: Final[str] = """⏳ While you wait, markets close

A market has resolved:
{market_title}

✅ Result: {resolved_outcome}

In markets like this, the decision has to be made before the result is obvious — not after the market has already repriced.

DeepAlpha helps you check in advance:
— outcome probability;
— key market drivers;
— risks;
— possible edge versus market price.

After resolution, you can only watch the result.
Before resolution, you can still make a decision.

Check the next market before it closes 👇
{bot_username}"""


def render_resolved_market_recap(
    lang: str,
    market_title: str,
    resolved_outcome: str,
    bot_username: str = "@DeepAlphaAI_bot",
) -> str:
    template = _RU_TEMPLATE if lang == "ru" else _EN_TEMPLATE
    return template.format(
        market_title=market_title.strip(),
        resolved_outcome=resolved_outcome.strip(),
        bot_username=bot_username.strip(),
    )
