# リポジトリガイド（CLAUDE.md / AGENTS.md 共通）

このファイルは、このリポジトリを扱う AI エージェント（Claude Code / Codex など）および人間の開発者へのガイダンスです。

> **注記**: `CLAUDE.md` と `AGENTS.md` は同一内容を保ちます。片方だけを更新せず、両方を同時に更新してください。

## このリポジトリの位置づけ

kajitaharuka 名義の Unity パッケージを **VCC / ALCOM（VRChat Package Manager）から導入できるようにするための配信基盤**です。VRChat の `vrchat-community/template-package-listing` から派生しています。パイプライン上の役割は `infra`（商品そのものではない）。全体像は `docs/REPOSITORY_MAP.md`（生成物）を参照してください。

配信 URL: `https://harukakajita.github.io/vpm-repos/index.json`

## 何が生成物で、何を人が管理するか

| 対象 | 誰が管理するか |
|---|---|
| `source.json` | **人（エージェント）が編集する。** 配信対象リポジトリの一覧と、リスティングの名前・ID・作者 |
| `Website/` | 人が編集する（ランディングページの体裁。任意） |
| `index.json` / `docs/` の生成物 | **GitHub Actions が生成する。手で編集しない** |

## 何を掲載するか（2026-08-15 決定）

**掲載するのは無料公開しているパッケージだけ**にしています。2026-08-15 時点では
`UnityEditorLocalization` の 1 つで、`githubRepos` にもそれしか並んでいません。

理由は、VCC / ALCOM のリスティングは URL を知っていれば誰でも購読でき、**そこに並べた
パッケージは誰でも取得できる**ためです。有料販売しているパッケージ（EPE / UEWCE / TAE / UMPD）は
ここへ載せません。

この方針から次が従います。**有料パッケージのリポジトリで VPM 形式の成果物を書き出す必要はありません。**
販売単位の宣言（各リポジトリの `pipeline/repo.json` の `saleUnit.distribution`）からも
`vpm-zip` を外してあり、新規リリースへ VPM の zip は添付しません
（**過去のリリースに添付済みのものは、購入者の手元との整合を崩さないため削除しません**）。

今後、無料公開するパッケージを作った場合はここへ追加する余地があります。その場合は
`githubRepos` へリポジトリを足し、当該リポジトリ側の `saleUnit.distribution` に `vpm-zip` を戻します。

## 反映のしくみ（ここを間違えると「リリースしたのに VCC に出ない」が起きる）

`.github/workflows/build-listing.yml` が `source.json` の `githubRepos` に並んだリポジトリの **GitHub Releases を走査**して `index.json` を再生成します。

- **GitHub Release を作っただけでは反映されません。** ワークフローの実行が必要です（`source.json` への push、または `workflow_dispatch` の手動実行）。
- 実際に 2026-07-25 に、UEL 1.3.0 の Release があるのにリスティングへ載っていない状態を検出しました（最後のワークフロー実行が 2026-07-07 だったため）。**リリース後は必ずワークフローの実行と `index.json` の内容を確認**してください。
- 各 Release には VPM 用 zip（`{package-name}-{version}.zip`。zip 直下に `package.json` がある構造）が添付されている必要があります。書き出しは各開発リポジトリの VpmPackageExporter が行います。

## 検証

```bash
python3 scripts/pipeline/verify_repo_guide.py   # このリポジトリ自身のガイド整合検査
```

`index.json` の内容確認は、生成後に配信 URL か生成ファイルを直接読んで、対象パッケージの全バージョンが載っていることを確かめます。

## パイプライン整合性の 3 層（配布物・検査・契約）

このリポジトリには、テンプレートリポジトリ `UnityTemplate_2022_3_22f1` から**配布された生成物**があります（GOLD_STANDARD §2.10）。**配布物は編集しないでください。** 変更が必要なときはテンプレートリポジトリ側の正本を直して再配布します（配布物の先頭には `source-sha256` の生成物ヘッダがあり、書き換えると検査 3 が落ちます）。

配布プロファイルは **`guide`**（Unity パッケージを持たないリポジトリ向け）です。標準本体とリリース契約生成器は配られず、エージェンティック Unity 開発の知見・検査・CI・hooks・地図が入ります。

| ファイル | 位置づけ |
|---|---|
| `docs/REPOSITORY_MAP.md` | パイプラインのリポジトリ地図（正本は MySite の `pipeline/repositories.json`） |
| `docs/agentic-unity/` | エージェンティック Unity 開発の知見（撮影・MCP 運用・IMGUI・prefab） |
| `scripts/pipeline/verify_repo_guide.py` | 標準準拠検査（第 2 層） |
| `.githooks/` / `.github/workflows/pipeline-verify.yml` | commit / push / CI の関門 |
| `pipeline/repo.json` | **このリポジトリの手書き宣言**（配布物ではない） |

```bash
python3 scripts/pipeline/verify_repo_guide.py       # 標準準拠検査。error があれば非ゼロ終了
```

Windows で `python3` が Microsoft Store のエイリアスへ解決される環境では `python` を使ってください（hook 側はフォールバック済みです）。

- 検査は `git push` の前に `.githooks/pre-push` が走り、**error が 1 件でもあれば push を止めます**（fail-closed）。**`core.hooksPath` は git が追跡できない設定なので、clone のたびに `git config core.hooksPath .githooks` が要ります。** 忘れると配られた hook が一度も走りません。
- 誤検出や、この対象には筋が通らない指摘は、**検査そのものを消さず** `pipeline/repo.json` の `waivers` へ**理由を添えて**登録します。**期限の型が 3 つあり、どれを選ぶかが「その例外は何を約束しているか」の宣言**になります（スキーマの正本は GOLD_STANDARD §2.10）。
  - `expiresAt`: 「次のリリースまでに必ず片付ける」。**期日を過ぎると error になり push が止まります。**
  - 期限を書かない: 「この対象にこの指摘は筋が通らない」が将来も変わらないとき。
  - `reviewedAt` ＋ `reviewEveryMonths`: 「今は直さなくてよいが、十分に時間が経ったらもう一度考えたい」。期日が来ても止めず **warn で促すだけ**で、見直したら `reviewedAt` を当日へ更新します（その更新が「もう一度考えた」記録になります）。

**このリポジトリ 1 つでは全体像が分かりません。** 標準の正本はテンプレートリポジトリ、運用（レジストリ・商品情報・出品）の正本はサイトリポジトリ（`MySite` ＋ `external-content`）にあります。開発から販売まで回す作業は、**両方をセッションから読める状態にしてから**始めてください（GOLD_STANDARD §2.10。片方しか見えないと「書いてあるか」しか確かめられません）。パイプラインに乗っている全リポジトリの一覧と remote は MySite の `pipeline/repositories.json` が正本で、ローカルパスは機械ごとに違うため `npm run pipeline:repos` で解決結果を確認します。

## 作業の進め方

- ブランチは `main` 一本です。
- **複数リポジトリを跨ぐときは必ず明示的に `cd` してから git 操作**し、コミット後に diff stat の整合を確認します。
- push はユーザーの明示指示があるときだけ行います（配信に直結するため）。
- コミットメッセージ・コメント・文書は日本語で、短い 1 行サマリー（接頭辞なし）にします。
- 上流（VRChat のテンプレート）由来のファイルを改変するときは、更新時に取り込みづらくなるため理由を残します。
