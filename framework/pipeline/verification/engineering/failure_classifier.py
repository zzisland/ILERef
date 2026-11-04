from __future__ import annotations


def classify_failure(stage: str, output: str, *, timed_out: bool = False, command_missing: bool = False) -> tuple[str, str, str]:
    text = (output or "").lower()
    if timed_out: return "VALIDATION_BLOCKED", "TIMEOUT_BLOCKED", "COMMAND_TIMEOUT"
    if command_missing:
        return "VALIDATION_BLOCKED", "BUILD_TOOL_BLOCKED", "MAVEN_NOT_FOUND"
    patterns = (
        (("java_home", "no java compiler", "java executable was not found"), "VALIDATION_BLOCKED", "JDK_BLOCKED", "JDK_NOT_FOUND"),
        (("unknown host", "name or service not known", "nodename nor servname", "temporary failure in name resolution"), "VALIDATION_BLOCKED", "DEPENDENCY_BLOCKED", "DNS_ERROR"),
        (("could not resolve dependencies", "failed to read artifact descriptor", "could not transfer artifact"), "VALIDATION_BLOCKED", "DEPENDENCY_BLOCKED", "DEPENDENCY_RESOLUTION_ERROR"),
        (("connection refused", "connection reset", "network is unreachable"), "VALIDATION_BLOCKED", "DEPENDENCY_BLOCKED", "NETWORK_ERROR"),
        (("repository is unavailable", "service unavailable"), "VALIDATION_BLOCKED", "DEPENDENCY_BLOCKED", "REPOSITORY_UNAVAILABLE"),
        (("invalid target release", "release version", "unsupported class file major version"), "VALIDATION_BLOCKED", "JDK_BLOCKED", "JDK_VERSION_MISMATCH"),
        (("no tests were executed", "no tests matching pattern", "no tests to run"), "VALIDATION_FAILED", "TARGET_TEST_NOT_EXECUTED", "TARGET_TEST_NOT_FOUND"),
        (("compilation failure", "compilation error", "cannot find symbol", "incompatible types"), "VALIDATION_FAILED", "COMPILE_FAILED", "JAVA_COMPILATION_ERROR"),
        (("there are test failures", "assertionfailederror", "comparisonfailure"), "VALIDATION_FAILED", "TARGET_TEST_FAILED", "TEST_ASSERTION_FAILURE"),
        (("failed to execute goal", "pluginresolutionexception", "pluginexecutionexception"), "VALIDATION_BLOCKED", "PROJECT_CONFIGURATION_BLOCKED", "MAVEN_PLUGIN_ERROR"),
    )
    for needles, decision, status, category in patterns:
        if any(value in text for value in needles): return decision, status, category
    if stage == "COMPILE": return "VALIDATION_FAILED", "COMPILE_FAILED", "JAVA_COMPILATION_ERROR"
    if stage in {"TARGET_TEST", "MODULE_TEST"}: return "VALIDATION_FAILED", f"{stage}_FAILED", "TEST_RUNTIME_ERROR"
    return "VALIDATION_BLOCKED", "ENVIRONMENT_BLOCKED", "UNKNOWN_VALIDATION_ERROR"
