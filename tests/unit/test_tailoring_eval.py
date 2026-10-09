"""The offline tailoring claim eval."""

from evals.runners import tailoring


def test_tailoring_claim_gate_has_zero_false_accepts_or_rejects() -> None:
    """The claim gate accepts and rejects every case correctly."""
    summary, rows, failures = tailoring.run()
    assert failures == []
    assert summary == {"uncited_claims_accepted": 0, "supported_claims_rejected": 0}
    assert len(rows) == 5
