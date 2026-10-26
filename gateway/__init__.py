"""Provider-independent async gateway for NVIDIA NIM and Nebius Token Factory.

The only public operation is :meth:`AIGateway.generate`. Provider selection,
configuration, authentication, and HTTP details stay inside this package.
The implementation uses only the Python standard library.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


__all__ = [
    "AIGateway",
    "AIGatewayError",
    "APIAuthenticationError",
    "APIRequestError",
    "APITimeoutError",
    "GatewayConfigurationError",
    "InvalidProviderError",
    "MissingAPIKeyError",
    "ai_gateway",
]


class AIGatewayError(Exception):
    """Base class for safe, user-facing gateway errors."""


class GatewayConfigurationError(AIGatewayError):
    """Raised when gateway configuration is invalid or incomplete."""


class MissingAPIKeyError(GatewayConfigurationError):
    """Raised when the selected provider's API key is not configured."""


class InvalidProviderError(GatewayConfigurationError):
    """Raised when the selected provider is not supported."""


class APITimeoutError(AIGatewayError):
    """Raised when a provider request exceeds the configured timeout."""


class APIAuthenticationError(AIGatewayError):
    """Raised when provider credentials are rejected or lack access."""


class APIRequestError(AIGatewayError):
    """Raised when a provider request fails or returns an invalid response."""


@dataclass(frozen=True)
class _ProviderConfig:
    api_key_env: str
    base_url_env: str
    model_env: str


@dataclass(frozen=True)
class _ProviderSettings:
    api_key: str
    base_url: str
    model: str


# These are the only supported providers. Values are environment variable names,
# not credentials; model IDs and endpoint URLs are configured by the user.
_PROVIDERS: dict[str, _ProviderConfig] = {
    "nvidia": _ProviderConfig(
        api_key_env="NVIDIA_API_KEY",
        base_url_env="NVIDIA_API_BASE_URL",
        model_env="NVIDIA_MODEL",
    ),
    "nebius": _ProviderConfig(
        api_key_env="NEBIUS_API_KEY",
        base_url_env="NEBIUS_API_BASE_URL",
        model_env="NEBIUS_MODEL",
    ),
}


class AIGateway:
    """Send chat completion requests through NVIDIA NIM or Nebius Token Factory."""

    async def generate(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        provider: str | None = None,
        model: str | None = None,
        **request_options: Any,
    ) -> dict[str, Any]:
        """Return the provider's OpenAI-compatible chat completion JSON.

        ``provider`` defaults to ``AI_PROVIDER`` (then ``nvidia``). The selected
        provider's key, base URL, and model are read from environment variables.
        Additional keyword arguments, such as ``temperature`` or ``max_tokens``,
        are sent as chat completion request options.
        """
        selected_provider = (provider or os.getenv("AI_PROVIDER", "nvidia")).strip().lower()
        provider_config = _PROVIDERS.get(selected_provider)
        if provider_config is None:
            raise InvalidProviderError(
                "Unsupported AI provider. Choose either 'nvidia' or 'nebius'."
            )
        if not messages:
            raise ValueError("messages must contain at least one message")
        if request_options.get("stream") is True:
            raise ValueError("Streaming responses are not supported by this gateway method")

        settings = self._load_settings(provider_config, model_override=model)
        timeout = self._load_timeout()
        payload = {
            "model": settings.model,
            "messages": list(messages),
            **request_options,
        }

        try:
            # urllib is blocking; run it in a worker thread so this API remains
            # non-blocking for the caller's asyncio event loop.
            return await asyncio.wait_for(
                asyncio.to_thread(self._post_json, selected_provider, settings, payload, timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise APITimeoutError(
                f"{selected_provider} request timed out after {timeout:g} seconds."
            ) from None

    @staticmethod
    def _load_settings(
        config: _ProviderConfig, *, model_override: str | None
    ) -> _ProviderSettings:
        api_key = os.getenv(config.api_key_env, "").strip()
        if not api_key:
            raise MissingAPIKeyError(
                f"{config.api_key_env} is required for the selected provider."
            )

        base_url = os.getenv(config.base_url_env, "").strip().rstrip("/")
        if not base_url:
            raise GatewayConfigurationError(f"{config.base_url_env} must be configured.")
        try:
            parsed_url = urlsplit(base_url)
        except ValueError:
            raise GatewayConfigurationError(
                f"{config.base_url_env} must be a valid HTTP(S) base URL."
            ) from None
        if (
            parsed_url.scheme not in ("http", "https")
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise GatewayConfigurationError(
                f"{config.base_url_env} must be an HTTP(S) base URL without embedded credentials."
            )

        model_name = (model_override or os.getenv(config.model_env, "")).strip()
        if not model_name:
            raise GatewayConfigurationError(f"{config.model_env} must be configured.")

        return _ProviderSettings(api_key=api_key, base_url=base_url, model=model_name)

    @staticmethod
    def _load_timeout() -> float:
        raw_timeout = os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "60").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise GatewayConfigurationError(
                "AI_REQUEST_TIMEOUT_SECONDS must be a positive number."
            ) from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise GatewayConfigurationError(
                "AI_REQUEST_TIMEOUT_SECONDS must be a positive number."
            )
        return timeout

    @staticmethod
    def _post_json(
        provider: str,
        settings: _ProviderSettings,
        payload: Mapping[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        try:
            request_body = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError):
            raise APIRequestError("The chat request contains values that cannot be encoded as JSON.") from None

        request = Request(
            f"{settings.base_url}/chat/completions",
            data=request_body,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                response_body = response.read()
        except HTTPError as error:
            error.close()
            if error.code in (401, 403):
                raise APIAuthenticationError(
                    f"{provider} rejected the configured credentials or access. "
                    "Check the selected provider's API key and model access."
                ) from None
            if error.code == 429:
                raise APIRequestError(
                    f"{provider} rate limit exceeded (HTTP 429). Retry after a short delay."
                ) from None
            if error.code >= 500:
                raise APIRequestError(
                    f"{provider} service returned HTTP {error.code}. Try again later."
                ) from None
            raise APIRequestError(
                f"{provider} API rejected the request (HTTP {error.code}). "
                "Check the configured model and request options."
            ) from None
        except (TimeoutError, socket.timeout):
            raise APITimeoutError(f"{provider} request timed out.") from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise APITimeoutError(f"{provider} request timed out.") from None
            raise APIRequestError(f"Could not connect to the {provider} API.") from None
        except HTTPException:
            raise APIRequestError(f"{provider} API returned an incomplete HTTP response.") from None
        except OSError:
            raise APIRequestError(f"Could not complete the request to the {provider} API.") from None

        try:
            result = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIRequestError(f"{provider} API returned invalid JSON.") from None
        if not isinstance(result, dict):
            raise APIRequestError(f"{provider} API returned an unexpected response format.")
        return result


# Public singleton interface: ``await ai_gateway.generate(...)``.
ai_gateway = AIGateway()
