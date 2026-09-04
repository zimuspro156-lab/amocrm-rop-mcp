"""Pydantic models for MCP tool inputs and compact LLM-facing outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Period(BaseModel):
    date_from: str
    date_to: str
    timezone: str


class ManagerRef(BaseModel):
    id: int
    name: str


class StatusRef(BaseModel):
    id: int
    name: str


class PipelineRef(BaseModel):
    id: int
    name: str


class ManagerMetrics(BaseModel):
    manager_id: int
    manager_name: str
    new_leads: int = 0
    active_leads: int = 0
    won_leads: int = 0
    lost_leads: int = 0
    won_revenue: float = 0
    overdue_tasks: int = 0
    stale_leads: int = 0
    leads_without_next_task: int = 0


class SalesOverview(BaseModel):
    period: Period
    new_leads: int
    active_leads: int
    won_leads: int
    lost_leads: int
    won_revenue: float
    overdue_tasks: int
    leads_without_next_task: int
    stale_leads: int
    truncated: bool = False
    managers: list[ManagerMetrics]


class PipelineStatusSummary(BaseModel):
    status_id: int
    status_name: str
    is_won: bool = False
    is_lost: bool = False
    leads_count: int
    leads_value: float
    average_lead_value: float


class PipelineSummary(BaseModel):
    pipeline_id: int
    pipeline_name: str
    statuses: list[PipelineStatusSummary]
    total_active: int
    total_won: int
    total_lost: int
    total_value_active: float


class ManagerPerformance(BaseModel):
    manager: ManagerRef
    period: Period
    new_leads: int
    active_leads: int
    won_leads: int
    lost_leads: int
    won_revenue: float
    average_won_check: float
    completed_tasks: int
    overdue_tasks: int
    stale_leads: int
    leads_without_next_task: int
    conversion_new_to_won: float | None
    calculation_notes: list[str]


class ManagerComparisonRow(BaseModel):
    manager_id: int
    manager_name: str
    new_leads: int
    won: int
    lost: int
    active: int
    won_revenue: float
    average_check: float
    conversion: float | None
    completed_tasks: int
    overdue_tasks: int
    stale_leads: int
    leads_without_next_task: int


class CompareManagersResult(BaseModel):
    period: Period
    managers: list[ManagerComparisonRow]
    calculation_notes: list[str]


class StaleLead(BaseModel):
    lead_id: int
    lead_name: str
    price: float
    pipeline: PipelineRef
    status: StatusRef
    manager: ManagerRef
    updated_at: str | None
    last_activity_at: str | None
    days_without_activity: float
    closest_task_at: str | None
    has_overdue_task: bool
    activity_source: Literal["events", "updated_at"]


class StaleLeadsResult(BaseModel):
    total: int
    days_without_activity: int
    truncated: bool = False
    leads: list[StaleLead]


class OverdueTask(BaseModel):
    task_id: int
    text: str
    task_type_id: int
    responsible_user: ManagerRef
    complete_till: str | None
    overdue_seconds: int
    entity_id: int | None = None
    entity_type: str | None = None
    lead_name: str | None = None
    price: float | None = None
    pipeline: PipelineRef | None = None
    status: StatusRef | None = None


class OverdueByManager(BaseModel):
    manager_id: int
    manager_name: str
    count: int


class OverdueTasksResult(BaseModel):
    total: int
    by_manager: list[OverdueByManager]
    truncated: bool = False
    tasks: list[OverdueTask]


class LeadWithoutTask(BaseModel):
    lead_id: int
    lead_name: str
    price: float
    manager: ManagerRef
    pipeline: PipelineRef
    status: StatusRef
    updated_at: str | None
    last_activity_at: str | None


class LeadsWithoutTasksResult(BaseModel):
    total: int
    truncated: bool = False
    leads: list[LeadWithoutTask]


class TimelineItem(BaseModel):
    timestamp: str
    event_type: str
    actor: ManagerRef | None = None
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeadHistory(BaseModel):
    lead: dict[str, Any]
    current_state: dict[str, Any]
    timeline: list[TimelineItem]


class SearchLead(BaseModel):
    lead_id: int
    name: str
    price: float
    manager: ManagerRef
    pipeline: PipelineRef
    status: StatusRef
    updated_at: str | None


class SearchLeadsResult(BaseModel):
    total: int
    leads: list[SearchLead]


class CreateTaskResult(BaseModel):
    success: bool
    task_id: int
    entity: dict[str, Any]
    responsible_user: ManagerRef
    complete_till: str
    text: str


class MoveLeadResult(BaseModel):
    success: bool
    lead_id: int
    from_state: dict[str, str] = Field(alias="from")
    to_state: dict[str, str] = Field(alias="to")

    model_config = {"populate_by_name": True}


class AddNoteResult(BaseModel):
    success: bool
    lead_id: int
    note_id: int
    text: str
    created_at: str | None


class ErrorResult(BaseModel):
    success: bool = False
    error: str
    message: str


class DateRangeInput(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    pipeline_id: int | None = None
    manager_ids: list[int] | None = None


class LimitMixin(BaseModel):
    limit: int = Field(default=50, ge=1, le=250)


class CompareManagersInput(BaseModel):
    manager_ids: list[int] = Field(min_length=2)
    date_from: str | None = None
    date_to: str | None = None
    pipeline_id: int | None = None

    @field_validator("manager_ids")
    @classmethod
    def _unique_ids(cls, value: list[int]) -> list[int]:
        if len(set(value)) < 2:
            raise ValueError("manager_ids must contain at least two distinct users")
        return value


class CreateTaskInput(BaseModel):
    entity_id: int
    responsible_user_id: int
    complete_till: str
    text: str = Field(min_length=1, max_length=4000)
    entity_type: Literal["leads", "contacts", "companies", "customers"] = "leads"
    task_type_id: int | None = None

    @model_validator(mode="after")
    def _text_not_blank(self) -> CreateTaskInput:
        if not self.text.strip():
            raise ValueError("text must not be empty")
        return self
