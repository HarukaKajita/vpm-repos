#!/usr/bin/env python3
# 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
# 編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
# source: UnityTemplate_2022_3_22f1/scripts/pipeline/check_l10n_catalogs.py
# source-sha256: 73cbd91df236a5ee9878ee879ac563b1808e3f6e298531e666ad2c0e5c48aacf
"""翻訳カタログの整合を Unity を起動せずに 1 コマンドで検証する（ゴールド標準 §2.10 第2層）。

なぜ要るか: カタログを編集する作業の大半は Unity の外で起きる（エージェントによる翻訳追加・
複数リポジトリ跨ぎの作業・他プロジェクトで Editor がロックを持っている場合）。それなのに
`Tools > UnityEditorLocalization > Validate Catalogs` は Editor 上で人がメニューを押したときに
しか動かない。結果として「検証が要るたびに使い捨てのスクリプトが書かれ、同じ判定が何度も
書き直される」状態が続いていた（2026-08-14 に 1 セッションで 3 回再実装されたのを実測）。

このスクリプトは**新しい判定を 1 つも持たない**。既にある検査を 1 つの入口へ束ねるだけである。
判定の正本は次のとおりで、ここはそれを呼ぶだけ:

  - カタログの構造（key 集合・placeholder・孤児テーブル・未追跡テーブル・manifest の不備）
      → `verify_repo_guide.py` の検査 18（C# の `EditorL10nValidator` と同じ規則）
  - コードが宣言・参照するキーの網羅（全ロケールが等しく欠いているキー）
      → `verify_repo_guide.py` の検査 21
  - `Tr` の引数個数と placeholder の整合 / Unity 公式用語 / 文言中の識別子の実在
      → UnityEditorLocalization 同梱スキル `editor-localization-translation-quality` の
        `scripts/*.py`（用語対照表などの資料がそちらにあるため、正本もそちらに置く）

使い方（対象リポジトリのルートで実行）:
    python3 scripts/pipeline/check_l10n_catalogs.py             # error があれば非ゼロ終了
    python3 scripts/pipeline/check_l10n_catalogs.py --strict    # warn も失敗として扱う
    python3 scripts/pipeline/check_l10n_catalogs.py --json      # 機械可読の結果を出力
    python3 scripts/pipeline/check_l10n_catalogs.py --require-skill-scripts
        # スキル同梱スクリプトを解決できないこと自体を失敗にする（リリース関門向け）

**検査できなかったことを緑にしない。** スキル同梱スクリプトを解決できなければ、その分は
「未実行」として必ず出力に明示する（既定では終了コードに影響しないが、黙って省略はしない）。

正本: UnityTemplate_2022_3_22f1/scripts/pipeline/check_l10n_catalogs.py
各開発リポジトリへは scripts/distribute_standard.py が配布する（配布物は編集しない）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# スキル同梱スクリプトの置き場所（UEL パッケージ内の相対パス）。
SKILL_SCRIPTS_REL = "skills/editor-localization-translation-quality/scripts"
UEL_PACKAGE = "com.kajitaharuka.unity-editor-localization"


def load_verify_module():
    """同じフォルダに配布されている `verify_repo_guide.py` を読み込む。

    判定を写さずに呼ぶための依存。見つからない場合は黙って劣化させず、直し方を出して止める
    （2 つは常に対で配布されるので、片方が無いのは配布が壊れている状態である）。
    """
    path = HERE / "verify_repo_guide.py"
    if not path.is_file():
        print(f"ERROR: {path} がありません。標準の再配布が必要です"
              f"（テンプレートリポジトリで scripts/distribute_standard.py）", file=sys.stderr)
        raise SystemExit(2)
    spec = importlib.util.spec_from_file_location("verify_repo_guide", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["verify_repo_guide"] = module  # dataclass の解決に登録が要る
    spec.loader.exec_module(module)
    return module


@dataclass
class ExternalResult:
    """スキル同梱スクリプト 1 本ぶんの実行結果（未実行も含めて必ず記録する）。"""

    name: str
    command: list[str]
    status: str  # "ok" / "failed" / "skipped"
    summary: str
    output: str = ""


@dataclass
class Report:
    findings: list = field(default_factory=list)
    external: list[ExternalResult] = field(default_factory=list)
    skill_scripts: Path | None = None
    skill_scripts_note: str = ""


# ---------------------------------------------------------------------------
# スキル同梱スクリプトの解決
#
# **`Library/PackageCache/` は候補に入れない。** 実測（2026-08-14）で、5 リポジトリの
# PackageCache に居た UEL のコピーは版が揃っておらず、`check_unity_official_terms.py` は
# どのコピーにも無く、`check_message_identifiers.py` は 5 つ中 1 つにしか無かった。
# 古いコピーを掴むと「走ったが中身が古い」という、走らないより悪い状態になる。
# ---------------------------------------------------------------------------


def resolve_skill_scripts(verify, ctx, explicit: str | None) -> tuple[Path | None, str]:
    """スキル同梱スクリプトのフォルダを解決する。戻り値は (パス, 経路の説明)。"""
    if explicit:
        path = Path(explicit).expanduser()
        return (path, "--skill-scripts") if path.is_dir() else (None, f"--skill-scripts が無効: {path}")

    env = os.environ.get("KAJITAHARUKA_L10N_SKILL_SCRIPTS")
    if env and Path(env).expanduser().is_dir():
        return Path(env).expanduser(), "環境変数 KAJITAHARUKA_L10N_SKILL_SCRIPTS"

    # 自リポジトリが正本を持っている場合（UnityEditorLocalization 自身）
    for candidate in sorted((ctx.root / "Packages").glob(f"*/{SKILL_SCRIPTS_REL}")):
        if candidate.is_dir():
            return candidate, "自リポジトリ同梱の正本"

    # レジストリ経由で UEL のチェックアウトを解決する（設計 §5 の解決手順を再利用）
    site_root = verify.resolve_site_repo(ctx)
    if site_root is not None:
        for repo_path in verify.resolve_registry_repos(site_root):
            candidate = repo_path / "Packages" / UEL_PACKAGE / SKILL_SCRIPTS_REL
            if candidate.is_dir():
                return candidate, f"レジストリ経由（{repo_path.name}）"

    # 兄弟ディレクトリ（レジストリを解決できない環境向けの最後の手段）
    sibling = ctx.root.parent / "UnityEditorLocalization" / "Packages" / UEL_PACKAGE / SKILL_SCRIPTS_REL
    if sibling.is_dir():
        return sibling, "兄弟ディレクトリ"

    return None, "解決できず"


# ---------------------------------------------------------------------------
# スキル同梱スクリプトの実行
# ---------------------------------------------------------------------------


def run_external(name: str, command: list[str], cwd: Path) -> ExternalResult:
    process = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    output = (process.stdout or "") + (process.stderr or "")
    status = "ok" if process.returncode == 0 else "failed"
    summary = next((line for line in reversed(output.splitlines()) if line.strip()), "")
    return ExternalResult(name=name, command=command, status=status, summary=summary, output=output)


def build_external_commands(verify, ctx, scripts: Path) -> list[tuple[str, list[str]]]:
    """走らせるコマンドを、リポジトリの実体（manifest とコード）から組み立てる。

    引数を `pipeline/repo.json` へ書かせないのは、ファサード名やカタログの場所を変えたときに
    設定だけが古くなり「検査しているつもりで 0 件」になるのを避けるため。検査 21 と同じ流儀で
    宣言から読む。
    """
    tables = verify.collect_l10n_default_tables(ctx)
    if not tables:
        return []
    package_dirs = [str(package_dir.relative_to(ctx.root)) for _, package_dir, _ in ctx.packages]
    src_args: list[str] = []
    for rel in package_dirs:
        src_args += ["--src", rel]

    # `Tr` の呼び出し規約はファサードごとに違う。key 先頭型（各製品の `XxxL10n.Tr(key, args)`）と
    # scope 先頭型（基盤本体の `EditorL10n.Tr(scope, key, args)`）を取り違えると、引数を 1 つ
    # ずらして数えてしまう。宣言から読んだ `key_index` でそのまま振り分ける。
    method_args: list[str] = []
    for facade in verify.collect_l10n_facades(ctx):
        flag = "--method" if facade.key_index == 0 else "--method-scope-first"
        method_args += [flag, f"{facade.class_name}.Tr"]

    commands: list[tuple[str, list[str]]] = []
    # 引数個数の検査はキー単位で完結するので、複数 scope のカタログをまとめて 1 回で回す。
    # scope ごとに分けて回すと、走査範囲に混在する別 scope のキーが軒並み
    # 「カタログに key がありません」になってしまう（`Samples~` を持つ UEL がその形）。
    if method_args:
        catalog_args: list[str] = []
        for table in tables:
            catalog_args += ["--catalog", table.table_rel]
        commands.append((
            "Tr 引数と placeholder の整合",
            [sys.executable, str(scripts / "check_tr_placeholder_parity.py"),
             *catalog_args, *src_args, *method_args],
        ))

    # 用語と識別子の検査はカタログ 1 枚ごとに意味が閉じている（対照表も既定ロケール基準）ので
    # scope ごとに回す。
    for table in tables:
        locales_dir = str(Path(table.table_rel).parent)
        commands.append((
            f"Unity 公式のエディタ翻訳との用語一致（{locales_dir}）",
            [sys.executable, str(scripts / "check_unity_official_terms.py"), locales_dir],
        ))
        commands.append((
            f"文言が名乗る識別子の実在（{table.scope}/{table.default_locale}）",
            [sys.executable, str(scripts / "check_message_identifiers.py"),
             "--catalog", table.table_rel, *src_args],
        ))
    return commands


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def collect(verify, root: Path, explicit_scripts: str | None) -> Report:
    ctx = verify.build_context(root)
    verify.check_18_l10n_catalogs(ctx)
    verify.check_21_l10n_key_coverage(ctx)

    report = Report(findings=list(ctx.findings))
    scripts, note = resolve_skill_scripts(verify, ctx, explicit_scripts)
    report.skill_scripts, report.skill_scripts_note = scripts, note

    if not verify.collect_l10n_default_tables(ctx):
        return report  # 翻訳カタログを持たないリポジトリでは外部検査も走らせない

    if scripts is None:
        report.external.append(ExternalResult(
            name="スキル同梱の検査（Tr 引数 / Unity 公式用語 / 識別子の実在）",
            command=[],
            status="skipped",
            summary=f"UnityEditorLocalization 同梱スキルの scripts/ を解決できません（{note}）。"
                    f"--skill-scripts か環境変数 KAJITAHARUKA_L10N_SKILL_SCRIPTS で場所を指定してください",
        ))
        return report

    for name, command in build_external_commands(verify, ctx, scripts):
        missing = not Path(command[1]).is_file()
        if missing:
            report.external.append(ExternalResult(
                name=name, command=command, status="skipped",
                summary=f"{Path(command[1]).name} が解決先にありません（UnityEditorLocalization が古い可能性）",
            ))
            continue
        report.external.append(run_external(name, command, root))
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="翻訳カタログの整合を Unity 無しで検証する")
    parser.add_argument("--root", default=".", help="対象リポジトリのルート（既定: カレント）")
    parser.add_argument("--strict", action="store_true", help="warn も失敗として扱う")
    parser.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    parser.add_argument("--skill-scripts", help="UEL 同梱スキルの scripts/ を明示指定する")
    parser.add_argument("--require-skill-scripts", action="store_true",
                        help="スキル同梱スクリプトを実行できないこと自体を失敗として扱う")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: 対象ディレクトリがありません: {root}", file=sys.stderr)
        return 2

    verify = load_verify_module()
    report = collect(verify, root, args.skill_scripts)

    errors = [f for f in report.findings if f.severity == verify.ERROR]
    warnings = [f for f in report.findings if f.severity == verify.WARN]
    failed_external = [r for r in report.external if r.status == "failed"]
    skipped_external = [r for r in report.external if r.status == "skipped"]

    if args.json:
        print(json.dumps({
            "root": str(root),
            "errors": [f.__dict__ for f in errors],
            "warnings": [f.__dict__ for f in warnings],
            "skillScripts": str(report.skill_scripts) if report.skill_scripts else None,
            "skillScriptsNote": report.skill_scripts_note,
            "external": [{"name": r.name, "status": r.status, "summary": r.summary,
                          "command": r.command, "output": r.output} for r in report.external],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"== 翻訳カタログ検証: {root.name}")
        for line in verify.collapse_findings(report.findings):
            print(line)
        if not report.findings:
            print("検査 18・21: 問題は見つかりませんでした。")
        for result in report.external:
            mark = {"ok": "OK   ", "failed": "ERROR", "skipped": "SKIP "}[result.status]
            print(f"{mark} {result.name}: {result.summary}")
            if result.status == "failed":
                for line in result.output.splitlines():
                    print(f"      {line}")
        print(f"-- error {len(errors)} 件 / warn {len(warnings)} 件 / "
              f"外部検査 失敗 {len(failed_external)} 件・未実行 {len(skipped_external)} 件")
        if skipped_external and not args.require_skill_scripts:
            print("   未実行の検査は「問題なし」ではありません。"
                  "リリース関門では --require-skill-scripts を付けて未実行を失敗にしてください。")

    if errors or failed_external:
        return 1
    if args.require_skill_scripts and skipped_external:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
