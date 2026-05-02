import discord
from discord.ext import commands
import asyncio
import time
import re
import yt_dlp
from youtube_transcript_api import _api
import nest_asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

nest_asyncio.apply()

# ============================================
# 設定・オプション
# ============================================

YDL_OPTIONS = {
    'format': 'ba',
    'noplaylist': True,
    'cookiefile': 'cookies.txt',
    'js_runtimes': {
        'deno': {}
    }
}

# プレイリスト取得用（noplaylist: False）
YDL_PLAYLIST_OPTIONS = {
    'format': 'ba',
    'noplaylist': False,
    'extract_flat': 'in_playlist',  # 各動画のメタデータのみ取得（高速）
    'cookiefile': 'cookies.txt',
    'quiet': True,
}

PLAYLIST_MAX_TRACKS = 50  # 一度に追加できる最大曲数

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

LYRIC_DELAY = 0

# ============================================
# キュー管理クラス
# ============================================

@dataclass
class Track:
    audio_url: str
    lyric_url: str
    title: str = "Unknown"
    requester: str = "Unknown"

class GuildQueue:
    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Optional[Track] = None
        self.vc: Optional[discord.VoiceClient] = None
        self.is_playing: bool = False
        self.skip_flag: bool = False
        self.stop_flag: bool = False
        self.lyric_task: Optional[asyncio.Task] = None

# ギルドごとのキュー管理
guild_queues: dict[int, GuildQueue] = {}

def get_queue(guild_id: int) -> GuildQueue:
    if guild_id not in guild_queues:
        guild_queues[guild_id] = GuildQueue()
    return guild_queues[guild_id]

# ============================================
# ユーティリティ
# ============================================

def get_youtube_lyrics(video_id):
    """YouTubeから字幕を取得（日・英を優先し、なければデフォルトを使用）"""
    try:
        transcript_list = _api.YouTubeTranscriptApi().list(video_id)
        try:
            transcript = transcript_list.find_transcript(['ja', 'en'])
        except:
            transcript = next(iter(transcript_list))
        print(f"✅ 字幕を取得しました (言語: {transcript.language})")
        return transcript.fetch()
    except Exception as e:
        print(f"❌ 字幕リストの取得に失敗しました: {e}")
        return []

def extract_video_id(url):
    match = re.search(r'(?:youtube\.com\/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else None

def is_playlist_url(url: str) -> bool:
    """URLがYouTubeプレイリストかどうか判定"""
    return bool(re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url))

async def fetch_playlist_tracks(url: str, requester: str, max_tracks: int = PLAYLIST_MAX_TRACKS) -> tuple[list[Track], str]:
    """
    プレイリストURLからTrackリストを取得。
    戻り値: (tracks, playlist_title)
    """
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_PLAYLIST_OPTIONS) as ydl:
            return ydl.extract_info(url, download=False)

    info = await loop.run_in_executor(None, _extract)

    playlist_title = info.get('title', 'Unknown Playlist')
    entries = info.get('entries', [])

    tracks = []
    for entry in entries[:max_tracks]:
        if entry is None:
            continue
        video_id = entry.get('id') or entry.get('url', '')
        video_url = f"https://www.youtube.com/watch?v={video_id}" if len(video_id) == 11 else entry.get('url', '')
        title = entry.get('title', 'Unknown')
        if video_url:
            tracks.append(Track(
                audio_url=video_url,
                lyric_url=video_url,
                title=title,
                requester=requester
            ))

    return tracks, playlist_title

async def fetch_title(url: str) -> str:
    """動画タイトルだけ取得（軽量）"""
    loop = asyncio.get_event_loop()
    def _extract():
        opts = {**YDL_OPTIONS, 'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('title', 'Unknown')
    try:
        return await loop.run_in_executor(None, _extract)
    except:
        return 'Unknown'

# ============================================
# 再生ループ
# ============================================

async def play_queue(ctx: commands.Context, gq: GuildQueue):
    """キューを順番に再生するメインループ"""
    while gq.queue:
        if gq.stop_flag:
            break

        track = gq.queue.popleft()
        gq.current = track
        gq.is_playing = True
        gq.skip_flag = False

        audio_video_id = extract_video_id(track.audio_url)
        lyric_video_id = extract_video_id(track.lyric_url)

        await ctx.send(f"🎵 **再生開始：** {track.title}（リクエスト：{track.requester}）")

        # 字幕取得
        lyrics = get_youtube_lyrics(lyric_video_id) if lyric_video_id else []
        if not lyrics:
            await ctx.send("📝 歌詞データなし。音楽のみ再生します。")

        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(track.audio_url, download=False)
                stream_url = info['url']

            source = await discord.FFmpegOpusAudio.from_probe(stream_url, **FFMPEG_OPTIONS)
            gq.vc.play(source)

            start_time = time.time()
            current_message = None

            # 歌詞表示ループ
            for i, item in enumerate(lyrics):
                if gq.skip_flag or gq.stop_flag or not gq.vc.is_playing():
                    break

                display_time = start_time + (item.start + LYRIC_DELAY)
                sleep_duration = display_time - time.time()
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)

                if gq.skip_flag or gq.stop_flag:
                    break

                prev_text   = lyrics[i-1].text.strip() if i > 0 else "---"
                current_text = item.text.strip()
                next_text   = lyrics[i+1].text.strip() if i + 1 < len(lyrics) else "---"
                next_next   = lyrics[i+2].text.strip() if i + 2 < len(lyrics) else "---"

                embed = discord.Embed(
                    title="🎤 Karaoke Streaming",
                    description=(
                        f"**前：** {prev_text}\n"
                        f"**今：** {current_text}\n"
                        f"**次：** {next_text}\n"
                        f"**次々：** {next_next}"
                    ),
                    color=discord.Color.gold()
                )
                embed.set_footer(text=f"Now: {track.title}  |  キュー残り: {len(gq.queue)}曲")

                if current_message is None:
                    current_message = await ctx.send(embed=embed)
                else:
                    try:
                        await current_message.edit(embed=embed)
                    except:
                        current_message = await ctx.send(embed=embed)

            # 歌詞なし or 歌詞終了後、再生終了を待つ
            while gq.vc.is_playing() and not gq.skip_flag and not gq.stop_flag:
                await asyncio.sleep(0.5)

            # スキップ時は強制停止
            if gq.vc.is_playing():
                gq.vc.stop()

        except Exception as e:
            await ctx.send(f"❌ 再生エラー：{e}")
            print(f"詳細ログ: {e}")

    # ループ終了処理
    gq.is_playing = False
    gq.current = None

    if gq.stop_flag:
        gq.queue.clear()
        gq.stop_flag = False
        await ctx.send("⏹️ 再生を停止しました。キューをクリアしました。")
    else:
        await ctx.send("🎉 キューの再生がすべて終わりました！")

    if gq.vc and gq.vc.is_connected():
        await gq.vc.disconnect()
    gq.vc = None

# ============================================
# Bot設定
# ============================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Botがログインしました: {bot.user}")

# ============================================
# コマンド
# ============================================

@bot.command(name='karaoke')
async def karaoke_command(ctx: commands.Context, audio_url: str, lyric_url: str = None):
    """曲またはプレイリストをキューに追加して再生"""
    if not ctx.author.voice:
        await ctx.send("❌ ボイスチャンネルに接続してください")
        return

    gq = get_queue(ctx.guild.id)

    # ── プレイリストURLの場合 ──────────────────────────
    if is_playlist_url(audio_url):
        await ctx.send(f"⏳ プレイリストを読み込み中... （最大 {PLAYLIST_MAX_TRACKS} 曲）")
        try:
            tracks, pl_title = await fetch_playlist_tracks(audio_url, ctx.author.display_name)
        except Exception as e:
            await ctx.send(f"❌ プレイリストの取得に失敗しました: {e}")
            return

        if not tracks:
            await ctx.send("❌ プレイリストに再生可能な曲が見つかりませんでした")
            return

        for track in tracks:
            gq.queue.append(track)

        embed = discord.Embed(
            title="📋 プレイリストをキューに追加",
            description=f"**{pl_title}**\n{len(tracks)} 曲をキューに追加しました",
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"キュー合計: {len(gq.queue)}曲")
        await ctx.send(embed=embed)

    # ── 単曲URLの場合 ─────────────────────────────────
    else:
        if lyric_url is None:
            lyric_url = audio_url

        if not extract_video_id(audio_url):
            await ctx.send("❌ 有効な音声用YouTube URLではありません")
            return
        if not extract_video_id(lyric_url):
            await ctx.send("❌ 有効な歌詞用YouTube URLではありません")
            return

        await ctx.send("⏳ タイトルを取得中...")
        title = await fetch_title(audio_url)
        track = Track(
            audio_url=audio_url,
            lyric_url=lyric_url,
            title=title,
            requester=ctx.author.display_name
        )
        gq.queue.append(track)
        await ctx.send(f"✅ **キューに追加：** {title}（キュー {len(gq.queue)} 曲目）")

    # ── 再生開始（未再生時のみ） ───────────────────────
    if not gq.is_playing:
        if gq.vc is None or not gq.vc.is_connected():
            gq.vc = await ctx.author.voice.channel.connect()
        asyncio.create_task(play_queue(ctx, gq))


@bot.command(name='playlist', aliases=['pl'])
async def playlist_command(ctx: commands.Context, playlist_url: str, max_tracks: int = PLAYLIST_MAX_TRACKS):
    """YouTubeプレイリストをキューに追加（上限曲数を指定可能）"""
    if not ctx.author.voice:
        await ctx.send("❌ ボイスチャンネルに接続してください")
        return
    if not is_playlist_url(playlist_url):
        await ctx.send("❌ 有効なYouTubeプレイリストURLではありません（`?list=...` が必要です）")
        return

    max_tracks = max(1, min(max_tracks, 200))  # 1〜200の範囲に制限
    await ctx.send(f"⏳ プレイリストを読み込み中... （最大 {max_tracks} 曲）")

    try:
        tracks, pl_title = await fetch_playlist_tracks(playlist_url, ctx.author.display_name, max_tracks)
    except Exception as e:
        await ctx.send(f"❌ プレイリストの取得に失敗しました: {e}")
        return

    if not tracks:
        await ctx.send("❌ プレイリストに再生可能な曲が見つかりませんでした")
        return

    gq = get_queue(ctx.guild.id)
    for track in tracks:
        gq.queue.append(track)

    embed = discord.Embed(
        title="📋 プレイリストをキューに追加",
        description=f"**{pl_title}**\n{len(tracks)} 曲をキューに追加しました",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"キュー合計: {len(gq.queue)}曲")
    await ctx.send(embed=embed)

    if not gq.is_playing:
        if gq.vc is None or not gq.vc.is_connected():
            gq.vc = await ctx.author.voice.channel.connect()
        asyncio.create_task(play_queue(ctx, gq))


@bot.command(name='queue', aliases=['q', 'list'])
async def queue_command(ctx: commands.Context):
    """現在のキューを表示"""
    gq = get_queue(ctx.guild.id)

    if gq.current is None and not gq.queue:
        await ctx.send("📭 キューは空です。`/karaoke <URL>` で曲を追加してください！")
        return

    lines = []
    if gq.current:
        lines.append(f"🎵 **再生中：** {gq.current.title}（{gq.current.requester}）")

    if gq.queue:
        lines.append("\n**待機中：**")
        for i, track in enumerate(gq.queue, 1):
            lines.append(f"　`{i}.` {track.title}（{track.requester}）")
    else:
        lines.append("\n（待機中の曲はありません）")

    embed = discord.Embed(
        title="🎤 カラオケキュー",
        description="\n".join(lines),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"合計 {len(gq.queue)}曲待機中")
    await ctx.send(embed=embed)


@bot.command(name='skip', aliases=['s'])
async def skip_command(ctx: commands.Context):
    """現在の曲をスキップ"""
    gq = get_queue(ctx.guild.id)
    if not gq.is_playing or not gq.current:
        await ctx.send("⏭️ 再生中の曲がありません")
        return
    gq.skip_flag = True
    await ctx.send(f"⏭️ **スキップ：** {gq.current.title}")


@bot.command(name='stop')
async def stop_command(ctx: commands.Context):
    """再生を停止してキューをクリア"""
    gq = get_queue(ctx.guild.id)
    if not gq.is_playing:
        await ctx.send("⏹️ 再生中の曲がありません")
        return
    gq.stop_flag = True
    gq.skip_flag = True  # 現在の再生ループも抜ける
    await ctx.send("⏹️ 停止中...")


@bot.command(name='nowplaying', aliases=['np'])
async def nowplaying_command(ctx: commands.Context):
    """現在再生中の曲を表示"""
    gq = get_queue(ctx.guild.id)
    if not gq.current:
        await ctx.send("🎵 現在再生中の曲はありません")
        return
    embed = discord.Embed(
        title="🎤 Now Playing",
        description=f"**{gq.current.title}**\nリクエスト：{gq.current.requester}",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"キュー残り: {len(gq.queue)}曲")
    await ctx.send(embed=embed)


@bot.command(name='remove', aliases=['rm'])
async def remove_command(ctx: commands.Context, index: int):
    """キューから指定番号の曲を削除（1始まり）"""
    gq = get_queue(ctx.guild.id)
    if not gq.queue:
        await ctx.send("📭 キューは空です")
        return
    if index < 1 or index > len(gq.queue):
        await ctx.send(f"❌ 有効な番号を指定してください（1〜{len(gq.queue)}）")
        return
    queue_list = list(gq.queue)
    removed = queue_list.pop(index - 1)
    gq.queue = deque(queue_list)
    await ctx.send(f"🗑️ **削除：** {removed.title}")


@bot.command(name='clear')
async def clear_command(ctx: commands.Context):
    """キューをクリア（再生中の曲はそのまま）"""
    gq = get_queue(ctx.guild.id)
    gq.queue.clear()
    await ctx.send("🗑️ キューをクリアしました（再生中の曲は継続します）")


@bot.command(name='karaoke_help', aliases=['khelp'])
async def help_command(ctx: commands.Context):
    """カラオケBotのヘルプを表示"""
    embed = discord.Embed(
        title="🎤 カラオケBot コマンド一覧",
        color=discord.Color.gold()
    )
    embed.add_field(name="/karaoke <URL> [歌詞URL]",       value="曲 or プレイリストをキューに追加して再生", inline=False)
    embed.add_field(name="/playlist <URL> [最大曲数]",      value="プレイリストをキューに追加（デフォルト50曲、最大200曲）", inline=False)
    embed.add_field(name="/queue (または /q, /list)",       value="キュー一覧を表示",          inline=False)
    embed.add_field(name="/skip (または /s)",               value="現在の曲をスキップ",        inline=False)
    embed.add_field(name="/stop",                           value="停止＆キュークリア",         inline=False)
    embed.add_field(name="/nowplaying (または /np)",        value="再生中の曲を表示",           inline=False)
    embed.add_field(name="/remove <番号>",                  value="キューから曲を削除",         inline=False)
    embed.add_field(name="/clear",                          value="待機キューをクリア",         inline=False)
    await ctx.send(embed=embed)


# 実行
TOKEN = "ここにトークンを入力"
bot.run(TOKEN)
