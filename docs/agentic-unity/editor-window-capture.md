<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/docs/agentic-unity/editor-window-capture.md
     source-sha256: e6b23e40db82ce1ab865d55c4acbca63ee6e830cf2b29df808b075274dea3efa -->

# エディタの画面を証跡として残す

**生成物。正本はテンプレートリポジトリの `docs/agentic-unity/editor-window-capture.md`。**

## 画面読み取り API は使わない

**確認**: 2026-08-23 / Sparkler / Windows 11

`UnityEditorInternal.InternalEditorUtility.ReadScreenPixel` は**画面そのもの**を読む。
Unity が前面に無いと、**その領域に重なっている別アプリの画面がそのまま PNG に入る**。
実際に踏み、利用者のブラウザ画面が写った画像を作ってしまった。

エージェントは Unity を前面に持ってこないまま作業するのが普通なので、この API は原理的に使えない。

## UEWCE を使う

`UnityEditorWindowCaptureExtension`（`com.kajitaharuka.unity-editor-window-capture-extension`）は
**GUIView の backbuffer** を読むので、他のウィンドウが混ざらない。

```csharp
var opt = new Kajitaharuka.EditorWindowCapture.CaptureOptions {
    IncludeTabBar = true,
    Repaint = Kajitaharuka.EditorWindowCapture.CaptureRepaintMode.Immediate,
    WriteManifest = false,
};
var r = Kajitaharuka.EditorWindowCapture.WindowCapture.CaptureNow(window, opt, outputPath);
// r.success / r.width / r.height / r.suspectedBlank / r.uniformity / r.errorMessage
```

入っていないプロジェクトには `Packages/manifest.json` へ一時導入する。

### 守ること

- **素の `EditorWindow` は Unity が非前面でも中身の入った画像が撮れる。**
  撮影直前に `RepaintImmediately` を打てるため。
- **Inspector 系（`UnityEditor.PropertyEditor` 派生）だけは前面が要る。**
  既存 backbuffer をそのまま掴む経路になるので、非前面だとタブ帯しか写らないことがある。
- **撮影時のサイズ指定（`CaptureOptions.WindowSize`）に頼らない。**
  先にウィンドウをリサイズし、`RepaintImmediately` を数回打って落ち着かせてから、
  サイズ指定なしで撮る。UEWCE は Inspector 系への撮影時サイズ指定を仕様として拒否する。
- **`suspectedBlank` と `uniformity` を必ず確認する。** success でも中身が無いことがある。

## macOS の Retina 2x と同寸で Windows から撮る（2026-08-26 実測）

商品ページの画像は macOS の Retina 2x（`pixelsPerPoint: 2.0`）で撮ってきた。**Windows 機からでも
同寸で撮り直せる。**デスクトップのスケーリングは触らなくてよい。

1. **原本の寸法を sidecar から読む。** 撮影時に `WriteManifest` を有効にしていれば
   `<image>.png.json` に `width` / `height` / `pixelsPerPoint` が残っている。
   promo-kit の `assets/screenshots/<slug>/` に置いてある。
2. **Unity の UI Scaling を 200% にする。** `EditorPrefs` の `CustomEditorUIScale`（int。
   選択肢は 100 / 125 / 150 / 175 / 200 / 225 / 250 / 300 / 350）。
   **次に起動したエディタから効く**ので、設定してから対象プロジェクトを開く。
   Preferences > UI Scaling と同じ設定で、**ユーザー単位・全プロジェクト共通**。
3. 開いたエディタで `EditorGUIUtility.pixelsPerPoint` が 2 になっているのを確認する。
4. **窓の論理サイズはタブバーぶんを引く。** `CaptureWindowSizeScope.Apply(window, 1000, 661)` で
   原本の 1000x682pt になる（タブバー 21pt が上に乗るため）。撮ると 2000x1364px。
5. 撮り終わったら `CustomEditorUIScale` を**消して戻す**。消し忘れると、次に起動した
   すべてのエディタが 200% になる。**戻すのは、値を書いたのと同じエディタで消してから
   そのエディタを終了する**こと。`EditorPrefs` はメモリ上に溜めて終了時に書き出すため、
   別のエディタが古い値を抱えたままだと、そちらの終了時に**消したはずの値が書き戻る**
   （2026-08-26 に実測。撮影側で消した後も、別プロジェクトのエディタが 200 を抱えていて
   次に起動したエディタが 200% になった）。

**UEWCE は実機の `pixelsPerPoint` より大きい値への拡大を拒否する**（`CaptureOptions.OutputPixelsPerPoint`
のコメント）。だから 100% のままでは 1000x682px しか撮れず、拡大でごまかすこともできない。
2x が必要なら UI Scaling を上げるのが唯一の道である。

### 環境ぶんの差は残る（許容するか判断する）

| 差 | 内容 |
|---|---|
| 解決先パス | `/Users/...`（macOS）が `D:/...`（Windows）になる。パスを写す画面では必ず出る |
| 動的な文字列 | プレビューのファイル名など、時刻を含む表示は撮り直すと変わる |
| フォントのラスタライズ | macOS と Windows で微妙に違う（`cross-os-capture-verification` が別物として比較している程度の差） |
| 撮影プロジェクトの違い | Project Settings の左サイドバーのように、**導入パッケージが写る画面**は撮影先を揃えないと項目が変わる |

**合成画像（BOOTH ギャラリー等）は生の撮影を差し替えてから promo-kit で再生成する**
（`node promo-kit/scripts/render.mjs <slug> --shot <id>`）。crop を使う合成は、原本と同じ切り出し
範囲を割り出してから作る（Pillow で 1 行ずつ突き合わせれば位置は機械的に求まる）。

## 撮った画像は読んで確かめる

撮りっぱなしにせず、エージェント自身が画像を読んで内容を確認する。
Sparkler では、この確認で「表の最下行が切れている」「同じ語が 2 か所で違う意味になっている」を
コードを読むだけでは気づけない形で発見した。**レイアウトの不具合は画像でしか見つからない。**
