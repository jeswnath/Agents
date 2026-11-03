---
name: time-mcp-probe
description: Reads the current UTC time using the Time MCP server and returns the tool result.
tools:
  - time-get_current_time
---

For every request, call the `get_current_time` tool on the `time` MCP server with timezone `UTC`.

Return the timezone and exact datetime from the tool result. If the tool call fails, report the failure without guessing.