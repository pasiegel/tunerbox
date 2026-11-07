# TunerBox

A personal IPTV "cable box": your local cable channel numbers and guide (set up for Xfinity in Grand Rapids, Michigan; pick your own provider in Setup), filled with feeds from your IPTV playlists. Inspired by MythTV's front end, Tunarr's channel mapping and IPTVnator's playlist handling.

* **Real channel numbers.** Your provider's lineup is the backbone: out of the box the Xfinity Grand Rapids lineup in `channels.csv` (2 HSN, 3 WWMT, 8 WOOD, 42 CNN…), or any US/Canadian provider picked by ZIP/postal code. Playlist channels are fuzzy-matched onto it.
* **Local guide.** Listings come from Gracenote (the data behind zap2it and TV Guide) for your provider (Xfinity Grand Rapids by default), so the guide matches your numbers. US and Canada.
* **Everything else gets a number too.** Every working playlist channel that isn't on the lineup is numbered from 20000 up, with online XMLTV guides.
* **Manual mapping.** A Tunarr-style picker lets you search every feed, preview it, and pin the one you want.
* **M3U and Xtream Codes.** Paste an Xtream `get.php` link and it switches to the Xtream API automatically.
* **Cable box UI.** Picture-in-picture guide grid, channel banner with now/next and progress, number-key tuning, last channel, and static "No Signal" screens. Dead feeds fail over to backups automatically.
* **DVR.** Record one show, a series on one channel, or new episodes on any channel. Recordings are saved as MP4.
* **Works with other apps.** `/playlist.m3u` and `/epg.xml` work in VLC or TiviMate, and it emulates an HDHomeRun tuner for Plex, Emby and Jellyfin.

## Getting started
1. **Install.** Download the latest build for your computer from the **Releases** page: the Windows `TunerBox-Setup-….exe`, the Mac `.dmg` or the Linux `.tar.gz`. ffmpeg is included, nothing else to install. (Or run from source, below.)
2. **Open TunerBox.** It shows **No channels yet**; press Enter or click to open **Setup**.
3. **Pick your area.** It starts with Xfinity in Grand Rapids, MI (ZIP 49503). Anywhere else: Setup › **Guide**, enter your ZIP or postal code, press **Find providers**, choose your cable, satellite or antenna lineup and press **Save**.
4. **Add a playlist.** Setup › **Playlists**: paste your M3U URL or Xtream login (or pick *HDHomeRun* to use an antenna tuner), then **Scan playlists now**. The first scan takes a few minutes.
5. **Watch.** Press **Watch TV**. Up/down or number keys change channels, **G** opens the guide, **M** the menu.
6. **Other screens.** Phones, tablets and TV browsers on your network open `http://<this-computer>:8642/`. Setup › **Phones** pairs a phone as a remote.

More detail is in [First-time setup](#first-time-setup) below.

## Requirements (running from source)
* Python 3.10+
* ffmpeg on the PATH (`winget install ffmpeg`, `brew install ffmpeg`, `apt install ffmpeg`)
* A Chromium-based browser, Firefox, or Safari 17+ on a Mac

## Run it
| OS | Command |
|---|---|
| Windows | double-click `run.bat` (first run also adds **TunerBox** to the Desktop and Start Menu) |
| macOS / Linux | `./run.sh` |

TunerBox opens as a desktop app window. F11 or F toggles full screen, and closing the window shuts everything down. The first run builds a `.venv` and shows a console while it installs; after that the app starts without one.

While the app is open, other devices on your network (a TV browser, tablet, Plex) can use `http://<this-pc-ip>:8642/`.

| Option | What it does |
|---|---|
| `run.bat --server` | server only, no window: for a PC that just feeds other devices |
| `run.bat --browser` | use your web browser instead of the app window |
| `run.bat --shortcut` | re-create the Desktop/Start Menu shortcuts |

On Linux the app window needs GTK or Qt (see the pywebview docs); without one it falls back to the browser.

## Installers and other devices
| Device | How |
|---|---|
| Windows, Mac, Linux | Download from the GitHub **Releases** page: Windows installer (`TunerBox-Setup-….exe`, installs per user, no admin) or portable zip, Mac `.dmg`, Linux `.tar.gz`. ffmpeg is included. Data lives in `%LOCALAPPDATA%\TunerBox` (Windows), `~/Library/Application Support/TunerBox` (Mac) or `~/.local/share/TunerBox` (Linux). |
| Android phone/tablet, Android TV, Fire TV | Open `http://<pc-ip>:8642/` in the browser (Chrome, Silk). |
| iPad (iPadOS 15+) / iPhone | In Safari open `http://<pc-ip>:8642/`, then Share › **Add to Home Screen**. It opens full screen like an app. |

The PC running TunerBox does the tuning, guide and DVR, and must be on for the other devices. The first time it starts, Windows asks whether to allow it on the network: choose **Private networks**.

iPhone/iPad Safari plays the server's HLS output (`/api/hls/<number>`); other browsers use MPEG-TS over Media Source Extensions.

Building it yourself: `python packaging/build.py` on Windows (PyInstaller + Inno Setup 6), macOS (`.app`) or Linux. GitHub Actions does this for every release; see `RELEASING.md`.

## First-time setup
1. With no channels, the screen says **No channels yet**. Press Enter or click it to open **Setup** (also Menu › Channel Setup).
2. Outside Grand Rapids (or not on Xfinity)? Go to **Guide**, enter your ZIP or postal code and press **Find providers**. Choose your provider (for example *Xfinity - Digital*, *DIRECTV* or *Local Over the Air Broadcast* for an antenna) and press **Save**. Its channel numbers become your lineup.
3. Go to **Playlists** and add your M3U URL or file, or your Xtream server, username and password.
4. Optional: press **Groups…** on the playlist to untick groups you don't want, such as other countries, adult or radio. **Check all** / **Uncheck all** apply to the groups listed, so after picking a country or typing a search they only change those groups (the buttons then show the count, e.g. *Uncheck 93 listed*). **Only keep listed** keeps the listed groups and hides every other one.
   * Hidden groups are removed straight away. Ticking one again rescans the playlist to bring it back.
5. Press **Scan playlists now**. The scan loads the playlist, checks which streams work, matches channels to your lineup, numbers the leftovers, and downloads the guides. The first scan takes a few minutes.
6. Go to **Channel Lineup** and set the filter to *Weak matches* to review guesses. Press **Change** to search and preview feeds, then **Use** to pin one. Manual picks survive rescans.
7. Press **Watch TV**.

Changing provider later replaces the lineup: press **Scan playlists now** again afterwards.

## Remote / keyboard
| Key | Live TV | Guide |
|---|---|---|
| 0-9 | tune channel | jump to channel |
| ↑ ↓ / PgUp PgDn | channel up/down | move / page |
| ← → | volume | move through time |
| Enter | info banner | watch, or record options |
| G | guide | close guide |
| I | full info | |
| * (or A) | add / remove channel in Favorites | add / remove highlighted channel |
| V | Favorites guide | switch Favorites only / all channels |
| R | record current show | record options |
| L / Backspace | last channel | back |
| D | recordings | |
| / | search | search |
| T | Sports & PPV | Sports & PPV |
| M | menu | |
| U, +/− | mute, volume | |
| F / F11 | full screen | |

**Using a real remote:** open **Menu › Remote control setup** on the device the remote is paired with. Highlight an action, press OK, then press the remote button. Any key a remote sends works, including odd codes, media keys and color buttons. Game controllers and gamepad-style Bluetooth remotes work too. Mappings are saved per device; **Save as default for all devices** stores them on the server. Arrow keys, Enter and Esc always work on that screen, so you can't lock yourself out.

Mouse and touch also work. Click the video for the banner, move the mouse for an on-screen remote, and swipe up/down to change channel.

**Watching a recording:** Space/OK pause, ← → skip back 10 s / ahead 30 s, PgUp/PgDn 5 minutes. **I** shows the recording's info (episode, when it was recorded, description, how much you've watched), **G** opens the guide over it (closing the guide goes back to the recording, picking a channel switches to live TV), Back hides the info or stops, **S** stops.

## Favorites
Star the channels you watch most, then see just those in the guide.
* **Add or remove:** press **\*** (or **A**) on live TV or on a channel in the guide, click the ☆ next to a channel number in the guide, use **Menu › Add to Favorites**, or the *Add to Favorites* line when you press OK on a future show.
* **See them:** **V** or **Menu › ★ Favorites guide** opens the guide with only favorites; **V** in the guide switches between favorites and all channels. Typing a number in the Favorites guide jumps to that channel among all channels if it isn't a favorite.
* Favorites are kept on the TunerBox PC, so every TV, tablet and the phone remote share them. On the phone remote, favorites are listed first in Channels with a ★ to add or remove.

## Local channels (your own videos)
Turn folders of videos into channels that play around the clock, like ErsatzTV or dizqueTV. Open **Setup › Local channels**:

1. Name the channel, pick a number (suggested from 2000 up), and list one or more folders, one per line. Folders on this PC (`D:\TV\Seinfeld`) and network shares (`\\nas\media\TV\Frasier`) both work. In the PC app, **Add a folder…** opens a folder picker; **Check folders** shows how many videos TunerBox can see.
2. Choose the play order:
   * **In order:** episodes one after another, then it starts again.
   * **Rotate shows:** one episode of each show (subfolder) in turn, each show in episode order. Good for a "sitcoms" channel.
   * **Shuffle:** a new random order every time round.
3. **Create channel.** TunerBox reads the folders in the background (lengths are remembered, so re-reading is quick), and the channel appears in the guide with two days of listings.

Tuning in joins whatever is "on", like real TV. Titles, seasons and episodes come from file names such as `Show - S01E02 - Title.mkv` or `Show.S01E02.Title.1080p.mkv`. Samples and trailers are skipped. Use **Re-read folders** after adding new files.

Every file is converted to one common format while it plays (H.264 + AAC, with black bars as needed), so mixed libraries play smoothly from one file to the next. This uses some CPU on the PC; choose 720p under **Settings › Local channels picture size** for less. The PC running TunerBox needs access to the network share; if the share needs a password, open it once in File Explorer and tick "Remember my credentials".

## HDHomeRun antenna tuner
Use a SiliconDust HDHomeRun as a channel source: **Setup › Playlists › Add a playlist › Type: HDHomeRun antenna tuner**, then **Find tuners on my network** (or type its IP address) and **Add**.

* Antenna channels are matched to your lineup by callsign, using the local over-the-air lineup from Gracenote. For example a station on antenna 3.1 becomes its cable channel, and its subchannels go to the matching lineup channels. Antenna channels not on your lineup are added as extra channels.
* Matched local channels play from the antenna first, with any playlist feeds kept as backups.
* Antenna TV is MPEG-2, which browsers can't play, so tuner channels are re-encoded to H.264 by default. You can change this per playlist with the **Video** setting on its card.
* Each TV screen watching an antenna channel uses one tuner. When all tuners are busy, the channel shows No Signal.

This is separate from TunerBox *pretending* to be an HDHomeRun for Plex, Emby and Jellyfin, which still works as before.

## Phone remote
Pair a phone once and use it as the remote for any TunerBox screen (the PC window, a browser on the TV, the iPad), or to manage playlists from the sofa.

1. On the PC open **Setup › Phones › Pair a phone** (or on the TV screen: **Menu › Use a phone as the remote**).
2. Scan the QR code with the phone's camera, or open the address shown and type the 6-digit code. The code lasts 5 minutes.
3. The phone opens the remote: D-pad, OK, Guide, Menu, channel and volume, number pad, plus **Channels**, **Search** and **Sports** lists you tap to tune. **Add to Home Screen** makes it an app icon.

The phone must be on the same Wi-Fi. If several screens are on, pick one at the top. Paired phones are listed under Setup › Phones, where you can rename or remove them. Only a hash of each phone's key is stored.

**Lock setup** (Setup › Phones): only the PC and paired phones can change playlists, channels and settings. Watching TV stays open to every device on the network, so other TVs, tablets and Plex keep working.

## Stream cache
For streams that stutter or freeze, turn on the stream cache. TunerBox then holds back a few seconds of video before it starts playing, and if the stream hiccups it plays from that cache instead of freezing.

* Default for every screen: **Setup › Settings › Stream cache** (Off, 5, 10, 20, 30 or 60 seconds).
* One screen only (e.g. the iPad on Wi-Fi): **Menu › Stream cache** on that screen.
* HLS sources (`.m3u8`) fill the cache almost at once, because TunerBox starts a few segments back. Plain TS streams (most Xtream providers) arrive in real time, so the first picture takes about as long as the cache size.
* The server also reads ahead for each viewer, so a slow Wi-Fi device never makes the provider connection wait.

## Sleep timer and screensaver
Like a cable box, a TV screen asks **"Are you still watching?"** after 4 hours without a button press. If nobody presses a button within 2 minutes, it turns off and stops streaming. Once a screen is off, a screensaver (a drifting clock) starts after 10 minutes. Any button wakes it and turns the TV back on.

Neither happens while a recording is running or a phone remote is open. Turn either one on or off, or change its time, in **Menu › Sleep timer & screensaver** on the TV (OK switches or steps through the times), or in **Setup › Settings**. The settings apply to every screen.

## Search and Sports & PPV
* **Search** (`/`, Menu › Search, or SEARCH on the on-screen remote) looks through channel names and numbers, the next 7 days of the guide (shows, teams, games), and event feeds.
  * Type on a keyboard, or use the on-screen keyboard with a remote: ◀ ▶ move between the keyboard and the results.
  * OK on a show that's on now tunes it. A later show opens the record options.
* **Sports & PPV** (`T`, or Menu › Sports & PPV) has four tabs:
  * **On Now:** live games from the guide.
  * **Coming Up:** the next 2 days. OK opens the record options.
  * **Sports Channels:** sports networks with what's on now.
  * **Events & PPV:** event groups from your playlists, such as PPV, ESPN+, Peacock events, and NFL/NBA/NHL team feeds.
* **Event feeds only carry video while an event is on**, so the playlist scan usually marks them dead and they get no channel number.
  * Opening a group checks which feeds are live (if the last check is over 15 minutes old). Live feeds are listed first.
  * You can still try any feed. Last/Back returns to the channel you were watching.
  * Xtream feeds aren't checked unless *probe Xtream* is on in Settings, because providers limit connections.
  * Empty and ended slots ("NO EVENT STREAMING", "ESPN+ 12") are folded away under **Show empty and ended slots**.
  * Only US groups (and groups with no country label) are shown. To change that, set `sports_regions` in `settings.json`, e.g. `"US,CA,UK"`, or `"*"` for everything.

## Docker
Server-only image with ffmpeg included. Data (settings, database, recordings, logs, lineup CSV) lives in the `/data` volume.

```sh
docker compose up -d        # then open http://<host>:8642/ and go to /setup
```

* HDHomeRun auto-discovery needs `network_mode: host` (Linux); adding a tuner by IP works on any network.
* For Local channels, mount your videos (e.g. `/path/to/videos:/media:ro`) and use `/media/...` as the folder in Setup.
* If you turn on *require pairing*, pair your browser/phone from Setup like any other device; in a bridged container your PC isn't seen as loopback.

## How matching works
* **Locals** (WNBC, KABC, WGN…) match by callsign, including subchannels: a station's DT2 is matched to its second channel on the lineup, and so on. If no local stream exists, the national network feed (NBC, FOX…) is used. You can turn that off in Settings.
* **Cable networks** match by normalized name plus aliases (CNN = Cable News Network, HGTV, A&E…). The tvg-id only supports a name match, since many playlists set tvg-ids loosely.
* **Penalties** push down UK/Canada feeds, West/Pacific feeds, and Spanish feeds on English channels.
* **Backups.** Up to 4 more matching feeds are kept per channel. If the main one dies, the box tries them in order.
* **Skipped** on purpose: public-access, government-access and PPV slots.

## Files
* `data/channels.csv`: the Xfinity Grand Rapids lineup (`Channel Number,Channel Name`), read on every scan. It's skipped when you pick a different provider; to use your own CSV with another provider, set `lineup_csv_lineup` to `""` in `settings.json`
* `data/settings.json`: all settings, also editable in Setup › Settings
* `data/tunerbox.db`: playlists, mappings, guide, recordings (SQLite)
* `data/recordings/`: DVR output
* `data/tunerbox.log`: log

## Notes
* Gracenote is a free public endpoint, not an official API. If it ever breaks, add an XMLTV source for locals instead.
* Xtream providers often allow only 1-2 connections. Stream checks skip Xtream sources by default, and live TV plus a recording each use one connection.
