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
