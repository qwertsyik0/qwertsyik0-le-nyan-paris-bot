from __future__ import annotations

from .main_base import app
from .admin_ext import router as admin_ext_router

app.include_router(admin_ext_router)
