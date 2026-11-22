"""The Python script connector: a stdio MCP server whose one tool runs a world asset's Python
script on the stage's input, so a number or a ranking comes from code, never an agent's judgement.

`python_script` runs `python3 <assets>/<script> [<inputs>...] < <input> > <output>`: the script is
a `.py` file in the world's assets (never outside it), the stage's `input` its stdin, its further
`inputs` its arguments, and what it prints the stage's output file. A script that exits non-zero
or runs past `seconds` fails the stage with its last stderr lines; nothing is written then.

It runs in the connector sandbox (ADR 0021) like every connector process, and the script with it:
no network, reads of the world's assets and the run's folders, writes of its stage's folder only.
It is a Connector Package (ADR 0019): `swarmify-connector.toml` beside it is everything Swarmify
knows of it.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("python_script")
SECONDS = 60
STDERR_LINES = 20


def run(
    script: str,
    output_path: str,
    assets_path: str,
    input_path: str = "",
    input_paths: list[str] | None = None,
    seconds: int = SECONDS,
) -> dict[str, Any]:
    """Run the script and write its stdout to `output_path`. With `input_paths` and no
    `input_path`, the first of them is stdin and the rest its arguments."""
    if not output_path:
        raise ToolError("python_script needs an output, the file its stdout becomes")
    if not assets_path:
        raise ToolError("python_script needs the world's assets folder")
    assets = Path(assets_path).resolve()
    path = (assets / script).resolve()
    if not path.is_relative_to(assets) or path.suffix != ".py" or not path.is_file():
        raise ToolError(f"{script} is not a .py file in the world's assets")
    paths = list(input_paths or [])
    stdin_path = input_path or (paths.pop(0) if paths else "")
    stdin = Path(stdin_path).read_bytes() if stdin_path else b""
    out = Path(output_path)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # the assets are read-only
    try:
        done = subprocess.run(
            [sys.executable, str(path), *paths],
            input=stdin,
            capture_output=True,
            timeout=seconds,
            cwd=out.parent,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{script} ran past its {seconds} s limit") from exc
    if done.returncode != 0:
        tail = "\n".join(done.stderr.decode(errors="replace").splitlines()[-STDERR_LINES:])
        raise ToolError(f"{script} exited {done.returncode}:\n{tail}")
    out.write_bytes(done.stdout)
    return {"output": str(out), "bytes": len(done.stdout), "script": script}


@mcp.tool(
    description="Run a world asset's Python script with the stage's input as stdin; its stdout"
    " becomes the stage's output file."
)
def python_script(
    script: str,
    idempotency_key: str,
    output_path: str = "",
    assets_path: str = "",
    input_path: str = "",
    input_paths: list[str] | None = None,
    seconds: int = SECONDS,
    frontmatter: dict[str, Any] | None = None,
    input: str = "",
    inputs: list[str] | None = None,
    output: str = "",
) -> dict[str, Any]:
    """`input`, `inputs`, `output` and `frontmatter` are the names the core also sends; the
    resolved paths are what count."""
    del idempotency_key, frontmatter, input, inputs, output  # a rerun simply runs it again
    return run(script, output_path, assets_path, input_path, input_paths, seconds)


if __name__ == "__main__":
    mcp.run("stdio")
