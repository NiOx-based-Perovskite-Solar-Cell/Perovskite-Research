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
model_error, before_tool, after_tool, after_answer, decision and run_end. Raising `HookBlock` in a before_*
event refuses the action.

## Workflows

**recheck**: blind re-extraction. The model sees each row's key columns (layer, material, step, property)
but never the recorded values. Values come back with page + quote and pass the quote check. Code compares
them (units, rounding, figure tolerance), and the judge settles text that still differs. Output is
`findings.csv`, with status per cell: mismatch, unit_mismatch, missing_in_record, not_found, review,
not_checked, match.

**extract**: the mapper (Big model) lists the devices, control first, with their layer stacks. The extract
model then fills Vender+Materials, Process and Properties+Stability rows for all devices. Rules run before the
decision model:
- a quote that is not on its page → verify_more
- a value read off a figure → verify_more
- a table value goes to the model only if the number is in the page text

The small decision model judges the rest. Output: `<doi>_Sample<N>.xlsx` drafts in the Raw Data layout, and
`review.csv` with each row's evidence and verdict.

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

Python ≥ 3.10. Set `OPENROUTER_API_KEY` or pass `api_key=`. The key only goes into the Authorization header.

## Command line

```bash
psc-agent pairs   --raw "Raw Data" --pdf "original paper"
psc-agent recheck --raw "Raw Data" --pdf "original paper" --model anthropic/claude-sonnet-5.5 \
                  --skills "agent for research/skills" --budget 5 [--only DOI ...] [--workers 4]
psc-agent extract "original paper/13 Stabilized hole-selective layer.pdf" --model anthropic/claude-sonnet-5.5 \
                  --mapper-model anthropic/claude-opus-5.5 --decision-model anthropic/claude-haiku-5.5
psc-agent analyze --goal "Which HTL modifications raise PCE, through which channel, and why?" \
                  --data processed/prepared_v2 --skills "agent for research/skills" \
                  --model anthropic/claude-sonnet-5.5 --decision-model anthropic/claude-haiku-5.5
psc-agent map     --data processed/prepared_v2 --skills "agent for research/skills"
psc-agent trace   analyze_out/trace.jsonl
psc-agent learn   analyze_out/trace.jsonl recheck_out/trace.jsonl --skills "agent for research/skills" [--model M]
psc-agent skills  --skills "agent for research/skills" [--role decision]
```

Every model command writes `trace.jsonl` and caches responses in `<out>/.cache`, so a rerun costs nothing.

## Web app (Flask backend + HTML / Tailwind / JS frontend)

```bash
pip install -e "agent for research[web]"
python -m psc_agent serve --root .        # http://127.0.0.1:5000 with waitress; --dev = Flask dev server
```

The app has six tabs:

| tab | what it shows |
|---|---|
| Overview | pairing counts, data tables, recent jobs, setup |
| Recheck | pick papers → findings table filtered by status |
| Extract | pick or upload a PDF → review table and .xlsx drafts |
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
