#!/usr/bin/env python3
"""make_full.py — フルアニメ方式アニメ制作ツール

バージョン 2.1（2026-11-21）
  v2.1 の変更点:
    1. 中間ファイルの保存・再利用（キャッシュ）:
       正規化済み場面セグメントをキャッシュDirに保存し、再実行時は
       素材・秒数・音声・テロップが変わっていない場面を再利用する。
       変わった場面だけ作り直す。場面ごとに再利用/再生成をログに明示。
       キャッシュDirは既定で「<出力ファイル名>.cache」（出力の隣）。
       --cache-dir で変更、--no-cache で無効化できる。
    2. 連結の再エンコード削減:
       全セグメントのエンコード条件が同一なら concat demuxer の
       -c copy で連結する。コピー連結の結果を検証し、失敗したら
       再エンコード連結へ自動でフォールバックする。その前提として、
       セグメント音声を 48kHz ステレオに統一した（従来は音声素材が
       モノラルの場面と無音場面（ステレオ）で条件が揃っていなかった）。
    3. 検証の自動化と報告:
       完成後に総秒数（場面表合計との差 ±0.5 秒以内）・解像度
       1920x1080・音声ストリームの有無を ffprobe で確認し、最終
       ファイルの全デコードチェックを行う。結果は「検証リポート」
       として標準出力と <出力名>.verify.txt に残す。
  v2.0（2026-11-20）: フルアニメ方式を正式化（短編で実証した方式）。

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
  python3 make_full.py scene_spec.json [--cache-dir DIR] [--no-cache]

スペックの書式は README.md を参照。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

TOOL_VERSION = '2.1'
FRAME_W, FRAME_H, FPS = 1920, 1080, 30
DEFAULT_FONT = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc'
AUDIO_DELAY_MS = 1000
SEG_DUR_TOLERANCE = 0.15  # seconds (per-segment check, as in v2.0)
TOTAL_DUR_TOLERANCE = 0.5  # seconds (final verification)


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


# ---------------------------------------------------------------------------
# v2.1: segment cache
# ---------------------------------------------------------------------------

def _fingerprint(path):
    st = os.stat(path)
    return {'path': os.path.abspath(path), 'size': st.st_size,
            'mtime_ns': st.st_mtime_ns}


def scene_cache_key(scene, spec_dir, font, audio_mode):
    """Hash of everything that determines a segment's rendered content."""
    def res(p):
        return p if os.path.isabs(p) else os.path.join(spec_dir, p)

    payload = {
        'tool_version': TOOL_VERSION,
        'frame': [FRAME_W, FRAME_H, FPS],
        'font': font,
        'audio_mode': audio_mode,
        'audio_delay_ms': AUDIO_DELAY_MS,
        'segment_format': 'h264-1920x1080-30fps/aac-48k-stereo',
        'duration': float(scene['duration']),
        'video': _fingerprint(res(scene['video'])),
        'audio': (_fingerprint(res(scene['audio']))
                  if scene.get('audio') else None),
        'telops': scene.get('telops', []),
    }
    blob = json.dumps(payload, sort_keys=True,
                      ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:16]


def build_segment(scene, idx, spec_dir, build_dir, seg_out, font, audio_mode):
    """Build one scene segment to seg_out; returns info dict."""
    name = scene.get('name', f'scene{idx}')
    dur = float(scene['duration'])
    os.makedirs(build_dir, exist_ok=True)

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
        tfile = os.path.join(build_dir, f'telop_{j}.txt')
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

    cmd = ['ffmpeg', '-y', '-v', 'error', *inputs, '-filter_complex', fc,
           *maps, '-t', str(dur), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio_mode:
        # v2.1: force 48kHz stereo so every segment shares one encoding
        # signature (voice sources are mono; the silent-scene source is
        # stereo — without this, copy concat cannot be used safely).
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2']
    cmd += [seg_out]
    run(cmd)

    actual = media_duration(seg_out)
    ok = abs(actual - dur) <= SEG_DUR_TOLERANCE
    note = f'src {vw}x{vh}@{vfps:g}fps {src_dur:.2f}s'
    if looped:
        note += ' (looped to fill)'
    return dict(name=name, target=dur, actual=actual, ok=ok,
                note=note, path=seg_out)


def get_segment(scene, idx, spec_dir, cache_dir, font, audio_mode,
                use_cache):
    """Return (info, reused) — reusing a cached segment when possible."""
    name = scene.get('name', f'scene{idx}')
    dur = float(scene['duration'])
    if use_cache:
        key = scene_cache_key(scene, spec_dir, font, audio_mode)
        seg = os.path.join(cache_dir, f'seg{idx:02d}_{key}.mp4')
        meta_path = seg + '.json'
        if os.path.exists(seg) and os.path.exists(meta_path):
            try:
                with open(meta_path, encoding='utf-8') as fh:
                    meta = json.load(fh)
                actual = media_duration(seg)
                if abs(actual - dur) <= SEG_DUR_TOLERANCE:
                    info = dict(name=name, target=dur, actual=actual,
                                ok=True, note=meta.get('note', ''),
                                path=seg)
                    return info, True
                print(f'    cache segment duration off ({actual:.3f}s); '
                      'rebuilding')
            except Exception as exc:  # corrupt cache entry -> rebuild
                print(f'    cache entry unusable ({exc}); rebuilding')
        build_dir = os.path.join(cache_dir, f'build{idx:02d}')
        info = build_segment(scene, idx, spec_dir, build_dir, seg,
                             font, audio_mode)
        with open(meta_path, 'w', encoding='utf-8') as fh:
            json.dump({'note': info['note'], 'actual': info['actual'],
                       'key': key}, fh, ensure_ascii=False)
        return info, False
    build_dir = os.path.join(cache_dir, f'build{idx:02d}')
    seg = os.path.join(cache_dir, f'seg{idx:02d}.mp4')
    info = build_segment(scene, idx, spec_dir, build_dir, seg,
                         font, audio_mode)
    return info, False


# ---------------------------------------------------------------------------
# v2.1: concat with copy-first + re-encode fallback
# ---------------------------------------------------------------------------

def _stream_signature(path):
    """Encoding signature used to decide whether -c copy concat is safe."""
    info = probe(path)
    sig = []
    for s in info['streams']:
        if s.get('codec_type') == 'video':
            num, den = s.get('avg_frame_rate', '0/1').split('/')
            fps = round(float(num) / float(den), 3) if float(den) else 0.0
            sig.append(('v', s.get('codec_name'), int(s['width']),
                        int(s['height']), s.get('pix_fmt'), fps))
        elif s.get('codec_type') == 'audio':
            sig.append(('a', s.get('codec_name'),
                        int(s.get('sample_rate', 0)),
                        int(s.get('channels', 0))))
    return tuple(sig)


def concat_segments(seg_paths, workdir, total, audio_mode):
    """Join segments; returns (joined_path, method).

    Tries stream-copy concat first when all segments share one encoding
    signature, verifies the result, and falls back to a re-encode concat
    when the copy is impossible or fails verification.
    """
    listfile = os.path.join(workdir, 'concat.txt')
    with open(listfile, 'w', encoding='utf-8') as fh:
        for p in seg_paths:
            fh.write(f"file '{p}'\n")
    joined = os.path.join(workdir, 'joined.mp4')

    sigs = {_stream_signature(p) for p in seg_paths}
    if len(sigs) == 1:
        try:
            run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
                 '-i', listfile, '-c', 'copy', joined])
            dur = media_duration(joined)
            if abs(dur - total) <= TOTAL_DUR_TOLERANCE:
                return joined, f'copy (verified, {dur:.3f}s)'
            print(f'  copy concat duration off: {dur:.3f}s vs target '
                  f'{total:.2f}s; falling back to re-encode concat')
        except subprocess.CalledProcessError:
            print('  copy concat failed; falling back to re-encode concat')
    else:
        print('  segment encodings differ; using re-encode concat')

    cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
           '-i', listfile, '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if audio_mode:
        cmd += ['-c:a', 'aac', '-b:a', '128k']
    cmd += [joined]
    run(cmd)
    return joined, 're-encode'


# ---------------------------------------------------------------------------
# v2.1: automated verification report
# ---------------------------------------------------------------------------

def verify_output(output, total, audio_mode):
    """Check the finished file; returns (report_text, all_ok)."""
    lines = []
    checks = []

    def record(label, ok, detail):
        checks.append(ok)
        lines.append(f"  [{'OK' if ok else 'NG'}] {label}: {detail}")

    info = probe(output)
    dur = float(info['format']['duration'])
    record('総秒数', abs(dur - total) <= TOTAL_DUR_TOLERANCE,
           f'{dur:.3f}s（場面表合計 {total:.2f}s、許容差 '
           f'±{TOTAL_DUR_TOLERANCE}s）')

    vstreams = [s for s in info['streams'] if s.get('codec_type') == 'video']
    if vstreams:
        w, h = int(vstreams[0]['width']), int(vstreams[0]['height'])
        record('解像度', (w, h) == (FRAME_W, FRAME_H), f'{w}x{h}')
    else:
        record('解像度', False, '映像ストリームなし')

    astreams = [s for s in info['streams'] if s.get('codec_type') == 'audio']
    record('音声ストリーム', bool(astreams) == bool(audio_mode),
           f"{'あり' if astreams else 'なし'}"
           f"（期待: {'あり' if audio_mode else 'なし'}）")

    proc = subprocess.run(['ffmpeg', '-v', 'error', '-i', output,
                           '-f', 'null', '-'],
                          capture_output=True, text=True)
    decode_ok = proc.returncode == 0 and not proc.stderr.strip()
    record('全デコードチェック', decode_ok,
           'エラーなし' if decode_ok
           else (proc.stderr.strip().splitlines() or ['decode error'])[0])

    all_ok = all(checks)
    header = (f'== 検証リポート (make_full.py v{TOOL_VERSION}) ==\n'
              f'  対象: {output}\n')
    verdict = f"  判定: {'合格' if all_ok else '不合格'}"
    return header + '\n'.join(lines) + '\n' + verdict, all_ok


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='フルアニメ方式アニメ制作ツール (make_full.py)')
    parser.add_argument('spec', help='場面表JSONのパス')
    parser.add_argument('--cache-dir', default=None,
                        help='セグメントキャッシュの保存先'
                             '（既定: <出力名>.cache）')
    parser.add_argument('--no-cache', action='store_true',
                        help='キャッシュを使わず全場面を作り直す')
    args = parser.parse_args(argv)

    started = time.time()
    spec_path = os.path.abspath(args.spec)
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
    use_cache = not args.no_cache
    cache_dir = args.cache_dir or os.path.join(out_dir, f'{stem}.cache')
    if use_cache:
        os.makedirs(cache_dir, exist_ok=True)
    font = spec.get('font', DEFAULT_FONT)
    fade_in = float(spec.get('fade_in', 0.7))
    fade_out = float(spec.get('fade_out', 1.4))
    scenes = spec['scenes']
    total = sum(float(s['duration']) for s in scenes)
    audio_mode = any(s.get('audio') for s in scenes)

    print(f"== {spec.get('title', stem)}: {len(scenes)} scenes, "
          f"target total {total:.2f}s, audio={'yes' if audio_mode else 'no'} "
          f"(make_full.py v{TOOL_VERSION}, "
          f"cache={'on: ' + cache_dir if use_cache else 'off'}) ==")
    seg_infos = []
    n_reused = 0
    for i, scene in enumerate(scenes, 1):
        seg_dir = cache_dir if use_cache else workdir
        info, reused = get_segment(scene, i, spec_dir, seg_dir, font,
                                   audio_mode, use_cache)
        seg_infos.append(info)
        n_reused += 1 if reused else 0
        flag = 'OK' if info['ok'] else 'DURATION MISMATCH'
        origin = 'cache reuse' if reused else 'generated'
        print(f"scene {i:2d} ({info['name']}): target {info['target']:.2f}s "
              f"actual {info['actual']:.3f}s [{flag}] [{origin}]")
        print(f"    {info['note']}")
    bad = [i for i in seg_infos if not i['ok']]
    if bad:
        print('ERROR: segment duration mismatch in: '
              + ', '.join(i['name'] for i in bad))
        return 1
    print(f'  segments: {n_reused} reused, '
          f'{len(seg_infos) - n_reused} generated')

    # Concatenate (copy-first with verified fallback to re-encode).
    joined, method = concat_segments([i['path'] for i in seg_infos],
                                     workdir, total, audio_mode)
    print(f'  concat: {method}')

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
    print(f'  elapsed: {time.time() - started:.1f}s')

    # v2.1: automated verification + report file.
    report, all_ok = verify_output(output, total, audio_mode)
    print(report)
    report_path = os.path.splitext(output)[0] + '.verify.txt'
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write(report + '\n')
    print(f'  verify report: {report_path}')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
