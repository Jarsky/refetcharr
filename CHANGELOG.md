# Changelog

## 1.7.0 (2026-11-13)

- **Profiles** (Settings → Replacement): **Max savings** (the smallest release that qualifies, MEDIUM picture is enough,
  Atmos not kept, smaller targets), **Balanced** (the defaults: HIGH at the smallest size) and **Max quality** (HIGH
  only, Atmos and lossless kept, audio order ahead of size, roomier targets). Picking one saves its settings; change
  any of them afterwards and it shows as Custom. A new release choice, **Smallest that qualifies**, is on the Quality
  page too.
- **Pick a suggestion by clicking it**: each film's releases are a list to choose from, with one Grab button for the
  pick. Picks are remembered (and kept when the film is searched again and the release is still offered), and **Grab
  picks** grabs each selected film's pick, the best one where you didn't pick.
- **Estimates**: ticking films in Flagged films shows their size now, about what they'd be after, and the saving
  (freed and added shown apart when some films grow). Films with suggestions use the real size of their pick; the
  rest use the target size. **Estimate with** compares the profiles on the same films without changing anything.
  Suggestions shows the same for the selected films' picks.

## 1.6.3 (2026-11-13)

- **The smallest HIGH release wins again**: your audio codec order came before size, so a release with lossless
  audio (DTS-HD MA, DTS:X, TrueHD) beat an equally HIGH one many GB smaller, even with Keep lossless off. Among HIGH
  releases, size now comes first; your audio order only decides between releases within 10% of the same size. A
  third of the suggestions picked a bigger file this way, a median of 3.9 GB more each. Audio you've chosen to keep
  (Atmos, surround, lossless) is still required, as before. Run Find replacements again to re-rank suggestions.
- A release that two indexers both list shows once in a film's suggestions.

## 1.6.2 (2026-11-12)

- **Refused grabs**: Radarr says "Getting release from indexer failed" when qBittorrent refuses a torrent it already has
  (409 Conflict: the same release through another indexer, or an earlier download still seeding). A picked release
  that's refused now leaves the film exactly as it was (1.6.0/1.6.1 dropped its record first), says why in words and
  keeps the suggestion; an automatic search moves on to the next best release (up to three). A release with the same
  name as the film's current file is turned down.
- A download waiting in qBittorrent's queue (queued, paused or delayed) isn't counted as stalled: the clock starts
  when it does.
- The problem banner shows a short reason and can be dismissed (it comes back if a different problem turns up).
- A section of the menu shows its sub-items' counts (or a dot) while it's collapsed, e.g. Suggestions under Library.
- **New defaults** for new installs: video codecs x265, x264, AV1; audio codecs Atmos, TrueHD, DTS:X, DTS-HD, DTS, DD+, DD;
  keep Atmos on; at least 3 seeders; stalled after 2 hours. Saved settings are unchanged.

## 1.6.1 (2026-11-12)

- **Suggestions**: select films and **Grab best picks** to grab each one's best option at once. Opened, a film lists
  every option the same way, as a card with its rating, size, picture, HDR, audio and seeders and its own Grab button;
  the best pick is highlighted. The list shows what each film has now and its best pick side by side.
- Buttons are plain again (the neon gradient stays on the logo and the name), and every settings page's button just
  says **Save**.

## 1.6.0 (2026-11-12)

- **Aim for HIGH at the smallest size**: among the releases that qualify, Refetcharr now takes the smallest one whose
  picture rates HIGH; when none does, the MEDIUM one with the best picture (never below your minimum). Resolution
  (when Prefer the highest resolution is on) and your codec order still come first. The old way, the size nearest
  Preferred, is a choice under Settings → Quality. Each film's details say which applied.
- **Ratings recalibrated** on this library: HIGH from 6 Mbps (1080p), 15 (4K), 3.5 (720p), 2 (SD) of x265 picture,
  LOW below 3.5 / 9 / 2 / 1. Typical good encodes here are 5-6 Mbps at 1080p and 15-18 at 4K; the old marks (8 and 20)
  rated most of them MEDIUM.
- **Low quality** films: a new group (Flagged films, off for auto-queue) for files whose picture rates LOW. Their
  replacement must rate higher, of any size up to Maximum.
- **Replace again** (Activity → Replaced, or a film's details): puts a replaced film right. **Needs attention** lists
  those whose picture rates LOW now or that lost HDR (or surround, for films replaced from 1.5 on): 23 and 27 here.
  The search wants a better file than the current one and what was lost back; the earlier saving still counts.
- **Suggestions** (Library → Suggestions): **Find replacements** on selected films (Flagged films, Replaced, or a
  film's details) searches now and lists every release that qualifies, ranked, with rating, size, picture, HDR,
  audio and seeders. Grab the first or any other; it goes through the usual checks and import.
- **Save buttons** stay dull until something changes, and again once saved. Messages share one look: mint when
  saved, pink for problems, cyan for information.
- **New logo** (the neon replay icon) in the menu, the tab and the sign-in page, and its gradient on the name and the
  main buttons.

## 1.5.0 (2026-11-12)

- **A new look**: a side menu like Sonarr's, with sections that open to their pages: Dashboard; Library → Oversized films;
  Activity → Replaced, Not replaced, Events; Settings → Replacement, Quality, Queue & schedule, Radarr, Timings, Security.
  On a phone the menu slides in from the ☰ button. Old `#settings` links open Settings → Radarr.
- **Click any film** (Oversized films, Replaced, Not replaced, Downloads) for its details: why it was flagged, the file before,
  the release chosen and the file now side by side (resolution, codecs, picture bitrate, HDR, audio, channels, Atmos, lossless,
  rating), why that release won and the next best, and every release turned down with the reason. What a film loses is marked
  in red. Films tried before 1.5 show what was kept then (the reasons weren't recorded).
- **Ratings** (LOW / MEDIUM / HIGH) for every file, like Totality's: the picture is rated for its resolution in x265 terms
  (the audio's share taken off the size first; x264 counts at 1/1.5, AV1 at 1/0.75, ...), the audio by bitrate (lossless
  is HIGH), weighted 70/30. Shown in Oversized films and as before → after in Replaced. **Settings → Quality** has the
  thresholds per resolution (SD, 720p, 1080p, 4K) as sliders, the audio thresholds, the weighting and the codec factors.
- **A replacement must reach a picture rating** (MEDIUM by default): worked out from its title and size, so a 4.2 GB
  1080p x264 release, or one where TrueHD takes most of the size, is refused as too weak, while x265 at that size passes.
- **Keep HDR** (on): an HDR or Dolby Vision film's replacement must say HDR or DV in its title, and the downloaded file
  must really be HDR (PQ or HLG). 27 4K films had become SDR before this.
- **Keep surround sound** (on): a 5.1 or 7.1 film's replacement mustn't be stereo, checked on the title and on the file.
- **Keep Atmos** and **Keep lossless audio** (off): the same for Atmos and for TrueHD / DTS-HD MA / FLAC / PCM.
- **Radarr's webhook**: Settings → Radarr has a check, with a Fix button, that adds a "Refetcharr" webhook in Radarr →
  Connect. Each import is looked at within a minute (a new remux is flagged straight away rather than at the weekly
  scan). It needs Radarr to reach Refetcharr (Refetcharr's address for Radarr, default http://refetcharr:8787).
- **Media server check** (Settings → Radarr): warns if no Plex, Emby or Jellyfin connection in Radarr hears about
  upgrades. Refetcharr's swaps are Radarr imports, so that connection is what refreshes the server.
- **Activity → Events**: the whole activity log, searchable and filtered by kind.

## 1.4.0 (2026-11-11)

- **Sign-in**, one user like Sonarr and Radarr. Until a login exists the web UI asks you to create one; after that every page and
  API call needs it (the health check stays open). The password is stored as a PBKDF2 hash; sessions are signed, HttpOnly
  cookies ("keep me signed in" lasts 30 days). Ten wrong passwords from one address lock it out for 15 minutes.
- **Settings → Security**: change the username or password (needs the current one; signs out every other browser). **Sign out**
  in the header.
- **API key** (Settings → API key, `X-Api-Key` header) for scripts. The TUI and the `refetcharr` commands in the container use it
  by themselves.
- **From the container**: `refetcharr login set [USER]` (asks for the password), `refetcharr login reset` (the web UI asks for a
  new login), `refetcharr login show`, `refetcharr apikey [--new]`. They work with the server stopped.

## 1.3.4 (2026-11-11)

- A film Radarr imported by itself is recognised by the release name Radarr keeps (sceneName), not only by size: an indexer's
  listed size can be 10% off the file, so Songbird (listed 11.2 GB, file 10.0 GB) waited for Radarr forever.
- Every hour (and at start), films written off as refused, missing or nothing to import are checked again: if Radarr imported
  the release anyway, they count as replaced (with the file check's verdict noted). She Said and Songbird were two.

## 1.3.3 (2026-11-11)

- The file check now actually reads track titles from ffprobe (1.3.2 only asked for the language tag, so its rule never fired).

## 1.3.2 (2026-11-11)

- An audio track whose title says English ("English DTS-HD Master Audio 5.1") passes the English check even when its language tag
  is wrong: Eastern Promises (BeiTai) tags its only, English, track as Chinese.

## 1.3.1 (2026-11-11)

- **Untagged audio counts as English** (Replacement → Release requirements, on by default): many encodes leave the audio language
  blank ("und"), and the file check refused them as having no English track, so they sat in Radarr's queue unimported. A file
  whose tracks are all untagged now passes (the release was only grabbed because it listed English); one naming only other
  languages, like a Chinese-only track, is still refused.

## 1.3.0 (2026-11-11)

- **Queue the biggest wins, or Replace selected,** starts those films straight away: the downloads-at-once and daily limits and the
  schedule are for auto-queue, so they no longer hold back what you ask for by hand. Pause still does.
- **Auto-queue / Manual queue** in the header is a button: click it to switch.
- **Paused** shows on the pause button itself, which glows pink slowly until you resume (steady with reduced motion).
- **Tiny x265 files can be gone after** (Replacement → What to go after; off by default). For those films the rule turns round:
  the new file must be at least 10% bigger, at least Minimum and under Maximum. They're queued after the savings, smallest first,
  and show as space used ("grew") rather than freed. "Replace selected" works for them too.
- **Leave alone** (Replacement): films whose current file is from a listed release group (e.g. nikt0) are never replaced, releases
  from a second list are never downloaded, and single films can be ignored from Oversized films (Ignore selected / Show ignored).
  The Oversized list shows each file's release group (after the next rescan).
- **Every tunable is in the UI:** Settings → Radarr connection has the address for film links (was fixed to one Radarr), and a new
  Timings card holds the waits that were fixed in code (gone from the queue, import retry, no video file, qBittorrent not
  completing, library refresh, setup checks).

## 1.2.1 (2026-11-04)

- A film fully downloaded but not yet completed by qBittorrent no longer shows as 100%: it says it's waiting, is called out after
  an hour, and keeps its slot. It's never imported early, since qBittorrent hasn't verified the data.
- A page left open reloads itself when Refetcharr is updated, and page files are revalidated on every load.

## 1.2.0 (2026-11-04)

- New Replacement tab, separate from the app's Settings: everything that was fixed in the old script is now a setting. Sizes
  have Radarr-style sliders with several markers (too small, minimum, preferred, maximum, in MB/min and GB/h), the on/off rules
  are toggles, and preferred video and audio codecs are lists you fill and drag into order. Lists start empty, which chooses
  releases exactly as before.
- A download with no progress for a set time (6 hours by default) gets a warning and stops taking up a download slot. It stays
  tracked and still imports if it finishes.
- Rescan library asks first.
- Imports: a scene release still being unpacked is no longer written off as having no video file (and ones already written
  off recover), a slow answer from Radarr no longer holds up every other film, and an import in progress isn't asked for twice.
- Progress can't show over 100% when Radarr reports slightly negative bytes left.

## 1.1.0 (2026-11-04)

- Settings → Radarr connection: the address and API key can be set in the app (Test before saving). The key stays on
  the server: the page only shows whether one is saved, and a blank field keeps it. Blank values fall back to `.env`.
  `settings.json` is now written mode 600 because it can hold the key.
- Settings → Radarr setup: a checklist of what Refetcharr relies on in Radarr (qBittorrent as a download client,
  completed download handling, no recycle bin, a "Remux" custom format scored -10000 in every profile your films use,
  size limits matching Refetcharr's caps, the media mount, ffprobe), each with a Fix button where it can be fixed
  safely. Nothing in Radarr changes unless you press Fix. Problems badge the Settings tab, like Radarr's health
  warnings, and show in the terminal UI, which has a matching "Radarr connection and setup" menu entry.
- Errors that mean a bad API key or an unreachable Radarr say so plainly and link to Settings.

## 1.0.1 (2026-11-03)

- Web UI: each section is its own page (the links didn't switch pages), Downloads and Recent activity are the same height
  and scroll inside, and the tabs badge what's new like Radarr/Sonarr: downloads running, films not replaced and newly
  replaced since you last looked, and a new scan. The browser tab shows the count of new failures.
- A download briefly missing from Radarr's queue is no longer treated as gone: it must be absent 15 minutes, and a film
  marked missing that comes back carries on by itself.
- The activity feed keeps its history across restarts.

## 1.0.0 (2026-11-03)

First release, grown from the `radarr-cleanup` scripts.

- Web UI and terminal UI over one engine and API.
- Library scan with groups (1080p remuxes, 4K over the cap, 4K remuxes, 1080p over the cap, tiny x265) and CSV reports.
- Queue: auto-queue, downloads at once, films per day, schedule window, group toggles, queue by count or by film.
- Release rules: seeders, size ratio, size floor, same resolution or higher, x265 first, English audio, DV only with HDR10.
- Downloads checked with ffprobe before import; override import for smaller same-quality files; Radarr's own imports detected and checked.
- Retry for films that weren't replaced. History carried over from `radarr-cleanup`'s state.json.
