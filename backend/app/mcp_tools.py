"""MCP-style integrations exposed as selectable OpenAI tools.

The module deliberately keeps the integrations as ordinary Python functions. A
caller can select the function definitions it needs with :func:`get_mcp_tools`
and dispatch tool calls with :func:`invoke_tool`.

Runtime settings are read lazily from the required ``mcp_config.yaml`` file in
the project root. A local ``.env`` file supplies secrets and optional
environment overrides; existing process-environment values always win.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "mcp_config.yaml"
DOTENV_PATH = PROJECT_ROOT / ".env"
NO_RESULT = "no result"

REALPING_ENDPOINTS = frozenset(
    {
        "transactions",
        "listings",
        "area-stats",
        "address",
        "building",
        "value-check",
        "parking",
        "rent-yield",
        "comps-estimate",
        "usage",
        "health",
    }
)

REALPING_PARAM_ALIASES = {
    "city": "縣市",
    "county": "縣市",
    "district": "區",
    "building_type": "建物型態",
}

REALPING_ENDPOINT_PARAMS = {
    "area-stats": {
        "allowed": {"縣市", "區", "建物型態", "months"},
        "required": {"縣市", "區"},
    },
    "rent-yield": {
        "allowed": {"縣市", "區", "建物型態", "min_n", "limit"},
        "required": set(),
    },
}


class MCPError(RuntimeError):
    """Base exception for configuration and upstream API failures."""


class MCPConfigurationError(MCPError):
    """Raised when a required API key or configuration value is missing."""


class MCPRequestError(MCPError):
    """Raised when an upstream MCP integration returns an error."""


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursive merge without mutating either input mapping."""

    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MCPConfigurationError(f"Configuration file not found: {path}")
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MCPConfigurationError("Install PyYAML from backend/requirements.txt") from exc

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise MCPConfigurationError(f"Unable to parse {path}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise MCPConfigurationError(f"{path} must contain a YAML mapping")
    return dict(loaded)


def _load_dotenv(path: Path) -> None:
    """Load simple dotenv entries without overwriting process environment."""

    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MCPConfigurationError(f"Unable to read {path}: {exc}") from exc

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _set_nested(config: dict[str, Any], section: str, key: str, value: Any) -> None:
    section_config = config.setdefault(section, {})
    if isinstance(section_config, dict):
        section_config[key] = value


def mcp_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load MCP configuration and apply environment-variable overrides.

    Supported secrets are ``APIFY_API_TOKEN``, ``REALPING_API_KEY`` and
    ``GOOGLE_MAPS_API_KEY``. The corresponding values may also be placed in
    the YAML file as ``api_token``/``api_key`` fields for local development.
    """

    requested_path = os.environ.get("MCP_CONFIG_PATH")
    path = Path(config_path or requested_path or CONFIG_PATH)
    _load_dotenv(path.parent / ".env" if config_path else DOTENV_PATH)

    config = _read_config_file(path)

    env_overrides = {
        ("apify", "api_token"): ("APIFY_API_TOKEN", "APIFY_TOKEN"),
        ("realping", "api_key"): ("REALPING_API_KEY", "REALPING_TOKEN"),
        ("google_maps", "api_key"): (
            "GOOGLE_MAPS_API_KEY",
            "GOOGLE_MAPS_KEY",
            "GOOGLE_API_KEY",
        ),
        ("openai", "model"): ("OPENAI_MODEL",),
    }
    for (section, key), names in env_overrides.items():
        for name in names:
            value = os.environ.get(name)
            if value:
                _set_nested(config, section, key, value)
                break

    if os.environ.get("MCP_HTTP_TIMEOUT"):
        _set_nested(config, "http", "timeout", os.environ["MCP_HTTP_TIMEOUT"])
    if os.environ.get("MCP_OUTPUT_MODE"):
        _set_nested(config, "output", "mode", os.environ["MCP_OUTPUT_MODE"])
    return config


def _runtime_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    loaded = mcp_config()
    return _deep_merge(loaded, config or {})


def _required(config: Mapping[str, Any], section: str, key: str) -> str:
    value = config.get(section, {})
    if isinstance(value, Mapping):
        result = value.get(key)
    else:
        result = None
    if not isinstance(result, str) or not result.strip():
        env_name = {
            ("apify", "api_token"): "APIFY_API_TOKEN",
            ("realping", "api_key"): "REALPING_API_KEY",
            ("google_maps", "api_key"): "GOOGLE_MAPS_API_KEY",
        }.get((section, key), f"{section.upper()}_{key.upper()}")
        raise MCPConfigurationError(f"Missing {env_name} (or {section}.{key} in mcp_config.yaml)")
    return result.strip()


def _config_value(config: Mapping[str, Any], section: str, key: str) -> Any:
    section_config = config.get(section)
    if not isinstance(section_config, Mapping) or key not in section_config:
        raise MCPConfigurationError(f"Missing {section}.{key} in mcp_config.yaml")
    return section_config[key]


def _timeout(config: Mapping[str, Any]) -> float:
    try:
        value = float(_config_value(config, "http", "timeout"))
    except (TypeError, ValueError) as exc:
        raise MCPConfigurationError("http.timeout must be a number") from exc
    if value <= 0:
        raise MCPConfigurationError("http.timeout must be greater than zero")
    return value


def _output_mode(config: Mapping[str, Any]) -> str:
    value = _config_value(config, "output", "mode")
    if not isinstance(value, str) or value.strip().lower() not in {"print", "return"}:
        raise MCPConfigurationError("output.mode must be either 'print' or 'return'")
    return value.strip().lower()


def _is_no_result(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, str):
        normalized = result.strip().lower()
        return not normalized or normalized in {
            "error",
            "failed",
            "failure",
            "no result",
            "not found",
        }
    if isinstance(result, (list, tuple)):
        return not result or all(_is_no_result(item) for item in result)
    if not isinstance(result, Mapping):
        return False
    if not result:
        return True
    if result.get("error") or result.get("errors"):
        return True
    if result.get("success") is False or result.get("ok") is False:
        return True
    failure_statuses = {
        "error",
        "failed",
        "failure",
        "not_found",
        "zero_results",
    }
    if str(result.get("status", "")).lower() in failure_statuses:
        return True
    status_code = result.get("status_code", result.get("code"))
    if isinstance(status_code, int) and status_code >= 400:
        return True
    if result.get("count") == 0 or result.get("total") == 0:
        return True
    empty_collections = ("data", "items", "places", "results")
    return any(
        key in result and result[key] in (None, [], {})
        for key in empty_collections
    )


def _deliver_output(result: Any, mode: str) -> str | None:
    """Serialize a tool result and either return it or write it to stdout."""

    output = (
        NO_RESULT
        if _is_no_result(result)
        else json.dumps(result, ensure_ascii=False, default=str)
    )
    return _deliver_text(output, mode)


def _deliver_text(output: str, mode: str) -> str | None:
    if mode == "print":
        print(output)
        return None
    return output


def _fallback_output(config: Mapping[str, Any] | None) -> str | None:
    try:
        mode = _output_mode(_runtime_config(config))
    except Exception:
        mode = "return"
    return _deliver_text(NO_RESULT, mode)


def _no_result_on_error(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception:
            return _fallback_output(kwargs.get("config"))

    return wrapped


def _non_negative_integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MCPConfigurationError(f"{name} must be a non-negative integer")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MCPConfigurationError(f"{name} must be a positive integer")
    return value


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _empty_result_retries(config: Mapping[str, Any]) -> int:
    return _non_negative_integer(
        _config_value(config, "retry", "empty_result_retries"),
        "retry.empty_result_retries",
    )


def _retry_result(
    operation: Callable[[], Any],
    should_retry: Callable[[Any], bool],
    retries: int,
) -> Any:
    """Retry an operation only when its returned value is unusable."""

    result = operation()
    for _ in range(retries):
        if not should_retry(result):
            break
        result = operation()
    return result


def _request_json_once(
    url: str,
    *,
    method: str,
    body: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
) -> Any:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = raw.decode("utf-8", errors="replace").strip()
        try:
            detail = json.loads(detail).get("error", detail) if detail else detail
        except json.JSONDecodeError:
            pass
        raise MCPRequestError(
            f"HTTP {exc.code} from {url}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise MCPRequestError(f"Request to {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise MCPRequestError(f"Request to {url} timed out") from exc

    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPRequestError(
            f"Expected JSON from {url}, received HTTP {status}"
        ) from exc


def _request_json(
    url: str,
    *,
    method: str = "GET",
    params: Mapping[str, Any] | None = None,
    payload: Any = None,
    headers: Mapping[str, str] | None = None,
    timeout: float,
    user_agent: str,
    empty_result_retries: int,
) -> Any:
    """Make a JSON request, retry empty results, and normalize failures."""

    query: list[tuple[str, Any]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            query.extend((key, item) for item in value)
        else:
            query.append((key, value))
    if query:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(query)}"

    body = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        **(headers or {}),
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    return _retry_result(
        lambda: _request_json_once(
            url,
            method=method,
            body=body,
            headers=request_headers,
            timeout=timeout,
        ),
        _is_no_result,
        empty_result_retries,
    )


@_no_result_on_error
def threads(
    searchQuery: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Search public Threads posts with the configured Apify actor."""

    query = _require_text(searchQuery, "searchQuery")

    runtime = _runtime_config(config)
    output_mode = _output_mode(runtime)
    token = _required(runtime, "apify", "api_token")
    actor_id = _require_text(
        _config_value(runtime, "apify", "actor_id"),
        "apify.actor_id",
    )
    wait_for_finish = int(_config_value(runtime, "apify", "wait_for_finish"))
    if not 1 <= wait_for_finish <= 300:
        raise ValueError("apify.wait_for_finish must be between 1 and 300 seconds")

    payload = {"searchQuery": query}

    base_url = str(_config_value(runtime, "apify", "base_url")).rstrip("/")
    encoded_actor = urllib.parse.quote(actor_id, safe="~")
    url = f"{base_url}/v2/acts/{encoded_actor}/run-sync-get-dataset-items"
    # Apify's synchronous dataset endpoint calls this run-level limit
    # ``timeout`` (the older ``waitForFinish`` name is used by other APIs).
    params: dict[str, Any] = {"timeout": wait_for_finish}
    max_items = _config_value(runtime, "apify", "max_items")
    if max_items is not None:
        params["limit"] = int(max_items)
    result = _request_json(
        url,
        method="POST",
        params=params,
        payload=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_timeout(runtime),
        user_agent=str(_config_value(runtime, "http", "user_agent")),
        empty_result_retries=_empty_result_retries(runtime),
    )
    return _deliver_output(result, output_mode)


def _normalize_realping_params(
    endpoint: str,
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized = dict(params or {})
    for alias, api_name in REALPING_PARAM_ALIASES.items():
        if alias in normalized:
            normalized.setdefault(api_name, normalized.pop(alias))

    contract = REALPING_ENDPOINT_PARAMS.get(endpoint)
    if contract is None:
        return normalized

    unsupported = sorted(set(normalized) - contract["allowed"])
    if unsupported:
        allowed = ", ".join(sorted(contract["allowed"]))
        raise ValueError(
            f"{endpoint} does not accept: {', '.join(unsupported)}. "
            f"Supported query parameters are: {allowed}"
        )
    missing = sorted(contract["required"] - set(normalized))
    if missing:
        raise ValueError(f"{endpoint} requires query parameters: {', '.join(missing)}")
    return normalized


@_no_result_on_error
def realPing(
    endpoint: str,
    params: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Query one of RealPing's structured Taiwanese property-data endpoints."""

    endpoint = _require_text(endpoint, "endpoint").lstrip("/")
    if endpoint not in REALPING_ENDPOINTS:
        choices = ", ".join(sorted(REALPING_ENDPOINTS - {"health"}))
        raise ValueError(f"endpoint must be one of: {choices}, health")
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("params must be an object of query parameters")
    normalized_params = _normalize_realping_params(endpoint, params)

    runtime = _runtime_config(config)
    output_mode = _output_mode(runtime)
    base_url = str(_config_value(runtime, "realping", "base_url")).rstrip("/")
    headers: dict[str, str] = {}
    if endpoint != "health":
        headers["X-API-Key"] = _required(runtime, "realping", "api_key")
    result = _request_json(
        f"{base_url}/{endpoint}",
        params=normalized_params,
        headers=headers,
        timeout=_timeout(runtime),
        user_agent=str(_config_value(runtime, "http", "user_agent")),
        empty_result_retries=_empty_result_retries(runtime),
    )
    return _deliver_output(result, output_mode)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


@_no_result_on_error
def googleMaps(
    query: str = "",
    operation: str | None = None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_m: float | None = None,
    place_id: str | None = None,
    page_token: str | None = None,
    language_code: str | None = None,
    region_code: str | None = None,
    included_type: str | None = None,
    open_now: bool | None = None,
    min_rating: float | None = None,
    max_results: int | None = None,
    field_mask: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Use Google Maps Platform for place search, details, or geocoding.

    ``text_search`` uses Places API (New). ``place_details`` uses a Place ID,
    while ``geocode`` and ``reverse_geocode`` use the Geocoding API.
    """

    runtime = _runtime_config(config)
    output_mode = _output_mode(runtime)

    operation = _require_text(
        operation
        if operation is not None
        else _config_value(runtime, "google_maps", "default_operation"),
        "operation",
    )
    max_results = (
        max_results
        if max_results is not None
        else _config_value(runtime, "google_maps", "default_max_results")
    )
    supported = {"text_search", "place_details", "geocode", "reverse_geocode"}
    if operation not in supported:
        raise ValueError(f"operation must be one of: {', '.join(sorted(supported))}")
    if (
        not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or not 1 <= max_results <= 20
    ):
        raise ValueError("max_results must be an integer between 1 and 20")

    api_key = _required(runtime, "google_maps", "api_key")
    timeout = _timeout(runtime)

    if operation == "text_search":
        query = _require_text(query, "query")
        payload: dict[str, Any] = {"textQuery": query, "maxResultCount": max_results}
        if page_token:
            payload["pageToken"] = _require_text(page_token, "page_token")
        if language_code:
            payload["languageCode"] = _require_text(language_code, "language_code")
        if region_code:
            payload["regionCode"] = _require_text(region_code, "region_code")
        if included_type:
            payload["includedType"] = _require_text(included_type, "included_type")
        if open_now is not None:
            payload["openNow"] = bool(open_now)
        if min_rating is not None:
            rating = _number(min_rating, "min_rating")
            if not 0 <= rating <= 5:
                raise ValueError("min_rating must be between 0 and 5")
            payload["minRating"] = rating
        if latitude is not None or longitude is not None:
            if latitude is None or longitude is None:
                raise ValueError("latitude and longitude must be provided together")
            center = {
                "latitude": _number(latitude, "latitude"),
                "longitude": _number(longitude, "longitude"),
            }
            radius = _number(
                radius_m
                if radius_m is not None
                else _config_value(runtime, "google_maps", "default_radius_m"),
                "radius_m",
            )
            if radius <= 0:
                raise ValueError("radius_m must be greater than zero")
            payload["locationBias"] = {
                "circle": {"center": center, "radius": radius}
            }
        places_url = str(
            _config_value(runtime, "google_maps", "places_base_url")
        ).rstrip("/")
        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask
            or str(_config_value(runtime, "google_maps", "field_mask")),
        }
        result = _request_json(
            f"{places_url}/places:searchText",
            method="POST",
            payload=payload,
            headers=headers,
            timeout=timeout,
            user_agent=str(_config_value(runtime, "http", "user_agent")),
            empty_result_retries=_empty_result_retries(runtime),
        )
        return _deliver_output(result, output_mode)

    if operation == "place_details":
        place_id = _require_text(place_id, "place_id") if place_id else ""
        if not place_id:
            raise ValueError("place_id is required for place_details")
        places_url = str(
            _config_value(runtime, "google_maps", "places_base_url")
        ).rstrip("/")
        params = {"languageCode": language_code} if language_code else None
        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask
            or str(
                _config_value(
                    runtime,
                    "google_maps",
                    "place_details_field_mask",
                )
            ),
        }
        encoded_place_id = urllib.parse.quote(place_id, safe="")
        result = _request_json(
            f"{places_url}/places/{encoded_place_id}",
            params=params,
            headers=headers,
            timeout=timeout,
            user_agent=str(_config_value(runtime, "http", "user_agent")),
            empty_result_retries=_empty_result_retries(runtime),
        )
        return _deliver_output(result, output_mode)

    geocoding_url = str(
        _config_value(runtime, "google_maps", "geocoding_base_url")
    ).rstrip("/")
    if operation == "geocode":
        query = _require_text(query, "query")
        params = {
            "address": query,
            "key": api_key,
            "language": language_code,
            "region": region_code,
        }
    else:
        if latitude is None or longitude is None:
            raise ValueError("latitude and longitude are required for reverse_geocode")
        params = {
            "latlng": f"{_number(latitude, 'latitude')},{_number(longitude, 'longitude')}",
            "key": api_key,
            "language": language_code,
            "result_type": included_type,
        }
    result = _request_json(
        f"{geocoding_url}/geocode/json",
        params=params,
        timeout=timeout,
        user_agent=str(_config_value(runtime, "http", "user_agent")),
        empty_result_retries=_empty_result_retries(runtime),
    )
    return _deliver_output(result, output_mode)


@_no_result_on_error
def webSearch(
    search_context_size: str | None = None,
    user_location: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | str | None:
    """Return the native OpenAI Responses API web-search tool definition.

    The actual search is performed by OpenAI when this object is included in a
    Responses API request; this function intentionally does not make a local
    network request.
    """

    runtime = _runtime_config(config)
    tool_type = str(_config_value(runtime, "openai", "web_search_type"))
    if tool_type not in {"web_search", "web_search_preview"}:
        raise ValueError("openai.web_search_type must be 'web_search' or 'web_search_preview'")
    tool: dict[str, Any] = {"type": tool_type}
    if search_context_size is not None:
        if search_context_size not in {"low", "medium", "high"}:
            raise ValueError("search_context_size must be low, medium, or high")
        tool["search_context_size"] = search_context_size
    if user_location is not None:
        if not isinstance(user_location, Mapping):
            raise ValueError("user_location must be an object")
        tool["user_location"] = dict(user_location)
    return tool


MCP_TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "threads": threads,
    "realPing": realPing,
    "googleMaps": googleMaps,
}


def _function_schema(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


FUNCTION_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "threads": _function_schema(
        "threads",
        "Search public Threads posts using a keyword, phrase, or hashtag.",
        {
            "type": "object",
            "properties": {
                "searchQuery": {
                    "type": "string",
                    "description": "Keywords, a phrase, or a hashtag.",
                },
            },
            "required": ["searchQuery"],
            "additionalProperties": False,
        },
    ),
    "realPing": _function_schema(
        "realPing",
        "Query RealPing's Taiwanese sale transactions, valuations, parking "
        "prices, and rent yields. "
        "Use rent-yield for rental-yield questions; area-stats reports sale-price statistics.",
        {
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "enum": sorted(REALPING_ENDPOINTS),
                    "description": (
                        "API endpoint. area-stats requires 縣市 and 區. For rental questions, "
                        "rent-yield is the only rental-related endpoint."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Exact endpoint query parameters. Use Chinese keys "
                        "縣市, 區, and 建物型態, "
                        "not city, district, or building_type. area-stats example: "
                        '{"縣市":"臺北市","區":"大安區","months":12}. '
                        "area-stats does not accept rent, size_tsubo, or has_elevator. "
                        "rent-yield accepts 縣市, 區, 建物型態, min_n, and limit."
                    ),
                },
            },
            "required": ["endpoint"],
            "additionalProperties": False,
        },
    ),
    "googleMaps": _function_schema(
        "googleMaps",
        "Search Google Maps places, retrieve place details, or geocode an address.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Place search text or address."},
                "operation": {
                    "type": "string",
                    "enum": ["text_search", "place_details", "geocode", "reverse_geocode"],
                },
                "latitude": {"type": ["number", "null"]},
                "longitude": {"type": ["number", "null"]},
                "radius_m": {"type": ["number", "null"]},
                "place_id": {"type": ["string", "null"]},
                "page_token": {"type": ["string", "null"]},
                "language_code": {"type": ["string", "null"]},
                "region_code": {"type": ["string", "null"]},
                "included_type": {"type": ["string", "null"]},
                "open_now": {"type": ["boolean", "null"]},
                "min_rating": {"type": ["number", "null"]},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                "field_mask": {"type": ["string", "null"]},
            },
            "required": ["query", "operation"],
            "additionalProperties": False,
        },
    ),
}


def _chat_completions_function(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a Responses function definition to Chat Completions format."""

    return {
        "type": "function",
        "function": {
            "name": definition["name"],
            "description": definition["description"],
            "parameters": definition["parameters"],
        },
    }


def get_mcp_tools(
    selected: Sequence[str] | None = None,
    *,
    api: str = "responses",
    include_web_search: bool = True,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a selectively enabled list of OpenAI tool definitions.

    ``api="responses"`` returns the current Responses API format. Pass
    ``api="chat_completions"`` for the older Chat Completions wrapper.
    """

    if api not in {"responses", "chat_completions"}:
        raise ValueError("api must be 'responses' or 'chat_completions'")
    if isinstance(selected, str):
        requested = [selected]
    else:
        requested = (
            list(selected)
            if selected is not None
            else [*FUNCTION_TOOL_DEFINITIONS, "webSearch"]
        )
    normalized = requested
    valid = set(FUNCTION_TOOL_DEFINITIONS) | {"webSearch"}
    unknown = sorted(set(normalized) - valid)
    if unknown:
        raise ValueError(f"Unknown MCP tool(s): {', '.join(unknown)}")

    result: list[dict[str, Any]] = []
    for name in normalized:
        if name == "webSearch":
            if include_web_search:
                result.append(webSearch(config=config))
            continue
        definition = json.loads(json.dumps(FUNCTION_TOOL_DEFINITIONS[name]))
        result.append(
            _chat_completions_function(definition)
            if api == "chat_completions"
            else definition
        )
    return result


@_no_result_on_error
def invoke_tool(
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Dispatch an OpenAI function-call payload to one of the local tools."""

    if name == "webSearch":
        return webSearch(config=config, **dict(arguments or {}))
    function = MCP_TOOL_FUNCTIONS.get(name)
    if function is None:
        raise ValueError(f"Unknown MCP tool: {name}")
    return function(config=config, **dict(arguments or {}))


# ---------------------------------------------------------------------------
# Conclave research-stage compatibility bridge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McpServer:
    """A selectable group of local tools exposed to :mod:`app.mcp`.

    The research stage historically connected these provider objects to remote
    MCP sessions. The current integrations execute ordinary Python functions,
    so ``connect`` below supplies a lightweight session-compatible adapter.
    """

    name: str
    default_prompt: str = ""
    url: str = ""  # Retained for callers/tests that still construct legacy adapters.
    tools: tuple[str, ...] | None = None
    fixed_tool_arguments: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False
    )
    required_tool_arguments: dict[str, tuple[str, ...]] = field(
        default_factory=dict, repr=False
    )


@dataclass(frozen=True)
class ToolRoute:
    """Route a namespaced model tool to a local integration function."""

    provider: str
    remote_name: str
    session: Any
    fixed_arguments: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class PreparedTools:
    """Function schemas and routes prepared for one planner request."""

    routes: dict[str, ToolRoute] = field(default_factory=dict)
    function_tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _DiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, Any]


def load_config() -> dict[str, Any]:
    """Load and validate the sections required by the research-stage tools."""

    config = mcp_config()
    required_sections = (
        "output",
        "retry",
        "http",
        "apify",
        "realping",
        "google_maps",
        "openai",
    )
    missing = [
        name
        for name in required_sections
        if not isinstance(config.get(name), Mapping)
    ]
    if missing:
        raise MCPConfigurationError(
            "mcp_config.yaml: missing mapping(s): " + ", ".join(missing)
        )
    return config


def _section(config: Mapping[str, Any], *path: str) -> dict[str, Any]:
    """Return a nested mapping or raise a configuration error."""

    value: Any = config
    for key in path:
        if not isinstance(value, Mapping) or not isinstance(value.get(key), Mapping):
            raise MCPConfigurationError(
                "mcp_config.yaml: missing mapping " + ".".join(path)
            )
        value = value[key]
    return dict(value)


def maps() -> McpServer:
    """Expose the direct Google Maps integration to the research stage."""

    return McpServer(name="maps", tools=("googleMaps",))


def apify_threads_post() -> McpServer:
    """Expose the direct Apify Threads integration to the research stage."""

    return McpServer(name="apify_threads_post", tools=("threads",))


def realping() -> McpServer:
    """Expose the direct RealPing integration to the research stage."""

    return McpServer(name="realping", tools=("realPing",))


def redact(value: object) -> str:
    """Remove credential-like environment values from diagnostic output."""

    text = str(value)
    sensitive_suffixes = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
    for name, secret in os.environ.items():
        if (
            name.upper().endswith(sensitive_suffixes)
            and len(secret) >= 8
            and secret.lower() != "local"
        ):
            text = text.replace(secret, "<redacted>")
    return text


def error_text(exc: BaseException) -> str:
    """Render an exception without leaking configured credentials."""

    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(error_text(child) for child in exc.exceptions)
    return redact(f"{type(exc).__name__}: {exc}")


class _LocalToolSession:
    def __init__(self, timeout: float):
        self.timeout = timeout

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Match the MCP ClientSession call shape without blocking the loop."""

        result = await asyncio.to_thread(
            invoke_tool,
            name,
            arguments,
            config={"http": {"timeout": self.timeout}},
        )
        failed = _is_no_result(result)
        return type(
            "LocalToolResult",
            (),
            {
                "structured_content": {"result": result or NO_RESULT},
                "content": (),
                "is_error": failed,
            },
        )()


@asynccontextmanager
async def connect(server: McpServer, timeout: float):
    """Prepare a session-shaped adapter and discover the provider's tools."""

    names = server.tools or tuple(FUNCTION_TOOL_DEFINITIONS)
    discovered: list[_DiscoveredTool] = []
    for name in names:
        definition = FUNCTION_TOOL_DEFINITIONS.get(name)
        if definition is None:
            raise MCPConfigurationError(f"Unknown local MCP tool: {name}")
        discovered.append(
            _DiscoveredTool(
                name=name,
                description=str(definition.get("description", name)),
                input_schema=deepcopy(definition.get("parameters", {})),
            )
        )
    yield _LocalToolSession(timeout), discovered


def _compact_schema(value: Any, description_limit: int) -> Any:
    if isinstance(value, list):
        return [_compact_schema(item, description_limit) for item in value]
    if isinstance(value, dict):
        return {
            key: item[:description_limit]
            if key == "description" and isinstance(item, str)
            else _compact_schema(item, description_limit)
            for key, item in value.items()
        }
    return value


def register_tools(
    server: McpServer,
    session: Any,
    discovered: Sequence[Any],
    wanted: set[str],
    prepared: PreparedTools,
    runtime: Mapping[str, Any],
) -> None:
    """Register local tools using the namespaced schema expected by app.mcp."""

    selected = [tool for tool in discovered if tool.name in wanted]
    missing = wanted - {tool.name for tool in selected}
    if missing:
        raise MCPConfigurationError(
            f"{server.name}: tools not found: {', '.join(sorted(missing))}"
        )

    schema_limit = int(runtime.get("schema_description_chars", 180))
    tool_limit = int(runtime.get("tool_description_chars", 400))
    for tool in selected:
        model_name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server.name}__{tool.name}")
        if len(model_name) > 64:
            digest = hashlib.sha256(model_name.encode()).hexdigest()[:8]
            model_name = model_name[:55] + "_" + digest
        if model_name in prepared.routes:
            raise MCPConfigurationError(f"Tool name collision: {model_name}")

        fixed = server.fixed_tool_arguments.get(tool.name, {})
        prepared.routes[model_name] = ToolRoute(
            provider=server.name,
            remote_name=tool.name,
            session=session,
            fixed_arguments=fixed,
        )
        parameters = _compact_schema(deepcopy(tool.input_schema), schema_limit)
        if isinstance(parameters, dict):
            properties = parameters.get("properties")
            if fixed and isinstance(properties, dict):
                parameters["properties"] = {
                    key: value for key, value in properties.items() if key not in fixed
                }
            required = parameters.get("required", [])
            required = required if isinstance(required, list) else []
            required = [key for key in required if key not in fixed]
            required.extend(server.required_tool_arguments.get(tool.name, ()))
            parameters["required"] = list(dict.fromkeys(required))

        prepared.function_tools.append(
            {
                "type": "function",
                "function": {
                    "name": model_name,
                    "description": f"[{server.name}] "
                    + str(tool.description or tool.name)[:tool_limit],
                    "parameters": parameters,
                },
            }
        )


def render_result(result: Any, limit: int) -> tuple[str, bool]:
    """Convert a local/session-compatible result to bounded, redacted JSON."""

    data = getattr(result, "structured_content", None)
    if data is not None:
        if isinstance(data, Mapping) and set(data) == {"result"}:
            data = data["result"]
        text = (
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, default=str)
        )
    else:
        text = "\n".join(
            block.text
            for block in (getattr(result, "content", None) or ())
            if getattr(block, "type", None) == "text"
        )
    safe_text = redact(text)
    failed = bool(getattr(result, "is_error", False)) or _is_no_result(text)
    payload = {
        "is_error": failed,
        "result": safe_text[:limit],
        "truncated": len(safe_text) > limit,
    }
    return json.dumps(payload, ensure_ascii=False), failed


def _execute_function_call(call: Any, config: Mapping[str, Any]) -> str:
    try:
        arguments = json.loads(call.arguments or "{}")
        if not isinstance(arguments, Mapping):
            return NO_RESULT
        result = invoke_tool(call.name, arguments, config=config)
    except Exception:
        return NO_RESULT
    return result if isinstance(result, str) and result.strip() else NO_RESULT


def _openai_response_is_empty(response: Any) -> bool:
    output_text = str(getattr(response, "output_text", "") or "").strip()
    output = getattr(response, "output", None) or []
    has_function_call = any(
        getattr(item, "type", None) == "function_call" for item in output
    )
    return not output_text and not has_function_call


@_no_result_on_error
def openaiTools(
    prompt: str,
    selected_mcp: Sequence[Callable[..., Any]],
    *,
    config: Mapping[str, Any] | None = None,
    client: Any = None,
    model: str | None = None,
    instructions: str | None = None,
    max_rounds: int | None = None,
    max_output_tokens: int | None = None,
    **response_options: Any,
) -> str | None:
    """Run a prompt with selected MCP function objects and return final text.

    Custom function calls are executed locally until the model produces text or
    ``max_rounds`` is reached. Native tools such as ``webSearch`` are executed
    by the Responses API.
    """

    prompt = _require_text(prompt, "prompt")
    selected = list(selected_mcp)
    if not selected:
        raise ValueError("selected_mcp must contain at least one MCP function")

    valid_names = set(FUNCTION_TOOL_DEFINITIONS) | {"webSearch"}
    names: list[str] = []
    for function in selected:
        name = getattr(function, "__name__", None)
        if not callable(function) or name not in valid_names:
            raise ValueError(
                "selected_mcp must contain threads, realPing, googleMaps, or webSearch"
            )
        if name not in names:
            names.append(name)

    reserved = {
        "input",
        "instructions",
        "max_output_tokens",
        "model",
        "previous_response_id",
        "tools",
    }
    conflicts = sorted(reserved.intersection(response_options))
    if conflicts:
        raise ValueError(
            f"Pass {', '.join(conflicts)} through the named openaiTools arguments"
        )

    runtime = _runtime_config(config)
    output_mode = _output_mode(runtime)
    selected_model = _require_text(
        model or _config_value(runtime, "openai", "model"),
        "model",
    )
    round_limit = _positive_integer(
        max_rounds
        if max_rounds is not None
        else _config_value(runtime, "openai", "max_rounds"),
        "openai.max_rounds",
    )
    output_limit = _positive_integer(
        max_output_tokens
        if max_output_tokens is not None
        else _config_value(runtime, "openai", "max_output_tokens"),
        "openai.max_output_tokens",
    )
    tool_output_limit = _positive_integer(
        _config_value(runtime, "openai", "max_tool_output_chars"),
        "openai.max_tool_output_chars",
    )
    empty_result_retries = _empty_result_retries(runtime)
    default_prompt = _config_value(runtime, "openai", "default_prompt")
    if default_prompt is not None and not isinstance(default_prompt, str):
        raise MCPConfigurationError("openai.default_prompt must be a string")
    if instructions is not None:
        instructions = _require_text(instructions, "instructions")
    instruction_parts = [
        part.strip()
        for part in (default_prompt, instructions)
        if isinstance(part, str) and part.strip()
    ]
    combined_instructions = "\n\n".join(instruction_parts)

    if client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise MCPConfigurationError(
                "Install the OpenAI client from backend/requirements.txt before calling openaiTools"
            ) from exc
        client = OpenAI()

    tools = get_mcp_tools(names, config=runtime)
    common_request: dict[str, Any] = {
        "model": selected_model,
        "tools": tools,
        "max_output_tokens": output_limit,
        **response_options,
    }
    if combined_instructions:
        common_request["instructions"] = combined_instructions

    response = _retry_result(
        lambda: client.responses.create(input=prompt, **common_request),
        _openai_response_is_empty,
        empty_result_retries,
    )
    completed_rounds = 0
    # Tool functions must return their output to OpenAI even when the user's
    # final output mode is ``print``.
    tool_runtime = _deep_merge(runtime, {"output": {"mode": "return"}})

    while True:
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            output_text = str(response.output_text or "").strip()
            if not output_text:
                raise MCPError("OpenAI returned neither final text nor a custom function call")
            return _deliver_text(output_text, output_mode)

        if completed_rounds >= round_limit:
            raise MCPError(f"OpenAI exceeded the configured limit of {round_limit} tool rounds")
        completed_rounds += 1

        tool_outputs: list[dict[str, Any]] = []
        for call in calls:
            tool_result = _execute_function_call(call, tool_runtime)
            if tool_result == NO_RESULT:
                return _deliver_text(NO_RESULT, output_mode)
            if len(tool_result) > tool_output_limit:
                tool_result = json.dumps(
                    {"truncated": True, "preview": tool_result[:tool_output_limit]},
                    ensure_ascii=False,
                )
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": tool_result,
                }
            )

        previous_response_id = response.id
        response = _retry_result(
            lambda: client.responses.create(
                input=tool_outputs,
                previous_response_id=previous_response_id,
                **common_request,
            ),
            _openai_response_is_empty,
            empty_result_retries,
        )


__all__ = [
    "FUNCTION_TOOL_DEFINITIONS",
    "MCP_TOOL_FUNCTIONS",
    "McpServer",
    "MCPConfigurationError",
    "MCPError",
    "MCPRequestError",
    "PreparedTools",
    "REALPING_ENDPOINTS",
    "ToolRoute",
    "apify_threads_post",
    "connect",
    "error_text",
    "get_mcp_tools",
    "googleMaps",
    "invoke_tool",
    "load_config",
    "maps",
    "mcp_config",
    "openaiTools",
    "realPing",
    "realping",
    "redact",
    "register_tools",
    "render_result",
    "threads",
    "webSearch",
]
