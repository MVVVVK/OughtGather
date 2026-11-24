"""No-network verification for phase-three audit and privacy boundaries."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "phase3_offline", Path(__file__).with_name("phase3_offline.py")
)
phase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phase)


def item(url, title, recent=True):
    return {"url_id": url, "title_id": title, "within_7_days": recent}


def test_exact_overlap_not_mistaken_for_semantic_independence():
    rows = [
        {"name": "BBC World", "status": "ok", "items": [
            item("u1", "t1"), item("u2", "t2"), item("u3", "t3", False)]},
        {"name": "BBC News — Africa", "status": "ok", "items": [
            item("u1", "renamed"), item("other", "t2"), item("unmatched", "new"),
            item("old", "old", False)]},
    ]
    result = next(x for x in phase.compare_exact(rows) if x["main"] == "BBC World")
    assert result["same_url_digest"] == 1
    assert result["same_title_digest"] == 1
    assert result["exact_either"] == 2
    assert result["branch_unmatched_unverified"] == 1
    assert result["independent_information_gain"] is None
    assert result["same_event_different_title"] is None


def test_no_evidence_yields_unverified_not_zero_overlap():
    result = phase.compare_exact([])[0]
    assert result["status"] == "unverified"
    assert "exact_either" not in result


def test_anonymization_and_source_count_reconciliation():
    raw = [{
        "source": "Private Name", "source_url": "https://private.invalid/secret?k=token",
        "rss_returned": 10, "historical_dedup": 5, "candidates": 5,
        "text_candidates": 3, "media_candidates": 2,
        "attempted": 2, "quota_limited": 3, "quality_passed": 2,
        "full_text_obtained": 1, "summary_obtained": 1, "selected": 2,
        "status": "success",
    }]
    ai = {
        "mode": "shadow", "status": "ok", "candidate_count": 5,
        "candidate_cap": 200, "skipped_candidate_cap": 0,
        "agreement": ["private-candidate"], "ai_recommended": ["private-candidate"],
        "traditional_selected": ["private-candidate"],
    }
    result = phase.summarize_production(raw, ai, 123)
    assert result["sources"][0]["source_id"] == "F01"
    assert result["totals"]["rss_returned"] == 10
    assert result["quality_unattempted"] == 3
    assert result["candidate_count_matches_source_totals"] is True
    assert "Private Name" not in str(result)
    assert "private.invalid" not in str(result)
    assert "private-candidate" not in str(result)
    assert "token" not in str(result)


def test_audit_does_not_infer_production_cap_effect_from_public_feed():
    sample = phase.audit(
        {"checked_at": "2026-11-23T19:00:00Z", "sources": []},
        [{"source_url": "https://secret.invalid/", "rss_returned": 2,
          "candidates": 2, "quota_limited": 2}],
        {"candidate_count": 2, "candidate_cap": 200,
         "skipped_candidate_cap": 0, "ai_recommended": [],
         "traditional_selected": [], "agreement": []},
        22,
    )
    assert sample["production"]["quality_unattempted"] == 2
    assert sample["production"]["ai"]["skipped_candidate_cap"] == 0
    assert sample["evidence"]["semantic_and_cross_language_overlap"] == "unverified"
    assert "secret.invalid" not in str(sample)
