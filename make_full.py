#!/usr/bin/env python3
"""make_full.py — フルアニメ方式アニメ制作ツール

『星海鉄道カプセル便』の短編『星降る丘の少女たち』（2026-11-20公開）で
実証した制作方式を一般化したもの。場面表（スペックJSON）を書いて
実行すると、1本分の完成動画が出力される。

方式（短編の実証済み設定をそのまま踏襲）:
  - 場面絵を動画化した「場面動画」を、そのまま場面として使う。
    背景プレートと人物レイヤーの切り分け（旧・重ね撮影方式）はしない。
  - 場面動画は 1920x1080 / 30fps に正規化する。元動画の解像度や
    フレームレートが場面ごとに違っていても、拡大縮小（画面を覆うまで
    拡大）＋中央クロップ＋fps変換で揃える。
  - 場面動画が場面の秒数より短い場合は、先頭からループして埋める。
  - 音声は場面ごとに任意指定。1つでも音声のある場面があれば全場面に
    音声トラックを持たせ（無指定の場面は無音）、音声は 1 秒遅延 +
    無音パディングで場面長に統一（48kHz）。音声の場面が1つも無ければ
    音声トラック自体を持たない（短編と同じ無音形式）。
  - 場面動画に音声が含まれていても、それは使われない。
  - テロップは場面ごとに drawtext で焼き込む（旧ツールと同形式）。
  - 話全体のフェード（イン 0.7 秒 / アウト 1.4 秒）は連結後に一度だけ適用。

使い方:
  python3 make_full.py scene_spec.json

スペックの書式は README.md を参照。
"""
import json
import os
import subprocess
import sys

FRAME_W, FRAME_H, FPS = 1920, 1080, 30
DEFAULT_FONT = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc'
AUDIO_DELAY_MS = 1000
SEG_DUR_TOLERANCE = 0.15  # seconds


def run(cmd):
    subprocess.run(cmd, check=True)


def probe(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format',
         '-of', 'json', path],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def media_duration(path):
    return float(probe(path)['format']['duration'])


def video_info(path):
    """(width, height, fps) of the first video stream."""
    info = probe(path)
    for s in info['streams']:
        if s.get('codec_type') == 'video':
            num, den = s.get('avg_frame_rate', '0/1').split('/')
            fps = float(num) / float(den) if float(den) else 0.0
            return int(s['width']), int(s['height']), fps
    raise ValueError(f'no video stream: {path}')


TELOP_POSITIONS = ('bottom-center', 'bottom-right', 'bottom-left',
                   'top-center', 'top-right', 'top-left', 'center')


def telop_xy(telop):
    """drawtext x/y expressions for a telop spec."""
    if 'x' in telop and 'y' in telop:
        return str(telop['x']), str(telop['y'])
    pos = telop.get('position', 'bottom-center')
    if pos not in TELOP_POSITIONS:
        raise ValueError(f'unknown telop position: {pos}')
    mx = int(telop.get('margin_x', 60))
    my = int(telop.get('margin_y', 40))
    if 'center' in pos and pos != 'center':
        x = '(w-text_w)/2'
    elif 'right' in pos:
        x = f'w-text_w-{mx}'
    elif 'left' in pos:
        x = str(mx)
    else:
        x = '(w-text_w)/2'
    if pos.startswith('bottom'):
        y = f'h-text_h-{my}'
    elif pos.startswith('top'):
        y = str(my)
    else:
        y = '(h-text_h)/2'
    return x, y


def build_segment(scene, idx, spec_dir, workdir, font, audio_mode):
    """Build one scene segment; returns (seg_path, info dict)."""
    name = scene.get('name', f'scene{idx}')
    dur = float(scene['duration'])
    sdir = os.path.join(workdir, f'scene{idx:02d}')
    os.makedirs(sdir, exist_ok=True)

    def res(p):
        return p if os.path.isabs(p) else os.path.join(spec_dir, p)

    vpath = res(scene['video'])
    vw, vh, vfps = video_info(vpath)
    src_dur = media_duration(vpath)
    looped = src_dur < dur - 0.05

    # -stream_loop -1 makes a short source fill the scene; the trim in the
    # filter chain cuts it to exactly the scene length either way.
    inputs = ['-stream_loop', '-1', '-i', vpath]
    parts = [
        f'[0:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase'
        f':flags=lanczos,crop={FRAME_W}:{FRAME_H},fps={FPS},setsar=1,'
        f'trim=duration={dur},setpts=PTS-STARTPTS[v0]'
    ]
    cur = 'v0'

    for j, telop in enumerate(scene.get('telops', [])):
        tfile = os.path.join(sdir, f'telop_{j}.txt')
        with open(tfile, 'w', encoding='utf-8') as fh:
            fh.write(telop['text'])
        x, y = telop_xy(telop)
        start = float(telop.get('start', 0))
        end = float(telop.get('end', dur))
        parts.append(
            f"[{cur}]drawtext=fontfile={font}:textfile={tfile}:"
            f"fontcolor={telop.get('color', 'white')}:"
            f"fontsize={int(telop.get('fontsize', 60))}:"
            f"borderw={int(telop.get('borderw', 3))}:bordercolor=black:"
            f"x={x}:y={y}:enable='between(t,{start},{end})'[tx{j}]")
        cur = f'tx{j}'

    parts.append(f'[{cur}]format=yuv420p[v]')
    fc = ';'.join(parts)

    maps = ['-map', '[v]']
    if audio_mode:
        audio = scene.get('audio')
        if audio:
            inputs += ['-i', res(audio)]
            fc += (f';[1:a]aresample=48000,'
                   f'adelay={AUDIO_DELAY_MS}|{AUDIO_DELAY_MS},apad,'
                   f'atrim=0:{dur}[a]')
        else:
            inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            fc += f';[1:a]aresample=48000,atrim=0:{dur}[a]'
        maps += ['-map', '[a]']

    seg = os.path.join(workdir, f'seg{idx:02d}.mp4')
    cmd = ['ffmpeg', '-y', '-v', 'error', *inputs, '-filter_complex', fc,
           *maps, '-t', str(dur), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio_mode:
        cmd += ['-c:a', 'aac', '-b:a', '128k']
    cmd += [seg]
    run(cmd)

    actual = media_duration(seg)
    ok = abs(actual - dur) <= SEG_DUR_TOLERANCE
    note = f'src {vw}x{vh}@{vfps:g}fps {src_dur:.2f}s'
    if looped:
        note += ' (looped to fill)'
    info = dict(name=name, target=dur, actual=actual, ok=ok,
                note=note, path=seg)
    return seg, info


def extract_still(src, t, out):
    run(['ffmpeg', '-y', '-v', 'error', '-ss', str(t), '-i', src,
         '-frames:v', '1', out])


def scene_still_times(scene):
    dur = float(scene['duration'])
    t1 = min(2.0, dur * 0.25)
    telops = scene.get('telops')
    if telops:
        t0 = float(telops[0].get('start', 0))
        t1e = float(telops[0].get('end', dur))
        t2 = min((t0 + t1e) / 2.0, dur - 0.1)
    else:
        t2 = min(7.0, dur - 0.5)
    if t2 <= t1:
        t2 = min(t1 + 1.0, dur - 0.1)
    return [round(t1, 2), round(t2, 2)]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    spec_path = os.path.abspath(argv[1])
    spec_dir = os.path.dirname(spec_path)
    with open(spec_path, encoding='utf-8') as fh:
        spec = json.load(fh)

    def res(p):
        return p if os.path.isabs(p) else os.path.join(spec_dir, p)

    output = res(spec.get('output', 'video.mp4'))
    out_dir = os.path.dirname(output)
    stem = os.path.splitext(os.path.basename(output))[0]
    workdir = os.path.join(out_dir, f'{stem}_work')
    stills_dir = os.path.join(out_dir, 'stills')
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(stills_dir, exist_ok=True)
    font = spec.get('font', DEFAULT_FONT)
    fade_in = float(spec.get('fade_in', 0.7))
    fade_out = float(spec.get('fade_out', 1.4))
    scenes = spec['scenes']
    total = sum(float(s['duration']) for s in scenes)
    audio_mode = any(s.get('audio') for s in scenes)

    print(f"== {spec.get('title', stem)}: {len(scenes)} scenes, "
          f"target total {total:.2f}s, audio={'yes' if audio_mode else 'no'} ==")
    seg_infos = []
    for i, scene in enumerate(scenes, 1):
        seg, info = build_segment(scene, i, spec_dir, workdir, font,
                                  audio_mode)
        seg_infos.append(info)
        flag = 'OK' if info['ok'] else 'DURATION MISMATCH'
        print(f"scene {i:2d} ({info['name']}): target {info['target']:.2f}s "
              f"actual {info['actual']:.3f}s [{flag}]")
        print(f"    {info['note']}")
    bad = [i for i in seg_infos if not i['ok']]
    if bad:
        print('ERROR: segment duration mismatch in: '
              + ', '.join(i['name'] for i in bad))
        return 1

    # Concatenate (all segments share codec parameters).
    listfile = os.path.join(workdir, 'concat.txt')
    with open(listfile, 'w', encoding='utf-8') as fh:
        for info in seg_infos:
            fh.write(f"file '{info['path']}'\n")
    joined = os.path.join(workdir, 'joined.mp4')
    run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
         '-i', listfile, '-c', 'copy', joined])

    # Whole-video fades, applied once.
    if fade_in > 0 or fade_out > 0:
        vf = []
        af = []
        if fade_in > 0:
            vf.append(f'fade=t=in:st=0:d={fade_in}')
            af.append(f'afade=t=in:st=0:d={fade_in}')
        if fade_out > 0:
            st = total - fade_out
            vf.append(f'fade=t=out:st={st}:d={fade_out}')
            af.append(f'afade=t=out:st={st}:d={fade_out}')
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', joined,
               '-vf', ','.join(vf)]
        if audio_mode:
            cmd += ['-af', ','.join(af)]
        cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']
        if audio_mode:
            cmd += ['-c:a', 'aac', '-b:a', '128k']
        cmd += [output]
        run(cmd)
    else:
        run(['ffmpeg', '-y', '-v', 'error', '-i', joined, '-c', 'copy', output])

    # Verification stills: two per scene (from segments) + final-file frames.
    for i, (scene, info) in enumerate(zip(scenes, seg_infos), 1):
        for t in scene_still_times(scene):
            extract_still(info['path'], t,
                          os.path.join(stills_dir, f'scene{i:02d}_t{t}.png'))
    final_times = [total * f for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    final_times.append(total - 2.0)
    for t in final_times:
        extract_still(output, round(t, 2),
                      os.path.join(stills_dir, f'final_t{t:.1f}.png'))

    out_dur = media_duration(output)
    out_size = os.path.getsize(output)
    print('== summary ==')
    for i, info in enumerate(seg_infos, 1):
        print(f"  scene {i:2d} ({info['name']}): {info['actual']:.3f}s")
    print(f'  total: {out_dur:.3f}s (target {total:.2f}s)')
    print(f'  output: {output} ({out_size:,} bytes)')
    print(f'  stills: {stills_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
