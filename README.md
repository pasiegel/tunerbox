# TunerBox

A personal IPTV "cable box": your local cable channel numbers and guide (pick your provider by ZIP or postal code in Setup), filled with feeds from your IPTV playlists. Inspired by MythTV's front end, Tunarr's channel mapping and IPTVnator's playlist handling.

* **Real channel numbers.** Your provider's lineup is the backbone: any US or Canadian cable, satellite or antenna lineup picked by ZIP/postal code (a sample cable lineup ships in `channels.csv`). Playlist channels are fuzzy-matched onto it.
* **Local guide.** Listings come from Gracenote (the data behind zap2it and TV Guide) for your provider, so the guide matches your numbers. US and Canada.
* **Everything else gets a number too.** Every working playlist channel that isn't on the lineup is numbered from 20000 up, with online XMLTV guides.
* **Manual mapping.** A Tunarr-style picker lets you search every feed, preview it, and pin the one you want.
* **M3U and Xtream Codes.** Paste an Xtream `get.php` link and it switches to the Xtream API automatically.
* **Cable box UI.** Picture-in-picture guide grid, channel banner with now/next and progress, number-key tuning, last channel, and static "No Signal" screens. Dead feeds fail over to backups automatically.
* **DVR.** Record one show, a series on one channel, or new episodes on any channel. Recordings are saved as MP4. If TunerBox stops mid-recording, what was captured is kept; recordings don't start with under 2 GB free and stop (keeping what they have) below 0.5 GB. Don't need it? Turn off **Setup › Settings › DVR › Recordings** and Record, Recordings and the DVR tab disappear from every screen.
* **On Demand.** Movies and TV shows that come with your playlist, each on its own screen, with posters, resume, Continue Watching, My List and search. Off until you turn it on for a playlist.
* **My Library.** Your own movies and TV shows in On Demand, read straight from your NAS over the network (SMB, no mount needed) or from a folder, with details and posters from TMDB. Nothing is written next to your files.
* **Works with other apps.** `/playlist.m3u` and `/epg.xml` work in VLC or TiviMate, and it emulates an HDHomeRun tuner for Plex, Emby and Jellyfin.

## Getting started
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

On a phone the TV screen switches to a touch layout: bigger buttons, panels that fit the screen and a **Close** button to leave the guide or a menu. Tap the picture to show or hide the controls (see [On-screen controls](#on-screen-controls)) and swipe up or down to change channel. Held upright, the top buttons show a small label under each icon so they all fit. On an iPhone, the full-screen button opens the video in Safari's own player.

Roku and other slow devices: set a playlist's **Video** to *Re-encode to 720p*, or add `?mode=roku720` to a stream URL (`http://<pc-ip>:8642/api/stream/<number>?mode=roku720`). It deinterlaces, caps the picture at 720p and the bitrate at 8 Mbps.

Building it yourself: `python packaging/build.py` on Windows (PyInstaller + Inno Setup 6), macOS (`.app`) or Linux. GitHub Actions does this for every release; see `RELEASING.md`.

## First-time setup
1. With no channels, the screen says **No channels yet**. Press Enter or click it to open **Setup** (also Menu › Channel Setup).
2. To use your own provider's channel numbers, go to **Guide**, enter your ZIP or postal code and press **Find providers**. Choose your provider (for example *Xfinity - Digital*, *DIRECTV* or *Local Over the Air Broadcast* for an antenna) and press **Save**. Its channel numbers become your lineup.
3. Go to **Playlists** and add your M3U URL or file, or your Xtream server, username and password.
4. Optional: press **Groups…** on the playlist to untick groups you don't want, such as other countries, adult or radio. **Check all** / **Uncheck all** apply to the groups listed, so after picking a country or typing a search they only change those groups (the buttons then show the count, e.g. *Uncheck 93 listed*). **Only keep listed** keeps the listed groups and hides every other one.
   * Hidden groups are removed straight away. Ticking one again rescans the playlist to bring it back.
5. Press **Scan playlists now**. The scan loads the playlist, checks which streams work, matches channels to your lineup, numbers the leftovers, and downloads the guides. The first scan takes a few minutes.
6. Go to **Channel Lineup** and set the filter to *Weak matches* to review guesses. Press **Change** to search and preview feeds, then **Use** to pin one. Manual picks survive rescans.
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
| S | start the show over ([catch-up](#catch-up-tv) channels) | |
| D | recordings | |
| / | search | search |
| T | Sports & Events | Sports & Events |
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
* **Continue Watching:** movies and episodes you stopped part-way through, then recordings in a row of their own, the latest first. OK resumes.
* **Sports on now:** games one of your channels or event feeds carries, with the score.
* **Recently recorded** and **Coming up** (scheduled recordings and reminders).

Arrows move, OK opens, number keys tune. To start one screen on live TV instead, turn off **Menu › Settings › Start on Home** there; opening TunerBox with `?ch=<number>` always starts on that channel. To turn Home off for every screen, untick **Setup › Settings › Screens › Home screen**: screens then start on live TV and the Home button opens the guide (each screen picks this up the next time it's turned on).

Recordings remember where you stopped, on the server, so any screen can **Resume** them (from Home or **Recordings**).

## Guide genre filter
In the guide, press **▲** on the first channel to highlight the genre picker next to the date, then **◀ ▶** to pick **All, Movies, Sports, News or Kids**; **OK** or **▼** goes back to the channels. That needs only the arrows and OK, so it works with any remote. **C** on a keyboard steps through the genres too (or give it a button of its own in **Remote control setup**: *Guide: filter by genre*), and on a touch screen you tap the day at the top left. Only channels with a show of that genre on in the hours shown are listed, and their other shows are dimmed; moving through time updates the list. It works with the Favorites guide too (V). Genres come from the guide's categories, so channels whose guide has none never show up in a filter.

## Catch-up TV
Some providers keep an archive of what their channels showed in the last few days: catch-up, also called timeshift or replay. TunerBox reads it from the playlist and shows it in the guide. No setup is needed.

* **Xtream logins:** channels the provider marks with an archive (`tv_archive`), with the number of days it keeps.
* **M3U playlists:** the `catchup`, `catchup-days` and `catchup-source` attributes (as in Kodi and TiviMate: `default`, `append`, `shift`, `flussonic` and `xc`), or the older `timeshift` / `tvg-rec`.

On a channel with catch-up:

* **Start over:** press **S** while watching (or Menu › **Start this show over**, or **Start over** under **More** on the on-screen controls) to watch the show on now from the beginning. The info banner shows **↺ START OVER: S**.
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

1. Under **Add a library folder**, pick **Movies** or **TV shows**, enter the **folder or network location** (`\\nas\Media\Movies`, `smb://192.168.1.20/Media/Movies`, `D:\Movies`, or in Docker `/media/Movies`) and, for a network share, the NAS username and password. **Connect & browse** checks the login and lets you click into folders and **Use this folder**.
2. **Add library**. The folder is scanned in the background; each library becomes a category in On Demand.
3. For details and posters, get a free key at themoviedb.org (Settings › API), paste the *API Key* or *API Read Access Token* under **Details and posters (TMDB)** and **Save**.
4. Check **Matches** › *Needs review*: **Fix** searches TMDB so you can pick the right title (kept through rescans), **Leave out** hides a file.

* **Nothing is written to your shares.** Text goes in TunerBox's database and posters in its data folder (`posters/`). Renaming files or folders is safe; a renamed folder is looked up again.
* **Naming:** movies as `Dune Part Two (2024)/anything.mkv` or `Dune Part Two (2024).mkv` (scene names like `Dune.Part.Two.2024.2160p.WEB-DL.mkv` work too); shows as `Show/Season 01/Show - S01E02 - Title.mkv` (`1x02` and `Season 2/04 - Title.mkv` work too). Samples, trailers and `Extras` folders are skipped.
* **Network shares** are read with a built-in SMB2/3 client, so Docker doesn't need the share mounted.
* **Folders on the TunerBox computer** work too: `D:\Movies` on Windows, `/home/me/Movies` on Linux. A drive letter mapped to a NAS works as long as TunerBox runs as the same Windows user.
* **In Docker**, TunerBox only sees its container. Mount the folder as a volume and enter the path inside the container: with `-v /srv/media:/media:ro`, the host's `/srv/media/Movies` is `/media/Movies` in Setup. Read-only is fine. A NAS share mounted on the host (CIFS/NFS) works the same way, and is the easy route for guest-only shares.
* **Who can add folders:** the TunerBox PC and paired devices; on a Docker server, also any browser on your home network until **Lock setup** is on (see [Security notes](#security-notes)).
* **New files** are picked up by **Scan libraries now** or automatically (Look for new files: once a day by default). Details are refreshed every 30 days; titles TMDB didn't know are tried again after a week.
* **Playing** uses the same player as playlist titles. Files are converted while they play only as far as needed (sound to AAC); set **Video** to re-encode for players that can't handle the original.

## HDHomeRun antenna tuner
Use a SiliconDust HDHomeRun as a channel source: **Setup › Playlists › Add a playlist › Type: HDHomeRun antenna tuner**, then **Find tuners on my network** (or type its IP address) and **Add**.

* Antenna channels are matched to your lineup by callsign, using the local over-the-air lineup from Gracenote. For example a station on antenna 3.1 becomes its cable channel, and its subchannels go to the matching lineup channels. Antenna channels not on your lineup are added as extra channels.
* Matched local channels play from the antenna first, with any playlist feeds kept as backups.
* Antenna TV is MPEG-2, which browsers can't play, so tuner channels are re-encoded to H.264 by default. You can change this per playlist with the **Video** setting on its card.
* Each antenna channel being watched or recorded uses one tuner, however many screens are on it. When all tuners are busy, the screen says what's using them (see [Connection limits](#connection-limits)).

This is separate from TunerBox *pretending* to be an HDHomeRun for Plex, Emby and Jellyfin, which still works as before. That emulated tuner offers Plex as many connections as your playlists allow in total (when every playlist has a limit), otherwise 4; set **Tuners offered to Plex** in Settings to choose the number yourself. When they're all in use it answers Plex the way a real HDHomeRun does ("All tuners in use").

## Connection limits
Xtream accounts usually allow only 1 or 2 streams at once, and an HDHomeRun has a fixed number of tuners. TunerBox keeps to that limit for each playlist, so a channel that can't start tells you why instead of showing No Signal.

* **The limit:** Xtream accounts and HDHomeRun tuners report it themselves. A plain M3U can't, so it has no limit until you set one: **Setup › Playlists › Connections** on its card (this also overrides what an Xtream account reports).
* **What counts:** one connection per channel or event feed that's on (however many screens, Plex and recordings share it), and one per On Demand title playing. Changing channel gives up the old one first.
* **When a playlist is full:** a channel with a backup feed on another playlist plays from there. Otherwise the screen shows **All connections in use**, lists what's using them (for example *Recording: The News* on 8) and offers **Watch 8 instead**: a channel that's already on can be watched on more screens for free.
* **Recordings come first:** a recording that's starting takes a connection from live TV or On Demand when the playlist is full. That screen then gets the same message, with the recording's channel to switch to. Turn this off in **Setup › Settings › DVR › Recordings come first**: the recording then waits for a connection to free up. A recording never takes over from another recording.
* **Conflicts:** recordings on different channels that overlap by more than a playlist allows are marked **⚠ CONFLICT** under Coming up in Recordings, with the reason, and you're warned when you schedule one. A channel whose backup feed is on another playlist with room doesn't count as a conflict.
* **Setup › Tuners** shows each playlist's connections in use against its limit, and who is using each one.

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

## On Demand (movies and TV shows)
Many playlists come with a library of movies and shows besides the live channels. TunerBox leaves it alone until you turn it on in Setup › **Playlists**, on the playlist's card: Until a playlist has it turned on, On Demand is left out of Home, the Menu, the key hints and the phone remote.

* **Xtream logins** have two switches, **On Demand: movies** and **On Demand: TV shows**. The lists are read when you turn one on and again at every full scan.
* **Plain M3U playlists** have one switch: entries whose address is a video file (`.mp4`, `.mkv`…) become titles instead of channels. Names like `Show S02 E04` are grouped into a show with seasons.
* **Categories…** next to each switch picks which movie or TV show categories are shown (untick the ones you don't want). Categories named like `EN - MOVIES` can be filtered by language there too.
* **Languages…** picks which labels are shown. Big providers put a label in front of every title: a language (`EN - Dune`, `DE - …`), a language and quality (`4K-EN`, `IN-EN`) or a platform (`NF` for Netflix, `D+` for Disney+). TunerBox cuts the label off the name, shows it as a small badge, and lists each label with how many titles have it and an example, so unfamiliar ones are easy to judge. Untick the languages you don't watch: with only English, a 235,000-title catalogue shrinks to about 37,000. Labels a provider adds later start unticked. Titles with no label are their own entry. This also works for TV shows, whose categories often have no language in them.

Press `O` (or Menu › **On Demand**, or **On Demand** on the on-screen controls) to open On Demand, where you left it. Live TV keeps playing in the corner. The tabs are **Movies** and **TV Shows** (each with its categories and posters), **Continue Watching** and **My List** (movies and shows together) and **Search**. Arrows move between the tabs, the categories and the titles; OK on a movie gives Resume / Play / My List; OK on a show opens its seasons and episodes; `*` adds to My List; `O` or Back closes it.

**Search** (`/` in On Demand) looks through the movies and shows. Accents and punctuation don't matter (*amelie* finds *Amélie*), the words can be in any order, a year narrows it (*dune 2021*), small typos still find it, and movies are found by actor or director too. A film the playlist has under several labels (*Dune* in EN, 4K-EN, DE…) is listed once; its options have **Other versions** to pick another language or quality. The main **Search** (`/` while watching) finds movies, shows and episodes alongside channels and guide listings.

While a title plays: OK or Space pauses, ← goes back 10 seconds, → skips 30, PgUp/PgDn jump 5 minutes, Back stops and returns to where you were. Where you stopped is remembered on the server, so any screen can resume it. A title uses one of your provider's connections while it plays, like a live channel, and follows the playlist's **Video** setting (pick a re-encode there if a movie has picture but won't play in the browser).

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
* **Two TunerBoxes on one host** (say one for the family and one private): give the second its own data volume and port, e.g. `-v tunerbox-private-data:/data` and `-p 8644:8644` with the command `python -m tunerbox --server --port 8644` (with host networking, just the `--port`). Then set **Setup › Settings › Other apps › Network name** on each (before adding them as tuners), so Plex, Jellyfin, Emby and Channels DVR list them under different names. Home-screen icons stay "TunerBox". Each one gets its own HDHomeRun device ID for Plex automatically.

## Security notes
* TunerBox has no logins on your home network: anyone on the Wi-Fi can watch, and change setup until **Setup › Phones › lock** is on. On a Docker server that includes PC-only settings (ffmpeg, recordings folder, ports) and folders on the server. Pair your phone (and on a NAS, your PC's browser) and turn the lock on if others share your network.
* It refuses changes that come from other websites (a page on another site making your browser send them) and only answers to IP addresses and local names, so another site can't point its own name at it (DNS rebinding). If you open TunerBox by another name, add it under **Settings › Advanced › Allowed host names** (or `TUNERBOX_ALLOWED_HOSTS`).
* Playlist files and library folders on the TunerBox computer can only be added from this PC or a paired device (on a Docker server, also from the home network until locked); from anywhere else use web addresses and network shares. Exporting playlists (with their logins) always needs this PC or a paired device. ffmpeg only takes stream protocols (http(s), udp/rtp/rtsp/rtmp/srt, files).
* Don't forward port 8642 to the internet or put it behind a reverse proxy: use a VPN (Tailscale, WireGuard) for watching away from home.
* Passwords (playlists, NAS shares) and the TMDB key are stored in TunerBox's data folder as they are: keep that folder (the Docker volume) out of shared backups.

## How matching works
* **Locals** (WNBC, KABC, WGN…) match by callsign, including subchannels: a station's DT2 is matched to its second channel on the lineup, and so on. If no local stream exists, the national network feed (NBC, FOX…) is used. You can turn that off in Settings.
* **Cable networks** match by normalized name plus aliases (CNN = Cable News Network, HGTV, A&E…). The tvg-id only supports a name match, since many playlists set tvg-ids loosely.
* **Penalties** push down UK/Canada feeds, West/Pacific feeds, and Spanish feeds on English channels.
* **Backups.** Up to 4 more matching feeds are kept per channel. If the main one dies, the box tries them in order.
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
* On Demand (movies and TV shows with resume, Continue Watching, My List and search), live game scores in Sports, reminders, the grouped Settings page, the Docker image, the 720p (Roku) re-encode mode, the phone / iPhone Safari touch layout, the test suite, Gracenote retries, faster guide downloads, PPV/event playlist refreshes and DVR disk-space safety by Kevin Richards ([@richakev](https://github.com/richakev), from the [richakev/tunerbox](https://github.com/richakev/tunerbox) fork).
* My Library (your own movies and shows from a NAS or a folder in On Demand, with TMDB details and posters), the Midnight look, the on-screen controls, the Setup sidebar and Overview page, the phone remote restyle, phone pairing on a NAS, and the protections against other websites and unknown host names by Hawkeyez88 ([@hawkeyez88](https://github.com/hawkeyez88)).
