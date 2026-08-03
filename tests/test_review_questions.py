from __future__ import annotations

import pytest

from anomaly_science.annotation.review_questions import ReviewOption, ReviewQuestion, validate_review_answers


QUESTIONS = (
    ReviewQuestion(
        question_id="episode_state",
        label="Episode state",
        options=(ReviewOption("clean", "clean"), ReviewOption("noisy", "noisy")),
    ),
)


def test_required_review_question_accepts_only_declared_values() -> None:
    validate_review_answers({"review_answers": {"episode_state": "clean"}}, QUESTIONS)

    with pytest.raises(ValueError, match="required review answers missing"):
        validate_review_answers({"review_answers": {}}, QUESTIONS)
    with pytest.raises(ValueError, match="invalid answer"):
        validate_review_answers({"review_answers": {"episode_state": "invented"}}, QUESTIONS)
    with pytest.raises(ValueError, match="unknown review answers"):
        validate_review_answers(
            {"review_answers": {"episode_state": "clean", "future_outcome": "win"}},
            QUESTIONS,
        )


def test_review_question_identifiers_are_machine_stable() -> None:
    with pytest.raises(ValueError, match="invalid review question id"):
        ReviewQuestion(
            question_id="Episode state",
            label="Episode state",
            options=(ReviewOption("clean", "clean"), ReviewOption("noisy", "noisy")),
        )
