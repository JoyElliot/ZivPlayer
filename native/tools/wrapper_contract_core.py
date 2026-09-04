# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ContractError(RuntimeError):
    pass


TYPE_DESCRIPTORS = {
    "Boolean": "Z",
    "Context": "Landroid/content/Context;",
    "Double": "D",
    "DoubleArray": "[D",
    "Int": "I",
    "IntArray": "[I",
    "Long": "J",
    "LongArray": "[J",
    "String": "Ljava/lang/String;",
    "Surface": "Landroid/view/Surface;",
    "Unit": "V",
    "Array<String>": "[Ljava/lang/String;",
    "Array<String?>": "[Ljava/lang/String;",
}

KOTLIN_METHOD_PATTERN = re.compile(
    r"\bprivate\s+external\s+fun\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*"
    r"\((?P<parameters>.*?)\)\s*(?::\s*(?P<result>[A-Za-z0-9_?<>]+))?",
    re.DOTALL,
)
CPP_METHOD_PATTERN = re.compile(
    r'\{\s*const_cast\s*<\s*char\s*\*\s*>\s*\(\s*"(?P<name>[^"\\]+)"\s*\)\s*,'
    r'\s*const_cast\s*<\s*char\s*\*\s*>\s*\(\s*"(?P<descriptor>[^"\\]+)"\s*\)\s*,'
    r"\s*reinterpret_cast\s*<\s*void\s*\*\s*>\s*\(\s*"
    r"(?P<function>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\}",
    re.DOTALL,
)

WIRE_PREFIX = r"(?:INTEGER|LONG|DOUBLE|STRING|PRESENCE)_[A-Z0-9_]+"
CPP_ENUMS = {
    "integerFields": "IntegerField",
    "longFields": "LongField",
    "doubleFields": "DoubleField",
    "stringFields": "StringField",
    "presenceFlags": "PresenceFlag",
}
EXPECTED_METHOD_COUNT = 16
EXPECTED_BINDINGS_CLASS = (
    "io/github/joyelliot/zivplayer/platform/libmpv/MpvNativeBindings"
)
EXPECTED_LIBRARY_NAME = "zivplayer_mpv"
EXPECTED_METHODS = (
    ("nativeCreate", "(Landroid/content/Context;)J", "GuardedNativeCreate"),
    (
        "nativeSetOptionString",
        "(JLjava/lang/String;Ljava/lang/String;)I",
        "GuardedNativeSetOptionString",
    ),
    ("nativeInitialize", "(J)I", "GuardedNativeInitialize"),
    ("nativeCommand", "(J[Ljava/lang/String;)I", "GuardedNativeCommand"),
    (
        "nativeGetPropertyDouble",
        "(JLjava/lang/String;[D)I",
        "GuardedNativeGetPropertyDouble",
    ),
    (
        "nativeGetPropertyBoolean",
        "(JLjava/lang/String;[I)I",
        "GuardedNativeGetPropertyBoolean",
    ),
    (
        "nativeSetPropertyDouble",
        "(JLjava/lang/String;D)I",
        "GuardedNativeSetPropertyDouble",
    ),
    (
        "nativeSetPropertyBoolean",
        "(JLjava/lang/String;Z)I",
        "GuardedNativeSetPropertyBoolean",
    ),
    (
        "nativeSetPropertyString",
        "(JLjava/lang/String;Ljava/lang/String;)I",
        "GuardedNativeSetPropertyString",
    ),
    (
        "nativeObserveProperty",
        "(JLjava/lang/String;IJ)I",
        "GuardedNativeObserveProperty",
    ),
    ("nativeUnobserveProperty", "(JJ)I", "GuardedNativeUnobserveProperty"),
    (
        "nativeWaitEvent",
        "(JD[I[J[D[Ljava/lang/String;)I",
        "GuardedNativeWaitEvent",
    ),
    ("nativeWakeup", "(J)I", "GuardedNativeWakeup"),
    (
        "nativeAttachSurface",
        "(JLandroid/view/Surface;)I",
        "GuardedNativeAttachSurface",
    ),
    ("nativeDetachSurface", "(J)I", "GuardedNativeDetachSurface"),
    ("nativeTerminateDestroy", "(J)I", "GuardedNativeTerminateDestroy"),
)
EXPECTED_WIRE_COUNTS = {
    "integerFieldCount": 8,
    "longFieldCount": 4,
    "doubleFieldCount": 1,
    "stringFieldCount": 2,
}
EXPECTED_PRESENCE_VALUES = {1, 2, 4, 8, 16}
EXPECTED_WIRE_ENTRIES = {
    "integerFields": (
        ("INTEGER_EVENT_ID", "kEventId", 0),
        ("INTEGER_EVENT_ERROR", "kEventError", 1),
        ("INTEGER_PROPERTY_FORMAT", "kPropertyFormat", 2),
        ("INTEGER_END_REASON", "kEndReason", 3),
        ("INTEGER_END_ERROR", "kEndError", 4),
        ("INTEGER_INSERT_COUNT", "kInsertCount", 5),
        ("INTEGER_PRESENCE_FLAGS", "kPresenceFlags", 6),
        ("INTEGER_PROPERTY_FLAG", "kPropertyFlag", 7),
    ),
    "longFields": (
        ("LONG_REPLY_USERDATA", "kReplyUserdata", 0),
        ("LONG_PLAYLIST_ENTRY_ID", "kPlaylistEntryId", 1),
        ("LONG_PLAYLIST_INSERT_ID", "kPlaylistInsertId", 2),
        ("LONG_PROPERTY_INT64", "kPropertyInt64", 3),
    ),
    "doubleFields": (("DOUBLE_PROPERTY_VALUE", "kPropertyDouble", 0),),
    "stringFields": (
        ("STRING_PROPERTY_NAME", "kPropertyName", 0),
        ("STRING_PROPERTY_VALUE", "kPropertyString", 1),
    ),
    "presenceFlags": (
        ("PRESENCE_REPLY_USERDATA", "kHasReplyUserdata", 1),
        ("PRESENCE_PLAYLIST_ENTRY_ID", "kHasPlaylistEntryId", 2),
        ("PRESENCE_END_REASON", "kHasEndReason", 4),
        ("PRESENCE_PLAYLIST_INSERT_ID", "kHasPlaylistInsertId", 8),
        ("PRESENCE_PROPERTY_VALUE", "kHasPropertyValue", 16),
    ),
}


@dataclass(frozen=True)
class KotlinContract:
    methods: list[tuple[str, str]]
    wire_constants: dict[str, int]


@dataclass(frozen=True)
class CppContract:
    methods: list[tuple[str, str, str]]
    wire_constants: dict[str, int]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as failure:
        raise ContractError(f"cannot read {path}: {failure}") from failure


def _load_contract_text(source: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as failure:
        raise ContractError(f"invalid contract TOML: {failure}") from failure
    if data.get("schemaVersion") != 1 or data.get("kind") != "zivplayer-libmpv-jni-contract":
        raise ContractError("unsupported wrapper contract schema")
    methods = data.get("methods")
    if not isinstance(methods, list) or not methods:
        raise ContractError("the wrapper contract has no native methods")
    if data.get("methodCount") != EXPECTED_METHOD_COUNT or len(methods) != EXPECTED_METHOD_COUNT:
        raise ContractError(
            f"the wrapper contract must declare exactly {EXPECTED_METHOD_COUNT} native methods"
        )
    return data


def _load_contract(path: Path) -> dict[str, Any]:
    return _load_contract_text(_read_text(path))


def _blank(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _mask_source(source: str, *, strings: bool) -> str:
    """Mask C++/Kotlin comments and optionally literals, preserving offsets."""
    chars = list(source)
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = length if end < 0 else end
            _blank(chars, index, end)
            index = end
            continue
        if source.startswith("/*", index):
            start = index
            index += 2
            depth = 1
            while index < length and depth > 0:
                if source.startswith("/*", index):
                    depth += 1
                    index += 2
                elif source.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth != 0:
                raise ContractError("an unterminated block comment was found")
            _blank(chars, start, index)
            continue
        if source.startswith('"""', index):
            start = index
            end = source.find('"""', index + 3)
            if end < 0:
                raise ContractError("an unterminated triple-quoted string was found")
            index = end + 3
            if strings:
                _blank(chars, start, index)
            continue
        if source[index] in {'"', "'"}:
            quote = source[index]
            start = index
            index += 1
            closed = False
            while index < length:
                if source[index] == "\\":
                    index = min(length, index + 2)
                    continue
                index += 1
                if source[index - 1] == quote:
                    closed = True
                    break
            if not closed:
                raise ContractError("an unterminated quoted literal was found")
            if strings:
                _blank(chars, start, index)
            continue
        index += 1
    return "".join(chars)


def _splice_cpp_lines(source: str) -> str:
    """Apply C++ translation phase 2 before recognizing line comments."""
    return re.sub(r"\\\r?\n", "", source)


def _matching_delimiter(source: str, opening: int, left: str, right: str) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == left:
            depth += 1
        elif source[index] == right:
            depth -= 1
            if depth == 0:
                return index
    raise ContractError(f"an expected {left}{right} block has no closing delimiter")


def _brace_depth(source: str, start: int, position: int) -> int:
    return source.count("{", start, position) - source.count("}", start, position)


def _unique_match(pattern: re.Pattern[str], source: str, description: str) -> re.Match[str]:
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ContractError(f"expected exactly one {description}, found {len(matches)}")
    return matches[0]


def _descriptor(type_name: str) -> str:
    normalized = re.sub(r"\s+", "", type_name)
    try:
        return TYPE_DESCRIPTORS[normalized]
    except KeyError as failure:
        raise ContractError(f"unsupported Kotlin JNI type: {normalized}") from failure


def _kotlin_method(match: re.Match[str]) -> tuple[str, str]:
    parameters = match.group("parameters").strip()
    arguments: list[str] = []
    if parameters:
        for parameter in parameters.split(","):
            parameter = parameter.strip()
            if not parameter:
                continue
            if ":" not in parameter:
                raise ContractError(f"malformed Kotlin parameter: {parameter!r}")
            _name, type_name = parameter.split(":", 1)
            arguments.append(_descriptor(type_name))
    result = _descriptor(match.group("result") or "Unit")
    return match.group("name"), f"({''.join(arguments)}){result}"


def _simple_kotlin_integer(source: str, start: int, name: str) -> int:
    declaration = re.compile(
        rf"(?:internal\s+)?const\s+val\s+{re.escape(name)}\s*=\s*"
        r"(?P<base>\d+)(?:\s+shl\s+(?P<shift>\d+))?\b"
    ).match(source, start)
    if declaration is None:
        raise ContractError(f"Kotlin wire constant {name} must use a simple integer literal")
    line_end = source.find("\n", declaration.end())
    line_end = len(source) if line_end < 0 else line_end
    if source[declaration.end() : line_end].strip().rstrip(";").strip():
        raise ContractError(f"Kotlin wire constant {name} has an unsupported initializer")
    value = int(declaration.group("base"))
    shift = declaration.group("shift")
    return value << int(shift) if shift is not None else value


def _kotlin_contract(source: str, bindings_class: str, library_name: str) -> KotlinContract:
    clean = _mask_source(source, strings=False)
    masked = _mask_source(source, strings=True)
    if "/" not in bindings_class:
        raise ContractError("bindingsClass must be a slash-separated binary name")
    expected_package, expected_object = bindings_class.rsplit("/", 1)
    expected_package = expected_package.replace("/", ".")

    packages = re.findall(r"(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$", masked)
    if packages != [expected_package]:
        raise ContractError(
            f"Kotlin package drifted: expected {expected_package!r}, got {packages!r}"
        )
    imports = set(
        re.findall(r"(?m)^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)\s*$", masked)
    )
    if not {"android.content.Context", "android.view.Surface"}.issubset(imports):
        raise ContractError("the Kotlin bindings lost the exact Android Context/Surface imports")

    object_pattern = re.compile(
        rf"\binternal\s+object\s+{re.escape(expected_object)}\s*:\s*MpvNativeApi\s*\{{"
    )
    object_match = _unique_match(object_pattern, masked, "Kotlin bindings object")
    if _brace_depth(masked, 0, object_match.start()) != 0:
        raise ContractError("the Kotlin bindings object must be a top-level declaration")
    opening = masked.find("{", object_match.start(), object_match.end())
    closing = _matching_delimiter(masked, opening, "{", "}")

    if re.search(r"\bJvm(?:Static|Name)\b", masked):
        raise ContractError("the Kotlin bindings must not rewrite native JVM member identity")

    method_matches = list(KOTLIN_METHOD_PATTERN.finditer(masked))
    external_markers = list(re.finditer(r"\bexternal\s+fun\b", masked))
    if len(method_matches) != len(external_markers):
        raise ContractError("every Kotlin external method must be a private binding declaration")
    methods: list[tuple[str, str]] = []
    for match in method_matches:
        if not (opening < match.start() < closing):
            raise ContractError("a Kotlin native method exists outside the bindings object")
        if _brace_depth(masked, opening + 1, match.start()) != 0:
            raise ContractError("Kotlin native methods must be direct bindings-object members")
        methods.append(_kotlin_method(match))

    library_candidates = list(
        re.finditer(r"\b(?:private\s+)?const\s+val\s+LIBRARY_NAME\b", masked)
    )
    if len(library_candidates) != 1:
        raise ContractError("expected exactly one Kotlin LIBRARY_NAME declaration")
    library_candidate = library_candidates[0]
    if not (opening < library_candidate.start() < closing) or _brace_depth(
        masked, opening + 1, library_candidate.start()
    ) != 0:
        raise ContractError("Kotlin LIBRARY_NAME must be a direct bindings-object member")
    library_declaration = re.compile(
        r'private\s+const\s+val\s+LIBRARY_NAME\s*=\s*"(?P<value>[^"\\]*)"'
    ).match(clean, library_candidate.start())
    if library_declaration is None or library_declaration.group("value") != library_name:
        raise ContractError("the Kotlin library name does not match the contract")
    object_masked = masked[opening + 1 : closing]
    if len(re.findall(r"\bSystem\s*\.\s*loadLibrary\s*\(\s*LIBRARY_NAME\s*\)", object_masked)) != 1:
        raise ContractError("the Kotlin bindings must load LIBRARY_NAME exactly once")
    if len(re.findall(r"\bSystem\b", masked)) != 1:
        raise ContractError("Kotlin System is shadowed or used outside the library load")
    if len(re.findall(r"\bLIBRARY_NAME\b", masked)) != 2:
        raise ContractError("Kotlin LIBRARY_NAME is shadowed or used outside the library load")
    for name, _descriptor_value in methods:
        if len(re.findall(rf"\b{re.escape(name)}\b", masked)) != 2:
            raise ContractError(f"Kotlin {name} is shadowed or not called exactly once")

    constant_candidates = list(
        re.finditer(rf"\binternal\s+const\s+val\s+(?P<name>{WIRE_PREFIX})\b", masked)
    )
    constants: dict[str, int] = {}
    for candidate in constant_candidates:
        name = candidate.group("name")
        if not (opening < candidate.start() < closing) or _brace_depth(
            masked, opening + 1, candidate.start()
        ) != 0:
            raise ContractError(f"Kotlin wire constant {name} is outside the bindings object")
        if name in constants:
            raise ContractError(f"duplicate Kotlin wire constant {name}")
        constants[name] = _simple_kotlin_integer(masked, candidate.start(), name)
    return KotlinContract(methods=methods, wire_constants=constants)


def _jni_descriptor_parts(descriptor: str) -> tuple[list[str], str]:
    if not descriptor.startswith("(") or ")" not in descriptor:
        raise ContractError(f"malformed JNI descriptor: {descriptor}")
    closing = descriptor.index(")")
    arguments_source = descriptor[1:closing]
    result_source = descriptor[closing + 1 :]

    def take_one(source: str, index: int, *, allow_void: bool) -> tuple[str, int]:
        if index >= len(source):
            raise ContractError(f"malformed JNI descriptor: {descriptor}")
        marker = source[index]
        if marker in "ZIJD" or (allow_void and marker == "V"):
            return marker, index + 1
        if marker == "L":
            end = source.find(";", index + 1)
            if end < 0:
                raise ContractError(f"malformed JNI descriptor: {descriptor}")
            return source[index : end + 1], end + 1
        if marker == "[":
            nested, end = take_one(source, index + 1, allow_void=False)
            return "[" + nested, end
        raise ContractError(f"unsupported JNI descriptor component in {descriptor}")

    arguments: list[str] = []
    index = 0
    while index < len(arguments_source):
        value, index = take_one(arguments_source, index, allow_void=False)
        arguments.append(value)
    result, result_end = take_one(result_source, 0, allow_void=True)
    if result_end != len(result_source):
        raise ContractError(f"malformed JNI result descriptor: {descriptor}")
    return arguments, result


def _jni_cpp_type(descriptor: str, *, result: bool = False) -> str:
    mapping = {
        "Z": "jboolean",
        "I": "jint",
        "J": "jlong",
        "D": "jdouble",
        "V": "void",
        "Ljava/lang/String;": "jstring",
        "Landroid/content/Context;": "jobject",
        "Landroid/view/Surface;": "jobject",
        "[I": "jintArray",
        "[J": "jlongArray",
        "[D": "jdoubleArray",
        "[Ljava/lang/String;": "jobjectArray",
    }
    try:
        value = mapping[descriptor]
    except KeyError as failure:
        raise ContractError(f"unsupported JNI C++ descriptor component: {descriptor}") from failure
    if not result and value == "void":
        raise ContractError("JNI method arguments cannot be void")
    return value


def _cpp_parameter_type(parameter: str) -> str:
    normalized = re.sub(r"\s*\*\s*", "* ", re.sub(r"\s+", " ", parameter.strip()))
    match = re.fullmatch(
        r"(?P<type>JNIEnv\*|jobject|jboolean|jint|jlong|jdouble|jstring|"
        r"jintArray|jlongArray|jdoubleArray|jobjectArray)\s+[A-Za-z_][A-Za-z0-9_]*",
        normalized,
    )
    if match is None:
        raise ContractError(f"unsupported guarded JNI parameter declaration: {parameter!r}")
    return match.group("type")


def _validate_guarded_function(
    clean: str,
    masked: str,
    function: str,
    descriptor: str,
) -> None:
    arguments, result_descriptor = _jni_descriptor_parts(descriptor)
    expected_return = _jni_cpp_type(result_descriptor, result=True)
    expected_parameters = ["JNIEnv*", "jobject"] + [
        _jni_cpp_type(argument) for argument in arguments
    ]
    definition = _unique_match(
        re.compile(rf"\b(?P<result>jint|jlong|void)\s+{re.escape(function)}\s*\("),
        masked,
        f"guarded JNI definition {function}",
    )
    if definition.group("result") != expected_return:
        raise ContractError(f"guarded JNI return type drifted for {function}")
    parameter_open = masked.find("(", definition.start(), definition.end())
    parameter_close = _matching_delimiter(masked, parameter_open, "(", ")")
    actual_parameters = [
        _cpp_parameter_type(parameter)
        for parameter in clean[parameter_open + 1 : parameter_close].split(",")
        if parameter.strip()
    ]
    if actual_parameters != expected_parameters:
        raise ContractError(
            f"guarded JNI parameters drifted for {function}: "
            f"expected {expected_parameters!r}, got {actual_parameters!r}"
        )
    body_open = masked.find("{", parameter_close)
    if body_open < 0 or masked[parameter_close + 1 : body_open].strip() != "noexcept":
        raise ContractError(f"guarded JNI definition {function} must be noexcept")
    body_close = _matching_delimiter(masked, body_open, "{", "}")
    body = masked[body_open + 1 : body_close]
    expected_helper = {"jint": "GuardStatus", "jlong": "GuardToken", "void": "GuardVoid"}[
        expected_return
    ]
    helpers = re.findall(r"\b(Guard(?:Status|Token|Void))\s*\(", body)
    if helpers != [expected_helper]:
        raise ContractError(f"guarded JNI function {function} lost its {expected_helper} boundary")
    if expected_return == "void":
        helper_pattern = rf"\b{expected_helper}\s*\(\s*env\s*,"
    else:
        helper_pattern = rf"\breturn\s+{expected_helper}\s*\(\s*env\s*,"
    if re.search(helper_pattern, body) is None:
        raise ContractError(f"guarded JNI function {function} does not return through its guard")
    if not function.startswith("Guarded"):
        raise ContractError(f"native registration function {function} is not a guarded entry")
    expected_native = "Native" + function.removeprefix("GuardedNative")
    native_calls = re.findall(r"\b(Native[A-Za-z0-9_]+)\s*\(", body)
    if native_calls != [expected_native]:
        raise ContractError(
            f"guarded JNI function {function} must call only {expected_native}, got {native_calls!r}"
        )


def _validate_guard_helper(
    clean: str,
    masked: str,
    name: str,
    result_type: str,
    allocation_failure: str,
    generic_failure: str,
) -> None:
    definition = _unique_match(
        re.compile(
            rf"\btemplate\s*<\s*typename\s+Function\s*>\s*{re.escape(result_type)}\s+"
            rf"{re.escape(name)}\s*\(\s*JNIEnv\s*\*\s*env\s*,\s*"
            r"Function\s+function\s*\)\s*noexcept\s*\{"
        ),
        masked,
        f"C++ {name} helper",
    )
    opening = masked.find("{", definition.start(), definition.end())
    closing = _matching_delimiter(masked, opening, "{", "}")
    actual = re.sub(r"\s+", " ", clean[opening + 1 : closing]).strip()
    expected = (
        "try { return function(); } "
        "catch (const std::bad_alloc&) { "
        f"ThrowOutOfMemory(env); return {allocation_failure}; "
        "} catch (const std::exception&) { "
        f"ThrowUnexpectedNativeFailure(env); return {generic_failure}; "
        "} catch (...) { "
        f"ThrowUnexpectedNativeFailure(env); return {generic_failure}; "
        "}"
    )
    if actual != expected:
        raise ContractError(f"C++ {name} no longer provides the canonical exception boundary")


def _simple_cpp_integer(entry: str, name: str) -> int:
    match = re.fullmatch(
        rf"\s*{re.escape(name)}\s*=\s*(?P<base>\d+)"
        r"(?:\s*<<\s*(?P<shift>\d+))?\s*",
        entry,
    )
    if match is None:
        raise ContractError(f"C++ wire constant {name} must use a simple integer literal")
    value = int(match.group("base"))
    shift = match.group("shift")
    return value << int(shift) if shift is not None else value


def _cpp_wire_constants(clean: str, masked: str) -> dict[str, int]:
    constants: dict[str, int] = {}
    count_names = (
        "kIntegerFieldCount",
        "kLongFieldCount",
        "kDoubleFieldCount",
        "kStringFieldCount",
    )
    for name in count_names:
        declaration = _unique_match(
            re.compile(
                rf"\bconstexpr\s+int\s+{re.escape(name)}\s*=\s*(?P<value>\d+)\s*;"
            ),
            masked,
            f"C++ wire count {name}",
        )
        if _brace_depth(masked, 0, declaration.start()) != 1:
            raise ContractError(f"C++ wire count {name} must be in the wrapper namespace")
        constants[name] = int(declaration.group("value"))

    for enum_name in CPP_ENUMS.values():
        declaration = _unique_match(
            re.compile(rf"\benum\s+{re.escape(enum_name)}\s*:\s*int\s*\{{"),
            masked,
            f"C++ {enum_name} enum",
        )
        if _brace_depth(masked, 0, declaration.start()) != 1:
            raise ContractError(f"C++ {enum_name} must be in the wrapper namespace")
        opening = masked.find("{", declaration.start(), declaration.end())
        closing = _matching_delimiter(masked, opening, "{", "}")
        terminator = masked.find(";", closing)
        if terminator < 0 or masked[closing + 1 : terminator].strip():
            raise ContractError(f"C++ {enum_name} has a malformed terminator")
        body = clean[opening + 1 : closing]
        entries = [entry for entry in body.split(",") if entry.strip()]
        for entry in entries:
            name_match = re.match(r"\s*(?P<name>k[A-Za-z0-9_]+)\s*=", entry)
            if name_match is None:
                raise ContractError(f"C++ {enum_name} contains an unrecognized entry")
            name = name_match.group("name")
            if name in constants:
                raise ContractError(f"duplicate C++ wire constant {name}")
            constants[name] = _simple_cpp_integer(entry, name)
    return constants


def _validate_on_load(clean: str, masked: str, bindings_class: str) -> None:
    declaration = _unique_match(
        re.compile(
            r'\bconstexpr\s+char\s+kBindingsClass\s*\[\s*\]\s*=\s*'
            r'"(?P<value>[^"\\]*)"\s*;'
        ),
        clean,
        "C++ kBindingsClass declaration",
    )
    if declaration.group("value") != bindings_class:
        raise ContractError("the C++ bindings class does not match the contract")
    if _brace_depth(masked, 0, declaration.start()) != 1:
        raise ContractError("C++ kBindingsClass must be in the wrapper namespace")

    on_load = _unique_match(
        re.compile(
            r'extern\s+"C"\s+JNIEXPORT\s+jint\s+JNICALL\s+JNI_OnLoad\s*\('
            r"\s*JavaVM\s*\*\s*(?P<vm>[A-Za-z_][A-Za-z0-9_]*)\s*,"
            r"\s*void\s*\*\s*(?:[A-Za-z_][A-Za-z0-9_]*)?\s*\)\s*\{"
        ),
        clean,
        "JNI_OnLoad definition",
    )
    if _brace_depth(masked, 0, on_load.start()) != 0:
        raise ContractError("JNI_OnLoad must have external top-level linkage")
    opening = clean.find("{", on_load.start(), on_load.end())
    closing = _matching_delimiter(masked, opening, "{", "}")
    body = clean[opening + 1 : closing]
    body_masked = masked[opening + 1 : closing]

    environment = _unique_match(
        re.compile(
            r"\bJNIEnv\s*\*\s*(?P<env>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*nullptr\s*;"
        ),
        body,
        "JNI_OnLoad JNIEnv declaration",
    )
    if _brace_depth(body_masked, 0, environment.start()) != 0:
        raise ContractError("JNI_OnLoad JNIEnv must be declared in the function scope")
    environment_name = environment.group("env")
    environment_pattern = re.escape(environment_name)
    vm_pattern = re.escape(on_load.group("vm"))
    _unique_match(
        re.compile(
            rf"\b{vm_pattern}\s*->\s*GetEnv\s*\(\s*"
            rf"reinterpret_cast\s*<\s*void\s*\*\s*\*\s*>\s*\(\s*&\s*{environment_pattern}\s*\)\s*,\s*"
            r"JNI_VERSION_1_6\s*\)"
        ),
        body,
        "JNI_OnLoad GetEnv binding",
    )

    find_class = _unique_match(
        re.compile(
            r"\bjclass\s+(?P<bindings>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            rf"{environment_pattern}\s*->\s*FindClass\s*"
            r"\(\s*kBindingsClass\s*\)\s*;"
        ),
        body,
        "JNI_OnLoad FindClass binding",
    )
    if _brace_depth(body_masked, 0, find_class.start()) != 0:
        raise ContractError("JNI_OnLoad FindClass must run in the function scope")
    bindings = re.escape(find_class.group("bindings"))
    register_pattern = re.compile(
        rf"\bconst\s+jint\s+(?P<status>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        rf"{environment_pattern}\s*->\s*RegisterNatives\s*\(\s*{bindings}\s*,\s*kMethods\s*,\s*"
        r"static_cast\s*<\s*jint\s*>\s*\(\s*sizeof\s*\(\s*kMethods\s*\)\s*/\s*"
        r"sizeof\s*\(\s*kMethods\s*\[\s*0\s*\]\s*\)\s*\)\s*\)"
    )
    register = _unique_match(register_pattern, body, "JNI_OnLoad RegisterNatives binding")
    if _brace_depth(body_masked, 0, register.start()) != 0:
        raise ContractError("JNI_OnLoad RegisterNatives must run in the function scope")
    status = re.escape(register.group("status"))
    failure_check = _unique_match(
        re.compile(
            rf"\bif\s*\(\s*{status}\s*!=\s*JNI_OK\s*\|\|\s*"
            rf"{environment_pattern}\s*->\s*ExceptionCheck\s*\(\s*\)\s*\)\s*\{{\s*"
            r"return\s+JNI_ERR\s*;\s*\}"
        ),
        body,
        "JNI_OnLoad RegisterNatives failure check",
    )
    if _brace_depth(body_masked, 0, failure_check.start()) != 0:
        raise ContractError("JNI_OnLoad failure check must run in the function scope")
    if len(re.findall(r"->\s*RegisterNatives\s*\(", masked)) != 1:
        raise ContractError("RegisterNatives must appear exactly once inside JNI_OnLoad")
    if len(re.findall(r"->\s*FindClass\s*\(\s*kBindingsClass\s*\)", masked)) != 1:
        raise ContractError("FindClass(kBindingsClass) must appear exactly once inside JNI_OnLoad")
    if len(re.findall(r"\bkBindingsClass\b", masked)) != 2:
        raise ContractError("C++ kBindingsClass is shadowed or referenced outside JNI_OnLoad")
    if len(re.findall(r"\bkMethods\b", masked)) != 4:
        raise ContractError("C++ kMethods is shadowed or referenced outside JNI_OnLoad")
    canonical_body = re.sub(r"\s+", "", body_masked)
    expected_body = re.sub(
        r"\s+",
        "",
        """
        JNIEnv* env = nullptr;
        if (vm == nullptr ||
            vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK ||
            env == nullptr) {
            return JNI_ERR;
        }
        if (av_jni_set_java_vm(vm, nullptr) < 0) {
            return JNI_ERR;
        }
        jclass bindings = env->FindClass(kBindingsClass);
        if (bindings == nullptr) {
            return JNI_ERR;
        }
        const jint status = env->RegisterNatives(
                bindings,
                kMethods,
                static_cast<jint>(sizeof(kMethods) / sizeof(kMethods[0])));
        env->DeleteLocalRef(bindings);
        if (status != JNI_OK || env->ExceptionCheck()) {
            return JNI_ERR;
        }
        return JNI_VERSION_1_6;
        """,
    )
    if canonical_body != expected_body:
        raise ContractError("JNI_OnLoad must use the canonical direct registration body")


def _cpp_contract(
    source: str,
    bindings_class: str,
    expected: list[tuple[str, str, str]],
) -> CppContract:
    if re.search(r"\?\?[=/'()!<>-]", source):
        raise ContractError("C++ trigraphs are forbidden in the JNI ABI source")
    spliced = _splice_cpp_lines(source)
    clean = _mask_source(spliced, strings=False)
    masked = _mask_source(spliced, strings=True)
    if re.search(r"(?:u8|u|U|L)?R\"", clean):
        raise ContractError("C++ raw string literals are outside the source validator grammar")
    directives = tuple(
        line.strip()
        for line in clean.splitlines()
        if re.match(r"^\s*(?:#|%:)", line)
    )
    expected_directives = (
        "#include <jni.h>",
        "#include <libavcodec/jni.h>",
        "#include <mpv/client.h>",
        "#include <climits>",
        "#include <clocale>",
        "#include <condition_variable>",
        "#include <cstdint>",
        "#include <cstring>",
        "#include <exception>",
        "#include <memory>",
        "#include <mutex>",
        "#include <new>",
        "#include <string>",
        "#include <thread>",
        "#include <unordered_map>",
        "#include <utility>",
        "#include <vector>",
    )
    if directives != expected_directives:
        raise ContractError("C++ preprocessor directives must be the fixed wrapper include list")
    if re.search(
        r"(?m)^\s*(?:#|%:)\s*(?:define|undef|if|ifdef|ifndef|elif|else|endif)\b",
        masked,
    ):
        raise ContractError("C++ macros and conditional compilation are forbidden in the JNI ABI source")

    _validate_guard_helper(
        clean,
        masked,
        "GuardStatus",
        "jint",
        "MPV_ERROR_NOMEM",
        "MPV_ERROR_GENERIC",
    )
    _validate_guard_helper(clean, masked, "GuardToken", "jlong", "0", "0")

    table = _unique_match(
        re.compile(r"\bconst\s+JNINativeMethod\s+kMethods\s*\[\s*\]\s*=\s*\{"),
        masked,
        "C++ kMethods table",
    )
    if _brace_depth(masked, 0, table.start()) != 1:
        raise ContractError("the C++ kMethods table must be in the wrapper namespace")
    opening = masked.find("{", table.start(), table.end())
    closing = _matching_delimiter(masked, opening, "{", "}")
    body = clean[opening + 1 : closing]
    matches = list(CPP_METHOD_PATTERN.finditer(body))
    remainder = list(body)
    for match in matches:
        _blank(remainder, match.start(), match.end())
    if re.sub(r"[\s,]", "", "".join(remainder)):
        raise ContractError("the C++ kMethods table contains an unrecognized entry")
    outside = clean[: opening + 1] + (" " * (closing - opening - 1)) + clean[closing:]
    if CPP_METHOD_PATTERN.search(outside):
        raise ContractError("a C++ native method entry exists outside kMethods")
    methods = [
        (match.group("name"), match.group("descriptor"), match.group("function"))
        for match in matches
    ]
    for _name, descriptor, function in expected:
        _validate_guarded_function(clean, masked, function, descriptor)
        expected_native = "Native" + function.removeprefix("GuardedNative")
        if len(re.findall(rf"\b{re.escape(expected_native)}\b", masked)) != 2:
            raise ContractError(
                f"C++ {expected_native} is shadowed or referenced outside its definition and guard"
            )
    helper_calls = {"GuardStatus": 0, "GuardToken": 0, "GuardVoid": 0}
    for _name, descriptor, _function in expected:
        _arguments, result_descriptor = _jni_descriptor_parts(descriptor)
        result_type = _jni_cpp_type(result_descriptor, result=True)
        helper_calls[{"jint": "GuardStatus", "jlong": "GuardToken", "void": "GuardVoid"}[
            result_type
        ]] += 1
    for helper, call_count in helper_calls.items():
        definition_count = 1 if helper in {"GuardStatus", "GuardToken"} else 0
        if len(re.findall(rf"\b{re.escape(helper)}\b", masked)) != call_count + definition_count:
            raise ContractError(f"C++ {helper} is shadowed or used outside a registered guard")
    _validate_on_load(clean, masked, bindings_class)
    return CppContract(methods=methods, wire_constants=_cpp_wire_constants(clean, masked))


def _mask_cmake(source: str) -> str:
    if re.search(r"#?\[(?P<equals>=*)\[", source):
        raise ContractError("CMake bracket comments are outside the source validator grammar")
    chars = list(source)
    index = 0
    quoted = False
    while index < len(source):
        if source[index] == "\\" and quoted:
            index = min(len(source), index + 2)
            continue
        if source[index] == '"':
            quoted = not quoted
            index += 1
            continue
        if source[index] == "#" and not quoted:
            end = source.find("\n", index + 1)
            end = len(source) if end < 0 else end
            _blank(chars, index, end)
            index = end
            continue
        index += 1
    if quoted:
        raise ContractError("an unterminated CMake string was found")
    return "".join(chars)


def _validate_cmake(source: str, library_name: str) -> None:
    masked = _mask_cmake(source)
    target = re.escape(library_name)
    _unique_match(
        re.compile(
            r"\bif\s*\(\s*NOT\s+CMAKE_BUILD_TYPE\s+STREQUAL\s+\"Release\"\s*\)"
        ),
        masked,
        "CMake Release-only gate",
    )
    if len(re.findall(r"\bCMAKE_BUILD_TYPE\b", masked)) != 1:
        raise ContractError("CMake must bind the wrapper build type exactly once")
    _unique_match(
        re.compile(
            rf"\badd_library\s*\(\s*{target}\s+SHARED\s+"
            rf"{target}\.cpp\s*\)"
        ),
        masked,
        "CMake wrapper shared-library target and source",
    )
    forbidden_commands = re.findall(
        r"(?i)\b(set_property|cmake_language|function|macro|include|add_subdirectory|target_sources)\s*\(",
        masked,
    )
    if forbidden_commands:
        raise ContractError(
            f"CMake contract source uses forbidden dynamic command(s): {forbidden_commands!r}"
        )
    all_property_blocks = list(
        re.finditer(r"(?i)\bset_target_properties\s*\(", masked)
    )
    if len(all_property_blocks) != 3:
        raise ContractError(
            f"CMake must contain exactly three static target-property blocks, found {len(all_property_blocks)}"
        )
    output_tokens = list(re.finditer(r"\bOUTPUT_NAME\b", masked))
    if len(output_tokens) != 1:
        raise ContractError(
            f"CMake must set exactly one literal OUTPUT_NAME, found {len(output_tokens)}"
        )
    blocks = list(
        re.finditer(
            rf"\bset_target_properties\s*\(\s*{target}\s+PROPERTIES(?P<body>[^)]*)\)",
            masked,
            re.DOTALL,
        )
    )
    if len(blocks) != 1:
        raise ContractError("CMake must contain exactly one wrapper target-property block")
    output_names: list[str] = []
    for block in blocks:
        output_names.extend(
            re.findall(r"\bOUTPUT_NAME\s+([A-Za-z_][A-Za-z0-9_]*)\b", block.group("body"))
        )
    if output_names != [library_name]:
        raise ContractError(
            f"CMake OUTPUT_NAME drifted: expected {[library_name]!r}, got {output_names!r}"
        )
    wrapper_property_tokens = re.findall(
        r"\$\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*",
        blocks[0].group("body"),
    )
    if wrapper_property_tokens != [
        "CXX_EXTENSIONS",
        "OFF",
        "OUTPUT_NAME",
        library_name,
        "PREFIX",
        "lib",
        "SUFFIX",
        "so",
        "RELEASE_POSTFIX",
    ]:
        raise ContractError(
            "CMake wrapper properties must fix CXX extensions and the literal Android library name"
        )


def _wire_entries(
    wire: dict[str, Any],
    table_name: str,
    expected_values: set[int] | None,
    seen_kotlin: set[str],
    seen_cpp: set[str],
) -> list[tuple[str, str, int]]:
    entries = wire.get(table_name)
    if not isinstance(entries, list) or not entries:
        raise ContractError(f"missing wire table {table_name}")
    parsed: list[tuple[str, str, int]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ContractError(f"malformed wire entry in {table_name}")
        kotlin_name = entry.get("kotlin")
        cpp_name = entry.get("cpp")
        value = entry.get("value")
        if (
            not isinstance(kotlin_name, str)
            or not isinstance(cpp_name, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
        ):
            raise ContractError(f"malformed wire entry in {table_name}")
        if kotlin_name in seen_kotlin or cpp_name in seen_cpp:
            raise ContractError(f"duplicate wire name in {table_name}")
        seen_kotlin.add(kotlin_name)
        seen_cpp.add(cpp_name)
        parsed.append((kotlin_name, cpp_name, value))
    values = [value for _kotlin, _cpp, value in parsed]
    if len(set(values)) != len(values):
        raise ContractError(f"duplicate wire value in {table_name}")
    if expected_values is not None and set(values) != expected_values:
        raise ContractError(
            f"wire values for {table_name} must be exactly {sorted(expected_values)!r}"
        )
    return parsed


def validate_sources(
    contract_source: str,
    cpp_source: str,
    kotlin_source: str,
    cmake_source: str,
) -> int:
    contract = _load_contract_text(contract_source)
    expected: list[tuple[str, str, str]] = []
    for entry in contract["methods"]:
        if not isinstance(entry, dict):
            raise ContractError("the wrapper contract contains a malformed method")
        name = entry.get("name")
        descriptor = entry.get("descriptor")
        function = entry.get("function")
        if not all(isinstance(value, str) and value for value in (name, descriptor, function)):
            raise ContractError("the wrapper contract contains a malformed method")
        expected.append((name, descriptor, function))
    if len({name for name, _descriptor_value, _function in expected}) != len(expected):
        raise ContractError("the wrapper contract contains duplicate method names")
    if len({function for _name, _descriptor_value, function in expected}) != len(expected):
        raise ContractError("the wrapper contract contains duplicate native functions")
    if tuple(expected) != EXPECTED_METHODS:
        raise ContractError("wrapper contract schema v1 native methods drifted")

    bindings_class = contract.get("bindingsClass")
    library_name = contract.get("libraryName")
    if not isinstance(bindings_class, str) or not bindings_class:
        raise ContractError("the wrapper contract has no bindingsClass")
    if not isinstance(library_name, str) or not library_name:
        raise ContractError("the wrapper contract has no libraryName")
    if bindings_class != EXPECTED_BINDINGS_CLASS:
        raise ContractError("wrapper contract schema v1 bindingsClass drifted")
    if library_name != EXPECTED_LIBRARY_NAME:
        raise ContractError("wrapper contract schema v1 libraryName drifted")

    actual_kotlin = _kotlin_contract(kotlin_source, bindings_class, library_name)
    expected_kotlin = [(name, descriptor) for name, descriptor, _function in expected]
    if actual_kotlin.methods != expected_kotlin:
        raise ContractError(
            f"Kotlin native table drifted: expected {expected_kotlin!r}, "
            f"got {actual_kotlin.methods!r}"
        )
    actual_cpp = _cpp_contract(cpp_source, bindings_class, expected)
    if actual_cpp.methods != expected:
        raise ContractError(
            f"C++ registration table drifted: expected {expected!r}, got {actual_cpp.methods!r}"
        )
    _validate_cmake(cmake_source, library_name)

    wire = contract.get("wire")
    if not isinstance(wire, dict):
        raise ContractError("the wrapper contract has no wire table")
    count_pairs = (
        ("integerFieldCount", "integerFields", "INTEGER_FIELD_COUNT", "kIntegerFieldCount"),
        ("longFieldCount", "longFields", "LONG_FIELD_COUNT", "kLongFieldCount"),
        ("doubleFieldCount", "doubleFields", "DOUBLE_FIELD_COUNT", "kDoubleFieldCount"),
        ("stringFieldCount", "stringFields", "STRING_FIELD_COUNT", "kStringFieldCount"),
    )
    seen_kotlin: set[str] = set()
    seen_cpp: set[str] = set()
    all_entries: list[tuple[str, str, int]] = []
    expected_kotlin_constants: dict[str, int] = {}
    expected_cpp_constants: dict[str, int] = {}
    for contract_name, table_name, kotlin_name, cpp_name in count_pairs:
        expected_count = wire.get(contract_name)
        if (
            not isinstance(expected_count, int)
            or isinstance(expected_count, bool)
            or expected_count <= 0
        ):
            raise ContractError(f"invalid wire count {contract_name}")
        if expected_count != EXPECTED_WIRE_COUNTS[contract_name]:
            raise ContractError(
                f"wire schema v1 requires {contract_name}={EXPECTED_WIRE_COUNTS[contract_name]}"
            )
        expected_kotlin_constants[kotlin_name] = expected_count
        expected_cpp_constants[cpp_name] = expected_count
        parsed_entries = _wire_entries(
            wire,
            table_name,
            set(range(expected_count)),
            seen_kotlin,
            seen_cpp,
        )
        if tuple(parsed_entries) != EXPECTED_WIRE_ENTRIES[table_name]:
            raise ContractError(f"wrapper contract schema v1 {table_name} drifted")
        all_entries.extend(parsed_entries)

    presence_entries = _wire_entries(
        wire,
        "presenceFlags",
        EXPECTED_PRESENCE_VALUES,
        seen_kotlin,
        seen_cpp,
    )
    if any(value <= 0 or value & (value - 1) != 0 for _kotlin, _cpp, value in presence_entries):
        raise ContractError("presence flags must be unique non-zero powers of two")
    if tuple(presence_entries) != EXPECTED_WIRE_ENTRIES["presenceFlags"]:
        raise ContractError("wrapper contract schema v1 presenceFlags drifted")
    all_entries.extend(presence_entries)
    for kotlin_name, cpp_name, expected_value in all_entries:
        expected_kotlin_constants[kotlin_name] = expected_value
        expected_cpp_constants[cpp_name] = expected_value

    if actual_kotlin.wire_constants != expected_kotlin_constants:
        raise ContractError(
            "Kotlin wire constants drifted: "
            f"expected {expected_kotlin_constants!r}, got {actual_kotlin.wire_constants!r}"
        )
    if actual_cpp.wire_constants != expected_cpp_constants:
        raise ContractError(
            "C++ wire constants drifted: "
            f"expected {expected_cpp_constants!r}, got {actual_cpp.wire_constants!r}"
        )
    return len(expected)


def validate(contract_path: Path, cpp_path: Path, kotlin_path: Path, cmake_path: Path) -> int:
    return validate_sources(
        _read_text(contract_path),
        _read_text(cpp_path),
        _read_text(kotlin_path),
        _read_text(cmake_path),
    )
