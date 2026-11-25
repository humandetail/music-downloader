#!/usr/bin/env python3
"""批量下载歌单。音源接口由 GD 音乐台（music.gdstudio.xyz）提供。"""

import argparse
from http.client import HTTPException
import json
import math
from pathlib import Path
import re
import shutil
import sys
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


try:
    import mutagen
    from mutagen import flac, id3
except ModuleNotFoundError as exc:
    if exc.name != "mutagen":
        raise
    mutagen = None


API_BASE = "https://music-api.gdstudio.xyz/api.php"
HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_ERRORS = (OSError, HTTPException, ValueError)
AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm"}
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
COVER_MIMES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}
TAGGABLE_SUFFIXES = {".mp3", ".flac"}
LRC_TIMESTAMP = re.compile(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]")
MAX_COVER_BYTES = 10 * 1024**2
AUDIO_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/webm": ".webm",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="按歌单 ID 下载歌曲。音源接口由 GD 音乐台提供。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("playlist_id", nargs="?", default="3778678", help="歌单 ID")
    parser.add_argument("-o", "--save-dir", type=Path, default=Path("music"), help="保存目录，包含 songs、lyrics 和 covers 子目录")
    parser.add_argument("-s", "--source", default="netease", help="音源")
    parser.add_argument("-b", "--bitrate", choices=("128", "192", "320", "740", "999"),
                        default="320", help="音质，740 和 999 为无损档位")
    parser.add_argument("-d", "--delay", type=float, default=6.1,
                        help="API 请求间隔（秒，至少 5 秒）")
    parser.add_argument("-t", "--timeout", type=float, default=30, help="网络超时（秒）")
    parser.add_argument("--lyrics-only", action="store_true", help="仅为已保存的音频补齐 LRC")
    parser.add_argument("--cover-only", action="store_true", help="仅为已保存的音频补齐封面")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+", args.playlist_id):
        parser.error("歌单 ID 必须为纯数字")
    if not math.isfinite(args.delay) or args.delay < 5:
        parser.error("--delay 必须为至少 5 秒的有限数值")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout 必须为大于 0 的有限数值")
    return args


def fetch_playlist(data):
    """兼容列表、songs/data 包装和网易云 playlist.tracks。"""
    while isinstance(data, dict):
        for key in ("playlist", "tracks", "songs", "data"):
            if isinstance(data.get(key), (dict, list)):
                data = data[key]
                break
        else:
            raise ValueError("响应中没有曲目列表，请检查歌单 ID、公开状态和音源")
    if not isinstance(data, list):
        raise ValueError("歌单响应格式不正确")
    return data


def track_details(song, index):
    title = song.get("name") or song.get("title") or f"track_{index}"
    artists = song.get("artist") or song.get("artists") or song.get("ar") or ""
    if isinstance(artists, list):
        artists = ", ".join(str(a.get("name") or "") if isinstance(a, dict) else str(a)
                            for a in artists)
    song_id = song.get("id") or song.get("url_id") or song.get("songmid")
    return str(title), str(artists), song_id


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    # 常见文件系统按字节限制文件名长度，预留扩展名空间。
    name = name.encode("utf-8")[:230].decode("utf-8", errors="ignore").strip(" .")
    if re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])", name.split(".")[0]):
        name = "_" + name
    return name or "untitled"


def download_file(url, folder, name, timeout):
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("歌曲链接必须使用 HTTP 或 HTTPS")
    with urlopen(Request(url, headers=HEADERS), timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        if content_type.startswith("text/") or content_type in {"application/json", "application/xml"}:
            raise ValueError(f"下载地址返回了 {content_type}")
        suffix = AUDIO_TYPES.get(content_type)
        if suffix is None:
            suffix = Path(urlsplit(response.url).path).suffix.lower()

        prefix = response.read(16)
        if not prefix:
            raise ValueError("下载内容为空")
        if prefix.startswith(b"fLaC"):
            suffix = ".flac"
        elif prefix.startswith(b"ID3"):
            suffix = ".mp3"
        if suffix not in AUDIO_SUFFIXES:
            raise ValueError("无法识别音频格式")

        path = folder / (name + suffix)
        if path.exists():
            return path, None

        temp_path = None
        try:
            with NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(prefix)
                shutil.copyfileobj(response, temp)
                size = temp.tell()
            expected_size = response.headers.get("Content-Length")
            if expected_size is not None and size != int(expected_size):
                raise ValueError(f"下载不完整（{size}/{expected_size} 字节）")
            if path.exists():
                return path, None
            temp_path.replace(path)
            return path, size
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def save_sidecar(path, data):
    temp_path = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(data)
        if not path.exists():
            temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def download_cover(url, timeout):
    if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("封面链接必须使用 HTTP 或 HTTPS")
    with urlopen(Request(url, headers=HEADERS), timeout=timeout) as response:
        image = response.read(MAX_COVER_BYTES + 1)
    if len(image) > MAX_COVER_BYTES:
        raise ValueError("封面超过 10 MiB")
    if image.startswith(b"\xff\xd8\xff"):
        return ".jpg", image
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", image
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return ".webp", image
    if image.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", image
    raise ValueError("封面响应不是可识别的图片")


def synced_lyrics(lrc):
    offset_match = re.search(r"(?im)^\[offset:\s*([+-]?\d+)\s*\]", lrc)
    offset = int(offset_match[1]) if offset_match else 0
    lyrics = []
    for line in lrc.splitlines():
        timestamps = []
        while (match := LRC_TIMESTAMP.match(line)) is not None:
            minute, second, fraction = match.groups(default="")
            time_ms = int(minute) * 60_000 + int(second) * 1_000 + int(fraction.ljust(3, "0"))
            timestamps.append(max(0, time_ms + offset))
            line = line[match.end():]
        lyrics.extend((line.strip(), time_ms) for time_ms in timestamps)
    return sorted(lyrics, key=lambda lyric: lyric[1])


def update_mp3_tags(path, title, artist, cover_path, lrc):
    try:
        tags = id3.ID3(path, translate=False)
    except id3.ID3NoHeaderError:
        tags = id3.ID3()
    current = (str(tags.get("TIT2", "")), str(tags.get("TPE1", "")))
    reversed_names = current == (artist, title) and title != artist
    changed = False
    for frame, value, previous in zip((id3.TIT2, id3.TPE1), (title, artist), current):
        if value and (not previous.strip() or reversed_names):
            tags.setall(frame.__name__, [frame(encoding=1, text=[value])])
            changed = True
    if cover_path and not any(picture.type == 3 for picture in tags.getall("APIC")):
        picture = id3.APIC(encoding=1, mime=COVER_MIMES[cover_path.suffix.lower()],
                           type=3, desc="Front cover", data=cover_path.read_bytes())
        while picture.HashKey in tags:
            picture.salt += " "
        tags.add(picture)
        changed = True
    if lrc and not tags.getall("USLT"):
        tags.add(id3.USLT(encoding=1, lang="und", desc="", text=lrc))
        changed = True
    if lrc and not tags.getall("SYLT") and (timed := synced_lyrics(lrc)):
        tags.add(id3.SYLT(encoding=1, lang="und", format=2, type=1, desc="", text=timed))
        changed = True
    if changed:
        version = 3 if tags.version[:2] == (2, 3) else 4
        return lambda target: tags.save(target, v2_version=version, v1=1)
    return None


def update_flac_tags(path, title, artist, cover_path, lrc):
    audio = flac.FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    current = tuple(next(iter(audio.get(key, [])), "") for key in ("title", "artist"))
    reversed_names = current == (artist, title) and title != artist
    changed = False
    for key, value, previous in zip(("title", "artist"), (title, artist), current):
        if value and (not previous.strip() or reversed_names):
            audio[key] = [value]
            changed = True
    if cover_path and not any(picture.type == 3 for picture in audio.pictures):
        picture = flac.Picture()
        picture.type = 3
        picture.mime = COVER_MIMES[cover_path.suffix.lower()]
        picture.desc = "Front cover"
        picture.data = cover_path.read_bytes()
        audio.add_picture(picture)
        changed = True
    if lrc and not (audio.get("lyrics") or audio.get("syncedlyrics") or audio.get("unsyncedlyrics")):
        audio["lyrics"] = [lrc]
        changed = True
    return audio.save if changed else None


def write_audio_tags(path, title, artist, cover_path, lrc):
    suffix = path.suffix.lower()
    if suffix not in TAGGABLE_SUFFIXES:
        return False
    original = path.stat()
    prepare = update_mp3_tags if suffix == ".mp3" else update_flac_tags
    save = prepare(path, title, artist, cover_path, lrc)
    if save is None:
        return False

    with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        shutil.copy2(path, temp_path)
        save(temp_path)
        current = path.stat()
        if (current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
            original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns
        ):
            raise OSError("音频在写入标签期间已发生变化，请重试")
        temp_path.replace(path)
        return True
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    args = parse_args()
    if mutagen is None:
        print("缺少 mutagen。请先运行 python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    next_request = 0.0
    metadata_only = args.lyrics_only or args.cover_only
    want_lyrics = not metadata_only or args.lyrics_only
    want_cover = not metadata_only or args.cover_only

    def api_request(kind, item_id, **params):
        nonlocal next_request
        delay = next_request - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        query = urlencode(dict(types=kind, id=item_id, source=args.source, **params))
        next_request = time.monotonic() + args.delay
        with urlopen(Request(f"{API_BASE}?{query}", headers=HEADERS), timeout=args.timeout) as response:
            return json.load(response)

    print(f"正在获取歌单 {args.playlist_id} …")
    try:
        songs = fetch_playlist(api_request("playlist", args.playlist_id))
        if not songs:
            raise ValueError("歌单为空")
        songs_dir = args.save_dir / "songs"
        lyrics_dir = args.save_dir / "lyrics"
        covers_dir = args.save_dir / "covers"
        for directory in (songs_dir, lyrics_dir, covers_dir):
            directory.mkdir(parents=True, exist_ok=True)
    except REQUEST_ERRORS as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 1

    total = len(songs)
    print(f"共 {total} 首，音质 {args.bitrate}，API 请求间隔 {args.delay:g} 秒")
    success = fail = skip = 0
    lyric_success = lyric_fail = lyric_missing = lyric_skip = 0
    cover_success = cover_fail = cover_missing = cover_skip = 0
    tag_success = tag_fail = tag_unsupported = 0

    for index, song in enumerate(songs, 1):
        song = song if isinstance(song, dict) else {}
        title, artist, song_id = track_details(song, index)
        name = f"{title} - {artist}" if artist else title
        print(f"[{index}/{total}] {name}")
        if not song_id:
            print("    缺少歌曲 ID，跳过")
            skip += 1
            continue

        filename = sanitize_filename(name)
        if metadata_only:
            # 补歌词或封面时也能识别之前两种目录布局的音频。
            audio_dirs = (songs_dir, args.save_dir, args.save_dir / filename)
            audio_paths = [directory / (filename + suffix)
                           for directory in dict.fromkeys(audio_dirs)
                           for suffix in sorted(AUDIO_SUFFIXES)
                           if (directory / (filename + suffix)).is_file()]
            if not audio_paths:
                skip += 1
                continue
        else:
            try:
                info = api_request("url", song_id, br=args.bitrate)
                url = info.get("url") if isinstance(info, dict) else None
                if not isinstance(url, str) or not url:
                    raise ValueError("接口未返回可用的歌曲链接")
                path, size = download_file(url, songs_dir, filename, args.timeout)
                audio_paths = [path]
            except REQUEST_ERRORS as exc:
                print(f"    下载失败：{exc}", file=sys.stderr)
                fail += 1
                continue

            if size is None:
                print("    音频已存在，跳过")
                skip += 1
            else:
                print(f"    下载完成（{size / 1024**2:.2f} MiB）→ {path.name}")
                success += 1

        cover_path = None
        if want_cover:
            cover_path = next((covers_dir / (filename + suffix) for suffix in COVER_SUFFIXES
                               if (covers_dir / (filename + suffix)).is_file()), None)
            if cover_path is not None:
                cover_skip += 1
            else:
                try:
                    album = song.get("al") or song.get("album")
                    cover_url = album.get("picUrl") if isinstance(album, dict) else None
                    if not cover_url and song.get("pic_id"):
                        info = api_request("pic", song["pic_id"], size=500)
                        if not isinstance(info, dict) or "url" not in info:
                            raise ValueError("封面接口响应格式不正确")
                        cover_url = info["url"]
                    if cover_url:
                        suffix, image = download_cover(cover_url, args.timeout)
                        target = covers_dir / (filename + suffix)
                        save_sidecar(target, image)
                        cover_path = target
                        print(f"    封面已保存 → {cover_path.name}")
                        cover_success += 1
                    else:
                        print("    接口暂无封面")
                        cover_missing += 1
                except REQUEST_ERRORS as exc:
                    print(f"    封面获取失败：{exc}", file=sys.stderr)
                    cover_fail += 1

        lrc_path = lyrics_dir / (filename + ".lrc")
        if want_lyrics:
            if lrc_path.exists():
                lyric_skip += 1
            else:
                try:
                    info = api_request("lyric", song.get("lyric_id") or song_id)
                    if not isinstance(info, dict) or "lyric" not in info:
                        raise ValueError("歌词接口响应格式不正确")
                    lyrics = info["lyric"]
                    if isinstance(lyrics, str) and lyrics.strip():
                        save_sidecar(lrc_path, (lyrics.rstrip("\r\n") + "\n").encode("utf-8"))
                        print(f"    歌词已保存 → {lrc_path.name}")
                        lyric_success += 1
                    else:
                        print("    接口暂无歌词")
                        lyric_missing += 1
                except REQUEST_ERRORS as exc:
                    print(f"    歌词获取失败：{exc}", file=sys.stderr)
                    lyric_fail += 1

        try:
            lrc = lrc_path.read_text(encoding="utf-8-sig") if want_lyrics and lrc_path.is_file() else ""
        except REQUEST_ERRORS as exc:
            print(f"    读取本地歌词失败：{exc}", file=sys.stderr)
            tag_fail += 1
            lrc = ""
        for path in audio_paths:
            if path.suffix.lower() not in TAGGABLE_SUFFIXES:
                tag_unsupported += 1
                continue
            try:
                if write_audio_tags(path, title, artist, cover_path, lrc.strip()):
                    tag_success += 1
                    print(f"    已写入音频标签 → {path.name}")
            except REQUEST_ERRORS + (mutagen.MutagenError,) as exc:
                print(f"    标签写入失败（{path.name}）：{exc}", file=sys.stderr)
                tag_fail += 1

    if metadata_only:
        print(f"未匹配到音频或缺少歌曲 ID：{skip} 首")
    else:
        print(f"音频。成功 {success}，失败 {fail}，跳过 {skip}，总计 {total}")
    if want_lyrics:
        print(f"歌词。保存 {lyric_success}，已有 {lyric_skip}，暂无 {lyric_missing}，失败 {lyric_fail}")
    if want_cover:
        print(f"封面。保存 {cover_success}，已有 {cover_skip}，暂无 {cover_missing}，失败 {cover_fail}")
    print(f"音频标签。更新 {tag_success}，格式暂不支持 {tag_unsupported}，失败 {tag_fail}")
    return int(fail > 0 or lyric_fail > 0 or cover_fail > 0 or tag_fail > 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n下载已中断", file=sys.stderr)
        sys.exit(130)
