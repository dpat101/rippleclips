# Setting up the Ripple Effect clips droplet

Droplet: **rippleclips**, `134.199.155.143` (Sydney, 4 GB, created 3 November 2026).
The app runs as the user `clips`; the code is in `/home/clips/rippleclips`.

Each step says which window it goes in:

- **PowerShell** on your PC
- **Droplet**: after logging in with step 1
- **Browser**

Never paste a key, password or token into the Claude chat.

## 1. Log in to the droplet (PowerShell)

```
ssh root@134.199.155.143
```

Type `yes` if it asks about the fingerprint.

## 2. Make a read-only key for GitHub (Droplet)

Paste this whole block:

```
id -u clips >/dev/null 2>&1 || useradd -m -s /bin/bash clips
sudo -u clips -H bash -c 'mkdir -p ~/.ssh && ssh-keygen -t ed25519 -N "" -f ~/.ssh/github-rippleclips -C rippleclips-droplet -q && printf "Host github-rippleclips\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/github-rippleclips\n  IdentitiesOnly yes\n" >> ~/.ssh/config && chmod 600 ~/.ssh/config && cat ~/.ssh/github-rippleclips.pub'
```

It prints one line starting `ssh-ed25519`. That is a public key, safe to copy.

## 3. Give the droplet read access to the code (Browser)

1. Go to https://github.com/dpat101/rippleclips/settings/keys and press **Add deploy key**.
2. Title: `rippleclips droplet`. Key: paste the `ssh-ed25519 ...` line.
3. Leave **Allow write access** unticked. Press **Add key**.

## 4. Fetch the code and install (Droplet)

```
sudo -u clips -H bash -c 'ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null && git clone git@github-rippleclips:dpat101/rippleclips.git ~/rippleclips'
bash /home/clips/rippleclips/deploy/install.sh
```

It takes about 5 minutes and ends with `== Done`.

## 5. Anthropic key (Browser, then Droplet)

1. In https://console.anthropic.com create a workspace called **Ripple clips** and set its monthly
   spend limit to **US$10** (Settings, Limits).
2. In that workspace create an API key and copy it.
3. On the droplet:

   ```
   nano /home/clips/.rippleclips-env
   ```

   Paste the key straight after `ANTHROPIC_API_KEY=` (no spaces), then Ctrl+O, Enter, Ctrl+X.
4. Check it is there without showing it (it should print a number around 100 and `sk-ant`):

   ```
   grep '^ANTHROPIC_API_KEY=' /home/clips/.rippleclips-env | cut -d= -f2 | tr -d '\n' | wc -c; grep -o '^ANTHROPIC_API_KEY=sk-ant' /home/clips/.rippleclips-env | cut -d= -f2
   ```

## 6. Google Drive sign-in

Use the Google account that **owns** The Ripple Effect folder.

**Browser:**

1. Go to https://console.cloud.google.com, signed in as that account. Create a project called `rippleclips`.
2. **APIs & Services → Library**, search **Google Drive API**, press **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform): app name `Ripple clips`, user type
   **External**, your email for support and contact. Under **Audience**, press **Publish app** so it is
   "In production". This stops the sign-in from expiring every 7 days. Google doesn't need to check an
   app used only by you; you will just see a "Google hasn't verified this app" screen once.
4. **Clients → Create client**, type **Desktop app**, name `rippleclips`. Press **Download JSON**.

**PowerShell** (a new window; change the file name to the one you downloaded):

```
scp "C:\Users\david\Downloads\client_secret_XXXX.json" root@134.199.155.143:/home/clips/.rippleclips-google-client.json
ssh root@134.199.155.143 'chown clips:clips /home/clips/.rippleclips-google-client.json && chmod 600 /home/clips/.rippleclips-google-client.json'
```

Then sign in through a tunnel (**PowerShell**):

```
ssh -t -L 8085:localhost:8085 root@134.199.155.143 'bash /home/clips/rippleclips/deploy/google-signin.sh'
```

It prints a link. Open it in the browser on your PC, pick the folder's owner account, press **Advanced →
Go to Ripple clips** on the warning screen, and allow Drive access. PowerShell then says `Saved.`

## 7. First run (Droplet)

```
sudo -u clips -H bash -c 'cd ~/rippleclips && .venv/bin/python -m clips.run'
```

This does TRE Ep52 and later (it ignores older episodes; see `START_EPISODE`). The first run also
downloads the speech-to-text model. Turning speech into text takes roughly half the length of the video (an hour-long video about 30 to 40 minutes); it prints a progress line every 5 minutes of audio. When it ends, each episode folder in Drive has
a **Clips** folder with the clips, a `.txt` with each clip's descriptions, and a `clips.json` list, and a
**Full episode** folder with the finished episode (intro in front, warm-up cut, sound levelled) and its
title and descriptions. The intro is the video called `Intro` in the main The Ripple Effect folder, played at the start and the
end; add a video called `Outro` there for a different ending.

Check what was done:

```
sudo -u clips -H bash -c 'cd ~/rippleclips && .venv/bin/python -m clips.run --status'
```

## 8. Switch on the daily run (Droplet)

Only after you are happy with the clips from step 7:

```
bash /home/clips/rippleclips/deploy/install.sh --cron
```

From then on it runs every day at 3am Brisbane time, picks up code merged to `main` by itself, and handles each
new `TRE Ep..` folder once nothing in it has changed for 2 hours.

Logs: `tail -50 /home/clips/rippleclips.log`

## 9. Posting through ContentStudio (stage 2)

**Browser:** in ContentStudio, switch on **API** in the left menu and press **Generate API Key**.

**PowerShell:** open the settings file and add the key on a new line, `CONTENTSTUDIO_API_KEY=` followed by the
key with no spaces (Ctrl+O, Enter, Ctrl+X to save):

```
ssh -t root@134.199.155.143 'nano /home/clips/.rippleclips-env'
```

Check it can see your accounts (it prints names and ids, never the key):

```
ssh root@134.199.155.143 'cd /home/clips/rippleclips && sudo -u clips -H git pull --ff-only -q && sudo -u clips -H .venv/bin/pip install -q -r requirements.txt && sudo -u clips -H .venv/bin/python -m clips.run --contentstudio-check'
```

Then, in the same settings file:

- `POSTING=draft` makes posts as **drafts** in ContentStudio only, for checking. Nothing is published.
- `POSTING=on` posts for real: one clip a day at 6pm Brisbane (`POST_TIME`), handed to ContentStudio up to 3 days
  ahead. For the first 2 weeks only clips you drag into `Clips/Approved` in an episode folder are posted; after
  that every clip is, unless you drag it into `Clips/Don't post`.
- `POSTING=off` (or no line) stops everything. Posts already handed over can be deleted in ContentStudio.
