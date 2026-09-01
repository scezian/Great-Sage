"""
YouTube stream extraction worker for the Stream tab's hybrid mpv playback.
Runs yt-dlp off the UI thread; emits stream_ready(dict) or error(str).

Strategy:
  - Prefer a single pre-muxed video+audio format at/under the target height
    (simplest playback path -- one URL, no mpv audio-file juggling).
  - Fall back to separate bestvideo+bestaudio only when no muxed format
    covers the requested height (this only happens above 1080p on YouTube,
    where 1440p/2160p ship video-only).
"""
from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp


class YouTubeExtractWorker(QThread):
    stream_ready = pyqtSignal(dict)
    error        = pyqtSignal(str)

    def __init__(self, video_url: str, target_height: int = None, parent=None):
        super().__init__(parent)
        self.video_url     = video_url
        self.target_height = target_height

    def run(self):
        try:
            fmt_selector = self._build_format_selector(self.target_height)
            ydl_opts = {
                'format': fmt_selector,
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.video_url, download=False)

            available_heights = sorted({
                f.get('height') for f in info.get('formats', [])
                if f.get('height') and f.get('vcodec') != 'none'
            }, reverse=True)

            result = {
                'title':     info.get('title', ''),
                'duration':  info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'available_heights': available_heights,
            }

            if info.get('requested_formats'):
                video_fmt = next((f for f in info['requested_formats']
                                   if f.get('vcodec') != 'none'), None)
                audio_fmt = next((f for f in info['requested_formats']
                                   if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
                if not video_fmt or not video_fmt.get('url'):
                    self.error.emit("No playable video stream resolved for this video.")
                    return
                result['url']    = video_fmt['url']
                result['height'] = video_fmt.get('height')
                if audio_fmt and audio_fmt.get('url'):
                    result['audio_url'] = audio_fmt['url']
            elif info.get('url'):
                result['url']    = info['url']
                result['height'] = info.get('height')
            else:
                self.error.emit("No playable stream URL resolved for this video.")
                return

            self.stream_ready.emit(result)
        except yt_dlp.utils.DownloadError as e:
            self.error.emit(f"yt-dlp extraction failed: {e}")
        except Exception as e:
            self.error.emit(f"Unexpected extraction error: {e}")

    @staticmethod
    def _build_format_selector(target_height: int = None) -> str:
        if target_height is None:
            return "best/bestvideo+bestaudio"
        return (
            f"best[height<={target_height}]"
            f"/bestvideo[height<={target_height}]+bestaudio"
        )
