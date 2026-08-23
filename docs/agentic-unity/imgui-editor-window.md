<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/docs/agentic-unity/imgui-editor-window.md
     source-sha256: 213eb51e5cd9a03573484e0d35f438426370afce6a85e0580971a3a63d70f9b3 -->

# エディタ拡張（IMGUI）で踏んだ罠

**生成物。正本はテンプレートリポジトリの `docs/agentic-unity/imgui-editor-window.md`。**

いずれも Unity 2022.3.22f1 / Sparkler の調整用ウィンドウ（2026-08-23）で実機確認した。

## レイアウト

### `GUILayout.MinHeight` は `ExpandHeight` を打ち消す

`BeginScrollView` に `MinHeight` を渡すと `stretchHeight` が 0 に戻り、**残り高さを取らなくなる**。
オプションの順序を入れ替えても同じ。窓を 1000px にしても中帯が 80px のままになった。

**スクロールビューはオプションを何も渡さないのが正解。** それだけで残りの高さを取り、
下帯が自然に最下段へ来る。

### `EditorGUILayout.LabelField` は折り返さない

高さが `singleLineHeight` 固定なので、スタイルに `wordWrap = true` を付けても効かず、
**溢れた後半が黙って切れる**。結果を告げる文の後半が消えるので、警告文ほど危ない。

- 折り返したい本文は `GUILayout.Label(text, style)` を使う。
- 逆に 1 行に収めたいもの（パスなど）は `wordWrap = false` + `TextClipping.Clip` の専用スタイルにする。
  空白を含まないパスは `wordWrap` でも 1 単語扱いで縮まないため、他の要素を画面外へ押し出す。

### 帯の高さは定数で見積もらない

注記の有無と折り返しで実高さは変わる。**実際に配られた矩形を測る。**
「下帯の上端 − 上帯の下端」のように、両端の `GUILayoutUtility.GetLastRect()` から引き算するのが確実。
`EndScrollView` 直後の `GetLastRect()` は**割り当て高さではなく要求した最小値**を返すので、
これで測ると窓を広げても値が動かない。

### 自動で畳むときは人の意思と別フラグで持つ

窓が低いときに帯を自動で畳む機能を、人が操作するフラグ（`_expanded`）へ直接書くと、
窓を広げても戻らず**押しても開かない壊れた foldout** になる。
`_autoCollapsed*` を別に持ち、描画は合成値で行う。

戻す条件は単純なヒステリシスにしない。「戻すと再び足りなくなる」ため 2 状態を往復して振動する
（実測で 36px ↔ 275px）。**帯を開いたときと畳んだときの実高さの差を憶えておき、
「戻しても最低高さを割らない」ときだけ戻す。**

### `EditorGUI.EndChangeCheck()` の真だけで意思を保存しない

値が実際に変わったかも見る。自動で畳んだ状態の foldout で偽の変更が拾われると、
その状態が「人が畳んだ」ことになって戻らなくなる。

## イベントと状態

### 描画の途中で行数を変えない

Layout と Repaint でコントロール数が食い違うと
`ArgumentException: Getting control N's position in a group with only M controls` になる。

- 構造の変更・診断の修復・モーダル表示は**遅延キューへ積み、次フレームの Layout で流す**。
  積むときに `GUIUtility.keyboardControl = 0` でフォーカスを外す。
- **時刻で分岐して描画物を増減させない。** 「2 秒だけ印を出す」を描画中に判定すると、
  印が消える瞬間に Layout と Repaint がずれ、**その行のボタンの当たり判定が 1 つ隣へずれる**。
  期限の判定は Layout でだけ行い、フレーム内では不変にする。

### 標準 Inspector での編集は `hierarchyChanged` を飛ばさない

コンポーネントのプロパティ変更では再解決が走らず、**古い集計と新しいフィールド値が同居する**。
`ObjectChangeEvents.changesPublished` を購読し、`ChangeGameObjectOrComponentProperties` などで
再解決フラグを立てる（コールバックの中で解決し直さない）。

### `SerializedProperty` はキャッシュしてよい

`FindProperty` は ParticleSystem のような巨大なシリアライズではルートからの走査になり、
1 回およそ 18 マイクロ秒掛かる。段別の検査で 168 回引いて **3 ms** を使っていた。
`SerializedObject` だけでなく `SerializedProperty` も憶えると **0.36 ms** になった（12 分の 1）。

- `Update()` を跨いでも `SerializedProperty` は有効なので引き直す必要はない。
- ただし**「見つからなかった」を憶えてはいけない**。配列要素は後から生えるので、
  憶えると要素を足しても永久に `null` を返し続ける。
- 配列サイズの変更で無効化されることがあるので、触って例外なら引き直す。

### Undo

- `SerializedObject.ApplyModifiedProperties()` は Undo を積む。**`Undo.RecordObject` を併用しない**
  （Undo が 2 段になり Ctrl+Z が 2 回必要になる）。
- **ドラッグ 1 回 = Undo 1 件**に畳む。個々のスライダに書くと入れ忘れるので、
  イベントの入口（`MouseDown` / `MouseUp`）で一括して開閉する。
- 遅延実行が走るときはドラッグ用グループを畳まず捨てる。残すと次の `MouseUp` が
  無関係な操作まで 1 件に潰す。

### `EditorWindow.minSize` は `OnEnable` で設定する

メニューを開く経路でしか設定しないと、**既に開かれていた窓には永久に反映されない**
（ウィンドウの状態はレイアウトへ保存されるため）。

### private フィールドはドメインリロードで初期化子へ戻る

`SessionState` に保存した値だけが生き残ると、**画面の表示と実データが食い違う**。
「基準値は残るのに倍率だけ 1.0 へ戻り、スライダに 1px 触れた瞬間に値が基準へ飛ぶ」という事故が起きた。
**対になる状態は同じ寿命で持つ。**
