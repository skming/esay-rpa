"""供 Playwright 注入的共享 picker overlay 表达式。"""
from __future__ import annotations

from pathlib import Path

from app.services.js_expression_loader import load_factory_callback_entry_expression

_SOURCE_PATH = Path(__file__).with_name("picker_overlay.js")

PICKER_OVERLAY_JS = load_factory_callback_entry_expression(
    _SOURCE_PATH,
    entry_import="import { createDomLocator } from './dom_locator.js';",
    entry_marker="export const startPicker = ",
    callback_name="__rpaPickerEvent__",
)
