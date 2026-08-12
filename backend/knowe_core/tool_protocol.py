"""Typed decoding for tool calls that providers incorrectly place in text content.

This module is deliberately a *protocol adapter*, not a chat-text scrubber.

OpenAI-compatible providers are supposed to put function calls in ``tool_calls``.
Some providers/models instead serialize a control frame into ``content`` (XML, JSON,
or a function expression).  Treating that content as public prose and trying to erase
known spellings later is unsafe: streaming makes the first emitted bytes irreversible,
and every new serialization variant creates another hole.

The adapter works on the complete provider turn, before any content is committed to the
public channel.  It accepts a small set of real grammars, validates names against the
active tool schemas, and returns one of three typed outcomes:

``plain``
    Ordinary assistant text.  The caller may publish it.
``tool_calls``
    A control frame was decoded into canonical OpenAI tool-call objects.  The original
    text must stay private.
``invalid``
    The turn structurally looks like a tool control frame but is malformed or uses an
    unsupported encoding.  It must fail closed, never fall through to chat text.

Unknown XML/JSON wrapper names do not require code changes: tool identity comes from the
active schema, not from a growing list of tags such as ``tool_calls`` / ``invoke``.
"""

from __future__ import annotations

import ast
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Literal


ProtocolKind = Literal["plain", "tool_calls", "invalid"]
_MAX_PROTOCOL_CHARS = 1_000_000


@dataclass(frozen=True)
class TextToolProtocol:
    kind: ProtocolKind
    tool_calls: tuple[dict[str, Any], ...] = ()
    reason: str = ""
    encoding: str = ""


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    parameters: dict[str, Any]


def _tool_specs(schemas: list[dict[str, Any]] | None) -> dict[str, _ToolSpec]:
    specs: dict[str, _ToolSpec] = {}
    for item in schemas or []:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        parameters = fn.get("parameters")
        specs[name] = _ToolSpec(
            name=name,
            parameters=parameters if isinstance(parameters, dict) else {},
        )
    return specs


def _strip_outer_fence(value: str) -> str:
    """Remove one complete Markdown fence without caring about its language label."""
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped
    first_break = stripped.find("\n")
    if first_break < 0:
        return stripped
    tail = stripped.rstrip()
    if not tail.endswith("```"):
        return stripped
    return tail[first_break + 1:-3].strip()


def _remove_trailing_commas(value: str) -> str:
    """Remove only structural trailing commas, never comma-like text in strings."""
    out: list[str] = []
    index = 0
    quote = ""
    escaped = False
    length = len(value)
    while index < length:
        char = value[index]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {"\"", "'"}:
            quote = char
            out.append(char)
            index += 1
            continue
        if char == ",":
            cursor = index + 1
            while cursor < length and value[cursor].isspace():
                cursor += 1
            if cursor < length and value[cursor] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def decode_json_compat(value: Any) -> tuple[Any | None, bool]:
    """Decode provider JSON with a small, deterministic compatibility envelope.

    Provider tool arguments are frequently almost-JSON: an otherwise valid object may
    contain a raw newline, a trailing comma, or Python-style single quotes.  Rejecting
    those turns immediately loses useful work, while guessing missing fields would be
    unsafe.  This helper therefore performs only syntax-preserving repairs and never
    invents keys or values.

    The boolean return value records whether a non-strict/compatibility decoder was
    required.  ``None`` means no supported decoder could prove the payload's value.
    """
    if not isinstance(value, str):
        return value, False

    raw = _strip_outer_fence(value)
    if not raw or len(raw) > _MAX_PROTOCOL_CHARS:
        return None, False

    candidates: list[tuple[str, bool]] = [(raw, False)]
    without_trailing_commas = _remove_trailing_commas(raw)
    if without_trailing_commas != raw:
        candidates.append((without_trailing_commas, True))

    for candidate, repaired in candidates:
        for strict in (True, False):
            try:
                return json.loads(candidate, strict=strict), repaired or not strict
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

    # ``literal_eval`` is data-only (unlike eval) and covers single-quoted mappings and
    # Python booleans/null-like values.  Restrict it to complete structured literals.
    if raw[:1] in {"{", "["} and raw[-1:] in {"}", "]"}:
        for candidate, repaired in candidates:
            try:
                parsed = ast.literal_eval(candidate)
            except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
                continue
            if isinstance(parsed, (dict, list, str, int, float, bool)) or parsed is None:
                return parsed, True
    return None, False


def decode_json_object_arguments(value: Any) -> tuple[dict[str, Any] | None, bool]:
    """Return a proven argument object and whether syntax repair was needed."""
    if isinstance(value, dict):
        return dict(value), False
    parsed, repaired = decode_json_compat(value)
    if isinstance(parsed, dict):
        return dict(parsed), repaired
    return None, repaired


def _contains_tool_token(value: str, names: set[str]) -> bool:
    """Exact identifier-token lookup using the active schema, not protocol keywords."""
    lower = value.lower()
    for name in names:
        needle = name.lower()
        cursor = 0
        while True:
            index = lower.find(needle, cursor)
            if index < 0:
                break
            before = lower[index - 1] if index else ""
            after_index = index + len(needle)
            after = lower[after_index] if after_index < len(lower) else ""
            if (not before or not (before.isalnum() or before == "_")) and (
                not after or not (after.isalnum() or after == "_")
            ):
                return True
            cursor = index + 1
    return False


def _coerce_scalar(raw: Any, schema: dict[str, Any] | None) -> Any:
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    declared = (schema or {}).get("type") if isinstance(schema, dict) else None
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), None)

    if declared == "string" or declared is None:
        return raw
    if declared == "integer":
        try:
            return int(text)
        except ValueError:
            return raw
    if declared == "number":
        try:
            return float(text)
        except ValueError:
            return raw
    if declared == "boolean":
        lowered = text.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        return raw
    if declared in {"array", "object"}:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return raw
        if declared == "array" and isinstance(parsed, list):
            return parsed
        if declared == "object" and isinstance(parsed, dict):
            return parsed
    return raw


def _coerce_args(args: dict[str, Any], spec: _ToolSpec) -> dict[str, Any]:
    properties = spec.parameters.get("properties")
    prop_map = properties if isinstance(properties, dict) else {}
    return {
        str(key): _coerce_scalar(value, prop_map.get(str(key)))
        for key, value in args.items()
    }


def _canonical_call(name: str, args: dict[str, Any], index: int) -> dict[str, Any]:
    arguments = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(
        f"{index}\0{name}\0{arguments}".encode("utf-8", "replace")
    ).hexdigest()[:16]
    return {
        "id": f"text_call_{digest}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _name_from_mapping(value: dict[str, Any], names: set[str]) -> str | None:
    candidates: list[Any] = []
    for key in ("name", "tool", "tool_name"):
        candidates.append(value.get(key))
    function = value.get("function")
    if isinstance(function, str):
        candidates.append(function)
    elif isinstance(function, dict):
        candidates.extend((function.get("name"), function.get("tool")))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip() in names:
            return candidate.strip()
    return None


def _args_from_mapping(value: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Return decoded arguments and whether an argument field was actually present.

    A JSON document that merely describes a tool schema (for example
    ``{"name": "web_search", "description": ...}``) is not an invocation.  Requiring
    an explicit argument-bearing field prevents the adapter from turning documentation
    into an executable call.  Zero-argument calls still serialize ``arguments: {}``.
    """
    function = value.get("function")
    sources: list[tuple[bool, Any]] = []
    keys = ("arguments", "args", "parameters", "input", "payload")
    if isinstance(function, dict):
        sources.extend((key in function, function.get(key)) for key in keys)
    sources.extend((key in value, value.get(key)) for key in keys)
    for present, source in sources:
        if not present:
            continue
        if isinstance(source, dict):
            return dict(source), True
        if isinstance(source, str):
            parsed, _repaired = decode_json_object_arguments(source)
            return parsed, True
        if source is None:
            return None, True
        return None, True
    return {}, False


def _json_calls(parsed: Any, specs: dict[str, _ToolSpec]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen_nodes: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        marker = id(node)
        if marker in seen_nodes:
            return
        seen_nodes.add(marker)

        name = _name_from_mapping(node, set(specs))
        if name is not None:
            raw_args, has_arguments = _args_from_mapping(node)
            if has_arguments and raw_args is not None:
                args = _coerce_args(raw_args, specs[name])
                calls.append(_canonical_call(name, args, len(calls)))
            # A recognized identity node is one call frame. If its arguments are absent
            # or malformed, do not search its descendants for an accidental substitute;
            # the caller will classify the whole JSON document as invalid.
            return
        for child in node.values():
            if isinstance(child, (dict, list)):
                visit(child)

    visit(parsed)
    return calls


def _json_contains_tool_name(parsed: Any, names: set[str]) -> bool:
    if isinstance(parsed, str):
        return parsed in names
    if isinstance(parsed, list):
        return any(_json_contains_tool_name(item, names) for item in parsed)
    if isinstance(parsed, dict):
        return any(
            _json_contains_tool_name(key, names) or _json_contains_tool_name(value, names)
            for key, value in parsed.items()
        )
    return False


def _json_has_call_shape(parsed: Any) -> bool:
    """Recognize the function-call *data model*, independent of wrapper spellings."""
    if isinstance(parsed, list):
        return any(_json_has_call_shape(item) for item in parsed)
    if not isinstance(parsed, dict):
        return False
    arg_fields = {"arguments", "args", "parameters", "input", "payload"}
    identity_fields = {"name", "tool", "tool_name"}
    keys = {str(key) for key in parsed}
    function = parsed.get("function")
    if isinstance(function, dict):
        fn_keys = {str(key) for key in function}
        if fn_keys & identity_fields and (fn_keys & arg_fields or keys & arg_fields):
            return True
    if keys & identity_fields and keys & arg_fields:
        return True
    return any(_json_has_call_shape(value) for value in parsed.values())


def _embedded_json_call(
    value: str, specs: dict[str, _ToolSpec]
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Find one JSON value embedded in otherwise non-JSON text.

    Returns ``(calls, mixed, call_shaped)``.  Parsing is bounded so hostile prose full
    of braces cannot turn classification into quadratic work.
    """
    decoder = json.JSONDecoder()
    attempts = 0
    for index, char in enumerate(value):
        if char not in "{[":
            continue
        attempts += 1
        if attempts > 128:
            break
        try:
            parsed, end = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        calls = _json_calls(parsed, specs)
        shaped = _json_has_call_shape(parsed)
        if not calls and not shaped:
            continue
        prefix = value[:index].strip()
        suffix = value[index + end:].strip()
        return calls, bool(prefix or suffix), shaped
    return [], False, False


def _local_name(tag: Any) -> str:
    raw = str(tag or "")
    return raw.rsplit("}", 1)[-1] if "}" in raw else raw


def _element_tool_name(element: ET.Element, names: set[str]) -> str | None:
    tag_name = _local_name(element.tag)
    if tag_name in names:
        return tag_name
    for value in element.attrib.values():
        candidate = str(value).strip()
        if candidate in names:
            return candidate
    for child in list(element):
        if list(child):
            continue
        candidate = "".join(child.itertext()).strip()
        if candidate in names:
            return candidate
    return None


def _xml_significant_outside_calls(root: ET.Element, call_nodes: set[int]) -> bool:
    """Reject mixed prose + control documents; pure whitespace around calls is fine."""
    def walk(element: ET.Element, inside_call: bool = False) -> bool:
        now_inside = inside_call or id(element) in call_nodes
        if not now_inside and (element.text or "").strip():
            return True
        for child in list(element):
            if walk(child, now_inside):
                return True
            if not now_inside and (child.tail or "").strip():
                return True
        return False

    return walk(root)


def _xml_args(element: ET.Element, tool_name: str, spec: _ToolSpec) -> dict[str, Any]:
    args: dict[str, Any] = {}

    # Non-identity attributes on the call element are legitimate compact arguments.
    for key, value in element.attrib.items():
        if str(value).strip() == tool_name:
            continue
        args[_local_name(key)] = value

    for child in element.iter():
        if child is element:
            continue
        value = "".join(child.itertext()).strip()
        # Identity leaves such as <name>web_search</name> describe the call itself,
        # not an argument named ``name``.
        if value == tool_name and not list(child):
            continue
        key: str | None = None
        for _attr_key, attr_value in child.attrib.items():
            candidate = str(attr_value).strip()
            if candidate and candidate != tool_name:
                key = candidate
                break
        if key is None and not list(child):
            tag = _local_name(child.tag)
            if tag and tag not in {"root"}:
                key = tag
        if not key:
            continue
        args[key] = value

    return _coerce_args(args, spec)


def _parse_xml_document(value: str) -> ET.Element:
    # ElementTree does not resolve external entities, but rejecting declarations also
    # prevents entity-expansion payloads and keeps this adapter data-only.
    upper = value.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("XML declarations with entities are not allowed")
    try:
        return ET.fromstring(value)
    except ET.ParseError:
        # Providers often emit several sibling call elements.  A synthetic root turns
        # that fragment into one document without depending on wrapper tag names.
        return ET.fromstring(f"<root>{value}</root>")


def _xml_calls(root: ET.Element, specs: dict[str, _ToolSpec]) -> tuple[list[dict[str, Any]], bool]:
    names = set(specs)
    candidates: list[tuple[ET.Element, str]] = []
    for element in root.iter():
        name = _element_tool_name(element, names)
        if name is not None:
            candidates.append((element, name))

    # If a tool is represented by its own tag, descendants can echo the same name in a
    # leaf. Keep the outermost candidate for each branch so one call is not duplicated.
    # A parent map makes this linear rather than repeatedly walking every subtree.
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    candidate_ids = {id(element) for element, _ in candidates}
    filtered: list[tuple[ET.Element, str]] = []
    for element, name in candidates:
        parent = parent_by_id.get(id(element))
        nested_under_candidate = False
        while parent is not None:
            if id(parent) in candidate_ids:
                nested_under_candidate = True
                break
            parent = parent_by_id.get(id(parent))
        if not nested_under_candidate:
            filtered.append((element, name))

    if not filtered:
        return [], False
    if _xml_significant_outside_calls(root, {id(element) for element, _ in filtered}):
        return [], True

    calls = [
        _canonical_call(name, _xml_args(element, name, specs[name]), index)
        for index, (element, name) in enumerate(filtered)
    ]
    return calls, False


def _ast_call(value: str, specs: dict[str, _ToolSpec]) -> tuple[list[dict[str, Any]], bool]:
    try:
        parsed = ast.parse(value, mode="eval")
    except SyntaxError:
        return [], False
    expr = parsed.body
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        return [], False
    name = expr.func.id
    if name not in specs:
        return [], False

    args: dict[str, Any] = {}
    malformed = False
    if expr.args:
        if len(expr.args) == 1:
            try:
                positional = ast.literal_eval(expr.args[0])
            except (ValueError, TypeError, SyntaxError):
                malformed = True
            else:
                if isinstance(positional, dict):
                    args.update(positional)
                else:
                    malformed = True
        else:
            malformed = True
    for keyword in expr.keywords:
        if keyword.arg is None:
            malformed = True
            continue
        try:
            args[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError, SyntaxError):
            malformed = True
    if malformed:
        return [], True
    coerced = _coerce_args(args, specs[name])
    return [_canonical_call(name, coerced, 0)], False


def decode_text_tool_protocol(
    text: str,
    tool_schemas: list[dict[str, Any]] | None,
    *,
    finish_reason: str | None = None,
) -> TextToolProtocol:
    """Classify one *complete* assistant content string.

    ``finish_reason`` is part of the provider protocol.  A provider that says the turn
    ended for tool calls but supplies no decodable calls is an invalid control frame,
    never ordinary chat text.
    """
    specs = _tool_specs(tool_schemas)
    if not specs:
        if (finish_reason or "").lower() in {"tool_calls", "function_call"}:
            return TextToolProtocol(
                "invalid", reason="provider marked a tool-call turn but no tool schema is active"
            )
        return TextToolProtocol("plain")

    raw = _strip_outer_fence(text or "")
    if not raw:
        if (finish_reason or "").lower() in {"tool_calls", "function_call"}:
            return TextToolProtocol(
                "invalid", reason="provider marked a tool-call turn but supplied no call"
            )
        return TextToolProtocol("plain")
    names = set(specs)
    if len(raw) > _MAX_PROTOCOL_CHARS:
        structured = raw.lstrip()[:1] in {"<", "{", "["}
        if (
            (finish_reason or "").lower() in {"tool_calls", "function_call"}
            or structured
            or _contains_tool_token(raw, names)
        ):
            return TextToolProtocol("invalid", reason="tool protocol frame is too large")
        return TextToolProtocol("plain")

    # Preserve normal XML entities inside an actual XML document. Only decode an
    # entity-escaped control frame when there are no literal tags yet; unconditional
    # html.unescape() would turn a valid ``&amp;`` argument into malformed raw ``&``.
    decoded_for_structure = raw.strip()
    if "<" not in decoded_for_structure and any(
        token in decoded_for_structure.lower() for token in ("&lt;", "&#60;", "&#x3c;")
    ):
        decoded_for_structure = html.unescape(decoded_for_structure).strip()

    # JSON is parsed as data, then recursively normalized to canonical calls.  If a
    # provider prefixes/suffixes the JSON with prose, it is a mixed control/public turn
    # and fails closed instead of publishing either half.
    if decoded_for_structure[:1] in {"{", "["}:
        parsed, repaired = decode_json_compat(decoded_for_structure)
        if parsed is None:
            if _contains_tool_token(decoded_for_structure, names):
                return TextToolProtocol("invalid", reason="malformed JSON tool protocol", encoding="json")
        else:
            calls = _json_calls(parsed, specs)
            if calls:
                return TextToolProtocol(
                    "tool_calls",
                    tuple(calls),
                    encoding="json_compat" if repaired else "json",
                )
            if _json_contains_tool_name(parsed, names) or _json_has_call_shape(parsed):
                return TextToolProtocol(
                    "invalid",
                    reason="JSON contains a non-decodable function-call frame",
                    encoding="json_compat" if repaired else "json",
                )
    elif "{" in decoded_for_structure or "[" in decoded_for_structure:
        calls, mixed, shaped = _embedded_json_call(decoded_for_structure, specs)
        if calls or shaped:
            return TextToolProtocol(
                "invalid" if mixed or not calls else "tool_calls",
                tuple(calls) if calls and not mixed else (),
                reason="tool-call JSON is mixed with public text" if mixed else (
                    "JSON contains a non-decodable function-call frame" if not calls else ""
                ),
                encoding="json",
            )

    # XML wrapper/tag names are intentionally irrelevant.  Parsing is attempted for an
    # XML fragment anywhere in the turn, not only when the first byte is '<'.  This
    # catches provider preambles followed by a call and classifies the mixed turn as an
    # error rather than streaming the preamble and protocol separately.
    if "<" in decoded_for_structure:
        try:
            root = _parse_xml_document(decoded_for_structure)
        except (ET.ParseError, ValueError):
            if _contains_tool_token(decoded_for_structure, names):
                return TextToolProtocol("invalid", reason="malformed XML tool protocol", encoding="xml")
        else:
            calls, mixed = _xml_calls(root, specs)
            if calls:
                return TextToolProtocol("tool_calls", tuple(calls), encoding="xml")
            if mixed or _contains_tool_token(decoded_for_structure, names):
                return TextToolProtocol(
                    "invalid", reason="XML contains a mixed or non-decodable function-call frame", encoding="xml"
                )

    # A whole-expression function call is another real grammar.  ``ast`` validates the
    # shape; no regex tries to guess it from partial prose.
    calls, malformed = _ast_call(decoded_for_structure, specs)
    if calls:
        return TextToolProtocol("tool_calls", tuple(calls), encoding="function")
    if malformed:
        return TextToolProtocol("invalid", reason="malformed function-call protocol", encoding="function")

    if (finish_reason or "").lower() in {"tool_calls", "function_call"}:
        return TextToolProtocol(
            "invalid", reason="provider marked a tool-call turn but its payload is unsupported"
        )

    # Conservative fail-closed rule for future structured encodings: a whole structured
    # document that carries an active tool identifier is control-plane data, even when
    # this version cannot execute that encoding yet.  It is not published as prose.
    if decoded_for_structure[:1] in {"<", "{", "["} and _contains_tool_token(
        decoded_for_structure, names
    ):
        return TextToolProtocol("invalid", reason="unsupported structured tool protocol")

    return TextToolProtocol("plain")


__all__ = [
    "TextToolProtocol",
    "decode_json_compat",
    "decode_json_object_arguments",
    "decode_text_tool_protocol",
]
