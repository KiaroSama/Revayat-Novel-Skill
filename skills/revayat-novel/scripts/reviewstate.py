"""Typed review evidence and explicit transitions shared by record and delivery."""

from __future__ import annotations

import json
from functools import wraps
from pathlib import Path


class Refused(ValueError):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason, self.detail = reason, detail

    def report(self):
        return {"ok": False, "refused": self.reason, "detail": self.detail}


def guarded(function):
    """Expected persisted-input failures have a result, not a traceback."""
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Refused as error:
            return error.report()
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            return {"ok": False, "refused": "unreadable-state", "detail": str(error)}
        except ValueError as error:
            return {"ok": False, "refused": getattr(error, "reason", "invalid-state"), "detail": str(error)}
    return call


def cli(function):
    @wraps(function)
    def call(*args, **kwargs):
        import runlog

        def invoke():
            try:
                return function(*args, **kwargs)
            except (OSError, UnicodeError, ValueError) as error:
                print(json.dumps({"ok": False, "refused": getattr(error, "reason", "invalid-input"),
                                  "detail": str(error)}, ensure_ascii=False))
                return 2
        return runlog.execute(invoke)
    return call


def object_file(path, *, optional=False):
    try:
        value = json.loads(Path(path).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        if optional:
            return None
        raise Refused("absent-state", f"{path} is absent") from None
    except (OSError, UnicodeError, ValueError) as error:
        raise Refused("unreadable-state", f"cannot read {path}: {error}") from None
    if not isinstance(value, dict):
        raise Refused("invalid-state", f"{path} must contain a JSON object")
    return value


def strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def require(condition, detail):
    if not condition:
        raise Refused("invalid-state", detail)


def episodes_valid(episodes):
    require(isinstance(episodes, dict), "episodes must be an object")
    for key, episode in episodes.items():
        require(isinstance(key, str) and isinstance(episode, dict), "invalid repair episode")
        attempts = episode.get("attempts")
        require(type(attempts) is int and attempts >= 0, "invalid repair attempt count")
        require(episode.get("state") in ("open", "closed"), "invalid repair state")
        require(isinstance(episode.get("source"), str), "invalid repair source identity")
        for field in ("wordings", "arguments", "revisions"):
            require(strings(episode.get(field)) and len(episode[field]) == attempts,
                    f"invalid episode {field}")
        require(strings(episode.get("rejected")), "invalid rejected wording history")
        require(isinstance(episode.get("superseded"), list), "invalid archived episodes")


def read_review(path, *, stage, rubrics, optional=False, history_only=False, snapshot=None):
    found = object_file(path, optional=optional) if snapshot is None else json.loads(snapshot)
    if found is None:
        require(snapshot is None, "review must be an object")
        return {}
    require(isinstance(found, dict), "review must be an object")
    schema = found.get("schema")
    current, legacy = f"revayat-novel/{stage}@2", f"revayat-novel/{stage}@1"
    if schema not in (current, legacy):
        raise Refused("unsupported-version", f"unsupported {stage} review schema {schema!r}")
    require(type(found.get("ok")) is bool, "review requires an explicit boolean ok")
    require(isinstance(found.get("revision"), str), "review requires a revision")
    require(type(found.get("round")) is int and found["round"] >= 1, "invalid review round")
    require(isinstance(found.get("history"), list) and all(strings(x) for x in found["history"]),
            "invalid review history")
    require(strings(found.get("resolved")), "invalid resolved issue list")
    episodes_valid(found.get("episodes"))
    field, payload = ("findings", "detail") if stage == "meaning" else ("edits", "target")
    require(isinstance(found.get(field), list), f"review requires {field}")
    records = found[field]
    for item in records:
        require(isinstance(item, dict) and all(isinstance(item.get(k), str)
                for k in ("id", "rubric", payload)), f"invalid {stage} record")
        require(item["rubric"] in rubrics and bool(item[payload].strip()), "invalid review rubric or payload")
    if stage == "meaning":
        expected = sorted(f"{item['id']}/{item['rubric']}" for item in records
                          if rubrics[item["rubric"]]["kind"] == "meaning")
        require(strings(found.get("blocking")) and found["blocking"] == expected,
                "blocking findings disagree with the recorded arguments")
        require(found["ok"] is (not expected), "meaning status disagrees with findings")
    else:
        require("applied" in found and (found["applied"] is None or isinstance(found["applied"], str)),
                "invalid applied revision")
    if schema == legacy:
        if history_only:
            return found  # Carry validated history into a freshly proved review, never approve it.
        raise Refused("unverified-review", "legacy review has no explicit state; regenerate and record a fresh review")
    require(isinstance(found.get("event"), str) and found["event"].startswith("review-event1:"),
            "missing review event identity")
    require(isinstance(found.get("book"), str), "review requires its book identity")
    if stage == "meaning":
        require(found.get("status") == ("approved" if found["ok"] else "rejected"),
                "meaning approval has no consistent explicit status")
    else:
        status = found.get("status")
        require(status in ("proposed", "applied", "approved-no-change", "refused"), "missing or invalid fluency status")
        require(found["ok"] is (status != "refused"), "fluency status disagrees with ok")
        if status != "refused":
            import repairlog
            require("refused_edits" not in found and "refused" not in found
                    and not repairlog.blocked(found["episodes"]), "refused or exhausted evidence cannot approve delivery")
        if status == "refused":
            require(not records and isinstance(found.get("refused"), str)
                    and isinstance(found.get("refused_edits"), list) and bool(found["refused_edits"]),
                    "refusal requires its proposal evidence")
        elif status == "proposed":
            require(bool(records) and found["applied"] is None, "invalid proposed state")
        elif status == "approved-no-change":
            require(not records and found["applied"] is None, "invalid no-change approval")
        else:
            require(bool(records) and bool(found["applied"]) and isinstance(found.get("changes"), list),
                    "applied review requires changes and a revision")
        require(isinstance(found.get("meaning_revision"), str) and isinstance(found.get("meaning_dir"), str),
                "fluency review requires its meaning prerequisites")
    return found


def fluency_decision(found, *, operation):
    """One transition decision for apply, status and signoff's verdict."""
    status = found["status"]
    if status == "refused":
        raise Refused(found["refused"], found.get("detail", "this fluency pass was refused"))
    if operation == "apply" and status == "applied":
        raise Refused("already-applied", "these edits were already applied")
    if operation == "verdict" and status == "proposed":
        raise Refused("edits-unapplied", "the proposed edits have not been applied")
    return status
