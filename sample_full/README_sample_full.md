# 見本（フルアニメ方式）/ Sample (Full-Animation Method)

短編『星降る丘の少女たち　星の光の中で、愛は生きる』の場面1の素材、そのまま実行できる見本です。

This is a ready-to-run sample: the assets of Scene 1 from the short film
*Girls on the Starfall Hill*.

## 中身 / Contents

- `scene1_raw.mp4`——場面1の場面動画（生成したままの未加工品。1248×704 / 24fps / 10秒）
  The scene video exactly as generated (1248×704, 24 fps, 10 s) — not normalized.
- `sample_full_spec.json`——見本の場面表 / The sample scene table.

## 実行 / Run

リポジトリのトップフォルダで / From the repository's top folder:

```bash
python3 make_full.py sample_full/sample_full_spec.json
```

## 出来上がるもの / Result

- `sample_full/sample_full_output.mp4`——1920×1080 / 30fps / 10秒の動画。
  元動画が 1248×704・24fps でも、ツールが自動で大きさとフレームレートを揃え、
  冒頭フェードイン（0.7秒）と末尾フェードアウト（1.4秒）を付けます。
  A 1920×1080, 30 fps, 10-second video. Even though the source is
  1248×704 at 24 fps, the tool normalizes size and frame rate, and adds
  the opening fade-in (0.7 s) and closing fade-out (1.4 s).
- `sample_full/stills/`——確認用の静止画 / Check stills.

短編本編（全5場面）の場面表は、トップフォルダの `starhill_spec.json` です。
場面動画そのものは同梱していません（場面1だけ、この見本に入っています）。

The full five-scene table for the short film is `starhill_spec.json` in the
top folder. The scene videos themselves are not bundled (only Scene 1, here).
