# 音乐歌单下载器

按歌单 ID 批量保存音频、原文 LRC 歌词和封面。脚本会把歌名、歌手、封面和歌词写入 MP3 或 FLAC 标签，播放器可以直接从音频读取这些信息。

## 快速开始

在项目目录运行。使用 `uv` 时，依赖会自动从 `requirements.txt` 安装：

```bash
uv run --with-requirements requirements.txt music_downloader.py 3778678
```

也可以使用 Python 3 虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python music_downloader.py 3778678
```

将 `3778678` 换成你的歌单 ID。歌单分享链接带有 `id=` 时，填这个参数对应的纯数字即可。省略歌单 ID 会使用示例歌单 `3778678`。

## 常用命令

以下示例假设已激活虚拟环境。使用 `uv` 时，把每行开头的 `python` 换成 `uv run --with-requirements requirements.txt`。

```bash
# 请求无损音质，并保存到指定目录
python music_downloader.py 3778678 --bitrate 999 --save-dir ./music

# 为指定歌单中已经下载的歌曲补歌词和封面
python music_downloader.py 3778678 --save-dir ./music --lyrics-only --cover-only

# 查看全部参数
python music_downloader.py --help
```

单独使用 `--lyrics-only` 或 `--cover-only` 可只补一种资源。两个模式都只处理已经下载的歌曲，并将对应资源一并补入受支持的音频标签。

## 参数

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `playlist_id` | `3778678` | 歌单 ID，纯数字 |
| `-o` / `--save-dir` | `./music` | 输出目录，相对于运行命令的位置 |
| `-s` / `--source` | `netease` | 音源，歌单和曲目能否解析取决于接口支持 |
| `-b` / `--bitrate` | `320` | 可选 `128`、`192`、`320`、`740`、`999`，后两档为无损请求档位，实际音质由接口返回决定 |
| `-d` / `--delay` | `6.1` | API 请求间隔，单位为秒，最小值为 `5` |
| `-t` / `--timeout` | `30` | 网络请求超时，单位为秒 |
| `--lyrics-only` | 关闭 | 仅给已有音频补歌词 |
| `--cover-only` | 关闭 | 仅给已有音频补封面 |

## 输出和播放器

同一首歌使用相同的主文件名，封面扩展名由实际图片格式决定。

```text
music/
├── songs/
│   └── 江南 - 林俊杰.mp3
├── lyrics/
│   └── 江南 - 林俊杰.lrc
└── covers/
    └── 江南 - 林俊杰.jpg
```

- 歌名和歌手从歌单字段分别写入音频标签。
- MP3 内嵌封面和歌词。LRC 有时间戳时，还会写入同步歌词标签。FLAC 使用原生封面块和保留时间戳的 `LYRICS` 标签。
- 其他音频格式仍会保存独立的封面和 LRC。能否显示同步歌词取决于播放器对标签或外部 LRC 的支持。
- 已导入 MyGo Music 的歌曲更新标签后，在 **设置 → 本地文件 → 重新扫描** 里刷新曲库。

重复运行时，已有音频、歌词和封面文件会跳过下载；已有音频仍可补写缺少的标签。单首失败会继续处理后续歌曲，运行结束时会汇总失败数量。

音源接口由 [GD 音乐台](https://music.gdstudio.org/) 提供。
