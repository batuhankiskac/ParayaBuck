# Short-term weakness: what was tried and where it stands

A one-page map of the short-term drawdown question for production K
(`equity_leveraged_trend_v1` 1.2.0). The numbers and methods live in the linked
reports; this page only says what each one closed and what is left. Status as
of 2026-10-23.

## The question

In October 2026 the account fell 13–14 % from its 2026-10-05 peak while QQQ
fell about 7 %, and the trend vote stayed on, so K stayed fully invested in
QLD. The owner asked whether the bot can react faster to short pullbacks.

## Answer in brief

**No tradeable rule found. The short-term weakness is the price of K's edge,
not a defect that can be fixed.** Every causal way to cut K on a pullback is on
the wrong side of the rebound that usually follows. Every way to add exposure
after a drop also buys into the first legs of real crashes. The only lever left is a risk
preference: lower the vol target and accept less return (see "What is left").

## Why it fails, in one picture

`short_term_oracle_v1` shows the prize is real but sits entirely in knowing
**where the low is**. An oracle that exits on a 2–3 % pullback and restores
0–5 sessions after the known low adds +4 to +11 pp/yr after tax. False alarms
barely hurt it. A causal rule cannot see the low. It re-enters after a
rebound, above its exit price about 4 times in 5, and sits out the strongest
sessions. That chase costs more than the exit saves.

## What was tested

| Family | Report | Result |
|---|---|---|
| Size of the prize (oracle, not tradeable) | [`short_term_oracle_v1`](short_term_oracle_v1.md) | +4…+11 pp/yr after tax with perfect foresight of the low; the value is in re-entry timing |
| Exit on a 3 / 5 % pullback, re-enter on a rebound or SMA10 | [`short_term_reentry_v1`](short_term_reentry_v1.md) | All 6 arms fail, −4.1…−15.1 pp/yr after tax; 63–89 % of exits are false alarms; tax-free variants lose too |
| Restore at the cut price or lower, half cut, deep staged cut (−8 / −12 %) | [`short_term_restore_v1`](short_term_restore_v1.md) | All 6 arms fail; the closest one (C2) is −0.35…−0.71 pp after tax and loses both bootstraps and pooled OOS |
| Buy more after a deep drop (boost to the cap or 1.5× weight) | [`short_term_boost_v1`](short_term_boost_v1.md) | All 6 arms fail; max DD rises to 54–71 % (boosts into the spring-2000 crash). The post-drop edge is +0.32 %/session for 5 sessions and about 0 over 10 |
| Earlier dip / spike overlays (5-day −5 %, RSI(2)) | [`dip_overlay_v1`](dip_overlay_v1.md) | Fail: dip arms add 7–9 pp DD, trims lose 2017+ and OOS |
| Intraday stop on QLD (8 / 12 % below the prior close) | [`intraday_stop_v1`](intraday_stop_v1.md) | Fail: 8 % whipsaws, 12 % rarely fires and sells crash bottoms |
| Protective puts | [`put_hedge_v1`](put_hedge_v1.md) | All 5 arms fail after tax (−0.6…−4.2 pp); puts pay when K is already in cash |
| Collars (put financed by a short call) | [`collar_v1`](collar_v1.md) | All 4 arms fail after tax and in both bootstraps; the call sells the rallies K lives on |
| Volatility filters and faster signals | [`vix_filter_v1`](vix_filter_v1.md), [`ewma_band_v1`](ewma_band_v1.md), [`crash_entry_v1`](crash_entry_v1.md), [`exec_delay_v1`](exec_delay_v1.md) | None passes |

The full list, including older studies, is in [`INDEX.md`](INDEX.md).

### Questions answered inside the reports

These were raised later and are covered in a report's caveats or amendments,
not in a separate study:

- **Switch QLD to QQQ instead of cutting to half.** It would realise all of the
  QLD gain on every switch, so after tax it does worse than the half cut, which
  already loses (`short_term_restore_v1` §8).
- **A resting limit order at the cut price for re-entry.** It would remove part
  of the +1.4 % gap chase, but not the extra turnover and tax. Not simulated
  (`short_term_restore_v1` §8).
- **"+0.5–0.6 %/session after an 8 % drop."** This figure was inflated because
  it was measured only until the rebound. Over a fixed horizon it is +0.32 %/session for 5
  sessions (`short_term_restore_v1` Amendment 1, `short_term_boost_v1`).

## What is left

Lowering the vol target ([`vt_level_k_v1`](vt_level_k_v1.md)) is a choice about
how much risk to take, not a better strategy. The costs are:

| `vt` | Max DD (pre-tax) | After-tax cost, 2000 / 2010 / 2017 windows | October 2026 loss |
|---|---|---|---|
| 0.35 (production) | 46.7 % | — | −13.42 % |
| 0.30 | 41.4 % | −0.87 / −1.07 / −1.83 pp/yr | −13.33 % |
| 0.25 | 35.7 % | −1.97 / −2.49 / −4.58 pp/yr | −11.20 % |

The knob acts in high volatility. It does little when a fall starts from a calm
market, as in October 2026. It makes the big drawdowns shallower but not rarer. It
also raises turnover (sells 10.7 → 14.0 → 17.6 per year). Nothing in this list is
proposed for adoption; changing `vt` is the owner's call.

For the live view, the dashboard's trend card shows where the vote would flip
and puts the current drawdown in the context of K's backtest episodes
([`docs/dashboard.md`](../dashboard.md)).
