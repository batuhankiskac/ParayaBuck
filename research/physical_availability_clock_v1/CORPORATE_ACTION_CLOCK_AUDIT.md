# Corporate-action timing

Canonical v5 action CSV records `ex_date`, optional `record_date` and `payable_date`. `load_csv_corporate_actions` excludes non-cash action types for split-adjusted bars and maps cash-dividend `ex_date` to UTC midnight. The backtest applies these action timestamps before the fill cycle on the next execution-bar label. This preserves the legacy ex-date accounting order and avoids inventing a payable-date cash model.

Physical equity replay must place the ex-date action before that session's opening execution, based on the position held before the ex-date; it must not process the action at the previous day's close merely because that is when the previous daily signal becomes available. No corporate-action accounting code or canonical joins were changed in this campaign. Full physical event verification remains pending the isolated crypto baseline repair.
