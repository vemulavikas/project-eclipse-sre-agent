"""Send one harmless prompt through the configured provider.

Run from the repository root after setting the selected provider's environment
variables: ``python -m gateway.test_gateway``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from gateway import AIGatewayError, ai_gateway


def _load_project_env() -> None:
    """Load simple KEY=VALUE entries from the root .env without logging values."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


async def main() -> None:
    _load_project_env()
    try:
        result = await ai_gateway.generate(
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly: gateway connection successful",
                }
            ],
            max_tokens=32,
            temperature=0,
        )
    except AIGatewayError as error:
        # Gateway messages are intentionally credential-free.
        print(f"Gateway request failed: {error}")
        raise SystemExit(1) from None

    choices = result.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content")
    if content is None:
        print("Gateway request succeeded, but the response contained no message content.")
    elif isinstance(content, str):
        # Keep configured credentials out of terminal output even if a response
        # unexpectedly repeats one of them.
        safe_content = content
        for variable in ("NVIDIA_API_KEY", "NEBIUS_API_KEY"):
            secret = os.getenv(variable, "")
            if secret:
                safe_content = safe_content.replace(secret, "[REDACTED]")
        print(safe_content)
    else:
        # Avoid dumping structured provider response objects that might contain
        # request or diagnostic metadata.
        print("Gateway request succeeded and returned a structured response.")


if __name__ == "__main__":
    asyncio.run(main())
