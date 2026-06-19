from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta

import pytest

import vietlott_analytics.predictions as predictions_module
from vietlott_analytics.catalog import PRODUCTS
from vietlott_analytics.io import Observation, ProductDataset
from vietlott_analytics.predictions import (
    PredictionLedger,
    _digit_uniform_expectation,
    build_backtest_report,
    finalize_backtests,
)


def _dataset(draws: int) -> ProductDataset:
    product = PRODUCTS["mega645"]
    start = date(2024, 1, 1)
    observations = [
        Observation(
            draw_id=str(index + 1).zfill(5),
            draw_date=start + timedelta(days=index),
            values=tuple(sorted({((index * 3 + offset * 8) % 45) + 1 for offset in range(6)})),
        )
        for index in range(draws)
    ]
    return ProductDataset(
        product=product,
        observations=observations,
        source_counts=Counter({"vietlott.vn": draws}),
        status_counts=Counter({"confirmed": draws}),
        validation_counts=Counter({"valid": draws}),
        latest_fetched_at=f"2024-03-{min(draws, 28):02d}T00:00:00+00:00",
    )


def _digit_dataset(draws: int) -> ProductDataset:
    product = PRODUCTS["max3d"]
    start = date(2024, 1, 1)
    observations = [
        Observation(
            draw_id=str(index + 1).zfill(5),
            draw_date=start + timedelta(days=index),
            outcomes=(
                f"{index % 10}{(index + 3) % 10}{(index + 7) % 10}",
                f"{(index + 5) % 10}{(index + 1) % 10}{(index + 8) % 10}",
            ),
        )
        for index in range(draws)
    ]
    return ProductDataset(
        product=product,
        observations=observations,
        source_counts=Counter({"vietlott.vn": draws}),
        status_counts=Counter({"confirmed": draws}),
        validation_counts=Counter({"valid": draws}),
        latest_fetched_at=f"2024-03-{min(draws, 28):02d}T00:00:00+00:00",
    )


def _number_observation_counts(observations: list[Observation]) -> Counter[int]:
    return Counter(value for item in observations for value in item.values)


def _digit_observation_counts(
    observations: list[Observation],
    positions: int,
) -> list[Counter[int]]:
    counters = [Counter() for _ in range(positions)]
    for item in observations:
        predictions_module._update_digit_counts(counters, item.outcomes, 1)
    return counters


def test_prediction_ledger_is_idempotent_and_appends_evaluations(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = PredictionLedger.load(path)
    ledger.process_product(_dataset(40))
    original_predictions = [
        event.copy() for event in ledger.events if event["event_type"] == "prediction"
    ]
    assert len(original_predictions) == 4
    assert {event["strategy"] for event in original_predictions} == {
        "audit_signal",
        "balanced_signal",
        "recent_frequency",
        "uniform_seeded",
    }

    ledger.process_product(_dataset(40))
    assert len(ledger.events) == 4

    ledger.process_product(_dataset(41))
    predictions = [event for event in ledger.events if event["event_type"] == "prediction"]
    evaluations = [event for event in ledger.events if event["event_type"] == "evaluation"]
    assert len(predictions) == 8
    assert len(evaluations) == 4
    assert predictions[:4] == original_predictions
    assert all(event["actual_draw_id"] == "00041" for event in evaluations)
    report = ledger.site_report()
    assert report["schema_version"] == 2
    assert report["evaluation_count"] == 4
    assert report["outcome_summary"]["exact"] == 0
    assert report["product_outcomes"]["mega645"]["evaluated_predictions"] == 4
    assert report["history_limit_per_product"] == 100
    assert len(report["archived_evaluations"]) == report["evaluation_count"] == 4
    assert report["archived_evaluations"][0]["actual_draw_id"] == "00041"
    assert len(report["pending_predictions"]) == report["pending_count"] == 4
    assert report["pending_predictions"][0]["prediction"]
    assert report["pending_predictions"][0]["prediction_generated_at"]
    assert report["recent_evaluations"][0]["prediction"]
    assert report["recent_evaluations"][0]["prediction_generated_at"]
    assert report["recent_evaluations"][0]["outcome"]["status"] in {
        "exact",
        "near",
        "wrong",
    }

    ledger.save()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    saved = [json.loads(line) for line in lines]
    assert all(event["event_type"] in {"prediction", "evaluation"} for event in saved)
    assert all(len(event["event_hash"]) == 64 for event in saved)
    assert saved[0]["previous_event_hash"] is None
    assert saved[-1]["ledger_index"] == 11
    reloaded = PredictionLedger.load(path)
    assert reloaded.validate_integrity()["status"] == "valid"
    assert reloaded.site_report()["ledger_integrity"]["root_hash"] == saved[-1]["event_hash"]


def test_prediction_ledger_detects_historical_tampering(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = PredictionLedger.load(path)
    ledger.process_product(_dataset(40))
    ledger.save()

    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    events[1]["prediction"]["numbers"][0] = 45
    path.write_text(
        "\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for event in events
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        PredictionLedger.load(path)


@pytest.mark.parametrize("mutation", ["delete", "insert", "append_unsealed"])
def test_prediction_ledger_detects_structural_tampering(tmp_path, mutation) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = PredictionLedger.load(path)
    ledger.process_product(_dataset(40))
    ledger.save()
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]

    if mutation == "delete":
        events.pop(1)
    elif mutation == "insert":
        events.insert(1, events[0].copy())
    else:
        events.append({"event_type": "prediction", "prediction_id": "unsealed"})

    path.write_text(
        "\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for event in events
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        PredictionLedger.load(path)


def test_history_fingerprint_covers_all_observations() -> None:
    original = _dataset(40)
    changed = _dataset(40)
    first = changed.observations[0]
    changed.observations[0] = Observation(
        draw_id=first.draw_id,
        draw_date=first.draw_date,
        values=(1, 2, 3, 4, 5, 45),
    )

    assert original.fingerprint == changed.fingerprint
    assert original.history_fingerprint != changed.history_fingerprint


def test_walk_forward_backtest_reports_uniform_baseline() -> None:
    report = build_backtest_report(_dataset(160))

    assert report["status"] == "complete"
    assert report["method"] == "walk_forward"
    assert report["samples"] > 0
    assert report["walk_forward_samples"] > report["samples"]
    target_scope = report["target_scope"]
    assert target_scope["method"] == "same_confirmed_draw_targets_for_all_strategies"
    assert target_scope["target_draw_count"] == report["samples"]
    assert target_scope["first_target_draw_id"] == report["first_test_draw_id"]
    assert target_scope["latest_target_draw_id"] == report["latest_test_draw_id"]
    assert target_scope["no_strategy_specific_filtering"] is True
    phase_split = report["phase_split"]
    assert phase_split["method"] == "chronological_formula_selection_then_final_evaluation"
    assert phase_split["formulas_frozen_before_final_evaluation"] is True
    assert phase_split["selection_result_used_to_choose_formulas"] is False
    assert phase_split["final_evaluation_feedback_used_for_model_selection"] is False
    assert (
        phase_split["selection_phase"]["draw_count"]
        + phase_split["final_evaluation_phase"]["draw_count"]
        == report["walk_forward_samples"]
    )
    assert phase_split["final_evaluation_phase"]["draw_count"] == report["samples"]
    assert phase_split["final_evaluation_phase"]["scope_id"] == target_scope["scope_id"]
    assert (
        phase_split["final_evaluation_phase"]["draw_ids_sha256"]
        == target_scope["target_draw_ids_sha256"]
    )
    trial_registry = report["multiple_testing_trials"]
    assert trial_registry["method"] == "benjamini_hochberg_over_published_and_registered_trials"
    assert trial_registry["trial_count"] == 13
    assert trial_registry["published_trial_count"] == 3
    assert trial_registry["registered_parameter_variant_count"] == 10
    assert {
        row["published_comparison_key"]
        for row in trial_registry["trials"]
        if row["published"]
    } == {"comparison", "recent_comparison", "audit_comparison"}
    assert all(
        row["target_scope_id"] == target_scope["scope_id"]
        for row in trial_registry["trials"]
    )
    window_sensitivity = report["window_sensitivity"]
    assert window_sensitivity["method"] == "registered_recent_window_sensitivity"
    assert window_sensitivity["registered_window_draws"] == [50, 200, 500]
    assert window_sensitivity["primary_recent_window_draws"] == report[
        "recent_window_draws"
    ]
    assert window_sensitivity["trial_count"] == 9
    assert window_sensitivity["primary_trial_count"] == 3
    assert window_sensitivity["alternative_window_trial_count"] == 6
    assert {
        row["trial_id"] for row in window_sensitivity["trials"]
    }.issubset({row["trial_id"] for row in trial_registry["trials"]})
    bootstrap = report["comparison"]["block_bootstrap_check"]
    assert bootstrap["status"] == "available"
    assert bootstrap["method"] == "moving_block_bootstrap"
    assert bootstrap["resamples"] == 199
    assert len(bootstrap["seed"]) == 16
    assert bootstrap["preserve_time_structure"] == "contiguous_observation_blocks"
    assert bootstrap["no_multiple_testing_decision"] is True
    assert bootstrap["normal_approximation"]["method"] == "paired_normal_mean_interval"
    assert bootstrap["confidence_interval_lower"] <= bootstrap["confidence_interval_upper"]
    assert all(
        row["block_bootstrap_check"]["status"] == "available"
        and row["block_bootstrap_check"]["normal_approximation"]["method"]
        == "paired_normal_mean_interval"
        for row in trial_registry["trials"]
    )
    trial_log = report["trial_disposition_log"]
    assert trial_log["method"] == "registered_trial_disposition_log"
    assert trial_log["included_trial_count"] == trial_registry["trial_count"]
    assert trial_log["failed_trial_count"] == trial_registry["trial_count"]
    assert trial_log["rejected_configuration_count"] >= 4
    assert trial_log["retained_record_count"] == (
        trial_log["included_trial_count"] + trial_log["rejected_configuration_count"]
    )
    assert {
        row["trial_id"] for row in trial_log["included_trials"]
    } == {
        row["trial_id"] for row in trial_registry["trials"]
    }
    assert all(
        row["included_in_multiple_testing"] is False
        and row["evaluated_on_final_scope"] is False
        and row["reason_code"]
        for row in trial_log["rejected_configurations"]
    )
    assert report["baseline"]["strategy"] == "uniform_exact_expectation"
    assert report["baseline"]["method"] == "exact_hypergeometric_expectation"
    assert report["baseline"]["average_hits"] == 0.8
    partial_baseline = report["baseline"]["partial_match_baseline"]
    assert partial_baseline["method"] == "exact_hypergeometric_distribution"
    assert partial_baseline["score_basis"] == "main_number_hits"
    assert partial_baseline["samples"] == report["samples"]
    assert partial_baseline["partial_match_rule"] == "0 < hit_count_t < pick_count"
    assert partial_baseline["partial_match_probability"] == pytest.approx(
        sum(
            row["probability"]
            for row in report["baseline"]["hit_distribution"]
            if 0 < row["hits"] < report["baseline"]["partial_match_baseline"]["pick_count"]
        )
    )
    assert (
        partial_baseline["expected_partial_match_count"]
        > partial_baseline["expected_near_count"]
        > partial_baseline["expected_exact_count"]
    )
    formulas = report["score_formulas"]
    assert formulas["product_kind"] == "number_set"
    assert formulas["score_unit"] == "main_number_hits_per_draw"
    assert formulas["comparison_metric"] == "mean_hit_difference"
    assert formulas["special_numbers_policy"] == "special_numbers_not_scored_in_backtest"
    assert {row["strategy"] for row in formulas["strategies"]} == {
        "balanced_signal",
        "recent_frequency",
        "audit_signal",
    }
    assert any(
        "(overdue_ratio - 1)" in row["formula"]
        for row in formulas["strategies"]
    )
    assert report["recent_model"]["strategy"] == "recent_frequency"
    assert report["audit_model"]["strategy"] == "audit_signal"
    assert "recent_comparison" in report
    assert "audit_comparison" in report
    assert "approximate_p_value" in report["comparison"]
    assert report["comparison"]["confidence_level"] == 0.95
    assert (
        report["comparison"]["confidence_interval_lower"]
        <= report["comparison"]["mean_hit_difference"]
        <= report["comparison"]["confidence_interval_upper"]
    )
    assert report["comparison"]["beats_baseline"] is False
    for key in (
        "model",
        "recent_model",
        "audit_model",
        "baseline",
        "comparison",
        "recent_comparison",
        "audit_comparison",
    ):
        assert report[key]["target_scope_id"] == target_scope["scope_id"]
        assert report[key]["target_draw_count"] == target_scope["target_draw_count"]


def test_number_backtest_predictions_use_only_prior_draws(monkeypatch) -> None:
    dataset = _dataset(90)
    observations = dataset.observations
    start = 30
    original_number_scores = predictions_module._number_scores
    original_pair_scores = predictions_module._number_pair_scores_from_counts
    score_calls: list[tuple[int, int, int]] = []
    pair_calls: list[int] = []

    def guarded_pair_scores(product, pair_counts, draw_count):
        history = observations[:draw_count]
        assert pair_counts == predictions_module._number_pair_counts(history)
        pair_calls.append(draw_count)
        return original_pair_scores(product, pair_counts, draw_count)

    def guarded_number_scores(
        product,
        total_counts,
        total_draws,
        recent_counts,
        recent_draws,
        short_counts,
        short_draws,
        last_seen,
        current_index,
    ):
        history = observations[:total_draws]
        expected_last_seen = {}
        for index, item in enumerate(history):
            for value in item.values:
                expected_last_seen[value] = index
        assert current_index == total_draws
        assert total_counts == _number_observation_counts(history)
        assert recent_counts == _number_observation_counts(
            observations[total_draws - recent_draws : total_draws]
        )
        assert short_counts == _number_observation_counts(
            observations[total_draws - short_draws : total_draws]
        )
        assert last_seen == expected_last_seen
        score_calls.append((total_draws, recent_draws, short_draws))
        return original_number_scores(
            product,
            total_counts,
            total_draws,
            recent_counts,
            recent_draws,
            short_counts,
            short_draws,
            last_seen,
            current_index,
        )

    monkeypatch.setattr(
        predictions_module,
        "_number_pair_scores_from_counts",
        guarded_pair_scores,
    )
    monkeypatch.setattr(predictions_module, "_number_scores", guarded_number_scores)

    report = build_backtest_report(dataset)

    assert report["status"] == "complete"
    assert score_calls == [
        (index, min(window, index), min(50, index))
        for index in range(start, len(observations))
        for window in (50, 200, 500)
    ]
    assert pair_calls == list(range(start, len(observations)))


def test_digit_backtest_predictions_use_only_prior_draws(monkeypatch) -> None:
    dataset = _digit_dataset(90)
    observations = dataset.observations
    start = 30
    positions = dataset.product.sequence_length or 0
    original_digit_sequence = predictions_module._digit_sequence_from_scores
    calls: list[tuple[int, str, int]] = []
    expected_calls = (
        ("balanced", 50),
        ("recent", 50),
        ("audit", 50),
        ("balanced", 200),
        ("recent", 200),
        ("audit", 200),
        ("balanced", 500),
        ("recent", 500),
        ("audit", 500),
        ("short", 500),
        ("long", 500),
        ("balanced_no_long_penalty", 500),
        ("audit_unclipped", 500),
    )

    def guarded_digit_sequence(total, recent, short, symbols, strategy, seed):
        target_index = start + len(calls) // len(expected_calls)
        expected_strategy, expected_recent_window = expected_calls[
            len(calls) % len(expected_calls)
        ]
        assert strategy == expected_strategy
        assert total == _digit_observation_counts(
            observations[:target_index],
            positions,
        )
        assert recent == _digit_observation_counts(
            observations[
                max(0, target_index - expected_recent_window) : target_index
            ],
            positions,
        )
        assert short == _digit_observation_counts(
            observations[max(0, target_index - 50) : target_index],
            positions,
        )
        assert f"|{observations[target_index].draw_id}|" in seed
        calls.append((target_index, strategy, expected_recent_window))
        return original_digit_sequence(total, recent, short, symbols, strategy, seed)

    monkeypatch.setattr(
        predictions_module,
        "_digit_sequence_from_scores",
        guarded_digit_sequence,
    )

    report = build_backtest_report(dataset)

    assert report["status"] == "complete"
    assert calls == [
        (index, strategy, window)
        for index in range(start, len(observations))
        for strategy, window in expected_calls
    ]


def test_digit_walk_forward_backtest_reports_digit_score_formula() -> None:
    report = build_backtest_report(_digit_dataset(160))

    assert report["status"] == "complete"
    assert report["walk_forward_samples"] > report["samples"]
    assert report["phase_split"]["final_evaluation_phase"]["draw_count"] == report[
        "samples"
    ]
    assert report["phase_split"]["final_evaluation_phase"]["scope_id"] == report[
        "target_scope"
    ]["scope_id"]
    assert report["multiple_testing_trials"]["trial_count"] == 13
    assert report["multiple_testing_trials"]["published_trial_count"] == 3
    assert report["multiple_testing_trials"]["registered_parameter_variant_count"] == 10
    window_sensitivity = report["window_sensitivity"]
    assert window_sensitivity["registered_window_draws"] == [50, 200, 500]
    assert window_sensitivity["primary_recent_window_draws"] == report[
        "recent_window_draws"
    ]
    assert window_sensitivity["trial_count"] == 9
    assert window_sensitivity["primary_trial_count"] == 3
    assert window_sensitivity["alternative_window_trial_count"] == 6
    bootstrap = report["comparison"]["block_bootstrap_check"]
    assert bootstrap["status"] == "available"
    assert bootstrap["method"] == "moving_block_bootstrap"
    assert bootstrap["resamples"] == 199
    assert bootstrap["normal_approximation"]["method"] == "paired_normal_mean_interval"
    trial_log = report["trial_disposition_log"]
    assert trial_log["included_trial_count"] == 13
    assert trial_log["failed_trial_count"] == 13
    assert trial_log["rejected_configuration_count"] >= 4
    assert any(
        row["reason_code"] == "tier_breakdown_is_explanatory_not_selection_target"
        for row in trial_log["rejected_configurations"]
    )
    assert report["baseline"]["method"] == "exact_sequence_enumeration"
    partial_baseline = report["baseline"]["partial_match_baseline"]
    assert partial_baseline["method"] == "exact_sequence_enumeration"
    assert partial_baseline["score_basis"] == "best_position_matches"
    assert partial_baseline["samples"] == report["samples"]
    assert partial_baseline["candidate_space_size"] == 1000
    assert (
        partial_baseline["partial_match_rule"]
        == "0 < best_position_matches_t < sequence_length"
    )
    assert partial_baseline["expected_partial_match_count"] > partial_baseline[
        "expected_exact_count"
    ]
    assert 0 < partial_baseline["zero_match_probability"] < 1
    formulas = report["score_formulas"]
    assert formulas["product_kind"] == "digit_sequence"
    assert formulas["score_unit"] == "best_position_matches_per_draw"
    assert formulas["comparison_metric"] == "mean_position_match_difference"
    assert "max_actual" in formulas["per_draw_score"]
    assert "actual outcomes_t" in formulas["comparison_difference"]
    assert {row["strategy"] for row in formulas["strategies"]} == {
        "balanced_signal",
        "recent_frequency",
        "audit_signal",
    }
    assert all("selection_rule" in row for row in formulas["strategies"])


def test_digit_uniform_expectation_enumerates_complete_space() -> None:
    expected, exact_probability, distribution = _digit_uniform_expectation(
        {"111"},
        [1, 2],
        3,
    )

    assert expected == 1.5
    assert exact_probability == 0.125
    assert distribution == {
        0: 0.125,
        1: 0.375,
        2: 0.375,
        3: 0.125,
    }


def _mock_phase_split(scope_id: str, final_count: int) -> dict[str, object]:
    return {
        "method": "chronological_formula_selection_then_final_evaluation",
        "walk_forward_target_draw_count": final_count + 5,
        "selection_fraction": 0.5,
        "formulas_frozen_before_final_evaluation": True,
        "selection_result_used_to_choose_formulas": False,
        "final_evaluation_feedback_used_for_model_selection": False,
        "selection_phase": {
            "phase": "formula_selection",
            "scope_id": "selection-scope",
            "draw_count": 5,
        },
        "final_evaluation_phase": {
            "phase": "final_evaluation",
            "scope_id": scope_id,
            "draw_count": final_count,
        },
    }


def _mock_trial(
    *,
    scope_id: str,
    final_count: int,
    trial_id: str,
    metric_key: str,
    difference: float,
    p_value: float,
    published_key: str | None = None,
) -> dict[str, object]:
    return {
        "trial_id": trial_id,
        "strategy": trial_id.split(":", 1)[0],
        "label": trial_id,
        "variant_role": (
            "published_final_model"
            if published_key is not None
            else "registered_parameter_variant"
        ),
        "published": published_key is not None,
        "published_comparison_key": published_key,
        "target_scope_id": scope_id,
        "target_draw_count": final_count,
        metric_key: difference,
        "paired_z_score": 0.0,
        "approximate_p_value": p_value,
        "standard_error": 0.0,
        "confidence_level": 0.95,
        "confidence_interval_lower": difference,
        "confidence_interval_upper": difference,
        "parameters": {},
    }


def _mock_multiple_testing_trials(
    *,
    scope_id: str,
    final_count: int,
    metric_key: str,
    trials: list[dict[str, object]],
) -> dict[str, object]:
    published_count = sum(bool(row["published"]) for row in trials)
    return {
        "method": "benjamini_hochberg_over_published_and_registered_trials",
        "scope_policy": "published_final_models_plus_registered_parameter_variants",
        "product_kind": "number_set"
        if metric_key == "mean_hit_difference"
        else "digit_sequence",
        "comparison_metric": metric_key,
        "trial_count": len(trials),
        "published_trial_count": published_count,
        "registered_parameter_variant_count": len(trials) - published_count,
        "trials": trials,
    }


def _mock_trial_disposition_log(
    *,
    scope_id: str,
    final_count: int,
    metric_key: str,
    trials: list[dict[str, object]],
) -> dict[str, object]:
    included_trials = [
        {
            "trial_id": trial["trial_id"],
            "strategy": trial["strategy"],
            "label": trial["label"],
            "variant_role": trial["variant_role"],
            "published": trial["published"],
            "published_comparison_key": trial["published_comparison_key"],
            "included_in_multiple_testing": True,
            "evaluated_on_final_scope": True,
            "target_scope_id": scope_id,
            "target_draw_count": final_count,
            "comparison_metric": metric_key,
            metric_key: trial[metric_key],
            "approximate_p_value": trial["approximate_p_value"],
            "q_value_global_bh": None,
            "effect_direction": "positive"
            if float(trial[metric_key]) > 0
            else "non_positive",
            "result_status": "failed_raw_baseline_test"
            if float(trial[metric_key]) > 0
            else "failed_non_positive_effect",
            "parameters_sha256": "0" * 64,
            "parameters": {},
        }
        for trial in trials
    ]
    return {
        "method": "registered_trial_disposition_log",
        "retention_policy": "record_included_failed_and_rejected_backtest_configs",
        "product_kind": "number_set"
        if metric_key == "mean_hit_difference"
        else "digit_sequence",
        "comparison_metric": metric_key,
        "scope_policy": "test",
        "included_trial_count": len(included_trials),
        "published_trial_count": sum(bool(row["published"]) for row in included_trials),
        "registered_parameter_variant_count": sum(
            row["variant_role"] == "registered_parameter_variant"
            for row in included_trials
        ),
        "raw_unadjusted_winning_trial_count": 0,
        "adjusted_winning_trial_count": 0,
        "failed_trial_count": len(included_trials),
        "rejected_configuration_count": 1,
        "retained_record_count": len(included_trials) + 1,
        "included_trials": included_trials,
        "rejected_configurations": [
            {
                "config_id": "test:rejected",
                "label": "Rejected test config",
                "strategy_family": "test",
                "disposition": "rejected_before_final_evaluation",
                "reason_code": "test_guardrail",
                "reason": "Rejected in test fixture",
                "target_scope_id": scope_id,
                "target_draw_count": final_count,
                "included_in_multiple_testing": False,
                "evaluated_on_final_scope": False,
                "published": False,
                "parameters": {},
            }
        ],
    }


def test_finalize_backtests_applies_global_bh_correction() -> None:
    scope = {
        "scope_id": "scope-a",
        "target_draw_count": 10,
        "target_draw_ids_sha256": "a" * 64,
    }
    first_trials = [
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="balanced_signal:published_final",
            metric_key="mean_hit_difference",
            difference=0.1,
            p_value=0.01,
            published_key="comparison",
        ),
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="audit_signal:published_final",
            metric_key="mean_hit_difference",
            difference=0.1,
            p_value=0.04,
            published_key="audit_comparison",
        ),
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="short_frequency:shadow",
            metric_key="mean_hit_difference",
            difference=0.0,
            p_value=1.0,
        ),
    ]
    second_trials = [
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="balanced_signal:published_final",
            metric_key="mean_position_match_difference",
            difference=0.1,
            p_value=0.06,
            published_key="comparison",
        ),
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="audit_signal:published_final",
            metric_key="mean_position_match_difference",
            difference=-0.1,
            p_value=0.001,
            published_key="audit_comparison",
        ),
        _mock_trial(
            scope_id="scope-a",
            final_count=10,
            trial_id="short_frequency:shadow",
            metric_key="mean_position_match_difference",
            difference=0.0,
            p_value=1.0,
        ),
    ]
    reports = [
        {
            "product": {"slug": "first"},
            "backtest": {
                "status": "complete",
                "samples": 10,
                "target_scope": scope,
                "phase_split": _mock_phase_split("scope-a", 10),
                "multiple_testing_trials": _mock_multiple_testing_trials(
                    scope_id="scope-a",
                    final_count=10,
                    metric_key="mean_hit_difference",
                    trials=first_trials,
                ),
                "trial_disposition_log": _mock_trial_disposition_log(
                    scope_id="scope-a",
                    final_count=10,
                    metric_key="mean_hit_difference",
                    trials=first_trials,
                ),
                "comparison": {
                    "mean_hit_difference": 0.1,
                    "approximate_p_value": 0.01,
                    "target_scope_id": "scope-a",
                    "target_draw_count": 10,
                },
                "audit_comparison": {
                    "mean_hit_difference": 0.1,
                    "approximate_p_value": 0.04,
                    "target_scope_id": "scope-a",
                    "target_draw_count": 10,
                },
            },
        },
        {
            "product": {"slug": "second"},
            "backtest": {
                "status": "complete",
                "samples": 10,
                "target_scope": scope,
                "phase_split": _mock_phase_split("scope-a", 10),
                "multiple_testing_trials": _mock_multiple_testing_trials(
                    scope_id="scope-a",
                    final_count=10,
                    metric_key="mean_position_match_difference",
                    trials=second_trials,
                ),
                "trial_disposition_log": _mock_trial_disposition_log(
                    scope_id="scope-a",
                    final_count=10,
                    metric_key="mean_position_match_difference",
                    trials=second_trials,
                ),
                "comparison": {
                    "mean_position_match_difference": 0.1,
                    "approximate_p_value": 0.06,
                    "target_scope_id": "scope-a",
                    "target_draw_count": 10,
                },
                "audit_comparison": {
                    "mean_position_match_difference": -0.1,
                    "approximate_p_value": 0.001,
                    "target_scope_id": "scope-a",
                    "target_draw_count": 10,
                },
            },
        },
    ]

    summary = finalize_backtests(reports)

    assert summary["comparison_count"] == 4
    assert summary["correction_trial_count"] == 6
    assert summary["target_scope_validation"]["status"] == "validated"
    assert summary["target_scope_validation"]["product_count"] == 2
    assert summary["phase_split_validation"]["status"] == "validated"
    assert summary["phase_split_validation"]["product_count"] == 2
    assert summary["multiple_testing_registry_validation"]["status"] == "validated"
    assert (
        summary["multiple_testing_registry_validation"]["correction_trial_count"] == 6
    )
    assert summary["trial_disposition_validation"]["status"] == "validated"
    assert summary["trial_disposition_validation"]["included_trial_count"] == 6
    assert summary["trial_disposition_validation"]["failed_trial_count"] == 5
    assert summary["trial_disposition_validation"]["rejected_configuration_count"] == 2
    assert summary["trial_disposition_validation"]["retained_record_count"] == 8
    assert summary["unadjusted_winning_comparisons"] == 2
    assert summary["adjusted_winning_comparisons"] == 1
    assert summary["products_with_adjusted_win"] == ["first"]
    assert reports[0]["backtest"]["comparison"]["q_value_global_bh"] == 0.03
    assert reports[0]["backtest"]["comparison"]["multiple_testing_scope"] == 6
    assert reports[0]["backtest"]["comparison"]["beats_baseline"] is True
    assert reports[0]["backtest"]["audit_comparison"]["beats_baseline"] is False
    assert reports[1]["backtest"]["audit_comparison"]["beats_baseline"] is False
    first_trial_log = reports[0]["backtest"]["trial_disposition_log"]
    assert first_trial_log["adjusted_winning_trial_count"] == 1
    assert first_trial_log["failed_trial_count"] == 2
    assert {
        row["result_status"] for row in first_trial_log["included_trials"]
    } == {
        "adjusted_win",
        "raw_win_failed_global_correction",
        "failed_non_positive_effect",
    }


def test_finalize_backtests_rejects_target_scope_mismatch() -> None:
    report = build_backtest_report(_dataset(160))
    broken = deepcopy(report)
    broken["recent_comparison"]["target_scope_id"] = "different-scope"

    with pytest.raises(ValueError, match="target_scope_id mismatch"):
        finalize_backtests([{"product": {"slug": "mega645"}, "backtest": broken}])


def test_finalize_backtests_rejects_phase_split_mismatch() -> None:
    report = build_backtest_report(_dataset(160))
    broken = deepcopy(report)
    broken["phase_split"]["final_evaluation_phase"]["scope_id"] = "different-scope"

    with pytest.raises(ValueError, match="final phase target_scope_id mismatch"):
        finalize_backtests([{"product": {"slug": "mega645"}, "backtest": broken}])


def test_prediction_report_prefers_newer_model_for_same_cutoff(tmp_path) -> None:
    older = {
        "event_type": "prediction",
        "prediction_id": "older-model",
        "product": "mega645",
        "strategy": "balanced_signal",
        "strategy_label": "balanced",
        "model_version": "1.0.0",
        "generated_at": "2026-06-14T12:00:00+00:00",
        "dataset_cutoff_draw_id": "00100",
        "dataset_cutoff_date": "2026-06-13",
        "dataset_fingerprint": "same-cutoff",
        "target": "first_confirmed_draw_after_cutoff",
        "prediction": {"numbers": [1, 2, 3, 4, 5, 6], "special_numbers": []},
        "parameters": {"selection_count": 6},
        "research_only": True,
    }
    newer = {
        **older,
        "prediction_id": "newer-model",
        "model_version": "1.1.0",
        "generated_at": "2026-06-13T12:00:00+00:00",
        "prediction": {"numbers": [7, 8, 9, 10, 11, 12], "special_numbers": []},
    }

    report = PredictionLedger(
        path=tmp_path / "ledger.jsonl",
        events=[older, newer],
    ).site_report()

    [latest] = report["latest"]["mega645"]
    assert latest["prediction_id"] == "newer-model"
    assert latest["prediction"]["numbers"] == [7, 8, 9, 10, 11, 12]
    assert {row["prediction_id"] for row in report["pending_predictions"]} == {
        "newer-model",
        "older-model",
    }


def test_prediction_report_uses_strict_exact_and_near_rules(tmp_path) -> None:
    base_prediction = {
        "event_type": "prediction",
        "product": "mega645",
        "strategy": "balanced_signal",
        "strategy_label": "Tín hiệu cân bằng",
        "model_version": "1.0.0",
        "generated_at": "2026-06-13T12:00:00+00:00",
        "dataset_cutoff_draw_id": "00100",
        "dataset_cutoff_date": "2026-06-13",
        "dataset_fingerprint": "frozen",
        "prediction": {
            "numbers": [1, 2, 3, 4, 5, 6],
            "special_numbers": [],
        },
        "parameters": {"selection_count": 6},
    }
    actuals = (
        ("exact", [1, 2, 3, 4, 5, 6]),
        ("near", [1, 2, 3, 4, 5, 7]),
        ("wrong", [1, 8, 9, 10, 11, 12]),
    )
    events = []
    for index, (expected_status, actual_numbers) in enumerate(actuals):
        prediction_id = f"prediction-{index}"
        events.append({**base_prediction, "prediction_id": prediction_id})
        events.append(
            {
                "event_type": "evaluation",
                "evaluation_id": f"evaluation-{index}",
                "prediction_id": prediction_id,
                "product": "mega645",
                "strategy": "balanced_signal",
                "model_version": "1.0.0",
                "evaluated_at": "2026-06-14T12:00:00+00:00",
                "actual_draw_id": f"0010{index + 1}",
                "actual_draw_date": "2026-06-14",
                "actual_result": {
                    "numbers": actual_numbers,
                    "special_numbers": [],
                },
                "metrics": {
                    "exact_hit": expected_status == "exact",
                    "hit_count": len(
                        set(base_prediction["prediction"]["numbers"])
                        & set(actual_numbers)
                    ),
                    "special_hit_count": 0,
                },
            }
        )

    report = PredictionLedger(path=tmp_path / "ledger.jsonl", events=events).site_report()

    summary = report["outcome_summary"]
    assert summary["evaluated_draws"] == 3
    assert summary["evaluated_predictions"] == 3
    assert summary["exact"] == 1
    assert summary["near"] == 1
    assert summary["wrong"] == 1
    assert summary["partial_matches"] == 2
    assert summary["zero_matches"] == 0
    assert summary["expected_exact_by_chance"] > 0
    assert summary["expected_near_by_chance"] > summary["expected_exact_by_chance"]
    assert summary["near_excess_vs_chance"] > 0

    product_outcome = report["product_outcomes"]["mega645"]
    assert product_outcome["evaluated_draws"] == 3
    assert product_outcome["evaluated_predictions"] == 3
    assert product_outcome["exact"] == 1
    assert product_outcome["near"] == 1
    assert product_outcome["wrong"] == 1
    assert product_outcome["partial_matches"] == 2
    assert product_outcome["zero_matches"] == 0
    assert product_outcome["score_kind"] == "numbers"
    assert product_outcome["expected_near_by_chance"] == summary["expected_near_by_chance"]
    assert product_outcome["score_distribution"] == [
        {"score": 1, "count": 1},
        {"score": 5, "count": 1},
        {"score": 6, "count": 1},
    ]
    statuses = {
        evaluation["prediction_id"]: evaluation["outcome"]["status"]
        for evaluation in report["recent_evaluations"]
    }
    assert len(report["archived_evaluations"]) == 3
    assert statuses == {
        "prediction-0": "exact",
        "prediction-1": "near",
        "prediction-2": "wrong",
    }
    near_row = next(
        evaluation
        for evaluation in report["recent_evaluations"]
        if evaluation["outcome"]["status"] == "near"
    )
    assert (
        near_row["outcome"]["baseline_probability"]["near"]
        > near_row["outcome"]["baseline_probability"]["exact"]
    )


def test_digit_near_probability_accounts_for_multiple_prize_outcomes(tmp_path) -> None:
    outcomes = [
        "312",
        "097",
        "756",
        "585",
        "958",
        "008",
        "795",
        "713",
        "998",
        "953",
        "307",
        "449",
        "849",
        "207",
        "137",
        "038",
        "017",
        "198",
        "436",
        "401",
    ]
    prediction = {
        "event_type": "prediction",
        "prediction_id": "max3dpro-prediction",
        "product": "max3dpro",
        "strategy": "balanced_signal",
        "strategy_label": "balanced",
        "model_version": "1.3.0",
        "generated_at": "2026-06-13T19:25:04+00:00",
        "dataset_cutoff_draw_id": "00739",
        "dataset_cutoff_date": "2026-06-13",
        "dataset_fingerprint": "frozen",
        "prediction": {"sequence": "015"},
        "parameters": {"sequence_length": 3},
    }
    evaluation = {
        "event_type": "evaluation",
        "evaluation_id": "max3dpro-evaluation",
        "prediction_id": "max3dpro-prediction",
        "product": "max3dpro",
        "strategy": "balanced_signal",
        "model_version": "1.3.0",
        "evaluated_at": "2026-06-16T11:32:02+00:00",
        "actual_draw_id": "00740",
        "actual_draw_date": "2026-06-16",
        "actual_result": {"outcomes": outcomes},
        "metrics": {
            "exact_hit": False,
            "best_position_matches": 2,
        },
    }

    report = PredictionLedger(
        path=tmp_path / "ledger.jsonl",
        events=[prediction, evaluation],
    ).site_report()

    [row] = report["recent_evaluations"]
    baseline = row["outcome"]["baseline_probability"]
    assert row["outcome"]["status"] == "near"
    assert baseline["actual_outcomes"] == 20
    assert baseline["candidate_space_size"] == 1000
    assert baseline["exact"] == 0.02
    assert baseline["near"] == 0.401
    assert report["product_outcomes"]["max3dpro"]["expected_near_by_chance"] == 0.401
