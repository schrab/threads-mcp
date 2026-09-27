"""Threads MCP server.

A Model Context Protocol (MCP) server that lets any MCP-capable agent publish
content to Threads via the official Meta Threads API:
https://developers.facebook.com/docs/threads
"""

from threads_mcp._version import __version__
from threads_mcp.server import mcp, main

__all__ = ["mcp", "main", "__version__"]
