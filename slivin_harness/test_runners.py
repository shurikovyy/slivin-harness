"""Conservative source-based runner resolution for discovered JavaScript tests.

This is a lexical descriptor, not a JavaScript interpreter or proof of assertions.
Unknown/mixed frameworks fail closed; owner commands never enter this resolver.
"""
from __future__ import annotations

import re
from pathlib import Path

from .protocol import ArtifactContractError


class TestRunnerResolutionError(ArtifactContractError):
    def __init__(self, code: str, path: str, message: str):
        super().__init__(code=code, field="registered_checks.path", message=message,
                         expected="An unambiguous supported test runner and its configured toolchain", actual=path)


def _tokens(source: str) -> list[tuple[str, str]]:
    """Discard comments and opaque literal bodies, retain module string tokens.

    Templates (including interpolation) and regex literals are opaque: uncertain
    code must not manufacture an import from text. Unsupported lexical input is
    diagnosed instead of falling back to the installed runner.
    """
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(source):
        rest = source[index:]
        if rest[0].isspace():
            index += 1
            continue
        if rest.startswith("//") or (index == 0 and rest.startswith("#!")):
            end = source.find("\n", index)
            index = len(source) if end < 0 else end + 1
            continue
        if rest.startswith("/*"):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unterminated comment")
            index = end + 2
            continue
        quote = rest[0]
        regex = quote == "/" and (not result or result[-1][1] in {"=", "(", ",", ":", "return", "=>", "[", "!"})
        if quote in "'\"`" or regex:
            end, escaped, char_class = index + 1, False, False
            while end < len(source):
                char = source[end]
                if not escaped:
                    if regex and char == "[":
                        char_class = True
                    elif regex and char == "]":
                        char_class = False
                    elif char == quote and not char_class:
                        break
                if char == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                end += 1
            if end == len(source):
                raise ValueError("Unterminated literal")
            body = source[index + 1:end]
            result.append(("string" if quote in "'\"" else "opaque", body))
            index = end + 1
            continue
        match = re.match(r"[A-Za-z_$][\w$]*|=>", rest)
        token = match.group() if match else rest[0]
        result.append(("code", token))
        index += len(token)
    return result


def resolve_javascript_runner(path: Path, *, relative: str) -> str:
    try:
        tokens = _tokens(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise TestRunnerResolutionError("TEST_RUNNER_UNSUPPORTED", relative, "Cannot resolve JavaScript test syntax") from exc
    modules: set[str] = set()
    for index, (kind, value) in enumerate(tokens):
        if kind != "string":
            continue
        before = tokens[:index]
        # CommonJS require(), ESM import(), static import ... from and import '...'.
        if len(before) >= 2 and before[-1] == ("code", "(") and before[-2] in {("code", "require"), ("code", "import")}:
            if len(before) < 3 or before[-3] != ("code", "."):
                modules.add(value)
        elif before and before[-1] == ("code", "import"):
            modules.add(value)
        elif before and before[-1] == ("code", "from"):
            statement = before[max((i + 1 for i, token in enumerate(before) if token == ("code", ";")), default=0):]
            if ("code", "import") in statement:
                modules.add(value)
    code = " ".join(value if kind == "code" else "LITERAL" for kind, value in tokens)
    native = "node:test" in modules
    jest = "@jest/globals" in modules or bool(re.search(r"\b(?:expect\s*\(|jest\s*\.)", code))
    test_call = bool(re.search(r"\b(?:test|it|describe)\s*(?:\.\s*(?:each|only|skip)\s*)?\(", code))
    foreign = bool(modules & {"vitest", "mocha", "ava", "tape", "bun:test"})
    if foreign or (native and jest):
        raise TestRunnerResolutionError("TEST_RUNNER_AMBIGUOUS", relative, "Mixed or unsupported framework evidence; provide an owner-defined check for this path")
    if native:
        return "NODE_TEST"
    if jest and (test_call or "@jest/globals" in modules):
        return "JEST"
    raise TestRunnerResolutionError("TEST_RUNNER_UNSUPPORTED", relative, "No unambiguous node:test import or Jest test/assertion evidence")
