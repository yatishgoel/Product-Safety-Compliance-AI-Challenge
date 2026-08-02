# Product Safety Compliance

Give it a product file (text, PDF or image) and it says whether the product can ship:
**Accepted**, **Rejected**, or **Needs Review**, with a reason for every decision.

## Run it

Docker needs nothing installed but Docker:

```bash
make docker-run     # builds and serves on localhost:8000, Tesseract inside
```

Locally, Tesseract is a system binary so uv cannot install it:

```bash
brew install tesseract          # or: apt-get install tesseract-ocr
make setup
make check                      # scores the 30 provided files
make serve                      # localhost:8000, UI at /, docs at /docs
```

`make setup` installs Python dependencies only. Node is not required to run anything: the UI
production build is committed, so the API and the container serve it as-is. `make ui-setup` and
`make ui` are only for changing the frontend.

### Configuration

Everything optional. With nothing set it runs offline and scores 28 of 30 on the provided files.

| Variable | Effect when set |
|---|---|
| `GEMINI_API_KEY` | turns on the vision tier, so damaged photos get decided instead of escalated |
| `PHOENIX_COLLECTOR_ENDPOINT` + `PHOENIX_API_KEY` | turns on tracing |
| `GEMINI_MODEL`, `JUDGE_MODEL` | override the default `gemini-3-flash-preview` |

Two ways to set them. A `.env` file is the one that survives opening a new terminal:

```bash
cp .env.example .env        # then fill in GEMINI_API_KEY
make serve                  # the Makefile loads .env automatically
```

Or export them, which only lasts for that shell:

```bash
export GEMINI_API_KEY=your-key-here
make serve
```

Either works for Docker too. `make docker-run` passes the variable *names* to the container and
lets Docker read the values from your environment, so no secret reaches the image, the build log
or your shell history.

```bash
make env      # says which tiers are on, without printing any value
```

Forgetting the key is the easy mistake, and it is quiet: the API keeps working, images just route
to Needs Review instead of being re-read. The browser page says so when it happens, and `make env`
answers it directly. No key is read from a file that is committed, and `.env` is gitignored.

| Command | What it does |
|---|---|
| `make check` | score the 30 provided files |
| `make report` | run the 94 case corpus |
| `make test` | unit tests |
| `make index` | show the compiled forbidden list |
| `make evaluate FILE=...` | one file from the CLI |

### Running the evaluation set

```bash
make report
```

That regenerates the corpus from `eval/corpus/manifest.yaml` and scores all 94 cases. It takes a
couple of minutes because the image tiers run real OCR.

**Set the key first if you want the real number.** The corpus is built so that the last few
percent need the vision tier and the judge, so without a key it reports 90 percent rather than 99:

```bash
export GEMINI_API_KEY=your-key-here     # or put it in .env, which make loads
make report
```

```
tier                  pass    rate
t0_regression        30/30    100%
...
OVERALL              93/94     99%

recall on forbidden .... 100.0%
false-reject rate ......   0.0%
SILENT ACCEPTS .........      0   must be 0
```

The bottom block is the part worth reading. Silent accepts must be zero; overall percentage is
the least interesting line on the page.

More detail when you want it:

| Command | What it does |
|---|---|
| `uv run python -m eval.report --failures` | same run, plus every failing case and why |
| `uv run python -m eval.report --tier t2_near_miss` | score one tier, seconds instead of minutes |
| `uv run python -m eval.report --no-gates` | skip the corpus self-checks |
| `uv run python -m eval.generate --clean` | rebuild the corpus files only |
| `uv run python -m eval.verify_labels --refresh` | re-check the corpus chemistry against PubChem |

`verify_labels` is the one that answers "how do you know your test set is right". It resolves both
sides of every `must_cite` claim and reports confirmed, unverified or refuted. Network bound and
rate limited, so it is not part of `make report`; it writes a cache that the offline gate reads.

The corpus can also run as a Phoenix experiment, which uploads it as a versioned dataset and gives
per-case diffs between two runs. Needs the same two Phoenix variables tracing uses:

```bash
uv run python -m eval.run_experiment --name "baseline"
```

## API

```bash
curl -X POST localhost:8000/evaluate_product -F "file=@texts/P-2025-0004_Ultra_Conditioner.txt"
```

```json
{
  "product_name": "Ultra Conditioner",
  "status": "Rejected",
  "reason": ["Contains C6H6 (exact match)"],
  "evidence": [{"ingredient": "C6H6", "forbidden_entry": "C6H6",
                "matched_by": "literal", "confidence": "certain"}],
  "unread": [],
  "forbidden_list_version": "13e-165fef9a4fdd"
}
```

Takes an uploaded `file` or a `path`. Bring your own list with `-F "forbidden_list=@my_list.csv"`
in CSV, JSON or plain lines. Needs Review returns 202, so a caller cannot mistake an escalation
for a clean pass.

Also: `POST /evaluate_products` for batches, `GET /health`, `GET /policy`, `POST /inspect_policy`.

The browser page at `/` is a React app whose build is committed, so the container serves it
offline. It shows the parsed forbidden list, every ingredient extracted, and which entry each
match hit.

## Why it is built this way

**The 30 sample files cannot tell a good system from a bad one.** Every banned ingredient appears
in exactly the form the CSV lists, so ten lines of case-insensitive string matching scores 30 out
of 30. Across all 20 text-layer products, "Benzene" appears zero times and "Caffeine" zero times.
PubChem lookups, structural matching and an LLM all measure as worth nothing on the visible data,
while the half of the dataset that discriminates was held back.

So the first thing I built was not the pipeline, it was a 94 case corpus covering what actually
breaks. Run it with `make report`.

| Tier | What it tests |
|---|---|
| t0_regression | the 30 provided files, so nothing already working breaks |
| t1_synonym | list says `C6H6`, label says "Benzene". Also E-numbers, CAS numbers, IUPAC |
| t2_near_miss | Sodium **Laureth** Sulfate is not Sodium **Lauryl** Sulfate. Must be Accepted |
| t3_ocr | images degraded until OCR mangles the chemistry |
| t4_layout | inline INCI panels, prose, "Formulated without: Coumarin" which must be Accepted |
| t5_adversarial | salt swaps, Cyrillic lookalikes, Spanish labels |
| t6_degenerate | empty and corrupt files, which must never come back Accepted |
| t7_lists | custom forbidden lists in several formats |

The chemistry claims in the corpus were checked against PubChem independently: 30 confirmed,
7 PubChem could not answer, 0 refuted. The test set is not just my opinion.

## How it works

### The index, built once at startup

The forbidden list has 13 entries and changes monthly. Products are unbounded. So the expensive
work happens once on the small side. Each entry is looked up on PubChem and filed under every key
an ingredient could match on:

| Drawer | Keys | Holds |
|---|---|---|
| `literal` | 3,055 | the entry plus every PubChem synonym |
| `hill` | 107 | canonical molecular formula, so `C2H5OH` equals `C2H6O` |
| `ocr_variant` | 5 | `H2O2` also filed as `h202` |
| `inchikey` | 13 | structure hash |
| `inchikey_parent` | 13 | structure after stripping the counter ion |
| `pubchem_cid` | 13 | registry ID |

The synonym drawer is why `Benzene`, `E218` and `128-37-0` all resolve offline with no model.
`inchikey_parent` is what catches salt substitution: potassium and ammonium lauryl sulfate reduce
to the same parent as sodium lauryl sulfate, while sodium *laureth* sulfate does not and is
correctly left alone. Results are cached in the repo, so a fresh clone runs offline.

### Reading the file

Text is read directly. PDFs use the text layer, rendering and OCRing only pages that have none.
Images go to Tesseract, then Gemini for what Tesseract cannot read cleanly.

OCR reliably mangles chemistry: `H2O2` comes back as `H202`. So every token from a lossy engine
gets a sanity check, `H <= 2C + 2 + N`. `H202` would need 202 hydrogens on zero carbons, so the
read is provably broken and escalates. On the provided images this fires 9 times out of 10.

Not a confidence threshold, deliberately. Tesseract reports **89 to 91 percent confidence** on
those impossible tokens and is right to: the pixels really do look like `H`, `2`, `0`, `2`. It is
confident about the glyphs and wrong about the chemistry, which is exactly what a confidence score
cannot see.

EasyOCR was removed for defeating that check. It read `C8H10N4O2` as `C8HION4O2`, a real iodine
compound, so the corruption was invisible and caffeine was dropped from two labels in silence.

### Matching, in three stages

Each stage only sees what the previous one could not settle.

```
index lookup       offline, instant       about 900 tokens
    v
PubChem resolver   network                about  10 tokens
    v
LLM judge          one batched call       about   5 tokens
```

The judge runs last because it is the only stage that can be confidently wrong. It catches what no
database lists: `E216`, "Butylated Hydroxytoluene" (PubChem 404s on that exact spelling), Spanish
labels.

It kept insisting `Fulvene` was `C6H6`. They share a formula but are different molecules. Rather
than keep rewriting the prompt, the deterministic layer now vetoes the model: both sides get
resolved to structure hashes and compared.

```
Fulvene  vs C6H6           different structures        -> vetoed
Vanillin vs Methylparaben  different structures        -> vetoed
Benceno  vs C6H6           cannot resolve, cannot check -> trusted
```

### Deciding

```
solid evidence           -> Rejected
weak evidence only       -> Needs Review
could not read something -> Needs Review
clean and complete       -> Accepted
```

Three outcomes because the two mistakes cost differently. Missing a banned substance is a recall.
Wrongly flagging a clean product costs somebody five minutes. So doubt escalates and never
resolves to Accepted.

That is enforced by the types. `Ok([])` meaning "checked, found nothing" is a different type from
`Err(...)` meaning "could not answer", so the usual bug where a swallowed error reads as a clean
pass does not compile.

## Results

On the 94 case corpus:

| | offline | with a Gemini key |
|---|---|---|
| overall | 90% | **99%** |
| recall on banned substances | 92.3% | **100%** |
| false rejects | 0% | **0%** |
| citation accuracy | 100% | **100%** |
| silent accepts | 5 | **0** |

Silent accepts, a banned product returned as Accepted, is the only genuinely unacceptable
outcome. Needs Review is a cost, not a failure.

On the 30 provided files: 28 of 30 offline, 30 of 30 with a key, no wrong verdicts either way.
As explained above, that number does not mean much.

**99% is measured on a corpus I wrote.** It tests the failure modes I thought of, so the fair
claim is "99% on cases built to be hard", not "99% accurate". What I would defend more strongly
is the shape of the errors: across every configuration, zero silent accepts and zero false
rejects.

## Observability

Tracing goes to Phoenix, off until you set `PHOENIX_COLLECTOR_ENDPOINT` and `PHOENIX_API_KEY`.
Each screening is one trace named after the file, with a span per stage carrying in/out counts,
so you can see where a product left the funnel and what it cost to get there.

Token counts come from instrumenting the google-genai client, not from anything hand-written.
Phoenix prices them, and its arithmetic agrees to the cent with Google's published rates.

| Path taken | Cost | Latency |
|---|---|---|
| exact match, offline only | $0 | 2 ms |
| image the OCR mangled, vision then match | $0.0014 | 2.3 s |
| E-number, needs the judge | $0.0020 | 3.8 s |

The first row is the point of the cascade, and most traffic looks like it.

## Known gaps

**Formaldehyde releasers are not implemented.** DMDM Hydantoin releases formaldehyde but is not
formaldehyde, so every layer correctly reports "different substance". Catching it needs a curated
list of about a dozen releasers, which is domain data. Note the trap: plain `Urea` is harmless and
appears in four provided products, only *diazolidinyl* and *imidazolidinyl* urea release it.

**The judge does not scale past a few dozen entries.** It puts the whole forbidden list in every
prompt: 473 tokens at 13 entries, 15,259 at 2,000. Cost is the smaller problem, accuracy is the
real one. It needs retrieval to shortlist candidates first.

**Nothing cross-checks what vision reads.** The veto guards matching, not extraction. A misread
that is chemically plausible would pass. Comparing two readers and escalating on disagreement is
the general answer and is not built.

**A new forbidden list is slow the first time**, about 4 seconds per entry against PubChem. Cached
after that, but a 500 entry list is a 30 minute cold build.

**The batch endpoint is sequential, and there is no auth or rate limiting.**

## Assumptions

- The forbidden list is authoritative. If it bans `Aqua`, water gets rejected.
- Salt forms are the same substance. Potassium Lauryl Sulfate is rejected when Sodium Lauryl
  Sulfate is banned.
- English labels are the main target. Other languages route through the LLM.
