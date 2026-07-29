#!/usr/bin/env python3
# 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
# 編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
# source: UnityTemplate_2022_3_22f1/scripts/pipeline/verify_repo_guide.py
# source-sha256: 47cb2f6204872ba7d673e1d9ebab45b3b005a64a2eeccc2900da956fb4b6a16d
"""リポジトリガイドと実装の整合を機械検証する（ゴールド標準 §2.10 第2層）。

原則: **文書がリポジトリ自身の状態について主張することは、すべて機械で確かめられる。**
数や状態を手書きするなら、同じ事実を検査するアサーションを必ず対にする。

判定の主役は `pipeline/repo.json` の**構造化された宣言**で、自然言語のヒューリスティックは
補助（warn）に留める。誤検出は検査を消すのではなく、理由付きの waiver で個別に逃がす。

使い方（対象リポジトリのルートで実行）:
    python3 scripts/pipeline/verify_repo_guide.py            # 検査（error があれば非ゼロ終了）
    python3 scripts/pipeline/verify_repo_guide.py --strict   # warn も失敗として扱う
    python3 scripts/pipeline/verify_repo_guide.py --json     # 機械可読の結果を出力

検査対象の細則:
- `.meta` 検査はパッケージルート自身を対象にしない（UPM 慣例で root は `.meta` を持たない。
  root の `.meta` が**ある**ことは逆に error）。Unity が無視する名前のフォルダは subtree ごと
  除外し、symlink はファイル・フォルダとも追跡しない。
- パス長は **git 追跡パスの `/` 区切り論理文字列**を基準に、`.meta` を含めて計測する
  （OS のファイルシステム表現に依存させない）。

正本: UnityTemplate_2022_3_22f1/scripts/pipeline/verify_repo_guide.py
各開発リポジトリへは scripts/distribute_standard.py が配布する（配布物は編集しない）。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

ERROR = "error"
WARN = "warn"

SUPPORTED_CONFIG_SCHEMA = 1

# Unity が無視するファイル・フォルダ名（.meta を持たない）
IGNORED_NAME_PATTERNS = (".*", "*~", "*.tmp", "cvs", "CVS")

# 検査 2: リポジトリルート起点とみなすバッククォート内パスの接頭辞
REPO_ROOT_PREFIXES = (
    "Packages/",
    "Assets/",
    "ProjectSettings/",
    "Publish/",
    "docs/",
    "scripts/",
    "pipeline/",
    ".claude/",
    ".agents/",
    ".github/",
)

# 検査 2: この語が同じ行にあるパスは「他リポジトリの話」としてここからは検証しない
FOREIGN_REPO_MARKERS = (
    "MySite",
    "external-content",
    "テンプレートリポジトリ",
    "UnityTemplate",
    "基盤側",
    "基盤リポジトリ",
    "利用者",
    "購入者",
    "ホストプロジェクト側",
    "別リポジトリ",
    "他リポジトリ",
)

# 検査 4: テスト整備状況の否定的な主張（補助的な検出。判定の主役は packagePolicies）
TEST_SUBJECT_RE = re.compile(r"(EditMode|テスト|Tests|testables)")
NEGATIVE_CLAIM_RE = re.compile(r"(未整備|未登録|未導入|存在しない|ありません|は無い|はない|が無い|がない)")

# 検査 10: スキル名の形（kebab-case・2 語以上）
SKILL_TOKEN_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
SKILL_TOKEN_DENYLIST = {
    "kebab-case", "camel-case", "pascal-case", "snake-case",
    "es-419", "zh-hans", "zh-hant", "pt-br", "pt-pt", "es-es",
    "keep-a-changelog", "read-only", "fail-closed", "pay-what-you-want",
    "no-refunds", "unity-editor-extension", "custom-eula",
}
FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)

GENERATED_MARKER = "source-sha256:"
GENERATED_HEADER_MARKER = "生成物:"
GENERATED_SHA_RE = re.compile(r"source-sha256:\s*([0-9a-f]{64})")

MAX_PATH_LENGTH = 150

DISTRIBUTED_FILES = (
    "docs/GOLD_STANDARD.md",
    "docs/REPOSITORY_MAP.md",
    ".github/workflows/pipeline-verify.yml",
)
DISTRIBUTED_GLOBS = ("scripts/pipeline/*.py",)
# 標準の正本リポジトリ（テンプレート）で「正本そのもの」であるファイル。
# ここに無い配布物（レジストリ由来の地図など）はテンプレートでも生成物として扱う。
CANONICAL_IN_TEMPLATE = ("docs/GOLD_STANDARD.md",)
CANONICAL_GLOBS_IN_TEMPLATE = ("scripts/pipeline/*.py",)

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_CHECK_IDS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "+"}
VALID_ROLES = {"standard", "product", "site", "content", "infra", "sandbox"}
ARTIFACT_KINDS = {"sale-zip", "tgz", "unitypackage", "vpm-zip", "pdf"}


# ---------------------------------------------------------------------------
# 結果
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    check: str
    severity: str
    message: str
    path: str | None = None


def collapse_findings(findings: list[Finding]) -> list[str]:
    """同じ指摘が多数のファイルで出る場合に 1 行へまとめる（翻訳 README 等で埋もれるのを防ぐ）。"""
    grouped: dict[tuple[str, str, str], list[str]] = {}
    order: list[tuple[str, str, str]] = []
    for finding in findings:
        key = (finding.check, finding.severity, finding.message)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if finding.path and finding.path not in grouped[key]:
            grouped[key].append(finding.path)

    lines = []
    for check, severity, message in order:
        paths = grouped[(check, severity, message)]
        mark = "ERROR" if severity == ERROR else "WARN "
        if not paths:
            lines.append(f"{mark} 検査{check}: {message}")
        elif len(paths) <= 3:
            lines.append(f"{mark} 検査{check}: {message} [{', '.join(paths)}]")
        else:
            lines.append(f"{mark} 検査{check}: {message} [{', '.join(paths[:3])} ほか {len(paths) - 3} 件]")
    return lines


@dataclass
class RepoContext:
    root: Path
    config: dict
    tracked: set[str]
    tracked_dirs: set[str] = field(default_factory=set)
    packages: list[tuple[str, Path, dict]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, severity: str, message: str, path: str | None = None) -> None:
        self.findings.append(Finding(check, severity, message, path))

    @property
    def role(self) -> str:
        return self.config.get("role", "product")

    @property
    def is_product(self) -> bool:
        return self.role == "product"

    @property
    def is_standard_source(self) -> bool:
        return self.role == "standard"

    def waivers_for(self, check: str) -> list[dict]:
        result = []
        for waiver in self.config.get("waivers") or []:
            if isinstance(waiver, dict) and str(waiver.get("checkId")) == check:
                result.append(waiver)
        return result

    def is_waived(self, check: str, target: str) -> bool:
        """完全一致か glob 一致のみを認める（部分文字列一致は範囲が読めないため使わない）。"""
        for waiver in self.waivers_for(check):
            pattern = str(waiver.get("target", ""))
            if pattern and (pattern == target or fnmatch.fnmatch(target, pattern)):
                return True
        return False


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else ""


def load_json(path: Path) -> dict | None:
    """JSON をオブジェクトとして読む。壊れている・オブジェクトでない場合は None。"""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def is_unity_ignored_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORED_NAME_PATTERNS)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_generated_header(text: str) -> tuple[str | None, str]:
    """生成物ヘッダを取り除き (宣言された sha256, 正本と同一になるはずの本文) を返す。

    除去規則は distribute_standard.py の挿入規則と対になっている。
    """
    match = GENERATED_SHA_RE.search(text)
    if not match:
        return None, text
    declared = match.group(1)

    if text.lstrip().startswith("<!--"):
        start = text.index("<!--")
        end = text.find("-->", start)
        if end == -1:
            return declared, text
        return declared, (text[:start] + text[end + 3 :]).lstrip("\n")

    lines = text.splitlines(keepends=True)
    output: list[str] = []
    in_header = False
    done = False
    for line in lines:
        if not done and not in_header and line.startswith("#") and GENERATED_HEADER_MARKER in line:
            in_header = True
            continue
        if in_header:
            if line.startswith("#"):
                continue
            in_header, done = False, True
            if line.strip() == "":
                continue
        output.append(line)
    return declared, "".join(output)


def collect_doc_files(ctx: RepoContext) -> list[Path]:
    """検査対象の「このリポジトリ自身が書いた文書」を集める（生成物・規範文書は除外）。"""
    candidates: list[Path] = []
    for name in ("CLAUDE.md", "AGENTS.md", "README.md"):
        path = ctx.root / name
        if path.is_file():
            candidates.append(path)
    claude, agents = ctx.root / "CLAUDE.md", ctx.root / "AGENTS.md"
    if claude.is_file() and agents.is_file() and claude.read_bytes() == agents.read_bytes():
        candidates = [p for p in candidates if p != agents]
    docs_dir = ctx.root / "docs"
    if docs_dir.is_dir():
        candidates.extend(sorted(docs_dir.rglob("*.md")))

    result = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if GENERATED_MARKER in text[:1000]:
            continue  # 配布された生成物は正本側で検査する
        rel = path.relative_to(ctx.root).as_posix()
        if any(rel == str(item) or fnmatch.fnmatch(rel, str(item)) for item in ctx.config.get("normativeDocs") or []):
            continue
        result.append(path)
    return result


def walk_package(package_dir: Path):
    """パッケージ配下を Unity 無視名と symlink を除いて走査する。"""
    for current, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = [
            name
            for name in dirnames
            if not is_unity_ignored_name(name) and not (Path(current) / name).is_symlink()
        ]
        files = [
            name
            for name in filenames
            if not is_unity_ignored_name(name) and not (Path(current) / name).is_symlink()
        ]
        yield Path(current), dirnames, files


# ---------------------------------------------------------------------------
# 検査 0（枠外）: pipeline/repo.json 自体の妥当性
# ---------------------------------------------------------------------------


def check_00_config(ctx: RepoContext) -> None:
    config_path = ctx.root / "pipeline" / "repo.json"
    if not config_path.is_file():
        severity = ERROR if ctx.is_product else WARN
        ctx.add("+", severity, "pipeline/repo.json がありません（宣言が無いと構造化検査ができません）")
        return

    schema = ctx.config.get("$schemaVersion")
    if schema != SUPPORTED_CONFIG_SCHEMA:
        # 未知のスキーマで黙って通すと、検査していないのに通ったように見える
        ctx.add("+", ERROR, f"pipeline/repo.json の $schemaVersion が未対応です: {schema}（対応: {SUPPORTED_CONFIG_SCHEMA}）")
        return

    if ctx.role not in VALID_ROLES:
        ctx.add("+", ERROR, f"pipeline/repo.json の role が不正です: {ctx.role}（{'/'.join(sorted(VALID_ROLES))}）")
    if "standardProfile" in ctx.config:
        ctx.add(
            "+",
            ERROR,
            "pipeline/repo.json に standardProfile があります。配布プロファイルの正本は MySite の pipeline/repositories.json です",
        )

    today = os.environ.get("PIPELINE_TODAY") or run_git(ctx.root, "log", "-1", "--format=%cs").strip()
    for index, waiver in enumerate(ctx.config.get("waivers") or []):
        label = f"waivers[{index}]"
        if not isinstance(waiver, dict):
            ctx.add("+", ERROR, f"{label} はオブジェクトである必要があります（checkId / target / reason / expiresAt）")
            continue
        check_id = str(waiver.get("checkId", ""))
        if check_id not in VALID_CHECK_IDS:
            ctx.add("+", ERROR, f"{label} の checkId が未知です: {check_id or '(未設定)'}")
        if not str(waiver.get("target", "")).strip():
            ctx.add("+", ERROR, f"{label} に target がありません")
        if not str(waiver.get("reason", "")).strip():
            ctx.add("+", ERROR, f"{label} に reason がありません（理由の無い例外は認めない）")
        expires = waiver.get("expiresAt")
        if expires is not None:
            if not ISO_DATE_RE.match(str(expires)):
                ctx.add("+", ERROR, f"{label} の expiresAt は YYYY-MM-DD 形式で書いてください: {expires}")
            elif today and str(expires) < today:
                ctx.add("+", ERROR, f"{label} は {expires} に期限切れです: {waiver.get('target')}")

    if ctx.is_product:
        for key in ("productSlug", "saleUnit"):
            if not ctx.config.get(key):
                ctx.add("+", ERROR, f"pipeline/repo.json に {key} がありません（product には必須）")
        sale_unit = ctx.config.get("saleUnit") or {}
        if sale_unit:
            distribution = set(sale_unit.get("distribution") or [])
            # パッケージ由来の成果物を配る商品だけが packages を必要とする（技術同人誌の pdf 等は不要）
            if distribution & {"tgz", "unitypackage", "vpm-zip"} and not sale_unit.get("packages"):
                ctx.add("+", ERROR, "saleUnit.packages がありません（成果物集合を決定できません）")
            if sale_unit.get("versionPolicy") not in {"lockstep", "primary"}:
                ctx.add("+", ERROR, "saleUnit.versionPolicy は lockstep か primary である必要があります")
            for kind in sale_unit.get("distribution") or []:
                if kind not in ARTIFACT_KINDS:
                    ctx.add("+", ERROR, f"saleUnit.distribution に未知の種別があります: {kind}")


# ---------------------------------------------------------------------------
# 検査 1: CLAUDE.md と AGENTS.md がバイト同一
# ---------------------------------------------------------------------------


def check_01_guide_pair(ctx: RepoContext) -> None:
    claude = ctx.root / "CLAUDE.md"
    agents = ctx.root / "AGENTS.md"
    if not claude.is_file() and not agents.is_file():
        ctx.add("1", ERROR, "CLAUDE.md と AGENTS.md が両方とも存在しません（GOLD_STANDARD §2.2 必須）")
        return
    if not claude.is_file():
        ctx.add("1", ERROR, "CLAUDE.md がありません（AGENTS.md のみ存在）")
        return
    if not agents.is_file():
        ctx.add("1", ERROR, "AGENTS.md がありません（CLAUDE.md のみ存在）")
        return
    if claude.read_bytes() != agents.read_bytes():
        ctx.add("1", ERROR, "CLAUDE.md と AGENTS.md の内容が一致しません（常に同一内容を保つ規約）")


# ---------------------------------------------------------------------------
# 検査 2: 文書内の相対パス参照が実在し、git 追跡済みである
# ---------------------------------------------------------------------------

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def _skip_path_token(token: str) -> bool:
    """プレースホルダを含む「形の説明」は実在検査の対象にしない。"""
    return (
        not token
        or any(ch in token for ch in "{}<>|")
        or "..." in token
        or token.startswith("http")
        or token.startswith("#")
        or token.startswith("mailto:")
    )


def _resolve_inside_repo(ctx: RepoContext, base: Path, target: str) -> str | None:
    """base 基準で target を解決し、リポジトリ内の相対パスを返す（外なら None）。"""
    try:
        resolved = (base / target).resolve()
        return resolved.relative_to(ctx.root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _reference_ok(ctx: RepoContext, rel: str) -> bool:
    """リポジトリ内に実在するか。**大文字小文字まで一致**することを要求する。

    macOS のファイルシステムは既定で大文字小文字を区別しないため、`exists()` だけだと
    `docs/gold_standard.md` のような誤りが素通りして Linux CI や利用者側でだけ壊れる。
    実ディレクトリの一覧と照合して厳密に判定する。追跡外のパス（`Library/` 等の
    gitignore 対象）も正当な言及なので、git 追跡までは要求しない。
    """
    rel = rel.rstrip("/")
    if not rel:
        return True  # リポジトリルート自身
    if any(ch in rel for ch in "*?["):
        return bool(list(ctx.root.glob(rel)))
    current = ctx.root
    for segment in rel.split("/"):
        try:
            names = {entry.name for entry in current.iterdir()}
        except OSError:
            return False
        if segment not in names:
            return False
        current = current / segment
    return True


GUIDE_FILES = ("CLAUDE.md", "AGENTS.md", "README.md")
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")


def check_02_relative_paths(ctx: RepoContext) -> None:
    for doc in collect_doc_files(ctx):
        rel_doc = doc.relative_to(ctx.root).as_posix()
        text = doc.read_text(encoding="utf-8")
        is_guide = doc.name in GUIDE_FILES and doc.parent == ctx.root

        # (a) 相対 markdown リンク — 文書の所在基準で解決する
        #     コードスパン内の `[label](url)` のような書式説明は対象外にする
        for target in MD_LINK_RE.findall(CODE_SPAN_RE.sub("", text)):
            target = target.split(" ")[0].split("#")[0].strip()
            if _skip_path_token(target) or target.startswith("/"):
                continue
            if "/" not in target and "." not in target:
                continue  # `url` のようなプレースホルダ語はパスではない
            if ctx.is_waived("2", target):
                continue
            inside = _resolve_inside_repo(ctx, doc.parent, target)
            if inside is None:
                ctx.add("2", ERROR, f"リンク先がリポジトリの外を指しています（単独 clone で切れます）: {target}", rel_doc)
            elif not _reference_ok(ctx, inside):
                ctx.add("2", ERROR, f"リンク先が存在しません: {target}", rel_doc)

        # (b) リポジトリルート起点のバッククォート付きパス（ガイド 3 点のみ）
        #     他リポジトリに言及している行のパスはここからは検証できないので対象外にする
        if not is_guide:
            continue
        for line in text.splitlines():
            if any(marker in line for marker in FOREIGN_REPO_MARKERS):
                continue
            for token in BACKTICK_RE.findall(line):
                token = token.strip()
                if _skip_path_token(token) or not token.startswith(REPO_ROOT_PREFIXES):
                    continue
                if ctx.is_waived("2", token):
                    continue
                if not _reference_ok(ctx, token):
                    ctx.add("2", ERROR, f"参照先が存在しません: `{token}`", rel_doc)


# ---------------------------------------------------------------------------
# 検査 3: 配布された標準が正本と一致する
# ---------------------------------------------------------------------------


def _distributed_paths(ctx: RepoContext) -> list[Path]:
    paths = []
    for rel in DISTRIBUTED_FILES:
        path = ctx.root / rel
        if path.is_file():
            paths.append(path)
    for pattern in DISTRIBUTED_GLOBS:
        paths.extend(sorted(ctx.root.glob(pattern)))
    return paths


def check_03_distributed_standard(ctx: RepoContext) -> None:
    manifest = load_json(ctx.root / "pipeline" / "standard-manifest.json")
    seen: set[str] = set()

    for path in _distributed_paths(ctx):
        rel = path.relative_to(ctx.root).as_posix()
        seen.add(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            ctx.add("3", ERROR, "配布物を読み取れません", rel)
            continue

        is_canon_here = ctx.is_standard_source and (
            rel in CANONICAL_IN_TEMPLATE
            or any(fnmatch.fnmatch(rel, pattern) for pattern in CANONICAL_GLOBS_IN_TEMPLATE)
        )
        if is_canon_here:
            if GENERATED_MARKER in text[:1000]:
                ctx.add("3", ERROR, "標準の正本に生成物ヘッダが付いています（正本を配布物で上書きした疑い）", rel)
            continue

        declared, body = strip_generated_header(text)
        if declared is None:
            ctx.add("3", ERROR, "配布物ヘッダ（source-sha256）がありません。テンプレートから再配布してください", rel)
            continue
        actual = sha256_text(body)
        if actual != declared:
            ctx.add(
                "3",
                ERROR,
                f"配布物が改変されています（宣言 {declared[:12]}… / 実際 {actual[:12]}…）。編集はテンプレート側で行ってください",
                rel,
            )

    if manifest is None:
        ctx.add("3", ERROR, "pipeline/standard-manifest.json がありません（配布の鮮度と範囲を確認できません）")
        return

    source = manifest.get("source") or {}
    if manifest.get("$schemaVersion") != 1:
        ctx.add("3", ERROR, f"配布台帳の $schemaVersion が未対応です: {manifest.get('$schemaVersion')}")
    if source.get("dirty"):
        ctx.add("3", ERROR, "dirty な正本から配布されています（source commit が記録されていません）。再配布してください")
    elif not source.get("commit"):
        ctx.add("3", ERROR, "配布台帳に source commit がありません。再配布してください")

    listed = set((manifest.get("files") or {}).keys())
    for rel, expected in (manifest.get("files") or {}).items():
        path = ctx.root / rel
        if not path.is_file():
            ctx.add("3", ERROR, "配布台帳にあるファイルがありません。再配布してください", rel)
            continue
        if sha256_text(path.read_text(encoding="utf-8")) != expected:
            ctx.add("3", ERROR, "配布台帳の hash と一致しません。再配布してください", rel)
    for rel in sorted(seen - listed):
        if ctx.is_standard_source and (
            rel in CANONICAL_IN_TEMPLATE
            or any(fnmatch.fnmatch(rel, pattern) for pattern in CANONICAL_GLOBS_IN_TEMPLATE)
        ):
            continue  # テンプレートの正本そのものは配布物ではない
        # プロファイルを縮小したときの取り残し。台帳が配布物の所有権を持つ
        ctx.add("3", ERROR, "配布台帳に無い配布物が残っています（プロファイル変更時の取り残し。削除してください）", rel)


# ---------------------------------------------------------------------------
# 検査 4: テスト整備状況の宣言と実態の一致
# ---------------------------------------------------------------------------


def _package_has_tests(package_dir: Path) -> bool:
    """Tests asmdef と、その配下の実テストソースが両方あることを「整備済み」とする。

    空の asmdef だけを置いて検査を通す抜け道を塞ぐ。
    """
    tests = package_dir / "Tests"
    if not list(tests.rglob("*.asmdef")):
        return False
    return bool([path for path in tests.rglob("*.cs") if path.is_file()])


def check_04_test_policy(ctx: RepoContext) -> None:
    policies = ctx.config.get("packagePolicies") or {}

    # (a) 構造化された宣言と実態の照合（判定の主役）
    for name, package_dir, _ in ctx.packages:
        declared = str((policies.get(name) or {}).get("tests", "required"))
        has_tests = _package_has_tests(package_dir)
        if declared == "required" and not has_tests:
            ctx.add(
                "4",
                ERROR,
                f"{name}: EditMode テストが必須と宣言されていますが Tests/**/*.asmdef がありません"
                "（整備するか packagePolicies で waived を理由付きで宣言する）",
            )
        elif declared == "waived" and has_tests:
            ctx.add("4", WARN, f"{name}: packagePolicies で tests=waived ですが、実際には Tests asmdef があります（宣言を更新してください）")
        elif declared not in {"required", "waived"}:
            ctx.add("4", ERROR, f"{name}: packagePolicies.tests が不正です: {declared}")
        if declared == "waived" and not str((policies.get(name) or {}).get("reason", "")).strip():
            ctx.add("4", ERROR, f"{name}: tests=waived には reason が必要です")

    # (b) 自然言語の否定的な主張（補助。誤検出しうるので warn）
    if any(_package_has_tests(package_dir) for _, package_dir, _ in ctx.packages):
        for doc in collect_doc_files(ctx):
            rel_doc = doc.relative_to(ctx.root).as_posix()
            for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
                if not TEST_SUBJECT_RE.search(line) or not NEGATIVE_CLAIM_RE.search(line):
                    continue
                if ctx.is_waived("4", line.strip()):
                    continue
                excerpt = line.strip()
                if len(excerpt) > 80:
                    excerpt = excerpt[:80] + "…"
                ctx.add("4", WARN, f"テスト整備を否定する記述です。実態と合っているか確認してください: {excerpt}", f"{rel_doc}:{number}")


# ---------------------------------------------------------------------------
# 検査 5: Tests asmdef と testables の整合
# ---------------------------------------------------------------------------


def check_05_testables(ctx: RepoContext) -> None:
    if not ctx.packages:
        return
    manifest_path = ctx.root / "Packages" / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest is None:
        ctx.add("5", ERROR, "Packages/manifest.json を読み取れません")
        return
    testables = manifest.get("testables") or []
    package_names = {name for name, _, _ in ctx.packages}

    for name in testables:
        if name not in package_names:
            ctx.add("5", ERROR, f"testables に登録された {name} が Packages/ に存在しません", "Packages/manifest.json")
    for name, _, _ in ctx.packages:
        if name not in testables:
            ctx.add("5", ERROR, f"{name} が testables に登録されていません（GOLD_STANDARD §2.3）", "Packages/manifest.json")


# ---------------------------------------------------------------------------
# 検査 6: .meta 完全性と git 追跡
# ---------------------------------------------------------------------------


def check_06_meta_completeness(ctx: RepoContext) -> None:
    for _, package_dir, _ in ctx.packages:
        root_meta = package_dir.parent / f"{package_dir.name}.meta"
        if root_meta.exists():
            ctx.add(
                "6",
                ERROR,
                "パッケージルートに .meta があります（UPM 慣例に反する。GOLD_STANDARD §2.1）",
                root_meta.relative_to(ctx.root).as_posix(),
            )

        for current, dirnames, filenames in walk_package(package_dir):
            entries = [(current / name, True) for name in dirnames]
            entries += [(current / name, False) for name in filenames if not name.endswith(".meta")]
            for entry, is_dir in entries:
                rel = entry.relative_to(ctx.root).as_posix()
                meta_rel = f"{rel}.meta"
                if not (ctx.root / meta_rel).exists():
                    ctx.add("6", ERROR, "ディスク上に .meta がありません", rel)
                elif meta_rel not in ctx.tracked:
                    ctx.add("6", ERROR, ".meta が git 追跡されていません（利用者側でだけ壊れる）", meta_rel)
                if not is_dir and rel not in ctx.tracked:
                    ctx.add("6", ERROR, "アセット本体が git 追跡されていません", rel)


# ---------------------------------------------------------------------------
# 検査 7: パス長 150 字未満（git 追跡パス基準）
# ---------------------------------------------------------------------------


def check_07_path_length(ctx: RepoContext) -> None:
    sale_unit = ctx.config.get("saleUnit") or {}
    distribution = set(sale_unit.get("distribution") or [])
    display_name = sale_unit.get("displayName")

    for name, package_dir, _ in ctx.packages:
        prefix = package_dir.relative_to(ctx.root).as_posix() + "/"
        for tracked in sorted(ctx.tracked):
            if not tracked.startswith(prefix):
                continue
            tail = tracked[len(prefix) :]
            # UPM 出品レイアウト（<package-name>/ 起算）
            roots = [(f"{name}/{tail}", "UPM")]
            # .unitypackage 出品レイアウト（Assets/<DisplayName>/ 起算）は起点が長くなる
            if "unitypackage" in distribution and display_name:
                roots.append((f"Assets/{display_name}/{tail}", ".unitypackage"))
            for logical, layout in roots:
                if len(logical) >= MAX_PATH_LENGTH:
                    ctx.add("7", ERROR, f"パス長 {len(logical)} 字（{layout} 基準・UAS 2.1.e は 150 字未満）", logical)


# ---------------------------------------------------------------------------
# 検査 8: package.json の URL 3 種が /products/{slug}/ 規約に合う
# ---------------------------------------------------------------------------

URL_SUFFIXES = {"documentationUrl": "", "changelogUrl": "changelog/", "licensesUrl": "licenses/"}
SITE_BASE = "https://kajitaharuka.com/products/"


def check_08_package_urls(ctx: RepoContext) -> None:
    declared_slug = ctx.config.get("productSlug")
    for _, package_dir, meta in ctx.packages:
        rel = (package_dir / "package.json").relative_to(ctx.root).as_posix()
        slugs = set()
        for key, suffix in URL_SUFFIXES.items():
            url = meta.get(key)
            if not url:
                ctx.add("8", ERROR, f"{key} がありません", rel)
                continue
            if "{{" in url:
                continue  # テンプレートの雛形値
            if not url.startswith(SITE_BASE) or not url.endswith("/"):
                ctx.add("8", ERROR, f"{key} が URL 規約（{SITE_BASE}{{slug}}/…）に合いません: {url}", rel)
                continue
            parts = [part for part in url[len(SITE_BASE) :].split("/") if part]
            if suffix:
                expected_tail = suffix.rstrip("/")
                if len(parts) != 2 or parts[1] != expected_tail:
                    ctx.add("8", ERROR, f"{key} の末尾が /{expected_tail}/ ではありません: {url}", rel)
                    continue
            elif len(parts) != 1:
                ctx.add("8", ERROR, f"documentationUrl は商品ページ直下を指す必要があります: {url}", rel)
                continue
            slugs.add(parts[0])

        if len(slugs) > 1:
            ctx.add("8", ERROR, f"URL 3 種の slug が一致しません: {sorted(slugs)}", rel)
        elif slugs and declared_slug and next(iter(slugs)) != declared_slug:
            ctx.add(
                "8",
                ERROR,
                f"package.json の slug `{next(iter(slugs))}` が pipeline/repo.json の productSlug `{declared_slug}` と異なります",
                rel,
            )


# ---------------------------------------------------------------------------
# 検査 9: 販売単位の Exporter 設定アセットと Publish/ 命名
# ---------------------------------------------------------------------------

PUBLISH_EXTRA_ALLOWED = ("README.md", ".gitkeep")
# type: 2 = このプロジェクトの Assets 配下アセットへの参照。type: 3（スクリプト・パッケージ由来）は
# 依存パッケージ側に実体があり、このリポジトリの .meta には現れないので対象にしない。
LOCAL_ASSET_GUID_RE = re.compile(r"guid:\s*([0-9a-f]{32}),\s*type:\s*2\b")
EXPORT_EXPRESSION_RE = re.compile(r"^\s*(exportFileNameExpression|exportFilePathExpression|exportFolderPathExpression):\s*(\S.*)$", re.MULTILINE)
ENTRY_RE = re.compile(r"^\s*-\s*exporter:\s*\{fileID:", re.MULTILINE)


def _repo_guids(ctx: RepoContext) -> set[str]:
    """リポジトリ内の .meta が持つ GUID の集合（参照切れ検出用）。"""
    guids: set[str] = set()
    for tracked in ctx.tracked:
        if not tracked.endswith(".meta"):
            continue
        try:
            text = (ctx.root / tracked).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(r"^guid:\s*([0-9a-f]{32})", text, re.MULTILINE)
        if match:
            guids.add(match.group(1))
    return guids


def check_09_sale_unit(ctx: RepoContext) -> None:
    sale_unit = ctx.config.get("saleUnit")
    if ctx.is_product and sale_unit:
        assets = sale_unit.get("exporterAssets") or []
        if not assets and ctx.packages:
            ctx.add("9", ERROR, "saleUnit.exporterAssets が空です（販売成果物を再現できません）")
        guids = _repo_guids(ctx) if assets else set()
        for rel in assets:
            path = ctx.root / rel
            if not path.is_file():
                ctx.add("9", ERROR, "Exporter 設定アセットが存在しません", rel)
                continue
            if rel not in ctx.tracked:
                ctx.add("9", ERROR, "Exporter 設定アセットが git 追跡されていません（再現不能）", rel)
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not EXPORT_EXPRESSION_RE.search(text):
                ctx.add("9", ERROR, "Exporter 設定アセットに出力先の式がありません（Exporter ではない疑い）", rel)
            if "entries:" in text and not ENTRY_RE.search(text):
                ctx.add("9", ERROR, "ZipPacker の entries が空です（成果物を集約できません）", rel)
            missing = set(LOCAL_ASSET_GUID_RE.findall(text)) - guids
            if missing:
                ctx.add("9", ERROR, f"参照先を解決できない GUID があります（参照切れ）: {', '.join(sorted(missing))}", rel)

    publish_dir = ctx.root / "Publish"
    if not publish_dir.is_dir():
        return
    display_names = {meta.get("displayName") for _, _, meta in ctx.packages if meta.get("displayName")}
    if sale_unit and sale_unit.get("displayName"):
        display_names.add(sale_unit["displayName"])
    package_names = {name for name, _, _ in ctx.packages}

    patterns = [re.compile(rf"^{re.escape(d)}-\d+\.\d+\.\d+.*\.zip$") for d in display_names if d]
    patterns += [re.compile(rf"^{re.escape(n)}-\d+\.\d+\.\d+.*\.(tgz|unitypackage|zip)$") for n in package_names]
    patterns.append(re.compile(r"^release-\d+\.\d+\.\d+.*\.json$"))

    for path in sorted(publish_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(ctx.root).as_posix()
        if path.name in PUBLISH_EXTRA_ALLOWED or path.name.startswith("."):
            continue
        if ctx.is_waived("9", rel):
            continue
        if not any(pattern.match(path.name) for pattern in patterns):
            ctx.add("9", WARN, "Publish/ の命名規約（§2.7）に合いません", rel)


# ---------------------------------------------------------------------------
# 検査 10: 参照スキルの実在と、提供パッケージの依存宣言
# ---------------------------------------------------------------------------


def _index_skills(directory: Path, provider: str | None) -> dict[str, str | None]:
    """スキルディレクトリを走査し、{呼出名: 提供パッケージ名 or None} を返す。

    フォルダ名と SKILL.md の frontmatter `name` の**両方**を呼出名として索引する
    （両者が食い違うケースが実在するため。例: フォルダ unity-mcp-skill / name unity-mcp-orchestrator）。
    """
    found: dict[str, str | None] = {}
    if not directory.is_dir():
        return found
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        found[child.name] = provider
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            try:
                head = skill_md.read_text(encoding="utf-8")[:600]
            except OSError:
                continue
            match = FRONTMATTER_NAME_RE.search(head)
            if match:
                found[match.group(1).strip().strip('"\'')] = provider
    return found


def _skill_index(ctx: RepoContext) -> tuple[dict[str, str | None], bool]:
    index: dict[str, str | None] = {}
    home = Path.home()
    for directory in (home / ".claude" / "skills", home / ".agents" / "skills"):
        index.update(_index_skills(directory, None))
    for directory in (ctx.root / ".claude" / "skills", ctx.root / ".agents" / "skills"):
        index.update(_index_skills(directory, None))
    own_packages = {name for name, _, _ in ctx.packages}
    for skills_dir in sorted((ctx.root / "Packages").glob("*/skills")) if (ctx.root / "Packages").is_dir() else []:
        index.update(_index_skills(skills_dir, skills_dir.parent.name))

    site_root = resolve_site_repo(ctx)
    if site_root is None:
        return index, False
    index.update(_index_skills(site_root / "skills", None))
    for repo_path in resolve_registry_repos(site_root):
        for skills_dir in sorted(repo_path.glob("Packages/*/skills")):
            provider = skills_dir.parent.name
            index.update(_index_skills(skills_dir, None if provider in own_packages else provider))
    return index, True


def check_10_skill_references(ctx: RepoContext) -> None:
    index, registry_resolved = _skill_index(ctx)
    declared = [str(item) for item in ctx.config.get("skillRefs") or []]
    manifest = load_json(ctx.root / "Packages" / "manifest.json") or {}
    dependencies = set((manifest.get("dependencies") or {}).keys())
    own_packages = {name for name, _, _ in ctx.packages}

    # (a) 宣言されたスキルの実在と、提供パッケージの依存宣言（判定の主役）
    for skill in declared:
        if ctx.is_waived("10", skill):
            continue
        if skill not in index:
            severity = ERROR if registry_resolved else WARN
            note = "" if registry_resolved else "（MySite を解決できないため未確認）"
            ctx.add("10", severity, f"宣言されたスキル `{skill}` がどのスコープにも存在しません{note}")
            continue
        provider = index[skill]
        # 依存宣言の検査は Unity プロジェクトのリポジトリでのみ意味を持つ
        if provider and ctx.packages and provider not in dependencies and provider not in own_packages:
            ctx.add(
                "10",
                ERROR,
                f"スキル `{skill}` はパッケージ {provider} が提供しますが、Packages/manifest.json に依存宣言がありません",
                "Packages/manifest.json",
            )

    # (b) 文書に出てくるスキル名が宣言に載っているか（宣言の陳腐化を防ぐ補助検査）
    declared_set = set(declared)
    for doc in collect_doc_files(ctx):
        if not (doc.name in GUIDE_FILES and doc.parent == ctx.root):
            continue
        rel_doc = doc.relative_to(ctx.root).as_posix()
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            for token in BACKTICK_RE.findall(line):
                token = token.strip()
                if not SKILL_TOKEN_RE.match(token) or token in SKILL_TOKEN_DENYLIST:
                    continue
                if token in declared_set or ctx.is_waived("10", token):
                    continue
                if token not in index:
                    continue  # スキル名の形をしているだけの語（パス断片・用語）は無視する
                ctx.add("10", WARN, f"文書が参照するスキル `{token}` が skillRefs に宣言されていません", f"{rel_doc}:{number}")


# ---------------------------------------------------------------------------
# 追加検査（枠外）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 検査 11: Samples~ の .mat が壊れていないこと
#
# Samples~ は Unity がインポートしない隠しフォルダなので、開発リポジトリを
# 開いているだけでは破損に気づけない。実際に UMPD の同梱サンプルで、script 参照を
# 失った sub-asset が 3 つ紛れたまま 3 バージョン出荷された（1.0.2 で除去）。
# ---------------------------------------------------------------------------

MAT_DOC_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)", re.M)
MAT_NAME_RE = re.compile(r"^  m_Name: (.*)$", re.M)
MAT_NULL_SCRIPT_RE = re.compile(r"^  m_Script: \{fileID: 0\}$", re.M)
# guid を伴わない非ゼロの fileID 参照は、同じファイル内のドキュメントを指す
MAT_LOCAL_REF_RE = re.compile(r"fileID: (-?\d+)\}")
# Texture2D のピクセル本体。埋め込みテクスチャはここに 16 進で直列化される
TEX_DATA_RE = re.compile(r"^  _typelessdata: ([0-9a-fA-F]+)$", re.M)
# 未初期化メモリを表す既知の充填バイト。実データがこの 1 種類だけで埋まることはまず無い
DEBUG_FILL_BYTES = {0xCD, 0xCC, 0xDD, 0xFD, 0xAB, 0xBA, 0xFE}


def find_uninitialized_texture_data(text: str) -> list[str]:
    """埋め込み Texture2D の中身が未初期化のままでないかを見る。

    サブアセットのテクスチャは「入れ物を作る処理」と「焼く処理」が別なので、
    後者を呼び忘れると**サイズも形式も正しいのに中身だけ未初期化**という
    見た目では気づけない状態で出荷される（TAE 1.2.0 の同梱サンプル 7 点が実際にそうなった。
    全バイトが 0xCD ＝ Half として読むと -23.2 という無意味な値）。

    実データが単一のデバッグ充填バイトだけで構成されることは実質ありえないので、
    その形だけを拾う。真っ黒（全 0x00）や単色は正当なので対象にしない。
    """
    problems: list[str] = []
    for index, payload in enumerate(TEX_DATA_RE.findall(text)):
        try:
            data = bytes.fromhex(payload)
        except ValueError:
            continue
        if len(data) < 16:
            continue
        values = set(data)
        if len(values) == 1 and next(iter(values)) in DEBUG_FILL_BYTES:
            problems.append(
                f"埋め込みテクスチャの中身が未初期化のままです"
                f"（{index + 1} 個目・{len(data)} バイトすべて 0x{next(iter(values)):02X}）"
            )
    return problems


def scan_unity_yaml_asset(text: str) -> list[str]:
    """Unity の YAML アセットに壊れたドキュメントが無いかを見る。問題文の一覧を返す。"""
    problems: list[str] = []
    parts = re.split(r"^(--- !u!\d+ &-?\d+.*)$", text, flags=re.M)
    if len(parts) < 3:
        return problems

    docs = [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]
    file_ids: list[str] = []
    names: list[str] = []
    for marker, body in docs:
        match = MAT_DOC_RE.match(marker)
        if not match:
            continue
        class_id, file_id = match.group(1), match.group(2)
        file_ids.append(file_id)
        name_match = MAT_NAME_RE.search(body)
        name = name_match.group(1).strip() if name_match else ""
        if name:
            names.append(name)
        # MonoBehaviour の script 参照が null = 型を解決できない残留物
        if class_id == "114" and MAT_NULL_SCRIPT_RE.search(body):
            problems.append(f"script 参照を失ったサブアセットがあります（&{file_id} {name}）")

    for label, values in (("fileID", file_ids), ("m_Name", names)):
        duplicates = sorted({v for v in values if values.count(v) > 1})
        for value in duplicates:
            problems.append(f"{label} が重複しています: {value}")

    # ファイル内参照の解決（guid 付き＝外部参照は対象外）
    defined = set(file_ids)
    for line in text.splitlines():
        if "guid:" in line:
            continue
        for file_id in MAT_LOCAL_REF_RE.findall(line):
            if file_id != "0" and file_id not in defined:
                problems.append(f"同一ファイル内に解決できない参照があります: fileID {file_id}")

    problems.extend(find_uninitialized_texture_data(text))
    return problems


def check_11_sample_assets(ctx: RepoContext) -> None:
    for _, package_dir, _ in ctx.packages:
        samples = package_dir / "Samples~"
        if not samples.is_dir():
            continue
        for asset in sorted(samples.rglob("*")):
            if asset.suffix not in (".mat", ".asset") or not asset.is_file():
                continue
            try:
                text = asset.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = asset.relative_to(ctx.root).as_posix()
            for problem in scan_unity_yaml_asset(text):
                ctx.add("11", ERROR, problem, rel)


# ---------------------------------------------------------------------------
# 検査 12: 同梱スキルのミラーが正本と一致していること
#
# パッケージ同梱スキル（Packages/*/skills/）は、開発リポジトリで実際にエージェントが
# 読む位置（.claude/skills/ と .agents/skills/）へ複製して git 追跡している。
# 複製なので、正本だけを直すと黙って古いまま残る。実際に UEWCE で、商品ページと
# パッケージ README の訂正が正本のスキルには入ったのに、この 2 つの複製には
# 届かないまま「画面ロック中でも撮影できる」という裏付けの無い断定が残っていた。
#
# 正本に対応するミラーが存在するときだけ比較する（他リポジトリ由来のスキルは対象外）。
# ---------------------------------------------------------------------------

MIRROR_SKILL_DIRS = (".claude/skills", ".agents/skills")


def _skill_tree(directory: Path) -> dict[str, bytes] | None:
    """スキル 1 件分のファイル名→内容。読めないファイルがあれば None。

    `.meta` は比較しない。正本は `Packages/` 配下なので Unity が生成するが、
    ミラーはエージェントが読むだけで Unity にインポートされないため、
    同期スクリプトが意図的に複製していない（両者の正しい差）。
    """
    tree: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name.endswith(".meta"):
            continue
        try:
            tree[path.relative_to(directory).as_posix()] = path.read_bytes()
        except OSError:
            return None
    return tree


def check_12_skill_mirrors(ctx: RepoContext) -> None:
    packages_root = ctx.root / "Packages"
    if not packages_root.is_dir():
        return
    for skills_dir in sorted(packages_root.glob("*/skills")):
        for source in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            expected = _skill_tree(source)
            if expected is None:
                continue
            for mirror_root in MIRROR_SKILL_DIRS:
                mirror = ctx.root / mirror_root / source.name
                if not mirror.is_dir():
                    continue
                actual = _skill_tree(mirror)
                rel = f"{mirror_root}/{source.name}"
                if actual is None:
                    ctx.add("12", ERROR, "ミラーのファイルを読めません。", rel)
                    continue
                missing = sorted(set(expected) - set(actual))
                extra = sorted(set(actual) - set(expected))
                changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
                if not (missing or extra or changed):
                    continue
                detail = []
                if changed:
                    detail.append("内容が違う: " + ", ".join(changed))
                if missing:
                    detail.append("ミラーに無い: " + ", ".join(missing))
                if extra:
                    detail.append("正本に無い: " + ", ".join(extra))
                ctx.add(
                    "12",
                    ERROR,
                    f"同梱スキルのミラーが正本（{source.relative_to(ctx.root).as_posix()}）と一致しません。"
                    f"同期スクリプト（scripts/sync-agent-skills.mjs 等）を実行してください。"
                    f" {' / '.join(detail)}",
                    rel,
                )


def check_extra(ctx: RepoContext) -> None:
    for name, package_dir, meta in ctx.packages:
        rel = (package_dir / "package.json").relative_to(ctx.root).as_posix()
        if not meta.get("unityRelease"):
            ctx.add("+", WARN, f"{name}: package.json に unityRelease がありません（GOLD_STANDARD §2.5）", rel)
        if not (package_dir / "Third Party Notices.md").is_file():
            ctx.add("+", WARN, f"{name}: Third Party Notices.md がありません（UAS 1.2.a）")
        for required in ("README.md", "CHANGELOG.md", "LICENSE.md"):
            if not (package_dir / required).is_file():
                ctx.add("+", WARN, f"{name}: {required} がありません（GOLD_STANDARD §2.5）")


# ---------------------------------------------------------------------------
# 個人設定・サイトリポジトリの解決（設計 §5 の解決手順と同じ順序）
# ---------------------------------------------------------------------------

PERSONAL_CONFIG = Path.home() / ".kajitaharuka-pipeline.json"


def load_personal_config() -> dict:
    return load_json(PERSONAL_CONFIG) or {}


def resolve_site_repo(ctx: RepoContext) -> Path | None:
    personal = load_personal_config()
    candidates: list[str] = []
    override = (personal.get("overrides") or {}).get("mysite")
    if override:
        candidates.append(override)
    if personal.get("registryPath"):
        candidates.append(str(Path(personal["registryPath"]).expanduser().parent.parent))
    candidates += ["~/dev/MySite", str(ctx.root.parent.parent / "MySite")]

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if (path / "pipeline" / "repositories.json").is_file():
            return path
    return None


def resolve_registry_repos(site_root: Path) -> list[Path]:
    """レジストリに載っているリポジトリのうち、ローカルで解決できたものを返す。

    解決順は設計 §5 と同じ: 個人設定の overrides → localPathCandidates →
    `git remote get-url origin` による同一性検証。検証に通らない候補は採用しない。
    """
    registry = load_json(site_root / "pipeline" / "repositories.json")
    if not registry:
        return []
    overrides = load_personal_config().get("overrides") or {}
    resolved: list[Path] = []
    for entry in registry.get("repositories", []):
        remote = entry.get("remote") or {}
        expected = {remote.get("https"), remote.get("ssh")} - {None}
        candidates = []
        repo_id = entry.get("id")
        if repo_id and repo_id in overrides:
            candidates.append(overrides[repo_id])
        candidates += entry.get("localPathCandidates") or []
        for candidate in candidates:
            path = Path(str(candidate)).expanduser()
            if not (path / ".git").exists():
                continue
            actual = run_git(path, "remote", "get-url", "origin").strip()
            if expected and actual and actual not in expected:
                continue  # 同じパスにある別リポジトリを掴まない
            resolved.append(path)
            break
    return resolved


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def build_context(root: Path) -> RepoContext:
    config = load_json(root / "pipeline" / "repo.json") or {}
    tracked = {line for line in run_git(root, "ls-files").splitlines() if line}
    tracked_dirs: set[str] = set()
    for item in tracked:
        parts = item.split("/")
        for index in range(1, len(parts)):
            tracked_dirs.add("/".join(parts[:index]))
    ctx = RepoContext(root=root, config=config, tracked=tracked, tracked_dirs=tracked_dirs)

    packages_dir = root / "Packages"
    if packages_dir.is_dir():
        for entry in sorted(packages_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta = load_json(entry / "package.json")
            if meta and meta.get("name"):
                ctx.packages.append((meta["name"], entry, meta))
    return ctx


CHECKS = (
    check_00_config,
    check_01_guide_pair,
    check_02_relative_paths,
    check_03_distributed_standard,
    check_04_test_policy,
    check_05_testables,
    check_06_meta_completeness,
    check_07_path_length,
    check_08_package_urls,
    check_09_sale_unit,
    check_10_skill_references,
    check_11_sample_assets,
    check_12_skill_mirrors,
    check_extra,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="リポジトリガイドと実装の整合を検査する")
    parser.add_argument("--root", default=".", help="対象リポジトリのルート（既定: カレント）")
    parser.add_argument("--strict", action="store_true", help="warn も失敗として扱う")
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: 対象ディレクトリがありません: {root}", file=sys.stderr)
        return 2

    ctx = build_context(root)
    for check in CHECKS:
        check(ctx)

    errors = [f for f in ctx.findings if f.severity == ERROR]
    warnings = [f for f in ctx.findings if f.severity == WARN]

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "role": ctx.role,
                    "packages": [name for name, _, _ in ctx.packages],
                    "errors": [f.__dict__ for f in errors],
                    "warnings": [f.__dict__ for f in warnings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        label = ctx.config.get("repository") or root.name
        print(f"== 標準準拠検査: {label}（role={ctx.role}, packages={len(ctx.packages)}）")
        for line in collapse_findings(ctx.findings):
            print(line)
        if not ctx.findings:
            print("問題は見つかりませんでした。")
        print(f"-- error {len(errors)} 件 / warn {len(warnings)} 件")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
