"""Offline, read-only RSS/production audit. Never exports raw production URLs or IDs.

Usage:
  python scripts/rss_diagnostics/phase3_offline.py \
    --public rss-public-diagnostic.zip \
    --quality daily-gather-quality-report.zip \
    --ai daily-gather-ai-report.zip --run-id 45298550652 \
    --output phase3-redacted.json
ZIP inputs are existing GitHub Actions artifacts; no network or Secrets are used.
"""
import argparse
import json
import zipfile
from pathlib import Path


PAIRS = (
    ("Le Monde — Une", "Le Monde — International"),
    ("Le Monde — Une", "Le Monde — Justice"),
    ("Le Monde — Une", "Le Monde — Économie"),
    ("Le Monde — Une", "Le Monde — Les Décodeurs"),
    ("Le Monde — International", "Le Monde — Justice"),
    ("Le Monde — International", "Le Monde — Économie"),
    ("The Guardian World", "The Guardian Business"),
    ("The Guardian World", "The Guardian Technology"),
    ("The Guardian World", "The Guardian Science"),
    ("The Guardian World", "The Guardian — International Development"),
    ("France 24 English", "France 24 — Africa (EN)"),
    ("France 24 Français", "France 24 — Moyen-Orient"),
    ("France 24 English", "France 24 Français"),
    ("RFI Français", "RFI — Afrique"),
    ("BBC World", "BBC News — Africa"),
)


def load_json(path, member):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read(member))
    return json.loads(path.read_text(encoding="utf-8"))


def compare_exact(source_rows):
    """Count only demonstrable same-item URL/title digests; NOT semantic events."""
    rows = {row["name"]: row for row in source_rows}
    out = []
    for main, branch in PAIRS:
        a, b = rows.get(main), rows.get(branch)
        if not a or not b or a.get("status") != "ok" or b.get("status") != "ok":
            out.append({"main": main, "branch": branch, "status": "unverified"})
            continue
        aa = [item for item in a.get("items", []) if item.get("within_7_days")]
        bb = [item for item in b.get("items", []) if item.get("within_7_days")]
        url_ids = {item.get("url_id") for item in aa if item.get("url_id")}
        title_ids = {item.get("title_id") for item in aa if item.get("title_id")}
        url_n = sum(item.get("url_id") in url_ids for item in bb if item.get("url_id"))
        title_n = sum(item.get("title_id") in title_ids for item in bb if item.get("title_id"))
        exact_n = sum(bool((item.get("url_id") and item["url_id"] in url_ids)
                           or (item.get("title_id") and item["title_id"] in title_ids))
                      for item in bb)
        out.append({
            "main": main, "branch": branch, "status": "exact_only",
            "main_recent_7d": len(aa), "branch_recent_7d": len(bb),
            "same_url_digest": url_n, "same_title_digest": title_n,
            "exact_either": exact_n,
            "branch_unmatched_unverified": len(bb) - exact_n,
            "same_event_different_title": None,
            "independent_information_gain": None,
        })
    return out


FIELDS = (
    "rss_returned", "historical_dedup", "candidates", "text_candidates",
    "media_candidates", "attempted", "quality_passed", "quota_limited",
    "full_text_obtained", "rss_content_obtained", "summary_obtained",
    "failed", "selected",
)


def summarize_production(source_rows, ai_report, run_id):
    """Never copy source, source_url, titles, article URLs, candidate IDs or groups."""
    sources = []
    for index, raw in enumerate(source_rows, start=1):
        row = {"source_id": f"F{index:02d}"}
        for field in FIELDS:
            value = raw.get(field)
            # Missing counters are unknown, not evidence of zero activity.
            row[field] = value if type(value) is int and value >= 0 else None
        row["status"] = "success" if raw.get("status") == "success" else "other"
        sources.append(row)
    totals = {key: (sum(row[key] for row in sources)
                    if all(row[key] is not None for row in sources) else None)
              for key in FIELDS}
    ai_candidates = ai_report.get("candidate_count")
    agreement = ai_report.get("agreement") or []
    recommended = ai_report.get("ai_recommended") or []
    traditional = ai_report.get("traditional_selected") or []
    cap = ai_report.get("candidate_cap")
    return {
        "run_id": str(run_id), "source_labels": "local to this run; not stable identities",
        "sources": sources, "totals": totals,
        "ai": {
            "mode": ai_report.get("mode"), "status": ai_report.get("status"),
            "candidate_count": ai_candidates, "candidate_cap": cap,
            "skipped_candidate_cap": ai_report.get("skipped_candidate_cap"),
            "topic_count_after_audit": ai_report.get("topic_count_after_audit"),
            "ai_recommended_count": len(recommended),
            "traditional_selected_count": len(traditional),
            "agreement_count": len(agreement),
        },
        "candidate_count_matches_source_totals": (ai_candidates == totals["candidates"]
                                                   if ai_candidates is not None and totals["candidates"] is not None else None),
        "quality_unattempted": totals["quota_limited"],
        "quota_unattempted_is_not_quality_failure": True,
        "production_source_urls_exported": False,
    }


def audit(public_snapshot, quality_sources, ai_report, run_id):
    return {
        "evidence": {
            "public_snapshot_time": public_snapshot.get("checked_at"),
            "public_snapshot_count": 1,
            "event_level_manual_verification": "unverified",
            "multiple_day_trend": "unverified",
            "semantic_and_cross_language_overlap": "unverified",
        },
        "public_comparisons": compare_exact(public_snapshot.get("sources", [])),
        "production": summarize_production(quality_sources, ai_report, run_id),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", required=True)
    parser.add_argument("--quality", required=True)
    parser.add_argument("--ai", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    obj = audit(
        load_json(args.public, "rss-diagnostic.json"),
        load_json(args.quality, "quality-sources.json"),
        load_json(args.ai, "ai-editor-report.json"),
        args.run_id,
    )
    # JSON contains only aggregate fields and public publisher labels.
    Path(args.output).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
