<!-- PROTOCOL_RETRY_NUDGE -->
(System) The previous turn's output did not pass the structured output gate, and the content was not shown to the user.
Do not restate that protocol. If you need to continue investigating, call the corresponding tool; if you can already answer, call reply_to_user and put the complete natural-language answer in content. Do not output plain text directly.

<!-- WRAP_UP_NUDGE -->
(System) You have already been investigating for several consecutive turns — stop and answer now. Do not take any new actions this turn.

Answer the user directly using what you already know:
- If you have found the answer → tell it to the user. Don't mention how many turns you searched or which tools you used.
- If you haven't found it, and this is actually **work inside a project** (searching code, editing files, running commands, opening a browser) → stop searching; this was never meant to be your job. Gently direct the user into a project: explain that things are more convenient there (can search code, run commands, has people to follow up), check the <Platform Updates>, and if there's a matching project, name it and tell the user to go ask the Coordinator there; if not, offer to open one for them.
- If you genuinely cannot answer → honestly say you can't, and give them a next step.

One natural sentence is enough. Don't explain your internal process, and don't say things like "I tried multiple times" or "aborted."
Finally, you must call reply_to_user and put that sentence in the content parameter; do not output plain text directly.