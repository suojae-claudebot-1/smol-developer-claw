#!/usr/bin/env python3
"""Autonomous AI Server — entry point."""

from src.adapters.web.server import app
from src.config import CONFIG
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=CONFIG["port"], log_level="info")
