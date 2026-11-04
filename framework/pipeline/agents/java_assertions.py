from __future__ import annotations

"""Java AST utilities shared by Assertion Roulette pipeline stages.

The previous implementation searched only the outermost assertion text.  This
module deliberately exposes one tree-sitter based representation so Risk,
prompt construction, candidate validation, recovery, and revalidation agree on
assertion identity and nesting.
"""

import hashlib
from typing import Any, Iterable

from tree_sitter import Language, Parser
import tree_sitter_java


JAVA_LANGUAGE = Language(tree_sitter_java.language())

# Minimum JUnit 5 argument count without a message.  V1 only automatically
# rewrites message-last APIs whose overload can be identified conservatively.
SUPPORTED_ASSERTIONS: dict[str, int] = {
    "assertEquals": 2,
    "assertNotEquals": 2,
    "assertTrue": 1,
    "assertFalse": 1,
    "assertNull": 1,
    "assertNotNull": 1,
    "assertSame": 2,
    "assertNotSame": 2,
    "assertArrayEquals": 2,
    "assertIterableEquals": 2,
    "assertLinesMatch": 2,
    "assertInstanceOf": 2,
    "assertThrows": 2,
    "assertThrowsExactly": 2,
    "assertDoesNotThrow": 1,
}

CONTROL_FLOW_NODES = {
    "if_statement",
    "switch_expression",
    "switch_block_statement_group",
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
    "try_statement",
    "catch_clause",
    "finally_clause",
    "synchronized_statement",
}

COMMENT_NODE_TYPES = {"line_comment", "block_comment"}


class JavaAssertionParseError(ValueError):
    pass


def _executable_block_children(block) -> tuple[list[Any], list[str]]:
    """Return top-level executable children and ignored Java comment types.

    Tree-sitter exposes comments as named children of Java blocks.  Comments
    are trivia for ECT trigger-boundary enumeration, but every other named
    child remains a statement and therefore continues to widen or invalidate
    the boundary.
    """

    executable = []
    ignored_comments = []
    for child in block.named_children:
        if child.type in COMMENT_NODE_TYPES:
            ignored_comments.append(child.type)
        else:
            executable.append(child)
    return executable, ignored_comments


def extract_ect_trigger(source: str, *, from_assert_throws: bool) -> dict[str, Any]:
    """Extract one ECT trigger expression from a Java method using its AST.

    ``from_assert_throws=False`` reads the sole non-``fail`` expression from
    the body of one try statement. ``from_assert_throws=True`` reads the
    Executable argument of one ``assertThrows`` call.  An expression lambda
    and a block lambda containing exactly one expression statement have the
    same result; wider blocks and control-flow statements are rejected.
    """

    source_bytes = source.encode("utf-8")
    tree = Parser(JAVA_LANGUAGE).parse(source_bytes)
    if tree.root_node.has_error:
        return {"status": "JAVA_AST_PARSE_ERROR", "expression": None}

    def text(node) -> str:
        return _node_text(source_bytes, node).strip()

    def unwrap(node):
        while node is not None and node.type == "parenthesized_expression":
            children = list(node.named_children)
            if len(children) != 1:
                break
            node = children[0]
        return node

    def nodes_of_type(node, node_type: str):
        found = []
        if node.type == node_type:
            found.append(node)
        for child in node.named_children:
            found.extend(nodes_of_type(child, node_type))
        return found

    if from_assert_throws:
        calls = []
        for node in nodes_of_type(tree.root_node, "method_invocation"):
            name = node.child_by_field_name("name")
            if name is not None and text(name) in {"assertThrows", "assertThrowsExactly"}:
                calls.append(node)
        if len(calls) != 1:
            return {
                "status": "ASSERT_THROWS_COUNT_UNSUPPORTED",
                "expression": None,
                "assert_throws_count": len(calls),
            }
        arguments = calls[0].child_by_field_name("arguments")
        argument_nodes = list(arguments.named_children) if arguments is not None else []
        if len(argument_nodes) < 2 or argument_nodes[1].type != "lambda_expression":
            return {"status": "EXECUTABLE_LAMBDA_REQUIRED", "expression": None}
        body = argument_nodes[1].child_by_field_name("body")
        if body is None:
            return {"status": "LAMBDA_BODY_MISSING", "expression": None}
        body_kind = "EXPRESSION_LAMBDA"
        expression = body
        if body.type == "block":
            statements, ignored_comments = _executable_block_children(body)
            if len(statements) != 1:
                return {
                    "status": "BLOCK_LAMBDA_STATEMENT_COUNT_UNSUPPORTED",
                    "expression": None,
                    "statement_count": len(statements),
                    "ignored_comment_types": ignored_comments,
                }
            statement = statements[0]
            if statement.type != "expression_statement" or len(statement.named_children) != 1:
                return {
                    "status": "BLOCK_LAMBDA_EXPRESSION_STATEMENT_REQUIRED",
                    "expression": None,
                    "statement_type": statement.type,
                }
            expression = statement.named_children[0]
            body_kind = "SINGLE_EXPRESSION_BLOCK_LAMBDA"
        expression = unwrap(expression)
        return {
            "status": "OK",
            "expression": text(expression),
            "expression_node_type": expression.type,
            "body_kind": body_kind,
            "ignored_comment_types": ignored_comments if body.type == "block" else [],
        }

    tries = nodes_of_type(tree.root_node, "try_statement")
    if len(tries) != 1:
        return {"status": "TRY_COUNT_UNSUPPORTED", "expression": None, "try_count": len(tries)}
    body = tries[0].child_by_field_name("body")
    if body is None:
        return {"status": "TRY_BODY_MISSING", "expression": None}
    triggers = []
    statements, ignored_comments = _executable_block_children(body)
    for statement in statements:
        if statement.type != "expression_statement" or len(statement.named_children) != 1:
            return {
                "status": "TRY_BODY_EXPRESSION_STATEMENTS_REQUIRED",
                "expression": None,
                "statement_type": statement.type,
                "ignored_comment_types": ignored_comments,
            }
        expression = unwrap(statement.named_children[0])
        if expression.type == "method_invocation":
            name = expression.child_by_field_name("name")
            if name is not None and text(name) == "fail":
                continue
        triggers.append(expression)
    if len(triggers) != 1:
        return {
            "status": "TRIGGER_STATEMENT_COUNT_UNSUPPORTED",
            "expression": None,
            "trigger_count": len(triggers),
            "ignored_comment_types": ignored_comments,
        }
    expression = triggers[0]
    return {
        "status": "OK",
        "expression": text(expression),
        "expression_node_type": expression.type,
        "body_kind": "TRY_EXPRESSION_STATEMENT",
        "ignored_comment_types": ignored_comments,
    }


def find_assertion_calls(source: str) -> list[dict[str, Any]]:
    """Return all supported/unsupported ``assert*`` calls recursively.

    Every result retains source-compatible character offsets and an AST path.
    Nested assertions are returned as independent nodes with a parent identity.
    Assertions inside lambdas or ``assertAll`` are exposed but marked unsafe for
    V1 automatic refactoring rather than being silently ignored.
    """

    source_bytes = source.encode("utf-8")
    parser = Parser(JAVA_LANGUAGE)
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        raise JavaAssertionParseError("Java method contains parser error nodes.")

    byte_to_char = _byte_to_char_map(source_bytes)
    calls: list[dict[str, Any]] = []

    def visit(
        node,
        path: tuple[str, ...],
        assertion_parent: dict[str, Any] | None,
        control_flow: tuple[str, ...],
        lambda_depth: int,
    ) -> None:
        node_type = node.type
        next_control = control_flow
        if node_type in CONTROL_FLOW_NODES:
            next_control = control_flow + ("/".join(path),)
        next_lambda_depth = lambda_depth + (1 if node_type == "lambda_expression" else 0)

        current_parent = assertion_parent
        if node_type == "method_invocation":
            name_node = node.child_by_field_name("name")
            name = _node_text(source_bytes, name_node) if name_node is not None else ""
            if name.startswith("assert"):
                args_node = node.child_by_field_name("arguments")
                argument_nodes = list(args_node.named_children) if args_node is not None else []
                start = byte_to_char[node.start_byte]
                end = byte_to_char[node.end_byte]
                ast_path = "/".join(path)
                assertion_id = "assertion:v1:" + hashlib.sha256(
                    f"{name}\0{ast_path}\0{'|'.join(next_control)}".encode("utf-8")
                ).hexdigest()
                unsupported_reasons: list[str] = []
                if name not in SUPPORTED_ASSERTIONS:
                    unsupported_reasons.append("UNSUPPORTED_ASSERTION_API")
                if next_lambda_depth:
                    unsupported_reasons.append("ASSERTION_INSIDE_LAMBDA")
                if assertion_parent and assertion_parent["name"] == "assertAll":
                    unsupported_reasons.append("ASSERTION_INSIDE_ASSERT_ALL")
                call = {
                    "name": name,
                    "arguments": [_node_text(source_bytes, item).strip() for item in argument_nodes],
                    "argument_ranges": [
                        {
                            "start": byte_to_char[item.start_byte],
                            "end": byte_to_char[item.end_byte],
                        }
                        for item in argument_nodes
                    ],
                    "start": start,
                    "end": end,
                    "code": source[start:end],
                    "ast_path": ast_path,
                    "control_flow_path": list(next_control),
                    "parent_assertion_id": assertion_parent["assertion_id"] if assertion_parent else None,
                    "parent_assertion_api": assertion_parent["name"] if assertion_parent else None,
                    "nesting_depth": (assertion_parent["nesting_depth"] + 1) if assertion_parent else 0,
                    "statement_ordinal": _statement_ordinal(node),
                    "assertion_id": assertion_id,
                    "unsupported_reasons": unsupported_reasons,
                    "supported_context": not unsupported_reasons,
                }
                calls.append(call)
                current_parent = call

        for actual_index, child in enumerate(node.children):
            if not child.is_named:
                continue
            field = node.field_name_for_child(actual_index)
            segment = f"{field or child.type}:{_named_child_index(node, child)}"
            visit(child, path + (segment,), current_parent, next_control, next_lambda_depth)

    visit(tree.root_node, ("program:0",), None, (), 0)
    calls.sort(key=lambda item: (item["start"], -item["end"], item["ast_path"]))
    for index, call in enumerate(calls):
        call["assertion_ordinal"] = index
    return calls


def root_assertions(calls: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in calls if not item.get("parent_assertion_id")]


def descendant_assertions(
    call: dict[str, Any], calls: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    start, end = int(call["start"]), int(call["end"])
    return [
        item
        for item in calls
        if item is not call and start <= int(item["start"]) and int(item["end"]) <= end
    ]


def mask_nested_assertions(
    source: str,
    *,
    start: int,
    end: int,
    calls: Iterable[dict[str, Any]],
) -> str:
    """Mask maximal nested assertion calls inside one source range.

    Only direct outermost descendants are replaced, so overlapping AST ranges
    never corrupt the representation.  The marker preserves API and AST path.
    """

    candidates = sorted(
        (
            item
            for item in calls
            if start <= int(item["start"]) and int(item["end"]) <= end
        ),
        key=lambda item: (int(item["start"]), -int(item["end"])),
    )
    selected: list[dict[str, Any]] = []
    covered_end = -1
    for item in candidates:
        item_start, item_end = int(item["start"]), int(item["end"])
        if item_start < covered_end:
            continue
        selected.append(item)
        covered_end = item_end
    pieces: list[str] = []
    cursor = start
    for item in selected:
        item_start, item_end = int(item["start"]), int(item["end"])
        pieces.append(source[cursor:item_start])
        pieces.append(f" __NESTED_ASSERTION__{item['name']}__{item['ast_path']}__ ")
        cursor = item_end
    pieces.append(source[cursor:end])
    return "".join(pieces)


def _node_text(source_bytes: bytes, node) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8")


def _byte_to_char_map(source_bytes: bytes) -> list[int]:
    mapping = [0] * (len(source_bytes) + 1)
    char_index = 0
    byte_index = 0
    text = source_bytes.decode("utf-8")
    for character in text:
        encoded = character.encode("utf-8")
        for offset in range(len(encoded)):
            mapping[byte_index + offset] = char_index
        byte_index += len(encoded)
        char_index += 1
    mapping[len(source_bytes)] = len(text)
    return mapping


def _named_child_index(parent, child) -> int:
    for index, item in enumerate(parent.named_children):
        if item.id == child.id:
            return index
    return -1


def _statement_ordinal(node) -> int | None:
    current = node
    while current.parent is not None:
        parent = current.parent
        if parent.type in {"block", "switch_block_statement_group"}:
            for index, item in enumerate(parent.named_children):
                if item.id == current.id:
                    return index
            return None
        current = parent
    return None
