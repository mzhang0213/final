# Response schema templates — one per use-case.
# Each schema is sent inline with the prompt so the model knows exactly
# what shape to return. Keeping them small minimises per-turn token cost.

SCHEMAS: dict[str, dict] = {

    # Polled every ~15s during an active game
    "live": {
        "priority": "what to focus on RIGHT NOW (short phrase)",
        "action":   "specific next action (one sentence)",
        "warning":  "threat or risk to watch, or null",
    },

    # Player typed an explicit question
    "question": {
        "answer":    "direct answer to the question (one or two sentences)",
        "follow_up": "related thing to consider, or null",
    },

    # Triggered by a game event (kill, death, dragon, baron, tower)
    "event": {
        "event_type": "kill | death | dragon | baron | tower | other",
        "reaction":   "how to respond to this event (one sentence)",
        "adjustment": "what to change in your play going forward (one sentence)",
    },

    # Called once at game end for a session summary
    "post_game": {
        "summary":     "2-3 sentence overview of how the game went",
        "top_mistake": "single biggest mistake to fix",
        "focus_next":  "one concrete thing to practise next game",
    },
}
