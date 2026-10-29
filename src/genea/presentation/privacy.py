from __future__ import annotations

import sqlite3
from dataclasses import dataclass


RESTRICTED_CLASSES = frozenset(
    {"living", "restricted", "family_private", "strict_private"}
)


@dataclass(frozen=True)
class PrivacyContext:
    labels: dict[tuple[str, str], str]
    protected_references: frozenset[str]

    @classmethod
    def load(cls, connection: sqlite3.Connection) -> "PrivacyContext":
        labels = {
                (row["object_type"], row["object_id"]): row["classification"]
                for row in connection.execute(
                    "SELECT object_type,object_id,classification FROM privacy_labels"
                )
            }
        restricted_people = {
            object_id
            for (object_type, object_id), classification in labels.items()
            if object_type == "person" and classification in RESTRICTED_CLASSES
        }
        protected = {
            object_id
            for (_object_type, object_id), classification in labels.items()
            if classification in RESTRICTED_CLASSES
        }
        if restricted_people:
            placeholders = ",".join("?" for _ in restricted_people)
            protected.update(
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM person_occurrences WHERE person_id IN ({placeholders})",
                    tuple(sorted(restricted_people)),
                )
            )
        return cls(labels, frozenset(protected))

    def blocked(self, object_type: str, object_id: str | None) -> bool:
        return bool(
            object_id
            and self.labels.get((object_type, object_id)) in RESTRICTED_CLASSES
        )

    def restricted_ids(self, object_type: str) -> set[str]:
        return {
            object_id
            for (kind, object_id), classification in self.labels.items()
            if kind == object_type and classification in RESTRICTED_CLASSES
        }

    def blocked_reference(self, reference: str | None) -> bool:
        if not reference:
            return False
        value = str(reference).strip()
        candidates = {value}
        if ":" in value:
            prefix, object_id = value.split(":", 1)
            if prefix in {
                "person",
                "person_occurrence",
                "occurrence",
                "source_item",
                "event",
                "household",
            }:
                candidates.add(object_id)
        return bool(candidates & self.protected_references)

    @property
    def excluded_count(self) -> int:
        return sum(
            classification in RESTRICTED_CLASSES
            for classification in self.labels.values()
        )
