"""Normalize queries and apply automatic or explicitly configured subwords."""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import unicodedata

import jieba


DIMENSIONS = ("system_name", "table_comment", "field_comment", "sample_data")
LABELS = {
    "system_name": "System name",
    "table_comment": "Table comment",
    "field_comment": "Field comment",
    "sample_data": "Sample data",
    "table_name": "Table name",
    "field_name": "Field name",
}
HAN = r"\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ffff"
HAN_RUN = re.compile(f"[{HAN}]+")
LATIN_RUN = re.compile(r"[a-zA-Z0-9_]+")


def normalize(text: str) -> str:
    """Normalize search text only; retain original text for result display."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def identifier_tokens(text: str) -> tuple[str, ...]:
    """Split snake_case, camelCase and acronym boundaries into whole tokens."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))


@dataclass(frozen=True)
class Settings:
    """Validated configuration; reject typos instead of silently changing ranking."""

    weights: dict[str, int]
    auto_subwords: bool
    subword_overrides: dict[str, tuple[str, ...]]
    excluded_subwords: frozenset[str]
    custom_words: tuple[str, ...]

    @classmethod
    def load(cls, path: Path):
        with path.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("The configuration must be a JSON object")
        allowed = {"weights", "auto_subwords", "subword_overrides", "excluded_subwords", "custom_words"}
        if data.keys() - allowed:
            raise ValueError(f"Unknown configuration keys: {sorted(data.keys() - allowed)}")
        weights = data.get("weights", {})
        if not isinstance(weights, dict) or set(weights) != set(DIMENSIONS):
            raise ValueError(f"weights must contain exactly these four dimensions: {', '.join(DIMENSIONS)}")
        if any(type(value) is not int or value <= 0 for value in weights.values()):
            raise ValueError("Weights must be positive integers")
        if not (weights["sample_data"] > weights["system_name"]
                > weights["table_comment"] > weights["field_comment"]):
            raise ValueError("Weights must satisfy: sample_data > system_name > table_comment > field_comment")
        auto = data.get("auto_subwords", True)
        if type(auto) is not bool:
            raise ValueError("auto_subwords must be true or false")

        def words(value, label):
            if not isinstance(value, list) or any(not isinstance(word, str) or not normalize(word)
                                                  for word in value):
                raise ValueError(f"{label} must be a list of non-empty strings (an empty list is allowed)")
            return tuple(dict.fromkeys(normalize(word) for word in value))

        overrides = data.get("subword_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("subword_overrides must map queries to lists of subwords")
        normalized_overrides = {}
        for query, candidates in overrides.items():
            key = normalize(query)
            if not key:
                raise ValueError("Subword override queries must not be empty")
            terms = words(candidates, f"subword_overrides.{query}")
            if any(len(term) < 2 or term == key or term not in key
                   or not HAN_RUN.fullmatch(term) for term in terms):
                raise ValueError(
                    f"{query}: Each subword must be a Chinese substring of at least two characters "
                    "and must not equal the full query"
                )
            if key in normalized_overrides:
                raise ValueError(f"Duplicate query override after normalization: {query}")
            normalized_overrides[key] = terms
        return cls(
            dict(weights), auto, normalized_overrides,
            frozenset(words(data.get("excluded_subwords", []), "excluded_subwords")),
            words(data.get("custom_words", []), "custom_words"),
        )


@dataclass(frozen=True)
class Query:
    original: str
    normalized: str
    mode: str
    chinese_parts: tuple[str, ...]
    english_parts: tuple[str, ...]
    subwords: tuple[str, ...]


class QueryParser:
    def __init__(self, settings: Settings):
        self.settings = settings
        jieba.setLogLevel(logging.WARNING)
        self.tokenizer = jieba.Tokenizer()
        self.tokenizer.initialize()
        for word in settings.custom_words:
            self.tokenizer.add_word(word)

    def parse(self, raw: str) -> Query:
        """Keep the full query separate from weaker Chinese subword matches."""
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Enter a non-empty search query")
        if len(raw) > 200:
            raise ValueError("The search query must not exceed 200 characters")
        full = normalize(raw)
        chinese = tuple(HAN_RUN.findall(full))
        english = tuple(LATIN_RUN.findall(unicodedata.normalize("NFKC", raw)))
        if not chinese and not english:
            raise ValueError("The search query must contain Chinese characters, English letters, or digits")
        mode = "mixed" if chinese and english else "chinese" if chinese else "english"
        subwords = []
        if mode != "english":
            # An explicit override replaces automatic splitting, including [].
            if full in self.settings.subword_overrides:
                subwords = list(self.settings.subword_overrides[full])
            elif self.settings.auto_subwords:
                for part in chinese:
                    subwords.extend(self.tokenizer.cut_for_search(part, HMM=False))
            # Exclude single characters and avoid scoring full terms again as subwords.
            subwords = sorted({
                word for word in subwords
                if HAN_RUN.fullmatch(word) and len(word) >= 2
                and word not in self.settings.excluded_subwords
                and word != full
                and (mode != "mixed" or word not in chinese)
            }, key=lambda word: (-len(word), word))
        return Query(raw.strip(), full, mode, chinese, english, tuple(subwords))
