"""End-to-end test for the Ctrl+C / SIGINT shutdown hang.

Pin down the bug observed in production: pressing Ctrl+C (or sending SIGINT)
to a running `python -m horizon.main` server does NOT cause it to exit in a
reasonable time when there are any active HTTP keep-alive clients (e.g., the
frontend dashboard polling for data, or an SSE subscriber).

Root cause: uvicorn's `Server.shutdown()` waits forever for in-flight request
tasks to complete (default `timeout_graceful_shutdown=None`). With keep-alive
connections, that wait never finishes, so the lifespan's post-yield shutdown
code is never reached — background tasks are never cancelled, the DB is never
closed, and the process never exits.

Fix under test: `uvicorn.run()` must be called with a positive
`timeout_graceful_shutdown` so the server proceeds to lifespan.shutdown()
after a bounded delay.

This test is a *contract* test: it parses the AST of `horizon.main.main` to
verify that `timeout_graceful_shutdown` is wired up. It does not spawn a
real uvicorn process (that would be slow and flaky), but it is sufficient to
prevent regression of the user-visible bug.
"""

import ast
import inspect

import pytest


def _find_uvicorn_run_call(func_source: str) -> ast.Call:
    """Return the ast.Call node for uvicorn.run(...) inside `func_source`.

    Uses AST so it correctly handles multi-line calls, comments containing
    parentheses (e.g. `uvicorn's Server.shutdown()`), and nested expressions.
    """
    tree = ast.parse(func_source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
        ):
            return node
    raise AssertionError(
        "Could not locate `uvicorn.run(...)` call in horizon.main.main()"
    )


def test_main_passes_timeout_graceful_shutdown_to_uvicorn_run() -> None:
    """`horizon.main.main()` must call `uvicorn.run()` with
    `timeout_graceful_shutdown=<positive int>`.
    """
    from horizon import main

    source = inspect.getsource(main.main)
    call = _find_uvicorn_run_call(source)

    kwargs = {kw.arg: kw.value for kw in call.keywords}
    assert "timeout_graceful_shutdown" in kwargs, (
        "horizon.main.main() must pass `timeout_graceful_shutdown=<int>` to "
        "uvicorn.run(...) — without it, uvicorn waits forever for keep-alive "
        "connections to drain, the lifespan shutdown is never reached, and "
        "Ctrl+C hangs the process."
    )

    value_node = kwargs["timeout_graceful_shutdown"]

    # Resolve to an int: either an int literal or a name that resolves to int
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, int):
        value = value_node.value
    elif isinstance(value_node, ast.Name):
        # It is a named constant — verify it is defined as a positive int.
        const_name = value_node.id
        ns = {k: getattr(main, k) for k in dir(main) if not k.startswith("__")}
        assert const_name in ns, (
            f"timeout_graceful_shutdown={const_name} references an unknown "
            f"name in horizon.main; expected a module-level constant."
        )
        value = ns[const_name]
        assert isinstance(value, int), (
            f"timeout_graceful_shutdown constant {const_name}={value!r} must "
            f"be an int."
        )
    else:
        pytest.fail(
            f"timeout_graceful_shutdown must be an int literal or constant, "
            f"got AST node: {ast.dump(value_node)}"
        )

    assert value > 0, (
        f"timeout_graceful_shutdown={value} must be a positive integer; "
        "0 or negative values would still cause immediate forced shutdown."
    )


def test_timeout_graceful_shutdown_value_is_reasonable() -> None:
    """The chosen value must be long enough to let in-flight requests
    finish (so legitimate work is not lost) but short enough to be
    user-perceptible as 'responsive' on Ctrl+C.
    """
    from horizon import main

    source = inspect.getsource(main.main)
    call = _find_uvicorn_run_call(source)
    kwargs = {kw.arg: kw.value for kw in call.keywords}

    if "timeout_graceful_shutdown" not in kwargs:
        pytest.skip("covered by the other test")

    value_node = kwargs["timeout_graceful_shutdown"]
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, int):
        value = value_node.value
    elif isinstance(value_node, ast.Name):
        ns = {k: getattr(main, k) for k in dir(main) if not k.startswith("__")}
        value = ns[value_node.id]
    else:
        pytest.skip("not an int literal/constant — value not bounded here")

    # Industry standard for "graceful shutdown" of an HTTP service is
    # 5-30 seconds. Anything < 1s is too aggressive (kills in-flight
    # requests), anything > 60s is too slow (user-perceptible hang).
    assert 1 <= value <= 60, (
        f"timeout_graceful_shutdown={value}s is outside the recommended "
        f"1-60s range."
    )
