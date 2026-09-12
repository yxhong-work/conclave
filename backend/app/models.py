# backend/app/models.py
from datetime import datetime
from pydantic import BaseModel, Field

class CreateGroupRequest(BaseModel):
    question: str = Field(max_length=1000)
    creator_nickname: str = Field(max_length=30)
    expected_count: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, gt=0, le=86400)
    max_rounds: int = Field(default=3, ge=1, le=10)
    # ADAPTIVE_SPEC §7.1: decided at creation, immutable for the group's lifetime.
    adaptive_questions: bool = False

class JoinRequest(BaseModel):
    nickname: str = Field(max_length=30)

class SubmitResponseRequest(BaseModel):
    participant_id: str
    content: str = Field(max_length=4000)

class StartRequest(BaseModel):
    creator_token: str

class LeaveRequest(BaseModel):
    participant_id: str
    creator_token: str | None = None

class CreateGroupResponse(BaseModel):
    pin: str
    participant_id: str
    creator_token: str
    status: str = "collecting"

class JoinResponse(BaseModel):
    participant_id: str
    status: str

class GroupState(BaseModel):
    pin: str
    question: str
    status: str
    expected_count: int | None = None
    participant_count: int
    submitted_count: int
    deadline: str | None = None
    consensus: str | None = None
    is_creator: bool = False
    current_round: int = 1
    max_rounds: int = 3
    cooldown_remaining: int | None = None  # seconds until next-round opens; null unless done & not maxed
    # ADAPTIVE_SPEC §7.1/§7.4: effective flag + count only (no content surface).
    adaptive_questions: bool = False
    member_question_count: int | None = None

# --- Adaptive per-member questioning (ADAPTIVE_SPEC §7.2) ---
class MyQuestionResponse(BaseModel):
    """Strict response model — structurally cannot carry another member's
    question or any stance_digest content (§7.2)."""
    round: int
    question: str
    is_personal: bool

# --- Multi-round (SPEC MULTIROUND_SPEC.md §6) ---
class OpenRoundRequest(BaseModel):
    creator_token: str
    question: str | None = Field(default=None, max_length=1000)
    timeout_seconds: int | None = Field(default=None, gt=0, le=86400)

class CloseRoundRequest(BaseModel):
    creator_token: str

class RoundInfo(BaseModel):
    """Public-safe round history entry. NEVER includes stance_digest or research_brief (§5.6)."""
    round_number: int
    question: str
    consensus: str | None = None
    stance_shift_summary: str | None = None
    created_at: datetime
    analyzed_at: datetime | None = None

