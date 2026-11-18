"""Manager services and dependencies exposed to other application modules."""

from __future__ import annotations

from app.modules.manager import ai as ai
from app.modules.manager import history as history
from app.modules.manager.dependencies import DB as DB
from app.modules.manager.dependencies import Manager as Manager
from app.modules.manager.schemas import Domain as Domain
from app.modules.manager.schemas import ReportFilters as ReportFilters
