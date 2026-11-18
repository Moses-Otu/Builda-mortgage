from __future__ import annotations

from tests.conftest import Firm, MakeFirm


async def test_list_members(firm: Firm) -> None:
    adviser = await firm.add_member("adviser")
    resp = await firm.request("GET", "/api/v1/members", as_=adviser)
    assert resp.status_code == 200
    roles = sorted(m["role"] for m in resp.json())
    assert roles == ["adviser", "firm_admin"]


async def test_duplicate_member_is_409(firm: Firm) -> None:
    subject = await firm.add_member("adviser")
    resp = await firm.request(
        "POST", "/api/v1/members", json={"idp_subject": subject, "role": "read_only"}
    )
    assert resp.status_code == 409


async def test_only_firm_admin_manages_members(firm: Firm) -> None:
    adviser = await firm.add_member("adviser")
    resp = await firm.request(
        "POST", "/api/v1/members", as_=adviser, json={"idp_subject": "x", "role": "adviser"}
    )
    assert resp.status_code == 403


async def test_change_role(firm: Firm) -> None:
    subject = await firm.add_member("read_only")
    mid = firm.members[subject]
    resp = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=2, json={"role": "adviser"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "adviser"
    assert resp.json()["version"] == 3
    created = await firm.request("POST", "/api/v1/cases", as_=subject, json={"purpose": "purchase"})
    assert created.status_code == 201


async def test_deactivated_member_loses_access(firm: Firm) -> None:
    subject = await firm.add_member("adviser")
    mid = firm.members[subject]
    resp = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=2, json={"status": "deactivated"}
    )
    assert resp.status_code == 200
    assert (await firm.request("GET", "/api/v1/cases", as_=subject)).status_code == 403


async def test_last_admin_cannot_be_removed(firm: Firm) -> None:
    mid = firm.admin_membership_id
    demote = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=1, json={"role": "adviser"}
    )
    assert demote.status_code == 422
    deactivate = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=1, json={"status": "deactivated"}
    )
    assert deactivate.status_code == 422


async def test_admin_can_step_down_when_another_admin_exists(firm: Firm) -> None:
    await firm.add_member("firm_admin")
    resp = await firm.request(
        "PATCH",
        f"/api/v1/members/{firm.admin_membership_id}",
        if_match=1,
        json={"role": "adviser"},
    )
    assert resp.status_code == 200


async def test_same_identity_in_two_firms_keeps_separate_roles(
    firm: Firm, make_firm: MakeFirm
) -> None:
    other = await make_firm()
    subject = await firm.add_member("read_only")
    await other.add_member("adviser", subject=subject)

    in_first = await firm.request(
        "POST", "/api/v1/cases", as_=subject, json={"purpose": "purchase"}
    )
    in_other = await other.request(
        "POST", "/api/v1/cases", as_=subject, json={"purpose": "purchase"}
    )
    assert in_first.status_code == 403
    assert in_other.status_code == 201


async def test_invited_member_has_no_access_until_accepted(firm: Firm) -> None:
    subject = await firm.add_member("adviser", accept=False)
    listed = {m["membership_id"]: m for m in (await firm.request("GET", "/api/v1/members")).json()}
    member = listed[str(firm.members[subject])]
    assert member["status"] == "invited"
    assert member["accepted_at"] is None
    assert (await firm.request("GET", "/api/v1/cases", as_=subject)).status_code == 403

    accepted = await firm.accept(subject, firm.members[subject])
    assert accepted.status_code == 200
    assert accepted.json()["tenant_id"] == str(firm.tenant_id)
    assert (await firm.request("GET", "/api/v1/cases", as_=subject)).status_code == 200


async def test_admin_cannot_activate_an_unaccepted_invitation(firm: Firm) -> None:
    subject = await firm.add_member("adviser", accept=False)
    resp = await firm.request(
        "PATCH",
        f"/api/v1/members/{firm.members[subject]}",
        if_match=1,
        json={"status": "active"},
    )
    assert resp.status_code == 422
    assert (await firm.request("GET", "/api/v1/cases", as_=subject)).status_code == 403


async def test_revoked_invitation_cannot_be_accepted_or_reactivated(firm: Firm) -> None:
    subject = await firm.add_member("adviser", accept=False)
    mid = firm.members[subject]
    revoked = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=1, json={"status": "deactivated"}
    )
    assert revoked.status_code == 200
    assert (await firm.accept(subject, mid)).status_code == 404
    reactivate = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=2, json={"status": "active"}
    )
    assert reactivate.status_code == 422


async def test_previously_accepted_member_can_be_reactivated(firm: Firm) -> None:
    subject = await firm.add_member("adviser")
    mid = firm.members[subject]
    off = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=2, json={"status": "deactivated"}
    )
    assert off.status_code == 200, off.text
    on = await firm.request(
        "PATCH", f"/api/v1/members/{mid}", if_match=3, json={"status": "active"}
    )
    assert on.status_code == 200, on.text
    assert (await firm.request("GET", "/api/v1/cases", as_=subject)).status_code == 200


async def test_invitation_cannot_be_accepted_twice(firm: Firm) -> None:
    subject = await firm.add_member("adviser")
    assert (await firm.accept(subject, firm.members[subject])).status_code == 404
