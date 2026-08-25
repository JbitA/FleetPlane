from __future__ import annotations

from datetime import UTC, datetime

from fleetplane.core.health import classify_health
from fleetplane.domain.enums import HealthState
from fleetplane.domain.models import FleetSummary
from fleetplane.ports.metrics import MetricSink, NullMetricSink
from fleetplane.ports.store import FleetStore


class ReconciliationService:
    """Refreshes time-derived device health in bounded batches.

    Fleet-summary reads never invoke this service. The materialized summary is maintained by
    state writes; this worker only handles health transitions caused by passage of time.
    """

    def __init__(self, store: FleetStore, metrics: MetricSink | None = None) -> None:
        self.store = store
        self.metrics = metrics or NullMetricSink()

    def fleet_summary(self) -> FleetSummary:
        return self.store.get_fleet_summary()

    def reconcile_batch(
        self,
        *,
        cursor: str | None = None,
        limit: int = 200,
        now: datetime | None = None,
        health_state: HealthState | None = None,
    ) -> dict[str, object]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        page = self.store.list_device_states_page(
            limit=limit,
            cursor=cursor,
            health_state=health_state,
        )
        changed = 0
        conflicts = 0
        for state in page.items:
            expected = state.projection_version
            health, reasons = classify_health(state, now)
            if health == state.health_state and reasons == state.health_reasons:
                continue
            state.health_state = health
            state.health_reasons = reasons
            if self.store.compare_and_swap_device_state(state, expected):
                changed += 1
            else:
                conflicts += 1
        return {
            "processed": len(page.items),
            "changed": changed,
            "conflicts": conflicts,
            "next_cursor": page.next_cursor,
        }
