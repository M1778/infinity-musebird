from __future__ import annotations

import pytest

from musebird.errors import GuardViolation
from musebird.guard.ignore import Ignore
from musebird.guard.paths import Caps, check_upload, resolve_within, safe_member


@pytest.fixture()
def proj(tmp_path):
    p = tmp_path / "p"
    p.mkdir()
    (p / ".env").write_text("SECRET=1")
    (p / "src").mkdir()
    (p / "src" / "app.py").write_text("print(1)")
    (p / "node_modules").mkdir()
    (p / "node_modules" / "x.js").write_text("x")
    return p


def test_defaults_cover_dangerous_files(proj):
    ignore = Ignore(proj)
    assert ignore.ignored(proj / ".env")
    assert ignore.ignored(proj / "node_modules" / "x.js")
    assert not ignore.ignored(proj / "src" / "app.py")


def test_custom_patterns_and_negation(proj):
    (proj / ".musebirdignore").write_text("*.md\n!README.md\nbuild/\n")
    (proj / "NOTES.md").write_text("x")
    (proj / "README.md").write_text("x")
    ignore = Ignore(proj)
    assert ignore.ignored(proj / "NOTES.md")
    assert not ignore.ignored(proj / "README.md")


def test_outside_project_is_ignored(proj, tmp_path):
    outside = tmp_path / "other.txt"
    outside.write_text("x")
    assert Ignore(proj).ignored(outside)


def test_resolve_within_blocks_traversal(proj):
    with pytest.raises(GuardViolation):
        resolve_within(proj, "../escape.txt")
    with pytest.raises(GuardViolation):
        resolve_within(proj, "/etc/passwd")
    assert resolve_within(proj, "src/app.py") == (proj / "src" / "app.py").resolve()


def test_check_upload_walks_dirs_and_enforces_caps(proj):
    files = check_upload([proj / "src"], proj)
    assert [f.name for f in files] == ["app.py"]
    with pytest.raises(GuardViolation):
        check_upload([proj / "src"], proj, Caps(max_file=1))
    # .musebirdignore decides what is sent; check_upload only enforces path/size rules.
    assert [f.name for f in check_upload([proj / ".env"], proj)] == [".env"]
    with pytest.raises(GuardViolation):
        check_upload([proj / ".env"], proj, Caps(max_file=4))


def test_check_upload_rejects_symlinks(proj, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (proj / "link.txt").symlink_to(outside)
    with pytest.raises(GuardViolation):
        check_upload([proj / "link.txt"], proj)


def test_safe_member(proj):
    assert safe_member(proj, "a/b.txt") == (proj / "a/b.txt").resolve()
    for bad in ("../x", "/abs", "a/../../x", "C:/x"):
        with pytest.raises(GuardViolation):
            safe_member(proj, bad)


@pytest.mark.parametrize(
    "bad",
    [
        "../x",
        "..\\x",
        "a/../../x",
        "a\\..\\..\\x",
        "/abs",
        "\\abs",
        "C:/Windows/evil.dll",
        "C:\\Windows\\evil.dll",
        "\\\\server\\share\\x",
    ],
)
def test_safe_member_blocks_every_separator_variant(proj, bad):
    """Archive members may use either separator; both must be rejected."""
    with pytest.raises(GuardViolation):
        safe_member(proj, bad)


def test_safe_member_allows_normal_nested_paths(proj):
    assert safe_member(proj, "a/b.txt") == (proj / "a" / "b.txt").resolve()
    assert safe_member(proj, "./a/b.txt") == (proj / "a" / "b.txt").resolve()
