# NotebookLM YouTube Subtitle Formatter

This tool formats YouTube subtitles into clean Markdown for Google NotebookLM.

## Purpose

NotebookLM reads input files as Markdown.

Raw subtitle files cause three problems:
1. Short lines fragment sentences into small parts.
2. Missing blank lines merge all text into one large paragraph.
3. YouTube uses `>>` to mark speaker changes. Markdown parses `>>` as blockquotes and removes them.

This tool solves these problems:
- Downloads subtitles directly from YouTube URLs with yt-dlp.
- Preserves speaker turn markers.
- Joins broken sentence wraps into clear paragraphs.
- Formats timestamps to minutes and seconds for the first hour.
- Gives you both a command line interface and a local web user interface.

## Requirements

Install Python 3.8 or newer.

Install yt-dlp:

```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Web User Interface

Start the local web server:

```bash
python3 format_subtitles.py --serve
```

Open your browser to `http://localhost:8000`.

### 2. Command Line Interface

Format subtitles from a YouTube URL:

```bash
python3 format_subtitles.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Save the raw subtitle file to the `subtitles/` folder:

```bash
python3 format_subtitles.py "https://www.youtube.com/watch?v=VIDEO_ID" --keep-raw
```

Format a local subtitle file:

```bash
python3 format_subtitles.py transcript.srt
```

## Options

| Flag | Description |
| --- | --- |
| `--serve [PORT]` | Start the web server. Default port is 8000. |
| `-o, --output PATH` | Set the output file path. |
| `--lang CODE` | Set the subtitle language code. Default is `en`. |
| `--keep-raw` | Save the raw subtitle file into the `subtitles/` folder. |
| `--raw-dir DIR` | Set the folder for raw subtitle files. Default is `subtitles`. |
| `--speaker-mode MODE` | Set marker style: `dash`, `chevron`, `bullet`, `none`, `alternate`, `generic`. |
| `--no-heal` | Disable sentence healing. |
| `--no-timestamps` | Remove timestamps from the output. |

## License

MIT-0 License
