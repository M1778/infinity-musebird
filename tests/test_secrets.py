"""Secret-scan fixtures. Every value below is fake.

secret-scan: allow
"""

from __future__ import annotations

from musebird.guard.secrets import scan_text


def test_private_key_blocks():
    findings = scan_text("a.txt", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    assert any(f.kind == "private-key" for f in findings)


def test_common_tokens_block():
    text = (
        'aws = "AKIAIOSFODNN7EXAMPLE"\n'
        "gh = ghp_" + "a" * 36 + "\n"
        "slack = xoxb-1234567890-abcdef\n"
    )
    kinds = {f.kind for f in scan_text("config.py", text)}
    assert {"aws-access-key", "github-token", "slack-token"} <= kinds


def test_hatch_cookie_blocks():
    findings = scan_text("dump.txt", "hatch_sess=abcdef1234567890; path=/")
    assert any(f.kind == "hatch-cookie" for f in findings)


def test_allowlisted_placeholders_do_not_block():
    findings = scan_text("env.example", "AWS_SECRET_ACCESS_KEY='FAKEKEY'\napi_key = 'YOUR_KEY_GOES_HERE'\n")
    assert findings == []


def test_high_entropy_only_for_long_random_strings():
    findings = scan_text("x.py", "token = 'kJ8sL2mQ9xZ7vB4nR6tY1pW3cF5hG0dA'")
    assert any(f.kind == "high-entropy" for f in findings)
    assert scan_text("y.py", "word = 'hello-world-this-is-fine'") == []


def test_scan_reports_line_numbers_not_values():
    findings = scan_text("f", "\n\nghp_" + "b" * 36)
    assert findings[0].line == 3
    assert "ghp_" not in str(findings[0].to_dict())
