# gpu-control-center-mcp

Standalone stdio-to-Streamable-HTTP MCP bridge for [GPU Control Center](https://github.com/yankesswang/GPU-Control-Center).

Use this package when an external agent host (such as Claude Desktop or a stdio-only MCP client) needs to connect to a local or remote GPU Control Center MCP endpoint with Bearer token authentication and RBAC.

## Quick start with `uvx`

```bash
uvx --from ./mcp-package gpu-control-center-mcp \
  --url http://localhost:8090/mcp \
  --token gcc_mcp_your_token_here
```

Or install directly from GitHub:

```bash
uvx --from "git+https://github.com/yankesswang/GPU-Control-Center.git#subdirectory=mcp-package" \
  gpu-control-center-mcp \
  --url https://gpu.example.com/mcp \
  --token gcc_mcp_your_token_here
```

## Claude Desktop / Cursor (`mcpServers` stdio)

```json
{
  "mcpServers": {
    "gpu-control-center": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/yankesswang/GPU-Control-Center.git#subdirectory=mcp-package",
        "gpu-control-center-mcp"
      ],
      "env": {
        "GPU_CONTROL_CENTER_MCP_URL": "http://localhost:8090/mcp",
        "GPU_CONTROL_CENTER_MCP_TOKEN": "gcc_mcp_your_token_here"
      }
    }
  }
}
```
