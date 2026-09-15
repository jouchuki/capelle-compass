"""Abstract base class and value objects for forecasters.

Defining the contract first (per the billion-dollar-code blueprint) keeps the
CLI free of implementation knowledge — it depends only on the ``BaseForecaster``
interface, so a future regression family (Ridge, Theil-Sen, ARIMA) can swap in
without touching ``cli.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """One observed or projected period point with confidence-interval bounds.

    For historical points the lower/upper bounds collapse onto the observed
    value (zero-width interval) so the renderer can draw the historical and
    forecast series on a single chart without branching.
    """

    period: int
    predicted: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """The output of fitting + projecting one dimension slice.

    Fields:
        dimension: Which corpus column was sliced on (e.g., ``"wijk"``).
        dimension_value: The specific value within that column (e.g., ``"Wijk A"``).
        metric: Which variable was forecast (``"bedrag"`` | ``"aantal"`` | ``"ok_rate"``).
        confidence: Two-sided prediction-interval coverage applied (e.g., ``0.95``).
        slope: OLS coefficient on the year regressor — sign + magnitude indicate
            the trend direction; the human reader uses this to inform
            allocation decisions, not the prediction itself as gospel.
        intercept: OLS intercept; usually only meaningful next to the slope.
        r_squared: Fit quality on the observed data.
        historical: One ``ForecastPoint`` per observed year, with zero-width
            intervals so the chart layer can render a single combined series.
        forecast: One ``ForecastPoint`` per projected year with predicted +
            two-sided prediction interval.
    """

    dimension: str
    dimension_value: str
    metric: str
    confidence: float
    slope: float
    intercept: float
    r_squared: float
    historical: list[ForecastPoint]
    forecast: list[ForecastPoint]


class BaseForecaster(ABC):
    """Contract for forecasters operating over the jeugdzorg corpus.

    A forecaster takes a (dimension, dimension_value, metric) triple and
    projects ``periods_ahead`` periods beyond the last observed year, returning
    a ``ForecastResult``. Subclasses are responsible for fitting + projecting;
    the data loader supplies the aggregated input.
    """

    @abstractmethod
    def forecast(
        self,
        dimension: str,
        dimension_value: str,
        metric: str,
        periods_ahead: int,
        confidence: float,
    ) -> ForecastResult:
        """Fit on historical aggregates and project ``periods_ahead`` periods.

        Raises:
            ForecastError: When insufficient historical observations exist to
                produce a meaningful fit. The CLI catches this and reports it
                as a per-slice error in the JSON output rather than failing
                the whole command.
        """
