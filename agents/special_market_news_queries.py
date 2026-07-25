from typing import Callable, List


def is_social_post_count_market(question: str) -> bool:
    text = str(question or "").lower()
    has_platform = "truth social" in text or "truthsocial" in text
    has_count = any(token in text for token in (" post", "posts", "posting", "tweet", "tweets"))
    has_range = any(token in text for token in (" from ", " to ", "between", "120-139", "count"))
    return has_platform and has_count and has_range


def build_social_post_count_queries(question: str) -> List[str]:
    text = str(question or "")
    person = "Donald Trump" if "trump" in text.lower() else ""
    prefix = person or "Truth Social account"
    return [
        f"{prefix} Truth Social posts today",
        f"{prefix} Truth Social posting frequency",
        f"{prefix} Truth Social post count tracker",
        f"{prefix} Truth Social latest posts",
        f"{prefix} Truth Social activity archive",
        f"{prefix} Truth Social posts per day",
    ]


def wrap_targeted_news_queries(original: Callable[..., List[str]]) -> Callable[..., List[str]]:
    def enhanced(
        category_type: str,
        subcategory: str,
        entities: List[str],
        market_type: str,
        question: str,
        deadline: str = "",
    ) -> List[str]:
        base = original(
            category_type,
            subcategory,
            entities,
            market_type,
            question,
            deadline,
        ) or []
        if not is_social_post_count_market(question):
            return base

        merged: List[str] = []
        for query in build_social_post_count_queries(question) + list(base):
            clean = " ".join(str(query or "").split()).strip()
            if clean and clean not in merged:
                merged.append(clean)
        return merged[:7]

    return enhanced
