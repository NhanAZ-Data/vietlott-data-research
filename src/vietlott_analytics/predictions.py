from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from heapq import nlargest
from itertools import combinations
from itertools import product as cartesian_product
from pathlib import Path
from statistics import NormalDist, fmean, stdev
from typing import Any

from .catalog import PRODUCTS, AnalysisKind, AnalyticsProduct
from .io import Observation, ProductDataset

MODEL_VERSION = "1.3.0"
LEDGER_CHAIN_VERSION = 1
NUMBER_SCORE_POLICY = (
    "recent=0.6*short+0.4*recent; "
    "balanced=0.4*short+0.3*recent-0.15*long+0.15*(overdue_ratio-1)"
)
AUDIT_NUMBER_SCORE_POLICY = (
    "audit=0.45*long_hot+0.25*recent+0.15*short+0.15*pair_pressure; "
    "greedy pair-aware selection adds 0.12*selected_pair_bonus"
)
DIGIT_SCORE_POLICY = (
    "recent=0.6*short+0.4*recent; "
    "balanced=0.4*short+0.3*recent-0.2*long"
)
AUDIT_DIGIT_SCORE_POLICY = (
    "audit=0.45*long_hot+0.35*recent+0.20*short"
)
PAIR_WINDOW_LIMIT = 5000
NORMAL = NormalDist()
BACKTEST_MULTIPLE_TESTING_ALPHA = 0.05
BACKTEST_MODEL_KEYS = ("model", "recent_model", "audit_model")
BACKTEST_COMPARISON_KEYS = ("comparison", "recent_comparison", "audit_comparison")
BACKTEST_SCOPE_STRATEGIES = (
    "balanced_signal",
    "recent_frequency",
    "audit_signal",
    "uniform_exact_expectation",
)
BACKTEST_SELECTION_PHASE_FRACTION = 0.5
BACKTEST_NUMBER_SHADOW_TRIALS = (
    {
        "trial_id": "short_frequency_only",
        "strategy": "short_frequency",
        "label": "Tần suất cửa sổ ngắn",
        "score_key": "short_z",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "short_z_only", "short_window_draws": 50},
    },
    {
        "trial_id": "long_frequency_only",
        "strategy": "long_frequency",
        "label": "Tần suất toàn lịch sử",
        "score_key": "long_z",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "long_z_only"},
    },
    {
        "trial_id": "balanced_without_overdue",
        "strategy": "balanced_signal",
        "label": "Cân bằng không dùng độ vắng",
        "score_key": "balanced_no_overdue",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "balanced_without_overdue_ratio"},
    },
    {
        "trial_id": "audit_without_pair_greedy",
        "strategy": "audit_signal",
        "label": "Audit không thưởng cặp tham lam",
        "score_key": "audit",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "audit_score_without_selected_pair_bonus"},
    },
)
BACKTEST_DIGIT_SHADOW_TRIALS = (
    {
        "trial_id": "short_frequency_only",
        "strategy": "short_frequency",
        "label": "Tần suất cửa sổ ngắn",
        "score_strategy": "short",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "short_z_only", "short_window_draws": 50},
    },
    {
        "trial_id": "long_frequency_only",
        "strategy": "long_frequency",
        "label": "Tần suất toàn lịch sử",
        "score_strategy": "long",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "long_z_only"},
    },
    {
        "trial_id": "balanced_without_long_penalty",
        "strategy": "balanced_signal",
        "label": "Cân bằng không trừ tín hiệu dài",
        "score_strategy": "balanced_no_long_penalty",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "balanced_without_long_penalty"},
    },
    {
        "trial_id": "audit_unclipped",
        "strategy": "audit_signal",
        "label": "Audit không chặn z-score",
        "score_strategy": "audit_unclipped",
        "variant_role": "registered_parameter_variant",
        "parameters": {"score_policy": "audit_unclipped_z_scores"},
    },
)
BACKTEST_WINDOW_SENSITIVITY_WINDOWS = (50, 200, 500)
BACKTEST_WINDOW_SENSITIVITY_STRATEGIES = (
    {
        "strategy": "balanced_signal",
        "score_strategy": "balanced",
        "label": "Kết hợp ba dấu hiệu",
    },
    {
        "strategy": "recent_frequency",
        "score_strategy": "recent",
        "label": "Tần suất cửa sổ gần",
    },
    {
        "strategy": "audit_signal",
        "score_strategy": "audit",
        "label": "Tín hiệu kiểm định công bằng",
    },
)
BACKTEST_BLOCK_BOOTSTRAP_RESAMPLES = 199
BACKTEST_BLOCK_BOOTSTRAP_MIN_VALUES = 30
BACKTEST_BLOCK_BOOTSTRAP_MAX_VALUES = 2500
BACKTEST_BLOCK_BOOTSTRAP_CONFIDENCE_LEVEL = 0.95
BACKTEST_COMMON_REJECTED_CONFIGURATIONS = (
    {
        "config_id": "posthoc_best_variant_picker",
        "label": "Chọn biến thể thắng nhất sau khi xem phase cuối",
        "strategy_family": "model_selection",
        "reason_code": "would_use_final_evaluation_feedback",
        "reason": (
            "Cấu hình này bị loại vì dùng kết quả phase đánh giá cuối để chọn mô "
            "hình, làm rò rỉ dữ liệu vào quyết định công bố."
        ),
        "parameters": {"selection_rule": "pick_best_final_phase_p_value"},
    },
    {
        "config_id": "prize_weighted_score",
        "label": "Điểm theo trọng số giải thưởng",
        "strategy_family": "score_target",
        "reason_code": "incomplete_prize_coverage",
        "reason": (
            "Dữ liệu giải thưởng theo kỳ chưa phủ đủ mọi sản phẩm, đặc biệt Keno "
            "và Bingo18, nên không dùng làm mục tiêu backtest toàn hệ thống."
        ),
        "parameters": {"score_basis": "prize_tier_weighted_hit"},
    },
    {
        "config_id": "future_centered_window",
        "label": "Cửa sổ tần suất đặt giữa quanh kỳ đích",
        "strategy_family": "data_window",
        "reason_code": "would_include_future_draws",
        "reason": (
            "Cửa sổ đặt giữa quanh kỳ đích cần dữ liệu sau kỳ đang dự đoán, trái "
            "quy tắc walk-forward chỉ dùng lịch sử trước kỳ t."
        ),
        "parameters": {"window_policy": "centered_on_target_draw"},
    },
)
BACKTEST_NUMBER_REJECTED_CONFIGURATIONS = (
    {
        "config_id": "special_number_scoring",
        "label": "Tính điểm số đặc biệt chung với số chính",
        "strategy_family": "score_target",
        "reason_code": "not_comparable_across_number_products",
        "reason": (
            "Số đặc biệt không tồn tại ở mọi sản phẩm tập số và có miền giá trị "
            "khác số chính, nên bị loại khỏi điểm backtest chính."
        ),
        "parameters": {"score_basis": "main_and_special_numbers_combined"},
    },
)
BACKTEST_DIGIT_REJECTED_CONFIGURATIONS = (
    {
        "config_id": "tier_weighted_digit_score",
        "label": "Trọng số chuỗi chữ số theo hạng giải",
        "strategy_family": "score_target",
        "reason_code": "tier_breakdown_is_explanatory_not_selection_target",
        "reason": (
            "Phân rã theo hạng giải chỉ là metadata giải thích audit; dùng làm "
            "mục tiêu chọn mô hình sẽ tạo thêm mục tiêu hậu nghiệm."
        ),
        "parameters": {"score_basis": "tier_weighted_digit_outcomes"},
    },
)


@dataclass(slots=True)
class PredictionLedger:
    path: Path
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> PredictionLedger:
        events: list[dict[str, Any]] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError as error:
                        raise ValueError(f"Invalid prediction ledger line {line_number}") from error
        ledger = cls(path=path, events=events)
        ledger.validate_integrity()
        return ledger

    def process_product(self, dataset: ProductDataset) -> None:
        predictions = {
            event["prediction_id"]: event
            for event in self.events
            if event.get("event_type") == "prediction"
            and event.get("product") == dataset.product.slug
        }
        evaluated = {
            event["prediction_id"]
            for event in self.events
            if event.get("event_type") == "evaluation"
        }
        for prediction_id, prediction in predictions.items():
            if prediction_id in evaluated:
                continue
            actual = _first_observation_after(dataset.observations, prediction)
            if actual is not None:
                self.events.append(_evaluation_event(prediction, actual, dataset))
                evaluated.add(prediction_id)

        if not dataset.product.active:
            return
        latest = dataset.latest
        existing_keys = {
            (
                event.get("product"),
                event.get("dataset_cutoff_draw_id"),
                event.get("strategy"),
                event.get("model_version"),
            )
            for event in predictions.values()
        }
        for forecast in _forecast_events(dataset):
            key = (
                dataset.product.slug,
                latest.draw_id,
                forecast["strategy"],
                MODEL_VERSION,
            )
            if key not in existing_keys:
                self.events.append(forecast)

    def save(self) -> None:
        self._seal_events()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            for event in self.events:
                handle.write(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
        temp_path.replace(self.path)

    def validate_integrity(self) -> dict[str, object]:
        if not self.events:
            return _ledger_integrity_payload(self.events, "empty")
        has_chain = [
            all(key in event for key in ("ledger_index", "previous_event_hash", "event_hash"))
            for event in self.events
        ]
        if not any(has_chain):
            return _ledger_integrity_payload(self.events, "legacy_unsealed")
        if not all(has_chain):
            raise ValueError("Prediction ledger mixes sealed and unsealed historical events")
        previous_hash: str | None = None
        for index, event in enumerate(self.events):
            if event.get("ledger_index") != index:
                raise ValueError(f"Prediction ledger index mismatch at event {index}")
            if event.get("previous_event_hash") != previous_hash:
                raise ValueError(f"Prediction ledger chain break at event {index}")
            expected_hash = _event_hash(event)
            if event.get("event_hash") != expected_hash:
                raise ValueError(f"Prediction ledger hash mismatch at event {index}")
            previous_hash = expected_hash
        return _ledger_integrity_payload(self.events, "valid")

    def _seal_events(self) -> None:
        previous_hash: str | None = None
        for index, event in enumerate(self.events):
            event["ledger_index"] = index
            event["previous_event_hash"] = previous_hash
            event["event_hash"] = _event_hash(event)
            previous_hash = str(event["event_hash"])

    def site_report(self) -> dict[str, object]:
        predictions = [
            event for event in self.events if event.get("event_type") == "prediction"
        ]
        evaluations = [
            event for event in self.events if event.get("event_type") == "evaluation"
        ]
        predictions_by_id = {
            prediction["prediction_id"]: prediction for prediction in predictions
        }
        evaluation_details = [
            _evaluation_detail(predictions_by_id[evaluation["prediction_id"]], evaluation)
            for evaluation in evaluations
            if evaluation["prediction_id"] in predictions_by_id
        ]
        evaluated_ids = {event["prediction_id"] for event in evaluations}
        latest_by_product: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for prediction in predictions:
            product = prediction["product"]
            strategy = prediction["strategy"]
            old = latest_by_product[product].get(strategy)
            if old is None or _prediction_order(prediction) > _prediction_order(old):
                latest_by_product[product][strategy] = prediction

        performance: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        product_performance: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for evaluation in evaluation_details:
            performance[(evaluation["product"], evaluation["strategy"])].append(evaluation)
            product_performance[evaluation["product"]].append(evaluation)

        performance_rows = []
        for (product, strategy), rows in sorted(performance.items()):
            exact_hits = sum(row["outcome"]["status"] == "exact" for row in rows)
            near_hits = sum(row["outcome"]["status"] == "near" for row in rows)
            expected_exact = _expected_outcome_count(rows, "exact")
            expected_near = _expected_outcome_count(rows, "near")
            partial_matches = sum(bool(row["outcome"]["has_partial_match"]) for row in rows)
            hit_counts = [
                int(row["metrics"]["hit_count"])
                for row in rows
                if "hit_count" in row["metrics"]
            ]
            best_position = [
                int(row["metrics"]["best_position_matches"])
                for row in rows
                if "best_position_matches" in row["metrics"]
            ]
            performance_rows.append(
                {
                    "product": product,
                    "strategy": strategy,
                    "evaluations": len(rows),
                    "exact_hits": exact_hits,
                    "exact_hit_rate": _round(exact_hits / len(rows)),
                    "near_hits": near_hits,
                    "wrong": len(rows) - exact_hits - near_hits,
                    "expected_exact_by_chance": _significant(expected_exact),
                    "expected_near_by_chance": _significant(expected_near),
                    "near_excess_vs_chance": _significant(near_hits - expected_near),
                    "partial_matches": partial_matches,
                    "average_hits": _round(fmean(hit_counts)) if hit_counts else None,
                    "average_best_position_matches": (
                        _round(fmean(best_position)) if best_position else None
                    ),
                    "score_distribution": _score_distribution(rows),
                }
            )

        product_outcomes = {}
        for product, rows in sorted(product_performance.items()):
            product_exact = sum(row["outcome"]["status"] == "exact" for row in rows)
            product_near = sum(row["outcome"]["status"] == "near" for row in rows)
            expected_exact = _expected_outcome_count(rows, "exact")
            expected_near = _expected_outcome_count(rows, "near")
            product_partial = sum(
                bool(row["outcome"]["has_partial_match"]) for row in rows
            )
            product_zero = sum(
                int(row["outcome"]["matched_units"]) == 0 for row in rows
            )
            product_draws = {
                (row["actual_draw_date"], row["actual_draw_id"]) for row in rows
            }
            product_outcomes[product] = {
                "evaluated_draws": len(product_draws),
                "evaluated_predictions": len(rows),
                "exact": product_exact,
                "near": product_near,
                "wrong": len(rows) - product_exact - product_near,
                "expected_exact_by_chance": _significant(expected_exact),
                "expected_near_by_chance": _significant(expected_near),
                "near_excess_vs_chance": _significant(product_near - expected_near),
                "partial_matches": product_partial,
                "zero_matches": product_zero,
                "score_kind": rows[0]["outcome"]["score_kind"],
                "score_distribution": _score_distribution(rows),
            }

        pending = [
            prediction for prediction in predictions if prediction["prediction_id"] not in evaluated_ids
        ]
        exact_hits = sum(
            evaluation["outcome"]["status"] == "exact"
            for evaluation in evaluation_details
        )
        near_hits = sum(
            evaluation["outcome"]["status"] == "near"
            for evaluation in evaluation_details
        )
        expected_exact = _expected_outcome_count(evaluation_details, "exact")
        expected_near = _expected_outcome_count(evaluation_details, "near")
        partial_matches = sum(
            bool(evaluation["outcome"]["has_partial_match"])
            for evaluation in evaluation_details
        )
        zero_matches = sum(
            int(evaluation["outcome"]["matched_units"]) == 0
            for evaluation in evaluation_details
        )
        evaluated_draws = {
            (
                evaluation["product"],
                evaluation["actual_draw_date"],
                evaluation["actual_draw_id"],
            )
            for evaluation in evaluation_details
        }
        embedded_latest_count = sum(
            len(strategies) for strategies in latest_by_product.values()
        )
        pending_by_product = Counter(
            str(prediction["product"]) for prediction in pending
        )
        pending_predictions = [
            _pending_prediction_detail(prediction)
            for prediction in sorted(pending, key=_prediction_order, reverse=True)
        ]
        return {
            "schema_version": 2,
            "model_version": MODEL_VERSION,
            "ledger_integrity": self.validate_integrity(),
            "principle": (
                "Mọi dự đoán được ghi trước kết quả, giữ nguyên tham số và luôn so với "
                "baseline chọn đồng đều."
            ),
            "latest": {
                product: list(strategies.values())
                for product, strategies in sorted(latest_by_product.items())
            },
            "pending_count": len(pending),
            "embedded_pending_count": embedded_latest_count,
            "pending_by_product": dict(sorted(pending_by_product.items())),
            "pending_predictions": pending_predictions,
            "pending_embedding_note": (
                "latest chỉ nhúng dự đoán mới nhất của từng chiến lược để website gọn. "
                "pending_count đếm toàn bộ dự đoán chưa có kết quả trong ledger."
            ),
            "evaluation_count": len(evaluation_details),
            "outcome_summary": {
                "evaluated_draws": len(evaluated_draws),
                "evaluated_predictions": len(evaluation_details),
                "exact": exact_hits,
                "near": near_hits,
                "wrong": len(evaluation_details) - exact_hits - near_hits,
                "expected_exact_by_chance": _significant(expected_exact),
                "expected_near_by_chance": _significant(expected_near),
                "near_excess_vs_chance": _significant(near_hits - expected_near),
                "partial_matches": partial_matches,
                "zero_matches": zero_matches,
                "near_rule": (
                    "Gần đúng chỉ khi thiếu đúng một số hoặc một vị trí so với kết quả "
                    "đầy đủ. Trùng ít hơn vẫn được ghi số lượng nhưng tính là sai."
                ),
            },
            "product_outcomes": product_outcomes,
            "performance": performance_rows,
            "archived_evaluations": sorted(
                evaluation_details,
                key=_evaluation_order,
                reverse=True,
            ),
            "history_limit_per_product": 100,
            "recent_evaluations": [
                row
                for product in sorted(product_performance)
                for row in product_performance[product][-100:][::-1]
            ],
        }


def build_backtest_report(dataset: ProductDataset) -> dict[str, object]:
    if dataset.product.kind is AnalysisKind.NUMBER_SET:
        return _number_backtest(dataset)
    return _digit_backtest(dataset)


def finalize_backtests(product_reports: list[dict[str, Any]]) -> dict[str, Any]:
    correction_trials: list[tuple[dict[str, Any], str, dict[str, Any] | None]] = []
    published_comparison_count = 0
    target_scopes: list[dict[str, Any]] = []
    phase_splits: list[dict[str, Any]] = []
    trial_registries: list[dict[str, Any]] = []
    window_sensitivity_reports: list[dict[str, Any]] = []
    block_bootstrap_reports: list[dict[str, Any]] = []
    completed_backtests: list[tuple[str, dict[str, Any]]] = []
    completed_products = 0
    for report in product_reports:
        backtest = report.get("backtest")
        if not isinstance(backtest, dict) or backtest.get("status") != "complete":
            continue
        _validate_backtest_target_scope(backtest)
        _validate_backtest_phase_split(backtest)
        _validate_backtest_multiple_testing_trials(backtest)
        if "window_sensitivity" in backtest:
            _validate_backtest_window_sensitivity(backtest)
        if _backtest_has_block_bootstrap_checks(backtest):
            _validate_backtest_block_bootstrap_checks(backtest)
        _validate_backtest_trial_disposition_log(backtest)
        completed_products += 1
        product_slug = str(report["product"]["slug"])
        completed_backtests.append((product_slug, backtest))
        target_scope = backtest.get("target_scope")
        if isinstance(target_scope, dict):
            target_scopes.append(
                {
                    "product": product_slug,
                    "scope_id": target_scope.get("scope_id"),
                    "target_draw_count": target_scope.get("target_draw_count"),
                    "first_target_draw_id": target_scope.get("first_target_draw_id"),
                    "latest_target_draw_id": target_scope.get("latest_target_draw_id"),
                    "target_draw_ids_sha256": target_scope.get(
                        "target_draw_ids_sha256"
                    ),
                }
            )
        registry = backtest.get("multiple_testing_trials", {})
        trials = registry.get("trials", []) if isinstance(registry, dict) else []
        trial_registries.append(
            {
                "product": product_slug,
                "method": registry.get("method") if isinstance(registry, dict) else None,
                "trial_count": registry.get("trial_count") if isinstance(registry, dict) else None,
                "published_trial_count": registry.get("published_trial_count")
                if isinstance(registry, dict)
                else None,
                "registered_parameter_variant_count": registry.get(
                    "registered_parameter_variant_count"
                )
                if isinstance(registry, dict)
                else None,
                "target_scope_id": target_scope.get("scope_id")
                if isinstance(target_scope, dict)
                else None,
            }
        )
        for trial in trials:
            if not isinstance(trial, dict) or not isinstance(
                trial.get("approximate_p_value"),
                (int, float),
            ):
                continue
            comparison = None
            if trial.get("published"):
                key = str(trial.get("published_comparison_key"))
                comparison = backtest.get(key)
                if isinstance(comparison, dict):
                    published_comparison_count += 1
                else:
                    comparison = None
            correction_trials.append((trial, product_slug, comparison))
        sensitivity = backtest.get("window_sensitivity", {})
        if isinstance(sensitivity, dict):
            window_sensitivity_reports.append(
                {
                    "product": product_slug,
                    "method": sensitivity.get("method"),
                    "registered_window_draws": sensitivity.get(
                        "registered_window_draws"
                    ),
                    "primary_recent_window_draws": sensitivity.get(
                        "primary_recent_window_draws"
                    ),
                    "trial_count": sensitivity.get("trial_count"),
                    "primary_trial_count": sensitivity.get("primary_trial_count"),
                    "alternative_window_trial_count": sensitivity.get(
                        "alternative_window_trial_count"
                    ),
                    "included_trial_count": sensitivity.get("included_trial_count"),
                    "target_scope_id": target_scope.get("scope_id")
                    if isinstance(target_scope, dict)
                    else None,
                }
            )
        if _backtest_has_block_bootstrap_checks(backtest):
            block_bootstrap_reports.append(
                _backtest_block_bootstrap_summary(product_slug, backtest)
            )
        phase_split = backtest.get("phase_split")
        if isinstance(phase_split, dict):
            selection_phase = phase_split.get("selection_phase", {})
            final_phase = phase_split.get("final_evaluation_phase", {})
            phase_splits.append(
                {
                    "product": product_slug,
                    "method": phase_split.get("method"),
                    "walk_forward_target_draw_count": phase_split.get(
                        "walk_forward_target_draw_count"
                    ),
                    "selection_draw_count": selection_phase.get("draw_count"),
                    "final_evaluation_draw_count": final_phase.get("draw_count"),
                    "selection_scope_id": selection_phase.get("scope_id"),
                    "final_evaluation_scope_id": final_phase.get("scope_id"),
                    "target_scope_id": target_scope.get("scope_id")
                    if isinstance(target_scope, dict)
                    else None,
                    "formulas_frozen_before_final_evaluation": phase_split.get(
                        "formulas_frozen_before_final_evaluation"
                    ),
                }
            )

    q_values = _benjamini_hochberg(
        [float(trial["approximate_p_value"]) for trial, _, _ in correction_trials]
    )
    adjusted_wins = 0
    unadjusted_wins = 0
    products_with_adjusted_win: set[str] = set()
    products_with_unadjusted_win: set[str] = set()
    for (trial, product_slug, comparison), q_value in zip(
        correction_trials,
        q_values,
        strict=True,
    ):
        difference = _comparison_difference(trial)
        unadjusted = difference > 0 and float(trial["approximate_p_value"]) < 0.05
        adjusted = difference > 0 and q_value < BACKTEST_MULTIPLE_TESTING_ALPHA
        trial["q_value_global_bh"] = _round(q_value, 8)
        trial["beats_baseline_unadjusted"] = unadjusted
        trial["beats_baseline"] = adjusted
        trial["multiple_testing_method"] = "benjamini_hochberg"
        trial["multiple_testing_scope"] = len(correction_trials)
        if comparison is not None:
            comparison["q_value_global_bh"] = _round(q_value, 8)
            comparison["beats_baseline_unadjusted"] = unadjusted
            comparison["beats_baseline"] = adjusted
            comparison["multiple_testing_method"] = "benjamini_hochberg"
            comparison["multiple_testing_scope"] = len(correction_trials)
            comparison["multiple_testing_trial_id"] = trial.get("trial_id")
            unadjusted_wins += int(unadjusted)
            adjusted_wins += int(adjusted)
            if unadjusted:
                products_with_unadjusted_win.add(product_slug)
            if adjusted:
                products_with_adjusted_win.add(product_slug)

    trial_disposition_summaries = []
    for product_slug, backtest in completed_backtests:
        registry = backtest.get("multiple_testing_trials", {})
        log = backtest.get("trial_disposition_log", {})
        if isinstance(registry, dict) and isinstance(log, dict):
            trials = registry.get("trials", [])
            if isinstance(trials, list):
                _sync_backtest_trial_disposition_log(log, trials)
                _validate_backtest_trial_disposition_log(backtest)
                trial_disposition_summaries.append(
                    {
                        "product": product_slug,
                        "included_trial_count": log.get("included_trial_count"),
                        "failed_trial_count": log.get("failed_trial_count"),
                        "raw_unadjusted_winning_trial_count": log.get(
                            "raw_unadjusted_winning_trial_count"
                        ),
                        "adjusted_winning_trial_count": log.get(
                            "adjusted_winning_trial_count"
                        ),
                        "rejected_configuration_count": log.get(
                            "rejected_configuration_count"
                        ),
                        "retained_record_count": log.get("retained_record_count"),
                    }
                )

    return {
        "schema_version": 1,
        "comparison_count": published_comparison_count,
        "correction_trial_count": len(correction_trials),
        "product_count": completed_products,
        "multiple_testing_method": "benjamini_hochberg",
        "multiple_testing_scope_policy": (
            "published_final_models_plus_registered_parameter_variants"
        ),
        "alpha": BACKTEST_MULTIPLE_TESTING_ALPHA,
        "adjusted_winning_comparisons": adjusted_wins,
        "unadjusted_winning_comparisons": unadjusted_wins,
        "products_with_adjusted_win": sorted(products_with_adjusted_win),
        "products_with_unadjusted_win": sorted(products_with_unadjusted_win),
        "multiple_testing_registry_validation": {
            "status": "validated",
            "method": "published_and_registered_trial_registry",
            "product_count": len(trial_registries),
            "published_comparison_count": published_comparison_count,
            "correction_trial_count": len(correction_trials),
            "products": trial_registries,
            "interpretation": (
                "Benjamini-Hochberg dùng toàn bộ trial đã đăng ký trong "
                "multiple_testing_trials, gồm mô hình công bố và biến thể tham số."
            ),
        },
        "trial_disposition_validation": {
            "status": "validated",
            "method": "registered_trial_disposition_log",
            "product_count": len(trial_disposition_summaries),
            "included_trial_count": sum(
                int(row.get("included_trial_count") or 0)
                for row in trial_disposition_summaries
            ),
            "failed_trial_count": sum(
                int(row.get("failed_trial_count") or 0)
                for row in trial_disposition_summaries
            ),
            "raw_unadjusted_winning_trial_count": sum(
                int(row.get("raw_unadjusted_winning_trial_count") or 0)
                for row in trial_disposition_summaries
            ),
            "adjusted_winning_trial_count": sum(
                int(row.get("adjusted_winning_trial_count") or 0)
                for row in trial_disposition_summaries
            ),
            "rejected_configuration_count": sum(
                int(row.get("rejected_configuration_count") or 0)
                for row in trial_disposition_summaries
            ),
            "retained_record_count": sum(
                int(row.get("retained_record_count") or 0)
                for row in trial_disposition_summaries
            ),
            "products": trial_disposition_summaries,
            "interpretation": (
                "Mỗi sản phẩm lưu trial đã chạy nhưng không thắng sau hiệu chỉnh "
                "và các cấu hình bị loại trước phase đánh giá cuối."
            ),
        },
        "window_sensitivity_validation": {
            "status": "validated",
            "method": "registered_recent_window_sensitivity",
            "product_count": len(window_sensitivity_reports),
            "registered_window_draws": list(BACKTEST_WINDOW_SENSITIVITY_WINDOWS),
            "included_trial_count": sum(
                int(row.get("included_trial_count") or 0)
                for row in window_sensitivity_reports
            ),
            "primary_trial_count": sum(
                int(row.get("primary_trial_count") or 0)
                for row in window_sensitivity_reports
            ),
            "alternative_window_trial_count": sum(
                int(row.get("alternative_window_trial_count") or 0)
                for row in window_sensitivity_reports
            ),
            "products": window_sensitivity_reports,
            "interpretation": (
                "Mỗi report complete có ma trận chiến lược x cửa sổ 50/200/500. "
                "Các cửa sổ không phải mặc định được đăng ký như biến thể tham số "
                "và cùng đi vào hiệu chỉnh Benjamini-Hochberg."
            ),
        },
        "block_bootstrap_validation": {
            "status": "validated",
            "method": "moving_block_bootstrap",
            "product_count": len(block_bootstrap_reports),
            "resamples": BACKTEST_BLOCK_BOOTSTRAP_RESAMPLES,
            "confidence_level": BACKTEST_BLOCK_BOOTSTRAP_CONFIDENCE_LEVEL,
            "comparison_check_count": sum(
                int(row.get("comparison_check_count") or 0)
                for row in block_bootstrap_reports
            ),
            "trial_check_count": sum(
                int(row.get("trial_check_count") or 0)
                for row in block_bootstrap_reports
            ),
            "available_check_count": sum(
                int(row.get("available_check_count") or 0)
                for row in block_bootstrap_reports
            ),
            "normal_overlap_count": sum(
                int(row.get("normal_overlap_count") or 0)
                for row in block_bootstrap_reports
            ),
            "products": block_bootstrap_reports,
            "interpretation": (
                "Block bootstrap resample các kỳ liên tiếp để so sánh khoảng bất "
                "định với xấp xỉ chuẩn hiện tại; chẩn đoán này không đổi p/q/status."
            ),
        },
        "target_scope_validation": {
            "status": "validated",
            "method": "shared_target_scope_id_per_product",
            "product_count": len(target_scopes),
            "strategy_keys": list(BACKTEST_SCOPE_STRATEGIES),
            "products": target_scopes,
            "interpretation": (
                "Mỗi sản phẩm dùng cùng target_scope_id cho baseline, ba chiến lược "
                "và ba phép so sánh ghép cặp."
            ),
        },
        "phase_split_validation": {
            "status": "validated",
            "method": "chronological_formula_selection_then_final_evaluation",
            "product_count": len(phase_splits),
            "selection_fraction": BACKTEST_SELECTION_PHASE_FRACTION,
            "products": phase_splits,
            "interpretation": (
                "Mỗi backtest tách phase chọn/khóa công thức khỏi phase đánh giá cuối; "
                "target_scope công bố trùng phase đánh giá cuối."
            ),
        },
        "interpretation": (
            "Chỉ nhãn đã hiệu chỉnh mới được dùng cho kết luận tổng quan. "
            "Nhãn chưa hiệu chỉnh được giữ lại để kiểm tra độ nhạy."
        ),
    }


def _backtest_target_scope(
    product: AnalyticsProduct,
    targets: list[Observation],
) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(f"{product.slug}|{len(targets)}\n".encode())
    for target in targets:
        digest.update(f"{target.draw_id}|{target.draw_date.isoformat()}\n".encode())
    target_ids = [target.draw_id for target in targets]
    target_dates = [target.draw_date.isoformat() for target in targets]
    target_hash = digest.hexdigest()
    return {
        "schema_version": 1,
        "scope_id": target_hash[:24],
        "method": "same_confirmed_draw_targets_for_all_strategies",
        "target": "walk_forward_confirmed_draws",
        "target_draw_count": len(targets),
        "first_target_draw_id": target_ids[0],
        "latest_target_draw_id": target_ids[-1],
        "first_target_draw_date": target_dates[0],
        "latest_target_draw_date": target_dates[-1],
        "target_draw_ids_sha256": target_hash,
        "sample_target_draw_ids": {
            "first": target_ids[:5],
            "last": target_ids[-5:],
        },
        "shared_by": list(BACKTEST_SCOPE_STRATEGIES),
        "no_strategy_specific_filtering": True,
    }


def _backtest_phase_split(
    product: AnalyticsProduct,
    targets: list[Observation],
) -> dict[str, Any]:
    selection_count = 0
    if len(targets) > 1:
        selection_count = min(
            len(targets) - 1,
            max(1, int(len(targets) * BACKTEST_SELECTION_PHASE_FRACTION)),
        )
    selection_targets = targets[:selection_count]
    final_targets = targets[selection_count:]
    return {
        "schema_version": 1,
        "method": "chronological_formula_selection_then_final_evaluation",
        "walk_forward_target_draw_count": len(targets),
        "selection_fraction": BACKTEST_SELECTION_PHASE_FRACTION,
        "formulas_frozen_before_final_evaluation": True,
        "selection_result_used_to_choose_formulas": False,
        "final_evaluation_feedback_used_for_model_selection": False,
        "shared_by": list(BACKTEST_SCOPE_STRATEGIES),
        "selection_phase": _backtest_phase_scope(
            product,
            "formula_selection",
            selection_targets,
        ),
        "final_evaluation_phase": _backtest_phase_scope(
            product,
            "final_evaluation",
            final_targets,
        ),
        "interpretation": (
            "Nửa đầu cửa sổ walk-forward chỉ dùng để khóa/công bố công thức và kiểm tra "
            "chẩn đoán. Kết luận backtest công bố chỉ dùng phase đánh giá cuối."
        ),
    }


def _backtest_phase_scope(
    product: AnalyticsProduct,
    phase: str,
    targets: list[Observation],
) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(f"{product.slug}|{len(targets)}\n".encode())
    for target in targets:
        digest.update(f"{target.draw_id}|{target.draw_date.isoformat()}\n".encode())
    target_ids = [target.draw_id for target in targets]
    target_dates = [target.draw_date.isoformat() for target in targets]
    target_hash = digest.hexdigest()
    return {
        "phase": phase,
        "scope_id": target_hash[:24],
        "draw_count": len(targets),
        "first_draw_id": target_ids[0] if target_ids else None,
        "latest_draw_id": target_ids[-1] if target_ids else None,
        "first_draw_date": target_dates[0] if target_dates else None,
        "latest_draw_date": target_dates[-1] if target_dates else None,
        "draw_ids_sha256": target_hash,
        "sample_draw_ids": {
            "first": target_ids[:5],
            "last": target_ids[-5:],
        },
    }


def _target_scope_fields(target_scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_scope_id": target_scope["scope_id"],
        "target_draw_count": target_scope["target_draw_count"],
    }


def _backtest_trial_row(
    *,
    trial_id: str,
    strategy: str,
    label: str,
    variant_role: str,
    differences: list[float],
    metric_key: str,
    scope_fields: dict[str, Any],
    parameters: dict[str, Any],
    published_comparison_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    z_score, p_value = _paired_normal_test(differences)
    normal_interval = _normal_mean_interval(differences)
    row = {
        "trial_id": trial_id,
        "strategy": strategy,
        "label": label,
        "variant_role": variant_role,
        "published": published_comparison_key is not None,
        "published_comparison_key": published_comparison_key,
        **scope_fields,
        metric_key: _round(fmean(differences)),
        "paired_z_score": _round(z_score),
        "approximate_p_value": _round(p_value, 8),
        **normal_interval,
        "block_bootstrap_check": _backtest_block_bootstrap_check(
            test_id=trial_id,
            differences=differences,
            metric_key=metric_key,
        ),
        "parameters": parameters,
    }
    if metadata:
        row.update(metadata)
    return row


def _backtest_trial_disposition_log(
    product_kind: str,
    metric_key: str,
    trials: list[dict[str, Any]],
    scope_fields: dict[str, Any],
) -> dict[str, Any]:
    log = {
        "schema_version": 1,
        "method": "registered_trial_disposition_log",
        "retention_policy": "record_included_failed_and_rejected_backtest_configs",
        "product_kind": product_kind,
        "comparison_metric": metric_key,
        "scope_policy": (
            "all_recorded_trials_share_final_evaluation_scope; rejected_configs_are_not_evaluated"
        ),
        "rejected_configurations": _backtest_rejected_configurations(
            product_kind,
            scope_fields,
        ),
    }
    _sync_backtest_trial_disposition_log(log, trials)
    return log


def _backtest_rejected_configurations(
    product_kind: str,
    scope_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    templates = [*BACKTEST_COMMON_REJECTED_CONFIGURATIONS]
    if product_kind == "number_set":
        templates.extend(BACKTEST_NUMBER_REJECTED_CONFIGURATIONS)
    else:
        templates.extend(BACKTEST_DIGIT_REJECTED_CONFIGURATIONS)
    rows = []
    for template in templates:
        rows.append(
            {
                "config_id": f"{product_kind}:{template['config_id']}",
                "label": template["label"],
                "strategy_family": template["strategy_family"],
                "disposition": "rejected_before_final_evaluation",
                "reason_code": template["reason_code"],
                "reason": template["reason"],
                **scope_fields,
                "included_in_multiple_testing": False,
                "evaluated_on_final_scope": False,
                "published": False,
                "parameters": dict(template["parameters"]),
            }
        )
    return rows


def _sync_backtest_trial_disposition_log(
    log: dict[str, Any],
    trials: list[dict[str, Any]],
) -> None:
    included_trials = [
        _backtest_trial_disposition_row(trial)
        for trial in trials
    ]
    adjusted_wins = sum(
        row["result_status"] == "adjusted_win"
        for row in included_trials
    )
    raw_wins = sum(
        row["result_status"] in {
            "adjusted_win",
            "raw_win_failed_global_correction",
            "raw_win_pending_global_correction",
        }
        for row in included_trials
    )
    rejected_configurations = log.get("rejected_configurations", [])
    log.update(
        {
            "included_trial_count": len(included_trials),
            "published_trial_count": sum(
                bool(row["published"]) for row in included_trials
            ),
            "registered_parameter_variant_count": sum(
                row["variant_role"] == "registered_parameter_variant"
                for row in included_trials
            ),
            "raw_unadjusted_winning_trial_count": raw_wins,
            "adjusted_winning_trial_count": adjusted_wins,
            "failed_trial_count": len(included_trials) - adjusted_wins,
            "rejected_configuration_count": len(rejected_configurations),
            "retained_record_count": len(included_trials)
            + len(rejected_configurations),
            "included_trials": included_trials,
        }
    )


def _backtest_trial_disposition_row(trial: dict[str, Any]) -> dict[str, Any]:
    metric_key = "mean_hit_difference"
    if "mean_position_match_difference" in trial:
        metric_key = "mean_position_match_difference"
    parameters = trial.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    row = {
        "trial_id": trial.get("trial_id"),
        "strategy": trial.get("strategy"),
        "label": trial.get("label"),
        "variant_role": trial.get("variant_role"),
        "published": bool(trial.get("published")),
        "published_comparison_key": trial.get("published_comparison_key"),
        "included_in_multiple_testing": True,
        "evaluated_on_final_scope": True,
        "target_scope_id": trial.get("target_scope_id"),
        "target_draw_count": trial.get("target_draw_count"),
        "comparison_metric": metric_key,
        metric_key: trial.get(metric_key),
        "approximate_p_value": trial.get("approximate_p_value"),
        "q_value_global_bh": trial.get("q_value_global_bh"),
        "effect_direction": (
            "positive"
            if _comparison_difference(trial) > 0
            else "non_positive"
        ),
        "result_status": _backtest_trial_result_status(trial),
        "parameters_sha256": _stable_json_sha256(parameters),
        "parameters": parameters,
    }
    for key in (
        "diagnostic_group",
        "sensitivity_dimension",
        "parameter_value",
        "primary_parameter_value",
        "window_sensitivity_role",
    ):
        if key in trial:
            row[key] = trial[key]
    return row


def _backtest_trial_result_status(trial: dict[str, Any]) -> str:
    if _comparison_difference(trial) <= 0:
        return "failed_non_positive_effect"
    if trial.get("beats_baseline") is True:
        return "adjusted_win"
    if trial.get("beats_baseline_unadjusted") is True:
        if "q_value_global_bh" in trial:
            return "raw_win_failed_global_correction"
        return "raw_win_pending_global_correction"
    return "failed_raw_baseline_test"


def _stable_json_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _backtest_multiple_testing_trials(
    product_kind: str,
    metric_key: str,
    trials: list[dict[str, Any]],
) -> dict[str, Any]:
    published_count = sum(bool(row.get("published")) for row in trials)
    return {
        "schema_version": 1,
        "method": "benjamini_hochberg_over_published_and_registered_trials",
        "scope_policy": (
            "published_final_models_plus_registered_parameter_variants"
        ),
        "product_kind": product_kind,
        "comparison_metric": metric_key,
        "trial_count": len(trials),
        "published_trial_count": published_count,
        "registered_parameter_variant_count": len(trials) - published_count,
        "trials": trials,
    }


def _backtest_window_sensitivity(
    product_kind: str,
    metric_key: str,
    primary_recent_window: int,
    trials: list[dict[str, Any]],
) -> dict[str, Any]:
    primary_trials = [
        row
        for row in trials
        if row.get("parameter_value") == primary_recent_window
    ]
    alternative_trials = [
        row
        for row in trials
        if row.get("parameter_value") != primary_recent_window
    ]
    return {
        "schema_version": 1,
        "method": "registered_recent_window_sensitivity",
        "product_kind": product_kind,
        "comparison_metric": metric_key,
        "sensitivity_dimension": "recent_window_draws",
        "registered_window_draws": list(BACKTEST_WINDOW_SENSITIVITY_WINDOWS),
        "primary_recent_window_draws": primary_recent_window,
        "trial_count": len(trials),
        "included_trial_count": len(trials),
        "primary_trial_count": len(primary_trials),
        "alternative_window_trial_count": len(alternative_trials),
        "trials": trials,
        "interpretation": (
            "Mỗi chiến lược công bố được chạy lại trên các cửa sổ gần 50, 200 và "
            "500 kỳ. Cửa sổ mặc định vẫn là trial công bố; cửa sổ còn lại là biến "
            "thể đã đăng ký và được đưa vào hiệu chỉnh nhiều phép thử."
        ),
    }


def _backtest_window_trial_metadata(
    *,
    window: int,
    primary_recent_window: int,
) -> dict[str, Any]:
    return {
        "diagnostic_group": "recent_window_sensitivity",
        "sensitivity_dimension": "recent_window_draws",
        "parameter_value": window,
        "primary_parameter_value": primary_recent_window,
        "window_sensitivity_role": (
            "primary_published_window"
            if window == primary_recent_window
            else "registered_alternative_window"
        ),
    }


def _backtest_window_trial_parameters(
    *,
    product_kind: str,
    strategy: str,
    recent_window: int,
    primary_recent_window: int,
    short_window: int,
    pair_window: int | None = None,
) -> dict[str, Any]:
    if product_kind == "number_set":
        policy_by_strategy = {
            "balanced_signal": NUMBER_SCORE_POLICY,
            "recent_frequency": "0.6*short_z+0.4*recent_z",
            "audit_signal": AUDIT_NUMBER_SCORE_POLICY,
        }
    else:
        policy_by_strategy = {
            "balanced_signal": DIGIT_SCORE_POLICY,
            "recent_frequency": "0.6*short_z+0.4*recent_z",
            "audit_signal": AUDIT_DIGIT_SCORE_POLICY,
        }
    parameters: dict[str, Any] = {
        "score_policy": policy_by_strategy[strategy],
        "recent_window_draws": recent_window,
        "primary_recent_window_draws": primary_recent_window,
        "short_window_draws": short_window,
        "registered_window_draws": list(BACKTEST_WINDOW_SENSITIVITY_WINDOWS),
    }
    if pair_window is not None:
        parameters["pair_window_draws"] = pair_window
    return parameters


def _number_backtest_score_formulas() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product_kind": "number_set",
        "score_unit": "main_number_hits_per_draw",
        "score_unit_label": "số chính trùng mỗi kỳ",
        "per_draw_score": (
            "hit_count_t = |predicted_main_numbers_t ∩ actual_main_numbers_t|"
        ),
        "comparison_metric": "mean_hit_difference",
        "comparison_difference": "d_t = hit_count_t - E_uniform(hit_count_t)",
        "baseline_method": "exact_hypergeometric_expectation",
        "special_numbers_policy": "special_numbers_not_scored_in_backtest",
        "variables": [
            {
                "name": "short_z",
                "definition": "z-score tần suất số trong cửa sổ ngắn 50 kỳ",
            },
            {
                "name": "recent_z",
                "definition": "z-score tần suất số trong cửa sổ gần",
            },
            {
                "name": "long_z",
                "definition": "z-score tần suất số trên toàn lịch sử trước kỳ t",
            },
            {
                "name": "overdue_ratio",
                "definition": "min(4, số kỳ vắng hiện tại * xác suất xuất hiện đều)",
            },
            {
                "name": "pair_pressure",
                "definition": (
                    "trung bình tối đa 5 z-score dương của các cặp đồng xuất hiện "
                    "liên quan đến số đang xét"
                ),
            },
            {
                "name": "selected_pair_bonus",
                "definition": (
                    "trung bình z-score cặp dương giữa số ứng viên và các số đã chọn "
                    "trong bước tham lam"
                ),
            },
        ],
        "strategies": [
            {
                "strategy": "balanced_signal",
                "label": "Kết hợp ba dấu hiệu",
                "formula": (
                    "0.40*short_z + 0.30*recent_z - 0.15*long_z "
                    "+ 0.15*(overdue_ratio - 1)"
                ),
                "selection_rule": "chọn pick_count số có điểm cao nhất",
            },
            {
                "strategy": "recent_frequency",
                "label": "Tần suất cửa sổ gần",
                "formula": "0.60*short_z + 0.40*recent_z",
                "selection_rule": "chọn pick_count số có điểm cao nhất",
            },
            {
                "strategy": "audit_signal",
                "label": "Tín hiệu kiểm định công bằng",
                "formula": (
                    "0.45*clip(long_z) + 0.25*clip(recent_z) "
                    "+ 0.15*clip(short_z) + 0.15*pair_pressure"
                ),
                "selection_rule": (
                    "chọn tham lam theo audit_score + 0.12*selected_pair_bonus"
                ),
            },
        ],
    }


def _digit_backtest_score_formulas() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product_kind": "digit_sequence",
        "score_unit": "best_position_matches_per_draw",
        "score_unit_label": "vị trí trùng tốt nhất mỗi kỳ",
        "per_draw_score": (
            "best_position_matches_t = max_actual sum_i 1[predicted_digit_i = actual_digit_i]"
        ),
        "comparison_metric": "mean_position_match_difference",
        "comparison_difference": (
            "d_t = best_position_matches_t - E_uniform(best_position_matches_t | actual outcomes_t)"
        ),
        "baseline_method": "exact_sequence_enumeration",
        "multi_outcome_policy": (
            "nếu một kỳ có nhiều kết quả công bố, điểm là số vị trí khớp cao nhất "
            "so với các kết quả đó"
        ),
        "variables": [
            {
                "name": "short_z",
                "definition": "z-score tần suất chữ số tại từng vị trí trong cửa sổ ngắn",
            },
            {
                "name": "recent_z",
                "definition": "z-score tần suất chữ số tại từng vị trí trong cửa sổ gần",
            },
            {
                "name": "long_z",
                "definition": "z-score tần suất chữ số tại từng vị trí trên lịch sử trước kỳ t",
            },
            {
                "name": "clip(x)",
                "definition": "giới hạn tín hiệu về khoảng [-4, 4] trước khi ghép điểm audit",
            },
        ],
        "strategies": [
            {
                "strategy": "balanced_signal",
                "label": "Kết hợp ba dấu hiệu",
                "formula": "0.40*short_z + 0.30*recent_z - 0.20*long_z",
                "selection_rule": "chọn chữ số có điểm cao nhất ở từng vị trí",
            },
            {
                "strategy": "recent_frequency",
                "label": "Tần suất cửa sổ gần",
                "formula": "0.60*short_z + 0.40*recent_z",
                "selection_rule": "chọn chữ số có điểm cao nhất ở từng vị trí",
            },
            {
                "strategy": "audit_signal",
                "label": "Tín hiệu kiểm định công bằng",
                "formula": (
                    "0.45*clip(long_z) + 0.35*clip(recent_z) + 0.20*clip(short_z)"
                ),
                "selection_rule": "chọn chữ số có điểm audit cao nhất ở từng vị trí",
            },
        ],
    }


def _validate_backtest_target_scope(backtest: dict[str, Any]) -> None:
    target_scope = backtest.get("target_scope")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    expected_scope_id = target_scope.get("scope_id")
    expected_count = target_scope.get("target_draw_count")
    for key in ("baseline", *BACKTEST_MODEL_KEYS, *BACKTEST_COMPARISON_KEYS):
        row = backtest.get(key)
        if not isinstance(row, dict):
            continue
        if row.get("target_scope_id") != expected_scope_id:
            raise ValueError(f"Backtest {key} target_scope_id mismatch")
        if row.get("target_draw_count") != expected_count:
            raise ValueError(f"Backtest {key} target_draw_count mismatch")


def _validate_backtest_phase_split(backtest: dict[str, Any]) -> None:
    phase_split = backtest.get("phase_split")
    target_scope = backtest.get("target_scope")
    if not isinstance(phase_split, dict):
        raise ValueError("Backtest phase_split missing")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    selection_phase = phase_split.get("selection_phase")
    final_phase = phase_split.get("final_evaluation_phase")
    if not isinstance(selection_phase, dict) or not isinstance(final_phase, dict):
        raise ValueError("Backtest phase_split phases missing")
    selection_count = int(selection_phase.get("draw_count", -1))
    final_count = int(final_phase.get("draw_count", -1))
    total_count = int(phase_split.get("walk_forward_target_draw_count", -1))
    if selection_count < 0 or final_count <= 0:
        raise ValueError("Backtest phase_split draw_count invalid")
    if selection_count + final_count != total_count:
        raise ValueError("Backtest phase_split draw_count mismatch")
    if final_phase.get("scope_id") != target_scope.get("scope_id"):
        raise ValueError("Backtest final phase target_scope_id mismatch")
    if final_count != target_scope.get("target_draw_count"):
        raise ValueError("Backtest final phase target_draw_count mismatch")
    if backtest.get("samples") != final_count:
        raise ValueError("Backtest samples final phase mismatch")
    if phase_split.get("formulas_frozen_before_final_evaluation") is not True:
        raise ValueError("Backtest formulas must be frozen before final evaluation")
    if phase_split.get("selection_result_used_to_choose_formulas") is not False:
        raise ValueError("Backtest selection phase must not choose final formulas")
    if phase_split.get("final_evaluation_feedback_used_for_model_selection") is not False:
        raise ValueError("Backtest final evaluation feedback must not choose formulas")


def _validate_backtest_multiple_testing_trials(backtest: dict[str, Any]) -> None:
    registry = backtest.get("multiple_testing_trials")
    target_scope = backtest.get("target_scope")
    if not isinstance(registry, dict):
        raise ValueError("Backtest multiple_testing_trials missing")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    trials = registry.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("Backtest multiple_testing_trials empty")
    if registry.get("trial_count") != len(trials):
        raise ValueError("Backtest multiple_testing_trials trial_count mismatch")
    expected_scope_id = target_scope.get("scope_id")
    expected_count = target_scope.get("target_draw_count")
    published_keys: set[str] = set()
    for trial in trials:
        if not isinstance(trial, dict):
            raise ValueError("Backtest multiple_testing_trials row invalid")
        if trial.get("target_scope_id") != expected_scope_id:
            raise ValueError("Backtest multiple_testing_trials target_scope_id mismatch")
        if trial.get("target_draw_count") != expected_count:
            raise ValueError("Backtest multiple_testing_trials target_draw_count mismatch")
        if not isinstance(trial.get("approximate_p_value"), (int, float)):
            raise ValueError("Backtest multiple_testing_trials p-value missing")
        if trial.get("published"):
            key = trial.get("published_comparison_key")
            if not isinstance(key, str) or not isinstance(backtest.get(key), dict):
                raise ValueError("Backtest multiple_testing_trials published key invalid")
            published_keys.add(key)
            if backtest[key].get("approximate_p_value") != trial.get("approximate_p_value"):
                raise ValueError("Backtest multiple_testing_trials published p-value mismatch")
    present_comparison_keys = {
        key for key in BACKTEST_COMPARISON_KEYS if isinstance(backtest.get(key), dict)
    }
    if not present_comparison_keys.issubset(published_keys):
        raise ValueError("Backtest multiple_testing_trials missing published comparison")
    if registry.get("published_trial_count") != len(published_keys):
        raise ValueError("Backtest multiple_testing_trials published count mismatch")
    if registry.get("registered_parameter_variant_count") != len(trials) - len(published_keys):
        raise ValueError("Backtest multiple_testing_trials variant count mismatch")


def _validate_backtest_window_sensitivity(backtest: dict[str, Any]) -> None:
    sensitivity = backtest.get("window_sensitivity")
    registry = backtest.get("multiple_testing_trials")
    target_scope = backtest.get("target_scope")
    if not isinstance(sensitivity, dict):
        raise ValueError("Backtest window_sensitivity missing")
    if not isinstance(registry, dict):
        raise ValueError("Backtest multiple_testing_trials missing")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    trials = sensitivity.get("trials")
    registry_trials = registry.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("Backtest window_sensitivity trials missing")
    if not isinstance(registry_trials, list):
        raise ValueError("Backtest multiple_testing_trials trials missing")
    if sensitivity.get("registered_window_draws") != list(
        BACKTEST_WINDOW_SENSITIVITY_WINDOWS
    ):
        raise ValueError("Backtest window_sensitivity registered windows mismatch")
    primary_window = sensitivity.get("primary_recent_window_draws")
    if primary_window not in BACKTEST_WINDOW_SENSITIVITY_WINDOWS:
        raise ValueError("Backtest window_sensitivity primary window invalid")
    if sensitivity.get("trial_count") != len(trials):
        raise ValueError("Backtest window_sensitivity trial_count mismatch")
    if sensitivity.get("included_trial_count") != len(trials):
        raise ValueError("Backtest window_sensitivity included count mismatch")
    expected_trial_count = (
        len(BACKTEST_WINDOW_SENSITIVITY_WINDOWS)
        * len(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES)
    )
    if len(trials) != expected_trial_count:
        raise ValueError("Backtest window_sensitivity trial matrix incomplete")
    registry_ids = {row.get("trial_id") for row in registry_trials}
    expected_scope_id = target_scope.get("scope_id")
    expected_count = target_scope.get("target_draw_count")
    seen: set[tuple[str, int]] = set()
    primary_count = 0
    alternative_count = 0
    for trial in trials:
        if not isinstance(trial, dict):
            raise ValueError("Backtest window_sensitivity row invalid")
        trial_id = trial.get("trial_id")
        strategy = trial.get("strategy")
        window = trial.get("parameter_value")
        if trial_id not in registry_ids:
            raise ValueError("Backtest window_sensitivity trial missing from registry")
        if strategy not in {
            str(row["strategy"]) for row in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES
        }:
            raise ValueError("Backtest window_sensitivity strategy invalid")
        if window not in BACKTEST_WINDOW_SENSITIVITY_WINDOWS:
            raise ValueError("Backtest window_sensitivity window invalid")
        if (str(strategy), int(window)) in seen:
            raise ValueError("Backtest window_sensitivity duplicate row")
        seen.add((str(strategy), int(window)))
        if trial.get("target_scope_id") != expected_scope_id:
            raise ValueError("Backtest window_sensitivity target_scope_id mismatch")
        if trial.get("target_draw_count") != expected_count:
            raise ValueError("Backtest window_sensitivity target_draw_count mismatch")
        if trial.get("sensitivity_dimension") != "recent_window_draws":
            raise ValueError("Backtest window_sensitivity dimension mismatch")
        if trial.get("primary_parameter_value") != primary_window:
            raise ValueError("Backtest window_sensitivity primary value mismatch")
        parameters = trial.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("Backtest window_sensitivity parameters missing")
        if parameters.get("recent_window_draws") != window:
            raise ValueError("Backtest window_sensitivity parameter mismatch")
        if window == primary_window:
            primary_count += 1
            if trial.get("published") is not True:
                raise ValueError("Backtest window_sensitivity primary row not published")
            if trial.get("window_sensitivity_role") != "primary_published_window":
                raise ValueError("Backtest window_sensitivity primary role mismatch")
        else:
            alternative_count += 1
            if trial.get("published") is not False:
                raise ValueError("Backtest window_sensitivity alternative row published")
            if trial.get("variant_role") != "registered_parameter_variant":
                raise ValueError("Backtest window_sensitivity alternative role mismatch")
            if trial.get("window_sensitivity_role") != "registered_alternative_window":
                raise ValueError("Backtest window_sensitivity alternative role mismatch")
    expected_primary_count = len(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES)
    if primary_count != expected_primary_count:
        raise ValueError("Backtest window_sensitivity primary count mismatch")
    if sensitivity.get("primary_trial_count") != primary_count:
        raise ValueError("Backtest window_sensitivity primary summary mismatch")
    if sensitivity.get("alternative_window_trial_count") != alternative_count:
        raise ValueError("Backtest window_sensitivity alternative summary mismatch")


def _backtest_has_block_bootstrap_checks(backtest: dict[str, Any]) -> bool:
    if any(
        isinstance(backtest.get(key), dict)
        and "block_bootstrap_check" in backtest[key]
        for key in BACKTEST_COMPARISON_KEYS
    ):
        return True
    registry = backtest.get("multiple_testing_trials")
    trials = registry.get("trials", []) if isinstance(registry, dict) else []
    return any(
        isinstance(trial, dict) and "block_bootstrap_check" in trial
        for trial in trials
    )


def _validate_backtest_block_bootstrap_checks(backtest: dict[str, Any]) -> None:
    registry = backtest.get("multiple_testing_trials")
    target_scope = backtest.get("target_scope")
    if not isinstance(registry, dict):
        raise ValueError("Backtest multiple_testing_trials missing")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    trials = registry.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("Backtest multiple_testing_trials empty")
    expected_count = target_scope.get("target_draw_count")
    if expected_count is not None and expected_count >= BACKTEST_BLOCK_BOOTSTRAP_MIN_VALUES:
        expected_status = "available"
    else:
        expected_status = "not_available"
    for key in BACKTEST_COMPARISON_KEYS:
        row = backtest.get(key)
        if isinstance(row, dict):
            _validate_backtest_block_bootstrap_check(
                row.get("block_bootstrap_check"),
                expected_status,
            )
    for trial in trials:
        if not isinstance(trial, dict):
            raise ValueError("Backtest multiple_testing_trials row invalid")
        _validate_backtest_block_bootstrap_check(
            trial.get("block_bootstrap_check"),
            expected_status,
        )


def _validate_backtest_block_bootstrap_check(
    check: Any,
    expected_status: str,
) -> None:
    if not isinstance(check, dict):
        raise ValueError("Backtest block_bootstrap_check missing")
    if check.get("method") != "moving_block_bootstrap":
        raise ValueError("Backtest block_bootstrap_check method mismatch")
    if check.get("no_multiple_testing_decision") is not True:
        raise ValueError("Backtest block_bootstrap_check decision flag mismatch")
    if check.get("status") != expected_status:
        raise ValueError("Backtest block_bootstrap_check status mismatch")
    if expected_status != "available":
        return
    if check.get("resamples") != BACKTEST_BLOCK_BOOTSTRAP_RESAMPLES:
        raise ValueError("Backtest block_bootstrap_check resamples mismatch")
    if check.get("preserve_time_structure") != "contiguous_observation_blocks":
        raise ValueError("Backtest block_bootstrap_check preserve policy mismatch")
    if not isinstance(check.get("seed"), str) or len(str(check.get("seed"))) != 16:
        raise ValueError("Backtest block_bootstrap_check seed invalid")
    if not isinstance(check.get("block_length"), int):
        raise ValueError("Backtest block_bootstrap_check block length missing")
    if not isinstance(check.get("confidence_interval_lower"), (int, float)):
        raise ValueError("Backtest block_bootstrap_check interval missing")
    if not isinstance(check.get("confidence_interval_upper"), (int, float)):
        raise ValueError("Backtest block_bootstrap_check interval missing")
    if check["confidence_interval_lower"] > check["confidence_interval_upper"]:
        raise ValueError("Backtest block_bootstrap_check interval invalid")
    normal = check.get("normal_approximation")
    if not isinstance(normal, dict):
        raise ValueError("Backtest block_bootstrap_check normal comparison missing")
    if normal.get("method") != "paired_normal_mean_interval":
        raise ValueError("Backtest block_bootstrap_check normal method mismatch")
    if not isinstance(normal.get("confidence_interval_lower"), (int, float)):
        raise ValueError("Backtest block_bootstrap_check normal interval missing")
    if not isinstance(normal.get("confidence_interval_upper"), (int, float)):
        raise ValueError("Backtest block_bootstrap_check normal interval missing")


def _backtest_block_bootstrap_summary(
    product_slug: str,
    backtest: dict[str, Any],
) -> dict[str, Any]:
    comparison_checks = [
        backtest[key]["block_bootstrap_check"]
        for key in BACKTEST_COMPARISON_KEYS
        if isinstance(backtest.get(key), dict)
        and isinstance(backtest[key].get("block_bootstrap_check"), dict)
    ]
    registry = backtest.get("multiple_testing_trials", {})
    trials = registry.get("trials", []) if isinstance(registry, dict) else []
    trial_checks = [
        trial["block_bootstrap_check"]
        for trial in trials
        if isinstance(trial, dict)
        and isinstance(trial.get("block_bootstrap_check"), dict)
    ]
    checks = [*comparison_checks, *trial_checks]
    available_checks = [
        check for check in checks if check.get("status") == "available"
    ]
    return {
        "product": product_slug,
        "method": "moving_block_bootstrap",
        "comparison_check_count": len(comparison_checks),
        "trial_check_count": len(trial_checks),
        "available_check_count": len(available_checks),
        "normal_overlap_count": sum(
            bool(check.get("interval_overlap_with_normal_approximation"))
            for check in available_checks
        ),
        "max_block_length": max(
            (int(check.get("block_length") or 0) for check in available_checks),
            default=0,
        ),
        "sampling_methods": sorted(
            {
                str(check.get("sampling_method"))
                for check in available_checks
                if check.get("sampling_method")
            }
        ),
    }


def _validate_backtest_trial_disposition_log(backtest: dict[str, Any]) -> None:
    log = backtest.get("trial_disposition_log")
    registry = backtest.get("multiple_testing_trials")
    target_scope = backtest.get("target_scope")
    if not isinstance(log, dict):
        raise ValueError("Backtest trial_disposition_log missing")
    if not isinstance(registry, dict):
        raise ValueError("Backtest multiple_testing_trials missing")
    if not isinstance(target_scope, dict):
        raise ValueError("Backtest target_scope missing")
    included_trials = log.get("included_trials")
    rejected_configurations = log.get("rejected_configurations")
    registry_trials = registry.get("trials")
    if not isinstance(included_trials, list) or not isinstance(registry_trials, list):
        raise ValueError("Backtest trial_disposition_log included trials invalid")
    if not isinstance(rejected_configurations, list) or not rejected_configurations:
        raise ValueError("Backtest trial_disposition_log rejected configs missing")
    if log.get("included_trial_count") != len(registry_trials):
        raise ValueError("Backtest trial_disposition_log included count mismatch")
    if log.get("rejected_configuration_count") != len(rejected_configurations):
        raise ValueError("Backtest trial_disposition_log rejected count mismatch")
    if log.get("retained_record_count") != len(included_trials) + len(rejected_configurations):
        raise ValueError("Backtest trial_disposition_log retained count mismatch")
    registry_ids = {trial.get("trial_id") for trial in registry_trials}
    included_ids = {trial.get("trial_id") for trial in included_trials}
    if registry_ids != included_ids:
        raise ValueError("Backtest trial_disposition_log trial ids mismatch")
    failed_count = sum(
        row.get("result_status") != "adjusted_win"
        for row in included_trials
        if isinstance(row, dict)
    )
    if log.get("failed_trial_count") != failed_count:
        raise ValueError("Backtest trial_disposition_log failed count mismatch")
    expected_scope_id = target_scope.get("scope_id")
    expected_count = target_scope.get("target_draw_count")
    for row in included_trials:
        if not isinstance(row, dict):
            raise ValueError("Backtest trial_disposition_log included row invalid")
        if row.get("included_in_multiple_testing") is not True:
            raise ValueError("Backtest trial_disposition_log included row not retained")
        if row.get("evaluated_on_final_scope") is not True:
            raise ValueError("Backtest trial_disposition_log included row not evaluated")
        if row.get("target_scope_id") != expected_scope_id:
            raise ValueError("Backtest trial_disposition_log included scope mismatch")
        if row.get("target_draw_count") != expected_count:
            raise ValueError("Backtest trial_disposition_log included count mismatch")
        if not isinstance(row.get("parameters_sha256"), str):
            raise ValueError("Backtest trial_disposition_log parameter hash missing")
    for row in rejected_configurations:
        if not isinstance(row, dict):
            raise ValueError("Backtest trial_disposition_log rejected row invalid")
        if row.get("disposition") != "rejected_before_final_evaluation":
            raise ValueError("Backtest trial_disposition_log rejected disposition invalid")
        if row.get("included_in_multiple_testing") is not False:
            raise ValueError("Backtest trial_disposition_log rejected row retained")
        if row.get("evaluated_on_final_scope") is not False:
            raise ValueError("Backtest trial_disposition_log rejected row evaluated")
        if row.get("target_scope_id") != expected_scope_id:
            raise ValueError("Backtest trial_disposition_log rejected scope mismatch")
        if row.get("target_draw_count") != expected_count:
            raise ValueError("Backtest trial_disposition_log rejected count mismatch")
        if not row.get("reason_code"):
            raise ValueError("Backtest trial_disposition_log rejected reason missing")


def _forecast_events(dataset: ProductDataset) -> list[dict[str, Any]]:
    product = dataset.product
    latest = dataset.latest
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    dataset_observed_at = dataset.latest_fetched_at or f"{latest.draw_date.isoformat()}T00:00:00+00:00"
    fingerprint = dataset.history_fingerprint
    if product.kind is AnalysisKind.NUMBER_SET:
        forecasts = _number_forecasts(dataset)
    else:
        forecasts = _digit_forecasts(dataset)
    events = []
    for forecast in forecasts:
        identity = "|".join(
            (
                product.slug,
                latest.draw_id,
                forecast["strategy"],
                MODEL_VERSION,
                fingerprint,
            )
        )
        events.append(
            {
                "event_type": "prediction",
                "prediction_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                "product": product.slug,
                "strategy": forecast["strategy"],
                "strategy_label": forecast["strategy_label"],
                "model_version": MODEL_VERSION,
                "code_version": MODEL_VERSION,
                "generated_at": generated_at,
                "generated_at_timezone": "UTC",
                "dataset_observed_at": dataset_observed_at,
                "dataset_cutoff_draw_id": latest.draw_id,
                "dataset_cutoff_date": latest.draw_date.isoformat(),
                "dataset_cutoff_timezone": "Asia/Ho_Chi_Minh",
                "dataset_fingerprint": fingerprint,
                "target": "first_confirmed_draw_after_cutoff",
                "prediction": forecast["prediction"],
                "parameters": forecast["parameters"],
                "research_only": True,
            }
        )
    return events


def _number_forecasts(dataset: ProductDataset) -> list[dict[str, Any]]:
    product = dataset.product
    observations = dataset.observations
    total_counts = Counter(value for item in observations for value in item.values)
    recent_window = min(500 if product.slug == "keno" else 200, len(observations))
    short_window = min(50, len(observations))
    recent_counts = Counter(
        value for item in observations[-recent_window:] for value in item.values
    )
    short_counts = Counter(
        value for item in observations[-short_window:] for value in item.values
    )
    pair_window = min(PAIR_WINDOW_LIMIT, len(observations))
    pair_counts = _number_pair_counts(observations[-pair_window:])
    pair_scores = _number_pair_scores_from_counts(product, pair_counts, pair_window)
    last_seen: dict[int, int] = {}
    for index, item in enumerate(observations):
        for value in item.values:
            last_seen[value] = index
    scores = _number_scores(
        product,
        total_counts,
        len(observations),
        recent_counts,
        recent_window,
        short_counts,
        short_window,
        last_seen,
        len(observations),
    )
    _apply_audit_number_scores(scores, pair_scores)
    seed = f"{product.slug}|{dataset.latest.draw_id}|{MODEL_VERSION}"
    uniform = _uniform_number_pick(product, seed)
    balanced = _top_numbers(scores, "balanced", product.pick_count or 0, seed)
    recent = _top_numbers(scores, "recent", product.pick_count or 0, seed)
    audit_signal = _audit_number_pick(
        scores,
        pair_scores,
        product.pick_count or 0,
        seed + "|audit",
    )

    special_predictions = _special_forecasts(dataset, seed)
    result = []
    for strategy, label, values in (
        ("uniform_seeded", "Baseline đồng đều có seed", uniform),
        ("balanced_signal", "Tín hiệu cân bằng", balanced),
        ("recent_frequency", "Tần suất cửa sổ gần", recent),
        ("audit_signal", "Tín hiệu kiểm định công bằng", audit_signal),
    ):
        score_policy = (
            AUDIT_NUMBER_SCORE_POLICY
            if strategy == "audit_signal"
            else NUMBER_SCORE_POLICY
        )
        result.append(
            {
                "strategy": strategy,
                "strategy_label": label,
                "prediction": {
                    "numbers": values,
                    "special_numbers": special_predictions.get(strategy, []),
                },
                "parameters": {
                    "history_draws": len(observations),
                    "recent_window_draws": recent_window,
                    "short_window_draws": short_window,
                    "pair_window_draws": pair_window,
                    "selection_count": product.pick_count,
                    "pool_size": product.pool_size,
                    "score_policy": score_policy,
                    "seed_policy": "sha256(product, cutoff, model_version)",
                },
            }
        )
    return result


def _digit_forecasts(dataset: ProductDataset) -> list[dict[str, Any]]:
    product = dataset.product
    length = product.sequence_length or 0
    symbols = list(range(product.sequence_min, product.sequence_max + 1))
    total = [Counter() for _ in range(length)]
    recent = [Counter() for _ in range(length)]
    short = [Counter() for _ in range(length)]
    outcomes = [outcome for item in dataset.observations for outcome in item.outcomes]
    recent_draws = dataset.observations[-min(500, len(dataset.observations)) :]
    short_draws = dataset.observations[-min(50, len(dataset.observations)) :]
    recent_outcomes = [outcome for item in recent_draws for outcome in item.outcomes]
    short_outcomes = [outcome for item in short_draws for outcome in item.outcomes]

    for outcome in outcomes:
        for position, char in enumerate(outcome):
            total[position][int(char)] += 1
    for outcome in recent_outcomes:
        for position, char in enumerate(outcome):
            recent[position][int(char)] += 1
    for outcome in short_outcomes:
        for position, char in enumerate(outcome):
            short[position][int(char)] += 1

    seed = f"{product.slug}|{dataset.latest.draw_id}|{MODEL_VERSION}"
    uniform_rng = random.Random(_seed_int(seed + "|uniform"))
    uniform = "".join(str(uniform_rng.choice(symbols)) for _ in range(length))
    recent_mode = _digit_sequence_from_scores(total, recent, short, symbols, "recent", seed)
    balanced = _digit_sequence_from_scores(total, recent, short, symbols, "balanced", seed)
    audit_signal = _digit_sequence_from_scores(total, recent, short, symbols, "audit", seed)
    return [
        {
            "strategy": "uniform_seeded",
            "strategy_label": "Baseline đồng đều có seed",
            "prediction": {"sequence": uniform},
            "parameters": {
                "history_draws": len(dataset.observations),
                "recent_window_draws": len(recent_draws),
                "short_window_draws": len(short_draws),
                "sequence_length": length,
                "symbol_min": product.sequence_min,
                "symbol_max": product.sequence_max,
                "score_policy": DIGIT_SCORE_POLICY,
            },
        },
        {
            "strategy": "balanced_signal",
            "strategy_label": "Tín hiệu cân bằng",
            "prediction": {"sequence": balanced},
            "parameters": {
                "history_draws": len(dataset.observations),
                "recent_window_draws": len(recent_draws),
                "short_window_draws": len(short_draws),
                "sequence_length": length,
                "symbol_min": product.sequence_min,
                "symbol_max": product.sequence_max,
                "score_policy": DIGIT_SCORE_POLICY,
            },
        },
        {
            "strategy": "recent_frequency",
            "strategy_label": "Tần suất cửa sổ gần",
            "prediction": {"sequence": recent_mode},
            "parameters": {
                "history_draws": len(dataset.observations),
                "recent_window_draws": len(recent_draws),
                "short_window_draws": len(short_draws),
                "sequence_length": length,
                "symbol_min": product.sequence_min,
                "symbol_max": product.sequence_max,
                "score_policy": DIGIT_SCORE_POLICY,
            },
        },
        {
            "strategy": "audit_signal",
            "strategy_label": "Tín hiệu kiểm định công bằng",
            "prediction": {"sequence": audit_signal},
            "parameters": {
                "history_draws": len(dataset.observations),
                "recent_window_draws": len(recent_draws),
                "short_window_draws": len(short_draws),
                "sequence_length": length,
                "symbol_min": product.sequence_min,
                "symbol_max": product.sequence_max,
                "score_policy": AUDIT_DIGIT_SCORE_POLICY,
            },
        },
    ]


def _number_backtest(dataset: ProductDataset) -> dict[str, object]:
    product = dataset.product
    observations = dataset.observations
    minimum_history = min(200, max(30, len(observations) // 3))
    limit = 5000 if product.slug == "keno" else 1000
    start = max(minimum_history, len(observations) - limit)
    if start >= len(observations):
        return {"status": "insufficient_data", "samples": 0}
    walk_forward_targets = observations[start:]
    phase_split = _backtest_phase_split(product, walk_forward_targets)
    evaluation_start_offset = phase_split["selection_phase"]["draw_count"]
    evaluation_start_index = start + evaluation_start_offset
    evaluation_targets = walk_forward_targets[evaluation_start_offset:]
    target_scope = _backtest_target_scope(product, evaluation_targets)
    phase_split["final_evaluation_phase"]["scope_id"] = target_scope["scope_id"]
    phase_split["final_evaluation_phase"]["draw_ids_sha256"] = target_scope[
        "target_draw_ids_sha256"
    ]
    scope_fields = _target_scope_fields(target_scope)

    recent_window = 500 if product.slug == "keno" else 200
    short_window = 50
    registered_windows = BACKTEST_WINDOW_SENSITIVITY_WINDOWS
    total_counts: Counter[int] = Counter()
    last_seen: dict[int, int] = {}
    for index, item in enumerate(observations[:start]):
        total_counts.update(item.values)
        for value in item.values:
            last_seen[value] = index
    recent_items_by_window = {
        window: deque(observations[max(0, start - window) : start])
        for window in registered_windows
    }
    recent_counts_by_window = {
        window: Counter(value for item in items for value in item.values)
        for window, items in recent_items_by_window.items()
    }
    short_items = deque(observations[max(0, start - short_window) : start])
    short_counts = Counter(value for item in short_items for value in item.values)
    pair_window = min(PAIR_WINDOW_LIMIT, len(observations))
    pair_items = deque(observations[max(0, start - pair_window) : start])
    pair_counts = _number_pair_counts(pair_items)

    model_hits: list[int] = []
    recent_hits: list[int] = []
    audit_hits: list[int] = []
    differences: list[float] = []
    recent_differences: list[float] = []
    audit_differences: list[float] = []
    shadow_differences: dict[str, list[float]] = {
        str(trial["trial_id"]): []
        for trial in BACKTEST_NUMBER_SHADOW_TRIALS
    }
    window_differences: dict[tuple[str, int], list[float]] = {
        (str(strategy["strategy"]), window): []
        for window in registered_windows
        for strategy in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES
    }
    expected_hits = (product.pick_count or 0) ** 2 / product.pool_size
    for index in range(start, len(observations)):
        target = observations[index]
        pair_scores = _number_pair_scores_from_counts(
            product,
            pair_counts,
            len(pair_items),
        )
        scores_by_window = {}
        for window in registered_windows:
            window_scores = _number_scores(
                product,
                total_counts,
                index,
                recent_counts_by_window[window],
                len(recent_items_by_window[window]),
                short_counts,
                len(short_items),
                last_seen,
                index,
            )
            _apply_audit_number_scores(window_scores, pair_scores)
            scores_by_window[window] = window_scores
        scores = scores_by_window[recent_window]
        seed = f"backtest|{product.slug}|{target.draw_id}|{MODEL_VERSION}"
        models_by_window: dict[tuple[str, int], list[int]] = {}
        for window, window_scores in scores_by_window.items():
            balanced_seed = (
                seed
                if window == recent_window
                else f"{seed}|window|{window}|balanced_signal"
            )
            recent_seed = (
                seed + "|recent"
                if window == recent_window
                else f"{seed}|window|{window}|recent_frequency"
            )
            audit_seed = (
                seed + "|audit"
                if window == recent_window
                else f"{seed}|window|{window}|audit_signal"
            )
            models_by_window[("balanced_signal", window)] = _top_numbers(
                window_scores,
                "balanced",
                product.pick_count or 0,
                balanced_seed,
            )
            models_by_window[("recent_frequency", window)] = _top_numbers(
                window_scores,
                "recent",
                product.pick_count or 0,
                recent_seed,
            )
            models_by_window[("audit_signal", window)] = _audit_number_pick(
                window_scores,
                pair_scores,
                product.pick_count or 0,
                audit_seed,
            )
        model = models_by_window[("balanced_signal", recent_window)]
        recent_model = models_by_window[("recent_frequency", recent_window)]
        audit_model = models_by_window[("audit_signal", recent_window)]
        actual = set(target.values)
        model_hit = len(actual.intersection(model))
        recent_hit = len(actual.intersection(recent_model))
        audit_hit = len(actual.intersection(audit_model))
        model_hits.append(model_hit)
        recent_hits.append(recent_hit)
        audit_hits.append(audit_hit)
        differences.append(float(model_hit - expected_hits))
        recent_differences.append(float(recent_hit - expected_hits))
        audit_differences.append(float(audit_hit - expected_hits))
        for key, trial_model in models_by_window.items():
            window_differences[key].append(
                float(len(actual.intersection(trial_model)) - expected_hits)
            )
        for trial in BACKTEST_NUMBER_SHADOW_TRIALS:
            trial_id = str(trial["trial_id"])
            trial_model = _top_numbers(
                scores,
                str(trial["score_key"]),
                product.pick_count or 0,
                seed + f"|{trial_id}",
            )
            shadow_differences[trial_id].append(
                float(len(actual.intersection(trial_model)) - expected_hits)
            )

        total_counts.update(target.values)
        for value in target.values:
            last_seen[value] = index
        for window, recent_items in recent_items_by_window.items():
            recent_items.append(target)
            recent_counts_by_window[window].update(target.values)
            if len(recent_items) > window:
                expired = recent_items.popleft()
                recent_counts_by_window[window].subtract(expired.values)
        short_items.append(target)
        short_counts.update(target.values)
        if len(short_items) > short_window:
            expired_short = short_items.popleft()
            short_counts.subtract(expired_short.values)
        pair_items.append(target)
        _update_number_pair_counts(pair_counts, target, 1)
        if len(pair_items) > pair_window:
            expired_pair = pair_items.popleft()
            _update_number_pair_counts(pair_counts, expired_pair, -1)

    evaluation_model_hits = model_hits[evaluation_start_offset:]
    evaluation_recent_hits = recent_hits[evaluation_start_offset:]
    evaluation_audit_hits = audit_hits[evaluation_start_offset:]
    evaluation_differences = differences[evaluation_start_offset:]
    evaluation_recent_differences = recent_differences[evaluation_start_offset:]
    evaluation_audit_differences = audit_differences[evaluation_start_offset:]
    evaluation_window_differences = {
        key: values[evaluation_start_offset:]
        for key, values in window_differences.items()
    }
    evaluation_model_distribution = Counter(evaluation_model_hits)
    evaluation_recent_distribution = Counter(evaluation_recent_hits)
    evaluation_audit_distribution = Counter(evaluation_audit_hits)
    z_score, p_value = _paired_normal_test(evaluation_differences)
    recent_z_score, recent_p_value = _paired_normal_test(evaluation_recent_differences)
    audit_z_score, audit_p_value = _paired_normal_test(evaluation_audit_differences)
    difference_interval = _normal_mean_interval(evaluation_differences)
    recent_difference_interval = _normal_mean_interval(evaluation_recent_differences)
    audit_difference_interval = _normal_mean_interval(evaluation_audit_differences)
    baseline_distribution = _number_uniform_distribution(
        product.pool_size,
        product.pick_count or 0,
        len(evaluation_model_hits),
    )
    partial_match_baseline = _number_partial_match_baseline(
        product,
        len(evaluation_model_hits),
        baseline_distribution,
    )
    exact_probability = 1 / math.comb(product.pool_size, product.pick_count or 0)
    comparison_wins = fmean(evaluation_differences) > 0 and p_value < 0.05
    recent_comparison_wins = (
        fmean(evaluation_recent_differences) > 0 and recent_p_value < 0.05
    )
    audit_comparison_wins = fmean(evaluation_audit_differences) > 0 and audit_p_value < 0.05
    published_trials = [
        _backtest_trial_row(
            trial_id="balanced_signal:published_final",
            strategy="balanced_signal",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[0]["label"]),
            variant_role="published_final_model",
            differences=evaluation_differences,
            metric_key="mean_hit_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="number_set",
                strategy="balanced_signal",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
                pair_window=pair_window,
            ),
            published_comparison_key="comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
        _backtest_trial_row(
            trial_id="recent_frequency:published_final",
            strategy="recent_frequency",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[1]["label"]),
            variant_role="published_final_model",
            differences=evaluation_recent_differences,
            metric_key="mean_hit_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="number_set",
                strategy="recent_frequency",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
                pair_window=pair_window,
            ),
            published_comparison_key="recent_comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
        _backtest_trial_row(
            trial_id="audit_signal:published_final",
            strategy="audit_signal",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[2]["label"]),
            variant_role="published_final_model",
            differences=evaluation_audit_differences,
            metric_key="mean_hit_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="number_set",
                strategy="audit_signal",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
                pair_window=pair_window,
            ),
            published_comparison_key="audit_comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
    ]
    shadow_trials = [
        _backtest_trial_row(
            trial_id=f"{trial['strategy']}:{trial['trial_id']}",
            strategy=str(trial["strategy"]),
            label=str(trial["label"]),
            variant_role=str(trial["variant_role"]),
            differences=shadow_differences[str(trial["trial_id"])][
                evaluation_start_offset:
            ],
            metric_key="mean_hit_difference",
            scope_fields=scope_fields,
            parameters=dict(trial["parameters"]),
        )
        for trial in BACKTEST_NUMBER_SHADOW_TRIALS
    ]
    published_trials_by_strategy = {
        str(trial["strategy"]): trial
        for trial in published_trials
    }
    sensitivity_trials: list[dict[str, Any]] = []
    for window in registered_windows:
        for strategy_config in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES:
            strategy = str(strategy_config["strategy"])
            if window == recent_window:
                sensitivity_trials.append(published_trials_by_strategy[strategy])
                continue
            sensitivity_trials.append(
                _backtest_trial_row(
                    trial_id=f"{strategy}:recent_window_{window}",
                    strategy=strategy,
                    label=f"{strategy_config['label']} - cửa sổ {window} kỳ",
                    variant_role="registered_parameter_variant",
                    differences=evaluation_window_differences[(strategy, window)],
                    metric_key="mean_hit_difference",
                    scope_fields=scope_fields,
                    parameters=_backtest_window_trial_parameters(
                        product_kind="number_set",
                        strategy=strategy,
                        recent_window=window,
                        primary_recent_window=recent_window,
                        short_window=short_window,
                        pair_window=pair_window,
                    ),
                    metadata=_backtest_window_trial_metadata(
                        window=window,
                        primary_recent_window=recent_window,
                    ),
                )
            )
    registered_window_trials = [
        row
        for row in sensitivity_trials
        if row.get("window_sensitivity_role") == "registered_alternative_window"
    ]
    multiple_testing_trials = _backtest_multiple_testing_trials(
        "number_set",
        "mean_hit_difference",
        [*published_trials, *shadow_trials, *registered_window_trials],
    )
    window_sensitivity = _backtest_window_sensitivity(
        "number_set",
        "mean_hit_difference",
        recent_window,
        sensitivity_trials,
    )
    trial_disposition_log = _backtest_trial_disposition_log(
        "number_set",
        "mean_hit_difference",
        multiple_testing_trials["trials"],
        scope_fields,
    )
    report = {
        "schema_version": 2,
        "status": "complete",
        "method": "walk_forward",
        "samples": len(evaluation_model_hits),
        "walk_forward_samples": len(model_hits),
        "target_scope": target_scope,
        "phase_split": phase_split,
        "multiple_testing_trials": multiple_testing_trials,
        "window_sensitivity": window_sensitivity,
        "trial_disposition_log": trial_disposition_log,
        "score_formulas": _number_backtest_score_formulas(),
        "first_walk_forward_draw_id": observations[start].draw_id,
        "first_test_draw_id": observations[evaluation_start_index].draw_id,
        "latest_test_draw_id": observations[-1].draw_id,
        "initial_training_draws": evaluation_start_index,
        "initial_walk_forward_training_draws": start,
        "minimum_history_draws": minimum_history,
        "recent_window_draws": recent_window,
        "short_window_draws": short_window,
        "pair_window_draws": pair_window,
        "score_policy": NUMBER_SCORE_POLICY,
        "audit_score_policy": AUDIT_NUMBER_SCORE_POLICY,
        "model": {
            "strategy": "balanced_signal",
            **scope_fields,
            "average_hits": _round(fmean(evaluation_model_hits)),
            "exact_hits": evaluation_model_distribution[product.pick_count or 0],
            "hit_distribution": _counter_to_rows(evaluation_model_distribution),
        },
        "recent_model": {
            "strategy": "recent_frequency",
            **scope_fields,
            "average_hits": _round(fmean(evaluation_recent_hits)),
            "exact_hits": evaluation_recent_distribution[product.pick_count or 0],
            "hit_distribution": _counter_to_rows(evaluation_recent_distribution),
        },
        "audit_model": {
            "strategy": "audit_signal",
            **scope_fields,
            "average_hits": _round(fmean(evaluation_audit_hits)),
            "exact_hits": evaluation_audit_distribution[product.pick_count or 0],
            "hit_distribution": _counter_to_rows(evaluation_audit_distribution),
        },
        "baseline": {
            "strategy": "uniform_exact_expectation",
            "method": "exact_hypergeometric_expectation",
            **scope_fields,
            "average_hits": _round(expected_hits),
            "expected_average_hits": _round(expected_hits),
            "expected_exact_hits": _round(len(evaluation_model_hits) * exact_probability),
            "exact_hit_probability": _round(exact_probability, 12),
            "hit_distribution": baseline_distribution,
            "partial_match_baseline": partial_match_baseline,
        },
        "comparison": {
            **scope_fields,
            "mean_hit_difference": _round(fmean(evaluation_differences)),
            "paired_z_score": _round(z_score),
            "approximate_p_value": _round(p_value, 8),
            **difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="balanced_signal:published_final",
                differences=evaluation_differences,
                metric_key="mean_hit_difference",
            ),
            "beats_baseline_unadjusted": comparison_wins,
            "beats_baseline": False,
        },
        "recent_comparison": {
            **scope_fields,
            "mean_hit_difference": _round(fmean(evaluation_recent_differences)),
            "paired_z_score": _round(recent_z_score),
            "approximate_p_value": _round(recent_p_value, 8),
            **recent_difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="recent_frequency:published_final",
                differences=evaluation_recent_differences,
                metric_key="mean_hit_difference",
            ),
            "beats_baseline_unadjusted": recent_comparison_wins,
            "beats_baseline": False,
        },
        "audit_comparison": {
            **scope_fields,
            "mean_hit_difference": _round(fmean(evaluation_audit_differences)),
            "paired_z_score": _round(audit_z_score),
            "approximate_p_value": _round(audit_p_value, 8),
            **audit_difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="audit_signal:published_final",
                differences=evaluation_audit_differences,
                metric_key="mean_hit_difference",
            ),
            "beats_baseline_unadjusted": audit_comparison_wins,
            "beats_baseline": False,
        },
        "warning": (
            "Backtest cuốn chiếu chỉ dùng dữ liệu trước kỳ kiểm tra. Baseline là kỳ vọng "
            "siêu bội chính xác, không phải một lần bốc ngẫu nhiên. Nhãn vượt baseline chỉ "
            "được kết luận sau hiệu chỉnh nhiều phép thử trên toàn bộ sản phẩm."
        ),
    }
    _validate_backtest_target_scope(report)
    _validate_backtest_phase_split(report)
    _validate_backtest_multiple_testing_trials(report)
    _validate_backtest_window_sensitivity(report)
    _validate_backtest_block_bootstrap_checks(report)
    _validate_backtest_trial_disposition_log(report)
    return report


def _digit_backtest(dataset: ProductDataset) -> dict[str, object]:
    product = dataset.product
    observations = dataset.observations
    minimum_history = min(100, max(30, len(observations) // 3))
    limit = 5000 if product.slug == "bingo18" else 1000
    start = max(minimum_history, len(observations) - limit)
    if start >= len(observations):
        return {"status": "insufficient_data", "samples": 0}
    walk_forward_targets = observations[start:]
    phase_split = _backtest_phase_split(product, walk_forward_targets)
    evaluation_start_offset = phase_split["selection_phase"]["draw_count"]
    evaluation_start_index = start + evaluation_start_offset
    evaluation_targets = walk_forward_targets[evaluation_start_offset:]
    target_scope = _backtest_target_scope(product, evaluation_targets)
    phase_split["final_evaluation_phase"]["scope_id"] = target_scope["scope_id"]
    phase_split["final_evaluation_phase"]["draw_ids_sha256"] = target_scope[
        "target_draw_ids_sha256"
    ]
    scope_fields = _target_scope_fields(target_scope)

    length = product.sequence_length or 0
    symbols = list(range(product.sequence_min, product.sequence_max + 1))
    total = [Counter() for _ in range(length)]
    short = [Counter() for _ in range(length)]
    for item in observations[:start]:
        _update_digit_counts(total, item.outcomes, 1)
    recent_window = 500
    short_window = 50
    registered_windows = BACKTEST_WINDOW_SENSITIVITY_WINDOWS
    recent_items_by_window = {
        window: deque(observations[max(0, start - window) : start])
        for window in registered_windows
    }
    recent_counts_by_window = {
        window: [Counter() for _ in range(length)]
        for window in registered_windows
    }
    short_items = deque(observations[max(0, start - short_window) : start])
    for window, recent_items in recent_items_by_window.items():
        for item in recent_items:
            _update_digit_counts(recent_counts_by_window[window], item.outcomes, 1)
    for item in short_items:
        _update_digit_counts(short, item.outcomes, 1)
    recent = recent_counts_by_window[recent_window]

    model_exact_flags: list[bool] = []
    recent_exact_flags: list[bool] = []
    audit_exact_flags: list[bool] = []
    model_best: list[int] = []
    recent_best: list[int] = []
    audit_best: list[int] = []
    baseline_best_expected: list[float] = []
    baseline_exact_expected: list[float] = []
    baseline_score_distributions: list[dict[int, float]] = []
    shadow_differences: dict[str, list[float]] = {
        str(trial["trial_id"]): []
        for trial in BACKTEST_DIGIT_SHADOW_TRIALS
    }
    window_differences: dict[tuple[str, int], list[float]] = {
        (str(strategy["strategy"]), window): []
        for window in registered_windows
        for strategy in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES
    }
    for index in range(start, len(observations)):
        target = observations[index]
        seed = f"backtest|{product.slug}|{target.draw_id}|{MODEL_VERSION}"
        models_by_window: dict[tuple[str, int], str] = {}
        for window in registered_windows:
            window_recent = recent_counts_by_window[window]
            for strategy_config in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES:
                strategy = str(strategy_config["strategy"])
                score_strategy = str(strategy_config["score_strategy"])
                if window == recent_window and strategy == "balanced_signal":
                    strategy_seed = seed
                elif window == recent_window and strategy == "recent_frequency":
                    strategy_seed = seed + "|recent"
                elif window == recent_window and strategy == "audit_signal":
                    strategy_seed = seed
                else:
                    strategy_seed = f"{seed}|window|{window}|{strategy}"
                models_by_window[(strategy, window)] = _digit_sequence_from_scores(
                    total,
                    window_recent,
                    short,
                    symbols,
                    score_strategy,
                    strategy_seed,
                )
        model = models_by_window[("balanced_signal", recent_window)]
        recent_model = models_by_window[("recent_frequency", recent_window)]
        audit_model = models_by_window[("audit_signal", recent_window)]
        actual = set(target.outcomes)
        (
            expected_best_match,
            expected_exact_probability,
            expected_score_distribution,
        ) = _digit_uniform_expectation(actual, symbols, length)
        model_exact_flags.append(model in actual)
        recent_exact_flags.append(recent_model in actual)
        audit_exact_flags.append(audit_model in actual)
        model_best.append(_best_position_match(model, actual))
        recent_best.append(_best_position_match(recent_model, actual))
        audit_best.append(_best_position_match(audit_model, actual))
        baseline_best_expected.append(expected_best_match)
        baseline_exact_expected.append(expected_exact_probability)
        baseline_score_distributions.append(expected_score_distribution)
        for key, trial_model in models_by_window.items():
            window_differences[key].append(
                float(_best_position_match(trial_model, actual) - expected_best_match)
            )
        for trial in BACKTEST_DIGIT_SHADOW_TRIALS:
            trial_id = str(trial["trial_id"])
            trial_model = _digit_sequence_from_scores(
                total,
                recent,
                short,
                symbols,
                str(trial["score_strategy"]),
                seed + f"|{trial_id}",
            )
            shadow_differences[trial_id].append(
                float(_best_position_match(trial_model, actual) - expected_best_match)
            )

        _update_digit_counts(total, target.outcomes, 1)
        for window, recent_items in recent_items_by_window.items():
            recent_items.append(target)
            _update_digit_counts(recent_counts_by_window[window], target.outcomes, 1)
            if len(recent_items) > window:
                expired = recent_items.popleft()
                _update_digit_counts(
                    recent_counts_by_window[window],
                    expired.outcomes,
                    -1,
                )

        short_items.append(target)
        _update_digit_counts(short, target.outcomes, 1)
        if len(short_items) > short_window:
            expired_short = short_items.popleft()
            _update_digit_counts(short, expired_short.outcomes, -1)

    evaluation_model_exact = sum(model_exact_flags[evaluation_start_offset:])
    evaluation_recent_exact = sum(recent_exact_flags[evaluation_start_offset:])
    evaluation_audit_exact = sum(audit_exact_flags[evaluation_start_offset:])
    evaluation_model_best = model_best[evaluation_start_offset:]
    evaluation_recent_best = recent_best[evaluation_start_offset:]
    evaluation_audit_best = audit_best[evaluation_start_offset:]
    evaluation_baseline_best_expected = baseline_best_expected[evaluation_start_offset:]
    evaluation_baseline_exact_expected = baseline_exact_expected[evaluation_start_offset:]
    evaluation_baseline_distribution: Counter[int] = Counter()
    for distribution in baseline_score_distributions[evaluation_start_offset:]:
        for score, probability in distribution.items():
            evaluation_baseline_distribution[score] += probability
    samples = len(evaluation_model_best)
    differences = [
        float(model - baseline)
        for model, baseline in zip(
            evaluation_model_best,
            evaluation_baseline_best_expected,
            strict=True,
        )
    ]
    recent_differences = [
        float(model - baseline)
        for model, baseline in zip(
            evaluation_recent_best,
            evaluation_baseline_best_expected,
            strict=True,
        )
    ]
    audit_differences = [
        float(model - baseline)
        for model, baseline in zip(
            evaluation_audit_best,
            evaluation_baseline_best_expected,
            strict=True,
        )
    ]
    evaluation_window_differences = {
        key: values[evaluation_start_offset:]
        for key, values in window_differences.items()
    }
    z_score, p_value = _paired_normal_test(differences)
    recent_z_score, recent_p_value = _paired_normal_test(recent_differences)
    audit_z_score, audit_p_value = _paired_normal_test(audit_differences)
    difference_interval = _normal_mean_interval(differences)
    recent_difference_interval = _normal_mean_interval(recent_differences)
    audit_difference_interval = _normal_mean_interval(audit_differences)
    comparison_wins = fmean(differences) > 0 and p_value < 0.05
    recent_comparison_wins = (
        fmean(recent_differences) > 0 and recent_p_value < 0.05
    )
    audit_comparison_wins = fmean(audit_differences) > 0 and audit_p_value < 0.05
    published_trials = [
        _backtest_trial_row(
            trial_id="balanced_signal:published_final",
            strategy="balanced_signal",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[0]["label"]),
            variant_role="published_final_model",
            differences=differences,
            metric_key="mean_position_match_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="digit_sequence",
                strategy="balanced_signal",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
            ),
            published_comparison_key="comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
        _backtest_trial_row(
            trial_id="recent_frequency:published_final",
            strategy="recent_frequency",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[1]["label"]),
            variant_role="published_final_model",
            differences=recent_differences,
            metric_key="mean_position_match_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="digit_sequence",
                strategy="recent_frequency",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
            ),
            published_comparison_key="recent_comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
        _backtest_trial_row(
            trial_id="audit_signal:published_final",
            strategy="audit_signal",
            label=str(BACKTEST_WINDOW_SENSITIVITY_STRATEGIES[2]["label"]),
            variant_role="published_final_model",
            differences=audit_differences,
            metric_key="mean_position_match_difference",
            scope_fields=scope_fields,
            parameters=_backtest_window_trial_parameters(
                product_kind="digit_sequence",
                strategy="audit_signal",
                recent_window=recent_window,
                primary_recent_window=recent_window,
                short_window=short_window,
            ),
            published_comparison_key="audit_comparison",
            metadata=_backtest_window_trial_metadata(
                window=recent_window,
                primary_recent_window=recent_window,
            ),
        ),
    ]
    shadow_trials = [
        _backtest_trial_row(
            trial_id=f"{trial['strategy']}:{trial['trial_id']}",
            strategy=str(trial["strategy"]),
            label=str(trial["label"]),
            variant_role=str(trial["variant_role"]),
            differences=shadow_differences[str(trial["trial_id"])][
                evaluation_start_offset:
            ],
            metric_key="mean_position_match_difference",
            scope_fields=scope_fields,
            parameters=dict(trial["parameters"]),
        )
        for trial in BACKTEST_DIGIT_SHADOW_TRIALS
    ]
    published_trials_by_strategy = {
        str(trial["strategy"]): trial
        for trial in published_trials
    }
    sensitivity_trials: list[dict[str, Any]] = []
    for window in registered_windows:
        for strategy_config in BACKTEST_WINDOW_SENSITIVITY_STRATEGIES:
            strategy = str(strategy_config["strategy"])
            if window == recent_window:
                sensitivity_trials.append(published_trials_by_strategy[strategy])
                continue
            sensitivity_trials.append(
                _backtest_trial_row(
                    trial_id=f"{strategy}:recent_window_{window}",
                    strategy=strategy,
                    label=f"{strategy_config['label']} - cửa sổ {window} kỳ",
                    variant_role="registered_parameter_variant",
                    differences=evaluation_window_differences[(strategy, window)],
                    metric_key="mean_position_match_difference",
                    scope_fields=scope_fields,
                    parameters=_backtest_window_trial_parameters(
                        product_kind="digit_sequence",
                        strategy=strategy,
                        recent_window=window,
                        primary_recent_window=recent_window,
                        short_window=short_window,
                    ),
                    metadata=_backtest_window_trial_metadata(
                        window=window,
                        primary_recent_window=recent_window,
                    ),
                )
            )
    registered_window_trials = [
        row
        for row in sensitivity_trials
        if row.get("window_sensitivity_role") == "registered_alternative_window"
    ]
    multiple_testing_trials = _backtest_multiple_testing_trials(
        "digit_sequence",
        "mean_position_match_difference",
        [*published_trials, *shadow_trials, *registered_window_trials],
    )
    window_sensitivity = _backtest_window_sensitivity(
        "digit_sequence",
        "mean_position_match_difference",
        recent_window,
        sensitivity_trials,
    )
    trial_disposition_log = _backtest_trial_disposition_log(
        "digit_sequence",
        "mean_position_match_difference",
        multiple_testing_trials["trials"],
        scope_fields,
    )
    report = {
        "schema_version": 2,
        "status": "complete",
        "method": "walk_forward",
        "samples": samples,
        "walk_forward_samples": len(model_best),
        "target_scope": target_scope,
        "phase_split": phase_split,
        "multiple_testing_trials": multiple_testing_trials,
        "window_sensitivity": window_sensitivity,
        "trial_disposition_log": trial_disposition_log,
        "score_formulas": _digit_backtest_score_formulas(),
        "first_walk_forward_draw_id": observations[start].draw_id,
        "first_test_draw_id": observations[evaluation_start_index].draw_id,
        "latest_test_draw_id": observations[-1].draw_id,
        "initial_training_draws": evaluation_start_index,
        "initial_walk_forward_training_draws": start,
        "minimum_history_draws": minimum_history,
        "recent_window_draws": recent_window,
        "short_window_draws": short_window,
        "symbol_min": product.sequence_min,
        "symbol_max": product.sequence_max,
        "score_policy": DIGIT_SCORE_POLICY,
        "audit_score_policy": AUDIT_DIGIT_SCORE_POLICY,
        "model": {
            "strategy": "balanced_signal",
            **scope_fields,
            "exact_hits": evaluation_model_exact,
            "exact_hit_rate": _round(evaluation_model_exact / samples),
            "average_best_position_matches": _round(fmean(evaluation_model_best)),
        },
        "recent_model": {
            "strategy": "recent_frequency",
            **scope_fields,
            "exact_hits": evaluation_recent_exact,
            "exact_hit_rate": _round(evaluation_recent_exact / samples),
            "average_best_position_matches": _round(fmean(evaluation_recent_best)),
        },
        "audit_model": {
            "strategy": "audit_signal",
            **scope_fields,
            "exact_hits": evaluation_audit_exact,
            "exact_hit_rate": _round(evaluation_audit_exact / samples),
            "average_best_position_matches": _round(fmean(evaluation_audit_best)),
        },
        "baseline": {
            "strategy": "uniform_exact_expectation",
            "method": "exact_sequence_enumeration",
            **scope_fields,
            "candidate_space_size": len(symbols) ** length,
            "expected_exact_hits": _round(sum(evaluation_baseline_exact_expected)),
            "expected_exact_hit_rate": _round(fmean(evaluation_baseline_exact_expected)),
            "average_best_position_matches": _round(
                fmean(evaluation_baseline_best_expected)
            ),
            "score_distribution": _expected_counter_to_rows(
                evaluation_baseline_distribution,
                samples,
            ),
            "partial_match_baseline": _digit_partial_match_baseline(
                evaluation_baseline_distribution,
                samples,
                length,
                len(symbols) ** length,
            ),
        },
        "comparison": {
            **scope_fields,
            "mean_position_match_difference": _round(fmean(differences)),
            "paired_z_score": _round(z_score),
            "approximate_p_value": _round(p_value, 8),
            **difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="balanced_signal:published_final",
                differences=differences,
                metric_key="mean_position_match_difference",
            ),
            "beats_baseline_unadjusted": comparison_wins,
            "beats_baseline": False,
        },
        "recent_comparison": {
            **scope_fields,
            "mean_position_match_difference": _round(
                fmean(recent_differences)
            ),
            "paired_z_score": _round(recent_z_score),
            "approximate_p_value": _round(recent_p_value, 8),
            **recent_difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="recent_frequency:published_final",
                differences=recent_differences,
                metric_key="mean_position_match_difference",
            ),
            "beats_baseline_unadjusted": recent_comparison_wins,
            "beats_baseline": False,
        },
        "audit_comparison": {
            **scope_fields,
            "mean_position_match_difference": _round(fmean(audit_differences)),
            "paired_z_score": _round(audit_z_score),
            "approximate_p_value": _round(audit_p_value, 8),
            **audit_difference_interval,
            "block_bootstrap_check": _backtest_block_bootstrap_check(
                test_id="audit_signal:published_final",
                differences=audit_differences,
                metric_key="mean_position_match_difference",
            ),
            "beats_baseline_unadjusted": audit_comparison_wins,
            "beats_baseline": False,
        },
        "warning": (
            "Baseline được tính chính xác trên toàn bộ không gian chuỗi hợp lệ của từng kỳ. "
            "Các kết quả cùng một kỳ ở trò chơi nhiều hạng giải không hoàn toàn độc lập, "
            "vì vậy p-value vẫn chỉ là xấp xỉ và cần được đọc cùng kích thước hiệu ứng."
        ),
    }
    _validate_backtest_target_scope(report)
    _validate_backtest_phase_split(report)
    _validate_backtest_multiple_testing_trials(report)
    _validate_backtest_window_sensitivity(report)
    _validate_backtest_block_bootstrap_checks(report)
    _validate_backtest_trial_disposition_log(report)
    return report


def _number_scores(
    product: AnalyticsProduct,
    total_counts: Counter[int],
    total_draws: int,
    recent_counts: Counter[int],
    recent_draws: int,
    short_counts: Counter[int],
    short_draws: int,
    last_seen: dict[int, int],
    current_index: int,
) -> dict[int, dict[str, float]]:
    probability = (product.pick_count or 0) / product.pool_size
    total_sd = math.sqrt(max(total_draws * probability * (1 - probability), 1e-12))
    recent_sd = math.sqrt(max(recent_draws * probability * (1 - probability), 1e-12))
    short_sd = math.sqrt(max(short_draws * probability * (1 - probability), 1e-12))
    scores = {}
    for value in range(product.pool_min or 1, (product.pool_max or 0) + 1):
        long_z = (
            (total_counts[value] - total_draws * probability) / total_sd
            if total_draws
            else 0.0
        )
        recent_z = (
            (recent_counts[value] - recent_draws * probability) / recent_sd
            if recent_draws
            else 0.0
        )
        short_z = (
            (short_counts[value] - short_draws * probability) / short_sd
            if short_draws
            else 0.0
        )
        draws_since = current_index - 1 - last_seen.get(value, -1)
        overdue_ratio = min(4.0, draws_since * probability)
        scores[value] = {
            "long_z": long_z,
            "recent_z": recent_z,
            "short_z": short_z,
            "overdue_ratio": overdue_ratio,
            "recent": 0.6 * short_z + 0.4 * recent_z,
            "balanced_no_overdue": 0.4 * short_z + 0.3 * recent_z - 0.15 * long_z,
            "balanced": (
                0.4 * short_z
                + 0.3 * recent_z
                - 0.15 * long_z
                + 0.15 * (overdue_ratio - 1)
            ),
        }
    return scores


def _number_pair_counts(observations: Iterable[Observation]) -> Counter[tuple[int, int]]:
    counts: Counter[tuple[int, int]] = Counter()
    for observation in observations:
        _update_number_pair_counts(counts, observation, 1)
    return counts


def _update_number_pair_counts(
    counts: Counter[tuple[int, int]],
    observation: Observation,
    direction: int,
) -> None:
    values = sorted(set(observation.values))
    for pair in combinations(values, 2):
        counts[pair] += direction
        if counts[pair] <= 0:
            del counts[pair]


def _number_pair_scores_from_counts(
    product: AnalyticsProduct,
    pair_counts: Counter[tuple[int, int]],
    draw_count: int,
) -> dict[tuple[int, int], float]:
    pick_count = product.pick_count or 0
    pool_size = product.pool_size
    if draw_count <= 0 or pick_count < 2 or pool_size < 2:
        return {}
    probability = pick_count * (pick_count - 1) / (pool_size * (pool_size - 1))
    expected = draw_count * probability
    sd = math.sqrt(max(draw_count * probability * (1 - probability), 1e-12))
    return {
        pair: _clip_signal((count - expected) / sd)
        for pair, count in pair_counts.items()
    }


def _apply_audit_number_scores(
    scores: dict[int, dict[str, float]],
    pair_scores: dict[tuple[int, int], float],
) -> None:
    pair_pressures = _number_pair_pressures(pair_scores)
    for value, row in scores.items():
        pair_pressure = pair_pressures.get(value, 0.0)
        row["audit_pair_pressure"] = pair_pressure
        row["audit"] = (
            0.45 * _clip_signal(row["long_z"])
            + 0.25 * _clip_signal(row["recent_z"])
            + 0.15 * _clip_signal(row["short_z"])
            + 0.15 * pair_pressure
        )


def _number_pair_pressures(
    pair_scores: dict[tuple[int, int], float],
) -> dict[int, float]:
    buckets: dict[int, list[float]] = defaultdict(list)
    for (left, right), score in pair_scores.items():
        if score <= 0:
            continue
        buckets[left].append(score)
        buckets[right].append(score)
    return {
        value: fmean(nlargest(5, values))
        for value, values in buckets.items()
    }


def _audit_number_pick(
    scores: dict[int, dict[str, float]],
    pair_scores: dict[tuple[int, int], float],
    count: int,
    seed: str,
) -> list[int]:
    selected: list[int] = []
    remaining = set(scores)
    while remaining and len(selected) < count:
        value = max(
            remaining,
            key=lambda candidate: (
                scores[candidate]["audit"]
                + 0.12 * _selected_pair_bonus(candidate, selected, pair_scores),
                _stable_jitter(seed, candidate),
            ),
        )
        selected.append(value)
        remaining.remove(value)
    return sorted(selected)


def _selected_pair_bonus(
    value: int,
    selected: list[int],
    pair_scores: dict[tuple[int, int], float],
) -> float:
    if not selected:
        return 0.0
    bonuses = [
        max(0.0, pair_scores.get(tuple(sorted((value, other))), 0.0))
        for other in selected
    ]
    return fmean(bonuses)


def _top_numbers(
    scores: dict[int, dict[str, float]],
    key: str,
    count: int,
    seed: str,
) -> list[int]:
    ranked = sorted(
        scores,
        key=lambda value: (scores[value][key], _stable_jitter(seed, value)),
        reverse=True,
    )
    return sorted(ranked[:count])


def _uniform_number_pick(product: AnalyticsProduct, seed: str) -> list[int]:
    rng = random.Random(_seed_int(seed + "|uniform"))
    values = list(range(product.pool_min or 1, (product.pool_max or 0) + 1))
    return sorted(rng.sample(values, product.pick_count or 0))


def _special_forecasts(dataset: ProductDataset, seed: str) -> dict[str, list[int]]:
    product = dataset.product
    if not product.special_count or product.special_min is None or product.special_max is None:
        return {}
    observations = [item for item in dataset.observations if item.special_values]
    total_counts = Counter(value for item in observations for value in item.special_values)
    recent_window = min(200, len(observations))
    short_window = min(50, len(observations))
    recent_counts = Counter(
        value for item in observations[-recent_window:] for value in item.special_values
    )
    short_counts = Counter(
        value for item in observations[-short_window:] for value in item.special_values
    )
    pool = list(range(product.special_min, product.special_max + 1))
    expected = product.special_count / len(pool)
    total_sd = math.sqrt(max(len(observations) * expected * (1 - expected), 1e-12))
    recent_sd = math.sqrt(max(recent_window * expected * (1 - expected), 1e-12))
    short_sd = math.sqrt(max(short_window * expected * (1 - expected), 1e-12))
    score_rows = {}
    for value in pool:
        long_z = (total_counts[value] - len(observations) * expected) / total_sd
        recent_z = (recent_counts[value] - recent_window * expected) / recent_sd
        short_z = (short_counts[value] - short_window * expected) / short_sd
        score_rows[value] = {
            "balanced": 0.4 * short_z + 0.3 * recent_z - 0.2 * long_z,
            "recent": 0.6 * short_z + 0.4 * recent_z,
            "audit": (
                0.5 * _clip_signal(long_z)
                + 0.3 * _clip_signal(recent_z)
                + 0.2 * _clip_signal(short_z)
            ),
        }
    rng = random.Random(_seed_int(seed + "|special"))
    return {
        "uniform_seeded": sorted(rng.sample(pool, product.special_count)),
        "balanced_signal": _top_numbers(
            score_rows,
            "balanced",
            product.special_count,
            seed + "|special",
        ),
        "recent_frequency": _top_numbers(
            score_rows,
            "recent",
            product.special_count,
            seed + "|special",
        ),
        "audit_signal": _top_numbers(
            score_rows,
            "audit",
            product.special_count,
            seed + "|special",
        ),
    }


def _digit_sequence_from_scores(
    total: list[Counter[int]],
    recent: list[Counter[int]],
    short: list[Counter[int]],
    symbols: list[int],
    strategy: str,
    seed: str,
) -> str:
    result = []
    for position, (total_counter, recent_counter, short_counter) in enumerate(
        zip(total, recent, short, strict=True)
    ):
        total_observations = sum(total_counter.values())
        recent_observations = sum(recent_counter.values())
        short_observations = sum(short_counter.values())
        probability = 1 / len(symbols)
        expected_total = total_observations * probability if total_observations else 0
        expected_recent = recent_observations * probability if recent_observations else 0
        expected_short = short_observations * probability if short_observations else 0
        total_sd = math.sqrt(max(total_observations * probability * (1 - probability), 1e-12))
        recent_sd = math.sqrt(max(recent_observations * probability * (1 - probability), 1e-12))
        short_sd = math.sqrt(max(short_observations * probability * (1 - probability), 1e-12))
        scores = {}
        for digit in symbols:
            long_z = (
                (total_counter[digit] - expected_total) / total_sd
                if total_observations
                else 0
            )
            recent_z = (
                (recent_counter[digit] - expected_recent) / recent_sd
                if recent_observations
                else 0
            )
            short_z = (
                (short_counter[digit] - expected_short) / short_sd
                if short_observations
                else 0
            )
            if strategy == "recent":
                score = 0.6 * short_z + 0.4 * recent_z
            elif strategy == "audit":
                score = (
                    0.45 * _clip_signal(long_z)
                    + 0.35 * _clip_signal(recent_z)
                    + 0.2 * _clip_signal(short_z)
                )
            elif strategy == "short":
                score = short_z
            elif strategy == "long":
                score = long_z
            elif strategy == "balanced_no_long_penalty":
                score = 0.4 * short_z + 0.3 * recent_z
            elif strategy == "audit_unclipped":
                score = 0.45 * long_z + 0.35 * recent_z + 0.2 * short_z
            else:
                score = 0.4 * short_z + 0.3 * recent_z - 0.2 * long_z
            scores[digit] = score + _stable_jitter(f"{seed}|{position}", digit) * 1e-6
        result.append(str(max(scores, key=scores.get)))
    return "".join(result)


def _evaluation_event(
    prediction: dict[str, Any],
    actual: Observation,
    dataset: ProductDataset,
) -> dict[str, Any]:
    product = dataset.product
    predicted = prediction["prediction"]
    if product.kind is AnalysisKind.NUMBER_SET:
        numbers = set(int(value) for value in predicted.get("numbers", []))
        actual_numbers = set(actual.values)
        predicted_special = set(int(value) for value in predicted.get("special_numbers", []))
        actual_special = set(actual.special_values)
        special_exact = (
            predicted_special == actual_special
            if product.special_count
            else True
        )
        metrics = {
            "hit_count": len(numbers.intersection(actual_numbers)),
            "main_exact_hit": numbers == actual_numbers,
            "special_exact_hit": special_exact,
            "exact_hit": numbers == actual_numbers and special_exact,
            "special_hit_count": len(predicted_special.intersection(actual_special)),
        }
        actual_result: dict[str, object] = {
            "numbers": list(actual.values),
            "special_numbers": list(actual.special_values),
        }
    else:
        sequence = str(predicted.get("sequence", ""))
        actual_set = set(actual.outcomes)
        metrics = {
            "exact_hit": sequence in actual_set,
            "best_position_matches": _best_position_match(sequence, actual_set),
        }
        actual_result = {"outcomes": list(actual.outcomes)}
    identity = f"{prediction['prediction_id']}|{actual.draw_id}"
    return {
        "event_type": "evaluation",
        "evaluation_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
        "prediction_id": prediction["prediction_id"],
        "product": product.slug,
        "strategy": prediction["strategy"],
        "model_version": prediction["model_version"],
        "evaluated_at": dataset.latest_fetched_at
        or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "actual_draw_id": actual.draw_id,
        "actual_draw_date": actual.draw_date.isoformat(),
        "actual_result": actual_result,
        "metrics": metrics,
    }


def _first_observation_after(
    observations: list[Observation],
    prediction: dict[str, Any],
) -> Observation | None:
    cutoff_date = prediction["dataset_cutoff_date"]
    cutoff_id = prediction["dataset_cutoff_draw_id"]
    cutoff_key = (
        cutoff_date,
        int(cutoff_id) if str(cutoff_id).isdigit() else str(cutoff_id),
    )
    for observation in observations:
        key = (
            observation.draw_date.isoformat(),
            int(observation.draw_id) if observation.draw_id.isdigit() else observation.draw_id,
        )
        if key > cutoff_key:
            return observation
    return None


def _prediction_order(
    prediction: dict[str, Any],
) -> tuple[str, int | str, tuple[int, ...], str, str]:
    draw_id = str(prediction["dataset_cutoff_draw_id"])
    draw_key: int | str = int(draw_id) if draw_id.isdigit() else draw_id
    return (
        prediction["dataset_cutoff_date"],
        draw_key,
        _version_key(str(prediction.get("model_version", ""))),
        str(prediction.get("generated_at", "")),
        str(prediction.get("prediction_id", "")),
    )


def _version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _evaluation_detail(
    prediction: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    product = PRODUCTS.get(str(prediction["product"]))
    metrics = evaluation["metrics"]
    predicted_result = prediction["prediction"]
    actual_result = evaluation["actual_result"]
    if "hit_count" in metrics:
        predicted_numbers = {
            int(value) for value in predicted_result.get("numbers", [])
        }
        actual_numbers = {
            int(value) for value in actual_result.get("numbers", [])
        }
        predicted_special = {
            int(value) for value in predicted_result.get("special_numbers", [])
        }
        actual_special = {
            int(value) for value in actual_result.get("special_numbers", [])
        }
        matched_numbers = sorted(predicted_numbers.intersection(actual_numbers))
        matched_special = sorted(predicted_special.intersection(actual_special))
        required_units = len(predicted_numbers) + len(predicted_special)
        matched_units = len(matched_numbers) + len(matched_special)
        exact = (
            predicted_numbers == actual_numbers
            and predicted_special == actual_special
        )
        near = not exact and required_units > 0 and matched_units == required_units - 1
        score_kind = "numbers"
        score = len(matched_numbers)
        score_total = len(predicted_numbers)
        score_label = f"{score}/{score_total} số chính"
        if predicted_special:
            score_label += (
                f", {len(matched_special)}/{len(predicted_special)} số đặc biệt"
            )
        comparison = {
            "matched_numbers": matched_numbers,
            "missed_numbers": sorted(predicted_numbers - actual_numbers),
            "actual_only_numbers": sorted(actual_numbers - predicted_numbers),
            "matched_special_numbers": matched_special,
        }
        baseline_probability = _number_outcome_probability(
            product,
            predicted_numbers=len(predicted_numbers),
            actual_numbers=len(actual_numbers),
            predicted_special=len(predicted_special),
            actual_special=len(actual_special),
            matched_units=matched_units,
            score=score,
            required_units=required_units,
        )
    else:
        sequence = str(predicted_result.get("sequence", ""))
        outcomes = {str(value) for value in actual_result.get("outcomes", [])}
        best_outcome = _best_matching_outcome(sequence, outcomes)
        matched_positions = [
            index
            for index, (left, right) in enumerate(
                zip(sequence, best_outcome, strict=False)
            )
            if left == right
        ]
        required_units = len(sequence)
        matched_units = len(matched_positions)
        exact = sequence in outcomes
        near = not exact and required_units > 0 and matched_units == required_units - 1
        score_kind = "positions"
        score = matched_units
        score_total = required_units
        score_label = f"{score}/{score_total} vị trí"
        comparison = {
            "best_matching_outcome": best_outcome,
            "matched_positions": matched_positions,
        }
        baseline_probability = _digit_outcome_probability(
            product,
            outcomes=outcomes,
            score=score,
            required_units=required_units,
        )

    status = "exact" if exact else "near" if near else "wrong"
    return {
        **evaluation,
        "strategy_label": prediction.get("strategy_label", prediction["strategy"]),
        "prediction_generated_at": prediction["generated_at"],
        "dataset_cutoff_draw_id": prediction["dataset_cutoff_draw_id"],
        "dataset_cutoff_date": prediction["dataset_cutoff_date"],
        "dataset_fingerprint": prediction["dataset_fingerprint"],
        "prediction": predicted_result,
        "outcome": {
            "status": status,
            "status_label": {
                "exact": "Đúng toàn bộ",
                "near": "Gần đúng",
                "wrong": "Sai",
            }[status],
            "score_kind": score_kind,
            "score": score,
            "score_total": score_total,
            "score_label": score_label,
            "matched_units": matched_units,
            "required_units": required_units,
            "baseline_probability": baseline_probability,
            "has_partial_match": not exact and matched_units > 0,
            **comparison,
        },
    }


def _pending_prediction_detail(prediction: dict[str, Any]) -> dict[str, Any]:
    return {
        "prediction_id": prediction["prediction_id"],
        "product": prediction["product"],
        "strategy": prediction["strategy"],
        "strategy_label": prediction.get("strategy_label", prediction["strategy"]),
        "model_version": prediction["model_version"],
        "prediction_generated_at": prediction["generated_at"],
        "dataset_cutoff_draw_id": prediction["dataset_cutoff_draw_id"],
        "dataset_cutoff_date": prediction["dataset_cutoff_date"],
        "dataset_fingerprint": prediction["dataset_fingerprint"],
        "prediction": prediction["prediction"],
        "target": prediction.get("target", "first_confirmed_draw_after_cutoff"),
    }


def _evaluation_order(evaluation: dict[str, Any]) -> tuple[str, int | str, str, str]:
    draw_id = str(evaluation["actual_draw_id"])
    draw_key: int | str = int(draw_id) if draw_id.isdigit() else draw_id
    return (
        str(evaluation["actual_draw_date"]),
        draw_key,
        str(evaluation.get("prediction_generated_at", "")),
        str(evaluation.get("prediction_id", "")),
    )


def _score_distribution(rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    counts = Counter(int(row["outcome"]["score"]) for row in rows)
    return [
        {"score": score, "count": counts[score]}
        for score in range(max(counts, default=0) + 1)
        if counts[score]
    ]


def _expected_outcome_count(rows: list[dict[str, Any]], key: str) -> float:
    return sum(
        float(row["outcome"].get("baseline_probability", {}).get(key, 0.0))
        for row in rows
    )


def _number_outcome_probability(
    product: AnalyticsProduct | None,
    *,
    predicted_numbers: int,
    actual_numbers: int,
    predicted_special: int,
    actual_special: int,
    matched_units: int,
    score: int,
    required_units: int,
) -> dict[str, object]:
    if product is None or product.kind is not AnalysisKind.NUMBER_SET:
        return _empty_probability("unknown")
    main_distribution = _hypergeometric_match_distribution(
        product.pool_size,
        actual_numbers,
        predicted_numbers,
    )
    if predicted_special:
        special_pool_size = (
            product.special_max - product.special_min + 1
            if product.special_min is not None and product.special_max is not None
            else product.pool_size
        )
        special_distribution = _hypergeometric_match_distribution(
            special_pool_size,
            actual_special,
            predicted_special,
        )
    else:
        special_distribution = {0: 1.0}

    combined: dict[int, float] = defaultdict(float)
    for main_hits, main_probability in main_distribution.items():
        for special_hits, special_probability in special_distribution.items():
            combined[main_hits + special_hits] += main_probability * special_probability

    near_units = required_units - 1
    return {
        "model": "uniform_same_ticket_shape",
        "score_basis": "main_numbers",
        "exact": _significant(combined.get(required_units, 0.0)),
        "near": _significant(combined.get(near_units, 0.0) if near_units >= 0 else 0.0),
        "matched_units": _significant(combined.get(matched_units, 0.0)),
        "score": _significant(main_distribution.get(score, 0.0)),
    }


def _digit_outcome_probability(
    product: AnalyticsProduct | None,
    *,
    outcomes: set[str],
    score: int,
    required_units: int,
) -> dict[str, object]:
    if product is None or product.kind is not AnalysisKind.DIGIT_SEQUENCE:
        return _empty_probability("unknown")
    symbols = list(range(product.sequence_min, product.sequence_max + 1))
    length = product.sequence_length or required_units
    _, exact_probability, score_distribution = _digit_uniform_expectation(
        outcomes,
        symbols,
        length,
    )
    near_score = required_units - 1
    return {
        "model": "uniform_sequence_enumeration",
        "score_basis": "best_position_matches",
        "candidate_space_size": len(symbols) ** length,
        "actual_outcomes": len(outcomes),
        "exact": _significant(exact_probability),
        "near": _significant(
            score_distribution.get(near_score, 0.0) if near_score >= 0 else 0.0
        ),
        "matched_units": _significant(score_distribution.get(score, 0.0)),
        "score": _significant(score_distribution.get(score, 0.0)),
    }


def _hypergeometric_match_distribution(
    pool_size: int,
    actual_successes: int,
    picks: int,
) -> dict[int, float]:
    if pool_size <= 0 or picks < 0 or actual_successes < 0 or picks > pool_size:
        return {}
    denominator = math.comb(pool_size, picks)
    minimum_hits = max(0, picks - (pool_size - actual_successes))
    maximum_hits = min(picks, actual_successes)
    return {
        hits: (
            math.comb(actual_successes, hits)
            * math.comb(pool_size - actual_successes, picks - hits)
            / denominator
        )
        for hits in range(minimum_hits, maximum_hits + 1)
    }


def _empty_probability(model: str) -> dict[str, object]:
    return {
        "model": model,
        "exact": 0.0,
        "near": 0.0,
        "matched_units": 0.0,
        "score": 0.0,
    }


def _best_position_match(prediction: str, outcomes: set[str]) -> int:
    if not outcomes:
        return 0
    return max(
        sum(
            left == right
            for left, right in zip(prediction, outcome, strict=False)
        )
        for outcome in outcomes
    )


def _best_matching_outcome(prediction: str, outcomes: set[str]) -> str:
    if not outcomes:
        return ""
    return max(
        sorted(outcomes),
        key=lambda outcome: (
            sum(
                left == right
                for left, right in zip(prediction, outcome, strict=False)
            ),
            outcome,
        ),
    )


def _update_digit_counts(
    counters: list[Counter[int]],
    outcomes: tuple[str, ...],
    direction: int,
) -> None:
    for outcome in outcomes:
        for position, char in enumerate(outcome):
            counters[position][int(char)] += direction


def _paired_normal_test(differences: list[float]) -> tuple[float, float]:
    if len(differences) < 2 or stdev(differences) == 0:
        return 0.0, 1.0
    z_score = fmean(differences) / (stdev(differences) / math.sqrt(len(differences)))
    p_value = 2 * (1 - NORMAL.cdf(abs(z_score)))
    return z_score, max(0.0, min(1.0, p_value))


def _normal_mean_interval(differences: list[float]) -> dict[str, float]:
    mean_difference = fmean(differences)
    if len(differences) < 2:
        return {
            "standard_error": 0.0,
            "confidence_level": 0.95,
            "confidence_interval_lower": _round(mean_difference),
            "confidence_interval_upper": _round(mean_difference),
        }
    standard_error = stdev(differences) / math.sqrt(len(differences))
    margin = NORMAL.inv_cdf(0.975) * standard_error
    return {
        "standard_error": _round(standard_error),
        "confidence_level": 0.95,
        "confidence_interval_lower": _round(mean_difference - margin),
        "confidence_interval_upper": _round(mean_difference + margin),
    }


def _backtest_block_bootstrap_check(
    *,
    test_id: str,
    differences: list[float],
    metric_key: str,
) -> dict[str, Any]:
    full_count = len(differences)
    if full_count < BACKTEST_BLOCK_BOOTSTRAP_MIN_VALUES:
        return {
            "status": "not_available",
            "method": "moving_block_bootstrap",
            "reason": "Không đủ kỳ đánh giá cuối để chạy block bootstrap đã khóa.",
            "minimum_values": BACKTEST_BLOCK_BOOTSTRAP_MIN_VALUES,
            "full_value_count": full_count,
            "no_multiple_testing_decision": True,
        }

    sampled_values, sampling_method = _backtest_bootstrap_sample_values(
        differences,
        BACKTEST_BLOCK_BOOTSTRAP_MAX_VALUES,
    )
    block_length = _backtest_block_bootstrap_length(len(sampled_values))
    starts = list(range(0, max(1, len(sampled_values) - block_length + 1)))
    seed_hex, _values_hash = _backtest_diagnostic_seed(
        test_id=test_id,
        values=differences,
        method_version="backtest-block-bootstrap-v1",
    )
    rng = random.Random(int(seed_hex, 16))
    statistics: list[float] = []
    for _ in range(BACKTEST_BLOCK_BOOTSTRAP_RESAMPLES):
        bootstrapped: list[float] = []
        while len(bootstrapped) < len(sampled_values):
            start = rng.choice(starts)
            bootstrapped.extend(sampled_values[start : start + block_length])
        statistics.append(fmean(bootstrapped[: len(sampled_values)]))

    ordered = sorted(statistics)
    alpha = 1 - BACKTEST_BLOCK_BOOTSTRAP_CONFIDENCE_LEVEL
    lower = _percentile(ordered, alpha / 2)
    upper = _percentile(ordered, 1 - alpha / 2)
    normal = _normal_mean_interval(sampled_values)
    normal_lower = float(normal["confidence_interval_lower"])
    normal_upper = float(normal["confidence_interval_upper"])
    return {
        "status": "available",
        "method": "moving_block_bootstrap",
        "resamples": BACKTEST_BLOCK_BOOTSTRAP_RESAMPLES,
        "seed": seed_hex,
        "statistic_name": metric_key,
        "observed_statistic": _round(fmean(sampled_values)),
        "bootstrap_mean": _round(fmean(statistics)),
        "confidence_level": BACKTEST_BLOCK_BOOTSTRAP_CONFIDENCE_LEVEL,
        "confidence_interval_lower": _round(lower),
        "confidence_interval_upper": _round(upper),
        "block_length": block_length,
        "full_value_count": full_count,
        "bootstrap_value_count": len(sampled_values),
        "sampling_method": sampling_method,
        "preserve_time_structure": "contiguous_observation_blocks",
        "normal_approximation": {
            "method": "paired_normal_mean_interval",
            "confidence_level": normal["confidence_level"],
            "standard_error": normal["standard_error"],
            "confidence_interval_lower": normal["confidence_interval_lower"],
            "confidence_interval_upper": normal["confidence_interval_upper"],
        },
        "interval_overlap_with_normal_approximation": (
            max(float(lower), normal_lower) <= min(float(upper), normal_upper)
        ),
        "no_multiple_testing_decision": True,
        "interpretation": (
            "Block bootstrap resample các đoạn kỳ liên tiếp để kiểm tra độ bền của "
            "khoảng chuẩn; chẩn đoán này không đổi p/q/status chính."
        ),
    }


def _backtest_bootstrap_sample_values(
    values: list[float],
    max_values: int,
) -> tuple[list[float], str]:
    if len(values) <= max_values:
        return list(values), "full_sequence"
    step = len(values) / max_values
    sampled = [
        values[min(len(values) - 1, int(index * step))]
        for index in range(max_values)
    ]
    return sampled, "deterministic_even_spacing"


def _backtest_diagnostic_seed(
    *,
    test_id: str,
    values: list[float],
    method_version: str,
) -> tuple[str, str]:
    value_text = json.dumps(
        [_round(value, 12) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    values_hash = hashlib.sha256(value_text.encode()).hexdigest()
    seed_hex = hashlib.sha256(
        f"{test_id}:{len(values)}:{values_hash}:{method_version}".encode()
    ).hexdigest()[:16]
    return seed_hex, values_hash


def _backtest_block_bootstrap_length(value_count: int) -> int:
    return max(4, min(50, round(math.sqrt(value_count))))


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * max(0.0, min(1.0, probability))
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    fraction = position - lower_index
    return sorted_values[lower_index] * (1 - fraction) + sorted_values[upper_index] * fraction


def _number_partial_match_baseline(
    product: AnalyticsProduct,
    samples: int,
    distribution_rows: list[dict[str, float | int]],
) -> dict[str, object]:
    pick_count = product.pick_count or 0
    probability_by_hits = {
        int(row["hits"]): float(row["probability"])
        for row in distribution_rows
    }
    exact_probability = probability_by_hits.get(pick_count, 0.0)
    near_hits = pick_count - 1
    near_probability = (
        probability_by_hits.get(near_hits, 0.0)
        if 0 <= near_hits < pick_count
        else 0.0
    )
    zero_probability = probability_by_hits.get(0, 0.0)
    partial_probability = sum(
        probability
        for hits, probability in probability_by_hits.items()
        if 0 < hits < pick_count
    )
    any_match_probability = 1 - zero_probability
    return {
        "schema_version": 1,
        "method": "exact_hypergeometric_distribution",
        "score_basis": "main_number_hits",
        "score_unit": "main_number_hits_per_draw",
        "samples": samples,
        "pool_size": product.pool_size,
        "pick_count": pick_count,
        "partial_match_rule": "0 < hit_count_t < pick_count",
        "near_rule": "hit_count_t == pick_count - 1 and not exact",
        "zero_match_rule": "hit_count_t == 0",
        "exact_probability": _significant(exact_probability),
        "near_probability": _significant(near_probability),
        "partial_match_probability": _significant(partial_probability),
        "zero_match_probability": _significant(zero_probability),
        "any_match_probability": _significant(any_match_probability),
        "expected_exact_count": _round(samples * exact_probability),
        "expected_near_count": _round(samples * near_probability),
        "expected_partial_match_count": _round(samples * partial_probability),
        "expected_zero_match_count": _round(samples * zero_probability),
        "expected_any_match_count": _round(samples * any_match_probability),
    }


def _number_uniform_distribution(
    pool_size: int,
    pick_count: int,
    samples: int,
) -> list[dict[str, float | int]]:
    denominator = math.comb(pool_size, pick_count)
    minimum_hits = max(0, 2 * pick_count - pool_size)
    rows = []
    for hits in range(minimum_hits, pick_count + 1):
        probability = (
            math.comb(pick_count, hits)
            * math.comb(pool_size - pick_count, pick_count - hits)
            / denominator
        )
        rows.append(
            {
                "hits": hits,
                "probability": _round(probability, 12),
                "expected_count": _round(samples * probability),
            }
        )
    return rows


def _digit_partial_match_baseline(
    expected_score_counts: Counter[int],
    samples: int,
    sequence_length: int,
    candidate_space_size: int,
) -> dict[str, object]:
    def probability(score: int) -> float:
        return float(expected_score_counts.get(score, 0.0)) / samples if samples else 0.0

    exact_probability = probability(sequence_length)
    near_score = sequence_length - 1
    near_probability = (
        probability(near_score) if 0 <= near_score < sequence_length else 0.0
    )
    zero_probability = probability(0)
    partial_probability = sum(
        probability(score) for score in range(1, sequence_length)
    )
    any_match_probability = 1 - zero_probability
    return {
        "schema_version": 1,
        "method": "exact_sequence_enumeration",
        "score_basis": "best_position_matches",
        "score_unit": "best_position_matches_per_draw",
        "samples": samples,
        "candidate_space_size": candidate_space_size,
        "sequence_length": sequence_length,
        "partial_match_rule": "0 < best_position_matches_t < sequence_length",
        "near_rule": "best_position_matches_t == sequence_length - 1 and not exact",
        "zero_match_rule": "best_position_matches_t == 0",
        "multi_outcome_policy": "score is the best match across actual outcomes in each draw",
        "exact_probability": _significant(exact_probability),
        "near_probability": _significant(near_probability),
        "partial_match_probability": _significant(partial_probability),
        "zero_match_probability": _significant(zero_probability),
        "any_match_probability": _significant(any_match_probability),
        "expected_exact_count": _round(samples * exact_probability),
        "expected_near_count": _round(samples * near_probability),
        "expected_partial_match_count": _round(samples * partial_probability),
        "expected_zero_match_count": _round(samples * zero_probability),
        "expected_any_match_count": _round(samples * any_match_probability),
    }


def _digit_uniform_expectation(
    outcomes: set[str],
    symbols: list[int],
    length: int,
) -> tuple[float, float, dict[int, float]]:
    symbol_set = set(symbols)
    valid_outcomes = {
        tuple(int(char) for char in outcome)
        for outcome in outcomes
        if len(outcome) == length
        and all(char.isdigit() and int(char) in symbol_set for char in outcome)
    }
    if not valid_outcomes:
        return 0.0, 0.0, {0: 1.0}

    space_size = len(symbols) ** length
    tail_probabilities = {0: 1.0}
    positions = tuple(range(length))
    exact_match_candidates = [set() for _ in range(length + 1)]
    for outcome in valid_outcomes:
        for matching_count in range(1, length + 1):
            for matching_positions in combinations(positions, matching_count):
                matching = set(matching_positions)
                mismatching_positions = [
                    position for position in positions if position not in matching
                ]
                replacement_options = [
                    [symbol for symbol in symbols if symbol != outcome[position]]
                    for position in mismatching_positions
                ]
                for replacements in cartesian_product(*replacement_options):
                    candidate = list(outcome)
                    for position, replacement in zip(
                        mismatching_positions,
                        replacements,
                        strict=True,
                    ):
                        candidate[position] = replacement
                    exact_match_candidates[matching_count].add(tuple(candidate))

    covered: set[tuple[int, ...]] = set()
    for threshold in range(length, 0, -1):
        covered.update(exact_match_candidates[threshold])
        tail_probabilities[threshold] = len(covered) / space_size

    score_distribution = {
        score: tail_probabilities[score]
        - tail_probabilities.get(score + 1, 0.0)
        for score in range(length + 1)
    }
    expected_best_match = sum(
        tail_probabilities[threshold] for threshold in range(1, length + 1)
    )
    exact_probability = tail_probabilities[length]
    return expected_best_match, exact_probability, score_distribution


def _expected_counter_to_rows(
    counter: Counter[int],
    samples: int,
) -> list[dict[str, float | int]]:
    return [
        {
            "matches": score,
            "expected_count": _round(counter[score]),
            "average_probability": _round(counter[score] / samples, 12),
        }
        for score in sorted(counter)
    ]


def _comparison_difference(comparison: dict[str, Any]) -> float:
    for key in ("mean_hit_difference", "mean_position_match_difference"):
        value = comparison.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    if not p_values:
        return []
    total = len(p_values)
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [1.0] * total
    running_minimum = 1.0
    for reverse_rank, (original_index, p_value) in enumerate(
        reversed(ordered),
        start=1,
    ):
        rank = total - reverse_rank + 1
        candidate = min(1.0, p_value * total / rank)
        running_minimum = min(running_minimum, candidate)
        adjusted[original_index] = running_minimum
    return adjusted


def _counter_to_rows(counter: Counter[int]) -> list[dict[str, int]]:
    return [{"hits": hits, "count": counter[hits]} for hits in sorted(counter)]


def _stable_jitter(seed: str, value: int) -> float:
    digest = hashlib.sha256(f"{seed}|{value}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _clip_signal(value: float, limit: float = 4.0) -> float:
    return max(-limit, min(limit, value))


def _seed_int(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _significant(value: float, digits: int = 12) -> float:
    return float(f"{float(value):.{digits}g}")


def _event_hash(event: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in event.items()
        if key != "event_hash"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ledger_integrity_payload(
    events: list[dict[str, Any]],
    status: str,
) -> dict[str, object]:
    root = events[-1].get("event_hash") if events else None
    return {
        "chain_version": LEDGER_CHAIN_VERSION,
        "algorithm": "sha256",
        "status": status,
        "event_count": len(events),
        "root_hash": root,
    }
