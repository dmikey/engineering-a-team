import os
from typing import Tuple


def should_auto_dispatch(confidence: str, auto_dispatch_enabled: bool, dispatch_count: int, auto_dispatch_max: int) -> Tuple[bool, str]:
    """Return whether the task-assignment workflow should auto-dispatch an agent run."""
    if not auto_dispatch_enabled:
        return False, "auto-dispatch disabled"

    if dispatch_count >= auto_dispatch_max:
        return False, f"dispatch limit reached ({dispatch_count}/{auto_dispatch_max})"

    if str(confidence).upper() not in {"HIGH", "VERY_HIGH"}:
        return False, f"confidence too low: {confidence}"

    return True, "allowed"
