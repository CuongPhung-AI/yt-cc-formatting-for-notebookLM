#!/usr/bin/env python3
"""
NotebookLM YouTube Subtitle & Transcript Formatter
Converts YouTube subtitle exports (raw TXT, SRT, VTT, SBV, and YouTube copy-pastes)
or fetches subtitles directly from YouTube URLs via yt-dlp, transforming them into
clean, structured Markdown and text specifically optimized for Google NotebookLM.

Why this is needed:
  - NotebookLM parses text documents as CommonMark / Markdown.
  - Raw subtitle files lack double line breaks (\\n\\n), causing NotebookLM to collapse
    all text into a single gigantic paragraph.
  - YouTube captions use '>> ' for speaker changes, which Markdown parses as blockquotes
    and strips, losing speaker separation.
  - YouTube subtitles hard-wrap at 35-40 characters, fragmenting sentences.

This tool solves all of the above and produces structured Markdown with:
  - Direct YouTube URL downloading via yt-dlp (no manual downsub.com downloads needed)
  - Prioritizes manual creator subtitles, falling back to auto-generated ASR captions
  - Preserved paragraphs and turns (\\n\\n)
  - Clear speaker turn markers (em-dash '— ', chevron '» ', alternating speaker names, or generic)
  - Rejoined sentence wraps and continuation healing
  - Section headers (## Part 1, ## [05:00], etc.) for NotebookLM outline indexing
  - Full support for direct URLs, TXT, SRT, VTT, SBV, and YouTube transcript copy-pastes.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import List, Dict, Optional, Tuple, Any

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def is_youtube_url(source: str) -> bool:
    """Determines if the given input string is a YouTube URL or video ID."""
    if not source:
        return False
    yt_patterns = [
        r'(https?://)?(www\.|m\.)?youtube\.com/watch\?',
        r'(https?://)?(www\.|m\.)?youtube\.com/shorts/',
        r'(https?://)?(www\.|m\.)?youtube\.com/live/',
        r'(https?://)?(www\.|m\.)?youtube\.com/embed/',
        r'(https?://)?youtu\.be/',
    ]
    if any(re.search(p, source, re.IGNORECASE) for p in yt_patterns):
        return True
    # If not a local file on disk, check if it matches an 11-char YouTube video ID
    if not os.path.exists(source) and re.match(r'^[a-zA-Z0-9_-]{11}$', source):
        return True
    return False


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be safely used as a filename."""
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "youtube_transcript"


def find_matching_track(tracks_dict: Dict[str, List[Dict]], requested_lang: str) -> Optional[Tuple[str, List[Dict]]]:
    """Finds the best matching subtitle track for the requested language."""
    if not tracks_dict:
        return None
    base_lang = requested_lang.split('-')[0].lower()

    # 1. First priority: native/original non-translated track (e.g. 'en-orig')
    orig_code = f"{base_lang}-orig"
    for code, formats in tracks_dict.items():
        if code.lower() == orig_code:
            return code, formats

    # 2. Exact match (e.g. 'en')
    for code, formats in tracks_dict.items():
        if code.lower() == requested_lang.lower():
            return code, formats

    # 3. Base language match (e.g. 'en')
    for code, formats in tracks_dict.items():
        if code.lower() == base_lang:
            return code, formats

    # 4. Prefix match (e.g. 'en-US', 'en-GB')
    for code, formats in tracks_dict.items():
        if code.lower().startswith(f"{base_lang}-"):
            return code, formats
    return None


def fetch_youtube_subtitles(
    url: str,
    lang: str = "en",
    keep_raw: bool = False,
    raw_path: Optional[str] = None,
    raw_dir: str = "subtitles"
) -> Dict[str, Any]:
    """
    Downloads subtitles from a YouTube URL via yt-dlp Python API.
    Prioritizes manual creator subtitles first, falling back to auto-generated captions.
    Filters out on-the-fly auto-translations to avoid YouTube HTTP 429 rate limits.
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is required to download subtitles directly from YouTube URLs. "
            "Please install it using: pip install yt-dlp"
        )

    print(f"Fetching subtitle metadata from YouTube: {url} ...")
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise RuntimeError(f"Error fetching YouTube video information: {e}")

        video_title = info.get('title') or "YouTube Video"
        manual_subs = info.get('subtitles') or {}
        auto_subs = info.get('automatic_captions') or {}

        # 1. Try manual subtitles first
        match = find_matching_track(manual_subs, lang)
        is_auto = False
        track_type = "manual"

        # 2. Fall back to auto-generated captions
        if not match:
            match = find_matching_track(auto_subs, lang)
            is_auto = True
            track_type = "auto-generated"

        if not match:
            avail_manual = ", ".join(manual_subs.keys()) if manual_subs else "None"
            avail_auto = ", ".join(list(auto_subs.keys())[:15]) if auto_subs else "None"
            raise ValueError(
                f"No subtitles found for language '{lang}'.\n"
                f"  Available manual languages: {avail_manual}\n"
                f"  Available auto-caption languages: {avail_auto}\n"
                f"Tip: Pass --lang with one of the available language codes."
            )

        chosen_lang, formats = match
        print(f"Found {track_type} subtitle track: '{chosen_lang}' for '{video_title}'.")

        # Exclude auto-translated URLs ('tlang=') which cause YouTube HTTP 429 Too Many Requests
        native_formats = [f for f in formats if 'tlang=' not in f.get('url', '')]
        candidate_formats = native_formats if native_formats else formats

        # Prioritize formats: srt (cleanest timedtext without rolling line duplication), vtt, srv3, json3
        preferred_exts = ['srt', 'vtt', 'srv3', 'json3']
        chosen_fmt = None
        for ext in preferred_exts:
            for fmt in candidate_formats:
                if fmt.get('ext') == ext:
                    chosen_fmt = fmt
                    break
            if chosen_fmt:
                break
        if not chosen_fmt:
            chosen_fmt = candidate_formats[0]

        fmt_ext = chosen_fmt.get('ext', 'vtt')
        try:
            resp = ydl.urlopen(chosen_fmt['url'])
            content = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            raise RuntimeError(f"Error downloading subtitle stream: {e}")

        sanitized_title = sanitize_filename(video_title)
        raw_target = None
        if keep_raw:
            if raw_path:
                raw_target = raw_path
                parent_dir = os.path.dirname(raw_target)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
            else:
                os.makedirs(raw_dir, exist_ok=True)
                raw_target = os.path.join(raw_dir, f"{sanitized_title}.{chosen_lang}.{fmt_ext}")

            with open(raw_target, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Saved raw subtitle file to: {raw_target}")

        return {
            'title': video_title,
            'sanitized_title': sanitized_title,
            'content': content,
            'format': fmt_ext,
            'lang': chosen_lang,
            'is_auto': is_auto,
            'raw_file': raw_target
        }


def strip_html_tags(text: str) -> str:
    """Strips HTML/VTT tags like <v Speaker>, <c>, </c>, <00:01:23.456>."""
    return re.sub(r'<[^>]+>', '', text)


def clean_sound_tags(text: str) -> str:
    """Removes sound and action bracket cues like [laughter], [screaming], [music]."""
    return re.sub(r'\[[a-zA-Z0-9_\s\xa0]+\]', '', text).strip()


def split_speaker_turns(text: str, initial_marker: bool = False) -> List[Dict[str, Any]]:
    """
    Splits cue text when speaker change markers (>> or &gt;&gt; or > ) occur within the text.
    Returns list of dicts: [{'text': '...', 'speaker_marker': bool}]
    """
    tokens = re.split(r'(?:^|\s+)(?:>>|&gt;&gt;|>)\s*', text)
    result = []
    starts_with_marker = bool(re.match(r'^(?:>>|&gt;&gt;|>)\s*', text))
    for idx, token in enumerate(tokens):
        token = token.strip()
        if not token:
            continue
        marker = (initial_marker or starts_with_marker) if idx == 0 else True
        result.append({'text': token, 'speaker_marker': marker})
    return result


class SubtitleParser:
    """Detects and parses various subtitle formats into a unified cue list."""

    @staticmethod
    def parse(content: str) -> List[Dict]:
        """
        Parses content into list of cues:
        [{ 'timestamp': '00:01:23' (or None), 'speaker_marker': bool, 'text': '...' }]
        """
        content = content.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not content:
            return []

        # 1. Detect WebVTT
        if content.startswith('WEBVTT') or '\nWEBVTT' in content[:100]:
            return SubtitleParser.parse_vtt(content)

        # 2. Detect SRT
        if re.search(r'^\d+\s*\n\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}', content, re.MULTILINE):
            return SubtitleParser.parse_srt(content)

        # 3. Detect YouTube SBV
        if re.search(r'^\d+:\d{2}:\d{2}\.\d{3},\d+:\d{2}:\d{2}\.\d{3}', content, re.MULTILINE):
            return SubtitleParser.parse_sbv(content)

        # 4. Detect YouTube copy-pasted transcript (lines alternating timestamps like 0:00 or 12:34)
        if SubtitleParser.is_youtube_copy_paste(content):
            return SubtitleParser.parse_youtube_copy_paste(content)

        # 5. Default: Raw text with or without '>> ' speaker markers (like downsub.com exports)
        return SubtitleParser.parse_raw_text(content)

    @staticmethod
    def parse_raw_text(content: str) -> List[Dict]:
        lines = content.split('\n')
        cues = []
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            turns = split_speaker_turns(line_str, initial_marker=(len(cues) == 0))
            for turn in turns:
                cues.append({
                    'timestamp': None,
                    'speaker_marker': turn['speaker_marker'],
                    'text': turn['text']
                })
        return cues

    @staticmethod
    def parse_srt(content: str) -> List[Dict]:
        blocks = re.split(r'\n\s*\n', content)
        cues = []
        time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2})[,\.]\d{3}\s*-->\s*(\d{2}:\d{2}:\d{2})[,\.]\d{3}')

        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if not lines:
                continue
            timestamp = None
            text_lines = []
            for line in lines:
                m = time_pattern.search(line)
                if m:
                    timestamp = m.group(1)
                elif not line.isdigit():
                    text_lines.append(strip_html_tags(line))

            if text_lines:
                combined_text = ' '.join(text_lines)
                turns = split_speaker_turns(combined_text)
                for turn in turns:
                    cues.append({
                        'timestamp': timestamp,
                        'speaker_marker': turn['speaker_marker'],
                        'text': turn['text']
                    })

        return SubtitleParser.dedupe_rolling_cues(cues)

    @staticmethod
    def parse_vtt(content: str) -> List[Dict]:
        lines = content.split('\n')
        cues = []
        time_pattern = re.compile(r'(\d{1,2}:)?(\d{2}:\d{2})[,\.]\d{3}\s*-->')
        current_time = None
        current_text = []

        header_tags = ('WEBVTT', 'NOTE', 'STYLE', 'REGION', 'Kind:', 'Language:')

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith(header_tags):
                continue

            time_match = time_pattern.search(line_str)
            if time_match:
                if current_text:
                    text_str = ' '.join(current_text)
                    turns = split_speaker_turns(text_str)
                    for turn in turns:
                        cues.append({
                            'timestamp': current_time,
                            'speaker_marker': turn['speaker_marker'],
                            'text': turn['text']
                        })
                    current_text = []
                # Format timestamp
                m_hours = time_match.group(1)
                m_min_sec = time_match.group(2)
                current_time = (m_hours if m_hours else '00:') + m_min_sec
                if current_time.count(':') == 1:
                    current_time = '00:' + current_time
            else:
                cleaned = strip_html_tags(line_str)
                if cleaned and not cleaned.isdigit():
                    current_text.append(cleaned)

        if current_text:
            text_str = ' '.join(current_text)
            turns = split_speaker_turns(text_str)
            for turn in turns:
                cues.append({
                    'timestamp': current_time,
                    'speaker_marker': turn['speaker_marker'],
                    'text': turn['text']
                })

        return SubtitleParser.dedupe_rolling_cues(cues)

    @staticmethod
    def parse_sbv(content: str) -> List[Dict]:
        blocks = re.split(r'\n\s*\n', content)
        cues = []
        time_pattern = re.compile(r'(\d+:\d{2}:\d{2})\.\d{3},\d+:\d{2}:\d{2}\.\d{3}')

        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if not lines:
                continue
            timestamp = None
            text_lines = []
            for line in lines:
                m = time_pattern.search(line)
                if m:
                    timestamp = m.group(1)
                else:
                    text_lines.append(strip_html_tags(line))

            if text_lines:
                combined_text = ' '.join(text_lines)
                turns = split_speaker_turns(combined_text)
                for turn in turns:
                    cues.append({
                        'timestamp': timestamp,
                        'speaker_marker': turn['speaker_marker'],
                        'text': turn['text']
                    })

        return cues

    @staticmethod
    def is_youtube_copy_paste(content: str) -> bool:
        sample_lines = [l.strip() for l in content.split('\n')[:20] if l.strip()]
        time_count = sum(1 for l in sample_lines if re.match(r'^\d{1,2}:\d{2}(?::\d{2})?$', l))
        return time_count >= 2

    @staticmethod
    def parse_youtube_copy_paste(content: str) -> List[Dict]:
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        cues = []
        current_time = None
        current_text = []

        for line in lines:
            if re.match(r'^\d{1,2}:\d{2}(?::\d{2})?$', line):
                if current_text:
                    text_str = ' '.join(current_text)
                    turns = split_speaker_turns(text_str)
                    for turn in turns:
                        cues.append({
                            'timestamp': current_time,
                            'speaker_marker': turn['speaker_marker'],
                            'text': turn['text']
                        })
                    current_text = []
                current_time = line
            else:
                current_text.append(strip_html_tags(line))

        if current_text:
            text_str = ' '.join(current_text)
            turns = split_speaker_turns(text_str)
            for turn in turns:
                cues.append({
                    'timestamp': current_time,
                    'speaker_marker': turn['speaker_marker'],
                    'text': turn['text']
                })

        return cues

    @staticmethod
    def dedupe_rolling_cues(cues: List[Dict]) -> List[Dict]:
        """Deduplicates rolling auto-caption lines from YouTube exports."""
        if not cues:
            return []
        cleaned = []
        for cue in cues:
            text = cue['text'].strip()
            if not text:
                continue
            if cleaned and cleaned[-1]['text'] == text:
                continue
            # Check if current text contains previous text at start
            if cleaned and text.startswith(cleaned[-1]['text']):
                cleaned[-1]['text'] = text
                continue
            cleaned.append(cue)
        return cleaned


def heal_continuations(cues: List[Dict]) -> List[Dict]:
    """
    Merges cues where the subtitle file artificially split a single speaker's thought across cues.
    Never merges across explicit speaker changes.
    """
    if not cues:
        return []

    terminals = ('.', '?', '!', '"', "'", ']', ')')
    trailing_connectors = {
        'i', 'and', 'the', 'a', 'an', 'to', 'of', 'in', 'for', 'with', 'on', 'at', 'by',
        'from', 'as', 'into', 'like', 'or', 'so', 'but', 'because', 'if', 'when', 'where',
        'while', 'is', 'was', 'are', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
        'my', 'your', 'his', 'her', 'our', 'their', 'uh', 'um', 'say'
    }

    merged = []
    for cue in cues:
        text = cue['text'].strip()
        if not text:
            continue

        if not merged:
            merged.append(cue.copy())
            continue

        # If this cue explicitly marks a speaker change, do NOT merge with previous
        if cue.get('speaker_marker', False):
            merged.append(cue.copy())
            continue

        prev_text = merged[-1]['text'].rstrip()

        # Extract first alphanumeric word/char of current cue
        cleaned_prefix = re.sub(r'^[\[\(].*?[\]\)]\s*', '', text).lstrip('"\'‘“— ')
        starts_lowercase = bool(cleaned_prefix and cleaned_prefix[0].islower() and not cleaned_prefix.startswith('i '))

        # Check punctuation and word endings of previous cue
        ends_with_comma_or_dash = prev_text.endswith((',', ';', ':', '-', '—'))
        prev_words = prev_text.split()
        last_word = prev_words[-1].lower().strip('.,?!\"\'') if prev_words else ''
        ends_with_connector = last_word in trailing_connectors
        no_terminal = not prev_text.endswith(terminals)

        should_merge = starts_lowercase or (no_terminal and (ends_with_comma_or_dash or ends_with_connector))

        if should_merge:
            merged[-1]['text'] = prev_text + ' ' + text
        else:
            merged.append(cue.copy())

    return merged


def group_speaker_turns(cues: List[Dict]) -> List[Dict]:
    """
    Groups consecutive cues belonging to the same speaker turn.
    When cues contain speaker_marker (e.g. >> from YouTube), cues between markers
    are merged into a single coherent turn for NotebookLM ingestion.
    """
    if not cues:
        return []

    has_markers = any(c.get('speaker_marker', False) for c in cues)
    if not has_markers:
        return cues

    turns = []
    current_turn = None

    for cue in cues:
        is_new_speaker = cue.get('speaker_marker', False)
        if is_new_speaker or current_turn is None:
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                'timestamp': cue.get('timestamp'),
                'speaker_marker': is_new_speaker,
                'texts': [cue['text']]
            }
        else:
            current_turn['texts'].append(cue['text'])

    if current_turn:
        turns.append(current_turn)

    # Convert back to cue format
    result = []
    for turn in turns:
        result.append({
            'timestamp': turn['timestamp'],
            'speaker_marker': turn['speaker_marker'],
            'text': ' '.join(turn['texts'])
        })
    return result


def format_timestamp(ts: Optional[str]) -> str:
    """
    Shortens timestamp to minutes (MM:SS) for durations under 1 hour,
    retaining hours (H:MM:SS) once it reaches or exceeds 1 hour.
    """
    if not ts:
        return ""
    ts = ts.strip("[] `")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        try:
            if int(h) == 0:
                return f"{int(m):02d}:{s}"
            return f"{int(h)}:{m}:{s}"
        except ValueError:
            return ts
    elif len(parts) == 2:
        return ts
    return ts


def format_for_notebooklm(
    cues: List[Dict],
    title: str = "YouTube Transcript",
    speaker_mode: str = "dash",
    speaker_names: Optional[List[str]] = None,
    chunk_size: int = 0,
    keep_sound_tags: bool = True,
    heal_sentences: bool = True,
    include_timestamps: bool = True,
    output_format: str = "markdown"
) -> str:
    """
    Formats parsed cues into NotebookLM-ready Markdown.
    
    speaker_mode:
      - 'dash': uses literary em-dash '— ' to indicate speaker change without making assumptions (Default)
      - 'chevron': uses clean unicode chevron '» ' (resembles YouTube's >> without triggering markdown blockquotes)
      - 'bullet': uses bullet point '• '
      - 'none': clean paragraphs separated by double newlines only
      - 'alternate': alternates between speaker names (Speaker 1, Speaker 2 or custom names)
      - 'generic': uses '**Speaker:**' for all dialogue turns
    """
    if heal_sentences:
        cues = heal_continuations(cues)
        cues = group_speaker_turns(cues)

    lines = []
    # Title & Metadata
    lines.append(f"# {title}\n")
    lines.append("> Formatted for Google NotebookLM source ingestion.\n")
    lines.append("---\n")

    current_speaker_idx = 0
    turn_count = 0
    section_count = 1

    for cue in cues:
        text = cue['text'].strip()
        if not keep_sound_tags:
            text = clean_sound_tags(text)
        if not text:
            continue

        # Check for section chunking
        if chunk_size > 0 and turn_count > 0 and turn_count % chunk_size == 0:
            formatted_ts = format_timestamp(cue['timestamp']) if cue.get('timestamp') else ""
            section_time = f" ({formatted_ts})" if (include_timestamps and formatted_ts) else ""
            lines.append(f"\n## Part {section_count + 1}{section_time}\n")
            section_count += 1

        # Determine speaker / turn marker
        if speaker_mode == 'dash':
            speaker_label = "— "
        elif speaker_mode == 'chevron':
            speaker_label = "» "
        elif speaker_mode == 'bullet':
            speaker_label = "• "
        elif speaker_mode == 'alternate':
            names = speaker_names or ["Speaker 1", "Speaker 2"]
            speaker_label = f"**{names[current_speaker_idx % len(names)]}:** "
            current_speaker_idx += 1
        elif speaker_mode == 'generic':
            speaker_label = "**Speaker:** "
        else:
            speaker_label = ""

        # Timestamp prefix (shortened to MM:SS under 1 hour)
        timestamp_prefix = ""
        if include_timestamps and cue.get('timestamp'):
            formatted_ts = format_timestamp(cue['timestamp'])
            if formatted_ts:
                timestamp_prefix = f"`[{formatted_ts}]` "

        # Format turn with double newlines
        formatted_turn = f"{timestamp_prefix}{speaker_label}{text}\n"
        lines.append(formatted_turn)
        turn_count += 1

    return "\n".join(lines)


class SubtitleServerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for serving the NotebookLM Web UI and fetching subtitles."""

    def end_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Connection', 'close')

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_cors_headers()
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/save_raw':
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_len).decode('utf-8')
                data = json.loads(body)
                title = data.get('title', 'transcript')
                ext = data.get('format', 'srt')
                content = data.get('content', '')
                raw_dir = data.get('raw_dir', 'subtitles')

                os.makedirs(raw_dir, exist_ok=True)
                sanitized = sanitize_filename(title)
                filename = f"{sanitized}.{ext}"
                target_path = os.path.join(raw_dir, filename)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(content)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'success': True, 'path': target_path, 'filename': filename}).encode('utf-8'))
            except Exception as e:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
            return
        else:
            self.send_response(404)
            self.end_cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ('/', '/index.html'):
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'notebooklm_subtitle_formatter.html')
            if not os.path.exists(html_path):
                self.send_response(404)
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(b"Error: notebooklm_subtitle_formatter.html not found.")
                return

            with open(html_path, 'rb') as f:
                html_bytes = f.read()

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_cors_headers()
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        elif path == '/favicon.ico':
            self.send_response(204)
            self.end_cors_headers()
            self.end_headers()
            return

        elif path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'server': 'format_subtitles'}).encode('utf-8'))
            return

        elif path == '/api/fetch':
            params = urllib.parse.parse_qs(parsed.query)
            urls = params.get('url', [])
            lang = params.get('lang', ['en'])[0]
            keep_raw = params.get('keep_raw', ['0'])[0].lower() in ('1', 'true', 'yes')
            raw_dir = params.get('raw_dir', ['subtitles'])[0]

            if not urls:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': 'Missing url parameter'}).encode('utf-8'))
                return

            target_url = urls[0].strip()
            try:
                result = fetch_youtube_subtitles(url=target_url, lang=lang, keep_raw=keep_raw, raw_dir=raw_dir)
                payload = {
                    'success': True,
                    'title': result['title'],
                    'sanitized_title': result['sanitized_title'],
                    'content': result['content'],
                    'format': result['format'],
                    'lang': result['lang'],
                    'is_auto': result['is_auto'],
                    'raw_file': result.get('raw_file')
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode('utf-8'))
            except Exception as e:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
            return

        else:
            self.send_response(404)
            self.end_cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        # Cleaner server log output
        sys.stderr.write(f"[WebUI Server] {args[0]} - {args[1]}\n")


def run_server(port: int = 8000):
    """Starts the local HTTP server and opens the web UI."""
    server_address = ('127.0.0.1', port)
    try:
        httpd = ThreadingHTTPServer(server_address, SubtitleServerHandler)
        httpd.daemon_threads = True
    except OSError as e:
        print(f"Error binding to port {port}: {e}", file=sys.stderr)
        print("Try specifying a different port: python format_subtitles.py --serve <port>", file=sys.stderr)
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"\n========================================================")
    print(f" NotebookLM YouTube Subtitle Formatter Web UI")
    print(f" Server running at: {url}")
    print(f" Press Ctrl+C to stop the server")
    print(f"========================================================\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Format YouTube subtitles and transcripts for optimal Google NotebookLM ingestion."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="YouTube video URL (e.g. https://www.youtube.com/watch?v=...) or path to local subtitle file (.txt, .srt, .vtt, .sbv)"
    )
    parser.add_argument(
        "--serve",
        nargs="?",
        const=8000,
        type=int,
        metavar="PORT",
        help="Start local web server and open the Web UI in your browser (default port: 8000)."
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (.md or .txt). Defaults to <title>_notebooklm.md"
    )
    parser.add_argument(
        "--lang",
        default="en",
        help="Subtitle language code to download when passing a YouTube URL. Defaults to 'en'."
    )
    parser.add_argument(
        "--keep-raw",
        action="store_true",
        help="When downloading from YouTube, retain the raw downloaded subtitle file (.vtt or .srt) on disk inside the subtitles/ folder."
    )
    parser.add_argument(
        "--raw-dir",
        default="subtitles",
        help="Directory to save raw downloaded subtitle files when --keep-raw is enabled. Defaults to 'subtitles'."
    )
    parser.add_argument(
        "--raw-path",
        help="Custom file path to save raw downloaded subtitle when --keep-raw is enabled (overrides --raw-dir)."
    )
    parser.add_argument(
        "--speaker-mode",
        choices=["dash", "chevron", "bullet", "none", "alternate", "generic"],
        default="dash",
        help="Turn marker style: 'dash' (— em-dash, neutral dialogue marker; default), 'chevron' (» clean unicode chevron), 'bullet' (•), 'none' (paragraphs only), 'alternate' (Speaker 1/2), or 'generic' (Speaker:)."
    )
    parser.add_argument(
        "--names",
        help="Comma-separated list of speaker names (e.g. 'Alex,Sam' or 'Host,Guest'). Used with alternate mode."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        help="Number of dialogue turns per section header (## Part N). Set to 0 to disable. Default: 0 (disabled)."
    )
    parser.add_argument(
        "--strip-tags",
        action="store_true",
        help="Remove audio/sound bracket tags like [laughter], [screaming], [music]."
    )
    parser.add_argument(
        "--no-heal",
        action="store_true",
        help="Disable healing/merging of broken sentence continuations."
    )
    parser.add_argument(
        "--no-timestamps", "--remove-timestamps", "--strip-timestamps",
        dest="no_timestamps",
        action="store_true",
        help="Remove timestamps completely from dialogue turns and section headers."
    )
    parser.add_argument(
        "--title",
        help="Document title for the transcript header (defaults to video title or filename)."
    )

    args = parser.parse_args()

    if args.serve is not None:
        run_server(port=args.serve)
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    # Detect whether source is a YouTube URL or a local file
    if is_youtube_url(args.source):
        try:
            yt_result = fetch_youtube_subtitles(
                url=args.source,
                lang=args.lang,
                keep_raw=args.keep_raw,
                raw_path=args.raw_path,
                raw_dir=args.raw_dir
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        content = yt_result['content']
        doc_title = args.title or yt_result['title']
        default_out = f"{yt_result['sanitized_title']}_notebooklm.md"
    else:
        if not os.path.exists(args.source):
            print(f"Error: File not found and not a valid YouTube URL: {args.source}", file=sys.stderr)
            sys.exit(1)

        with open(args.source, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        base_path, _ = os.path.splitext(args.source)
        doc_title = args.title or os.path.basename(base_path).replace("_", " ").title()
        default_out = f"{base_path}_notebooklm.md"

    names = [n.strip() for n in args.names.split(",")] if args.names else None

    cues = SubtitleParser.parse(content)
    if not cues:
        print(f"Warning: No subtitle cues or text could be extracted from {args.source}", file=sys.stderr)
        sys.exit(1)

    formatted_output = format_for_notebooklm(
        cues=cues,
        title=doc_title,
        speaker_mode=args.speaker_mode,
        speaker_names=names,
        chunk_size=args.chunk_size,
        keep_sound_tags=not args.strip_tags,
        heal_sentences=not args.no_heal,
        include_timestamps=not args.no_timestamps
    )

    out_path = args.output or default_out

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(formatted_output)

    print(f"Successfully processed {len(cues)} cues.")
    print(f"Saved NotebookLM-ready document to: {out_path}")


if __name__ == "__main__":
    main()
