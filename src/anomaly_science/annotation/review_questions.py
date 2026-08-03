"""Typed, strategy-declared categorical questions for manual review."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_QUESTION_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ReviewOption:
    value: str
    label: str

    def __post_init__(self) -> None:
        if not _QUESTION_ID.fullmatch(self.value):
            raise ValueError(f"invalid review option value: {self.value!r}")
        if not self.label.strip():
            raise ValueError("review option label is required")

    def as_payload(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label}


@dataclass(frozen=True, slots=True)
class ReviewQuestion:
    question_id: str
    label: str
    options: tuple[ReviewOption, ...]
    required: bool = True

    def __post_init__(self) -> None:
        if not _QUESTION_ID.fullmatch(self.question_id):
            raise ValueError(f"invalid review question id: {self.question_id!r}")
        if not self.label.strip():
            raise ValueError("review question label is required")
        if len(self.options) < 2:
            raise ValueError("a review question requires at least two options")
        values = tuple(option.value for option in self.options)
        if len(set(values)) != len(values):
            raise ValueError(f"duplicate option in review question {self.question_id!r}")

    def as_payload(self) -> dict[str, object]:
        return {
            "id": self.question_id,
            "label": self.label,
            "required": self.required,
            "options": [option.as_payload() for option in self.options],
        }


def validate_review_answers(payload: dict[str, Any], questions: tuple[ReviewQuestion, ...]) -> None:
    """Validate the strategy-specific answer codebook at the server boundary."""

    answers = payload.get("review_answers")
    if not questions:
        if answers is not None and not isinstance(answers, dict):
            raise ValueError("review_answers must be an object")
        return
    if not isinstance(answers, dict):
        raise ValueError("review_answers must be an object")

    known = {question.question_id: question for question in questions}
    unknown = sorted(set(map(str, answers)) - set(known))
    if unknown:
        raise ValueError(f"unknown review answers: {unknown}")
    missing = sorted(
        question.question_id
        for question in questions
        if question.required and not str(answers.get(question.question_id) or "")
    )
    if missing:
        raise ValueError(f"required review answers missing: {missing}")
    for question_id, raw_value in answers.items():
        value = str(raw_value)
        allowed = {option.value for option in known[str(question_id)].options}
        if value not in allowed:
            raise ValueError(f"invalid answer {value!r} for review question {question_id!r}")


def questions_payload(questions: tuple[ReviewQuestion, ...]) -> list[dict[str, object]]:
    return [question.as_payload() for question in questions]
