from __future__ import annotations

import pytest

from meip.cases import lifecycle
from meip.cases.models import STATUS_CHECK, STORABLE_STATUSES, CaseStatus
from meip.core.errors import ForbiddenError
from meip.tenancy.models import Role
from meip.tenancy.permissions import ROLE_PERMISSIONS, Permission, has_permission, require


class TestLifecycle:
    def test_every_status_has_transition_entry(self) -> None:
        assert set(lifecycle.TRANSITIONS) == set(CaseStatus)

    def test_closed_can_only_reopen_to_evidence_collection(self) -> None:
        assert lifecycle.TRANSITIONS[CaseStatus.CLOSED] == {CaseStatus.COLLECTING_EVIDENCE}
        assert lifecycle.is_reopening(CaseStatus.CLOSED, CaseStatus.COLLECTING_EVIDENCE)

    @pytest.mark.parametrize("status", [s for s in CaseStatus if s is not CaseStatus.CLOSED])
    def test_every_open_status_can_close(self, status: CaseStatus) -> None:
        assert lifecycle.is_defined(status, CaseStatus.CLOSED)

    def test_unavailable_transitions_are_all_defined(self) -> None:
        for current, target in lifecycle.NOT_YET_AVAILABLE:
            assert lifecycle.is_defined(current, target)

    def test_verified_only_reachable_from_review_and_not_yet(self) -> None:
        sources = {
            s for s, targets in lifecycle.TRANSITIONS.items() if CaseStatus.VERIFIED in targets
        }
        assert sources == {CaseStatus.IN_REVIEW}
        assert lifecycle.unavailable_reason(CaseStatus.IN_REVIEW, CaseStatus.VERIFIED)

    def test_verified_is_not_storable(self) -> None:
        assert CaseStatus.VERIFIED not in STORABLE_STATUSES
        assert "verified" not in STATUS_CHECK

    def test_leaving_verified_is_a_reopen(self) -> None:
        for target in (CaseStatus.COLLECTING_EVIDENCE, CaseStatus.IN_REVIEW):
            assert lifecycle.is_reopening(CaseStatus.VERIFIED, target)

    def test_verified_and_closed_are_not_editable(self) -> None:
        assert CaseStatus.VERIFIED not in lifecycle.EDITABLE_STATUSES
        assert CaseStatus.CLOSED not in lifecycle.EDITABLE_STATUSES


class TestPermissions:
    def test_every_role_has_a_policy(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_firm_admin_has_everything(self) -> None:
        assert ROLE_PERMISSIONS[Role.FIRM_ADMIN] == frozenset(Permission)

    def test_only_firm_admin_manages_members(self) -> None:
        holders = {r for r in Role if has_permission(r, Permission.MEMBER_MANAGE)}
        assert holders == {Role.FIRM_ADMIN}

    def test_read_only_cannot_write(self) -> None:
        writes = {
            Permission.CASE_CREATE,
            Permission.CASE_UPDATE,
            Permission.CASE_TRANSITION,
            Permission.CASE_CLOSE,
            Permission.APPLICANT_WRITE,
            Permission.MEMBER_MANAGE,
        }
        assert not writes & ROLE_PERMISSIONS[Role.READ_ONLY]

    def test_case_administrator_cannot_close_or_assign(self) -> None:
        perms = ROLE_PERMISSIONS[Role.CASE_ADMINISTRATOR]
        assert Permission.CASE_CLOSE not in perms
        assert Permission.CASE_ASSIGN_ADVISER not in perms
        assert Permission.CASE_CREATE in perms

    def test_require_raises(self) -> None:
        with pytest.raises(ForbiddenError):
            require(Role.READ_ONLY, Permission.CASE_CREATE)
        require(Role.ADVISER, Permission.CASE_CREATE)
