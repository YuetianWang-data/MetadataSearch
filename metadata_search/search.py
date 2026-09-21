"""Match each field row and rank by explicit business rules, not word frequency."""

from dataclasses import dataclass
from math import ceil

from .loader import FieldRecord
from .query import DIMENSIONS, LABELS, Query, QueryParser, Settings, identifier_tokens, normalize


IDENTIFIERS = ("table_name", "field_name")
ENGLISH_MATCH_TYPES = {3: "Exact identifier", 2: "Identifier tokens", 1: "Cross-identifier tokens"}


@dataclass(frozen=True)
class PreparedRecord:
    record: FieldRecord
    text: dict[str, str]
    identifiers: dict[str, frozenset[str]]


class SearchEngine:
    def __init__(self, records: list[FieldRecord], settings: Settings):
        self.settings = settings
        self.parser = QueryParser(settings)
        # Prepare searchable copies once; never overwrite the original values.
        self.records = [
            PreparedRecord(
                record,
                {key: normalize(getattr(record, key)) for key in (*DIMENSIONS, *IDENTIFIERS)},
                {key: frozenset(identifier_tokens(getattr(record, key))) for key in IDENTIFIERS},
            )
            for record in records
        ]

    def _chinese_match(self, prepared: PreparedRecord, query: Query):
        if query.mode == "mixed":
            exact = [key for key in DIMENSIONS
                     if all(part in prepared.text[key] for part in query.chinese_parts)]
        else:
            exact = [key for key in DIMENSIONS if query.normalized in prepared.text[key]]
        # A dimension contributes once: a full hit cannot also earn subword points.
        partial = [key for key in DIMENSIONS if key not in exact
                   and any(word in prepared.text[key] for word in query.subwords)]
        weights = self.settings.weights
        # Tuple ordering guarantees that counts beat weights and full hits beat partials.
        rank = (
            len(exact), sum(weights[key] for key in exact),
            len(partial), sum(weights[key] for key in partial),
        )
        evidence = []
        for key in exact:
            terms = query.chinese_parts if query.mode == "mixed" else (query.normalized,)
            evidence.append({"dimension": key, "label": LABELS[key], "kind": "Full query",
                             "terms": list(terms)})
        for key in partial:
            evidence.append({"dimension": key, "label": LABELS[key], "kind": "Subword",
                             "terms": [word for word in query.subwords if word in prepared.text[key]]})
        return rank, evidence

    def _english_match(self, prepared: PreparedRecord, query: Query):
        tokens = set(identifier_tokens(query.original)) if query.mode == "english" else {
            token for part in query.english_parts for token in identifier_tokens(part)
        }
        matched = []
        exact = []
        phrase = query.normalized if query.mode == "english" else normalize(" ".join(query.english_parts))
        for key in IDENTIFIERS:
            if prepared.text[key] == phrase:
                exact.append(key)
            if tokens and tokens <= prepared.identifiers[key]:
                matched.append(key)
        # Multiword queries may span the table and field identifiers of this row.
        available = prepared.identifiers["table_name"] | prepared.identifiers["field_name"]
        if not tokens or not tokens <= available:
            return (0, 0, 0), []
        grade = 3 if exact else 2 if matched else 1
        dimensions = exact or matched or [
            key for key in IDENTIFIERS if tokens & prepared.identifiers[key]
        ]
        evidence = [
            {"dimension": key, "label": LABELS[key],
             "kind": "Exact identifier" if key in exact else "Identifier tokens",
             "terms": [phrase] if key in exact else sorted(tokens & prepared.identifiers[key])}
            for key in dimensions
        ]
        return (grade, len(exact), len(matched)), evidence

    def _match(self, prepared: PreparedRecord, query: Query):
        if query.mode == "chinese":
            return self._chinese_match(prepared, query)
        if query.mode == "english":
            return self._english_match(prepared, query)

        # Mixed queries must satisfy both languages within the same source row.
        chinese_rank, chinese_evidence = self._chinese_match(prepared, query)
        english_rank, english_evidence = self._english_match(prepared, query)
        if not chinese_evidence or not english_evidence:
            return (), []
        return chinese_rank + english_rank, chinese_evidence + english_evidence

    def search(self, text: str, page: int = 1, page_size: int = 20) -> dict:
        """Return one page of ranked original rows with their matching evidence."""
        if type(page) is not int or page < 1:
            raise ValueError("page must be a positive integer")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size must be an integer between 1 and 100")
        query = self.parser.parse(text)
        hits = []
        for prepared in self.records:
            rank, evidence = self._match(prepared, query)
            if evidence:
                hits.append((rank, prepared.record, evidence))
        # Python's stable sort preserves original file/row order when all scores tie.
        hits.sort(key=lambda hit: hit[0], reverse=True)
        total = len(hits)
        pages = max(1, ceil(total / page_size))
        page = min(page, pages)
        start = (page - 1) * page_size
        items = []
        for rank, record, evidence in hits[start:start + page_size]:
            if query.mode == "english":
                match_type = ENGLISH_MATCH_TYPES[rank[0]]
            else:
                match_type = "Full query" if rank[0] else "Subword"
            items.append({
                **record.to_dict(),
                "rank": list(rank),
                "matches": evidence,
                "match_type": match_type,
            })
        return {
            "query": query.original, "mode": query.mode, "subwords": list(query.subwords),
            "weights": self.settings.weights, "total": total, "page": page,
            "page_size": page_size, "pages": pages, "items": items,
        }
