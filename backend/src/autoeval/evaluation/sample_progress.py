"""Sample counts for terminal execution details and historical report projections."""


def with_terminal_progress(summary, selected_count, observation=None):
    if selected_count <= 0 or (summary or {}).get("progress"):
        return summary
    observed = (summary or {}).get("observed_cases")
    if type(observed) is int and 0 <= observed <= selected_count:
        progress = {
            "source": "archived_case_executions",
            "snapshot_kind": "final",
            "finish_count": observed,
            "total_count": selected_count,
        }
    elif isinstance(observation, dict):
        done = observation.get("finish_count")
        if type(done) is not int or not 0 <= done <= selected_count:
            return summary
        progress = {**observation, "total_count": selected_count}
        if progress.get("snapshot_kind") != "final":
            progress.update(snapshot_kind="last_observed", stale=True)
    else:
        return summary
    return {**(summary or {}), "progress": progress}


def report_summary(summary):
    """Keep monitor metadata out of the immutable scoring report."""
    if not summary or "progress" not in summary:
        return summary
    return {k: v for k, v in summary.items() if k != "progress"} or None
