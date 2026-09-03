"""
Phase Tracker — Streamlit edition (Google Sheets storage)
Run with:  streamlit run phase_tracker_streamlit.py
Requires:  pip install streamlit pandas altair gspread google-auth

Every game (in progress and finished) is stored as a row in a Google Sheet,
so history survives redeploys and is shared across anyone using the app.
"""

import json
import uuid
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

PHASE_COUNT = 10
SHEET_COLUMNS = ["id", "created_at", "updated_at", "finished_at", "base_players", "history"]
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

INK = "#1E2A24"
CARD = "#F6F1E3"
CARD_LINE = "#DCD0AE"
GOLD = "#C69A3B"
GOLD_DEEP = "#A97F2C"
MUTED = "#6B6350"  # darkened from the original for better contrast on light backgrounds

# ---------------------------------------------------------------------------
# Core game logic (framework-agnostic)
# ---------------------------------------------------------------------------

def replay(base_players, history):
    """Rebuild every player's current state by replaying the full round
    history from scratch. Editing an old round just means editing an entry
    in `history` — everything downstream recomputes correctly."""
    state = {}
    for p in base_players:
        state[p["id"]] = {
            "id": p["id"],
            "name": p["name"],
            "current_phase": 1,
            "phase_scores": [0] * PHASE_COUNT,
            "completed": [False] * PHASE_COUNT,
            "total_score": 0,
            "finished": False,
        }

    annotated_history = []
    for round_data in history:
        entries = []
        for p in base_players:
            s = state[p["id"]]
            if s["finished"]:
                continue
            raw = round_data["entries"].get(p["id"], {"score": 0, "completed": False})
            phase_index = s["current_phase"] - 1
            phase_at_round = s["current_phase"]

            s["phase_scores"][phase_index] += raw["score"]
            s["total_score"] += raw["score"]

            if raw["completed"]:
                s["completed"][phase_index] = True
                if s["current_phase"] < PHASE_COUNT:
                    s["current_phase"] += 1
                else:
                    s["finished"] = True

            entries.append({
                "player_id": p["id"],
                "player_name": p["name"],
                "phase": phase_at_round,
                "score": raw["score"],
                "completed": raw["completed"],
            })
        annotated_history.append({"round": round_data["round"], "entries": entries})

    players = [state[p["id"]] for p in base_players]
    return players, annotated_history


def rank_players(players):
    def sort_key(p):
        phase_rank = PHASE_COUNT + 1 if p["finished"] else p["current_phase"]
        return (-phase_rank, p["total_score"])
    return sorted(players, key=sort_key)


def zero_rule_valid(active_ids, inputs):
    if not active_ids:
        return True
    zero_count = sum(1 for pid in active_ids if inputs.get(pid, {}).get("score", 0) == 0)
    return zero_count == 1


# ---------------------------------------------------------------------------
# Persistence — Google Sheets, one row per game
# ---------------------------------------------------------------------------

def sheets_configured():
    return "gcp_service_account" in st.secrets and "gsheet" in st.secrets


@st.cache_resource(show_spinner=False)
def get_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource(show_spinner=False)
def get_worksheet():
    client = get_client()
    spreadsheet_id = st.secrets["gsheet"]["spreadsheet_id"]
    sh = client.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet("games")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="games", rows=1000, cols=len(SHEET_COLUMNS))
    header = ws.row_values(1)
    if header != SHEET_COLUMNS:
        ws.clear()
        ws.append_row(SHEET_COLUMNS)
    return ws


def _find_row(ws, game_id):
    ids = ws.col_values(1)
    for i, val in enumerate(ids[1:], start=2):
        if val == game_id:
            return i
    return None


def create_game(base_players):
    ws = get_worksheet()
    game_id = uuid.uuid4().hex[:10]
    now = datetime.utcnow().isoformat()
    ws.append_row([game_id, now, now, "", json.dumps(base_players), json.dumps([])])
    return game_id


def update_game_history(game_id, history):
    ws = get_worksheet()
    row = _find_row(ws, game_id)
    if row is None:
        return
    now = datetime.utcnow().isoformat()
    ws.update_cell(row, 3, now)
    ws.update_cell(row, 6, json.dumps(history))


def finish_game(game_id):
    ws = get_worksheet()
    row = _find_row(ws, game_id)
    if row is None:
        return
    now = datetime.utcnow().isoformat()
    ws.update_cell(row, 3, now)
    ws.update_cell(row, 4, now)


def list_games():
    ws = get_worksheet()
    records = ws.get_all_records()
    records.sort(key=lambda r: r["created_at"], reverse=True)
    return records


def get_unfinished_game():
    for g in list_games():
        if not g["finished_at"]:
            return g
    return None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

def init_state():
    if "started" not in st.session_state:
        st.session_state.started = False
        st.session_state.game_id = None
        st.session_state.base_players = []
        st.session_state.history = []
        st.session_state.finished_saved = False
        st.session_state.show_celebration = False


def start_game(names):
    base_players = [
        {"id": f"p{i}", "name": n.strip() if n.strip() else f"Player {i + 1}"}
        for i, n in enumerate(names)
    ]
    game_id = create_game(base_players)
    st.session_state.base_players = base_players
    st.session_state.history = []
    st.session_state.game_id = game_id
    st.session_state.started = True
    st.session_state.finished_saved = False
    st.session_state.show_celebration = False


def reset_game():
    if st.session_state.get("game_id"):
        finish_game(st.session_state.game_id)
    st.session_state.started = False
    st.session_state.game_id = None
    st.session_state.base_players = []
    st.session_state.history = []
    st.session_state.finished_saved = False
    st.session_state.show_celebration = False


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

def inject_css():
    st.markdown(
        f"""
        <style>
        .stApp {{ background: radial-gradient(ellipse at top, #122A22 0%, #0D1F19 100%); }}
        h1, h2, h3 {{ font-family: Georgia, 'Iowan Old Style', serif; color: #F6F1E3 !important; }}
        .block-container {{ padding-top: 2rem; }}
        div[data-testid="stForm"] {{
            background: {CARD};
            border: 1px solid {CARD_LINE};
            border-radius: 6px;
            padding: 1.1rem 1.2rem;
        }}
        div[data-testid="stExpander"] {{
            background: {CARD};
            border: 1px solid {CARD_LINE};
            border-radius: 6px;
        }}
        .stButton button {{
            background-color: {GOLD_DEEP};
            color: #FBF6E9;
            border: none;
        }}
        .stButton button:hover {{
            background-color: {GOLD};
            color: #FBF6E9;
        }}
        /* Force legible text on tables/dataframes regardless of light/dark theme */
        div[data-testid="stTable"] {{
            background: {CARD};
            border: 1px solid {CARD_LINE};
            border-radius: 4px;
            padding: 6px 4px;
        }}
        div[data-testid="stTable"] table {{ color: {INK} !important; }}
        div[data-testid="stTable"] th {{
            color: {INK} !important;
            background: {CARD} !important;
            border-bottom: 1px solid {CARD_LINE} !important;
        }}
        div[data-testid="stTable"] td {{
            color: {INK} !important;
            background: #FDFBF6 !important;
        }}
        div[data-testid="stDataFrame"] {{
            background: {CARD};
            border-radius: 4px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Setup screen
# ---------------------------------------------------------------------------

def render_setup():
    st.title("Phase Tracker")
    st.caption("10-phase scorepad")

    unfinished = get_unfinished_game()
    if unfinished is not None:
        base_players = json.loads(unfinished["base_players"])
        history = json.loads(unfinished["history"])
        names_preview = ", ".join(p["name"] for p in base_players)
        when = str(unfinished["created_at"])[:16].replace("T", " ")
        st.info(f"Game in progress from {when} — {names_preview} ({len(history)} rounds played).")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Resume this game"):
                st.session_state.game_id = unfinished["id"]
                st.session_state.base_players = base_players
                st.session_state.history = history
                st.session_state.started = True
                st.session_state.finished_saved = False
                st.rerun()
        with col2:
            if st.button("Discard and start fresh"):
                finish_game(unfinished["id"])
                st.rerun()
        st.divider()

    num_players = st.number_input("Number of players", min_value=2, max_value=12, value=4, step=1)

    names = []
    with st.form("setup_form"):
        st.write("Names")
        for i in range(int(num_players)):
            names.append(st.text_input(f"Player {i + 1}", key=f"setup_name_{i}", placeholder=f"Player {i + 1}"))
        submitted = st.form_submit_button("Start game")
        if submitted:
            start_game(names)
            st.rerun()


# ---------------------------------------------------------------------------
# Leaderboard, chart, filters, dashboards
# ---------------------------------------------------------------------------

def render_leaderboard(ranked):
    st.subheader("Leaderboard")
    st.caption("Ranked by furthest phase reached, ties broken by lowest score")
    rows = []
    for i, p in enumerate(ranked):
        rows.append({
            "Rank": i + 1,
            "Player": p["name"] + (" 🏆" if p["finished"] else ""),
            "Phase": "Finished" if p["finished"] else f"{p['current_phase']} / {PHASE_COUNT}",
            "Total score": p["total_score"],
        })
    df = pd.DataFrame(rows).set_index("Rank")
    st.table(df)


def render_bar_chart(ranked, key_prefix="live"):
    st.subheader("Total score by rank")
    df = pd.DataFrame({
        "Player": [f"#{i + 1} {p['name']}" for i, p in enumerate(ranked)],
        "Total score": [p["total_score"] for p in ranked],
    })
    order = list(df["Player"])
    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Player:N", sort=order, title=None, axis=alt.Axis(labelAngle=-20)),
            y=alt.Y("Total score:Q"),
            color=alt.Color("Player:N", sort=order, legend=None, scale=alt.Scale(scheme="tableau20")),
            tooltip=["Player", "Total score"],
        )
    )
    labels = bars.mark_text(dy=-8, color="#F6F1E3", fontSize=13).encode(text="Total score:Q")
    chart = (bars + labels).properties(height=320)
    st.altair_chart(chart, use_container_width=True)


def render_phase_filter_table(players, key_prefix="live"):
    st.subheader("Phase score breakdown")
    all_names = [p["name"] for p in players]
    selected_players = st.multiselect(
        "Filter by player", options=all_names, default=all_names, key=f"{key_prefix}_player_filter"
    )
    selected_phases = st.multiselect(
        "Filter by phase",
        options=list(range(1, PHASE_COUNT + 1)),
        default=list(range(1, PHASE_COUNT + 1)),
        format_func=lambda x: f"Phase {x}",
        key=f"{key_prefix}_phase_filter",
    )
    if not selected_players or not selected_phases:
        st.info("Select at least one player and one phase to see the table.")
        return
    rows = {}
    for p in players:
        if p["name"] not in selected_players:
            continue
        rows[p["name"]] = [p["phase_scores"][ph - 1] for ph in selected_phases]
    df = pd.DataFrame(rows, index=[f"Phase {ph}" for ph in selected_phases]).T
    st.dataframe(df, use_container_width=True)


def render_dashboards(ranked):
    st.subheader("Player dashboards")
    for i, p in enumerate(ranked):
        status = "Completed all phases 🏆" if p["finished"] else f"Phase {p['current_phase']} of {PHASE_COUNT}"
        with st.expander(f"#{i + 1} · {p['name']} — {p['total_score']} pts — {status}"):
            phase_df = pd.DataFrame(
                {
                    "Phase": list(range(1, PHASE_COUNT + 1)),
                    "Score": p["phase_scores"],
                    "Completed": ["✔" if c else "" for c in p["completed"]],
                }
            ).set_index("Phase").T
            st.dataframe(phase_df, use_container_width=True)


def render_winner_banner(winner):
    st.markdown(
        f"""
        <div style="background:{CARD}; border:1px solid {CARD_LINE}; border-radius:6px;
                    padding:28px 22px; text-align:center; margin-bottom:18px;">
            <div style="font-size:44px; line-height:1;">🏆</div>
            <div style="font-family:Georgia, serif; font-size:26px; color:{INK}; margin-top:6px;">
                Congratulations, {winner['name']}!
            </div>
            <div style="font-size:13.5px; color:{MUTED}; margin-top:4px;">
                First to clear all {PHASE_COUNT} phases — finishing with {winner['total_score']} points.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Round entry
# ---------------------------------------------------------------------------

def render_round_entry(players, round_number):
    active_players = [p for p in players if not p["finished"]]
    finished_players = [p for p in players if p["finished"]]

    st.subheader(f"Record round {round_number}")
    st.caption(
        "One player goes out first and scores 0 — everyone else logs what they were left holding. "
        "Check 'Completed' for anyone who cleared their phase; they move up next round. Anyone who "
        "doesn't stays put, and their score adds to what they've already built up on that phase. "
        "The game ends the moment someone clears phase 10."
    )

    with st.form(f"round_form_{round_number}"):
        inputs = {}
        for p in active_players:
            col1, col2, col3 = st.columns([3, 2, 2])
            with col1:
                st.write(f"**{p['name']}** · phase {p['current_phase']}")
            with col2:
                score = st.number_input(
                    "Score", min_value=0, max_value=200, value=0, step=1,
                    key=f"score_{p['id']}_{round_number}", label_visibility="collapsed",
                )
            with col3:
                completed = st.checkbox("Completed", key=f"done_{p['id']}_{round_number}")
            inputs[p["id"]] = {"score": score, "completed": completed}

        for p in finished_players:
            st.caption(f"{p['name']} — finished, sitting out this round")

        submitted = st.form_submit_button(f"Record round {round_number}")

        if submitted:
            active_ids = [p["id"] for p in active_players]
            if not zero_rule_valid(active_ids, inputs):
                st.error("Exactly one player must score 0 this round.")
            else:
                st.session_state.history.append({"round": round_number, "entries": inputs})
                update_game_history(st.session_state.game_id, st.session_state.history)
                st.rerun()


# ---------------------------------------------------------------------------
# Editable round history
# ---------------------------------------------------------------------------

def render_history(annotated_history):
    if not annotated_history:
        return
    st.subheader(f"Round history ({len(annotated_history)})")
    st.caption("Made a mistake? Open a round below, fix it, and save.")

    for r in reversed(annotated_history):
        round_num = r["round"]
        summary = " · ".join(
            f"{e['player_name']} +{e['score']}{'✔' if e['completed'] else ''}" for e in r["entries"]
        )
        with st.expander(f"Round {round_num} — {summary}"):
            with st.form(f"edit_round_{round_num}"):
                draft = {}
                for e in r["entries"]:
                    col1, col2, col3 = st.columns([3, 2, 2])
                    with col1:
                        st.write(f"**{e['player_name']}** · phase {e['phase']}")
                    with col2:
                        score = st.number_input(
                            "Score", min_value=0, max_value=200, value=e["score"], step=1,
                            key=f"edit_score_{round_num}_{e['player_id']}", label_visibility="collapsed",
                        )
                    with col3:
                        completed = st.checkbox(
                            "Completed", value=e["completed"], key=f"edit_done_{round_num}_{e['player_id']}"
                        )
                    draft[e["player_id"]] = {"score": score, "completed": completed}

                save = st.form_submit_button("Save changes")
                if save:
                    active_ids = [e["player_id"] for e in r["entries"]]
                    if not zero_rule_valid(active_ids, draft):
                        st.error("Exactly one player must score 0 this round.")
                    else:
                        for h in st.session_state.history:
                            if h["round"] == round_num:
                                h["entries"] = draft
                                break
                        update_game_history(st.session_state.game_id, st.session_state.history)
                        st.rerun()


# ---------------------------------------------------------------------------
# Past games archive
# ---------------------------------------------------------------------------

def _game_label(g, base_players):
    players_str = ", ".join(p["name"] for p in base_players)
    when = str(g["created_at"])[:16].replace("T", " ")
    status = "Finished" if g["finished_at"] else "In progress"
    return f"{when} · {players_str} · {status}"


def _render_one_game(g, expanded_view=True):
    base_players = json.loads(g["base_players"])
    history = json.loads(g["history"]) if g["history"] else []
    players, annotated_history = replay(base_players, history)
    ranked = rank_players(players)
    if not ranked:
        st.caption("No rounds were played in this game.")
        return
    render_leaderboard(ranked)
    render_bar_chart(ranked, key_prefix=f"game_{g['id']}")
    render_phase_filter_table(players, key_prefix=f"game_{g['id']}")
    if annotated_history:
        st.write("Round-by-round")
        for r in annotated_history:
            summary = " · ".join(
                f"{e['player_name']} +{e['score']}{'✔' if e['completed'] else ''}" for e in r["entries"]
            )
            st.caption(f"Round {r['round']}: {summary}")


def render_past_games():
    st.title("Past games")
    games = list_games()
    if not games:
        st.info("No games recorded yet — play a game and it'll show up here.")
        return

    labels = ["All games"]
    label_to_game = {}
    for g in games:
        base_players = json.loads(g["base_players"])
        label = _game_label(g, base_players)
        labels.append(label)
        label_to_game[label] = g

    selected = st.selectbox("Filter by game", options=labels)

    if selected == "All games":
        for g in games:
            base_players = json.loads(g["base_players"])
            with st.expander(_game_label(g, base_players)):
                _render_one_game(g)
    else:
        _render_one_game(label_to_game[selected])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="Phase Tracker", page_icon="🂡", layout="centered")
    inject_css()

    if not sheets_configured():
        st.title("Phase Tracker")
        st.error("Google Sheets isn't connected yet.")
        st.markdown(
            "Add your service-account credentials and spreadsheet ID to `.streamlit/secrets.toml` "
            "(locally) or to your app's **Secrets** settings (on Streamlit Community Cloud)."
        )
        st.stop()

    try:
        get_worksheet()
    except Exception as e:
        st.title("Phase Tracker")
        st.error(f"Couldn't reach the Google Sheet: {e}")
        st.markdown(
            "Double check that: the spreadsheet ID in secrets is correct, the Sheets and Drive APIs "
            "are enabled on your Google Cloud project, and the sheet is shared with your service "
            "account's email as an Editor."
        )
        st.stop()

    init_state()

    page = st.sidebar.radio("View", ["Play", "Past games"])

    if page == "Past games":
        render_past_games()
        return

    if not st.session_state.started:
        render_setup()
        return

    players, annotated_history = replay(st.session_state.base_players, st.session_state.history)
    ranked = rank_players(players)
    game_over = len(players) > 0 and any(p["finished"] for p in players)
    next_round = len(st.session_state.history) + 1

    if game_over and not st.session_state.finished_saved:
        finish_game(st.session_state.game_id)
        st.session_state.finished_saved = True
        st.session_state.show_celebration = True

    top_left, top_right = st.columns([4, 1])
    with top_left:
        st.title("Phase Tracker")
        st.caption("Game complete" if game_over else f"Round {next_round}")
    with top_right:
        if st.button("New game"):
            reset_game()
            st.rerun()

    if game_over:
        winner = ranked[0]
        if st.session_state.show_celebration:
            st.balloons()
            st.session_state.show_celebration = False
        render_winner_banner(winner)

    render_leaderboard(ranked)
    render_bar_chart(ranked)
    render_phase_filter_table(players, key_prefix="live")
    render_dashboards(ranked)

    st.divider()

    if not game_over:
        render_round_entry(players, next_round)

    st.divider()
    render_history(annotated_history)


if __name__ == "__main__":
    main()