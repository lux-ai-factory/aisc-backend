"""The engine's changes in the project's ledger, the platform's record of who changed what.

Each change writes its event to the engine's outbox (models.LedgerOutbox) in the transaction of the
change, so a rolled back change leaves no event and a committed one always has one. The events
are posted, in order and once each, to the platform's internal route
(POST /internal/projects/{pid}/ledger/events) with PLATFORM_LEDGER_ENGINE_TOKEN, by
`manage.py deliver_ledger_events`, which makes a pass over every project database (the
aisc-backend-ledger service). In the Configurator the backend is not given that token (can_send), so
it only queues. A process that does hold it also posts the project's queued events after the commit,
in a thread of their own: a request never waits for the platform, and never fails because of it.
What a pass could not send stays queued for the next one.

The outbox is the engine's own table rather than the project database's `ledger.emit`: the
platform lists the engine among the callers of its internal route (LEDGER_CALLERS), and its relay
reads `ledger.outbox` only for the roles it knows.

An event never names who acted. It cites the request the gateway witnessed (X-AISC-Request-Id),
and the platform takes the person from its record of that request. The worker's events cite the
request that started their run, and the evaluation is the run. A setting's or a secret's value is
never in an event, only its key. Nothing is written while LEDGER_MODE is off (the default), or
outside a project: an engine on its own has no ledger to write to.

The engine's own audit log (aisc_backend.audit) is kept as it is.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib import error, request as urlrequest

from asgiref.sync import async_to_sync, markcoroutinefunction, sync_to_async
from django.db import connections, router, transaction

from aisc_backend import config_secrets, projectdb

logger = logging.getLogger(__name__)

REQUEST_HEADER = "X-AISC-Request-Id"
#: The witnessed request being served, or None outside one.
current_request: ContextVar[str | None] = ContextVar("aisc_ledger_request", default=None)

#: The action that starts a run; the worker's events cite it.
RUN_REQUESTED = "engine.evaluation.run_requested"
#: A plugin's configuration; its secrets are masked before it is sent.
CONFIGURED = "engine.plugin.configured"
#: The platform's answers that refuse the event itself (no project, another event with this id, too
#: big, malformed): it is kept with the status and never sent again. Any other answer is for the
#: operator to fix, so the event waits for the next pass.
REFUSALS = (404, 409, 413, 422)
#: Postgres advisory lock of a delivery pass, per project database ("ldgr" in ASCII).
_PASS_LOCK = 0x6C646772
TIMEOUT = 5.0


def on() -> bool:
    return (os.environ.get("LEDGER_MODE") or "off").strip().lower() in ("record", "enforce")


def request_id_of(value) -> str | None:
    """A presented request id, if it is a UUID."""
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


#: The gateway's client: only a request it sent may name the request it witnessed.
GATEWAY_CLIENT = os.environ.get("AISC_GATEWAY_CLIENT_ID", "aisc-gateway")


def _from_gateway(request) -> bool:
    """Whether the request comes through the gateway: its token verifies and was issued to the gateway.
    A caller reaching the engine directly, with no token or one of its own, could otherwise make an event
    cite any request it names. With sign-in off nothing is verified anywhere, and the id is kept."""
    from aisc_backend.auth import keycloak
    from aisc_backend.project_door import GATEWAY_TOKEN_HEADER

    if not keycloak.AUTH_ENABLED:
        return True
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip() if scheme.lower() == "bearer" else ""
    token = token or request.headers.get(GATEWAY_TOKEN_HEADER, "").strip()
    if not token:
        return False
    try:
        claims = keycloak.verify_token(token)
    except Exception:  # noqa: BLE001 - any token that does not verify cites nothing
        return False
    return (claims or {}).get("azp") == GATEWAY_CLIENT


class RequestId:
    """Async middleware: the witnessed request id, for the events of the request being served, kept only
    on a request the gateway sent (_from_gateway)."""

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        presented = request_id_of(request.headers.get(REQUEST_HEADER))
        if presented and not await sync_to_async(_from_gateway)(request):
            presented = None
        token = current_request.set(presented)
        try:
            return await self.get_response(request)
        finally:
            current_request.reset(token)


def _database() -> str:
    from aisc_backend.models import LedgerOutbox

    return router.db_for_write(LedgerOutbox)


def emit(action: str, *, item_type: str, item_id, details: dict | None = None, using: str | None = None,
         request_id: str | None = None, run_id=None, **states) -> int | None:
    """Queue one event on `using` (the admitted project's database), inside the caller's transaction.

    `states` takes content, before and after. Returns the outbox row's id, or None while the ledger is
    off or no project is admitted."""
    from aisc_backend.models import LedgerOutbox

    pid = projectdb.admitted_pid.get()
    if not on() or pid is None:
        return None
    unknown = set(states) - {"content", "before", "after"}
    if unknown:
        raise TypeError(f"not an event field: {', '.join(sorted(unknown))}")
    using = using or _database()
    event = {"event_id": str(uuid.uuid4()), "request_id": request_id or current_request.get(), "action": action,
             "item_type": item_type, "item_id": None if item_id is None else str(item_id),
             "details": details or {}}
    if run_id is not None:
        event["run_id"] = str(run_id)
    event.update({key: value for key, value in states.items() if value is not None})
    row = LedgerOutbox.objects.using(using).create(project_pid=pid, event=event)
    if can_send():
        transaction.on_commit(lambda: deliver_soon(using), using=using)
    return row.id


def emit_for_run(action: str, evaluation_pid, *, item_type: str, item_id, details: dict | None = None,
                 using: str | None = None) -> int | None:
    """Queue a worker's event: it cites the request and the run of its evaluation's start. None when the
    start was not recorded (the ledger was off then), since the platform would have no run to bind it to."""
    from aisc_backend.models import LedgerOutbox

    if not on() or projectdb.admitted_pid.get() is None:
        return None
    using = using or _database()
    start = (LedgerOutbox.objects.using(using)
             .filter(event__action=RUN_REQUESTED, event__item_id=str(evaluation_pid)).order_by("id").first())
    if start is None or not start.event.get("request_id"):
        logger.info("ledger: %s of evaluation %s not recorded: its run's start is not", action, evaluation_pid)
        return None
    return emit(action, item_type=item_type, item_id=item_id, details=details, using=using,
                request_id=start.event["request_id"], run_id=start.event.get("run_id"))


async def atomically(work):
    """Await the coroutine function `work` in one transaction on the admitted database, and return its answer.

    The engine's routes are async and Django's transactions are not: the work runs from a thread that holds
    the transaction, and each ORM call it awaits runs back in that same thread, so every write of the
    work, and the events it queues, commit or roll back together."""
    def run():
        with transaction.atomic(using=_database()):
            return async_to_sync(work)()
    return await sync_to_async(run)()


def deliver(using: str, send, limit: int = 100) -> tuple[int, int]:
    """Post the queued events of one project database, oldest first, and return (sent, not sent).

    `send(pid, event)` returns the platform's HTTP status, or None when it cannot be asked. A 2xx marks
    the event delivered; a status in REFUSALS keeps it with that status; anything else ends the pass, so
    the order holds. One pass at a time per database: in this process, and on Postgres across processes."""
    lock = _process_lock(using)
    if not lock.acquire(blocking=False):
        return 0, 0
    try:
        connection = connections[using]
        if connection.vendor != "postgresql":
            return _deliver(using, send, limit)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_PASS_LOCK])
            if not cursor.fetchone()[0]:
                return 0, 0
        try:
            return _deliver(using, send, limit)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_PASS_LOCK])
    finally:
        lock.release()


_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _process_lock(using: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(using, threading.Lock())


def _deliver(using: str, send, limit: int) -> tuple[int, int]:
    from aisc_backend.models import LedgerOutbox

    sent = failed = 0
    queued = LedgerOutbox.objects.using(using).filter(delivered_at__isnull=True, refused__isnull=True)
    for row in list(queued.order_by("id")[:limit]):
        status = send(str(row.project_pid), _as_sent(row.event))
        if status is not None and 200 <= status < 300:
            LedgerOutbox.objects.using(using).filter(pk=row.pk).update(delivered_at=datetime.now(timezone.utc))
            sent += 1
            continue
        failed += 1
        if status in REFUSALS:
            logger.error("ledger: the platform refused engine event %s (%s, status %s); kept, not sent again",
                         row.event.get("event_id"), row.event.get("action"), status)
            LedgerOutbox.objects.using(using).filter(pk=row.pk).update(refused=status)
            continue
        logger.warning("ledger: engine event %s not sent (%s); it and the later ones stay queued",
                       row.event.get("event_id"), "no answer" if status is None else f"status {status}")
        break
    return sent, failed


def _as_sent(event: dict) -> dict:
    """The event as the platform receives it. A configuration event queued before its secrets were masked at
    save is masked here, so no plaintext secret leaves the engine; the stored row is left to the cleanup
    (`manage.py clean_old_secrets`)."""
    if not isinstance(event, dict):
        return event
    content = event.get("content")
    if event.get("action") != CONFIGURED or not isinstance(content, dict) or "config" not in content:
        return event
    return {**event, "content": {**content, "config": config_secrets.masked_for_ledger(content["config"])}}


def can_send() -> bool:
    """Whether this process may post to the platform. The backend imports plugin code, so it is not given
    the engine's ledger token: its events wait in the outbox for aisc-backend-ledger, which is."""
    return bool(os.environ.get("PLATFORM_URL")) and bool(os.environ.get("PLATFORM_LEDGER_ENGINE_TOKEN"))


def deliver_soon(using: str) -> threading.Thread:
    """One pass for this database in a thread of its own, closing that thread's connection after."""
    def run():
        try:
            deliver(using, http_sender())
        except Exception as exc:  # noqa: BLE001 - the next pass sends what this one could not
            logger.warning("ledger: a delivery pass on %s stopped: %s", using, type(exc).__name__)
        finally:
            if using in connections.settings:
                connections[using].close()
    worker = threading.Thread(target=run, name="aisc-ledger-delivery", daemon=True)
    worker.start()
    return worker


class _Reply:
    def __init__(self, status: int, body: bytes):
        self.status, self.body = status, body


def _post(url: str, body: bytes, headers: dict) -> _Reply:
    req = urlrequest.Request(url, data=body, method="POST", headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as res:
            return _Reply(res.status, res.read())
    except error.HTTPError as exc:
        return _Reply(exc.code, exc.read())


def _no_project(reply) -> bool:
    """Whether a 404 is the platform's own "no project" answer, not a wrong address."""
    try:
        detail = json.loads(reply.body or b"{}").get("detail")
    except (ValueError, AttributeError):
        return False
    return isinstance(detail, str) and detail.startswith("no project ")


def http_sender(post=None):
    """A `send` for `deliver` that posts to the platform's internal ledger route.

    It returns None (the event stays queued) when PLATFORM_URL or PLATFORM_LEDGER_ENGINE_TOKEN is not
    set, when the platform cannot be reached, and on a 404 that is not the platform's "no project"."""
    post = post or _post

    def send(pid: str, event: dict) -> int | None:
        base = (os.environ.get("PLATFORM_URL") or "").rstrip("/")
        token = os.environ.get("PLATFORM_LEDGER_ENGINE_TOKEN") or ""
        if not base or not token:
            logger.warning("ledger: PLATFORM_URL or PLATFORM_LEDGER_ENGINE_TOKEN is not set; engine events stay queued")
            return None
        try:
            reply = post(f"{base}/internal/projects/{pid}/ledger/events", json.dumps(event).encode("utf-8"),
                         {"Content-Type": "application/json", "X-AISC-Service-Token": token})
        except Exception as exc:  # noqa: BLE001 - the platform is down, or the URL is wrong
            logger.warning("ledger: the platform can't be reached (%s); engine events stay queued",
                           type(exc).__name__)
            return None
        if reply.status == 404 and not _no_project(reply):
            logger.warning("ledger: %s answered 404 without naming the project; check PLATFORM_URL", base)
            return None
        return reply.status
    return send
