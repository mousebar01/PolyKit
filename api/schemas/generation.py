from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    status: Literal["pending", "running", "done", "error", "cancelled", "interrupted"]
    progress: int = 0              # 0–100
    step: Optional[str] = None    # Human-readable current step
    output_url: Optional[str] = None
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
