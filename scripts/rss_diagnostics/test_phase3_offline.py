"""No-network verification for phase-three audit and privacy boundaries."""
import importlib.util
import json
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
        "mode": "shadow", "status": "ok", "candidate_count": 15,
        "candidate_cap": 200, "skipped_candidate_cap": 0,
        "agreement": ["private-candidate"], "ai_recommended": ["private-candidate"],
        "traditional_selected": ["private-candidate"],
    }
    result = phase.summarize_production(raw * 3, ai, 123)
    assert result["source_count"] == 3
    assert "sources" not in result and result["per_source_rows_exported"] is False
    assert result["totals"]["rss_returned"] == 30
    assert result["quality_unattempted"] == 9
    assert result["candidate_count_matches_source_totals"] is True
    assert "Private Name" not in str(result)
    assert "private.invalid" not in str(result)
    assert "private-candidate" not in str(result)
    assert "token" not in str(result)


def test_audit_does_not_infer_production_cap_effect_from_public_feed():
    sample = phase.audit(
        {"checked_at": "2026-11-23T19:00:00Z", "sources": []},
        [{"source_url": "https://secret.invalid/", "rss_returned": 2,
          "candidates": 2, "quota_limited": 2}] * 3,
        {"candidate_count": 6, "candidate_cap": 200,
         "skipped_candidate_cap": 0, "ai_recommended": [],
         "traditional_selected": [], "agreement": []},
        22,
    )
    assert sample["production"]["quality_unattempted"] == 6
    assert sample["production"]["ai"]["skipped_candidate_cap"] == 0
    assert sample["evidence"]["semantic_and_cross_language_overlap"] == "unverified"
    assert sample["evidence"]["public_snapshot_count"] == 1
    assert sample["evidence"]["event_level_manual_verification"] == "unverified"
    assert "secret.invalid" not in str(sample)


def test_missing_source_counters_remain_unknown():
    result = phase.summarize_production(
        [{"rss_returned": 4, "candidates": None}],
        {"candidate_count": 4, "ai_recommended": []}, 99,
    )
    assert result["totals"]["candidates"] is None
    assert result["candidate_count_matches_source_totals"] is None
    assert result["totals"]["failed"] is None


def test_distinct_source_fingerprints_are_never_exported():
    rows = [
        {"rss_returned": 11, "historical_dedup": 8, "candidates": 3},
        {"rss_returned": 47, "historical_dedup": 10, "candidates": 37},
        {"rss_returned": 2, "historical_dedup": 1, "candidates": 1},
    ]
    report = phase.summarize_production(rows, {"candidate_count": 41}, 1)
    assert report["source_count"] == 3
    assert report["totals"]["rss_returned"] == 60
    assert "sources" not in report and "F01" not in str(report) and "F02" not in str(report)


def test_summary_without_item_digests_is_unverified_not_zero():
    rows = [{"name": name, "status": "ok"} for name in ("BBC World", "BBC News — Africa")]
    result = next(row for row in phase.compare_exact(rows) if row["main"] == "BBC World")
    assert result["status"] == "unverified" and "exact_either" not in result


def test_missing_ai_counts_and_small_source_groups_are_not_reported_as_zero():
    for rows in ([], [{"rss_returned": 7}], [{"rss_returned": 7}] * 2):
        result = phase.summarize_production(rows, {}, 1)
        assert result["totals"]["rss_returned"] is None
        assert result["ai"]["ai_recommended_count"] is None
        assert result["ai"]["agreement_count"] is None
    assert phase.summarize_production([{}] * 3, {"agreement": []}, 1)["ai"]["agreement_count"] == 0


def test_committed_public_snapshot_agrees_with_available_first_snapshot_comparisons():
    data = Path(__file__).resolve().parents[2] / "docs/data"
    first = json.loads((data / "rss_probe_2026-11-24.json").read_text())
    second = json.loads((data / "rss_snapshot_2026-11-24_0902.json").read_text())
    phase3 = json.loads((data / "rss_phase3_redacted_2026-11-24.json").read_text())
    assert all("items" not in row for snapshot in (first, second) for row in snapshot["sources"])
    compared = {(r["main"], r["branch"]): r for r in phase3["public_comparisons"]}
    for row in first["comparisons"]:
        if "exact_url_or_title_overlap" in row:
            assert compared[(row["main"], row["branch"])]["exact_either"] == row["exact_url_or_title_overlap"]
    production = phase3["production"]
    assert "sources" not in production and production["per_source_rows_exported"] is False
    assert production["totals"]["candidates"] == production["ai"]["candidate_count"]
