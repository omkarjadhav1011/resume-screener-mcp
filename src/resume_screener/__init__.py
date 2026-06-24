"""Resume Screener MCP — a local stdio MCP server for Claude Code.

The server extracts text from PDF/DOCX resumes and prepares "judging packets"
for Claude Code to score against a job description. The server itself never
calls an LLM; Claude Code is the judge.
"""

__version__ = "0.1.0"
