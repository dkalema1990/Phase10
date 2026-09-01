"""
Phase Tracker — Streamlit edition
Run with:  streamlit run phase_tracker_streamlit.py
Requires:  pip install streamlit pandas
"""

import streamlit as st
import pandas as pd

PHASE_COUNT = 10

# ---------------------------------------------------------------------------
# Core game logic (framework-agnostic — same rules as the React version)
# ---------------------------------------------------------------------------

def replay(base_players, history):
    """Rebuild every player's current state by replaying the full round
    history from scratch. This is the single source of truth: editing an
    old round just means editing an entry in `history`, and everything
    downstream (phase, completed phases, totals) recomputes correctly."""
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
                continue  # sat out this round
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
        return (-phase_rank, p["total_score"])  # further phase first, then lower score
    return sorted(players, key=sort_key)


def zero_rule_valid(active_ids, inputs):
    """Exactly one active player must score 0 this round (the round winner)."""
    if not active_ids:
        return True
    zero_count = sum(1 for pid in active_ids if inputs.get(pid, {}).get("score", 0) == 0)
    return zero_count == 1


# ---------------------------------------------------------------------------
# Streamlit state setup
# ---------------------------------------------------------------------------

def init_state():
    if "started" not in st.session_state:
        st.session_state.started = False
        st.session_state.base_players = []
        st.session_state.history = []


def start_game(names):
    base_players = [
        {"id": f"p{i}", "name": n.strip() if n.strip() else f"Player {i + 1}"}
        for i, n in enumerate(names)
    ]
    st.session_state.base_players = base_players
    st.session_state.history = []
    st.session_state.started = True


def reset_game():
    st.session_state.started = False
    st.session_state.base_players = []
    st.session_state.history = []


# ---------------------------------------------------------------------------
# Styling — light theming to match the tabletop scorepad look
# ---------------------------------------------------------------------------

def inject_css():
    st.markdown(
        """
        <style>
        .stApp { background: radial-gradient(ellipse at top, #122A22 0%, #0D1F19 100%); }
        h1, h2, h3 { font-family: Georgia, 'Iowan Old Style', serif; color: #F6F1E3 !important; }
        .block-container { padding-top: 2rem; }
        div[data-testid="stForm"] {
            background: #F6F1E3;
            border: 1px solid #DCD0AE;
            border-radius: 6px;
            padding: 1.1rem 1.2rem;
        }
        div[data-testid="stExpander"] {
            background: #F6F1E3;
            border: 1px solid #DCD0AE;
            border-radius: 6px;
        }
        .stButton button {
            background-color: #A97F2C;
            color: #FBF6E9;
            border: none;
        }
        .stButton button:hover {
            background-color: #C69A3B;
            color: #FBF6E9;
        }
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
# Leaderboard + dashboards
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
        "doesn't stays put, and their score adds to what they've already built up on that phase."
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
                completed = st.checkbox(
                    "Completed", key=f"done_{p['id']}_{round_number}",
                )
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
                            "Completed", value=e["completed"],
                            key=f"edit_done_{round_num}_{e['player_id']}",
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
                        st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="Phase Tracker", page_icon="🂡", layout="centered")
    inject_css()
    init_state()

    if not st.session_state.started:
        render_setup()
        return

    players, annotated_history = replay(st.session_state.base_players, st.session_state.history)
    ranked = rank_players(players)
    all_finished = len(players) > 0 and all(p["finished"] for p in players)
    next_round = len(st.session_state.history) + 1

    top_left, top_right = st.columns([4, 1])
    with top_left:
        st.title("Phase Tracker")
        st.caption("Game complete" if all_finished else f"Round {next_round}")
    with top_right:
        if st.button("New game"):
            reset_game()
            st.rerun()

    render_leaderboard(ranked)
    render_dashboards(ranked)

    st.divider()

    if all_finished:
        st.success(f"Every player has cleared all {PHASE_COUNT} phases. Check the leaderboard above, or start a new game.")
    else:
        render_round_entry(players, next_round)

    st.divider()
    render_history(annotated_history)


if __name__ == "__main__":
    main()