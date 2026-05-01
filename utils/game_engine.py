"""Core game logic — data loading, round generation, scoring."""
import csv
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CSV_PATH = Path(__file__).parent.parent / "data" / "categorized_words_phrases.csv"

LETTERS = list("abcdefghijklmnop")

# Color emojis for found groups (NYT Connections style)
GROUP_COLORS = ["🟨", "🟩", "🟦", "🟪"]
GROUP_COLOR_NAMES = ["Yellow", "Green", "Blue", "Purple"]

# ── Scoring constants ──────────────────────────────────────────────────────────
POINTS_CORRECT        = 100
POINTS_WRONG          = -25
POINTS_SPEED_FIRST    = 50
POINTS_SPEED_SECOND   = 30
POINTS_SPEED_THIRD    = 10
POINTS_PERFECT_ROUND  = 75   # found all 3 guessable groups in a round
POINTS_PERFECT_GAME   = 150  # perfect round in every round of the game
STREAK_BONUS = {2: 20, 3: 40, 4: 60}   # per consecutive correct guess within a game


def _parse_words(raw: str) -> list[str]:
    """Parse '[word1, word2, ...]' from CSV into a list."""
    cleaned = raw.strip().lstrip("[").rstrip("]")
    return [w.strip() for w in cleaned.split(",") if w.strip()]


def load_categories() -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cat   = row["category"].strip()
            words = _parse_words(row["words_and_phrases"])
            if len(words) >= 4:
                cats[cat] = words
    return cats


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class GroupData:
    category:  str
    words:     list[str]
    letters:   list[str]
    color_idx: int          # 0-3 → which color it will get when found
    found_by:  int | None = None
    found_at:  float | None = None


@dataclass
class RoundState:
    round_num:   int
    groups:      list[GroupData]
    letter_map:  dict[str, tuple[str, int]]  # letter → (word, group_idx)
    started_at:  float
    found_order: list[int] = field(default_factory=list)   # group_idx ordered by discovery
    # {user_id: correct_count} for this round
    round_scores: dict[int, int] = field(default_factory=dict)
    board_message_id: int | None = None

    @property
    def remaining_letters(self) -> list[str]:
        found_letters = {
            letter
            for idx in self.found_order
            for letter in self.groups[idx].letters
        }
        return [l for l in LETTERS if l in self.letter_map and l not in found_letters]

    @property
    def groups_remaining(self) -> int:
        return 4 - len(self.found_order)

    def is_complete(self) -> bool:
        # Round ends when 3 groups found (4th auto-reveals)
        return len(self.found_order) >= 3

    def validate_guess(self, letters: str) -> tuple[bool, str, int | None]:
        """
        Returns (is_valid, error_msg, group_idx).
        group_idx is the matched group index if guess is correct, else None.
        """
        letters = letters.lower().strip()
        if len(letters) != 4:
            return False, "Please enter exactly 4 letters.", None
        if len(set(letters)) != 4:
            return False, "All 4 letters must be different.", None

        remaining = set(self.remaining_letters)
        for ch in letters:
            if ch not in self.letter_map:
                return False, f"`{ch}` is not a valid letter on this board.", None
            if ch not in remaining:
                return False, f"`{ch}` is from a group already found.", None

        guessed_set = set(letters)
        for idx, group in enumerate(self.groups):
            if idx in self.found_order:
                continue
            if set(group.letters) == guessed_set:
                return True, "", idx

        return False, "", None   # valid letters but wrong grouping

    def check_one_away(self, letters: str) -> bool:
        """True if exactly 3 of 4 guessed letters belong to the same unfound group."""
        letters = letters.lower().strip()
        if len(letters) != 4:
            return False
        guessed = set(letters)
        for idx, group in enumerate(self.groups):
            if idx in self.found_order:
                continue
            if len(set(group.letters) & guessed) == 3:
                return True
        return False


@dataclass
class GameSession:
    channel_id:   int
    guild_id:     int
    started_by:   int
    total_rounds: int
    game_id:      str       = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at:   str       = ""
    current_round_num: int  = 0
    rounds:       list[RoundState] = field(default_factory=list)
    # cumulative game scores per user
    scores:       dict[int, int]   = field(default_factory=dict)
    # consecutive correct guesses without a wrong one (resets on wrong guess)
    correct_streaks: dict[int, int] = field(default_factory=dict)
    # per-user counts for stats persistence
    correct_counts: dict[int, int]  = field(default_factory=dict)
    wrong_counts:   dict[int, int]  = field(default_factory=dict)
    groups_found_counts: dict[int, int] = field(default_factory=dict)
    perfect_round_counts: dict[int, int] = field(default_factory=dict)
    first_find_counts: dict[int, int]    = field(default_factory=dict)
    fastest_ms:   dict[int, int | None]  = field(default_factory=dict)
    status:       str = "active"   # active | completed

    @property
    def current_round(self) -> RoundState | None:
        if self.rounds:
            return self.rounds[-1]
        return None

    def add_score(self, user_id: int, pts: int):
        self.scores[user_id] = self.scores.get(user_id, 0) + pts

    def winner(self) -> int | None:
        if not self.scores:
            return None
        return max(self.scores, key=lambda u: self.scores[u])


# ── Game engine ────────────────────────────────────────────────────────────────

class GameEngine:
    def __init__(self):
        self._cats: dict[str, list[str]] | None = None

    @property
    def categories(self) -> dict[str, list[str]]:
        if self._cats is None:
            self._cats = load_categories()  # CSV fallback (dev / first boot)
        return self._cats

    def set_categories(self, cats: dict[str, list[str]]) -> None:
        """Inject categories loaded from the DB (replaces CSV cache)."""
        self._cats = cats

    def reload_categories(self):
        self._cats = load_categories()

    def build_round(self, round_num: int) -> RoundState:
        eligible = {k: v for k, v in self.categories.items() if len(v) >= 4}
        if len(eligible) < 4:
            raise RuntimeError(
                f"Not enough eligible categories (need ≥4, found {len(eligible)}). "
                "Check that data/categorized_words_phrases.csv is properly quoted — "
                "the words_and_phrases column must be wrapped in double quotes."
            )
        chosen   = random.sample(list(eligible.keys()), 4)

        groups: list[GroupData] = []
        for color_idx, cat in enumerate(chosen):
            words = random.sample(self.categories[cat], 4)
            groups.append(GroupData(
                category=cat,
                words=words,
                letters=[],        # assigned after shuffle
                color_idx=color_idx,
            ))

        # Flatten words and shuffle
        flat: list[tuple[str, int]] = []   # (word, group_idx)
        for idx, g in enumerate(groups):
            for w in g.words:
                flat.append((w, idx))
        random.shuffle(flat)

        letter_map: dict[str, tuple[str, int]] = {}
        for i, (word, gidx) in enumerate(flat):
            letter = LETTERS[i]
            letter_map[letter] = (word, gidx)
            groups[gidx].letters.append(letter)

        return RoundState(
            round_num=round_num,
            groups=groups,
            letter_map=letter_map,
            started_at=time.time(),
        )

    def process_guess(
        self,
        session: GameSession,
        user_id: int,
        username: str,
        letters: str,
    ) -> dict:
        """
        Process a guess and return a result dict with all outcome data.
        Mutates session scores and round state.
        """
        rnd = session.current_round
        if rnd is None:
            return {"error": "No active round."}

        is_valid, err, group_idx = rnd.validate_guess(letters)

        result: dict = {
            "valid": is_valid,
            "error": err,
            "group_idx": group_idx,
            "points_earned": 0,
            "breakdown": [],
            "round_complete": False,
            "auto_reveal_group": None,
        }

        if err and group_idx is None:
            # Hard validation error (bad letters)
            return result

        # Initialise per-user counters
        session.scores.setdefault(user_id, 0)
        session.correct_streaks.setdefault(user_id, 0)
        session.correct_counts.setdefault(user_id, 0)
        session.wrong_counts.setdefault(user_id, 0)
        session.groups_found_counts.setdefault(user_id, 0)
        session.perfect_round_counts.setdefault(user_id, 0)
        session.first_find_counts.setdefault(user_id, 0)
        session.fastest_ms.setdefault(user_id, None)

        if not is_valid:
            # Wrong guess
            session.add_score(user_id, POINTS_WRONG)
            session.correct_streaks[user_id] = 0
            session.wrong_counts[user_id] += 1
            result["points_earned"] = POINTS_WRONG
            result["breakdown"].append(f"{POINTS_WRONG} wrong guess")
            return result

        # ── Correct guess ──────────────────────────────────────────────────────
        group        = rnd.groups[group_idx]
        group.found_by = user_id
        group.found_at = time.time()
        rnd.found_order.append(group_idx)

        pts = POINTS_CORRECT
        result["breakdown"].append(f"+{POINTS_CORRECT} correct group")

        # Speed bonus
        find_pos = len(rnd.found_order)   # 1st, 2nd, 3rd found in this round
        speed_pts = {1: POINTS_SPEED_FIRST, 2: POINTS_SPEED_SECOND, 3: POINTS_SPEED_THIRD}.get(find_pos, 0)
        if speed_pts:
            pts += speed_pts
            result["breakdown"].append(f"+{speed_pts} speed #{find_pos}")
            session.first_find_counts[user_id] += (1 if find_pos == 1 else 0)

        # Fastest group time
        elapsed_ms = int((group.found_at - rnd.started_at) * 1000)
        prev_fastest = session.fastest_ms[user_id]
        if prev_fastest is None or elapsed_ms < prev_fastest:
            session.fastest_ms[user_id] = elapsed_ms

        # Consecutive correct streak bonus
        session.correct_streaks[user_id] += 1
        streak = session.correct_streaks[user_id]
        streak_bonus = STREAK_BONUS.get(min(streak, 4), STREAK_BONUS.get(4, 60) if streak > 4 else 0)
        if streak_bonus:
            pts += streak_bonus
            result["breakdown"].append(f"+{streak_bonus} streak ×{streak}")

        session.add_score(user_id, pts)
        session.correct_counts[user_id] += 1
        session.groups_found_counts[user_id] += 1
        result["points_earned"] = pts
        rnd.round_scores[user_id] = rnd.round_scores.get(user_id, 0) + 1

        # Check if round is complete (3 groups found → auto-reveal 4th)
        if rnd.is_complete():
            remaining_idx = next(
                i for i in range(4) if i not in rnd.found_order
            )
            last_group = rnd.groups[remaining_idx]
            rnd.found_order.append(remaining_idx)
            last_group.found_at = time.time()
            result["auto_reveal_group"] = remaining_idx

            # Perfect round bonus (user found all 3 guessable groups)
            user_finds = sum(
                1 for g in rnd.groups[:3]  # first 3 groups (4th is auto)
                if g.found_by == user_id
            )
            # Actually check if user found all groups they could have
            user_group_finds = sum(
                1 for g in rnd.groups
                if g.found_by == user_id and g != last_group
            )
            if user_group_finds == 3:
                pts_bonus = POINTS_PERFECT_ROUND
                session.add_score(user_id, pts_bonus)
                session.perfect_round_counts[user_id] += 1
                result["points_earned"] += pts_bonus
                result["breakdown"].append(f"+{pts_bonus} perfect round!")

            result["round_complete"] = True

        return result

    def found_groups_text(self, rnd: RoundState) -> str:
        """One line per found group — shown at the top of every board embed."""
        lines = []
        for order_pos, gidx in enumerate(rnd.found_order):
            g = rnd.groups[gidx]
            emoji = GROUP_COLORS[order_pos]
            words = "  ·  ".join(w.capitalize() for w in g.words)
            lines.append(f"{emoji}  **{g.category}**\n> {words}")
        return "\n".join(lines)

    def remaining_columns(self, rnd: RoundState) -> tuple[str, str]:
        """
        Split remaining words into two roughly equal columns.
        Returns (left_text, right_text) where each line is  **x**  ›  Word.
        """
        rem = rnd.remaining_letters
        mid = (len(rem) + 1) // 2
        left  = rem[:mid]
        right = rem[mid:]

        def col(letters: list[str]) -> str:
            return "\n".join(
                f"**{l}** › {rnd.letter_map[l][0]}" for l in letters
            )

        return col(left), col(right) if right else "\u200b"

    def build_round_summary(self, rnd: RoundState, scores_snapshot: dict[int, int]) -> str:
        lines = ["**Round Summary**", ""]
        for order_pos, gidx in enumerate(rnd.found_order):
            g = rnd.groups[gidx]
            emoji = GROUP_COLORS[order_pos]
            if gidx == rnd.found_order[-1] and len(rnd.found_order) == 4 and order_pos == 3:
                finder = "*(auto-revealed)*"
            elif g.found_by:
                finder = f"<@{g.found_by}>"
            else:
                finder = "*(nobody)*"
            lines.append(f"{emoji} **{g.category}** — {finder}")
            lines.append(f"    {', '.join(g.words)}")
        return "\n".join(lines)
