"""LLM credentials loader + Anthropic client factory.

Credentials come from key.txt (project root by default) or environment variables.
Env vars take precedence over file values (12-factor).
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from anthropic import AsyncAnthropic


DEFAULT_KEY_FILE = Path(__file__).resolve().parents[3] / "key.txt"


class LLMCredentialsError(RuntimeError):
    """Raised when required LLM credentials cannot be located."""


@dataclass(frozen=True)
class LLMCredentials:
    auth_token: str
    base_url: str
    model: str


_KEY_LINE_RE = re.compile(r'"([A-Z_][A-Z0-9_]*)"\s*:\s*"([^"]*)"')


def _parse_key_file(path: Path) -> dict[str, str]:
    """Tolerantly parse a key.txt-style file. Lines look like:
        "KEY": "value",
    Whitespace and trailing commas are ignored. Lines not matching are skipped.
    Returns an empty dict if the file doesn't exist or is empty.
    """
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _KEY_LINE_RE.search(line)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def load_llm_credentials(
    key_file: Optional[Path] = None,
) -> LLMCredentials:
    """Load credentials. Env vars override file values."""
    path = Path(key_file) if key_file else DEFAULT_KEY_FILE
    file_data = _parse_key_file(path)

    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or file_data.get("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or file_data.get("ANTHROPIC_BASE_URL", "")
    model = (
        os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
        or file_data.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or ""
    )

    if not auth_token or not base_url:
        raise LLMCredentialsError(
            "Missing ANTHROPIC_AUTH_TOKEN or ANTHROPIC_BASE_URL. "
            f"Checked file {path} and environment variables."
        )

    if not model:
        model = "claude-3-5-sonnet-20241022"  # safe fallback

    return LLMCredentials(auth_token=auth_token, base_url=base_url, model=model)


def build_anthropic_client(key_file: Optional[Path] = None) -> AsyncAnthropic:
    """Construct an AsyncAnthropic client from current credentials."""
    creds = load_llm_credentials(key_file=key_file)
    return AsyncAnthropic(auth_token=creds.auth_token, base_url=creds.base_url)
