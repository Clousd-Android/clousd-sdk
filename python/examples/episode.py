"""One evaluation episode on a CLOUSD phone: reset to a saved state, give the agent the screen, apply its actions,
check the result. Replace `my_agent` with your model call.

    CLOUSD_API_KEY=cl_live_... python episode.py dev_04f2
"""
import sys

from clousd import Clousd


def my_agent(screenshot_jpeg: bytes, texts: list) -> dict:
    """Your policy: look at the screen and return one action. This stub just opens Chrome."""
    return {"op": "open_app", "package": "com.android.chrome"}


def run(device_name: str, goal_text: str = "Chrome", steps: int = 10) -> bool:
    c = Clousd()
    phone = c.device(device_name)

    # 1. reset: the newest restorable snapshot is the clean starting state
    snaps = [s for s in phone.snapshots() if s.get("restorable")]
    if snaps:
        phone.restore(snaps[0]["id"])

    for _ in range(steps):
        # 2. look
        shot, texts = phone.screenshot(width=540), phone.screen_text()
        # 3. check: done when the goal text is on the screen
        if any(goal_text.lower() in t.lower() for t in texts):
            return True
        # 4. act
        act = my_agent(shot, texts)
        phone.action(act.pop("op"), **act)
    return False


if __name__ == "__main__":
    print("success" if run(sys.argv[1]) else "failed")
