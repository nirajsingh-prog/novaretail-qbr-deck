# NovaRetail QBR – AI-Assisted Executive Deck Generation

Generates the 5-slide NovaRetail Quarterly Business Review deck **programmatically** from the raw dataset,
filling the fixed template (`novaretail_template.pptx`) with python-pptx. No slide is edited by hand.

**Final deck:** [`output/NovaRetail_QBR_Executive_Deck.pptx`](output/NovaRetail_QBR_Executive_Deck.pptx)

## How to run

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# put the 3 provided files in ./inputs/
#   novaretail_template.pptx, novaretail_dataset.xlsx, novaretail_icon_repo.zip

python generate_deck.py --inspect          # (optional) list every shape name in the template
python generate_deck.py                    # rule-based narrative  -> output/NovaRetail_QBR_Executive_Deck.pptx
python generate_deck.py --llm gemini       # Gemini-written narrative (needs GOOGLE_API_KEY or Vertex AI ADC)
```

For Vertex AI instead of an API key: `export GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<id> GOOGLE_CLOUD_LOCATION=us-central1`
and `gcloud auth application-default login`. The icon repo can be passed as the `.zip` or as an unzipped folder (`--icons path/`).

## Project structure

| File | Responsibility |
|---|---|
| `src/analysis.py` | Loads the 3 sheets, pivots region × quarter, computes KPIs and "who's winning / who's at risk" facts |
| `src/narrative.py` | Turns metrics into headlines, bullets, actions; rule-based or Gemini (validated, falls back per field); icon selection |
| `src/deck_builder.py` | Finds named shapes, writes text, inserts native charts and icons, removes dashed boxes and instruction text |
| `shape_map.json` | Exact template shape names per slide (from `--inspect`) |
| `generate_deck.py` | CLI entry point; also writes `output/narrative.json` (all slide copy, for review) |

## Design decisions

**Finding placeholders** – every shape is located by its `shape.name` (e.g. `chart_performance`, `insights_risk`, `icon_3`),
never by coordinates. The exact template names are listed in `shape_map.json` (used by default). If a name doesn't match the expected pattern, the builder falls back to the template's bracketed
instruction text, and `shape_map.json` pins each role to its exact shape name (e.g. `kpi1_value`, `summary_text`, `recommendations_text`, `closing_statement`).
The run fails loudly if any `[FILL…]`, `[VALUE]`, `CHART` or `ICON` text is left in the deck.

**Charts** – native, editable PowerPoint charts (`GraphicFrame`), inserted at the placeholder's exact position and size.
Each region keeps the same colour on every slide.

| Slide | Chart | Why |
|---|---|---|
| 2 Performance | Clustered column, revenue by region × quarter | Compares scale and growth at the same time |
| 3 Customer | Line, NPS by region | The story is about direction (APAC/Europe up, Latin America down) |
| 4 Risk | Line, stockout rate by region | Shows supply strain building over the quarters |

**KPIs (slide 1)** – computed in Python, never by the LLM: total revenue for the latest quarter, revenue growth vs the
first quarter in the data (the dataset has 4 quarters, so true year-on-year can't be calculated and isn't claimed),
and revenue-weighted blended NPS.

**Icons** – chosen by keyword-matching each slide's theme against the icon names, not in file order:
globe_region (multi-region overview), growth_chart, customer_star, risk_alert, lightbulb_action.
The unused icons (delivery_truck, return_arrow, shield_security) were considered but are narrower than each slide's theme.

**Narrative** – every region name and number in the text is derived from the data, so the deck regenerates correctly
when next quarter's dataset is dropped in. Gemini output is validated (structure and length); any field that fails keeps the rule-based text.

**Clean-up** – dashed boxes and every caption inside them are deleted after replacement, instruction italics are removed,
text size is auto-fitted to the fixed boxes, and "Capstone Template" in the footer is replaced with the quarter.
