# M3 candidate-owned OCP fixture

The real gate consumes the immutable R4 composite bars and accepted R5
projection.  Unit tests use small in-memory `Bar` fixtures only; no fixture
data is promoted to an R1–R5 artifact, and no fixture test invokes a portfolio
backtest or an execution engine.
