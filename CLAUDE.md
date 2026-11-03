# rippleclips: rules for Claude Code

Turns Dave's podcast episodes ("The Ripple Effect", previously "Real Talk with Dave & Marshall") into short
vertical clips with captions and funny descriptions, then (from stage 2) posts them to Instagram, Facebook,
YouTube Shorts and TikTok through ContentStudio. Dave asked for it on 3 November 2026: "fully hands off".
The plan is the Claude Doc "Ripple Effect clips: hands-off plan" in the project.

## How it works

- Episodes land in Google Drive: folder "The Ripple Effect", subfolders `TRE Ep51`, `TRE Ep52`, ... Each
  subfolder can hold several short videos recorded the same day. Dave owns the folder.
- `clips/run.py` runs once a day from cron, at 3am Brisbane (17:00 UTC; Dave, 3 November 2026: new episodes come every few days) on the droplet `rippleclips` (134.199.155.143, user `clips`). For each
  new video in a settled episode folder (nothing changed for 2 hours): download → transcribe on the droplet
  (`faster-whisper`, free) → Claude (`claude-opus-5-5`) picks the funny moments and writes descriptions for each
  platform (`clips/pick.py`, style in `style/examples.md`) → ffmpeg cuts 1080x1920 with face-based framing
  (one person cropped, or two people stacked) and burnt-in word-by-word captions (`clips/cut.py`,
  `clips/captions.py`) → uploads to `<episode>/Clips` in Drive → deletes local copies.
- Each video also becomes a **full episode** for Spotify and YouTube (Dave, 3 November 2026; `clips/episode.py`):
  Claude judges where the real conversation starts and ends (the warm-up and off-air chatter are cut, at most
  5 minutes each end), the sound is levelled to -16 LUFS, and `Intro.mp4` from the main Drive folder (Dave's own,
  music from a free no-copyright site) goes in front and again at the end (or `Outro.mp4` there, if Dave adds one). Saved with a title and Spotify/YouTube descriptions to
  `<episode>/Full episode`. `FULL_EPISODES=0` in the env file switches it off.
- Transcripts are kept in `~/rippleclips-work/transcripts/` so a video is never transcribed twice.
- **Posting** (stage 2, built 3 November 2026; `clips/publish.py`, SETUP.md step 9): after each run, clips from
  TRE Ep52 on are handed to ContentStudio, two a day (Dave, 4 November 2026), each platform at its own time
  (`SLOT_TIMES`, Brisbane) moved by a random 0 to 30 minutes either way, at most 3 days ahead, one post per
  platform with that platform's caption. `POSTING` in the env file: off (default) / draft (ContentStudio drafts
  only) / on. First 2 weeks after `on`: only clips Dave drags into `<episode>/Clips/Approved`; after that, all
  except `Clips/Don't post`. ContentStudio's post_type values and timezone are not documented: check with drafts
  first (`POST_TYPES`, `CONTENTSTUDIO_TIMES_IN_UTC`). `--contentstudio-check` lists workspaces and accounts. Each clip is one
  upload to ContentStudio's media library, used for every platform, and deleted from the library 2 days after its post
  day (`CS_DELETE_AFTER_DAYS`, `DELETE /media/{id}`), so the 42 GB library (full on 4 November 2026) never fills again.
  `CONTENTSTUDIO_VIDEO=link` hands over a Drive link instead (ContentStudio still copies it into the library).
  YouTube's title goes in `youtube_options.title` (100 characters at most); the API spec is
  https://api-prod.contentstudio.io/api-docs.json.
- **Full episodes go out by themselves** (Dave, 4 November 2026; `clips/release.py`): one a week, oldest first, on
  `RELEASE_DAY` at `RELEASE_TIME` (Tuesday 7pm Brisbane) moved by up to 30 minutes, no approval. YouTube and Facebook
  get the video through ContentStudio (Facebook full videos are monetised; sent as post type `video`, because `feed` stops at 20 minutes; `FACEBOOK_FULL_POST_TYPE` changes it); Podbean gets an MP3 through its API
  (`PODBEAN_CLIENT_ID` / `PODBEAN_CLIENT_SECRET`, client credentials) and passes it to Apple and the other apps.
  Spotify stays Dave's manual video upload: Podbean only sends audio to Spotify, and he has his own Spotify show, so
  Spotify must never be switched on in Podbean (it would make a second show). Same `POSTING` switch as the clips.
  `--podbean-check` signs in and lists the podcast. The Podbean API calls follow its help article and the `podbean`
  Rust crate (Podbean's docs page doesn't load without a browser); check with a draft first.
- What has been done is in `~/rippleclips-state.json`; a video that fails is tried again at the next daily run, at most 3 times.

## Stages

1. Clips and descriptions into Drive only. Nothing is posted. (Built 3 November 2026.)
2. Posting through ContentStudio's API (`X-API-Key`, docs: https://docs.contentstudio.io/articles/contentstudio-api-f955a6ab),
   spread out over the week, with an approval step for the first 2 weeks (drag clips from "To approve" to
   "Approved" in Drive), then hands-off.
3. Fortnightly PDF report emailed to Dave: views per platform and the clips that worked, fed back into picking.
   ContentStudio's API gives per-post figures for Facebook, Instagram and YouTube; not TikTok.
4. Possibly: full episodes to Podbean (it has an API). Spotify has no upload API.

## Rules

- Never commit to `main`; every change is a pull request Dave merges. The droplet pulls `main` at each daily run, so
  merging deploys.
- Secrets live only on the droplet: `~/.rippleclips-env` (Anthropic key, later ContentStudio key),
  `~/.rippleclips-google-client.json`, `~/.rippleclips-google-token.json` (all chmod 600). Never commit,
  print or log them. Never ask Dave to paste one into the chat.
- Nothing is posted publicly without the approval step being in place, and nothing changes the platforms'
  settings. Claude never invents what was said, never mocks a named person who is not on the show, and
  never jokes at anyone's struggle (the show covers mental health and recovery).
- The Anthropic key is in its own workspace with a US$10 monthly limit.
- Dave is not a programmer: plain English, exact commands, say which window (PowerShell, Droplet, Browser),
  full paths and `ssh root@134.199.155.143` every time.

## Commands

`python3 -m unittest discover -s tests` · on the droplet as `clips`:
`.venv/bin/python -m clips.run [--status | --local FILE --episode N]`
