from app.db.repository import (
    create_endpoint,
    create_event,
    create_delivery_attempt,
    count_delivery_attempts,
    get_stale_pending_events,
)
from datetime import datetime, timedelta, timezone
from app.db.model import Event, DeliveryAttempt


def test_count_delivery_attempts_only_counts_its_own_event(db):

    # create one endpoint
    endpoint = create_endpoint(db, "https://example.com/hook", "order.created", "s3cr3t")

    # create 2 events

    event_a = create_event(db, endpoint.id, "order.created", {"n": 1})
    event_b = create_event(db, endpoint.id, "order.created", {"n": 2})

    create_delivery_attempt(db, event_a.id, True, 200, "ok", 1, 42)
    create_delivery_attempt(db, event_a.id, False, 500, "err", 2, 51)
    create_delivery_attempt(db, event_a.id, False, None, None, 3, None)
    create_delivery_attempt(db, event_b.id, True, 200, "ok", 1, 30)

    assert count_delivery_attempts(db, event_a.id) == 3
    assert count_delivery_attempts(db, event_b.id) == 1

# write the make_event function here to create the old events as the create_event creates an event with a created_at = {now} timestamp

def make_event(db, endpoint_id, *, created_at, status="pending"):
    event = Event(endpoint_id=endpoint_id, event_type = "e", payload = {}, status = status, created_at=created_at)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event

def make_attempt(db, event_id, *, attempted_at, attempt_number=1):
    attempt = DeliveryAttempt(event_id=event_id, success=False, response_status_code=500, response_body=None, attempt_number=attempt_number, duration_ms=None, attempted_at=attempted_at)

    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt

# test exclude old event with recent attempt

def test_exclude_old_event_with_recent_attempt(db):
    # arrange
    endpoint = create_endpoint(db, "https://exaample.com/hook", "e", "s")

    now = datetime.now(timezone.utc)

    # old by creation but active one minute ago

    event = make_event(db, endpoint.id, created_at=now - timedelta(hours=2))
    make_attempt(db, event.id, attempted_at=now - timedelta(minutes=1))

    #act 
    result = get_stale_pending_events(db, now - timedelta(minutes=15))

    #assert
    assert event.id not in [e.id for e in result]

def test_returns_old_events_with_no_attempts(db):
    # arrange
    endpoint = create_endpoint(db, "https://example.com/hook", "e", "s")

    # make an event with an older timestamp to test it against the returns_old_events test
    event = make_event(db, endpoint.id, created_at=datetime.now(timezone.utc) - timedelta(hours=2))

    # act
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = get_stale_pending_events(db, cutoff)

    # assert
    assert event.id in [e.id for e in result]


# test for a recently created event here

def test_excludes_recent_event(db):
    # arrange
    endpoint = create_endpoint(db, "https://example.com/hook", "e", "s")

    event = make_event(db, endpoint.id, created_at=datetime.now(timezone.utc) - timedelta(minutes=1)) # a recently created event is here

    # act
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = get_stale_pending_events(db, cutoff)

    # assert
    assert event.id not in [e.id for e in result]

# test for the non pending events

def test_excludes_non_pending_events(db):
    # arrange 
    endpoint = create_endpoint(db, "https://example.com/hook", "e", "s")

    event = make_event(db, endpoint.id, created_at=datetime.now(timezone.utc) - timedelta(hours=2), status="delivered") # an event created 2 hours ago

    # act
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = get_stale_pending_events(db, cutoff)

    # assert

    assert event.id  not in [e.id for e in result]

# check if the event respects the limit

def test_respects_limit(db):
    # arrange
    endpoint = create_endpoint(db, "https://example.com/hook", "e", "s")

    now = datetime.now(timezone.utc)

    oldest = make_event(db, endpoint.id, created_at=now - timedelta(hours=3))

    middle = make_event(db, endpoint.id, created_at=now - timedelta(hours=2))

    newest = make_event(db, endpoint.id, created_at=now - timedelta(hours=1))

    # act
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = get_stale_pending_events(db, cutoff, limit=2)

    #assert

    assert len(result) == 2

    assert [e.id for e in result] == [oldest.id, middle.id]


    



