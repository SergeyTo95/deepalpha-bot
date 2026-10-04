"""Offline lexical candidates; no rewrite, diagnosis, search or extra model call."""
from functools import lru_cache
import re

from wordfreq import top_n_list
from velia_request_understanding import _edit_distance, LITERAL_PATTERN


_PROTECTED = LITERAL_PATTERN
_WORDS = re.compile(r"(?<![\w./:@-])(?:[а-яёА-ЯЁ]{4,24}|[a-zA-Z]{4,24})(?![\w./:@-])")
_VOWELS = str.maketrans({letter: "_" for letter in "аеёиоуыэюяaeiouy"})


@lru_cache(maxsize=2)
def _lexicon(language):
    # The packaged data covers topics broadly; no per-topic/custom word list.
    return {word: rank for rank, word in enumerate(top_n_list(language, 500000))
        if re.fullmatch(r"[а-яё]+" if language == "ru" else r"[a-z]+", word)}


@lru_cache(maxsize=256)
def _candidates(word, language):
    lexicon = _lexicon(language)
    if word in lexicon:
        return ()
    folded = word.translate(_VOWELS)
    found = {value for value in lexicon if len(value) == len(word) and value.translate(_VOWELS) == folded}
    alphabet = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" if language == "ru" else "abcdefghijklmnopqrstuvwxyz"
    for index in range(len(word) + 1):
        left, right = word[:index], word[index:]
        edits = [left + right[1:]] if right else []
        if len(right) > 1:
            edits.append(left + right[1] + right[0] + right[2:])
        edits.extend(left + letter + right for letter in alphabet)
        if right:
            edits.extend(left + letter + right[1:] for letter in alphabet)
        found.update(value for value in edits if value in lexicon)
    # Sound-alike candidates come before merely frequent unrelated words.
    found = [value for value in found if _edit_distance(word, value) <= max(1, int(min(len(word), len(value)) * 0.4))]
    return tuple(sorted(found, key=lambda value: (value.translate(_VOWELS) != folded,
        _edit_distance(word, value), lexicon[value], value))[:6])


def spelling_hints(question):
    """Offer bounded choices for unknown words, ignoring literals/identifiers.

    Returned words are spelling possibilities, not asserted user information.
    Source and offsets belong to the original input; raw input stays intact.
    """
    masked = _PROTECTED.sub(lambda match: " " * len(match.group()), question)
    hints, seen = [], set()
    for match in _WORDS.finditer(masked):
        raw = match.group()
        word = raw.casefold()
        # Uppercase/camelCase tokens are commonly identifiers or acronyms.
        if raw.isupper() or (any(letter.isupper() for letter in raw[1:])) or word in seen:
            continue
        seen.add(word)
        language = "ru" if re.fullmatch(r"[а-яё]+", word) else "en"
        candidates = _candidates(word, language)
        if candidates:
            hints.append({"word": raw, "candidates": list(candidates)})
            if len(hints) == 6:
                break
    return hints


def phonetic_restoration(fragment):
    """Fill an empty confirmation candidate only for unique sound-alike words.

    This never changes a direct/search decision. A result is still a proposal
    requiring the user's confirmation, not an automatically established fact.
    """
    if not re.fullmatch(r"[^\W\d_]+(?:[ ,\-]+[^\W\d_]+)*", fragment.strip()):
        return None
    tokens = re.findall(r"[^\W\d_]+", fragment.casefold())
    if not 1 <= len(tokens) <= 4:
        return None
    restored, changed = [], 0
    for word in tokens:
        language = "ru" if re.fullmatch(r"[а-яё]+", word) else "en"
        if word in _lexicon(language):
            restored.append(word)
            continue
        if not 4 <= len(word) <= 24:
            return None
        nearby = [candidate for candidate in _candidates(word, language)
            if candidate.translate(_VOWELS) == word.translate(_VOWELS)]
        if len(nearby) != 1:
            return None
        restored.append(nearby[0])
        changed += 1
    # Individually restored unknown terms are proposed as a list; a space
    # alone would falsely make them look like a single unfamiliar term.
    separator = ", " if len(restored) > 1 and changed == len(restored) else " "
    return separator.join(restored) if changed else None
