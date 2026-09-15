"""OLS linear-regression forecaster — annual aggregates with prediction intervals.

The user explicitly scoped the production forecaster to *simple* linear
regression on the corpus's own dimensions (Wijk, Categorie, Zorgaanbieder,
Route, age band) so the resulting slope + CI are interpretable inputs to
allocation decisions. Sophisticated alternatives (ARIMA, GBM, hierarchical
Bayes) are explicitly out of scope for v1 — they live behind the same
``BaseForecaster`` contract for future swap-in if needed.
"""

from __future__ import annotations

import logging

import numpy as np
import statsmodels.api as sm

from capelle_jeugdzorg_tool.base_forecaster import (
    BaseForecaster,
    ForecastPoint,
    ForecastResult,
)
from capelle_jeugdzorg_tool.data import JeugdzorgDataLoader

_logger = logging.getLogger("capelle_jeugdzorg.forecaster")


class ForecastError(RuntimeError):
    """Raised when a forecast cannot be produced (insufficient data, all-NaN slice, etc.).

    The CLI catches this per-slice so one weak dimension value never breaks an
    entire ``forecast`` command — it surfaces as an entry in the response's
    ``errors`` array instead.
    """


class OLSLinearForecaster(BaseForecaster):
    """Ordinary least squares on (year, value) pairs per dimension slice.

    Fit:  y = beta_0 + beta_1 * year
    PI:   ``statsmodels.get_prediction(obs=True, alpha=1-confidence)``.

    Why obs=True: we want a *prediction* interval for a future observation,
    not a confidence interval for the regression mean — the human reader is
    sizing future spend / volume / ok-rate, not the estimated mean of an
    unobserved population.
    """

    def __init__(self, loader: JeugdzorgDataLoader) -> None:
        self._loader = loader

    def forecast(
        self,
        dimension: str,
        dimension_value: str,
        metric: str,
        periods_ahead: int,
        confidence: float,
    ) -> ForecastResult:
        if periods_ahead <= 0:
            raise ForecastError(f"periods_ahead must be > 0, got {periods_ahead}")
        if not (0.0 < confidence < 1.0):
            raise ForecastError(
                f"confidence must be in (0, 1), got {confidence}"
            )

        df = self._loader.aggregate_annual(dimension, metric)
        dim_col = JeugdzorgDataLoader.DIMENSION_COLUMNS[dimension]
        year_col = JeugdzorgDataLoader.YEAR_COLUMN

        slice_df = df[df[dim_col].astype(str) == dimension_value].copy()
        slice_df = slice_df.dropna(subset=["value"]).sort_values(year_col)

        min_obs = self._loader._settings.min_observations  # noqa: SLF001
        if len(slice_df) < min_obs:
            raise ForecastError(
                f"need >= {min_obs} observations for "
                f"{dimension}={dimension_value!r} {metric!r}, "
                f"got {len(slice_df)}"
            )

        years = slice_df[year_col].astype(int).to_numpy()
        values = slice_df["value"].astype(float).to_numpy()

        regressor = sm.add_constant(years.astype(float))
        model = sm.OLS(values, regressor).fit()

        last_year = int(years.max())
        future_years = np.arange(last_year + 1, last_year + 1 + periods_ahead)
        future_regressor = sm.add_constant(
            future_years.astype(float), has_constant="add"
        )
        prediction = model.get_prediction(future_regressor)
        prediction_intervals = prediction.conf_int(
            obs=True, alpha=1.0 - confidence
        )
        predicted_means = prediction.predicted_mean

        historical = [
            ForecastPoint(
                period=int(year),
                predicted=float(value),
                lower=float(value),
                upper=float(value),
            )
            for year, value in zip(years, values, strict=True)
        ]
        forecast_points = [
            ForecastPoint(
                period=int(year),
                predicted=float(mean),
                lower=float(lower),
                upper=float(upper),
            )
            for year, mean, (lower, upper) in zip(
                future_years, predicted_means, prediction_intervals, strict=True
            )
        ]

        return ForecastResult(
            dimension=dimension,
            dimension_value=dimension_value,
            metric=metric,
            confidence=confidence,
            slope=float(model.params[1]),
            intercept=float(model.params[0]),
            r_squared=float(model.rsquared),
            historical=historical,
            forecast=forecast_points,
        )
