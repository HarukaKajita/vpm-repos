#!/usr/bin/env python3
# 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
# 編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
# source: UnityTemplate_2022_3_22f1/scripts/pipeline/verify_repo_guide.py
# source-sha256: c6cc3bc133516e9da3c3ef5547d0e3a65d36a7b91b104445ff57b4398ac22c34
"""リポジトリガイドと実装の整合を機械検証する（ゴールド標準 §2.10 第2層）。

原則: **文書がリポジトリ自身の状態について主張することは、すべて機械で確かめられる。**
数や状態を手書きするなら、同じ事実を検査するアサーションを必ず対にする。

判定の主役は `pipeline/repo.json` の**構造化された宣言**で、自然言語のヒューリスティックは
補助（warn）に留める。誤検出は検査を消すのではなく、理由付きの waiver で個別に逃がす。

使い方（対象リポジトリのルートで実行）:
    python3 scripts/pipeline/verify_repo_guide.py            # 検査（error があれば非ゼロ終了）
    python3 scripts/pipeline/verify_repo_guide.py --strict   # warn も失敗として扱う
    python3 scripts/pipeline/verify_repo_guide.py --json     # 機械可読の結果を出力
    python3 scripts/pipeline/verify_repo_guide.py --separate-untracked
                                                             # 未追跡ファイル起因の指摘を別枠へ分ける

検査対象の細則:
- `.meta` 検査はパッケージルート自身を対象にしない（UPM 慣例で root は `.meta` を持たない。
  root の `.meta` が**ある**ことは逆に error）。Unity が無視する名前のフォルダは subtree ごと
  除外し、symlink はファイル・フォルダとも追跡しない。
- パス長は **git 追跡パスの `/` 区切り論理文字列**を基準に、`.meta` を含めて計測する
  （OS のファイルシステム表現に依存させない）。
- 「同梱物（`.tgz` に入るパス）」は `Packages/<name>/` 配下の git 追跡ファイル全部で、`.meta` も
  `Tests/` も `Samples~` / `Documentation~` も含む（実物の tgz と `git ls-tree` の突き合わせで確認）。
  除くのは npm / `Client.Pack` が既定で落とす OS・VCS 由来の名前だけ。

「自分の変更だけを見る」ための分離（`--separate-untracked`・2026-08-14 追加）:
- 同じリポジトリで複数のエージェントが並行作業すると、**他の作業が置いた未追跡ファイルで検査 6 が
  構造的に赤くなる**（実測 2026-08-14: TAE で error 14 件・UMPD で error 26 件が出たが、全件が
  並行作業の未追跡ファイルで、当人の変更由来は 0 件だった）。赤が常態になると本物の error を見落とす。
- **絞り込み（`--paths <glob>` 方式）は採らない。** 対象を狭める手段は「見なかったことにする」に
  転びやすく、しかも狭めた範囲の外に本物の error があっても出力から消えてしまう。代わりに
  **全件を検査したまま、未追跡ファイル起因の指摘だけを別枠へ寄せて両方見せる**（消さない・隠さない）。
- 「自分の変更」と「他人の置き土産」を分ける境界は **git の index** に置く。`git add -N`（intent-to-add）
  したものは追跡済みとして扱われ、通常の集計に戻る。つまり **コミット前に自分の変更を検査したいなら
  intent-to-add してから走らせる**のが正しい手順で、これは検査 6 に限らず新規ファイルを置く作業全般の
  前提になる（GOLD_STANDARD §2.10 の検査 6 の項）。
- 既定（フラグ無し）の挙動は一切変えない。pre-push hook はフラグ無しで呼ぶため、関門は緩まない。

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
# pipeline/repo.json のトップレベルに書いてよいキー。**閉じた集合**として扱い、これ以外は
# 打ち間違いとみなして error にする（宣言が黙って無視されるのを防ぐ）。
KNOWN_CONFIG_KEYS = frozenset({
    "$schemaVersion", "repository", "role", "productSlug", "tagPolicy",
    "saleUnit", "packagePolicies", "skillRefs", "skillSync", "waivers",
    "licensePageLanguages", "prePush", "normativeDocs", "externalSkills", "notes",
})

VALID_CHECK_IDS = {
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "+",
}
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
    # git がまだ知らない（未追跡の）ファイルに起因する指摘か。並行作業の置き土産と
    # 自分の変更を切り分けるための属性で、`--separate-untracked` のときだけ出力を分ける。
    # **判定の強さ（error / warn）は変わらない**。付けるのは指摘を出す側で、
    # 「その指摘の主語であるアセット自体を git が知らない」ときに限る。
    untracked: bool = False


def finding_payload(finding: Finding) -> dict:
    """`--json` 出力用の辞書。

    `finding.__dict__` をそのまま出さないのは、**内部の属性を 1 つ足しただけで JSON の
    キー集合が変わり、読み手の互換が黙って壊れる**ため（`untracked` を足したときに実際に
    起きかけた）。出力の形はここで明示的に固定する。
    """
    return {
        "check": finding.check,
        "severity": finding.severity,
        "message": finding.message,
        "path": finding.path,
    }


def split_untracked(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """(未追跡起因でない指摘, 未追跡起因の指摘) に分ける。順序は元のまま保つ。"""
    return (
        [finding for finding in findings if not finding.untracked],
        [finding for finding in findings if finding.untracked],
    )


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

    def add(
        self,
        check: str,
        severity: str,
        message: str,
        path: str | None = None,
        untracked: bool = False,
    ) -> None:
        self.findings.append(Finding(check, severity, message, path, untracked))

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

    # **未知のトップレベルキーは error。** repo.json は自前の閉じたスキーマなので、知らないキーは
    # 常に打ち間違いである。ここを見ないと `prePush` を `prePish` と書いた瞬間に
    # **宣言した pre-push の検査が 1 つも走らないまま push が通る**（hook は知らないキーを読まない）。
    # 「宣言が黙って無効になる」形はこの検査体系でいちばん危ない失敗なので、器の名前から潰す。
    unknown_keys = sorted(set(ctx.config) - KNOWN_CONFIG_KEYS)
    if unknown_keys:
        ctx.add(
            "+",
            ERROR,
            f"pipeline/repo.json に未知のキーがあります: {', '.join(unknown_keys)}"
            f"（打ち間違いなら直してください。宣言が黙って無視され、宣言したはずの検査が"
            f"走らないまま通ってしまいます。キーを増やすときは verify_repo_guide.py の "
            f"KNOWN_CONFIG_KEYS へも足すこと）",
        )

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

    # 法文だけ言語を絞る方針を採るときの宣言。宣言があれば検査 17 が error で厳密に照合し、
    # 無ければ warn に留める。空配列を許すと「全部消す」宣言と「書き忘れ」が区別できない
    license_langs = ctx.config.get("licensePageLanguages")
    if license_langs is not None and (
        not isinstance(license_langs, list)
        or not license_langs
        or not all(isinstance(item, str) and item.strip() for item in license_langs)
    ):
        ctx.add(
            "+",
            ERROR,
            "pipeline/repo.json の licensePageLanguages は空でない文字列の配列である必要があります"
            "（例: [\"ja\", \"en\"]）",
        )

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


def _has_negative_test_claim(line: str) -> bool:
    """テストの整備状況を否定している文が 1 つでもあるか。

    話題語（テスト・EditMode 等）と否定語が**同じ文の中に**現れたときだけ真とする。
    行全体で見ると、無関係な 2 つの文に散った語を拾って誤検出する。
    文の切れ目は句点・改行相当の記号で見る（Markdown の 1 行に複数文が入るため）。
    """
    for sentence in re.split(r"[。！？]", line):
        if TEST_SUBJECT_RE.search(sentence) and NEGATIVE_CLAIM_RE.search(sentence):
            return True
    return False


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
                # 話題語と否定語を「行」でなく「文」の単位で見る。行で見ると、別々の文に
                # 散っている 2 語を拾ってしまう。実測（2026-08-14 / UMPD CLAUDE.md:156）:
                # 「…テストの期待値も実行 OS で割れなくなる。`Environment.NewLine` を使ってよいのは…
                # OS ごとに中身が変わる理由が無い。」が、「テスト」と「が無い」の同居として鳴った。
                # どちらの文もテストの整備状況を否定していない。
                if not _has_negative_test_claim(line):
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
    """パッケージ配下の全ファイル・全サブフォルダに `.meta` があり、git 追跡されていることを見る。

    指摘には「未追跡起因かどうか」の印を付ける（`--separate-untracked` で別枠へ寄せるため）。
    印を付ける条件は**その指摘の主語であるアセット自体を git が知らないこと**で、
    「追跡済みのアセットなのに `.meta` が欠けている／追跡されていない」は印を付けない。
    後者は自分がコミットした内容そのものの不備であり、他人の作業では説明できないため。
    """
    for _, package_dir, _ in ctx.packages:
        root_meta = package_dir.parent / f"{package_dir.name}.meta"
        if root_meta.exists():
            # ルートの `.meta` は追跡の有無に関わらず「あること自体」が UPM 慣例違反なので分離しない
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
                # git はフォルダを追跡しないので、フォルダは「追跡ファイルを含むか」で見る。
                # `git add -N`（intent-to-add）したものは index に載るため追跡済みとして扱われ、
                # 自分の作業中の変更はここで未追跡起因から外れる（これが分離の境界）。
                asset_untracked = rel not in (ctx.tracked_dirs if is_dir else ctx.tracked)
                if not (ctx.root / meta_rel).exists():
                    ctx.add("6", ERROR, "ディスク上に .meta がありません", rel, untracked=asset_untracked)
                elif meta_rel not in ctx.tracked:
                    ctx.add(
                        "6",
                        ERROR,
                        ".meta が git 追跡されていません（利用者側でだけ壊れる）",
                        meta_rel,
                        untracked=asset_untracked,
                    )
                if not is_dir and rel not in ctx.tracked:
                    ctx.add("6", ERROR, "アセット本体が git 追跡されていません", rel, untracked=True)


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
# **判定はディレクトリ配置の暗黙の規約ではなく、pipeline/repo.json の `skillSync` 宣言に従う。**
# 旧実装は `Packages/` の有無で対象を決めていたため、正本をルート直下 `skills/` に置く
# MySite を丸ごと素通りし、商品ページ規準 190 行がミラーへ届かないまま build が落ちる
# ところまで行った（2026-07-29）。さらに「対応するミラーが無ければ continue」していたため、
# **ミラーがまるごと欠けている状態も見逃していた**。宣言駆動にして両方を塞ぐ。
#
# 宣言の形（pipeline/repo.json）:
#   "skillSync": {
#     "sourceRoots": ["skills"],                       // glob 可。例: "Packages/*/skills"
#     "mirrors": [".claude/skills", ".agents/skills"],
#     "syncCommand": "node scripts/sync-skills.mjs"    // 失敗時に案内するコマンド
#   }
#
# 宣言が無いのにミラーが実在する場合は「宣言漏れ」として error にする。
# これをしないと、宣言を消すだけで検査を黙らせられてしまう。
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


def _mirror_roots_with_skills(ctx: RepoContext) -> list[str]:
    """実体としてスキルを 1 件以上持つミラーの一覧。宣言漏れの検出に使う。"""
    found = []
    for mirror_root in MIRROR_SKILL_DIRS:
        directory = ctx.root / mirror_root
        if directory.is_dir() and any(p.is_dir() for p in directory.iterdir()):
            found.append(mirror_root)
    return found


def check_12_skill_mirrors(ctx: RepoContext) -> None:
    decl = ctx.config.get("skillSync")

    if not isinstance(decl, dict):
        # 宣言が無いのにミラーが実在するなら宣言漏れ。宣言を消せば検査を黙らせられる、
        # という抜け道を塞ぐためここは error にする。
        for mirror_root in _mirror_roots_with_skills(ctx):
            ctx.add(
                "12",
                ERROR,
                "スキルのミラーが存在しますが pipeline/repo.json に skillSync 宣言がありません。"
                "正本・ミラー・同期コマンドを宣言してください（宣言が無いと同期を検査できません）。",
                mirror_root,
            )
        return

    source_patterns = decl.get("sourceRoots")
    mirror_roots = decl.get("mirrors")
    sync_command = decl.get("syncCommand")
    if not (isinstance(source_patterns, list) and source_patterns):
        ctx.add("12", ERROR, "skillSync.sourceRoots が宣言されていません。", "pipeline/repo.json")
        return
    if not (isinstance(mirror_roots, list) and mirror_roots):
        ctx.add("12", ERROR, "skillSync.mirrors が宣言されていません。", "pipeline/repo.json")
        return
    hint = f"同期コマンド: {sync_command}" if sync_command else "同期スクリプトを実行してください。"

    # 宣言した正本の解決。1 つも解決しないなら宣言と実体が食い違っている。
    source_dirs: list[Path] = []
    for pattern in source_patterns:
        source_dirs.extend(sorted(p for p in ctx.root.glob(str(pattern)) if p.is_dir()))
    if not source_dirs:
        ctx.add(
            "12",
            ERROR,
            f"skillSync.sourceRoots が指す正本が存在しません: {', '.join(map(str, source_patterns))}",
            "pipeline/repo.json",
        )
        return

    sources: dict[str, Path] = {}
    for source_dir in source_dirs:
        for skill in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            if skill.name in sources:
                ctx.add(
                    "12",
                    ERROR,
                    f"同名のスキルが複数の正本にあります: {skill.name}（どちらをミラーすべきか決まりません）",
                    skill.relative_to(ctx.root).as_posix(),
                )
                continue
            sources[skill.name] = skill
    if not sources:
        ctx.add("12", ERROR, "宣言した正本にスキルが 1 件もありません。", "pipeline/repo.json")
        return

    for mirror_root in mirror_roots:
        mirror_root = str(mirror_root)
        for name, source in sources.items():
            mirror = ctx.root / mirror_root / name
            rel = f"{mirror_root}/{name}"
            # 旧実装はここで continue しており、ミラーがまるごと欠けていても素通りしていた。
            if not mirror.is_dir():
                ctx.add("12", ERROR, f"正本にあるスキルのミラーがありません。{hint}", rel)
                continue

            expected = _skill_tree(source)
            actual = _skill_tree(mirror)
            if expected is None:
                ctx.add("12", ERROR, "正本のファイルを読めません。", source.relative_to(ctx.root).as_posix())
                continue
            if actual is None:
                ctx.add("12", ERROR, "ミラーのファイルを読めません。", rel)
                continue

            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
            if missing or extra or changed:
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
                    f"ミラーが正本（{source.relative_to(ctx.root).as_posix()}）と一致しません。"
                    f"{hint} {' / '.join(detail)}",
                    rel,
                )

            # ミラーは git 追跡されて初めてエージェントの手元へ届く。
            # 未追跡は「ローカルでは一致しているのに配布先には無い」状態になる（.meta で踏んだのと同じ型）。
            untracked = [
                f for f in sorted(actual) if f"{rel}/{f}" not in ctx.tracked
            ]
            if untracked:
                ctx.add(
                    "12",
                    ERROR,
                    f"ミラーのファイルが git 未追跡です（配布されません）: {', '.join(untracked)}",
                    rel,
                )

        # 正本に対応しないミラーは warn に留める。同期スクリプトの意味論が 2 通りあるため:
        # sync-agent-skills.mjs は「正本に無い別スキルのディレクトリには触れない」（他リポジトリの
        # パッケージ由来のスキルを許す設計）、MySite の sync-skills.mjs は生成先を完全に作り直す。
        # 前者では正当な状態なので error にはできない。ただし後者では次の同期で消えるため、
        # どちらにせよ「気づけない状態」を作らないよう可視化はする。
        mirror_dir = ctx.root / mirror_root
        if mirror_dir.is_dir():
            for orphan in sorted(p for p in mirror_dir.iterdir() if p.is_dir()):
                if orphan.name not in sources:
                    ctx.add(
                        "12",
                        WARN,
                        "このリポジトリの正本に対応しないミラーがあります"
                        "（他リポジトリのパッケージ由来なら正常。手で置いたなら次の同期で消える可能性があります）。",
                        f"{mirror_root}/{orphan.name}",
                    )


# ---------------------------------------------------------------------------
# 検査 13: lockstep スイートの版とスイート内の依存宣言が揃っていること
#
# lockstep のスイートは全パッケージを同じ版で同時に出荷する。にもかかわらず
# スイート内の `dependencies` が古い版のままだと、UPM は「その版以上」としか要求しないので、
# **利用者が古い基盤と新しい従属パッケージを組み合わせられてしまう**。
# 実際に TAE で、Curve / Gradient が基盤を `1.1.0` と宣言したまま 1.2.5 まで進み、
# その組み合わせでは 1.2.4 で直したサンプラー設定消失バグが残る状態になっていた
# （1.1.0 のリリース時だけ手で揃えており、1.2.0 以降で忘れられた。2026-07-29 に検出）。
# 手で揃える運用は忘れるので機械で止める。
# ---------------------------------------------------------------------------


def check_13_suite_versions(ctx: RepoContext) -> None:
    sale_unit = ctx.config.get("saleUnit") or {}
    if not ctx.is_product or sale_unit.get("versionPolicy") != "lockstep":
        return
    members = set(sale_unit.get("packages") or [])
    if len(members) < 2:
        return

    versions: dict[str, str] = {}
    for name, _package_dir, meta in ctx.packages:
        if name in members and meta.get("version"):
            versions[name] = str(meta["version"])

    distinct = sorted(set(versions.values()))
    if len(distinct) > 1:
        detail = ", ".join(f"{n}={v}" for n, v in sorted(versions.items()))
        ctx.add(
            "13",
            ERROR,
            f"lockstep のスイートで version が揃っていません（{detail}）",
            "pipeline/repo.json",
        )
        return
    suite_version = distinct[0] if distinct else None
    if not suite_version:
        return

    for name, package_dir, meta in ctx.packages:
        if name not in members:
            continue
        rel = (package_dir / "package.json").relative_to(ctx.root).as_posix()
        for dep_name, dep_version in (meta.get("dependencies") or {}).items():
            if dep_name not in members:
                continue  # スイート外への依存はこの検査の対象外
            if str(dep_version) != suite_version:
                ctx.add(
                    "13",
                    ERROR,
                    f"スイート内の依存宣言が版と一致しません: {dep_name} を {dep_version} と宣言していますが"
                    f"スイートは {suite_version} です。UPM の依存は「その版以上」なので、古い宣言のままだと"
                    f"利用者が古い {dep_name} と組み合わせられ、その版で直した不具合が残ります。",
                    rel,
                )


# ---------------------------------------------------------------------------
# 検査 14: 同梱物の宣言と実体が一致していること
#
# `samples` は Package Manager の Samples タブに出る**宣言**で、実体との対応は Unity が確かめない。
# 実体が無ければ利用者側でインポートに失敗し、逆に `Samples~/` へ置いただけで宣言し忘れると
# 同梱したつもりのサンプルが Package Manager から見えないまま出荷される（GOLD_STANDARD §2.5）。
# `Samples~` は Unity がインポートしない隠しフォルダなので、開発リポジトリを開いていても
# どちらの向きの食い違いにも気づけない。
#
# `Third Party Notices.md` は UAS 1.2.a の提出要件で、成分が無い場合も「含まれない」と明記した
# 最小ファイルを置く規約（§2.5）。無いと審査で落ちるのでリリースを止める。
#
# パッケージへ `CLAUDE.md` / `AGENTS.md` を同梱する場合は、リポジトリ直下（検査 1）と同じく
# 対で同一内容にする。片方だけだと、利用者側のエージェントが Claude 系か AGENTS 規約系かで
# 受け取る指示が変わる。
# ---------------------------------------------------------------------------

GUIDE_PAIR = ("CLAUDE.md", "AGENTS.md")


def check_14_bundled_contents(ctx: RepoContext) -> None:
    for name, package_dir, meta in ctx.packages:
        package_rel = package_dir.relative_to(ctx.root).as_posix()
        manifest_rel = f"{package_rel}/package.json"

        declared: set[str] = set()
        for index, sample in enumerate(meta.get("samples") or []):
            label = f"samples[{index}]"
            if not isinstance(sample, dict):
                ctx.add("14", ERROR, f"{name}: {label} はオブジェクトである必要があります", manifest_rel)
                continue
            for key in ("displayName", "description", "path"):
                if not str(sample.get(key) or "").strip():
                    ctx.add("14", ERROR, f"{name}: {label} に {key} がありません", manifest_rel)
            sample_path = str(sample.get("path") or "").strip()
            if not sample_path:
                continue
            target = package_dir / sample_path
            declared.add(os.path.normpath(target))
            if not target.is_dir():
                ctx.add("14", ERROR, f"{name}: {label} の path に実体がありません: {sample_path}", manifest_rel)

        # 実体だけがある側の検出。`Samples~/<名前>` が慣例だが、`Samples~/<まとまり>/<名前>` と
        # 束ねる書き方も許されるので、**直下のフォルダ配下に宣言が 1 つも無い**ときだけ落とす
        # （束ねたフォルダ自体は宣言されないため、直下しか見ないと誤検出する）。まとまりの中で
        # 一部だけ宣言し忘れた場合は素通りするが、まとめる構成を採るかどうかは書き手が決められる。
        samples_dir = package_dir / "Samples~"
        if samples_dir.is_dir():
            for child in sorted(samples_dir.iterdir()):
                if not child.is_dir() or child.is_symlink():
                    continue
                prefix = os.path.normpath(child) + os.sep
                if any(entry == os.path.normpath(child) or entry.startswith(prefix) for entry in declared):
                    continue
                ctx.add(
                    "14",
                    ERROR,
                    f"{name}: Samples~/{child.name} が package.json の samples に宣言されていません"
                    f"（宣言しないと Package Manager から見えません。GOLD_STANDARD §2.5）",
                    manifest_rel,
                )

        if not (package_dir / "Third Party Notices.md").is_file():
            ctx.add("14", ERROR, f"{name}: Third Party Notices.md がありません（§2.5・UAS 1.2.a）", package_rel)

        # パッケージ直下のガイドは購入者のエージェント向けで、2026-07-31 から必須。
        # リポジトリ直下（開発者向け）とはファイル名が同じでも読み手が違うため、
        # 「片方だけある」「中身が一致しない」に加えて「そもそも無い」も落とす。
        claude, agents = package_dir / GUIDE_PAIR[0], package_dir / GUIDE_PAIR[1]
        if not claude.is_file() and not agents.is_file():
            ctx.add(
                "14",
                ERROR,
                f"{name}: 購入者のエージェント向けの {GUIDE_PAIR[0]} / {GUIDE_PAIR[1]} がありません"
                f"（GOLD_STANDARD §2.2・§2.5。リポジトリ直下の開発者向けガイドをコピーしないこと）",
                package_rel,
            )
        elif claude.is_file() != agents.is_file():
            present, missing = GUIDE_PAIR if claude.is_file() else GUIDE_PAIR[::-1]
            ctx.add(
                "14",
                ERROR,
                f"{name}: {present} はありますが {missing} がありません（対で同一内容にする規約）",
                package_rel,
            )
        elif claude.read_bytes() != agents.read_bytes():
            ctx.add("14", ERROR, f"{name}: 同梱の CLAUDE.md と AGENTS.md の内容が一致しません", package_rel)


# ---------------------------------------------------------------------------
# 検査 15: 同梱スクリーンショットが UI の実装より古くないこと
#
# UI の文言・ラベル・活性状態・HelpBox を直しても、その UI を写した画像は取り残される。
# 画像は「主張が散るサーフェス」の中で**唯一 grep で見つけられない面**なので、
# 文言を直した本人が意識して探さない限り必ず置き去りになる。
# 実際に TAE curve で、1.2.3 で直した HelpBox の文言が同梱 `inspector.png` に旧文言のまま残り、
# 購入者が README の設定ガイドで最初に見る図として数バージョン出荷され続けた
# （同じ画像を商品ページ 19 言語も参照していた。2026-07-31 検出）。
#
# 判定は git のコミット時刻という**代理指標**で、「必ず間違い」を意味しない
# （UI に影響しないコード変更でも時刻は進む）。したがって severity は warn に留め、
# リリースを止めるのではなく「開いて見比べろ」を出す。error にすると UI を触るたびに
# 撮り直しか waiver を強いることになり、包括 waiver で黙らせる運用に倒れて本命の事故も見逃す。
#
# UI を決めるのは `Editor/`（描画コード）と `Runtime/`（Inspector が描く `[SerializeField]` と
# その属性・範囲）、そして**リポジトリ内の翻訳カタログ**。カタログをリポジトリ全体で見るのは、
# TAE の Curve/Gradient が自前のカタログを持たず基盤パッケージのカタログを引いており、
# 基盤側のカタログだけを直すと従属パッケージの画像が所属パッケージを触らずに古くなるため。
# ---------------------------------------------------------------------------

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# UI を決めるパッケージ内のディレクトリ（画像との時刻比較の対象）
UI_SOURCE_DIRS = ("Editor", "Runtime")
# 同梱画像でも「UI を写したもの」ではない直下フォルダ。`Samples~/` は利用者のプロジェクトへ
# 取り込まれる素材、`Tests/` は検証用の入力で、どちらも UI の見た目とは無関係に古くてよい。
NON_SCREENSHOT_DIRS = ("Samples~", "Tests")


def _last_commit(root: Path, *paths: str) -> tuple[int, str] | None:
    """指定パス群の最終コミットを (epoch 秒, YYYY-MM-DD) で返す。

    git が使えない・履歴に無い（浅い clone・未コミット）場合は None。
    時刻を推測せず黙るのは検査 0 の `PIPELINE_TODAY` 解決と同じ流儀
    （git という証拠が無いときは何も主張しない）。
    """
    if not paths:
        return None
    output = run_git(root, "log", "-1", "--format=%ct %cs", "--", *paths).split()
    if len(output) != 2 or not output[0].isdigit():
        return None
    return int(output[0]), output[1]


def _worktree_dirty_paths(root: Path) -> set[str]:
    """作業ツリーで変更・追加されているパス（git 未追跡も含む）。"""
    result: set[str] = set()
    for line in run_git(root, "status", "--porcelain").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]  # リネームは移動先だけを見る
        result.add(path.strip('"'))
    return result


def check_15_stale_screenshots(ctx: RepoContext) -> None:
    """検査 15「画像と実物の一致」。同梱スクリーンショットが現物の UI と食い違っていないかを見る。

    かつて「同梱画像の鮮度」と呼んでいたが、**「いつ撮ったか」の話に読めて誤解を招く**ため
    2026-08-10 に呼び名を変えた。実際に問うているのは撮影時期ではなく、
    **その画像が今の画面と同じものを写しているか**である（判定にコミット時刻を使うのは
    それを直接測れないからで、時刻は代理指標にすぎない）。
    """
    # 翻訳カタログはリポジトリ内で共有されうるので、所属パッケージで絞らない
    locale_paths = sorted(t for t in ctx.tracked if t.endswith(".json") and "/Locales/" in t)

    tracked_images = sorted(t for t in ctx.tracked if t.lower().endswith(IMAGE_SUFFIXES))

    targets: list[tuple[str, Path, str, list[str]]] = []
    for name, package_dir, _ in ctx.packages:
        package_rel = package_dir.relative_to(ctx.root).as_posix()
        prefix = f"{package_rel}/"
        images = []
        for tracked in tracked_images:
            if not tracked.startswith(prefix):
                continue
            if tracked[len(prefix) :].split("/")[0] in NON_SCREENSHOT_DIRS:
                continue
            if ctx.is_waived("15", tracked):
                continue
            images.append(tracked)
        if images:
            targets.append((name, package_dir, package_rel, images))

    if not targets:
        return  # 同梱画像を持たないリポジトリでは git を余計に叩かない

    dirty = _worktree_dirty_paths(ctx.root)

    for name, package_dir, package_rel, images in targets:
        sources: list[tuple[str, list[str]]] = [
            (f"{directory}/", [f"{package_rel}/{directory}"])
            for directory in UI_SOURCE_DIRS
            if (package_dir / directory).is_dir()
        ]
        if locale_paths:
            sources.append(("翻訳カタログ", locale_paths))

        newest: tuple[int, str, str] | None = None
        for label, paths in sources:
            commit = _last_commit(ctx.root, *paths)
            if commit and (newest is None or commit[0] > newest[0]):
                newest = (commit[0], commit[1], label)
        if newest is None:
            continue  # UI 側の履歴が読めない（git が無い・浅い clone）なら何も言わない

        ui_epoch, ui_date, ui_label = newest
        for rel in images:
            if rel in dirty:
                continue  # 作業ツリーで触っている＝撮り直しの最中とみなす
            commit = _last_commit(ctx.root, rel)
            if commit is None or commit[0] >= ui_epoch:
                continue
            ctx.add(
                "15",
                WARN,
                f"{name}: 同梱スクリーンショットが実物の UI と一致していないおそれがあります"
                f"（UI は {ui_date} に {ui_label} を変更）。同梱画像は購入者が README や "
                f"Documentation~ で最初に見る説明なので、現行 UI と食い違うと実装ではなく画像のほうを"
                f"信じます。画像は文言を grep しても出てこない唯一のサーフェスなので、UI を直しても"
                f"取り残されます。開いて現行 UI と見比べ、違っていれば撮り直してください。"
                f"**同じ構図のコピーは商品ページ（external-content/products/<slug>/assets/）と"
                f"出品プラットフォームにもあり、そちらはこの検査の対象外です**。3 面まとめて見直してください"
                f"（GOLD_STANDARD §2.5・§2.10）",
                rel,
            )


# ---------------------------------------------------------------------------
# 検査 16: 同梱物の変更が CHANGELOG に記録されていること
#
# `.tgz` に入るファイルは**そのまま購入者の手元へ届く**。実装だけでなく、README・詳細ガイド・
# サンプル・エージェント向けガイド・テストコードのコメントまで、すべて購入者が読む面である。
# ところが変更履歴は人手で書くので、「同梱物を足した／変えた」のに書き忘れても誰も気づかない。
# 購入者は変更履歴以外に「何が変わったか」を知る手段を持たないため、書き忘れは
# **出荷してから発覚し、遡って直せない**（過去の版の記録は書き換えない方針のため）。
#
# 実際に TAE の 2.0.0 直前の監査で、最新タグ 1.2.6 以降に足した同梱物のうち 5 件が
# `[Unreleased]` に落ちていた。とくに `CLAUDE.md` / `AGENTS.md` は**新規同梱**なのに
# 「手引きに追記した」としか読めない書き方で `ドキュメント` 節に入っており、購入者からは
# 「もともと入っていたものを直した」と読めた（2026-08-02 検出）。
#
# 3 段構えで、断定できる度合いに応じて severity を分ける:
#
# 1. **error**: 同梱物が変わったのに記録先が丸ごと空。これは自然言語の判断を一切含まない
#    構造的な事実（`[Unreleased]` にもタグ未作成の版の節にも 1 行も無い）なので、検査 14 と
#    同じくリリースを止める。
# 2. **warn**: 新規追加があるのに「追加 / Added」節が無い。節の名前は表記の規約なので
#    （`New` などの言い換えもありうる）自然言語のヒューリスティックであり、§2.10 の方針どおり
#    warn に留める。
# 3. **warn**: **購入者が同梱物そのものとして受け取るファイル**（パッケージ直下の `.md`・
#    `Documentation~/`・`Samples~/`）を新規追加したのに、その名前が「追加 / Added」節の本文に
#    出てこない。上の TAE の事故はここで鳴る。`Editor/` / `Runtime/` のソースは「挙動」として
#    書かれるものでファイル名を求めるのは筋違いなので対象にしない。
#
# **「tgz に入るパス」の判定根拠**: 1.2.6 の実物 3 本（base / curve / gradient）と、EPE 2.0.1 の
# 実物を、それぞれ同じタグ時点の `git ls-tree` と突き合わせて確認した。中身は
# **`Packages/<name>/` 配下の git 追跡ファイル全部と完全一致**で、`.meta` も `Tests/` も
# `Samples~` も `Documentation~` も入る。したがって除外するのは npm / `Client.Pack` が
# 既定で落とす OS・VCS 由来のファイルだけでよい（現状どのリポジトリにも 1 件も無い）。
#
# **タグが無いリポジトリは検査しない**。比較の起点が無く、「最初のリリースまでの全部」を
# 記録漏れとして鳴らしても新規リポジトリを騒がせるだけで意味がない。浅い clone でタグが
# 取れない場合も同じく黙る（検査 15 と同じ「git という証拠が無いときは何も主張しない」）。
#
# **CHANGELOG.md が無いパッケージも検査しない**。その不備は check_extra が warn で言うので、
# ここで重ねて鳴らすと同じ話が 2 度出るだけになる。
#
# **記録先は `[Unreleased]` だけではない**。リリース工程は「検査 → CHANGELOG 畳み込み →
# 成果物 → コミット → タグ」の順で進むため、畳み込んだ直後・タグを打つ前には `[Unreleased]` が
# 空になる。この窓で誤って error を出さないよう、**まだタグの無い版の節**も記録先として数える。
# ---------------------------------------------------------------------------

# npm / Client.Pack が既定で tarball から落とす OS・VCS 由来の名前（パス区切りごとに照合する）。
# ここに載らないものは `.meta` も隠しフォルダも含めてすべて同梱物として扱う。
NON_BUNDLED_NAME_PATTERNS = (
    ".git*", ".hg", ".svn", "CVS", "node_modules", ".DS_Store", "._*",
    "*.orig", "*.swp", "*.swo", ".npmrc", ".npmignore", "npm-debug.log", "package-lock.json",
)

CHANGELOG_VERSION_RE = re.compile(r"^##\s+\[?([^\]\s]+)\]?")
CHANGELOG_SECTION_RE = re.compile(r"^###\s+(.+?)\s*$")
# 「追加」節の見出し。日英どちらの表記でも受ける（併記の CHANGELOG では両方が現れる）
ADDED_SECTION_RE = re.compile(r"^(追加|added)\b", re.IGNORECASE)
# 購入者が「同梱物そのもの」として受け取る直下フォルダ
BUNDLED_ARTIFACT_DIRS = ("Documentation~", "Samples~")


@dataclass
class ChangelogSection:
    """CHANGELOG の版ごとの節（`## [x.y.z]` 単位）。"""

    label: str
    body: list[str] = field(default_factory=list)
    added_body: list[str] = field(default_factory=list)


def parse_changelog(text: str) -> list[ChangelogSection]:
    sections: list[ChangelogSection] = []
    current: ChangelogSection | None = None
    subsection: str | None = None
    for line in text.splitlines():
        version = CHANGELOG_VERSION_RE.match(line)
        if version:
            current = ChangelogSection(version.group(1))
            subsection = None
            sections.append(current)
            continue
        if current is None:
            continue
        current.body.append(line)
        heading = CHANGELOG_SECTION_RE.match(line)
        if heading:
            subsection = heading.group(1)
            continue
        if subsection and ADDED_SECTION_RE.match(subsection):
            current.added_body.append(line)
    return sections


def _tag_exists(version: str, tags: set[str], policy: str | None) -> bool:
    """version に対応するタグが既にあるか。`tagPolicy` があればそれに従う。"""
    if policy == "bare":
        return version in tags
    if policy == "v-prefix":
        return f"v{version}" in tags
    return bool({version, f"v{version}"} & tags)  # 宣言が無ければどちらの命名でも既出とみなす


def changelog_sinks(
    sections: list[ChangelogSection], package_version: str, tags: set[str], policy: str | None
) -> list[ChangelogSection]:
    """「最新タグ以降の変更を書く先」になりうる節を返す。

    `[Unreleased]` に加え、**まだタグの無い版の節**（畳み込み済み・タグ未作成）を含める。
    """
    sinks = []
    for section in sections:
        if section.label.lower() == "unreleased":
            sinks.append(section)
        elif package_version and section.label == package_version and not _tag_exists(package_version, tags, policy):
            sinks.append(section)
    return sinks


def is_bundled_path(rel_in_package: str) -> bool:
    """パッケージルート相対のパスが `.tgz` に入るか。"""
    return not any(
        fnmatch.fnmatch(segment, pattern)
        for segment in rel_in_package.split("/")
        for pattern in NON_BUNDLED_NAME_PATTERNS
    )


def bundled_artifact_token(rel_in_package: str) -> str | None:
    """「新規追加そのものが変更履歴の主語になる同梱物」なら、記録に現れるべき語を返す。

    `Documentation~/` と `Samples~/` は 1 回の追加で何十ファイルにもなるためフォルダ名へ畳む。
    """
    if rel_in_package.endswith(".meta"):
        return None  # `.meta` は同梱物だが変更履歴の主語にはならない（本体と対で動く）
    head = rel_in_package.split("/")[0]
    if head in BUNDLED_ARTIFACT_DIRS:
        return head
    if "/" in rel_in_package or not rel_in_package.lower().endswith(".md"):
        return None  # 実装ソースは「挙動」として書かれるのでファイル名は求めない
    if rel_in_package == "CHANGELOG.md":
        return None  # 自分自身の追加は自分に書けない
    return rel_in_package


def _latest_tag(root: Path) -> str | None:
    """HEAD から辿れる直近のタグ。無い・git が読めない場合は None。"""
    tag = run_git(root, "describe", "--tags", "--abbrev=0").strip()
    if not tag or not run_git(root, "log", "--format=%H", "-1", tag).strip():
        return None
    return tag


def check_16_changelog_coverage(ctx: RepoContext) -> None:
    if not ctx.packages:
        return
    tag = _latest_tag(ctx.root)
    if tag is None:
        return  # 比較の起点が無い（初回リリース前・浅い clone）
    tags = set(run_git(ctx.root, "tag", "--list").split())
    policy = ctx.config.get("tagPolicy")

    for name, package_dir, meta in ctx.packages:
        package_rel = package_dir.relative_to(ctx.root).as_posix()
        changelog = package_dir / "CHANGELOG.md"
        if not changelog.is_file():
            continue  # 不在は check_extra が warn で言う
        changelog_rel = f"{package_rel}/CHANGELOG.md"
        if ctx.is_waived("16", changelog_rel):
            continue
        try:
            text = changelog.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # タグ → 作業ツリーの差分。未追跡ファイルは検査 6 が error で拾うのでここでは見ない
        changed: list[str] = []
        added: list[str] = []
        for line in run_git(ctx.root, "diff", "--name-status", "-M", tag, "--", package_rel).splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status, path = parts[0], parts[-1]
            if not path.startswith(f"{package_rel}/"):
                continue
            rel_in_package = path[len(package_rel) + 1 :]
            if not is_bundled_path(rel_in_package):
                continue
            if rel_in_package in ("CHANGELOG.md", "CHANGELOG.md.meta"):
                continue  # 記録そのものの変更に記録を要求すると循環する
            changed.append(rel_in_package)
            if status.startswith("A"):
                added.append(rel_in_package)
        if not changed:
            continue

        sinks = changelog_sinks(parse_changelog(text), str(meta.get("version") or ""), tags, policy)
        recorded = "\n".join(line for sink in sinks for line in sink.body).strip()
        added_text = "\n".join(line for sink in sinks for line in sink.added_body).strip()

        if not recorded:
            ctx.add(
                "16",
                ERROR,
                f"{name}: 最新タグ {tag} 以降に同梱物が {len(changed)} 件変わっていますが、CHANGELOG に"
                f"記録がありません（`[Unreleased]` もタグ未作成の版の節も空）。`.tgz` に入るファイルは"
                f"そのまま購入者の手元へ届き、購入者は変更履歴以外に何が変わったかを知る手段を持ちません。"
                f"過去の版の記録は後から書き換えない方針なので、書き忘れたまま出荷すると取り返せません"
                f"（GOLD_STANDARD §2.5・§2.10）",
                changelog_rel,
            )
            continue

        if added and not added_text:
            ctx.add(
                "16",
                WARN,
                f"{name}: 同梱物に {len(added)} 件のファイルが新規追加されていますが、CHANGELOG の記録に"
                f"「追加」/「Added」の節がありません。既存の変更と新規同梱は購入者にとって別の出来事なので、"
                f"追加は追加として立ててください（GOLD_STANDARD §2.5・§2.10）",
                changelog_rel,
            )
            continue  # 節ごと無いなら、下の名指し検査は同じ話を繰り返すだけ

        lowered = added_text.lower()
        for token in sorted({t for t in (bundled_artifact_token(a) for a in added) if t}):
            if token.lower() in lowered:
                continue
            target = f"{package_rel}/{token}"
            if ctx.is_waived("16", target):
                continue
            ctx.add(
                "16",
                WARN,
                f"{name}: `{token}` を新規に同梱しましたが、CHANGELOG の「追加」/「Added」節が"
                f"この名前に触れていません。既存ファイルの更新と新規同梱は購入者にとって別の出来事で、"
                f"更新としか読めない書き方だと「もともと入っていたもの」と受け取られます"
                f"（TAE で `CLAUDE.md` / `AGENTS.md` の新規同梱が「手引きに追記した」としか読めない形で"
                f"別の節に入っていた。2026-08-02 検出）。「追加」節へ新規同梱として明記してください"
                f"（GOLD_STANDARD §2.5・§2.10）",
                target,
            )


# ---------------------------------------------------------------------------
# 検査 17: ライセンス本文の面間整合と、保証範囲の主張の一致
#
# 自作 EULA の本文は 1 か所では完結せず、少なくとも 5 つの面へ同時に散る:
#   (1) サイトの正本 `external-content/products/<slug>/licenses/eula.md`
#   (2) パッケージ同梱の `LICENSE.md`（`.tgz` に入り購入者の手元へ届く）
#   (3) ライセンス掲示ページ `pages/licenses/<lang>.mdx`
#   (4) 出品プラットフォームの説明文（`descriptions/*.md` / `publish.json`）
#   (5) CHANGELOG（購入者が「権利義務が変わった」と知る唯一の経路）
# 片面だけ直すと購入者の手元と正本が食い違う。実際に EPE で、`LICENSE.md` の全面差し替え
# （4 行の英文 → 108 行の EULA。購入者の権利義務が変わる）が CHANGELOG に 1 行も無いまま
# リリース直前まで進んだ（2026-08-02 検出）。検査 16 は同梱物の変更一般を warn で拾うが、
# **ライセンスは購入者の権利義務そのもの**なので、この検査で error へ格上げする。
#
# さらに、EULA 第 10 条で保証範囲を「購入の時点で商品ページに明示した機能および対応環境の範囲」に
# 紐づけた（2026-08-06 確定）ことで、**商品ページの対応環境の記載が契約の内容になった**。
# `package.json` の `unity` と説明文の対応環境が食い違うと、そのまま契約不適合になる。
# これは EULA の文言変更が生んだ新しい義務であり、人間の注意力に任せてよい種類のものではない。
#
# サイトリポジトリが解決できない環境（他人の clone・CI の最小構成）では黙る。
# 検査 10・12 と同じ流儀で、「証拠が無いときは何も主張しない」。
# ---------------------------------------------------------------------------

# CHANGELOG がライセンスの変更に触れているとみなす語。日英どちらの表記でも拾う
LICENSE_MENTION_TOKENS = ("license", "licence", "ライセンス", "使用許諾", "eula", "規約")


def normalize_license_text(text: str) -> str:
    """ライセンス本文を比較用に正規化する（コードフェンス除去＋空白畳み）。

    サイト側の正本は**掲示ページ**なので、同梱物とバイト一致とは限らない。実測で 3 通りある:
      - 完全一致（EPE: `eula.md` がそのまま `LICENSE.md`）
      - 掲示用の前置きを添え、本文を ```text フェンスで包む（UEL: `mit.md` が素の MIT を包む）
      - 同一商品内でパッケージごとに前文だけ差し替える（UMPD の companion パッケージ）
    最初の 2 つは「同梱物の本文が正本に含まれる」で等しく通り、3 つ目だけが外れる。
    バイト一致を全体の規約にすると、正当な掲示の作り方まで落としてしまう。
    """
    without_fence = re.sub(r"^\s*```[^\n]*$", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", without_fence).strip()


def _site_product_dir(ctx: RepoContext) -> Path | None:
    """サイトリポジトリ側の商品ディレクトリ。解決できなければ None。"""
    slug = str(ctx.config.get("productSlug") or "").strip()
    if not slug:
        return None
    site_root = resolve_site_repo(ctx)
    if site_root is None:
        return None
    product_dir = site_root / "external-content" / "products" / slug
    return product_dir if product_dir.is_dir() else None


def _license_page_langs(product_dir: Path) -> set[str] | None:
    """`pages/licenses/` にある言語タグの集合。ディレクトリが無ければ None。"""
    pages_dir = product_dir / "pages" / "licenses"
    if not pages_dir.is_dir():
        return None
    return {path.stem for path in pages_dir.glob("*.mdx")}


def check_17_license_surfaces(ctx: RepoContext) -> None:
    if not ctx.packages:
        return
    product_dir = _site_product_dir(ctx)
    if product_dir is None:
        return

    publish = load_json(product_dir / "publish.json") or {}
    core = publish.get("core") if isinstance(publish.get("core"), dict) else {}
    license_decl = core.get("license") if isinstance(core.get("license"), dict) else {}
    is_self_license = str(license_decl.get("source") or "") == "self"

    # --- 17-a: 正本の実在と、同梱 LICENSE.md の本文が正本に含まれること -----
    canonical_norm: str | None = None
    if is_self_license:
        ref = str(license_decl.get("ref") or "").strip()
        if not ref:
            ctx.add("17", ERROR, "publish.json の core.license.source が self ですが ref がありません")
        else:
            canonical = product_dir / ref
            if not canonical.is_file():
                ctx.add("17", ERROR, f"publish.json の core.license.ref が指す正本がサイト側にありません: {ref}")
            else:
                canonical_norm = normalize_license_text(canonical.read_text(encoding="utf-8", errors="ignore"))

    if canonical_norm is not None:
        primary = str((ctx.config.get("saleUnit") or {}).get("primaryPackage") or "")
        for name, package_dir, _ in ctx.packages:
            package_rel = package_dir.relative_to(ctx.root).as_posix()
            bundled = package_dir / "LICENSE.md"
            target = f"{package_rel}/LICENSE.md"
            if ctx.is_waived("17", target) or not bundled.is_file():
                continue  # 不在は check_extra が warn で言う
            if normalize_license_text(bundled.read_text(encoding="utf-8", errors="ignore")) in canonical_norm:
                continue
            if name == primary:
                ctx.add(
                    "17",
                    ERROR,
                    f"{name}: 同梱 LICENSE.md の本文が、サイトの正本（{ref}）に含まれていません。"
                    f"`.tgz` に入るファイルなので、そのまま購入者の手元へ届きます。"
                    f"販売単位の主パッケージの契約書は、掲示している正本と食い違ってはいけません。"
                    f"どちらが新しいかを確かめ、正本を決めてから両方を揃えてください"
                    f"（GOLD_STANDARD §2.5・§2.10）",
                    target,
                )
            else:
                ctx.add(
                    "17",
                    WARN,
                    f"{name}: 同梱 LICENSE.md の本文が、サイトの正本（{ref}）に含まれていません。"
                    f"**その版はサイトのどこにも掲示されていない**状態であり、購入者が手元の契約書を"
                    f"第三者へ示せる URL がありません。スイートは全パッケージへ**バイト同一**のものを"
                    f"同梱する規約（GOLD_STANDARD §2.10 検査 17）なので、主パッケージ側も同じ状態か、"
                    f"この従属パッケージだけが独自版を持っているかのどちらかです。"
                    f"後者なら差し替え版を廃し、主パッケージと同一のものへ揃えてください",
                    target,
                )

    # --- 17-b: ライセンス掲示ページの言語セット -----------------------------
    page_langs = _license_page_langs(product_dir)
    if page_langs is not None:
        declared = ctx.config.get("licensePageLanguages")
        if isinstance(declared, list) and declared:
            expected = {str(item) for item in declared}
            if page_langs != expected:
                missing = sorted(expected - page_langs)
                extra = sorted(page_langs - expected)
                detail = "・".join(
                    part
                    for part in (
                        f"不足: {', '.join(missing)}" if missing else "",
                        f"余分: {', '.join(extra)}" if extra else "",
                    )
                    if part
                )
                ctx.add(
                    "17",
                    ERROR,
                    f"pages/licenses/ の言語セットが pipeline/repo.json の licensePageLanguages の宣言と"
                    f"一致しません（{detail}）。法文だけ言語を絞る方針を採るなら、宣言と実体を必ず揃えて"
                    f"ください。削り忘れた版が残ると、正本と違う内容の契約書が読める状態になります",
                )
        else:
            meta_langs = load_json(product_dir / "meta.json") or {}
            site_langs = {str(item) for item in (meta_langs.get("langs") or [])}
            if site_langs and page_langs != site_langs:
                ctx.add(
                    "17",
                    WARN,
                    f"pages/licenses/ の言語セット（{len(page_langs)} 言語）が meta.json の langs"
                    f"（{len(site_langs)} 言語）と一致しません。法文だけ言語を絞るのは正当な方針ですが、"
                    f"意図した絞り込みなら pipeline/repo.json に licensePageLanguages を宣言してください"
                    f"（宣言があれば error で厳密に照合します。無いと削り忘れと区別できません）",
                )

    # --- 17-c: LICENSE.md の変更が CHANGELOG に記録されているか -------------
    if is_self_license:
        _check_license_changelog(ctx)
        _check_license_identical_in_suite(ctx)

    # --- 17-d: 対応環境の主張が package.json と一致するか -------------------
    #
    # 照合は `unity` だけでなく **`unity` + `unityRelease` を連結した版**で行う。
    # `unity` だけで見ていた頃は「Unity 2022.3 以降」も「2022.3.0f1 以降」も
    # 「2022.3 以降（最小要件 2022.3.0f1）」も等しく通り、**5 商品で書式が 3 通りに割れていても
    # 検出できなかった**（2026-08-06 検出）。動作環境は法定の表示事項かつ契約の内容なので、
    # 粒度のばらつきそのものが問題になる。
    #
    # 商品説明文（`descriptions/*.md`）も同じ版を名乗っているかを見る。
    # 従来は `publish.json` の `requirements` しか照合しておらず、**販売プラットフォームへ実際に
    # 投入される本文は誰とも突き合わされていなかった**（両者は別々に手書きされている）。
    requirements = ""
    description_input = publish.get("descriptionInput")
    if isinstance(description_input, dict):
        requirements = str(description_input.get("requirements") or "")

    description_texts: list[tuple[str, str]] = []
    for path in sorted((product_dir / "descriptions").glob("*.md")):
        try:
            description_texts.append((path.name, path.read_text(encoding="utf-8")))
        except OSError:
            continue

    for name, package_dir, meta in ctx.packages:
        rel = (package_dir / "package.json").relative_to(ctx.root).as_posix()
        unity = str(meta.get("unity") or "").strip()
        if not unity or ctx.is_waived("17", rel):
            continue
        release = str(meta.get("unityRelease") or "").strip()
        wanted = f"{unity}.{release}" if release else unity

        if requirements and wanted not in requirements:
            ctx.add(
                "17",
                ERROR,
                f"{name}: package.json の対応 Unity `{wanted}`（unity + unityRelease）が、"
                f"publish.json の descriptionInput.requirements に現れません（現在の記載: 「{requirements}」）。"
                f"EULA 第 10 条で保証範囲を「購入の時点で商品ページに明示した機能および対応環境の範囲」に"
                f"紐づけたため、**この記載は契約の内容そのもの**です。"
                f"`unity` だけの一致では書式のばらつき（「2022.3 以降」と「2022.3.0f1 以降」の混在）を"
                f"止められないので、**連結した版で名乗る**こと",
                rel,
            )
        for fname, text in description_texts:
            if wanted in text:
                continue
            target = f"descriptions/{fname}"
            if ctx.is_waived("17", target):
                continue
            ctx.add(
                "17",
                ERROR,
                f"{name}: 商品説明文 {fname} が対応 Unity `{wanted}` を名乗っていません。"
                f"**この本文は販売プラットフォームへそのまま投入されます**。"
                f"publish.json の requirements と descriptions は別々に手書きされるので、"
                f"片方だけ直すと購入者が読む側が古いまま残ります",
                target,
            )


def _check_license_identical_in_suite(ctx: RepoContext) -> None:
    """スイート内の全パッケージの同梱 LICENSE.md がバイト同一か（GOLD_STANDARD §2.10・D24）。

    **1 商品 = 1 ライセンス**なので、同じ商品のどのパッケージを見ても同じ契約書でなければならない。
    パッケージごとに前文を差し替えた版は作らない。差し替え版は**サイトのどこにも掲示されていない
    契約書**を生み、購入者が手元の契約書を第三者へ示せる URL が無くなる
    （UMPD の companion が実際にその状態だった。2026-08-06 検出）。
    「どのパッケージの契約書か」の書き分けは、契約書の側ではなく**規約本体の定義**で解く。

    当初は「最新タグ以降に一部のパッケージだけ変わっていないか」（co-movement）で測っていたが、
    2 つの穴があった:
      - **既に食い違っている状態を検出できない**。どちらも変わっていなければ黙る（UMPD がまさにこれ）。
      - **収束方向の片側変更を誤検出する**。差し替え版を正へ揃える修正は「片方だけ変わった」に見える。
    バイト同一で測ればどちらも起きず、判定が「今どうなっているか」だけで決まる。
    """
    if len(ctx.packages) < 2:
        return

    digests: dict[str, list[str]] = {}
    for name, package_dir, _ in ctx.packages:
        package_rel = package_dir.relative_to(ctx.root).as_posix()
        license_path = package_dir / "LICENSE.md"
        if not license_path.is_file() or ctx.is_waived("17", f"{package_rel}/LICENSE.md"):
            continue  # 不在は check_extra が warn で言う
        digests.setdefault(hashlib.sha256(license_path.read_bytes()).hexdigest(), []).append(name)

    if len(digests) < 2:
        return

    groups = "／".join(
        f"[{', '.join(sorted(names))}]" for _, names in sorted(digests.items(), key=lambda kv: sorted(kv[1]))
    )
    ctx.add(
        "17",
        ERROR,
        f"販売単位の中で同梱 LICENSE.md の内容が揃っていません（{len(digests)} 種類: {groups}）。"
        f"**1 商品 = 1 ライセンス**なので、同じ商品を買った購入者が、どのパッケージを見るかで"
        f"違う契約書を読む状態になります。パッケージごとに前文を差し替えた版は作らず、"
        f"全パッケージへバイト同一のものを同梱してください。「どのパッケージの契約書か」の"
        f"書き分けは、契約書ではなく**規約本体の定義**で解きます（GOLD_STANDARD §2.10 検査 17）",
    )


def _check_license_changelog(ctx: RepoContext) -> None:
    """最新タグ以降に LICENSE.md が変わっているのに、CHANGELOG がそれに触れていない場合に落とす。"""
    tag = _latest_tag(ctx.root)
    if tag is None:
        return  # 比較の起点が無い（初回リリース前・浅い clone）
    tags = set(run_git(ctx.root, "tag", "--list").split())
    policy = ctx.config.get("tagPolicy")

    for name, package_dir, meta in ctx.packages:
        package_rel = package_dir.relative_to(ctx.root).as_posix()
        license_rel = f"{package_rel}/LICENSE.md"
        if ctx.is_waived("17", license_rel):
            continue
        if not run_git(ctx.root, "diff", "--name-only", tag, "--", license_rel).strip():
            continue  # 変わっていない

        changelog = package_dir / "CHANGELOG.md"
        if not changelog.is_file():
            continue  # 不在は check_extra が warn で言う
        try:
            text = changelog.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        sinks = changelog_sinks(parse_changelog(text), str(meta.get("version") or ""), tags, policy)
        recorded = "\n".join(line for sink in sinks for line in sink.body).lower()
        if any(token in recorded for token in LICENSE_MENTION_TOKENS):
            continue

        ctx.add(
            "17",
            ERROR,
            f"{name}: 最新タグ {tag} 以降に LICENSE.md が変わっていますが、CHANGELOG がライセンスに"
            f"触れていません。ライセンスの変更は**購入者の権利義務そのものの変更**であり、同梱物の"
            f"更新一般（検査 16）より重い出来事です。購入者は変更履歴以外にそれを知る手段を持たず、"
            f"過去の版の記録は後から書き換えない方針なので、書き忘れたまま出荷すると取り返せません"
            f"（EPE で 4 行の英文から 108 行の EULA への全面差し替えが記録されないままリリース直前まで"
            f"進んだ。2026-08-02 検出。GOLD_STANDARD §2.5・§2.10）",
            f"{package_rel}/CHANGELOG.md",
        )


# ---------------------------------------------------------------------------
# 検査 18: 翻訳カタログ（`*.l10n-manifest.json` + `Locales/*.json`）の整合
#
# 翻訳カタログは 5 つの Unity リポジトリ合計で 97 個の JSON に散っており、その整合は
# UEL の C# 検証器 `EditorL10nValidator` が見ている。ただしそれは **Unity の Editor 上で
# 人がメニュー（`Tools > ... > Validate Catalogs`）を押したときにしか動かない**。
# batchmode 用の入口 `ValidateForCI()` は実装済みなのに、どのワークフローもそれを呼んでいない
# （2026-08-09 時点で実測）。「検証器はあるが誰も実行しない」ので、壊れたまま出荷できてしまう。
#
# さらに **`Samples~` 配下のカタログは C# の検証器では構造的に永久に検査できない**。
# Unity は `~` で終わるフォルダを import しないため、その中の manifest とテーブルは
# `AssetDatabase` に載らず scope として登録されない。しかし `Samples~` は `.tgz` に入り
# **そのまま購入者の手元へ届く**（検査 14・16 と同じ前提）。届いた先で Package Manager から
# import されれば、欠けたキーはそのまま購入者の Unity で文言の欠落として出る。
# だから Python 側では `Samples~` 配下も等しく対象にする（実例: UEL の
# `Samples~/LocalizedEditorWindow/Editor/Localization/`）。
#
# 判定規則の**正本は C# 側**（`EditorL10nValidator.ValidateScope` / `ExtractPlaceholders` /
# `FindMissingPlaceholderNumbers`）で、ここはその写しである。片方だけ直すと Editor 上の判定と
# CI の判定が割れて「どちらが正しいのか分からない」状態になるので、規則を変えるときは
# **必ず両方を同時に**直すこと。
#
# 指摘の出し方: 1 件 1 findings にすると 19 ロケール × 数百キーで出力が読めなくなるため、
# (locale, 事象) 単位へ畳んで **件数を必ず本文に明示**し、代表例を数件添える。
# 件数を隠さないので「握り潰し」にはならず、詳細は名指しされたテーブルを開けば分かる。
#
# C# に無い検査を 2 つだけ足している（孤児テーブル・git 未追跡テーブル）。どちらも
# 「AssetDatabase を起点に manifest からしか辿らない C# 側では構造的に見えないが、
# ファイルシステムと git 追跡状態を直接見られる Python 側なら塞げる」位置にあるものに限る。
# ---------------------------------------------------------------------------

# C# の `EditorL10nCatalog.IsManifestPath` と同じ判定にする。C# は「`.json` で終わり、
# **ファイル名に `l10n-manifest` を含む**」で拾うので、`foo.l10n-manifest.json` だけでなく
# `l10n-manifest-extra.json` のような名前も Unity 側では manifest として読まれる。
# Python 側だけ接尾辞一致にしていると、Unity が読むのにここは見ない manifest が生まれる。
L10N_MANIFEST_NAME_MARKER = "l10n-manifest"

# C# の `EditorL10nValidator.PlaceholderRegex` と同じ意味。`{{0}}` のエスケープは拾わず、
# `{0:N2}` のような書式指定付きは番号 0 として拾う。
L10N_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{(\d+)(?:[^}]*)\}(?!\})")

# 欠番列挙を打ち切る上限。C# は最大番号までそのままループするが、Python 側で同じことをすると
# 壊れた入力（`{2000000000}` など）1 つで検査が止まる。実データの placeholder は 0〜数個なので、
# この上限を超える時点で placeholder 集合が既定ロケールと一致するはずがなく、
# 同じキーが必ず PlaceholderMismatch（error）で拾われる。取りこぼしにはならない。
L10N_PLACEHOLDER_NUMBER_LIMIT = 1000


def is_l10n_manifest_path(path: str) -> bool:
    """C# の `EditorL10nCatalog.IsManifestPath` と同じ判定（`.json` 終わり＋ファイル名に marker を含む）。"""
    if not path or not path.lower().endswith(".json"):
        return False
    return L10N_MANIFEST_NAME_MARKER in path.rsplit("/", 1)[-1]


def normalize_locale_tag(locale: str) -> str:
    """C# の `EditorL10n.NormalizeLocaleTag` と同じ正規化（`pt_br` → `pt-BR`、`ZH-hans` → `zh-Hans`）。"""
    if not locale or not locale.strip():
        return ""
    parts = locale.strip().replace("_", "-").split("-")
    for index, part in enumerate(parts):
        if not part:
            continue
        if index == 0:
            parts[index] = part.lower()
        elif len(part) == 4 and part.isalpha():
            parts[index] = part[0].upper() + part[1:].lower()
        elif len(part) == 2 and part.isalpha():
            parts[index] = part.upper()
        else:
            parts[index] = part.lower()
    # C# は結合時に空要素を捨てる（`string.Join("-", parts.Where(part => !IsNullOrEmpty(part)))`）。
    # 落とさないと `ja--JP` が `ja--JP`、`-ja` が `-ja` のまま残り、Unity 側が導く `ja-JP` / `ja` と
    # 食い違って「Python では重複に見えないが Unity では同じタグ」という取りこぼしが生まれる。
    return "-".join(part for part in parts if part)


def _l10n_placeholder_number(placeholder: str) -> int:
    """C# の `int.TryParse` と同じ判定（32bit に収まらない番号は -1＝解釈不能）。"""
    try:
        number = int(placeholder)
    except ValueError:
        return -1
    return number if -2147483648 <= number <= 2147483647 else -1


def extract_l10n_placeholders(text: str) -> set[str]:
    """C# の `ExtractPlaceholders` と同じ規則で placeholder の番号文字列を集める。"""
    if not text:
        return set()
    return {match.group(1) for match in L10N_PLACEHOLDER_RE.finditer(text)}


def find_missing_placeholder_numbers(placeholders: set[str]) -> list[int]:
    """C# の `FindMissingPlaceholderNumbers` と同じ規則で欠番（0 から最大番号まで）を返す。"""
    numbers = sorted({n for n in (_l10n_placeholder_number(p) for p in placeholders) if n >= 0})
    if not numbers or numbers[-1] > L10N_PLACEHOLDER_NUMBER_LIMIT:
        return []
    present = set(numbers)
    return [number for number in range(numbers[-1] + 1) if number not in present]


def _format_l10n_placeholders(placeholders: set[str]) -> str:
    """C# の `FormatPlaceholders` と同じ並び（番号順・解釈不能なものは末尾）で一覧を作る。"""

    def sort_key(placeholder: str) -> tuple[int, str]:
        number = _l10n_placeholder_number(placeholder)
        return (number if number >= 0 else 2147483647, placeholder)

    return ",".join(sorted(placeholders, key=sort_key)) if placeholders else "（なし）"


def _l10n_sample(items: list[str], limit: int = 6) -> str:
    """指摘の代表例を列挙する（総件数は必ずメッセージ本文で別に示す）。"""
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" ほか {len(items) - limit} 件"


def load_l10n_table(path: Path) -> dict[str, str] | None:
    """翻訳テーブル JSON を key→value の辞書として読む。JSON として読めなければ None。

    同じ key が重複していれば後勝ち（C# の `entries[key] = value` と同じ）。
    """
    data = load_json(path)
    if data is None:
        return None
    entries: dict[str, str] = {}
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue  # C# も key が空の要素は読み飛ばす
        value = entry.get("value")
        entries[key] = value if isinstance(value, str) else ""
    return entries


def check_18_l10n_catalogs(ctx: RepoContext) -> None:
    manifests = sorted(
        rel
        for rel in ctx.tracked
        if rel.startswith("Packages/") and is_l10n_manifest_path(rel)
    )
    if not manifests:
        return  # 翻訳カタログを持たないリポジトリ（サイト・技術同人誌など）では何も言わない
    # 孤児テーブルの判定は**リポジトリ全体の登録済みパスを集めてから 1 回だけ**行う。
    # manifest ごとに独立して判定すると、1 つの `Locales/` フォルダを 2 つの manifest が
    # 分け合う構成（scope を分けた本体と拡張など）で、互いのテーブルを孤児として偽 warn する。
    registered_rels: set[str] = set()
    # scope → 先にその scope を宣言した manifest。C# は 2 つめ以降の manifest を**丸ごと**捨てる。
    seen_scopes: dict[str, str] = {}
    for manifest_rel in manifests:
        registered_rels |= _check_l10n_manifest(ctx, manifest_rel, seen_scopes)
    _check_l10n_orphan_tables(ctx, registered_rels)


def _check_l10n_manifest(ctx: RepoContext, manifest_rel: str, seen_scopes: dict[str, str]) -> set[str]:
    """1 つの manifest を検証し、**登録済み tablePath の集合**を返す（孤児判定は呼び出し元が行う）。"""
    manifest_path = ctx.root / manifest_rel
    if ctx.is_waived("18", manifest_rel):
        return set()

    document = load_json(manifest_path)
    if document is None:
        ctx.add(
            "18",
            ERROR,
            "翻訳カタログの manifest が **この検査では** JSON として読めません（BOM 付き・"
            "構文エラーなど）。Unity 側が同じように失敗するとは限らない（`TextAsset.text` は BOM を"
            "落とすため C# 側は読める公算があり、そこは未検証）が、読めない JSON をそのまま出荷するのは"
            "正しくないので error のままにしている。まず素の UTF-8（BOM 無し）の正しい JSON へ直すこと",
            manifest_rel,
        )
        return set()

    scope = str(document.get("scope") or "").strip()
    if not scope:
        ctx.add(
            "18",
            ERROR,
            "翻訳カタログの manifest に scope がありません。Unity 側は scope の無い manifest を"
            "黙って読み飛ばすため、テーブルを何枚置いても 1 つも読み込まれません",
            manifest_rel,
        )
        return set()

    # 同じ scope を 2 つの manifest が名乗ると、C# は**後から読んだ manifest を丸ごと無視する**
    # （`EditorL10nCatalog.TryAddManifest` の重複判定）。無視された側のテーブルは表示にも検証にも
    # 現れないので、そちらの不備は永久に見えない。重複タグ（下）と同じ理由で error にする。
    if scope in seen_scopes:
        ctx.add(
            "18",
            ERROR,
            f"{scope}: この scope は `{seen_scopes[scope]}` が既に宣言しています。Unity 側は"
            f"先に読んだ manifest だけを採用し、後の manifest を**丸ごと**無視するため、"
            f"こちらのテーブルは表示にも検証にも現れません。scope を分けるか manifest を"
            f"統合してください",
            manifest_rel,
        )
        return set()
    seen_scopes[scope] = manifest_rel

    default_locale = normalize_locale_tag(str(document.get("defaultLocale") or ""))
    fixed_terms = {item for item in (document.get("fixedTerms") or []) if isinstance(item, str)}

    # --- テーブルの読み込み（tablePath は manifest のあるディレクトリからの相対） -------
    tables: dict[str, dict[str, str]] = {}
    table_rels: dict[str, str] = {}
    # manifest に登録済みの tablePath（読めたかどうかに関わらず記録する）。孤児テーブルの検出に使う。
    registered_rels: set[str] = set()
    # 正規化後のロケールタグ → その tablePath。重複検出に使う。
    seen_tags: dict[str, str] = {}
    for locale in document.get("locales") or []:
        if not isinstance(locale, dict):
            continue
        tag = normalize_locale_tag(str(locale.get("tag") or ""))
        table_path_value = str(locale.get("tablePath") or "").strip()
        if not tag or not table_path_value:
            continue  # C# も tag / tablePath が空の要素は読み飛ばす
        table_path = manifest_path.parent / table_path_value
        table_rel = os.path.relpath(table_path, ctx.root).replace(os.sep, "/")
        # 重複で捨てる側も「manifest に登録済み」ではあるので、孤児テーブルとして二重に
        # 鳴らさないよう、重複判定より先に登録済みとして記録する。
        registered_rels.add(table_rel)
        # 正規化後に同じタグへ潰れる項目が 2 つあると、C# の `AddLocale` は**先に読んだ方だけ**を
        # 採用して 2 つめを黙って捨てる。捨てられたテーブルは誰にも表示されず、検証も受けない。
        # Python 側で後勝ちの上書きにすると「先に読んだテーブルの不備が消える」ため、
        # どちらの不備も隠れないよう重複そのものを error にする。
        if tag in seen_tags:
            ctx.add(
                "18",
                ERROR,
                f"{scope}/{tag}: manifest に同じロケールタグの locales 項目が 2 つあります"
                f"（tablePath: `{seen_tags[tag]}` と `{table_path_value}`）。タグは正規化してから"
                f"比較されるため、表記が違っても同じタグへ潰れます。Unity 側は先に読んだ方だけを"
                f"採用し 2 つめを黙って捨てるので、捨てられたテーブルは表示にも検証にも現れません。"
                f"どちらを残すか決めて片方を削除してください",
                manifest_rel,
            )
            continue
        seen_tags[tag] = table_path_value
        if ctx.is_waived("18", table_rel):
            continue
        if not table_path.is_file():
            ctx.add(
                "18",
                ERROR,
                f"{scope}/{tag}: manifest の tablePath が指す翻訳テーブルがありません"
                f"（tablePath: {table_path_value}）。Unity 側はテーブルを空として読み込むため、"
                f"そのロケールを選んだ利用者には**全キーが既定ロケールのまま**表示されます",
                manifest_rel,
            )
            continue
        # git 未追跡のテーブルは内容が完全でも配布物（`.tgz` / `.unitypackage`）に入らない。
        # 手元の Unity では正しく見えるので、出荷して初めて壊れていることが分かる型の不備になる。
        if table_rel not in ctx.tracked:
            ctx.add(
                "18",
                WARN,
                f"{scope}/{tag}: 翻訳テーブルが git 未追跡です。手元の Unity では読めるため気付き"
                f"にくいですが、配布物に入らないため購入者の環境ではそのロケールを選んでも"
                f"**全キーが既定ロケールのまま**表示されます。`git add` してください",
                table_rel,
            )
        entries = load_l10n_table(table_path)
        if entries is None:
            ctx.add(
                "18",
                ERROR,
                f"{scope}/{tag}: 翻訳テーブルが **この検査では** JSON として読めません（BOM 付き・"
                f"構文エラーなど）。Unity 側が同じように失敗するとは限らない（`TextAsset.text` は BOM を"
                f"落とすため C# 側は読める公算があり、そこは未検証）が、読めない JSON をそのまま出荷"
                f"するのは正しくないので error のままにしている。まず素の UTF-8（BOM 無し）の"
                f"正しい JSON へ直すこと",
                table_rel,
            )
            continue
        # テーブル自身が名乗る `locale` と manifest の `tag` の食い違い（C# の `LoadEntries` が
        # 同じ条件で警告を出す）。読み込みは manifest の tag で行われるので表示は壊れないが、
        # 「別ロケールのテーブルを取り違えて登録した」ときの唯一の手掛かりがこれになる。
        # `load_l10n_table` は entries しか返さないので、この 1 フィールドだけ読み直す。
        declared_tag = normalize_locale_tag(str((load_json(table_path) or {}).get("locale") or ""))
        if declared_tag and declared_tag != tag:
            ctx.add(
                "18",
                WARN,
                f"{scope}/{tag}: テーブルが名乗るロケール `{declared_tag}` が manifest の tag と"
                f"違います。Unity 側は manifest の tag で登録するため表示は壊れませんが、"
                f"別ロケールのテーブルを取り違えて登録している疑いがあります",
                table_rel,
            )
        tables[tag] = entries
        table_rels[tag] = table_rel

    # --- 既定ロケールの成立（C# の ValidateScope 冒頭と同じ順序・同じ深刻度） -----------
    if not default_locale:
        ctx.add(
            "18",
            ERROR,
            f"{scope}: manifest の defaultLocale が空です。既定ロケールは全ロケールの照合基準"
            f"であり、無いとカタログの正しさを誰も判定できません",
            manifest_rel,
        )
    if default_locale not in tables:
        ctx.add(
            "18",
            ERROR,
            f"{scope}: 既定ロケール `{default_locale or '(未設定)'}` の翻訳テーブルが manifest に"
            f"ありません。基盤が無い環境では既定ロケールのテーブルだけを直接読むため、"
            f"これが欠けると翻訳キーがそのまま UI に出ます",
            manifest_rel,
        )
        return registered_rels

    # --- 各ロケールの照合（C# の ValidateScope 本体と同じ規則） -------------------------
    default_table = tables[default_locale]
    default_keys = set(default_table)
    for tag in sorted(tables):
        table = tables[tag]
        table_rel = table_rels[tag]
        keys = set(table)

        missing_keys = sorted(default_keys - keys)
        if missing_keys:
            ctx.add(
                "18",
                ERROR,
                f"{scope}/{tag}: 既定ロケール `{default_locale}` にあるキーが {len(missing_keys)} 件"
                f"ありません（{_l10n_sample(missing_keys)}）。欠けたキーは UI にキー名がそのまま"
                f"出るか既定ロケールの文言のまま残ります",
                table_rel,
            )

        extra_keys = sorted(keys - default_keys)
        if extra_keys:
            ctx.add(
                "18",
                WARN,
                f"{scope}/{tag}: 既定ロケール `{default_locale}` に無いキーが {len(extra_keys)} 件"
                f"あります（{_l10n_sample(extra_keys)}）。誰も引かない死んだ訳文か、既定ロケール側の"
                f"キー削除・改名の取りこぼしのどちらかです",
                table_rel,
            )

        gaps: list[str] = []
        for key in sorted(keys):
            placeholders = extract_l10n_placeholders(table[key])
            missing_numbers = find_missing_placeholder_numbers(placeholders)
            if missing_numbers:
                gaps.append(
                    f"{key}（present={_format_l10n_placeholders(placeholders)} / "
                    f"missing={','.join(str(number) for number in missing_numbers)}）"
                )
        if gaps:
            ctx.add(
                "18",
                WARN,
                f"{scope}/{tag}: placeholder の番号に欠番があるキーが {len(gaps)} 件あります"
                f"（{_l10n_sample(gaps, 3)}）。`{{1}}` だけで `{{0}}` が無い形は、書式引数の"
                f"渡し忘れか番号の付け間違いを示します",
                table_rel,
            )

        mismatches: list[str] = []
        same_as_default: list[str] = []
        for key in sorted(default_keys & keys):
            expected = extract_l10n_placeholders(default_table[key])
            actual = extract_l10n_placeholders(table[key])
            if expected != actual:
                mismatches.append(
                    f"{key}（既定={_format_l10n_placeholders(expected)} / "
                    f"{tag}={_format_l10n_placeholders(actual)}）"
                )
            # 固定語（manifest の fixedTerms。型名・ファイル名など全ロケールで同値が正当）は除く
            if tag != default_locale and table[key] == default_table[key] and key not in fixed_terms:
                same_as_default.append(key)

        if mismatches:
            ctx.add(
                "18",
                ERROR,
                f"{scope}/{tag}: placeholder の集合が既定ロケール `{default_locale}` と違うキーが "
                f"{len(mismatches)} 件あります（{_l10n_sample(mismatches, 3)}）。"
                f"呼び出し側は既定ロケールの番号を前提に引数を渡すため、"
                f"**実行時に書式例外か文字化けした文言**になります",
                table_rel,
            )

        if same_as_default:
            ctx.add(
                "18",
                WARN,
                f"{scope}/{tag}: 値が既定ロケール `{default_locale}` と同一のキーが "
                f"{len(same_as_default)} 件あります（{_l10n_sample(same_as_default)}）。"
                f"未翻訳の取りこぼしなら訳し、全ロケールで同値が正当な固定語なら manifest の "
                f"fixedTerms へ登録してください（登録すればこの警告は出ません）",
                table_rel,
            )

    return registered_rels


def _check_l10n_orphan_tables(ctx: RepoContext, registered_rels: set[str]) -> None:
    """manifest に登録されていない翻訳テーブル（孤児）を warn で鳴らす。

    C# 側にはこの検査が無い。C# は AssetDatabase から manifest を拾い、そこに書かれた
    tablePath しか辿らないため、「`Locales/` に JSON を足したが manifest への追記を忘れた」形は
    構造的に見えない（誰も読まないので、誰も欠落に気付かない）。Python 側は git 追跡パスを
    直接列挙できるので、この位置でだけ塞げる。

    走査は「登録済みテーブルが実在するフォルダの直下」に限る。フォルダ名（`Locales`）に依存せず、
    かつ無関係な JSON（asmdef 隣の設定ファイル等）を巻き込まないための範囲設定である。
    """
    for directory in sorted({rel.rsplit("/", 1)[0] for rel in registered_rels if "/" in rel}):
        prefix = f"{directory}/"
        for candidate in sorted(ctx.tracked):
            if not candidate.startswith(prefix) or "/" in candidate[len(prefix) :]:
                continue  # 直下のみ（サブフォルダは別の manifest の管轄でありうる）
            if not candidate.lower().endswith(".json") or candidate in registered_rels:
                continue
            if is_l10n_manifest_path(candidate):
                continue  # manifest 自身が同じフォルダに置かれている場合
            if is_unity_ignored_name(candidate.rsplit("/", 1)[-1]):
                continue  # `.schema.json` など Unity が import しない名前は翻訳テーブルになりえない
            if ctx.is_waived("18", candidate):
                continue
            ctx.add(
                "18",
                WARN,
                "翻訳テーブルらしき JSON が、同じフォルダの翻訳を管轄する manifest のどれにも"
                "登録されていません。"
                f"登録が無いロケールは Unity 側の言語切替候補にすら現れないため、訳した内容が"
                f"誰にも届きません。manifest へ `tag` / `tablePath` を追記するか、"
                f"使わないなら削除してください",
                candidate,
            )


# ---------------------------------------------------------------------------
# 検査 19: 翻訳文書の命名（`<正本の basename>.<locale>.md`）
#
# 同梱文書の翻訳版の命名が 2 通りに割れていた（2026-08-09 実測）:
#   - TAE: `README.ja.md` / `Documentation~/index.ja.md`（**正しい形**）
#   - UMPD: `README_JP.md` / `Documentation~/*_JP.md`（**違反**。2026-08-09 に `.ja.md` へ改名済み）
# root README の翻訳 `docs/readme/README.<locale>.md` は既に前者の形で規定済み（§2.2）なので、
# 規約自体は存在しており、従っていない置き場所が残っている状態になっている。
#
# 命名を `<正本の basename>.<locale>.md` に揃える理由:
#   - locale の表記を翻訳カタログ（`Locales/<tag>.json`）と一致させると、「どの言語の文書か」を
#     人も機械も 1 つの規則で読める。`_JP` は国コードであって言語タグではなく、
#     `zh-Hans` / `pt-BR` のような下位区分を持つ言語をそもそも表現できない。
#   - 正本と翻訳が名前順で隣り合い、正本（無印）がどれかが一目で決まる。
#
# **走査範囲は git 追跡された `.md` 全体**。GOLD_STANDARD §2.2 はこの規約について置き場所を
# 問わないと明記し、リポジトリ直下の `docs/readme/README.zh-Hans.md` を例に挙げている。
# `Packages/` 配下に限ると、リポジトリ直下の `docs/readme/` や `docs/` を素通りする。
#   （実測 2026-08-09: `docs/license-draft-ja.md` がこの範囲でだけ拾えた）
# `Samples~` / `Documentation~` 配下も当然含む（`.tgz` に入って購入者の手元へ届く）。
#
# 判定は**汎用の接尾辞ヒューリスティックではなく、既知のロケールタグ集合に錨を打つ**。
# 「2〜3 文字の言語らしき接尾辞」方式は、`README.es-ES` のような**正しい名前**を違反と誤判定し、
# しかも `README.es.es.md` という壊れた改名先を提案していた（`es-ES` / `pt-PT` は実運用の
# 19 ロケールに含まれ、5 リポジトリの `docs/readme/` に計 10 枚実在する）。同時に
# `README.jp` / `README_zh-Hans` / `Guide_pt-BR` のような**本物の違反**は取りこぼしていた。
# ---------------------------------------------------------------------------

# 実運用の 19 ロケール。**正本は各パッケージの `Packages/*/Editor/**/*.l10n-manifest.json` の
# `locales[].tag`** で、ここはその写しである（検査 19 は翻訳カタログを持たないリポジトリ
# （サイト等）でも走るため、manifest から動的に読まず定数として持つ）。
# ロケールを増減したときは manifest と**この定数の両方**を更新すること。
CANONICAL_LOCALE_TAGS = (
    "ja", "en", "zh-Hans", "zh-Hant", "ko", "fr", "de", "it",
    "es-ES", "es-419", "pt-BR", "pt-PT", "ru", "pl", "tr", "th", "vi", "uk", "id",
)
CANONICAL_LOCALE_TAG_SET = frozenset(CANONICAL_LOCALE_TAGS)
CANONICAL_LOCALE_TAG_BY_LOWER = {tag.lower(): tag for tag in CANONICAL_LOCALE_TAGS}

# 誤った書き方 → あるべきロケールタグ。canonical 集合との照合は**大小を無視して**行うため、
# `JA` / `EN` / `ZH-hans` のような「正しいタグの大小違い・区切り違い」はここに載せる必要が無い
# （載せるのは canonical 集合に無い綴りだけ）。値は必ず canonical 集合の要素にすること
# （`sp` → `es` のように canonical に無いタグを提案すると、直した先がまた違反になる）。
#
# **`UK` は入れない**: 国コードとしては英国を指すが、ロケールタグとしてはウクライナ語（`uk`）である。
# 別名として `en` へ読み替えると、本物のウクライナ語文書を英語文書へ改名させてしまう。
# 別名表に載せない結果、`README-UK.md` は canonical 集合との大小無視照合で `uk`（ウクライナ語）と
# 解釈される。規約が「国コードではなく言語タグを使う」と定めている以上、この解釈を正とする
# （英国向け英語のつもりなら、そもそも `README.en.md` が正しい名前である）。
LOCALE_TAG_ALIASES = {
    # 1 語（国コード由来・3 文字コード）
    "jp": "ja", "jpn": "ja",
    "us": "en", "gb": "en", "eng": "en",
    "cn": "zh-Hans", "sc": "zh-Hans", "chs": "zh-Hans",
    # スクリプト未指定の `zh` は実運用の既定である簡体字へ寄せる（繁体字なら提案を `zh-Hant` へ読み替える）
    "zh": "zh-Hans",
    "tw": "zh-Hant", "hk": "zh-Hant", "tc": "zh-Hant", "cht": "zh-Hant",
    "kr": "ko", "kor": "ko",
    "ger": "de",
    "br": "pt-BR", "bra": "pt-BR",
    "vn": "vi",
    "ua": "uk",
    # **地域を持つ言語の「地域なし」表記**。`es` / `pt` は canonical 集合に無い（実運用は
    # `es-ES` / `es-419` / `pt-BR` / `pt-PT` の 4 つ）ので、載せないと `README_ES.md` /
    # `README_PT.md` / `README_SP.md` が「解決できない＝翻訳文書ではない」と判定されて素通りする。
    # 提案先は既定の地域（スペイン本国・ポルトガル本国）へ寄せ、意図が中南米・ブラジルなら
    # 人が `es-419` / `pt-BR` へ読み替える。
    "es": "es-ES", "sp": "es-ES", "spa": "es-ES", "mx": "es-419", "es-mx": "es-419",
    "pt": "pt-PT", "por": "pt-PT",
    # 2 語（言語＋地域）。まとめて読み替えないと 1 語だけが接尾辞と判定され、
    # `setup-en-US` に対して base に `en` が残った `setup-en.en.md` という壊れた改名先を出す。
    "ja-jp": "ja",
    "en-us": "en", "en-gb": "en",
    "zh-cn": "zh-Hans", "zh-sg": "zh-Hans",
    "zh-tw": "zh-Hant", "zh-hk": "zh-Hant",
    "ko-kr": "ko",
    "vi-vn": "vi",
    "uk-ua": "uk",
}

# ファイル名の区切りとして同格に扱う文字（`README.ja` / `README_JP` / `README-jp` を同じ形とみなす）。
TRANSLATION_DOC_SEPARATOR_RE = re.compile(r"([._-])")

# 生成物・依存物・第三者製アセットのディレクトリ名。通常は git 追跡されないが、追跡されている場合に
# 走査へ紛れ込まないよう名前で落とす。**第三者製を落とすのは、こちらが改名すると配布元の更新を
# 取り込むたびに衝突するため**（実例: Lame の `Assets/ThirdParty/lilToon/README_JP.md`。
# lilToon は第三者製で、命名規約はこちらの規約の適用範囲外）。
TRANSLATION_DOC_SKIP_DIRS = frozenset({
    "node_modules", "Library", "Temp", "obj",
    "ThirdParty", "Third Party", "third_party", "vendor", "Vendor",
})


def resolve_locale_tag(candidate: str) -> str | None:
    """区切りを `-` に揃えた候補文字列を、大小を無視して canonical なロケールタグへ解決する。"""
    lowered = candidate.lower()
    if lowered in CANONICAL_LOCALE_TAG_BY_LOWER:
        return CANONICAL_LOCALE_TAG_BY_LOWER[lowered]
    return LOCALE_TAG_ALIASES.get(lowered)


def suggest_translation_doc_name(stem: str) -> tuple[str, str] | None:
    """`.md` を除いた stem から (あるべき locale タグ, あるべき basename) を返す。

    次のどちらかなら None（＝この検査は何も言わない）:
      - 既に正しい形（stem の最後の `.` 以降が canonical なロケールタグと**完全一致**）
      - 翻訳文書らしい痕跡が無い（`Third Party Notices` / `getting-started` など）
    """
    # 1) 正しい形をまず確定させる。大小まで一致していることを求めるので、`README.zh-hans` のような
    #    表記ゆれはここでは通らず、2) で違反として拾われる。
    dot_index = stem.rfind(".")
    if dot_index >= 0 and stem[dot_index + 1 :] in CANONICAL_LOCALE_TAG_SET:
        return None

    # 1.5) **stem 全体がロケールタグそのもの**の形（`ja.md` / `es-ES.md` / `zh-Hans.md`）は
    #      翻訳文書ではなく「その言語のためのファイル」なので何も言わない。下のループの
    #      `len(tokens) <= count` ガードは 1 セグメントのタグ（`ja`）しか落とせず、
    #      `es-ES` のような 2 セグメントのタグは base に `es` が残って壊れた提案になる。
    if resolve_locale_tag(stem.replace("_", "-").replace(".", "-")) is not None:
        return None

    # 2) 末尾から 1 セグメント / 2 セグメントを取り、区切りを `-` に揃えて照合する。
    #    2 セグメントを先に見るのは、`Guide_pt-BR` の `BR` だけを拾って base に `pt` を残す
    #    （＝壊れた改名先を出す）ことを避けるため。
    pieces = TRANSLATION_DOC_SEPARATOR_RE.split(stem)
    tokens = pieces[0::2]
    for count in (2, 1):
        if len(tokens) <= count:
            continue  # base が空になる形（stem 全体がロケールタグ）は翻訳文書とみなさない
        tail = tokens[-count:]
        if any(not token for token in tail):
            continue  # `README..ja` のように区切りが連続している形
        tag = resolve_locale_tag("-".join(tail))
        if tag is None:
            continue
        # 区切りはすべて 1 文字なので、末尾の長さは「先行する区切り 1 + 語の長さ + 語間の区切り」。
        tail_length = 1 + (count - 1) + sum(len(token) for token in tail)
        base = stem[:-tail_length]
        if not base:
            continue
        return tag, f"{base}.{tag}.md"
    return None


def check_19_translation_doc_naming(ctx: RepoContext) -> None:
    for tracked in sorted(ctx.tracked):
        # 拡張子は大小を無視する（`README_JP.MD` を素通りさせない）。
        if not tracked.lower().endswith(".md"):
            continue
        segments = tracked.split("/")
        if any(segment in TRANSLATION_DOC_SKIP_DIRS for segment in segments[:-1]):
            continue
        if ctx.is_waived("19", tracked):
            continue
        suggestion = suggest_translation_doc_name(segments[-1][: -len(".md")])
        if suggestion is None:
            continue
        locale, expected = suggestion
        base = expected[: -len(f".{locale}.md")]
        directory = "/".join(segments[:-1])
        prefix = f"{directory}/" if directory else ""
        sibling = f"{prefix}{base}.md"
        # **同じディレクトリに「これはその翻訳版だ」と言える兄弟が実在すること**を error の条件にする。
        # `id`（インドネシア語）・`it`（イタリア語）・`de` などは英単語やその一部としても現れるため
        # （`sample-id.md` / `do-it.md`）、タグ集合との一致だけでは翻訳文書だと断定できない。
        #
        # 兄弟として認めるのは 2 種類:
        #   (a) 無印の正本 `<base>.md`
        #   (b) **正しく名付けられた別言語の翻訳** `<base>.<canonical タグ>.md`
        # (b) が要るのは、翻訳を専用フォルダへ集める配置では正本がそこに無いため。実例は
        # GOLD_STANDARD §2.2 が規約の例に挙げている `docs/readme/`（正本は root の `README.md`で、
        # このフォルダには翻訳 18 枚しか無い）。(a) だけを条件にすると、規約が名指ししている
        # まさにその場所で違反が warn 止まりになり、CI も pre-push も通ってしまう。
        siblings = [sibling] if sibling in ctx.tracked else []
        siblings += [
            candidate
            for tag in CANONICAL_LOCALE_TAGS
            if (candidate := f"{prefix}{base}.{tag}.md") in ctx.tracked
        ]
        if siblings:
            evidence = (
                f"同じフォルダに正本 `{base}.md` があるため"
                if sibling in ctx.tracked
                else f"同じフォルダに正しく名付けられた翻訳 `{siblings[0].split('/')[-1]}` があるため"
            )
            ctx.add(
                "19",
                ERROR,
                f"翻訳文書の名前がロケールタグの表記になっていません（`{expected}` にしてください）。"
                f"{evidence}、これはその翻訳版と判断しました。"
                f"翻訳文書は `<正本の basename>.<locale>.md` とし、ロケールタグは翻訳カタログ"
                f"（`Locales/{locale}.json`）と同じ表記を使う規約です（GOLD_STANDARD §2.2）。"
                f"`_JP` のような国コードは言語タグではなく、`zh-Hans` / `pt-BR` のような下位区分を"
                f"持つ言語を表現できません。改名は `git mv` で行い、対になる `.md.meta` があれば"
                f"同時に改名して GUID を保ってください",
                tracked,
            )
        else:
            ctx.add(
                "19",
                WARN,
                f"翻訳文書らしい名前ですが、同じフォルダに正本 `{base}.md` も、正しく名付けられた"
                f"別言語の翻訳 `{base}.<locale>.md` も無いため、翻訳版だと断定できません"
                f"（翻訳版なら `{expected}` へ改名してください）。"
                f"`it`（イタリア語）や `id`（インドネシア語）は英単語としても現れるため、"
                f"兄弟ファイルが実在する場合だけ error にしています（GOLD_STANDARD §2.2）",
                tracked,
            )


# ---------------------------------------------------------------------------
# 検査 20: 宣言したスキルが版管理下にあるか（2026-08-10 制定）
#
# 検査 10 は「宣言したスキルがどこかのスコープに実在するか」しか見ない。索引には
# `~/.claude/skills` / `~/.agents/skills`（ホームスコープ）が含まれるので、**版管理外の
# 手元フォルダに置いただけのスキルでも緑になる**。
#
# 実際にそれで事故が起きた（2026-08-10 実測）。パイプラインの中核である
# `release-unity-package`（68KB）を含む 6 件が、履歴もバックアップもレビューも無い
# `~/.claude/skills/` の実体フォルダとして 1 か所だけに存在していた。8 リポジトリが
# `skillRefs` で宣言していたが、検査 10 は全部緑を出していた。マシンが飛べば消える状態である。
#
# この検査は**ホームスコープを索引から外して**同じ宣言を解決し直す。版管理下の正本へ
# 解決できないものを error にする。第三者製のスキル（自分たちが版管理する対象ではないもの）は
# `pipeline/repo.json` の `externalSkills` で名前を挙げて除外する。
# ---------------------------------------------------------------------------


def _versioned_skill_index(ctx: RepoContext) -> tuple[set[str], bool]:
    """**版管理下の正本だけ**を走査してスキル名の集合を返す（ホームスコープは見ない）。"""
    names: set[str] = set()

    def add(directory: Path) -> None:
        names.update(_index_skills(directory, None).keys())

    # 自リポジトリ
    add(ctx.root / "skills")
    if (ctx.root / "Packages").is_dir():
        for skills_dir in sorted((ctx.root / "Packages").glob("*/skills")):
            add(skills_dir)

    site_root = resolve_site_repo(ctx)
    if site_root is None:
        return names, False
    add(site_root / "skills")
    for repo_path in resolve_registry_repos(site_root):
        add(repo_path / "skills")
        for skills_dir in sorted(repo_path.glob("Packages/*/skills")):
            add(skills_dir)
    return names, True


def check_20_skills_under_version_control(ctx: RepoContext) -> None:
    declared = [str(item) for item in ctx.config.get("skillRefs") or []]
    if not declared:
        return
    external = {str(item) for item in ctx.config.get("externalSkills") or []}
    versioned, registry_resolved = _versioned_skill_index(ctx)

    for skill in sorted(set(declared)):
        if skill in external or skill in versioned or ctx.is_waived("20", skill):
            continue
        severity = ERROR if registry_resolved else WARN
        note = "" if registry_resolved else "（MySite を解決できないため未確認）"
        ctx.add(
            "20",
            severity,
            f"宣言されたスキル `{skill}` が版管理下の正本に見つかりません{note}。"
            f"手元の `~/.claude/skills` にあるだけの状態は、履歴もバックアップもレビューも無く、"
            f"マシンが飛べば消えます（2026-08-10 に実際に 6 件がこの状態でした）。"
            f"自分たちのスキルなら GOLD_STANDARD §2.6 の類型に従って正本を置いてください"
            f"（開発・運用スキルは `MySite/skills/`、購入者へ同梱するスキルは `Packages/<pkg>/skills/`）。"
            f"第三者製で版管理の対象外なら `pipeline/repo.json` の `externalSkills` へ名前を挙げてください",
        )


# ---------------------------------------------------------------------------
# 検査 21: コードが宣言・参照する翻訳キーが、既定ロケールのカタログに載っていること
#
# 検査 18 が見るのは**ロケール同士の一致**だけである。全ロケールが等しく欠いているキーは、
# 集合が揃っているので構造上まったく鳴らない。実際にそれで抜けた（2026-08-14 / UMPD）:
# `UmpdTextKey` へ 13 キーを足して 19 ロケールの JSON を 1 つも更新しないままリリース直前まで
# 進み、`Rendering (Mixed)` と出ていたグループ見出しが `group.header.mixed` に化けた。
# `Tr` はキーが未登録のとき**キー文字列をそのまま返し、書式引数を捨てる**ため、例外は出ず、
# 機能テストも緑のままになる。人の目でしか気付けない位置にあるので機械で塞ぐ。
#
# 同じ穴は EditMode テスト（`editor-localization-optional-integration` 同梱の
# `TextKeyCatalogCoverage.cs.txt`）でも塞げるが、**それは Unity を起動しないと走らない**。
# カタログを編集する作業の大半は Unity の外（エージェントによる翻訳追加・複数リポジトリ跨ぎ・
# 他プロジェクトで Editor がロックを持っている場合）で起きるので、同じ判定を Unity 非依存で
# 持つ。二重管理に見えるが、片方は「出荷コードの探索経路をそのまま使う」ことに価値があり、
# こちらは「Unity 無しで push 前に落とせる」ことに価値がある。判定規則が食い違うと困るのは
# **キーの網羅**という 1 点だけで、そこは両方とも「defaultLocale のテーブルに在るか」で同じ。
#
# 走査は git 追跡された `Packages/**/*.cs` のみ。未追跡のコードは配布物に入らないため、
# 追跡外の宣言を根拠に「在る／無い」を判定すると手元だけで通る検査になる。
# ---------------------------------------------------------------------------

# `internal const string Foo = "bar";` / `public const string ...`。UEL 同梱スキルの
# `check_tr_placeholder_parity.py` と同じ抽出規則。
L10N_CONST_RE = re.compile(r"const\s+string\s+(?P<name>\w+)\s*=\s*\"(?P<value>(?:[^\"\\]|\\.)*)\"\s*;")
# 翻訳キーを集約するクラス。`EpeTextKey` / `UmpdTextKey` / `TaeTextKey` のように接尾辞で名乗る。
L10N_TEXTKEY_CLASS_RE = re.compile(r"\bclass\s+(?P<name>\w*TextKey)\b")
# ファサードの `Tr` 宣言。`static string Tr(string key, params object[] args)` のような形。
L10N_TR_DECL_RE = re.compile(r"\bstatic\s+string\s+Tr\s*\((?P<params>[^)]*)\)")
L10N_CLASS_RE = re.compile(r"\b(?:static\s+|sealed\s+|partial\s+|abstract\s+)*class\s+(?P<name>\w+)")
# `Tr` の第 1 引数が翻訳キー定数の参照らしい形か。この形で解決できないのは「動的なキー」ではなく
# 走査範囲か命名の問題なので、警告ではなく error にする（黙って検査から抜けるのを防ぐ）。
L10N_KEY_CONST_REF_RE = re.compile(r"^\w*(?:TextKey|Keys?)\.\w+$")


@dataclass
class L10nScopeTable:
    """1 つの scope の既定ロケールテーブル（検査 21 と入口スクリプトが共有する最小の情報）。"""

    scope: str
    default_locale: str
    table_rel: str
    entries: dict[str, str]


@dataclass
class L10nFacade:
    """`Tr` を提供するファサード 1 つぶん。呼び出し規約（scope 引数の有無）と既定 scope を持つ。"""

    class_name: str
    key_index: int
    scope: str | None
    declared_in: str


def collect_l10n_default_tables(ctx: RepoContext) -> list[L10nScopeTable]:
    """各 scope の**既定ロケールのテーブルだけ**を読む軽量な読み込み。

    manifest とテーブルの不備（tablePath 欠落・重複タグ・未追跡・壊れた JSON）を判定するのは
    検査 18 の責務で、ここは判定を一切持たない。読めなかった scope は黙って落とす
    （落ちた理由は検査 18 が同じ実行の中で必ず報告する）。
    """
    tables: list[L10nScopeTable] = []
    for manifest_rel in sorted(rel for rel in ctx.tracked
                               if rel.startswith("Packages/") and is_l10n_manifest_path(rel)):
        manifest_path = ctx.root / manifest_rel
        document = load_json(manifest_path)
        if document is None:
            continue
        scope = str(document.get("scope") or "").strip()
        default_locale = normalize_locale_tag(str(document.get("defaultLocale") or ""))
        if not scope or not default_locale:
            continue
        for locale in document.get("locales") or []:
            if not isinstance(locale, dict):
                continue
            if normalize_locale_tag(str(locale.get("tag") or "")) != default_locale:
                continue
            table_path = manifest_path.parent / str(locale.get("tablePath") or "")
            entries = load_l10n_table(table_path) if table_path.is_file() else None
            if entries is None:
                break
            table_rel = os.path.relpath(table_path, ctx.root).replace(os.sep, "/")
            tables.append(L10nScopeTable(scope, default_locale, table_rel, entries))
            break
    return tables


def _enclosing_class(content: str, position: int) -> str | None:
    """`position` より前にある直近の `class X` 宣言の名前（ファサードの所属を決めるのに使う）。"""
    last = None
    for match in L10N_CLASS_RE.finditer(content, 0, position):
        last = match.group("name")
    return last


def _l10n_source_files(ctx: RepoContext) -> list[tuple[str, str]]:
    """git 追跡された `Packages/**/*.cs` を (相対パス, 本文) で返す（読めないものは飛ばす）。"""
    files: list[tuple[str, str]] = []
    for rel in sorted(rel for rel in ctx.tracked
                      if rel.startswith("Packages/") and rel.endswith(".cs")):
        try:
            files.append((rel, (ctx.root / rel).read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return files


def collect_l10n_facades(ctx: RepoContext, sources: list[tuple[str, str]] | None = None) -> list[L10nFacade]:
    """`Tr` を提供するクラスを宣言から見つけ、呼び出し規約と既定 scope を決める。

    設定ファイルへ書かせず宣言から読むのは、ファサード名を変えたときに設定だけが古くなって
    「検査しているつもりで 0 件」になるのを避けるため。第 1 引数が `scope` という名前なら
    scope 先頭型（基盤の `EditorL10n.Tr(scope, key, args)`）、そうでなければ key 先頭型
    （各製品の `XxxL10n.Tr(key, args)`）と解釈する。
    """
    facades: dict[str, L10nFacade] = {}
    for rel, content in (sources if sources is not None else _l10n_source_files(ctx)):
        for match in L10N_TR_DECL_RE.finditer(content):
            class_name = _enclosing_class(content, match.start())
            if not class_name or class_name in facades:
                continue
            params = [part.strip() for part in match.group("params").split(",") if part.strip()]
            first_name = params[0].split()[-1].lstrip("@") if params else ""
            key_index = 1 if first_name == "scope" else 0
            scope_match = re.search(
                r"const\s+string\s+Scope\s*=\s*\"(?P<value>[^\"]*)\"\s*;", content)
            facades[class_name] = L10nFacade(
                class_name=class_name,
                key_index=key_index,
                scope=scope_match.group("value") if scope_match else None,
                declared_in=rel,
            )
    return sorted(facades.values(), key=lambda facade: facade.class_name)


def _l10n_find_calls(content: str, needle: str) -> list[tuple[int, str]]:
    """`Class.Tr(` の各出現について (行番号, 引数文字列) を返す（括弧・文字列リテラルを考慮）。"""
    results: list[tuple[int, str]] = []
    start = 0
    while True:
        index = content.find(needle, start)
        if index == -1:
            return results
        if index > 0 and (content[index - 1].isalnum() or content[index - 1] in "._"):
            start = index + 1  # `XxxEpeL10n.Tr` のような部分一致を除く
            continue
        depth, in_string, escaped = 0, False, False
        for position in range(index + len(needle) - 1, len(content)):
            char = content[position]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    results.append((content.count("\n", 0, index) + 1,
                                    content[index + len(needle):position]))
                    start = position + 1
                    break
        else:
            return results


def _l10n_split_args(text: str) -> list[str]:
    """トップレベルのカンマで引数を分割する（入れ子の括弧と文字列リテラルは無視する）。"""
    args: list[str] = []
    depth, in_string, escaped = 0, False, False
    current: list[str] = []
    for char in text:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            current.append(char)
        elif char in "([{":
            depth += 1
            current.append(char)
        elif char in ")]}":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def check_21_l10n_key_coverage(ctx: RepoContext) -> None:
    tables = collect_l10n_default_tables(ctx)
    if not tables:
        return  # 翻訳カタログを持たないリポジトリでは何も言わない（検査 18 と同じ方針）
    sources = _l10n_source_files(ctx)
    if not sources:
        return
    by_scope = {table.scope: table for table in tables}
    # scope を 1 つしか持たないリポジトリでは、どのキーもその scope の話でしかない。
    # 複数 scope（本体＋ Samples~ など）では、どの scope に属すかを静的に決められない宣言が
    # あるため「どれか 1 つに在れば良い」で判定する。取りこぼす側（別 scope に在るキーを
    # 正しいとみなす）へ倒すのは、誤検出で検査ごと無効化されるより軽いと判断した。
    all_keys: set[str] = set()
    for table in tables:
        all_keys |= set(table.entries)

    def known(key: str, scope: str | None) -> bool:
        table = by_scope.get(scope or "")
        return key in table.entries if table else key in all_keys

    scope_label = tables[0].scope if len(tables) == 1 else "全 scope"

    # --- ① TextKey クラスが宣言するキー（出荷コードが参照するか否かに関わらず全件） -------
    for rel, content in sources:
        for class_match in L10N_TEXTKEY_CLASS_RE.finditer(content):
            class_name = class_match.group("name")
            if ctx.is_waived("21", class_name):
                continue
            body = _class_body(content, class_match.end())
            declared = [(match.group("name"), match.group("value"))
                        for match in L10N_CONST_RE.finditer(body)]
            if not declared:
                ctx.add(
                    "21",
                    ERROR,
                    f"`{class_name}` から翻訳キーの宣言を 1 件も拾えていません。クラスは在るのに"
                    f"0 件という状態は、宣言の書き方が変わって**この検査が何も見ていない**ことを"
                    f"意味します（0 件のまま緑になるのが一番危ない）。宣言の形を戻すか、"
                    f"検査側の抽出規則を合わせてください",
                    rel,
                )
                continue
            # 指摘には定数名とキー文字列の**両方**を出す。定数名だけではカタログ側で検索できず、
            # キーだけではコード側で検索できないので、片方だけだと必ず往復が要る。
            missing = sorted(
                f'{name}="{value}"'
                for name, value in declared
                if not known(value, None) and not ctx.is_waived("21", value)
            )
            if missing:
                ctx.add(
                    "21",
                    ERROR,
                    f"`{class_name}` が宣言する翻訳キーのうち {len(missing)} 件が既定ロケールの"
                    f"カタログ（{scope_label}）にありません（{_l10n_sample(missing)}）。"
                    f"`Tr` は未登録のキーを**キー文字列のまま返し、書式引数を捨てる**ため、"
                    f"訳が出ないのではなく画面にキー名が出て埋め込むはずの値ごと消えます。"
                    f"全ロケールが等しく欠いていると検査 18 では構造上鳴りません",
                    rel,
                )

    # --- ② `Tr` 呼び出しが参照するキー（文字列リテラル・定数のどちらも） ------------------
    consts: dict[str, str] = {}
    for _, content in sources:
        for match in L10N_CONST_RE.finditer(content):
            consts[match.group("name")] = match.group("value")

    facades = collect_l10n_facades(ctx, sources)
    calls = resolved = 0
    for facade in facades:
        needle = f"{facade.class_name}.Tr("
        for rel, content in sources:
            for line, argtext in _l10n_find_calls(content, needle):
                args = _l10n_split_args(argtext)
                if len(args) <= facade.key_index:
                    continue
                calls += 1
                scope = facade.scope
                if facade.key_index == 1:
                    scope_arg = args[0]
                    if scope_arg.startswith('"') and scope_arg.endswith('"'):
                        scope = scope_arg[1:-1]
                    else:
                        scope = consts.get(scope_arg.split(".")[-1].strip(), facade.scope)
                raw = args[facade.key_index]
                if raw.startswith('"') and raw.endswith('"'):
                    key = raw[1:-1]
                else:
                    key = consts.get(raw.split(".")[-1].strip())
                    if key is None:
                        if L10N_KEY_CONST_REF_RE.match(raw) and not ctx.is_waived("21", rel):
                            ctx.add(
                                "21",
                                ERROR,
                                f"{rel}:{line}: 翻訳キー定数 `{raw}` を解決できません。定数の宣言が"
                                f"git 追跡された `Packages/` の外にあるか、消えています。"
                                f"解決できない呼び出しは検査から黙って抜けます",
                                rel,
                            )
                        continue  # 変数で組み立てるキーは静的に決まらない（正当な形）
                resolved += 1
                if not known(key, scope) and not ctx.is_waived("21", key):
                    ctx.add(
                        "21",
                        ERROR,
                        f"{rel}:{line}: `{facade.class_name}.Tr` が参照するキー `{key}` が既定"
                        f"ロケールのカタログ（{scope or scope_label}）にありません。画面には訳文の"
                        f"代わりにキー名が出ます",
                        rel,
                    )

    # 「呼び出しは在るのに 1 件も解決できなかった」を成功として返さない。検査 0 件の緑と、
    # 検査して問題が無かった緑が見分けられないと、この検査は在るだけで何も守らない。
    if calls > 0 and resolved == 0:
        ctx.add(
            "21",
            ERROR,
            f"`Tr` の呼び出しを {calls} 件検出しましたが、キーを 1 つも解決できていません"
            f"（ファサード: {', '.join(facade.class_name for facade in facades) or 'なし'}）。"
            f"この状態の緑は「問題が無い」ではなく「何も見ていない」です",
        )


def _class_body(content: str, position: int) -> str:
    """`class X` 宣言の直後から、対応する閉じ波括弧までの本文を返す。

    ファイル単位で定数を拾うと、同じファイルに翻訳キー以外の定数を持つクラスが同居していた
    場合にそれらまで「カタログに無いキー」と誤検出する。クラスの境界で切る。
    """
    start = content.find("{", position)
    if start == -1:
        return ""
    depth = 0
    for index in range(start, len(content)):
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                return content[start + 1:index]
    return content[start + 1:]


def check_extra(ctx: RepoContext) -> None:
    for name, package_dir, meta in ctx.packages:
        rel = (package_dir / "package.json").relative_to(ctx.root).as_posix()
        if not meta.get("unityRelease"):
            ctx.add("+", WARN, f"{name}: package.json に unityRelease がありません（GOLD_STANDARD §2.5）", rel)
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
    # 環境変数を最優先にする。CI では個人設定ファイルが無く、`~/dev/MySite` も存在しないため、
    # チェックアウト先を明示できないと横断検査（10・12・17）が丸ごと黙ってしまう。
    # `PIPELINE_TODAY` と同じ流儀で、環境から与えられた事実を推測より優先する
    env_override = os.environ.get("PIPELINE_SITE_REPO")
    if env_override:
        candidates.append(env_override)
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
    check_13_suite_versions,
    check_14_bundled_contents,
    check_15_stale_screenshots,
    check_16_changelog_coverage,
    check_17_license_surfaces,
    check_18_l10n_catalogs,
    check_19_translation_doc_naming,
    check_20_skills_under_version_control,
    check_21_l10n_key_coverage,
    check_extra,
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="リポジトリガイドと実装の整合を検査する")
    parser.add_argument("--root", default=".", help="対象リポジトリのルート（既定: カレント）")
    parser.add_argument("--strict", action="store_true", help="warn も失敗として扱う")
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    parser.add_argument(
        "--separate-untracked",
        action="store_true",
        help=(
            "未追跡ファイル起因の指摘を別枠へ分け、集計と終了コードから外す"
            "（並行作業の置き土産と自分の変更を切り分ける。指摘自体は消さず必ず表示する）"
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: 対象ディレクトリがありません: {root}", file=sys.stderr)
        return 2

    ctx = build_context(root)
    for check in CHECKS:
        check(ctx)

    # 既定では分離しない（`untracked` の印は付いていても集計は従来どおり）。
    # フラグを付けたときだけ 2 つの欄へ分け、終了コードは前者だけで決める。
    if args.separate_untracked:
        findings, untracked_findings = split_untracked(ctx.findings)
    else:
        findings, untracked_findings = ctx.findings, []

    errors = [f for f in findings if f.severity == ERROR]
    warnings = [f for f in findings if f.severity == WARN]

    if args.json:
        payload = {
            "root": str(root),
            "role": ctx.role,
            "packages": [name for name, _, _ in ctx.packages],
            "errors": [finding_payload(f) for f in errors],
            "warnings": [finding_payload(f) for f in warnings],
        }
        if args.separate_untracked:
            # 頼まれたときだけ欄を足す（既定の JSON の形は 1 キーも変えない）
            payload["untrackedErrors"] = [
                finding_payload(f) for f in untracked_findings if f.severity == ERROR
            ]
            payload["untrackedWarnings"] = [
                finding_payload(f) for f in untracked_findings if f.severity == WARN
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        label = ctx.config.get("repository") or root.name
        print(f"== 標準準拠検査: {label}（role={ctx.role}, packages={len(ctx.packages)}）")
        for line in collapse_findings(findings):
            print(line)
        if not findings:
            # 下に別枠が続くときに「問題は見つかりませんでした。」だけを出すと嘘になる
            print("別枠のもの以外に問題は見つかりませんでした。" if untracked_findings else "問題は見つかりませんでした。")
        summary = f"-- error {len(errors)} 件 / warn {len(warnings)} 件"
        if untracked_findings:
            # 「error 0 件」だけを切り取って読まれないよう、同じ行に分離した件数を必ず添える
            summary += f"（別枠: 未追跡ファイル起因 {len(untracked_findings)} 件）"
        print(summary)
        if untracked_findings:
            print("")
            print(
                f"== 未追跡ファイル起因 {len(untracked_findings)} 件"
                "（git がまだ知らないファイルが原因。上の集計と終了コードには含めていません）"
            )
            for line in collapse_findings(untracked_findings):
                print(line)
            print(
                "-- 自分が置いたファイルなら `git add -N <path>`（intent-to-add）してから検査し直してください"
                "（上の集計へ戻ります）。"
            )
            print(
                "   残る分は他の作業が置いたファイルです。**この欄が空でないリポジトリはリリースできません**"
                "（利用者側でだけ壊れます）。分離は自分の変更を切り分けるためのもので、消してよい理由ではありません。"
            )

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
