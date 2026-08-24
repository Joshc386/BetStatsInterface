# Player Prop Model — Feature → Target Metric Map

> **Background research, not a build brief (superseded 2026-06).** This predates the
> project's scope decisions and describes a predictive model that **is not part of
> BetStats** — the platform surfaces observed metrics and hit rates, and deliberately
> does not model or predict (see `CONTEXT.md`). Two of the sources named below are also
> no longer available: FBref lost its Opta-derived stats in January 2026 and FotMob was
> dropped as a source (`docs/adr/0001`, `docs/adr/0002`). Kept for the causal reasoning
> behind each target metric, which still informs which metrics are worth surfacing.

## Framework

Every target metric has two components to model separately:

- **Opportunity rate** — how often does the player get into situations where the event can happen?
- **Conversion rate** — given the opportunity, how likely are they to produce the outcome?

The causal chain for all metrics follows:

```
Player behaviour/style → Observable proxy stats → Target outcome
```

Features should sit as close to the *cause* as possible, not just correlate with historical outcomes.

---

## Target: Fouls Committed (1+)

**Causal story:** A player fouls because they attempt defensive actions and either mistime them or deliberately stop a dangerous situation. Foul rate = defensive action *volume* × defensive action *quality*.

```
Defensive role intensity
        ↓
Tackles attempted per 90        ← volume of attempts (not just successful)
Pressures per 90                ← pressing intensity
Duel attempt rate               ← how often they engage physically
        ↓
Timing/technique quality
        ↓
Foul rate = f(volume, quality, opponent difficulty, referee)
```

### Feature → Target Links

| Feature | Why it predicts fouls | Source |
|---|---|---|
| Tackles attempted per 90 (not won) | Failed tackle attempts = fouls. Win rate tells you quality | FBref / FotMob |
| Pressures per 90 | High pressers get into more situations where fouls happen | FBref |
| Duel loss rate | Losing duels → desperation foul to stop counter | FotMob |
| Opponent dribble attempts vs player | More dribbles attempted against = more foul opportunities | FotMob |
| Position (CB/DM) | Structural — these roles have higher defensive action frequency | FBref |
| Referee foul rate | External multiplier — same action gets called differently | Ref Stats UK |
| Match context (chasing game) | Teams behind commit more fouls | Derived |
| Ball recoveries per 90 | Proxy for defensive involvement and engagement frequency | FotMob |

### What to Ignore
- Raw historical foul count as a direct feature — it's the output, not the cause. Using it as an input is just persistence modelling with no causal mechanism.

---

## Target: Shots / Shots on Target (1+)

**Causal story:** A player gets a shot when they reach a shooting position with the ball. SoT is then a function of shot location quality + technique + pressure at time of shot.

```
Attacking role + movement pattern
        ↓
Touches in box per 90           ← access to shooting positions
Progressive passes received     ← ball getting to them in dangerous areas
Shot creating actions (SCA)     ← involvement in the buildup to shots
        ↓
Shot quality at moment of attempt
        ↓
SoT = f(position quality, technique, defensive pressure on shot)
```

### Feature → Target Links

| Feature | Why it predicts shots/SoT | Source |
|---|---|---|
| Touches in penalty area per 90 | Direct proxy for being in shooting positions | FBref / FotMob |
| Progressive passes received per 90 | Getting the ball in forward areas | FBref |
| SCA (shot creating actions) per 90 | Involved in shot creation — correlates with own shot frequency | FBref |
| Shot location distribution | Central positions → higher SoT rate than wide/long range | FotMob / StatsBomb |
| Shots under pressure rate | Pressured shots → lower SoT conversion | FBref |
| Role (striker vs winger vs CM) | Structural frequency differences | Derived |
| Opponent defensive shape / low block rate | Low block = fewer shooting lanes | Derived |
| Chances created (SCA proxy) | High chance creators get shots from own buildup | FotMob |

---

## Target: Tackles (1+)

**Causal story:** Tackles are a subset of defensive actions. A player attempts a tackle when an opponent is dribbling past them or carrying ball nearby. Tackle frequency reflects defensive workload *and* defensive style.

```
Defensive workload + style
        ↓
Defensive actions per 90        ← overall defensive involvement
Duel attempt frequency          ← willingness to engage
Position on pitch when defending ← deeper = more tackle opportunities
        ↓
Tackle attempt rate × success rate
        ↓
Tackles won per 90
```

### Feature → Target Links

| Feature | Why it predicts tackles | Source |
|---|---|---|
| Defensive actions per 90 | Tackles are a subset — higher actions = more tackle attempts | FBref |
| Dribbles faced per 90 | Can't tackle if nobody dribbles at you | FotMob |
| Position (fullback, DM) | Structurally in tackle-heavy zones | FBref |
| Team's defensive line height | High line = more open field defending = more tackle situations | Derived |
| Opponent dribble attempt rate | High dribbling teams force more tackle situations | FBref / FotMob |
| Pressure success rate | Reflects defensive engagement style | FBref |
| Ground duels attempted per 90 | Direct precursor to tackle attempts | FotMob |

---

## Cross-Metric Features (Apply to All Targets)

| Feature | Relevance | Source |
|---|---|---|
| Minutes played (starter vs sub) | Exposure normalisation — per-90 only valid for sufficient minutes | FBref / FotMob |
| Rolling window (last 5 vs last 10 games) | Recency weighting captures form better than season average | Derived |
| Home / away | Behavioural differences in all defensive/attacking metrics | FBref |
| Opponent strength | Adjusts expected opportunity rate up or down | Derived |
| Referee (card/foul rate) | Significant external multiplier, especially for fouls/cards | Ref Stats UK |
| Touch heatmap zone | Compact encoding of positional role — more stable than counting stats | FotMob |

---

## Data Sources

| Source | Access | What it gives |
|---|---|---|
| FBref (via soccerdata) | Free | Aggregated player match stats: fouls, tackles, pressures, SCA, progressive passes |
| FotMob (via scraping) | Free (scrape) | Granular per-match player data: duels, touches in box, heatmap zones |
| Ref Stats UK | Free | Referee-level foul and card rates per match |
| StatsBomb open data | Free (GitHub) | Full event stream — useful for methodology validation, not Championship |
| Understat (via soccerdata) | Free | Shot-level xG with x/y coordinates — PL only |

---

## FotMob API Endpoints to Investigate

When scraping via DevTools (Network → XHR/Fetch tab):

```
https://www.fotmob.com/api/matchDetails?matchId=...
https://www.fotmob.com/api/playerData?playerId=...
https://www.fotmob.com/api/matchStats?matchId=...
```

**Priority fields to extract per player per match:**

```
Defensive:
- Duels attempted (ground + aerial split)
- Pressures attempted
- Ball recoveries
- Dribbles faced

Attacking:
- Touches in final third / penalty area
- Progressive carries received
- Chances created

General:
- Touch heatmap zone data (positional role proxy)
- Minutes played
```

---

## Modelling Notes

- **Model type:** Logistic regression (binary classification) or Poisson regression (count → P(1+))
- **Train/test split:** Time-series only — train on seasons N-2/N-1, test on season N. Never random split.
- **Validation metrics:** Brier score, log loss, calibration curve
- **Feature importance:** Run Random Forest after logistic baseline to empirically rank features
- **Key risk:** Championship FBref data only reliable from ~2017/18 — keep models regularised due to limited sample size
