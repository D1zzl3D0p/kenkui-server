"""Reservations retain their original pack attribution through terminal outcomes."""

import pytest

from kenkui_server.billing.models import AuthorizationStatus, InMemoryBillingRepository


def packs(repo, account="a"):
    return {lot.reference: lot for lot in repo.credit_lots(account)}


def test_grants_first_then_oldest_pack_and_release_restores_each_source():
    repo = InMemoryBillingRepository()
    repo.process_payment_event("stripe", "old", "a", 500)
    repo.process_payment_event("stripe", "new", "a", 1100)
    repo.grant("a", 100, reference="welcome")
    repo.reserve("job", "a", 700)
    lots = packs(repo)
    assert [lots[r].reserved for r in ["welcome", "stripe:old", "stripe:new"]] == [100, 500, 100]
    assert lots["stripe:old"].usage_status == "reserved"
    repo.finalize("job", AuthorizationStatus.RELEASED)
    repo.finalize("job", AuthorizationStatus.RELEASED)
    assert all(lot.reserved == lot.consumed == 0 for lot in repo.credit_lots("a"))
    assert repo.account("a").available_credits == 1700
    assert packs(repo)["stripe:old"].usage_status == "unused"


def test_success_uses_only_allocated_packs_including_bonus_credits():
    repo = InMemoryBillingRepository()
    repo.process_payment_event("stripe", "old", "a", 1100)
    repo.process_payment_event("stripe", "new", "a", 2400)
    repo.reserve("one", "a", 1050)
    repo.reserve("two", "a", 100)
    repo.finalize("two", AuthorizationStatus.SETTLED)
    repo.finalize("one", AuthorizationStatus.RELEASED)
    # Finishing out of order must not move consumption to another pack.
    lots = packs(repo)
    assert (lots["stripe:old"].consumed, lots["stripe:new"].consumed) == (50, 50)
    assert all(lot.usage_status == "used" for lot in lots.values())
    assert sum(lot.available for lot in lots.values()) == 3400
    assert repo.account("a").available_credits == 3400
    repo.finalize("two", AuthorizationStatus.SETTLED)
    repo.finalize("two", AuthorizationStatus.RELEASED)
    assert packs(repo)["stripe:new"].consumed == 50


def test_replay_does_not_create_another_pack_or_authorization_allocation():
    repo = InMemoryBillingRepository()
    for _ in range(2):
        repo.process_payment_event("stripe", "one", "a", 500)
        repo.reserve("job", "a", 20)
    assert len(repo.credit_lots("a")) == 1
    assert repo.credit_lots("a")[0].reserved == 20
    with pytest.raises(ValueError, match="payment_reference_conflict"):
        repo.process_payment_event("stripe", "one", "b", 500)
    with pytest.raises(ValueError, match="payment_reference_conflict"):
        repo.process_payment_event("stripe", "one", "a", 1100)
    with pytest.raises(ValueError, match="authorization_conflict"):
        repo.reserve("job", "b", 20)
    assert repo.credit_lots("b") == ()


def test_invalid_finalization_and_insufficient_balance_do_not_change_lots():
    repo = InMemoryBillingRepository()
    repo.process_payment_event("stripe", "one", "a", 500)
    before = repo.credit_lots("a")
    with pytest.raises(ValueError, match="insufficient_credits"):
        repo.reserve("too-big", "a", 501)
    assert repo.credit_lots("a") == before
    repo.reserve("job", "a", 1)
    with pytest.raises(ValueError, match="invalid_final_authorization_status"):
        repo.finalize("job", AuthorizationStatus.RESERVED)
    assert repo.credit_lots("a")[0].reserved == 1
    assert repo.credit_lots("a")[0].consumed == 0
