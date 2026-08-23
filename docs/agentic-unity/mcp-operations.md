<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/docs/agentic-unity/mcp-operations.md
     source-sha256: 6d3dbd878b48439d4db5d7a0d3328e0f4571988117f12facfdcef079ab7cf25f -->

# MCP for Unity 越しに起動中のエディタを操作する

**生成物。正本はテンプレートリポジトリの `docs/agentic-unity/mcp-operations.md`。**

## モーダルダイアログが出ると全ての呼び出しがタイムアウトする

**確認**: 2026-08-23 / Sparkler / Unity 2022.3.22f1 / Windows 11

Unity がモーダル（`EditorUtility.DisplayDialog`、`Prefab Has Been Modified` など）を出すと、
Unity のメインスレッドはそのダイアログのメッセージループへ入り、ブリッジの要求を一切処理しなくなる。

- エージェント側の見え方は `Timeout receiving Unity response` だけ。
- **ハートビート（`~/.unity-mcp/unity-mcp-status-*.json`）は `"reason":"ready"` のまま**なので、
  症状だけでは「生きているのに無反応」としか読めない。
- **Unity 内のコードは 1 行も動かない。** `EditorApplication.update` も回らないので、
  Unity 自身に解決させることは原理的に不可能。外部プロセスから触るしかない。

### 予防（これでほとんど消える）

実際に踏んだ 2 件はどちらも「Prefab Stage を dirty のまま放置」が原因だった。

1. 一時的に値を書き換えたら、戻したうえで `stage.ClearDirtiness()` を必ず呼ぶ。
2. **再コンパイルを要求する前に `StageUtility.GoToMainStage()` でステージを閉じる。**
   開いていなければドメインリロードは何も尋ねない。
3. 検証用の一時アセットは、実験が終わった時点で片付ける。

### 検出

Win32 のモーダルは**所有者ウィンドウを無効化する**。よって外部プロセスから
Unity のトップレベルウィンドウを列挙し、

- タイトルに `Unity <バージョン>` を含むウィンドウの `IsWindowEnabled` が `false`

なら、モーダルで停止していると断定できる。ダイアログ本体は標準の `#32770` で、
子に本物の `Button` があるので**ラベルもそのまま読める**（何を訊かれているかを人へ正確に伝えられる）。

### 解除

**ダイアログ本体へ `WM_COMMAND` を `PostMessage` する。**

```
PostMessage(hDlg, WM_COMMAND, MAKEWPARAM(ctrlId, BN_CLICKED), hButton)
```

- **`BM_CLICK` をボタンへ送っても閉じない**（モーダルが自前のメッセージループで回っているため）。
  実機で試して確認した。
- **自動で押してよいのは `Cancel` だけ。** Cancel は保留中の操作を中止するだけで、
  編集内容も成果物も失われない。`Save` と `Discard Changes` はどちらも成果物を変えるので、
  検出結果（ダイアログ名とボタン一覧）を人へ提示して決めてもらう。

実装例は MySite の `scripts/unity/unity-modal-dialog.ps1`。

### 却下した案

| 案 | 却下の理由 |
|---|---|
| Unity 側の API で抑止する | メインスレッドが止まっているので Unity 内のコードは動かない |
| Prefab Mode の Auto Save を有効にする | 尋ねられなくなる代わりに、実験的な書き換えが商品プレハブへ自動保存される |
| デスクトップの ComputerUse で押す | 前面化とスクリーン座標に依存するぶん Win32 経由より脆い。そもそも使えない環境が多い |

## タイムアウトした呼び出しは再送されることがある

**確認**: 2026-08-23 / Sparkler

タイムアウトした `execute_code` が再送され、**同じ副作用が複数回起きた**
（ダイアログを出すコードが 6 回登録され、閉じても次が出てくる状態になった）。

- ダイアログが湧き続けても、すぐ「無限ループだ」と判断しない。数回続けて解除し、収まるかを見る。
- **副作用のあるコードを MCP 経由で流すときは冪等に書く。** イベント購読・アセット生成・
  ファイル書き込みは「既にあるなら何もしない」を先頭に置く。

## 画面が更新されないときの見分け方

`EditorWindow.Repaint()` は再描画を**予約するだけ**で、Unity が非前面だと実際の `OnGUI` は
なかなか走らない。エージェントから状態を確かめたいときは、内部 API の
`EditorWindow.RepaintImmediately()`（`NonPublic | Instance`）を呼ぶと同期的に 1 回描ける。

- ただし `OnGUI` が早期 return する経路（Prefab Stage が開いていない等）に入っていると
  何も測れない。**測る前に前提条件を整える。**
- `EditorApplication.delayCall` は非前面だと発火が遅れる。確実に 1 回動かしたいときは
  `EditorApplication.update` に自分を外すハンドラを積むほうが速い。
