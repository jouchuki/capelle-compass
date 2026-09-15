"""
Quota enforcement — bound the number of finished analyses a single
account may run per day.

Public API is re-exported from here so handlers depend on the
package, not on a specific implementation path.
"""

from capelle_platform.quota.base_enforcer import (
    BaseQuotaEnforcer,
    QuotaExceededError,
    QuotaStatus,
)
from capelle_platform.quota.impl_daily import DailyAnalysisQuotaEnforcer

__all__ = [
    "BaseQuotaEnforcer",
    "DailyAnalysisQuotaEnforcer",
    "QuotaExceededError",
    "QuotaStatus",
]
