"""Business orchestration: amoCRM client + analytics, no MCP protocol here."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from amocrm_client import AmoCRMClient
from analytics import (
    LOST_STATUS_ID,
    MEANINGFUL_EVENT_TYPES,
    WON_STATUS_ID,
    aggregate_manager_metrics,
    average_check,
    calculate_conversion,
    calculate_overdue,
    calculate_revenue,
    calculate_stale,
    group_leads_by_manager,
    group_tasks_by_manager,
    is_active_lead,
    last_activity_timestamp,
    leads_without_next_task,
    pipeline_snapshot,
)
from config import Settings
from dates import datetime_to_unix, isoformat_or_none, now_tz, parse_datetime, parse_period, unix_to_datetime
from exceptions import AmoCRMNotFoundError, AmoCRMPermissionError, AmoCRMValidationError, ConfigurationError
from schemas import (
    AddNoteResult,
    CompareManagersResult,
    CreateTaskResult,
    LeadHistory,
    LeadsWithoutTasksResult,
    LeadWithoutTask,
    ManagerComparisonRow,
    ManagerMetrics,
    ManagerPerformance,
    ManagerRef,
    MoveLeadResult,
    OverdueByManager,
    OverdueTask,
    OverdueTasksResult,
    Period,
    PipelineRef,
    PipelineStatusSummary,
    PipelineSummary,
    SalesOverview,
    SearchLead,
    SearchLeadsResult,
    StaleLead,
    StaleLeadsResult,
    StatusRef,
    TimelineItem,
)

logger = logging.getLogger(__name__)

EVENT_DESCRIPTIONS = {
    "lead_added": "Lead created",
    "lead_status_changed": "Lead stage changed",
    "entity_responsible_changed": "Responsible user changed",
    "task_added": "Task created",
    "task_completed": "Task completed",
    "task_deleted": "Task deleted",
    "common_note_added": "Note added",
    "attachment_note_added": "File attached",
    "incoming_call": "Incoming call",
    "outgoing_call": "Outgoing call",
    "incoming_chat_message": "Incoming chat message",
    "outgoing_chat_message": "Outgoing chat message",
    "incoming_sms": "Incoming SMS",
    "outgoing_sms": "Outgoing SMS",
    "site_visit_note_added": "Site visit",
    "lead_deleted": "Lead deleted",
    "lead_restored": "Lead restored",
}


class ROPService:
    def __init__(self, client: AmoCRMClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    @property
    def tz(self) -> str:
        return self.settings.app_timezone

    def period(self, date_from: str | None, date_to: str | None) -> tuple[datetime, datetime, Period]:
        start, finish = parse_period(date_from, date_to, self.tz)
        return start, finish, Period(date_from=start.isoformat(), date_to=finish.isoformat(), timezone=self.tz)

    async def catalogs(self) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[tuple[int, int], dict[str, Any]]]:
        users = {int(user["id"]): user for user in await self.client.get_users() if user.get("id")}
        pipelines = {int(item["id"]): item for item in await self.client.get_pipelines() if item.get("id")}
        statuses: dict[tuple[int, int], dict[str, Any]] = {}
        for pipeline in pipelines.values():
            embedded = pipeline.get("_embedded") or {}
            for status in embedded.get("statuses") or []:
                statuses[(int(pipeline["id"]), int(status["id"]))] = status
        return users, pipelines, statuses

    def _manager_ref(self, user_id: int | None, users: dict[int, dict[str, Any]]) -> ManagerRef:
        user_id = int(user_id or 0)
        user = users.get(user_id) or {}
        name = str(user.get("name") or user.get("email") or (f"User {user_id}" if user_id else "Unassigned"))
        return ManagerRef(id=user_id, name=name)

    def _pipeline_ref(self, pipeline_id: int | None, pipelines: dict[int, dict[str, Any]]) -> PipelineRef:
        pipeline_id = int(pipeline_id or 0)
        pipeline = pipelines.get(pipeline_id) or {}
        return PipelineRef(id=pipeline_id, name=str(pipeline.get("name") or f"Pipeline {pipeline_id}"))

    def _status_ref(
        self,
        pipeline_id: int | None,
        status_id: int | None,
        pipelines: dict[int, dict[str, Any]],
    ) -> StatusRef:
        status_id = int(status_id or 0)
        pipeline_id = int(pipeline_id or 0)
        pipeline = pipelines.get(pipeline_id) or {}
        embedded = pipeline.get("_embedded") or {}
        for status in embedded.get("statuses") or []:
            if int(status.get("id") or 0) == status_id:
                return StatusRef(id=status_id, name=str(status.get("name") or f"Status {status_id}"))
        return StatusRef(id=status_id, name=f"Status {status_id}")

    def _lead_filters(
        self,
        *,
        pipeline_id: int | None = None,
        manager_id: int | None = None,
        manager_ids: list[int] | None = None,
        status_id: int | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        closed_from: datetime | None = None,
        closed_to: datetime | None = None,
        updated_to: datetime | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        statuses: list[dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if pipeline_id:
            filters["pipeline_id"] = pipeline_id
        ids = manager_ids or ([manager_id] if manager_id else None)
        if ids:
            filters["responsible_user_id"] = ids
        if status_id and pipeline_id:
            filters["statuses"] = [{"pipeline_id": pipeline_id, "status_id": status_id}]
        elif status_id:
            filters["statuses"] = [{"status_id": status_id}]
        if statuses:
            filters["statuses"] = statuses
        if created_from or created_to:
            created: dict[str, int] = {}
            if created_from:
                created["from"] = datetime_to_unix(created_from)
            if created_to:
                created["to"] = datetime_to_unix(created_to)
            filters["created_at"] = created
        if closed_from or closed_to:
            closed: dict[str, int] = {}
            if closed_from:
                closed["from"] = datetime_to_unix(closed_from)
            if closed_to:
                closed["to"] = datetime_to_unix(closed_to)
            filters["closed_at"] = closed
        if updated_to:
            filters["updated_at"] = {"to": datetime_to_unix(updated_to)}
        if min_price is not None or max_price is not None:
            price: dict[str, float] = {}
            if min_price is not None:
                price["from"] = min_price
            if max_price is not None:
                price["to"] = max_price
            filters["price"] = price
        return filters

    async def _closed_leads(
        self,
        *,
        status_id: int,
        date_from: datetime,
        date_to: datetime,
        pipeline_id: int | None,
        manager_ids: list[int] | None,
    ) -> list[dict[str, Any]]:
        pipelines = await self.client.get_pipelines()
        if pipeline_id:
            statuses = [{"pipeline_id": pipeline_id, "status_id": status_id}]
        else:
            statuses = [{"pipeline_id": int(item["id"]), "status_id": status_id} for item in pipelines if item.get("id")]
        return await self.client.get_leads(
            filters=self._lead_filters(
                manager_ids=manager_ids,
                closed_from=date_from,
                closed_to=date_to,
                statuses=statuses,
            )
        )

    async def _active_leads(
        self,
        *,
        pipeline_id: int | None = None,
        manager_id: int | None = None,
        manager_ids: list[int] | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        leads = await self.client.get_leads(
            filters=self._lead_filters(
                pipeline_id=pipeline_id,
                manager_id=manager_id,
                manager_ids=manager_ids,
                status_id=status_id,
                min_price=min_price,
            ),
            max_items=max_items,
        )
        return [lead for lead in leads if is_active_lead(lead)]

    async def _events_map(self, leads: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        if not leads:
            return {}
        try:
            events = await self.client.get_events_for_leads([int(lead["id"]) for lead in leads if lead.get("id")])
        except AmoCRMPermissionError:
            logger.warning("Events API is not available; falling back to lead updated_at")
            return {}
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            entity_id = int(event.get("entity_id") or 0)
            grouped[entity_id].append(event)
        return dict(grouped)

    async def sales_overview(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
        manager_ids: list[int] | None = None,
    ) -> SalesOverview:
        start, finish, period = self.period(date_from, date_to)
        pipeline_id = pipeline_id or self.settings.default_pipeline_id
        users, pipelines, _ = await self.catalogs()
        stale_days = self.settings.default_stale_days
        now = now_tz(self.tz)

        new_leads = await self.client.get_leads(
            filters=self._lead_filters(
                pipeline_id=pipeline_id,
                manager_ids=manager_ids,
                created_from=start,
                created_to=finish,
            )
        )
        won_leads = await self._closed_leads(
            status_id=WON_STATUS_ID,
            date_from=start,
            date_to=finish,
            pipeline_id=pipeline_id,
            manager_ids=manager_ids,
        )
        lost_leads = await self._closed_leads(
            status_id=LOST_STATUS_ID,
            date_from=start,
            date_to=finish,
            pipeline_id=pipeline_id,
            manager_ids=manager_ids,
        )
        active_leads = await self._active_leads(pipeline_id=pipeline_id, manager_ids=manager_ids)
        tasks = await self.client.get_tasks(
            filters={"is_completed": False, **({"responsible_user_id": manager_ids} if manager_ids else {})},
            order={"complete_till": "asc"},
        )
        overdue = calculate_overdue(tasks, now)
        events_map = await self._events_map(active_leads)
        stale = calculate_stale(active_leads, now=now, days_without_activity=stale_days, events_by_lead=events_map)
        without_tasks = leads_without_next_task(active_leads)

        involved_ids = set(manager_ids or [])
        for collection in (new_leads, won_leads, lost_leads, active_leads, overdue, stale, without_tasks):
            for item in collection:
                involved_ids.add(int(item.get("responsible_user_id") or 0))

        managers: list[ManagerMetrics] = []
        grouped_new = group_leads_by_manager(new_leads)
        grouped_won = group_leads_by_manager(won_leads)
        grouped_lost = group_leads_by_manager(lost_leads)
        grouped_active = group_leads_by_manager(active_leads)
        grouped_overdue = group_tasks_by_manager(overdue)
        grouped_stale = group_leads_by_manager(stale)
        for manager_id in sorted(involved_ids):
            if manager_id == 0 and not manager_ids:
                continue
            ref = self._manager_ref(manager_id, users)
            metrics = aggregate_manager_metrics(
                manager_id=manager_id,
                manager_name=ref.name,
                new_leads=grouped_new.get(manager_id, []),
                active_leads=grouped_active.get(manager_id, []),
                won_leads=grouped_won.get(manager_id, []),
                lost_leads=grouped_lost.get(manager_id, []),
                overdue_tasks=grouped_overdue.get(manager_id, []),
                stale_leads=grouped_stale.get(manager_id, []),
            )
            managers.append(ManagerMetrics.model_validate(metrics))

        truncated = len(active_leads) >= self.settings.max_list_items
        return SalesOverview(
            period=period,
            new_leads=len(new_leads),
            active_leads=len(active_leads),
            won_leads=len(won_leads),
            lost_leads=len(lost_leads),
            won_revenue=calculate_revenue(won_leads),
            overdue_tasks=len(overdue),
            leads_without_next_task=len(without_tasks),
            stale_leads=len(stale),
            truncated=truncated,
            managers=managers,
        )

    async def pipeline_summary(self, pipeline_id: int | None = None, manager_id: int | None = None) -> PipelineSummary:
        pipeline_id = pipeline_id or self.settings.default_pipeline_id
        pipelines = await self.client.get_pipelines()
        if pipeline_id is None:
            main = next((item for item in pipelines if item.get("is_main")), pipelines[0] if pipelines else None)
            if main is None:
                raise AmoCRMNotFoundError("No pipelines are available in this amoCRM account")
            pipeline_id = int(main["id"])
        pipeline = next((item for item in pipelines if int(item.get("id") or 0) == pipeline_id), None)
        if pipeline is None:
            raise AmoCRMNotFoundError(f"Pipeline {pipeline_id} was not found")
        statuses = (pipeline.get("_embedded") or {}).get("statuses") or []
        leads = await self.client.get_leads(filters=self._lead_filters(pipeline_id=pipeline_id, manager_id=manager_id))
        snapshot = pipeline_snapshot(leads, statuses)
        active = [lead for lead in leads if is_active_lead(lead)]
        won = [lead for lead in leads if int(lead.get("status_id") or 0) == WON_STATUS_ID]
        lost = [lead for lead in leads if int(lead.get("status_id") or 0) == LOST_STATUS_ID]
        return PipelineSummary(
            pipeline_id=pipeline_id,
            pipeline_name=str(pipeline.get("name") or ""),
            statuses=[PipelineStatusSummary.model_validate(item) for item in snapshot],
            total_active=len(active),
            total_won=len(won),
            total_lost=len(lost),
            total_value_active=calculate_revenue(active),
        )

    async def manager_performance(
        self,
        manager_id: int,
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
    ) -> ManagerPerformance:
        start, finish, period = self.period(date_from, date_to)
        users, _, _ = await self.catalogs()
        if manager_id not in users:
            raise AmoCRMNotFoundError(f"User {manager_id} was not found or is not readable by this integration")
        pipeline_id = pipeline_id or self.settings.default_pipeline_id
        now = now_tz(self.tz)
        stale_days = self.settings.default_stale_days
        manager_ids = [manager_id]

        new_leads = await self.client.get_leads(
            filters=self._lead_filters(pipeline_id=pipeline_id, manager_ids=manager_ids, created_from=start, created_to=finish)
        )
        won_leads = await self._closed_leads(
            status_id=WON_STATUS_ID, date_from=start, date_to=finish, pipeline_id=pipeline_id, manager_ids=manager_ids
        )
        lost_leads = await self._closed_leads(
            status_id=LOST_STATUS_ID, date_from=start, date_to=finish, pipeline_id=pipeline_id, manager_ids=manager_ids
        )
        active_leads = await self._active_leads(pipeline_id=pipeline_id, manager_ids=manager_ids)
        open_tasks = await self.client.get_tasks(filters={"is_completed": False, "responsible_user_id": manager_id})
        completed_tasks = await self.client.get_tasks(
            filters={
                "is_completed": True,
                "responsible_user_id": manager_id,
                "updated_at": {"from": datetime_to_unix(start), "to": datetime_to_unix(finish)},
            }
        )
        overdue = calculate_overdue(open_tasks, now)
        events_map = await self._events_map(active_leads)
        stale = calculate_stale(active_leads, now=now, days_without_activity=stale_days, events_by_lead=events_map)
        without_tasks = leads_without_next_task(active_leads)
        revenue = calculate_revenue(won_leads)
        conversion = calculate_conversion(len(won_leads), len(new_leads))
        notes = [
            "new_leads counts leads created in the selected period.",
            "won_leads / lost_leads count leads closed into system statuses 142 / 143 during the selected period.",
            "active_leads, stale_leads, overdue_tasks and leads_without_next_task are a current snapshot, not historical.",
            "conversion_new_to_won = won_leads / new_leads for the same period. This is not historical stage-to-stage conversion.",
            "completed_tasks uses tasks marked completed in the selected period (updated_at), which is an approximation.",
            "stale_leads prefer Events API timestamps; if events are unavailable, lead.updated_at is used as a fallback.",
            "Higher CRM activity does not mean better performance. Results and activity are separate metrics.",
        ]
        return ManagerPerformance(
            manager=self._manager_ref(manager_id, users),
            period=period,
            new_leads=len(new_leads),
            active_leads=len(active_leads),
            won_leads=len(won_leads),
            lost_leads=len(lost_leads),
            won_revenue=revenue,
            average_won_check=average_check(revenue, len(won_leads)),
            completed_tasks=len(completed_tasks),
            overdue_tasks=len(overdue),
            stale_leads=len(stale),
            leads_without_next_task=len(without_tasks),
            conversion_new_to_won=conversion,
            calculation_notes=notes,
        )

    async def compare_managers(
        self,
        manager_ids: list[int],
        date_from: str | None = None,
        date_to: str | None = None,
        pipeline_id: int | None = None,
    ) -> CompareManagersResult:
        if len(set(manager_ids)) < 2:
            raise AmoCRMValidationError("compare_managers requires at least two distinct manager_ids")
        rows: list[ManagerComparisonRow] = []
        notes: list[str] = []
        period: Period | None = None
        for manager_id in manager_ids:
            performance = await self.manager_performance(manager_id, date_from, date_to, pipeline_id)
            period = performance.period
            notes = performance.calculation_notes
            rows.append(
                ManagerComparisonRow(
                    manager_id=performance.manager.id,
                    manager_name=performance.manager.name,
                    new_leads=performance.new_leads,
                    won=performance.won_leads,
                    lost=performance.lost_leads,
                    active=performance.active_leads,
                    won_revenue=performance.won_revenue,
                    average_check=performance.average_won_check,
                    conversion=performance.conversion_new_to_won,
                    completed_tasks=performance.completed_tasks,
                    overdue_tasks=performance.overdue_tasks,
                    stale_leads=performance.stale_leads,
                    leads_without_next_task=performance.leads_without_next_task,
                )
            )
        assert period is not None
        notes = notes + ["This comparison does not assign a ranking or label managers as good or bad."]
        return CompareManagersResult(period=period, managers=rows, calculation_notes=notes)

    async def find_stale_leads(
        self,
        days_without_activity: int | None = None,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        limit: int = 50,
    ) -> StaleLeadsResult:
        days = days_without_activity or self.settings.default_stale_days
        now = now_tz(self.tz)
        users, pipelines, _ = await self.catalogs()
        leads = await self._active_leads(
            pipeline_id=pipeline_id or self.settings.default_pipeline_id,
            manager_id=manager_id,
            status_id=status_id,
            min_price=min_price,
        )
        events_map = await self._events_map(leads)
        stale = calculate_stale(leads, now=now, days_without_activity=days, events_by_lead=events_map)
        open_tasks = await self.client.get_tasks(filters={"is_completed": False, "entity_type": "leads"})
        overdue_by_lead = {
            int(task.get("entity_id") or 0)
            for task in calculate_overdue(open_tasks, now)
            if task.get("entity_type") == "leads"
        }
        items: list[StaleLead] = []
        for lead in stale[:limit]:
            pipeline_id_value = int(lead.get("pipeline_id") or 0)
            lead_id = int(lead["id"])
            items.append(
                StaleLead(
                    lead_id=lead_id,
                    lead_name=str(lead.get("name") or ""),
                    price=float(lead.get("price") or 0),
                    pipeline=self._pipeline_ref(pipeline_id_value, pipelines),
                    status=self._status_ref(pipeline_id_value, int(lead.get("status_id") or 0), pipelines),
                    manager=self._manager_ref(int(lead.get("responsible_user_id") or 0), users),
                    updated_at=isoformat_or_none(unix_to_datetime(lead.get("updated_at"), self.tz)),
                    last_activity_at=isoformat_or_none(unix_to_datetime(lead.get("last_activity_at"), self.tz)),
                    days_without_activity=float(lead.get("days_without_activity") or 0),
                    closest_task_at=isoformat_or_none(unix_to_datetime(lead.get("closest_task_at"), self.tz)),
                    has_overdue_task=lead_id in overdue_by_lead,
                    activity_source=lead.get("activity_source") or "updated_at",
                )
            )
        return StaleLeadsResult(
            total=len(stale),
            days_without_activity=days,
            truncated=len(stale) > limit,
            leads=items,
        )

    async def find_overdue_tasks(
        self,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        limit: int = 100,
    ) -> OverdueTasksResult:
        now = now_tz(self.tz)
        users, pipelines, _ = await self.catalogs()
        filters: dict[str, Any] = {"is_completed": False}
        if manager_id:
            filters["responsible_user_id"] = manager_id
        tasks = await self.client.get_tasks(filters=filters, order={"complete_till": "asc"})
        overdue = calculate_overdue(tasks, now)
        lead_ids = [
            int(task["entity_id"])
            for task in overdue
            if task.get("entity_type") == "leads" and task.get("entity_id")
        ]
        leads_by_id: dict[int, dict[str, Any]] = {}
        if lead_ids:
            unique = list(dict.fromkeys(lead_ids))
            fetched = await self.client.get_leads(filters={"id": unique[:250]})
            leads_by_id = {int(lead["id"]): lead for lead in fetched}
            if pipeline_id:
                overdue = [
                    task
                    for task in overdue
                    if task.get("entity_type") != "leads"
                    or int((leads_by_id.get(int(task.get("entity_id") or 0)) or {}).get("pipeline_id") or 0) == pipeline_id
                ]
        by_manager_map: dict[int, int] = defaultdict(int)
        for task in overdue:
            by_manager_map[int(task.get("responsible_user_id") or 0)] += 1
        result_tasks: list[OverdueTask] = []
        for task in overdue[:limit]:
            lead = leads_by_id.get(int(task.get("entity_id") or 0)) if task.get("entity_type") == "leads" else None
            pipeline_ref = None
            status_ref = None
            if lead:
                pipeline_ref = self._pipeline_ref(int(lead.get("pipeline_id") or 0), pipelines)
                status_ref = self._status_ref(int(lead.get("pipeline_id") or 0), int(lead.get("status_id") or 0), pipelines)
            result_tasks.append(
                OverdueTask(
                    task_id=int(task["id"]),
                    text=str(task.get("text") or ""),
                    task_type_id=int(task.get("task_type_id") or 0),
                    responsible_user=self._manager_ref(int(task.get("responsible_user_id") or 0), users),
                    complete_till=isoformat_or_none(unix_to_datetime(task.get("complete_till"), self.tz)),
                    overdue_seconds=int(task.get("overdue_seconds") or 0),
                    entity_id=int(task["entity_id"]) if task.get("entity_id") else None,
                    entity_type=str(task.get("entity_type") or "") or None,
                    lead_name=str(lead.get("name") or "") if lead else None,
                    price=float(lead.get("price") or 0) if lead else None,
                    pipeline=pipeline_ref,
                    status=status_ref,
                )
            )
        by_manager = [
            OverdueByManager(manager_id=manager_id, manager_name=self._manager_ref(manager_id, users).name, count=count)
            for manager_id, count in sorted(by_manager_map.items(), key=lambda item: item[1], reverse=True)
        ]
        return OverdueTasksResult(total=len(overdue), by_manager=by_manager, truncated=len(overdue) > limit, tasks=result_tasks)

    async def find_leads_without_tasks(
        self,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        limit: int = 100,
    ) -> LeadsWithoutTasksResult:
        users, pipelines, _ = await self.catalogs()
        leads = await self._active_leads(
            pipeline_id=pipeline_id or self.settings.default_pipeline_id,
            manager_id=manager_id,
            status_id=status_id,
            min_price=min_price,
        )
        missing = leads_without_next_task(leads)
        events_map = await self._events_map(missing[:limit])
        items: list[LeadWithoutTask] = []
        for lead in missing[:limit]:
            last_ts, _source = last_activity_timestamp(lead, events_map.get(int(lead["id"])))
            pipeline_id_value = int(lead.get("pipeline_id") or 0)
            items.append(
                LeadWithoutTask(
                    lead_id=int(lead["id"]),
                    lead_name=str(lead.get("name") or ""),
                    price=float(lead.get("price") or 0),
                    manager=self._manager_ref(int(lead.get("responsible_user_id") or 0), users),
                    pipeline=self._pipeline_ref(pipeline_id_value, pipelines),
                    status=self._status_ref(pipeline_id_value, int(lead.get("status_id") or 0), pipelines),
                    updated_at=isoformat_or_none(unix_to_datetime(lead.get("updated_at"), self.tz)),
                    last_activity_at=isoformat_or_none(unix_to_datetime(last_ts, self.tz)),
                )
            )
        return LeadsWithoutTasksResult(total=len(missing), truncated=len(missing) > limit, leads=items)

    async def lead_history(self, lead_id: int) -> LeadHistory:
        users, pipelines, _ = await self.catalogs()
        lead = await self.client.get_lead(lead_id)
        if not lead or not lead.get("id"):
            raise AmoCRMNotFoundError(f"Lead {lead_id} was not found")
        events = await self.client.get_events(filters={"entity": "lead", "entity_id": [lead_id]}, max_items=250)
        notes = await self.client.get_notes("leads", lead_id, max_items=250)
        tasks = await self.client.get_tasks(filters={"entity_type": "leads", "entity_id": [lead_id]}, max_items=250)
        timeline: list[TimelineItem] = []
        pipeline_id = int(lead.get("pipeline_id") or 0)

        for event in events:
            created = unix_to_datetime(event.get("created_at"), self.tz)
            if created is None:
                continue
            event_type = str(event.get("type") or "event")
            timeline.append(
                TimelineItem(
                    timestamp=created.isoformat(),
                    event_type=event_type,
                    actor=self._manager_ref(int(event.get("created_by") or 0), users) if event.get("created_by") else None,
                    description=self._describe_event(event, pipelines),
                    metadata=_event_metadata(event),
                )
            )
        for note in notes:
            created = unix_to_datetime(note.get("created_at"), self.tz)
            if created is None:
                continue
            params = note.get("params") or {}
            timeline.append(
                TimelineItem(
                    timestamp=created.isoformat(),
                    event_type=str(note.get("note_type") or "note"),
                    actor=self._manager_ref(int(note.get("created_by") or 0), users) if note.get("created_by") else None,
                    description=str(params.get("text") or "Note added"),
                    metadata={"note_id": note.get("id"), "note_type": note.get("note_type")},
                )
            )
        for task in tasks:
            created = unix_to_datetime(task.get("created_at"), self.tz)
            if created is not None:
                timeline.append(
                    TimelineItem(
                        timestamp=created.isoformat(),
                        event_type="task_added",
                        actor=self._manager_ref(int(task.get("created_by") or 0), users) if task.get("created_by") else None,
                        description=f"Task created: {task.get('text') or ''}".strip(),
                        metadata={"task_id": task.get("id"), "complete_till": task.get("complete_till")},
                    )
                )
            if task.get("is_completed"):
                completed = unix_to_datetime(task.get("updated_at"), self.tz)
                if completed is not None:
                    timeline.append(
                        TimelineItem(
                            timestamp=completed.isoformat(),
                            event_type="task_completed",
                            actor=self._manager_ref(int(task.get("updated_by") or 0), users) if task.get("updated_by") else None,
                            description=f"Task completed: {task.get('text') or ''}".strip(),
                            metadata={"task_id": task.get("id")},
                        )
                    )

        timeline.sort(key=lambda item: item.timestamp)
        current_state = {
            "lead_id": int(lead["id"]),
            "name": lead.get("name"),
            "price": lead.get("price") or 0,
            "manager": self._manager_ref(int(lead.get("responsible_user_id") or 0), users).model_dump(),
            "pipeline": self._pipeline_ref(pipeline_id, pipelines).model_dump(),
            "status": self._status_ref(pipeline_id, int(lead.get("status_id") or 0), pipelines).model_dump(),
            "updated_at": isoformat_or_none(unix_to_datetime(lead.get("updated_at"), self.tz)),
            "closest_task_at": isoformat_or_none(unix_to_datetime(lead.get("closest_task_at"), self.tz)),
            "is_active": is_active_lead(lead),
        }
        compact_lead = {
            "lead_id": int(lead["id"]),
            "name": lead.get("name"),
            "price": lead.get("price") or 0,
            "created_at": isoformat_or_none(unix_to_datetime(lead.get("created_at"), self.tz)),
            "closed_at": isoformat_or_none(unix_to_datetime(lead.get("closed_at"), self.tz)),
        }
        return LeadHistory(lead=compact_lead, current_state=current_state, timeline=timeline)

    def _describe_event(self, event: dict[str, Any], pipelines: dict[int, dict[str, Any]]) -> str:
        event_type = str(event.get("type") or "event")
        base = EVENT_DESCRIPTIONS.get(event_type, event_type)
        if event_type == "lead_status_changed":
            after = _nested_status(event.get("value_after"))
            before = _nested_status(event.get("value_before"))
            if after:
                after_name = self._status_ref(after.get("pipeline_id"), after.get("id"), pipelines).name
                before_name = self._status_ref(before.get("pipeline_id"), before.get("id"), pipelines).name if before else "?"
                return f"Stage changed: {before_name} → {after_name}"
        return base

    async def search_leads(
        self,
        query: str | None = None,
        lead_id: int | None = None,
        manager_id: int | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 20,
    ) -> SearchLeadsResult:
        users, pipelines, _ = await self.catalogs()
        if lead_id:
            lead = await self.client.get_lead(lead_id)
            leads = [lead] if lead and lead.get("id") else []
        else:
            leads = await self.client.get_leads(
                query=query,
                filters=self._lead_filters(
                    pipeline_id=pipeline_id,
                    manager_id=manager_id,
                    status_id=status_id,
                    min_price=min_price,
                    max_price=max_price,
                ),
                max_items=limit,
                page_limit=min(limit, 250),
            )
        items: list[SearchLead] = []
        for lead in leads[:limit]:
            pipeline_id_value = int(lead.get("pipeline_id") or 0)
            items.append(
                SearchLead(
                    lead_id=int(lead["id"]),
                    name=str(lead.get("name") or ""),
                    price=float(lead.get("price") or 0),
                    manager=self._manager_ref(int(lead.get("responsible_user_id") or 0), users),
                    pipeline=self._pipeline_ref(pipeline_id_value, pipelines),
                    status=self._status_ref(pipeline_id_value, int(lead.get("status_id") or 0), pipelines),
                    updated_at=isoformat_or_none(unix_to_datetime(lead.get("updated_at"), self.tz)),
                )
            )
        return SearchLeadsResult(total=len(items), leads=items)

    async def create_task(
        self,
        entity_id: int,
        responsible_user_id: int,
        complete_till: str,
        text: str,
        entity_type: str = "leads",
        task_type_id: int | None = None,
    ) -> CreateTaskResult:
        from dates import is_reasonable_task_due

        if entity_type not in {"leads", "contacts", "companies", "customers"}:
            raise AmoCRMValidationError("entity_type must be leads, contacts, companies or customers")
        if not text.strip():
            raise AmoCRMValidationError("Task text must not be empty")
        due = parse_datetime(complete_till, self.tz)
        ok, reason = is_reasonable_task_due(due, now_tz(self.tz))
        if not ok:
            raise AmoCRMValidationError(reason)
        users, _, _ = await self.catalogs()
        if responsible_user_id not in users:
            raise AmoCRMNotFoundError(f"User {responsible_user_id} was not found")
        if entity_type == "leads":
            lead = await self.client.get_lead(entity_id)
            if not lead.get("id"):
                raise AmoCRMNotFoundError(f"Lead {entity_id} was not found")
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "text": text.strip(),
            "complete_till": datetime_to_unix(due),
            "responsible_user_id": responsible_user_id,
        }
        if task_type_id is not None:
            payload["task_type_id"] = task_type_id
        created = await self.client.create_tasks([payload])
        if not created:
            raise ConfigurationError("amoCRM did not return a created task")
        task = created[0]
        return CreateTaskResult(
            success=True,
            task_id=int(task["id"]),
            entity={"id": entity_id, "type": entity_type},
            responsible_user=self._manager_ref(responsible_user_id, users),
            complete_till=due.isoformat(),
            text=text.strip(),
        )

    async def move_lead(self, lead_id: int, status_id: int, pipeline_id: int | None = None) -> MoveLeadResult:
        users_unused, pipelines, statuses = await self.catalogs()
        del users_unused
        lead = await self.client.get_lead(lead_id)
        if not lead.get("id"):
            raise AmoCRMNotFoundError(f"Lead {lead_id} was not found")
        current_pipeline_id = int(lead.get("pipeline_id") or 0)
        current_status_id = int(lead.get("status_id") or 0)
        target_pipeline_id = pipeline_id or current_pipeline_id
        if target_pipeline_id not in pipelines:
            raise AmoCRMValidationError(f"Pipeline {target_pipeline_id} does not exist")
        target_status = statuses.get((target_pipeline_id, status_id))
        if target_status is None:
            raise AmoCRMValidationError(f"Status {status_id} does not belong to pipeline {target_pipeline_id}")
        payload: dict[str, Any] = {"status_id": status_id}
        if target_pipeline_id != current_pipeline_id:
            payload["pipeline_id"] = target_pipeline_id
        await self.client.update_lead(lead_id, payload)
        from_pipeline = self._pipeline_ref(current_pipeline_id, pipelines)
        from_status = self._status_ref(current_pipeline_id, current_status_id, pipelines)
        to_pipeline = self._pipeline_ref(target_pipeline_id, pipelines)
        to_status = self._status_ref(target_pipeline_id, status_id, pipelines)
        return MoveLeadResult.model_validate(
            {
                "success": True,
                "lead_id": lead_id,
                "from": {"pipeline": from_pipeline.name, "status": from_status.name},
                "to": {"pipeline": to_pipeline.name, "status": to_status.name},
            }
        )

    async def add_note(self, lead_id: int, text: str) -> AddNoteResult:
        if not text.strip():
            raise AmoCRMValidationError("Note text must not be empty")
        lead = await self.client.get_lead(lead_id)
        if not lead.get("id"):
            raise AmoCRMNotFoundError(f"Lead {lead_id} was not found")
        note = await self.client.add_note("leads", lead_id, text.strip())
        return AddNoteResult(
            success=True,
            lead_id=lead_id,
            note_id=int(note.get("id") or 0),
            text=text.strip(),
            created_at=isoformat_or_none(unix_to_datetime(note.get("created_at"), self.tz)),
        )


def _nested_status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if isinstance(first, dict) and "lead_status" in first:
        status = first.get("lead_status")
        return status if isinstance(status, dict) else None
    return None


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata = {"event_id": event.get("id"), "type": event.get("type")}
    if event.get("type") not in MEANINGFUL_EVENT_TYPES:
        metadata["meaningful_activity"] = False
    return metadata
