# 旧方式（重ね撮影／レイヤー方式）/ Legacy: Layered Method

このフォルダは、Version 1.0 の「重ね撮影方式」の道具一式です。
**旧方式として参考に残しています。新しく作る方は、トップフォルダの
`make_full.py`（フルアニメ方式）を使ってください。**

This folder holds the Version 1.0 toolset (the layered / "overlay shooting"
method), **kept for reference as the legacy method. New work should use
`make_full.py` (the full-animation method) in the top folder.**

## なぜ方式を変えたか / Why the method changed

重ね撮影方式は、背景プレート（人物を消した場面絵）と緑背景の人物動画を
別々に用意し、クロマキーで抜いて重ねる方式でした。実際に第1話を作る中で、

- 人物の配置・大きさ・遠近を場面ごとに手で合わせ続ける必要があり、
- 生成した人物動画の内部の寄り・流れで体が切れたり、
- 緑の抜け残りが出たりと、

段取りが多く、失敗の箇所も多いことが分かりました。
フルアニメ方式（場面絵をそのまま動画化して繋ぐ）は段取りが少なく、
短編『星降る丘の少女たち』で実証できたため、Version 2.0 から
こちらを正式な方式にしました。

The layered method needed background plates and green-screen character
videos prepared separately, keyed and composited scene by scene. Making
Episode 1 with it showed that it has many steps and many failure points:
per-scene placement and perspective fitting, characters clipped by drift
inside generated videos, green fringes, and so on. The full-animation
method (animating each scene picture as-is and joining the results) has
far fewer steps and was proven on the short film *Girls on the Starfall
Hill*, so from Version 2.0 it is the official method.

## 中身 / Contents

- `make_episode.py`——旧方式の本体 / The legacy tool.
- `episode1_spec.json`——第1話の場面表の実例（素材は同梱していません）
  The real Episode 1 scene table (assets not bundled).
- `sample/`——1場面分（第1話・場面5）の見本素材と見本スペック。
  そのまま実行できます / A runnable one-scene sample (Episode 1, Scene 5):

  ```bash
  python3 legacy/make_episode.py legacy/sample/sample_spec.json
  ```

- `images/`——旧方式で作った第1話の画面 / Frames from Episode 1, made with this method.

旧方式の詳しい説明は、Version 1.0 の README（このリポジトリの変更履歴に
残っています。タグ `v1.0` の付いたコミットがそれです）にあります。

Full documentation of the legacy method is in the Version 1.0 README,
kept in this repository's history (the commit tagged `v1.0`).
