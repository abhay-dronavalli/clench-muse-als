# data/

- `menu.yaml` the hand-written menu tree, English and Spanish: at most 5 items per level (the core
  adds "Other..."), plus optional `more` lists that "Other..." shows when there is no AI.
- `contacts.yaml` the People menu. Each contact names the `.env` variables holding its phone number
  and Telegram chat id; the values never go in git.
- `profile.yaml` the patient: name, starting language, help alert contact.
- `clench.db` (git-ignored, created by the core) local SQLite history: events and phrases (PRD A7, D14).
- `seed_demo_week.json` (later chunk) a simulated week of use for the "5 -> 1" demo moment.
- `recordings/` CSVs of real Muse sessions for replay tests. The CSVs are git-ignored; only the folder is kept.
