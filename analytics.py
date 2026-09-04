"""Pure sales analytics. No amoCRM HTTP calls live here."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from dates import days_between

WON_STATUS_ID = 142
LOST_STATUS_ID = 143
UNSORTED_STATUS_TYPE = 1

MEANINGFUL_EVENT_TYPES = frozenset(
    {
        "incoming_call",
        "outgoing_call",
        "incoming_chat_message",
        "outgoing_chat_message",
        "incoming_sms",
        "outgoing_sms",
        "common_note_added",
        "attachment_note_added",
        "task_completed",
        "task_result_added",
        "lead_status_changed",
        "entity_responsible_changed",
        "site_visit_note_added",
        "lead_added",
    }
)


def is_won_status(status_id: int | None) -> bool:
    return int(status_id or 0) == WON_STATUS_ID


def is_lost_status(status_id: int | None) -> bool:
    return int(status_id or 0) == LOST_STATUS_ID


def is_closed_status(status_id: int | None) -> bool:
    return is_won_status(status_id) or is_lost_status(status_id)


def is_active_lead(lead: Mapping[str, Any]) -> bool:
    return not is_closed_status(lead.get("status_id"))


def calculate_revenue(leads: Iterable[Mapping[str, Any]]) -> float:
    return float(sum(float(lead.get("price") or 0) for lead in leads))


def calculate_conversion(won: int, new: int) -> float | None:
    if new <= 0:
        return None
    return round((won / new) * 100, 2)


def average_check(revenue: float, won: int) -> float:
    if won <= 0:
        return 0.0
    return round(revenue / won, 2)


def calculate_overdue(
    tasks: Iterable[Mapping[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    overdue: list[dict[str, Any]] = []
    now_ts = int(now.timestamp())
    for task in tasks:
        if task.get("is_completed"):
            continue
        complete_till = int(task.get("complete_till") or 0)
        if complete_till and complete_till < now_ts:
            item = dict(task)
            item["overdue_seconds"] = now_ts - complete_till
            overdue.append(item)
    overdue.sort(key=lambda item: int(item.get("complete_till") or 0))
    return overdue


def last_activity_timestamp(
    lead: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[int, str]:
    """Return (unix_ts, source) for the latest meaningful activity."""
    updated_at = int(lead.get("updated_at") or lead.get("created_at") or 0)
    if not events:
        return updated_at, "updated_at"
    latest = 0
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type and event_type not in MEANINGFUL_EVENT_TYPES:
            continue
        created = int(event.get("created_at") or 0)
        if created > latest:
            latest = created
    if latest <= 0:
        return updated_at, "updated_at"
    return latest, "events"


def calculate_stale(
    leads: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    days_without_activity: int,
    events_by_lead: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    threshold_seconds = days_without_activity * 86400
    now_ts = int(now.timestamp())
    stale: list[dict[str, Any]] = []
    for lead in leads:
        if not is_active_lead(lead):
            continue
        lead_id = int(lead.get("id") or 0)
        events = (events_by_lead or {}).get(lead_id)
        last_ts, source = last_activity_timestamp(lead, events)
        if last_ts <= 0:
            continue
        idle_seconds = now_ts - last_ts
        if idle_seconds >= threshold_seconds:
            item = dict(lead)
            item["last_activity_at"] = last_ts
            item["days_without_activity"] = round(idle_seconds / 86400, 2)
            item["activity_source"] = source
            stale.append(item)
    stale.sort(key=lambda item: int(item.get("last_activity_at") or 0))
    return stale


def leads_without_next_task(leads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for lead in leads:
        if not is_active_lead(lead):
            continue
        closest = int(lead.get("closest_task_at") or 0)
        if closest <= 0:
            result.append(dict(lead))
    return result


def group_leads_by_manager(leads: Iterable[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lead in leads:
        grouped[int(lead.get("responsible_user_id") or 0)].append(dict(lead))
    return dict(grouped)


def group_tasks_by_manager(tasks: Iterable[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        grouped[int(task.get("responsible_user_id") or 0)].append(dict(task))
    return dict(grouped)


def pipeline_snapshot(
    leads: Iterable[Mapping[str, Any]],
    statuses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_status: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lead in leads:
        by_status[int(lead.get("status_id") or 0)].append(dict(lead))

    snapshot: list[dict[str, Any]] = []
    for status in statuses:
        status_id = int(status.get("id") or 0)
        bucket = by_status.get(status_id, [])
        value = calculate_revenue(bucket)
        count = len(bucket)
        snapshot.append(
            {
                "status_id": status_id,
                "status_name": str(status.get("name") or ""),
                "is_won": is_won_status(status_id),
                "is_lost": is_lost_status(status_id),
                "leads_count": count,
                "leads_value": value,
                "average_lead_value": round(value / count, 2) if count else 0.0,
            }
        )
    return snapshot


def empty_manager_metrics(manager_id: int, manager_name: str) -> dict[str, Any]:
    return {
        "manager_id": manager_id,
        "manager_name": manager_name,
        "new_leads": 0,
        "active_leads": 0,
        "won_leads": 0,
        "lost_leads": 0,
        "won_revenue": 0.0,
        "overdue_tasks": 0,
        "stale_leads": 0,
        "leads_without_next_task": 0,
        "completed_tasks": 0,
    }


def aggregate_manager_metrics(
    *,
    manager_id: int,
    manager_name: str,
    new_leads: Sequence[Mapping[str, Any]],
    active_leads: Sequence[Mapping[str, Any]],
    won_leads: Sequence[Mapping[str, Any]],
    lost_leads: Sequence[Mapping[str, Any]],
    overdue_tasks: Sequence[Mapping[str, Any]],
    stale_leads: Sequence[Mapping[str, Any]],
    completed_tasks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    metrics = empty_manager_metrics(manager_id, manager_name)
    metrics["new_leads"] = len(new_leads)
    metrics["active_leads"] = len(active_leads)
    metrics["won_leads"] = len(won_leads)
    metrics["lost_leads"] = len(lost_leads)
    metrics["won_revenue"] = calculate_revenue(won_leads)
    metrics["overdue_tasks"] = len(overdue_tasks)
    metrics["stale_leads"] = len(stale_leads)
    metrics["leads_without_next_task"] = len(leads_without_next_task(active_leads))
    metrics["completed_tasks"] = len(completed_tasks or [])
    return metrics


def days_idle(last_activity: datetime, now: datetime) -> float:
    return round(max(days_between(last_activity, now), 0.0), 2)
