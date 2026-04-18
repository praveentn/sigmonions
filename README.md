# 🎮 Sigmonions — Discord Word-Grouping Game

A multiplayer Discord bot game inspired by NYT Connections. Find groups of 4 words that share a hidden category. Race your friends, build streaks, and climb the leaderboard.

---

## Table of Contents

1. [How to Play](#how-to-play)
2. [Commands](#commands)
3. [Scoring](#scoring)
4. [Create the Discord App](#create-the-discord-app)
5. [Install & Run](#install--run)
6. [Deployment (Railway)](#deployment-railway)

---

## How to Play

### The Board
Each round shows **16 words or phrases** labelled `a` through `p`, arranged in a 4×4 grid.

```
`a` Solar        `b` Flamenco     `c` Python       `d` Rigatoni
`e` Waltz        `f` JavaScript   `g` Monsoon       `h` Spaghetti
`i` Tango        `j` Tornado      `k` Kotlin        `l` Penne
`m` Ballet       `n` Blizzard     `o` Ruby          `p` Fusilli
```

Hidden inside are **4 groups of 4** — each group shares a category:
- 🟨 **Dances** → b (Flamenco), e (Waltz), i (Tango), m (Ballet)  
- 🟩 **Programming Languages** → c (Python), f (JavaScript), k (Kotlin), o (Ruby)  
- 🟦 **Weather Phenomena** → g (Monsoon), j (Tornado), n (Blizzard), …  
- 🟪 **Types of Pasta** → d (Rigatoni), h (Spaghetti), l (Penne), p (Fusilli)

### Guessing

Type the **4 letters** of a group you've identified:

```
/sigmonion guess letters:beim
```

- ✅ **Correct** — the category is revealed, words disappear from the board, points awarded!  
- ❌ **Wrong** — 25 points deducted, words stay on the board.

After **3 groups** are found the 4th is **auto-revealed**. Then the next round starts automatically.

---

## Commands

| Command | Description |
|---|---|
| `/sigmonion play` | Start a 5-round game |
| `/sigmonion play rounds:10` | Start a 10-round game (max 20) |
| `/sigmonion guess letters:abcd` | Submit a group guess |
| `/sigmonion board` | Re-display the current board |
| `/sigmonion scores` | Show scores for the current game |
| `/sigmonion stop` | Stop the game (host or admin) |
| `/sigmonion stats` | Your lifetime stats |
| `/sigmonion stats user:@Player` | View another player's stats |
| `/sigmonion leaderboard` | Server leaderboard (by total points) |
| `/sigmonion leaderboard sort_by:accuracy` | Sort by accuracy, groups, streak, or best game |
| `/sigmonion server` | Server-wide statistics & insights |
| `/sigmonion help` | Quick in-Discord help |

---

## Scoring

| Event | Points |
|---|---|
| Correct group | **+100** |
| 1st to find a group in a round | **+50** speed bonus |
| 2nd to find | **+30** |
| 3rd to find | **+10** |
| Wrong guess | **−25** |
| 2 consecutive correct guesses (no wrong) | **+20** streak bonus |
| 3 in a row | **+40** |
| 4+ in a row | **+60** |
| Perfect round (you found all 3 guessable groups) | **+75** |

### Stats Tracked
- Total points, best game score, average points per game  
- Accuracy rate (correct ÷ total guesses)  
- Groups found, groups per round  
- Fastest group find time  
- First-find count (most first discoveries)  
- Current and best win streak  
- Perfect rounds count  
- Server-level: total games, accuracy, active players, top scorer, most accurate, longest streak

---

## Create the Discord App

### Step 1 — New Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name (e.g. `Sigmonions`)
3. Click **Create**

### Step 2 — Create a Bot

1. In the left sidebar click **Bot**
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token** click **Reset Token** → copy and save it (you'll put it in `.env`)
4. Under **Privileged Gateway Intents** enable:
   - ✅ **Message Content Intent**

### Step 3 — OAuth2 Invite URL

1. Left sidebar → **OAuth2** → **URL Generator**
2. Under **Scopes** check:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Under **Bot Permissions** check:
   - ✅ `Send Messages`
   - ✅ `Embed Links`
   - ✅ `Read Message History`
   - ✅ `Use Slash Commands`
4. Copy the generated URL at the bottom and open it in your browser to invite the bot to your server.

---

## Install & Run

### Prerequisites
- Python **3.10+**
- A Discord bot token (see above)

### Setup

```bash
# 1. Clone / download the repo
git clone https://github.com/yourname/sigmonions.git
cd sigmonions

# 2. Add your token
echo "DISCORD_TOKEN=your_token_here" > .env

# (Optional) For instant slash-command registration during development:
echo "DISCORD_GUILD_ID=your_server_id" >> .env
```

### Start

**macOS / Linux:**
```bash
./start.sh
```

**Windows:**
```bat
start.bat
```

Both scripts automatically:
- Detect your Python version
- Create a virtual environment (`venv/`)
- Install all dependencies
- Check and free the HTTP port
- Launch the bot

The bot also starts a local status dashboard at `http://localhost:8080/`.

### Manual run (after first setup)

```bash
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

python bot.py
```

---

## Deployment (Railway)

1. Push the repo to GitHub.
2. In Railway: **New Project** → **Deploy from GitHub repo** → select this repo.
3. Set environment variables in Railway's dashboard:
   - `DISCORD_TOKEN` = your bot token
   - Do **not** set `DISCORD_GUILD_ID` in production (global commands propagate to all servers)
4. Railway auto-detects `requirements.txt` and runs `python bot.py`.

> **Note:** Global slash commands can take up to **1 hour** to appear after first deploy. Guild commands (`DISCORD_GUILD_ID` set) appear instantly — use them for local testing only.

---

## File Structure

```
sigmonions/
├── bot.py                    ← Entry point
├── requirements.txt
├── start.sh / start.bat      ← One-click launchers
├── .env                      ← Your secrets (never commit this)
├── data/
│   ├── categorized_words_phrases.csv   ← 70+ word categories
│   └── sigmonions.db         ← SQLite database (auto-created)
├── cogs/
│   ├── sigmonion_cog.py      ← Game commands (/sigmonion play, guess, …)
│   └── stats_cog.py          ← Stats & leaderboard commands
└── utils/
    ├── database.py           ← Async SQLite helpers (aiosqlite)
    └── game_engine.py        ← Round generation, scoring, board rendering
```

---

## Adding More Categories

Edit `data/categorized_words_phrases.csv`. Each row needs:

```
#,category,words_and_phrases
71,My Category,[Word1,Word2,Word3,Word4,Word5,Word6,Word7,Word8]
```

Minimum **4 words** per category. The engine randomly picks 4 from however many you provide.
