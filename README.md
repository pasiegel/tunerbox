# TunerBox

A personal IPTV "cable box": your local cable channel numbers and guide (pick your provider by ZIP or postal code in Setup), filled with feeds from your IPTV playlists. Inspired by MythTV's front end, Tunarr's channel mapping and IPTVnator's playlist handling.

* **Setup wizard.** A new install walks you through it once: your area for the guide, a tuner or playlist, a My Library folder, recordings on or off and a phone remote. Every step can be skipped.
* **Real channel numbers.** Your provider's lineup is the backbone: any US or Canadian cable, satellite or antenna lineup picked by ZIP/postal code (a sample cable lineup ships in `channels.csv`). Playlist channels are fuzzy-matched onto it.
* **Local guide.** Listings come from Gracenote (the data behind zap2it and TV Guide) for your provider, so the guide matches your numbers. US and Canada.
* **Everything else gets a number too.** Every working playlist channel that isn't on the lineup is numbered from 20000 up, with online XMLTV guides.
* **Manual mapping.** A Tunarr-style picker lets you search every feed, preview it, and pin the one you want.
* **Many kinds of source.** M3U playlists (a web address or a file), Xtream Codes logins (paste a `get.php` link and it switches to the Xtream API automatically), a SiliconDust HDHomeRun antenna tuner on your network, and your own video folders as 24/7 channels. Mix as many as you like; see [Sources and outputs](#sources-and-outputs).
* **Cable box UI.** Picture-in-picture guide grid, channel banner with now/next and progress, number-key tuning, last channel, and static "No Signal" screens. Dead feeds fail over to backups automatically.
* **DVR.** Record one show, a series on one channel, or new episodes on any channel. Recordings are saved as MP4. If TunerBox stops mid-recording, what was captured is kept; recordings don't start with under 2 GB free and stop (keeping what they have) below 0.5 GB. Don't need it? Turn off **Setup › Settings › DVR › Recordings** and Record, Recordings and the DVR tab disappear from every screen.
* **On Demand.** Movies and TV shows that come with your playlist (an Xtream login's movies and series, or the video files in an M3U), each on its own screen, with posters, resume, Continue Watching, My List and search. Off until you turn it on for a playlist.
* **My Library.** Your own movies and TV shows in On Demand, read straight from your NAS over the network (SMB, no mount needed) or from a folder, with details and posters from TMDB. Nothing is written next to your files.
* **Works with other apps.** `/playlist.m3u` and `/epg.xml` work in VLC or TiviMate, and TunerBox shows up as an HDHomeRun tuner in Plex, Emby, Jellyfin and Channels DVR.

## Sources and outputs
TunerBox can **use a real HDHomeRun as a source** and, separately, **act as an HDHomeRun** for other apps. Both work at the same time.

**Sources (what TunerBox plays from).** Any mix of these; every channel gets a number on your lineup, and a lineup channel found in more than one source keeps the others as backup feeds.

| Source | Where in Setup | Live channels | On Demand |
|---|---|---|---|
| **M3U playlist**: a web address or a file | Playlists › Add › *M3U playlist* | Yes. Streams can be http(s), HLS (`.m3u8`), udp/rtp, rtsp, rtmp or srt. A guide named in the playlist header (`x-tvg-url`) is read automatically. | Entries whose address is a video file (`.mp4`, `.mkv`…), when **On Demand** is on for the playlist |
| **Xtream Codes login**: server, username and password, or a `get.php` link | Playlists › Add › *Xtream Codes login* | Yes, as MPEG-TS or HLS (**Stream format**). The provider's guide and connection limit are read automatically. | Movies and TV shows, each with its own switch |
| **HDHomeRun antenna tuner** (SiliconDust) | Playlists › Add › *HDHomeRun antenna tuner*, then **Find tuners on my network** or type its IP address | Yes: antenna channels are matched to your lineup by callsign. One tuner per channel in use. See [HDHomeRun](#hdhomerun-as-a-source-and-as-a-tuner-for-plex). | - |
| **My Channels**: folders of your own videos | My Channels | Yes, as 24/7 channels from 2000 up | - |
| **My Library**: movie and TV folders on a NAS (SMB) or this computer | My Library | - | Yes, with TMDB details and posters |

Guide listings come from Gracenote for your lineup, plus the playlists' own guides and any online XMLTV guides you add in Setup › **Guide**.

**Outputs (what other apps can use).** The addresses are listed with **Copy** buttons in Setup › Settings › **Other apps**.

| Output | Address | Use it in |
|---|---|---|
| Web TV screen and Setup | `http://<tunerbox>:8642/` | Any browser: PC, TV, tablet, phone (also the phone remote) |
| M3U playlist | `http://<tunerbox>:8642/playlist.m3u` | VLC, TiviMate and other IPTV players |
| XMLTV guide | `http://<tunerbox>:8642/epg.xml` | The same players, and as the guide in Plex, Emby or Jellyfin |
| HDHomeRun tuner (emulated) | `http://<tunerbox>:8642` | Plex (Live TV & DVR), Emby, Jellyfin, Channels DVR: add it as an HDHomeRun tuner |
| Channels and guide as JSON | `/channels.json`, `/guide.json`, `/api/catchup` | The Roku app (compact and gzipped; `/guide.json` covers 3 hours back to 12 ahead, `?hours=` up to 72; `/channels.json?unmapped=0` leaves out the extra channels) |

## Getting started
New to TunerBox? The **[Getting started guide](docs/getting-started.md)** walks through the first setup with screenshots.

1. **Install.** Download the latest build for your computer from the **Releases** page: the Windows `TunerBox-Setup-….exe`, the Mac `.dmg` or the Linux `.tar.gz`. ffmpeg is included, nothing else to install. (Or run from source, below.)
2. **Open TunerBox.** It shows **No channels yet**; press Enter or click to open **Setup**.
3. **Pick your area.** It starts with a sample cable lineup. To use yours: Setup › **Guide**, enter your ZIP or postal code, press **Find providers**, choose your cable, satellite or antenna lineup and press **Save**.
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
| `run.bat --server` | server only, no window: for a PC that just feeds other devices. On Windows it puts a TunerBox icon in the notification area (system tray): double-click to open TunerBox, right-click for Setup or **Exit TunerBox**. Add `--no-tray` to leave the icon out. The Start menu's **TunerBox (server only)** shortcut runs this. |
| `run.bat --browser` | use your web browser instead of the app window |
| `run.bat --shortcut` | re-create the Desktop/Start Menu shortcuts |

On Linux the app window needs GTK or Qt (see the pywebview docs); without one it falls back to the browser.

## Installers and other devices
| Device | How |
|---|---|
| Windows, Mac, Linux | Download from the GitHub **Releases** page: Windows installer (`TunerBox-Setup-….exe`, installs to `C:\Program Files\TunerBox`; **Install for me only** needs no admin) or portable zip, Mac `.dmg`, Linux `.tar.gz`. ffmpeg is included. Data lives in `%LOCALAPPDATA%\TunerBox` (Windows), `~/Library/Application Support/TunerBox` (Mac) or `~/.local/share/TunerBox` (Linux). |
| Android phone/tablet, Android TV, Fire TV | Open `http://<pc-ip>:8642/` in the browser (Chrome, Silk). |
| iPad (iPadOS 15+) / iPhone | In Safari open `http://<pc-ip>:8642/`, then Share › **Add to Home Screen**. It opens full screen like an app. |

The PC running TunerBox does the tuning, guide and DVR, and must be on for the other devices. The first time it starts, Windows asks whether to allow it on the network: choose **Private networks**.

iPhone/iPad Safari plays the server's HLS output (`/api/hls/<number>`); other browsers use MPEG-TS over Media Source Extensions.

On a phone the TV screen switches to a touch layout: bigger buttons and panels that fit the screen. A **Close** button (bottom right) leaves the guide or a menu on phones, tablets and iPads, and in any browser once the mouse is used. Tap the picture to show or hide the controls (see [On-screen controls](#on-screen-controls)) and swipe up or down to change channel. Held upright, the top buttons show a small label under each icon so they all fit. On an iPhone, the full-screen button opens the video in Safari's own player.

Roku and other slow devices: set a playlist's **Video** to *Re-encode to 720p*, or add `?mode=roku720` to a stream URL (`http://<pc-ip>:8642/api/stream/<number>?mode=roku720`). It deinterlaces, caps the picture at 720p and the bitrate at 8 Mbps.

Building it yourself: `python packaging/build.py` on Windows (PyInstaller + Inno Setup 6), macOS (`.app`) or Linux. GitHub Actions does this for every release; see `RELEASING.md`.

## First-time setup
**Setup wizard.** A new install opens on a short wizard the first time: your ZIP or postal code for the guide (pick your provider, or *No local guide*), an antenna tuner, M3U playlist or Xtream login (it scans and says how many channels each one gave, or why it couldn't be read), a My Library folder, recordings on or off, and pairing a phone. Every step has **Skip**, and **Skip setup** leaves it altogether; it works with a mouse, a touch screen or a TV remote. It only runs once: installs that were set up before it existed never see it, and **Setup › Overview › Setup wizard** runs it again. Everything it does can also be done by hand:

1. With no channels, the screen says **No channels yet**. Press Enter or click it to open **Setup** (also Menu › Channel Setup).
2. To use your own provider's channel numbers, go to **Guide**, enter your ZIP or postal code and press **Find providers**. Choose your provider (for example *Xfinity - Digital*, *DIRECTV* or *Local Over the Air Broadcast* for an antenna) and press **Save**. Its channel numbers become your lineup.
3. Go to **Playlists** and add your M3U URL or file, your Xtream server, username and password, or an HDHomeRun antenna tuner (see [Sources and outputs](#sources-and-outputs) for everything TunerBox can use). An address with a second address inside it (one pasted after text already in the box) is refused: clear the box and paste it again.
4. Optional: press **Groups…** on the playlist to untick groups you don't want, such as other countries, adult or radio. **Check all** / **Uncheck all** apply to the groups listed, so after picking a country or typing a search they only change those groups (the buttons then show the count, e.g. *Uncheck 93 listed*). **Only keep listed** keeps the listed groups and hides every other one.
   * Hidden groups are removed straight away. Ticking one again rescans the playlist to bring it back.
5. Press **Scan playlists now**. The scan loads the playlist, checks which streams work, matches channels to your lineup, numbers the leftovers, and downloads the guides. The first scan takes a few minutes.
6. Go to **Channel Lineup** and set the filter to *Weak matches* to review guesses. Press **Change** to search and preview feeds, then **Use** to pin one. Manual picks survive rescans. **Feeds** shows a channel's main feed and backups, and **Remove** drops a wrong one.
7. Press **Watch TV**.

**Backing up your playlists, or moving them to another PC:** in **Setup › Playlists**, **Export playlists** saves every playlist (with its login, hidden groups, skip pattern and video setting) as a `tunerbox-playlists-<date>.json` file. **Import playlists…** on this or another TunerBox adds them back and scans; a playlist that's already there (same address and login) is updated instead of added twice. The file holds your passwords in plain text, so keep it private. Exporting only works on the TunerBox PC itself or a paired phone.

Changing provider later replaces the lineup: press **Scan playlists now** again afterwards.

## Remote / keyboard
| Key | Live TV | Guide |
|---|---|---|
| 0-9 | tune channel | jump to channel |
| ↑ ↓ / PgUp PgDn | channel up/down | move / page |
| ← → | volume | move through time |
| Enter | info banner | watch, or record options |
| H (Home) | Home | Home |
| G | guide | close guide |
| I | full info | |
| * (or A) | add / remove channel in Favorites | add / remove highlighted channel |
| V | Favorites guide | switch Favorites only / all channels |
| ▲ on the first channel, then ◀ ▶ (or C) | | filter by genre: All, Movies, Sports, News, Kids |
| R | record current show | record options |
| L / Backspace | last channel | back |
| D | recordings | |
| / | search | search |
| T | Sports & Events | Sports & Events |
| X | game stats | |
| O | On Demand | On Demand |
| M | menu | |
| U, +/− | mute, volume | |
| F / F11 | full screen | |

**Using a real remote:** open **Menu › Remote control setup** on the device the remote is paired with. Highlight an action, press OK, then press the remote button. Any key a remote sends works, including odd codes, media keys and color buttons. Game controllers and gamepad-style Bluetooth remotes work too. Mappings are saved per device; **Save as default for all devices** stores them on the server. Arrow keys, Enter and Esc always work on that screen, so you can't lock yourself out.

### On-screen controls
Mouse and touch also work. Move the mouse (or tap the picture) for the on-screen controls; click the video for the banner; swipe up/down to change channel.

* **Top:** **Home**, **Guide**, **On Demand**, **Sports & Events** and **Search**, plus full screen on the right.
* **Bottom:** what's on, then channel up/down, **Last**, **Info**, **Favorite** and **Record**, the volume, **Menu**, and **More** (⋯) for **Recordings** and the **Favorites guide**.

Buttons for features that are off (On Demand, Recordings, Home) are left out. On a phone the bottom buttons are icons only; on a tablet held upright they wrap onto a second row.

**Watching a recording:** Space/OK pause, ← → skip back 10 s / ahead 30 s, PgUp/PgDn 5 minutes. **I** shows the recording's info (episode, when it was recorded, description, how much you've watched), **G** opens the guide over it (closing the guide goes back to the recording, picking a channel switches to live TV), Back hides the info or stops, **S** stops.

## Home
When the TV turns on it opens on **Home**, with the last channel playing in the corner. Press **H** (or the remote's Home button, **Menu › Home**, or **Home** on the on-screen controls and the phone remote) to get back to it from anywhere; Back or H again returns to full-screen TV.

Home has shortcuts (Guide, On Demand, Sports, Recordings, Search, Menu) and then rows, each shown only when it has something in it:
* **★ Favorites on now** and **Recent channels** (the ones this screen tuned to): what's on, with the show's picture and how far in it is. OK tunes; * adds or removes a favorite.
* **Continue Watching:** movies and episodes you stopped part-way through, then recordings in a row of their own, the latest first. OK resumes. When you finish an episode, the show stays with the next one as **Up next**. A film or show you started in more than one version (another language, 4K, another playlist) shows once, as the version you played last; **Setup › Settings › Screens › Continue Watching: show every version** gives each its own tile. **Remove from Continue Watching** in a title's options takes it off without marking it watched.
* **Sports on now:** games one of your channels or event feeds carries, with the score.
* **Recently recorded** and **Coming up** (scheduled recordings and reminders).

**Setup › Settings › Screens › Home rows** puts the rows in the order you want (drag them, or use the arrows) and switches off the ones you don't want; **Reset to default** puts them back.

Arrows move, OK opens, number keys tune. To start one screen on live TV instead, turn off **Menu › Settings › Start on Home** there; opening TunerBox with `?ch=<number>` always starts on that channel. To turn Home off for every screen, untick **Setup › Settings › Screens › Home screen**: screens then start on live TV and the Home button opens the guide (each screen picks this up the next time it's turned on).

Recordings remember where you stopped, on the server, so any screen can **Resume** them (from Home or **Recordings**).

## Guide genre filter
In the guide, press **▲** on the first channel to highlight the genre picker next to the date, then **◀ ▶** to pick **All, Movies, Sports, News or Kids**; **OK** or **▼** goes back to the channels. That needs only the arrows and OK, so it works with any remote. **C** on a keyboard steps through the genres too (or give it a button of its own in **Remote control setup**: *Guide: filter by genre*), and on a touch screen you tap the day at the top left. Only channels with a show of that genre on in the hours shown are listed, and their other shows are dimmed; moving through time updates the list. It works with the Favorites guide too (V). Genres come from the guide's categories, so channels whose guide has none never show up in a filter.

## Catch-up TV
Some providers keep an archive of what their channels showed in the last few days: catch-up, also called timeshift or replay. TunerBox reads it from the playlist and shows it in the guide. No setup is needed.

* **Xtream logins:** channels the provider marks with an archive (`tv_archive`), with the number of days it keeps.
* **M3U playlists:** the `catchup`, `catchup-days` and `catchup-source` attributes (as in Kodi and TiviMate: `default`, `append`, `shift`, `flussonic` and `xc`), or the older `timeshift` / `tvg-rec`.

On a channel with catch-up:

* **You can tell:** the info banner shows **↺ CATCH-UP 3 DAYS** (or however long that channel's archive is), and the Menu's *This channel* heading says the same.
* **Start over:** Menu › **↺ Start this show over** (or **Start over** under **More** on the on-screen controls) plays the show on now from the beginning. It's in the Menu rather than on a key, so it works with any remote.
* **Watch shows that already aired:** in the guide, **◀** goes back as far as the archive does. Shows you can still watch have a **↺**. OK on one gives **Watch (catch-up)**; on the show on now, **Start over**.

A catch-up show plays in the On Demand player: OK pauses, **◀ ▶** move a minute (the archive starts on the minute), PgUp/PgDn 5 minutes, and Back returns to live TV. It uses one of the provider's connections, like a live channel. Only the channels the provider keeps an archive for have it. With a big provider that is often a few hundred channels, mostly UK and European ones. The guide keeps past listings for those channels for as long as their archive.

## Themes
**Setup › Settings › Screens › Theme** sets the colours for every TV screen, Setup and the phone remote: **Midnight** (the default), **Classic Blue**, **Retro 90s**, **High Contrast** and **Light**. A TV screen can pick its own in **Menu › Theme** (kept in that browser); *Use the default* follows Setup again. Light is for Setup and the phone remote: TV screens stay dark with it, since their text sits on pictures and video.

## Favorites
Star the channels you watch most, then see just those in the guide.
* **Add or remove:** press **\*** (or **A**) on live TV or on a channel in the guide, click the ☆ next to a channel number in the guide, use **Menu › Add to Favorites**, or the *Add to Favorites* line when you press OK on a future show.
* **See them:** **V** or **Menu › ★ Favorites guide** opens the guide with only favorites; **V** in the guide switches between favorites and all channels. Typing a number in the Favorites guide jumps to that channel among all channels if it isn't a favorite.
* Favorites are kept on the TunerBox PC, so every TV, tablet and the phone remote share them. On the phone remote, favorites are listed first in Channels with a ★ to add or remove.

## My Channels (your videos as 24/7 channels)
Turn folders of videos into channels that play around the clock, like ErsatzTV or dizqueTV. Open **Setup › My Channels**:

1. Name the channel, pick a number (suggested from 2000 up), and list one or more folders, one per line. Folders on this PC (`D:\TV\Seinfeld`) and network shares (`\\nas\media\TV\Frasier`) both work. In the PC app, **Add a folder…** opens a folder picker; **Check folders** shows how many videos TunerBox can see.
2. Choose the play order:
   * **In order:** episodes one after another, then it starts again.
   * **Rotate shows:** one episode of each show (subfolder) in turn, each show in episode order. Good for a "sitcoms" channel.
   * **Shuffle:** a new random order every time round.
3. **Create channel.** TunerBox reads the folders in the background (lengths are remembered, so re-reading is quick), and the channel appears in the guide with two days of listings.

Tuning in joins whatever is "on", like real TV. Titles, seasons and episodes come from file names such as `Show - S01E02 - Title.mkv` or `Show.S01E02.Title.1080p.mkv`. Samples and trailers are skipped. Use **Re-read folders** after adding new files.

Every file is converted to one common format while it plays (H.264 + AAC, with black bars as needed), so mixed libraries play smoothly from one file to the next. This uses some CPU on the PC; choose 720p under **Settings › Local channels picture size** for less. The PC running TunerBox needs access to the network share; if the share needs a password, open it once in File Explorer and tick "Remember my credentials".

## My Library (your own movies and TV shows in On Demand)
Setup › **My Library** adds your own movie and TV folders to On Demand, next to your playlists' titles: same posters, resume, Continue Watching, My List and search.

1. Press **+ Add library**, pick **Movies** or **TV shows**, enter the **folder or network location** (`\\nas\Media\Movies`, `smb://192.168.1.20/Media/Movies`, `D:\Movies`, or in Docker `/media/Movies`) and, for a network share, the NAS username and password. **Connect & browse** checks the login and lets you click into folders and **Use this folder**.
2. **Add library**. The folder is scanned in the background; each library becomes a category in On Demand. A library's **Edit** changes it, hides it from On Demand or removes it.
3. For details and posters, get a free key at themoviedb.org (Settings › API), then on the **Details and posters** card press **Set up**, paste the *API Key* or *API Read Access Token* and **Save**.
4. Check **Matches** › *Needs review*: **Fix** searches TMDB so you can pick the right title (kept through rescans), **Leave out** hides a file. Matches can show just movies or just TV shows, and *No video details* lists the videos whose length, resolution and sound haven't been read yet (or couldn't be). The **Quality** column shows each file's type, size, resolution, HDR, sound and bitrate.

* **Nothing is written to your shares.** Text goes in TunerBox's database and posters in its data folder (`posters/`). Renaming or moving files and folders is safe: they're recognised and keep where you stopped, My List and their match (a file renamed to a different title is looked up again).
* **Naming:** movies as `Dune Part Two (2024)/anything.mkv` or `Dune Part Two (2024).mkv` (scene names like `Dune.Part.Two.2024.2160p.WEB-DL.mkv` work too); shows as `Show/Season 01/Show - S01E02 - Title.mkv` (`1x02` and `Season 2/04 - Title.mkv` work too). Samples, trailers and `Extras` folders are skipped.
* **Network shares** are read with a built-in SMB2/3 client, so Docker doesn't need the share mounted.
* **Folders on the TunerBox computer** work too: `D:\Movies` on Windows, `/home/me/Movies` on Linux. A drive letter mapped to a NAS works as long as TunerBox runs as the same Windows user.
* **In Docker**, TunerBox only sees its container. Mount the folder as a volume and enter the path inside the container: with `-v /srv/media:/media:ro`, the host's `/srv/media/Movies` is `/media/Movies` in Setup. Read-only is fine. A NAS share mounted on the host (CIFS/NFS) works the same way, and is the easy route for guest-only shares.
* **Who can add folders:** the TunerBox PC and paired devices; on a Docker server, also any browser on your home network until **Lock setup** is on (see [Security notes](#security-notes)).
* **Automatic tasks** (the card on the page; **Edit** to change them, **Run all** to run them now) keep the libraries up to date. Each can run every 30 minutes up to every week, at a set time (and, weekly, on a set day):
  * **Find new and changed videos** (every 30 minutes): adds new videos, follows renamed and moved ones, and hides ones that are gone. New videos are in On Demand as soon as the scan finds them. Every video that still needs its details (length, resolution, HDR, audio, for On Demand and Pick a version) then gets them in a job of its own, however many there are, so a long run of reading files never holds up the next scan. Files are read 2 at once, with a short rest after each (a quarter of the time it took, at most a second); *video details: read* on this task's row changes that from 1 (the gentlest) to 4 (go back to 1 if the log says the NAS stopped answering). My Library has its own job lanes, so channel scans and guide updates don't wait for it. Folders that haven't changed are skipped, so it's light on the NAS; each folder is still read in full once a day.
  * **Update details and posters** (daily at 4 am): titles TMDB didn't know are tried again after a week, and details are refreshed every 30 days.
  * **Remove missing videos** (daily at 4 am): a video whose file is gone is hidden at once and comes back as it was if the file does; after 30 days (*once missing for*) it's taken out. Its watch progress and My List entry are kept another 90 days in case it returns. Your files are never deleted.
* **Scan now** does a quick scan of every library; a library's **Full rescan** starts that library over: every folder is read again (for a file replaced under the same name, which a quick scan doesn't notice), every title's details and posters are downloaded again (titles you fixed by hand keep their match), and every video's details are grabbed again, however long that takes.
* **New in My Library:** titles found in the last 30 days (and shows with a new episode) have their own category in On Demand and a row on Home.
* **Playing** uses the same player as playlist titles. Files are converted while they play only as far as needed (sound to AAC); set **Video** to re-encode for players that can't handle the original.
* **Audio tracks and subtitles:** while one of your own titles plays, Menu › **This title** has **Audio** (when the file has more than one track, e.g. a commentary or another language) and **Subtitles**. The choice is kept for that movie or show, so the next episode uses the same.
  * Subtitles can be text ones inside the file (SRT, ASS, WebVTT, MP4 text) or subtitle files next to the video: `Movie.srt`, `Movie.en.srt`, `Movie.en.forced.srt`, or any `.srt`/`.ass`/`.vtt` in a `Subs` folder beside it. They're drawn over the picture and stay in step when you skip.
  * The first time a film's built-in subtitles are turned on, TunerBox reads the whole file for them (about 30 seconds for a 2½-hour film over the network); after that they come straight from its cache (`cache/subs` in its data folder, nothing is written next to your files).
  * Picture subtitles (PGS from Blu-ray, VobSub from DVD) are listed but can't be shown yet.
  * Playlist titles (Xtream, M3U) play their first audio track without subtitles: reading them would take a second provider connection.
* **Copies of the same film** in two libraries (or a library and a playlist) are one title with **Other versions**, named by their library or playlist when they have no language or quality label. **Your own copy comes first**: Search, the title's options and Play use it, ahead of the playlists' versions (and of *List these languages first*), and among equally good search matches films you own are listed first.

## HDHomeRun: as a source, and as a tuner for Plex
TunerBox works with HDHomeRun both ways, at the same time.

### A real HDHomeRun as a source
Use a SiliconDust HDHomeRun as a channel source: **Setup › Playlists › Add a playlist › Type: HDHomeRun antenna tuner**, then **Find tuners on my network** (or type its IP address) and **Add**.

* Antenna channels are matched to your lineup by callsign, using the local over-the-air lineup from Gracenote. For example a station on antenna 3.1 becomes its cable channel, and its subchannels go to the matching lineup channels. Antenna channels not on your lineup are added as extra channels.
* Matched local channels play from the antenna first, with any playlist feeds kept as backups.
* Antenna TV is MPEG-2, which browsers can't play, so tuner channels are re-encoded to H.264 by default. You can change this per playlist with the **Video** setting on its card.
* Each antenna channel being watched or recorded uses one tuner, however many screens are on it. When all tuners are busy, the screen says what's using them (see [Connection limits](#connection-limits)).

* Adding a TunerBox's own address here is refused, so TunerBox can't be added to itself.

### TunerBox as an HDHomeRun for Plex, Emby, Jellyfin and Channels DVR
TunerBox also *pretends* to be an HDHomeRun, so those apps can add it as a tuner and get every channel on your lineup, with your channel numbers, from all your sources (playlists, antenna and My Channels).

1. In the app, add an HDHomeRun tuner by its network address and enter TunerBox's address, `http://<tunerbox>:8642` (Setup › Settings › **Other apps** shows it with a **Copy** button). In Plex: **Settings › Live TV & DVR › Set up Plex DVR**, then *enter its network address manually*. TunerBox isn't found by the apps' automatic search, so type the address.
2. For the guide, use the XMLTV address `http://<tunerbox>:8642/epg.xml`, so the listings match TunerBox's numbers.

* **Tuners:** it offers as many as your playlists allow in total (when every playlist has a limit), otherwise 4; set **Tuners offered to Plex** in Settings to choose the number yourself. When they're all in use it answers the way a real HDHomeRun does ("All tuners in use").
* **Sharing:** Plex watching a channel that a TV screen or a recording is already on shares that one connection (see [Connection limits](#connection-limits)).
* **Two TunerBoxes:** give each its own **Network name** (Setup › Settings › Other apps) before adding them, so the apps tell them apart. Each one has its own HDHomeRun device ID.

## Connection limits
Xtream accounts usually allow only 1 or 2 streams at once, and an HDHomeRun has a fixed number of tuners. TunerBox keeps to that limit for each playlist, so a channel that can't start tells you why instead of showing No Signal.

* **The limit:** Xtream accounts and HDHomeRun tuners report it themselves. A plain M3U can't, so it has no limit until you set one: **Setup › Playlists › Connections** on its card (this also overrides what an Xtream account reports).
* **What counts:** one connection per channel or event feed that's on (however many screens, Plex and recordings share it), and one per On Demand title playing. Changing channel gives up the old one first.
* **When a playlist is full:** a channel with a backup feed on another playlist plays from there. Otherwise the screen shows **All connections in use**, lists what's using them (for example *Recording: The News* on 8) and offers **Watch 8 instead**: a channel that's already on can be watched on more screens for free.
* **Recordings come first:** a recording that's starting takes a connection from live TV or On Demand when the playlist is full. That screen then gets the same message, with the recording's channel to switch to. Turn this off in **Setup › Settings › DVR › Recordings come first**: the recording then waits for a connection to free up. A recording never takes over from another recording.
* **Conflicts:** recordings on different channels that overlap by more than a playlist allows are marked **⚠ CONFLICT** under Coming up in Recordings, with the reason, and you're warned when you schedule one. A channel whose backup feed is on another playlist with room doesn't count as a conflict.
* **Setup › Tuners** shows each playlist's connections in use against its limit, what each one is playing (with the show on now), and who is using it: a TV screen, a recording, a Roku by its name, or Plex, VLC, Kodi, Jellyfin, Emby or Channels DVR.
* **Stop** (Setup › Tuners, on each stream) ends a stuck or unwanted stream for everyone on it and frees its connection, after asking. A recording on it is stopped too. TV screens show *Stopped from Setup* instead of reconnecting, until a channel is picked; apps like Roku and Plex can't reopen it for 30 seconds.

## Phone remote
Pair a phone once and use it as the remote for any TunerBox screen (the PC window, a browser on the TV, the iPad), or to manage playlists from the sofa.

1. On the PC open **Setup › Phones › Pair a phone** (or on the TV screen: **Menu › Use a phone as the remote**). On Docker or a NAS, open Setup from any browser at home.
2. Scan the QR code with the phone's camera, or open the address shown and type the 6-digit code. The code lasts 5 minutes.
3. The phone opens the remote: D-pad, OK, Guide, Menu, channel and volume, number pad, plus **Channels**, **Search** and **Sports** lists you tap to tune. **Add to Home Screen** makes it an app icon.

The phone must be on the same Wi-Fi. If several screens are on, pick one at the top. Paired phones are listed under Setup › Phones, where you can rename or remove them. Only a hash of each phone's key is stored.

**On Docker or a NAS** there's no screen of its own, so nobody browses from "the PC". Until setup is locked, any browser on your home network counts as the PC: it can pair phones, change every setting and add folders on the server. The QR code and link use the address that browser opened TunerBox with. Pair the browser you use for Setup too (Pair a phone, then open `/pair` in that browser and type the code), so it can still change things once the lock is on.

**Lock setup** (Setup › Phones): only the PC and paired phones can change playlists, channels and settings, delete recordings, or see a channel's original stream address (it can contain your provider login). Watching TV and scheduling recordings stay open to every device on the network, so other TVs, tablets and Plex keep working. Turning the lock on or off needs the PC or a paired device, so whoever turns it on can still get in.

Whether or not setup is locked, the settings that name a program or a place on disk (ffmpeg path, recordings folder, lineup CSV, guide files on disk, port, HTTPS) can only be changed on the PC itself; on Docker, from any browser at home until setup is locked, then from paired devices. Folders and playlist files on the TunerBox computer (My Library, a playlist file) need the PC or a paired device in the same way. Exporting playlists, which holds their logins, always needs the PC or a paired device.

## HTTPS (install as an app)
Browsers only offer **Install app** (Android Chrome, desktop Chrome and Edge) for secure pages. TunerBox can also serve itself over HTTPS on a second port, **8643**, while the normal address on **8642** stays plain http and works exactly as before. Plex, Emby, Jellyfin, Channels, VLC, the Roku app and `/playlist.m3u` keep using http on 8642.

* **Turn it on:** **Setup › Settings › HTTPS** on the TunerBox PC, then restart TunerBox. The page shows the HTTPS address and any problem (for example, port 8643 already used by another program; http still starts either way).
* **Certificate:** TunerBox makes its own certificate authority once, and from it a certificate for this PC's names and network addresses (renewed by itself). In Docker, add the host's address under **Extra addresses for the certificate**, and map port 8643.
* **Each device, once:** download `http://<this PC>:8642/tunerbox-ca.crt` on it and trust it (Setup › Settings › HTTPS has the steps for Android, iPhone/iPad and Windows). Then open `https://<this PC>:8643/` and install it; the phone remote installs the same way from `/remote`.
* Over HTTPS, channel logos and posters from plain-http sites come through TunerBox (`/api/img`), since browsers block them on secure pages. Setup and the phone pairing code always give the http addresses.
* iPhone and iPad don't need any of this: Safari's **Add to Home Screen** works over plain http.

## Stream cache
For streams that stutter or freeze, turn on the stream cache. TunerBox then holds back a few seconds of video before it starts playing, and if the stream hiccups it plays from that cache instead of freezing.

* Default for every screen: **Setup › Settings › Stream cache** (Off, 5, 10, 20, 30 or 60 seconds).
* One screen only (e.g. the iPad on Wi-Fi): **Menu › Stream cache** on that screen.
* HLS sources (`.m3u8`) fill the cache almost at once, because TunerBox starts a few segments back. Plain TS streams (most Xtream providers) arrive in real time, so the first picture takes about as long as the cache size.
* The server also reads ahead for each viewer, so a slow Wi-Fi device never makes the provider connection (or the other screens) wait.
* A screen that tunes to a channel another screen is already on fills its cache at once: the server keeps the last few seconds of every channel being watched.

## Log
**Setup › Log** follows TunerBox's log as it's written: channels, movies and episodes starting and stopping (*On Demand: playing …*), scans, guide updates, My Library's scans, video details and TMDB lookups, and problems. Filter by text, or show only warnings and errors, what's playing, or My Library. **Pause** holds new lines until **Follow**. Logins, passwords and keys are hidden, in the view and in the file. With **Lock setup** on, only the TunerBox computer and paired phones can open it.

## Stream stats
To see how a stream is doing, turn on **Menu › Stream stats** on that screen. A box in the top-left corner updates every second while something plays. It shows the picture size, codec and frame rate, the audio format, the download rate, how many seconds are cached ahead, dropped frames (in red when the screen can't keep up) and whether the player is waiting for data. It also says when TunerBox is converting the picture to H.264 for that screen. The setting is remembered for that screen only. On iPhone and iPad, Safari's built-in player reports less, so the codecs, frame rate and download rate are left out. The same goes for recordings that aren't .ts files, which play as a plain file.

## Sleep timer and screensaver
Like a cable box, a TV screen asks **"Are you still watching?"** after 4 hours without a button press. If nobody presses a button within 2 minutes, it turns off and stops streaming. Once a screen is off, a screensaver (a drifting clock) starts after 10 minutes. Any button wakes it and turns the TV back on.

Neither happens while a recording is running or a phone remote is open. Turn either one on or off, or change its time, in **Menu › Sleep timer & screensaver** on the TV (OK switches or steps through the times), or in **Setup › Settings**. The settings apply to every screen.

## Search and Sports & Events
* **Search** (`/`, Menu › Search, or **Search** on the on-screen controls) looks through channel names and numbers, the next 7 days of the guide (shows, teams, games), and event feeds.
  * Type on a keyboard, or use the on-screen keyboard with a remote: ◀ ▶ move between the keyboard and the results.
  * OK on a show that's on now tunes it. A later show opens the record options.
* **Sports & Events** (`T`, Menu › Sports & Events, or **Sports & Events** on the on-screen controls) has four tabs:
  * **On Now:** live games from the guide.
  * **Coming Up:** the next 2 days. OK opens the record options.
  * **Live games and scores.** Pick the leagues you follow in Setup › Settings › **Sports** (NFL, college football, NBA, college basketball, MLB and NHL to start with; WNBA and soccer leagues are there too). Their games come from ESPN's public scoreboard, which needs no account, and each one is matched to a guide listing or an event feed by team names and start time. Matched games show in On Now and Coming Up with the score, even when the guide doesn't label the listing as sports or the game runs past its slot. Games none of your channels carry are listed dimmed with where they air; turn that off with *List games you have no channel for*.
  * **Sports Channels:** sports networks with what's on now.
  * **Events:** event groups from your playlists, such as PPV, PLE, special events, ESPN+, Peacock events, and NFL/NBA/NHL team feeds.
* **Event feeds only carry video while an event is on**, so the playlist scan usually marks them dead and they get no channel number.
  * Opening a group checks which feeds are live (if the last check is over 15 minutes old). Live feeds are listed first.
  * You can still try any feed. Last/Back returns to the channel you were watching.
  * Xtream feeds aren't checked unless *probe Xtream* is on in Settings, because providers limit connections.
  * Empty and ended slots ("NO EVENT STREAMING", "ESPN+ 12") are folded away under **Show empty and ended slots**.
  * Only US groups (and groups with no country label) are shown. To change that, set `sports_regions` in `settings.json`, e.g. `"US,CA,UK"`, or `"*"` for everything.

### Game stats
While a game is on, press **X** (or Menu › **Game stats & live scores**) for its box score in a card in the top right corner, over the picture, which keeps playing full size. The card's top line has each team's logo, short name and score, the leader brighter. On the **Scores** tab it becomes a slim panel down the right with a tile per game, like Home's (a team without a logo shows its abbreviation). On a phone held upright the panel is a sheet along the bottom with the picture above it.

* **Tabs** (◀ ▶): each team's players, **Team** (team stats), **Plays** (the latest 10, newest first) and **Scores** (live scores). ▲ ▼ scroll.
  * Basketball shows points, rebounds, assists and field goals; football passing, rushing and receiving; hockey skaters and goalies; baseball batting and pitching. Soccer has team stats only.
  * Stats start when the game does, and update every 20 seconds.
* **Scores** lists every other game being played in the leagues you follow (men's college basketball is Division I only; women's college basketball is left out), your teams' games first, up to 15 at a time. ▲ ▼ pick one and OK watches it. If more than one channel or event feed has it, you choose which, as on the Sports screen. The panel stays open and switches to that game.
* On a channel with no game the scoreboard knows about, the panel shows just the **Scores** tab, or says no games are live. With no leagues or teams picked in Setup › Settings › **Sports**, it says to turn them on there.
* Channel up/down, number keys, volume and mute still work with the panel open. Back or X closes it.
* It works for games in the leagues ticked in Setup › Settings › **Sports** that are matched to the channel or event feed you're watching (the ones with a score in Sports › On Now). Stats come from ESPN's public game summaries, with no account needed.
* **Roku:** **\*** opens the Menu: choose **Game stats & live scores** (also under **More** on the control bar).

## On Demand (movies and TV shows)
Many playlists come with a library of movies and shows besides the live channels. TunerBox leaves it alone until you turn it on in Setup › **Playlists**, on the playlist's card: Until a playlist has it turned on, On Demand is left out of Home, the Menu, the key hints and the phone remote.

* **Xtream logins** have two switches, **On Demand: movies** and **On Demand: TV shows**. The lists are read when you turn one on and again at every full scan.
* **Plain M3U playlists** have one switch: entries whose address is a video file (`.mp4`, `.mkv`…) become titles instead of channels. Names like `Show S02 E04` are grouped into a show with seasons.
* **Categories…** next to each switch picks which movie or TV show categories are shown (untick the ones you don't want). Categories named like `EN - MOVIES` can be filtered by language there too.
* **Languages…** picks which labels are shown. Big providers put a label in front of every title: a language (`EN - Dune`, `DE - …`), a language and quality (`4K-EN`, `IN-EN`) or a platform (`NF` for Netflix, `D+` for Disney+). TunerBox cuts the label off the name, shows it as a small badge, and lists each label with how many titles have it and an example, so unfamiliar ones are easy to judge. Untick the languages you don't watch: with only English, a 235,000-title catalogue shrinks to about 37,000. Labels a provider adds later start unticked. Titles with no label are their own entry. This also works for TV shows, whose categories often have no language in them.

Press `O` (or Menu › **On Demand**, or **On Demand** on the on-screen controls) to open On Demand, where you left it. Live TV keeps playing in the corner. The tabs are **Movies** and **TV Shows** (each with its categories and posters), **Continue Watching** and **My List** (movies and shows together) and **Search**. Arrows move between the tabs, the categories and the titles; OK on a movie gives Resume / Play / My List; OK on a show opens its seasons and episodes; `*` adds to My List; `O` or Back closes it.

**Search** (`/` in On Demand) looks through the movies and shows. Accents and punctuation don't matter (*amelie* finds *Amélie*), the words can be in any order, a year narrows it (*dune 2021*), small typos still find it, and movies are found by actor or director too. A film the playlist has under several labels (*Dune* in EN, 4K-EN, DE…) is listed once; its options have **Other versions** to pick another language or quality. **Pick a version** lists the best first (your own copy, then the best picture, then the highest bitrate) and shows what's known of each: resolution, HDR, sound, bitrate, file type and size (a provider's size is worked out from its bitrate and length, marked ≈). Where it comes from: what ffmpeg read when that version last played, else the provider's own details of it (most Xtream panels send them), else its file, read when the playlist has a connection free (the first 2 MB, or, when those don't say, as far into the file as ffmpeg needs: an MP4 keeps its details at the end). Each version says *Reading…* while it's read, or why it couldn't be, and **Read details again** tries those again. When the only connection free is the one this screen is using (live TV in the corner, or a movie), it's paused for the reading and comes back on by itself, a movie from where it was; another screen's stream is never stopped for it; your own copy is read from the NAS there and then if My Library hasn't got to it yet. Settings › *Pick a version: movie details from the files* picks when the files are read: **Automatically** (when Pick a version opens), **Only when I ask** (when you press Read details), or **Off**; *Pause what's playing to read them* turns the pausing off. Until then, what the file name or label says (*1080p x264 DTS 5.1*, *4K*) is shown. Each version's length is shown when they differ. Switching between a 24 fps and a 25 fps copy keeps your place in the film. The main **Search** (`/` while watching) finds movies, shows and episodes alongside channels and guide listings.

While a title plays: OK or Space pauses, ← goes back 10 seconds, → skips 30, PgUp/PgDn jump 5 minutes, Back stops and returns to where you were. With a mouse or touch, click or drag the **seek bar** to go to any point (a time tip follows the pointer, and the bar stays up while you use it); it works for catch-up and recordings too. Titles start quickly: the first seconds are sent as fast as they arrive, then at playing speed. Where you stopped is remembered on the server, so any screen can resume it. A title uses one of your provider's connections while it plays, like a live channel, and follows the playlist's **Video** setting (pick a re-encode there if a movie has picture but won't play in the browser).

## Docker
Server-only image with ffmpeg included. Data (settings, database, recordings, logs, lineup CSV) lives in the `/data` volume.

Each release publishes the image `ghcr.io/pasiegel/tunerbox` for 64-bit PCs (amd64) and ARM64 (Raspberry Pi 4/5). Docker picks the right one by itself. It's public: no login is needed to pull it.

| Tag | Use it for |
|---|---|
| `latest` | the newest release |
| `1.5` | the newest 1.5.x release (bug fixes, no big changes) |
| `1.5.0` | exactly that release, never changes |

**First install**

```sh
docker pull ghcr.io/pasiegel/tunerbox:latest
docker run -d --name tunerbox --restart unless-stopped -p 8642:8642   -v tunerbox-data:/data -e TZ=America/New_York ghcr.io/pasiegel/tunerbox:latest
```

Or with Compose: copy `compose.yaml` from this repository into a folder, then run `docker compose up -d` there. Open `http://<host>:8642/setup` to add your playlists.

**Updating to a new release.** Your settings, guide and recordings are in the `tunerbox-data` volume, so replacing the container keeps them.

```sh
# Compose (in the folder with compose.yaml)
docker compose pull && docker compose up -d

# docker run: pull, then remove and start the container again with the same options as before
docker pull ghcr.io/pasiegel/tunerbox:latest
docker stop tunerbox && docker rm tunerbox
docker run -d --name tunerbox --restart unless-stopped -p 8642:8642   -v tunerbox-data:/data -e TZ=America/New_York ghcr.io/pasiegel/tunerbox:latest

docker image prune -f        # optional: delete the old image
```

* **Podman** takes the same commands (`podman pull …`, `podman run …`). With a systemd Quadlet that has `AutoUpdate=registry`, `sudo podman auto-update` pulls the new image and restarts TunerBox; the `podman-auto-update.timer` does it daily on its own.
* **Portainer:** open the container, **Recreate**, and tick **Re-pull image**.
* **Watchtower** (`containrrr/watchtower`) can update it automatically if you already use it.
* **Which version is running?** `docker image inspect ghcr.io/pasiegel/tunerbox:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}'`.
* **Going back** to an older release: use its tag (for example `ghcr.io/pasiegel/tunerbox:1.4.7`) in place of `latest`. Back up the volume first: a newer version may have upgraded the database.

To build the image yourself instead, use `build: .` in `compose.yaml` (or `docker build -t tunerbox .`).

* Set `TZ` (in `compose.yaml`, or `-e TZ=` with `docker run`) to your time zone, e.g. `America/Chicago`: guide times and DVR scheduling follow it.
* HDHomeRun auto-discovery needs `network_mode: host` (Linux); adding a tuner by IP works on any network.
* For My Channels and My Library, mount your videos (e.g. `/path/to/videos:/media:ro`) and use `/media/...` as the folder in Setup. The path is the one inside the container, not the host's.
* The image sets `TUNERBOX_TRUST_LAN=1`: with no screen of its own, any browser on your home network counts as the TunerBox PC (settings, folders, pairing) until **Setup › Phones › Lock setup** is on. Pair your browser before turning the lock on; afterwards only paired devices can change setup.
* **Unraid:** see [Unraid](#unraid) below.
* **Two TunerBoxes on one host** (say one for the family and one private): give the second its own data volume and port, e.g. `-v tunerbox-private-data:/data` and `-p 8644:8644` with the command `python -m tunerbox --server --port 8644` (with host networking, just the `--port`). Then set **Setup › Settings › Other apps › Network name** on each (before adding them as tuners), so Plex, Jellyfin, Emby and Channels DVR list them under different names. Home-screen icons stay "TunerBox". Each one gets its own HDHomeRun device ID for Plex automatically.

### Unraid

TunerBox isn't in Community Applications, so add it by hand. It takes a few minutes and updates like any other container.

1. Open the **Docker** tab and press **Add Container** at the bottom. (If it's missing, turn Docker on under **Settings › Docker**.)
2. Turn on **Advanced View** (top right) and fill in:

   | Field | Value |
   |---|---|
   | Name | `TunerBox` |
   | Repository | `ghcr.io/pasiegel/tunerbox:latest` |
   | Icon URL | `https://raw.githubusercontent.com/pasiegel/tunerbox/main/web/icons/icon-192.png` |
   | WebUI | `http://[IP]:[PORT:8642]/` |
   | Extra Parameters | `--user 99:100` |
   | Network Type | `Bridge` (or `Host`, see below) |

   **Don't skip `--user 99:100`.** Unraid's shares belong to `nobody:users` (99:100), and without it TunerBox can't write its appdata folder: the container stops at once with `Permission denied: '/data/settings.json.tmp'` in its log.
3. Press **Add another Path, Port, Variable, Label or Device** once for each of these:

   | Config Type | Name | Container | Host / Value | Access mode |
   |---|---|---|---|---|
   | Port | Web UI | `8642` (TCP) | `8642` | |
   | Path | Data | `/data` | `/mnt/user/appdata/tunerbox` | Read/Write |
   | Variable | Time zone | `TZ` | your time zone, e.g. `America/Chicago` | |
   | Path | Videos (optional) | `/media` | your media share, e.g. `/mnt/user/Media` | Read Only |

   * **Data** holds the settings, the database, logs and recordings. Keep it on appdata (the cache drive if you have one), and include it in your appdata backup.
   * **Time zone** sets the guide times and when recordings start.
   * **Videos** is only for My Channels and My Library from a folder: in Setup, use the path inside the container (`/media/Movies` for `/mnt/user/Media/Movies`). My Library can also read an SMB share over the network with no path at all (`\\tower\Media\Movies`, with a user that can read it).
   * **HTTPS** (optional, for installing TunerBox as an app): add a Port `8643` → `8643` as well, switch HTTPS on in **Setup › Settings › HTTPS**, and add your Unraid server's address under **Extra addresses for the certificate**.
4. Press **Apply**. Unraid pulls the image and starts it. Click the TunerBox icon › **WebUI** (or open `http://<unraid-ip>:8642/`): a new install opens the setup wizard.

**Network type.** `Bridge` works for everything except finding an HDHomeRun on its own: add the tuner by its IP address instead. With `Host`, HDHomeRun discovery works and the port fields are ignored (TunerBox is at port 8642 on the Unraid server; make sure nothing else uses it). With `Custom: br0` TunerBox gets an address of its own on your network.

**Recordings** go in `/data/recordings`, which is `/mnt/user/appdata/tunerbox/recordings` on the server. To keep them on the array instead, add a Path `/recordings` → `/mnt/user/Recordings` (Read/Write), then set **Setup › Settings › Recordings folder** to `/recordings`.

**Updates.** On the Docker tab, **Check for Updates**, then **apply update** on TunerBox (or let the CA Auto Update Applications plugin do it). Your settings stay in appdata. To stay on one release, use its tag in Repository, e.g. `ghcr.io/pasiegel/tunerbox:1.9.0`.

**Plex, Jellyfin or Emby on the same Unraid server:** add TunerBox as an HDHomeRun tuner at `http://<unraid-ip>:8642`, not `localhost`: each container has its own `localhost`.

**Already started it without `--user 99:100`?** Add it under Extra Parameters and, if the container still won't start, reset the folder's owner in the Unraid terminal: `chown -R nobody:users /mnt/user/appdata/tunerbox`.

## Security notes
* TunerBox has no logins on your home network: anyone on the Wi-Fi can watch, and change setup until **Setup › Phones › lock** is on. On a Docker server that includes PC-only settings (ffmpeg, recordings folder, ports) and folders on the server. Pair your phone (and on a NAS, your PC's browser) and turn the lock on if others share your network.
* It refuses changes that come from other websites (a page on another site making your browser send them) and only answers to IP addresses and local names, so another site can't point its own name at it (DNS rebinding). If you open TunerBox by another name, add it under **Settings › Advanced › Allowed host names** (or `TUNERBOX_ALLOWED_HOSTS`).
* Playlist files and library folders on the TunerBox computer can only be added from this PC or a paired device (on a Docker server, also from the home network until locked); from anywhere else use web addresses and network shares. Exporting playlists (with their logins) always needs this PC or a paired device. ffmpeg only takes stream protocols (http(s), udp/rtp/rtsp/rtmp/srt, files).
* Don't forward port 8642 to the internet or put it behind a reverse proxy: use a VPN (Tailscale, WireGuard) for watching away from home.
* Passwords (playlists, NAS shares) and the TMDB key are stored in TunerBox's data folder as they are: keep that folder (the Docker volume) out of shared backups.

## How matching works
* **Locals** (WNBC, KABC, WGN…) match by callsign, including subchannels: a station's DT2 is matched to its second channel on the lineup, and so on. If no local stream exists, the national network feed (NBC, FOX…) is used. You can turn that off in Settings.
* **Cable networks** match by normalized name plus aliases (CNN = Cable News Network, HGTV, A&E…). The tvg-id only supports a name match, since many playlists set tvg-ids loosely.
* **Gracenote codes.** A lineup straight from Gracenote often names cable channels by code (`HGTVD`, `DSCHD`, `ESPN2HD`, `NIK`). About 130 of these also look for the network's full name (Home and Garden Television, Discovery Channel, Nickelodeon…), so playlists that spell names out still match.
* **Penalties** push down UK/Canada feeds, West/Pacific feeds, and Spanish feeds on English channels.
* **Backups.** Up to 4 more matching feeds are kept per channel. If the main one dies, the box tries them in order.
* **Feeds list.** In **Setup › Channel lineup**, an automatically matched channel with backups has a **Feeds** button: it lists the main feed and its backups in the order they're tried, each with a preview. **Remove** takes a wrong feed off that channel for good (rescans and re-matching skip it); removing the main feed makes the first backup the main one. **Allow again** brings the removed feeds back and re-matches.
* **Feed order.** In the same list, **Make main** plays a backup first, and the ▲ ▼ arrows change the order the backups are tried in. Re-matching keeps your order (feeds found later go after it); **Use best-match order** goes back to TunerBox's own.
* **Switch feed while watching.** On a channel with backups, **Menu › Switch feed** lists its feeds (with the playlist, and any that weren't working at the last scan) and plays the one you pick straight away. It doesn't change the order; Setup does that.
* **Skipped** on purpose: public-access, government-access and PPV slots.

## Files
* `data/channels.csv`: the sample cable lineup (`Channel Number,Channel Name`), read on every scan. It's skipped when you pick a different provider; to use your own CSV with another provider, set `lineup_csv_lineup` to `""` in `settings.json`
* `data/settings.json`: all settings, also editable in Setup › Settings
* `data/tunerbox.db`: playlists, mappings, guide, recordings (SQLite)
* `data/recordings/`: DVR output
* `data/tunerbox.log`: log (kept to about 20 MB: older parts roll over to `tunerbox.log.1` to `.3`)

## Notes
* The guide refreshes when TunerBox starts (unless it was updated in the last 30 minutes), then every 12 hours (`epg_refresh_hours`) while it runs. A Gracenote block that times out is retried, and if it still fails the listings from the last refresh are kept for that time slot. Menu › **Refresh TV guide** does it right away.
* Gracenote is a free public endpoint, not an official API. If it ever breaks, add an XMLTV source for locals instead.
* Xtream providers often allow only 1-2 connections (see [Connection limits](#connection-limits)). Stream checks skip Xtream sources by default.
* **One connection per channel.** Every screen, Plex and the DVR on the same channel share one provider connection, and a screen joining a channel that's already on starts at once. Different channels each use one. A screen whose playlist **Video** setting differs (say, one re-encoding to 720p) needs its own; a recording joins whatever is already running. Plex gets AAC audio when it shares with a TV.

## Development
* Tests: `pip install pytest`, then `python -m pytest`. They use a temporary data folder, never yours, and need no ffmpeg or network.
* `constraints.txt` pins exact package versions for CI, release builds and Docker (`pip install -r requirements.txt -c constraints.txt`). Running from source ignores it. Regenerate it after changing `requirements.txt` (the command is at the top of the file).
* The TV page's script is split into parts in `web/tv/`. The server joins them, in name order, into `/static/tv.js` inside one shared scope, so there is no build step. Code that runs at load time can only use what earlier parts define.
* Database changes go at the end of `MIGRATIONS` in `tunerbox/db.py`; the database's `user_version` records how many have run.

## Credits
* On Demand (movies and TV shows with resume, Continue Watching, My List and search), live game scores in Sports, reminders, the grouped Settings page, the Docker image, the 720p (Roku) re-encode mode, the phone / iPhone Safari touch layout, the test suite, Gracenote retries, faster guide downloads, PPV/event playlist refreshes and DVR disk-space safety by richakev ([@richakev](https://github.com/richakev), from the [richakev/tunerbox](https://github.com/richakev/tunerbox) fork).
* My Library (your own movies and shows from a NAS or a folder in On Demand, with TMDB details and posters), the Midnight look, the on-screen controls, the Setup sidebar and Overview page, the phone remote restyle, phone pairing on a NAS, and the protections against other websites and unknown host names by Hawkeyez88 ([@hawkeyez88](https://github.com/hawkeyez88)).
