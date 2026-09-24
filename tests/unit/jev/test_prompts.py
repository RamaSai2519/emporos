from __future__ import annotations

import hashlib

import pytest

from emporos.jev.prompts import DEFAULT_PROMPT, JevPrompt


def test_the_hash_is_the_sha256_of_the_text() -> None:
    prompt = JevPrompt(version="v1", system_text="hello")

    assert prompt.content_hash == hashlib.sha256(b"hello").hexdigest()


def test_the_hash_is_stable_across_instances() -> None:
    assert JevPrompt("v1", "same").content_hash == JevPrompt("v2", "same").content_hash


def test_the_hash_changes_with_the_text() -> None:
    assert JevPrompt("v1", "one").content_hash != JevPrompt("v1", "two").content_hash


def test_a_prompt_needs_a_version_and_text() -> None:
    with pytest.raises(ValueError, match="version"):
        JevPrompt(version=" ", system_text="x")
    with pytest.raises(ValueError, match="text"):
        JevPrompt(version="v1", system_text=" ")


def test_the_default_prompt_is_versioned() -> None:
    assert DEFAULT_PROMPT.version == "v1"
    assert len(DEFAULT_PROMPT.content_hash) == 64
