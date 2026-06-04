"""Tests for LLM credential loader and Anthropic client factory."""
import os
import tempfile
from pathlib import Path

import pytest

from horizon.internal.llm.client import (
    LLMCredentials,
    load_llm_credentials,
    build_anthropic_client,
    LLMCredentialsError,
)


SAMPLE_KEY_FILE = '''
"ANTHROPIC_AUTH_TOKEN": "sk-test-token-abc123",
"ANTHROPIC_BASE_URL": "https://api.example.com/anthropic",
"ANTHROPIC_DEFAULT_SONNET_MODEL": "test-model-m3",
'''


def test_load_llm_credentials_from_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
    creds = load_llm_credentials(key_file=f)
    assert creds.auth_token == "sk-test-token-abc123"
    assert creds.base_url == "https://api.example.com/anthropic"
    assert creds.model == "test-model-m3"


def test_env_var_overrides_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-override-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env-override.example.com")
    creds = load_llm_credentials(key_file=f)
    assert creds.auth_token == "env-override-token"
    assert creds.base_url == "https://env-override.example.com"


def test_missing_credentials_raises(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text("# empty")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with pytest.raises(LLMCredentialsError):
        load_llm_credentials(key_file=f)


def test_build_anthropic_client_returns_async_client(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.txt"
    f.write_text(SAMPLE_KEY_FILE)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = build_anthropic_client(key_file=f)
    from anthropic import AsyncAnthropic
    assert isinstance(client, AsyncAnthropic)
