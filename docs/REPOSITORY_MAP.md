<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/（MySite の pipeline/repositories.json から生成）
     source-sha256: 7032157e3a2f965b73f64234b383ea640fafd15990c09cec07cee3850ff09780 -->

# パイプラインのリポジトリ地図

kajitaharuka 名義の Unity パッケージ／アセットの開発〜販売パイプラインに乗っているリポジトリの一覧。**正本は MySite の `pipeline/repositories.json`** で、この文書はそこから生成される。

> この地図は公開リポジトリ向けに縮約されている（非公開リポジトリの行と、すべての URL・ローカルパスは含まれない）。

| 場所 | 何の正本か |
|---|---|
| `UnityTemplate_2022_3_22f1` | 標準（ゴールド標準・配布器・検証スクリプト） |
| 各開発リポジトリ | 実装とリリース資材。標準は配布されたコピーを持つ |
| `MySite` | 運用（実在リポジトリ一覧・出品資料・商品ページの site 実装） |
| `external-content` | 商品情報とリリース契約 |

## 販売リポジトリ

| リポジトリ | 商品 slug | 既定 / 作業ブランチ | 公開範囲 |
|---|---|---|---|
| **UnityEditorLocalization** | unity-editor-localization | main / develop | public |

## 配信基盤

| リポジトリ | 商品 slug | 既定 / 作業ブランチ | 公開範囲 |
|---|---|---|---|
| **vpm-repos** | — | main / main | public |

## ローカルパスの解決手順

ローカルの配置はマシンごとに異なるため、**この文書にはパスを書かない**。解決は必ず次の順で行う。

1. `~/.kajitaharuka-pipeline.json` の `overrides`（リポジトリにコミットしない個人設定）
2. レジストリの `localPathCandidates` を順に試す
3. **`git -C <path> remote get-url origin` がレジストリの remote と一致することを検証する**（同じパスにある別リポジトリを掴まないため）
4. 解決できなければ remote が入口。clone コマンドを提示して止まる（勝手に clone しない）

