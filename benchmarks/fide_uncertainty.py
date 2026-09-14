"""Seed-level uncertainty for DeltaFrag, computed from committed reports only.

WHY THIS FILE EXISTS RATHER THAN A CONFIDENCE INTERVAL BOLTED ONTO THE TABLES.

A reviewer asked for "a 95% interval and a test against zero". Attaching a generic t
interval to `mean [min-max]` would have been wrong three times over, and each way is
worth stating because each is a mistake this analysis has to avoid:

  1. THE UNIT. A run has 32 cases and 16 fragmentation pairs. Those 16 pairs are not 16
     independent samples of anything: they share one machine, one gateway process, one
     case structure, one execution order, and one generated value set. The only thing the
     seed varies is the GENERATED VALUES. So the independent unit is the SEED, n = 12, and
     the 192 pairs behind a twelve-seed sweep are 12 observations, not 192.
  2. THE ESTIMAND. `mean [min-max]` is a description of twelve numbers. An interval is a
     statement about a population. The population here is the distribution of seed-level
     DeltaFrag induced by the corpus value generator, HOLDING MACHINE, CONFIGURATION,
     CASE STRUCTURE, ORDER AND TARGET VERSION FIXED. It is not a distribution over
     deployments, payloads, or products, and it must never be read as one.
  3. THE DISTRIBUTION. Seed-level DeltaFrag on this design takes values in
     {-16/16, ..., 16/16}: bounded, discrete, and at n = 12 frequently degenerate -- the
     enumerated Presidio arm is 0.875 or 0.625 and nothing else. A normal-theory interval
     on twelve points from a bounded lattice with ties is not defensible, and a bootstrap
     of a degenerate sample returns its own point mass and calls it a confidence interval.

WHAT IS ACTUALLY REPORTED, and what each thing assumes.

  * PER-SEED VALUES AND THE PAIRED 2x2 TABLE, always. These are the measurement. Everything
    below is a summary of them and none of it replaces them.
  * SIGN TEST of H0: median(Delta) = 0. Exact binomial, two-sided. Assumes only that the
    twelve seed-level differences are independent draws from a common distribution. Zeros
    are excluded and the reduced n is reported, which is the conservative convention.
  * SIGN-FLIP (RANDOMIZATION) TEST of H0: the distribution of Delta is symmetric about
    zero. Exact by full enumeration of all 2^n sign patterns (4096 at n = 12), so there is
    no Monte Carlo error. This is the test OF THE MEAN and it is not the sign test; they
    answer different questions and are labelled separately throughout.
  * DISTRIBUTION-FREE INTERVAL FOR THE MEDIAN from order statistics. Its coverage is a
    binomial quantity and is printed EXACTLY -- at n = 12 the achievable levels straddle
    95% and calling either one "the 95% interval" would be a rounding of the guarantee.
  * BCa BOOTSTRAP INTERVAL FOR THE MEAN, reported with an explicit warning at n = 12 and
    REFUSED outright when the sample is degenerate. A bootstrap of twelve identical values
    is twelve identical values.

WHAT IS NOT REPORTED, AND WHY.

  * No interval for a deterministic reference policy. `passthrough`, `redact-all`,
    `chunk-local`, `bounded-retention` and `retention-plus-decoding` are regex policies
    whose behaviour does not depend on the drawn values; their DeltaFrag is identical at
    every seed by construction. An interval there would be manufactured.
  * No interval for a single-seed row. The four Google Cloud rows are single-seed and have
    no seed distribution to estimate; that is a gap, not a number.
  * No pooled test over 192 pairs. See (1).

MULTIPLICITY. The primary family is predeclared in `PRIMARY_FAMILY` below and Holm's
step-down procedure is applied within it. Everything outside that family is descriptive
and is printed without an adjusted p-value.

HONESTY DISCLOSURE, and it is not a footnote: THIS ANALYSIS PLAN WAS WRITTEN AFTER THE
DATA WERE COLLECTED. DeltaFrag = 0 is the null the profile was designed around and in that
sense the hypothesis is not fished for, but the choice of test, interval and family was
made with the twelve seed values already visible. The p-values are therefore reported as
descriptive evidence, not as controlling a pre-registered error rate.

Usage:
    python benchmarks/fide_uncertainty.py --results benchmarks/results/v2-response-split
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import pathlib
import random
from dataclasses import dataclass
from typing import Any

# The predeclared primary family: the questions the article actually asks.
PRIMARY_FAMILY = (
    ("presidio-chunk-local", "exhaustive-2-part"),
    ("presidio-chunk-local", "midpoint"),
    ("presidio-retention", "exhaustive-2-part"),
    ("presidio-retention", "midpoint"),
)


# --------------------------------------------------------------------------------------
# Exact combinatorics. No SciPy: the harness distribution is stdlib plus httpx, and an
# exact binomial tail at n <= 32 is three lines.
# --------------------------------------------------------------------------------------


def binom_pmf(k: int, n: int, p: float = 0.5) -> float:
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    return sum(binom_pmf(i, n, p) for i in range(0, k + 1))


def sign_test(values: list[float]) -> dict[str, Any]:
    """Exact two-sided sign test of H0: median = 0.

    Assumptions, stated because they are the only ones this test makes: the values are
    independent draws from a common distribution. Nothing about symmetry, normality, or
    the scale. Zeros carry no information about the direction of the median and are
    dropped, which is conservative -- it lowers n and widens the p-value.
    """
    pos = sum(1 for v in values if v > 0)
    neg = sum(1 for v in values if v < 0)
    zeros = sum(1 for v in values if v == 0)
    n = pos + neg
    if n == 0:
        return {
            "test": "exact two-sided sign test of H0: median = 0",
            "n_nonzero": 0,
            "positive": 0,
            "negative": 0,
            "zeros": zeros,
            "p_exact": None,
            "note": (
                "every seed-level difference is exactly zero, so the sign test has no "
                "information. This is not evidence for the null: it is the absence of "
                "any discordant observation to test with."
            ),
        }
    k = min(pos, neg)
    p = min(1.0, 2.0 * binom_cdf(k, n, 0.5))
    return {
        "test": "exact two-sided sign test of H0: median = 0",
        "statistic_min_pos_neg": k,
        "n_nonzero": n,
        "positive": pos,
        "negative": neg,
        "zeros": zeros,
        "p_exact": p,
    }


def sign_flip_test(values: list[float]) -> dict[str, Any]:
    """Exact randomization test of H0: the distribution of the differences is symmetric
    about zero. Full enumeration of all 2^n sign assignments; no Monte Carlo error.

    THIS IS THE TEST OF THE MEAN. The sign test above is a test of the median. They are
    reported separately and neither is ever labelled as the other. The extra assumption
    bought here is symmetry under the null, which is what licenses flipping signs.
    """
    n = len(values)
    if n == 0:
        return {"test": "exact sign-flip test", "p_exact": None}
    if n > 22:
        raise ValueError(f"refusing to enumerate 2**{n} sign patterns; use n <= 22")
    observed = abs(sum(values))
    extreme = 0
    total = 0
    for signs in itertools.product((1, -1), repeat=n):
        total += 1
        if abs(sum(s * v for s, v in zip(signs, values))) >= observed - 1e-12:
            extreme += 1
    return {
        "test": (
            "exact two-sided sign-flip randomization test of H0: the seed-level "
            "differences are symmetric about zero (a test of the MEAN)"
        ),
        "n": n,
        "observed_mean": round(sum(values) / n, 6),
        "sign_patterns_enumerated": total,
        "patterns_at_least_as_extreme": extreme,
        "p_exact": extreme / total,
    }


def median_interval(values: list[float], target: float = 0.95) -> dict[str, Any]:
    """Distribution-free interval for the MEDIAN from order statistics.

    The interval [x_(k+1), x_(n-k)] covers the population median with probability
    1 - 2 * P(Bin(n, 0.5) <= k - 1)... expressed here directly as a sum, because the
    achievable levels at n = 12 are a coarse lattice (0.9614, 0.8540, ...) and the honest
    thing is to print the level that was ACHIEVED rather than to call the nearest one 95%.
    """
    n = len(values)
    if n < 6:
        return {"interval": None, "note": f"n = {n} is too small for a useful order-statistic interval"}
    ordered = sorted(values)
    best = None
    for k in range(0, n // 2):
        coverage = 1.0 - 2.0 * binom_cdf(k, n, 0.5)
        if coverage >= target:
            best = (k, coverage)
    if best is None:
        return {
            "interval": [ordered[0], ordered[-1]],
            "achieved_coverage": 1.0 - 2.0 * binom_pmf(0, n, 0.5),
            "note": "no order-statistic interval reaches the requested level at this n",
        }
    k, coverage = best
    return {
        "method": "distribution-free order-statistic interval for the median",
        "interval": [ordered[k], ordered[n - 1 - k]],
        "order_statistics": [k + 1, n - k],
        "achieved_coverage": round(coverage, 6),
        "requested": target,
        "note": (
            "coverage is an exact binomial quantity and is not 0.95; the achievable "
            "levels at this n are a coarse lattice and the achieved level is printed "
            "rather than rounded to the requested one"
        ),
    }


def bca_bootstrap_mean(
    values: list[float], level: float = 0.95, resamples: int = 20000, seed: int = 20260909
) -> dict[str, Any]:
    """BCa percentile interval for the MEAN, with a refusal path.

    REFUSES on a degenerate sample. Resampling twelve identical numbers returns twelve
    identical numbers, and printing [0.875, 0.875] as a 95% confidence interval would be
    the most misleading line in the whole analysis: it looks like precision and it is an
    artefact of a statistic with no sampling variation in the observed data.
    """
    n = len(values)
    if n == 0:
        return {"interval": None, "note": "no observations"}
    if len(set(values)) == 1:
        return {
            "interval": None,
            "refused": True,
            "note": (
                f"all {n} seed-level values are identical ({values[0]}). A bootstrap of a "
                "degenerate sample reproduces the point mass; no interval is identified "
                "from these data. The correct statement is that the observed value did "
                "not vary across the seeds measured, which is a stronger and simpler "
                "claim than any interval."
            ),
        }
    rng = random.Random(seed)
    point = sum(values) / n
    boots = []
    for _ in range(resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(sample) / n)
    boots.sort()

    # Bias correction.
    below = sum(1 for b in boots if b < point)
    if below in (0, resamples):
        return {
            "interval": None,
            "refused": True,
            "note": "bias correction undefined: every bootstrap replicate falls on one side of the point estimate",
        }
    z0 = _inverse_normal_cdf(below / resamples)
    # Acceleration by jackknife.
    jack = []
    for i in range(n):
        rest = values[:i] + values[i + 1:]
        jack.append(sum(rest) / len(rest))
    jbar = sum(jack) / n
    num = sum((jbar - j) ** 3 for j in jack)
    den = 6.0 * (sum((jbar - j) ** 2 for j in jack) ** 1.5)
    a = num / den if den else 0.0

    def endpoint(alpha: float) -> float:
        z = _inverse_normal_cdf(alpha)
        adj = z0 + (z0 + z) / (1 - a * (z0 + z))
        q = _normal_cdf(adj)
        idx = min(resamples - 1, max(0, int(round(q * (resamples - 1)))))
        return boots[idx]

    alpha = (1 - level) / 2
    return {
        "method": "BCa bootstrap percentile interval for the mean",
        "point_estimate": round(point, 6),
        "interval": [round(endpoint(alpha), 6), round(endpoint(1 - alpha), 6)],
        "level": level,
        "resamples": resamples,
        "bias_correction_z0": round(z0, 6),
        "acceleration_a": round(a, 6),
        "warning": (
            f"n = {n}. BCa coverage is asymptotic; on a bounded discrete lattice with "
            "ties at this n the interval is approximate and is reported beside the exact "
            "order-statistic interval for the median rather than instead of it."
        ),
    }


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inverse_normal_cdf(p: float) -> float:
    """Acklam's rational approximation, adequate at the precision used here."""
    if not 0.0 < p < 1.0:
        raise ValueError(p)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm's step-down adjustment over the predeclared family."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, float] = {}
    running = 0.0
    for i, (key, p) in enumerate(items):
        adjusted = min(1.0, (m - i) * p)
        running = max(running, adjusted)
        out[key] = running
    return out


# --------------------------------------------------------------------------------------
# Reading the evidence
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedObservation:
    seed: str
    policy: str
    oracle: str
    leak_single: float
    leak_adversarial: float
    delta_frag: float
    discordance: dict[str, Any] | None
    path: str


def _oracle_of(report: dict[str, Any]) -> str:
    block = report.get("metrics", {}).get("partition_oracle")
    if block:
        return block["oracle"]
    # Reports emitted before the partition-oracle block: fall back to the derived label,
    # which is the only oracle statement those artefacts carry.
    label = report["checks"]["response_injection_containment"]["fragmentation_strategy"]
    return "exhaustive-2-part" if label == "exhaustive-2-part" else "midpoint"


def collect(results: pathlib.Path) -> list[SeedObservation]:
    out: list[SeedObservation] = []
    for path in sorted(results.rglob("*.json")):
        if path.name.startswith("seed-sweep"):
            continue
        if path.name == "fide-sweep.json":
            continue
        if path.name.endswith(".summary.json"):
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if not str(report.get("schema", "")).startswith("llm-shield.streaming-privacy"):
            continue
        metrics = report["metrics"]
        out.append(
            SeedObservation(
                seed=report["corpus"]["seed"],
                policy=path.stem,
                oracle=_oracle_of(report),
                leak_single=metrics["leak_rate"]["single_chunk"],
                leak_adversarial=metrics["leak_rate"]["adversarial"],
                delta_frag=metrics["delta_frag"],
                discordance=metrics.get("discordance"),
                path=str(path),
            )
        )
    return out


def collect_from_sweeps(results: pathlib.Path) -> list[SeedObservation]:
    """Midpoint seed sweeps live in `seed-sweep*.json` as policy -> per-seed rows."""
    out: list[SeedObservation] = []
    for path in sorted(results.glob("seed-sweep*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for policy, block in document.items():
            if not isinstance(block, dict):
                continue
            for row in block.get("runs", []) or []:
                if not isinstance(row, dict) or "delta_frag" not in row:
                    continue
                out.append(
                    SeedObservation(
                        seed=str(row.get("seed", "")),
                        policy=policy,
                        oracle="midpoint",
                        leak_single=row["leak_single_chunk"],
                        leak_adversarial=row["leak_adversarial"],
                        delta_frag=row["delta_frag"],
                        discordance=None,
                        path=f"{path.name}:{policy}",
                    )
                )
    return out


SYSTEMATIC_SEEDS = frozenset(f"{i:016x}" for i in range(1, 65))


def in_inference_frame(seed: str) -> bool:
    """Only the SYSTEMATICALLY ENUMERATED seeds are in the frame.

    The sweeps walk `0000000000000001` upward, so which seeds were measured was fixed
    before any result was seen. `a1b2c3d4e5f60001` is the hand-picked seed the published
    single-run rows were measured at, and its values were read while the article was
    written. Including it in an inference sample would be selecting on the outcome. It is
    reported as a separate point observation instead, which is what it is.
    """
    return seed in SYSTEMATIC_SEEDS


def deduplicate(group: list["SeedObservation"]) -> tuple[list["SeedObservation"], list[dict[str, Any]]]:
    """One observation per seed, and a CONSISTENCY CHECK on the duplicates.

    A seed can appear twice in one (policy, oracle) group: once in a sweep row and once
    as a single-run artefact measured at the same seed. Those must agree, and if they do
    not the two artefacts are not describing the same run -- which is a finding, not a
    thing to average away.
    """
    by_seed: dict[str, list[SeedObservation]] = {}
    for obs in group:
        by_seed.setdefault(obs.seed, []).append(obs)
    kept: list[SeedObservation] = []
    conflicts: list[dict[str, Any]] = []
    for seed, rows in sorted(by_seed.items()):
        values = {(r.leak_single, r.leak_adversarial, r.delta_frag) for r in rows}
        if len(values) > 1:
            conflicts.append({
                "seed": seed,
                "sources": [r.path for r in rows],
                "values": sorted(str(v) for v in values),
            })
        kept.append(sorted(rows, key=lambda r: (r.discordance is None, r.path))[0])
    return kept, conflicts


def analyse(group: list[SeedObservation]) -> dict[str, Any]:
    values = [o.delta_frag for o in group]
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(variance)
    body: dict[str, Any] = {
        "n_seeds": n,
        "seeds": [o.seed for o in group],
        "per_seed_delta_frag": values,
        "per_seed_leak_single": [o.leak_single for o in group],
        "per_seed_leak_adversarial": [o.leak_adversarial for o in group],
        "mean": round(mean, 6),
        "median": round(sorted(values)[n // 2] if n % 2 else
                        (sorted(values)[n // 2 - 1] + sorted(values)[n // 2]) / 2, 6),
        "min": min(values),
        "max": max(values),
        "sd": round(sd, 6),
        # Effect size on the natural scale is the mean itself: a change in the proportion
        # of cases that leaked. d_z is printed beside it and is meaningless when sd = 0.
        "effect_size_natural_scale": round(mean, 6),
        "effect_size_dz": (round(mean / sd, 6) if sd > 0 else None),
        "sign_test": sign_test(values),
        "sign_flip_test": sign_flip_test(values),
    }
    # DEGENERATE SAMPLES GET NO INTERVAL, and the reason is printed in place of one.
    #
    # Twelve identical values have no sampling variation IN THESE DATA. An order-statistic
    # interval on them is [c, c] and a sign-flip p-value on them is 2 / 2**n -- both look
    # like precision and both are restating "every observation was c". For a deterministic
    # reference policy that constant is a property of the code; for a live detector under
    # an enumerating oracle it is the result itself and is far better stated as "did not
    # vary across the 12 seeds measured" than as an interval of zero width.
    if len(set(values)) == 1:
        body["median_interval"] = {
            "interval": None,
            "refused": True,
            "note": (
                f"degenerate sample: all {n} seed-level values are exactly {values[0]}. "
                "No interval is identified. The measurement is the invariance itself."
            ),
        }
        body["mean_interval"] = bca_bootstrap_mean(values)
        body["sign_flip_test"]["degenerate_warning"] = (
            "the sample is constant, so this p-value only restates that the constant is "
            f"{'not ' if values[0] else ''}zero. It is not evidence about a population "
            "and must not be reported as a test result."
        )
        body["sign_test"]["degenerate_warning"] = body["sign_flip_test"][
            "degenerate_warning"
        ]
    else:
        body["median_interval"] = median_interval(values)
        body["mean_interval"] = bca_bootstrap_mean(values)
    tables = [o.discordance for o in group if o.discordance]
    if tables:
        body["discordant_pairs_per_seed"] = [
            {
                "seed": o.seed,
                "adversarial_only": (o.discordance or {}).get("adversarial_only"),
                "single_chunk_only": (o.discordance or {}).get("single_chunk_only"),
                "both": (o.discordance or {}).get("both_arms_leaked"),
                "neither": (o.discordance or {}).get("neither_arm_leaked"),
                "pairs": (o.discordance or {}).get("pairs_complete"),
            }
            for o in group
            if o.discordance
        ]
        body["discordance_note"] = (
            "These counts are per seed and are NOT pooled. Pooling them would be a "
            "McNemar test over 16 * n pairs and would treat one run's pairs as "
            "independent replicates, which they are not."
        )
    else:
        body["discordance_note"] = (
            "no paired 2x2 table in these artefacts: they predate metrics.discordance"
        )
    if len(set(values)) == 1:
        body["determinism_note"] = (
            f"all {n} seeds give exactly {values[0]}. If the target is a deterministic "
            "policy this is expected and no interval is identified; if it is a live "
            "detector this is itself the result -- the enumeration removed the "
            "seed-to-seed variation rather than a favourable seed being selected."
        )
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--out", default=None, help="write JSON here as well as stdout")
    parser.add_argument("--min-seeds", type=int, default=6)
    args = parser.parse_args(argv)

    root = pathlib.Path(args.results)
    observations = collect(root) + collect_from_sweeps(root)

    groups: dict[tuple[str, str], list[SeedObservation]] = {}
    for obs in observations:
        groups.setdefault((obs.policy, obs.oracle), []).append(obs)

    report: dict[str, Any] = {
        "estimand": (
            "the expected seed-level DeltaFrag = LeakRate(adversarial) - "
            "LeakRate(single_chunk) over the distribution of generated value sets induced "
            "by pii_leak_benchmark.v2_emitter.make_seeded_fixture, holding machine, "
            "gateway configuration, target version, case structure and execution order "
            "FIXED"
        ),
        "sampling_frame": (
            "one seed = one independent observation. The 16 fragmentation pairs inside a "
            "run are not independent replicates: they share a process, a configuration, a "
            "case structure and one drawn value set. The seeds are consecutive small "
            "integers rather than a random sample, and the independence claim rests on "
            "the seed being hashed into a Mersenne Twister state -- an assumption about "
            "the generator, not a randomisation this study performed."
        ),
        "generalises_to": "other generated value sets on this machine and configuration",
        "does_not_generalise_to": (
            "other machines, other gateway versions or configurations, other case "
            "structures, other corpora, production payloads, or products"
        ),
        "analysis_plan_written": "after the data were collected; see the module docstring",
        "multiplicity_policy": (
            "Holm step-down within the predeclared primary family "
            f"{[f'{p}:{o}' for p, o in PRIMARY_FAMILY]}; everything else is descriptive "
            "and carries no adjusted p-value"
        ),
        "groups": {},
        "excluded": {},
    }

    report["out_of_frame_point_observations"] = {}
    for (policy, oracle), group in sorted(groups.items()):
        key = f"{policy}::{oracle}"
        deduped, conflicts = deduplicate(group)
        if conflicts:
            report.setdefault("artefact_conflicts", {})[key] = conflicts
        outside = [o for o in deduped if not in_inference_frame(o.seed)]
        if outside:
            report["out_of_frame_point_observations"][key] = [
                {"seed": o.seed, "delta_frag": o.delta_frag,
                 "leak_single": o.leak_single, "leak_adversarial": o.leak_adversarial,
                 "source": o.path,
                 "why_excluded": "hand-picked published seed; including it would select on the outcome"}
                for o in outside
            ]
        group = [o for o in deduped if in_inference_frame(o.seed)]
        if len(group) < args.min_seeds:
            report["excluded"][key] = (
                f"{len(group)} seed(s): fewer than the {args.min_seeds} required for any "
                "seed-level inference. A single-seed row has no seed distribution to "
                "estimate and is reported as the point measurement it is."
            )
            continue
        report["groups"][key] = analyse(sorted(group, key=lambda o: o.seed))

    primary_p: dict[str, float] = {}
    for policy, oracle in PRIMARY_FAMILY:
        key = f"{policy}::{oracle}"
        block = report["groups"].get(key)
        if block and block["sign_flip_test"].get("p_exact") is not None:
            primary_p[key] = block["sign_flip_test"]["p_exact"]
    if primary_p:
        adjusted = holm(primary_p)
        report["primary_family_holm_adjusted_p"] = {
            k: round(v, 8) for k, v in adjusted.items()
        }
        for key, value in adjusted.items():
            report["groups"][key]["holm_adjusted_p_sign_flip"] = round(value, 8)

    text = json.dumps(report, indent=1)
    print(text)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
