"""The kit fixture's fake render, a stdio MCP server: it "renders" the Template `project` by
writing what it was given to the output file, as JSON, and the video's sidecar beside it, as a
real render does. The composition `Broken` fails."""

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("kit")


@mcp.tool(description="Render the Template `project` into the stage's output file.")
def kit(
    output_path: str,
    project: str,
    composition: str = "",
    input_path: str = "",
    idempotency_key: str = "",
    frontmatter: dict | None = None,
    assets_path: str = "",
) -> dict[str, Any]:
    if composition == "Broken":
        raise ToolError(f"kit render exited with 1: no composition {composition} in {project}")
    script = Path(input_path).read_text() if input_path else ""
    rendered = {"project": project, "script": script, "scriptPath": input_path}
    out = Path(output_path)
    out.write_text(json.dumps(rendered))
    head = {**(frontmatter or {}), "type": "video", "author": "kit", "file": out.name}
    front = "\n".join(f"{k}: {json.dumps(v)}" for k, v in head.items())
    out.with_name(f"{out.name}.md").write_text(f"---\n{front}\n---\n\n# {out.name}\n")
    return {"output": output_path}


if __name__ == "__main__":
    mcp.run("stdio")
