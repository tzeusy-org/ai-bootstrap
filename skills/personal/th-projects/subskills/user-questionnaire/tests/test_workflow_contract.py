from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
PACKET_CONTRACT = (ROOT / "references" / "packet-contract.md").read_text(encoding="utf-8")
REVIEW_PROTOCOL = (ROOT / "references" / "adversarial-review.md").read_text(encoding="utf-8")
INTERACTION = (ROOT / "references" / "interaction-and-application.md").read_text(
    encoding="utf-8"
)
TEMPLATE = (ROOT / "assets" / "questionnaire-template.md").read_text(encoding="utf-8")


def test_REQ_owner_questionnaire_001_keeps_ordinary_judgment_autonomous():
    """Spec: REQ-owner-questionnaire-001."""
    assert "Admit only genuine owner gates" in SKILL
    assert "ordinary engineering judgment" in SKILL
    assert "decision-autonomy policy" in SKILL


def test_REQ_owner_questionnaire_002_requires_two_independent_verdicts():
    """Spec: REQ-owner-questionnaire-002."""
    assert "independent adversarial subagent" in SKILL
    assert "problem scope" in SKILL
    assert "recommendation" in SKILL
    assert "Scope verdict: Pass | Revise | Reject" in REVIEW_PROTOCOL
    assert "Recommendation verdict: Pass | Revise | Reject" in REVIEW_PROTOCOL


def test_REQ_owner_questionnaire_003_defines_resumable_concise_packets():
    """Spec: REQ-owner-questionnaire-003."""
    assert "git check-ignore --no-index --quiet" in SKILL
    assert "2-4 genuine choices" in PACKET_CONTRACT
    assert "one decision at a time" in SKILL
    assert "Never place secrets" in SKILL


def test_REQ_owner_questionnaire_004_records_exact_owner_decision_provenance():
    """Spec: REQ-owner-questionnaire-004."""
    for field in (
        "Actor/channel",
        "Recorded at",
        "Choice",
        "Owner edits",
        "Post-answer review",
        "Final authorization boundary",
        "Canonical destination",
        "Routing evidence",
    ):
        assert f"- {field}:" in TEMPLATE
    assert "materially edited option" in INTERACTION


def test_REQ_owner_questionnaire_005_routes_without_mutating_authority():
    """Spec: REQ-owner-questionnaire-005."""
    assert "questionnaire itself never mutates" in SKILL
    assert "No packet can release external/live action" in SKILL
    assert "canonical owning protocol" in INTERACTION
    corpus = "\n".join((SKILL, PACKET_CONTRACT, INTERACTION, TEMPLATE))
    for forbidden in (
        "apply their recorded signoffs",
        "actions released by this answer",
        "This answer authorizes",
        "unless individually authorized",
    ):
        assert forbidden not in corpus
    assert "packet releases no" in corpus
    assert "obtain any required\n  explicit authorization separately" in INTERACTION


def test_REQ_owner_questionnaire_006_package_requires_fail_closed_validation():
    """Spec: REQ-owner-questionnaire-006."""
    assert "--require-review-ready" in SKILL
    assert "validator is structural evidence" in SKILL
    assert "not semantic" in SKILL


def test_REQ_owner_questionnaire_007_walkthrough_ends_with_apply_offer():
    """Spec: REQ-owner-questionnaire-007."""
    assert "Close every walkthrough with the apply offer" in SKILL
    assert "Update tracker artifacts" in SKILL
    assert "## Apply offer at the end of the walkthrough" in INTERACTION
    assert "### Update tracker artifacts" in INTERACTION
    assert "Do not force-close an item assigned to the\n   owner" in INTERACTION
    assert "re-ask trigger" in INTERACTION
