"""Research tools for causally visible horizontal resistance."""

__all__ = ["BoundaryCandidateConfig", "build_boundary_review_candidates"]


def __getattr__(name: str):
    if name in __all__:
        from anomaly_science.strategy.visible_resistance import candidates

        return getattr(candidates, name)
    raise AttributeError(name)
