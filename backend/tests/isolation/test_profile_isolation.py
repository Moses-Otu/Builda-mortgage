"""C1 remediation: no firm can see personal data that another firm recorded about a person,
and no firm can grant itself (or anyone) membership without the person's acceptance.

Scenario throughout: Firm A records contact details for a person; Firm B, which knows the
person's identity-provider subject, tries to learn them.
"""

from __future__ import annotations

import uuid

import asyncpg

from tests.conftest import TEST_DB, Firm, MakeFirm, db_url

A_EMAIL = "a-only.secret@firm-a.test"
A_NAME = "Firm-A Recorded Name"


async def _person_known_to_firm_a(firm_a: Firm) -> str:
    subject = f"person-{uuid.uuid4()}"
    resp = await firm_a.request(
        "POST",
        "/api/v1/members",
        json={
            "idp_subject": subject,
            "role": "adviser",
            "email": A_EMAIL,
            "display_name": A_NAME,
        },
    )
    assert resp.status_code == 201, resp.text
    firm_a.members[subject] = uuid.UUID(resp.json()["membership_id"])
    assert (await firm_a.accept(subject, firm_a.members[subject])).status_code == 200
    return subject


async def _members_by_id(firm: Firm) -> dict[str, dict[str, object]]:
    listing = (await firm.request("GET", "/api/v1/members")).json()
    return {m["membership_id"]: m for m in listing}


def _assert_no_firm_a_data(text: str) -> None:
    assert A_EMAIL not in text
    assert A_NAME not in text


async def test_adding_existing_identity_reveals_nothing(make_firm: MakeFirm) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    subject = await _person_known_to_firm_a(firm_a)

    resp = await firm_b.request(
        "POST", "/api/v1/members", json={"idp_subject": subject, "role": "adviser"}
    )
    assert resp.status_code == 201
    _assert_no_firm_a_data(resp.text)
    body = resp.json()
    assert body["email"] is None
    assert body["display_name"] is None
    assert body["status"] == "invited"

    listing = await firm_b.request("GET", "/api/v1/members")
    _assert_no_firm_a_data(listing.text)


async def test_existing_and_new_identities_are_indistinguishable(make_firm: MakeFirm) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    known = await _person_known_to_firm_a(firm_a)
    unknown = f"person-{uuid.uuid4()}"

    responses = []
    for subject in (known, unknown):
        resp = await firm_b.request(
            "POST",
            "/api/v1/members",
            json={"idp_subject": subject, "role": "adviser", "email": "b@firm-b.test"},
        )
        assert resp.status_code == 201
        responses.append(resp.json())

    def shape(body: dict[str, object]) -> dict[str, object]:
        return {
            k: v for k, v in body.items() if k not in {"membership_id", "user_id", "created_at"}
        }

    assert shape(responses[0]) == shape(responses[1])


async def test_each_firm_sees_only_its_own_record_of_a_shared_person(
    make_firm: MakeFirm,
) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    subject = await _person_known_to_firm_a(firm_a)
    resp = await firm_b.request(
        "POST",
        "/api/v1/members",
        json={
            "idp_subject": subject,
            "role": "read_only",
            "email": "b-view@firm-b.test",
            "display_name": "Firm-B Name",
        },
    )
    assert resp.status_code == 201
    firm_b.members[subject] = uuid.UUID(resp.json()["membership_id"])

    a_view = {
        m["membership_id"]: m for m in (await firm_a.request("GET", "/api/v1/members")).json()
    }
    b_view = {
        m["membership_id"]: m for m in (await firm_b.request("GET", "/api/v1/members")).json()
    }
    a_record = a_view[str(firm_a.members[subject])]
    b_record = b_view[str(firm_b.members[subject])]
    assert (a_record["email"], a_record["display_name"]) == (A_EMAIL, A_NAME)
    assert (b_record["email"], b_record["display_name"]) == ("b-view@firm-b.test", "Firm-B Name")
    _assert_no_firm_a_data(str(b_view))
    assert "b-view@firm-b.test" not in str(a_view)


async def test_invitation_grants_no_access_without_consent(make_firm: MakeFirm) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    subject = await _person_known_to_firm_a(firm_a)
    resp = await firm_b.request(
        "POST", "/api/v1/members", json={"idp_subject": subject, "role": "firm_admin"}
    )
    invitation_id = resp.json()["membership_id"]

    # Not usable until the person accepts, even as firm_admin.
    assert (await firm_b.request("GET", "/api/v1/cases", as_=subject)).status_code == 403
    me = (await firm_b.client.get("/api/v1/me", headers=firm_b.bearer(subject))).json()
    assert [m["tenant_id"] for m in me["memberships"]] == [str(firm_a.tenant_id)]
    assert [i["membership_id"] for i in me["invitations"]] == [invitation_id]

    # The inviting firm cannot force activation.
    forced = await firm_b.request(
        "PATCH", f"/api/v1/members/{invitation_id}", json={"version": 1, "status": "active"}
    )
    assert forced.status_code == 422
    assert (await firm_b.request("GET", "/api/v1/cases", as_=subject)).status_code == 403


async def test_only_the_invited_identity_can_accept(make_firm: MakeFirm) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    subject = await _person_known_to_firm_a(firm_a)
    resp = await firm_b.request(
        "POST", "/api/v1/members", json={"idp_subject": subject, "role": "adviser"}
    )
    invitation_id = resp.json()["membership_id"]

    # Firm B's own admin cannot accept on the person's behalf...
    assert (await firm_b.accept(firm_b.admin_subject, invitation_id)).status_code == 404
    # ...nor can an unrelated identity.
    assert (await firm_b.accept(f"stranger-{uuid.uuid4()}", invitation_id)).status_code == 404
    # The person can.
    assert (await firm_b.accept(subject, invitation_id)).status_code == 200


async def test_me_shows_only_the_callers_own_token_claims(make_firm: MakeFirm) -> None:
    firm_a = await make_firm("Firm A")
    subject = await _person_known_to_firm_a(firm_a)
    me = await firm_a.client.get("/api/v1/me", headers=firm_a.bearer(subject))
    assert me.status_code == 200
    # The test token carries no email/name claims; nothing recorded by a firm is substituted.
    assert me.json()["email"] is None
    assert me.json()["display_name"] is None
    _assert_no_firm_a_data(me.text)


async def test_database_confines_contact_details_to_the_recording_firm(
    make_firm: MakeFirm,
) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    subject = await _person_known_to_firm_a(firm_a)
    await firm_b.add_member("adviser", subject=subject)

    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm_b.tenant_id))
        rows = await conn.fetch("SELECT email, display_name FROM memberships")
        assert A_EMAIL not in str(rows)
        assert A_NAME not in str(rows)
        # And the definer function hands back nothing but an identifier.
        returned = await conn.fetchval(
            "SELECT app_register_user(gen_random_uuid(), $1, $2)",
            "https://meip-test.localhost/",
            subject,
        )
        assert isinstance(returned, uuid.UUID)
    finally:
        await conn.close()
