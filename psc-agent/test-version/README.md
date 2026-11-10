# psc-agent

A research agent for perovskite solar cell papers. It does three jobs:

- **recheck**: audits hand-entered device workbooks (`Raw Data/<doi>_Sample<N>.xlsx`) cell by cell against
  their source PDFs.
- **extract**: drafts workbooks for papers that have none yet.
- **analyze**: works through the processed dataset to find key insights, each backed by evidence.

Models run on OpenRouter. Two mechanisms steer them, and neither needs a code change:

- **Skills** (`SKILL.md`) are the instructions. The skills listed for a role make up that role's system prompt.
- **Hooks** are the checkpoints around every model call and tool call: trace, budget, evidence quotes, data map.

## Roles, models, skills

Each role can run on a different model: Big models plan and map, small models judge.

| role | job | default skills |
|---|---|---|
| `recheck` | re-extract the hidden cells of existing rows | psc-domain, workbook-schema, evidence-rules, read-figures, recheck-task |
| `mapper` (Big) | paper map: every device with its layer stack | … extract-task |
| `extract` | rows of the other sheets for every device | … extract-task |
| `judge` | is recorded text the same as the paper's? | psc-domain, judge-equivalence |
| `decision` (small) | accept / verify_more / revise + probability per claim | psc-domain, evidence-rules, analysis-method, data-map, decision-rubric |
| `planner` (Big) | analysis plan: questions with acceptance criteria | psc-domain, data-map, analysis-method, plan-task |
| `analyst` | answers one question with data tools | psc-domain, data-map, analysis-method, analyst-tools |
| `reporter` (Big) | INSIGHTS.md from accepted claims | … insight-report |
| `si_reader` | reads a paper's text: does it refer to supporting information, and which address does it print? | si-reader-task |
| `si_finder` | finds and downloads a paper's supporting information on the web | si-finder-task |
| `learner` | rules for the next run, from the trace | learner-task |

`psc-agent skills --role analyst` prints the exact system prompt a role gets.

To change how a model works, edit or add a skill: `<skills dir>/<name>/SKILL.md` with front matter
`name`, `description`, `roles: [..]` and `order`. A project skill with the same `name` replaces the default one.
Two skills are generated into the project skills folder:

- **`data-map`**: the analysts' compass. Every table and column with its role, meaning and paper coverage.
  DataMapHook rebuilds it at the start of a run whenever a data file was added, moved, removed or changed.
- **`learned-rules`**: written by `psc-agent learn` from the trace. It lists tool calls that worked, the calls
  that failed and why, and what the decision model kept asking for, plus rules condensed by a Big model.

## Hooks (checkpoints)

| hook | event | what it enforces |
|---|---|---|
| `TraceHook(path)` | every event | `trace.jsonl`: model calls (tokens, cost, tool calls), tool results and errors, answers, decisions |
| `QuoteCheckHook()` | after_answer | an evidence quote must be on the page it cites, or the value is untrusted (recheck → `review`, extract → `verify_more`) |
| `BudgetHook(usd)` | before_model | no model calls past the budget |
| `DataMapHook(data, skills)` | run_start | data-map skill matches the data files |

A hook is any object with `on_<event>(**ctx)` methods. The events are run_start, before_model, after_model,
model_error, before_tool, after_tool, after_answer, decision, run_end, plus batch (papers about to run) and
progress (scope, done, total: steps finished in one paper), which the web page turns into a progress bar and an
estimate of the time left. Raising `HookBlock` in a before_*
event refuses the action.

## Workflows

**recheck**: blind re-extraction. The model sees each row's key columns (layer, material, step, property)
but never the recorded values. Values come back with page + quote and pass the quote check. Code compares
them (units, rounding, figure tolerance), and the judge settles text that still differs. Output is
`findings.csv`, with status per cell: mismatch, unit_mismatch, missing_in_record, not_found, needs_si, review,
not_checked, match. Rules that keep the list honest:
- the paper giving more values than the record (a thickness next to a pressure) is not a mismatch: each recorded
  value is paired with a paper value of its own unit, and the extra ones are noted ("paper also gives 100 nm");
- `98+` is 98 with a qualifier; an amount written in the recorded unit's note (`M (0.5 mmol in 5 mL)`) counts;
- numbers that agree once the recorded unit is read as another one of the same kind (1440 "sec" is 24 h as minutes)
  are `unit_mismatch`, and the detail names the likely right unit;
- a paper value for a blank Descriptions or Remarks cell is not reported (curators leave them empty);
- `needs_si`: the recorded value is not in the main text (or the paper points to another report) and no SI is loaded,
  so it probably comes from the SI. Save the SI in `SI/` and rerun: it then becomes match or `not_found`;
- identical review rows of several devices go to the judge once.
- `si_link` and `si_save_as` columns (findings.csv, review.csv, and an SI column in the web tables): where to download
  the SI (the publisher's supplementary page when its address is known, else the DOI link) and the file name to save
  it under in `original paper/SI/`. Filled on the rows the SI could settle (`needs_si`, `not_found`; every row for
  extract) and only when no SI is loaded. The result page also shows one notice with the link.

**extract**: the mapper (Big model) lists the devices, control first, with their layer stacks. The extract
model then fills Vender+Materials, Process and Properties+Stability rows for all devices. Rules run before the
decision model:
- a quote that is not on its page → verify_more
- a value read off a figure → verify_more
- a table value goes to the model only if the number is in the page text

The small decision model judges the rest. Output: `<doi>_Sample<N>.xlsx` drafts in the Raw Data layout, and
`review.csv` with each row's evidence and verdict.

**supporting information (SI)**: recipes, suppliers and extra tables are often only in the SI, so every workflow reads
it when it is there. SI PDFs live in `<pdf folder>/SI/<doi key>*.pdf` (the doi key is the DOI as lowercase letters
and digits, e.g. `101039d3lf00258f_SI.pdf`). Pairing finds them; `load_paper(pdf, si=[...])` appends them after the
main pages as text only (at most 60 pages), marked `(supporting information)`, so page numbers and quotes work as
before. `--no-si` ignores them. Two ways to get the files:
- by hand: download them from the journal and drop them in the `SI/` folder;
- `psc-agent si --raw ... --pdf ...`: the `si_finder` agent searches the web (DuckDuckGo), reads the article page,
  downloads the SI and writes `si_report.csv` (found / blocked / not_found) and `si_links.md`: for every paper it could
  not save, the pages where a person gets the SI (publisher page, DOI link, repository links it saw) and the file name
  to save it under. Publishers such as ACS and Wiley answer scripts with HTTP 403, so those need a manual download.
  `--links-only` skips the search and the model and just writes those pages.
- the web page: the Recheck list has an **Upload SI** button per paper, and the box beside a result lists each SI
  link once with an **Upload the file here** button (`POST /api/si` with `key` and `files`; `GET /api/si`,
  `DELETE /api/si/<name>`). The file is checked (a readable PDF), saved as `SI/<doi key>_SI.pdf` (`_SI2` ... for more)
  and used by the next run; remove it with the ✕ next to its name.
- `psc-agent si --browser`: the agent uses a Chrome window of its own (Playwright drives the installed Chrome;
  `pip install playwright`, no browser download) that you sign in once with `psc-agent browser login [--url <your
  library's login page>]` (it keeps the session in `~/.psc-agent/chrome`, a profile separate from your daily Chrome:
  Chrome does not allow remote control of the default profile). Publishers that answer plain requests with 403 serve
  their pages to a signed-in browser, so the agent opens the article, finds the supplementary file and saves it in
  `SI/`. A bot check or a sign-in page is never solved by the program: the window stays open for you
  (`--wait-human 300` seconds, default) and the terminal says so. Papers run one after another. It runs on the host,
  not in the Docker container. `--cdp-url http://127.0.0.1:9222` attaches to a Chrome you started yourself with
  `--remote-debugging-port` and its own `--user-data-dir`.

**analyze**:
1. The planner (Big model) turns the goal into acceptance criteria (ACs).
2. For each AC, the analyst calls the data tools. Every number in its claim cites a tool result `[Tn]`.
3. The decision model accepts it, or sends it back with a reason. A rule rejects claims that cite nothing,
   and p < 0.7 becomes verify_more ("escalate:").
4. The reporter writes INSIGHTS.md from accepted claims only.

`PLAN.md` binds each AC to its evidence file and verdict.

The data tools implement the project's method, so the model cannot get it wrong:

- papers are DOI keys (two spellings of one DOI merge)
- a modified device is compared only with the control of the same paper
- one paper = one vote
- exact sign test

On the current data, `paper_deltas(["pce"])` reproduces the 22-paper reference
`processed/eda/within_paper_deltas.csv` exactly. It finds 29 papers with control + treated devices
(28 positive, median ΔPCE +1.9).

Tools: list_tables, describe_table, column_values, query_rows, paper_deltas, within_paper_correlation,
paper_devices. A wrong table or column name comes back as an error that lists the valid or similar names.

## Package layout

```
src/psc_agent/
├─ core/        the engine every workflow shares
│   openrouter   model client: retries, cache, cost        runtime    role → model + system prompt + hooks; Usage
│   skills       SKILL.md files → system prompt per role   hooks      TraceHook, BudgetHook, QuoteCheckHook
│   evidence     rows with page + quote; the quote check   agent      tool-calling loop
│   decision     accept / verify_more / revise             learn      trace → learned-rules skill
├─ sources/     paper (PDF text + page images) · workbook (Raw Data xlsx) · pairing (PDF ↔ workbooks by DOI)
├─ recheck/     rechecker · compare · prompts · report · batch
├─ extract/     extractor
├─ analyze/     analyst · tools (DataTools) · datamap (data-map skill + DataMapHook)      needs pandas
├─ web/         Flask app: api · jobs (ProgressHook, CancelHook) · tasks · templates/ · static/
├─ skills/      default SKILL.md files, one folder per skill
└─ cli.py       psc-agent / python -m psc_agent
```

Dependencies point one way: the workflows and web use core and sources, and core uses nothing above it.
Everything public is importable straight from `psc_agent`.

## Install

```bash
pip install -e "agent for research[analysis]"      # [analysis] adds pandas for analyze / map
# from another project
pip install "psc-agent[analysis] @ git+https://github.com/<owner>/<repo>.git#subdirectory=agent%20for%20research"
```

Python ≥ 3.10. Put `OPENROUTER_API_KEY=sk-or-...` in a `.env` file (copy `.env.example`; it is git-ignored; the CLI reads `./.env` and the
project folder's `.env`), or set it as an environment variable (which wins over the file), or pass `api_key=`. The key only goes into the Authorization header.

## One command for a paper: `psc-agent run`

Per paper, cheapest step first, so the extract / recheck agent reads the paper **once**, together with its SI:

1. **SI reader** (role `si_reader`, a model with no tools): reads the paper's own text. Does it refer to supporting information
   ("Supporting Information", "supplementary material", "ESI", "Figure S3")? No: the SI is skipped (the sentence it relied on is
   kept in the report). Does the paper print an address for its SI ("Supplementary data can be found online at ...")? That
   link is enough (status `printed`; an address the paper does not contain is dropped). About $0.002 a paper with Haiku 5.5.
   `--no-si-reader` swaps it for a text rule (no model, the same words plus two different "Figure S3" / "Table S1" references,
   which flags 49 of the 53 papers and pulls out no address). If the model fails, the rule decides.
2. **SI link (no model):** ACS and Wiley supplementary pages follow from the DOI: that link is enough.
3. **SI agent** (role `si_finder`): only for the rest. It searches the web and answers with the SI addresses
   (`--browser`: downloads through your signed-in Chrome). SI files already in `SI/` are used as they are.
4. **One reading** with the SI appended: the **extract** agent (mapper, extract, decision) for a PDF with no workbooks, the
   **recheck** agent (recheck, judge) for a paper with hand-entered workbooks (`--mode auto|extract|recheck`).

Each agent is its own role with its own skills and model. Defaults: the **SI reader**, **extract** and **recheck** agents on **Haiku 5.5**,
the **SI agent** (web search with tools) on **Sonnet 5.5**, through OpenRouter (`OPENROUTER_API_KEY`; billed per token).
Change one with `--si-model`, `--extract-model`, `--recheck-model`, the others with `--model`, or use your Claude plan with
`--provider claude-code` (then the tool calling of the SI agent is emulated and less reliable).

```
psc-agent run --raw "Raw Data" --pdf "original paper" --mode extract --limit 2 --out run_out
```
Outputs: `run_summary.csv` (one line per paper: mode, SI status, result, cost), `si_links.md` (SI to download by hand),
`findings.csv` + `papers/` (recheck), `extract/<key>/` (drafts and review.csv). SI statuses: `have`, `not_referenced`,
`pattern`, `found`, `links`, `blocked`, `not_found`, `manual`, `error`. Put downloaded SI in `original paper/SI/` and run again.

## The full loop, no person in it: `psc-agent loop`

Papers in, trusted rows out. Per paper: SI (as in `run`) -> **extract** (a sheet group that failed, e.g. a 503, is asked
again, `--rounds`) -> **second reader**: a `Rechecker` on another model reads the paper against every cell of the drafts
-> **final status**: a row is `accepted` only when the second reader confirmed it (and the first pass did not say
`revise`), otherwise `held` with the reason -> **gate** -> `staged/`.

```bash
# 1. measure the pipeline once, on papers that have hand-entered workbooks (this costs model calls)
psc-agent loop --raw "Raw Data" --pdf "original paper" --calibrate --limit 4 --provider openrouter --model anthropic/claude-haiku-5.5                --verifier-model anthropic/claude-sonnet-5.5 --out loop_out/cal
# 2. then run on the PDFs that have no workbooks: accepted rows are staged only if the calibrated precision >= --min-precision
psc-agent loop --raw "Raw Data" --pdf "original paper" --provider openrouter --model anthropic/claude-haiku-5.5                --verifier-model anthropic/claude-sonnet-5.5 --out loop_out/run1
```

- `--calibrate` compares what the extractor alone produced and what both readers accepted with the hand-entered
  workbooks and writes `calibration.json` (extract model, second-reader model, found rate, correct-when-found rate).
- The gate opens only with a calibration made with these very models, over at least 100 hand-entered values, whose
  precision of the accepted rows reaches `--min-precision` (default 0.9). Otherwise the accepted drafts wait in
  `papers/<key>/accepted/` and nothing is staged. It never writes into `Raw Data/` and never runs prepare-data.
- Outputs: `provenance.csv` (every row, both readings, final status, both models), `held.csv`, `staged/*.xlsx` (accepted
  rows only, Raw Data layout), `audit_sample.csv` (`--sample`, 5% of the accepted rows, for an occasional look; it blocks nothing),
  `loop_summary.csv`, `REPORT.md`.
- A second reader on the **same** model repeats its own mistakes: use `--verifier-model` / `--verifier-provider`.
- Values read off a figure stay `confidence = figure` even when both readers agree (two readings of a plot are estimates).
- What it cannot do: values only in an SI that could not be downloaded stay missing, and the Gemini free tier (about 20
  requests a day per model) is too small for a loop: a 429 stops the paper and is reported in `REPORT.md`.

## Providers (`--provider`)

| provider | key | what it is good for | limits |
|---|---|---|---|
| `openrouter` (default) | `OPENROUTER_API_KEY` | any hosted model, tool calling, billed per token | |
| `claude-code` | none (your Claude plan) | Claude models at no extra charge | Claude only; cost shown is API-equivalent |
| `gemini` | `GOOGLE_GEMINI_API_KEY` (or `GEMINI_API_KEY`, `GOOGLE_API_KEY`) | reads the **whole PDF** (no page images), JSON bound to the schema; default `gemini-3.5-flash` | no tool calling (no analyze, and `run` skips the SI web-search agent; the SI reader and extract run on Gemini); cost not priced; the free tier has only Flash models and rate limits (retried), Pro models need billing |
| `ollama` | `OLLAMA_CLOUD_API_KEY` (or `OLLAMA_API_KEY`) | open models through Ollama Cloud (`https://ollama.com/v1`), tool calling; default `deepseek-v4-pro` | needs credits or a plan (HTTP 402 otherwise); cost not priced |

`psc-agent si --provider ollama --model deepseek-v4-pro --urls-only` makes the agent search the web and answer with the
SI addresses only (the file when a page shows it, else the page that lists it; only addresses it really saw are kept,
the publisher page and the DOI link follow), written to `si_links.md` / `si_report.csv` with status `links`.

Compare two runs on the papers both have, by the same rules: `psc-agent compare evaluate_out/runA evaluate_out/runB
--labels A B` writes headline numbers, cell-by-cell agreement, per sheet, per core field, per paper and the cells where
they disagree. Runs scored under older comparison rules should be scored again on a copy first:
`cp -r runA runA_rescored; psc-agent evaluate ... --rescore --out runA_rescored` (no model call).

## Command line

```bash
psc-agent pairs   --raw "Raw Data" --pdf "original paper"
psc-agent si      --raw "Raw Data" --pdf "original paper" [--only DOI ...] [--workers 3]   # SI -> original paper/SI/
psc-agent recheck --raw "Raw Data" --pdf "original paper" --model anthropic/claude-sonnet-5.5 \
                  --skills "agent for research/skills" --budget 5 [--only DOI ...] [--workers 4]
psc-agent extract "original paper/13 Stabilized hole-selective layer.pdf" --model anthropic/claude-sonnet-5.5 \
                  --mapper-model anthropic/claude-opus-5.5 --decision-model anthropic/claude-haiku-5.5
psc-agent analyze --goal "Which HTL modifications raise PCE, through which channel, and why?" \
                  --data processed/prepared_v2 --skills "agent for research/skills" \
                  --model anthropic/claude-sonnet-5.5 --decision-model anthropic/claude-haiku-5.5
psc-agent columns --data processed/prepared_v2 [--table T ...] [--relevance core drop ...] [--format text|md|csv] [--out FILE]
psc-agent map     --data processed/prepared_v2 --skills "agent for research/skills"
psc-agent trace   analyze_out/trace.jsonl
psc-agent learn   analyze_out/trace.jsonl recheck_out/trace.jsonl --skills "agent for research/skills" [--model M]
psc-agent skills  --skills "agent for research/skills" [--role decision]
```

Every model command writes `trace.jsonl` and caches responses in `<out>/.cache`, so a rerun costs nothing.

## Docker

The image runs the web app by default, and every CLI command through the same entrypoint. From this folder:

```bash
cp .env.example .env                       # put OPENROUTER_API_KEY=... in it; passed as an environment variable, not baked in
docker compose up --build                  # web app on http://127.0.0.1:5000
docker compose run --rm psc pairs --raw "Raw Data" --pdf "original paper"
docker compose run --rm psc evaluate --raw "Raw Data" --pdf "original paper" --provider openrouter --budget 5 --out out/eval
docker compose run --rm psc si --raw "Raw Data" --pdf "original paper" --links-only --out out/si
```

`docker-compose.yml` mounts `Raw Data/` and `processed/` read-only, `original paper/` writable (downloaded SI goes to
`original paper/SI/`), `skills/` (the data map is written there), `out/` (results of CLI runs: use `--out out/<name>`)
and a volume for the web jobs. The port is published on 127.0.0.1 only: the service has no login and starts paid
model runs. `--provider claude-code` (the Claude subscription) needs the `claude` CLI and its login, so it is not
available in the container; use OpenRouter. In Git Bash on Windows, prefix commands that take a `/path` argument with
`MSYS_NO_PATHCONV=1`.

## Agent server (your own Chrome, outside Docker)

Publishers answer a script with 403 but serve a browser that is signed in through your institution. Docker has no Chrome
and no sign-in, so the agent that needs the browser runs as a small server **on your computer**; the web app (in Docker
or not) sends it a paper and gets the SI back. The server drives *your* signed-in Chrome: it is the same agent as
`si --browser`, behind a token.

```bash
pip install playwright                          # drives the installed Chrome, no browser download
python -m psc_agent browser login               # once: sign in to the publishers / your library in the window
python -m psc_agent agent-server --pdf "../../original paper"        # http://127.0.0.1:5100
```

It needs `AGENT_SERVER_TOKEN` in `.env` (the command prints a random one to paste when it is missing) and
`OPENROUTER_API_KEY` (the agent runs on `--model`, Sonnet 5.5 by default, at most `--budget` USD a paper). `--pdf` must be the folder the
web app reads: the SI is saved in `<pdf folder>/SI/<doi key>_SI.pdf`, which Docker mounts, so the web app finds it by key.
`docker-compose.yml` already points the web app at `http://host.docker.internal:5100`; outside Docker set
`AGENT_SERVER_URL=http://127.0.0.1:5100` in `.env`. Restart the web app after adding the token.

Then step 2 of *Extract* shows **Download the SI with my Chrome**: a Chrome window opens on your computer and the agent
reads the publisher page and downloads the file. A sign-in page or a bot check is left to you (the window stays open,
`--wait-human` seconds, 300 by default); the program never solves one. One paper at a time. If the server is not
running the step says so. Only the token holder can use it, so keep the default `--host 127.0.0.1`.

Limits: a page that only lets you *view* a file (a licence that blocks the download) still cannot be saved, and the agent
cannot yet read a PDF it has not downloaded. Use it only for papers you would download by hand: some licences forbid
automated downloading.

API: `GET /health`, `POST /si/download` (multipart `pdf`, `key`, optional `model`; header `Authorization: Bearer <token>`).
The web app: `GET /api/agent-server`, `POST /api/jobs {kind: "si_download"}`.

## Columns (what to keep for the analysis)

`psc-agent columns` prints every column of the processed tables (289 in `processed/prepared_v2`) with its type, share
of rows filled, number of papers, example values, its meaning and how much the analysis needs it; the web app shows
the same on the Columns tab (filter by table and relevance, search, download CSV or Markdown, print). The point is to
cut the dataset down to what the research needs: insights from the extracted papers (which class of substance changes
the device results, how and through which channel), not tuning a PCE or retention value.

- Relevance: `core` (the analysis reads it), `context` (needed to compare fairly), `provenance` (identifiers and source
  traces, for joins and audit), `drop` (redundant, constant, empty or a raw leftover: a candidate to remove).
- Measured from the tables: `psc_agent/analyze/columns.py`. Meaning and relevance are written by hand in
  `processed/eda/column_notes.csv` (table, column, relevance, description, why); the older
  `processed/eda/column_catalog.csv` fills in the meaning of a column that has no note yet. Edit the notes file to
  change a rating; a column without a note shows as "not rated".

## Web app (Flask backend + HTML / Tailwind / JS frontend)

```bash
pip install -e "agent for research[web]"
python -m psc_agent serve --root .        # http://127.0.0.1:5000 with waitress; --dev = Flask dev server
```

The app has six tabs:

| tab | what it shows |
|---|---|
| Overview | pairing counts, data tables, recent jobs, setup |
| Dashboard | accuracy of an extraction against the hand-entered workbooks (see below), and a form to run an evaluation |
| Recheck | pick papers → findings table filtered by status |
| Extract | three steps: 1 pick or upload a PDF, 2 settle its SI (check, find the link, upload the file), 3 extract → review table and .xlsx drafts |
| Analyze | goal → AC cards with verdicts and evidence, then INSIGHTS.md |
| Skills | view and edit SKILL.md files, preview the exact system prompt of each role |
| Jobs | live log and cost, Cancel, files, trace digest, "Learn from this run" |

- Every run is a background job in `runs/<job id>/`, with the same files as the CLI.
- The workflow's hooks drive the page:
  - ProgressHook streams the log and cost
  - CancelHook stops the job before its next model call
  - BudgetHook caps each job (5 USD unless the form says otherwise)
  - TraceHook writes trace.jsonl
- REST API: `GET /api/status | pairs | pdfs | skills | prompt?role= | data/tables`, `POST /api/jobs {kind, params}`,
  `GET /api/jobs/<id>[/files | findings | review | analysis | trace]`, `POST /api/jobs/<id>/cancel`, `POST /api/uploads`,
  `PUT|DELETE /api/skills/<name>`, `POST /api/data/map`.
- To embed it, call `create_app(WebConfig(root=...))`. It returns a plain Flask app.
- Safety:
  - The server binds 127.0.0.1 and has no login, so put it behind authentication before exposing it.
  - The key stays in the server's environment.
  - Uploads must be PDFs (checked by their `%PDF-` header) of at most 200 MB.
  - Files are served only from the job's own folder.
- Styling: `app.css` is prebuilt and committed, so running the app needs no Node. After changing classes in
  `index.html` or `app.js`, rebuild it with `cd "agent for research/tailwind" && npm ci && npm run build`.

### Extract in three steps

1. **Choose a paper** (a PDF in the folder, or upload one).
2. **Supporting information**, settled before the paper is read: the page first checks the paper's own text for references to an SI
   with rules (free) and shows what to do: *SI loaded* (files already saved), *not referenced* (nothing to find), *pattern* (ACS and
   Wiley: the publisher's page, open it and download), or *agent needed* (another publisher). **Read the paper for its SI (model)**
   lets the SI reader (Haiku 5.5, under a cent) read the text instead: it can overrule the rule, and returns the address the paper
   prints (*printed*); only when the paper prints none and the publisher has no known page does the SI agent search the web (it
   answers with addresses and downloads nothing). Then **upload the SI file** if you have it (saved as `original paper/SI/<doi key>_SI.pdf`,
   removable with the ✕). Uploading is optional.
3. **Extract**: the button says what will be read ("Extract (paper + 1 SI file)" or "Extract without the SI"), and the paper
   is read together with its SI once. Without the SI the rows only it holds are missing and flagged; upload it and extract again to fill them.

API: `GET /api/si/plan?pdf=<id>`, `POST /api/jobs {kind: "si"}` (the SI reader, model `si_reader`, Haiku 5.5, then the SI agent `si_finder`, Sonnet 5.5, when needed),
`POST /api/si` (upload), `POST /api/jobs {kind: "extract"}` (reads the SI saved for the paper).

### Dashboard (accuracy)

The Dashboard tab scores an `evaluate` run: the model reads each paper without the workbooks and every hand-entered value is
compared with what it extracted. Tiles: **values that match**, **of those in the PDF text** (the fair measure: values only the
SI holds cannot be found in a PDF), **correct when given** (precision), **values the model gave** (coverage), **core fields**
and **rows found**; then by sheet, by core field and by paper (click a paper for its values that did not match), and a
filterable list of the values that did not match with what the model gave instead.

- Pick a second run in **Compare with** to see two runs side by side (two models, with and without the SI): A, B, the
  difference in points, and the cells where only one of them matches. Only papers finished in both runs are compared.
- Runs listed: the web's own evaluate jobs, and the folders `out/` and `evaluate_out/` (project root and working
  folder; Docker mounts `evaluate_out/` read-only), so runs made with the CLI show up too.
- **Run an evaluation** (bottom of the tab): choose papers that have workbooks, a model and a budget. It is a normal job
  (progress and cancel in Jobs); each finished paper appears in the Dashboard at once. The auditor is off unless ticked.
- API: `GET /api/dashboard/runs | run?id= | compare?a=&b= | cells?id=&status=&sheet=&paper=&core=1`.

## Python / backend

```python
from psc_agent import Runtime, Rechecker, Extractor, TraceHook, QuoteCheckHook, BudgetHook, load_paper, load_workbook
from psc_agent import Analyst, DataTools, DataMapHook        # need pandas

rt = Runtime({"default": "anthropic/claude-sonnet-5.5", "mapper": "anthropic/claude-opus-5.5",
              "decision": "anthropic/claude-haiku-5.5"},
             skills_dirs=["skills"],                               # your lab's skills, added to the defaults
             hooks=[TraceHook("trace.jsonl"), QuoteCheckHook(), BudgetHook(10)])

report = Rechecker(runtime=rt).recheck(load_paper(pdf_bytes, name="p.pdf"),
                                       [load_workbook(xlsx_bytes, name="10.1000_x.1_Sample1.xlsx")])
report.model_dump()                                                 # JSON for an API response
Extractor(rt).extract("new.pdf", "out/new")                         # .xlsx drafts + review.csv
Analyst(rt, DataTools("processed/prepared_v2")).run("what drives PCE gains?", "out/analysis")
```

Every call blocks for minutes, so a FastAPI backend should run it with `run_in_threadpool`. A Runtime and its
hooks are thread-safe, so share them across requests. `run_tools(rt, role, messages, tools)` and
`decide(rt, kind, items)` are the building blocks if you need a workflow of your own.

## Limits

- Not yet run against a live model: no `OPENROUTER_API_KEY` was available. The tests use a fake OpenRouter
  per role. Try one paper (`--only`, a single PDF) and set `--budget` before a full run.
- Supporting Information is not read: those values come back null with note "SI".
- When two samples of one paper have identical layer stacks, the model may mix them up. Check `paper_device`.
- Figure values are estimates. `figure_tolerance` (recheck) sets how strict to be, and extract always leaves
  them to a person.
- TypeSafe's Jev fits the decision contract (typed choice + probability) but is not wired in: there is no key,
  and its SDK schema is unverified. To try it, call it where `decide` calls the decision role.
