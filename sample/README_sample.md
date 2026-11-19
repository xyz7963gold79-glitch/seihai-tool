# 見本素材について / About the Sample

## 日本語

これは、ツールを「まず動かしてみる」ための、1場面だけの見本素材です。

『星海鉄道カプセル便』第1話の場面5（駅で、駅員のリラと少女ホシカが出会う場面）の素材を、そのまま小さくまとめたものです。

### 中身

| ファイル | 内容 |
|---|---|
| `bg_station.png` | 背景プレート（人物を消した駅の場面絵） |
| `char_lira.mp4` | 人物動画：リラ（緑背景で撮ったもの） |
| `char_hoshika.mp4` | 人物動画：ホシカ（緑背景で撮ったもの） |
| `voice.mp3` | この場面の声の音声 |
| `sample_spec.json` | 見本の場面表（パスはすべて相対パスです） |

### 動かし方

`seihai-tool` フォルダの中で、次の1コマンドを実行します（Python 3 と ffmpeg が必要です）。

```bash
python3 make_episode.py sample/sample_spec.json
```

### 出来上がるもの

- `sample/sample_output.mp4`——18秒の動画（1920×1080 / 30fps）。駅の背景の上に、2人の人物が緑から抜き出されて重なっています。声は1秒遅れて始まります。
- `sample/stills/`——確認用の静止画が自動で切り出されます。

緑色（クロマキーの色）は、スペックに書かれていないので、ツールが動画の四隅から自動で測ります。配置と大きさ（リラ：k=1.51、位置 (-120, 78)／ホシカ：k=1.47、位置 (300, 195)）は、第1話の承認済みの値をそのまま使っています。

数値を変えて再実行すると、人物の位置や大きさがどう変わるか試せます。それが、このツールの基本的な使い方です。

---

## English

This is a one-scene sample so you can try the tool right away.

It bundles the actual assets of Scene 5 from Episode 1 of *Seihai Railway Capsule Delivery* — the station scene where Lira, the station attendant, meets the girl Hoshika.

### Contents

| File | What it is |
|---|---|
| `bg_station.png` | Background plate (the station scene art with the characters removed) |
| `char_lira.mp4` | Character video: Lira, shot against a green background |
| `char_hoshika.mp4` | Character video: Hoshika, shot against a green background |
| `voice.mp3` | Voice audio for this scene |
| `sample_spec.json` | The sample scene table (all paths are relative) |

### How to run

From inside the `seihai-tool` folder, run this one command (Python 3 and ffmpeg required):

```bash
python3 make_episode.py sample/sample_spec.json
```

### What you get

- `sample/sample_output.mp4` — an 18-second video (1920×1080, 30 fps): the two characters are keyed out of their green backgrounds and composited over the station. The voices start after a 1-second delay.
- `sample/stills/` — check stills, extracted automatically.

The chroma-key green is not written in the spec, so the tool measures it automatically from the videos' corner pixels. The placements and scales (Lira: k=1.51 at (-120, 78); Hoshika: k=1.47 at (300, 195)) are the approved values from Episode 1, unchanged.

Try changing the numbers and running it again to see how positions and sizes change — that is the basic way this tool is used.
