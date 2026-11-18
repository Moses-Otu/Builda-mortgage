"""Identifier generation.

UUIDv7 (RFC 9562) gives time-ordered, index-friendly, non-guessable identifiers.
Python 3.13's stdlib has no ``uuid7``; this is a small, spec-conformant implementation.
"""

from __future__ import annotations

import os
import time
import uuid

_MASK_48 = (1 << 48) - 1
_MASK_12 = (1 << 12) - 1
_MASK_62 = (1 << 62) - 1


def uuid7() -> uuid.UUID:
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 62) & _MASK_12
    rand_b = rand & _MASK_62
    value = (
        ((unix_ms & _MASK_48) << 80)
        | (0x7 << 76)  # version 7
        | (rand_a << 64)
        | (0b10 << 62)  # RFC 4122/9562 variant
        | rand_b
    )
    return uuid.UUID(int=value)
