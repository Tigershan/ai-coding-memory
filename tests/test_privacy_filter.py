"""privacy_filter 单测：正例必命中、反例不误伤。"""
from __future__ import annotations

from core.privacy_filter import redact, total_hits


# ==================== 正例（必须命中） ====================

def test_aws_access_key():
    text = "我用的是 AKIAIOSFODNN7EXAMPLE 这个 key"
    out, counts = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert counts.get("aws_access_key") == 1
    assert "<REDACTED:aws_access_key>" in out


def test_aws_secret_with_context():
    text = 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'
    out, counts = redact(text)
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in out
    assert counts.get("aws_secret_key") == 1


def test_openai_token():
    text = "OPENAI_API_KEY=sk-abc1234567890ABCDEFGHIJKLMNOP"
    out, counts = redact(text)
    assert "sk-abc1234567890ABCDEFGHIJKLMNOP" not in out
    # 可能被 openai_token 或 generic_secret_kv 命中其一即可
    assert total_hits(counts) >= 1


def test_anthropic_token():
    text = "use sk-ant-api03-AAAA1111BBBB2222CCCC3333DDDD"
    out, counts = redact(text)
    assert "sk-ant-api03-AAAA1111BBBB2222CCCC3333DDDD" not in out
    assert counts.get("openai_token") == 1


def test_slack_token():
    text = "webhook header: xoxb-THIS-IS-A-FAKE-TOKEN-FOR-TESTING-ONLY"
    out, counts = redact(text)
    assert "xoxb-THIS-IS-A-FAKE-TOKEN-FOR-TESTING-ONLY" not in out
    assert counts.get("slack_token") == 1


def test_github_token():
    text = "git remote set-url origin https://x:ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@github.com/foo/bar.git"
    out, counts = redact(text)
    assert "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in out
    assert counts.get("github_token") == 1


def test_jwt():
    text = (
        "Authorization: Bearer "
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36"
    )
    out, counts = redact(text)
    assert "SflKxwRJSMeKKF2QT4fwpMeJf36" not in out
    assert counts.get("jwt") == 1


def test_jdbc_password():
    text = "url: jdbc:mysql://host:3306/db?user=admin&password=Secret123!"
    out, counts = redact(text)
    assert "Secret123!" not in out
    assert "password=" in out  # prefix 保留
    assert counts.get("jdbc_password") == 1


def test_generic_password_assignment():
    text = 'password = "MySecretPwd99"'
    out, counts = redact(text)
    assert "MySecretPwd99" not in out
    assert counts.get("generic_secret_kv") == 1


def test_rsa_private_key_block():
    text = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abc\n"
        "moredata\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    out, counts = redact(text)
    assert "MIIEpAIBAAKCAQEA" not in out
    assert "before" in out and "after" in out
    assert counts.get("private_key_block") == 1


# ==================== 反例（不应误伤） ====================

def test_normal_code_unaffected():
    text = """
def hello(name: str) -> str:
    return f"Hello, {name}!"

x = 12345
y = "some normal string"
"""
    out, counts = redact(text)
    assert out == text
    assert total_hits(counts) == 0


def test_commit_sha_not_redacted():
    text = "git checkout 1a2b3c4d5e6f7890abcdef1234567890abcdef12"
    out, counts = redact(text)
    # 40-字符 hex 没有 secret/aws/key 上下文，不应命中 aws_secret_contextual
    assert out == text
    assert total_hits(counts) == 0


def test_uuid_not_redacted():
    text = "user_id = 'b4f3a2c1-7890-4321-abcd-ef0123456789'"
    out, counts = redact(text)
    # 关键字 user_id 不在 generic_secret_kv 的字段名集合里
    assert out == text
    assert total_hits(counts) == 0


def test_empty_input():
    out, counts = redact("")
    assert out == ""
    assert counts == {}


def test_non_string_input_returns_safe():
    out, counts = redact(None)  # type: ignore[arg-type]
    assert out == ""
    assert counts == {}
