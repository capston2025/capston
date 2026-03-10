from __future__ import annotations

from enum import Enum


class GoalKind(str, Enum):
    ADD_TO_LIST = "add_to_list"
    REMOVE_FROM_LIST = "remove_from_list"
    CLEAR_LIST = "clear_list"
    FILTER = "filter"
    AUTH = "auth"
    CLOSE_OVERLAY = "close_overlay"
    OPEN_DETAIL = "open_detail"
    VERIFY_STATIC = "verify_static"
    APPLY_SELECTION = "apply_selection"
    SUBMIT_FORM = "submit_form"
    NAVIGATE = "navigate"
    GENERIC_FALLBACK = "generic_fallback"
