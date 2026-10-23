# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pymc==5.20.1", "numpy<2.2", "arviz==0.20.0", "matplotlib==3.9.4"]
# ///
"""Infer FIPS's hidden verification window from the outside (write-up 02).

    uv run experiments/bayes/fit.py [--draws 1000] [--out experiments/writeups/02-window/posterior.json]

Reads write-up 01's data.json (run experiments/analyze.py first) and fits one
model of what happens after a cut:

    stuck      only if the cut comes before the window closes, W seconds after
               start-up, with a probability p(t) that may change with the cut's
               time t after start-up (logistic in t). A stuck cut comes back
               when the window closes: at W + noise after start-up.
    rerouted   after the dead-link timeout T plus a delay d. d is usually short
               (fast) and sometimes tens of seconds (slow), with a separate
               slow share inside the window and in a warm mesh.

A wait that timed out is a censored observation (the reroute took at least
that long), not a dropped one. Cuts whose time after start-up is unknown (warm
mesh, timeout sweep) only inform the delay.

Fits the model three times, each with more of the evidence:
    where      which cuts got stuck, every stuck cut cut off at 45 s
    how-long   ...and, for censored cuts, that they lasted at least the wait
    when-back  ...and when the four cuts left alone came back
and writes thinned draws plus precomputed curves for the charts.
"""

import argparse
import json
from pathlib import Path

import arviz as az
import numpy as np
import pymc as pm
import pytensor.tensor as pt

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "experiments" / "writeups" / "01-reroute" / "data.json"
OUT = ROOT / "experiments" / "writeups" / "02-window" / "posterior.json"

T_SCALE = 100.0  # logistic slope per 100 s
STEP = 2.0  # softness of the window edge, seconds; keeps W differentiable
SHORT_WAIT = 45.0  # the stuck-reroute wait, used to hide the left-alone cuts' return


def load(path):
    d = json.loads(path.read_text())
    fresh, warm = [], []
    for r in d["stuck"] + d["unlock"]:
        row = {
            "y": r["reroute_s"],
            "cens": r["censored"],
            "T": r.get("dead_timeout_s", 6),
            "left_alone": r in d["unlock"],
        }
        if r["fresh"]:
            fresh.append({**row, "t": r["since_start_s"]})
        elif r["rep"] > 1:
            warm.append(row)
    # The sweep and the early runs' rerouted cuts: delay only. The early runs'
    # stuck cuts have no known time after start-up and no other use here.
    for r in d["sweep"] + d["early"]:
        if not r["censored"]:
            warm.append({"y": r["reroute_s"], "cens": False, "T": r["dead_timeout_s"], "left_alone": False})
    return fresh, warm


def log_sf_normal(x, mu, sigma):
    return pt.log(0.5 * pt.erfc((x - mu) / (sigma * np.sqrt(2.0))))


def log_sf_lognormal(x, mu, sigma):
    return log_sf_normal(pt.log(x), mu, sigma)


def delay_logp(d, cens, slow_p, mu_f, sd_f, mu_s, sd_s):
    """Log density (or survival, if censored) of a reroute delay d > 0 under
    the fast/slow mixture."""
    d = np.maximum(d, 1e-3)
    fast = pt.switch(cens, log_sf_lognormal(d, mu_f, sd_f), pm.logp(pm.LogNormal.dist(mu_f, sd_f), d))
    slow = pt.switch(cens, log_sf_lognormal(d, mu_s, sd_s), pm.logp(pm.LogNormal.dist(mu_s, sd_s), d))
    return pm.math.logsumexp(pt.stack([pt.log1p(-slow_p) + fast, pt.log(slow_p) + slow]), axis=0)


def build(fresh, warm, evidence):
    t = np.array([r["t"] for r in fresh])
    T = np.array([r["T"] for r in fresh], float)
    y = np.array([r["y"] for r in fresh])
    cens = np.array([r["cens"] for r in fresh])
    if evidence != "when-back":
        # Pretend the left-alone cuts had the usual 45 s wait.
        hide = np.array([r["left_alone"] and r["y"] > SHORT_WAIT for r in fresh])
        y = np.where(hide, SHORT_WAIT, y)
        cens = cens | hide
    wT = np.array([r["T"] for r in warm], float)
    wy = np.array([r["y"] for r in warm])

    with pm.Model() as model:
        # A timer anywhere in the first 20 minutes.
        W = pm.Uniform("W", 0, 1200)
        a = pm.Normal("a", 0, 1.5)  # logit p at t = 100 s
        b = pm.Normal("b", 0, 1.5)  # change in logit p per 100 s
        # Spread of the return around W: at least the ~1 s the reachability
        # wait needs per probe. Without that floor, fits that see no return
        # push it to zero and the sampler diverges on the censoring cliffs.
        sd_back = pm.Deterministic("sd_back", 1 + pm.HalfNormal("sd_back_extra", 10))
        mu_f = pm.Normal("mu_f", np.log(2), 1)
        sd_f = pm.HalfNormal("sd_f", 1)
        # Slow is slower than fast by construction (median at least 1.6×), or
        # chains can swap the two components.
        gap = pm.TruncatedNormal("gap", np.log(10), 0.7, lower=0.5)
        mu_s = pm.Deterministic("mu_s", mu_f + gap)
        sd_s = pm.HalfNormal("sd_s", 1)
        slow_win = pm.Beta("slow_win", 1, 1)
        slow_warm = pm.Beta("slow_warm", 1, 1)

        inside = pm.math.sigmoid((W - t) / STEP)
        p_stuck = pm.math.sigmoid(a + b * (t - 100) / T_SCALE) * inside
        p_stuck = pt.clip(p_stuck, 1e-9, 1 - 1e-9)

        # Stuck: back at W + noise after start-up, i.e. y = W - t + noise.
        if evidence == "where":
            # Only that it got stuck: the censored value carries no length.
            stuck_lp = pt.switch(cens, 0.0, -np.inf)
        else:
            stuck_lp = pt.switch(
                cens,
                log_sf_normal(y, W - t, sd_back),
                pm.logp(pm.Normal.dist(W - t, sd_back), y),
            )
        slow_p = slow_win * inside + slow_warm * (1 - inside)
        moved_lp = delay_logp(y - T, cens, slow_p, mu_f, sd_f, mu_s, sd_s)
        pm.Potential(
            "fresh",
            pm.math.logsumexp(pt.stack([pt.log(p_stuck) + stuck_lp, pt.log1p(-p_stuck) + moved_lp]), axis=0).sum(),
        )
        pm.Potential("warm", delay_logp(wy - wT, np.zeros(len(wy), bool), slow_warm, mu_f, sd_f, mu_s, sd_s).sum())
    return model


def start(warm):
    """Initial values from the data's own summary. The delay mixture has a
    local mode (one wide component, the other squeezed against the gap's
    bound) about 10 log units below the main one; a chain started at random
    can stay there for the whole run."""
    d = np.array([r["y"] - r["T"] for r in warm])
    fast = d[d < np.percentile(d, 90)]
    return {"W": 250.0, "mu_f": float(np.log(np.median(fast))), "sd_f": float(np.std(np.log(fast))),
            "gap": 1.8, "slow_warm": 0.05}


def curves(post, rng):
    """Per-draw curves for the charts, summarised as quantiles."""
    grid = np.arange(0, 401, 2.0)
    W = post["W"][:, None]
    p = 1 / (1 + np.exp(-(post["a"][:, None] + post["b"][:, None] * (grid - 100) / T_SCALE)))
    p_stuck = p / (1 + np.exp(-(W - grid) / STEP))
    q = [5, 25, 50, 75, 95]
    stuck_curve = {"t": grid.tolist(), **{f"q{k}": np.percentile(p_stuck, k, axis=0).round(4).tolist() for k in q}}

    # Time a is cut off from b after a cut at time t (timeout 6 s): simulate
    # SIMS cuts per draw. Each simulated cut keeps its random numbers across
    # the grid (common random numbers), so the curves are smooth in t.
    SIMS = 10
    rep = lambda v: np.repeat(v, SIMS)[:, None]  # noqa: E731
    n = len(post["W"]) * SIMS
    Wr = rep(post["W"])
    p_r = np.repeat(p_stuck, SIMS, axis=0)
    inside = 1 / (1 + np.exp(-(Wr - grid) / STEP))
    slow_p = rep(post["slow_win"]) * inside + rep(post["slow_warm"]) * (1 - inside)
    stuck = rng.random((n, 1)) < p_r
    slow = rng.random((n, 1)) < slow_p
    z = rng.standard_normal((n, 1))
    d = np.where(slow, np.exp(rep(post["mu_s"]) + rep(post["sd_s"]) * z), np.exp(rep(post["mu_f"]) + rep(post["sd_f"]) * z))
    back = Wr + rep(post["sd_back"]) * rng.standard_normal((n, 1)) - grid
    off = np.where(stuck, np.maximum(back, 6 + d), 6 + d)
    outage = {"t": grid.tolist(), **{f"q{k}": np.percentile(off, k, axis=0).round(2).tolist() for k in [50, 90, 95, 99]}}
    outage["mean"] = off.mean(axis=0).round(2).tolist()

    # Warm mesh, by dead-link timeout: percentiles of the reroute time.
    timeouts = np.arange(2, 31, 1.0)
    z = rng.standard_normal((n, 1))
    slow = rng.random((n, 1)) < rep(post["slow_warm"])
    d = np.where(slow, np.exp(rep(post["mu_s"]) + rep(post["sd_s"]) * z), np.exp(rep(post["mu_f"]) + rep(post["sd_f"]) * z))
    reroute = timeouts[None, :] + d
    by_timeout = {"T": timeouts.tolist(), **{f"q{k}": np.percentile(reroute, k, axis=0).round(2).tolist() for k in [50, 95, 99]}}
    return stuck_curve, outage, by_timeout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    fresh, warm = load(DATA)
    print(f"{len(fresh)} cuts at a known time after start-up, {len(warm)} for the delay only", flush=True)
    out = {"data": {"fresh": fresh, "warm": warm}, "fits": {}}
    rng = np.random.default_rng(args.seed)
    for evidence in ("where", "how-long", "when-back"):
        with build(fresh, warm, evidence):
            idata = pm.sample(
                args.draws, tune=1000, chains=4, cores=4, target_accept=0.95,
                random_seed=args.seed, progressbar=False,
                initvals=start(warm),
            )
        summary = az.summary(idata, round_to=3)
        div = int(idata.sample_stats.diverging.sum())
        print(f"\n== {evidence}: {div} divergences", flush=True)
        print(summary[["mean", "sd", "hdi_3%", "hdi_97%", "r_hat", "ess_bulk"]])
        post = {k: idata.posterior[k].values.reshape(-1) for k in idata.posterior.data_vars}
        keep = rng.choice(len(post["W"]), size=min(4000, len(post["W"])), replace=False)
        post = {k: v[keep] for k, v in post.items()}
        stuck_curve, outage, by_timeout = curves(post, rng)
        out["fits"][evidence] = {
            "divergences": div,
            "rhat_max": float(summary["r_hat"].max()),
            "ess_min": float(summary["ess_bulk"].min()),
            "summary": {k: {c: float(summary.loc[k, c]) for c in ("mean", "sd", "hdi_3%", "hdi_97%")} for k in summary.index},
            "W": np.round(post["W"], 2).tolist(),
            "stuck_curve": stuck_curve,
            "outage": outage,
            "by_timeout": by_timeout,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
