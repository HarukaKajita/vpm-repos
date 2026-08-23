<!-- 生成物: この内容はテンプレートリポジトリ UnityTemplate_2022_3_22f1 から配布されたコピーです。
     編集はテンプレート側で行い、scripts/distribute_standard.py で再配布してください。
     source: UnityTemplate_2022_3_22f1/docs/agentic-unity/prefab-and-variant.md
     source-sha256: cd105a22063e921870573fa5adb6a7d8a0d1425af074f9d079ab085e09b16409 -->

# プレハブ / バリアント / マテリアルバリアントの API の実際

**生成物。正本はテンプレートリポジトリの `docs/agentic-unity/prefab-and-variant.md`。**

いずれも Unity 2022.3.22f1 / Sparkler（2026-08-23）で実機確認した。

## 引数の順序を間違えると「別の失敗」に見える

`PrefabUtility.RevertRemovedGameObject` の実際のシグネチャは

```csharp
RevertRemovedGameObject(GameObject gameObjectInInstance, GameObject assetGameObject, InteractionMode action)
```

で、**インスタンス側が第 1 引数、アセット側が第 2 引数**。読みやすい順（アセットが先）と逆である。

逆に渡すと `Calling apply or revert methods on an object which is not part of a Prefab instance is not supported`
という例外になる。この文言は「Prefab Mode（プレビューシーン）だから拒否された」と読めてしまうが、
**実シーン上のインスタンスでも同じ例外になる**ので、そちらを疑うと原因に辿り着けない。

`Type.GetMethod(...).GetParameters()` で**引数名を確かめる**のが最短。型だけ見ても順序は分からない。

## 入れ子プレハブの参照先はバリアントで差し替えられない

`PrefabUtility.ReplacePrefabAssetOfPrefabInstance` は
`Input instance is not an outermost Prefab instance root` で拒否する。
バリアントの中の入れ子インスタンスは `IsAnyPrefabInstanceRoot == true` でも
`IsOutermostPrefabInstanceRoot == false` なので通らない。

**「バリアントごとに中身のプレハブを差し替える」は Unity のオーバーライド機構に無い。** 取れる形は 2 つ:

1. インスタンスを消さず、中身のプロパティ（マテリアル・色など）をインラインで上書きする
   → 色の定義がバリアントの数だけ増える
2. ベースの入れ子インスタンスを**外し**（`m_RemovedGameObjects`）、望みのプレハブを**足す**
   （`m_AddedGameObjects`）
   → 定義は 1 か所で済む。ただし足した側はベースの変更を追わなくなるので、
   **配置は親（ピボット）側に持たせる**。ピボットはベースが所有し続けるので配置は伝播する

## 「入れ子の中身か」の判定は最も近いインスタンスルートで見る

```csharp
var nearest = PrefabUtility.GetNearestPrefabInstanceRoot(go);
bool nested = nearest != null && nearest != stage.prefabContentsRoot;
```

`GetOutermostPrefabInstanceRoot` を使うと、**入れ子を持つプレハブのバリアント**で
最外がステージのルート自身になり、入れ子の中身なのに素通りする。

なお `GetCorrespondingObjectFromSource` は**1 段しか遡らない**。入れ子に対しては
外側プレハブ内のコピーを返すので、「このアセットのインスタンスか」を調べる用途には使えない。
そこは `GetCorrespondingObjectFromSourceAtPath(go, assetPath)` を使う。

## Prefab Stage での書き込み

- モジュール構造体 API（`ps.emission.rateOverTime = ...` など）で書いた変更は、直後に
  `PrefabUtility.RecordPrefabInstancePropertyModifications` を呼ばないと
  `m_Modifications` に載らず、**保存で黙って戻る**。
- `SerializedObject.ApplyModifiedProperties()` 経由なら override は自動で記録される。
- override を落とすかどうかの判定は**ソース側の値**と比べる。セッション開始時の値と比べると、
  バリアントを開いた時点で既に override 済みの値が基準になり、その値へ戻したときに
  ベースの値まで飛ぶ。しかも `InteractionMode.AutomatedAction` だと Undo にも載らない。
- `PrefabUtility.SaveAsPrefabAsset` は Prefab Stage の dirty を落とさない。
  `stage.ClearDirtiness()` を明示的に呼ばないと、保存ゲートを永久に抜けられない実装になる。

## マテリアルバリアント

Unity 2022.1 以降、マテリアルは `m_Parent` を持てる（Material Variant）。

- `m_Parent: {fileID: 0}` なら**ただの独立したマテリアル**。名前が `_Blue` でもバリアントではない。
- 親子にすると、子は上書きしたプロパティだけを持ち、他は親に追従する。
  色差分のように「1 プロパティだけ違う」用途はこれが正しい形。
- 既存の独立マテリアルをバリアント化するときは、**親と同値のプロパティを子から落とす**ところまでやらないと、
  親を直しても子が追従しない（見た目は変わらないので気づけない）。
