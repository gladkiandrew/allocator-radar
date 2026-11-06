"""Provider adapter: subscription backend is default; API key never reaches the CLI; prompts are guarded."""
import json
import subprocess

import pytest
from pydantic import BaseModel

from radar import db
from radar.llm import provider as P
from radar.wall import live


class Out(BaseModel):
    action: str


def test_default_backend_is_subscription():
    assert isinstance(P.get_provider(), P.ClaudeCodeProvider)


def test_claude_code_call_strips_api_key_and_logs(monkeypatch):
    seen = {}

    def fake_run(cmd, input, capture_output, text, cwd, env, timeout):
        seen.update(cmd=cmd, env=env, cwd=cwd, input=input)
        out = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"action": "voted"},
               "usage": {"input_tokens": 5, "output_tokens": 3}, "modelUsage": {"claude-opus-5-5": {}},
               "total_cost_usd": 0.01}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    monkeypatch.setattr(subprocess, "run", fake_run)
    res, meta = P.ClaudeCodeProvider().complete(system="sys", user="The Board voted.", schema=Out, purpose="test")
    assert res.action == "voted" and meta.model == "claude-opus-5-5"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    i = seen["cmd"].index("--tools")
    assert seen["cmd"][i + 1] == "" and "--strict-mcp-config" in seen["cmd"] and "--no-session-persistence" in seen["cmd"]
    with db.connect() as con:
        assert live.check_egress(con) == []
        assert con.execute("SELECT count(*) FROM egress_log WHERE purpose = 'llm'").fetchone()[0] == 1


def test_unscrubbed_prompt_refused(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(P.UnscrubbedPrompt):
        P.ClaudeCodeProvider().complete(system="s", user="call 305-555-0100", schema=Out, purpose="test")
