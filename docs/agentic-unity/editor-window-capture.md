<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/docs/agentic-unity/editor-window-capture.md
     source-sha256: ba98c694acc601a9979a30c0dfadf36fa305321244565986c3d579f02b05b875 -->

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

## 撮った画像は読んで確かめる

撮りっぱなしにせず、エージェント自身が画像を読んで内容を確認する。
Sparkler では、この確認で「表の最下行が切れている」「同じ語が 2 か所で違う意味になっている」を
コードを読むだけでは気づけない形で発見した。**レイアウトの不具合は画像でしか見つからない。**
