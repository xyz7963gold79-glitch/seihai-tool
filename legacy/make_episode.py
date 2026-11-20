#!/usr/bin/env python3
"""make_episode.py — レイヤー方式（セル方式）リミテッドアニメ制作ツール

『星海鉄道カプセル便』第1話で実証した制作方式を一般化したもの。
場面表（スペックJSON）を書いて実行すると、1話分の完成動画が出力される。

方式（第1話の実証済み設定をそのまま踏襲）:
  - 背景プレート（人物を消した場面絵）の上に、緑背景の人物動画を
    クロマキー（similarity 0.12 / blend 0.06 + despill）で抜いて重ねる。
  - 人物動画はピンポン再生（順方向+逆方向）で場面長までループする。
  - グロー（光の明滅）は RGB(gbrp) 空間でスクリーン合成する。
    ※ YUV 空間で合成すると画面が紫に寄る不具合が第1話で確認されたため厳禁。
  - 音声は 1 秒遅延 + 無音パディングで場面長に統一（48kHz）。
  - 話全体のフェード（イン 0.7 秒 / アウト 1.4 秒）は連結後に一度だけ適用。

使い方:
  python3 make_episode.py episode_spec.json [--stills-only]

スペックの書式は README.md を参照。
"""
import hashlib
import json
import math
import os
import subprocess
import sys

FRAME_W, FRAME_H, FPS = 1920, 1080, 30
DEFAULT_FONT = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc'
KEY_SIMILARITY = 0.12
KEY_BLEND = 0.06
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
    """(width, height, fps, nb_frames) of the first video stream."""
    info = probe(path)
    for s in info['streams']:
        if s.get('codec_type') == 'video':
            num, den = s.get('avg_frame_rate', '0/1').split('/')
            fps = float(num) / float(den) if float(den) else 0.0
            if s.get('nb_frames'):
                nb = int(s['nb_frames'])
            else:
                nb = int(round(float(s.get('duration', 0)) * fps))
            return int(s['width']), int(s['height']), fps, nb
    raise ValueError(f'no video stream: {path}')


def measure_green(video):
    """Auto-measure the green-screen color from the video's corner pixels.

    Extracts one frame (t=1.0s) as raw RGB and averages 10x10 patches at the
    four corners (offset 4 px from the edges). Returns an int 0xRRGGBB.
    """
    w, h, _fps, _nb = video_info(video)
    raw = subprocess.run(
        ['ffmpeg', '-v', 'error', '-ss', '1.0', '-i', video,
         '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
        check=True, capture_output=True).stdout
    expected = w * h * 3
    if len(raw) < expected:
        raise ValueError(f'frame extraction too short for {video}')
    patch = 10
    off = 4
    corners = [(off, off), (w - off - patch, off),
               (off, h - off - patch), (w - off - patch, h - off - patch)]
    rs = gs = bs = cnt = 0
    for (x0, y0) in corners:
        for y in range(y0, y0 + patch):
            base = (y * w + x0) * 3
            for x in range(patch):
                i = base + x * 3
                rs += raw[i]
                gs += raw[i + 1]
                bs += raw[i + 2]
                cnt += 1
    r, g, b = round(rs / cnt), round(gs / cnt), round(bs / cnt)
    return (r << 16) | (g << 8) | b


def parse_color(value):
    """Accept 0xRRGGBB int, '#RRGGBB' or 'RRGGBB' string -> int."""
    if isinstance(value, int):
        return value
    s = str(value).strip().lstrip('#')
    if s.lower().startswith('0x'):
        s = s[2:]
    return int(s, 16)


def layer_scale(layer, video_w, video_h):
    """Resolve a layer's scale factor k.

    Priority: explicit 'scale' > 'perspective' computation.
    Perspective model: apparent size is proportional to how far the
    character's feet/base sit below the horizon line.
        k = ref_scale * (feet_y - horizon_y) / (ref_feet_y - horizon_y)
    i.e. a character whose base is at ref_feet_y appears at ref_scale;
    farther away (base higher, closer to the horizon) -> smaller.
    """
    if 'scale' in layer:
        return float(layer['scale'])
    p = layer.get('perspective')
    if p:
        horizon = float(p.get('horizon_y', 0))
        feet = float(p['feet_y'])
        ref_feet = float(p['ref_feet_y'])
        ref_scale = float(p['ref_scale'])
        if ref_feet == horizon:
            raise ValueError('perspective: ref_feet_y must differ from horizon_y')
        return ref_scale * (feet - horizon) / (ref_feet - horizon)
    raise ValueError(f"layer needs 'scale' or 'perspective': {layer}")


def make_glow(workdir, cx, cy, r, color):
    """3-second looping warm radial glow video (screen-blend source).

    Cached in workdir by parameter hash. Pulse curve and falloff match
    Episode 1: intensity 0.55 + 0.30*sin(2*pi*i/84), quadratic falloff.
    """
    key = hashlib.sha1(f'{cx},{cy},{r},{tuple(color)}'.encode()).hexdigest()[:12]
    out = os.path.join(workdir, f'glow_{key}.mp4')
    if os.path.exists(out):
        return out
    import numpy as np
    H, W = FRAME_H, FRAME_W
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    fall = np.clip(1 - d / r, 0, 1) ** 2
    col = np.array(color, dtype=np.float32)
    frames = 90
    proc = subprocess.Popen(
        ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
         '-s', f'{W}x{H}', '-r', '30', '-i', '-', '-c:v', 'libx264',
         '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
    for i in range(frames):
        inten = 0.55 + 0.30 * math.sin(2 * math.pi * i / 84.0)
        img = (fall[:, :, None] * col[None, None, :] * inten).astype(np.uint8)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError('glow generation failed')
    return out


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


def build_segment(scene, idx, spec_dir, workdir, font):
    """Build one scene segment; returns (seg_path, info dict)."""
    name = scene.get('name', f'scene{idx}')
    dur = float(scene['duration'])
    sdir = os.path.join(workdir, f'scene{idx:02d}')
    os.makedirs(sdir, exist_ok=True)

    def res(p):
        return p if os.path.isabs(p) else os.path.join(spec_dir, p)

    bg = res(scene['background'])
    inputs = ['-loop', '1', '-framerate', str(FPS), '-i', bg]
    parts = ['[0:v]scale=1920:1080,setsar=1[bg0]']
    cur = 'bg0'
    n = 1
    layer_infos = []
    for layer in scene.get('layers', []):
        vpath = res(layer['video'])
        vw, vh, _fps, nb = video_info(vpath)
        if 'green' in layer and layer['green'] is not None:
            green, gsrc = parse_color(layer['green']), 'spec'
        else:
            green, gsrc = measure_green(vpath), 'auto'
        k = layer_scale(layer, vw, vh)
        w, h = int(vw * k), int(vh * k)
        pp = 2 * nb  # ping-pong frame count at source fps
        ox, oy = int(layer['x']), int(layer['y'])
        inputs += ['-i', vpath]
        parts.append(
            f'[{n}:v]chromakey=0x{green:06X}:{KEY_SIMILARITY}:{KEY_BLEND},'
            f'despill=type=green,split[a{n}][b{n}];[b{n}]reverse[r{n}];'
            f'[a{n}][r{n}]concat=n=2:v=1:a=0,loop=loop=-1:size={pp},'
            f'scale={w}:{h},setsar=1,fps={FPS}[L{n}]')
        parts.append(f'[{cur}][L{n}]overlay={ox}:{oy}[ov{n}]')
        cur = f'ov{n}'
        layer_infos.append(f"layer{n}: green=0x{green:06X}({gsrc}) k={k:.3f} "
                           f'@({ox},{oy}) {os.path.basename(vpath)}')
        n += 1

    glow = scene.get('glow')
    if glow:
        gpath = make_glow(workdir, int(glow['x']), int(glow['y']),
                          int(glow['radius']),
                          glow.get('color', [255, 214, 140]))
        opacity = float(glow.get('opacity', 0.55))
        inputs += ['-stream_loop', '-1', '-i', gpath]
        # RGB (gbrp) blend — never YUV (purple-tint bug in Ep.1's first build).
        parts.append(f'[{cur}]format=gbrp[cug];[{n}:v]format=gbrp[glg];'
                     f'[cug][glg]blend=all_mode=screen:all_opacity={opacity}[gw]')
        cur = 'gw'
        n += 1

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

    audio = scene.get('audio')
    if audio:
        inputs += ['-i', res(audio)]
        fc += (f';[{n}:a]aresample=48000,'
               f'adelay={AUDIO_DELAY_MS}|{AUDIO_DELAY_MS},apad[a]')
    else:
        inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        fc += f';[{n}:a]aresample=48000,apad[a]'

    seg = os.path.join(workdir, f'seg{idx:02d}.mp4')
    run(['ffmpeg', '-y', '-v', 'error', *inputs, '-filter_complex', fc,
         '-map', '[v]', '-map', '[a]', '-t', str(dur),
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-b:a', '128k', seg])
    actual = media_duration(seg)
    ok = abs(actual - dur) <= SEG_DUR_TOLERANCE
    info = dict(name=name, target=dur, actual=actual, ok=ok,
                layers=layer_infos, path=seg)
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

    output = res(spec.get('output', 'episode.mp4'))
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

    print(f"== {spec.get('title', stem)}: {len(scenes)} scenes, "
          f"target total {total:.2f}s ==")
    seg_infos = []
    for i, scene in enumerate(scenes, 1):
        seg, info = build_segment(scene, i, spec_dir, workdir, font)
        seg_infos.append(info)
        flag = 'OK' if info['ok'] else 'DURATION MISMATCH'
        print(f"scene {i:2d} ({info['name']}): target {info['target']:.2f}s "
              f"actual {info['actual']:.3f}s [{flag}]")
        for li in info['layers']:
            print(f'    {li}')
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

    # Episode fades, applied once.
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
        run(['ffmpeg', '-y', '-v', 'error', '-i', joined,
             '-vf', ','.join(vf), '-af', ','.join(af),
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-b:a', '128k', output])
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
