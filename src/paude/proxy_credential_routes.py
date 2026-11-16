"""Custom credential routes, optionally restricted to a request path prefix.

Custom routes precede the proxy's complete built-in table. More specific
path prefixes come first because the proxy uses the first matching route.
Different credentials may share a custom host when explicit, distinct path
prefixes determine precedence. Host-only conflicts and collisions with live
built-in credentials remain rejected, including on proxy rebuilds.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, NoReturn

from paude.endpoints import authority_host, normalize_allowed_endpoints

CREDENTIAL_ROUTES_PATH = "/data/auth/credential-routes.json"
CREDENTIAL_ROUTES_ENV = "PAUDE_PROXY_CREDENTIALS_CONFIG"

_DEFAULT_PORT = 443

# Copy of paude-proxy's embedded internal/credentials/credentials.json at the
# PAUDE_PROXY_VERSION pinned in containers/proxy/Dockerfile. A custom routing
# file replaces this table entirely, so it MUST be kept in sync whenever that
# pin is bumped, or sessions using --credential-domain lose built-in routes.
DEFAULT_CREDENTIAL_ROUTES: list[dict[str, Any]] = [
    {
        "env_var": "CHATGPT_AUTH_FILE",
        "injector": "chatgpt",
        "params": {"path_prefix": "/backend-api"},
        "domains": ["chatgpt.com"],
    },
    {
        "env_var": "ANTHROPIC_OAUTH_CREDS_FILE",
        "injector": "anthropic_oauth",
        "domains": ["api.anthropic.com", ".claude.ai"],
    },
    {
        "env_var": "CLAUDE_CODE_OAUTH_TOKEN",
        "injector": "bearer",
        "domains": ["api.anthropic.com", ".claude.ai"],
    },
    {
        "env_var": "ANTHROPIC_API_KEY",
        "injector": "api_key",
        "params": {"header_name": "x-api-key"},
        "domains": [".anthropic.com"],
    },
    {
        "env_var": "OPENAI_API_KEY",
        "injector": "bearer",
        "domains": [".openai.com", "chatgpt.com", ".chatgpt.com"],
    },
    {
        "env_var": "CURSOR_API_KEY",
        "injector": "bearer",
        "domains": [".cursor.com", ".cursorapi.com"],
    },
    {
        "env_var": "GH_TOKEN",
        "injector": "bearer",
        "domains": ["api.github.com"],
    },
    {
        "env_var": "GOOGLE_APPLICATION_CREDENTIALS",
        "injector": "gcloud",
        "domains": [".googleapis.com"],
    },
]


def _routable_secret_vars(provider_name: str) -> list[str]:
    """Return the provider's secret env vars that have a default route."""
    from paude.providers import get_provider

    routable = {route["env_var"] for route in DEFAULT_CREDENTIAL_ROUTES}
    return [
        var for var in get_provider(provider_name).secret_env_vars if var in routable
    ]


def split_credential_domain(spec: str) -> tuple[str, str]:
    """Split a normalized ``provider=host:port[/path]`` spec."""
    provider, _, authority = spec.partition("=")
    return provider, authority


def _split_target(target: str) -> tuple[str, str]:
    """Separate an authority from an optional literal path prefix."""
    authority, separator, path = target.partition("/")
    return authority, separator + path


def _canonical_target(target: str) -> str:
    """Normalize the authority without changing path matching semantics."""
    authority, prefix = _split_target(target)
    if any(char.isspace() or ord(char) < 32 for char in prefix) or any(
        char in prefix for char in "?#*%\\"
    ):
        raise ValueError("path must be a literal prefix without query or fragment")
    if authority.endswith("]") or ":" not in authority:
        authority = f"{authority}:{_DEFAULT_PORT}"
    (canonical,) = normalize_allowed_endpoints([authority])
    return canonical + prefix


def parse_credential_domains(
    values: list[str] | None,
    providers: list[str],
) -> list[str]:
    """Normalize ``PROVIDER=HOST[:PORT][/PATH_PREFIX]`` specs.

    Raises:
        ValueError: On a malformed spec, a provider the session does not
            configure, or a provider with no API-key credential to route.
            Host collisions between credentials are rejected later, where
            the full routing table exists — see
            ``build_credential_routes_config``.
    """
    normalized: list[str] = []
    for value in values or []:
        provider, sep, target = (part.strip() for part in value.partition("="))
        if not sep or not provider or not target:
            raise ValueError(
                f"Invalid credential domain '{value}'; "
                "expected PROVIDER=HOST[:PORT][/PATH_PREFIX]."
            )
        if provider not in providers:
            configured = ", ".join(providers) or "none"
            raise ValueError(
                f"Credential domain '{value}' names provider '{provider}', which "
                f"this session does not configure (providers: {configured})."
            )
        if not _routable_secret_vars(provider):
            raise ValueError(
                f"Provider '{provider}' has no API-key credential that can be "
                "routed to a custom domain."
            )
        try:
            canonical = _canonical_target(target)
        except ValueError as exc:
            raise ValueError(f"Invalid credential domain '{value}': {exc}") from None
        spec = f"{provider}={canonical}"
        if spec not in normalized:
            normalized.append(spec)
    return normalized


def credential_domain_hosts(specs: list[str]) -> list[str]:
    """Return the hosts the specs route credentials to, for the allowlist."""
    return list(
        dict.fromkeys(
            authority_host(_split_target(split_credential_domain(s)[1])[0])
            for s in specs
        )
    )


def credential_domain_endpoints(specs: list[str]) -> list[str]:
    """Return the nonstandard-port authorities the specs need allowed."""
    authorities = (_split_target(split_credential_domain(spec)[1])[0] for spec in specs)
    return list(
        dict.fromkeys(a for a in authorities if not a.endswith(f":{_DEFAULT_PORT}"))
    )


def _custom_routes(specs: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """Build each spec's custom routes as ``(spec, host, route)`` triples."""
    defaults = {route["env_var"]: route for route in DEFAULT_CREDENTIAL_ROUTES}
    custom: list[tuple[str, str, dict[str, Any]]] = []
    for spec in specs:
        provider, target = split_credential_domain(spec)
        authority, prefix = _split_target(target)
        host = authority_host(authority)
        for var in _routable_secret_vars(provider):
            route = copy.deepcopy(defaults[var])
            route["domains"] = [host]
            if prefix:
                route.setdefault("params", {})["path_prefix"] = prefix
            custom.append((spec, host, route))
    custom.sort(
        key=lambda entry: -len(entry[2].get("params", {}).get("path_prefix", ""))
    )
    return custom


def _reject_host_collision(
    spec: str, env_var: str, host: str, rival_var: str, rival_source: str
) -> NoReturn:
    """Reject ambiguous custom routes or a live built-in collision."""
    raise ValueError(
        f"Credential domain '{spec}' routes {env_var} to host '{host}', "
        f"which already receives {rival_var} via {rival_source}; the proxy "
        "injects only the first matching credential. Use distinct explicit "
        "path prefixes for custom hosts, or separate provider hosts."
    )


def _route_domain_matches(domain: str, host: str) -> bool:
    """Match a route domain the way paude-proxy's store does.

    A ``.suffix`` entry requires the dot (the proxy's ``HasSuffix``), so
    the bare apex — ``cursor.com`` against ``.cursor.com`` — does not
    match; an entry without the dot matches the host exactly. This is
    stricter than the advisory ``domain_matches_host``, whose suffix
    branch also matches the apex.
    """
    if domain.startswith("."):
        return host.endswith(domain)
    return host == domain


def _proxy_env_names(
    proxy_credentials: Mapping[str, str] | None,
) -> set[str] | None:
    """Env var names the proxy container will actually carry.

    ``None`` (no session context) stays ``None`` so callers get the
    conservative every-route-live behavior. A ``ProxyCredentials`` also
    carries the ChatGPT-OAuth mode signal, which reaches the proxy as
    ``PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE``.
    """
    if proxy_credentials is None:
        return None
    names = set(proxy_credentials)
    if getattr(proxy_credentials, "chatgpt_oauth_mode", False):
        from paude.backends.proxy_config import PROXY_CHATGPT_AUTH_STATE_ENV

        names.add(PROXY_CHATGPT_AUTH_STATE_ENV)
    return names


def _live_builtin_routes(proxy_env_names: set[str] | None) -> list[dict[str, Any]]:
    """Built-in routes the proxy will load given its env var names.

    paude-proxy skips a route whose env var has no source
    (``BuildFromConfig``), with two injector-specific fallbacks: the gcloud
    route is sourced from ``GCP_ADC_JSON`` and the chatgpt route from the
    ChatGPT-OAuth mode signal. Like ``DEFAULT_CREDENTIAL_ROUTES``, keep the
    fallback names in sync with the ``PAUDE_PROXY_VERSION`` pin. ``None``
    means no session context; assume every built-in route is live so the
    check stays conservative.
    """
    if proxy_env_names is None:
        return DEFAULT_CREDENTIAL_ROUTES
    from paude.backends.proxy_config import (
        PROXY_CHATGPT_AUTH_STATE_ENV,
        PROXY_GCP_ADC_ENV,
    )

    fallbacks = {
        "GOOGLE_APPLICATION_CREDENTIALS": PROXY_GCP_ADC_ENV,
        "CHATGPT_AUTH_FILE": PROXY_CHATGPT_AUTH_STATE_ENV,
    }
    return [
        route
        for route in DEFAULT_CREDENTIAL_ROUTES
        if route["env_var"] in proxy_env_names
        or fallbacks.get(route["env_var"]) in proxy_env_names
    ]


def _reject_ambiguous_hosts(
    custom: list[tuple[str, str, dict[str, Any]]],
    proxy_env_names: set[str] | None,
) -> None:
    """Allow explicit path precedence while preserving built-in protections."""
    claims: dict[str, list[tuple[str, str, str]]] = {}
    for spec, host, route in custom:
        prefix = route.get("params", {}).get("path_prefix", "")
        env_var = route["env_var"]
        for prior_var, prior_spec, prior_prefix in claims.get(host, []):
            if prior_var != env_var and (
                not prefix or not prior_prefix or prefix == prior_prefix
            ):
                _reject_host_collision(
                    spec, env_var, host, prior_var, f"spec '{prior_spec}'"
                )
        claims.setdefault(host, []).append((env_var, spec, prefix))
        for default in _live_builtin_routes(proxy_env_names):
            if default["env_var"] != env_var and any(
                _route_domain_matches(domain, host) for domain in default["domains"]
            ):
                _reject_host_collision(
                    spec, env_var, host, default["env_var"], "a built-in route"
                )


def validate_credential_domain_specs(
    specs: list[str],
    proxy_credentials: Mapping[str, str] | None = None,
) -> None:
    """Reject ambiguous custom routes and live built-in collisions.

    Runs the same check ``build_credential_routes_config`` enforces, for
    callers that validate early (``paude create``) rather than build.

    Args:
        specs: Normalized specs from ``parse_credential_domains``.
        proxy_credentials: The credentials paude will hand the proxy, used
            to decide which built-in routes are live; ``None`` (no session
            context) assumes every built-in route is live. A
            ``ProxyCredentials`` is accepted and its ChatGPT-OAuth mode
            signal counts too.
    """
    _reject_ambiguous_hosts(_custom_routes(specs), _proxy_env_names(proxy_credentials))


def build_credential_routes_config(
    specs: list[str],
    proxy_credentials: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the full paude-proxy routing file for the given specs.

    Args:
        specs: Normalized specs from ``parse_credential_domains``.
        proxy_credentials: The credentials paude will hand the proxy, used
            to decide which built-in routes are live; ``None`` (no session
            context) assumes every built-in route is live.

    Raises:
        ValueError: If different credentials share an ambiguous custom path,
            a host-only route, or another credential's live built-in domain
            (see ``_reject_ambiguous_hosts``).
    """
    custom = _custom_routes(specs)
    _reject_ambiguous_hosts(custom, _proxy_env_names(proxy_credentials))
    return {
        "credentials": [route for _, _, route in custom]
        + copy.deepcopy(DEFAULT_CREDENTIAL_ROUTES)
    }
