"""Online Data Siege defense: one relevant inspection per event, no file I/O.

Clean-practice reference statistics stay fixed so persistent faults cannot
redefine normal. Bounded direction priors reduce opposite-tail false alarms;
contracts use declared SLAs plus published bounds. No phase names, event-ID
answer tables or run seeds guide verdicts. Practice labels are used for fitting
the documented calibration parameters only, never supplied at runtime.
"""
from api import Verdict
from math import log


FEATURE_Z = 1.25
BATCH_Z = 1.5
CONTRACT_Z = 1.25
LINEAGE_Z = 1.0
EMBEDDING_Z = 1.0
DIRECTION_STRENGTH = 1.5
# Laplace-smoothed positive-direction probabilities from the published
# practice faults: volume 3/3, amount mean 1/1, serving skew 7/7.
POSITIVE_PRIORS = {"row_count": 0.8, "mean_amount": 2 / 3, "feature_shift": 8 / 9}
# Clean-practice maximum-likelihood centers under the published mean + 3-sigma
# constraint, normalized to each upper bound. See research/clean_calibration.json.
PRIOR_CENTERS = {"null_rate": 0.39858, "staleness": 0.49050, "freshness": 0.48228,
                 "duration": 0.81737, "centroid_shift": 0.44994, "doc_age": 0.54772}


def register(ctx):
    # RPCs belong in handlers: the event channel is not ready during register.
    ctx.on("data_batch", check_data_batch)
    ctx.on("contract_checkpoint", check_contract_checkpoint)
    ctx.on("lineage_run", check_lineage_run)
    ctx.on("feature_materialization", check_feature_materialization)
    ctx.on("embedding_batch", check_embedding_batch)


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _finite(value):
    return isinstance(value, (int, float)) and value == value and abs(value) != float("inf")


def _inspect(ctx, method, cost, *args):
    cache = ctx.state.setdefault("inspection_cache", {})
    key = (method, args)
    if key in cache:
        return cache[key]
    if "initial_budget" not in ctx.state:
        ctx.state["initial_budget"] = ctx.tools.budget_remaining()
    # One relevant call is the minimum needed to see this event's measurements.
    # Preserve coverage instead of going blind at the tail of a shorter budget.
    # No speculative calls, repeated inspections, or calls for future references.
    result = getattr(ctx.tools, method)(*args)
    ctx.state["inspection_spend"] = ctx.state.get("inspection_spend", 0.0) + cost
    if not isinstance(result, dict):
        return {"error": "invalid inspection response"}
    if "error" not in result:
        cache[key] = result
    return result


def _unavailable(result, pillar):
    if "error" in result:
        return Verdict(alert=False, confidence=0.0, pillar=pillar,
                       reason="inspection unavailable: " + str(result["error"]))
    return None


def _verdict(reasons, pillar):
    return Verdict(alert=bool(reasons), confidence=0.95 if reasons else 0.8,
                   pillar=pillar,
                   reason="; ".join(reasons) if reasons else "no abnormal signal observed")


def _upper(ctx, key, value, limit, label, reasons):
    """Compare against a fixed clean reference; keep bounded diagnostic history."""
    if not _finite(value):
        reasons.append(label + ": invalid measurement")
        return 10.0
    history = ctx.state.setdefault("metric_history", {}).setdefault(key, [])
    z = 0.0
    if value > limit:
        reasons.append(label + ": published limit exceeded")
        z = 3.1
    elif key[-1] in PRIOR_CENTERS:
        center = limit * PRIOR_CENTERS[key[-1]]
        sigma = max((limit - center) / 3.0, abs(limit) * 0.02, 1e-9)
        z = (value - center) / sigma
        thresholds = {"null_rate": BATCH_Z, "staleness": BATCH_Z,
                      "freshness": CONTRACT_Z, "duration": LINEAGE_Z,
                      "centroid_shift": EMBEDDING_Z, "doc_age": EMBEDDING_Z}
        threshold = thresholds[key[-1]]
        if z > threshold:
            reasons.append(label + ": calibrated one-sided deviation")
    if value <= limit:
        history.append(value)
        if len(history) > 64:
            del history[0]
    return z


def _directional_threshold(base, positive_probability, signed):
    probability = positive_probability if signed >= 0 else 1 - positive_probability
    # A Gaussian shift of two standard deviations gives this log-prior
    # correction. Keep both directions sensitive, even with a strong prior.
    correction = DIRECTION_STRENGTH * log(0.5 / probability) / 2.0
    return max(0.75, min(2.0, base + correction))


def _two_sided(value, low, high, label, reasons, metric=None):
    if not _finite(value):
        reasons.append(label + ": invalid measurement")
        return 10.0
    center = (low + high) / 2.0
    sigma = max((high - low) / 6.0, 1e-9)
    signed = (value - center) / sigma
    z = abs(signed)
    threshold = _directional_threshold(BATCH_Z, POSITIVE_PRIORS.get(metric, 0.5), signed)
    if z > threshold:
        reasons.append(label + ": calibrated distribution deviation")
    return z


def check_data_batch(payload, ctx):
    r = _inspect(ctx, "batch_profile", 1.0, payload["batch_id"])
    unavailable = _unavailable(r, "checks")
    if unavailable is not None:
        return unavailable
    b, reasons = ctx.baseline, []
    group = payload.get("table", "orders")
    signals = [
        _two_sided(r["row_count"], b["row_count_min"], b["row_count_max"],
                   "batch volume", reasons, "row_count"),
        _two_sided(r["mean_amount"], b["mean_amount_min"], b["mean_amount_max"],
                   "amount mean", reasons, "mean_amount"),
        _upper(ctx, (group, "null_rate"), r["null_rate"]["customer_id"],
               b["null_rate_max"], "customer_id null rate", reasons),
        _upper(ctx, (group, "staleness"), r["staleness_min"],
               b["staleness_min_max"], "batch staleness", reasons),
    ]
    if not reasons and sum(z > 2.0 for z in signals) >= 2:
        reasons.append("multiple moderately abnormal batch measurements")
    # There is no published standard-deviation limit: learn this separately.
    std = r.get("std_amount")
    if _finite(std):
        history = ctx.state.setdefault("amount_std_history", {}).setdefault(group, [])
        if len(history) >= 10:
            center = _median(history)
            scale = max(1.4826 * _median([abs(x - center) for x in history]),
                        abs(center) * 0.15, 1e-9)
            if abs(std - center) > 3.5 * scale:
                reasons.append("amount spread changed")
        if not reasons:
            history.append(std)
            if len(history) > 64:
                del history[0]
    return _verdict(reasons, "checks")


def check_contract_checkpoint(payload, ctx):
    r = _inspect(ctx, "contract_diff", 1.5,
                 payload["contract_id"], payload["checkpoint_batch_id"])
    unavailable = _unavailable(r, "contracts")
    if unavailable is not None:
        return unavailable
    reasons = list(r["violations"])
    delay = r["freshness_delay_min"]
    if not _finite(delay):
        reasons.append("invalid contract freshness measurement")
    else:
        if delay > ctx.baseline["freshness_delay_max_min"]:
            reasons.append("contract freshness exceeds published clean bound")
        sla = payload.get("declared_sla", {}).get("freshness_min")
        if _finite(sla) and delay > sla:
            reasons.append("declared freshness SLA exceeded")
    return _verdict(reasons, "contracts")


def check_lineage_run(payload, ctx):
    r = _inspect(ctx, "lineage_graph_slice", 1.0, payload["run_id"])
    unavailable = _unavailable(r, "lineage")
    if unavailable is not None:
        return unavailable
    reasons = []
    upstream = set(r["actual_upstream"])
    declared = set(item["name"] for item in payload.get("inputs", [])
                   if isinstance(item, dict) and "name" in item)
    declared.update(payload.get("expected_upstream", []))
    if not declared.issubset(upstream):
        reasons.append("missing declared upstream: " + ", ".join(sorted(declared - upstream)))
    expected_outputs = payload.get("expected_downstream_count", len(payload.get("outputs", [])))
    if r["actual_downstream_count"] < max(1, expected_outputs):
        reasons.append("orphan or missing downstream output")
    job = payload.get("job", "default")
    graphs = ctx.state.setdefault("lineage_history", {}).setdefault(job, [])
    if len(graphs) >= 3:
        counts = {}
        for graph in graphs:
            for edge in graph:
                counts[edge] = counts.get(edge, 0) + 1
        stable = set(edge for edge, count in counts.items() if count >= 2)
        if not stable.issubset(upstream):
            reasons.append("missing historically stable upstream: " +
                           ", ".join(sorted(stable - upstream)))
    # Exclude structural faults from graph learning; use runtime history separately.
    if not reasons:
        graphs.append(tuple(sorted(upstream)))
        if len(graphs) > 64:
            del graphs[0]
    _upper(ctx, (job, "duration"), r["duration_ms"],
           ctx.baseline["lineage_duration_ms_max"], "transform runtime", reasons)
    return _verdict(reasons, "lineage")


def check_feature_materialization(payload, ctx):
    r = _inspect(ctx, "feature_drift", 2.0,
                 payload["feature_view"], payload["batch_id"])
    unavailable = _unavailable(r, "ai_infra")
    if unavailable is not None:
        return unavailable
    reasons = []
    if not all(_finite(r.get(field)) for field in ("train_std", "serve_mean", "train_mean", "mean_shift_sigma")) or r["train_std"] <= 0:
        return _verdict(["invalid feature reference statistics"], "ai_infra")
    signed = (r["serve_mean"] - r["train_mean"]) / r["train_std"]
    limit = ctx.baseline["feature_mean_shift_sigma_max"]
    shift = max(r["mean_shift_sigma"], abs(signed))
    history = ctx.state.setdefault("signed_feature_history", {}).setdefault(payload["feature_view"], [])
    # For zero-centered Gaussian residuals, the absolute residual's mean +
    # 3 std is about 2.606 times the signed standard deviation. Using signed
    # residuals avoids treating a positive-only magnitude as Gaussian.
    prior_sigma = limit / 2.606
    # The training mean is the normative center. Recent serving values must
    # not redefine "normal" and hide persistent training-serving skew.
    center = 0.0
    sigma = prior_sigma
    if len(history) >= 10:
        negative = [-value for value in history if value < 0]
        positive = [value for value in history if value > 0]
        scales = [_median(side) / 0.67448975 for side in (negative, positive) if len(side) >= 6]
        if scales:
            # Use the less contaminated side, with a prior and a scale floor.
            observed = min(scales)
            scale_weight = min(len(history), 32)
            sigma = max(0.7 * prior_sigma,
                        min(prior_sigma, (16 * prior_sigma + scale_weight * observed) / (16 + scale_weight)))
    threshold = _directional_threshold(FEATURE_Z, POSITIVE_PRIORS["feature_shift"], signed - center)
    if shift > limit or abs(signed - center) > threshold * sigma:
        reasons.append("training-serving signed residual deviation")
    if shift <= limit:
        history.append(signed)
        if len(history) > 64:
            del history[0]
    return _verdict(reasons, "ai_infra")


def check_embedding_batch(payload, ctx):
    r = _inspect(ctx, "embedding_drift", 2.0,
                 payload["corpus"], payload["chunk_batch_id"])
    unavailable = _unavailable(r, "ai_infra")
    if unavailable is not None:
        return unavailable
    reasons = []
    group = payload["corpus"]
    shift_z = _upper(ctx, (group, "centroid_shift"), r["centroid_shift"],
                     ctx.baseline["embedding_centroid_shift_max"], "embedding drift", reasons)
    age_z = _upper(ctx, (group, "doc_age"), r["avg_doc_age_days"],
                   ctx.baseline["corpus_avg_doc_age_days_max"], "corpus age", reasons)
    if not reasons and shift_z > 2.0 and age_z > 2.0:
        reasons.append("combined corpus drift and aging")
    return _verdict(reasons, "ai_infra")
