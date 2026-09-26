"""The ranking score (PRD section 9, layer 2) for any list of candidates: menu tiles, the sentences
on a suggestions screen, or the home Suggested phrases.

    score = w_use    * recency-weighted use count      (a use loses half its weight every 3 days)
          + w_time   * time-of-day match               (uses at this hour, +/- 1 hour at half weight)
          + w_state  * body-state fit                  (urgent items when the state is elevated)
          + w_ai     * AI prior                        (Jev's probability, 0 without Jev)
          - w_reject * recent rejections               (confirm screens cancelled in the last 24 h)

Weights come from data/profile.yaml and are normalized to sum to 1. Each part is scaled to 0..1
before it is weighted, so the weights compare like with like: use and time are divided by the
largest value among the candidates being ranked, the state fit and the AI prior already are 0..1,
and rejections count 1 - 0.5^n (one cancel 0.5, two 0.75, ...).

Two ways to order (decisions.md "Learning"):
  - stable_order: menu levels keep the menu.yaml order (motor memory matters for AAC users). An item
    only moves above the one before it when its score beats that one by a clear margin (the
    hysteresis factor, default 1.5x). Pinned items (home Suggested) stay first.
  - full_order: highest score first, for sentence lists (suggestions screen, Suggested).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from core.contracts import BodyStateLevel

DAY_S = 86_400.0
HALF_LIFE_DAYS = 3.0
REJECT_WINDOW_S = DAY_S  # rejections older than this no longer count
NEIGHBOUR_HOUR = 0.5  # weight of a use one hour before or after the current hour
DEFAULT_HYSTERESIS = 1.5
MIN_GAP = 0.02  # a move also needs this much absolute difference (scores are 0..1 scale)


class Weights(BaseModel):
    """PRD section 9 starting weights, tuned by hand in data/profile.yaml."""

    model_config = ConfigDict(extra="forbid")

    use: float = Field(default=0.4, ge=0)
    time: float = Field(default=0.2, ge=0)
    state: float = Field(default=0.1, ge=0)
    ai: float = Field(default=0.3, ge=0)
    reject: float = Field(default=0.3, ge=0)

    def normalized(self) -> Weights:
        total = self.use + self.time + self.state + self.ai + self.reject
        if total <= 0:
            return Weights(use=0, time=0, state=0, ai=0, reject=0)
        return Weights(
            use=self.use / total,
            time=self.time / total,
            state=self.state / total,
            ai=self.ai / total,
            reject=self.reject / total,
        )


@dataclass(frozen=True)
class Use:
    """One confirmed use: when (Unix seconds) and the local hour it happened in."""

    t: float
    hour: int


@dataclass
class Evidence:
    """What the history says about one candidate."""

    uses: list[Use] = field(default_factory=list)
    rejections: list[float] = field(default_factory=list)  # times of cancelled confirm screens


def decay(age_s: float) -> float:
    """Weight of a use `age_s` seconds old: 1 now, 0.5 after 3 days, 0.25 after 6."""
    return 0.5 ** (max(age_s, 0.0) / DAY_S / HALF_LIFE_DAYS)


def hour_weight(use_hour: int, hour: int) -> float:
    """1 for the same hour, 0.5 for the hour before or after (23 and 0 are neighbours), else 0."""
    d = abs(use_hour - hour) % 24
    d = min(d, 24 - d)
    return 1.0 if d == 0 else NEIGHBOUR_HOUR if d == 1 else 0.0


def recency_count(uses: Iterable[Use], now: float) -> float:
    return sum(decay(now - u.t) for u in uses)


def hour_match(uses: Iterable[Use], now: float, hour: int) -> float:
    """Recency-weighted uses at this time of day (the hour histogram smoothed over +/- 1 hour)."""
    return sum(decay(now - u.t) * hour_weight(u.hour, hour) for u in uses)


def recent_rejections(rejections: Iterable[float], now: float) -> int:
    return sum(1 for t in rejections if 0 <= now - t <= REJECT_WINDOW_S)


def state_fit(level: BodyStateLevel | None, urgent: bool) -> float:
    """Body-state hook (PRD D10: it only reorders). Urgent items (pain, bathroom, help) fit an
    elevated state. The state arrives with the sensor chunk; until then level is None and this is 0."""
    return 1.0 if level == "elevated" and urgent else 0.0


@dataclass(frozen=True)
class Candidate:
    id: str
    evidence: Evidence
    urgent: bool = False
    ai_prior: float = 0.0  # 0..1


@dataclass(frozen=True)
class Scored:
    """A candidate's score and its parts, each on a 0..1 scale before weighting."""

    id: str
    score: float
    use: float
    time: float
    state: float
    ai: float
    reject: float
    uses: float  # recency-weighted use count, unscaled
    at_hour: float  # recency-weighted uses at this time of day, unscaled


def score_all(
    candidates: Sequence[Candidate],
    *,
    now: float,
    hour: int,
    weights: Weights,
    state_level: BodyStateLevel | None = None,
) -> dict[str, Scored]:
    w = weights.normalized()
    uses = {c.id: recency_count(c.evidence.uses, now) for c in candidates}
    at_hour = {c.id: hour_match(c.evidence.uses, now, hour) for c in candidates}
    top_use = max(uses.values(), default=0.0)
    top_hour = max(at_hour.values(), default=0.0)
    out: dict[str, Scored] = {}
    for c in candidates:
        use = uses[c.id] / top_use if top_use > 0 else 0.0
        time = at_hour[c.id] / top_hour if top_hour > 0 else 0.0
        state = state_fit(state_level, c.urgent)
        ai = min(max(c.ai_prior, 0.0), 1.0)
        reject = 1.0 - 0.5 ** recent_rejections(c.evidence.rejections, now)
        total = w.use * use + w.time * time + w.state * state + w.ai * ai - w.reject * reject
        out[c.id] = Scored(c.id, total, use, time, state, ai, reject, uses[c.id], at_hour[c.id])
    return out


def beats(a: float, b: float, factor: float = DEFAULT_HYSTERESIS) -> bool:
    """True when score `a` beats `b` by a clear margin: at least `factor` times `b` (for a positive
    `b`), and never by less than MIN_GAP."""
    return a - b >= max(MIN_GAP, (factor - 1.0) * abs(b)) - 1e-9  # tolerance for float rounding


def stable_order(
    ids: Sequence[str], scores: Mapping[str, float], *, factor: float = DEFAULT_HYSTERESIS, pinned: int = 0
) -> list[str]:
    """`ids` (in menu.yaml order) with an item moved up only past neighbours it clearly beats. The
    first `pinned` ids never move and nothing moves above them."""
    order = list(ids)
    for i in range(pinned + 1, len(order)):
        j = i
        while j > pinned and beats(scores.get(order[j], 0.0), scores.get(order[j - 1], 0.0), factor):
            order[j - 1], order[j] = order[j], order[j - 1]
            j -= 1
    return order


def full_order(ids: Sequence[str], scores: Mapping[str, float]) -> list[str]:
    """Highest score first; ties keep their order in `ids`."""
    return sorted(ids, key=lambda i: -scores.get(i, 0.0))
