"""End-to-end validation of all 9 MCP tools in ai-coding-memory.

Usage:
    AI_MEMORY_DATA_ROOT=<dir> python3 scripts/_mcp_validation_runner.py
If AI_MEMORY_DATA_ROOT not set, a fresh tempdir is created.

This is a validation harness (not a long-lived script). Safe to delete after run.
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path

# Setup paths
ROOT = Path("/Users/tiger/skills/ai-coding-memory")
sys.path.insert(0, str(ROOT / "mcp-server"))
sys.path.insert(0, str(ROOT))

# Setup isolated data root
DATA_ROOT = os.environ.get("AI_MEMORY_DATA_ROOT")
if not DATA_ROOT:
    DATA_ROOT = tempfile.mkdtemp(prefix="ai-mem-mcp-test-")
    os.environ["AI_MEMORY_DATA_ROOT"] = DATA_ROOT

print(f"==== AI_MEMORY_DATA_ROOT = {DATA_ROOT}")


def banner(title):
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


def head(label, value, limit=400):
    s = str(value).replace("\n", " | ")
    if len(s) > limit:
        s = s[:limit] + "..."
    print(f"  {label}: {s}")


import server as srv
from server import (
    search_memory,
    read_page,
    list_topics,
    project_context,
    remember,
    forget,
    pending_distill_count,
    get_next_distill_task,
    submit_distill_result,
)
from core import memory_store as ms
from core import task_pack
from core import recall_log
from core import agents_md_sync
from core.paths import (
    DATA_ROOT as PATHS_ROOT,
    PERSONAL_DIR,
    PROJECTS_DIR,
    PENDING_DIR,
    ARCHIVE_DIR,
    LOG_DIR,
)


print(f"  PATHS_ROOT actual = {PATHS_ROOT}")
assert str(PATHS_ROOT) == str(Path(DATA_ROOT).resolve()), (
    f"DATA_ROOT mismatch: {PATHS_ROOT} != {Path(DATA_ROOT).resolve()}"
)


WS1 = Path(DATA_ROOT) / "workspaces" / "repoA"
WS2 = Path(DATA_ROOT) / "workspaces" / "repoB"
WS_NOGIT = Path(DATA_ROOT) / "workspaces" / "nogit"


def make_git_workspace(path: Path, remote_url: str):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=path, check=True)


make_git_workspace(WS1, "git@github.com:acme/repo-a.git")
make_git_workspace(WS2, "https://github.com/acme/repo-b.git")
WS_NOGIT.mkdir(parents=True, exist_ok=True)


banner("Scenario 0: self-check / environment sanity")
head("PERSONAL_DIR exists", PERSONAL_DIR.exists())
head("PROJECTS_DIR exists", PROJECTS_DIR.exists())
head("PENDING_DIR exists", PENDING_DIR.exists())


# ====================================================================
banner("Scenario 1: cross-IDE remember then recall via search_memory")
out = remember(
    text="# Redis EVALSHA fallback\n\nUse SCRIPT LOAD then EVALSHA, fall back to EVAL on NOSCRIPT.",
    scope="auto",
    tags=["redis", "evalsha", "noscript", "cache"],
    workspace=str(WS_NOGIT),
    value="high",
)
head("remember(IDE-A personal) output", out, 600)

out = remember(
    text="# Acme repo-a auth pattern\n\nThe auth middleware rejects requests with X-Internal: true but missing JWT.",
    scope="project",
    tags=["auth", "middleware", "jwt"],
    workspace=str(WS1),
    value="high",
)
head("remember(IDE-B project repo-a) output", out, 600)

out = remember(
    text="# Acme repo-b config flag\n\nALWAYS_USE_CDN=true breaks local dev.",
    scope="project",
    tags=["config", "cdn", "local-dev"],
    workspace=str(WS2),
    value="medium",
)
head("remember(IDE-C project repo-b) output", out, 600)

print("  on-disk:")
for p in sorted(Path(DATA_ROOT).rglob("*.md")):
    if "workspaces" in str(p):
        continue
    print(f"    {p.relative_to(DATA_ROOT)}")

out = search_memory("EVALSHA", scope="auto", workspace=str(WS_NOGIT))
print(f"  search(EVALSHA) ws=nogit:")
print(out)
print()
assert "evalsha" in out.lower() or "Redis" in out, "expected to recall personal redis memory cross-IDE"
print("  PASS: personal memory recalled cross-IDE")


# ====================================================================
banner("Scenario 2: cross-project relevance (tags must overlap >= 2)")
# Search from repo-b for "middleware" - should still grep-hit repo-a's body which contains the word.
# Filter: fm_tags = [auth, middleware, jwt], q_words = {middleware} -> overlap=1 < 2 fails by tag.
# Title 'Acme repo-a auth pattern' jaccard with 'middleware' = 0 -> fails by title.
# So this query should NOT cross-pollinate from repo-a.
out = search_memory("middleware", scope="auto", workspace=str(WS2))
print("  search('middleware') from ws=repoB:")
print(out)
print()

# Now query with explicit tag words that overlap >=2 (auth + middleware), AND a body word for grep.
out2 = search_memory("auth middleware", scope="auto", workspace=str(WS2))
print("  search('auth middleware') from ws=repoB:")
print(out2)
print()
# Cross-project should kick in: fm_tags ∩ {auth, middleware} = 2 >= 2; grep for "auth middleware" -
# body contains "auth middleware rejects" -> substring "auth middleware" matches!
if "repo-a" in out2.lower() or "acme-repo-a" in out2.lower():
    print("  PASS: cross-project memory from repo-a surfaced when querying from repo-b")
else:
    print("  *** NOTE: cross-project did not surface even with overlapping tags + literal substring in body ***")


# ====================================================================
banner("Scenario 3: scope=current_project restricts to current project dir")
out = search_memory("auth middleware", scope="current_project", workspace=str(WS2))
print(out)
print()
if "未在你的个人知识库中找到" in out:
    print("  PASS: current_project=repo-b correctly returned empty for repo-a auth memory")
else:
    print("  NOTE: current_project may have unexpected hits")


# ====================================================================
banner("Scenario 4: source protection (manual file should reject auto overwrite)")
existing_mems = ms.list_memories(scope="personal")
redis_mem = next((m for m in existing_mems if "redis" in m.title.lower()), None)
assert redis_mem is not None, "redis personal memory missing"
print(f"  target id={redis_mem.id} source={redis_mem.source} value={redis_mem.value}")
fake_auto = ms.Memory(
    id=redis_mem.id,
    scope=redis_mem.scope,
    title=redis_mem.title,
    body="# Different body to attempt overwrite",
    source="auto",
    value="low",
    tags=[],
)
got_exception = None
try:
    ms.save(fake_auto)
except PermissionError as e:
    got_exception = e
print(f"  save(auto over manual) raised: {got_exception!r}")
assert got_exception is not None, "expected PermissionError when overwriting manual file"
print("  PASS: source=manual protection enforced")


# ====================================================================
banner("Scenario 5: conflict detection (bi-directional tagging)")
out = remember(
    text="# Redis EVAL alternative\n\nDirectly use EVAL when caching unavailable.",
    scope="auto",
    tags=["redis", "evalsha", "noscript", "alternative"],
    workspace=str(WS_NOGIT),
    value="medium",
)
head("remember (conflict candidate)", out, 400)
all_personal = ms.list_memories(scope="personal")
print(f"  personal count = {len(all_personal)}")
for m in all_personal:
    print(f"    id={m.id} source={m.source} conflicts={m.potential_conflicts} superseded_by={m.potentially_superseded_by}")

new_mem = next((m for m in all_personal if "alternative" in m.body.lower()), None)
old_mem = next((m for m in all_personal if "noscript" in m.body.lower() and m.id != (new_mem.id if new_mem else "")), None)
assert new_mem is not None, "new memory not found"
assert old_mem is not None, "old memory not found"
assert new_mem.potential_conflicts, "expected potential_conflicts on new memory"
assert old_mem.id in new_mem.potential_conflicts, "old id should be in new.potential_conflicts"
assert new_mem.id in old_mem.potentially_superseded_by, (
    f"old.potentially_superseded_by should include new id; got {old_mem.potentially_superseded_by}"
)
print("  PASS: bi-directional conflict markers")


# ====================================================================
banner("Scenario 6: archive then restore (forget tool)")
target_id = old_mem.id
out = forget(target_id)
print(out)
assert "已归档" in out
arch_path = ARCHIVE_DIR / f"{target_id}.md"
assert arch_path.exists(), f"archived file missing: {arch_path}"
print(f"  archive file exists: {arch_path}")
out2 = search_memory("EVALSHA NOSCRIPT", scope="auto", workspace=str(WS_NOGIT))
print(f"  search after archive (first 500 chars):")
print(out2[:500])
assert target_id not in out2, "archived memory should not be returned by search"

restored = ms.restore(target_id)
print(f"  restored to: {restored}")
assert restored is not None and restored.exists()
print("  PASS: archive/restore round-trip")


# ====================================================================
banner("Scenario 7: read_page (security + truncation + log)")
some_md = next(PERSONAL_DIR.glob("*.md"))
out = read_page(str(some_md))
head("read_page valid (personal/*.md)", out, 400)
if "Redis" in out or "redis" in out:
    print("  PASS: read_page on personal/*.md works")
else:
    print("  *** BUG-1: read_page REJECTED a valid personal/*.md file. ***")
    print("  *** root cause: _is_path_inside_wiki() checks WIKI_ROOT (~/.ai-memory/wiki/) ***")
    print("  *** but the new data model stores memories under personal/ and projects/    ***")

# also test on a projects/*.md file
proj_md = None
for d in PROJECTS_DIR.iterdir() if PROJECTS_DIR.exists() else []:
    for f in d.glob("*.md"):
        proj_md = f
        break
    if proj_md:
        break
if proj_md:
    out = read_page(str(proj_md))
    head("read_page valid (projects/*.md)", out, 300)
    if "拒绝读取" in out:
        print("  *** BUG-1 (cont.): read_page also REJECTED projects/*.md ***")

out_bad = read_page("/etc/passwd")
head("read_page /etc/passwd", out_bad, 200)
assert "拒绝读取" in out_bad

out_nx = read_page(str(PERSONAL_DIR / "no-such-file.md"))
head("read_page missing", out_nx, 200)
# under current buggy code this returns 拒绝读取 instead of 文件不存在 because the safety check fires first
if "文件不存在" in out_nx:
    print("  read_page missing-file path uses correct branch")
else:
    print(f"  NOTE: missing-file path was short-circuited by safety check (because file lives outside wiki/)")


# ====================================================================
banner("Scenario 8: project_context + AGENTS.md sync")
out = project_context(str(WS1))
print(out)
agents_md = WS1 / "AGENTS.md"
assert agents_md.exists(), "AGENTS.md should be written for project workspace"
print(f"  AGENTS.md created at: {agents_md}")
content = agents_md.read_text()
print("  AGENTS.md marker block detection:")
print(f"    contains MARKER_START: {agents_md_sync.MARKER_START in content}")
print(f"    contains MARKER_END:   {agents_md_sync.MARKER_END in content}")
agents_md.write_text(f"# user pre-existing readme\n\n{content}")
project_context(str(WS1))
content2 = agents_md.read_text()
assert "user pre-existing readme" in content2, "user content above marker block should be preserved"
print("  PASS: AGENTS.md keeps user content + has marker block")

out_nogit = project_context(str(WS_NOGIT))
head("project_context(nogit)", out_nogit, 300)
assert "不在 git" in out_nogit

WS_EMPTY = Path(DATA_ROOT) / "workspaces" / "empty-repo"
make_git_workspace(WS_EMPTY, "git@github.com:acme/empty-repo.git")
out_empty = project_context(str(WS_EMPTY))
head("project_context(empty-repo no memories)", out_empty, 300)


# ====================================================================
banner("Scenario 9: list_topics across scopes")
out = list_topics(scope="all", workspace=str(WS1))
print(out)


# ====================================================================
banner("Scenario 10: host_agent task pack full lifecycle")
n0 = pending_distill_count()
head("pending_distill_count (initial)", n0, 200)
assert "暂无" in n0

task_id = task_pack.write_task(
    prompt="Synthetic prompt for testing.\nReturn topics.",
    session={"sessionId": "sess-1", "ide": "cursor", "workspace": str(WS1)},
    project_key="github.com/acme/repo-a",
)
print(f"  wrote task_id = {task_id}")

n1 = pending_distill_count()
head("pending_distill_count (after write)", n1, 200)
assert "1 个待整理" in n1

task_blob = get_next_distill_task()
print("  get_next_distill_task output:")
print(task_blob)
assert f"TASK_ID: {task_id}" in task_blob

assert (PENDING_DIR / f"{task_id}.task.in_progress").exists()
assert not (PENDING_DIR / f"{task_id}.task").exists()

n2 = pending_distill_count()
head("pending_distill_count (after take)", n2, 200)

yaml_result = """topics:
  - title: synthetic memory from task pack
    summary: from test
    scope: project
    value: medium
    tags: [task-pack, test, synthetic]
    should_keep: true
    body: |
      # synthetic memory from task pack

      body from submit_result
    keep_reason: useful test
  - title: junk to cold
    summary: low value
    scope: personal
    value: low
    tags: [junk]
    should_keep: false
    body: |
      # junk to cold

      content
    keep_reason: noise
"""
out = submit_distill_result(task_id, yaml_result)
print("  submit_distill_result output:")
print(out)

assert not (PENDING_DIR / f"{task_id}.task.in_progress").exists()
proj_dir = PROJECTS_DIR / "github.com_acme_repo-a"
written = list(proj_dir.glob("*.md"))
print(f"  projects/repo-a now contains {len(written)} files:")
for p in written:
    print(f"    {p.name}")
# cold concept removed: should_keep=false now directly dropped (no .cold/ dir)
assert any("synthetic" in p.name for p in written), "synthetic memory should land in project dir"
print("  PASS: task pack full lifecycle (cold concept removed)")


# ====================================================================
banner("Scenario 11: submit with bad task_id")
out = submit_distill_result("deadbeef0000", "topics: []")
head("submit(bad task_id)", out, 300)
assert "未找到" in out
print("  PASS: bad task_id error path")


# ====================================================================
banner("Scenario 12: error paths - empty remember / missing memory_id in forget")
out = remember("", workspace=str(WS1))
head("remember empty text", out, 200)
assert "失败" in out

out = forget("does-not-exist-xxx")
head("forget non-existent id", out, 200)
assert "未找到" in out

out = project_context("")
head("project_context empty workspace", out, 200)
assert "需要 workspace" in out

out = read_page(str(PERSONAL_DIR))
head("read_page on directory", out, 200)
if "不是文件" in out:
    print("  read_page on dir uses correct branch")
else:
    print("  NOTE: read_page on dir short-circuited by WIKI_ROOT safety check (BUG-1 cascade)")
print("  PASS: error paths (other than BUG-1 cascade)")


# ====================================================================
banner("Scenario 13: recall log written by search_memory + read_page")
search_memory("redis", scope="auto", workspace=str(WS1))
some_md = next(PERSONAL_DIR.glob("*.md"))
read_page(str(some_md))
log_files = list(LOG_DIR.glob("recall-*.jsonl"))
print(f"  log files: {log_files}")
assert log_files, "expected at least one recall log"
content = log_files[0].read_text()
print("  last log lines (up to 6):")
for ln in content.strip().splitlines()[-6:]:
    print(f"    {ln}")
assert "search" in content
if "read" in content:
    print("  PASS: recall log records both search + read")
else:
    print("  *** BUG-1 cascade: no 'read' events logged because read_page is broken ***")


# ====================================================================
banner("Scenario 14: stats CLI works on isolated data")
result = subprocess.run(
    [sys.executable, str(ROOT / "cli" / "ai_memory.py"), "stats"],
    capture_output=True, text=True, env={**os.environ},
)
print("  stdout:")
print(result.stdout)
if result.stderr:
    print("  stderr:")
    print(result.stderr)


# ====================================================================
banner("Scenario 15: mtime-based auto upgrade to source=edited")
synthetic = next((m for m in ms.list_memories() if "synthetic" in m.title.lower()), None)
assert synthetic is not None
print(f"  synthetic before: source={synthetic.source}, _mtime_at_write={synthetic._mtime_at_write}")
path = synthetic.file_path
text = path.read_text()
time.sleep(7)
path.write_text(text + "\n\n<!-- user edited -->\n")
reloaded = ms.load(path)
print(f"  synthetic after edit: source={reloaded.source}, _mtime_at_write={reloaded._mtime_at_write}, file mtime now={path.stat().st_mtime}")
assert reloaded.source == "edited", f"expected source=edited after user edit, got {reloaded.source}"
print("  PASS: mtime auto-upgrade to source=edited")


# ====================================================================
banner("Scenario 16: search reflects edited boost (source=edited * 1.2)")
out = search_memory("synthetic memory", scope="auto", workspace=str(WS1))
print(out)


# ====================================================================
banner("Scenario 17: read_page truncation on huge file")
huge_path = PERSONAL_DIR / "2026-05-16-huge-test-0000.md"
big_body = "x" * 80_000
huge_text = "---\nid: 2026-05-16-huge-test-0000\nscope: personal\nsource: manual\nvalue: low\n---\n\n# huge\n\n" + big_body
huge_path.write_text(huge_text)
out = read_page(str(huge_path))
print(f"  output length: {len(out)} (file was {huge_path.stat().st_size} bytes)")
if "截断" in out or ("truncated" in out.lower() and "拒绝" not in out):
    print("  PASS: read_page truncates large file")
else:
    print("  *** BUG-1 cascade: read_page rejected the huge file too; truncation path unreachable ***")


# ====================================================================
banner("Scenario 18: pending_distill_count with in_progress only")
task_id_b = task_pack.write_task(
    prompt="another",
    session={"sessionId":"x","ide":"qoder","workspace":str(WS1)},
    project_key="github.com/acme/repo-a",
)
get_next_distill_task()
out = pending_distill_count()
head("pending_distill_count (only in_progress)", out, 300)


# ====================================================================
banner("Scenario 19: list_topics empty current_project (no memories yet)")
WS_FRESH = Path(DATA_ROOT) / "workspaces" / "fresh-repo"
make_git_workspace(WS_FRESH, "git@github.com:acme/fresh-repo.git")
out = list_topics(scope="current_project", workspace=str(WS_FRESH))
print(out)


# ====================================================================
banner("Scenario 20: project_key normalization (ssh vs https same)")
from core.project_key import resolve_project_key
infoA = resolve_project_key(str(WS1))
infoB = resolve_project_key(str(WS2))
print(f"  WS1 key: {infoA}")
print(f"  WS2 key: {infoB}")
WS3 = Path(DATA_ROOT) / "workspaces" / "repoA-https-clone"
make_git_workspace(WS3, "https://github.com/acme/repo-a.git")
infoC = resolve_project_key(str(WS3))
print(f"  WS3 (https same repo as ssh WS1) key: {infoC}")
assert infoA["key"] == infoC["key"], f"https vs ssh should produce same key: {infoA['key']} vs {infoC['key']}"
print("  PASS: project_key normalization")


# ====================================================================
banner("Scenario 21: scope=auto without workspace")
out = search_memory("redis", scope="auto", workspace=None)
print(out[:600])


# ====================================================================
banner("Scenario 22: scope=all output")
out = search_memory("redis", scope="all", workspace=None)
print(out[:1500])


# ====================================================================
banner("Scenario 23: get_next_distill_task when no pending")
# After scenario 18 we should have only in_progress (no .task). Confirm get_next returns 暂无.
out = get_next_distill_task()
head("get_next_distill_task (no pending)", out, 200)

# ====================================================================
banner("Scenario 24: remember scope='project' without workspace -> falls back to personal")
out = remember(
    text="# Project scope without workspace",
    scope="project",
    tags=["fallback"],
    workspace=None,
    value="low",
)
head("remember(project, no workspace) output", out, 400)
# Should have ended up in personal dir (scope auto-fallback)
last = ms.list_memories()[0]
print(f"  last memory: scope={last.scope}, project_key={last.project_key}")

# ====================================================================
banner("Scenario 25: edited boost NOT applied in search (load-time upgrade not persisted)")
# Synthetic memory had source upgraded to 'edited' in scenario 15, but the file on disk
# still has source=auto in frontmatter. searcher uses parse_fm directly, not load().
syn_path = next(PERSONAL_DIR.parent.rglob("*synthetic-memory*"))
text_now = syn_path.read_text()
from core.frontmatter import parse as parse_fm
fm_now, _ = parse_fm(text_now)
print(f"  on-disk source: {fm_now.get('source')}")
mem_now = ms.load(syn_path)
print(f"  ms.load() reports source: {mem_now.source}  (upgrade detected in memory)")
print(f"  → searcher reads frontmatter directly → boost (1.2) is NOT applied until next save()")

# ====================================================================
banner("Scenario 26: write_task -> file actually persisted")
import json as _json
task_id_c = task_pack.write_task(
    prompt="prompt c", session={"sessionId":"s","ide":"qoder","workspace":""},
    project_key=None,
)
written = PENDING_DIR / f"{task_id_c}.task"
print(f"  task file exists: {written.exists()}")
print(f"  file content (first 200 chars): {written.read_text()[:200]}")
parsed = _json.loads(written.read_text())
print(f"  parsed keys: {sorted(parsed.keys())}")
print(f"  project_key value: {parsed.get('project_key')!r} (should be 'null' string)")

# ====================================================================
banner("Scenario 27: forget on archived id (idempotency)")
to_arch_id = ms.list_memories()[0].id
out1 = forget(to_arch_id)
head("forget once", out1, 200)
out2 = forget(to_arch_id)
head("forget again (already archived)", out2, 200)
if "未找到" in out2:
    print("  Note: a second forget of same id returns 未找到 (memory already moved to archive/)")
else:
    print(f"  CHECK: idempotent behavior may differ: {out2[:200]}")

# ====================================================================
banner("Scenario 28: search special chars in query (regex safety)")
ms.save(ms.Memory(
    id=ms.make_id("regex-test"),
    scope="personal",
    title="regex-test",
    body="# regex-test\n\nUse `re.escape()` to handle [brackets] and (parens) safely.",
    source="manual",
    value="medium",
    tags=[],
))
out = search_memory("(parens)", scope="personal")
print(out[:600])

# ====================================================================
banner("Scenario 29: submit malformed YAML")
task_id_d = task_pack.write_task(
    prompt="x", session={"sessionId":"y","ide":"z","workspace":""},
    project_key=None,
)
get_next_distill_task()
out = submit_distill_result(task_id_d, "this is :: not :: valid : yaml\n[[[")
print(out)

# ====================================================================
print()
print("=" * 80)
print("  ALL SCENARIOS COMPLETED")
print("=" * 80)
print(f"  DATA_ROOT = {DATA_ROOT}")
